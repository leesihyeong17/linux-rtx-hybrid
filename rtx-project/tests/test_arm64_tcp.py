# SPDX-License-Identifier: GPL-2.0
"""Real loopback endpoint tests and fail-closed evidence/cleanup checks."""
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_arm64_tcp as runner

spec = importlib.util.spec_from_file_location("endpoint", ROOT / "tests/runtime/tcp_endpoint.py")
endpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endpoint)


class EvidenceTests(unittest.TestCase):
    def test_missing_success_and_hashes_rejected(self):
        for record in ({}, {"status": "PASS", "mode": "transfer", "bytes": 0, "half_close": True}):
            with self.assertRaises(ValueError):
                runner.validate_pair(record, record, 0, "transfer")

    def test_false_reset_rejected(self):
        with self.assertRaisesRegex(ValueError, "reset not observed"):
            runner.validate_pair({"status": "PASS", "mode": "reset"},
                                 {"status": "PASS", "mode": "reset", "bytes": 1}, 1, "reset")

    def test_different_endpoint_hashes_rejected(self):
        server = dict(status="PASS", mode="transfer", bytes=4097, sha256="a" * 64, half_close=True)
        client = dict(server, sha256="b" * 64, peer=server)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            runner.validate_pair(client, server, 4097, "transfer")

    def test_old_kernel_cannot_be_labelled_v1(self):
        with patch.object(runner.platform, "release", return_value="5.4.214-kimseungbeom+"):
            with self.assertRaisesRegex(ValueError, "boot required"):
                runner.boot_evidence(Path("/does/not/exist"), "v1")

    def test_setup_failure_cleans_only_created_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            network = runner.Network(Path(tmp))
            calls = []
            def command(args, required=True):
                calls.append(args)
                if args == ["ip", "netns", "add", network.names[1]]:
                    raise RuntimeError("injected namespace creation failure")
                return subprocess.CompletedProcess(args, 0, "", "")
            with patch.object(network, "command", side_effect=command):
                with self.assertRaises(RuntimeError):
                    network.setup()
                self.assertEqual(network.cleanup(), [])
            deletions = [c for c in calls if c[:3] == ["ip", "netns", "delete"]]
            self.assertEqual(deletions, [["ip", "netns", "delete", network.names[0]]])

    def test_cleanup_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            network = runner.Network(Path(tmp))
            network.owned = [network.names[0]]
            with patch.object(network, "command", return_value=subprocess.CompletedProcess([], 1)):
                self.assertEqual(network.cleanup(), network.owned)


class LoopbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = None

    def tearDown(self):
        if self.server is not None and self.server.poll() is None:
            self.server.kill()
            self.server.communicate()

    def start_server(self, size, mode="transfer"):
        ready = self.root / "ready"
        self.server = subprocess.Popen([sys.executable, str(runner.ENDPOINT), "server",
                                       "--host", "127.0.0.1", "--port", "0", "--bytes", str(size),
                                       "--mode", mode, "--ready", str(ready), "--timeout", "3"],
                                      text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.server.poll() is not None:
                self.fail("server did not start: " + str(self.server.communicate()))
            if ready.exists():
                try:
                    return json.loads(ready.read_text())["port"]
                except (ValueError, KeyError):
                    pass
            time.sleep(0.01)
        self.fail("server not ready")

    def transfer(self, size, mode="transfer"):
        port = self.start_server(size, mode)
        client = subprocess.run([sys.executable, str(runner.ENDPOINT), "client",
                                 "--host", "127.0.0.1", "--port", str(port), "--bytes", str(size),
                                 "--mode", mode, "--timeout", "3"],
                                text=True, capture_output=True, timeout=5)
        stdout, stderr = self.server.communicate(timeout=5)
        self.assertEqual(client.returncode, 0, client.stdout + client.stderr)
        self.assertEqual(self.server.returncode, 0, stdout + stderr)
        runner.validate_pair(json.loads(client.stdout), json.loads(stdout), size, mode)

    def test_empty_transfer_and_half_close(self):
        self.transfer(0)

    def test_non_aligned_transfer(self):
        self.transfer(4097)

    def test_large_transfer(self):
        self.transfer(1048576)

    def test_reset_handshake(self):
        self.transfer(1, "reset")

    def test_corrupt_payload_fails(self):
        port = self.start_server(4097)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall(b"corrupt")
            sock.shutdown(socket.SHUT_WR)
        stdout, _ = self.server.communicate(timeout=5)
        self.assertNotEqual(self.server.returncode, 0)
        self.assertEqual(json.loads(stdout)["status"], "FAIL")

    def test_truncated_payload_fails(self):
        port = self.start_server(4097)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
            sock.sendall(endpoint.payload(0, 100))
            sock.shutdown(socket.SHUT_WR)
        stdout, _ = self.server.communicate(timeout=5)
        self.assertNotEqual(self.server.returncode, 0)
        self.assertIn("premature EOF", json.loads(stdout)["error"])

    def test_client_timeout_not_success(self):
        port = self.start_server(4097)
        with socket.create_connection(("127.0.0.1", port), timeout=3):
            stdout, _ = self.server.communicate(timeout=5)
        self.assertNotEqual(self.server.returncode, 0)
        self.assertEqual(json.loads(stdout)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
