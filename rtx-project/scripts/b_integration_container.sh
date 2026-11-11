#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update > /evidence/dependencies.log 2>&1
apt-get install -y --no-install-recommends build-essential gcc-x86-64-linux-gnu libc6-dev-amd64-cross binutils-x86-64-linux-gnu flex bison libssl-dev libelf-dev bc cpio python3 qemu-system-x86 >> /evidence/dependencies.log 2>&1
mkdir -p /work/src /work/out /work/root/dev
{ uname -a; x86_64-linux-gnu-gcc --version; qemu-system-x86_64 --version; } > /evidence/environment.txt
tar -xf /evidence/source.tar -C /work/src
python3 /evidence/harness/apply_overlay.py /work/src /evidence/harness /evidence
cd /work/src
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- x86_64_defconfig > /evidence/config.log 2>&1
scripts/config --file /work/out/.config -d MODULES -e TCP_RTX_DEBUG -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT -e SERIAL_8250_CONSOLE --set-str SYSTEM_TRUSTED_KEYS '' --set-str LOCALVERSION '-rtx-b-integration'
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig >> /evidence/config.log 2>&1
cp /work/out/.config /evidence/kernel.config
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 bzImage > /evidence/build.log 2>&1
cp /work/out/arch/x86/boot/bzImage /evidence/bzImage
x86_64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror /evidence/smoke.c -o /work/root/init
cd /work/root
mknod dev/console c 5 1
mknod dev/null c 1 3
find . -print0 | cpio --null -o -H newc > /evidence/initramfs.cpio 2>/evidence/cpio.log
cd /evidence
sha256sum bzImage initramfs.cpio kernel.config smoke.c build.sh runner.sh source.tar instrumentation.patch harness/* > SHA256SUMS
timeout 180 qemu-system-x86_64 -accel tcg -m 512 -smp 1 -kernel bzImage -initrd initramfs.cpio -append 'console=ttyS0 panic=1 oops=panic rdinit=/init' -nographic -no-reboot -nic none > serial.log 2>&1
python3 harness/check_results.py serial.log cases.json > summary.json
printf 'PASS\n' > result.txt
