# SPDX-License-Identifier: GPL-2.0
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_arm64_n_distribution as runner


def snapshot(first, last):
    rows = [f"{name} {first + last}" for name in sorted(runner.COUNTERS)]
    for bucket in runner.BUCKETS:
        count = first if bucket == "1-1" else last if bucket == "32768-" else 0
        rows.append(f"n_hist {bucket} {count}")
    return "\n".join(rows) + "\n"


class Distribution(unittest.TestCase):
    def test_existing_counts_are_subtracted(self):
        result = runner.distribution(snapshot(1000, 10), snapshot(1006, 12))
        self.assertEqual(result["insert_samples"], 8)
        self.assertEqual(result["percent"]["1-1"], 75)
        self.assertEqual(result["percent"]["32768-"], 25)
        self.assertEqual(result["counters"]["ack_clean"], 8)

    def test_malformed_missing_reset_and_empty_observations_are_rejected(self):
        before = snapshot(10, 5)
        after = snapshot(12, 6)
        for invalid in (before, snapshot(9, 10), after + "n_hist 1-1 0\n",
                        after.replace("n_hist 32768- 6", "n_hist 32768- -1"),
                        after.replace("2-3", "2-4"), after.replace("ack_trim 18\n", ""),
                        after.replace("insert_append 18", "insert_append 15"),
                        after.replace("sack_block 18", "sack_block 10"),
                        after + "ack_trim 0\n", after + "new_counter 2\n"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                runner.distribution(before, invalid)

    def test_matrix_and_symmetric_delay(self):
        self.assertEqual(len(set(runner.MATRIX)), 7)
        self.assertEqual({(l, r) for l, r, o in runner.MATRIX if not o},
                         {(l, r) for l in (0, 1) for r in (0, 20, 50)})
        self.assertEqual(runner.netem(1, 50, 0), ["loss", "1%", "delay", "25ms"])
        self.assertEqual(runner.netem(1, 20, 25), ["loss", "1%", "delay", "10ms", "reorder", "25%"])

    def test_sample_preserves_raw_and_cleans_network_on_endpoint_failure(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)
                proc = path / "proc-stats"
                proc.write_text(snapshot(140, 8))
                def transfer(loss, reverse, size, mode):
                    proc.write_text(snapshot(149, 9))
                    if failed:
                        raise ValueError("hash mismatch")
                    return {"status": "PASS"}
                with patch.object(runner, "STATS", proc), patch.object(runner, "Network") as mock:
                    network = mock.return_value
                    network.names = ["a", "b"]
                    network.links = ["da", "db"]
                    network.command.return_value.stdout = "test offload"
                    network.cleanup.return_value = []
                    network.case.side_effect = transfer
                    result = runner.measure(path, (1, 20, 25), 4096)
                    netem = [c.args[0] for c in network.command.call_args_list if "netem" in c.args[0]]
                    self.assertEqual(len(netem), 2)
                    self.assertIn("reorder", netem[0])
                    self.assertNotIn("reorder", netem[1])
                    network.case.assert_called_once_with(1, False, 4096, "transfer")
                    network.cleanup.assert_called_once()
                self.assertEqual((path / "before.txt").read_text(), snapshot(140, 8))
                self.assertEqual((path / "after.txt").read_text(), snapshot(149, 9))
                self.assertEqual(json.loads((path / "status.json").read_text()), result)
                self.assertEqual(result["status"], "FAIL" if failed else "PASS")
                self.assertEqual(result.get("insert_samples"), None if failed else 10)

    def test_table_pools_events_and_excludes_pilot_and_failure_from_matrix(self):
        def row(phase, status, first, last):
            return dict(phase=phase, status=status, loss_percent_each_egress=0,
                        sender_reorder_percent=0, added_rtt_ms=0,
                        histogram=runner.parse_stats(snapshot(first, last))[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            runner.write_table(path, [row("pilot", "PASS", 20, 80), row("matrix", "PASS", 1, 0),
                                      row("matrix", "PASS", 2, 7), row("matrix", "FAIL", 99, 1)])
            with (path / "distribution.csv").open() as stream:
                rows = [r for r in csv.DictReader(stream) if r["bucket"] == "1-1"]
            self.assertEqual([(r["phase"], r["repetitions_completed"], r["total"], float(r["percent"]))
                              for r in rows], [("pilot", "1", "100", 20), ("matrix", "2", "10", 30)])


if __name__ == "__main__":
    unittest.main()
