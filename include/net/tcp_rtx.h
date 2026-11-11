/* SPDX-License-Identifier: GPL-2.0 */
/*
 * TCP retransmit queue adapter (rtx_*)
 *
 * All structural access to the retransmit queue should go through these
 * helpers, with either the baseline rbtree or a bounded list suffix.
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
 *  - Measurement counters: see the CONFIG_TCP_RTX_STATS section below.
 *
 * Backend: RB by default; CONFIG_TCP_RTX_HYBRID enables the list suffix.
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

#include <linux/log2.h>
#include <linux/percpu.h>

void tcp_rbtree_insert(struct rb_root *root, struct sk_buff *skb);

/* Which part of the queue a currently linked skb lives in. */
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

/*
 * ---- measurement counters (CONFIG_TCP_RTX_STATS) ----
 *
 * Per-CPU event counters plus a histogram of the queue length N, sampled
 * every time an skb is linked into the queue. N counts skbs (a GSO skb is
 * one entry), not TCP segments. Read with `cat /proc/rtx_stats`, reset by
 * writing anything to it as root (do it while no traffic is running).
 *
 * The enum is always defined so call sites compile in every configuration.
 * With the option off, all helpers are empty and the generated code does
 * not change. Keep it off for final performance runs (design doc 10.3).
 */
enum rtx_stat {
	/* counted inside the adapter (A) */
	RTX_STAT_INSERT_APPEND,
	RTX_STAT_INSERT_ORDERED,
	RTX_STAT_INSERT_SPLIT,
	RTX_STAT_UNLINK_TREE,
	RTX_STAT_UNLINK_LIST,
	RTX_STAT_UNLINK_PURGE,		/* subset of UNLINK_*: whole-queue purge */
	RTX_STAT_PROMOTE,
	RTX_STAT_FIND_COVER,		/* rtx_find_cover() calls */
	RTX_STAT_FIND_COVER_TREE_STEPS,	/* tree nodes compared */
	RTX_STAT_FIND_COVER_LIST_STEPS,	/* list nodes compared (hybrid) */
	/* counted at call sites (C) */
	RTX_STAT_SACK_SKIP_REUSE,	/* tcp_sacktag_skip() kept the cursor */
	RTX_STAT_SACK_BLOCK,		/* SACK blocks processed */
	RTX_STAT_SACK_WALK_SKB,		/* skbs visited by tcp_sacktag_walk() */
	RTX_STAT_SHIFT_PARTIAL,
	RTX_STAT_SHIFT_MERGE,
	RTX_STAT_SHIFT_FALLBACK,
	RTX_STAT_ACK_CLEAN,		/* tcp_clean_rtx_queue() calls */
	RTX_STAT_ACK_UNLINK,		/* skbs removed by tcp_clean_rtx_queue() */
	RTX_STAT_ACK_TRIM,		/* partial ACK head trims */
	__RTX_STAT_MAX
};

/* N histogram bucket b holds N in [2^b, 2^(b+1)-1]; the last is open. */
#define RTX_N_HIST_BUCKETS	16

#ifdef CONFIG_TCP_RTX_STATS
struct rtx_stats {
	u64 cnt[__RTX_STAT_MAX];
	u64 n_hist[RTX_N_HIST_BUCKETS];
};
DECLARE_PER_CPU(struct rtx_stats, rtx_stats);

static inline void rtx_stat_add(enum rtx_stat id, u64 val)
{
	this_cpu_add(rtx_stats.cnt[id], val);
}

static inline void __rtx_count_link(struct sock *sk)
{
	u32 n = ++tcp_sk(sk)->rtx_count;
	unsigned int b = ilog2(n);

	if (b >= RTX_N_HIST_BUCKETS)
		b = RTX_N_HIST_BUCKETS - 1;
	this_cpu_inc(rtx_stats.n_hist[b]);
}

static inline void __rtx_count_unlink(struct sock *sk)
{
	tcp_sk(sk)->rtx_count--;
}
#else
static inline void rtx_stat_add(enum rtx_stat id, u64 val)
{
}

static inline void __rtx_count_link(struct sock *sk)
{
}

static inline void __rtx_count_unlink(struct sock *sk)
{
}
#endif

static inline void rtx_stat_inc(enum rtx_stat id)
{
	rtx_stat_add(id, 1);
}

/* ---- backend internals (no transaction check) ---- */

#ifdef CONFIG_TCP_RTX_HYBRID
static inline struct sk_buff *__rtx_list_first(const struct sock *sk)
{
	const struct tcp_sock *tp = tcp_sk(sk);

	if (list_empty(&tp->rtx_recent))
		return NULL;
	return list_entry(tp->rtx_recent.next, struct sk_buff, list);
}
#endif

/* Sequence partition, valid only outside a sequence-changing transaction. */
static inline enum rtx_domain __rtx_domain(const struct sock *sk,
					 const struct sk_buff *skb)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	const struct sk_buff *first = __rtx_list_first(sk);

	if (first && !before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(first)->seq))
		return RTX_DOMAIN_LIST;
#endif
	return RTX_DOMAIN_TREE;
}

static inline enum rtx_domain rtx_domain(const struct sock *sk,
					const struct sk_buff *skb)
{
	enum rtx_domain domain;
#if defined(CONFIG_TCP_RTX_DEBUG) && defined(CONFIG_TCP_RTX_HYBRID)
	const struct sk_buff *pos;
	bool member = false;
#endif

	rtx_assert_no_txn(sk);
	domain = __rtx_domain(sk, skb);
#if defined(CONFIG_TCP_RTX_DEBUG) && defined(CONFIG_TCP_RTX_HYBRID)
	list_for_each_entry(pos, &tcp_sk(sk)->rtx_recent, list) {
		if (pos == skb) {
			member = true;
			break;
		}
	}
	WARN_ON_ONCE(member != (domain == RTX_DOMAIN_LIST));
#endif
	return domain;
}

/* Report the selected partition and number of nodes compared. */
static inline struct sk_buff *__rtx_find_cover(struct sock *sk, u32 seq,
					       unsigned int *steps,
					       bool *list_search)
{
	struct rb_node *parent, **p = &sk->tcp_rtx_queue.rb_node;
	struct sk_buff *skb, *found = NULL;
	unsigned int n = 0;

	if (list_search)
		*list_search = false;
#ifdef CONFIG_TCP_RTX_HYBRID
	skb = __rtx_list_first(sk);
	if (skb && !before(seq, TCP_SKB_CB(skb)->seq)) {
		if (list_search)
			*list_search = true;
		list_for_each_entry(skb, &tcp_sk(sk)->rtx_recent, list) {
			n++;
			if (before(seq, TCP_SKB_CB(skb)->seq))
				break;
			if (before(seq, TCP_SKB_CB(skb)->end_seq)) {
				found = skb;
				break;
			}
		}
		goto done;
	}
#endif
	while (*p) {
		parent = *p;
		skb = rb_to_skb(parent);
		n++;
		if (before(seq, TCP_SKB_CB(skb)->seq)) {
			p = &parent->rb_left;
			continue;
		}
		if (!before(seq, TCP_SKB_CB(skb)->end_seq)) {
			p = &parent->rb_right;
			continue;
		}
		found = skb;
		break;
	}
#ifdef CONFIG_TCP_RTX_HYBRID
done:
#endif
	if (steps)
		*steps = n;
	return found;
}

/* Ordered insert into the tree part only. Never re-decides the domain. */
static inline void __rtx_tree_insert(struct sock *sk, struct sk_buff *skb)
{
	tcp_rbtree_insert(&sk->tcp_rtx_queue, skb);
#ifdef CONFIG_TCP_RTX_HYBRID
	if (!tcp_sk(sk)->rtx_tree_first ||
	    before(TCP_SKB_CB(skb)->seq,
		   TCP_SKB_CB(tcp_sk(sk)->rtx_tree_first)->seq))
		tcp_sk(sk)->rtx_tree_first = skb;
	if (!tcp_sk(sk)->rtx_tree_last ||
	    after(TCP_SKB_CB(skb)->seq,
		  TCP_SKB_CB(tcp_sk(sk)->rtx_tree_last)->seq))
		tcp_sk(sk)->rtx_tree_last = skb;
#endif
}

#ifdef CONFIG_TCP_RTX_HYBRID
static inline void __rtx_limit(struct sock *sk)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct sk_buff *skb;

	if (tp->rtx_recent_len <= CONFIG_TCP_RTX_RECENT_MAX)
		return;
	skb = __rtx_list_first(sk);
	list_del(&skb->list);
	tp->rtx_recent_len--;
	/* General ordered insertion also performs RB balancing. */
	__rtx_tree_insert(sk, skb);
	rtx_stat_inc(RTX_STAT_PROMOTE);
}
#endif

static inline void __rtx_unlink(struct sock *sk, struct sk_buff *skb,
				 enum rtx_domain domain)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	struct tcp_sock *tp = tcp_sk(sk);

	if (domain == RTX_DOMAIN_LIST) {
		list_del(&skb->list);
		tp->rtx_recent_len--;
		rtx_stat_inc(RTX_STAT_UNLINK_LIST);
		goto counted;
	}
	if (tp->rtx_tree_first == skb)
		tp->rtx_tree_first = skb_rb_next(skb);
	if (tp->rtx_tree_last == skb)
		tp->rtx_tree_last = skb_rb_prev(skb);
#endif
	rb_erase(&skb->rbnode, &sk->tcp_rtx_queue);
	rtx_stat_inc(RTX_STAT_UNLINK_TREE);
#ifdef CONFIG_TCP_RTX_HYBRID
counted:
#endif
	__rtx_count_unlink(sk);
}

/* ---- public API ---- */

static inline void rtx_init(struct sock *sk)
{
	sk->tcp_rtx_queue = RB_ROOT;
#ifdef CONFIG_TCP_RTX_HYBRID
	INIT_LIST_HEAD(&tcp_sk(sk)->rtx_recent);
	tcp_sk(sk)->rtx_recent_len = 0;
	tcp_sk(sk)->rtx_tree_first = NULL;
	tcp_sk(sk)->rtx_tree_last = NULL;
#endif
#ifdef CONFIG_TCP_RTX_DEBUG
	tcp_sk(sk)->rtx_txn_open = 0;
#endif
#ifdef CONFIG_TCP_RTX_STATS
	tcp_sk(sk)->rtx_count = 0;
#endif
}

/* Cache hint for the ACK fast path. Not a structural access. */
static inline void rtx_prefetch(const struct sock *sk)
{
	prefetch(sk->tcp_rtx_queue.rb_node);
}

static inline bool rtx_empty(const struct sock *sk)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	if (!list_empty(&tcp_sk(sk)->rtx_recent))
		return false;
#endif
	return RB_EMPTY_ROOT(&sk->tcp_rtx_queue);
}

/* Lowest sequence skb, or NULL if the queue is empty. */
static inline struct sk_buff *rtx_first(const struct sock *sk)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	return tcp_sk(sk)->rtx_tree_first ?: __rtx_list_first(sk);
#else
	return skb_rb_first(&sk->tcp_rtx_queue);
#endif
}

/* Highest sequence skb, or NULL if the queue is empty. */
static inline struct sk_buff *rtx_last(const struct sock *sk)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	const struct tcp_sock *tp = tcp_sk(sk);
#endif

	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	if (!list_empty(&tp->rtx_recent))
		return list_last_entry(&tp->rtx_recent, struct sk_buff, list);
	return tp->rtx_tree_last;
#else
	return skb_rb_last(&sk->tcp_rtx_queue);
#endif
}

static inline struct sk_buff *rtx_next(const struct sock *sk,
				       const struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	if (rtx_domain(sk, skb) == RTX_DOMAIN_LIST)
		return skb->list.next == &tcp_sk(sk)->rtx_recent ? NULL :
			list_entry(skb->list.next, struct sk_buff, list);
	return skb_rb_next(skb) ?: __rtx_list_first(sk);
#else
	return skb_rb_next(skb);
#endif
}

static inline struct sk_buff *rtx_prev(const struct sock *sk,
				       const struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	if (rtx_domain(sk, skb) == RTX_DOMAIN_LIST)
		return skb->list.prev == &tcp_sk(sk)->rtx_recent ?
			tcp_sk(sk)->rtx_tree_last :
			list_entry(skb->list.prev, struct sk_buff, list);
#endif
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
	struct sk_buff *skb;
	unsigned int steps;
	bool list_search;

	rtx_assert_no_txn(sk);
	skb = __rtx_find_cover(sk, seq, &steps, &list_search);
	rtx_stat_inc(RTX_STAT_FIND_COVER);
	rtx_stat_add(list_search ? RTX_STAT_FIND_COVER_LIST_STEPS :
		     RTX_STAT_FIND_COVER_TREE_STEPS, steps);
	return skb;
}

/* Insert an skb at its sequence position. */
static inline void rtx_insert_ordered(struct sock *sk, struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	if (__rtx_domain(sk, skb) == RTX_DOMAIN_LIST) {
		struct sk_buff *pos;
		struct list_head *at = &tcp_sk(sk)->rtx_recent;

		list_for_each_entry(pos, &tcp_sk(sk)->rtx_recent, list) {
			if (before(TCP_SKB_CB(skb)->seq,
				   TCP_SKB_CB(pos)->seq)) {
				at = &pos->list;
				break;
			}
		}
		list_add_tail(&skb->list, at);
		tcp_sk(sk)->rtx_recent_len++;
		__rtx_limit(sk);
	} else
#endif
		__rtx_tree_insert(sk, skb);
	rtx_stat_inc(RTX_STAT_INSERT_ORDERED);
	__rtx_count_link(sk);
}

/* Insert an skb that is known to come after the current tail.
 * Hybrid: append to the recent suffix, promoting its head if needed.
 */
static inline void rtx_append(struct sock *sk, struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_HYBRID
	list_add_tail(&skb->list, &tcp_sk(sk)->rtx_recent);
	tcp_sk(sk)->rtx_recent_len++;
	__rtx_limit(sk);
#else
	__rtx_tree_insert(sk, skb);
#endif
	rtx_stat_inc(RTX_STAT_INSERT_APPEND);
	__rtx_count_link(sk);
}

/* Remove an skb from the queue structure only (see contract above). */
static inline void rtx_unlink(struct sock *sk, struct sk_buff *skb)
{
	rtx_assert_no_txn(sk);
	__rtx_unlink(sk, skb, rtx_domain(sk, skb));
}

/* Replace a linked skb with the same sequence range; TCP owns its lifetime. */
static inline void rtx_replace(struct sock *sk, struct sk_buff *old,
			       struct sk_buff *new)
{
	rtx_assert_no_txn(sk);
#ifdef CONFIG_TCP_RTX_DEBUG
	WARN_ON_ONCE(TCP_SKB_CB(old)->seq != TCP_SKB_CB(new)->seq ||
		     TCP_SKB_CB(old)->end_seq != TCP_SKB_CB(new)->end_seq);
#endif
#ifdef CONFIG_TCP_RTX_HYBRID
	if (rtx_domain(sk, old) == RTX_DOMAIN_LIST) {
		list_replace(&old->list, &new->list);
		return;
	}
	if (tcp_sk(sk)->rtx_tree_first == old)
		tcp_sk(sk)->rtx_tree_first = new;
	if (tcp_sk(sk)->rtx_tree_last == old)
		tcp_sk(sk)->rtx_tree_last = new;
#endif
	rb_replace_node(&old->rbnode, &new->rbnode, &sk->tcp_rtx_queue);
}

/* SACK shift can consume a TREE skb up to the first LIST sequence.
 * Save its domain and successor before the sequence mutation, even when
 * the skb has already lost its payload. Commit never frees the skb.
 */
struct rtx_shift_token {
	struct sk_buff *skb;
	struct sk_buff *next;
	u8 domain;
};

static inline void rtx_shift_prepare(struct sock *sk, struct sk_buff *skb,
				     struct rtx_shift_token *tok)
{
	rtx_assert_no_txn(sk);
	tok->skb = skb;
	tok->domain = rtx_domain(sk, skb);
	tok->next = rtx_next(sk, skb);
#ifdef CONFIG_TCP_RTX_DEBUG
	tcp_sk(sk)->rtx_txn_open = 1;
#endif
}

static inline void rtx_shift_commit(struct sock *sk,
				    struct rtx_shift_token *tok, bool remove)
{
#ifdef CONFIG_TCP_RTX_DEBUG
	WARN_ON_ONCE(!tcp_sk(sk)->rtx_txn_open);
#endif
	if (remove)
		__rtx_unlink(sk, tok->skb, tok->domain);
#ifdef CONFIG_TCP_RTX_DEBUG
	tcp_sk(sk)->rtx_txn_open = 0;
#endif
	tok->skb = NULL;
	tok->next = NULL;
}

#ifdef CONFIG_TCP_RTX_DEBUG
/* Structural checks only, after a transaction. TCP accounting is separate. */
static inline bool rtx_validate_structure(struct sock *sk)
{
	struct sk_buff *skb, *prev = NULL;
#ifdef CONFIG_TCP_RTX_HYBRID
	struct tcp_sock *tp = tcp_sk(sk);
	unsigned int count = 0;
#endif

	if (tcp_sk(sk)->rtx_txn_open)
		return false;
#ifdef CONFIG_TCP_RTX_HYBRID
	if (tp->rtx_tree_first != skb_rb_first(&sk->tcp_rtx_queue) ||
	    tp->rtx_tree_last != skb_rb_last(&sk->tcp_rtx_queue))
		return false;
#endif
	for (skb = skb_rb_first(&sk->tcp_rtx_queue); skb;
	     skb = skb_rb_next(skb)) {
		if (!before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(skb)->end_seq) ||
		    (prev && after(TCP_SKB_CB(prev)->end_seq,
				   TCP_SKB_CB(skb)->seq)))
			return false;
		prev = skb;
	}
#ifdef CONFIG_TCP_RTX_HYBRID
	if (tp->rtx_recent.next->prev != &tp->rtx_recent ||
	    tp->rtx_recent.prev->next != &tp->rtx_recent)
		return false;
	list_for_each_entry(skb, &tp->rtx_recent, list) {
		if (++count > CONFIG_TCP_RTX_RECENT_MAX ||
		    skb->list.next->prev != &skb->list ||
		    skb->list.prev->next != &skb->list ||
		    !before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(skb)->end_seq) ||
		    (prev && after(TCP_SKB_CB(prev)->end_seq,
				   TCP_SKB_CB(skb)->seq)))
			return false;
		prev = skb;
	}
	if (count != tp->rtx_recent_len)
		return false;
#endif
	return true;
}
#endif

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
	tok->domain = rtx_domain(sk, skb);
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
	WARN_ON_ONCE(__rtx_find_cover(sk, bcb->seq, NULL, NULL));
	tcp_sk(sk)->rtx_txn_open = 0;
#endif
#ifdef CONFIG_TCP_RTX_HYBRID
	if (tok->domain == RTX_DOMAIN_LIST) {
		list_add(&buff->list, &tok->skb->list);
		tcp_sk(sk)->rtx_recent_len++;
		__rtx_limit(sk);
	} else
#endif
		__rtx_tree_insert(sk, buff);
	rtx_stat_inc(RTX_STAT_INSERT_SPLIT);
	__rtx_count_link(sk);
	tok->skb = NULL;
}

#endif /* _NET_TCP_RTX_H */
