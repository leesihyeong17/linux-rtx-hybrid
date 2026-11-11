#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Build committed kernel sources and boot an isolated x86 QEMU guest in Docker.
set -euo pipefail
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
mkdir -p "$repo/rtx-project/.work"
output=$(mktemp -d "$repo/rtx-project/.work/b-runtime-XXXXXXXX")
name="rtx-b-runtime-$(date +%s)-$$"
image=${RTX_BUILD_IMAGE:-ubuntu:22.04}
reuse=
mode=smoke
while [[ $# -gt 0 ]]; do
    case "$1" in
        --packets) mode=packets; shift ;;
        --kernel-results)
            [[ $# -ge 2 ]] || { echo 'Missing results directory' >&2; exit 2; }
            reuse=$(cd "$2" && pwd); shift 2 ;;
        *) echo 'Usage: run_b_runtime.sh [--packets] [--kernel-results DIR]' >&2; exit 2 ;;
    esac
done
if [[ -n $reuse ]]; then
    (cd "$reuse" && shasum -a 256 -c SHA256SUMS) > "$output/reuse-checksums.log"
    built_sha=$(cat "$reuse/source-sha.txt")
    git -C "$repo" diff --exit-code "$built_sha" HEAD -- . ':!rtx-project' > "$output/kernel-source-diff.txt"
    if [[ $mode == packets ]]; then
        grep -qx 'CONFIG_KPROBE_EVENTS=y' "$reuse/kernel.config"
        grep -qx 'CONFIG_TUN=y' "$reuse/kernel.config"
    fi
fi
cleanup() {
    rc=$?
    trap - EXIT
    docker rm -f "$name" > "$output/cleanup.log" 2>&1 || true
    docker ps -a --filter "name=^/${name}$" --format '{{.Names}}' > "$output/remaining-containers.txt"
    if [[ -s $output/remaining-containers.txt ]]; then rc=1; fi
    printf '%s\n' "$rc" > "$output/exit-code.txt"
    printf 'Results: %s\n' "$output"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker image inspect "$image" > "$output/image.json"
git -C "$repo" rev-parse HEAD > "$output/source-sha.txt"
git -C "$repo" archive HEAD > "$output/source.tar"
if [[ -n $reuse ]]; then
    cp "$reuse/bzImage" "$reuse/kernel.config" "$output/"
    cp "$reuse/source-sha.txt" "$output/kernel-source-sha.txt"
fi
cp "$repo/rtx-project/tests/runtime/smoke.c" "$output/smoke.c"
cp "$repo/rtx-project/scripts/b_runtime_container.sh" "$output/build.sh"
cp "$0" "$output/runner.sh"
printf '%s\n' "$mode" > "$output/mode.txt"
if [[ $mode == packets ]]; then
    cp "$repo/rtx-project/tests/runtime/packet_peer.c" "$output/"
    cp "$repo/rtx-project/tests/runtime/check_packet_trace.py" "$output/"
fi
docker run --name "$name" --mount "type=bind,source=$output,target=/evidence" "$image" bash /evidence/build.sh
