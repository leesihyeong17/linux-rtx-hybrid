/* SPDX-License-Identifier: GPL-2.0 */
#include <linux/ktime.h>

#ifdef RTX_BENCH_OBSERVE
static struct sock *bm_observe_sk;
static bool bm_fail_alloc;
static u64 bm_p0, bm_p1, bm_c0, bm_c1;
static unsigned int bm_prepares, bm_commits, bm_promotes, bm_tail_links;

void rtx_bench_tail_event(struct sock *sk)
{
	if (sk == READ_ONCE(bm_observe_sk))
		bm_tail_links++;
}

void rtx_bench_promote_event(struct sock *sk)
{
	if (sk == READ_ONCE(bm_observe_sk))
		bm_promotes++;
}

static struct sk_buff *bm_alloc(struct sock *sk, int size, gfp_t gfp,
				bool force)
{
	if (sk == READ_ONCE(bm_observe_sk) && bm_fail_alloc)
		return NULL;
	return sk_stream_alloc_skb(sk, size, gfp, force);
}

static void bm_prepare(struct sock *sk, struct sk_buff *skb,
		       struct rtx_split_token *tok)
{
	if (sk != READ_ONCE(bm_observe_sk)) {
		rtx_split_prepare(sk, skb, tok);
		return;
	}
	bm_prepares++;
	bm_p0 = ktime_get_ns();
	rtx_split_prepare(sk, skb, tok);
	bm_p1 = ktime_get_ns();
}

static void bm_commit(struct sock *sk, struct rtx_split_token *tok,
		      struct sk_buff *buff)
{
	if (sk != READ_ONCE(bm_observe_sk)) {
		rtx_split_commit(sk, tok, buff);
		return;
	}
	bm_c0 = ktime_get_ns();
	rtx_split_commit(sk, tok, buff);
	bm_c1 = ktime_get_ns();
	bm_commits++;
}
#endif
