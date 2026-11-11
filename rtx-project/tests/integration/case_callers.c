// SPDX-License-Identifier: GPL-2.0

static void b_case_callers(void)
{
	int which;

	/* Both tcp_fragment(WRITE) and tso_fragment's paged-data fast path. */
	for (which = 0; which < 2; which++) {
		struct socket *sock = b_socket();
		struct sk_buff *skb, *back;
		struct sock *sk;
		int ret;

		if (!sock)
			return;
		sk = sock->sk;
		skb = b_add(sk, true, 1000, which ? 0 : 4000, true);
		if (!skb) {
			b_release(sock);
			return;
		}
		if (which) {
			struct page *page = alloc_page(GFP_KERNEL);
			unsigned int i;

			B_EXPECT(page != NULL);
			if (!page) {
				b_release(sock);
				return;
			}
			for (i = 0; i < 4000; i++)
				((u8 *)page_address(page))[i] =
					(u8)(i * 31 + 7);
			skb_fill_page_desc(skb, 0, page, 0, 4000);
			skb->len = skb->data_len = 4000;
			skb->truesize += 4000;
			sk_wmem_queued_add(sk, 4000);
			sk_mem_charge(sk, 4000);
			TCP_SKB_CB(skb)->end_seq = 5001;
			tcp_set_skb_tso_segs(skb, 1000);
		}
		tcp_sk(sk)->snd_nxt = 1000;
		ret = which ? tso_fragment(sk, skb, 1500, 1000, GFP_KERNEL) :
			tcp_fragment(sk, TCP_FRAG_IN_WRITE_QUEUE, skb, 1500,
				1000, GFP_KERNEL);
		B_EXPECT(!ret && !b_prepare_count && !b_commit_count);
		B_EXPECT(rtx_empty(sk) &&
			skb_queue_len(&sk->sk_write_queue) == 2);
		B_EXPECT(list_empty(&tcp_sk(sk)->tsorted_sent_queue));
		B_EXPECT(!tcp_sk(sk)->packets_out && !tcp_sk(sk)->rtx_txn_open);
		back = skb_peek_tail(&sk->sk_write_queue);
		B_EXPECT(back != skb && TCP_SKB_CB(back)->end_seq == 5001);
		B_EXPECT(!(TCP_SKB_CB(skb)->tcp_flags & TCPHDR_FIN));
		B_EXPECT(TCP_SKB_CB(back)->tcp_flags & TCPHDR_FIN);
		B_EXPECT(sk->sk_wmem_queued == skb->truesize + back->truesize);
		b_payload(skb, back, 4000);
		pr_info("BVERIFY write-queue caller=%s transaction=0\n",
			which ? "tso_fragment" : "tcp_fragment");
		b_release(sock);
	}
	for (which = 0; which < 2; which++) {
		struct socket *sock = b_socket();
		struct tcp_sock *tp;
		struct sk_buff *skb;

		if (!sock)
			return;
		tp = tcp_sk(sock->sk);
		skb = b_add(sock->sk, false, 1000, 4000, false);
		if (!skb) {
			b_release(sock);
			return;
		}
		tp->snd_una = 1000;
		tp->high_seq = 5000;
		tp->rx_opt.sack_ok = 0;
		inet_csk(sock->sk)->icsk_ca_state = TCP_CA_Recovery;
		if (which)
			tcp_newreno_mark_lost(sock->sk, true);
		else
			b_test_mark_head_lost(sock->sk);
		B_EXPECT(b_prepare_count == 1 && b_commit_count == 1);
		B_EXPECT(skb->len == 1000 && tp->lost_out == 1 &&
			tp->packets_out == 4);
		B_EXPECT(b_valid(sock->sk));
		pr_info("BVERIFY caller=%s split=1 lost_out=1\n",
			which ? "tcp_newreno_mark_lost" : "tcp_mark_head_lost");
		b_release(sock);
	}
}
