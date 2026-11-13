#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Compile skb offsets using a pinned ARM64 build; never load probes or modules."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile

from build_arm64 import FLAGS, PINS, output, sha256, verify_config, verify_source

PROJECT = Path(__file__).resolve().parents[1]
FIELDS = ("SKB_CB", "CB_SEQ", "CB_END_SEQ", "CB_SEGS", "SEQ_SIZE", "END_SEQ_SIZE", "SEGS_SIZE")


def parse_offsets(assembly):
    offsets = {}
    for name in FIELDS:
        values = re.findall(r"->" + name + r"\s+[$#]?(\d+)\s", assembly)
        if len(values) != 1:
            raise ValueError("missing/duplicate compiler offset: " + name)
        offsets[name] = int(values[0])
    if [offsets[k] for k in ("SEQ_SIZE", "END_SEQ_SIZE", "SEGS_SIZE")] != [4, 4, 2]:
        raise ValueError("unexpected TCP skb field widths")
    return offsets


def probe_definitions(offsets):
    seq, end, segs = (offsets["SKB_CB"] + offsets[name] for name in ("CB_SEQ", "CB_END_SEQ", "CB_SEGS"))
    return (f"p:rtx_trim tcp_trim_head len=%x2:u32 seq=+{seq}(%x1):u32 "
            f"end_seq=+{end}(%x1):u32 segs=+{segs}(%x1):u16\n"
            "r:rtx_trim_ret tcp_trim_head ret=$retval:s32\n")


def verify(args):
    if platform.machine() != "aarch64" or not output(["gcc", "-dumpmachine"]).startswith("aarch64"):
        raise ValueError("native ARM64 compiler required")
    for key in FLAGS:
        if os.environ.get(key):
            raise ValueError("unset " + key)
    source, build = args.source.resolve(strict=True), args.build_output.resolve(strict=True)
    root = PROJECT / ".work"
    root.mkdir(exist_ok=True)
    with (root / "arm64-build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_source(source, args.variant)
        config_hash = verify_config(build, args.variant)
        vmlinux_hash = sha256(build / "vmlinux")
        evidence = []
        for path in build.glob("validation-*/status.json"):
            status = json.loads(path.read_text())
            if (status.get("status") == "PASS" and status.get("source_sha") == PINS[args.variant]
                    and status.get("config_sha256") == config_hash
                    and status.get("hashes", {}).get("vmlinux") == vmlinux_hash):
                evidence.append(str(path))
        if not evidence:
            raise ValueError("no completed build matches this source/config/vmlinux")
        run = Path(tempfile.mkdtemp(prefix="d-trim-layout-", dir=str(root)))
        print(run, flush=True)
        shutil.copy2(PROJECT / "tests/runtime/arm64_trim_offsets.c", run / "offsets.c")
        (run / "Makefile").write_text("obj-m := offsets.o\n")
        command = ["make", "--no-print-directory", "-C", str(source), "O=" + str(build),
                   "M=" + str(run), "ARCH=arm64", "CC=gcc", "HOSTCC=gcc", "HOSTCXX=g++",
                   "LD=ld", "HOSTLD=ld", "CROSS_COMPILE=", "KCONFIG_CONFIG=.config", "LOCALVERSION=", "offsets.s"]
        env = dict(os.environ, LC_ALL="C", GIT_OPTIONAL_LOCKS="0")
        for key in ("MAKEFLAGS", "MFLAGS", "KBUILD_OUTPUT", "KBUILD_SRC", "KCONFIG_ALLCONFIG"):
            env.pop(key, None)
        (run / "command.txt").write_text(shlex.join(command) + "\n")
        state = dict(status="RUNNING", source_sha=PINS[args.variant], config_sha256=config_hash,
                     vmlinux_sha256=vmlinux_hash, build_evidence=evidence,
                     compiler=output(["gcc", "--version"]), runtime_probe="NOT RUN")
        try:
            with (run / "build.log").open("w") as stream:
                result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=120)
            (run / "build.exitcode").write_text(str(result.returncode) + "\n")
            if result.returncode:
                raise ValueError("offset compile failed; inspect build.log")
            verify_source(source, args.variant)
            if verify_config(build, args.variant) != config_hash or sha256(build / "vmlinux") != vmlinux_hash:
                raise ValueError("kernel build changed during offset verification")
            offsets = parse_offsets((run / "offsets.s").read_text())
            probes = probe_definitions(offsets)
            (run / "probes.txt").write_text(probes)
            state.update(status="PASS", offsets=offsets, probe_definitions=probes,
                         assembly_sha256=sha256(run / "offsets.s"), probe_source_sha256=sha256(run / "offsets.c"))
        finally:
            if state["status"] != "PASS":
                state["status"] = "FAIL"
            (run / "layout.json").write_text(json.dumps(state, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=PINS, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, "verify_arm64_trim_layout: " + str(exc) + "\n")


if __name__ == "__main__":
    main()
