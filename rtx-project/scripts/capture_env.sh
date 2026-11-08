#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# Read-only environment snapshot. No sudo, traffic generation, or configuration changes.
set -euo pipefail
umask 077
export LC_ALL=C
export GIT_OPTIONAL_LOCKS=0

usage() {
	cat <<'EOF'
Usage: capture_env.sh [--output-root DIR] [--repo DIR]
                      [--build-config FILE] [--label NAME]

Create a new private env-<UTC>-<label>-XXXXXX directory under output-root.
Defaults: repository containing this script; <repository>/rtx-project/.work/results.
Read the running kernel config separately from an optional build config.
Print the absolute result directory to stdout. Command output stays in its logs.
Exit: 0 = complete capture, 2 = partial capture, 1 = fatal/usage error.
Partial captures preserve every probe's exit code; they are not test results.
EOF
}

die() { printf 'capture_env: %s\n' "$*" >&2; exit 1; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo=$(cd -- "$script_dir/../.." && pwd -P)
output_root=
build_config=
label=manual
while (($#)); do
	case $1 in
		--help|-h) usage; exit 0 ;;
		--output-root|--repo|--build-config|--label)
			(($# >= 2)) && [[ -n $2 ]] || die "missing value for $1"
			case $1 in
				--output-root) output_root=$2 ;;
				--repo) repo=$2 ;;
				--build-config) build_config=$2 ;;
				--label) label=$2 ;;
			esac
			shift 2 ;;
		*) die "unknown argument: $1" ;;
	esac
done
[[ $label =~ ^[a-zA-Z0-9._-]{1,64}$ ]] || die 'label must be 1-64 letters, digits, dots, underscores or hyphens'
for tool in git date mktemp mkdir chmod cat cp sha256sum timeout uname; do
	command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done
repo=$(git -C "$repo" rev-parse --show-toplevel) || die 'repo must be a Git worktree'
repo=$(cd -- "$repo" && pwd -P)
[[ -z $build_config || ( -f $build_config && -r $build_config ) ]] || die 'build config is not a readable regular file'
output_root=${output_root:-"$repo/rtx-project/.work/results"}
mkdir -p -- "$output_root" || die "cannot create output root: $output_root"
output_root=$(cd -- "$output_root" && pwd -P)
out=$(mktemp -d -- "$output_root/env-$(date -u +%Y%m%dT%H%M%SZ)-$label-XXXXXX") || die 'cannot create capture directory'
chmod 700 -- "$out"
mkdir -- "$out/logs"
statuses=$out/statuses.tsv
printf 'probe\tstatus\texit_code\tlog\n' >"$statuses"
total=0
incomplete=0
finished=0
trap 'rc=$?; if (( ! finished )); then printf "capture_env: incomplete/fatal capture (exit %s): %s\n" "$rc" "$out" >&2; fi' EXIT

record() {
	local name=$1 status=$2 rc=$3
	printf '%s\t%s\t%s\tlogs/%s.txt\n' "$name" "$status" "$rc" "$name" >>"$statuses"
	total=$((total + 1))
	if [[ $status != ok ]]; then incomplete=$((incomplete + 1)); fi
}

# Bound each external read, including Git and driver/tool queries.
probe() {
	local name=$1 rc=0 status=ok
	shift
	{
		printf 'command:'
		printf ' %q' "$@"
		printf '\n'
	} >"$out/logs/$name.txt"
	if ! command -v "$1" >/dev/null 2>&1; then
		printf 'Tool not installed: %s\n' "$1" >>"$out/logs/$name.txt"
		rc=127
		status=missing
	else
		timeout --signal=TERM --kill-after=2s 10s "$@" >>"$out/logs/$name.txt" 2>&1 || rc=$?
		case $rc in
			0) ;;
			124|137) status=timeout ;;
			*) status=error ;;
		esac
		# Some tools (notably ss) return 0 even when a netlink query is denied.
		# Preserve the actual exit code but classify the capture as incomplete.
		if [[ $status != timeout ]]; then
			while IFS= read -r line; do
				case $line in
					*'Permission denied'*|*'Operation not permitted'*) status=permission_denied ;;
				esac
			done <"$out/logs/$name.txt"
		fi
	fi
	record "$name" "$status" "$rc"
}

unavailable() {
	local name=$1 reason=$2
	printf '%s\n' "$reason" >"$out/logs/$name.txt"
	record "$name" missing 127
}

snapshot_config() {
	local name=$1 source=$2
	local incomplete_before=$incomplete
	printf '%s\n' "$source" >"$out/$name.source"
	probe "$name-copy" cp -- "$source" "$out/$name.config"
	if ((incomplete == incomplete_before)); then
		probe "$name-sha256" sha256sum -- "$out/$name.config"
	fi
}

{
	printf 'started_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf 'invocation_directory=%s\nrepository=%s\nlabel=%s\n' "$PWD" "$repo" "$label"
	printf 'scope=read-only snapshot of this execution environment; no VM identity inferred\n'
	printf 'pmu_status=not tested; tool availability does not prove cycles support\n'
	printf 'host_load_outside_vm=not observable from this collector\n'
} >"$out/context.txt"
probe hostname hostname
probe kernel uname -a
probe identity id
probe os-release cat /etc/os-release
probe cpu lscpu
probe cpu-count nproc
probe cpuinfo cat /proc/cpuinfo
probe memory free -h
probe meminfo cat /proc/meminfo
probe load cat /proc/loadavg
probe disk df -h / /boot "$output_root"
probe mounts findmnt -T "$repo"
probe block-devices lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT
probe kernel-command-line cat /proc/cmdline
probe process-affinity taskset -pc "$$"
probe interrupts cat /proc/interrupts
probe softirqs cat /proc/softirqs
probe cpu-online cat /sys/devices/system/cpu/online
governors=(/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor)
if [[ -e ${governors[0]} ]]; then
	probe governors cat "${governors[@]}"
else
	unavailable governors 'CPU scaling governor sysfs files are not exposed in this environment.'
fi

probe git-head git -C "$repo" rev-parse HEAD
probe git-status git -C "$repo" status --short --branch --untracked-files=normal
probe git-diff-stat git -C "$repo" diff --stat HEAD
probe git-remotes git -C "$repo" remote -v
probe git-worktrees git -C "$repo" worktree list --porcelain
probe git-baseline git -C "$repo" rev-parse --verify 'v0-baseline^{commit}'

kernel_release=$(uname -r)
if [[ -e /boot/config-$kernel_release ]]; then
	snapshot_config running "/boot/config-$kernel_release"
elif [[ -e /proc/config.gz ]]; then
	printf '/proc/config.gz\n' >"$out/running.source"
	# Keep the config byte-exact; the normal probe format prepends a command header.
	if timeout --signal=TERM --kill-after=2s 10s gzip -cd /proc/config.gz >"$out/running.config" 2>"$out/logs/running-config-copy.txt"; then
		record running-config-copy ok 0
		probe running-sha256 sha256sum -- "$out/running.config"
	else
		rc=$?
		case $rc in
			124|137) record running-config-copy timeout "$rc" ;;
			127) record running-config-copy missing "$rc" ;;
			*) record running-config-copy error "$rc" ;;
		esac
	fi
else
	unavailable running-config "Neither /boot/config-$kernel_release nor /proc/config.gz exists."
fi
if [[ -n $build_config ]]; then snapshot_config build "$build_config"; fi

probe gcc gcc --version
probe compiler-target gcc -dumpmachine
probe make make --version
probe ld ld --version
probe python python3 --version
probe flex flex --version
probe bison bison --version
probe bc bc --version
probe openssl openssl version
probe ip-version ip -Version
probe tc-version tc -Version
probe ss-version ss -V
probe ethtool-version ethtool --version
probe iperf3-version iperf3 --version
probe perf-version perf --version
probe shellcheck-version shellcheck --version

probe addresses ip -details address show
probe routes-v4 ip -4 route show table all
probe routes-v6 ip -6 route show table all
probe netns ip netns list
probe links ip -details -statistics link show
probe qdiscs tc -s qdisc show
probe tcp-sockets ss -tin
probe netstat cat /proc/net/netstat
probe snmp cat /proc/net/snmp
probe network-sysctl sysctl net.ipv4.tcp_congestion_control net.ipv4.tcp_available_congestion_control \
	net.ipv4.tcp_rmem net.ipv4.tcp_wmem net.core.rmem_max net.core.wmem_max \
	net.ipv4.tcp_sack net.ipv4.tcp_timestamps net.ipv4.tcp_window_scaling \
	net.ipv4.tcp_ecn net.ipv4.tcp_recovery
probe perf-sysctl sysctl kernel.perf_event_paranoid kernel.kptr_restrict kernel.nmi_watchdog
iface_index=0
for iface_path in /sys/class/net/*; do
	[[ -e $iface_path ]] || continue
	iface=${iface_path##*/}
	iface_index=$((iface_index + 1))
	printf '%s\t%s\n' "$iface_index" "$iface" >>"$out/interfaces.tsv"
	probe "offload-$iface_index" ethtool -k "$iface"
done

{
	printf 'Environment capture only; no kernel build, boot, TCP correctness or benchmark verified.\n'
	printf 'Finished UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	printf 'Probes: %s; unavailable/failed: %s\n' "$total" "$incomplete"
	if ((incomplete)); then printf 'Status: PARTIAL (exit 2); review statuses.tsv and logs.\n';
	else printf 'Status: COMPLETE (exit 0).\n'; fi
	printf 'Running and build configs are independent; neither proves the running source commit.\n'
	printf 'PMU access and VM host load remain unverified. Review addresses/remotes before sharing.\n'
} >"$out/summary.txt"
finished=1
printf '%s\n' "$out"
if ((incomplete)); then
	printf 'capture_env: partial capture: %s/%s probes unavailable or failed; see %s/statuses.tsv\n' "$incomplete" "$total" "$out" >&2
	exit 2
fi
exit 0
