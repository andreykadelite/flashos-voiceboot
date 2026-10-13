#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends debootstrap ca-certificates squashfs-tools kmod initramfs-tools-core qemu-system-x86 ovmf mtools dosfstools exfatprogs python3 grub-common xz-utils curl sbsigntool rsync git build-essential scons pkg-config libglib2.0-dev libao-dev libspeechd-dev
