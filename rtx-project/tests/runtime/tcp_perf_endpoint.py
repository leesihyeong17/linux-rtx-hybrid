#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Bulk goodput and persistent request/response endpoints for a single Linux VM."""
import argparse
from array import array
import json
import math
import os
from pathlib import Path
import socket
import struct
import time


def busy_ticks():
    with open("/proc/stat") as stream:
        cpu = stream.readline().split()
    # user/nice already include guest time. Exclude idle, iowait and steal.
    return sum(int(cpu[i]) for i in (1, 2, 3, 6, 7, 8))


def socket_memory(sock):
    # ARM64 Linux SO_MEMINFO / enum sk_meminfo
    mem = struct.unpack("=9I", sock.getsockopt(socket.SOL_SOCKET, 55, 36))
    return dict(rmem_alloc=mem[0], wmem_alloc=mem[2], wmem_queued=mem[5], forward_alloc=mem[4])


def exact(sock, size):
    data = bytearray()
    while len(data) < size:
        piece = sock.recv(size - len(data))
        if not piece:
            raise ValueError("incomplete response")
        data.extend(piece)
    return data


def server(sock, workload):
    total = 0
    sock.settimeout(30)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    start_ticks = busy_ticks()
    start = time.monotonic_ns()
    sock.sendall(b"R")
    while True:
        data = sock.recv(65536)
        if not data:
            break
        total += len(data)
        if workload == "rr":
            sock.sendall(data)
    elapsed = time.monotonic_ns() - start
    ticks = busy_ticks() - start_ticks
    memory = socket_memory(sock)
    sock.sendall(struct.pack("!Q", total))
    return dict(bytes=total, elapsed_ns=elapsed, vm_busy_ticks=ticks, clk_tck=os.sysconf("SC_CLK_TCK"),
                final_socket_bytes=memory)


def client(sock, workload, seconds, latency_file):
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if exact(sock, 1) != b"R":
        raise ValueError("receiver not ready")
    payload = bytes(65536 if workload == "bulk" else 64)
    times = array("Q")
    total = 0
    start = time.monotonic_ns()
    end = start + int(seconds * 1e9)
    sample_at = start
    peak = {}
    while time.monotonic_ns() < end:
        sent = time.monotonic_ns()
        sock.sendall(payload)
        if workload == "rr":
            if exact(sock, len(payload)) != payload:
                raise ValueError("echo mismatch")
            times.append(time.monotonic_ns() - sent)
        total += len(payload)
        if sent >= sample_at:
            for key, value in socket_memory(sock).items():
                peak[key] = max(peak.get(key, 0), value)
            sample_at = sent + 100_000_000
    sock.shutdown(socket.SHUT_WR)
    if total == 0 or struct.unpack("!Q", exact(sock, 8))[0] != total:
        raise ValueError("receiver byte count mismatch")
    result = dict(bytes=total, sampled_peak_socket_bytes=peak, requests=len(times))
    if workload == "rr":
        with latency_file.open("xb") as out:
            times.tofile(out)
        ordered = sorted(times)
        for p in (50, 99):
            result[f"p{p}_us"] = ordered[math.ceil(len(ordered) * p / 100) - 1] / 1000
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("role", choices=("server", "client"))
    p.add_argument("workload", choices=("bulk", "rr"))
    p.add_argument("host")
    p.add_argument("directory", type=Path)
    p.add_argument("--seconds", type=float, required=True)
    args = p.parse_args()
    ready = args.directory / "port"
    with socket.socket() as s:
        s.settimeout(30)
        if args.role == "server":
            s.bind((args.host, 0))
            s.listen(1)
            tmp = ready.with_suffix(".tmp")
            tmp.write_text(str(s.getsockname()[1]))
            tmp.replace(ready)
            conn, _ = s.accept()
            with conn:
                record = server(conn, args.workload)
        else:
            s.connect((args.host, int(ready.read_text())))
            record = client(s, args.workload, args.seconds, args.directory / "latency-ns.u64")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
