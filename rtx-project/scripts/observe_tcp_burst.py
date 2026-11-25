#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Capture STATS v2 deltas around one loopback burst trial in an isolated VM."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from run_arm64_n_distribution import PORT, STATS, distribution, parse_stats

PILOT = Path(__file__).resolve().parents[1] / "tests/runtime/tcp_burst_pilot.py"


def snapshot(path):
    raw = STATS.read_text()
    path.write_text(raw)
    fmt, _, counts = parse_stats(raw)
    if fmt != "v2" or counts.get("filter_port") != PORT:
        raise ValueError("STATS v2 with filter_port %d is required" % PORT)
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pattern", choices=("gated", "continuous"))
    parser.add_argument("--output", required=True, type=Path, help="new evidence directory")
    parser.add_argument("--groups", type=int, default=64)
    parser.add_argument("--chunks-per-group", type=int, default=8)
    parser.add_argument("--chunk-bytes", type=int, default=4096)
    parser.add_argument("--port", type=int, default=PORT, choices=[PORT])
    parser.add_argument("--expect-build-id", help="expected GNU build ID of the observation kernel")
    args = parser.parse_args()
    if min(args.groups, args.chunks_per_group, args.chunk_bytes) < 1:
        parser.error("workload dimensions must be positive")
    run = args.output
    try:
        run.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        parser.exit(1, "%s\n" % exc)
    cmd = [sys.executable, "-B", str(PILOT), args.pattern, "--groups", str(args.groups),
           "--chunks-per-group", str(args.chunks_per_group), "--chunk-bytes", str(args.chunk_bytes),
           "--port", str(args.port)]
    if args.expect_build_id:
        cmd.extend(["--expect-build-id", args.expect_build_id])
    status = {"status": "FAIL", "command": cmd, "pattern": args.pattern,
              "counter_scope": "all sockets matching port 5001, both loopback endpoints combined"}
    try:
        before = snapshot(run / "before.txt")
        try:
            with (run / "workload.json").open("w") as out, (run / "workload.stderr").open("w") as err:
                subprocess.run(cmd, stdout=out, stderr=err, check=True)
        finally:
            after = snapshot(run / "after.txt")
        workload = json.loads((run / "workload.json").read_text())
        if workload["status"] != "PASS" or workload["port"] != PORT:
            raise ValueError("workload did not pass on the observed port")
        delta = distribution(before, after)
        counters = delta["counters"]
        if not counters["ack_unlink"]:
            raise ValueError("no fully ACKed skb removals during the transfer")
        status.update(status="PASS", build_id=workload["build_id"], boot_id=workload["boot_id"],
                      kernel_release=workload["kernel_release"], stats_format=delta["stats_format"],
                      filter_port=delta["filter_port"], counters=counters, histograms=delta["histograms"],
                      n_le_k_fraction=delta["le_share"], insert_samples=delta["insert_samples"],
                      ack_samples=delta["ack_samples"],
                      mean_full_removals_per_cleanup=counters["ack_unlink"] / counters["ack_clean"])
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        status["error"] = str(exc)
    finally:
        (run / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    print(run / "status.json")
    return int(status["status"] != "PASS")


if __name__ == "__main__":
    sys.exit(main())
