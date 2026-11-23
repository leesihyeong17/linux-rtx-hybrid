// SPDX-License-Identifier: GPL-2.0
#include <linux/init.h>
#include <net/net_namespace.h>

#define BM_BYTES 4000
#define BM_MSS 1000
#define BM_MAX_N 128

static unsigned int bm_iterations = 32;
static unsigned int bm_warmup_ms = 10000;

static int __init bm_iterations_arg(char *arg)
{
	return !kstrtouint(arg, 0, &bm_iterations);
}
__setup("rtx_bench_iterations=", bm_iterations_arg);

static int __init bm_warmup_arg(char *arg)
{
	return !kstrtouint(arg, 0, &bm_warmup_ms);
}
__setup("rtx_bench_warmup_ms=", bm_warmup_arg);

struct bm_case {
	const char *id;
	unsigned int n;
	unsigned int headlen;
	bool split;
	bool clone;
	bool atomic;
	bool fail_alloc;
};

static const struct bm_case bm_cases[] = {
	{ "append_empty", 0, 4000 },
	{ "append_short", 4, 4000 },
	{ "append_k16", 16, 4000 },
	{ "append_long", 64, 4000 },
	{ "split_linear", 16, 4000, true },
	{ "split_paged", 16, 0, true },
	{ "split_mixed", 16, 2000, true },
	{ "split_clone", 16, 4000, true, true },
	{ "split_atomic", 16, 4000, true, false, true },
#ifdef RTX_BENCH_OBSERVE
	{ "split_alloc_failure", 16, 4000, true, false, false, true },
#endif
};

struct bm_input {
	unsigned int len, data_len, headlen, frags, frag_size;
	unsigned int users, dataref;
	bool cloned, shared, header_cloned, nohdr, head_frag, frag_list;
};

static int bm_list_len(struct sock *sk)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	return tcp_sk(sk)->rtx_recent_len;
#else
	return -1;
#endif
}

static struct sk_buff *bm_skb(struct sock *sk, u32 seq, unsigned int headlen)
{
	struct sk_buff *skb = sk_stream_alloc_skb(sk, headlen,
						GFP_KERNEL, true);
	unsigned int i, rest = BM_BYTES - headlen;
	struct page *page;

	if (!skb)
		return NULL;
	for (i = 0; i < headlen; i++)
		*(u8 *)skb_put(skb, 1) = (u8)(i * 31 + 7);
	if (rest) {
		page = alloc_page(GFP_KERNEL);
		if (!page) {
			kfree_skb(skb);
			return NULL;
		}
		for (i = 0; i < rest; i++)
			((u8 *)page_address(page))[i] =
				(u8)((headlen + i) * 31 + 7);
		skb_fill_page_desc(skb, 0, page, 0, rest);
		skb->len += rest;
		skb->data_len = rest;
		skb->truesize += PAGE_SIZE;
	}
	TCP_SKB_CB(skb)->seq = seq;
	TCP_SKB_CB(skb)->end_seq = seq + BM_BYTES;
	TCP_SKB_CB(skb)->tcp_flags = TCPHDR_ACK | TCPHDR_PSH;
	tcp_set_skb_tso_segs(skb, BM_MSS);
	__skb_header_release(skb);
	sk_wmem_queued_add(sk, skb->truesize);
	sk_mem_charge(sk, skb->truesize);
	return skb;
}

static void bm_account_link(struct sock *sk, struct sk_buff *skb)
{
	struct tcp_sock *tp = tcp_sk(sk);

	skb->skb_mstamp_ns = 1000000;
	list_add_tail(&skb->tcp_tsorted_anchor, &tp->tsorted_sent_queue);
	tp->packets_out += tcp_skb_pcount(skb);
	tp->snd_nxt = TCP_SKB_CB(skb)->end_seq;
}

static int bm_valid(struct sock *sk)
{
	struct sk_buff *skb, *prev = NULL;
	struct tcp_sock *tp = tcp_sk(sk);
	unsigned int packets = 0, memory = 0, n = 0;
	struct list_head *link = tp->tsorted_sent_queue.next;

	for (skb = rtx_first(sk); skb; skb = rtx_next(sk, skb)) {
		if (n == BM_MAX_N || rtx_prev(sk, skb) != prev ||
		    (prev && TCP_SKB_CB(prev)->end_seq != TCP_SKB_CB(skb)->seq))
			return -1;
		if (!before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(skb)->end_seq) ||
		    TCP_SKB_CB(skb)->sacked)
			return -1;
		if (link != &skb->tcp_tsorted_anchor ||
		    link->next->prev != link || link->prev->next != link)
			return -1;
		link = link->next;
		n++;
		packets += tcp_skb_pcount(skb);
		memory += skb->truesize;
		prev = skb;
	}
	if (prev != rtx_last(sk) || packets != tp->packets_out ||
	    memory != sk->sk_wmem_queued || tp->sacked_out || tp->lost_out ||
	    tp->retrans_out || tp->highest_sack || tp->lost_skb_hint ||
	    tp->retransmit_skb_hint)
		return -1;
	if (link != &tp->tsorted_sent_queue)
		return -1;
#ifdef CONFIG_TCP_RTX_CACHE
	if (tp->rtx_tree_first != skb_rb_first(&sk->tcp_rtx_queue) ||
	    tp->rtx_tree_last != skb_rb_last(&sk->tcp_rtx_queue))
		return -1;
#endif
#ifdef CONFIG_TCP_RTX_HYBRID
	if (tp->rtx_recent_len != min_t(unsigned int, n,
					   CONFIG_TCP_RTX_RECENT_MAX))
		return -1;
#endif
	return n;
}

static bool bm_payload(struct sk_buff *front, struct sk_buff *back)
{
	unsigned int i;
	u8 byte;

	for (i = 0; i < BM_BYTES; i++) {
		struct sk_buff *skb = i < front->len ? front : back;
		unsigned int offset = i < front->len ? i : i - front->len;

		if (!skb || skb_copy_bits(skb, offset, &byte, 1) ||
		    byte != (u8)(i * 31 + 7))
			return false;
	}
	return true;
}

static void bm_snapshot(struct sk_buff *skb, struct bm_input *in)
{
	in->len = skb->len;
	in->data_len = skb->data_len;
	in->headlen = skb_headlen(skb);
	in->frags = skb_shinfo(skb)->nr_frags;
	in->frag_size = in->frags ?
		skb_frag_size(&skb_shinfo(skb)->frags[0]) : 0;
	in->users = refcount_read(&skb->users);
	in->dataref = atomic_read(&skb_shinfo(skb)->dataref);
	in->cloned = skb_cloned(skb);
	in->shared = skb_shared(skb);
	in->header_cloned = skb_header_cloned(skb);
	in->nohdr = skb->nohdr;
	in->head_frag = skb->head_frag;
	in->frag_list = skb_has_frag_list(skb);
}

static void bm_print_input(const struct bm_case *c, unsigned int iteration,
			   const struct bm_input *in, gfp_t gfp)
{
	char fragments[32];
	const char *layout = !in->data_len ? "linear" :
			    (in->headlen ? "mixed" : "paged");

	if (in->frags)
		snprintf(fragments, sizeof(fragments), "[%u]", in->frag_size);
	else
		strscpy(fragments, "[]", sizeof(fragments));
	pr_info("RTXBENCH_INPUT {\"workload_id\":\"%s\",\"iteration\":%u,\"skb_len_bytes\":%u,\"skb_data_len_bytes\":%u,\"skb_headlen_bytes\":%u,\"data_layout\":\"%s\",\"nr_frags\":%u,\"frag_sizes_bytes\":%s,\"has_frag_list\":%u,\"head_frag\":%u,\"skb_cloned\":%u,\"skb_shared\":%u,\"skb_header_cloned\":%u,\"nohdr\":%u,\"skb_users\":%u,\"dataref_raw\":%u,\"gfp_mask\":\"0x%x\",\"gfp_flags\":[\"%s\"]}\n",
		c->id, iteration, in->len, in->data_len, in->headlen, layout,
		in->frags, fragments, in->frag_list, in->head_frag, in->cloned,
		in->shared, in->header_cloned, in->nohdr, in->users,
		in->dataref,
		(__force unsigned int)gfp,
		c->atomic ? "GFP_ATOMIC" : "GFP_KERNEL");
}

static int bm_one(const struct bm_case *c, unsigned int iteration, bool emit)
{
	struct socket *sock;
	struct sock *sk;
	struct tcp_sock *tp;
	struct sk_buff *skb = NULL, *target = NULL, *clone = NULL, *back;
	struct bm_input input;
	unsigned int i, index = c->n / 2;
	unsigned long memory;
	u64 begin, elapsed = 0, overhead = 0;
	int err, ret = 0, after_n = -1, before_l = -1, after_l = -1;
	bool valid = false;
	gfp_t gfp = c->atomic ? GFP_ATOMIC : GFP_KERNEL;
#ifdef RTX_BENCH_OBSERVE
	s64 prepare = -1, commit = -1, window = -1, core = -1;
#endif

	err = sock_create_kern(&init_net, AF_INET, SOCK_STREAM,
			       IPPROTO_TCP, &sock);
	if (err)
		return err;
	sk = sock->sk;
	tp = tcp_sk(sk);
	lock_sock(sk);
	sk->sk_sndbuf = 1024 * 1024;
	for (i = 0; i < c->n; i++) {
		skb = bm_skb(sk, 1000 + i * BM_BYTES,
			     c->split && i == index ? c->headlen : BM_BYTES);
		if (!skb) {
			err = -ENOMEM;
			goto cleanup;
		}
		rtx_append(sk, skb);
		bm_account_link(sk, skb);
		if (i == index)
			target = skb;
	}
	if (bm_valid(sk) != c->n) {
		err = -EINVAL;
		goto cleanup;
	}
	if (!c->split) {
		target = bm_skb(sk, 1000 + c->n * BM_BYTES, c->headlen);
		if (!target) {
			err = -ENOMEM;
			goto cleanup;
		}
	}
	if (c->clone) {
		tcp_skb_tsorted_save(target) {
			clone = skb_clone(target, GFP_KERNEL);
		} tcp_skb_tsorted_restore(target);
		if (!clone) {
			err = -ENOMEM;
			goto cleanup;
		}
	}
	bm_snapshot(target, &input);
	before_l = bm_list_len(sk);
	memory = sk->sk_wmem_queued;
#ifdef RTX_BENCH_OBSERVE
	bm_p0 = bm_p1 = bm_c0 = bm_c1 = 0;
	bm_prepares = bm_commits = bm_promotes = bm_tail_links = 0;
	bm_fail_alloc = c->fail_alloc;
	WRITE_ONCE(bm_observe_sk, sk);
#endif
	begin = ktime_get_ns();
	barrier();
	overhead = ktime_get_ns() - begin;
	begin = ktime_get_ns();
	if (c->split)
		ret = tcp_fragment(sk, TCP_FRAG_IN_RTX_QUEUE, target, 1500,
				   BM_MSS, gfp);
	else
		rtx_append(sk, target);
	elapsed = ktime_get_ns() - begin;
#ifdef RTX_BENCH_OBSERVE
	WRITE_ONCE(bm_observe_sk, NULL);
	bm_fail_alloc = false;
	if (bm_prepares)
		prepare = bm_p1 - bm_p0;
	if (bm_commits) {
		commit = bm_c1 - bm_c0;
		window = bm_c1 - bm_p0;
		core = prepare + commit;
	}
#endif
	if (!c->split)
		bm_account_link(sk, target);
	after_n = bm_valid(sk);
	after_l = bm_list_len(sk);
	valid = ret == (c->fail_alloc ? -ENOMEM : 0) &&
		after_n == c->n + !c->fail_alloc &&
		input.len == BM_BYTES && input.headlen == c->headlen &&
		input.frags == !!(BM_BYTES - c->headlen) &&
		input.cloned == c->clone && !input.shared && !input.frag_list;
	if (c->split && !ret) {
		back = rtx_next(sk, target);
		valid &= back && target->len == 1500 && back->len == 2500;
		valid &= tp->packets_out == c->n * 4 + 1;
		valid &= !(TCP_SKB_CB(target)->tcp_flags & TCPHDR_PSH);
		valid &= back && (TCP_SKB_CB(back)->tcp_flags & TCPHDR_PSH);
		valid &= bm_payload(target, back);
	} else {
		valid &= bm_payload(target, NULL);
		valid &= tp->packets_out == (c->n + !c->split) * 4;
		if (c->fail_alloc)
			valid &= sk->sk_wmem_queued == memory;
	}
#ifdef RTX_BENCH_OBSERVE
	valid &= bm_prepares == (c->split && !c->fail_alloc);
	valid &= bm_commits == bm_prepares;
#ifdef CONFIG_TCP_RTX_HYBRID
	valid &= bm_promotes == (!c->fail_alloc &&
		c->n >= CONFIG_TCP_RTX_RECENT_MAX);
	valid &= bm_tail_links ==
		(IS_ENABLED(CONFIG_TCP_RTX_TAIL) ? bm_promotes : 0);
#else
	valid &= !bm_promotes;
	valid &= bm_tail_links ==
		(!c->split && IS_ENABLED(CONFIG_TCP_RTX_TAIL));
#endif
#endif
	if (emit) {
		bm_print_input(c, iteration, &input, gfp);
		pr_info("RTXBENCH_SAMPLE {\"workload_id\":\"%s\",\"iteration\":%u,\"operation\":\"%s\",\"n_before\":%u,\"n_after\":%d,\"l_before\":%d,\"l_after\":%d,\"attempts\":1,\"successes\":%u,\"failures\":%u,\"actual_ops\":1,\"return_code\":%d,\"elapsed_ns\":%llu,\"timer_overhead_ns\":%llu,\"valid\":%u}\n",
			c->id, iteration, c->split ? "split" : "append", c->n,
			after_n, before_l, after_l, !ret, !!ret, ret,
			(unsigned long long)elapsed,
			(unsigned long long)overhead,
			valid);
#ifdef RTX_BENCH_OBSERVE
		pr_info("RTXBENCH_DETAIL {\"workload_id\":\"%s\",\"iteration\":%u,\"rtx_split_prepare_ns\":%lld,\"rtx_split_commit_ns\":%lld,\"rtx_split_window_ns\":%lld,\"rtx_split_core_ns\":%lld,\"prepares\":%u,\"commits\":%u,\"promotions\":%u,\"tail_links\":%u}\n",
			c->id, iteration, (long long)prepare, (long long)commit,
			(long long)window, (long long)core,
			bm_prepares, bm_commits,
			bm_promotes, bm_tail_links);
#endif
	}
	err = valid ? 0 : -EINVAL;
cleanup:
#ifdef RTX_BENCH_OBSERVE
	WRITE_ONCE(bm_observe_sk, NULL);
	bm_fail_alloc = false;
#endif
	tcp_write_queue_purge(sk);
	if (clone) {
		tcp_skb_tsorted_anchor_cleanup(clone);
		kfree_skb(clone);
	}
	if (!rtx_empty(sk) || sk->sk_wmem_queued || tp->packets_out ||
	    !list_empty(&tp->tsorted_sent_queue))
		err = -EINVAL;
	release_sock(sk);
	sock_release(sock);
	return err;
}

static int __init bm_init(void)
{
	unsigned int c, i, rows = 0, failed = 0;
	u64 until;
	int k = -1;
	const char *mode = "performance";

#ifdef RTX_BENCH_OBSERVE
	mode = "observation";
#endif
#ifdef CONFIG_TCP_RTX_HYBRID
	k = CONFIG_TCP_RTX_RECENT_MAX;
#endif
	if (!bm_iterations || bm_iterations > 10000 || bm_warmup_ms > 60000) {
		pr_err("RTXBENCH_ERROR invalid boot parameters\n");
		return 0;
	}
	pr_info("RTXBENCH_CONFIG {\"protocol\":1,\"contract_version\":\"0.2\",\"mode\":\"%s\",\"iterations\":%u,\"warmup_ms\":%u,\"workloads\":%zu,\"hybrid\":%u,\"cache\":%u,\"tail\":%u,\"debug\":%u,\"stats\":%u,\"k\":%d,\"tcp_sock_bytes\":%zu}\n",
		mode, bm_iterations, bm_warmup_ms, ARRAY_SIZE(bm_cases),
		IS_ENABLED(CONFIG_TCP_RTX_HYBRID),
		IS_ENABLED(CONFIG_TCP_RTX_CACHE),
		IS_ENABLED(CONFIG_TCP_RTX_TAIL),
		IS_ENABLED(CONFIG_TCP_RTX_DEBUG),
		IS_ENABLED(CONFIG_TCP_RTX_STATS), k, sizeof(struct tcp_sock));
	for (c = 0; c < ARRAY_SIZE(bm_cases); c++) {
		until = ktime_get_ns() + (u64)bm_warmup_ms * NSEC_PER_MSEC;
		while (ktime_get_ns() < until) {
			if (bm_one(&bm_cases[c], 0, false)) {
				failed++;
				goto done;
			}
			cond_resched();
		}
		for (i = 0; i < bm_iterations; i++) {
			if (bm_one(&bm_cases[c], i, true)) {
				failed++;
				goto done;
			}
			rows++;
			cond_resched();
		}
	}
done:
	pr_info("RTXBENCH_DONE {\"rows\":%u,\"failed\":%u}\n", rows, failed);
	return 0;
}
late_initcall(bm_init);
