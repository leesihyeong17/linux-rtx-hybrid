// SPDX-License-Identifier: GPL-2.0
static void b_case_hybrid(void)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	const int k = CONFIG_TCP_RTX_RECENT_MAX;
	int position;

	/* The fixture validator supports at most 32 queued skbs. */
	if (k > 24) {
		B_EXPECT(false);
		return;
	}
	for (position = 0; position < 3; position++) {
		struct socket *sock = b_socket();
		struct sk_buff *skb, *first, *back;
		struct tcp_sock *tp;
		int i, ret, index = position == 0 ? 0 :
			(position == 1 ? k / 2 : k - 1);

		if (!sock)
			return;
		tp = tcp_sk(sock->sk);
		for (i = 0; i < k; i++) {
			skb = b_add(sock->sk, false, 1000 + i * 4000,
				    4000, i == k - 1);
			if (!skb) {
				b_release(sock);
				return;
			}
		}
		first = rtx_first(sock->sk);
		skb = first;
		for (i = 0; i < index; i++)
			skb = rtx_next(sock->sk, skb);
		B_EXPECT(tp->rtx_recent_len == k);
		B_EXPECT(rtx_domain(sock->sk, skb) == RTX_DOMAIN_LIST);
		ret = tcp_fragment(sock->sk, TCP_FRAG_IN_RTX_QUEUE, skb,
				   1500, 1000, GFP_KERNEL);
		B_EXPECT(!ret && b_prepare_count == 1 && b_commit_count == 1);
		if (ret) {
			b_release(sock);
			continue;
		}
		back = rtx_next(sock->sk, skb);
		B_EXPECT(rtx_domain(sock->sk, first) == RTX_DOMAIN_TREE);
		B_EXPECT(tp->rtx_tree_first == first &&
			 tp->rtx_tree_last == first);
		B_EXPECT(tp->rtx_recent_len == k &&
			 tp->packets_out == k * 4 + 1);
		B_EXPECT(skb->tcp_tsorted_anchor.next ==
			 &back->tcp_tsorted_anchor);
		B_EXPECT(rtx_validate_structure(sock->sk) && b_valid(sock->sk));
		b_payload(skb, back, 4000);
		pr_info("BVERIFY hybrid K=%d position=%d promote=1 split=1 accounting=ok\n",
			k, position);
		b_release(sock);
	}
#else
	B_EXPECT(true);
#endif
}
