// SPDX-License-Identifier: GPL-2.0
/*
 * rtx model harness: runs an operation trace against the real
 * include/net/tcp_rtx.h compiled in user space (see build.sh) and prints
 * one line per command. rtx_model.py generates the trace, computes the
 * expected lines from an independent reference model and compares.
 *
 * After every command the harness also checks, independently of
 * rtx_validate_structure():
 *   - red-black properties, parent links and in-order sequence order of T
 *   - list links of L, L length vs rtx_recent_len (hybrid)
 *   - rtx_tree_first/rtx_tree_last vs rb_first()/rb_last() (hybrid)
 *   - rtx_count vs the number of linked skbs (STATS)
 *   - no WARN_ON_ONCE() fired, list debug checks passed
 * A violation prints "ERR ..." and exits with status 2.
 *
 * Trace commands (ids are small integers, sequence numbers are u32):
 *   NEW id seq end         create an unlinked skb
 *   APPEND id              rtx_append()
 *   ORD id                 rtx_insert_ordered()
 *   UNLINK id              rtx_unlink()
 *   ACK p                  unlink skbs fully below p from rtx_first(),
 *                          then head-trim the first skb if p is inside it
 *   SPLIT id buff mid      rtx_split_prepare/commit, skb [a,c) -> [a,mid)
 *                          + buff [mid,c) (buff created here)
 *   SHIFT prev cur n       rtx_shift_prepare(cur), prev.end += n,
 *                          cur.seq += n; commit(remove = cur now empty)
 *   REPLACE old new        rtx_replace(), new gets old's range
 *   PURGE                  rtx_walk_from_safe() + rtx_unlink() over all
 *   COVER q                rtx_find_cover()
 *   OVERLAP s e            first skb overlapping [s,e) (model-only helper)
 *   DUMP                   queue state line
 *   NBUCKET n              rtx_n_bucket(n) of STATS v2 (PR #33), "-" if absent
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <net/tcp.h>
#include <linux/rbtree_augmented.h>	/* rb_is_red(), rb_parent() */

#ifdef CONFIG_TCP_RTX_STATS
DEFINE_PER_CPU(struct rtx_stats, rtx_stats);
#ifdef RTX_N_EXACT_MAX
__be16 rtx_stats_port;		/* STATS v2 (PR #33): 0 = no filter */
#define RTX_MODEL_HIST "v2"
#define RTX_MODEL_N_LINK(b) rtx_stats.n_hist[RTX_N_LINK][b]
#else
#define RTX_MODEL_HIST "v1"
#define RTX_MODEL_N_LINK(b) rtx_stats.n_hist[b]
#endif
#else
#define RTX_MODEL_HIST "none"
#endif

#ifdef RTX_N_EXACT_MAX
#define RTX_MODEL_NBUCKET 1	/* rtx_n_bucket() exists (PR #33) */
#else
#define RTX_MODEL_NBUCKET 0
#endif

#ifdef CONFIG_TCP_RTX_CACHE
#define RTX_MODEL_CACHE 1
#else
#define RTX_MODEL_CACHE 0
#endif
#ifdef CONFIG_TCP_RTX_TAIL
#define RTX_MODEL_TAIL 1
#else
#define RTX_MODEL_TAIL 0
#endif

#define MAX_SKB 65536

static struct sk_buff *skbs[MAX_SKB];
static struct tcp_sock tsk;
static struct sock *sk = &tsk.sk;
static unsigned long cmd_no;
static unsigned int warns;

static void fail(const char *fmt, ...) __attribute__((format(printf, 1, 2), noreturn));

#include <stdarg.h>
static void fail(const char *fmt, ...)
{
	va_list ap;

	printf("ERR cmd=%lu ", cmd_no);
	va_start(ap, fmt);
	vprintf(fmt, ap);
	va_end(ap);
	printf("\n");
	fflush(stdout);
	exit(2);
}

void rtx_model_warn(const char *file, int line, const char *cond)
{
	warns++;
	printf("ERR cmd=%lu WARN %s:%d %s\n", cmd_no, file, line, cond);
	fflush(stdout);
	exit(2);
}

/* CONFIG_DEBUG_LIST validators (glue, same checks as lib/list_debug.c). */
bool __list_add_valid(struct list_head *new, struct list_head *prev,
		      struct list_head *next)
{
	if (next->prev != prev || prev->next != next || new == prev ||
	    new == next)
		fail("list_add corruption");
	return true;
}

bool __list_del_entry_valid(struct list_head *entry)
{
	if (entry->next == LIST_POISON1 || entry->prev == LIST_POISON2 ||
	    entry->prev->next != entry || entry->next->prev != entry)
		fail("list_del corruption");
	return true;
}

static struct sk_buff *get(int id)
{
	if (id < 0 || id >= MAX_SKB || !skbs[id])
		fail("unknown skb id %d", id);
	return skbs[id];
}

static struct sk_buff *new_skb(int id, u32 seq, u32 end)
{
	struct sk_buff *skb;

	if (id < 0 || id >= MAX_SKB || skbs[id])
		fail("bad new id %d", id);
	skb = calloc(1, sizeof(*skb));
	if (!skb)
		fail("oom");
	/* Garbage in the shared link words, like a recycled skb. */
	memset(skb, 0xa5, offsetof(struct sk_buff, cb));
	skb->model_id = id;
	TCP_SKB_CB(skb)->seq = seq;
	TCP_SKB_CB(skb)->end_seq = end;
	skbs[id] = skb;
	return skb;
}

/* Unlinked skbs stay allocated but are poisoned so stale use crashes. */
static void retire(struct sk_buff *skb)
{
	memset(skb, 0x5a, offsetof(struct sk_buff, cb));
}

static int idof(const struct sk_buff *skb)
{
	return skb ? skb->model_id : -1;
}

/* ---- independent structure checks ---- */

static unsigned int rb_check(const struct rb_node *n, const struct rb_node *parent,
			     unsigned int *nodes)
{
	unsigned int lh, rh;

	if (!n)
		return 1;
	if (rb_parent(n) != parent)
		fail("rb parent link broken at skb %d", idof(rb_to_skb((struct rb_node *)n)));
	if (rb_is_red(n) && ((n->rb_left && rb_is_red(n->rb_left)) ||
			     (n->rb_right && rb_is_red(n->rb_right))))
		fail("rb red-red at skb %d", idof(rb_to_skb((struct rb_node *)n)));
	lh = rb_check(n->rb_left, n, nodes);
	rh = rb_check(n->rb_right, n, nodes);
	if (lh != rh)
		fail("rb black height %u != %u at skb %d", lh, rh,
		     idof(rb_to_skb((struct rb_node *)n)));
	(*nodes)++;
	return lh + !rb_is_red(n);
}

static unsigned int check_structure(void)
{
	const struct rb_node *root = sk->tcp_rtx_queue.rb_node;
	struct sk_buff *skb, *prev = NULL;
	unsigned int tree = 0, walked = 0, list = 0;

	if (root && rb_is_red(root))
		fail("rb root is red");
	rb_check(root, NULL, &tree);
	for (skb = skb_rb_first(&sk->tcp_rtx_queue); skb; skb = skb_rb_next(skb)) {
		if (prev && before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(prev)->end_seq))
			fail("tree order %d after %d", idof(skb), idof(prev));
		prev = skb;
		walked++;
	}
	if (walked != tree)
		fail("tree walk %u != node count %u", walked, tree);
#ifdef RTX_MODEL_TREE_CACHE
	if (tsk.rtx_tree_first != skb_rb_first(&sk->tcp_rtx_queue) ||
	    tsk.rtx_tree_last != skb_rb_last(&sk->tcp_rtx_queue))
		fail("tree cache first=%d/%d last=%d/%d",
		     idof(tsk.rtx_tree_first),
		     idof(skb_rb_first(&sk->tcp_rtx_queue)),
		     idof(tsk.rtx_tree_last),
		     idof(skb_rb_last(&sk->tcp_rtx_queue)));
#endif
#ifdef CONFIG_TCP_RTX_HYBRID
	{
		struct list_head *pos, *head = &tsk.rtx_recent;

		for (pos = head->next; pos != head; pos = pos->next) {
			if (pos->next->prev != pos || pos->prev->next != pos)
				fail("list link broken");
			skb = list_entry(pos, struct sk_buff, list);
			if (prev && before(TCP_SKB_CB(skb)->seq,
					   TCP_SKB_CB(prev)->end_seq))
				fail("list order %d after %d", idof(skb), idof(prev));
			prev = skb;
			if (++list > MAX_SKB)
				fail("list loop");
		}
		if (list != tsk.rtx_recent_len)
			fail("rtx_recent_len %u != list %u", tsk.rtx_recent_len, list);
		if (list > CONFIG_TCP_RTX_RECENT_MAX)
			fail("list %u > K", list);
	}
#endif
#ifdef CONFIG_TCP_RTX_STATS
	if (tsk.rtx_count != tree + list)
		fail("rtx_count %u != %u", tsk.rtx_count, tree + list);
#endif
#ifdef CONFIG_TCP_RTX_DEBUG
	if (!rtx_validate_structure(sk))
		fail("rtx_validate_structure() false");
#endif
	return tree + list;
}

/* ---- model-only range lookup (design 5.3 rtx_first_overlap) ---- */

static struct sk_buff *first_overlap(u32 start, u32 end)
{
	struct rb_node *n = sk->tcp_rtx_queue.rb_node;
	struct sk_buff *skb, *cand = NULL;

#ifdef CONFIG_TCP_RTX_HYBRID
	skb = __rtx_list_first(sk);
	if (skb && !before(start, TCP_SKB_CB(skb)->seq)) {
		/* Every tree skb ends at or before first(L).seq <= start. */
		list_for_each_entry(skb, &tsk.rtx_recent, list)
			if (after(TCP_SKB_CB(skb)->end_seq, start)) {
				cand = skb;
				break;
			}
		goto check;
	}
#endif
	while (n) {	/* lower bound on end_seq > start */
		skb = rb_to_skb(n);
		if (after(TCP_SKB_CB(skb)->end_seq, start)) {
			cand = skb;
			n = n->rb_left;
		} else {
			n = n->rb_right;
		}
	}
#ifdef CONFIG_TCP_RTX_HYBRID
	if (!cand)
		cand = __rtx_list_first(sk);
check:
#endif
	if (cand && !before(TCP_SKB_CB(cand)->seq, end))
		cand = NULL;
	return cand;
}

/* ---- commands ---- */

static void dump(void)
{
	struct sk_buff *skb;
	unsigned int n = 0;

	printf("D first=%d last=%d fwd=", idof(rtx_first(sk)), idof(rtx_last(sk)));
	rtx_walk(skb, sk) {
		printf("%s%d", n ? "," : "", idof(skb));
		if (++n > MAX_SKB)
			fail("walk loop");
	}
	printf(" bwd=");
	n = 0;
	for (skb = rtx_last(sk); skb; skb = rtx_prev(sk, skb)) {
		printf("%s%d", n ? "," : "", idof(skb));
		if (++n > MAX_SKB)
			fail("reverse walk loop");
	}
	printf(" dom=");
	rtx_walk(skb, sk)
		putchar(rtx_domain(sk, skb) == RTX_DOMAIN_LIST ? 'L' : 'T');
	printf(" empty=%d\n", rtx_empty(sk));
}

static void cmd_ack(u32 p)
{
	struct sk_buff *skb;
	unsigned int n = 0;

	while ((skb = rtx_first(sk)) && !after(TCP_SKB_CB(skb)->end_seq, p)) {
		rtx_unlink(sk, skb);
		retire(skb);
		n++;
	}
	/* tcp_trim_head(): seq moves forward in place, no relink. */
	if (skb && after(p, TCP_SKB_CB(skb)->seq))
		TCP_SKB_CB(skb)->seq = p;
	printf("ACK removed=%u\n", n);
}

static void cmd_split(struct sk_buff *skb, int buff_id, u32 mid)
{
	struct rtx_split_token tok;
	struct sk_buff *buff;
	u32 end = TCP_SKB_CB(skb)->end_seq;

	buff = new_skb(buff_id, mid, end);
	rtx_split_prepare(sk, skb, &tok);
	TCP_SKB_CB(skb)->end_seq = mid;
	rtx_split_commit(sk, &tok, buff);
	printf("OK\n");
}

static void cmd_shift(struct sk_buff *prev, struct sk_buff *skb, u32 n)
{
	struct rtx_shift_token tok;
	bool gone;

	rtx_shift_prepare(sk, skb, &tok);
	TCP_SKB_CB(prev)->end_seq += n;
	TCP_SKB_CB(skb)->seq += n;
	gone = TCP_SKB_CB(skb)->seq == TCP_SKB_CB(skb)->end_seq;
	rtx_shift_commit(sk, &tok, gone);
	if (gone)
		retire(skb);
	printf("SHIFT gone=%d\n", gone);
}

static void cmd_purge(void)
{
	struct sk_buff *skb = rtx_first(sk), *tmp;
	unsigned int n = 0;

	/* Same shape as tcp_rtx_queue_purge(). */
	rtx_walk_from_safe(skb, tmp, sk) {
		rtx_unlink(sk, skb);
		retire(skb);
		n++;
	}
	printf("PURGE removed=%u\n", n);
}

int main(void)
{
	char line[256], op[32];
	unsigned long a, b, c;
	int nf;

	rtx_init(sk);
	printf("CONF hybrid=%d k=%d debug=%d stats=%d cache=%d tail=%d hist=%s nbucket=%d\n",
#ifdef CONFIG_TCP_RTX_HYBRID
	       1, CONFIG_TCP_RTX_RECENT_MAX,
#else
	       0, 0,
#endif
#ifdef CONFIG_TCP_RTX_DEBUG
	       1,
#else
	       0,
#endif
#ifdef CONFIG_TCP_RTX_STATS
	       1,
#else
	       0,
#endif
	       RTX_MODEL_CACHE, RTX_MODEL_TAIL, RTX_MODEL_HIST,
	       RTX_MODEL_NBUCKET);

	while (fgets(line, sizeof(line), stdin)) {
		if (line[0] == '#' || line[0] == '\n')
			continue;
		cmd_no++;
		nf = sscanf(line, "%31s %lu %lu %lu", op, &a, &b, &c);
		if (nf < 1)
			fail("parse: %s", line);
		if (!strcmp(op, "NEW") && nf == 4) {
			new_skb(a, b, c);
			printf("OK\n");
		} else if (!strcmp(op, "APPEND") && nf == 2) {
			rtx_append(sk, get(a));
			printf("OK\n");
		} else if (!strcmp(op, "ORD") && nf == 2) {
			rtx_insert_ordered(sk, get(a));
			printf("OK\n");
		} else if (!strcmp(op, "UNLINK") && nf == 2) {
			rtx_unlink(sk, get(a));
			retire(get(a));
			printf("OK\n");
		} else if (!strcmp(op, "ACK") && nf == 2) {
			cmd_ack(a);
		} else if (!strcmp(op, "SPLIT") && nf == 4) {
			cmd_split(get(a), b, c);
		} else if (!strcmp(op, "SHIFT") && nf == 4) {
			cmd_shift(get(a), get(b), c);
		} else if (!strcmp(op, "REPLACE") && nf == 3) {
			struct sk_buff *old = get(a);
			struct sk_buff *new = new_skb(b, TCP_SKB_CB(old)->seq,
						      TCP_SKB_CB(old)->end_seq);

			rtx_replace(sk, old, new);
			retire(old);
			printf("OK\n");
		} else if (!strcmp(op, "PURGE") && nf == 1) {
			cmd_purge();
		} else if (!strcmp(op, "COVER") && nf == 2) {
			printf("C %d\n", idof(rtx_find_cover(sk, a)));
		} else if (!strcmp(op, "OVERLAP") && nf == 3) {
			printf("O %d\n", idof(first_overlap(a, b)));
		} else if (!strcmp(op, "NBUCKET") && nf == 2) {
#ifdef RTX_N_EXACT_MAX
			printf("B %u\n", rtx_n_bucket(a));
#else
			printf("B -\n");	/* no STATS v2 bucket function */
#endif
		} else if (!strcmp(op, "DUMP") && nf == 1) {
			dump();
		} else {
			fail("parse: %s", line);
		}
		check_structure();
	}
#ifdef CONFIG_TCP_RTX_STATS
	{
		unsigned int i, n = 0;

		printf("STAT append=%llu ordered=%llu split=%llu unlink_tree=%llu"
		       " unlink_list=%llu promote=%llu find_cover=%llu\n",
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_INSERT_APPEND],
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_INSERT_ORDERED],
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_INSERT_SPLIT],
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_UNLINK_TREE],
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_UNLINK_LIST],
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_PROMOTE],
		       (unsigned long long)rtx_stats.cnt[RTX_STAT_FIND_COVER]);
		printf("HIST");
		for (i = 0; i < RTX_N_HIST_BUCKETS; i++)
			if (RTX_MODEL_N_LINK(i))
				printf("%s%u:%llu", n++ ? "," : " ", i,
				       (unsigned long long)RTX_MODEL_N_LINK(i));
		printf("\n");
	}
#endif
	printf("END cmds=%lu warns=%u\n", cmd_no, warns);
	return 0;
}
