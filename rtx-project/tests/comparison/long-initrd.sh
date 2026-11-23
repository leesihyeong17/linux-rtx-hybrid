#!/bin/bash
set -euo pipefail
recipe=$(cd "$(dirname "$0")" && pwd)
base=$1
output=$2
dir=$(mktemp -d)
trap 'rm -f "$dir/guest.py" "$dir/overlay.cpio"; rmdir "$dir"' EXIT
cp "$recipe/guest.py" "$dir/"
(cd "$dir"; patch -p1 < "$recipe/long-queue.patch"; printf 'guest.py\n' | cpio -o -H newc > overlay.cpio)
{ gzip -dc "$base"; cat "$dir/overlay.cpio"; } | gzip -1 > "$output"
