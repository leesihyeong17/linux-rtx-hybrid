#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update > /evidence/dependencies.log 2>&1
apt-get install -y --no-install-recommends build-essential gcc-x86-64-linux-gnu libc6-dev-amd64-cross binutils-x86-64-linux-gnu flex bison libssl-dev libelf-dev bc cpio python3 qemu-system-x86 patch >> /evidence/dependencies.log 2>&1
python3 /evidence/harness/run_matrix.py --check-options
mkdir -p /work/src /work/root/dev /work/out /evidence/builds
{ uname -a; x86_64-linux-gnu-gcc --version; qemu-system-x86_64 --version; nproc; cat /proc/cpuinfo; } > /evidence/environment.txt
tar -xf /evidence/source.tar -C /work/src
cd /work/src
patch -p1 < /evidence/source.patch > /evidence/patch.log
cp net/ipv4/tcp_output.c /work/original-tcp_output.c
cp include/net/tcp_rtx.h /work/original-tcp_rtx.h
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- x86_64_defconfig > /evidence/base-config.log 2>&1
scripts/config --file /work/out/.config -e BLK_DEV_INITRD -e DEVTMPFS -e DEVTMPFS_MOUNT -e SERIAL_8250_CONSOLE -d TCP_RTX_DEBUG -d TCP_RTX_STATS --set-str SYSTEM_TRUSTED_KEYS '' --set-str LOCALVERSION '-rtx-b-bench'
make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig >> /evidence/base-config.log 2>&1
cp /work/out/.config /evidence/common.config
for variant in rb-basic rb-cache rb-tail hybrid-ordered hybrid-tail; do
    cp /evidence/common.config /work/out/.config
    scripts/config --file /work/out/.config -d TCP_RTX_HYBRID -d TCP_RTX_CACHE -d TCP_RTX_TAIL
    if [[ $variant != rb-basic ]]; then scripts/config --file /work/out/.config -e TCP_RTX_CACHE; fi
    if [[ $variant == *-tail ]]; then scripts/config --file /work/out/.config -e TCP_RTX_TAIL; fi
    if [[ $variant == hybrid-* ]]; then
        scripts/config --file /work/out/.config -e TCP_RTX_HYBRID --set-val TCP_RTX_RECENT_MAX "$RTX_BENCH_K"
    fi
    make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- olddefconfig > /evidence/config-current.log 2>&1
    for mode in performance observation; do
        dest=/evidence/builds/$variant-$mode
        mkdir -p "$dest"
        cp /work/original-tcp_output.c net/ipv4/tcp_output.c
        cp /work/original-tcp_rtx.h include/net/tcp_rtx.h
        python3 /evidence/harness/apply_overlay.py /work/src /evidence/harness "$dest" --mode "$mode"
        cp /work/out/.config "$dest/kernel.config"
        cp /evidence/config-current.log "$dest/config.log"
        scripts/diffconfig /evidence/common.config "$dest/kernel.config" > "$dest/config.diff"
        make O=/work/out ARCH=x86_64 CROSS_COMPILE=x86_64-linux-gnu- -j4 bzImage > "$dest/build.log" 2>&1
        cp /work/out/arch/x86/boot/bzImage "$dest/bzImage"
        printf 'BUILD %s %s PASS\n' "$variant" "$mode"
    done
done
x86_64-linux-gnu-gcc -static -O2 -Wall -Wextra -Werror /evidence/smoke.c -o /work/root/init
cd /work/root
mknod dev/console c 5 1
mknod dev/null c 1 3
find . -print0 | cpio --null -o -H newc > /evidence/initramfs.cpio 2>/evidence/cpio.log
python3 /evidence/harness/run_matrix.py /evidence
