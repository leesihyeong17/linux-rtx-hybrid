"""Quick run of the A user-space model test (tools/rtx_model).

Builds the harness from this checkout's include/net/tcp_rtx.h and runs the
required-case scenarios plus a few random seeds. The full run with many
seeds and the mutation check is scripts/a_model_test.sh.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(ROOT, "rtx-project", "tools", "rtx_model")
NEEDED = ("include/net/tcp_rtx.h", "include/linux/rbtree.h",
          "include/linux/list.h", "lib/rbtree.c", "net/ipv4/tcp_input.c")

sys.path.insert(0, TOOL)
import rtx_model  # noqa: E402


@unittest.skipUnless(shutil.which(os.environ.get("CC", "gcc")), "no C compiler")
@unittest.skipUnless(all(os.path.exists(os.path.join(ROOT, p)) for p in NEEDED),
                     "kernel sources not checked out (sparse clone)")
class RtxModelQuick(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rtx_model_test.")
        env = dict(os.environ, RTX_MODEL_KS="1 4", RTX_MODEL_PLAIN_KS="4")
        subprocess.run([os.path.join(TOOL, "build.sh"), cls.tmp], check=True,
                       env=env, capture_output=True)
        cls.bins = rtx_model.list_bins(cls.tmp)
        cls.ks = sorted({k for _, k in cls.bins},
                        key=lambda k: -1 if k is None else k)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_scenarios(self):
        for label, tr in rtx_model.scenarios(self.ks):
            fails = rtx_model.run_trace(self.tmp, self.bins, tr, label, None)
            self.assertEqual(fails, [], label)

    def test_random(self):
        for seed in range(10):
            tr = rtx_model.gen_random(seed, self.ks)
            fails = rtx_model.run_trace(self.tmp, self.bins, tr,
                                        "seed%d" % seed, None)
            self.assertEqual(fails, [], seed)

    def test_rejects_wrong_output(self):
        """The comparison must fail when an expected line is wrong."""
        tr = rtx_model.gen_random(1, self.ks)
        tr.expect[-1] = "D first=999"
        fails = rtx_model.run_trace(self.tmp, self.bins, tr, "neg", None)
        self.assertEqual(len(fails), len(self.bins))

    def test_hist_buckets_from_spec(self):
        hb = rtx_model.hist_bucket
        self.assertEqual([hb(n, "v1") for n in (1, 2, 3, 4, 65535, 65536, 10**6)],
                         [0, 1, 1, 2, 15, 15, 15])
        self.assertEqual([hb(n, "v2") for n in (0, 64, 65, 127, 128, 16383,
                                                16384, 10**6)],
                         [0, 64, 65, 65, 66, 72, 73, 73])


if __name__ == "__main__":
    unittest.main()
