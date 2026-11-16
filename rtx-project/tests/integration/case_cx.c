// SPDX-License-Identifier: GPL-2.0
/*
 * #15 C follow-up beyond the first matrix: the remaining paths.
 *  - SACK hole fill merges the skb that is lost_skb_hint;
 *  - retransmission re-split (tcp_xmit_retransmit_queue -> tcp_fragment)
 *    at the TREE last / LIST first / LIST tail skb (joint with B);
 *  - RACK time-based loss, and the RACK reorder, TLP and RTO timer handlers
 *    run directly (the handler bodies, not the timer wheel);
 *  - IPv6 sockets for the main ACK/SACK scenarios.
 */

/*
 * skb 1 and skb 3 SACKed, skb 2 lost (classic dupthresh): lost_skb_hint is
 * skb 3 with lost_cnt_hint 4. Filling skb 2 merges skb 2 and then skb 3
 * (the hint) into skb 1; the hint must move to skb 1 with count 0.
 * With n = K + 3, skb 2 is TREE last and skb 3 is LIST first.
 */
static void c_lost_hint_merge(u32 base)
{
	struct socket *sock = c_socket(base, C_CLASSIC);
	unsigned int n = c_k() + 3;
	struct sk_buff *s1, *h;
	struct c_count cnt;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	c_block_xmit = true;
	B_EXPECT(c_ack(sk, c_base, 2, c_seq(3), c_seq(4), c_seq(1),
		       c_seq(2)) == 1);
	s1 = rtx_find_cover(sk, c_seq(1));
	h = rtx_find_cover(sk, c_seq(3));
	B_EXPECT(inet_csk(sk)->icsk_ca_state == TCP_CA_Recovery);
	B_EXPECT(h && tp->lost_skb_hint == h && tp->lost_cnt_hint == 4);
	B_EXPECT(TCP_SKB_CB(rtx_find_cover(sk, c_seq(2)))->sacked ==
		 TCPCB_LOST);
	B_EXPECT(!c_xmits && !tp->retrans_out);
	c_check(sk, "losthint-A-marked");
	if (!s1 || !h)
		goto out;

	c_count(&cnt);
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(1), c_seq(4)) == 1);
	c_expect_shift(&cnt, 0, 2);
	B_EXPECT(tp->lost_skb_hint == s1 && tp->lost_cnt_hint == 0);
	B_EXPECT(TCP_SKB_CB(s1)->end_seq == c_seq(4) &&
		 tcp_skb_pcount(s1) == 12 && tp->sacked_out == 12);
	B_EXPECT(tp->lost_out == 4);
	c_check(sk, "losthint-B-merged");

	c_block_xmit = false;
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->packets_out && !sk->sk_wmem_queued);
	c_check(sk, "losthint-C-all");
	pr_info("BVERIFY cx lost-hint-merge k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/*
 * Mark one skb lost, enter recovery (real tcp_enter_recovery), then run
 * the real retransmit loop with room for two segments: it re-splits the lost
 * 4-segment skb (B's split path entered from retransmission). idx is the
 * TREE last (2), LIST first (3) or LIST tail (n - 1) skb.
 */
static void c_rexmit_split(u32 base, unsigned int idx)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3;
	struct sk_buff *skb, *back;
	struct tcp_sock *tp;
	struct sock *sk;
	char tag[40];

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n) || idx >= n)
		goto out;
	skb = rtx_find_cover(sk, c_seq(idx));
	/* the order tcp_fastretrans_alert() uses: mark, enter recovery, send */
	tcp_mark_skb_lost(sk, skb);
	B_EXPECT(tp->lost_out == 4);
	c_test_enter_recovery(sk);
	B_EXPECT(inet_csk(sk)->icsk_ca_state == TCP_CA_Recovery);
	tp->snd_cwnd = tcp_packets_in_flight(tp) + 2;
	tcp_xmit_retransmit_queue(sk);
	back = rtx_next(sk, skb);
	B_EXPECT(b_prepare_count == 1 && b_commit_count == 1 && c_xmits == 1);
	B_EXPECT(TCP_SKB_CB(skb)->end_seq == c_seq(idx) + 2000 &&
		 tcp_skb_pcount(skb) == 2);
	B_EXPECT((TCP_SKB_CB(skb)->sacked & (TCPCB_LOST | TCPCB_SACKED_RETRANS))
		 == (TCPCB_LOST | TCPCB_SACKED_RETRANS));
	B_EXPECT(back && TCP_SKB_CB(back)->seq == c_seq(idx) + 2000 &&
		 (TCP_SKB_CB(back)->sacked & TCPCB_TAGBITS) == TCPCB_LOST);
	B_EXPECT(tp->retrans_out == 2 && tp->lost_out == 4 &&
		 tp->total_retrans == 2);
	B_EXPECT(tp->retransmit_skb_hint == back);
	snprintf(tag, sizeof(tag), "rexmit%u-A-split", idx);
	c_check(sk, tag);

	/* SACK the not retransmitted half, then ACK through the skb */
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(idx) + 2000, c_seq(idx + 1)) == 1);
	B_EXPECT(tp->sacked_out == 2);
	snprintf(tag, sizeof(tag), "rexmit%u-B-sack", idx);
	c_check(sk, tag);
	B_EXPECT(c_ack(sk, c_seq(idx + 1), 0) == 1);
	B_EXPECT(!tp->retrans_out && !tp->lost_out && !tp->sacked_out);
	snprintf(tag, sizeof(tag), "rexmit%u-C-acked", idx);
	c_check(sk, tag);
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !sk->sk_wmem_queued);
	pr_info("BVERIFY cx rexmit-split k=%u idx=%u base=%u\n", c_k(), idx,
		base);
out:
	c_release(sock);
}

/*
 * RACK: skbs sent 1 ms apart; SACKing the last one with sacked_out >=
 * reordering gives a zero reordering window, so every earlier skb is
 * marked lost by time, not by dupthresh.
 */
static void c_rack_loss(u32 base)
{
	struct socket *sock = c_socket(base, C_RACK);
	unsigned int n = c_k() + 3, i = 0;
	struct sk_buff *skb;
	struct tcp_sock *tp;
	struct sock *sk;
	u64 now;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	now = tcp_clock_ns();
	for (skb = rtx_first(sk); skb; skb = rtx_next(sk, skb), i++)
		skb->skb_mstamp_ns = now - (n - i) * NSEC_PER_MSEC;
	c_block_xmit = true;
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(n - 1), c_seq(n)) == 1);
	B_EXPECT(tp->lost_out == 4 * (n - 1) && tp->sacked_out == 4);
	B_EXPECT(inet_csk(sk)->icsk_ca_state == TCP_CA_Recovery);
	c_check(sk, "rack-A-lost");
	c_block_xmit = false;
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(n - 1), c_seq(n)) == 1);
	B_EXPECT(tp->retrans_out > 0 && c_xmits > 0);
	c_check(sk, "rack-B-retrans");
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->lost_out && !tp->retrans_out);
	c_check(sk, "rack-C-all");
	pr_info("BVERIFY cx rack-loss k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/*
 * RACK reorder timer: equal send times and a large reordering window leave
 * the holes unmarked and arm the timer. The handler runs with tcp_mstamp
 * moved 10 s ahead (the expiry time), so the holes become lost and are
 * retransmitted.
 */
static void c_rack_timer(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(n - 2) + 1000, c_seq(n)) == 1);
	B_EXPECT(!tp->lost_out && tp->sacked_out == 7);
	B_EXPECT(inet_csk(sk)->icsk_pending == ICSK_TIME_REO_TIMEOUT);
	c_check(sk, "reotimer-A-armed");
	tp->tcp_mstamp += 10 * USEC_PER_SEC;
	tcp_rack_reo_timeout(sk);
	B_EXPECT(tp->lost_out == 4 * (n - 2) + 1);
	B_EXPECT(inet_csk(sk)->icsk_ca_state == TCP_CA_Recovery);
	B_EXPECT(tp->retrans_out > 0 && c_xmits > 0);
	c_check(sk, "reotimer-B-fired");
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->lost_out && !tp->retrans_out);
	c_check(sk, "reotimer-C-all");
	pr_info("BVERIFY cx rack-timer k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/*
 * TLP: the probe splits the LIST tail (pcount 4 -> 3 + 1) and retransmits
 * its last segment. The ACK for everything with a DSACK of that segment
 * ends the probe episode.
 */
static void c_tlp(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3;
	unsigned long probes;
	struct c_count a, b;
	struct sk_buff *last;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	probes = c_mib(LINUX_MIB_TCPLOSSPROBES);
	tcp_send_loss_probe(sk);
	last = rtx_last(sk);
	B_EXPECT(c_mib(LINUX_MIB_TCPLOSSPROBES) == probes + 1);
	B_EXPECT(tp->tlp_high_seq == tp->snd_nxt && tp->tlp_retrans);
	B_EXPECT(b_prepare_count == 1 && c_xmits == 1);
	B_EXPECT(last && TCP_SKB_CB(last)->seq == c_seq(n) - 1000 &&
		 tcp_skb_pcount(last) == 1);
	c_check(sk, "tlp-A-probe");
	c_count(&a);
	B_EXPECT(c_ack(sk, tp->snd_nxt, 1, c_seq(n) - 1000, c_seq(n)) == 1);
	c_count(&b);
	B_EXPECT(b.dsack - a.dsack == 1 && !tp->tlp_high_seq);
	B_EXPECT(rtx_empty(sk) && !sk->sk_wmem_queued);
	c_check(sk, "tlp-B-dsack-all");
	pr_info("BVERIFY cx tlp k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/*
 * RTO: after SACKs (one splitting the LIST head), the RTO handler enters
 * Loss, marks every unSACKed skb lost and retransmits one segment of the
 * head (re-split of the TREE first skb). Partial and full ACKs follow.
 */
static void c_rto(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3, sacked;
	unsigned long timeouts;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	B_EXPECT(c_ack(sk, c_base, 2, c_seq(3) + 1000, c_seq(3) + 3000,
		       c_seq(1), c_seq(2)) == 1);
	sacked = tp->sacked_out;
	B_EXPECT(sacked == 6 && b_prepare_count == 2);
	c_check(sk, "rto-A-sack");
	timeouts = c_mib(LINUX_MIB_TCPTIMEOUTS);
	tcp_retransmit_timer(sk);
	B_EXPECT(inet_csk(sk)->icsk_ca_state == TCP_CA_Loss);
	B_EXPECT(c_mib(LINUX_MIB_TCPTIMEOUTS) == timeouts + 1);
	B_EXPECT(tp->lost_out == tp->packets_out - tp->sacked_out);
	B_EXPECT(tp->retrans_out == 1 && c_xmits == 1 &&
		 b_prepare_count == 3);
	B_EXPECT(tcp_skb_pcount(rtx_first(sk)) == 1);
	c_check(sk, "rto-B-fired");
	B_EXPECT(c_ack(sk, c_seq(0) + 1000, 1, c_seq(1), c_seq(2)) == 1);
	B_EXPECT(!tp->retrans_out || c_xmits > 1);
	c_check(sk, "rto-C-partial");
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->lost_out && !tp->retrans_out &&
		 !sk->sk_wmem_queued);
	c_check(sk, "rto-D-all");
	pr_info("BVERIFY cx rto k=%u base=%u xmits=%u\n", c_k(), base,
		c_xmits);
out:
	c_release(sock);
}

static void c_x_all(void)
{
	unsigned int n = c_k() + 3;

	c_lost_hint_merge(1000);
	c_lost_hint_merge((u32)-8000);
	c_rexmit_split(1000, 2);
	c_rexmit_split(1000, 3);
	if (n - 1 != 3)
		c_rexmit_split(1000, n - 1);
	c_rack_loss(1000);
	c_rack_timer(1000);
	c_tlp(1000);
	c_tlp((u32)-3000);
	c_rto(1000);

	/* the main ACK/SACK scenarios once more on IPv6 sockets */
	c_family = AF_INET6;
	c_ack_cleanup(1000);
	c_ack_recovery(1000);
	c_shift_boundary(1000);
	c_shift_pair(1000, 2);
	c_lost_hint_merge(1000);
	c_rto(1000);
	c_family = AF_INET;
}

static void b_case_cx(void)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	c_shape = CONFIG_TCP_RTX_RECENT_MAX;
	c_x_all();
#else
	c_shape = 16;
	c_x_all();
	c_shape = 1;
	c_x_all();
#endif
}
