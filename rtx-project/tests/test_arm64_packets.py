# SPDX-License-Identifier: GPL-2.0
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_arm64_packets import netstat, validate


class PacketEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.log = "\n".join("RTX_PACKET %s bytes=4000 retransmitted=1000 PASS\n"
                             "RTX_ACCOUNT %s drained=1 total_retrans=1" % (c, c)
                             for c in ("SACK", "PARTIAL", "DSACK", "RENEGING"))
        self.log += "\nRTX_ACCOUNT SACK unacked=4 sacked=1\n"
        self.log += "RTX_ACCOUNT PARTIAL acked_bytes=2000 unacked=2 sacked=0 lost=0\n"
        self.log += "RTX_NATIVE PACKETS_COMPLETE (GSO/trim-path evidence still required)\n"
        self.before = dict(TCPDSACKRecv=0, TCPSACKReneging=0)
        self.after = dict(TCPDSACKRecv=1, TCPSACKReneging=1)

    def test_packet_success_does_not_claim_skb_trim(self):
        result = validate(self.log, self.before, self.after)
        self.assertTrue(result["partial_skb_trim"].startswith("NOT RUN"))

    def test_pass_marker_without_path_counter_rejected(self):
        with self.assertRaisesRegex(ValueError, "did not increase"):
            validate(self.log, self.before, self.before)

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


if __name__ == "__main__":
    unittest.main()
