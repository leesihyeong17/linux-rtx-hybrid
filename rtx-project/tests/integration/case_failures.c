// SPDX-License-Identifier: GPL-2.0
static void b_case_failures(void)
{
	int fault;

	for (fault = 1; fault <= 2; fault++) {
		struct socket *sock = b_socket();
		struct sock *sk;
		struct tcp_sock *tp;
		struct sk_buff *skb, *clone;
		unsigned long memory;
		int ret;

		if (!sock)
			return;
		sk = sock->sk;
		tp = tcp_sk(sk);
		skb = b_add(sk, false, 1000, 4000, true);
		if (!skb) {
			b_release(sock);
			return;
		}
		tcp_skb_tsorted_save(skb) {
			clone = skb_clone(skb, GFP_KERNEL);
		} tcp_skb_tsorted_restore(skb);
		B_EXPECT(clone != NULL);
		if (!clone) {
			b_release(sock);
			return;
		}
		B_EXPECT(skb_cloned(skb));
		tp->highest_sack = tp->lost_skb_hint = skb;
		tp->retransmit_skb_hint = skb;
		memory = sk->sk_wmem_queued;
		b_fault = fault;
		ret = tcp_fragment(sk, TCP_FRAG_IN_RTX_QUEUE, skb, 1500, 1000,
			GFP_KERNEL);
		B_EXPECT(ret == -ENOMEM);
		B_EXPECT(b_fault_hits == 1 && !b_prepare_count &&
			!b_commit_count);
		B_EXPECT(!tp->rtx_txn_open && rtx_first(sk) == skb &&
			rtx_last(sk) == skb);
		B_EXPECT(TCP_SKB_CB(skb)->seq == 1000 &&
			TCP_SKB_CB(skb)->end_seq == 5001);
		B_EXPECT(skb->len == 4000 && sk->sk_wmem_queued == memory);
		B_EXPECT(tp->packets_out == 4 && b_valid(sk));
		B_EXPECT(tp->highest_sack == skb &&
			tp->lost_skb_hint == skb &&
				tp->retransmit_skb_hint == skb);
		b_fault = 0;
		ret = tcp_fragment(sk, TCP_FRAG_IN_RTX_QUEUE, skb, 1500, 1000,
			GFP_KERNEL);
		B_EXPECT(!ret && b_prepare_count == 1 && b_commit_count == 1);
		B_EXPECT(b_valid(sk));
		b_payload(skb, rtx_next(sk, skb), 4000);
		/* The pre-existing clone still retains the original data. */
		B_EXPECT(clone->len == 4000);
		tcp_skb_tsorted_anchor_cleanup(clone);
		kfree_skb(clone);
		pr_info("BVERIFY failure-boundary=%s recovery=success\n",
			fault == 1 ? "unclone" : "allocation");
		b_release(sock);
	}
}
