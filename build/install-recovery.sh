#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
root=/opt/flashos-rootfs
test -f "$root/etc/debian_version"
test "$(tail -1 "$root/usr/sbin/policy-rc.d")" = 'exit 101'
rm -f "$root/etc/resolv.conf"
cp /etc/resolv.conf "$root/etc/resolv.conf"
chroot "$root" apt-get update
chroot "$root" apt-get install -y --no-install-recommends partclone testdisk hwinfo mtr-tiny \
    python3-tk xserver-xorg-core xserver-xorg-input-libinput xserver-xorg-video-fbdev \
    xserver-xorg-video-vesa xinit x11-xserver-utils fonts-dejavu-core wireless-regdb \
    firmware-ath9k-htc firmware-carl9170 firmware-zd1211
chroot "$root" apt-get install -y --no-install-recommends -t trixie-backports \
    firmware-ast firmware-intel-misc firmware-libertas firmware-cavium \
    firmware-myricom firmware-netronome firmware-netxen firmware-ti-connectivity
chroot "$root" apt-get clean
