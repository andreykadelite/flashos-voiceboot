#!/bin/bash
# Provision only the offline live rootfs, never the Windows host.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
root=/opt/flashos-rootfs
test -f "$root/etc/debian_version"
test "$(cat "$root/usr/sbin/policy-rc.d" | tail -1)" = 'exit 101'
rm -f "$root/etc/resolv.conf"
cp /etc/resolv.conf "$root/etc/resolv.conf"
chroot "$root" apt-get update
chroot "$root" apt-get install -y --no-install-recommends network-manager wpasupplicant rsync python3-cryptography
mkdir -p "$root/etc/NetworkManager/conf.d"
cat > "$root/etc/NetworkManager/conf.d/90-flashos.conf" <<'EOF'
[main]
no-auto-default=*
dns=default
[connectivity]
enabled=false
EOF
chroot "$root" systemctl enable NetworkManager.service
chroot "$root" systemctl disable NetworkManager-wait-online.service
chroot "$root" systemctl mask rsync.service
# NetworkManager connections stay in the RAM overlay. FlashOS stores its own
# validated portable profiles and encrypted credentials on FLASHBOOT separately.
chroot "$root" apt-get clean
