# SPDX-License-Identifier: GPL-2.0
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from inspect_arm64_trace import table


class TraceInventoryTests(unittest.TestCase):
    def test_symbol_does_not_imply_traceability(self):
        rows = table("ffff t tcp_ack\n", "tcp_other\n", True)
        ack = rows[0]
        self.assertEqual(ack["nm_symbols"], ["tcp_ack"])
        self.assertEqual(ack["ftrace"], "UNAVAILABLE")

    def test_wrong_boot_does_not_supply_runtime_evidence(self):
        rows = table("ffff t tcp_ack\n", "tcp_ack\n", False)
        self.assertTrue(rows[0]["ftrace"].startswith("NOT RUN"))
        self.assertEqual(rows[0]["ftrace_symbols"], [])

    def test_unreadable_not_absent(self):
        self.assertTrue(table("ffff t tcp_ack\n", None, True)[0]["ftrace"].startswith("BLOCKED"))

    def test_compiler_clones_are_recorded_without_prefix_collisions(self):
        rows = table("ffff t tcp_ack.isra.0\nfffe t tcp_ack_update_rtt\n", "tcp_ack.isra.0\n", True)
        self.assertEqual(rows[0]["nm_symbols"], ["tcp_ack.isra.0"])
        self.assertEqual(rows[0]["ftrace"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
