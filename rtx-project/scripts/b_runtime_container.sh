#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update > /evidence/dependencies.log 2>&1
apt-get install -y --no-install-recommends build-essential gcc-x86-64-linux-gnu libc6-dev-amd64-cross binutils-x86-64-linux-gnu flex bison libssl-dev libelf-dev bc cpio python3 qemu-system-x86 >> /evidence/dependencies.log 2>&1
mkdir -p /work/src /work/out /work/root/dev
{ uname -a; x86_64-linux-gnu-gcc --version; qemu-system-x86_64 --version; } > /evidence/environment.txt
if [[ ! -f /evidence/bzImage ]]; then
tar -xf /evidence/source.tar -C /work/src
cd /work/src
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- x86_64_defconfig > /evidence/config.log 2>&1
scripts/config --file /work/out/.config -d MODULES -e TCP_RTX_DEBUG -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT -e SERIAL_8250_CONSOLE -e DEBUG_FS -e FTRACE -e FUNCTION_TRACER -e TUN --set-str SYSTEM_TRUSTED_KEYS '' --set-str LOCALVERSION '-rtx-b-runtime'
if [[ $(cat /evidence/mode.txt) == packets ]]; then
    scripts/config --file /work/out/.config -e MODULES -e KPROBES -e KPROBE_EVENTS
fi
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig >> /evidence/config.log 2>&1
cp /work/out/.config /evidence/kernel.config
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 bzImage > /evidence/build.log 2>&1
cp /work/out/arch/x86/boot/bzImage /evidence/bzImage
fi
sources=(/evidence/smoke.c)
flags=()
if [[ $(cat /evidence/mode.txt) == packets ]]; then
    grep -qx CONFIG_KPROBE_EVENTS=y /evidence/kernel.config
    sources+=(/evidence/packet_peer.c)
    flags+=(-DRTX_PACKET_CASES)
fi
x86_64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror "${flags[@]}" "${sources[@]}" -o /work/root/init
cd /work/root
mknod dev/console c 5 1
mknod dev/null c 1 3
find . -print0 | cpio --null -o -H newc > /evidence/initramfs.cpio 2> /evidence/cpio.log
cd /evidence
sha256sum bzImage initramfs.cpio kernel.config smoke.c build.sh runner.sh source.tar mode.txt > SHA256SUMS
if [[ $(cat mode.txt) == packets ]]; then
    sha256sum packet_peer.c check_packet_trace.py >> SHA256SUMS
fi
timeout 180 qemu-system-x86_64 -accel tcg -m 512 -smp 1 -kernel bzImage -initrd initramfs.cpio -append 'console=ttyS0 panic=1 oops=panic rdinit=/init' -nographic -no-reboot -nic none > serial.log 2>&1
grep -q '^RTX_RUNTIME PASS' serial.log
grep -q '^RTX_KERNEL 5.4.214-rtx-b-runtime' serial.log
test "$(grep -c '^RTX_CASE .* PASS' serial.log)" -eq 4
grep -Eq 'rtx_selftest: [1-9][0-9]* passed, 0 failed' serial.log
if grep -Eq 'WARNING:|BUG:|Oops:|Kernel panic|RTX_RUNTIME FAIL|RTX_RUNTIME TIMEOUT' serial.log; then exit 1; fi
if [[ $(cat mode.txt) == packets ]]; then
    python3 check_packet_trace.py serial.log > trace-summary.txt
fi
printf 'PASS\n' > result.txt
