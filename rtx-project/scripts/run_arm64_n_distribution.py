#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Measure insert-time RTX queue lengths on the team's ARM64 STATS kernel."""
import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
import tempfile

from build_arm64 import sha256, write_status
from collect_c_arm64 import verify_identity
from run_arm64_tcp import ENDPOINT, Network, PROJECT

STATS = Path("/proc/rtx_stats")
BUCKETS = tuple("%d-%d" % (1 << b, (2 << b) - 1) for b in range(15)) + ("32768-",)
COUNTERS = set("sack_block sack_walk_skb find_cover_list_steps shift_partial shift_merge "
               "shift_fallback ack_clean ack_unlink ack_trim insert_append".split())
# Loss and RTT settings are symmetric. Only the sender introduces reordering.
MATRIX = [(loss, rtt, 0) for loss in (0, 1) for rtt in (0, 20, 50)] + [(1, 20, 25)]


def parse_stats(text):
    counters, histogram = {}, {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "n_hist" and parts[1] in BUCKETS:
            values, key = histogram, parts[1]
        elif len(parts) == 2 and parts[0] != "n_hist":
            values, key = counters, parts[0]
        else:
            raise ValueError("malformed stats line: " + line)
        if key in values or not re.fullmatch(r"[0-9]+", parts[-1]):
            raise ValueError("duplicate or invalid stats value: " + line)
        values[key] = int(parts[-1])
    if set(histogram) != set(BUCKETS) or not COUNTERS.issubset(counters):
        raise ValueError("incomplete RTX histogram or counters")
    return histogram, counters


def difference(before, after):
    if before.keys() != after.keys():
        raise ValueError("counter schema changed")
    delta = {key: after[key] - value for key, value in before.items()}
    if min(delta.values()) < 0:
        raise ValueError("counter decreased during sample")
    return delta


def distribution(before, after):
    old_hist, old_counts = parse_stats(before)
    new_hist, new_counts = parse_stats(after)
    hist = difference(old_hist, new_hist)
    counts = difference(old_counts, new_counts)
    total = sum(hist.values())
    if not total or not counts["insert_append"]:
        raise ValueError("no queue-length samples or appends during transfer")
    return {"histogram": hist, "insert_samples": total, "counters": counts,
            "percent": {key: 100 * hist[key] / total for key in BUCKETS}}


def netem(loss, rtt, reorder):
    options = ["loss", str(loss) + "%"]
    if rtt:
        options.extend(["delay", str(rtt // 2) + "ms"])
    if reorder:
        options.extend(["reorder", str(reorder) + "%"])
    return options


def measure(path, condition, size):
    loss, rtt, reorder = condition
    network = Network(path)
    result = {"status": "FAIL", "directory": path.name, "loss_percent_each_egress": loss,
              "added_rtt_ms": rtt, "sender_reorder_percent": reorder, "payload_bytes": size}
    try:
        network.setup()
        for side, (ns, link) in enumerate(zip(network.names, network.links)):
            command = ["ip", "netns", "exec", ns]
            network.command(command + ["tc", "qdisc", "replace", "dev", link, "root", "netem"] +
                            netem(loss, rtt, reorder if side == 0 else 0))
            offload = network.command(command + ["ethtool", "-k", link])
            (path / (link + "-offload.txt")).write_text(offload.stdout)
        # Host TCP, including SSH, contributes to these global counters.
        network.command(["ss", "-Htn", "state", "established"])
        before = STATS.read_text()
        (path / "before.txt").write_text(before)
        try:
            result["transfer"] = network.case(loss, False, size, "transfer")
        finally:
            after = STATS.read_text()
            (path / "after.txt").write_text(after)
        network.command(["ss", "-Htn", "state", "established"])
        for ns, link in zip(network.names, network.links):
            network.command(["ip", "netns", "exec", ns, "tc", "-s", "qdisc", "show", "dev", link])
        result.update(distribution(before, after))
        result["status"] = "PASS"
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        result["error"] = str(exc)
    finally:
        errors = network.cleanup()
        result["cleanup_errors"] = errors
        if errors:
            result["status"] = "FAIL"
        write_status(path, result)
    return result


def write_table(run, samples):
    # Keep the pilot separate. Pool link-event counts, not rounded percentages.
    groups = {}
    for sample in samples:
        if sample["status"] != "PASS":
            continue
        group = (sample["phase"], sample["loss_percent_each_egress"], sample["added_rtt_ms"],
                 sample["sender_reorder_percent"])
        groups.setdefault(group, []).append(sample["histogram"])
    with (run / "distribution.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["phase", "loss_percent_each_egress", "added_rtt_ms", "sender_reorder_percent",
                         "repetitions_completed", "bucket", "count", "total", "percent"])
        for group, histograms in groups.items():
            total = sum(sum(h.values()) for h in histograms)
            for key in BUCKETS:
                count = sum(h[key] for h in histograms)
                writer.writerow([*group, len(histograms), key, count, total, 100 * count / total])


def collect(args, run, state):
    build = json.loads((args.build_run / "status.json").read_text())
    release = platform.release()
    hashes = verify_identity(build, "hybrid", release, platform.machine())
    config = Path("/boot/config-" + release)
    if "CONFIG_TCP_RTX_STATS=y" not in config.read_text().splitlines():
        raise ValueError("running configuration lacks CONFIG_TCP_RTX_STATS=y")
    state.update(production_sha=build["production_sha"], fixture_sha=build["harness_sha"],
                 build_compiler=build["compiler"].splitlines()[0], identity_hashes=hashes,
                 boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                 config_sha256=sha256(config), build_run=str(args.build_run.resolve()),
                 runner_sha256=sha256(__file__), endpoint_sha256=sha256(ENDPOINT),
                 workload="one 198.18.0.1 -> 198.18.0.2 TCP transfer per fresh veth/netns pair",
                 stats_scope="VM-wide insert events including background TCP; no reset or subtraction")
    # Other instances of this runner on the same VM share this proc inode.
    with STATS.open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parse_stats(lock.read())
        if os.geteuid():
            raise PermissionError("sudo is required for the isolated veth and namespaces")
        env = subprocess.run(["bash", str(PROJECT / "scripts/capture_env.sh"), "--output-root",
                              str(run), "--label", "n-distribution"], capture_output=True, text=True)
        (run / "capture-env.log").write_text(env.stdout + env.stderr)
        if env.returncode not in (0, 2):
            raise RuntimeError("capture_env failed; inspect capture-env.log")
        state["env_capture"] = {"directory": Path(env.stdout.strip()).name,
                                "status": "PARTIAL" if env.returncode else "COMPLETE"}
        trials = [("pilot", 1, (0, 0, 0))]
        if not args.pilot_only:
            trials += [("matrix", rep, cond) for rep in range(1, args.repetitions + 1) for cond in MATRIX]
        for phase, rep, cond in trials:
            label = "%s-%02d-loss%d-rtt%d-reorder%d" % (phase, rep, *cond)
            path = run / label
            path.mkdir()
            result = measure(path, cond, args.bytes)
            result.update(phase=phase, repetition=rep)
            state["samples"].append(result)
            write_status(run, state)
            if result["status"] != "PASS":
                raise RuntimeError("failed sample " + label + ": " +
                                   str(result.get("error") or result["cleanup_errors"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-run", required=True, type=Path, help="completed C23 RB/HYBRID build record")
    parser.add_argument("--pilot-only", action="store_true")
    parser.add_argument("--bytes", type=int, default=1048576, help="bytes per transfer")
    parser.add_argument("--repetitions", type=int, default=3, help="repetitions per matrix condition")
    args = parser.parse_args()
    if min(args.bytes, args.repetitions) <= 0:
        parser.error("bytes and repetitions must be positive")
    root = PROJECT / ".work"
    root.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="d-n-dist-", dir=root))
    print(run, flush=True)
    state = dict(status="RUNNING", release=platform.release(), samples=[], argv=sys.argv[1:],
                 repetitions=args.repetitions, pilot_only=args.pilot_only, payload_bytes=args.bytes)
    write_status(run, state)

    def interrupt(signum, frame):
        raise InterruptedError("interrupted by signal %s" % signum)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, interrupt)
    try:
        if platform.machine() != "aarch64" or platform.release() != "5.4.214-rtx-c23-hybrid":
            state.update(status="BLOCKED", error="boot 5.4.214-rtx-c23-hybrid on the ARM64 VM")
            print(state["error"], file=sys.stderr)
        else:
            collect(args, run, state)
            state["status"] = "PASS"
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        state.update(status="FAIL", error=str(exc))
        print(exc, file=sys.stderr)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_IGN)
        write_table(run, state["samples"])
        write_status(run, state)
        files = sorted(p for p in run.rglob("*") if p.is_file())
        (run / "SHA256SUMS").write_text("".join(sha256(p) + "  " + str(p.relative_to(run)) + "\n"
                                               for p in files))
        if "SUDO_UID" in os.environ and "SUDO_GID" in os.environ:
            for path in [*run.rglob("*"), run]:
                os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]),
                         follow_symlinks=False)
    return {"PASS": 0, "BLOCKED": 2}.get(state["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
