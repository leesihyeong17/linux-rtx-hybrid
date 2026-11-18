#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Collect a booted C fixture kernel; never install, reboot, or clear dmesg."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import time

from build_arm64 import sha256, write_status

PRODUCTION = "e4de76000e09497907562f587efb9a5e914da7d1"
HARNESS = "01acce2b2c39288a76b928add03fb36f567d53c6"
ERROR = re.compile(r"BVERIFY FAIL|rtx_selftest: FAIL|\bCFAIL\b|RTX_RUNTIME (?:FAIL|TIMEOUT)|"
                   r"WARNING:|BUG:|Oops:|Kernel panic|KASAN:|UBSAN:")
CASE = re.compile(r"\bBVERIFY_CASE (\w+) checks=(\d+) failed=(\d+)\s*$")
TOTAL = re.compile(r"\bBVERIFY_TOTAL checks=(\d+) failed=(\d+)\s*$")
STATE = re.compile(r"\bCSTATE k(\d+) (\S+) (.+)$")
STATE_DATA = re.compile(
    r"una=\d+ nxt=\d+ po=\d+ so=\d+ lo=\d+ ro=\d+ del=\d+ tr=\d+ "
    r"ur=-?\d+ reo=\d+ ca=\d+ hs=-?\d+ lh=-?\d+/\d+ rh=-?\d+ "
    r"q=(?:\d+\+\d+:[0-9a-f]+/\d+,)*\s*$")


def expected_state_tags(shape):
    """Ordered c_check calls in the pinned cack, cshift and cx fixtures.

    Repeated tags cover wraparound runs. K=1 omits pair3/pair4 and the
    distinct tail retransmission; IPv6 reruns the selected cx scenarios.
    Keep this contract tied to HARNESS, not to a moving branch.
    """
    if shape not in (1, 16):
        raise ValueError("unsupported pinned CSTATE shape")

    def steps(prefix, suffixes):
        return [prefix + "-" + suffix for suffix in suffixes.split()]

    cleanup = steps("cleanup", "A-sack B-partial-head C-partial-sacked D-cross-boundary E-all")
    recovery = steps("recovery", "A-sack3 B-lost-retrans C-shift D-partial-retrans "
                     "E-dsack F-dsack-again G-ack-boundary H-all")
    boundary = steps("boundary", "0 A-split B-partial C-merge D-acked")
    losthint = steps("losthint", "A-marked B-merged C-all")
    rto = steps("rto", "A-sack B-fired C-partial D-all")
    ofo = steps("ofo", "A-retrans B-dsack-retrans C-dsack-boundary D-all")
    pair2 = steps("pair2", "A-split B-tag-next C-merge2 D-acked")
    tags = cleanup * 2 + recovery * 2
    tags += steps("reorder", "A-sack B-ack C-all")
    tags += steps("splitfail", "A-failed B-retry C-partial D-all")
    tags += boundary * 2
    for index in range(1, 5 if shape == 16 else 3):
        tags += steps("pair" + str(index), "A-split B-tag-next C-merge2 D-acked")
    tags += pair2 + losthint * 2
    for index in ((2, 3, 18) if shape == 16 else (2, 3)):
        tags += steps("rexmit" + str(index), "A-split B-sack C-acked")
    tags += steps("rhint", "A-stopped B-merged C-head-acked D-hint-removed E-all") * 2
    tags += ofo + steps("rack", "A-lost B-retrans C-all")
    tags += steps("reotimer", "A-armed B-fired C-all")
    tags += steps("tlp", "A-probe B-dsack-all") * 2 + rto
    tags += ["v6-" + tag for tag in cleanup + recovery + boundary + pair2 + losthint + rto + ofo]
    return tags


def validate_fixture(text, expected, shapes):
    """Validate native boot evidence without inventing a QEMU guest marker."""
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("invalid expected case inventory")
    if ERROR.search(text):
        raise ValueError("fixture failure or kernel diagnostic")
    cases, totals, states = [], [], {}
    for line in text.splitlines():
        if "BVERIFY_CASE" in line:
            match = CASE.search(line)
            if not match:
                raise ValueError("malformed BVERIFY_CASE")
            name, checks, failed = match.groups()
            cases.append((name, int(checks), int(failed)))
        if "BVERIFY_TOTAL" in line:
            match = TOTAL.search(line)
            if not match:
                raise ValueError("malformed BVERIFY_TOTAL")
            totals.append(tuple(map(int, match.groups())))
        if re.search(r"\bCSTATE\b", line):
            match = STATE.search(line)
            if not match:
                raise ValueError("malformed CSTATE")
            shape = int(match[1])
            if not STATE_DATA.fullmatch(match[3]):
                raise ValueError("malformed or truncated CSTATE payload")
            states.setdefault(shape, []).append(match[2])
    if [row[0] for row in cases] != expected:
        raise ValueError("missing, duplicate, reordered or unexpected case")
    if any(checks <= 0 or failed for _, checks, failed in cases):
        raise ValueError("zero checks or case failure")
    if totals != [(sum(row[1] for row in cases), 0)]:
        raise ValueError("missing, duplicate or inconsistent total")
    core = re.findall(r"rtx_selftest: (\d+) passed, (\d+) failed", text)
    if len(core) != 1 or int(core[0][0]) <= 0 or int(core[0][1]):
        raise ValueError("missing or failed adapter selftest")
    if set(states) != set(shapes):
        raise ValueError("missing or unexpected CSTATE shape")
    for shape, tags in states.items():
        if tags != expected_state_tags(shape):
            raise ValueError("missing, duplicate, reordered or unexpected CSTATE step for k" + str(shape))
    return {"cases": {name: checks for name, checks, _ in cases},
            "total_checks": totals[0][0], "core_checks": int(core[0][0]),
            "state_steps": {shape: len(tags) for shape, tags in states.items()}}


def verify_identity(state, mode, release, arch, boot=Path("/boot")):
    if state.get("status") != "PASS" or state.get("production_sha") != PRODUCTION or state.get("harness_sha") != HARNESS:
        raise ValueError("build evidence is incomplete or uses different source pins")
    modes = state.get("modes")
    if (not isinstance(modes, dict) or set(modes) != {"rb", "hybrid"} or
            any(not isinstance(row, dict) or row.get("status") != "PASS" or
                row.get("exit_code") != 0 for row in modes.values())):
        raise ValueError("both RB and HYBRID builds must pass")
    row = modes[mode]
    expected = "5.4.214-rtx-c23-" + mode
    if arch != "aarch64" or release != expected or row["kernelrelease"] != expected:
        raise ValueError("boot the selected ARM64 fixture kernel first")
    build = Path(row["build"])
    pairs = ((build / ".config", row["config_sha256"]),
             (boot / ("config-" + release), row["config_sha256"]),
             (build / "arch/arm64/boot/Image", row["hashes"]["arch/arm64/boot/Image"]),
             (boot / ("vmlinuz-" + release), row["hashes"]["arch/arm64/boot/Image"]),
             (boot / ("System.map-" + release), row["hashes"]["System.map"]))
    for path, expected_hash in pairs:
        if sha256(path) != expected_hash:
            raise ValueError("installed/build artifact mismatch: " + str(path))
    return {str(path): expected_hash for path, expected_hash in pairs}


def verify_reference(reference, build_status, log):
    if (reference.get("status"), reference.get("mode"), reference.get("production_sha"), reference.get("harness_sha")) != ("PASS", "rb", PRODUCTION, HARNESS):
        raise ValueError("reference is not a passed RB collection of these pins")
    rb = build_status["modes"]["rb"]
    if (reference.get("architecture") != "aarch64" or
            reference.get("running_kernel") != rb["kernelrelease"] or
            reference.get("config_sha256") != rb["config_sha256"] or
            reference.get("image_sha256") != rb["hashes"]["arch/arm64/boot/Image"]):
        raise ValueError("reference uses a different RB build")
    if sha256(log) != reference["dmesg_sha256"]:
        raise ValueError("reference dmesg changed")


def collect(args):
    args.output_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="c-boot-" + args.mode + "-", dir=str(args.output_root)))
    summary = {"status": "RUNNING", "mode": args.mode, "running_kernel": platform.release(),
               "architecture": platform.machine(), "production_sha": PRODUCTION, "harness_sha": HARNESS,
               "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "state_comparison": "NOT RUN", "performance": "NOT RUN"}
    write_status(run, summary)
    print(str(run), flush=True)
    try:
        build_status = json.loads((args.build_run / "status.json").read_text())
        summary["identity_hashes"] = verify_identity(build_status, args.mode, platform.release(), platform.machine())
        summary["config_sha256"] = build_status["modes"][args.mode]["config_sha256"]
        summary["image_sha256"] = build_status["modes"][args.mode]["hashes"]["arch/arm64/boot/Image"]
        summary["build_run"] = str(args.build_run.resolve())
        (run / "build-status.json").write_text(json.dumps(build_status, indent=2) + "\n")
        source = Path(build_status["source"])
        prep = source.parent / "evidence"
        for path, checksum in json.loads((prep / "harness-hashes.json").read_text()).items():
            if sha256(source / path) != checksum:
                raise ValueError("harness changed: " + path)
        expected = json.loads((prep / "cases.json").read_text())
        (run / "cases.json").write_text(json.dumps(expected) + "\n")
        summary["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        (run / "proc-version.txt").write_text(Path("/proc/version").read_text())
        command = (["sudo", "-n"] if os.geteuid() else []) + ["dmesg", "--color=never"]
        (run / "dmesg-command.json").write_text(json.dumps(command) + "\n")
        with (run / "dmesg.log").open("w") as out, (run / "dmesg.stderr").open("w") as err:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=30)
        (run / "dmesg.exit-code").write_text(str(result.returncode) + "\n")
        if result.returncode:
            raise ValueError("dmesg capture failed; authenticate with sudo -v in your terminal")
        text = (run / "dmesg.log").read_text(errors="replace")
        summary["dmesg_sha256"] = sha256(run / "dmesg.log")
        (run / "fixture.log").write_text("\n".join(line for line in text.splitlines()
                                                if re.search(r"BVERIFY_|CSTATE|rtx_selftest:", line)) + "\n")
        summary["fixture"] = validate_fixture(text, expected, (1, 16) if args.mode == "rb" else (16,))
        if args.reference:
            if args.mode != "hybrid":
                raise ValueError("reference comparison requires hybrid mode")
            reference = json.loads((args.reference / "status.json").read_text())
            log = args.reference / "dmesg.log"
            verify_reference(reference, build_status, log)
            reference_text = log.read_text(errors="replace")
            validate_fixture(reference_text, expected, (1, 16))
            module_path = source / "rtx-project/tests/integration/c_diff_states.py"
            spec = importlib.util.spec_from_file_location("c_diff_states", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            compared = module.compare(text, reference_text, 16)
            (run / "state-comparison.json").write_text(json.dumps(compared, indent=2) + "\n")
            if compared["diffs"]:
                raise ValueError("CSTATE differs from RB")
            summary["state_comparison"] = "PASS"
            summary["reference"] = str(args.reference.resolve())
        summary["status"] = "PASS"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        summary.update(status="FAIL", error=str(exc))
    finally:
        summary["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_status(run, summary)
        files = sorted(path for path in run.iterdir() if path.is_file() and path.name != "SHA256SUMS")
        (run / "SHA256SUMS").write_text("".join(sha256(path) + "  " + path.name + "\n" for path in files))
    if summary["status"] != "PASS":
        print(summary["error"], file=sys.stderr)
    return 0 if summary["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-run", type=Path, required=True)
    parser.add_argument("--mode", choices=("rb", "hybrid"), required=True)
    parser.add_argument("--reference", type=Path, help="passed RB collection directory")
    parser.add_argument("--output-root", type=Path, default=Path(__file__).resolve().parents[1] / ".work")
    return collect(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
