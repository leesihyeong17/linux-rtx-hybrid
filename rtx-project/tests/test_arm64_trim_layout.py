# SPDX-License-Identifier: GPL-2.0
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from verify_arm64_trim_layout import parse_offsets, probe_definitions


ASSEMBLY = '''
.ascii "->SKB_CB 40 offsetof(struct sk_buff, cb)"
.ascii "->CB_SEQ 0 offsetof(struct tcp_skb_cb, seq)"
.ascii "->CB_END_SEQ 4 offsetof(struct tcp_skb_cb, end_seq)"
.ascii "->CB_SEGS 8 offsetof(struct tcp_skb_cb, tcp_gso_segs)"
.ascii "->SEQ_SIZE 4 sizeof(seq)"
.ascii "->END_SEQ_SIZE 4 sizeof(end_seq)"
.ascii "->SEGS_SIZE 2 sizeof(tcp_gso_segs)"
'''


class LayoutTests(unittest.TestCase):
    def test_probe_follows_compiler_offsets_instead_of_assuming_layout(self):
        offsets = parse_offsets(ASSEMBLY.replace("SKB_CB 40", "SKB_CB 56"))
        text = probe_definitions(offsets)
        self.assertIn("seq=+56(%x1):u32", text)
        self.assertIn("end_seq=+60(%x1):u32", text)
        self.assertIn("segs=+64(%x1):u16", text)
        self.assertIn("len=%x2:u32", text)
        self.assertIn("ret=$retval:s32", text)

    def test_missing_offset_cannot_produce_probe(self):
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            parse_offsets(ASSEMBLY.replace("->CB_SEGS", "->OTHER"))

    def test_duplicate_offset_cannot_produce_probe(self):
        with self.assertRaisesRegex(ValueError, "missing/duplicate"):
            parse_offsets(ASSEMBLY + '.ascii "->SKB_CB 56 other"')

    def test_wrong_fetch_width_rejected(self):
        with self.assertRaisesRegex(ValueError, "field widths"):
            parse_offsets(ASSEMBLY.replace("SEGS_SIZE 2", "SEGS_SIZE 4"))


if __name__ == "__main__":
    unittest.main()
