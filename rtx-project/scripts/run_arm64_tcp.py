#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Run bounded TCP correctness tests in two newly owned netns; requires root.

Never change the management interface. No boot, installation, global sysctls,
offload changes, or global tracing changes. This is not a performance benchmark.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from build_arm64 import PINS, sha256, verify_config

PROJECT = Path(__file__).resolve().parents[1]
ENDPOINT = PROJECT / "tests/runtime/tcp_endpoint.py"
_spec = importlib.util.spec_from_file_location("tcp_pcap", PROJECT / "tests/runtime/check_tcp_pcap.py")
_pcap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pcap)


def validate_pair(client, server, size, mode):
    for record in (client, server):
        if record.get("status") != "PASS" or record.get("mode") != mode:
            raise ValueError("missing endpoint success")
    if mode == "reset":
        if client.get("reset_observed") is not True or server.get("bytes") != 1:
            raise ValueError("reset not observed")
    else:
        for record in (client, server):
            if record.get("bytes") != size or record.get("half_close") is not True:
                raise ValueError("length or half-close mismatch")
            if not re.fullmatch(r"[a-f0-9]{64}", record.get("sha256", "")):
                raise ValueError("missing SHA256")
        if client["sha256"] != server["sha256"]:
            raise ValueError("hash mismatch")
        if client.get("peer") != server:
            raise ValueError("peer response differs from server evidence")


class Network:
    def __init__(self, run):
        self.run = run
        token = uuid.uuid4().hex[:8]
        self.names = ["rtxda-" + token, "rtxdb-" + token]
        self.links = ["da" + token, "db" + token]
        self.owned = []
        self.children = []
        self.counter = 0

    def command(self, args, required=True):
        self.counter += 1
        with (self.run / "commands.log").open("a") as commands:
            commands.write(shlex.join(args) + "\n")
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        (self.run / ("command-%03d.log" % self.counter)).write_text(result.stdout + result.stderr)
        with (self.run / "commands.log").open("a") as commands:
            commands.write("exit_code=%d\n" % result.returncode)
        if required and result.returncode:
            raise RuntimeError("command failed: " + shlex.join(args))
        return result

    def setup(self):
        for name in self.names:
            self.command(["ip", "netns", "add", name])
            self.owned.append(name)
            (self.run / "owned-netns.json").write_text(json.dumps(self.owned))
        a, b = self.names
        va, vb = self.links
        self.command(["ip", "-n", a, "link", "add", va, "type", "veth", "peer", "name", vb])
        self.command(["ip", "-n", a, "link", "set", vb, "netns", b])
        for index, (ns, link) in enumerate(zip(self.names, self.links), 1):
            self.command(["ip", "-n", ns, "addr", "add", "198.18.0.%d/24" % index, "dev", link])
            self.command(["ip", "-n", ns, "link", "set", "lo", "up"])
            self.command(["ip", "-n", ns, "link", "set", link, "up"])
            self.command(["ip", "netns", "exec", ns, "ethtool", "-k", link], required=False)
            self.command(["ip", "netns", "exec", ns, "sysctl", "net.ipv4.tcp_sack"])

    def loss(self, percent):
        # Both egress directions, recorded explicitly. No physical NIC touched.
        for ns, link in zip(self.names, self.links):
            self.command(["ip", "netns", "exec", ns, "tc", "qdisc", "replace", "dev", link,
                          "root", "netem", "loss", str(percent) + "%"])

    def spawn(self, args, log):
        with (self.run / "commands.log").open("a") as commands:
            commands.write(shlex.join(args) + " > " + shlex.quote(str(log)) + "\n")
        with log.open("w") as stream:
            child = subprocess.Popen(args, stdout=stream, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        self.children.append(child)
        return child

    def start_capture(self):
        # -U flushes libpcap output, but does not disable the kernel's packet
        # block timeout. Fast smoke cases can finish before the first block.
        self.capture = self.spawn(["ip", "netns", "exec", self.names[0], "tcpdump", "-n", "--immediate-mode",
                                   "-Z", "root", "-i", self.links[0], "-s", "192", "-U", "-w",
                                   str(self.run / "traffic.pcap"), "tcp"], self.run / "tcpdump.log")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.capture.poll() is not None:
                raise RuntimeError("tcpdump exited before ready")
            if "listening on " in (self.run / "tcpdump.log").read_text():
                return
            time.sleep(0.02)
        raise RuntimeError("tcpdump readiness timeout")

    def finish_capture(self):
        if self.capture.poll() is not None:
            raise RuntimeError("tcpdump stopped during tests")
        os.killpg(self.capture.pid, signal.SIGINT)
        rc = self.capture.wait(timeout=5)
        (self.run / "tcpdump.exitcode").write_text(str(rc) + "\n")
        if rc:
            raise RuntimeError("tcpdump failed")
        dropped = re.search(r"(\d+) packets dropped by kernel", (self.run / "tcpdump.log").read_text())
        if not dropped or int(dropped[1]):
            raise ValueError("capture loss or missing drop counters")
        result = _pcap.summarize(self.run / "traffic.pcap")
        result["sack_packets"] = "OBSERVED" if result["sack_block_packets"] else "NOT OBSERVED"
        result["kernel_path"] = "NOT RUN (packet capture is not internal path evidence)"
        return result

    def case(self, loss, reverse, size, mode):
        label = "%s-%s-%s-%d" % (loss, "reverse" if reverse else "forward", mode, size)
        sender, receiver = self.names[::-1] if reverse else self.names
        address = "198.18.0.1" if reverse else "198.18.0.2"
        ready = self.run / (label + ".ready")
        common = [sys.executable, str(ENDPOINT)]
        options = ["--host", address, "--bytes", str(size), "--mode", mode]
        server_log, client_log = self.run / (label + "-server.json"), self.run / (label + "-client.json")
        server = self.spawn(["ip", "netns", "exec", receiver] + common + ["server"] + options +
                            ["--port", "0", "--ready", str(ready)], server_log)
        deadline = time.monotonic() + 5
        port = None
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("server exited before ready: " + label)
            if ready.exists():
                try:
                    port = json.loads(ready.read_text())["port"]
                    break
                except (ValueError, KeyError):
                    pass
            time.sleep(0.02)
        if port is None:
            raise RuntimeError("server readiness timeout: " + label)
        client = self.spawn(["ip", "netns", "exec", sender] + common + ["client"] + options +
                            ["--port", str(port)], client_log)
        client_rc = client.wait(timeout=40)
        server_rc = server.wait(timeout=5)
        record = dict(case=label, status="FAIL", loss_percent_each_egress=loss,
                      client_exit=client_rc, server_exit=server_rc)
        if client_rc or server_rc:
            raise RuntimeError("endpoint failure: " + label)
        c, s = json.loads(client_log.read_text()), json.loads(server_log.read_text())
        validate_pair(c, s, size, mode)
        record.update(status="PASS", bytes=c["bytes"], sha256=c.get("sha256"),
                      sack_negotiated=c.get("tcp_info", {}).get("sack_negotiated"),
                      sack_path="NOT RUN (negotiation does not prove SACK processing)")
        return record

    def cleanup(self):
        failures = []
        for child in self.children:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=3)
                except ProcessLookupError:
                    child.wait(timeout=3)
        for ns in reversed(self.owned):
            try:
                if self.command(["ip", "netns", "delete", ns], required=False).returncode:
                    failures.append(ns)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append(ns + ": " + str(exc))
        return failures


def boot_evidence(build, variant):
    release = "5.4.214-rtx-" + variant
    if platform.release() != release:
        raise ValueError("boot required: running " + platform.release() + ", expected " + release)
    config = verify_config(build, variant)
    if sha256(Path("/boot/config-" + release)) != config:
        raise ValueError("installed configuration does not match prepared build")
    if sha256(Path("/boot/vmlinuz-" + release)) != sha256(build / "arch/arm64/boot/Image"):
        raise ValueError("installed image does not match this build")
    return dict(source_sha=PINS[variant], config_sha256=config, kernelrelease=release,
                boot_checks="uname release, installed image and config hashes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=PINS)
    parser.add_argument("--build-output", type=Path)
    parser.add_argument("--smoke-current-kernel", action="store_true",
                        help="validate harness only; never labels results V0/V1")
    parser.add_argument("--output-root", type=Path, default=PROJECT / ".work")
    args = parser.parse_args()
    if args.smoke_current_kernel and (args.variant or args.build_output):
        parser.error("current-kernel smoke cannot be combined with a variant/build")
    if not args.smoke_current_kernel and not (args.variant and args.build_output):
        parser.error("supply variant/build-output, or explicitly select current-kernel smoke")
    if os.geteuid() != 0:
        print("BLOCKED: root is required to create dedicated network namespaces", file=sys.stderr)
        return 2
    if platform.machine() != "aarch64":
        parser.error("this validation runner requires the native ARM64 VM")
    args.output_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="d-tcp-", dir=str(args.output_root.resolve())))
    print(run, flush=True)
    state = dict(status="RUNNING", running_kernel=platform.release(), cases=[],
                 scope="harness smoke on existing kernel" if args.smoke_current_kernel else args.variant,
                 endpoint_sha256=sha256(ENDPOINT), runner_sha256=sha256(__file__),
                 performance="NOT RUN", partial_ack="NOT RUN", dsack="NOT RUN", reneging="NOT RUN")
    network = Network(run)
    def save():
        (run / "summary.json").write_text(json.dumps(state, indent=2) + "\n")
    def interrupted(signum, frame):
        raise InterruptedError("signal " + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    save()
    try:
        if not args.smoke_current_kernel:
            state.update(boot_evidence(args.build_output.resolve(), args.variant))
        before = network.command(["dmesg", "--raw"], required=False)
        before_text = before.stdout.splitlines()
        network.setup()
        network.start_capture()
        for loss in (0, 1):
            network.loss(loss)
            for reverse in (False, True):
                for size, mode in ((0, "transfer"), (4097, "transfer"),
                                   (1048576, "transfer"), (1, "reset")):
                    state["cases"].append(network.case(loss, reverse, size, mode))
                    save()
            for ns, link in zip(network.names, network.links):
                network.command(["ip", "netns", "exec", ns, "tc", "-s", "qdisc", "show", "dev", link])
        state["packet_capture"] = network.finish_capture()
        after = network.command(["dmesg", "--raw"], required=False)
        after_text = after.stdout.splitlines()
        if before.returncode or after.returncode or not before_text or after_text[:len(before_text)] != before_text:
            state.update(status="SKIP", kernel_log="unavailable or ring buffer continuity lost")
        else:
            delta = "\n".join(after_text[len(before_text):])
            (run / "kernel-new.log").write_text(delta + "\n")
            errors = re.findall(r"(?im)^.*(?:WARNING:|BUG:|Oops:|Kernel panic|KASAN:|soft lockup).*$", delta)
            state.update(status="FAIL" if errors else "PASS", kernel_errors=errors,
                         kernel_log="captured with retained baseline")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        state.update(status="FAIL", error=str(exc))
    finally:
        # Complete ownership-based cleanup even after Ctrl-C.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        errors = network.cleanup()
        state["cleanup_errors"] = errors
        if errors:
            state["status"] = "FAIL"
        save()
        if "SUDO_UID" in os.environ and "SUDO_GID" in os.environ:
            uid, gid = int(os.environ["SUDO_UID"]), int(os.environ["SUDO_GID"])
            for path in run.rglob("*"):
                os.chown(path, uid, gid, follow_symlinks=False)
            os.chown(run, uid, gid)
    return {"PASS": 0, "SKIP": 2}.get(state["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
