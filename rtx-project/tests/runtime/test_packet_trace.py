import unittest
from check_packet_trace import validate

GOOD = '''RTX_PACKET SACK bytes=4000 retransmitted=0 PASS
RTX_PACKET LOSS bytes=4000 retransmitted=1000 PASS
RTX_PACKET RTO bytes=4000 retransmitted=1000 PASS
RTX_TRACE_BEGIN
# entries-in-buffer/entries-written: 9/9
 tracing_mark_write: CASE SACK
 rtx_fragment: (tcp_fragment) queue=1 len=1000
 rtx_result: (tcp_fragment) result=0
 tracing_mark_write: CASE LOSS
 rtx_fragment: (tcp_fragment) queue=1 len=3000
 rtx_result: (tcp_fragment) result=0
 tracing_mark_write: CASE RTO
 rtx_fragment: (tcp_fragment) queue=1 len=1000
 rtx_result: (tcp_fragment) result=0
RTX_TRACE_END
'''


class TraceTests(unittest.TestCase):
    def test_complete_success(self):
        self.assertEqual(validate(GOOD)['SACK']['rtx_entries'], 1)

    def test_rejects_missing_fragment(self):
        with self.assertRaises(ValueError):
            validate(GOOD.replace(' rtx_fragment: (tcp_fragment) queue=1 len=1000\n', ''))

    def test_rejects_write_queue(self):
        with self.assertRaises(ValueError):
            validate(GOOD.replace('queue=1', 'queue=0'))

    def test_rejects_error_return(self):
        with self.assertRaises(ValueError):
            validate(GOOD.replace('result=0', 'result=-12'))

    def test_rejects_trace_loss(self):
        with self.assertRaises(ValueError):
            validate(GOOD.replace('9/9', '9/10'))

    def test_rejects_no_retransmission(self):
        with self.assertRaises(ValueError):
            validate(GOOD.replace('retransmitted=1000', 'retransmitted=0'))

    def test_rejects_missing_case(self):
        with self.assertRaises(ValueError):
            validate(GOOD.replace('CASE LOSS', 'CASE UNKNOWN'))


if __name__ == '__main__':
    unittest.main()
