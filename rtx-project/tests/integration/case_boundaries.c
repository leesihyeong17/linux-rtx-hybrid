// SPDX-License-Identifier: GPL-2.0
static void b_case_boundaries(void)
{
	static const u32 bases[] = { 1000, 0xffffff00U, 0xfffff060U };
	int b, position;

	for (b = 0; b < ARRAY_SIZE(bases); b++) {
		for (position = 0; position < 3; position++) {
			struct socket *sock = b_socket();
			struct sk_buff *nodes[3], *back;
			struct sock *sk;
			u32 old_end;
			int i, ret;

			if (!sock)
				return;
			sk = sock->sk;
			for (i = 0; i < 3; i++) {
				nodes[i] = b_add(sk, false,
					bases[b] + i * 4000, 4000, i == 2);
				if (!nodes[i]) {
					b_release(sock);
					return;
				}
			}
			old_end = TCP_SKB_CB(nodes[position])->end_seq;
			ret = tcp_fragment(sk, TCP_FRAG_IN_RTX_QUEUE,
				nodes[position], 1500, 1000, GFP_KERNEL);
			B_EXPECT(!ret && b_prepare_count == 1 &&
				b_commit_count == 1);
			if (ret) {
				b_release(sock);
				continue;
			}
			back = rtx_next(sk, nodes[position]);
			B_EXPECT(back != NULL);
			if (!back) {
				b_release(sock);
				continue;
			}
			B_EXPECT(TCP_SKB_CB(nodes[position])->end_seq ==
				 TCP_SKB_CB(back)->seq);
			B_EXPECT(TCP_SKB_CB(back)->end_seq == old_end);
			B_EXPECT(!(TCP_SKB_CB(nodes[position])->tcp_flags &
				   (TCPHDR_FIN | TCPHDR_PSH)));
			B_EXPECT(!!(TCP_SKB_CB(back)->tcp_flags & TCPHDR_FIN) ==
				 (position == 2));
			B_EXPECT(TCP_SKB_CB(back)->tcp_flags & TCPHDR_PSH);
			B_EXPECT(tcp_skb_pcount(nodes[position]) == 2 &&
				tcp_skb_pcount(back) == 3);
			B_EXPECT(tcp_sk(sk)->packets_out == 13 && b_valid(sk));
			b_payload(nodes[position], back, 4000);
			pr_info("BVERIFY boundary base=%08x position=%d FIN=%d GSO=2+3\n",
				bases[b], position, position == 2);
			b_release(sock);
		}
	}
	{
		struct socket *sock = b_socket();
		struct sk_buff *skb, *tail;
		int ret;

		if (!sock)
			return;
		skb = b_add(sock->sk, false, 0xffffff00U, 4000, true);
		if (!skb) {
			b_release(sock);
			return;
		}
		ret = tcp_fragment(sock->sk, TCP_FRAG_IN_RTX_QUEUE, skb, 1000,
			1000, GFP_KERNEL);
		B_EXPECT(!ret);
		tail = rtx_last(sock->sk);
		ret = tcp_fragment(sock->sk, TCP_FRAG_IN_RTX_QUEUE, tail,
			1000, 1000, GFP_KERNEL);
		B_EXPECT(!ret && b_prepare_count == 2 && b_commit_count == 2);
		B_EXPECT(tcp_sk(sock->sk)->packets_out == 4 &&
			b_valid(sock->sk));
		B_EXPECT(TCP_SKB_CB(rtx_last(sock->sk))->tcp_flags &
			 TCPHDR_FIN);
		pr_info("BVERIFY single-resplit wrap=1 FIN=1\n");
		b_release(sock);
	}
}
