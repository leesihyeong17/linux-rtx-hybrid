# SPDX-License-Identifier: GPL-2.0
import struct
from pathlib import Path
import tempfile
import unittest

from check_tcp_pcap import summarize


def capture(options=b"", endian="<"):
    options += b"\x01" * (-len(options) % 4)
    ethernet = bytes(12) + b"\x08\x00"
    ip = bytearray(20)
    ip[0], ip[9] = 0x45, 6
    struct.pack_into("!H", ip, 2, 40 + len(options))
    tcp = bytearray(20)
    tcp[12] = ((20 + len(options)) // 4) << 4
    frame = ethernet + ip + tcp + options
    header = struct.pack(endian + "IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 192, 1)
    return header + struct.pack(endian + "IIII", 0, 0, len(frame), len(frame)) + frame


class PcapTests(unittest.TestCase):
    def parse(self, data):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "traffic.pcap"
            path.write_bytes(data)
            return summarize(path)

    def test_negotiation_is_not_sack_block(self):
        result = self.parse(capture(b"\x04\x02"))
        self.assertEqual(result["sack_permitted_packets"], 1)
        self.assertEqual(result["sack_block_packets"], 0)

    def test_sack_block_both_byte_orders(self):
        for endian in ("<", ">"):
            result = self.parse(capture(b"\x05\x0a" + struct.pack("!II", 1000, 2000), endian))
            self.assertEqual(result["sack_block_packets"], 1)

    def test_truncated_packet_rejected(self):
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.parse(capture()[:-1])

    def test_truncated_record_header_rejected(self):
        with self.assertRaisesRegex(ValueError, "truncated record"):
            self.parse(capture() + b"\x00")

    def test_malformed_sack_rejected(self):
        with self.assertRaisesRegex(ValueError, "malformed SACK"):
            self.parse(capture(b"\x05\x04\x00\x00"))

    def test_infinite_option_loop_rejected(self):
        with self.assertRaisesRegex(ValueError, "option length"):
            self.parse(capture(b"\x05\x00\x00\x00"))

    def test_empty_capture_not_success(self):
        with self.assertRaisesRegex(ValueError, "no TCP"):
            self.parse(capture()[:24])

    def test_unsupported_linktype_rejected(self):
        data = bytearray(capture())
        struct.pack_into("<I", data, 20, 113)
        with self.assertRaisesRegex(ValueError, "Ethernet"):
            self.parse(data)


if __name__ == "__main__":
    unittest.main()
