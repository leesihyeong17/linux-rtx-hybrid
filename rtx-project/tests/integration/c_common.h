/* SPDX-License-Identifier: GPL-2.0 */
/*
 * C-domain (ACK/SACK) fixture helpers. Included after b_verify_common.h
 * into the disposable instrumented kernel only.
 *
 * Data skbs keep their payload in page fragments so that the real SACK
 * shift/merge path (skb_can_shift) is taken. ACKs are real TCP headers with
 * SACK options passed to tcp_ack(), so option parsing, DSACK detection,
 * cumulative cleanup, loss marking and the retransmit re-entry all run.
 * Transmission is replaced by a stub that frees the clone.
 */
int c_test_ack(struct sock *sk, struct sk_buff *skb);
void c_test_enter_recovery(struct sock *sk);

#define C_MSS	1000
#define C_LEN	4000

enum c_mode {
	C_NOLOSS,	/* RACK on, reordering 127: no loss marking */
	C_CLASSIC,	/* RACK off, reordering 3: tcp_mark_head_lost */
	C_RACK,		/* RACK on, reordering 3: time-based loss */
	C_QUIET,	/* RACK off, reordering 127: no automatic marking */
};

static unsigned int c_xmits, c_shape;
static int c_family = AF_INET;
static bool c_block_xmit;
static u32 c_base;
static int c_saved_recovery;
static struct inet_connection_sock_af_ops c_af_ops;

static int c_fake_xmit(struct sock *sk, struct sk_buff *skb, struct flowi *fl)
{
	c_xmits++;
	kfree_skb(skb);
	return 0;
}

/* c_block_xmit makes retransmission fail before sending (route error). */
static int c_fake_rebuild(struct sock *sk)
{
	return c_block_xmit ? -EHOSTUNREACH : 0;
}

static u32 c_seq(unsigned int i)
{
	return c_base + i * C_LEN;
}

static unsigned long c_stat(enum rtx_stat id)
{
	unsigned long sum = 0;
	int cpu;

	for_each_possible_cpu(cpu)
		sum += per_cpu(rtx_stats, cpu).cnt[id];
	return sum;
}

static unsigned long c_mib(int id)
{
	return snmp_fold_field(init_net.mib.net_statistics, id);
}

struct c_count {
	unsigned long partial, merge, fallback, trim, unlink, block;
	unsigned long shifted, merged, shiftfb, dsack, reorder;
};

static void c_count(struct c_count *c)
{
	c->partial = c_stat(RTX_STAT_SHIFT_PARTIAL);
	c->merge = c_stat(RTX_STAT_SHIFT_MERGE);
	c->fallback = c_stat(RTX_STAT_SHIFT_FALLBACK);
	c->trim = c_stat(RTX_STAT_ACK_TRIM);
	c->unlink = c_stat(RTX_STAT_ACK_UNLINK);
	c->block = c_stat(RTX_STAT_SACK_BLOCK);
	c->shifted = c_mib(LINUX_MIB_SACKSHIFTED);
	c->merged = c_mib(LINUX_MIB_SACKMERGED);
	c->shiftfb = c_mib(LINUX_MIB_SACKSHIFTFALLBACK);
	c->dsack = c_mib(LINUX_MIB_TCPDSACKRECV) +
		   c_mib(LINUX_MIB_TCPDSACKOFORECV);
	c->reorder = c_mib(LINUX_MIB_TCPSACKREORDER);
}

/* The rtx counters and the MIBs they sit next to must move together. */
static void c_expect_shift(const struct c_count *a, unsigned long partial,
			   unsigned long merge)
{
	struct c_count b;

	c_count(&b);
	B_EXPECT(b.partial - a->partial == partial);
	B_EXPECT(b.merge - a->merge == merge);
	B_EXPECT(b.shifted - a->shifted == partial);
	B_EXPECT(b.merged - a->merged == merge);
	B_EXPECT(b.fallback - a->fallback == b.shiftfb - a->shiftfb);
}

/* b_socket() for AF_INET; the same setup for AF_INET6. */
static struct socket *c_create(void)
{
	struct socket *sock;
	int err;

	if (c_family == AF_INET)
		return b_socket();
	err = sock_create_kern(&init_net, AF_INET6, SOCK_STREAM, IPPROTO_TCP,
			       &sock);
	B_EXPECT(!err);
	if (err)
		return NULL;
	lock_sock(sock->sk);
	sock->sk->sk_sndbuf = 1024 * 1024;
	b_observe_sk = sock->sk;
	b_prepare_count = b_commit_count = b_fault_hits = b_fault = 0;
	b_alloc_count = b_fail_alloc_at = 0;
	return sock;
}

static struct socket *c_socket(u32 base, enum c_mode mode)
{
	struct socket *sock = c_create();
	struct tcp_sock *tp;
	struct sock *sk;

	if (!sock)
		return NULL;
	sk = sock->sk;
	tp = tcp_sk(sk);
	c_af_ops = *inet_csk(sk)->icsk_af_ops;
	c_af_ops.queue_xmit = c_fake_xmit;
	c_af_ops.rebuild_header = c_fake_rebuild;
	inet_csk(sk)->icsk_af_ops = &c_af_ops;
	tp->mss_cache = C_MSS;
	tp->advmss = C_MSS;
	tp->rx_opt.mss_clamp = C_MSS;
	tp->tcp_header_len = sizeof(struct tcphdr);
	tp->rx_opt.sack_ok = TCP_SACK_SEEN;
	tp->rx_opt.snd_wscale = 2;
	tp->max_window = tp->snd_wnd = 65535 << 2;
	tp->snd_cwnd = 256;
	/* receive side used by tcp_transmit_skb() on retransmission */
	inet_csk(sk)->icsk_ack.rcv_mss = C_MSS;
	tp->rcv_wnd = tp->window_clamp = tp->rcv_ssthresh = 65535;
	/* a connected socket gets these from its route (sk_setup_caps) */
	sk->sk_gso_max_segs = 64;
	sk->sk_gso_max_size = GSO_MAX_SIZE;
	tp->snd_una = tp->snd_nxt = tp->write_seq = base;
	tp->snd_wl1 = 0;
	tp->reordering = mode == C_NOLOSS || mode == C_QUIET ? 127 : 3;
	c_saved_recovery = init_net.ipv4.sysctl_tcp_recovery;
	init_net.ipv4.sysctl_tcp_recovery =
		mode == C_CLASSIC || mode == C_QUIET ? 0 :
		TCP_RACK_LOSS_DETECTION;
	inet_sk_state_store(sk, TCP_ESTABLISHED);
	tcp_init_congestion_control(sk);
	c_xmits = 0;
	c_base = base;
	return sock;
}

static void c_release(struct socket *sock)
{
	struct sock *sk = sock->sk;

	c_block_xmit = false;
	inet_csk_clear_xmit_timers(sk);
	inet_sk_state_store(sk, TCP_CLOSE);
	init_net.ipv4.sysctl_tcp_recovery = c_saved_recovery;
	b_release(sock);
}

/* One rtx skb of len bytes at seq, payload only in a page fragment. */
static struct sk_buff *c_add(struct sock *sk, u32 seq, unsigned int len)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct sk_buff *skb = sk_stream_alloc_skb(sk, 0, GFP_KERNEL, true);
	struct page *page;
	unsigned int i;
	u8 *p;

	B_EXPECT(skb != NULL);
	if (!skb)
		return NULL;
	page = alloc_page(GFP_KERNEL);
	B_EXPECT(page != NULL);
	if (!page) {
		__kfree_skb(skb);
		return NULL;
	}
	p = page_address(page);
	for (i = 0; i < len; i++)
		p[i] = (u8)((seq - c_base + i) * 31 + 7);
	skb_fill_page_desc(skb, 0, page, 0, len);
	skb->len += len;
	skb->data_len += len;
	skb->truesize += len;
	skb->ip_summed = CHECKSUM_PARTIAL;
	TCP_SKB_CB(skb)->seq = seq;
	TCP_SKB_CB(skb)->end_seq = seq + len;
	TCP_SKB_CB(skb)->tcp_flags = TCPHDR_ACK;
	TCP_SKB_CB(skb)->sacked = 0;
	tcp_set_skb_tso_segs(skb, C_MSS);
	__skb_header_release(skb);
	sk_wmem_queued_add(sk, skb->truesize);
	sk_mem_charge(sk, skb->truesize);
	rtx_append(sk, skb);
	skb->skb_mstamp_ns = 1000000;
	list_add_tail(&skb->tcp_tsorted_anchor, &tp->tsorted_sent_queue);
	tp->packets_out += tcp_skb_pcount(skb);
	tp->snd_nxt = tp->write_seq = TCP_SKB_CB(skb)->end_seq;
	return skb;
}

static bool c_fill(struct sock *sk, unsigned int n)
{
	unsigned int i;

	for (i = 0; i < n; i++)
		if (!c_add(sk, c_seq(i), C_LEN))
			return false;
	B_EXPECT(!skb_headlen(rtx_first(sk)) &&
		 skb_is_nonlinear(rtx_first(sk)));
	return true;
}

/*
 * Real ACK segment: ack number plus up to three SACK blocks [s, e) given
 * as absolute sequence numbers. Returns tcp_ack()'s result.
 */
static int c_ack(struct sock *sk, u32 ack, unsigned int nblocks, ...)
{
	unsigned int optlen = nblocks ? 4 + 8 * nblocks : 0, i;
	struct sk_buff *skb = alloc_skb(MAX_TCP_HEADER + 64, GFP_KERNEL);
	struct tcphdr *th;
	va_list ap;
	u8 *opt;
	int ret;

	B_EXPECT(skb != NULL && nblocks <= 3);
	if (!skb || nblocks > 3) {
		kfree_skb(skb);
		return -2;
	}
	skb_reserve(skb, MAX_TCP_HEADER);
	th = skb_push(skb, sizeof(*th) + optlen);
	skb_reset_transport_header(skb);
	memset(th, 0, sizeof(*th) + optlen);
	th->source = htons(8080);
	th->doff = (sizeof(*th) + optlen) / 4;
	th->ack = 1;
	th->ack_seq = htonl(ack);
	th->window = htons(65535);
	opt = (u8 *)(th + 1);
	if (nblocks) {
		opt[0] = TCPOPT_NOP;
		opt[1] = TCPOPT_NOP;
		opt[2] = TCPOPT_SACK;
		opt[3] = TCPOLEN_SACK_BASE + 8 * nblocks;
		va_start(ap, nblocks);
		for (i = 0; i < 2 * nblocks; i++)
			put_unaligned_be32(va_arg(ap, u32), opt + 4 + 4 * i);
		va_end(ap);
	}
	memset(TCP_SKB_CB(skb), 0, sizeof(struct tcp_skb_cb));
	TCP_SKB_CB(skb)->seq = TCP_SKB_CB(skb)->end_seq = 0;
	TCP_SKB_CB(skb)->ack_seq = ack;
	TCP_SKB_CB(skb)->sacked = nblocks ? 2 + sizeof(*th) : 0;
	ret = c_test_ack(sk, skb);
	kfree_skb(skb);
	return ret;
}

/*
 * Hint meaning (tcp.h, tcp_mark_head_lost, tcp_verify_retransmit_hint):
 * - highest_sack is the skb right after the highest SACKed skb (NULL at
 *   the tail) whenever sacked_out != 0;
 * - lost_cnt_hint counts SACKed packets before lost_skb_hint;
 * - no LOST, not retransmitted, not SACKed skb lies before
 *   retransmit_skb_hint, or tcp_xmit_retransmit_queue() would skip it.
 */
static bool c_hints(struct sock *sk, const char *tag)
{
	struct tcp_sock *tp = tcp_sk(sk);
	struct sk_buff *skb, *last_sacked = NULL;
	unsigned int sacked_before = 0, n = 0;
	bool rtx_hint_seen = false, ok = true;

	for (skb = rtx_first(sk); skb && n < 64; skb = rtx_next(sk, skb), n++) {
		u8 f = TCP_SKB_CB(skb)->sacked;

		if (skb == tp->lost_skb_hint &&
		    tp->lost_cnt_hint != sacked_before) {
			pr_err("CHINT %s lost_cnt_hint=%u expected=%u\n", tag,
			       tp->lost_cnt_hint, sacked_before);
			ok = false;
		}
		if (skb == tp->retransmit_skb_hint)
			rtx_hint_seen = true;
		if (tp->retransmit_skb_hint && !rtx_hint_seen &&
		    (f & TCPCB_LOST) &&
		    !(f & (TCPCB_SACKED_ACKED | TCPCB_SACKED_RETRANS))) {
			pr_err("CHINT %s lost skb %u before retransmit hint\n",
			       tag, TCP_SKB_CB(skb)->seq - c_base);
			ok = false;
		}
		if (f & TCPCB_SACKED_ACKED) {
			sacked_before += tcp_skb_pcount(skb);
			last_sacked = skb;
		}
	}
	if (tp->sacked_out && last_sacked &&
	    tp->highest_sack != rtx_next(sk, last_sacked)) {
		pr_err("CHINT %s highest_sack=%d expected after %u\n", tag,
		       tp->highest_sack ?
		       (int)(TCP_SKB_CB(tp->highest_sack)->seq - c_base) : -1,
		       TCP_SKB_CB(last_sacked)->seq - c_base);
		ok = false;
	}
	return ok;
}

/* Every byte still queued must carry the pattern written for its seq. */
static bool c_payload(struct sock *sk)
{
	struct sk_buff *skb;
	unsigned int i, n = 0;
	u8 value;

	for (skb = rtx_first(sk); skb && n < 64; skb = rtx_next(sk, skb), n++)
		for (i = 0; i < skb->len; i++)
			if (skb_copy_bits(skb, i, &value, 1) ||
			    value != (u8)((TCP_SKB_CB(skb)->seq - c_base + i) *
					  31 + 7))
				return false;
	return true;
}

static int c_index(struct sock *sk, struct sk_buff *target)
{
	struct sk_buff *skb;
	int i = 0;

	if (!target)
		return -1;
	for (skb = rtx_first(sk); skb && i < 64; skb = rtx_next(sk, skb), i++)
		if (skb == target)
			return i;
	return -2;
}

/*
 * Canonical TCP-visible state, identical across RB and hybrid builds by
 * design. Queue domains are printed on a separate CDOMAIN line.
 */
static void c_state(struct sock *sk, const char *tag)
{
	static char buf[1400];
	struct tcp_sock *tp = tcp_sk(sk);
	struct sk_buff *skb;
	int len = 0, n = 0;

	len += scnprintf(buf + len, sizeof(buf) - len,
			 "CSTATE k%u %s%s una=%u nxt=%u po=%u so=%u lo=%u ro=%u del=%u tr=%u ur=%d reo=%u ca=%u hs=%d lh=%d/%u rh=%d q=",
			 c_shape, c_family == AF_INET6 ? "v6-" : "", tag,
			 tp->snd_una - c_base,
			 tp->snd_nxt - c_base, tp->packets_out, tp->sacked_out,
			 tp->lost_out, tp->retrans_out, tp->delivered,
			 tp->total_retrans, tp->undo_retrans, tp->reordering,
			 inet_csk(sk)->icsk_ca_state,
			 c_index(sk, tp->highest_sack),
			 c_index(sk, tp->lost_skb_hint), tp->lost_cnt_hint,
			 c_index(sk, tp->retransmit_skb_hint));
	for (skb = rtx_first(sk); skb && n < 64; skb = rtx_next(sk, skb), n++)
		len += scnprintf(buf + len, sizeof(buf) - len, "%u+%u:%x/%u,",
				 TCP_SKB_CB(skb)->seq - c_base, skb->len,
				 TCP_SKB_CB(skb)->sacked, tcp_skb_pcount(skb));
	pr_info("%s\n", buf);
#ifdef CONFIG_TCP_RTX_HYBRID
	len = scnprintf(buf, sizeof(buf), "CDOMAIN k%u %s%s list=%u ", c_shape,
			c_family == AF_INET6 ? "v6-" : "", tag,
			tp->rtx_recent_len);
	n = 0;
	for (skb = rtx_first(sk); skb && n < 64; skb = rtx_next(sk, skb), n++)
		len += scnprintf(buf + len, sizeof(buf) - len, "%c",
				 rtx_domain(sk, skb) == RTX_DOMAIN_TREE ?
				 'T' : 'L');
	pr_info("%s\n", buf);
#endif
}

/* Full check after every step: structure, accounting, hints, payload. */
static void c_check(struct sock *sk, const char *tag)
{
	bool valid = b_valid(sk), structure = rtx_validate_structure(sk);
	bool hints = c_hints(sk, tag), payload = c_payload(sk);

	B_EXPECT(valid);
	B_EXPECT(structure);
	B_EXPECT(hints);
	B_EXPECT(payload);
	if (!valid || !structure || !hints || !payload)
		pr_err("CFAIL %s valid=%d structure=%d hints=%d payload=%d\n",
		       tag, valid, structure, hints, payload);
	c_state(sk, tag);
}

static unsigned int c_k(void)
{
#ifdef CONFIG_TCP_RTX_HYBRID
	return CONFIG_TCP_RTX_RECENT_MAX;
#else
	return c_shape;
#endif
}
