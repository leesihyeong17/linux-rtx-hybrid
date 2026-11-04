/* SPDX-License-Identifier: GPL-2.0 */
/*
 * TCP retransmit queue adapter (rtx_*)
 *
 * All structural access to the retransmit queue should go through these
 * helpers, so that the backend (plain rbtree today, hybrid tree + list
 * later) can be changed without touching the callers.
 *
 * Contract (design doc section 7):
 *  - Locking: the caller already holds the socket serialization of its
 *    path. These helpers never sleep and never take locks.
 *  - Lookups return a borrowed skb pointer or NULL. No reference is taken.
 *  - rtx_unlink() only removes the skb from the queue structure. It does
 *    NOT free it, does NOT touch TCP accounting and does NOT clean the
 *    tsorted anchor. tcp_rtx_queue_unlink() keeps doing that part.
 *  - rtx_next()/rtx_prev() require an skb that is currently in the queue.
 *  - Insert helpers require an skb that is not linked anywhere.
 *
 * Backend: RB (V1). Thin wrappers around the existing rbtree code, so the
 * generated code should be identical to the unmodified kernel.
 *
 * This file is included from include/net/tcp.h. Do not include it directly.
 */
#ifndef _NET_TCP_RTX_H
#define _NET_TCP_RTX_H

#ifndef _TCP_H
#error "include <net/tcp.h> instead of <net/tcp_rtx.h>"
#endif

void tcp_rbtree_insert(struct rb_root *root, struct sk_buff *skb);

static inline void rtx_init(struct sock *sk)
{
	sk->tcp_rtx_queue = RB_ROOT;
}

static inline bool rtx_empty(const struct sock *sk)
{
	return RB_EMPTY_ROOT(&sk->tcp_rtx_queue);
}

/* Lowest sequence skb, or NULL if the queue is empty. */
static inline struct sk_buff *rtx_first(const struct sock *sk)
{
	return skb_rb_first(&sk->tcp_rtx_queue);
}

/* Highest sequence skb, or NULL if the queue is empty. */
static inline struct sk_buff *rtx_last(const struct sock *sk)
{
	return skb_rb_last(&sk->tcp_rtx_queue);
}

static inline struct sk_buff *rtx_next(const struct sock *sk,
				       const struct sk_buff *skb)
{
	return skb_rb_next(skb);
}

static inline struct sk_buff *rtx_prev(const struct sock *sk,
				       const struct sk_buff *skb)
{
	return skb_rb_prev(skb);
}

/* Return the skb with seq <= @seq < end_seq, or NULL if none covers it.
 * Same semantics as the original tcp_sacktag_bsearch().
 */
static inline struct sk_buff *rtx_find_cover(struct sock *sk, u32 seq)
{
	struct rb_node *parent, **p = &sk->tcp_rtx_queue.rb_node;
	struct sk_buff *skb;

	while (*p) {
		parent = *p;
		skb = rb_to_skb(parent);
		if (before(seq, TCP_SKB_CB(skb)->seq)) {
			p = &parent->rb_left;
			continue;
		}
		if (!before(seq, TCP_SKB_CB(skb)->end_seq)) {
			p = &parent->rb_right;
			continue;
		}
		return skb;
	}
	return NULL;
}

/* Insert an skb at its sequence position. */
static inline void rtx_insert_ordered(struct sock *sk, struct sk_buff *skb)
{
	tcp_rbtree_insert(&sk->tcp_rtx_queue, skb);
}

/* Insert an skb that is known to come after the current tail.
 * RB backend: same as an ordered insert. V3 will link it directly.
 */
static inline void rtx_append(struct sock *sk, struct sk_buff *skb)
{
	tcp_rbtree_insert(&sk->tcp_rtx_queue, skb);
}

/* Remove an skb from the queue structure only (see contract above). */
static inline void rtx_unlink(struct sock *sk, struct sk_buff *skb)
{
	rb_erase(&skb->rbnode, &sk->tcp_rtx_queue);
}

#endif /* _NET_TCP_RTX_H */
