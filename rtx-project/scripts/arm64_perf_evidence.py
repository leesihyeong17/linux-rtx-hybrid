# SPDX-License-Identifier: GPL-2.0
"""Shared provenance checks for native performance runs."""
import csv
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import struct
import subprocess
from contextlib import contextmanager

from build_arm64 import PINS, sha256, write_status


def running_build_id(notes):
    offset = 0
    while offset + 12 <= len(notes):
        namesize, size, kind = struct.unpack_from("<III", notes, offset)
        start = offset + 12
        desc = start + ((namesize + 3) & ~3)
        offset = desc + ((size + 3) & ~3)
        if offset > len(notes):
            break
        if notes[start:start + namesize] == b"GNU\0" and kind == 3:
            return notes[desc:desc + size].hex()
    raise ValueError("no GNU build ID in running kernel notes")


def boot_identity(record):
    build = json.loads(record.read_text())
    release = platform.release()
    if (platform.machine() != "aarch64" or build["status"] != "PASS" or build["stage"] != "built"
            or build["kernelrelease"] != release or not re.fullmatch("[0-9a-f]{40}", build["source_sha"])):
        raise ValueError("build evidence does not identify the running ARM64 kernel")
    if "mode" not in build and build["source_sha"] not in PINS.values():
        raise ValueError("unknown build record without an explicit fixture mode")
    out = Path(build["output"])
    config = out / ".config"
    image = build["hashes"]["arch/arm64/boot/Image"]
    for path, expected in [(config, build["config_sha256"]),
                           (Path("/boot/config-" + release), build["config_sha256"]),
                           (Path("/boot/vmlinuz-" + release), image),
                           (out / "vmlinux", build["hashes"]["vmlinux"])]:
        if sha256(path) != expected:
            raise ValueError("artifact mismatch: " + str(path))
    if re.search(r"^CONFIG_TCP_RTX_(DEBUG|STATS)=y$", config.read_text(), re.M):
        raise ValueError("performance requires RTX DEBUG/STATS off")
    build_id = running_build_id(Path("/sys/kernel/notes").read_bytes())
    elf = subprocess.check_output(["readelf", "-n", str(out / "vmlinux")], text=True)
    if not re.search(r"Build ID: " + build_id + r"\b", elf):
        raise ValueError("booted kernel build ID differs from vmlinux")
    return dict(source_sha=build["source_sha"], config_sha256=build["config_sha256"],
                image_sha256=image, kernelrelease=release, build_id=build_id,
                boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                compiler=build["compiler"], mode=build.get("mode", "tcp"),
                variant=build.get("variant", release))


def capture_environment(run, label):
    script = Path(__file__).with_name("capture_env.sh")
    result = subprocess.run(["bash", str(script), "--output-root", str(run), "--label", label],
                            capture_output=True, text=True)
    (run / "capture-env.log").write_text(result.stdout + result.stderr)
    if result.returncode not in (0, 2):
        raise RuntimeError("capture_env failed")
    capture = Path(result.stdout.strip())
    with (capture / "statuses.tsv").open() as stream:
        present = {row["probe"] for row in csv.DictReader(stream, delimiter="\t") if row["status"] == "ok"}
    missing = {"kernel", "cpuinfo", "cpu-online", "meminfo", "kernel-command-line"} - present
    if missing:
        raise ValueError("incomplete measurement environment: " + ", ".join(sorted(missing)))
    pmu = "tool_missing"
    if shutil.which("perf"):
        try:
            probe = subprocess.run(["perf", "stat", "-x", ";", "-e", "cycles", "--",
                                    "python3", "-c", "sum(range(200000))"], capture_output=True,
                                   text=True, timeout=15, env=dict(os.environ, LC_ALL="C"))
        except (OSError, subprocess.TimeoutExpired) as exc:
            (run / "pmu-probe.log").write_text(str(exc))
            pmu = "unavailable"
        else:
            (run / "pmu-probe.log").write_text(probe.stdout + probe.stderr)
            pmu = "unavailable"
            for line in probe.stderr.splitlines():
                fields = line.split(";")
                if (not probe.returncode and len(fields) >= 3 and fields[2].strip() == "cycles"
                        and fields[0].strip().isdigit() and int(fields[0]) > 0):
                    pmu = "accessible"
    return dict(capture="partial" if result.returncode else "complete",
                pmu_probe=pmu, workload_cycles=None)


@contextmanager
def evidence_run(run, state):
    def interrupt(signum, frame):
        raise InterruptedError(f"interrupted by signal {signum}")
    handlers = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    state["status"] = "RUNNING"
    write_status(run, state)
    try:
        yield
        state["status"] = "PASS"
    except BaseException as exc:
        state.update(status="FAIL", error=str(exc))
        raise
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        write_status(run, state)
        with (run / "SHA256SUMS").open("w") as sums:
            for path in sorted(run.rglob("*")):
                if path.is_file() and not path.is_symlink() and path != run / "SHA256SUMS":
                    sums.write(f"{sha256(path)}  {path.relative_to(run)}\n")
        if "SUDO_UID" in os.environ and "SUDO_GID" in os.environ:
            for path in [*run.rglob("*"), run]:
                os.chown(path, int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"]),
                         follow_symlinks=False)
