#!/usr/bin/env bash
# Build the rtx model harness against the real kernel sources.
#
#   build.sh [-H tcp_rtx.h] [-r REPO] OUT_DIR
#
# Copies include/net/tcp_rtx.h, include/linux/{rbtree,rbtree_augmented,list}.h
# and lib/rbtree.c from REPO (default: this checkout) unchanged, extracts
# tcp_rbtree_insert() from net/ipv4/tcp_input.c, and builds one binary per
# configuration:
#   rb            RB backend, DEBUG on, STATS on
#   rb-plain      RB backend, DEBUG off, STATS off
#   hyb-kN        HYBRID with CONFIG_TCP_RTX_RECENT_MAX=N, DEBUG on, STATS on
#   hyb-kN-plain  HYBRID, DEBUG off, STATS off
# If tcp_rtx.h knows CONFIG_TCP_RTX_CACHE (PR #25), also:
#   rb-cache      RB + CACHE (V2), DEBUG/STATS on
#   rb-tail       RB + CACHE + TAIL (V3), DEBUG/STATS on
#   hyb-kN-tail   HYBRID + CACHE + TAIL (V4), DEBUG/STATS on
# K values: $RTX_MODEL_KS (default "1 2 4 16"); plain builds for $RTX_MODEL_PLAIN_KS
# (default "4 16"). -H replaces tcp_rtx.h (used for mutation checks).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
header=""
while getopts "H:r:" opt; do
	case $opt in
	H) header=$OPTARG ;;
	r) repo=$(cd "$OPTARG" && pwd) ;;
	*) exit 1 ;;
	esac
done
shift $((OPTIND - 1))
out=${1:?usage: build.sh [-H tcp_rtx.h] [-r REPO] OUT_DIR}
header=${header:-$repo/include/net/tcp_rtx.h}
ks=${RTX_MODEL_KS:-"1 2 4 16"}
plain_ks=${RTX_MODEL_PLAIN_KS:-"4 16"}
cc=${CC:-gcc}

mkdir -p "$out/kinc/linux" "$out/kinc/net" "$out/src"
out=$(cd "$out" && pwd)
cp "$repo/include/linux/rbtree.h" "$repo/include/linux/rbtree_augmented.h" \
   "$repo/include/linux/list.h" "$out/kinc/linux/"
cp "$header" "$out/kinc/net/tcp_rtx.h"
cp "$repo/lib/rbtree.c" "$out/src/rbtree.c"
{
	echo '#include <net/tcp.h>'
	awk '/^void tcp_rbtree_insert\(struct rb_root \*root, struct sk_buff \*skb\)$/ {p=1}
	     p {print}
	     p && /^}$/ {exit}' "$repo/net/ipv4/tcp_input.c"
} > "$out/src/tcp_rbtree_insert.c"
grep -q rb_insert_color "$out/src/tcp_rbtree_insert.c" ||
	{ echo "tcp_rbtree_insert() not found in tcp_input.c" >&2; exit 1; }

cflags=(-std=gnu11 -O2 -g -Wall -Wextra -Wno-unused-parameter
	-Wno-sign-compare -Wno-missing-field-initializers -Werror
	-fno-strict-aliasing -DCONFIG_DEBUG_LIST
	-I "$here/shim" -I "$out/kinc")

# expected.conf records, per binary, the configuration this script asked
# for. rtx_model.py compares it with the binary's own CONF line, so a
# binary built (or renamed) with other options fails instead of being
# checked against its own claims.
: > "$out/expected.conf"
build() {
	local name=$1 expect=$2; shift 2
	"$cc" "${cflags[@]}" "$@" -o "$out/$name" "$here/harness.c" \
		"$out/src/rbtree.c" "$out/src/tcp_rbtree_insert.c"
	echo "$name $expect" >> "$out/expected.conf"
	echo "$name"
}

# PR #25 adds CONFIG_TCP_RTX_CACHE (selected by HYBRID) and
# CONFIG_TCP_RTX_TAIL (depends on CACHE). Mirror the Kconfig relations.
hyb=(-DCONFIG_TCP_RTX_HYBRID)
dbg=(-DCONFIG_TCP_RTX_DEBUG -DCONFIG_TCP_RTX_STATS)
has_cache=0
if grep -q CONFIG_TCP_RTX_CACHE "$header"; then
	has_cache=1
	hyb+=(-DCONFIG_TCP_RTX_CACHE)
fi

on="debug=1 stats=1"
off="debug=0 stats=0"
build rb "hybrid=0 k=0 $on cache=0 tail=0" "${dbg[@]}"
build rb-plain "hybrid=0 k=0 $off cache=0 tail=0"
if [ "$has_cache" = 1 ]; then
	build rb-cache "hybrid=0 k=0 $on cache=1 tail=0" \
		-DCONFIG_TCP_RTX_CACHE "${dbg[@]}"
	build rb-tail "hybrid=0 k=0 $on cache=1 tail=1" \
		-DCONFIG_TCP_RTX_CACHE -DCONFIG_TCP_RTX_TAIL "${dbg[@]}"
fi
for k in $ks; do
	build "hyb-k$k" "hybrid=1 k=$k $on cache=$has_cache tail=0" \
		"${hyb[@]}" -DCONFIG_TCP_RTX_RECENT_MAX="$k" "${dbg[@]}"
	if [ "$has_cache" = 1 ]; then
		build "hyb-k$k-tail" "hybrid=1 k=$k $on cache=1 tail=1" \
			"${hyb[@]}" -DCONFIG_TCP_RTX_TAIL \
			-DCONFIG_TCP_RTX_RECENT_MAX="$k" "${dbg[@]}"
	fi
done
for k in $plain_ks; do
	build "hyb-k$k-plain" "hybrid=1 k=$k $off cache=$has_cache tail=0" \
		"${hyb[@]}" -DCONFIG_TCP_RTX_RECENT_MAX="$k"
done
sha256sum "$out/kinc/net/tcp_rtx.h" "$out/kinc/linux/"*.h "$out/src/"*.c \
	| sed "s#$out/##" > "$out/inputs.sha256"
