// SPDX-License-Identifier: GPL-2.0
/* Boot-only addendum to the B benchmark overlay. */
static int __init compare_rtx_operations(unsigned int n)
{
	struct sk_buff *nodes[64], *skb;
	unsigned int i, idx, found = 0;
	struct socket *sock;
	struct sock *sk;
	u64 start, lookup, removal;
	int err;

	err = sock_create_kern(&init_net, AF_INET, SOCK_STREAM, IPPROTO_TCP,
			       &sock);
	if (err)
		return err;
	sk = sock->sk;
	lock_sock(sk);
	sk->sk_sndbuf = 1024 * 1024;
	for (i = 0; i < n; i++) {
		nodes[i] = bm_skb(sk, 1000 + i * BM_BYTES, BM_BYTES);
		if (!nodes[i]) {
			err = -ENOMEM;
			goto out;
		}
		rtx_append(sk, nodes[i]);
		bm_account_link(sk, nodes[i]);
	}
	if (bm_valid(sk) != n) {
		err = -EINVAL;
		goto out;
	}
	start = ktime_get_ns();
	for (i = 0; i < 4096; i++) {
		idx = (i * 17) & (n - 1);
		skb = rtx_find_cover(sk, 1001 + idx * BM_BYTES);
		found += skb == nodes[idx];
	}
	lookup = ktime_get_ns() - start;
	start = ktime_get_ns();
	for (i = 0; i < n; i++)
		rtx_unlink(sk, nodes[i]);
	removal = ktime_get_ns() - start;
	if (found != 4096 || !rtx_empty(sk))
		err = -EINVAL;
	for (i = 0; i < n; i++) {
		skb = nodes[i];
		list_del(&skb->tcp_tsorted_anchor);
		tcp_skb_tsorted_anchor_cleanup(skb);
		tcp_sk(sk)->packets_out -= tcp_skb_pcount(skb);
		sk_wmem_free_skb(sk, skb);
	}
	if (bm_valid(sk) != 0)
		err = -EINVAL;
	pr_info("RTXOPS n=%u hits=%u lookup_ns=%llu unlink_ns=%llu error=%d\n",
		n, found, lookup, removal, err);
out:
	tcp_write_queue_purge(sk);
	release_sock(sk);
	sock_release(sock);
	return err;
}

static int __init compare_rtx_init(void)
{
	unsigned int i;

	for (i = 0; i < 33; i++)
		if (compare_rtx_operations(4) || compare_rtx_operations(64))
			return -EINVAL;
	return 0;
}
late_initcall(compare_rtx_init);
