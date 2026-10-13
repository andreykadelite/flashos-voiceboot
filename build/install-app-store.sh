#!/bin/bash
# Build a portable Debian application runtime, kept separate from the live root.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
project=$(cd -- "$(dirname -- "$0")/.." && pwd)
runtime=/opt/flashos-app-runtime
live=/opt/flashos-rootfs
if [ ! -f "$runtime/etc/debian_version" ]; then
    debootstrap --variant=minbase --arch=amd64 trixie "$runtime" https://deb.debian.org/debian
fi
printf '#!/bin/sh\nexit 101\n' > "$runtime/usr/sbin/policy-rc.d"
chmod 755 "$runtime/usr/sbin/policy-rc.d"
rm -f "$runtime/etc/resolv.conf"
cp /etc/resolv.conf "$runtime/etc/resolv.conf"
cat > "$runtime/etc/apt/sources.list" <<'EOF'
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main contrib non-free non-free-firmware
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie-updates main contrib non-free non-free-firmware
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://security.debian.org/debian-security trixie-security main contrib non-free non-free-firmware
deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie-backports main contrib non-free non-free-firmware
EOF
mkdir -p "$runtime/etc/apt/apt.conf.d"
cat > "$runtime/etc/apt/apt.conf.d/90-flashos-store" <<'EOF'
APT::Install-Recommends "false";
APT::Install-Suggests "false";
APT::Get::AllowUnauthenticated "false";
Acquire::AllowInsecureRepositories "false";
Acquire::Retries "2";
Acquire::https::Timeout "30";
Acquire::http::Timeout "30";
Dpkg::Options { "--force-confdef"; "--force-confold"; };
APT::Get::AutomaticRemove "false";
EOF
mountpoint -q "$runtime/proc" || mount -t proc proc "$runtime/proc"
mountpoint -q "$runtime/dev" || mount --rbind /dev "$runtime/dev"
mount --make-rslave "$runtime/dev"
trap 'umount -R "$runtime/proc" "$runtime/dev" 2>/dev/null || true' EXIT
chroot "$runtime" apt-get update
chroot "$runtime" apt-get install -y --no-install-recommends \
    apt python3 python3-apt ca-certificates debian-archive-keyring locales libglib2.0-bin \
    dbus-x11 openbox xterm gnome-terminal orca at-spi2-core speech-dispatcher speech-dispatcher-espeak-ng espeak-ng \
    pipewire-pulse pipewire-alsa libasound2-plugins pulseaudio-utils \
    fonts-dejavu-core x11-utils xauth x11-xkb-utils x11-xserver-utils
chroot "$runtime" getent passwd flashos-app >/dev/null || \
    chroot "$runtime" useradd -u 1000 -m -s /bin/bash flashos-app
chroot "$runtime" passwd -l flashos-app
printf 'ru_RU.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n' > "$runtime/etc/locale.gen"
chroot "$runtime" locale-gen
printf 'LANG=ru_RU.UTF-8\n' > "$runtime/etc/default/locale"
printf '{"format":1,"suite":"trixie","role":"applications"}\n' > "$runtime/etc/flashos-app-root"
mkdir -p "$runtime/opt/flashos-store" "$runtime/var/lib/flashos-store"
python3 "$project/tools/provision-shared-speech.py" --live "$live" --runtime "$runtime"
python3 - "$runtime" <<'PY'
from pathlib import Path
import json,subprocess,sys
root=Path(sys.argv[1])
packages=subprocess.check_output(["chroot",str(root),"dpkg-query","-W","-f=$"+"{binary:Package}\n"],text=True).splitlines()
(root/"var/lib/flashos-store/base-packages.json").write_text(json.dumps({"format":1,"packages":sorted(packages)},indent=2)+"\n")
PY
# Reuse the same guarded, atomic seed packer for initial and incremental builds.
python3 "$project/tools/repack-app-runtime.py" --project "$project" --runtime "$runtime" --live "$live"
# The unprivileged application UID must match between host X11 and the app root.
chroot "$live" getent passwd flashos-app >/dev/null || \
    chroot "$live" useradd -u 1000 -m -s /usr/sbin/nologin flashos-app
chroot "$live" usermod -aG audio,video,render,pipewire flashos-app
chroot "$live" apt-get install -y --no-install-recommends x11-xserver-utils
