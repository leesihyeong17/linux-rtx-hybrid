#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Reuse B's deterministic TUN peer for native packet behavior, without reboot.

SACK, partial cumulative ACK, DSACK, reneging are independent cases. This does
not prove that the partial ACK trimmed one GSO skb; internal tracing is separate.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import tempfile

from build_arm64 import PINS, sha256
from run_arm64_tcp import Network, PROJECT, boot_evidence


def netstat(text):
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if line.startswith("TcpExt:"):
            keys, values = line.split(), lines[index + 1].split()
            if values[0] != "TcpExt:" or len(keys) != len(values):
                raise ValueError("malformed TcpExt counter table")
            return dict(zip(keys[1:], map(int, values[1:])))
    raise ValueError("missing TcpExt counters")


def validate(log, before, after):
    if "FAIL" in log or "TIMEOUT" in log:
        raise ValueError("packet peer reported failure")
    cases = ("SACK", "PARTIAL", "DSACK", "RENEGING")
    for case in cases:
        if len(re.findall(r"^RTX_PACKET " + case + r" bytes=4000 retransmitted=\d+ PASS$", log, re.M)) != 1:
            raise ValueError("missing or duplicate packet result: " + case)
        if not re.search(r"^RTX_ACCOUNT " + case + r" drained=1 total_retrans=\d+$", log, re.M):
            raise ValueError("missing drained accounting: " + case)
    if not re.search(r"^RTX_ACCOUNT SACK unacked=4 sacked=1$", log, re.M):
        raise ValueError("SACK accounting not observed")
    if "RTX_ACCOUNT PARTIAL acked_bytes=2000 unacked=2 sacked=0 lost=0" not in log:
        raise ValueError("partial ACK accounting not observed")
    if not re.search(r"^RTX_PACKET RENEGING bytes=4000 retransmitted=[1-9]\d* PASS$", log, re.M):
        raise ValueError("reneged data was not retransmitted")
    increments = {}
    for name in ("TCPDSACKRecv", "TCPSACKReneging"):
        if name not in before or name not in after:
            raise ValueError("missing counter: " + name)
        increments[name] = after[name] - before[name]
        if increments[name] < 1:
            raise ValueError("path counter did not increase: " + name)
    if log.count("RTX_NATIVE PACKETS_COMPLETE") != 1:
        raise ValueError("missing final peer completion")
    return dict(packet_cases={case: "PASS" for case in cases}, counter_deltas=increments,
                partial_skb_trim="NOT RUN: packet accounting alone does not prove GSO skb/trim invariants")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=PINS)
    parser.add_argument("--build-output", type=Path)
    parser.add_argument("--smoke-current-kernel", action="store_true")
    args = parser.parse_args()
    if args.smoke_current_kernel and (args.variant or args.build_output):
        parser.error("current-kernel smoke must not be labelled as V0/V1")
    if not args.smoke_current_kernel and not (args.variant and args.build_output):
        parser.error("variant/build-output or smoke-current-kernel required")
    if os.geteuid() != 0:
        parser.error("root is required for the dedicated namespace and TUN")
    if platform.machine() != "aarch64":
        parser.error("native ARM64 VM required")
    root = PROJECT / ".work"
    root.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="d-packets-", dir=str(root)))
    print(run, flush=True)
    network = Network(run)
    state = dict(status="RUNNING", scope="deterministic packet behavior",
                 running_kernel=platform.release(), variant=args.variant or "existing-kernel smoke",
                 partial_skb_trim="NOT RUN", performance="NOT RUN")
    def save():
        (run / "summary.json").write_text(json.dumps(state, indent=2) + "\n")
    def interrupted(signum, frame):
        raise InterruptedError("signal " + str(signum))
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    save()
    try:
        if not args.smoke_current_kernel:
            state.update(boot_evidence(args.build_output.resolve(), args.variant))
        for name in ("packet_peer.c", "native_packet_main.c"):
            shutil.copy2(PROJECT / "tests/runtime" / name, run / name)
        state["source_hashes"] = {name: sha256(run / name) for name in ("packet_peer.c", "native_packet_main.c")}
        network.command(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-DRTX_NATIVE_PEER",
                         str(run / "packet_peer.c"), str(run / "native_packet_main.c"), "-o", str(run / "peer")])
        state["peer_sha256"] = sha256(run / "peer")
        network.command(["gcc", "--version"])
        ns = network.names[0]
        network.command(["ip", "netns", "add", ns])
        network.owned.append(ns)
        (run / "owned-netns.json").write_text(json.dumps(network.owned))
        network.command(["ip", "-n", ns, "link", "set", "lo", "up"])
        before = netstat(network.command(["ip", "netns", "exec", ns, "cat", "/proc/net/netstat"]).stdout)
        kernel_before = network.command(["dmesg", "--raw"], required=False)
        peer = network.spawn(["ip", "netns", "exec", ns, str(run / "peer")], run / "peer.log")
        rc = peer.wait(timeout=40)
        (run / "peer.exitcode").write_text(str(rc) + "\n")
        if rc:
            raise RuntimeError("packet peer exited " + str(rc))
        after = netstat(network.command(["ip", "netns", "exec", ns, "cat", "/proc/net/netstat"]).stdout)
        (run / "netstat.json").write_text(json.dumps(dict(before=before, after=after), indent=2) + "\n")
        state.update(validate((run / "peer.log").read_text(), before, after))
        kernel_after = network.command(["dmesg", "--raw"], required=False)
        pre, post = kernel_before.stdout.splitlines(), kernel_after.stdout.splitlines()
        if kernel_before.returncode or kernel_after.returncode or not pre or post[:len(pre)] != pre:
            state.update(status="SKIP", kernel_log="unavailable or continuity lost")
        else:
            delta = "\n".join(post[len(pre):])
            (run / "kernel-new.log").write_text(delta + "\n")
            errors = re.findall(r"(?im)^.*(?:WARNING:|BUG:|Oops:|Kernel panic|KASAN:|soft lockup).*$", delta)
            state.update(status="FAIL" if errors else "PASS", kernel_errors=errors)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        state.update(status="FAIL", error=str(exc))
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        state["cleanup_errors"] = network.cleanup()
        if state["cleanup_errors"]:
            state["status"] = "FAIL"
        save()
        if "SUDO_UID" in os.environ and "SUDO_GID" in os.environ:
            uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
            for path in run.rglob("*"):
                os.chown(path, uid, gid, follow_symlinks=False)
            os.chown(run, uid, gid)
    return {"PASS": 0, "SKIP": 2}.get(state["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
