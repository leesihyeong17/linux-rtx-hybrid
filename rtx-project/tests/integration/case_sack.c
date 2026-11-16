// SPDX-License-Identifier: GPL-2.0

static bool b_sack_fill(struct sock *sk, unsigned int count, u32 base,
			unsigned int gap)
{
	unsigned int i;

	for (i = 0; i < count; i++)
		if (!b_add(sk, false, base + i * 4000 + (i ? gap : 0),
			   4000, i == count - 1))
			return false;
	tcp_sk(sk)->snd_una = base;
	return true;
}

static void b_sack_payload(struct sock *sk, struct sk_buff *skb, u32 base)
{
	unsigned int i, offset, visited = 0;
	u8 value;
	bool ok = true;

	while (skb && before(TCP_SKB_CB(skb)->seq, base + 4000)) {
		if (++visited > 3) {
			ok = false;
			break;
		}
		offset = TCP_SKB_CB(skb)->seq - base;
		for (i = 0; i < skb->len; i++) {
			if (skb_copy_bits(skb, i, &value, 1) ||
			    value != (u8)((offset + i) * 31 + 7)) {
				ok = false;
				break;
			}
		}
		skb = rtx_next(sk, skb);
	}
	B_EXPECT(ok && visited == 3);
}

static void b_sack_split(unsigned int count, unsigned int index, u32 base,
			 bool unaligned, bool fail_second)
{
	struct socket *sock = b_socket();
	struct sk_buff *original, *middle, *back, *cursor;
	struct tcp_sock *tp;
	struct sock *sk;
	u32 seq = base + index * 4000;
	u32 start = seq + (unaligned ? 1500 : 1000);
	u32 end = seq + (unaligned ? 3500 : 3000);
	u32 rounded_start = seq + (unaligned ? 2000 : 1000);
	unsigned int sacked = unaligned ? 1 : 2;
	unsigned long memory;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!b_sack_fill(sk, count, base, 0))
		goto out;
	original = rtx_find_cover(sk, seq);
	B_EXPECT(original && TCP_SKB_CB(original)->seq == seq);
	if (!original)
		goto out;
	tp->lost_skb_hint = tp->retransmit_skb_hint = original;
	b_fail_alloc_at = fail_second ? 2 : 0;
	cursor = b_test_sack_walk(sk, start, end);
	if (fail_second) {
		middle = rtx_next(sk, original);
		B_EXPECT(middle && cursor == middle);
		B_EXPECT(b_alloc_count == 2 && b_fault_hits == 1);
		B_EXPECT(b_prepare_count == 1 && b_commit_count == 1);
		B_EXPECT(TCP_SKB_CB(original)->end_seq == rounded_start);
		if (!middle)
			goto out;
		B_EXPECT(TCP_SKB_CB(middle)->seq == rounded_start);
		B_EXPECT(TCP_SKB_CB(middle)->end_seq == seq + 4000 +
			 (index == count - 1));
		B_EXPECT(!tp->sacked_out && tp->packets_out == count * 4);
		B_EXPECT(!tp->rtx_txn_open && b_valid(sk));
		B_EXPECT(rtx_validate_structure(sk));
		memory = sk->sk_wmem_queued;
		b_fail_alloc_at = 0;
		cursor = b_test_sack_walk(sk, start, end);
		B_EXPECT(sk->sk_wmem_queued > memory);
	}
	B_EXPECT(b_prepare_count == 2 && b_commit_count == 2);
	B_EXPECT(b_alloc_count == 2 + fail_second);
	B_EXPECT(b_fault_hits == fail_second);
	middle = rtx_next(sk, original);
	back = middle ? rtx_next(sk, middle) : NULL;
	B_EXPECT(middle && back);
	if (!middle || !back)
		goto out;
	B_EXPECT(tp->highest_sack == back);
	B_EXPECT(cursor == (unaligned ? rtx_next(sk, back) : back));
	B_EXPECT(TCP_SKB_CB(original)->end_seq == rounded_start);
	B_EXPECT(TCP_SKB_CB(middle)->seq == rounded_start &&
		 TCP_SKB_CB(middle)->end_seq == seq + 3000);
	B_EXPECT(TCP_SKB_CB(back)->seq == seq + 3000 &&
		 TCP_SKB_CB(back)->end_seq == seq + 4000 +
		 (index == count - 1));
	B_EXPECT(!(TCP_SKB_CB(original)->sacked & TCPCB_SACKED_ACKED));
	B_EXPECT(TCP_SKB_CB(middle)->sacked == TCPCB_SACKED_ACKED);
	B_EXPECT(!(TCP_SKB_CB(back)->sacked & TCPCB_SACKED_ACKED));
	B_EXPECT(!(TCP_SKB_CB(original)->tcp_flags &
		   (TCPHDR_PSH | TCPHDR_FIN)));
	B_EXPECT(!(TCP_SKB_CB(middle)->tcp_flags & (TCPHDR_PSH | TCPHDR_FIN)));
	B_EXPECT(TCP_SKB_CB(back)->tcp_flags & TCPHDR_PSH);
	B_EXPECT(!!(TCP_SKB_CB(back)->tcp_flags & TCPHDR_FIN) ==
		 (index == count - 1));
	B_EXPECT(tp->packets_out == count * 4 && tp->sacked_out == sacked);
	B_EXPECT(tp->delivered == sacked);
	B_EXPECT(list_empty(&middle->tcp_tsorted_anchor));
	B_EXPECT(tp->lost_skb_hint == original &&
		 tp->retransmit_skb_hint == original);
	B_EXPECT(b_valid(sk) && rtx_validate_structure(sk));
#ifdef CONFIG_TCP_RTX_HYBRID
	B_EXPECT(tp->rtx_recent_len == min_t(unsigned int, count + 2,
					   CONFIG_TCP_RTX_RECENT_MAX));
	B_EXPECT(rtx_domain(sk, original) ==
		 (index + CONFIG_TCP_RTX_RECENT_MAX < count + 2 ?
		  RTX_DOMAIN_TREE : RTX_DOMAIN_LIST));
#endif
	b_sack_payload(sk, original, seq);
	memory = sk->sk_wmem_queued;
	B_EXPECT(b_test_sack_walk(sk, start, end) ==
		 (unaligned ? rtx_next(sk, back) : back));
	B_EXPECT(b_prepare_count == 2 && b_commit_count == 2);
	B_EXPECT(tp->sacked_out == sacked && tp->delivered == sacked);
	B_EXPECT(sk->sk_wmem_queued == memory && b_valid(sk));
	B_EXPECT(!b_test_sack_walk(sk, base, tp->snd_nxt));
	B_EXPECT(!tp->highest_sack && tp->sacked_out == tp->packets_out);
	B_EXPECT(list_empty(&tp->tsorted_sent_queue) && b_valid(sk));
	B_EXPECT(b_prepare_count == 2 && b_commit_count == 2);
	pr_info("BVERIFY sack count=%u index=%u base=%u unaligned=%d second-failure=%d splits=2 retry=%d highest=end\n",
		count, index, base, unaligned, fail_second, fail_second);
out:
	b_release(sock);
}

static void b_sack_cross(unsigned int k, unsigned int gap)
{
	struct socket *sock = b_socket();
	struct sk_buff *first, *second, *cursor;
	struct tcp_sock *tp;
	struct sock *sk;
	u32 base = 1000;

	if (!sock)
		return;
	sk = sock->sk;
	tp = tcp_sk(sk);
	if (!b_sack_fill(sk, k + 1, base, gap))
		goto out;
	first = rtx_first(sk);
	second = rtx_next(sk, first);
#ifdef CONFIG_TCP_RTX_HYBRID
	B_EXPECT(rtx_domain(sk, first) == RTX_DOMAIN_TREE);
	B_EXPECT(rtx_domain(sk, second) == RTX_DOMAIN_LIST);
#endif
	if (gap) {
		B_EXPECT(!rtx_find_cover(sk, base + 4000));
		B_EXPECT(!rtx_find_cover(sk, base + 4000 + gap - 1));
	}
	cursor = b_test_sack_walk(sk, base + 1000, base + 6000 + gap);
	B_EXPECT(cursor && TCP_SKB_CB(cursor)->seq == base + 6000 + gap);
	B_EXPECT(tp->highest_sack == cursor && tp->sacked_out == 5);
	B_EXPECT(tp->packets_out == 4 * (k + 1) && tp->delivered == 5);
	B_EXPECT(b_prepare_count == 2 && b_commit_count == 2);
	B_EXPECT(b_valid(sk) && rtx_validate_structure(sk));
	B_EXPECT(TCP_SKB_CB(first)->sacked == 0);
	B_EXPECT(TCP_SKB_CB(rtx_next(sk, first))->sacked == TCPCB_SACKED_ACKED);
	B_EXPECT(TCP_SKB_CB(second)->sacked == TCPCB_SACKED_ACKED);
	B_EXPECT(rtx_find_cover(sk, base + 4000 + gap) == second);
	pr_info("BVERIFY sack cross-boundary K=%u gap=%u splits=2 sacked=5\n",
		k, gap);
out:
	b_release(sock);
}

static void b_case_sack(void)
{
	unsigned int k = 16;

#ifdef CONFIG_TCP_RTX_HYBRID
	k = CONFIG_TCP_RTX_RECENT_MAX;
#endif
	B_EXPECT(k <= 24);
	if (k > 24)
		return;
	b_sack_split(1, 0, 1000, false, false);
	b_sack_split(k, 0, 1000, false, false);
	b_sack_split(k, k / 2, 1000, false, false);
	b_sack_split(k, k - 1, 1000, false, false);
	b_sack_split(k, 0, 1000, false, true);
	b_sack_split(k, k - 1, 1000, true, false);
	b_sack_split(k, 0, (u32)-2000, false, false);
	b_sack_cross(k, 0);
	b_sack_cross(k, 4000);
}
