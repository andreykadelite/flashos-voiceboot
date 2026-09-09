"""Actual backup, corrupted-image rejection, restore and file carving on QEMU disks."""
import json
from pathlib import Path
import subprocess

from files import Volume, worker
import recovery
import toolkit


def run(record, source, live, root, digest):
    assert subprocess.check_output(['systemd-detect-virt'], text=True).strip() == 'qemu'
    assert subprocess.check_output(['lsblk', '-dn', '-o', 'SERIAL', '/dev/vdb'], text=True).strip() == 'FLASHOS-RESTORE'
    assert int(Path('/sys/class/block/vdb/size').read_text()) == 256 * 1024**2 // 512
    target = next(d for d in toolkit.devices(live, 'part') if d.path == '/dev/vdb1')
    assert source.path == '/dev/vda2' and target.disk != live and source.disk != live
    before = digest('/dev/vda')
    backup = record('partclone-backup', recovery.backup_job(source, live, root))
    assert not backup.failed, backup.result
    relative = backup.payload
    checked = record('partclone-verify', lambda job: recovery.verify_image(job, root, relative, live))
    assert not checked.failed, checked.result
    image = recovery.image_path(root, relative)
    target_before = digest(target.path)
    subprocess.run(['mount', '-o', 'remount,rw', str(root)], check=True)
    try:
        with image.open('r+b') as stream:
            stream.seek(-1, 2)
            original = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes([original[0] ^ 1]))
        subprocess.run(['sync', '-f', str(image)], check=True)
    finally:
        subprocess.run(['mount', '-o', 'remount,ro', str(root)], check=True)
    rejected = record('partclone-corrupt-rejected', recovery.restore_job(relative, target, live, root))
    assert rejected.failed and 'SHA256' in rejected.result and digest(target.path) == target_before
    subprocess.run(['mount', '-o', 'remount,rw', str(root)], check=True)
    try:
        with image.open('r+b') as stream:
            stream.seek(-1, 2)
            stream.write(original)
        subprocess.run(['sync', '-f', str(image)], check=True)
    finally:
        subprocess.run(['mount', '-o', 'remount,ro', str(root)], check=True)
    restored = record('partclone-restore-readback', recovery.restore_job(relative, target, live, root))
    assert not restored.failed, restored.result
    subprocess.run(['udevadm', 'settle'], check=True)
    def contents(job):
        current = next(d for d in toolkit.devices(live, 'part') if d.path == '/dev/vdb1')
        assert current.fstype == source.fstype
        left, right = Volume(source, live), Volume(current, live)
        try:
            left.open(job)
            right.open(job)
            for name in ('Пример.txt', 'data.bin', 'recovery.png'):
                assert digest(left.root / 'Documents' / name) == digest(right.root / 'Documents' / name), name
            assert (right.root / 'Documents/skip-link').is_symlink()
        finally:
            left.close(job)
            right.close(job)
        assert before == digest('/dev/vda'), 'Source disk was modified'
        job.result = 'Restored files and symbolic link match; source disk unchanged.'
    verified = record('restored-files-match', contents)
    assert not verified.failed, verified.result
    current = next(d for d in toolkit.devices(live, 'part') if d.path == '/dev/vdb1')
    smaller_fs = record('partclone-smaller-filesystem-backup', recovery.backup_job(current, live, root))
    assert not smaller_fs.failed, smaller_fs.result
    smaller_manifest = recovery.read_manifest(recovery.image_path(root, smaller_fs.payload))
    assert smaller_manifest['filesystem_bytes'] < smaller_manifest['source_bytes']
    block_map = image.with_name('blocks.map')
    target_before = digest(current.path)
    subprocess.run(['mount', '-o', 'remount,rw', str(root)], check=True)
    try:
        original_map = block_map.read_bytes()
        block_map.write_bytes(original_map[:-1] + b'X')
    finally:
        subprocess.run(['mount', '-o', 'remount,ro', str(root)], check=True)
    rejected = record('partclone-corrupt-map-rejected', recovery.restore_job(relative, current, live, root))
    assert rejected.failed and 'Карта блоков повреждена' in rejected.result and digest(current.path) == target_before
    subprocess.run(['mount', '-o', 'remount,rw', str(root)], check=True)
    try:
        block_map.write_bytes(original_map)
    finally:
        subprocess.run(['mount', '-o', 'remount,ro', str(root)], check=True)
    carved = record('photorec-carve', recovery.photorec_job(source, live, root))
    assert not carved.failed, carved.result
    def verify_carve(job):
        volume = Volume(source, live)
        try:
            volume.open(job)
            expected = digest(volume.root / 'Documents/recovery.png')
        finally:
            volume.close(job)
        found = [p for p in (root / 'recovery').glob('files-*/recovered*/*.png') if digest(p) == expected]
        assert found, 'PhotoRec did not recover the known PNG'
        assert before == digest('/dev/vda')
        job.result = 'PhotoRec recovered known PNG with matching SHA256; source unchanged.'
    recovered = record('photorec-content-match', verify_carve)
    assert not recovered.failed, recovered.result
    filesystem_roundtrips(record, live, root, digest)


def filesystem_roundtrips(record, live, root, digest):
    # This destructive fixture helper has no configurable disk path.
    assert subprocess.check_output(['lsblk', '-dn', '-o', 'SERIAL', '/dev/vdb'], text=True).strip() == 'FLASHOS-RESTORE'
    assert int(Path('/sys/class/block/vdb/size').read_text()) == 256 * 1024**2 // 512
    assert live != '/dev/vdb'
    mount = Path('/run/flashos-format-fixture')
    mount.mkdir(exist_ok=True)
    def refresh():
        subprocess.run(['udevadm', 'trigger', '--action=change', '--settle', '/sys/class/block/vdb1'],
                       check=True, timeout=30)
    for fstype, command in (('vfat', ['mkfs.fat', '-F', '32']), ('exfat', ['mkfs.exfat']), ('ntfs', ['mkntfs', '-Q', '-F'])):
        subprocess.run([*command, '/dev/vdb1'], check=True, capture_output=True, timeout=60)
        refresh()
        subprocess.run(['mount', '/dev/vdb1', str(mount)], check=True, timeout=20)
        try:
            (mount / 'roundtrip.bin').write_bytes(bytes(range(256)) * 2048)
            expected = digest(mount / 'roundtrip.bin')
        finally:
            subprocess.run(['umount', str(mount)], check=True, timeout=20)
        current = next(d for d in toolkit.devices(live, 'part') if d.path == '/dev/vdb1')
        assert current.fstype == fstype
        saved = record('partclone-' + fstype + '-backup', recovery.backup_job(current, live, root))
        assert not saved.failed, saved.result
        # Remove the original filesystem, so a false no-op restore cannot pass.
        subprocess.run([*command, '/dev/vdb1'], check=True, capture_output=True, timeout=60)
        refresh()
        current = next(d for d in toolkit.devices(live, 'part') if d.path == '/dev/vdb1')
        restored = record('partclone-' + fstype + '-restore', recovery.restore_job(saved.payload, current, live, root))
        assert not restored.failed, restored.result
        def contents(job):
            current = next(d for d in toolkit.devices(live, 'part') if d.path == '/dev/vdb1')
            volume = Volume(current, live)
            try:
                volume.open(job)
                assert digest(volume.root / 'roundtrip.bin') == expected
            finally:
                volume.close(job)
            job.result = fstype + ': original filesystem replaced, restored file SHA256 matches.'
        checked = record('partclone-' + fstype + '-file-match', contents)
        assert not checked.failed, checked.result
