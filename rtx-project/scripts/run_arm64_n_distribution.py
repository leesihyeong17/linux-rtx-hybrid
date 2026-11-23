#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Measure RTX queue lengths on the team's ARM64 STATS kernel.

Reads both /proc/rtx_stats formats:
  v1 (PR #12)  n_hist: N right after each insert, 16 power-of-two buckets.
  v2 (A STATS v2)  n_link: same sampling point as v1; n_ack: N on entry to
               tcp_clean_rtx_queue(). Buckets are exact for N 0..64. The
               runner limits counting to the test port while it runs.
"""
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
from contextlib import contextmanager

from arm64_perf_evidence import boot_identity
from build_arm64 import sha256, write_status
from collect_c_arm64 import verify_identity
from run_arm64_packets import netstat
from run_arm64_tcp import ENDPOINT, Network, PROJECT

STATS = Path("/proc/rtx_stats")
BUCKETS = tuple("%d-%d" % (1 << b, (2 << b) - 1) for b in range(15)) + ("32768-",)
V2_BUCKETS = tuple(str(n) for n in range(65)) + ("65-127",) + tuple(
    "%d-%d" % (1 << b, (2 << b) - 1) for b in range(7, 14)) + ("16384-",)
HISTOGRAMS = {"v1": ("n_hist",), "v2": ("n_link", "n_ack")}
HIST_BUCKETS = {"n_hist": BUCKETS, "n_link": V2_BUCKETS, "n_ack": V2_BUCKETS}
COUNTERS = set("sack_block sack_walk_skb find_cover_list_steps shift_partial shift_merge "
               "shift_fallback ack_clean ack_unlink ack_trim insert_append".split())
V2_COUNTERS = COUNTERS | set("filter_port insert_ordered insert_split unlink_tree unlink_list "
                             "promote find_cover find_cover_tree_steps sack_skip_reuse".split())
SHIFT_MIB = {"shift_partial": "TCPSackShifted", "shift_merge": "TCPSackMerged",
             "shift_fallback": "TCPSackShiftFallback"}
# Test traffic uses this server port so a v2 kernel can count only it.
PORT = 5001
# N <= K shares reported for v2 histograms (exact because K <= 64).
K_VALUES = (1, 4, 8, 16, 32, 64)
# Loss and RTT settings are symmetric. Only the sender introduces reordering.
MATRIX = [(loss, rtt, 0) for loss in (0, 1) for rtt in (0, 20, 50)] + [(1, 20, 25)]


def parse_stats(text):
    """Return (format, {histogram name: {bucket: count}}, counters)."""
    counters, hists = {}, {name: {} for name in HIST_BUCKETS}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] in HIST_BUCKETS and parts[1] in HIST_BUCKETS[parts[0]]:
            values, key = hists[parts[0]], parts[1]
        elif len(parts) == 2 and parts[0] not in HIST_BUCKETS:
            values, key = counters, parts[0]
        else:
            raise ValueError("malformed stats line: " + line)
        if key in values or not re.fullmatch(r"[0-9]+", parts[-1]):
            raise ValueError("duplicate or invalid stats value: " + line)
        values[key] = int(parts[-1])
    fmt = "v2" if hists["n_link"] or hists["n_ack"] or "filter_port" in counters else "v1"
    names = HISTOGRAMS[fmt]
    if any(hists[n] for n in HIST_BUCKETS if n not in names):
        raise ValueError("mixed v1 and v2 RTX histograms")
    if any(set(hists[n]) != set(HIST_BUCKETS[n]) for n in names) or \
            not (V2_COUNTERS if fmt == "v2" else COUNTERS).issubset(counters):
        raise ValueError("incomplete RTX histogram or counters")
    return fmt, {n: hists[n] for n in names}, counters


def share_le(hist, k):
    """Share of samples with N <= k; exact v2 buckets only."""
    total = sum(hist.values())
    return sum(c for b, c in hist.items() if "-" not in b and int(b) <= k) / total


def difference(before, after):
    if before.keys() != after.keys():
        raise ValueError("counter schema changed")
    delta = {key: after[key] - value for key, value in before.items()}
    if min(delta.values()) < 0:
        raise ValueError("counter decreased during sample")
    return delta


def distribution(before, after):
    old_fmt, old_hists, old_counts = parse_stats(before)
    fmt, new_hists, new_counts = parse_stats(after)
    if fmt != old_fmt:
        raise ValueError("stats format changed during sample")
    if fmt == "v2" and (old_counts["filter_port"], new_counts["filter_port"]) != (PORT, PORT):
        raise ValueError("both snapshots must select port %d" % PORT)
    port = new_counts.pop("filter_port", None)
    old_counts.pop("filter_port", None)
    hists = {name: difference(old_hists[name], new_hists[name]) for name in new_hists}
    counts = difference(old_counts, new_counts)
    insert = hists[HISTOGRAMS[fmt][0]]
    total = sum(insert.values())
    if not total or not counts["insert_append"]:
        raise ValueError("no queue-length samples or appends during transfer")
    result = {"stats_format": fmt, "histogram": insert, "insert_samples": total,
              "counters": counts,
              "percent": {key: 100 * insert[key] / total for key in insert}}
    if fmt == "v2":
        inserts = counts["insert_append"] + counts["insert_ordered"] + counts["insert_split"]
        if total != inserts:
            raise ValueError("n_link samples %d != inserts %d" % (total, inserts))
        acks = sum(hists["n_ack"].values())
        if not acks or acks != counts["ack_clean"]:
            raise ValueError("n_ack samples %d != ack_clean %d" % (acks, counts["ack_clean"]))
        result.update(filter_port=port, histograms=hists, ack_samples=acks,
                      le_share={name: {str(k): share_le(h, k) for k in K_VALUES}
                                for name, h in hists.items()})
    return result


@contextmanager
def port_filter(fmt, counters):
    """v2: count only PORT while measuring, then restore the previous filter."""
    if fmt == "v1":
        yield
        return
    STATS.write_text("port %d\n" % PORT)
    try:
        yield
    finally:
        STATS.write_text("port %d\n" % counters["filter_port"])


def netem(loss, rtt, reorder):
    options = ["loss", str(loss) + "%"]
    if rtt:
        options.extend(["delay", str(rtt // 2) + "ms"])
    if reorder:
        options.extend(["reorder", str(reorder) + "%"])
    return options


def read_tcpext(network, path, when):
    counters = {}
    for side, ns in zip(("sender", "receiver"), network.names):
        raw = network.command(["ip", "netns", "exec", ns, "cat", "/proc/net/netstat"]).stdout
        (path / (side + "-netstat-" + when + ".txt")).write_text(raw)
        table = netstat(raw)
        if any(table.get(name, -1) < 0 for name in SHIFT_MIB.values()):
            raise ValueError("missing or invalid TCP shift counters in " + side)
        counters[side] = {name: table[name] for name in SHIFT_MIB.values()}
    return counters


def measure(path, condition, size, stats_format="v1"):
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
        network.command(["ss", "-Htn", "state", "established"])
        if stats_format == "v2":
            mib_before = read_tcpext(network, path, "before")
        before = STATS.read_text()
        (path / "before.txt").write_text(before)
        try:
            result["transfer"] = network.case(loss, False, size, "transfer", PORT)
        finally:
            after = STATS.read_text()
            (path / "after.txt").write_text(after)
            if stats_format == "v2":
                mib_after = read_tcpext(network, path, "after")
        network.command(["ss", "-Htn", "state", "established"])
        for ns, link in zip(network.names, network.links):
            network.command(["ip", "netns", "exec", ns, "tc", "-s", "qdisc", "show", "dev", link])
        result.update(distribution(before, after))
        if result["stats_format"] != stats_format:
            raise ValueError("RTX stats format differs from run preflight")
        if stats_format == "v2":
            deltas = {side: difference(values, mib_after[side]) for side, values in mib_before.items()}
            combined = {name: sum(side[name] for side in deltas.values()) for name in SHIFT_MIB.values()}
            result.update(tcpext_by_endpoint=deltas, tcpext_total=combined)
            for counter, name in SHIFT_MIB.items():
                if result["counters"][counter] != combined[name]:
                    raise ValueError("%s delta %d != %s delta %d" %
                                     (counter, result["counters"][counter], name, combined[name]))
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


def write_table(run, samples, build_k=None):
    # Keep the pilot separate. Pool event counts, not rounded percentages.
    groups = {}
    for sample in samples:
        if sample["status"] != "PASS":
            continue
        group = (sample["phase"], sample["loss_percent_each_egress"], sample["added_rtt_ms"],
                 sample["sender_reorder_percent"])
        groups.setdefault(group, []).append(sample)
    dimensions = ["phase", "loss_percent_each_egress", "added_rtt_ms", "sender_reorder_percent"]
    with (run / "distribution.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([*dimensions, "histogram", "repetitions_completed", "bucket", "count", "total", "percent"])
        for group, rows in groups.items():
            hists = [r.get("histograms") or {"n_hist": r["histogram"]} for r in rows]
            for name in hists[0]:
                total = sum(sum(h[name].values()) for h in hists)
                for key in HIST_BUCKETS[name]:
                    count = sum(h[name][key] for h in hists)
                    writer.writerow([*group, name, len(rows), key, count, total, 100 * count / total])

    if build_k is None:
        return
    counters = ("find_cover", "find_cover_tree_steps", "find_cover_list_steps", "sack_block",
                "sack_walk_skb", "sack_skip_reuse", *SHIFT_MIB, "ack_clean", "ack_unlink", "ack_trim")
    thresholds = (4, 8, 16)
    with (run / "conditions.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([*dimensions, "build_k", "repetitions_completed",
                         *(f"{name}_le_{k}_pct" for name in HISTOGRAMS["v2"] for k in thresholds),
                         *counters, "steps_per_find_cover", *SHIFT_MIB.values()])
        for group, rows in groups.items():
            percentages = []
            for name in HISTOGRAMS["v2"]:
                pooled = {b: sum(r["histograms"][name][b] for r in rows) for b in V2_BUCKETS}
                percentages.extend(100 * share_le(pooled, k) for k in thresholds)
            totals = {key: sum(r["counters"][key] for r in rows) for key in counters}
            steps = totals["find_cover_tree_steps"] + totals["find_cover_list_steps"]
            mean = steps / totals["find_cover"] if totals["find_cover"] else None
            writer.writerow([*group, build_k, len(rows), *percentages, *totals.values(), mean,
                             *(sum(r["tcpext_total"][key] for r in rows) for key in SHIFT_MIB.values())])


def collect(args, run, state):
    record = args.build_run / "status.json"
    build = json.loads(record.read_text())
    release = platform.release()
    observation = build.get("mode") == "n-distribution"
    if not observation and "modes" not in build:
        raise ValueError("expected an N distribution build or a legacy C23 fixture record")
    if observation and (build.get("stage"), build.get("status")) != ("built", "PASS"):
        raise ValueError("a completed N distribution kernel build is required")
    expected = build["kernelrelease"] if observation else "5.4.214-rtx-c23-hybrid"
    if platform.machine() != "aarch64" or release != expected:
        state.update(status="BLOCKED", error="boot " + expected + " on the ARM64 VM")
        return
    if observation:
        identity = boot_identity(record, n_distribution=True)
        state.update(production_sha=identity["source_sha"], build_k=build["k"],
                     build_compiler=identity["compiler"], identity=identity)
    else:
        hashes = verify_identity(build, "hybrid", release, platform.machine())
        if not args.pilot_only:
            raise ValueError("the C23 fixture supports --pilot-only; use an N distribution build for the matrix")
        state.update(production_sha=build["production_sha"], fixture_sha=build["harness_sha"],
                     build_compiler=build["compiler"].splitlines()[0], identity_hashes=hashes)
    config = Path("/boot/config-" + release)
    if "CONFIG_TCP_RTX_STATS=y" not in config.read_text().splitlines():
        raise ValueError("running configuration lacks CONFIG_TCP_RTX_STATS=y")
    (run / "build-record.json").write_bytes(record.read_bytes())
    (run / "kernel.config").write_bytes(config.read_bytes())
    state.update(boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                 config_sha256=sha256(config), build_run=str(args.build_run.resolve()),
                 runner_sha256=sha256(__file__), endpoint_sha256=sha256(ENDPOINT),
                 workload="one 198.18.0.1 -> 198.18.0.2:%d TCP transfer per fresh veth/netns pair"
                 % PORT)
    # Other instances of this runner on the same VM share this proc inode.
    with STATS.open() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fmt, _, counters = parse_stats(lock.read())
        if observation and fmt != "v2":
            raise ValueError("N distribution build requires STATS v2")
        if os.geteuid():
            raise PermissionError("sudo is required for the isolated veth and namespaces")
        state["stats_format"] = fmt
        state["stats_scope"] = (
            "VM-wide events including background TCP (v1 has no filter)" if fmt == "v1" else
            "TCP sockets with local or remote port %d (v2 filter)" % PORT) + \
            "; before/after difference, no reset"
        with port_filter(fmt, counters):
            run_trials(args, run, state)


def run_trials(args, run, state):
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
        result = measure(path, cond, args.bytes, state["stats_format"])
        result.update(phase=phase, repetition=rep)
        state["samples"].append(result)
        write_status(run, state)
        if result["status"] != "PASS":
            raise RuntimeError("failed sample " + label + ": " +
                               str(result.get("error") or result["cleanup_errors"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-run", required=True, type=Path,
                        help="N distribution build evidence directory, or legacy C23 build for --pilot-only")
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
        collect(args, run, state)
        if state["status"] == "RUNNING":
            state["status"] = "PASS"
        elif state["status"] == "BLOCKED":
            print(state["error"], file=sys.stderr)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        state.update(status="FAIL", error=str(exc))
        print(exc, file=sys.stderr)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, signal.SIG_IGN)
        write_table(run, state["samples"], state.get("build_k"))
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
