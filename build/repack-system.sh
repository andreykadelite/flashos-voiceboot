#!/bin/bash
# Repack the already provisioned rootfs after reviewed source/package changes.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
root=/opt/flashos-rootfs
cd "$project"
test -f "$root/etc/debian_version"
test ! -S /tmp/flashos-qmp.sock
python3 -m unittest discover -s tests -q
cp src/*.py "$root/opt/flashos/"
cp boot/flashos.service "$root/etc/systemd/system/flashos.service"
cp boot/flashos-apps.service "$root/etc/systemd/system/flashos-apps.service"
chmod 644 "$root/etc/systemd/system/flashos-apps.service"
chroot "$root" systemctl enable flashos-apps.service
cp tools/verify-runtime.py "$root/tmp/verify-runtime.py"
chroot "$root" python3 /tmp/verify-runtime.py
python3 tools/sanitize-live-root.py "$root"
kernel=$(chroot "$root" readlink -f /vmlinuz)
initrd=$(chroot "$root" readlink -f /initrd.img)
test "${kernel#/boot/vmlinuz-}" = "${initrd#/boot/initrd.img-}"
kernel_version=${kernel#/boot/vmlinuz-}
test -d "$root/lib/modules/$kernel_version"
# Source/firmware/initramfs changes must not leave stale module indexes or initrd.
# Always target the shipped Debian kernel, never the container's running kernel.
chroot "$root" depmod -a "$kernel_version"
chroot "$root" update-initramfs -u -k "$kernel_version"
test "$(( $(stat -c %s "$root$kernel") + $(stat -c %s "$root$initrd") ))" -lt 470000000
cp "$root$kernel" dist/usb/flashos/vmlinuz
cp "$root$initrd" dist/usb/flashos/initrd
cp dist/usb/flashos/vmlinuz dist/usb/flashos/initrd dist/boot/flashos/
chroot "$root" apt-get clean
chroot "$root" dpkg-query -W > dist/packages.tsv
python3 tools/package-public-docs.py
printf 'FlashOS 2026.10.13.2\n' > dist/usb/flashos/flashos.marker
cp dist/usb/flashos/flashos.marker dist/boot/flashos/flashos.marker
mksquashfs "$root" dist/usb/flashos/filesystem.squashfs -noappend -comp xz -b 1M -processors 4 -wildcards -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' 'var/cache/apt/archives/*' 'var/lib/apt/lists/*'
python3 tools/verify-hardware-build.py --kernel-version "$(basename "$initrd" | sed 's/^initrd.img-//')"
python3 tools/verify-app-store-build.py
python3 tools/verify-toolkit-build.py
cd dist
find usb boot -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
