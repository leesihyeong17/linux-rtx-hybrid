"""Run D's TCP endpoint and metrics inside a disposable ARM64 guest."""
import base64
import ctypes
import resource
import struct
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import traceback
sys.path.insert(0, "/rtx-project/scripts")
from run_arm64_tcp_perf import Network, netem, trial

def output(*args):
    return subprocess.check_output(args, text=True)

def pmu_probe():
    # ARM64 perf_event_open, hardware CPU cycles, current task, user space.
    attr = ctypes.create_string_buffer(112)
    struct.pack_into("=IIQ", attr, 0, 0, len(attr), 0)
    struct.pack_into("=Q", attr, 40, (1 << 5) | (1 << 6))
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.syscall(241, ctypes.byref(attr), 0, -1, -1, 0)
    if fd < 0:
        return {"errno": ctypes.get_errno()}
    try:
        first, = struct.unpack("=Q", os.read(fd, 8))
        sum(range(100000))
        last, = struct.unpack("=Q", os.read(fd, 8))
        return {"counted": last > first}
    finally:
        os.close(fd)

def run():
    out = Path("/results")
    out.mkdir()
    net = Network(out)
    cmdline = Path("/proc/cmdline").read_text().split()
    variant = next(s.split("=")[1] for s in cmdline if s.startswith("comparison="))
    stats = Path("/proc/rtx_stats")
    rows = []
    (out/"pmu.json").write_text(json.dumps(pmu_probe()))
    try:
        if variant.endswith("-micro"):
            return
        net.setup()
        for condition, loss, rtt, reorder, sack in [
                ("clean", 0, 0, 0, 1), ("impaired", 0.5, 4, 10, 1),
                ("no-sack", 0.5, 4, 10, 0)]:
            for side, (ns, link) in enumerate(zip(net.names, net.links)):
                base = ["ip", "netns", "exec", ns]
                net.command(base + ["/bin/busybox","sysctl","-w",f"net.ipv4.tcp_sack={sack}"])
                net.command(base + ["tc","qdisc","replace","dev",link,"root","netem"] +
                            netem(loss, rtt, reorder if side==0 else 0))
                if condition == "clean":
                    net.command(base + ["ethtool","-k",link])
            for workload in ("bulk","rr"):
                prefix = condition + "-" + workload
                trial(net, out/(prefix+"-warm"), workload, 0.25, [None,None])
                if stats.exists():
                    stats.write_text("0\n")
                before = resource.getrusage(resource.RUSAGE_CHILDREN)
                value = trial(net, out/prefix, workload, 1.0, [None,None])
                after = resource.getrusage(resource.RUSAGE_CHILDREN)
                value["peer_user_s"] = after.ru_utime - before.ru_utime
                value["peer_system_s"] = after.ru_stime - before.ru_stime
                if stats.exists():
                    (out/(prefix+".stats")).write_text(stats.read_text())
                rows.append(dict(condition=condition,workload=workload,**value))
        (out/"metrics.json").write_text(json.dumps(rows))
    finally:
        if net.cleanup():
            raise RuntimeError("network cleanup")
        (out/"boot-id").write_text(Path("/proc/sys/kernel/random/boot_id").read_text())
        (out/"cmdline").write_text(" ".join(cmdline))
        (out/"cpuinfo").write_text(Path("/proc/cpuinfo").read_text())
        (out/"slabinfo").write_text(Path("/proc/slabinfo").read_text() if Path("/proc/slabinfo").exists() else "unavailable")
        # Preserve endpoint samples and command results; raw TCP timings retain
        # their native little-endian u64 format inside the archive.
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer,mode="w:gz") as archive:
            archive.add(out,arcname="results")
        print("RTXDATA "+base64.b64encode(buffer.getvalue()).decode(),flush=True)

try:
    run()
    print("RTX16 PASS",flush=True)
except Exception:
    traceback.print_exc()
    print("RTX16 FAIL",flush=True)
