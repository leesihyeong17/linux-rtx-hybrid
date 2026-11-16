// SPDX-License-Identifier: GPL-2.0

static void b_case_tsorted(void)
{
	static const struct {
		u8 flags;
		bool absent_ok;
	} cases[] = {
		{ 0, false },
		{ TCPCB_LOST, true },
		{ TCPCB_SACKED_RETRANS, false },
		{ TCPCB_LOST | TCPCB_SACKED_RETRANS, false },
		{ TCPCB_SACKED_ACKED, true },
		{ TCPCB_SACKED_ACKED | TCPCB_SACKED_RETRANS, true },
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(cases); i++) {
		struct socket *sock = b_socket();
		struct tcp_sock *tp;
		struct sk_buff *skb;
		u8 flags = cases[i].flags;

		if (!sock)
			return;
		tp = tcp_sk(sock->sk);
		skb = b_add(sock->sk, false, 1000, 1000, false);
		if (!skb) {
			b_release(sock);
			return;
		}
		TCP_SKB_CB(skb)->sacked = flags;
		tp->sacked_out = !!(flags & TCPCB_SACKED_ACKED);
		tp->lost_out = !!(flags & TCPCB_LOST);
		tp->retrans_out = !!(flags & TCPCB_SACKED_RETRANS);
		B_EXPECT(b_valid(sock->sk));
		list_del_init(&skb->tcp_tsorted_anchor);
		B_EXPECT(b_valid(sock->sk) == cases[i].absent_ok);
		list_add_tail(&skb->tcp_tsorted_anchor,
			      &tp->tsorted_sent_queue);
		B_EXPECT(b_valid(sock->sk));
		pr_info("BVERIFY tsorted flags=%u absent-valid=%d\n", flags,
			cases[i].absent_ok);
		b_release(sock);
	}
}
