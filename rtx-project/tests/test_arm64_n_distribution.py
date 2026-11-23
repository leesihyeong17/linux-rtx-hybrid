# SPDX-License-Identifier: GPL-2.0
import csv
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_arm64_n_distribution as runner
import arm64_perf_evidence as provenance


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
    def test_distribution_identity_checks_boot_files_config_and_build_id(self):
        source = "a" * 40
        release = "5.4.214-d30-hybrid-tail-n-distribution-k16-" + source[:8]
        config = ("CONFIG_TCP_RTX_HYBRID=y\nCONFIG_TCP_RTX_CACHE=y\nCONFIG_TCP_RTX_TAIL=y\n"
                  "CONFIG_TCP_RTX_STATS=y\nCONFIG_TCP_RTX_RECENT_MAX=16\n# CONFIG_TCP_RTX_DEBUG is not set\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / "status.json"
            elf, image = root / "vmlinux", root / ("vmlinuz-" + release)
            elf.write_bytes(b"test ELF")
            image.write_bytes(b"test Image")
            built_image = root / "arch/arm64/boot/Image"
            built_image.parent.mkdir(parents=True)
            built_image.write_bytes(image.read_bytes())
            (root / "notes").write_bytes(struct.pack("<III", 4, 4, 3) + b"GNU\0abcd")
            (root / "boot_id").write_text("test-boot")
            data = dict(status="PASS", stage="built", source_sha=source, mode="n-distribution",
                        variant="hybrid-tail", k=16, compiler="test", kernelrelease=release,
                        output=tmp, hashes={"arch/arm64/boot/Image": runner.sha256(image),
                                            "vmlinux": runner.sha256(elf)})
            def write(config_text):
                for name in (".config", "config-" + release):
                    (root / name).write_text(config_text)
                data["config_sha256"] = runner.sha256(root / ".config")
                record.write_text(json.dumps(data))
            def test_path(value):
                return root if value == tmp else root / Path(value).name
            with patch.object(provenance, "Path", side_effect=test_path), \
                    patch.object(provenance.platform, "machine", return_value="aarch64"), \
                    patch.object(provenance.platform, "release", return_value=release), \
                    patch.object(provenance.subprocess, "check_output", return_value="Build ID: 61626364") as elf_note:
                write(config)
                self.assertEqual(provenance.boot_identity(record, n_distribution=True)["source_sha"], source)
                with self.assertRaisesRegex(ValueError, "performance requires"):
                    provenance.boot_identity(record)
                for changed in (config.replace("STATS=y", "STATS=n"), config.replace("CACHE=y", "CACHE=n"),
                                config.replace("=16", "=4"), config + "CONFIG_TCP_RTX_DEBUG=y\n"):
                    write(changed)
                    with self.assertRaisesRegex(ValueError, "configuration"):
                        provenance.boot_identity(record, n_distribution=True)
                write(config)
                elf_note.return_value = "Build ID: ffffffff"
                with self.assertRaisesRegex(ValueError, "build ID differs"):
                    provenance.boot_identity(record, n_distribution=True)
                image.write_bytes(b"different Image")
                with self.assertRaisesRegex(ValueError, "artifact mismatch"):
                    provenance.boot_identity(record, n_distribution=True)

    def test_object_only_and_legacy_matrix_stop_before_traffic(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            args = SimpleNamespace(build_run=run, pilot_only=False)
            (run / "status.json").write_text(json.dumps(dict(mode="n-distribution", stage="object_only", status="PASS")))
            with self.assertRaisesRegex(ValueError, "completed N distribution"):
                runner.collect(args, run, {})
            (run / "status.json").write_text(json.dumps({"modes": {}}))
            with patch.object(runner.platform, "release", return_value="5.4.214-rtx-c23-hybrid"), \
                    patch.object(runner.platform, "machine", return_value="aarch64"), \
                    patch.object(runner, "verify_identity"), self.assertRaisesRegex(ValueError, "pilot-only"):
                runner.collect(args, run, {})

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

    def test_stable_wrong_port_and_missing_skip_reuse_are_rejected(self):
        for port in (0, 22):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "5001"):
                runner.distribution(snapshot_v2(1, 0, 1, port), snapshot_v2(3, 1, 2, port))
        raw = snapshot_v2(3, 1, 2).replace("sack_skip_reuse 0\n", "")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            runner.parse_stats(raw)

    def test_shift_deltas_in_both_namespaces_match_the_global_transfer_interval(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)
                proc = path / "stats"
                proc.write_text(snapshot_v2(2, 0, 1))
                def transfer(*args):
                    data = snapshot_v2(5, 1, 3)
                    proc.write_text(data.replace("shift_partial 0", "shift_partial 3"))
                    return {"status": "PASS"}

                def command(args):
                    if args[-1] != "/proc/net/netstat":
                        return SimpleNamespace(stdout="")
                    # Each endpoint starts at a different nonzero absolute value.
                    base, delta = (10, 2) if args[3] == "tx" else (20, 2 if mismatch else 1)
                    count = base + (delta if (path / "after.txt").exists() else 0)
                    return SimpleNamespace(stdout="TcpExt: TCPSackShifted TCPSackMerged TCPSackShiftFallback\n"
                                           + f"TcpExt: {count} 11 9\n")

                with patch.object(runner, "STATS", proc), patch.object(runner, "Network") as mock:
                    net = mock.return_value
                    net.names, net.links = ("tx", "rx"), ("v0", "v1")
                    net.case.side_effect = transfer
                    net.command.side_effect = command
                    net.cleanup.return_value = []
                    sample = runner.measure(path, (1, 20, 25), 4096, "v2")
                    net.cleanup.assert_called_once()
                self.assertEqual(sample["status"], "FAIL" if mismatch else "PASS")
                self.assertEqual(sample["tcpext_total"]["TCPSackShifted"], 4 if mismatch else 3)
                self.assertEqual(len(list(path.glob("*-netstat-*.txt"))), 4)
                if mismatch:
                    self.assertIn("shift_partial delta 3 != TCPSackShifted delta 4", sample["error"])

    def test_condition_table_pools_threshold_and_lookup_denominators(self):
        def sample(small, large, lookups, steps, phase="matrix"):
            row = runner.distribution(snapshot_v2(0, 0, 0), snapshot_v2(small, large, 2))
            row.update(phase=phase, status="PASS", loss_percent_each_egress=1, added_rtt_ms=20,
                       sender_reorder_percent=25, tcpext_total=dict.fromkeys(runner.SHIFT_MIB.values(), 0))
            row["counters"].update(find_cover=lookups, find_cover_list_steps=steps)
            return row
        samples = [sample(1, 0, 1, 2), sample(1, 6, 3, 4), sample(1, 0, 0, 0, "pilot")]
        samples.append(dict(samples[0], status="FAIL"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            runner.write_table(path, samples, 16)
            with (path / "conditions.csv").open() as stream:
                matrix, pilot = csv.DictReader(stream)
            self.assertEqual((matrix["repetitions_completed"], matrix["build_k"]), ("2", "16"))
            self.assertEqual(float(matrix["n_link_le_16_pct"]), 25)
            self.assertEqual(float(matrix["n_ack_le_4_pct"]), 100)
            self.assertEqual(float(matrix["steps_per_find_cover"]), 1.5)
            self.assertEqual(pilot["steps_per_find_cover"], "")

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
