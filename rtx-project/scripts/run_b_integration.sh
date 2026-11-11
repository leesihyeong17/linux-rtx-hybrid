#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
set -euo pipefail
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
mkdir -p "$repo/rtx-project/.work"
out=$(mktemp -d "$repo/rtx-project/.work/b-integration-XXXXXXXX")
name="rtx-b-integration-$(date +%s)-$$"
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
docker image inspect "$image" > "$out/image.json"
git -C "$repo" rev-parse HEAD > "$out/source-sha.txt"
git -C "$repo" archive HEAD > "$out/source.tar"
mkdir "$out/harness"
cp "$repo"/rtx-project/tests/integration/*.{c,h,py} "$out/harness/"
cp "$repo/rtx-project/tests/runtime/smoke.c" "$out/smoke.c"
cp "$repo/rtx-project/scripts/b_integration_container.sh" "$out/build.sh"
cp "$0" "$out/runner.sh"
docker run --name "$name" --mount "type=bind,source=$out,target=/evidence" "$image" bash /evidence/build.sh
