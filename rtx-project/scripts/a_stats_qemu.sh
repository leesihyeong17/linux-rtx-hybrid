#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# a_stats_qemu.sh - check /proc/rtx_stats (STATS v2) in a minimal x86_64
# kernel under QEMU: boot self test, port filter, histogram sums.
#
# Usage: rtx-project/scripts/a_stats_qemu.sh [-o LOG] [-k K] [--rb]
#   -o LOG   also write the log there (evidence file)
#   -k K     TCP_RTX_RECENT_MAX for the hybrid build (default 16)
#   --rb     build the RB backend instead of the hybrid
# Env: CC (default "ccache gcc"), OUT (build dir, default /tmp/a_stats_qemu),
#      KEEP=1 keeps the build dir for faster reruns.
# Needs: gcc, flex, bison, bc, libelf-dev, libssl-dev, qemu-system-x86,
#        busybox-static, cpio.
#
# Correctness evidence only (QEMU TCG, loopback, no netem): do not use the
# numbers as performance data.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
LOG=""; K=16; HYB=1
while [ $# -gt 0 ]; do
	case $1 in
	-o) LOG=$(realpath -m "$2"); shift 2 ;;
	-k) K=$2; shift 2 ;;
	--rb) HYB=0; shift ;;
	*) echo "unknown option $1" >&2; exit 2 ;;
	esac
done
CC=${CC:-ccache gcc}
OUT=$(realpath -m "${OUT:-/tmp/a_stats_qemu}")
[ "${KEEP:-0}" = 1 ] || rm -rf "$OUT"
mkdir -p "$OUT/initramfs"

# ---- kernel: tinyconfig + what the test needs ----
cd "$ROOT"
make -s O="$OUT" CC="$CC" tinyconfig
cfg() { scripts/config --file "$OUT/.config" "$@"; }
cfg -e 64BIT -e PRINTK -e TTY -e SERIAL_8250 -e SERIAL_8250_CONSOLE \
    -e BLK_DEV_INITRD -e BINFMT_ELF -e BINFMT_SCRIPT -e PROC_FS -e SYSFS \
    -e DEVTMPFS -e DEVTMPFS_MOUNT -e NET -e INET -e UNIX -e FUTEX \
    -e MULTIUSER -e TCP_RTX_DEBUG -e TCP_RTX_STATS
if [ "$HYB" = 1 ]; then
	cfg -e TCP_RTX_HYBRID --set-val TCP_RTX_RECENT_MAX "$K"
fi
make -s O="$OUT" CC="$CC" olddefconfig
make -s O="$OUT" CC="$CC" -j"$(nproc)" bzImage

# ---- initramfs: busybox + test script ----
IR=$OUT/initramfs
mkdir -p "$IR"/{bin,proc,sys,dev,tmp}
cp "$(command -v busybox)" "$IR/bin/busybox"
for a in sh dmesg mount cat grep awk sleep head echo ifconfig reboot; do
	ln -sf busybox "$IR/bin/$a"
done
# busybox nc stopped after ~5KB in earlier runs: use a tiny sender/receiver
cat > "$OUT/xfer.c" <<'EOF_C'
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
/* xfer PORT        receive one connection, print bytes
 * xfer PORT MB     send MB megabytes to 127.0.0.1:PORT */
int main(int argc, char **argv)
{
	struct sockaddr_in a = { .sin_family = AF_INET };
	static char buf[65536];
	long long total = 0;
	int s = socket(AF_INET, SOCK_STREAM, 0), one = 1;
	ssize_t n;

	a.sin_port = htons(atoi(argv[1]));
	a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	if (argc == 2) {
		int c;

		setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
		if (bind(s, (void *)&a, sizeof(a)) || listen(s, 1))
			return perror("listen"), 1;
		c = accept(s, NULL, NULL);
		while ((n = read(c, buf, sizeof(buf))) > 0)
			total += n;
		printf("A_STATS recv_bytes %lld\n", total);
		return 0;
	}
	if (connect(s, (void *)&a, sizeof(a)))
		return perror("connect"), 1;
	for (long long left = atoll(argv[2]) << 20; left > 0; left -= n)
		if ((n = write(s, buf, left < sizeof(buf) ? left : sizeof(buf))) <= 0)
			return perror("write"), 1;
	close(s);
	return 0;
}
EOF_C
gcc -static -O2 -o "$IR/bin/xfer" "$OUT/xfer.c"
cat > "$IR/init" <<'EOF'
#!/bin/sh
mount -t proc proc /proc
mount -t sysfs sys /sys
mount -t devtmpfs dev /dev 2>/dev/null
echo "A_STATS uname $(cat /proc/sys/kernel/osrelease)"
dmesg | grep "rtx_selftest"

ifconfig lo 127.0.0.1 up

# send MB megabytes over loopback to port P
run() {
	xfer "$2" &
	sleep 1
	xfer "$2" "$1"
	wait
}
dump() { awk -v t="$1" '{print "A_STATS " t " " $0}' /proc/rtx_stats; }

# 1) filter on 5001, traffic on 5002 -> nothing counted
echo "port 5001" > /proc/rtx_stats; echo 0 > /proc/rtx_stats
run 20 5002
dump other

# 2) filter on 5001, traffic on 5001 -> counted
echo 0 > /proc/rtx_stats
run 50 5001
dump match

# 3) bad input is rejected, filter cleared
echo "port x" > /proc/rtx_stats 2>/dev/null && echo "A_STATS badinput accepted" \
	|| echo "A_STATS badinput rejected"
echo "port 0" > /proc/rtx_stats
echo "A_STATS final_filter $(head -1 /proc/rtx_stats)"
echo "A_STATS done"
reboot -f    # with -no-reboot, QEMU exits (tinyconfig has no ACPI poweroff)
EOF
chmod +x "$IR/init"
(cd "$IR" && find . | cpio -o -H newc --quiet) > "$OUT/initramfs.cpio"

# ---- run ----
RAW=$OUT/qemu.log
timeout 600 qemu-system-x86_64 -m 512 -nographic -no-reboot \
	-kernel "$OUT/arch/x86/boot/bzImage" -initrd "$OUT/initramfs.cpio" \
	-append "console=ttyS0 panic=-1 loglevel=4" > "$RAW" 2>&1 || true

# ---- check ----
python3 - "$RAW" "$HYB" "$K" <<'PY' | tee "$OUT/result.txt"
import re, sys
t = open(sys.argv[1], errors='replace').read()
hyb, K = sys.argv[2] == '1', int(sys.argv[3])

def stats(tag):
    c, h = {}, {'n_link': {}, 'n_ack': {}}
    for line in re.findall(r'^A_STATS %s (.+)$' % tag, t, re.M):
        f = line.split()
        if len(f) == 3:
            h[f[0]][f[1]] = int(f[2])
        else:
            c[f[0]] = int(f[1])
    return c, h

ok = True
def check(cond, what):
    global ok
    ok &= bool(cond)
    print('%s %s' % ('PASS' if cond else 'FAIL', what))

m = re.search(r'rtx_selftest: (\d+) passed, (\d+) failed', t)
check(m and m.group(2) == '0', 'boot self test: %s' % (m.group(0) if m else 'missing'))
oc, oh = stats('other')
check(oc and all(v == 0 for k, v in oc.items() if k != 'filter_port') and
      not any(sum(x.values()) for x in oh.values()),
      'filter 5001, traffic 5002: all counters and histograms 0')
mc, mh = stats('match')
check(mc.get('filter_port') == 5001, 'filter_port shows 5001')
ins = mc['insert_append'] + mc['insert_ordered'] + mc['insert_split']
check(ins > 0 and sum(mh['n_link'].values()) == ins,
      'sum(n_link) %d == inserts %d' % (sum(mh['n_link'].values()), ins))
check(mc['ack_clean'] > 0 and sum(mh['n_ack'].values()) == mc['ack_clean'],
      'sum(n_ack) %d == ack_clean %d' % (sum(mh['n_ack'].values()), mc['ack_clean']))
check(len(mh['n_link']) == 74 and len(mh['n_ack']) == 74, 'bucket count 74 each')
if hyb:
    print('INFO unlink_list %d promote %d -> removed from L before promotion %.3f'
          % (mc['unlink_list'], mc['promote'],
             mc['unlink_list'] / max(1, mc['unlink_list'] + mc['promote'])))
le = sum(v for k, v in mh['n_ack'].items() if '-' not in k and int(k) <= K)
print('INFO n_ack N<=%d share %.3f (loopback, not a real distribution)'
      % (K, le / max(1, sum(mh['n_ack'].values()))))
check('A_STATS badinput rejected' in t, 'bad write rejected with error')
check('A_STATS final_filter filter_port 0' in t, 'port 0 clears filter')
check('A_STATS recv_bytes 52428800' in t and 'A_STATS recv_bytes 20971520' in t,
      'transfers complete (20MB on 5002, 50MB on 5001)')
check(not re.search(r'WARNING:|BUG:|Oops:|Kernel panic', t), 'no WARNING/BUG/Oops')
print('RESULT', 'PASS' if ok else 'FAIL')
PY

if [ -n "$LOG" ]; then
	{
		echo "# a_stats_qemu.sh $(date +%F)"
		echo "# machine: $(uname -m) QEMU TCG (Claude cloud), compiler: $($CC --version | head -1)"
		echo "# commit: $(git rev-parse --short=7 HEAD)$(git diff --quiet HEAD -- include net || echo ' +dirty')"
		echo "# config: tinyconfig + TCP_RTX_DEBUG=y TCP_RTX_STATS=y" \
		     "$([ "$HYB" = 1 ] && echo "TCP_RTX_HYBRID=y RECENT_MAX=$K" || echo "RB backend")"
		echo
		cat "$OUT/result.txt"
		echo
		echo "## raw (A_STATS / rtx_selftest lines)"
		grep -E "A_STATS|rtx_selftest" "$RAW" | tr -d '\r'
	} > "$LOG"
fi
grep -q '^RESULT PASS' "$OUT/result.txt"
