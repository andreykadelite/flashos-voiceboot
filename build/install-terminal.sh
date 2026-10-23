#!/bin/bash
# A full live-system terminal, independent of the persistent application runtime.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
root=/opt/flashos-rootfs
test -f "$root/etc/debian_version"
test "$(tail -1 "$root/usr/sbin/policy-rc.d")" = 'exit 101'
test -x "$root/bin/bash"
chroot "$root" apt-get install -y --no-install-recommends \
    python3-gi gir1.2-gtk-3.0 gir1.2-vte-2.91 libatk-adaptor
