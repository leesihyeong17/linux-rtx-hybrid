# SPDX-License-Identifier: GPL-2.0
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from arm64_ack_trace import append_probe, probe_fields
from build_arm64 import sha256


class AckTraceInputs(unittest.TestCase):
    def test_layout_is_bound_to_build_and_compiled_offsets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "vmlinux").write_bytes(b"test ELF")
            fields = dict(SKB_CB=40, CB_SEQ=0, CB_END_SEQ=4, CB_SEGS=8,
                          SEQ_SIZE=4, END_SEQ_SIZE=4, SEGS_SIZE=2)
            (root / "offsets.s").write_text("".join(f"->{key} {n} test\n" for key, n in fields.items()))
            ident = dict(source_sha="a", config_sha256="b")
            state = dict(ident, status="PASS", offsets=fields, vmlinux_sha256=sha256(root / "vmlinux"),
                         assembly_sha256=sha256(root / "offsets.s"))
            (root / "layout.json").write_text(json.dumps(state))
            self.assertIn("segs=+48(%x1):u16", probe_fields(root, root, ident))
            for changed in (dict(source_sha="different"), dict(config_sha256="different"),
                            dict(vmlinux_sha256="other ELF"), dict(offsets=dict(fields, SKB_CB=0))):
                (root / "layout.json").write_text(json.dumps(dict(state, **changed)))
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    probe_fields(root, root, ident)

    def test_registration_preserves_existing_probes_and_needs_no_seek(self):
        with tempfile.TemporaryDirectory() as folder:
            control = Path(folder) / "kprobe_events"
            control.write_text("another probe\n")
            append_probe(control, "one probe")
            self.assertEqual(control.read_text(), "another probe\none probe\n")
        # seq_lseek (also used by kprobe_events) rejects SEEK_END with EINVAL.
        # Use a read-only Linux seq_file and substitute only the command write.
        fd = os.open("/proc/self/maps", os.O_RDONLY)
        with patch("arm64_ack_trace.os.open", return_value=fd) as opened, \
                patch("arm64_ack_trace.os.write", return_value=4) as written:
            append_probe(Path("kprobe_events"), "cmd")
        opened.assert_called_once_with(Path("kprobe_events"), os.O_WRONLY | os.O_APPEND)
        written.assert_called_once_with(fd, b"cmd\n")
