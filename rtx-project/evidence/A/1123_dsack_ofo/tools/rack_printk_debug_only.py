#!/usr/bin/env python3
"""E7 전용: net/ipv4/tcp_recovery.c에 RACK 판정값 pr_info를 넣는다 (디버그 빌드 전용, 커밋 금지).
사용: rack_printk_debug_only.py <커널 트리>/net/ipv4/tcp_recovery.c"""
import sys
p = sys.argv[1]
s = open(p).read()
a = '''	tp->rack.advanced = 1;
	tp->rack.rtt_us = rtt_us;'''
b = '''		remaining = tcp_rack_skb_timeout(tp, skb, reo_wnd);
		if (remaining <= 0) {'''
assert s.count(a) == 1 and s.count(b) == 1
s = s.replace(a, a + '''
	pr_info("RACKDBG advance end_seq=%u rtt_us=%u sacked=%x xmit=%llu\\n", end_seq, rtt_us, sacked, xmit_time);''')
s = s.replace(b, '''		remaining = tcp_rack_skb_timeout(tp, skb, reo_wnd);
		pr_info("RACKDBG detect seq=%u reo_wnd=%u rack_rtt=%u age=%u remaining=%d ca=%u sacked_out=%u\\n", TCP_SKB_CB(skb)->seq, reo_wnd, tp->rack.rtt_us, tcp_stamp_us_delta(tp->tcp_mstamp, tcp_skb_timestamp_us(skb)), remaining, inet_csk(sk)->icsk_ca_state, tp->sacked_out);
		if (remaining <= 0) {''')
open(p, 'w').write(s)
