// SPDX-License-Identifier: GPL-2.0
/*
 * #15 item 1 (C): split, then real partial shift / full merge on
 * page-fragment skbs, including the TREE last / LIST first boundary and the
 * case where the current and the next skb are both merged in one
 * tcp_shift_skb_data() call. Run through tcp_ack() with real SACK options.
 */

#ifdef CONFIG_TCP_RTX_HYBRID
#define C_DOMAIN(sk, skb, d) B_EXPECT((skb) && rtx_domain(sk, skb) == (d))
#else
#define C_DOMAIN(sk, skb, d) do { } while (0)
#endif

/*
 * n = K + 3 skbs: 0..2 are TREE, 3.. are LIST (hybrid). Step A splits skb 2
 * and SACKs its back piece, step B shifts two MSS of first(L) into it
 * (partial), step C shifts the rest of first(L) (full merge).
 */
static void c_shift_boundary(u32 base)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	struct sk_buff *t2, *l3, *back, *next;
	unsigned int n = c_k() + 3;
	struct c_count cnt;
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!c_fill(sk, n))
		goto out;
	t2 = rtx_find_cover(sk, c_seq(2));
	l3 = rtx_find_cover(sk, c_seq(3));
	C_DOMAIN(sk, t2, RTX_DOMAIN_TREE);
	C_DOMAIN(sk, l3, RTX_DOMAIN_LIST);
	c_check(sk, "boundary-0");

	c_count(&cnt);
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(2) + 1000, c_seq(3)) == 1);
	back = rtx_next(sk, t2);
	B_EXPECT(b_prepare_count == 1 && b_commit_count == 1);
	B_EXPECT(back && TCP_SKB_CB(back)->seq == c_seq(2) + 1000 &&
		 TCP_SKB_CB(back)->sacked == TCPCB_SACKED_ACKED);
	B_EXPECT(tp->sacked_out == 3 && tp->highest_sack == l3);
	c_expect_shift(&cnt, 0, 0);
	C_DOMAIN(sk, back, RTX_DOMAIN_TREE);
	C_DOMAIN(sk, l3, RTX_DOMAIN_LIST);
#ifdef CONFIG_TCP_RTX_HYBRID
	B_EXPECT(tp->rtx_tree_last == back);
#endif
	c_check(sk, "boundary-A-split");
	if (!back)
		goto out;

	c_count(&cnt);
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(2) + 1000, c_seq(3) + 2000) == 1);
	c_expect_shift(&cnt, 1, 0);
	B_EXPECT(b_prepare_count == 1);
	B_EXPECT(TCP_SKB_CB(back)->end_seq == c_seq(3) + 2000 &&
		 tcp_skb_pcount(back) == 5);
	B_EXPECT(rtx_next(sk, back) == l3 &&
		 TCP_SKB_CB(l3)->seq == c_seq(3) + 2000 &&
		 tcp_skb_pcount(l3) == 2 && !TCP_SKB_CB(l3)->sacked);
	B_EXPECT(tp->sacked_out == 5 && tp->highest_sack == l3);
	C_DOMAIN(sk, back, RTX_DOMAIN_TREE);
	C_DOMAIN(sk, l3, RTX_DOMAIN_LIST);
	c_check(sk, "boundary-B-partial");

	c_count(&cnt);
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(2) + 1000, c_seq(4)) == 1);
	c_expect_shift(&cnt, 0, 1);
	B_EXPECT(b_prepare_count == 1);
	next = rtx_next(sk, back);
	B_EXPECT(TCP_SKB_CB(back)->end_seq == c_seq(4) &&
		 tcp_skb_pcount(back) == 7 && tp->sacked_out == 7);
	B_EXPECT(next == (n > 4 ? rtx_find_cover(sk, c_seq(4)) : NULL));
	B_EXPECT(tp->highest_sack == next);
#ifdef CONFIG_TCP_RTX_HYBRID
	B_EXPECT(tp->rtx_tree_last == back);
	B_EXPECT(tp->rtx_recent_len == c_k() - 1);
	if (next)
		C_DOMAIN(sk, next, RTX_DOMAIN_LIST);
#endif
	c_check(sk, "boundary-C-merge");

	c_count(&cnt);
	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->packets_out && !tp->sacked_out);
	B_EXPECT(!sk->sk_wmem_queued &&
		 list_empty(&tp->tsorted_sent_queue));
	c_check(sk, "boundary-D-acked");
	pr_info("BVERIFY cshift boundary k=%u base=%u partial=1 merge=1\n",
		c_k(), base);
out:
	c_release(sock);
}

/*
 * prev = back piece of skb cur-1 (SACKed), cur = unSACKed, next = cur+1
 * (SACKed). One SACK filling cur makes tcp_shift_skb_data() merge cur and
 * then next into prev and return prev; the walk must continue at the skb
 * after next. When it exists, two more MSS of cur+2 are shifted (partial)
 * by the same SACK block.
 */
static void c_shift_pair(u32 base, unsigned int cur)
{
	struct socket *sock = c_socket(base, C_NOLOSS);
	unsigned int n = c_k() + 3, sacked;
	struct sk_buff *front, *back, *mid, *next, *after;
	bool extra = cur + 2 < n;
	u32 end = extra ? c_seq(cur + 2) + 2000 : c_seq(cur + 2);
	struct c_count cnt;
	struct tcp_sock *tp;
	struct sock *sk;
	char tag[48];

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	B_EXPECT(cur >= 1 && cur + 1 < n);
	if (!c_fill(sk, n) || cur < 1 || cur + 1 >= n)
		goto out;

	B_EXPECT(c_ack(sk, c_base, 1, c_seq(cur - 1) + 1000, c_seq(cur)) == 1);
	front = rtx_find_cover(sk, c_seq(cur - 1));
	back = front ? rtx_next(sk, front) : NULL;
	B_EXPECT(b_prepare_count == 1 && back &&
		 TCP_SKB_CB(back)->sacked == TCPCB_SACKED_ACKED);
	snprintf(tag, sizeof(tag), "pair%u-A-split", cur);
	c_check(sk, tag);

	c_count(&cnt);
	B_EXPECT(c_ack(sk, c_base, 2, c_seq(cur + 1), c_seq(cur + 2),
		       c_seq(cur - 1) + 1000, c_seq(cur)) == 1);
	mid = rtx_find_cover(sk, c_seq(cur));
	next = rtx_find_cover(sk, c_seq(cur + 1));
	B_EXPECT(mid && next && rtx_next(sk, mid) == next);
	B_EXPECT(next && TCP_SKB_CB(next)->sacked == TCPCB_SACKED_ACKED);
	B_EXPECT(mid && !TCP_SKB_CB(mid)->sacked);
	B_EXPECT(tp->sacked_out == 7 && b_prepare_count == 1);
	c_expect_shift(&cnt, 0, 0);
	snprintf(tag, sizeof(tag), "pair%u-B-tag-next", cur);
	c_check(sk, tag);
	if (!back || !mid || !next)
		goto out;
#ifdef CONFIG_TCP_RTX_HYBRID
	pr_info("CPAIR k%u cur=%u prev=%c cur=%c next=%c\n", c_k(), cur,
		rtx_domain(sk, back) == RTX_DOMAIN_TREE ? 'T' : 'L',
		rtx_domain(sk, mid) == RTX_DOMAIN_TREE ? 'T' : 'L',
		rtx_domain(sk, next) == RTX_DOMAIN_TREE ? 'T' : 'L');
	if (cur == 2) {
		B_EXPECT(tp->rtx_tree_last == mid);
		C_DOMAIN(sk, next, RTX_DOMAIN_LIST);
	}
#endif

	c_count(&cnt);
	B_EXPECT(c_ack(sk, c_base, 1, c_seq(cur - 1) + 1000, end) == 1);
	/* mid and next are freed now; only back and after may be touched */
	c_expect_shift(&cnt, extra, 2);
	sacked = 3 + 4 + 4 + (extra ? 2 : 0);
	after = rtx_next(sk, back);
	B_EXPECT(b_prepare_count == 1);
	B_EXPECT(TCP_SKB_CB(back)->end_seq == end &&
		 tcp_skb_pcount(back) == sacked);
	B_EXPECT(tp->sacked_out == sacked);
	B_EXPECT(after == (extra ? rtx_find_cover(sk, end) :
			   (cur + 2 < n ? rtx_find_cover(sk, c_seq(cur + 2)) :
			    NULL)));
	B_EXPECT(tp->highest_sack == after);
	if (after && extra)
		B_EXPECT(TCP_SKB_CB(after)->seq == end &&
			 tcp_skb_pcount(after) == 2 &&
			 !TCP_SKB_CB(after)->sacked);
	snprintf(tag, sizeof(tag), "pair%u-C-merge2", cur);
	c_check(sk, tag);

	B_EXPECT(c_ack(sk, tp->snd_nxt, 0) == 1);
	B_EXPECT(rtx_empty(sk) && !tp->packets_out && !tp->sacked_out &&
		 !sk->sk_wmem_queued);
	snprintf(tag, sizeof(tag), "pair%u-D-acked", cur);
	c_check(sk, tag);
	pr_info("BVERIFY cshift pair k=%u cur=%u base=%u merge=2 partial=%d\n",
		c_k(), cur, base, extra);
out:
	c_release(sock);
}

static void c_shift_all(void)
{
	unsigned int k = c_k(), cur;

	c_shift_boundary(1000);
	c_shift_boundary((u32)-6000);
	for (cur = 1; cur <= 4 && cur + 1 < k + 3; cur++)
		c_shift_pair(1000, cur);
	c_shift_pair((u32)-9000, 2);
}

static void b_case_cshift(void)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	c_shape = CONFIG_TCP_RTX_RECENT_MAX;
	c_shift_all();
#else
	/* RB reference runs both shapes so hybrid output can be diffed. */
	c_shape = 16;
	c_shift_all();
	c_shape = 1;
	c_shift_all();
#endif
}
