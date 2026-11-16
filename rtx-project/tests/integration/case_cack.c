// SPDX-License-Identifier: GPL-2.0
/*
 * #15 items 2-4 (C): real tcp_ack() processing after SACK.
 *  - partial ACK (tcp_tso_acked -> tcp_trim_head on page-fragment skbs) and
 *    cumulative removal across the TREE/LIST boundary, including a partial
 *    ACK inside a SACKed skb;
 *  - hint meaning after split, shift and removal (checked by c_check);
 *  - loss marking with tcp_mark_head_lost, retransmission re-entry, DSACK of
 *    a retransmission, reordering, and ACK processing after a second split
 *    failure.
 */

/* SACK, partial ACKs and cumulative ACKs without loss marking. */
static void c_ack_cleanup(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3;
	struct c_count a, b;
	unsigned long wmem;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;

	/* skb 1 whole, middle of first(L) (two splits) */
	B_EXPECT(c_ack(sk, c_base, 2, c_seq(3) + 1000, c_seq(3) + 3000,
		       c_seq(1), c_seq(2)) == 1);
	B_EXPECT(tp->sacked_out == 6 && b_prepare_count == 2);
	c_check(sk, "cleanup-A-sack");

	c_count(&a);
	wmem = sk->sk_wmem_queued;
	B_EXPECT(c_ack(sk, c_seq(0) + 1000, 0) == 1);
	c_count(&b);
	B_EXPECT(b.trim - a.trim == 1 && b.unlink == a.unlink);
	B_EXPECT(tp->snd_una == c_seq(0) + 1000);
	B_EXPECT(tp->packets_out == 4 * n - 1 && tp->sacked_out == 6);
	B_EXPECT(sk->sk_wmem_queued == wmem - 1000);
	B_EXPECT(TCP_SKB_CB(rtx_first(sk))->seq == c_seq(0) + 1000 &&
		 tcp_skb_pcount(rtx_first(sk)) == 3);
	c_check(sk, "cleanup-B-partial-head");

	/* removes the rest of skb 0, ACKs half of the SACKed skb 1 */
	c_count(&a);
	B_EXPECT(c_ack(sk, c_seq(1) + 2000, 0) == 1);
	c_count(&b);
	B_EXPECT(b.unlink - a.unlink == 1 && b.trim - a.trim == 1);
	B_EXPECT(tp->sacked_out == 4 && tp->packets_out == 4 * n - 6);
	B_EXPECT(TCP_SKB_CB(rtx_first(sk))->seq == c_seq(1) + 2000 &&
		 TCP_SKB_CB(rtx_first(sk))->sacked == TCPCB_SACKED_ACKED);
	c_check(sk, "cleanup-C-partial-sacked");

	/* through the old boundary into the SACKed middle piece of skb 3 */
	c_count(&a);
	B_EXPECT(c_ack(sk, c_seq(3) + 2000, 0) == 1);
	c_count(&b);
	B_EXPECT(b.unlink - a.unlink == 3 && b.trim - a.trim == 1);
	B_EXPECT(tp->sacked_out == 1);
	B_EXPECT(TCP_SKB_CB(rtx_first(sk))->seq == c_seq(3) + 2000);
	c_check(sk, "cleanup-D-cross-boundary");

	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->packets_out && !tp->sacked_out &&
		 !tp->lost_out && !tp->retrans_out && !sk->sk_wmem_queued);
	B_EXPECT(list_empty(&tp->tsorted_sent_queue));
#ifdef CONFIG_TCP_RTX_HYBRID
	B_EXPECT(!tp->rtx_tree_first && !tp->rtx_tree_last &&
		 !tp->rtx_recent_len);
#endif
	c_check(sk, "cleanup-E-all");
	pr_info("BVERIFY cack cleanup k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/*
 * Classic loss detection: SACKs above the head enter recovery,
 * tcp_mark_head_lost() sets lost_skb_hint/lost_cnt_hint and the head is
 * retransmitted through the real retransmit path. Then more SACKs (split,
 * shift), a partial ACK of the retransmitted skb, a DSACK for it, and the
 * final cumulative ACK. Hints are checked after every ACK.
 */
static void c_ack_recovery(u32 base)
{
	struct socket *sock = c_socket(base, C_CLASSIC);
	unsigned int n = c_k() + 3;
	struct c_count a, b;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;

	B_EXPECT(c_ack(sk, c_base, 1, c_seq(1) + 1000, c_seq(2)) == 1);
	B_EXPECT(tp->sacked_out == 3);
	c_check(sk, "recovery-A-sack3");

	B_EXPECT(c_ack(sk, c_base, 1, c_seq(1) + 1000, c_seq(2) + 2000) == 1);
	B_EXPECT(tp->sacked_out == 5);
	B_EXPECT(inet_csk(sk)->icsk_ca_state == TCP_CA_Recovery);
	B_EXPECT(tp->lost_out > 0 && tp->retrans_out > 0 && c_xmits > 0);
	B_EXPECT(tp->lost_skb_hint != NULL);
	c_check(sk, "recovery-B-lost-retrans");

	/* SACK the rest of skb 2 and two MSS of first(L) (shift) */
	c_count(&a);
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(1) + 1000, c_seq(3) + 2000) == 1);
	c_count(&b);
	B_EXPECT(b.partial + b.merge > a.partial + a.merge);
	c_check(sk, "recovery-C-shift");

	/* partial cumulative ACK inside the retransmitted head */
	B_EXPECT(c_ack(sk, c_seq(0) + 2000, 1, c_seq(1) + 1000,
		       c_seq(3) + 2000) == 1);
	c_check(sk, "recovery-D-partial-retrans");

	/* DSACK for the first retransmitted MSS (below snd_una) */
	c_count(&a);
	B_EXPECT(c_ack(sk, c_seq(0) + 2000, 2, c_seq(0), c_seq(0) + 1000,
		       c_seq(1) + 1000, c_seq(3) + 2000) == 1);
	c_count(&b);
	B_EXPECT(b.dsack - a.dsack == 1);
	c_check(sk, "recovery-E-dsack");

	/* the same DSACK again must not be counted twice into undo state */
	B_EXPECT(c_ack(sk, c_seq(0) + 2000, 2, c_seq(0), c_seq(0) + 1000,
		       c_seq(1) + 1000, c_seq(3) + 2000) == 1);
	c_check(sk, "recovery-F-dsack-again");

	B_EXPECT(c_ack(sk, c_seq(3) + 2000, 0) == 1);
	c_check(sk, "recovery-G-ack-boundary");
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->packets_out && !tp->sacked_out &&
		 !tp->lost_out && !tp->retrans_out && !sk->sk_wmem_queued);
	c_check(sk, "recovery-H-all");
	pr_info("BVERIFY cack recovery k=%u base=%u xmits=%u retrans=%u\n",
		c_k(), base, c_xmits, tp->total_retrans);
out:
	c_release(sock);
}

/* SACK above the head, then a cumulative ACK of never-retransmitted data. */
static void c_ack_reorder(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3, seen;
	struct c_count a, b;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(2), c_seq(3) + 2000) == 1);
	c_check(sk, "reorder-A-sack");
	seen = tp->reord_seen;
	c_count(&a);
	B_EXPECT(c_ack(sk, c_seq(2), 1, c_seq(2), c_seq(3) + 2000) == 1);
	c_count(&b);
	B_EXPECT(b.reorder - a.reorder == 1 && tp->reord_seen == seen + 1);
	B_EXPECT(!tp->lost_out && !tp->retrans_out && !c_xmits);
	c_check(sk, "reorder-B-ack");
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !sk->sk_wmem_queued);
	c_check(sk, "reorder-C-all");
	pr_info("BVERIFY cack reorder k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/*
 * Second split of one SACK block fails inside tcp_fragment(): the first
 * split stays, nothing is SACKed, and later ACKs (retry, partial and full
 * cumulative) still process the queue correctly.
 */
static void c_ack_split_failure(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3;
	struct sk_buff *first, *middle;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	b_fail_alloc_at = 2;
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(3) + 1000, c_seq(3) + 3000) == 1);
	b_fail_alloc_at = 0;
	B_EXPECT(b_alloc_count == 2 && b_fault_hits == 1);
	B_EXPECT(b_prepare_count == 1 && b_commit_count == 1);
	B_EXPECT(!tp->sacked_out && !tp->rtx_txn_open);
	first = rtx_find_cover(sk, c_seq(3));
	middle = rtx_find_cover(sk, c_seq(3) + 1000);
	B_EXPECT(first && middle && first != middle &&
		 TCP_SKB_CB(middle)->seq == c_seq(3) + 1000 &&
		 TCP_SKB_CB(middle)->end_seq == c_seq(4));
	c_check(sk, "splitfail-A-failed");

	B_EXPECT(c_ack(sk, c_base, 1, c_seq(3) + 1000, c_seq(3) + 3000) == 1);
	B_EXPECT(b_prepare_count == 2 && tp->sacked_out == 2);
	c_check(sk, "splitfail-B-retry");

	B_EXPECT(c_ack(sk, c_seq(3) + 2000, 1, c_seq(3) + 1000,
		       c_seq(3) + 3000) == 1);
	B_EXPECT(tp->sacked_out == 1);
	c_check(sk, "splitfail-C-partial");
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->packets_out && !sk->sk_wmem_queued);
	c_check(sk, "splitfail-D-all");
	pr_info("BVERIFY cack split-failure k=%u base=%u\n", c_k(), base);
out:
	c_release(sock);
}

/* The oracles must reject wrong states (negative controls). */
static void c_ack_negative(void)
{
	struct socket *sock = c_socket(1000, C_CLASSIC);
	struct sk_buff *skb, *page_skb;
	struct tcp_sock *tp;
	struct sock *sk;
	u8 *p;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, 4))
		goto out;
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(1) + 1000, c_seq(2) + 2000) == 1);
	B_EXPECT(c_hints(sk, "negative-base") && c_payload(sk));
	B_EXPECT(tp->lost_skb_hint && tp->highest_sack);
	if (!tp->lost_skb_hint || !tp->highest_sack)
		goto out;

	tp->lost_cnt_hint++;
	B_EXPECT(!c_hints(sk, "negative-lost-cnt"));
	tp->lost_cnt_hint--;

	skb = tp->highest_sack;
	tp->highest_sack = rtx_next(sk, skb);
	B_EXPECT(!c_hints(sk, "negative-highest"));
	tp->highest_sack = skb;

	skb = tp->retransmit_skb_hint;
	tp->retransmit_skb_hint = rtx_last(sk);
	TCP_SKB_CB(rtx_first(sk))->sacked |= TCPCB_LOST;
	TCP_SKB_CB(rtx_first(sk))->sacked &= ~TCPCB_SACKED_RETRANS;
	B_EXPECT(!c_hints(sk, "negative-retransmit"));
	tp->retransmit_skb_hint = skb;

	page_skb = rtx_last(sk);
	p = page_address(skb_frag_page(&skb_shinfo(page_skb)->frags[0]));
	p[skb_frag_off(&skb_shinfo(page_skb)->frags[0])] ^= 0xff;
	B_EXPECT(!c_payload(sk));
	p[skb_frag_off(&skb_shinfo(page_skb)->frags[0])] ^= 0xff;
	B_EXPECT(c_payload(sk));
	pr_info("BVERIFY cack negative controls=4\n");
out:
	/* the forced LOST/RETRANS flags would break purge accounting checks */
	tp->sacked_out = tp->lost_out = tp->retrans_out = 0;
	for (skb = rtx_first(sk); skb; skb = rtx_next(sk, skb))
		TCP_SKB_CB(skb)->sacked = 0;
	c_release(sock);
}

static void c_ack_all(void)
{
	c_ack_cleanup(1000);
	c_ack_cleanup((u32)-7000);
	c_ack_recovery(1000);
	c_ack_recovery((u32)-5000);
	c_ack_reorder(1000);
	c_ack_split_failure(1000);
}

static void b_case_cack(void)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	c_shape = CONFIG_TCP_RTX_RECENT_MAX;
	c_ack_all();
#else
	c_shape = 16;
	c_ack_all();
	c_shape = 1;
	c_ack_all();
#endif
	c_ack_negative();
}
