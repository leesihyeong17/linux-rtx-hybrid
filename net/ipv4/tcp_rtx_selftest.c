// SPDX-License-Identifier: GPL-2.0
/*
 * Boot-time self test for the TCP retransmit queue adapter (rtx_*).
 * Built only with CONFIG_TCP_RTX_DEBUG. Results go to the kernel log:
 *
 *   dmesg | grep rtx_selftest
 *
 * It uses a zeroed tcp_sock and bare skbs, not a real connection, and only
 * checks the queue structure (order, links, lookups, split transaction).
 * With CONFIG_TCP_RTX_STATS it also checks the per-socket skb count. Its
 * own inserts show up in /proc/rtx_stats, so reset that before measuring.
 */
#include <linux/init.h>
#include <linux/slab.h>
#include <linux/skbuff.h>
#include <net/tcp.h>

#define SEG	100

static int rtx_st_pass, rtx_st_fail;

#define CHECK(cond, fmt, ...)						\
	do {								\
		if (cond) {						\
			rtx_st_pass++;					\
		} else {						\
			rtx_st_fail++;					\
			pr_err("rtx_selftest: FAIL %s:%d: " fmt "\n",	\
			       __func__, __LINE__, ##__VA_ARGS__);	\
		}							\
	} while (0)

static struct sk_buff *st_skb(u32 seq, u32 end_seq)
{
	struct sk_buff *skb = alloc_skb(0, GFP_KERNEL);

	if (skb) {
		TCP_SKB_CB(skb)->seq = seq;
		TCP_SKB_CB(skb)->end_seq = end_seq;
	}
	return skb;
}

static struct sock *st_sock(void)
{
	struct tcp_sock *tp = kzalloc(sizeof(*tp), GFP_KERNEL);

	if (!tp)
		return NULL;
	rtx_init((struct sock *)tp);
	return (struct sock *)tp;
}

static void st_free(struct sock *sk)
{
	struct sk_buff *skb = rtx_first(sk), *tmp;

	rtx_walk_from_safe(skb, tmp, sk) {
		rtx_unlink(sk, skb);
		kfree_skb(skb);
	}
	CHECK(rtx_empty(sk), "queue not empty after unlinking everything");
	CHECK(rtx_validate_structure(sk), "empty structure invalid");
#ifdef CONFIG_TCP_RTX_STATS
	CHECK(tcp_sk(sk)->rtx_count == 0, "rtx_count %u after unlinking everything",
	      tcp_sk(sk)->rtx_count);
#endif
	kfree(sk);
}

/* Fill with n contiguous skbs starting at base, each SEG long. */
static bool st_fill(struct sock *sk, u32 base, int n)
{
	int i;

	for (i = 0; i < n; i++) {
		struct sk_buff *skb = st_skb(base + i * SEG, base + (i + 1) * SEG);

		if (!skb)
			return false;
		rtx_append(sk, skb);
#ifdef CONFIG_TCP_RTX_HYBRID
		CHECK(tcp_sk(sk)->rtx_recent_len ==
		      min_t(unsigned int, i + 1, CONFIG_TCP_RTX_RECENT_MAX),
		      "append capacity");
		CHECK(rtx_validate_structure(sk), "append structure");
#endif
	}
	return true;
}

/*
 * Walk the whole queue: ranges must be non-empty, contiguous from base to
 * end, and prev/next must agree. Returns the number of skbs.
 */
static int st_check_order(struct sock *sk, u32 base, u32 end)
{
	struct sk_buff *skb, *prev = NULL;
	u32 expect = base;
	int n = 0;

	rtx_walk(skb, sk) {
		const struct tcp_skb_cb *cb = TCP_SKB_CB(skb);

		CHECK(cb->seq == expect, "skb %d starts at %u, expected %u",
		      n, cb->seq, expect);
		CHECK(before(cb->seq, cb->end_seq), "skb %d is empty", n);
		CHECK(rtx_prev(sk, skb) == prev, "prev link broken at skb %d", n);
		expect = cb->end_seq;
		prev = skb;
		n++;
	}
	CHECK(rtx_validate_structure(sk), "invalid structure");
	CHECK(expect == end, "queue ends at %u, expected %u", expect, end);
	CHECK(rtx_last(sk) == prev, "rtx_last is not the last walked skb");
#ifdef CONFIG_TCP_RTX_STATS
	CHECK(tcp_sk(sk)->rtx_count == n, "rtx_count %u, walked %d",
	      tcp_sk(sk)->rtx_count, n);
#endif
	return n;
}

/* Same steps as tcp_fragment(): prepare, change seqs, commit. */
static struct sk_buff *st_split(struct sock *sk, struct sk_buff *skb, u32 len)
{
	struct rtx_split_token tok;
	enum rtx_domain domain = rtx_domain(sk, skb);
	struct sk_buff *buff = st_skb(0, 0);

	if (!buff)
		return NULL;

	rtx_split_prepare(sk, skb, &tok);
	CHECK(tok.skb == skb && tok.domain == domain,
	      "token does not record skb/domain");
	CHECK(tcp_sk(sk)->rtx_txn_open, "transaction not open after prepare");

	TCP_SKB_CB(buff)->seq = TCP_SKB_CB(skb)->seq + len;
	TCP_SKB_CB(buff)->end_seq = TCP_SKB_CB(skb)->end_seq;
	TCP_SKB_CB(skb)->end_seq = TCP_SKB_CB(buff)->seq;

	rtx_split_commit(sk, &tok, buff);
	CHECK(!tcp_sk(sk)->rtx_txn_open, "transaction still open after commit");
	CHECK(!tok.skb, "token still valid after commit");

	CHECK(rtx_next(sk, skb) == buff, "buff is not right after skb");
	CHECK(rtx_prev(sk, buff) == skb, "skb is not right before buff");
	CHECK(rtx_find_cover(sk, TCP_SKB_CB(buff)->seq) == buff,
	      "find_cover(buff seq) does not return buff");
	CHECK(rtx_find_cover(sk, TCP_SKB_CB(buff)->seq - 1) == skb,
	      "find_cover(buff seq - 1) does not return skb");
	return buff;
}

static struct sk_buff *st_nth(struct sock *sk, int idx)
{
	struct sk_buff *skb;

	rtx_walk(skb, sk)
		if (idx-- == 0)
			return skb;
	return NULL;
}

/* Split the head, a middle skb and the tail of an 8 skb queue. */
static void st_split_positions(u32 base)
{
	struct sock *sk = st_sock();
	struct sk_buff *skb, *buff;
	u32 end = base + 8 * SEG;

	if (!sk || !st_fill(sk, base, 8)) {
		CHECK(0, "setup failed (base %u)", base);
		return;
	}
	CHECK(st_check_order(sk, base, end) == 8, "initial queue");

	skb = rtx_first(sk);				/* head */
	st_split(sk, skb, 30);
	CHECK(rtx_first(sk) == skb, "head changed after head split");
	CHECK(st_check_order(sk, base, end) == 9, "after head split");

	skb = st_nth(sk, 4);				/* middle */
	st_split(sk, skb, 50);
	CHECK(st_check_order(sk, base, end) == 10, "after middle split");

	skb = rtx_last(sk);				/* tail */
	buff = st_split(sk, skb, 1);
	CHECK(rtx_last(sk) == buff, "rtx_last not updated after tail split");
	CHECK(st_check_order(sk, base, end) == 11, "after tail split");

	CHECK(!rtx_find_cover(sk, end), "find_cover(end) must be NULL");
	CHECK(!rtx_find_cover(sk, base - 1), "find_cover(base - 1) must be NULL");

	st_free(sk);
}

/* Split on a queue with a single skb, then split the new tail again. */
static void st_split_single(void)
{
	struct sock *sk = st_sock();
	struct sk_buff *skb, *buff;

	if (!sk || !st_fill(sk, 1000, 1)) {
		CHECK(0, "setup failed");
		return;
	}
	skb = rtx_first(sk);
	buff = st_split(sk, skb, 40);
	CHECK(rtx_first(sk) == skb && rtx_last(sk) == buff, "single split ends");
	st_split(sk, buff, 30);
	CHECK(st_check_order(sk, 1000, 1000 + SEG) == 3, "after two splits");
	st_free(sk);
}

#ifdef CONFIG_TCP_RTX_HYBRID
static void st_hybrid(void)
{
	struct sock *sk = st_sock();
	struct sk_buff *head, *back, *next, *replacement;
	struct rtx_shift_token tok;
	struct tcp_sock *child;
	const int k = CONFIG_TCP_RTX_RECENT_MAX;
	u32 base = 0xffffff00U;

	if (!sk || !st_fill(sk, base, k)) {
		CHECK(0, "hybrid setup failed");
		if (sk)
			st_free(sk);
		return;
	}
	CHECK(RB_EMPTY_ROOT(&sk->tcp_rtx_queue), "K entries must stay in LIST");
	CHECK(tcp_sk(sk)->rtx_recent_len == k, "LIST length at K");
	head = rtx_first(sk);
	back = st_split(sk, head, 30);
	CHECK(rtx_domain(sk, head) == RTX_DOMAIN_TREE, "head not promoted");
	CHECK(back && rtx_domain(sk, back) == RTX_DOMAIN_LIST,
	      "new fragment not in LIST");
	CHECK(tcp_sk(sk)->rtx_tree_first == head &&
	      tcp_sk(sk)->rtx_tree_last == head, "promotion cache");
	tcp_sk(sk)->rtx_recent_len++;
	CHECK(!rtx_validate_structure(sk), "bad length undetected");
	tcp_sk(sk)->rtx_recent_len--;
	tcp_sk(sk)->rtx_tree_last = NULL;
	CHECK(!rtx_validate_structure(sk), "bad cache undetected");
	tcp_sk(sk)->rtx_tree_last = head;
	CHECK(st_check_order(sk, base, base + k * SEG) == k + 1,
	      "promotion order");

	/* Reinitialize the copied sentinel without touching the parent. */
	child = kmemdup(tcp_sk(sk), sizeof(*child), GFP_KERNEL);
	CHECK(child, "child allocation");
	if (child) {
		rtx_init((struct sock *)child);
		CHECK(rtx_empty((struct sock *)child) &&
		      rtx_validate_structure((struct sock *)child),
		      "child init");
		CHECK(tcp_sk(sk)->rtx_recent_len == k, "parent changed");
		st_free((struct sock *)child);
	}

	/* Replace on both sides of the boundary without changing membership. */
	replacement = st_skb(TCP_SKB_CB(head)->seq, TCP_SKB_CB(head)->end_seq);
	CHECK(replacement, "replacement allocation");
	if (replacement) {
		rtx_replace(sk, head, replacement);
		kfree_skb(head);
		head = replacement;
		CHECK(tcp_sk(sk)->rtx_tree_first == head &&
		      tcp_sk(sk)->rtx_tree_last == head, "replacement cache");
	}
	next = rtx_last(sk);
	replacement = st_skb(TCP_SKB_CB(next)->seq, TCP_SKB_CB(next)->end_seq);
	CHECK(replacement, "LIST replacement allocation");
	if (replacement) {
		rtx_replace(sk, next, replacement);
		kfree_skb(next);
		CHECK(rtx_last(sk) == replacement &&
		      rtx_domain(sk, replacement) == RTX_DOMAIN_LIST,
		      "LIST replacement membership");
	}

	/* Force the exact full-shift hazard: TREE seq becomes LIST head seq. */
	rtx_shift_prepare(sk, head, &tok);
	next = tok.next;
	TCP_SKB_CB(head)->seq = TCP_SKB_CB(head)->end_seq;
	rtx_shift_commit(sk, &tok, true);
	kfree_skb(head);
	CHECK(rtx_first(sk) == next && RB_EMPTY_ROOT(&sk->tcp_rtx_queue),
	      "full shift removed wrong domain");
	CHECK(rtx_validate_structure(sk), "shift structure");
	st_free(sk);
	pr_info("rtx_selftest: hybrid K=%d promotion/replace/shift/child executed\n",
		k);
}

static void st_hybrid_ordered(void)
{
	struct sock *sk = st_sock();
	struct sk_buff *a = st_skb(1000, 1050);
	struct sk_buff *b = st_skb(1100, 1150);
	struct sk_buff *gap = st_skb(1050, 1100);
	struct sk_buff *prefix = st_skb(950, 1000);

	if (!sk || !a || !b || !gap || !prefix) {
		CHECK(0, "ordered setup");
		kfree_skb(a);
		kfree_skb(b);
		kfree_skb(gap);
		kfree_skb(prefix);
		if (sk)
			st_free(sk);
		return;
	}
	rtx_append(sk, a);
	rtx_append(sk, b);
	CHECK(!rtx_find_cover(sk, 1075), "hole must not match");
	rtx_insert_ordered(sk, gap);
	rtx_insert_ordered(sk, prefix);
	CHECK(st_check_order(sk, 950, 1150) == 4, "ordered insertion");
	CHECK(rtx_find_cover(sk, 1050) == gap, "half-open boundary");
	CHECK(!rtx_find_cover(sk, 1150), "end excluded");
	/* Partial trim does not cross a neighbour or change the domain. */
	TCP_SKB_CB(a)->seq += 10;
	CHECK(!rtx_find_cover(sk, 1005) && rtx_find_cover(sk, 1010) == a,
	      "trim lookup");
	CHECK(rtx_validate_structure(sk), "trim structure");
	st_free(sk);
}
#endif

static int __init rtx_selftest_init(void)
{
	st_split_positions(1000);
	/* sequence space wraps inside the queue */
	st_split_positions(0xfffffd00);
	/* the first skb itself crosses the wrap */
	st_split_positions(0xffffffe0);
	st_split_single();
#ifdef CONFIG_TCP_RTX_HYBRID
	st_hybrid();
	st_hybrid_ordered();
#endif

	pr_info("rtx_selftest: %d passed, %d failed\n", rtx_st_pass, rtx_st_fail);
	return 0;
}
late_initcall(rtx_selftest_init);
