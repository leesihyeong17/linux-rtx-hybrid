#!/bin/bash
# Inputs are a clone of the kernel repository and an empty evidence directory.
set -euo pipefail
src=$1
dest=$2
recipe=$(cd "$(dirname "$0")" && pwd)
main=f78f5626ab0c307050f25b50606a61a104de6dac
fixture=0df8dcd98ac9a4affe7772a1a2b7c759da32d2ba
cd "$src"
if [[ -n $(git status --porcelain) ]]; then
    echo 'build.sh requires a clean disposable clone' >&2
    exit 1
fi
mkdir -p "$dest" /out16
makek() { make -j6 -s O=/out16 ARCH=arm64 "$@"; }
shift 2
if [[ $# -eq 0 ]]; then
    set -- v3 v4 v0 v1 rb-basic rb-cache v4-k4 v4-stats v3-stats v3-micro v4-micro v3-batch-micro v4-batch-micro
fi
for name in "$@"; do
    case "$name" in
        v0) revision=991defcc56cad49cfb221f44f5d161c9ea920c4a ;;
        v1) revision=231db9ed6a204d6407d88954da60ce7db1c2dabd ;;
        *) revision=$main ;;
    esac
    dir="$dest/$name"
    mkdir -p "$dir"
    git ls-files -- include/net/tcp_rtx.h net/ipv4/tcp_rtx_stats.c | xargs -r git restore --source=HEAD --
    git restore --source=HEAD -- net/ipv4/tcp_output.c
    git checkout -q --detach "$revision"
    if [[ "$name" == *-stats ]]; then git apply --unidiff-zero "$recipe/occupancy.patch"; fi
    if [[ "$name" == *-micro ]]; then
        h="$dir/harness"
        mkdir -p "$h"
        for file in benchmark.c hooks.h apply_overlay.py; do git show "$fixture:rtx-project/tests/benchmark/$file" > "$h/$file"; done
        python3 "$h/apply_overlay.py" "$src" "$h" "$dir" --mode performance
        cat "$recipe/lookup-unlink.c" >> net/ipv4/tcp_output.c
        if [[ $name == *-batch-micro ]]; then cat "$recipe/batch-append.c" >> net/ipv4/tcp_output.c; fi
    fi
    cp "$recipe/mini.config" "$dir/requested.config"
    case "$name" in v3*|v4*) echo 'CONFIG_TCP_RTX_CACHE=y' >> "$dir/requested.config"; echo 'CONFIG_TCP_RTX_TAIL=y' >> "$dir/requested.config";; esac
    if [[ $name == rb-cache ]]; then echo 'CONFIG_TCP_RTX_CACHE=y' >> "$dir/requested.config"; fi
    case "$name" in v4*) echo 'CONFIG_TCP_RTX_HYBRID=y' >> "$dir/requested.config"; echo 'CONFIG_TCP_RTX_RECENT_MAX=16' >> "$dir/requested.config";; esac
    if [[ $name == v4-k4 ]]; then sed -i 's/MAX=16/MAX=4/' "$dir/requested.config"; fi
    case "$name" in *-stats) echo 'CONFIG_TCP_RTX_STATS=y' >> "$dir/requested.config";; esac
    KCONFIG_ALLCONFIG="$dir/requested.config" makek allnoconfig >"$dir/build.log" 2>&1
    cp /out16/.config "$dir/kernel.config"
    for opt in NET_NS VETH NET_SCH_NETEM VIRTIO_CONSOLE VIRTIO_PCI PCI_HOST_GENERIC BLK_DEV_INITRD; do grep -qx "CONFIG_$opt=y" "$dir/kernel.config"; done
    for opt in CACHE TAIL HYBRID STATS DEBUG; do
        symbol=CONFIG_TCP_RTX_$opt=y
        if grep -qx "$symbol" "$dir/requested.config"; then grep -qx "$symbol" "$dir/kernel.config"; else ! grep -qx "$symbol" "$dir/kernel.config" || exit 1; fi
    done
    makek Image >>"$dir/build.log" 2>&1
    cp /out16/arch/arm64/boot/Image "$dir/Image"
    git rev-parse HEAD > "$dir/revision"
    sha256sum "$dir/Image" "$dir/kernel.config" > "$dir/hashes.txt"
    echo "built $name"
done
