#!/usr/bin/env python3
"""Reject incomplete or contradictory runtime evidence."""
import unittest
from check_results import validate

GOOD = '''rtx_selftest: 17 passed, 0 failed
BVERIFY_CASE failures checks=10 failed=0
BVERIFY_TOTAL checks=10 failed=0
RTX_RUNTIME PASS
'''


class EvidenceTests(unittest.TestCase):
    def test_complete(self):
        self.assertEqual(validate(GOOD, ['failures'])['failures']['checks'], 10)

    def test_reject_bad_evidence(self):
        variants = [
            GOOD.replace('RTX_RUNTIME PASS', ''),
            GOOD.replace('rtx_selftest: 17 passed, 0 failed', ''),
            GOOD.replace('checks=10', 'checks=0'),
            GOOD.replace('failed=0', 'failed=1'),
            GOOD.replace('BVERIFY_TOTAL checks=10', 'BVERIFY_TOTAL checks=11'),
            GOOD + 'BVERIFY_CASE failures checks=10 failed=0\n',
            GOOD + 'WARNING: unexpected kernel warning\n',
            GOOD + 'BVERIFY FAIL injected\n',
            GOOD.replace('BVERIFY_CASE failures', 'BVERIFY_CASE unknown'),
        ]
        for text in variants:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    validate(text, ['failures'])


if __name__ == '__main__':
    unittest.main()
