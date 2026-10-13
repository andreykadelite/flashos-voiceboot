#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
root=/opt/flashos-rootfs
mkdir -p "$project/dist"
if [ ! -f "$root/etc/debian_version" ]; then
    debootstrap --variant=minbase trixie "$root" https://deb.debian.org/debian
fi
printf '#!/bin/sh\nexit 101\n' > "$root/usr/sbin/policy-rc.d"
chmod +x "$root/usr/sbin/policy-rc.d"
rm -f "$root/etc/resolv.conf"
cp /etc/resolv.conf "$root/etc/resolv.conf"
cat > "$root/etc/apt/sources.list" <<'EOF'
deb https://deb.debian.org/debian trixie main contrib non-free-firmware
deb https://deb.debian.org/debian trixie-updates main contrib non-free-firmware
deb https://security.debian.org/debian-security trixie-security main contrib non-free-firmware
EOF
printf 'deb https://deb.debian.org/debian trixie-backports main contrib non-free-firmware\n' > "$root/etc/apt/sources.list.d/flashos-backports.list"
mountpoint -q "$root/proc" || mount -t proc proc "$root/proc"
mountpoint -q "$root/sys" || mount --rbind /sys "$root/sys"
mountpoint -q "$root/dev" || mount --rbind /dev "$root/dev"
mount --make-rslave "$root/sys"
mount --make-rslave "$root/dev"
trap 'umount -R "$root/proc" "$root/sys" "$root/dev" 2>/dev/null || true' EXIT
chroot "$root" apt-get update
chroot "$root" apt-get install -y --no-install-recommends live-boot systemd-sysv udev python3 espeak-ng dbus pciutils usbutils console-setup-linux kbd locales ca-certificates shim-signed grub-efi-amd64-signed grub-common efibootmgr
chroot "$root" apt-get install -y --no-install-recommends -t trixie-backports alsa-utils alsa-ucm-conf libasound2t64 pipewire pipewire-bin wireplumber pipewire-system-services wireplumber-system-services
chroot "$root" apt-get install -y --no-install-recommends memtester stress-ng smartmontools nvme-cli lshw dmidecode ethtool hdparm lm-sensors iproute2 iputils-ping traceroute dnsutils dhcpcd-base gddrescue parted gdisk fdisk dosfstools e2fsprogs ntfs-3g xfsprogs btrfs-progs libao4
chroot "$root" apt-get install -y --no-install-recommends -t trixie-backports smartmontools
chroot "$root" apt-get install -y --no-install-recommends curl gpg exfatprogs lsscsi sg3-utils iw rfkill
bash "$project/build/install-admin.sh"
bash "$project/build/install-recovery.sh"
bash "$project/build/install-hardware.sh"
python3 "$project/build/install-rhvoice.py"
bash "$project/build/install-app-store.sh"
chroot "$root" update-smart-drivedb -u github
chroot "$root" systemctl mask smartmontools.service
cp "$project/tools/verify-runtime.py" "$root/tmp/verify-runtime.py"
chroot "$root" python3 /tmp/verify-runtime.py
mkdir -p "$root/opt/flashos" "$root/etc/systemd/system/multi-user.target.wants"
mkdir -p "$root/etc/network"
cp "$project"/src/*.py "$root/opt/flashos/"
for service in flashos-pipewire flashos-wireplumber; do
    rm -f "$root/etc/systemd/system/$service.service" "$root/etc/systemd/system/multi-user.target.wants/$service.service"
done
cp "$project/boot/flashos.service" "$root/etc/systemd/system/"
cp "$project/boot/flashos-apps.service" "$root/etc/systemd/system/"
chmod 644 "$root/etc/systemd/system/flashos-apps.service"
chroot "$root" systemctl enable flashos-apps.service
chmod 644 "$root/etc/systemd/system/flashos.service"
chroot "$root" usermod -aG audio pipewire
mkdir -p "$root/etc/pipewire/pipewire.conf.d" "$root/etc/wireplumber/wireplumber.conf.d"
cp "$project/boot/pipewire-flashos.conf" "$root/etc/pipewire/pipewire.conf.d/90-flashos.conf"
cp "$project/boot/wireplumber-flashos.conf" "$root/etc/wireplumber/wireplumber.conf.d/90-flashos.conf"
mkdir -p "$root/etc/systemd/system/wireplumber.service.d"
printf '[Service]\nEnvironment=XDG_STATE_HOME=/run/pipewire/state\n' > "$root/etc/systemd/system/wireplumber.service.d/flashos.conf"
chroot "$root" systemctl enable pipewire.service wireplumber.service flashos.service
ln -sf /dev/null "$root/etc/systemd/system/getty@tty1.service"
ln -sf /dev/null "$root/etc/systemd/system/serial-getty@ttyS0.service"
printf 'flashos\n' > "$root/etc/hostname"
printf 'LANG=C.UTF-8\n' > "$root/etc/default/locale"
printf 'FONTFACE="Terminus"\nFONTSIZE="16"\nCODESET="Uni2"\n' > "$root/etc/default/console-setup"
chroot "$root" passwd -l root
python3 "$project/tools/sanitize-live-root.py" "$root"
chroot "$root" update-initramfs -u -k all
chroot "$root" apt-get clean
mkdir -p "$project/dist/boot/flashos/efi" "$project/dist/boot/EFI/debian" "$project/dist/boot/ventoy" "$project/dist/usb/flashos" "$project/dist/usb/ventoy" "$project/dist/usb/iso"
kernel=$(chroot "$root" readlink -f /vmlinuz)
initrd=$(chroot "$root" readlink -f /initrd.img)
test -f "$root$kernel" && test -f "$root$initrd"
test "$(stat -c %s "$root$kernel")" -gt 0
# Installer creates a 512 MiB FLASHBOOT; leave room for EFI and reports.
test "$(( $(stat -c %s "$root$kernel") + $(stat -c %s "$root$initrd") ))" -lt 470000000
cp "$root$kernel" "$project/dist/usb/flashos/vmlinuz"
cp "$root$initrd" "$project/dist/usb/flashos/initrd"
cp "$project/dist/usb/flashos/vmlinuz" "$project/dist/usb/flashos/initrd" "$project/dist/boot/flashos/"
cp "$root/usr/lib/shim/shimx64.efi.signed" "$project/dist/boot/flashos/efi/BOOTX64.EFI"
cp "$root/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed" "$project/dist/boot/flashos/efi/grubx64.efi"
cp "$root/usr/lib/shim/mmx64.efi.signed" "$project/dist/boot/flashos/efi/mmx64.efi"
cp "$project/boot/debian-grub.cfg" "$project/dist/boot/flashos/efi/grub.cfg"
cp "$project/boot/debian-grub.cfg" "$project/dist/boot/EFI/debian/grub.cfg"
cp "$project/boot/ventoy_grub.cfg" "$project/dist/usb/ventoy/ventoy_grub.cfg"
cp "$project/boot/ventoy.json" "$project/dist/usb/ventoy/ventoy.json"
python3 "$project/tools/package-public-docs.py"
printf 'FlashOS 2026.10.13.2\n' > "$project/dist/usb/flashos/flashos.marker"
cp "$project/dist/usb/flashos/flashos.marker" "$project/dist/boot/flashos/"
chroot "$root" dpkg-query -W > "$project/dist/packages.tsv"
mksquashfs "$root" "$project/dist/usb/flashos/filesystem.squashfs" -noappend -comp xz -b 1M -processors 4 -wildcards -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' 'var/cache/apt/archives/*' 'var/lib/apt/lists/*'
python3 "$project/tools/prepare-boot.py"
python3 "$project/tools/verify-hardware-build.py" --kernel-version "$(basename "$initrd" | sed 's/^initrd.img-//')"
python3 "$project/tools/verify-app-store-build.py"
python3 "$project/tools/verify-toolkit-build.py"
cd "$project/dist"
find usb boot -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
