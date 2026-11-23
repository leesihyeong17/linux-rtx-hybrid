// SPDX-License-Identifier: GPL-2.0
/* Supplemental boot-only fixture for appends shorter than one timer tick. */
static int __init compare_append_batch(unsigned int start_n, unsigned int count)
{
	struct sk_buff *pending[32], *skb;
	unsigned int i, prepared = 0;
	struct socket *sock;
	struct sock *sk;
	u64 start, elapsed = 0;
	int err;

	err = sock_create_kern(&init_net, AF_INET, SOCK_STREAM, IPPROTO_TCP,
			       &sock);
	if (err)
		return err;
	sk = sock->sk;
	lock_sock(sk);
	sk->sk_sndbuf = 1024 * 1024;
	for (i = 0; i < start_n; i++) {
		skb = bm_skb(sk, 1000 + i * BM_BYTES, BM_BYTES);
		if (!skb) {
			err = -ENOMEM;
			goto out;
		}
		rtx_append(sk, skb);
		bm_account_link(sk, skb);
	}
	if (bm_valid(sk) != start_n) {
		err = -EINVAL;
		goto out;
	}
	for (i = 0; i < count; i++) {
		skb = bm_skb(sk, 1000 + (start_n + i) * BM_BYTES, BM_BYTES);
		if (!skb) {
			err = -ENOMEM;
			break;
		}
		pending[prepared++] = skb;
	}
	start = ktime_get_ns();
	for (i = 0; i < prepared; i++)
		rtx_append(sk, pending[i]);
	elapsed = ktime_get_ns() - start;
	for (i = 0; i < prepared; i++)
		bm_account_link(sk, pending[i]);
	if (bm_valid(sk) != start_n + prepared)
		err = -EINVAL;
out:
	pr_info("RTXAPPEND start=%u count=%u elapsed_ns=%llu error=%d\n",
		start_n, prepared, elapsed, err);
	tcp_write_queue_purge(sk);
	release_sock(sk);
	sock_release(sock);
	return err;
}

static int __init compare_append_init(void)
{
	unsigned int i;

	for (i = 0; i < 33; i++)
		if (compare_append_batch(0, 12) || compare_append_batch(64, 32))
			return -EINVAL;
	return 0;
}
late_initcall(compare_append_init);
