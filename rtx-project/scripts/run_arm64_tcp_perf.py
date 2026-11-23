#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Run repeatable bulk and request/response workloads on a booted ARM64 kernel."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import sys
import tempfile
import time

from arm64_perf_evidence import boot_identity, capture_environment, evidence_run
from build_arm64 import sha256, write_status
from run_arm64_n_distribution import netem
from run_arm64_tcp import Network, PROJECT

PEER = PROJECT / "tests/runtime/tcp_perf_endpoint.py"


def metrics(workload, sender, receiver):
    size = receiver["bytes"]
    if size <= 0 or sender["bytes"] != size or receiver["elapsed_ns"] <= 0:
        raise ValueError("invalid receiver interval or byte count")
    if receiver["vm_busy_ticks"] < 0 or receiver["clk_tck"] <= 0:
        raise ValueError("invalid CPU time")
    values = dict(goodput_mbps=size * 8000 / receiver["elapsed_ns"],
                  vm_busy_ns_per_byte=receiver["vm_busy_ticks"] * 1e9 / receiver["clk_tck"] / size,
                  sender_peak_wmem_queued_bytes=sender["sampled_peak_socket_bytes"]["wmem_queued"])
    if workload == "rr":
        if (sender["requests"] * 64 != size or sender["p50_us"] <= 0
                or sender["p99_us"] < sender["p50_us"]):
            raise ValueError("invalid request counts or latency")
        values.update(p50_us=sender["p50_us"], p99_us=sender["p99_us"])
    return values


def trial(network, path, workload, seconds, cpus):
    path.mkdir()
    args = [workload, "198.18.0.2", str(path), "--seconds", str(seconds)]
    processes = []
    for side, role in ((1, "server"), (0, "client")):
        pin = [] if cpus[side] is None else ["taskset", "-c", str(cpus[side])]
        cmd = ["ip", "netns", "exec", network.names[side], *pin, sys.executable, "-B", str(PEER)]
        peer = network.spawn(cmd + [role] + args, path / (role + ".json"))
        processes.append(peer)
        if role == "server":
            deadline = time.monotonic() + 5
            while not (path / "port").exists():
                if peer.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("receiver startup failed: " + str(path))
                time.sleep(0.02)
    for proc in reversed(processes):
        if proc.wait(timeout=seconds + 65):
            raise ValueError("endpoint failure: " + str(path))
    sender = json.loads((path / "client.json").read_text())
    receiver = json.loads((path / "server.json").read_text())
    if workload == "rr" and (path / "latency-ns.u64").stat().st_size != 8 * sender["requests"]:
        raise ValueError("missing raw request timings")
    return metrics(workload, sender, receiver)


def summarize(run, rows):
    with (run / "summary.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["workload", "metric", "repetitions", "median", "q1", "q3"])
        for workload in ("bulk", "rr"):
            group = [row["metrics"] for row in rows if row["workload"] == workload]
            if not group:
                continue
            for key in group[0]:
                values = [sample[key] for sample in group]
                quartiles = (statistics.quantiles(values, n=4, method="inclusive")
                             if len(values) > 1 else values * 3)
                writer.writerow([workload, key, len(values), statistics.median(values),
                                 quartiles[0], quartiles[2]])


def execute(args, run, state):
    identity = boot_identity(args.build_record)
    if identity["mode"] != "tcp":
        raise ValueError("TCP performance requires a build without the operation fixture")
    if os.geteuid():
        raise PermissionError("network namespace setup requires sudo")
    state.update(identity=identity, peer_sha256=sha256(PEER), runner_sha256=sha256(__file__),
                 cpu_scope="whole VM busy CPU; includes sender, receiver, softirqs and background",
                 timer="monotonic_ns", settings=vars(args).copy(), samples=[])
    state["settings"]["build_record"] = str(args.build_record.resolve())
    shutil.copy2(args.build_record, run / "build-record.json")
    shutil.copy2("/boot/config-" + platform.release(), run / "kernel.config")
    state["environment"] = capture_environment(run, "tcp-perf")
    network = Network(run)
    try:
        network.setup()
        for side, (ns, link) in enumerate(zip(network.names, network.links)):
            cmd = ["ip", "netns", "exec", ns]
            network.command(cmd + ["tc", "qdisc", "replace", "dev", link, "root", "netem"] +
                            netem(args.loss, args.rtt, args.reorder if side == 0 else 0))
            network.command(cmd + ["ethtool", "-k", link])
        for rep in range(args.repeats):
            for workload in ("bulk", "rr"):
                base = f"{rep+1:02d}-{workload}"
                cpus = [args.sender_cpu, args.receiver_cpu]
                if args.warmup:
                    trial(network, run / (base + "-warmup"), workload, args.warmup, cpus)
                values = trial(network, run / base, workload, args.seconds, cpus)
                state["samples"].append(dict(workload=workload, repeat=rep + 1, path=base, metrics=values))
                write_status(run, state)
        for ns, link in zip(network.names, network.links):
            network.command(["ip", "netns", "exec", ns, "tc", "-s", "qdisc", "show", "dev", link])
        summarize(run, state["samples"])
    finally:
        state["cleanup_errors"] = network.cleanup()
        if state["cleanup_errors"]:
            raise RuntimeError("namespace cleanup failed; see status.json")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build-record", type=Path, required=True)
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--warmup", type=float, default=10)
    p.add_argument("--rtt", type=int, default=0, help="added round-trip ms, even integer")
    p.add_argument("--loss", type=int, default=0, help="loss percent on each egress")
    p.add_argument("--reorder", type=int, default=0, help="sender reorder percent with delay")
    p.add_argument("--sender-cpu", type=int)
    p.add_argument("--receiver-cpu", type=int)
    a = p.parse_args()
    if (a.repeats < 1 or not math.isfinite(a.seconds) or not math.isfinite(a.warmup)
            or a.seconds <= 0 or a.warmup < 0 or a.rtt < 0 or a.rtt % 2 or (a.reorder and not a.rtt)
            or not 0 <= a.loss < 100 or not 0 <= a.reorder <= 100):
        p.error("invalid duration, repeats or netem parameters")
    for cpu in (a.sender_cpu, a.receiver_cpu):
        if cpu is not None and cpu not in os.sched_getaffinity(0):
            p.error("selected CPU unavailable")
    root = PROJECT / ".work"
    root.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="d-tcp-perf-", dir=root))
    print(run, flush=True)
    state = dict(argv=sys.argv[1:])
    with evidence_run(run, state):
        execute(a, run, state)


if __name__ == "__main__":
    main()
