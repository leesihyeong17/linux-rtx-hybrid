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
	struct sk_buff *buff = st_skb(0, 0);

	if (!buff)
		return NULL;

	rtx_split_prepare(sk, skb, &tok);
	CHECK(tok.skb == skb && tok.domain == RTX_DOMAIN_TREE,
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

static int __init rtx_selftest_init(void)
{
	st_split_positions(1000);
	/* sequence space wraps inside the queue */
	st_split_positions(0xfffffd00);
	/* the first skb itself crosses the wrap */
	st_split_positions(0xffffffe0);
	st_split_single();

	pr_info("rtx_selftest: %d passed, %d failed\n", rtx_st_pass, rtx_st_fail);
	return 0;
}
late_initcall(rtx_selftest_init);
