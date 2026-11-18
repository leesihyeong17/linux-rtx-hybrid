# SPDX-License-Identifier: GPL-2.0
"""Reject incomplete native evidence and wrong installed/reference kernels."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("collect_c_arm64", SCRIPTS / "collect_c_arm64.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

# Synthetic unit-test input, never used as boot evidence.
STATE_DATA = ("una=0 nxt=4000 po=4 so=0 lo=0 ro=0 del=0 tr=0 ur=0 "
              "reo=3 ca=0 hs=-1 lh=-1/0 rh=-1 q=0+4000:0/4,")


def synthetic_states(shape):
    return "".join("[2.0] CSTATE k{} {} {}\n".format(shape, tag, STATE_DATA)
                   for tag in collector.expected_state_tags(shape))


LOG = "[1.0] rtx_selftest: 17 passed, 0 failed\n" + synthetic_states(16) + """[3.0] BVERIFY_CASE cack checks=3 failed=0
[3.1] BVERIFY_CASE cshift checks=2 failed=0
[3.2] BVERIFY_TOTAL checks=5 failed=0
"""


class NativeFixtureTests(unittest.TestCase):
    def check(self, text, shapes=(16,)):
        return collector.validate_fixture(text, ["cack", "cshift"], shapes)

    def test_native_success_does_not_require_qemu_guest(self):
        result = self.check(LOG)
        self.assertEqual(result["total_checks"], 5)
        self.assertEqual(result["state_steps"], {16: 139})

    def test_rejects_bad_or_incomplete_fixture(self):
        variants = ["", LOG.replace("cack checks=3", "cack checks=0"),
                    LOG.replace("cack checks=3 failed=0", "cack checks=3 failed=1"),
                    LOG.replace("TOTAL checks=5", "TOTAL checks=4"),
                    LOG.replace("BVERIFY_CASE cshift checks=2 failed=0", ""),
                    LOG.replace("BVERIFY_CASE cshift", "BVERIFY_CASE unknown"),
                    LOG + "BVERIFY_CASE cack checks=3 failed=0\n",
                    LOG + "BVERIFY_TOTAL checks=5 failed=0\n",
                    LOG + "BVERIFY_CASE cack checks=invalid failed=0\n",
                    LOG.replace("BVERIFY_TOTAL checks=5 failed=0", "BVERIFY_TOTAL"),
                    LOG.replace("rtx_selftest: 17 passed, 0 failed", ""),
                    LOG.replace("rtx_selftest: 17 passed, 0 failed", "rtx_selftest: 17 passed, 1 failed")]
        for text in variants:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.check(text)

    def test_rejects_kernel_diagnostics_even_with_zero_failures(self):
        for marker in ("BVERIFY FAIL", "WARNING:", "BUG:", "Oops:", "Kernel panic", "KASAN:", "UBSAN:",
                       "RTX_RUNTIME FAIL", "RTX_RUNTIME TIMEOUT", "CFAIL cleanup"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                self.check(LOG + marker)

    def test_rejects_missing_or_wrong_state_shape(self):
        variants = ["\n".join(line for line in LOG.splitlines() if "CSTATE" not in line),
                    LOG.replace("CSTATE k16", "CSTATE k1"), LOG + "CSTATE\n",
                    LOG + "CSTATE k1 cleanup una=0\n"]
        for text in variants:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.check(text)

    def test_rb_requires_both_shapes(self):
        with self.assertRaises(ValueError):
            self.check(LOG, (1, 16))
        result = self.check(LOG + synthetic_states(1), (1, 16))
        self.assertEqual(result["state_steps"], {16: 139, 1: 128})

    def test_rejects_missing_duplicate_or_reordered_state_steps(self):
        first = "[2.0] CSTATE k16 cleanup-A-sack " + STATE_DATA + "\n"
        second = "[2.0] CSTATE k16 cleanup-B-partial-head " + STATE_DATA + "\n"
        variants = [LOG.replace(first, "", 1), LOG + first,
                    LOG.replace(first + second, second + first, 1),
                    LOG.replace("cleanup-A-sack", "unknown", 1),
                    "\n".join(line for line in LOG.splitlines()
                              if "CSTATE" not in line or "cleanup-A-sack" in line)]
        for text in variants:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "CSTATE step"):
                self.check(text)

    def test_rejects_truncated_or_malformed_state_payload(self):
        for data in ("una=0", STATE_DATA.replace("nxt=4000 ", ""),
                     STATE_DATA.replace("q=0+4000:0/4,", "q=0+4000:"),
                     STATE_DATA.replace("po=4", "po=wrong")):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "CSTATE payload"):
                self.check(LOG.replace(STATE_DATA, data, 1))

    def test_pinned_inventory_covers_shape_and_ipv6_paths(self):
        rb_k1 = collector.expected_state_tags(1)
        k16 = collector.expected_state_tags(16)
        # Counts come from cack (33), cshift (30/22), cx (76/73).
        self.assertEqual((len(k16), len(rb_k1)), (139, 128))
        self.assertEqual(k16.count("cleanup-A-sack"), 2)
        self.assertEqual(k16.count("pair2-C-merge2"), 2)
        self.assertEqual(sum(tag.startswith("v6-") for tag in k16), 33)
        self.assertIn("rexmit18-A-split", k16)
        self.assertNotIn("rexmit18-A-split", rb_k1)
        self.assertNotIn("pair3-C-merge2", rb_k1)


class BootIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build, self.boot = self.root / "build", self.root / "boot"
        self.release = "5.4.214-rtx-c23-rb"
        for path, data in ((self.build / ".config", "config"),
                           (self.build / "arch/arm64/boot/Image", "image"),
                           (self.boot / ("config-" + self.release), "config"),
                           (self.boot / ("vmlinuz-" + self.release), "image"),
                           (self.boot / ("System.map-" + self.release), "map")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data)
        self.row = {"status": "PASS", "exit_code": 0, "kernelrelease": self.release,
                    "build": str(self.build), "config_sha256": collector.sha256(self.build / ".config"),
                    "hashes": {"arch/arm64/boot/Image": collector.sha256(self.build / "arch/arm64/boot/Image"),
                               "System.map": collector.sha256(self.boot / ("System.map-" + self.release))}}
        self.state = {"status": "PASS", "production_sha": collector.PRODUCTION,
                      "harness_sha": collector.HARNESS,
                      "modes": {"rb": self.row, "hybrid": {"status": "PASS", "exit_code": 0}}}

    def verify(self, **changes):
        return collector.verify_identity(self.state, "rb", changes.get("release", self.release),
                                         changes.get("arch", "aarch64"), self.boot)

    def test_installed_build_identity_matches(self):
        self.assertEqual(len(self.verify()), 5)

    def test_rejects_current_or_other_arch_kernel(self):
        for args in ({"release": "5.4.214-kimseungbeom+"}, {"arch": "x86_64"}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.verify(**args)

    def test_rejects_replaced_boot_image(self):
        (self.boot / ("vmlinuz-" + self.release)).write_text("different image")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            self.verify()

    def test_rejects_incomplete_or_wrong_source_build(self):
        original = copy.deepcopy(self.state)
        for key, value in (("status", "RUNNING"), ("production_sha", "wrong"), ("harness_sha", "wrong")):
            self.state = dict(original, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.verify()

    def test_rejects_missing_extra_or_unsuccessful_build_pair(self):
        original = copy.deepcopy(self.state)
        variants = [{"rb": self.row}, {}, None,
                    dict(original["modes"], extra={"status": "PASS", "exit_code": 0})]
        for mode in ("rb", "hybrid"):
            for key, value in (("status", "RUNNING"), ("status", "FAIL"), ("exit_code", 1),
                               ("exit_code", None)):
                modes = copy.deepcopy(original["modes"])
                modes[mode][key] = value
                variants.append(modes)
        for modes in variants:
            self.state = dict(original, modes=modes)
            with self.subTest(modes=modes), self.assertRaisesRegex(ValueError, "both RB and HYBRID"):
                self.verify()

    def test_reference_requires_same_rb_build_and_unchanged_log(self):
        log = self.root / "dmesg.log"
        log.write_text(LOG)
        ref = {"status": "PASS", "mode": "rb", "production_sha": collector.PRODUCTION,
               "harness_sha": collector.HARNESS, "running_kernel": self.release, "architecture": "aarch64",
               "config_sha256": self.row["config_sha256"],
               "image_sha256": self.row["hashes"]["arch/arm64/boot/Image"],
               "dmesg_sha256": collector.sha256(log)}
        collector.verify_reference(ref, self.state, log)
        for key, value in (("status", "FAIL"), ("mode", "hybrid"), ("config_sha256", "wrong"),
                           ("image_sha256", "wrong"), ("harness_sha", "wrong"), ("architecture", "x86_64")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                collector.verify_reference(dict(ref, **{key: value}), self.state, log)
        log.write_text(LOG + "changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            collector.verify_reference(ref, self.state, log)


if __name__ == "__main__":
    unittest.main()
