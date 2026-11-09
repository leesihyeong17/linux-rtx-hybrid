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
 *  - Split: see the rtx_split_* section below.
 *
 * Backend: RB (V1). Thin wrappers around the existing rbtree code, so the
 * generated code should be identical to the unmodified kernel.
 *
 * Helpers starting with __rtx_ are backend internals. Callers outside this
 * file must not use them.
 *
 * This file is included from include/net/tcp.h. Do not include it directly.
 */
#ifndef _NET_TCP_RTX_H
#define _NET_TCP_RTX_H

#ifndef _TCP_H
#error "include <net/tcp.h> instead of <net/tcp_rtx.h>"
#endif

void tcp_rbtree_insert(struct rb_root *root, struct sk_buff *skb);

/* Which part of the queue an skb lives in. The RB backend only has TREE. */
enum rtx_domain {
	RTX_DOMAIN_TREE = 0,
	RTX_DOMAIN_LIST = 1,
};

/* CONFIG_TCP_RTX_DEBUG: public rtx_* calls are not allowed while a split
 * transaction is open (between rtx_split_prepare() and rtx_split_commit()).
 * A violation is reported once with WARN_ON_ONCE(); it is detection only,
 * nothing is skipped or rolled back.
 */
#ifdef CONFIG_TCP_RTX_DEBUG
static inline void rtx_assert_no_txn(const struct sock *sk)
{
	WARN_ON_ONCE(tcp_sk(sk)->rtx_txn_open);
}
#else
static inline void rtx_assert_no_txn(const struct sock *sk)
{
}
#endif

/* ---- backend internals (no transaction check) ---- */

static inline struct sk_buff *__rtx_find_cover(struct sock *sk, u32 seq)
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

/* Ordered insert into the tree part only. Never re-decides the domain. */
static inline void __rtx_tree_insert(struct sock *sk, struct sk_buff *skb)
{
	tcp_rbtree_insert(&sk->tcp_rtx_queue, skb);
}

/* ---- public API ---- */

static inline void rtx_init(struct sock *sk)
{
	sk->tcp_rtx_queue = RB_ROOT;
}

static inline bool rtx_empty(const struct sock *sk)
{
	rtx_assert_no_txn(sk);
	return RB_EMPTY_ROOT(&sk->tcp_rtx_queue);
}

/* Lowest sequence skb, or NULL if the queue is empty. */
static inline struct sk_buff *rtx_first(const struct sock *sk)
{
	rtx_assert_no_txn(sk);
	return skb_rb_first(&sk->tcp_rtx_queue);
}

/* Highest sequence skb, or NULL if the queue is empty. */
static inline struct sk_buff *rtx_last(const struct sock *sk)
{
	rtx_assert_no_txn(sk);
	return skb_rb_last(&sk->tcp_rtx_queue);
}

static inline struct sk_buff *rtx_next(const struct sock *sk,
				       const struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
	return skb_rb_next(skb);
}

static inline struct sk_buff *rtx_prev(const struct sock *sk,
				       const struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
	return skb_rb_prev(skb);
}

/* In-order iteration. Replacements for skb_rbtree_walk*() on the rtx queue.
 * Only the _safe variant allows unlinking the current skb inside the loop.
 * It does NOT protect against removing the next skb as well (SACK shift
 * can do that): such callers must keep using the cursor they get back.
 */
#define rtx_walk(skb, sk)						\
	for (skb = rtx_first(sk); skb; skb = rtx_next(sk, skb))

#define rtx_walk_from(skb, sk)						\
	for (; skb; skb = rtx_next(sk, skb))

#define rtx_walk_from_safe(skb, tmp, sk)				\
	for (; tmp = skb ? rtx_next(sk, skb) : NULL, (skb != NULL);	\
	     skb = tmp)

/* Return the skb with seq <= @seq < end_seq, or NULL if none covers it.
 * Same semantics as the original tcp_sacktag_bsearch().
 */
static inline struct sk_buff *rtx_find_cover(struct sock *sk, u32 seq)
{
	rtx_assert_no_txn(sk);
	return __rtx_find_cover(sk, seq);
}

/* Insert an skb at its sequence position. */
static inline void rtx_insert_ordered(struct sock *sk, struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
	tcp_rbtree_insert(&sk->tcp_rtx_queue, skb);
}

/* Insert an skb that is known to come after the current tail.
 * RB backend: same as an ordered insert. V3 will link it directly.
 */
static inline void rtx_append(struct sock *sk, struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
	tcp_rbtree_insert(&sk->tcp_rtx_queue, skb);
}

/* Remove an skb from the queue structure only (see contract above). */
static inline void rtx_unlink(struct sock *sk, struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
	rb_erase(&skb->rbnode, &sk->tcp_rtx_queue);
}

/*
 * ---- split transaction (design doc 3.3 and 5.5) ----
 *
 * tcp_fragment() turns skb [a,c) into skb [a,b) + buff [b,c).
 *
 * rtx_split_prepare(sk, skb, tok)
 *   - Only for an skb that is in the rtx queue (TCP_FRAG_IN_RTX_QUEUE).
 *   - Call after the last point that can fail, right before seq/end_seq
 *     are changed. It cannot fail and allocates nothing, so no abort API
 *     exists. If a failing step is ever added after prepare, revisit this.
 *   - Records skb and the domain it lives in.
 *
 * Between prepare and commit
 *   - No public rtx_* call, no rtx_walk*, and no wrapper built on them
 *     (tcp_rtx_queue_head/tail/empty/unlink/unlink_and_free, ...).
 *     CONFIG_TCP_RTX_DEBUG reports a violation with WARN_ON_ONCE().
 *
 * rtx_split_commit(sk, tok, buff)
 *   - Call where buff is linked into the queue.
 *   - Preconditions: skb seq unchanged, skb end_seq == buff seq,
 *     buff end_seq == the old skb end_seq, buff not in the rtx queue.
 *     "Not in the queue" is checked by sequence lookup in debug builds,
 *     never by looking at the shared list/rbnode link fields.
 *   - Links buff right after skb in the recorded domain. TREE uses a
 *     tree-only ordered insert (no tail shortcut for a split in the
 *     middle). LIST links after skb, then restores the length limit,
 *     which may promote skb itself.
 *   - Always links buff, even if a debug check fails.
 *   - The token is dead afterwards. Do not assume where skb lives.
 *   - Only the queue structure is handled here. The tsorted anchor,
 *     pcount/wmem accounting and hints stay in tcp_fragment(), so a full
 *     consistency check belongs after tcp_fragment() is done with them.
 */
struct rtx_split_token {
	struct sk_buff *skb;
	u8 domain;		/* enum rtx_domain at prepare time */
#ifdef CONFIG_TCP_RTX_DEBUG
	u32 seq;		/* skb seq/end_seq at prepare time */
	u32 end_seq;
#endif
};

static inline void rtx_split_prepare(struct sock *sk, struct sk_buff *skb,
				     struct rtx_split_token *tok)
{
	rtx_assert_no_txn(sk);
	tok->skb = skb;
	tok->domain = RTX_DOMAIN_TREE;	/* RB backend: tree only */
#ifdef CONFIG_TCP_RTX_DEBUG
	tok->seq = TCP_SKB_CB(skb)->seq;
	tok->end_seq = TCP_SKB_CB(skb)->end_seq;
	tcp_sk(sk)->rtx_txn_open = 1;
#endif
}

static inline void rtx_split_commit(struct sock *sk,
				    struct rtx_split_token *tok,
				    struct sk_buff *buff)
{
#ifdef CONFIG_TCP_RTX_DEBUG
	const struct tcp_skb_cb *scb = TCP_SKB_CB(tok->skb);
	const struct tcp_skb_cb *bcb = TCP_SKB_CB(buff);

	WARN_ON_ONCE(!tcp_sk(sk)->rtx_txn_open);
	WARN_ON_ONCE(scb->seq != tok->seq ||
		     scb->end_seq != bcb->seq ||
		     bcb->end_seq != tok->end_seq);
	/* Nothing may cover buff's start yet: otherwise buff (or an
	 * overlapping skb) is already in the queue.
	 */
	WARN_ON_ONCE(__rtx_find_cover(sk, bcb->seq));
	tcp_sk(sk)->rtx_txn_open = 0;
#endif
	/* RB backend: tok->domain is always RTX_DOMAIN_TREE. */
	__rtx_tree_insert(sk, buff);
	tok->skb = NULL;
}

#endif /* _NET_TCP_RTX_H */
