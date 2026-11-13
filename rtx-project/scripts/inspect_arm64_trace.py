#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Read-only nm/ftrace inventory; never configure tracing or assign timings."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import tempfile

from build_arm64 import FUNCTIONS, PINS, sha256, verify_config

ALTERNATIVES = {
    "tcp_ack": "tcp_ack inclusive cost; count calls, not ACKed segments",
    "tcp_sacktag_walk": "enclosing tcp_sacktag_write_queue; diagnostic visits separately",
    "tcp_sacktag_write_queue": "tcp_ack plus independently observed SACK packets",
    "tcp_clean_rtx_queue": "tcp_ack plus post-ACK queue/accounting snapshots",
    "tcp_shift_skb_data": "enclosing SACK function plus diagnostic shift/merge evidence",
    "tcp_tso_acked": "tcp_trim_head entry/return + caller + GSO/queue snapshots",
    "tcp_trim_head": "diagnostic trim snapshot; retransmit callers must be distinguished",
    "rtx_find_cover": "STATS find_cover and visited nodes; user-space ns/op is a different metric",
}


def table(nm, available, matching_kernel):
    symbols = {line.split()[-1] for line in nm.splitlines() if len(line.split()) >= 3}
    traceable = {line.split()[0] for line in (available or "").splitlines() if line.strip()}
    result = []
    for function in FUNCTIONS:
        found = sorted(s for s in symbols if s == function or s.startswith(function + "."))
        traced = sorted(s for s in traceable if s == function or s.startswith(function + "."))
        if not matching_kernel:
            status = "NOT RUN: target kernel is not running"
        elif available is None:
            status = "BLOCKED: available_filter_functions unreadable"
        else:
            status = "AVAILABLE" if traced else "UNAVAILABLE"
        result.append(dict(function=function, nm_symbols=found, ftrace=status,
                           ftrace_symbols=traced if matching_kernel else [],
                           alternative=ALTERNATIVES[function]))
    return result


def read_state(root):
    data = {}
    for name in ("current_tracer", "tracing_on", "trace_options", "tracing_cpumask",
                 "function_profile_enabled", "set_ftrace_filter", "set_ftrace_pid",
                 "events/enable", "kprobe_events", "uprobe_events"):
        try:
            data[name] = (root / name).read_text()
        except OSError as exc:
            data[name] = {"unavailable": str(exc)}
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-output", required=True, type=Path)
    parser.add_argument("--variant", required=True, choices=PINS)
    parser.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[1] / ".work")
    args = parser.parse_args()
    build = args.build_output.resolve()
    config_hash = verify_config(build, args.variant)
    image_hash = sha256(build / "vmlinux")
    # Tie the symbol inventory to a completed build rather than a leftover ELF.
    successful = []
    for path in build.glob("validation-*/status.json"):
        try:
            status = json.loads(path.read_text())
            if (status.get("status") == "PASS" and status.get("source_sha") == PINS[args.variant]
                    and status.get("config_sha256") == config_hash
                    and status.get("hashes", {}).get("vmlinux") == image_hash):
                successful.append(str(path))
        except (OSError, ValueError):
            continue
    if not successful:
        parser.error("no completed build evidence matches this vmlinux/config")
    nm = subprocess.check_output(["nm", "--defined-only", str(build / "vmlinux")], text=True)
    release = "5.4.214-rtx-" + args.variant
    matching = platform.release() == release
    available, trace_root = None, None
    state = {}
    if matching:
        for root in (Path("/sys/kernel/tracing"), Path("/sys/kernel/debug/tracing")):
            try:
                available = (root / "available_filter_functions").read_text()
                trace_root = root
                break
            except OSError:
                continue
        if trace_root:
            state["global"] = read_state(trace_root)
            try:
                for instance in (trace_root / "instances").iterdir():
                    if instance.is_dir():
                        state[instance.name] = read_state(instance)
            except OSError as exc:
                state["instances_error"] = str(exc)
    report = dict(source_sha=PINS[args.variant], kernelrelease=release,
                  running_kernel=platform.release(), config_sha256=config_hash,
                  vmlinux_sha256=image_hash, build_evidence=successful,
                  functions=table(nm, available, matching), trace_state=state,
                  note="Symbol presence is not ftrace availability. No timing measured. "
                       "tracing_on=0 alone does not establish an uninstrumented run.")
    args.output_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="d-trace-", dir=str(args.output_root.resolve())))
    (run / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if available is not None:
        (run / "available_filter_functions.txt").write_text(available)
    lines = ["| Function | nm | ftrace | Alternative |", "| --- | --- | --- | --- |"]
    for row in report["functions"]:
        lines.append("| %s | %s | %s | %s |" % (row["function"],
                     ", ".join(row["nm_symbols"]) or "ABSENT", row["ftrace"], row["alternative"]))
    (run / "functions.md").write_text("\n".join(lines) + "\n")
    print(run)


if __name__ == "__main__":
    main()
