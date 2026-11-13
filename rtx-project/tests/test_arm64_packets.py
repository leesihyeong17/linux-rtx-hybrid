# SPDX-License-Identifier: GPL-2.0
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_arm64_packets import netstat, validate


class PacketEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.log = "\n".join("RTX_PACKET %s bytes=4000 retransmitted=1000 PASS\n"
                             "RTX_ACCOUNT %s drained=1 total_retrans=1" % (c, c)
                             for c in ("SACK", "PARTIAL", "DSACK", "DSACK_OFO", "RENEGING"))
        self.log += "\nRTX_ACCOUNT SACK unacked=4 sacked=1 lost=1 retrans=1\n"
        self.log += "RTX_ACCOUNT RENEGING unacked=4 sacked=1 lost=1 retrans=1\n"
        self.log += "RTX_ACCOUNT RENEGING recovered=1 unacked=3 sacked=0 lost=3 retrans=1 skipped=1\n"
        self.log += "RTX_ACCOUNT DSACK_OFO unacked=4 sacked=2 lost=0 retrans=0\n"
        self.log += "RTX_CONFIG PARTIAL tcp_early_retrans=0\n"
        self.log += "RTX_ACCOUNT PARTIAL acked_bytes=2000 unacked=2 sacked=0 lost=0\n"
        self.log += "RTX_NATIVE PACKETS_COMPLETE (GSO/trim-path evidence still required)\n"
        self.before = dict(TCPDSACKRecv=7, TCPDSACKOfoRecv=3, TCPSACKReneging=4)
        self.after = dict(TCPDSACKRecv=8, TCPDSACKOfoRecv=4, TCPSACKReneging=5)
        self.before.update(TCPSackShifted=11, TCPSackMerged=2, TCPSackShiftFallback=4)
        self.after.update(TCPSackShifted=12, TCPSackMerged=2, TCPSackShiftFallback=10)

    def test_packet_success_does_not_claim_skb_trim(self):
        result = validate(self.log, self.before, self.after)
        self.assertTrue(result["partial_skb_trim"].startswith("NOT RUN"))
        self.assertTrue(result["dsack_walk"].startswith("NOT RUN"))
        self.assertEqual(result["shift_mib_deltas"],
                         dict(TCPSackShifted=1, TCPSackMerged=0, TCPSackShiftFallback=6))

    def test_shift_reset_not_reported_as_comparable_delta(self):
        with self.assertRaisesRegex(ValueError, "counter decreased"):
            validate(self.log, self.before, dict(self.after, TCPSackShifted=0))

    def test_pass_marker_without_path_counter_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate(self.log, self.before, self.before)

    def test_multiple_reneging_events_rejected(self):
        with self.assertRaisesRegex(ValueError, "TCPSACKReneging"):
            validate(self.log, self.before, dict(self.after, TCPSACKReneging=6))

    def test_reneging_must_clear_sacked(self):
        with self.assertRaisesRegex(ValueError, "clear SACK"):
            validate(self.log.replace("recovered=1 unacked=3 sacked=0", "recovered=1 unacked=3 sacked=1"),
                     self.before, self.after)

    def test_outstanding_dsack_requires_its_own_mib(self):
        with self.assertRaisesRegex(ValueError, "TCPDSACKOfoRecv"):
            validate(self.log, self.before, dict(self.after, TCPDSACKOfoRecv=3))

    def test_drained_dsack_cannot_replace_outstanding_case(self):
        with self.assertRaisesRegex(ValueError, "outstanding DSACK"):
            validate(self.log.replace("DSACK_OFO unacked=4", "DSACK_OFO unacked=0"), self.before, self.after)

    def test_partial_requires_tlp_disabled(self):
        with self.assertRaisesRegex(ValueError, "disable TLP"):
            validate(self.log.replace("tcp_early_retrans=0", "tcp_early_retrans=3"), self.before, self.after)

    def test_missing_partial_accounting_rejected(self):
        with self.assertRaises(ValueError):
            validate(self.log.replace("unacked=2", "unacked=4"), self.before, self.after)

    def test_reneging_without_retransmission_rejected(self):
        with self.assertRaisesRegex(ValueError, "not retransmitted"):
            validate(self.log.replace("RENEGING bytes=4000 retransmitted=1000", "RENEGING bytes=4000 retransmitted=0"),
                     self.before, self.after)

    def test_failure_marker_cannot_be_hidden(self):
        with self.assertRaises(ValueError):
            validate(self.log + "RTX_PACKET FAIL error", self.before, self.after)

    def test_counter_table_length_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            netstat("TcpExt: TCPDSACKRecv TCPSACKReneging\nTcpExt: 1\n")

    def test_empty_counter_row_rejected(self):
        with self.assertRaises(ValueError):
            netstat("TcpExt: TCPDSACKRecv\n\n")


class PeerWireTests(unittest.TestCase):
    """Exercise the actual C packet encoder without TUN, sockets, or root."""
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        source = folder / "wire.c"
        source.write_text('''
int main(int argc, char **argv) {
    int pipes[2];
    unsigned char packet[80];
    if (argc != 3 || pipe(pipes)) return 2;
    tun = pipes[1];
    local_port = htons(12345);
    base_seq = strtoul(argv[1], NULL, 10);
    reply(base_seq, 0, 0, atoi(argv[2]));
    close(pipes[1]);
    ssize_t count = read(pipes[0], packet, sizeof(packet));
    return count < 0 || write(1, packet, count) != count;
}
''')
        cls.peer = folder / "wire"
        shared = Path(__file__).resolve().parent / "runtime/packet_peer.c"
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-DRTX_NATIVE_PEER",
                        "-include", str(shared), str(source), "-o", str(cls.peer)], check=True)

    def test_dsack_blocks_and_packet_checksums(self):
        for base in (10000, 0xfffffc00):
            for kind, blocks in ((1, (1000, 2000)), (2, (0, 1000)),
                                 (3, (1000, 2000, 1000, 3000))):
                with self.subTest(base=base, kind=kind):
                    packet = subprocess.check_output([str(self.peer), str(base), str(kind)])
                    total, = struct.unpack_from("!H", packet, 2)
                    self.assertEqual(total, len(packet))
                    self.assertEqual((packet[32] >> 4) * 4, len(packet) - 20)
                    self.assertEqual(packet[40:44], bytes((1, 1, 5, 2 + 4 * len(blocks))))
                    self.assertEqual(struct.unpack_from("!" + "I" * len(blocks), packet, 44),
                                     tuple((base + n) & 0xffffffff for n in blocks))
                    pseudo = packet[12:20] + struct.pack("!BBH", 0, 6, len(packet) - 20)
                    for data in (packet[:20], pseudo + packet[20:]):
                        value = sum(struct.unpack("!" + "H" * (len(data) // 2), data))
                        while value >> 16:
                            value = (value & 0xffff) + (value >> 16)
                        self.assertEqual(value, 0xffff)


if __name__ == "__main__":
    unittest.main()
