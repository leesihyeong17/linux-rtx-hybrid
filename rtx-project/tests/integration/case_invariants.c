// SPDX-License-Identifier: GPL-2.0
static void b_case_invariants(void)
{
	struct socket *sock = b_socket();
	struct tcp_sock *tp;
	struct sk_buff *skb, *back;
	struct sk_buff dummy = {};
	bool pending;
	int ret;

	if (!sock)
		return;
	tp = tcp_sk(sock->sk);
	skb = b_add(sock->sk, false, 1000, 4000, true);
	if (!skb) {
		b_release(sock);
		return;
	}
	tp->highest_sack = tp->lost_skb_hint = tp->retransmit_skb_hint = skb;
	/* Adjust packet counts of a SACKed/retransmitted skb. */
	TCP_SKB_CB(skb)->sacked = TCPCB_SACKED_ACKED | TCPCB_SACKED_RETRANS;
	tp->sacked_out = tp->retrans_out = 4;
	ret = tcp_fragment(sock->sk, TCP_FRAG_IN_RTX_QUEUE, skb, 1500, 1000,
		GFP_KERNEL);
	B_EXPECT(!ret && b_valid(sock->sk));
	B_EXPECT(tp->packets_out == 5 && tp->sacked_out == 5 &&
		tp->retrans_out == 5);
	B_EXPECT(tp->highest_sack == skb && tp->lost_skb_hint == skb &&
		tp->retransmit_skb_hint == skb);
	back = rtx_next(sock->sk, skb);
	B_EXPECT(skb->tcp_tsorted_anchor.next == &back->tcp_tsorted_anchor);
	B_EXPECT(back->tcp_tsorted_anchor.prev == &skb->tcp_tsorted_anchor);
	/* Negative controls must detect independently introduced damage. */
	tp->packets_out++;
	B_EXPECT(!b_valid(sock->sk));
	tp->packets_out--;
	tp->lost_skb_hint = &dummy;
	B_EXPECT(!b_valid(sock->sk));
	tp->lost_skb_hint = skb;
	list_del_init(&back->tcp_tsorted_anchor);
	B_EXPECT(!b_valid(sock->sk));
	list_add(&back->tcp_tsorted_anchor, &skb->tcp_tsorted_anchor);
	B_EXPECT(b_valid(sock->sk));
	pr_info("BVERIFY invariants counters=5/5/5 hints=live tsorted=adjacent negative-controls=3\n");
	b_release(sock);

	/* RACK consumes the post-split timestamp queue without splitting. */
	sock = b_socket();
	if (!sock)
		return;
	tp = tcp_sk(sock->sk);
	skb = b_add(sock->sk, false, 1000, 4000, false);
	if (!skb) {
		b_release(sock);
		return;
	}
	ret = tcp_fragment(sock->sk, TCP_FRAG_IN_RTX_QUEUE, skb, 1500, 1000,
		GFP_KERNEL);
	B_EXPECT(!ret && b_valid(sock->sk));
	tp->tcp_mstamp = 10000;
	tp->rack.mstamp = 5000;
	tp->rack.end_seq = 5001;
	tp->rack.rtt_us = 100;
	tp->rack.advanced = 1;
	inet_csk(sock->sk)->icsk_ca_state = TCP_CA_Recovery;
	pending = tcp_rack_mark_lost(sock->sk);
	B_EXPECT(!pending && !tp->rack.advanced);
	B_EXPECT(tp->lost_out == 5 && tp->packets_out == 5);
	B_EXPECT(list_empty(&tp->tsorted_sent_queue) && b_valid(sock->sk));
	B_EXPECT(b_prepare_count == 1 && b_commit_count == 1);
	pr_info("BVERIFY RACK post-split lost_out=5 tsorted=empty extra-split=0\n");
	b_release(sock);
}
