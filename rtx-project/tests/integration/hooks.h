/* SPDX-License-Identifier: GPL-2.0 */
/* Included only into the disposable instrumented kernel source. */
static struct sock *b_observe_sk;
static int b_fault, b_prepare_count, b_commit_count, b_fault_hits;

static int b_unclone(struct sock *sk, struct sk_buff *skb, gfp_t gfp)
{
	if (sk == b_observe_sk && b_fault == 1) {
		b_fault_hits++;
		return -ENOMEM;
	}
	return skb_unclone(skb, gfp);
}

static struct sk_buff *b_alloc(struct sock *sk, int size, gfp_t gfp,
			       bool force)
{
	if (sk == b_observe_sk && b_fault == 2) {
		b_fault_hits++;
		return NULL;
	}
	return sk_stream_alloc_skb(sk, size, gfp, force);
}

static void b_prepare(struct sock *sk, struct sk_buff *skb,
		      struct rtx_split_token *tok)
{
	if (sk == b_observe_sk)
		b_prepare_count++;
	rtx_split_prepare(sk, skb, tok);
}

static void b_commit(struct sock *sk, struct rtx_split_token *tok,
		     struct sk_buff *buff)
{
	if (sk == b_observe_sk)
		b_commit_count++;
	rtx_split_commit(sk, tok, buff);
}
