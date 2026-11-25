#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Run a gated or continuous application workload over a local TCP connection.

The receiver sends one numbered application receipt per group in both modes.
Only gated mode waits for each receipt before sending the next group. A write,
a receipt and a kernel skb are different units. This pilot measures neither
ACK cleanup batch sizes nor the time spent in kernel RTX functions.
Receiver wall time spans the send of its start signal through receipt of the
sender's EOF, including payload hashing and application receipts. CPU ticks
cover all CPUs in the VM over that interval at /proc/stat resolution.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import socket
import struct
import threading
import time


TIMEOUT = 30
RECEIPT = struct.Struct("!Q")


def kernel_id():
    try:
        notes = Path("/sys/kernel/notes").read_bytes()
    except OSError:
        return None
    offset = 0
    while offset + 12 <= len(notes):
        namesize, size, kind = struct.unpack_from("=III", notes, offset)
        name = offset + 12
        desc = name + ((namesize + 3) & ~3)
        offset = desc + ((size + 3) & ~3)
        if offset > len(notes):
            break
        if kind == 3 and notes[name:name + namesize] == b"GNU\0":
            return notes[desc:desc + size].hex()
    return None


def busy_ticks():
    # user/nice/system/IRQ/softirq. Idle, iowait and steal are excluded.
    with open("/proc/stat") as stream:
        cpu = stream.readline().split()
    return sum(int(cpu[i]) for i in (1, 2, 3, 6, 7))


def receive(listener, args):
    with listener:
        conn, _ = listener.accept()
    with conn:
        conn.settimeout(TIMEOUT)
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        digest = hashlib.sha256()
        total = 0
        conn.sendall(b"R")
        if conn.recv(1) != b"S":
            raise ValueError("missing start marker")
        tick_start = busy_ticks()
        start = time.monotonic_ns()
        conn.sendall(b"G")
        for group in range(1, args.groups + 1):
            remaining = args.chunks_per_group * args.chunk_bytes
            while remaining:
                data = conn.recv(min(remaining, 65536))
                if not data:
                    raise ValueError("sender closed before the last group")
                digest.update(data)
                total += len(data)
                remaining -= len(data)
            conn.sendall(RECEIPT.pack(group))
        if conn.recv(1):
            raise ValueError("unexpected bytes beyond the last group")
        elapsed = time.monotonic_ns() - start
        report = dict(received_bytes=total, receiver_sha256=digest.hexdigest(),
                      elapsed_ns=elapsed, vm_busy_ticks=busy_ticks() - tick_start,
                      clk_tck=os.sysconf("SC_CLK_TCK"))
        conn.sendall(json.dumps(report).encode("ascii"))


def send(sock, args, chunk):
    sock.settimeout(TIMEOUT)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    with sock.makefile("rb") as reader:
        if reader.read(1) != b"R":
            raise ValueError("receiver did not signal readiness")
        changed = threading.Condition()
        started = threading.Event()
        received = 0
        failure = None

        def read_receipts():
            nonlocal received, failure
            try:
                if reader.read(1) != b"G":
                    raise ValueError("missing timed receiver start signal")
                started.set()
                for group in range(1, args.groups + 1):
                    if reader.read(RECEIPT.size) != RECEIPT.pack(group):
                        raise ValueError("missing or out-of-order application receipt")
                    with changed:
                        received = group
                        changed.notify()
                return json.loads(reader.read(4096))
            except Exception as exc:
                with changed:
                    failure = exc
                    changed.notify()
                started.set()
                raise

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(read_receipts)
            try:
                sock.sendall(b"S")
                if not started.wait(TIMEOUT):
                    raise TimeoutError("receiver did not start its timer")
                if failure:
                    raise failure
                for group in range(1, args.groups + 1):
                    for _ in range(args.chunks_per_group):
                        sock.sendall(chunk)
                    if args.pattern == "gated":
                        with changed:
                            if not changed.wait_for(lambda: received == group or failure, TIMEOUT):
                                raise TimeoutError("application receipt timed out")
                            if failure:
                                raise failure
                sock.shutdown(socket.SHUT_WR)
                report = result.result(timeout=TIMEOUT)
                report["receipts_validated"] = received
                return report
            finally:
                # Also unblock the reader on a failed write or an interrupted trial.
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def run(args):
    bid = kernel_id()
    if args.expect_build_id and bid != args.expect_build_id.lower():
        raise ValueError("expected kernel build ID %s, running %s" % (args.expect_build_id, bid))
    chunk = (bytes(range(256)) * ((args.chunk_bytes + 255) // 256))[:args.chunk_bytes]
    expected = hashlib.sha256()
    writes = args.groups * args.chunks_per_group
    for _ in range(writes):
        expected.update(chunk)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", args.port))
        listener.listen(1)
        listener.settimeout(TIMEOUT)
        address = listener.getsockname()
        # Spawn so the receiver has a separate interpreter and no sender file descriptors.
        receiver = multiprocessing.get_context("spawn").Process(target=receive, args=(listener, args))
        receiver.start()
        try:
            with socket.create_connection(address, timeout=TIMEOUT) as sock:
                report = send(sock, args, chunk)
            receiver.join(TIMEOUT)
            if receiver.exitcode != 0:
                raise RuntimeError("receiver did not exit successfully")
        finally:
            if receiver.is_alive():
                receiver.terminate()
                receiver.join()
    sent = writes * args.chunk_bytes
    if report["received_bytes"] != sent or report["receiver_sha256"] != expected.hexdigest():
        raise ValueError("received payload did not match the sender's length and SHA-256")
    if report["receipts_validated"] != args.groups:
        raise ValueError("receipt count did not match the group count")
    return dict(status="PASS", pattern=args.pattern, topology="ipv4-loopback", port=address[1],
                groups=args.groups, chunks_per_group=args.chunks_per_group, chunk_bytes=args.chunk_bytes,
                sent_bytes=sent, sender_sha256=expected.hexdigest(), data_write_calls=writes,
                kernel_release=platform.release(), build_id=bid,
                boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                goodput_mbps=sent * 8000 / report["elapsed_ns"], **report)


def positive_int(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return n


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pattern", choices=("gated", "continuous"))
    p.add_argument("--groups", type=positive_int, default=64)
    p.add_argument("--chunks-per-group", type=positive_int, default=8)
    p.add_argument("--chunk-bytes", type=positive_int, default=4096)
    p.add_argument("--port", type=int, default=0, help="receiver loopback port (0 selects a free port)")
    p.add_argument("--expect-build-id", help="require this running kernel GNU build ID before the trial")
    args = p.parse_args()
    if not 0 <= args.port <= 65535:
        p.error("--port must be in 0..65535")
    if platform.system() != "Linux":
        p.error("run the pilot inside the Linux VM")
    print(json.dumps(run(args), sort_keys=True))


if __name__ == "__main__":
    main()
