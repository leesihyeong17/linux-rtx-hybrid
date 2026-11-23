# SPDX-License-Identifier: GPL-2.0
import csv
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests/runtime")]
import tcp_perf_endpoint as peer
from arm64_perf_evidence import evidence_run, running_build_id
from run_arm64_tcp_perf import metrics, summarize


class TcpPerformance(unittest.TestCase):
    def test_linux_notes_reject_truncation(self):
        note = struct.pack("<III", 4, 3, 3) + b"GNU\0xyz\0"
        self.assertEqual(running_build_id(note), "78797a")
        with self.assertRaises(ValueError):
            running_build_id(note[:-2])

    def test_transfer_and_quantiles_on_real_tcp(self):
        for mode in ("bulk", "rr"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                receiver = {}
                def serve():
                    conn, _ = listener.accept()
                    with conn:
                        receiver.update(peer.server(conn, mode))
                worker = threading.Thread(target=serve)
                worker.start()
                latency = Path(tmp) / "latency"
                with socket.create_connection(listener.getsockname(), timeout=5) as conn:
                    sender = peer.client(conn, mode, .04, latency)
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertGreater(metrics(mode, sender, receiver)["goodput_mbps"], 0)
                if mode == "rr":
                    self.assertEqual(latency.stat().st_size, 8 * sender["requests"])
                with self.assertRaises(ValueError):
                    metrics(mode, sender, dict(receiver, bytes=receiver["bytes"]-1))

    def test_summary_separates_workloads_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with self.assertRaises(ValueError), evidence_run(run, {}):
                summarize(run, [dict(workload="bulk", metrics={"x": 5}),
                                dict(workload="bulk", metrics={"x": 1}),
                                dict(workload="rr", metrics={"x": 7})])
                raise ValueError("test rejection")
            self.assertEqual(json.loads((run/"status.json").read_text())["status"], "FAIL")
            with (run/"summary.csv").open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual([(r["workload"],float(r["median"])) for r in rows], [("bulk",3),("rr",7)])
            self.assertIn("summary.csv", (run/"SHA256SUMS").read_text())
