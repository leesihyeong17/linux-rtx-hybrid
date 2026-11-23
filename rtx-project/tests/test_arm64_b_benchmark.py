# SPDX-License-Identifier: GPL-2.0
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
import run_arm64_b_benchmark as bench
from build_arm64 import sha256


class BenchmarkTests(unittest.TestCase):
    def test_boot_defaults_overrides_and_invalid_values(self):
        self.assertEqual(bench.boot_parameters("quiet"), {"iterations": 32, "warmup_ms": 10000})
        self.assertEqual(bench.boot_parameters("rtx_bench_iterations=7 rtx_bench_warmup_ms=0"),
                         {"iterations": 7, "warmup_ms": 0})
        for options in ("rtx_bench_iterations=0", "rtx_bench_warmup_ms=60001",
                        "rtx_bench_iterations=5 rtx_bench_iterations=5", "rtx_bench_iterations="):
            with self.subTest(options=options), self.assertRaises(ValueError):
                bench.boot_parameters(options)

    def test_backend_config_separates_cache_tail_and_k(self):
        for variant, flags in bench.BACKENDS.items():
            with self.subTest(variant=variant):
                values = bench.config_values(variant, 4, "-test")
                selected = tuple(values["TCP_RTX_" + k] == "y" for k in bench.SYMBOLS)
                self.assertEqual(selected, flags)
                self.assertEqual(values.get("TCP_RTX_RECENT_MAX"), "4" if flags[0] else None)
                self.assertEqual(values["TCP_RTX_STATS"], "n")

    def test_pinned_collector_accepts_and_rejects_complete_boot_records(self):
        if subprocess.run(["git", "-C", bench.REPO, "cat-file", "-e", bench.HARNESS],
                          stderr=subprocess.DEVNULL).returncode:
            self.skipTest("fetch PR #25 to run the pinned protocol integration test")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            harness = root / "harness"
            digest = bench.export_harness(harness)
            self.assertEqual(len(list(harness.iterdir())), len(bench.FILES))
            # Use B's own synthetic protocol rows in tests only.
            text = subprocess.check_output([sys.executable, "-B", "-c",
                                            "from test_benchmark import records,serial; print(serial(records()))"],
                                           cwd=harness, text=True)
            for case in ("valid", "missing", "bad_config", "no_smoke", "failed"):
                with self.subTest(case=case):
                    run = root / case
                    (run / "configs").mkdir(parents=True)
                    (run / "logs").mkdir()
                    config = run / "configs/kernel.config"
                    config.write_text("".join("CONFIG_TCP_RTX_" + key + "=y\n" for key in bench.SYMBOLS) +
                                      "CONFIG_TCP_RTX_RECENT_MAX=16\n")
                    manifest = dict(schema_version=1, contract_version="0.2", run_id="synthetic",
                                    mode="observation", variant="hybrid-tail", repeat=1, order=1,
                                    source_sha=bench.HARNESS, source_patch_sha256="fixture", benchmark_sha256=digest,
                                    config_sha256="bad" if case == "bad_config" else sha256(config),
                                    execution_mode="test", environment_id="fixture", host_arch="aarch64",
                                    target_arch="aarch64", observation_run_id=None, observation_link_reason="test",
                                    iterations=1, warmup_ms=0, exit_code=int(case == "failed"))
                    bench.json_file(run / "manifest.json", manifest)
                    raw = text
                    if case == "missing":
                        raw = "\n".join(line for line in text.splitlines() if not line.startswith("RTXBENCH_INPUT"))
                    if case == "no_smoke":
                        raw = text.replace("RTX_RUNTIME PASS", "")
                    (run / "logs/serial.log").write_text(raw)
                    if case != "valid":
                        with self.assertRaises(subprocess.CalledProcessError):
                            bench.validate_samples(run, harness)
                        self.assertFalse((run / "operation-counts.csv").exists())
                        self.assertFalse(json.loads((run / "manifest.json").read_text())["valid"])
                        continue
                    self.assertEqual(bench.validate_samples(run, harness), 10)
                    with (run / "operation-counts.csv").open() as stream:
                        counts = {r["workload_id"]: r for r in csv.DictReader(stream)}
                    self.assertEqual(counts["split_alloc_failure"]["failures"], "1")
                    self.assertEqual(counts["split_alloc_failure"]["successes"], "0")
                    self.assertEqual(counts["append_long"]["attempts"], "1")
