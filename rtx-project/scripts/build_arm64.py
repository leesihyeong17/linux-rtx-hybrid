#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Build a prepared, pinned native ARM64 kernel; never install or reboot.

Run in tmux or a detached session. Each attempt has a new validation directory.
An exit code alone is insufficient: artifacts, source and config are rechecked.
"""
import argparse
import fcntl
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
import time

PINS = {"v0": "991defcc56cad49cfb221f44f5d161c9ea920c4a",
        "v1": "231db9ed6a204d6407d88954da60ce7db1c2dabd"}
FUNCTIONS = ("tcp_ack", "tcp_sacktag_walk", "tcp_sacktag_write_queue",
             "tcp_clean_rtx_queue", "tcp_shift_skb_data", "tcp_tso_acked",
             "tcp_trim_head", "rtx_find_cover")
FLAGS = ("KCFLAGS", "KCPPFLAGS", "KAFLAGS", "LDFLAGS_vmlinux",
         "CFLAGS_KERNEL", "CFLAGS_MODULE")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def output(command):
    return subprocess.check_output(command, text=True).strip()


def verify_source(source, variant):
    if output(["git", "-C", str(source), "rev-parse", "HEAD"]) != PINS[variant]:
        raise ValueError("source HEAD is not the pinned " + variant)
    if output(["git", "-C", str(source), "status", "--porcelain=v1",
               "--untracked-files=all"]):
        raise ValueError("source worktree is dirty")


def verify_config(build, variant):
    config = (build / ".config").read_text()
    if sha256(build / ".config") != sha256(build / "final.config"):
        raise ValueError("config changed since preparation")
    for option in ("ARM64", "NET_NS", "VETH", "NET_SCH_NETEM"):
        if "CONFIG_" + option + "=y" not in config.splitlines():
            raise ValueError("missing config: " + option)
    for option in ("TCP_RTX_STATS", "TCP_RTX_DEBUG"):
        if re.search(r"^CONFIG_" + option + r"=", config, re.M):
            raise ValueError("production validation requires " + option + " off")
    if 'CONFIG_LOCALVERSION="-rtx-' + variant + '"' not in config.splitlines():
        raise ValueError("wrong LOCALVERSION")
    if (build / "prepare-status.txt").read_text() != "exit_code=0\nstage=prepared\n":
        raise ValueError("preparation did not complete")
    return sha256(build / ".config")


def artifacts(build):
    files = ("vmlinux", "System.map", "arch/arm64/boot/Image", "modules.order")
    for name in files:
        if not (build / name).is_file() or (build / name).stat().st_size == 0:
            raise ValueError("missing/empty build artifact: " + name)
    modules = (build / "modules.order").read_text().splitlines()
    for name in modules:
        path = (build / name).resolve()
        if build.resolve() not in path.parents or path.suffix != ".ko":
            raise ValueError("invalid modules.order entry")
        if not path.is_file() or not path.stat().st_size:
            raise ValueError("missing module: " + name)
    dtbs = list((build / "arch/arm64/boot/dts").rglob("*.dtb"))
    if not dtbs:
        raise ValueError("no DTBs produced")
    return {"hashes": {name: sha256(build / name) for name in files},
            "module_count": len(modules), "dtb_count": len(dtbs)}


def run_logged(command, log, env):
    with log.open("w") as stream:
        return subprocess.call(command, stdout=stream, stderr=subprocess.STDOUT, env=env)


def write_status(run, value):
    temp = run / "status.tmp"
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(run / "status.json")


def build(args):
    source, dest = args.source.resolve(strict=True), args.output.resolve(strict=True)
    if source == dest or source in dest.parents or dest in source.parents:
        raise ValueError("source and build output must be separate")
    if platform.machine() != "aarch64":
        raise ValueError("native aarch64 is required")
    if not output(["gcc", "-dumpmachine"]).startswith("aarch64"):
        raise ValueError("gcc does not target aarch64")
    for flag in FLAGS:
        if os.environ.get(flag):
            raise ValueError("unset " + flag)
    verify_source(source, args.variant)
    config_hash = verify_config(dest, args.variant)
    if shutil.disk_usage(dest).free < 2 * 1024**3:
        raise ValueError("less than 2 GiB free; inspect disk before continuing")
    # One full build across all outputs of this checkout. Never delete the lock.
    lockfile = Path(__file__).resolve().parents[1] / ".work/arm64-build.lock"
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    with lockfile.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = Path(tempfile.mkdtemp(prefix="validation-", dir=str(dest)))
        print(str(run), flush=True)
        state = dict(status="RUNNING", stage="build", pid=os.getpid(),
                     started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     source=str(source), source_sha=PINS[args.variant],
                     output=str(dest), config_sha256=config_hash,
                     kernelrelease="5.4.214-rtx-" + args.variant,
                     running_kernel=platform.release(), compiler=output(["gcc", "--version"]),
                     install="NOT RUN", boot="NOT RUN")
        write_status(run, state)
        command = ["make", "--no-print-directory", "-C", str(source), "O=" + str(dest),
                   "ARCH=arm64", "CC=gcc", "HOSTCC=gcc", "HOSTCXX=g++", "LD=ld",
                   "HOSTLD=ld", "CROSS_COMPILE=", "KCONFIG_CONFIG=.config",
                   "LOCALVERSION=", "-j2"]
        env = dict(os.environ, LC_ALL="C", GIT_OPTIONAL_LOCKS="0")
        for key in ("MAKEFLAGS", "MFLAGS", "KBUILD_OUTPUT", "KBUILD_SRC", "KCONFIG_ALLCONFIG"):
            env.pop(key, None)
        (run / "command.txt").write_text(shlex.join(command + ["Image", "modules", "dtbs"]) + "\n")
        try:
            rc = run_logged(command + ["Image", "modules", "dtbs"], run / "build.log", env)
            (run / "build.exitcode").write_text(str(rc) + "\n")
            if rc:
                raise RuntimeError("make exited " + str(rc))
            state["stage"] = "verify"
            write_status(run, state)
            verify_source(source, args.variant)
            if verify_config(dest, args.variant) != config_hash:
                raise ValueError("configuration changed while building")
            release = subprocess.check_output(command + ["-s", "kernelrelease"], env=env, text=True).strip()
            if release != state["kernelrelease"]:
                raise ValueError("kernelrelease mismatch: " + release)
            state.update(artifacts(dest))
            symbols = output(["nm", "--defined-only", str(dest / "vmlinux")])
            rows = []
            for function in FUNCTIONS:
                matches = [line.split()[-1] for line in symbols.splitlines()
                           if line.split()[-1] == function or line.split()[-1].startswith(function + ".")]
                rows.append({"function": function, "nm_symbols": matches,
                             "ftrace": "NOT RUN (requires boot and available_filter_functions)"})
            (run / "symbols.json").write_text(json.dumps(rows, indent=2) + "\n")
            state.update(status="PASS", stage="built")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            state.update(status="FAIL", error=str(exc))
        finally:
            state["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_status(run, state)
        return 0 if state["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--variant", choices=PINS, required=True)
    args = parser.parse_args()
    try:
        return build(args)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("build_arm64: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
