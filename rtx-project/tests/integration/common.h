/* SPDX-License-Identifier: GPL-2.0 */
#include <linux/init.h>
#include <net/net_namespace.h>

void b_test_mark_head_lost(struct sock *sk);

static unsigned int b_checks, b_failures;
#define B_EXPECT(condition) do { \
	b_checks++; \
	if (!(condition)) { \
		b_failures++; \
		pr_err("BVERIFY FAIL %s:%d %s\n", \
		       __func__, __LINE__, #condition); \
	} \
} while (0)

static struct socket *b_socket(void)
{
	struct socket *sock;
	int err = sock_create_kern(&init_net, AF_INET, SOCK_STREAM,
				  IPPROTO_TCP, &sock);

	B_EXPECT(!err);
	if (err)
		return NULL;
	lock_sock(sock->sk);
	sock->sk->sk_sndbuf = 1024 * 1024;
	b_observe_sk = sock->sk;
	b_prepare_count = b_commit_count = b_fault_hits = b_fault = 0;
	return sock;
}

static struct sk_buff *b_add(struct sock *sk, bool writeq, u32 seq,
			    unsigned int len, bool fin)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct sk_buff *skb = sk_stream_alloc_skb(sk, len, GFP_KERNEL, true);
	unsigned int i;

	B_EXPECT(skb != NULL);
	if (!skb)
		return NULL;
	for (i = 0; i < len; i++)
		*(u8 *)skb_put(skb, 1) = (u8)(i * 31 + 7);
	TCP_SKB_CB(skb)->seq = seq;
	TCP_SKB_CB(skb)->end_seq = seq + len + fin;
	TCP_SKB_CB(skb)->tcp_flags = TCPHDR_ACK | TCPHDR_PSH |
		(fin ? TCPHDR_FIN : 0);
	tcp_set_skb_tso_segs(skb, 1000);
	__skb_header_release(skb);
	sk_wmem_queued_add(sk, skb->truesize);
	sk_mem_charge(sk, skb->truesize);
	if (writeq) {
		tcp_add_write_queue_tail(sk, skb);
	} else {
		rtx_append(sk, skb);
		skb->skb_mstamp_ns = 1000000;
		list_add_tail(&skb->tcp_tsorted_anchor,
			&tp->tsorted_sent_queue);
		tp->packets_out += tcp_skb_pcount(skb);
		tp->snd_nxt = TCP_SKB_CB(skb)->end_seq;
	}
	return skb;
}

/* Bounded scans avoid hanging a failing boot on a corrupted queue. */
static bool b_member(struct sk_buff **nodes, int n, struct sk_buff *skb)
{
	int i;

	if (!skb)
		return true;
	for (i = 0; i < n; i++)
		if (nodes[i] == skb)
			return true;
	return false;
}

static bool b_valid(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct sk_buff *nodes[32], *skb, *prev = NULL;
	struct list_head *link;
	unsigned int packets = 0, sacked = 0, lost = 0, retrans = 0;
	unsigned long memory = 0;
	int n = 0, times = 0, i;

	if (tp->rtx_txn_open)
		return false;
	for (skb = rtx_first(sk); skb; skb = rtx_next(sk, skb)) {
		u8 flags = TCP_SKB_CB(skb)->sacked;
		unsigned int pcount = tcp_skb_pcount(skb);

		if (n == ARRAY_SIZE(nodes) || rtx_prev(sk, skb) != prev ||
		    !before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(skb)->end_seq))
			return false;
		if (prev && after(TCP_SKB_CB(prev)->end_seq,
			TCP_SKB_CB(skb)->seq))
			return false;
		nodes[n++] = skb;
		prev = skb;
		packets += pcount;
		sacked += (flags & TCPCB_SACKED_ACKED) ? pcount : 0;
		lost += (flags & TCPCB_LOST) ? pcount : 0;
		retrans += (flags & TCPCB_SACKED_RETRANS) ? pcount : 0;
		memory += skb->truesize;
	}
	if (rtx_last(sk) != prev || packets != tp->packets_out ||
	    sacked != tp->sacked_out || lost != tp->lost_out ||
	    retrans != tp->retrans_out || memory != sk->sk_wmem_queued)
		return false;
	if (!b_member(nodes, n, tp->highest_sack) ||
	    !b_member(nodes, n, tp->lost_skb_hint) ||
	    !b_member(nodes, n, tp->retransmit_skb_hint))
		return false;
	for (link = tp->tsorted_sent_queue.next;
	     link != &tp->tsorted_sent_queue; link = link->next) {
		if (++times > n || link->next->prev != link ||
			link->prev->next != link)
			return false;
		skb = list_entry(link, struct sk_buff, tcp_tsorted_anchor);
		if (!b_member(nodes, n, skb))
			return false;
	}
	for (i = 0; i < n; i++) {
		bool found = false;
		u8 flags = TCP_SKB_CB(nodes[i])->sacked;

		for (link = tp->tsorted_sent_queue.next;
		     link != &tp->tsorted_sent_queue; link = link->next)
			if (link == &nodes[i]->tcp_tsorted_anchor)
				found = true;
		if (!found && (!(flags & TCPCB_LOST) ||
			(flags & TCPCB_SACKED_RETRANS)))
			return false;
	}
	return true;
}

static void b_release(struct socket *sock)
{
	struct sock *sk = sock->sk;
	struct tcp_sock *tp = tcp_sk(sk);

	b_fault = 0;
	tcp_write_queue_purge(sk);
	B_EXPECT(rtx_empty(sk) && tcp_write_queue_empty(sk));
	B_EXPECT(list_empty(&tp->tsorted_sent_queue));
	B_EXPECT(!tp->highest_sack && !tp->lost_skb_hint &&
		!tp->retransmit_skb_hint);
	B_EXPECT(!sk->sk_wmem_queued && !tp->packets_out);
	tp->sacked_out = tp->lost_out = tp->retrans_out = 0;
	b_observe_sk = NULL;
	release_sock(sk);
	sock_release(sock);
}

static void b_payload(struct sk_buff *front, struct sk_buff *back,
		      unsigned int total)
{
	unsigned int i;
	u8 value;
	bool ok = true;

	for (i = 0; i < total; i++) {
		struct sk_buff *skb = i < front->len ? front : back;
		unsigned int offset = i < front->len ? i : i - front->len;

		if (skb_copy_bits(skb, offset, &value, 1) ||
			value != (u8)(i * 31 + 7)) {
			ok = false;
			break;
		}
	}
	B_EXPECT(ok);
}
