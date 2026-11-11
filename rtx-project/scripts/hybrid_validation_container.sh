#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update > /evidence/dependencies.log 2>&1
apt-get install -y --no-install-recommends build-essential gcc-x86-64-linux-gnu libc6-dev-amd64-cross binutils-x86-64-linux-gnu flex bison libssl-dev libelf-dev bc cpio python3 qemu-system-x86 patch >> /evidence/dependencies.log 2>&1
mkdir -p /work/src /work/root/dev
{ uname -a; x86_64-linux-gnu-gcc --version; qemu-system-x86_64 --version; } > /evidence/environment.txt
tar -xf /evidence/source.tar -C /work/src
cd /work/src
patch -p1 < /evidence/source.patch > /evidence/patch.log
for mode in rb-off rb-on hybrid-off hybrid-on; do
    out=/work/$mode
    mkdir -p "$out" /evidence/"$mode"
    make O="$out" ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- x86_64_defconfig > /evidence/"$mode"/config.log 2>&1
    scripts/config --file "$out/.config" -e MODULES -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT -e SERIAL_8250_CONSOLE -e DEBUG_FS -e FTRACE -e FUNCTION_TRACER -e TUN -e KPROBES -e KPROBE_EVENTS --set-str SYSTEM_TRUSTED_KEYS '' --set-str LOCALVERSION '-rtx-hybrid'
    if [[ $mode == hybrid-* ]]; then
        scripts/config --file "$out/.config" -e TCP_RTX_HYBRID --set-val TCP_RTX_RECENT_MAX 16
    else
        scripts/config --file "$out/.config" -d TCP_RTX_HYBRID
    fi
    if [[ $mode == *-on ]]; then
        scripts/config --file "$out/.config" -e TCP_RTX_DEBUG -e TCP_RTX_STATS
    else
        scripts/config --file "$out/.config" -d TCP_RTX_DEBUG -d TCP_RTX_STATS
    fi
    make O="$out" ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig >> /evidence/"$mode"/config.log 2>&1
    cp "$out/.config" /evidence/"$mode"/kernel.config
    make O="$out" ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 net/ipv4/ net/ipv6/ > /evidence/"$mode"/build.log 2>&1
    printf 'PASS\n' > /evidence/"$mode"/result.txt
    printf 'BUILD %s PASS\n' "$mode"
done
make O=/work/hybrid-on ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 bzImage > /evidence/kernel-build.log 2>&1
cp /work/hybrid-on/arch/x86/boot/bzImage /evidence/bzImage-packets
x86_64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror -DRTX_PACKET_CASES /evidence/smoke.c /evidence/packet_peer.c -o /work/root/init
cd /work/root
mknod dev/console c 5 1
mknod dev/null c 1 3
find . -print0 | cpio --null -o -H newc > /evidence/initramfs.cpio 2>/evidence/cpio.log
boot() {
    timeout 180 qemu-system-x86_64 -accel tcg -m 512 -smp 1 -kernel "$1" -initrd /evidence/initramfs.cpio -append 'console=ttyS0 panic=1 oops=panic rdinit=/init' -nographic -no-reboot -nic none > "$2" 2>&1
    grep -q '^RTX_RUNTIME PASS' "$2"
    grep -Eq 'rtx_selftest: [1-9][0-9]* passed, 0 failed' "$2"
    if grep -Eq 'WARNING:|BUG:|Oops:|Kernel panic|RTX_RUNTIME (FAIL|TIMEOUT)|rtx_selftest: FAIL' "$2"; then return 1; fi
}
boot /evidence/bzImage-packets /evidence/serial-packets.log
python3 /evidence/check_packet_trace.py /evidence/serial-packets.log > /evidence/trace-packets.txt
python3 /evidence/harness/apply_overlay.py /work/src /evidence/harness /evidence
cd /work/src
make O=/work/hybrid-on ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 bzImage > /evidence/fixture-build.log 2>&1
cp /work/hybrid-on/arch/x86/boot/bzImage /evidence/bzImage-fixtures
boot /evidence/bzImage-fixtures /evidence/serial.log
python3 /evidence/harness/check_results.py /evidence/serial.log /evidence/cases.json > /evidence/summary.json
python3 /evidence/check_packet_trace.py /evidence/serial.log > /evidence/trace-fixtures.txt
# Rebuild the same instrumented source at the smallest supported capacity.
cd /work/src
scripts/config --file /work/hybrid-on/.config --set-val TCP_RTX_RECENT_MAX 1
make O=/work/hybrid-on ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig > /evidence/k1-config.log 2>&1
cp /work/hybrid-on/.config /evidence/k1.config
make O=/work/hybrid-on ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 bzImage > /evidence/k1-build.log 2>&1
cp /work/hybrid-on/arch/x86/boot/bzImage /evidence/bzImage-k1
boot /evidence/bzImage-k1 /evidence/serial-k1.log
python3 /evidence/harness/check_results.py /evidence/serial-k1.log /evidence/cases.json > /evidence/summary-k1.json
python3 /evidence/check_packet_trace.py /evidence/serial-k1.log > /evidence/trace-k1.txt
cd /evidence
sha256sum bzImage-* initramfs.cpio source.tar source.patch serial*.log harness/* > SHA256SUMS
printf 'PASS\n' > result.txt
