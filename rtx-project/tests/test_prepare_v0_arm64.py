#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Check preparation guards and failure evidence without building a kernel.

Git, uname, GCC identification, and make are controlled fixtures; scripts/config
and scripts/diffconfig are the repository tools. These tests do not establish
that V0 compiles or boots. The separate native build log supplies that evidence.
"""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "rtx-project/scripts/prepare_v0_arm64.sh"
BASELINE = "991defcc56cad49cfb221f44f5d161c9ea920c4a"


class PrepareV0Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rtx-prepare-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "scripts").mkdir(parents=True)
        for tool in ("config", "diffconfig"):
            shutil.copy2(REPO / "scripts" / tool, self.source / "scripts" / tool)
        self.seed = self.root / "seed.config"
        self.seed.write_text('CONFIG_ARM64=y\nCONFIG_LOCALVERSION="-old"\n'
                             'CONFIG_DEBUG_INFO=y\nCONFIG_DEBUG_INFO_BTF=y\n')
        self.output = self.root / "output"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = os.environ.copy()
        for key in ("KCFLAGS", "KCPPFLAGS", "KAFLAGS", "LDFLAGS_vmlinux",
                    "CFLAGS_KERNEL", "CFLAGS_MODULE"):
            self.env.pop(key, None)
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        MOCK_BASELINE=BASELINE,
                        MOCK_MAKE_MARKER=str(self.root / "make-called"))
        self.tool("uname", '''case "$1" in
  -m) printf '%s\\n' "${MOCK_MACHINE:-aarch64}" ;;
  *) /usr/bin/uname "$@" ;;
esac
''')
        self.tool("gcc", '''case "$1" in
  -dumpmachine) echo aarch64-linux-gnu ;;
  --version) echo 'gcc fixture (no compilation)' ;;
  *) exit 99 ;;
esac
''')
        self.tool("git", '''case "$3" in
  rev-parse)
    case "$4" in
      --show-toplevel) printf '%s\\n' "$2" ;;
      HEAD) printf '%s\\n' "${MOCK_HEAD:-$MOCK_BASELINE}" ;;
      'v0-baseline^{commit}') printf '%s\\n' "${MOCK_TAG:-$MOCK_BASELINE}" ;;
      *) exit 99 ;;
    esac ;;
  status)
    if [ "${MOCK_STATUS_FAILURE:-0}" = 1 ] ||
       { [ "${MOCK_STATUS_FAILURE_AFTER_MAKE:-0}" = 1 ] && [ -e "$MOCK_MAKE_MARKER" ]; }; then
      echo 'fatal: injected status failure' >&2
      exit 128
    fi
    printf '%s' "${MOCK_DIRTY:-}" ;;
  *) exit 99 ;;
esac
''')
        self.tool("make", '''if [ "$1" = --version ]; then
  echo 'make fixture (no compilation)'
  exit 0
fi
for arg do
  case "$arg" in O=*) output=${arg#O=} ;; esac
  target=$arg
done
printf '%s\\n' "$target" >>"$MOCK_MAKE_MARKER"
if [ "$target" = "${MOCK_FAIL_TARGET:-}" ]; then
  echo 'injected make failure' >&2
  exit 42
fi
case "$target" in
  olddefconfig)
    if [ "${MOCK_DROP_NETEM:-0}" = 1 ]; then
      sed -i '/^CONFIG_NET_SCH_NETEM=/d' "$output/.config"
    fi ;;
  prepare) : ;;
  kernelrelease) printf '%s\\n' "${MOCK_RELEASE:-5.4.214-rtx-v0}" ;;
  *) exit 99 ;;
esac
''')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\nset -eu\n" + body)
        path.chmod(0o755)

    def run_prepare(self, **env):
        return subprocess.run(
            [str(SCRIPT), "--source", str(self.source), "--output",
             str(self.output), "--config", str(self.seed)],
            env=dict(self.env, **env), text=True, capture_output=True, timeout=30)

    def assert_rejected_before_make(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / "make-called").exists())

    def test_wrong_head_and_tag_rejected(self):
        for key in ("MOCK_HEAD", "MOCK_TAG"):
            with self.subTest(key=key):
                result = self.run_prepare(**{key: "0" * 40})
                self.assert_rejected_before_make(result)
                self.assertFalse(self.output.exists())

    def test_dirty_source_rejected(self):
        self.assert_rejected_before_make(self.run_prepare(MOCK_DIRTY=" M net/tcp.c"))

    def test_git_status_error_is_not_clean_source(self):
        result = self.run_prepare(MOCK_STATUS_FAILURE="1")
        self.assert_rejected_before_make(result)
        self.assertFalse(self.output.exists())

    def test_non_arm64_host_and_config_rejected(self):
        self.assert_rejected_before_make(self.run_prepare(MOCK_MACHINE="x86_64"))
        self.seed.write_text("CONFIG_X86_64=y\n")
        self.assert_rejected_before_make(self.run_prepare())

    def test_existing_output_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "keep-me"
        sentinel.write_text("existing build\n")
        self.assert_rejected_before_make(self.run_prepare())
        self.assertEqual(sentinel.read_text(), "existing build\n")

    def test_dangling_output_symlink_is_rejected(self):
        target = self.root / "uncreated-target"
        self.output.symlink_to(target)
        self.assert_rejected_before_make(self.run_prepare())
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(target.exists())

    def test_ambient_flags_rejected(self):
        self.assert_rejected_before_make(self.run_prepare(KCFLAGS="-O0"))

    def test_unavailable_certificate_is_not_silently_cleared(self):
        with self.seed.open("a") as config:
            config.write('CONFIG_SYSTEM_TRUSTED_KEYS="missing-distro-cert.pem"\n')
        result = self.run_prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_SYSTEM_TRUSTED_KEYS", result.stderr)
        self.assertFalse((self.output / "prepare.log").exists())
        self.assertIn('CONFIG_SYSTEM_TRUSTED_KEYS="missing-distro-cert.pem"',
                      (self.output / ".config").read_text())

    def test_make_failure_exit_and_stage_preserved(self):
        result = self.run_prepare(MOCK_FAIL_TARGET="prepare")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertEqual((self.output / "prepare-status.txt").read_text(),
                         "exit_code=42\nstage=prepare\n")
        self.assertIn("injected make failure", (self.output / "prepare.log").read_text())
        self.assertFalse((self.output / "kernelrelease.log").exists())

    def test_kconfig_dropped_requirement_is_failure(self):
        result = self.run_prepare(MOCK_DROP_NETEM="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_NET_SCH_NETEM", result.stderr)
        self.assertIn("stage=config-verify", (self.output / "prepare-status.txt").read_text())

    def test_wrong_release_is_failure(self):
        result = self.run_prepare(MOCK_RELEASE="5.4.214-rtx-v0+")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected kernelrelease", result.stderr)
        self.assertIn("stage=record-config", (self.output / "prepare-status.txt").read_text())

    def test_final_git_status_failure_cannot_report_prepared(self):
        result = self.run_prepare(MOCK_STATUS_FAILURE_AFTER_MAKE="1")
        self.assertEqual(result.returncode, 128, result.stdout + result.stderr)
        self.assertNotIn("Prepared V0:", result.stdout)
        self.assertIn("exit_code=128", (self.output / "prepare-status.txt").read_text())

    def test_success_records_seed_final_delta_and_checksums(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.output / "prepare-status.txt").read_text(),
                         "exit_code=0\nstage=prepared\n")
        self.assertEqual((self.output / "seed.config").read_bytes(), self.seed.read_bytes())
        self.assertEqual((self.output / "final.config").read_bytes(),
                         (self.output / ".config").read_bytes())
        self.assertTrue((self.output / "config-delta.txt").read_text())
        self.assertEqual((self.root / "make-called").read_text(),
                         "olddefconfig\nprepare\nkernelrelease\n")
        checksum = subprocess.run(["sha256sum", "-c", "config.sha256"],
                                  cwd=str(self.output), text=True, capture_output=True)
        self.assertEqual(checksum.returncode, 0, checksum.stdout + checksum.stderr)
        self.assertIn("Image/modules build and boot are still pending", result.stdout)


if __name__ == "__main__":
    unittest.main()
