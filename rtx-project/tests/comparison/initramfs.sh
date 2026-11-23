#!/bin/bash
set -euo pipefail
src=$1
dest=$2
recipe=$(cd "$(dirname "$0")" && pwd)
root=$(mktemp -d)
mkdir -p "$root"/{bin,sbin,dev,proc,sys,etc,run,tmp,usr/bin,usr/lib,rtx-project/tests/runtime,rtx-project/scripts}
cp /bin/busybox "$root/bin/"
for app in sh mount poweroff sync sysctl mkdir cat; do ln -s busybox "$root/bin/$app"; done
cp -L "$(command -v ip)" "$(command -v tc)" "$(command -v ethtool)" "$root/sbin/"
cp -L /usr/bin/python3 "$root/usr/bin/python3"
cp -a /usr/lib/python3.8 "$root/usr/lib/"
ln -s /usr/bin/python3 "$root/usr/bin/python3.8"
for path in /lib/ld-linux-aarch64.so.1 /usr/bin/python3 /sbin/ip /sbin/tc "$(command -v ethtool)" /usr/lib/python3.8/lib-dynload/*.so; do
    while read -r dep; do
        mkdir -p "$root$(dirname "$dep")"
        cp -L "$dep" "$root$dep"
    done < <(ldd "$path" 2>/dev/null | awk '/=> \// {print $3} $1 ~ /^\// {print $1}')
done
cp -L --parents /lib/ld-linux-aarch64.so.1 "$root/"
cp "$src"/rtx-project/tests/runtime/*.py "$root/rtx-project/tests/runtime/"
cp "$src"/rtx-project/scripts/*.py "$root/rtx-project/scripts/"
cp "$recipe/guest.py" "$root/"
cat >"$root/init" <<'INIT'
#!/bin/sh
export PATH=/sbin:/bin:/usr/bin PYTHONDONTWRITEBYTECODE=1
mount -t devtmpfs devtmpfs /dev
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /run
mount -t tmpfs tmpfs /tmp
/usr/bin/python3 /guest.py
poweroff -f
INIT
chmod 755 "$root/init"
cd "$root"
cc "$src/usr/gen_init_cpio.c" -o /tmp/rtx16-cpio
printf 'dir /dev 755 0 0\nnod /dev/console 600 0 0 c 5 1\n' >/tmp/rtx16-nodes
{ /tmp/rtx16-cpio /tmp/rtx16-nodes; find . -print0 | cpio --null -o -H newc; } | gzip -1 > "$dest"
