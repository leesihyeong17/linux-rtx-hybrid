#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Exercise collection failure accounting and safe output handling; no network changes."""
import csv
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capture_env.sh"


class CaptureEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rtx-capture-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "source repo"
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True)
        self.output = self.root / "results with spaces"
        self.config = self.root / "build config; literal $(name)"
        self.config.write_text('CONFIG_ARM64=y\nCONFIG_LOCALVERSION="-test"\n')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # A minimal PATH deliberately leaves optional tools unavailable.
        for command in ("bash", "dirname", "git", "date", "mktemp", "mkdir",
                        "chmod", "cat", "cp", "sha256sum", "timeout", "uname"):
            path = shutil.which(command)
            self.assertIsNotNone(path, "test dependency: " + command)
            (self.bin / command).symlink_to(path)
        self.mock("ip", "printf 'Operation not permitted\\n' >&2\nexit 1\n")
        self.mock("ss", "printf 'Cannot open netlink socket: Operation not permitted\\n' >&2\nexit 0\n")
        self.mock("ethtool", "exit 124\n")
        self.env = dict(os.environ, PATH=str(self.bin))

    def mock(self, command, body):
        file = self.bin / command
        file.write_text("#!/bin/bash\n" + body)
        file.chmod(0o755)

    def run_capture(self, *extra):
        return subprocess.run(
            ["/bin/bash", str(SCRIPT), "--repo", str(self.repo),
             "--output-root", str(self.output), *extra],
            env=self.env, text=True, capture_output=True, timeout=30)

    def test_partial_capture_preserves_failures_and_config(self):
        result = self.run_capture("--build-config", str(self.config), "--label", "test")
        self.assertEqual(result.returncode, 2, result.stderr)
        output = Path(result.stdout.strip())
        self.assertTrue(output.is_dir())
        self.assertEqual((output / "build.config").read_bytes(), self.config.read_bytes())
        digest = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.assertIn(digest, (output / "logs/build-sha256.txt").read_text())
        with (output / "statuses.tsv").open() as stream:
            rows = {row["probe"]: row for row in csv.DictReader(stream, delimiter="\t")}
        self.assertEqual(rows["iperf3-version"]["status"], "missing")
        self.assertEqual(rows["addresses"]["status"], "permission_denied")
        self.assertEqual(rows["addresses"]["exit_code"], "1")
        self.assertEqual(rows["tcp-sockets"]["status"], "permission_denied")
        self.assertEqual(rows["tcp-sockets"]["exit_code"], "0")
        self.assertEqual(rows["ethtool-version"]["status"], "timeout")
        self.assertEqual(rows["ethtool-version"]["exit_code"], "124")
        self.assertEqual(rows["git-head"]["status"], "error")
        self.assertIn("Status: PARTIAL", (output / "summary.txt").read_text())
        for entry in [output, *output.rglob("*")]:
            self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700 if entry.is_dir() else 0o600)

    def test_second_capture_never_overwrites_first(self):
        first = self.run_capture("--build-config", str(self.config))
        self.assertEqual(first.returncode, 2, first.stderr)
        first_path = Path(first.stdout.strip())
        snapshot = {str(p.relative_to(first_path)): p.read_bytes()
                    for p in first_path.rglob("*") if p.is_file()}
        self.config.write_text("CONFIG_ARM64=y\n# second capture\n")
        second = self.run_capture("--build-config", str(self.config))
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertNotEqual(first.stdout, second.stdout)
        self.assertEqual(snapshot, {str(p.relative_to(first_path)): p.read_bytes()
                                  for p in first_path.rglob("*") if p.is_file()})
        self.assertEqual((Path(second.stdout.strip()) / "build.config").read_bytes(), self.config.read_bytes())

    def test_invalid_inputs_do_not_create_results(self):
        for args in (("--label", "../escape"), ("--build-config", str(self.root / "missing")),
                     ("--unknown",), ("--label",)):
            with self.subTest(args=args):
                result = self.run_capture(*args)
                self.assertEqual(result.returncode, 1)
                self.assertFalse(self.output.exists())

    def test_existing_output_file_is_preserved(self):
        self.output.write_text("keep this file\n")
        result = self.run_capture()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.output.read_text(), "keep this file\n")


if __name__ == "__main__":
    unittest.main()
