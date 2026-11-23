# SPDX-License-Identifier: GPL-2.0
"""Preparation and build evidence failures, without a full kernel build."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_prepare_v0_arm64 as fixtures

SCRIPT = fixtures.REPO / "rtx-project/scripts/build_arm64.py"
spec = importlib.util.spec_from_file_location("arm64_build", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PrepareV1Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PrepareV0Tests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.env = dict(self.fixture.env, MOCK_BASELINE=build.PINS["v1"],
                        MOCK_RELEASE="5.4.214-rtx-v1")

    def prepare(self, **env):
        f = self.fixture
        return subprocess.run([str(fixtures.REPO / "rtx-project/scripts/prepare_v1_arm64.sh"),
                               "--source", str(f.source), "--output", str(f.output),
                               "--config", str(f.seed)], env=dict(self.env, **env),
                              text=True, capture_output=True, timeout=30)

    def test_pinned_v1_stats_debug_off(self):
        with self.fixture.seed.open("a") as stream:
            stream.write("CONFIG_TCP_RTX_STATS=y\nCONFIG_TCP_RTX_DEBUG=y\n")
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.fixture.output / "final.config").read_text()
        self.assertIn("# CONFIG_TCP_RTX_STATS is not set", config)
        self.assertIn("# CONFIG_TCP_RTX_DEBUG is not set", config)
        self.assertIn('CONFIG_LOCALVERSION="-rtx-v1"', config)

    def test_v0_cannot_be_mislabelled_v1(self):
        result = self.prepare(MOCK_HEAD=build.PINS["v0"])
        self.fixture.assert_rejected_before_make(result)

    def test_dirty_v1_is_rejected(self):
        self.fixture.assert_rejected_before_make(self.prepare(MOCK_DIRTY=" M net/ipv4/tcp_input.c"))

    def test_wrong_release_rejected(self):
        result = self.prepare(MOCK_RELEASE="5.4.214-rtx-v0")
        self.assertNotEqual(result.returncode, 0)

    def test_failed_prepare_keeps_exit_code(self):
        result = self.prepare(MOCK_FAIL_TARGET="prepare")
        self.assertEqual(result.returncode, 42)
        self.assertIn("exit_code=42", (self.fixture.output / "prepare-status.txt").read_text())

    def test_stats_reenabled_by_kconfig_is_rejected(self):
        make = self.fixture.bin / "make"
        make.write_text(make.read_text().replace("olddefconfig)",
                        'olddefconfig)\n    echo CONFIG_TCP_RTX_STATS=y >>"$output/.config"'))
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TCP_RTX_STATS unexpectedly enabled", result.stderr)


class BuildEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, content="artifact\n"):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def complete(self):
        for name in ("vmlinux", "System.map", "arch/arm64/boot/Image",
                     "drivers/test.ko", "arch/arm64/boot/dts/vendor/test.dtb"):
            self.put(name)
        self.put("modules.order", "drivers/test.ko\n")

    def test_complete_artifacts_have_hashes(self):
        self.complete()
        result = build.artifacts(self.root)
        self.assertEqual(result["module_count"], 1)
        self.assertEqual(result["dtb_count"], 1)

    def test_success_exit_without_image_cannot_pass(self):
        self.complete()
        (self.root / "arch/arm64/boot/Image").unlink()
        with self.assertRaisesRegex(ValueError, "artifact"):
            build.artifacts(self.root)

    def test_missing_module_cannot_pass(self):
        self.complete()
        (self.root / "drivers/test.ko").unlink()
        with self.assertRaisesRegex(ValueError, "missing module"):
            build.artifacts(self.root)

    def test_missing_dtbs_cannot_pass(self):
        self.complete()
        (self.root / "arch/arm64/boot/dts/vendor/test.dtb").unlink()
        with self.assertRaisesRegex(ValueError, "DTBs"):
            build.artifacts(self.root)

    def test_module_path_escape_rejected(self):
        self.complete()
        self.put("modules.order", "../outside.ko\n")
        with self.assertRaisesRegex(ValueError, "invalid"):
            build.artifacts(self.root)

    def test_failure_exit_not_hidden_by_log_capture(self):
        log = self.root / "build.log"
        rc = build.run_logged(["sh", "-c", "echo build-failed >&2; exit 42"], log, os.environ)
        self.assertEqual(rc, 42)
        self.assertIn("build-failed", log.read_text())

    def test_git_failure_not_treated_as_clean_source(self):
        with patch.object(build, "output", side_effect=[build.PINS["v1"],
                          subprocess.CalledProcessError(128, "git status")]):
            with self.assertRaises(subprocess.CalledProcessError):
                build.verify_source(self.root, "v1")

    def test_modified_config_rejected(self):
        self.put(".config", "changed\n")
        self.put("final.config", "original\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            build.verify_config(self.root, "v1")


if __name__ == "__main__":
    unittest.main()
