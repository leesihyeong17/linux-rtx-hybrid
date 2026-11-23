#!/bin/bash
# DSACK_OFO 재현: tinyconfig x86_64 커널(QEMU TCG)에서 D의 packet_peer를 N회 반복.
# 사용: run.sh NAME ITER [scripts/config 인자...]
#   예: run.sh rb 30            (RB 기본형)
#       run.sh hyb16 30 -e TCP_RTX_HYBRID --set-val TCP_RTX_RECENT_MAX 16
set -euo pipefail
NAME=$1; ITER=$2; shift 2
SRC=${SRC:?kernel source tree (merge 78ddfe8 / main f78f562)}
OUT=${WORK:-/tmp/dsack}/out-$NAME
HERE=${HERE:-$(cd "$(dirname "$0")" && pwd)}
CC="ccache gcc"
mkdir -p "$OUT"
make -s -C "$SRC" O="$OUT" CC="$CC" tinyconfig
cfg() { "$SRC/scripts/config" --file "$OUT/.config" "$@"; }
cfg -e 64BIT -e PRINTK -e TTY -e SERIAL_8250 -e SERIAL_8250_CONSOLE \
    -e BLK_DEV_INITRD -e BINFMT_ELF -e BINFMT_SCRIPT -e PROC_FS -e SYSFS \
    -e DEVTMPFS -e DEVTMPFS_MOUNT -e NET -e INET -e UNIX -e FUTEX \
    -e MULTIUSER -e SYSCTL -e PROC_SYSCTL -e NAMESPACES -e NET_NS \
    -e NETDEVICES -e NET_CORE -e TUN -e IPV6 \
    ${RTX_DBG:--e TCP_RTX_DEBUG -e TCP_RTX_STATS} "$@"
make -s -C "$SRC" O="$OUT" CC="$CC" olddefconfig
make -s -C "$SRC" O="$OUT" CC="$CC" -j"$(nproc)" bzImage 2>"$OUT/build.err" || { tail -20 "$OUT/build.err"; exit 1; }

IR=$OUT/initramfs
rm -rf "$IR"; mkdir -p "$IR"/{bin,proc,sys,dev,tmp}
cp "$(command -v busybox)" "$IR/bin/busybox"
for a in sh dmesg mount cat grep sleep echo unshare poweroff seq; do ln -sf busybox "$IR/bin/$a"; done
cp "${PEER:-$HERE/peer}" "$IR/bin/peer"
cat > "$IR/init" <<EOF
#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev 2>/dev/null
echo "DS uname \$(cat /proc/sys/kernel/osrelease)"
dmesg | grep rtx_selftest
for i in \$(seq 1 $ITER); do
	echo "DS iter \$i"
	unshare -n /bin/peer 2>&1 | grep -E "RTX_ACCOUNT|RTX_EXTRA|RTX_LATER|RTX_TIMING|RTX_ARRIVAL|FAIL|TIMEOUT|PACKETS_COMPLETE"
done
echo "DS done"
dmesg | grep -E "RACKDBG|RTX_MARK"
dmesg | grep -E "WARNING|BUG|Oops" || true
poweroff -f
EOF
chmod +x "$IR/init"
(cd "$IR" && find . | cpio -o -H newc --quiet) > "$OUT/initramfs.cpio"
grep -E "^(# )?CONFIG_TCP_RTX" "$OUT/.config" > "$OUT/rtx-config.txt"
qemu-system-x86_64 -m 512 -nographic -no-reboot \
	-kernel "$OUT/arch/x86/boot/bzImage" -initrd "$OUT/initramfs.cpio" \
	-append "console=ttyS0 quiet" > "$OUT/qemu.log" 2>&1 &
QP=$!
for _ in $(seq 1 1500); do
	grep -aq "reboot: System halted\|Kernel panic" "$OUT/qemu.log" && break
	kill -0 $QP 2>/dev/null || break
	sleep 2
done
kill $QP 2>/dev/null || true; wait $QP 2>/dev/null || true
grep -a -E "^DS|RTX_|rtx_selftest" "$OUT/qemu.log" | tr -d '\r' > "$OUT/result.txt"
echo "== $NAME: $(cat "$OUT/rtx-config.txt" | tr '\n' ' ')"
grep "DSACK_OFO" "$OUT/result.txt" | grep -o "lost=[0-9]* retrans=[0-9]*" | sort | uniq -c
grep -c PACKETS_COMPLETE "$OUT/result.txt" || true
