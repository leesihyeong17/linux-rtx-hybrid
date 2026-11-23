#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Ordinary-process TCP integrity/half-close/reset endpoint (no privileged ops).

Reuses the deterministic payload and reset handshake of smoke.c, without its
PID-1, mount, alarm/reboot, or interface management code. Not a benchmark.
"""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import struct
import sys

PATTERN = bytes((i * 31 + 7) & 255 for i in range(256))


def payload(offset, count):
    return (PATTERN * ((count + 511) // 256))[offset % 256:offset % 256 + count]


def info(sock):
    raw = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    if len(raw) < 104:
        raise ValueError("short Linux TCP_INFO")
    # include/uapi/linux/tcp.h: first six u8 fields, then aligned u32 fields.
    return {"sack_negotiated": bool(raw[5] & 2),
            "unacked_segments": struct.unpack_from("=I", raw, 24)[0],
            "sacked_segments": struct.unpack_from("=I", raw, 28)[0],
            "lost_segments": struct.unpack_from("=I", raw, 32)[0],
            "total_retrans_segments": struct.unpack_from("=I", raw, 100)[0],
            "raw_hex": raw.hex()}


def receive_all(sock, limit):
    result = bytearray()
    while True:
        data = sock.recv(min(4096, limit + 1 - len(result)))
        if not data:
            return bytes(result)
        result.extend(data)
        if len(result) > limit:
            raise ValueError("oversized response")


def server_connection(sock, size, mode):
    if mode == "reset":
        if sock.recv(1) != b"\x5a":
            raise ValueError("missing reset handshake")
        result = {"status": "PASS", "mode": mode, "bytes": 1, "tcp_info": info(sock)}
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        return result
    digest = hashlib.sha256()
    received = 0
    while True:
        data = sock.recv(65536)
        if not data:
            break
        if received + len(data) > size or data != payload(received, len(data)):
            raise ValueError("payload corruption or excess data")
        digest.update(data)
        received += len(data)
    if received != size:
        raise ValueError("premature EOF: received %d expected %d" % (received, size))
    result = {"status": "PASS", "mode": mode, "bytes": received,
              "sha256": digest.hexdigest(), "half_close": True, "tcp_info": info(sock)}
    sock.sendall(json.dumps(result).encode("ascii"))
    sock.shutdown(socket.SHUT_WR)
    return result


def client_connection(sock, size, mode):
    if mode == "reset":
        sock.sendall(b"\x5a")
        try:
            sock.recv(1)
        except ConnectionResetError:
            return {"status": "PASS", "mode": mode, "bytes": 1, "reset_observed": True}
        raise ValueError("expected ECONNRESET, got data or orderly EOF")
    digest = hashlib.sha256()
    sent = 0
    while sent < size:
        data = payload(sent, min(65536, size - sent))
        sock.sendall(data)
        digest.update(data)
        sent += len(data)
    sock.shutdown(socket.SHUT_WR)
    peer = json.loads(receive_all(sock, 4096))
    if (peer.get("status") != "PASS" or peer.get("bytes") != size or
            peer.get("sha256") != digest.hexdigest() or peer.get("half_close") is not True):
        raise ValueError("receiver length/hash/half-close mismatch")
    return {"status": "PASS", "mode": mode, "bytes": sent, "sha256": digest.hexdigest(),
            "half_close": True, "tcp_info": info(sock), "peer": peer}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("server", "client"))
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bytes", type=int, default=1048576)
    parser.add_argument("--mode", choices=("transfer", "reset"), default="transfer")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--ready", type=Path)
    args = parser.parse_args()
    if args.bytes < 0 or not 0 <= args.port <= 65535 or args.timeout <= 0:
        parser.error("invalid size, port or timeout")
    try:
        with socket.socket() as sock:
            sock.settimeout(args.timeout)
            if args.role == "server":
                sock.bind((args.host, args.port))
                sock.listen(1)
                if args.ready:
                    with args.ready.open("x") as ready:
                        json.dump({"port": sock.getsockname()[1]}, ready)
                peer, _ = sock.accept()
                with peer:
                    peer.settimeout(args.timeout)
                    result = server_connection(peer, args.bytes, args.mode)
            else:
                sock.connect((args.host, args.port))
                result = client_connection(sock, args.bytes, args.mode)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "role": args.role, "mode": args.mode,
                          "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
