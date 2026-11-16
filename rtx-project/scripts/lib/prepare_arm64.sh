#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Shared preparation for pinned ARM64 V0/V1; invoked by public wrappers.
set -euo pipefail
export LC_ALL=C
export GIT_OPTIONAL_LOCKS=0
umask 077

usage() {
	cat <<'EOF'
Usage: prepare_{v0,v1}_arm64.sh --source DIR --output DIR --config FILE

Use a clean pinned worktree and a readable ARM64 kernel config.
DIR for --output must not exist. Runs olddefconfig and prepare with native GCC,
not Image/modules, installation, or reboot. Existing output is never replaced.
Exit 0: preparation passed; nonzero: see stderr and output/prepare-status.txt.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
variant=${1:?internal variant argument required}
shift
case "$variant" in
 v0) baseline=991defcc56cad49cfb221f44f5d161c9ea920c4a ;;
 v1) baseline=231db9ed6a204d6407d88954da60ce7db1c2dabd ;;
 *) die 'unsupported variant' ;;
esac
release=5.4.214-rtx-$variant
source_dir= output_dir= seed_config=
while (($#)); do
	case "$1" in
		--source|--output|--config)
			(($# >= 2)) && [[ -n $2 ]] || die "missing value for $1"
			case "$1" in
				--source) source_dir=$2 ;;
				--output) output_dir=$2 ;;
				--config) seed_config=$2 ;;
			esac
			shift 2 ;;
		--help|-h) usage; exit 0 ;;
		*) die "unknown argument: $1" ;;
	esac
done
[[ -n $source_dir && -n $output_dir && -n $seed_config ]] || { usage >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || die 'this helper requires the native aarch64 VM'
for tool in git make gcc g++ ld as ar nm objcopy objdump readelf flex bison bc openssl python3 realpath sha256sum diff; do
	command -v "$tool" >/dev/null || die "required tool missing: $tool"
done
[[ ! -e $output_dir && ! -L $output_dir ]] || die "output already exists: $output_dir; choose a fresh directory"
source_dir=$(realpath -e -- "$source_dir")
seed_config=$(realpath -e -- "$seed_config")
output_dir=$(realpath -m -- "$output_dir")
[[ ! $source_dir =~ [[:space:]] && ! $output_dir =~ [[:space:]] ]] || die 'Kbuild source/output paths must not contain whitespace'
[[ ! -e $output_dir && ! -L $output_dir ]] || die "output already exists: $output_dir; choose a fresh directory"
[[ $output_dir != "$source_dir"/* && $source_dir != "$output_dir"/* ]] || die 'source and output must be separate directories'
[[ -r $seed_config ]] || die "cannot read seed config: $seed_config"
grep -qx 'CONFIG_ARM64=y' "$seed_config" || die 'seed config is not ARM64'
[[ $(git -C "$source_dir" rev-parse --show-toplevel) == "$source_dir" ]] || die '--source must be the worktree root'
if [[ $variant == v0 ]]; then
 [[ $(git -C "$source_dir" rev-parse 'v0-baseline^{commit}') == "$baseline" ]] || die 'v0-baseline tag differs from the reviewed baseline'
fi
[[ $(git -C "$source_dir" rev-parse HEAD) == "$baseline" ]] || die "source HEAD is not pinned $variant"
source_status=$(git -C "$source_dir" status --porcelain=v1 --untracked-files=all)
[[ -z $source_status ]] || die 'source worktree is dirty'
[[ ! -e $source_dir/.config && ! -d $source_dir/include/config ]] || die 'source has in-tree configuration; use a fresh worktree'
[[ $(gcc -dumpmachine) == aarch64* ]] || die 'gcc does not target aarch64'
# Prevent ambient build flags from silently changing the common experiment config.
for var in KCFLAGS KCPPFLAGS KAFLAGS LDFLAGS_vmlinux CFLAGS_KERNEL CFLAGS_MODULE; do
	[[ -z ${!var-} ]] || die "unset $var before preparing the common baseline"
done

mkdir -p -- "$(dirname -- "$output_dir")"
mkdir -- "$output_dir"
stage=initialization
finish() {
	local rc=$?
	trap - EXIT
	printf 'exit_code=%s\nstage=%s\n' "$rc" "$stage" >"$output_dir/prepare-status.txt"
	if ((rc)); then
		printf 'Preparation failed during %s; logs: %s\n' "$stage" "$output_dir" >&2
	fi
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cp -- "$seed_config" "$output_dir/seed.config"
cp -- "$seed_config" "$output_dir/.config"
{
	printf 'started_utc=%s\nsource=%s\ncommit=%s\nseed=%s\n' "$(date -u +%FT%TZ)" "$source_dir" "$baseline" "$seed_config"
	printf 'host=%s\nrunning_kernel=%s\narch=%s\n' "$(hostname)" "$(uname -r)" "$(uname -m)"
	printf 'compiler_path=%s\n' "$(command -v gcc)"
	gcc --version
	ld --version
	make --version
	df -h -- "$output_dir" /boot
} >"$output_dir/preparation.txt"

stage=config-edit
"$source_dir/scripts/config" --file "$output_dir/.config" \
	--set-str LOCALVERSION "-rtx-$variant" --disable LOCALVERSION_AUTO \
	--enable NAMESPACES --enable NET_NS --enable NET --enable INET \
	--enable NETDEVICES --enable VETH --enable NET_SCHED --enable NET_SCH_NETEM \
	--disable DEBUG_INFO --disable DEBUG_INFO_BTF --disable GDB_SCRIPTS \
	--disable TCP_RTX_STATS --disable TCP_RTX_DEBUG

# Use the repository's Kconfig/build machinery, with output outside the source.
# LOCALVERSION= suppresses the implicit '+' used for non-annotated Git tags.
make_cmd=(env -u MAKEFLAGS -u MFLAGS -u KBUILD_OUTPUT -u KBUILD_SRC \
	-u KCONFIG_ALLCONFIG make --no-print-directory -C "$source_dir" \
	"O=$output_dir" ARCH=arm64 LOCALVERSION= CROSS_COMPILE= \
	KCONFIG_CONFIG=.config CC=gcc HOSTCC=gcc HOSTCXX=g++ LD=ld HOSTLD=ld -j2)
run_make() {
	local target=$1
	stage=$target
	printf '%q ' "${make_cmd[@]}" "$target" >>"$output_dir/commands.log"
	printf '\n' >>"$output_dir/commands.log"
	"${make_cmd[@]}" "$target" >"$output_dir/$target.log" 2>&1
}
run_make olddefconfig
stage=config-verify
for option in ARM64 NAMESPACES NET_NS NET INET NETDEVICES VETH NET_SCHED NET_SCH_NETEM; do
	grep -qx "CONFIG_$option=y" "$output_dir/.config" || die "Kconfig did not retain CONFIG_$option=y"
done
for option in LOCALVERSION_AUTO DEBUG_INFO DEBUG_INFO_BTF GDB_SCRIPTS TCP_RTX_STATS TCP_RTX_DEBUG; do
	if grep -q "^CONFIG_$option=" "$output_dir/.config"; then
		die "CONFIG_$option unexpectedly enabled"
	fi
done
grep -qx "CONFIG_LOCALVERSION=\"-rtx-$variant\"" "$output_dir/.config" || die 'incorrect LOCALVERSION'

# Fail explicitly on unavailable distro certificates rather than clearing trust.
for option in SYSTEM_TRUSTED_KEYS MODULE_SIG_KEY; do
	key=$(sed -n "s/^CONFIG_$option=\"\(.*\)\"/\1/p" "$output_dir/.config")
	[[ -n $key ]] || continue
	[[ $option == MODULE_SIG_KEY && $key == certs/signing_key.pem ]] && continue
	if [[ $key == /* ]]; then
		[[ -r $key ]] || die "CONFIG_$option points to an unreadable key: $key"
	else
		[[ -r $source_dir/$key ]] || die "CONFIG_$option points to an unavailable key: $key"
	fi
done

run_make prepare
run_make kernelrelease
stage=record-config
[[ $(cat "$output_dir/kernelrelease.log") == "$release" ]] || die 'unexpected kernelrelease; inspect kernelrelease.log'
cp -- "$output_dir/.config" "$output_dir/final.config"
(cd "$output_dir" && sha256sum seed.config final.config >config.sha256)
python3 "$source_dir/scripts/diffconfig" "$output_dir/seed.config" "$output_dir/final.config" >"$output_dir/config-delta.txt"
if diff -u -- "$output_dir/seed.config" "$output_dir/final.config" >"$output_dir/config.diff"; then
	:
else
	rc=$?
	((rc == 1)) || exit "$rc"
fi
source_status=$(git -C "$source_dir" status --porcelain=v1 --untracked-files=all)
[[ -z $source_status ]] || die 'source changed during preparation'
stage=prepared
printf 'Prepared %s: %s\nRelease: %s\nImage/modules build and boot are still pending.\n' "${variant^^}" "$output_dir" "$release"
