#!/bin/bash
# Populate only the prepared offline Debian root; hardware auto-loads via udev.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
project=$(cd -- "$(dirname -- "$0")/.." && pwd)
root=/opt/flashos-rootfs
test -f "$root/etc/debian_version"
test "$(tail -1 "$root/usr/sbin/policy-rc.d")" = 'exit 101'

# Keep backports opt-in: no dist-upgrade or third-party DKMS blacklists.
chroot "$root" apt-get install -y --no-install-recommends \
    firmware-linux-free firmware-sof-signed alsa-firmware-loaders alsa-topology-conf \
    firmware-ath9k-htc firmware-carl9170 firmware-zd1211 atmel-firmware \
    intel-microcode amd64-microcode iucode-tool wireless-regdb
chroot "$root" apt-get install -y --no-install-recommends -t trixie-backports \
    linux-image-amd64 firmware-intel-sound firmware-cirrus firmware-realtek \
    firmware-misc-nonfree firmware-amd-graphics firmware-intel-graphics \
    firmware-nvidia-graphics firmware-iwlwifi firmware-atheros firmware-mediatek \
    firmware-brcm80211 firmware-bnx2 firmware-bnx2x firmware-qlogic firmware-ast \
    firmware-intel-misc firmware-libertas firmware-cavium firmware-myricom \
    firmware-netronome firmware-netxen firmware-ti-connectivity
# NVK/Zink on newer NVIDIA requires the Vulkan driver as well as OpenGL.
chroot "$root" apt-get install -y --no-install-recommends -t trixie-backports \
    libgl1-mesa-dri libglx-mesa0 libegl-mesa0 libgbm1 mesa-vulkan-drivers

# Universal live media must carry BOTH CPU vendors, independent of builder CPU.
cat > "$root/etc/default/intel-microcode" <<'EOF'
IUCODE_TOOL_INITRAMFS=early
IUCODE_TOOL_SCANCPUS=no
EOF
cat > "$root/etc/default/amd64-microcode" <<'EOF'
AMD64UCODE_INITRAMFS=early
EOF
mkdir -p "$root/etc/initramfs-tools/conf.d"
printf 'MODULES=most\n' > "$root/etc/initramfs-tools/conf.d/flashos-hardware"
python3 "$project/build/install-sof.py"
chroot "$root" depmod -a "$(chroot "$root" readlink -f /vmlinuz | sed 's|/boot/vmlinuz-||')"
