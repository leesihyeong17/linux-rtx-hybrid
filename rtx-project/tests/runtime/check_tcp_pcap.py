#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Count IPv4 TCP SACK options in an Ethernet classic pcap (not path proof)."""
import json
from pathlib import Path
import struct
import sys


def summarize(path):
    counts = dict(packets=0, tcp_packets=0, sack_permitted_packets=0,
                  sack_block_packets=0, fragmented_ipv4_packets=0)
    with Path(path).open("rb") as stream:
        header = stream.read(24)
        if len(header) != 24:
            raise ValueError("truncated pcap header")
        endians = {b"\xd4\xc3\xb2\xa1": "<", b"\xa1\xb2\xc3\xd4": ">",
                   b"\x4d\x3c\xb2\xa1": "<", b"\xa1\xb2\x3c\x4d": ">"}
        endian = endians.get(header[:4])
        if endian is None:
            raise ValueError("unsupported pcap format")
        major, minor, _, _, snaplen, linktype = struct.unpack(endian + "HHIIII", header[4:])
        if (major, minor) != (2, 4) or linktype != 1 or not 0 < snaplen <= 1048576:
            raise ValueError("requires Ethernet classic pcap 2.4")
        while True:
            record = stream.read(16)
            if not record:
                break
            if len(record) != 16:
                raise ValueError("truncated record header")
            _, _, length, original = struct.unpack(endian + "IIII", record)
            if length > snaplen or length > original:
                raise ValueError("invalid captured length")
            frame = stream.read(length)
            if len(frame) != length or length < 14:
                raise ValueError("truncated Ethernet frame")
            counts["packets"] += 1
            protocol = struct.unpack_from("!H", frame, 12)[0]
            start = 14
            while protocol in (0x8100, 0x88a8):
                if len(frame) < start + 4:
                    raise ValueError("truncated VLAN")
                protocol = struct.unpack_from("!H", frame, start + 2)[0]
                start += 4
            if protocol != 0x0800:
                continue
            ip = frame[start:]
            if len(ip) < 20 or ip[0] >> 4 != 4:
                raise ValueError("invalid IPv4 header")
            ihl = (ip[0] & 15) * 4
            total = struct.unpack_from("!H", ip, 2)[0]
            if ihl < 20 or len(ip) < ihl or total < ihl:
                raise ValueError("invalid IPv4 length")
            if struct.unpack_from("!H", ip, 6)[0] & 0x3fff:
                counts["fragmented_ipv4_packets"] += 1
                continue
            if ip[9] != 6:
                continue
            tcp = ip[ihl:min(total, len(ip))]
            if len(tcp) < 20:
                raise ValueError("truncated TCP header")
            hlen = (tcp[12] >> 4) * 4
            if hlen < 20 or hlen > len(tcp):
                raise ValueError("truncated TCP options")
            counts["tcp_packets"] += 1
            position = 20
            while position < hlen:
                kind = tcp[position]
                if kind == 0:
                    break
                if kind == 1:
                    position += 1
                    continue
                if position + 1 >= hlen:
                    raise ValueError("missing TCP option length")
                size = tcp[position + 1]
                if size < 2 or position + size > hlen:
                    raise ValueError("invalid TCP option length")
                if kind == 4:
                    if size != 2:
                        raise ValueError("malformed SACK-permitted option")
                    counts["sack_permitted_packets"] += 1
                elif kind == 5:
                    if size < 10 or (size - 2) % 8:
                        raise ValueError("malformed SACK blocks")
                    counts["sack_block_packets"] += 1
                position += size
    if not counts["tcp_packets"]:
        raise ValueError("no TCP packets captured")
    return counts


if __name__ == "__main__":
    try:
        print(json.dumps(summarize(sys.argv[1]), indent=2))
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
