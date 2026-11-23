#!/bin/bash
# Run from a full checkout on an Apple Silicon host with Docker Desktop.
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
repo=$(git -C "$recipe" rev-parse --show-toplevel)
out=$1
vfkit=$2
mkdir "$out"
out=$(cd "$out" && pwd)
test "$(shasum -a 256 "$vfkit" | cut -d' ' -f1)" = 0ed83fc8ca7aa708598835480dba1362406aa7cd1dab3b27464eb76327d9652d
cp "$vfkit" "$out/vfkit"
cp -R "$recipe" "$out/recipe"
git clone --bare --no-hardlinks -q "$repo" "$out/source.git"
fixture=0df8dcd98ac9a4affe7772a1a2b7c759da32d2ba
git -C "$out/source.git" fetch -q "$repo" "$fixture:refs/heads/issue36-fixture"
name="rtx16-$(date +%s)-$$"
docker ps -a --format '{{.Names}}' > "$out/containers-before.txt"
docker image ls --format '{{.Repository}}:{{.Tag}} {{.ID}}' > "$out/images-before.txt"
image=ubuntu:20.04
cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker ps -a --filter "name=^/${name}$" --format '{{.Names}}' > "$out/remaining.txt"
    test ! -s "$out/remaining.txt"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
docker run --pull=missing --name "$name" --platform linux/arm64 -v "$out:/work" "$image" bash -ec '
    test "$(dpkg --print-architecture)" = arm64
    export DEBIAN_FRONTEND=noninteractive
    apt-get update > /work/apt.log 2>&1
    apt-get install -y --no-install-recommends bc bison flex gcc make libc6-dev libelf-dev libssl-dev git python3 iproute2 ethtool busybox-static cpio kmod > /work/install.log 2>&1
    git -c safe.directory=/work/source.git clone -q --no-local /work/source.git /src
    git -C /src checkout -q --detach f78f5626ab0c307050f25b50606a61a104de6dac
    gcc --version > /work/compiler.txt
    bash /work/recipe/initramfs.sh /src /work/guest.gz > /work/initramfs.log 2>&1
    bash /work/recipe/build.sh /src /work/builds > /work/build.log 2>&1'
docker inspect "$name" --format '{{.Image}}' > "$out/builder-image.txt"
cleanup
trap - EXIT
bash "$recipe/long-initrd.sh" "$out/guest.gz" "$out/long.gz"
{ sysctl -n hw.model hw.logicalcpu hw.memsize kern.hv_support; sw_vers; uptime; "$out/vfkit" --version; } > "$out/host.txt"
python3 -B "$recipe/matrix.py" "$out" "$out/runs"
python3 -B "$recipe/matrix.py" "$out" "$out/long-runs" --long-queue
python3 -B "$recipe/matrix.py" "$out" "$out/batch-runs" --batch
python3 -B "$recipe/analyze.py" "$out" "$out/tables"
# Optional rendering dependency: Matplotlib.
if python3 -c 'import matplotlib' 2>/dev/null; then
    python3 "$recipe/figure.py" "$out/tables" "$out/tables/comparison.png"
fi
