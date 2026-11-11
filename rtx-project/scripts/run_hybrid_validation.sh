#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
mkdir -p "$repo/rtx-project/.work"
out=$(mktemp -d "$repo/rtx-project/.work/hybrid-XXXXXXXX")
name="rtx-hybrid-$(date +%s)-$$"
image=${RTX_BUILD_IMAGE:-ubuntu:22.04}
cleanup() {
    rc=$?
    trap - EXIT
    docker rm -f "$name" > "$out/cleanup.log" 2>&1 || true
    docker ps -a --filter "name=^/${name}$" --format '{{.Names}}' > "$out/remaining-containers.txt"
    [[ ! -s $out/remaining-containers.txt ]] || rc=1
    printf '%s\n' "$rc" > "$out/exit-code.txt"
    printf 'Results: %s\n' "$out"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker images > "$out/images-before.txt"
docker ps -a > "$out/containers-before.txt"
docker image inspect "$image" > "$out/image.json"
git -C "$repo" rev-parse HEAD > "$out/source-sha.txt"
git -C "$repo" archive HEAD > "$out/source.tar"
# Include tracked working changes explicitly; preserve the patch as provenance.
git -C "$repo" diff HEAD --binary > "$out/source.patch"
mkdir "$out/harness"
cp "$repo"/rtx-project/tests/integration/*.{c,h,py} "$out/harness/"
cp "$repo"/rtx-project/tests/runtime/{smoke.c,packet_peer.c,check_packet_trace.py} "$out/"
cp "$repo/rtx-project/scripts/hybrid_validation_container.sh" "$out/build.sh"
cp "$0" "$out/runner.sh"
printf 'Results: %s\n' "$out"
docker run --name "$name" --mount "type=bind,source=$out,target=/evidence" "$image" bash /evidence/build.sh
