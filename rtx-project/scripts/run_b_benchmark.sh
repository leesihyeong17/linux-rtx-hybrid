#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
mkdir -p "$repo/rtx-project/.work"
out=$(mktemp -d "$repo/rtx-project/.work/b-bench-XXXXXXXX")
name="rtx-b-bench-$(date +%s)-$$"
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
{ uname -a; uptime; docker info --format '{{json .}}'; } > "$out/host-environment.txt"
git -C "$repo" rev-parse HEAD > "$out/source-sha.txt"
git -C "$repo" archive HEAD > "$out/source.tar"
git -C "$repo" diff HEAD --binary > "$out/source.patch"
mkdir "$out/harness"
cp "$repo"/rtx-project/tests/benchmark/*.{c,h,py} "$out/harness/"
cp "$repo/rtx-project/tests/runtime/smoke.c" "$out/"
cp "$repo/rtx-project/scripts/b_benchmark_container.sh" "$out/build.sh"
cp "$0" "$out/runner.sh"
printf 'Results: %s\n' "$out"
docker run --name "$name" --mount "type=bind,source=$out,target=/evidence" \
    -e RTX_BENCH_ITERATIONS="${RTX_BENCH_ITERATIONS:-32}" \
    -e RTX_BENCH_WARMUP_MS="${RTX_BENCH_WARMUP_MS:-10000}" \
    -e RTX_BENCH_REPEATS="${RTX_BENCH_REPEATS:-10}" \
    -e RTX_BENCH_SEED="${RTX_BENCH_SEED:-16}" \
    -e RTX_BENCH_K="${RTX_BENCH_K:-16}" \
    -e RTX_BENCH_RUN_PREFIX="$(basename "$out")" \
    "$image" bash /evidence/build.sh
