"""Attach the verified FlashOS ZIP outside the Windows mapped PE image.

Nuitka reads its own, small runtime blob by its compiled length. The additional
ZIP is ordinary executable overlay data and remains readable with ZipFile.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
_IMAGE_SPEC = importlib.util.spec_from_file_location(
    "check_installer_image", ROOT / "tools/check-installer-image.py")
_IMAGE_GATE = importlib.util.module_from_spec(_IMAGE_SPEC)
_IMAGE_SPEC.loader.exec_module(_IMAGE_GATE)
MAX_STUB_BYTES = _IMAGE_GATE.MAX_IMAGE_BYTES
MAX_MANIFEST_BYTES = 2 * 1024**2
CHUNK_BYTES = 4 * 1024**2
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _fingerprint(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def _hash_file(path):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(CHUNK_BYTES):
            hasher.update(block)
    return hasher.hexdigest()


def pe_image(path):
    """Use the canonical image gate to bound the original runtime stub."""
    path = Path(path)
    file_bytes = path.stat().st_size
    if not 64 <= file_bytes <= MAX_STUB_BYTES:
        raise ValueError("The Nuitka executable must be a small runtime stub.")
    layout = _IMAGE_GATE.image_layout(path)
    return {"machine": "x64", "mapped_image_bytes": layout["image_bytes"],
            "stub_bytes": file_bytes, "pe_raw_end": layout["pe_file_end"]}


def _safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Invalid payload path.")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Invalid payload path.")


def verify_payload(path, expected_manifest_sha256, verify_objects=True):
    """Check the exact archive namespace and all content before publication."""
    if not isinstance(expected_manifest_sha256, str) or not DIGEST.fullmatch(expected_manifest_sha256):
        raise ValueError("Invalid pinned manifest digest.")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or any(entry.is_dir() for entry in entries):
            raise ValueError("Duplicate or directory payload entries are forbidden.")
        manifest_info = archive.getinfo("manifest.json")
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError("The payload manifest is too large.")
        raw = archive.read(manifest_info)
        if hashlib.sha256(raw).hexdigest() != expected_manifest_sha256:
            raise ValueError("The payload manifest does not match the compiled pin.")
        manifest = json.loads(raw)
        if (not isinstance(manifest, dict) or manifest.get("schema") != 1
                or not isinstance(manifest.get("objects"), dict)
                or not isinstance(manifest.get("files"), list)):
            raise ValueError("Unsupported payload manifest.")
        objects = manifest["objects"]
        records = manifest["files"]
        referenced = set()
        destinations = set()
        for digest, size in objects.items():
            if (not isinstance(digest, str) or not DIGEST.fullmatch(digest)
                    or type(size) is not int or size < 0):
                raise ValueError("Invalid payload object.")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid payload file record.")
            digest = record.get("sha256")
            section, relative = record.get("section"), record.get("path")
            _safe_path(relative)
            if section not in ("usb", "boot", "engine", "info"):
                raise ValueError("Invalid payload section.")
            destination = (section, relative)
            if destination in destinations:
                raise ValueError("Duplicate payload destination.")
            destinations.add(destination)
            if (not isinstance(digest, str) or digest not in objects
                    or type(record.get("bytes")) is not int or record["bytes"] != objects[digest]):
                raise ValueError("A payload file references an invalid object.")
            referenced.add(digest)
        if referenced != set(objects):
            raise ValueError("The archive contains unreferenced payload objects.")
        expected_names = {"manifest.json"} | {"objects/" + digest for digest in objects}
        if set(names) != expected_names:
            raise ValueError("Unexpected or missing payload archive entries.")
        for digest, size in objects.items():
            info = archive.getinfo("objects/" + digest)
            if info.file_size != size or info.flag_bits & 1:
                raise ValueError("Invalid payload object size or encryption.")
            if verify_objects:
                hasher = hashlib.sha256()
                with archive.open(info) as source:
                    while block := source.read(CHUNK_BYTES):
                        hasher.update(block)
                if hasher.hexdigest() != digest:
                    raise ValueError("Payload object SHA256 mismatch.")
    return {"manifest_sha256": expected_manifest_sha256,
            "objects_verified": len(objects), "files_verified": len(records)}


def _copy_stream(source, target, total_hasher):
    hasher = hashlib.sha256()
    while block := source.read(CHUNK_BYTES):
        target.write(block)
        hasher.update(block)
        total_hasher.update(block)
    return hasher.hexdigest()


def _verify_ranges(path, stub_bytes):
    prefix = hashlib.sha256()
    suffix = hashlib.sha256()
    complete = hashlib.sha256()
    with path.open("rb") as source:
        remaining = stub_bytes
        while remaining:
            block = source.read(min(CHUNK_BYTES, remaining))
            if not block:
                raise ValueError("Truncated output executable.")
            remaining -= len(block)
            prefix.update(block)
            complete.update(block)
        while block := source.read(CHUNK_BYTES):
            suffix.update(block)
            complete.update(block)
    return prefix.hexdigest(), suffix.hexdigest(), complete.hexdigest()


def append_payload(exe, payload, expected_manifest_sha256):
    """Atomically replace the stub only after validating its complete overlay."""
    exe, payload = Path(exe), Path(payload)
    if exe.is_symlink() or payload.is_symlink() or exe.resolve() == payload.resolve():
        raise ValueError("Distinct, ordinary executable and ZIP files are required.")
    exe_state, payload_state = _fingerprint(exe), _fingerprint(payload)
    image = pe_image(exe)
    if zipfile.is_zipfile(exe):
        raise ValueError("The executable already contains a ZIP overlay.")
    evidence = verify_payload(payload, expected_manifest_sha256)
    stub_hash, payload_hash = _hash_file(exe), _hash_file(payload)
    if _fingerprint(exe) != exe_state or _fingerprint(payload) != payload_state:
        raise ValueError("Build inputs changed during validation.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=exe.name + ".overlay-",
                                         suffix=".tmp", dir=exe.parent, delete=False) as target:
            temporary = Path(target.name)
            total_hasher = hashlib.sha256()
            with exe.open("rb") as source:
                copied_stub = _copy_stream(source, target, total_hasher)
            with payload.open("rb") as source:
                copied_payload = _copy_stream(source, target, total_hasher)
            target.flush()
            os.fsync(target.fileno())
        if copied_stub != stub_hash or copied_payload != payload_hash:
            raise ValueError("Build inputs changed while copying.")
        if temporary.stat().st_size != exe_state[2] + payload_state[2]:
            raise ValueError("The output executable size does not match its inputs.")
        prefix, suffix, output_hash = _verify_ranges(temporary, exe_state[2])
        if (prefix != stub_hash or suffix != payload_hash
                or output_hash != total_hasher.hexdigest()):
            raise ValueError("The output executable failed its streaming verification.")
        output_evidence = verify_payload(temporary, expected_manifest_sha256, verify_objects=False)
        if output_evidence != evidence:
            raise ValueError("The appended archive does not match the verified payload.")
        if _fingerprint(exe) != exe_state or _fingerprint(payload) != payload_state:
            raise ValueError("Build inputs changed before publication.")
        os.replace(temporary, exe)
        temporary = None
        return {**image, **evidence, "ok": True, "exe": str(exe.resolve()),
                "payload": str(payload.resolve()), "exe_bytes": exe_state[2] + payload_state[2],
                "stub_sha256": stub_hash, "payload_sha256": payload_hash,
                "exe_sha256": output_hash, "overlay_offset": exe_state[2],
                "overlay_bytes": payload_state[2]}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--manifest-sha256")
    options = parser.parse_args()
    pin = options.manifest_sha256
    if pin is None:
        sys.path.insert(0, str(ROOT))
        from installer._release import MANIFEST_SHA256
        pin = MANIFEST_SHA256
    print(json.dumps(append_payload(options.exe, options.payload, pin), sort_keys=True))


if __name__ == "__main__":
    main()
