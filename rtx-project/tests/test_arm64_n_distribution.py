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
                def transfer(loss, reverse, size, mode, port):
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
                    network.case.assert_called_once_with(1, False, 4096, "transfer", runner.PORT)
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
                        histogram=runner.parse_stats(snapshot(first, last))[1]["n_hist"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            runner.write_table(path, [row("pilot", "PASS", 20, 80), row("matrix", "PASS", 1, 0),
                                      row("matrix", "PASS", 2, 7), row("matrix", "FAIL", 99, 1)])
            with (path / "distribution.csv").open() as stream:
                rows = [r for r in csv.DictReader(stream) if r["bucket"] == "1-1"]
            self.assertEqual({r["histogram"] for r in rows}, {"n_hist"})
            self.assertEqual([(r["phase"], r["repetitions_completed"], r["total"], float(r["percent"]))
                              for r in rows], [("pilot", "1", "100", 20), ("matrix", "2", "10", 30)])


def snapshot_v2(small, large, acks, port=5001):
    """v2 dump: `small` inserts at N=16, `large` at N=17, `acks` ACKs at N=3.
    Counters are consistent with the histograms (inserts all appends)."""
    inserts = small + large
    counters = dict.fromkeys(runner.V2_COUNTERS, 0)
    counters.update(filter_port=port, insert_append=inserts, ack_clean=acks,
                    unlink_list=inserts)
    rows = ["%s %d" % item for item in sorted(counters.items())]
    for name, at in (("n_link", {"16": small, "17": large}), ("n_ack", {"3": acks})):
        rows += ["%s %s %d" % (name, b, at.get(b, 0)) for b in runner.V2_BUCKETS]
    return "\n".join(rows) + "\n"


class DistributionV2(unittest.TestCase):
    def test_v2_exact_k_shares_and_ack_histogram(self):
        result = runner.distribution(snapshot_v2(100, 50, 40), snapshot_v2(103, 51, 46))
        self.assertEqual(result["stats_format"], "v2")
        self.assertEqual(result["filter_port"], 5001)
        self.assertEqual(result["insert_samples"], 4)
        self.assertEqual(result["ack_samples"], 6)
        # K=16 splits the old 16-31 bucket: 3 of 4 inserts have N <= 16.
        self.assertEqual(result["le_share"]["n_link"]["16"], 0.75)
        self.assertEqual(result["le_share"]["n_link"]["8"], 0)
        self.assertEqual(result["le_share"]["n_ack"]["4"], 1)
        self.assertNotIn("filter_port", result["counters"])

    def test_v2_inconsistent_or_changed_dumps_are_rejected(self):
        before, after = snapshot_v2(100, 50, 40), snapshot_v2(103, 51, 46)
        for invalid in (snapshot_v2(103, 51, 46, port=0),             # filter changed
                        after.replace("insert_append 154", "insert_append 155"),
                        after.replace("ack_clean 46", "ack_clean 47"),
                        after.replace("n_ack 3 46", "n_ack 3 40"),        # no ACK samples
                        after.replace("n_link 64 0\n", ""),             # missing bucket
                        after + "n_hist 1-1 0\n",                       # mixed formats
                        snapshot(1, 1)):                                # v1 after v2
            with self.subTest(invalid=invalid[:40]), self.assertRaises(ValueError):
                runner.distribution(before, invalid)

    def test_v2_buckets_match_kernel_layout(self):
        self.assertEqual(len(runner.V2_BUCKETS), 74)
        self.assertEqual(runner.V2_BUCKETS[:2] + runner.V2_BUCKETS[64:67],
                         ("0", "1", "64", "65-127", "128-255"))
        self.assertEqual(runner.V2_BUCKETS[-2:], ("8192-16383", "16384-"))

    def test_port_filter_is_set_and_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp) / "proc-stats"
            writes = []
            with patch.object(runner, "STATS", proc), \
                    patch.object(Path, "write_text", lambda self, t: writes.append(t)):
                with runner.port_filter("v1", {}):
                    pass
                self.assertEqual(writes, [])
                with self.assertRaises(RuntimeError), runner.port_filter("v2", {"filter_port": 22}):
                    raise RuntimeError("sample failed")
            self.assertEqual(writes, ["port 5001\n", "port 22\n"])

    def test_v2_table_has_both_histograms(self):
        sample = runner.distribution(snapshot_v2(0, 0, 0), snapshot_v2(3, 1, 2))
        sample.update(phase="matrix", status="PASS", loss_percent_each_egress=1,
                      added_rtt_ms=20, sender_reorder_percent=0)
        with tempfile.TemporaryDirectory() as tmp:
            runner.write_table(Path(tmp), [sample])
            with (Path(tmp) / "distribution.csv").open() as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2 * 74)
        by = {(r["histogram"], r["bucket"]): r for r in rows}
        self.assertEqual((by["n_link", "16"]["count"], by["n_link", "16"]["total"]), ("3", "4"))
        self.assertEqual((by["n_ack", "3"]["count"], by["n_ack", "3"]["total"]), ("2", "2"))


if __name__ == "__main__":
    unittest.main()
