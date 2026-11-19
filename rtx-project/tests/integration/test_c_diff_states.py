import unittest

from c_diff_states import compare

REF = """[ 1.0] CSTATE k16 a-0 una=0 so=0 q=0+4000:0/4,
[ 1.1] CSTATE k16 a-1 una=0 so=3 q=0+1000:0/1,1000+3000:1/3,
[ 1.2] CSTATE k1 a-0 una=0 so=0 q=0+4000:0/4,
"""


class CompareTest(unittest.TestCase):
    def test_same(self):
        self.assertEqual(compare(REF, REF, 16)['diffs'], [])

    def test_shape_filter(self):
        self.assertEqual(compare(REF, REF, 1)['steps'], 1)

    def test_rejects_state_difference(self):
        hyb = REF.replace('so=3', 'so=4')
        self.assertEqual(compare(hyb, REF, 16)['diffs'], ['a-1'])

    def test_rejects_missing_step(self):
        hyb = '\n'.join(REF.splitlines()[:1] + REF.splitlines()[2:])
        with self.assertRaises(ValueError):
            compare(hyb, REF, 16)

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            compare('', REF, 16)


if __name__ == '__main__':
    unittest.main()
