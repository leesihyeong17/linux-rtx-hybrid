#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Build and collect the pinned B operation benchmark on the native ARM64 VM."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

from arm64_perf_evidence import boot_identity, capture_environment, evidence_run
from build_arm64 import FLAGS, artifacts, output, sha256, write_status
from run_arm64_tcp import Network, PROJECT

REPO = PROJECT.parent
LEGACY_HARNESS = "4e204ffda288734f722674bb696d40efbb78e9d6"
HARNESS = "0df8dcd98ac9a4affe7772a1a2b7c759da32d2ba"
HARNESSES = (HARNESS, LEGACY_HARNESS)
# Same C/H/Python bundle and digest convention as B's run_b_benchmark.sh.
FILES = ("apply_overlay.py", "benchmark.c", "collect.py", "hooks.h",
         "render_report.py", "run_matrix.py", "test_benchmark.py")
SYMBOLS = ("HYBRID", "CACHE", "TAIL")
BACKENDS = {"rb-basic": (0, 0, 0), "rb-cache": (0, 1, 0), "rb-tail": (0, 1, 1),
            "hybrid-ordered": (1, 1, 0), "hybrid-tail": (1, 1, 1)}


def json_file(path, values):
    path.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")


def export_harness(directory, revision=HARNESS):
    directory.mkdir()
    for name in FILES:
        blob = revision + ":rtx-project/tests/benchmark/" + name
        (directory / name).write_bytes(subprocess.check_output(["git", "-C", REPO, "show", blob]))
    hashes = {name: sha256(directory / name) for name in FILES}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def extract_tree(revision, directory, log):
    directory.mkdir()
    with subprocess.Popen(["git", "-C", REPO, "archive", revision], stdout=subprocess.PIPE,
                          stderr=log) as archive:
        try:
            subprocess.run(["tar", "-x", "-C", directory], stdin=archive.stdout, stderr=log, check=True)
        finally:
            archive.stdout.close()
        if archive.wait():
            raise ValueError("git archive did not complete")


def config_values(variant, k, suffix, mode="tcp"):
    values = dict(ARM64="y", NET_NS="y", VETH="y", NET_SCH_NETEM="y", PRINTK="y",
                  LOCALVERSION='"' + suffix + '"', LOCALVERSION_AUTO="n", LOG_BUF_SHIFT="23",
                  TCP_RTX_DEBUG="n", TCP_RTX_STATS="n")
    if mode == "n-distribution":
        if variant != "hybrid-tail":
            raise ValueError("N distribution requires the hybrid-tail backend")
        values.update(NET_SCHED="y", TCP_RTX_STATS="y")
    for field, enabled in zip(SYMBOLS, BACKENDS[variant]):
        values["TCP_RTX_" + field] = "y" if enabled else "n"
    if BACKENDS[variant][0]:
        values["TCP_RTX_RECENT_MAX"] = str(k)
    return values


def build(args):
    seed = args.base_config.resolve(strict=True)
    if not re.fullmatch("[0-9a-f]{40}", args.source_sha):
        raise ValueError("supply a full production commit SHA")
    if platform.machine() != "aarch64" or not output(["gcc", "-dumpmachine"]).startswith("aarch64"):
        raise ValueError("build on native ARM64 with native gcc")
    if "CONFIG_ARM64=y" not in seed.read_text().splitlines() or any(os.environ.get(f) for f in FLAGS):
        raise ValueError("use an ARM64 seed config and unset kernel compiler flag overrides")
    if output(["git", "-C", REPO, "rev-parse", args.source_sha + "^{commit}"]) != args.source_sha:
        raise ValueError("--source-sha must identify the commit itself")
    dest = args.directory.resolve()
    dest.mkdir(parents=True, exist_ok=False)
    evidence = dest / "evidence"
    evidence.mkdir()
    src, out = dest / "source", dest / "out"
    out.mkdir()
    harness_sha = args.harness_sha or (
        LEGACY_HARNESS if args.source_sha == LEGACY_HARNESS else HARNESS)
    state = dict(source_sha=args.source_sha, variant=args.variant, mode=args.mode, source=str(src),
                 output=str(out), compiler=output(["gcc", "--version"]), stage="prepare",
                 seed_sha256=sha256(seed), argv=sys.argv[1:])
    if args.mode == "n-distribution":
        state["k"] = args.k
    else:
        state["harness_sha"] = harness_sha
    print(evidence / "status.json", flush=True)
    env = dict(os.environ, LC_ALL="C")
    for key in ("MAKEFLAGS", "MFLAGS", "KBUILD_OUTPUT", "KBUILD_SRC", "KCONFIG_ALLCONFIG"):
        env.pop(key, None)
    make = ["make", "--no-print-directory", "-C", str(src), "O=" + str(out), "ARCH=arm64",
            "CC=gcc", "HOSTCC=gcc", "LD=ld", "CROSS_COMPILE=", "KCONFIG_CONFIG=.config",
            "LOCALVERSION=", "-j" + str(args.jobs)]
    with evidence_run(evidence, state), (evidence / "build.log").open("w") as log:
        def run(cmd):
            print(shlex.join([str(part) for part in cmd]), file=log, flush=True)
            subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, check=True)

        extract_tree(args.source_sha, src, log)
        if any((src / "net/ipv4" / name).exists()
               for name in ("b_benchmark.c", "b_verify_hooks.h")):
            raise ValueError("production source already contains a test overlay")
        if args.mode in ("performance", "observation"):
            harness = evidence / "harness"
            state["benchmark_sha256"] = export_harness(harness, harness_sha)
            run([sys.executable, "-B", harness / "apply_overlay.py", src, harness, evidence,
                 "--mode", args.mode])
            state["overlay_sha256"] = sha256(evidence / "instrumentation.patch")
        shutil.copy2(seed, evidence / "seed.config")
        config = out / ".config"
        shutil.copy2(evidence / "seed.config", config)
        suffix = f"-d30-{args.variant}-{args.mode}-k{args.k}-{args.source_sha[:8]}"
        values = config_values(args.variant, args.k, suffix, args.mode)
        edit = [src / "scripts/config", "--file", config]
        for symbol, value in values.items():
            if value in ("y", "n"):
                edit.extend(["--enable" if value == "y" else "--disable", symbol])
            elif symbol == "LOCALVERSION":
                edit.extend(["--set-str", symbol, suffix])
            else:
                edit.extend(["--set-val", symbol, value])
        run(edit)
        run(make + ["olddefconfig"])
        configured = dict(line.split("=", 1) for line in config.read_text().splitlines()
                          if line.startswith("CONFIG_"))
        for symbol, value in values.items():
            if configured.get("CONFIG_" + symbol, "n") != value:
                raise ValueError("Kconfig did not retain " + symbol)
        shutil.copy2(config, evidence / "kernel.config")
        state["config_sha256"] = sha256(config)
        with (evidence / "config.diff").open("w") as changes:
            subprocess.run([sys.executable, src / "scripts/diffconfig", evidence / "seed.config", config],
                           stdout=changes, check=True)
        targets = ["net/ipv4/tcp_output.o"] if args.object_only else ["Image", "modules", "dtbs"]
        if args.object_only and args.mode == "n-distribution":
            targets += ["net/ipv4/tcp_input.o", "net/ipv4/tcp_rtx_stats.o"]
        state["stage"] = "compile"
        write_status(evidence, state)
        run(make + targets)
        state["kernelrelease"] = (out / "include/config/kernel.release").read_text().strip()
        if not state["kernelrelease"].endswith(suffix) or sha256(config) != state["config_sha256"]:
            raise ValueError("release or configuration changed during build")
        if args.object_only:
            state.update(stage="object_only", object_sha256=sha256(out / targets[0]))
            if args.mode == "n-distribution":
                state["object_hashes"] = {name: sha256(out / name) for name in targets}
        else:
            state.update(artifacts(out), stage="built")


def boot_parameters(cmdline):
    result = {"iterations": 32, "warmup_ms": 10000}
    for key in result:
        prefix = "rtx_bench_" + key + "="
        matches = [arg[len(prefix):] for arg in shlex.split(cmdline) if arg.startswith(prefix)]
        if matches:
            if len(matches) != 1 or not re.fullmatch("0|[1-9][0-9]*", matches[0]):
                raise ValueError("supply one decimal boot parameter for " + key)
            result[key] = int(matches[0])
    if not (1 <= result["iterations"] <= 10000 and 0 <= result["warmup_ms"] <= 60000):
        raise ValueError("benchmark parameters out of range")
    return result


def native_smoke(directory):
    directory.mkdir()
    network = Network(directory)
    results = []
    try:
        network.setup()
        for size, mode in ((0, "transfer"), (4097, "transfer"), (1048576, "transfer"), (1, "reset")):
            results.append(network.case(0, False, size, mode))
    finally:
        errors = network.cleanup()
        json_file(directory / "cases.json", dict(cases=results, cleanup_errors=errors))
        if errors:
            raise ValueError("TCP smoke cleanup failed")
    return "D_NATIVE_TCP_SMOKE RTX_RUNTIME PASS"


def validate_samples(run, harness):
    with (run / "logs/collector.log").open("w") as log:
        subprocess.run([sys.executable, "-B", harness / "collect.py", run], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    counts = {}
    for line in (run / "samples.jsonl").read_text().splitlines():
        row = json.loads(line)
        key = row["workload_id"]
        group = counts.setdefault(key, {"attempts": 0, "successes": 0, "failures": 0})
        for field in group:
            group[field] += row[field]
    with (run / "operation-counts.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["workload_id", "attempts", "successes", "failures"])
        writer.writeheader()
        for workload, group in sorted(counts.items()):
            writer.writerow(dict(group, workload_id=workload))
    return sum(group["attempts"] for group in counts.values())


def collect(args, run, state):
    record = args.build_record.resolve(strict=True)
    identity = boot_identity(record)
    build_record = json.loads(record.read_text())
    harness_sha = build_record.get("harness_sha")
    if identity["mode"] not in ("performance", "observation") or harness_sha not in HARNESSES:
        raise ValueError("expected a full B benchmark kernel from the pinned harness")
    if os.geteuid():
        raise PermissionError("dmesg and the isolated TCP smoke require sudo")
    state.update(identity=identity, harness_sha=harness_sha)
    logs, configs = run / "logs", run / "configs"
    logs.mkdir()
    configs.mkdir()
    with (logs / "dmesg-before.raw").open("wb") as stream:
        subprocess.run(["dmesg", "--raw"], stdout=stream, check=True)
    harness = run / "harness"
    benchmark_hash = export_harness(harness, harness_sha)
    if (benchmark_hash != build_record["benchmark_sha256"]
            or sha256(record.parent / "instrumentation.patch") != build_record["overlay_sha256"]):
        raise ValueError("benchmark/overlay hash mismatch")
    shutil.copy2(record, configs / "build-record.json")
    shutil.copy2(Path(build_record["output"]) / ".config", configs / "kernel.config")
    for name in ("config.diff", "instrumentation.patch", "source-hashes.json"):
        shutil.copy2(record.parent / name, configs / name)
    cmdline = Path("/proc/cmdline").read_text()
    (logs / "cmdline.txt").write_text(cmdline)
    params = boot_parameters(cmdline)
    state["environment"] = capture_environment(run, "b-bench")
    smoke = native_smoke(run / "smoke")
    after = subprocess.check_output(["dmesg", "--raw"])
    (logs / "dmesg.raw").write_bytes(after)
    if not after.startswith((logs / "dmesg-before.raw").read_bytes()):
        raise ValueError("kernel log continuity lost during smoke")
    # B expects boot output and a userspace completion marker on the same stream.
    (logs / "serial.log").write_bytes(after + b"\n" + smoke.encode() + b"\n")
    manifest = dict(identity, **params, schema_version=1, contract_version="0.2",
                    run_id=identity["boot_id"], execution_mode="native_arm64_vm",
                    environment_id=sha256("/etc/machine-id"), host_arch=platform.machine(),
                    target_arch="aarch64", benchmark_sha256=benchmark_hash, harness_sha=harness_sha,
                    source_patch_sha256=hashlib.sha256(b"").hexdigest(),
                    instrumentation_patch_sha256=build_record["overlay_sha256"],
                    repeat=args.repeat, order=args.order, exit_code=0, timer="ktime_get_ns",
                    observation_run_id=None, observation_link_reason="pair_by_variant_and_repeat",
                    network="fixed-state synthetic skb; isolated veth for post-boot TCP smoke only",
                    pmu_status=state["environment"]["pmu_probe"], cycles=None)
    json_file(run / "manifest.json", manifest)
    state["operations"] = validate_samples(run, harness)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    b = commands.add_parser("build", help="create a fresh source copy and native build")
    b.add_argument("--directory", required=True, type=Path)
    b.add_argument("--source-sha", help="full production SHA; required for N distribution")
    b.add_argument("--harness-sha", choices=HARNESSES,
                   help="override the benchmark revision to reproduce an older build")
    b.add_argument("--base-config", required=True, type=Path)
    b.add_argument("--variant", choices=BACKENDS, required=True)
    b.add_argument("--mode", choices=("tcp", "performance", "observation", "n-distribution"), required=True)
    b.add_argument("--k", type=int, default=16)
    b.add_argument("--jobs", type=int, default=2)
    b.add_argument("--object-only", action="store_true", help="compile the relevant TCP objects only")
    c = commands.add_parser("collect", help="collect this boot once and validate using B's collector")
    c.add_argument("--build-record", required=True, type=Path)
    c.add_argument("--repeat", required=True, type=int, help="independent boot replicate, starts at 1")
    c.add_argument("--order", required=True, type=int, help="boot position in the agreed run plan")
    c.add_argument("--output-root", type=Path, default=PROJECT / ".work")
    args = parser.parse_args()
    if args.action == "build":
        if not 1 <= args.k <= 1024 or args.jobs < 1:
            parser.error("invalid K or jobs")
        if args.mode == "n-distribution" and (not args.source_sha or args.variant != "hybrid-tail" or args.harness_sha):
            parser.error("n-distribution needs an explicit --source-sha, --variant hybrid-tail and no harness")
        args.source_sha = args.source_sha or HARNESS
        build(args)
    else:
        if min(args.repeat, args.order) < 1:
            parser.error("repeat/order must start at 1")
        args.output_root.mkdir(parents=True, exist_ok=True)
        run = Path(tempfile.mkdtemp(prefix="d-b-native-", dir=args.output_root.resolve()))
        print(run, flush=True)
        state = dict(argv=sys.argv[1:])
        with evidence_run(run, state):
            collect(args, run, state)


if __name__ == "__main__":
    main()
