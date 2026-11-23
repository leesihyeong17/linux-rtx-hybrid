#!/usr/bin/env bash
# A: user-space model equivalence test for the rtx adapter (design 11.1).
#
#   a_model_test.sh [-o LOG] [-s SEEDS] [-m MUTANT_SEEDS] [-k] [REV]
#
#   REV   git revision whose include/net/tcp_rtx.h, rbtree/list headers,
#         lib/rbtree.c and tcp_rbtree_insert() are tested (default HEAD).
#         The harness/model always come from the current checkout.
#   -s    random seeds, "A:B" (default 0:2000)
#   -m    seeds for the mutation check, "" to skip (default 0:100)
#   -o    also write the log to this file
#   -k    keep the work directory
#
# Steps: build every configuration (tools/rtx_model/build.sh), run the
# required-case scenarios, run random traces, then the mutation check.
# Exit status is non-zero if any trace fails or any mutant survives.
set -euo pipefail

seeds=0:2000
mseeds=0:100
log=""
keep=0
while getopts "o:s:m:k" opt; do
	case $opt in
	o) log=$OPTARG ;;
	s) seeds=$OPTARG ;;
	m) mseeds=$OPTARG ;;
	k) keep=1 ;;
	*) exit 1 ;;
	esac
done
shift $((OPTIND - 1))
rev=${1:-HEAD}

here=$(cd "$(dirname "$0")" && pwd)
tool=$here/../tools/rtx_model
top=$(git -C "$here" rev-parse --show-toplevel)
work=$(mktemp -d "${TMPDIR:-/tmp}/a_model.XXXXXX")
[ "$keep" = 1 ] || trap 'rm -rf "$work"' EXIT

if [ -n "$log" ]; then
	mkdir -p "$(dirname "$log")"
	exec > >(tee "$log") 2>&1
fi

sha=$(git -C "$top" rev-parse "$rev")
git -C "$top" archive "$sha" include/net/tcp_rtx.h include/linux/rbtree.h \
	include/linux/rbtree_augmented.h include/linux/list.h lib/rbtree.c \
	net/ipv4/tcp_input.c | tar -x -C "$work" --one-top-level=src

echo "# a_model_test.sh"
echo "date:     $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "machine:  $(uname -srm) ($(hostname))"
echo "compiler: $(${CC:-gcc} --version | head -1)"
echo "python:   $(python3 --version 2>&1)"
echo "target:   $rev = $sha"
echo "tool:     $(git -C "$top" rev-parse HEAD)$(git -C "$top" diff --quiet -- rtx-project/tools/rtx_model rtx-project/scripts/a_model_test.sh || echo ' (+uncommitted tool changes)')"
echo "seeds:    random $seeds, mutants ${mseeds:-skipped}"
echo "config:   kernel CONFIG_* are set per binary, see CONF lines"
echo

echo "## build"
"$tool/build.sh" -r "$work/src" "$work/bin"
cat "$work/bin/inputs.sha256"
for b in "$work"/bin/rb* "$work"/bin/hyb-*; do
	[ -x "$b" ] && printf '%-14s %s\n' "$(basename "$b")" \
		"$("$b" </dev/null | head -1)"
done
echo

status=0
echo "## scenarios"
python3 "$tool/rtx_model.py" scenarios --bins "$work/bin" \
	--fail-dir "$work/fail" || status=1
echo
echo "## random"
python3 "$tool/rtx_model.py" random --bins "$work/bin" --seeds "$seeds" \
	--fail-dir "$work/fail" || status=1
if [ -d "$work/fail" ]; then
	echo "failed traces kept in $work/fail"
	keep=1
	trap - EXIT
fi
if [ -n "$mseeds" ]; then
	echo
	echo "## mutants"
	python3 "$tool/mutants.py" --repo "$work/src" --work "$work/mutants" \
		--seeds "$mseeds" || status=1
fi
echo
echo "RESULT $([ $status = 0 ] && echo PASS || echo FAIL)"
exit $status
