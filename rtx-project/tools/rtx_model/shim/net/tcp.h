/* SPDX-License-Identifier: GPL-2.0 */
/*
 * User-space stand-in for include/net/tcp.h: only the pieces that
 * include/net/tcp_rtx.h uses. Layouts that matter for the adapter are kept:
 * sk_buff overlays next/prev, rbnode and list in one union exactly like
 * include/linux/skbuff.h, so writing one view clobbers the others.
 */
#ifndef _TCP_H
#define _TCP_H

#include "../kshim.h"
#include <linux/rbtree.h>
#include <linux/list.h>

struct sk_buff {
	union {
		struct {
			struct sk_buff *next;
			struct sk_buff *prev;
			union {
				void *dev;
				unsigned long dev_scratch;
			};
		};
		struct rb_node rbnode;
		struct list_head list;
	};
	char cb[48] __attribute__((aligned(8)));
	int model_id;		/* harness only */
};

/* Same leading layout as struct tcp_skb_cb. */
struct tcp_skb_cb {
	__u32 seq;
	__u32 end_seq;
};
#define TCP_SKB_CB(__skb) ((struct tcp_skb_cb *)&((__skb)->cb[0]))

/* From include/linux/skbuff.h. */
#define rb_to_skb(rb) rb_entry_safe(rb, struct sk_buff, rbnode)
#define skb_rb_first(root) rb_to_skb(rb_first(root))
#define skb_rb_last(root)  rb_to_skb(rb_last(root))
#define skb_rb_next(skb)   rb_to_skb(rb_next(&(skb)->rbnode))
#define skb_rb_prev(skb)   rb_to_skb(rb_prev(&(skb)->rbnode))

struct sock {
	struct rb_root tcp_rtx_queue;
};

/* Fields as in include/linux/tcp.h. Before PR #25 the tree endpoint cache
 * lives under CONFIG_TCP_RTX_HYBRID; PR #25 moves it to CONFIG_TCP_RTX_CACHE
 * (selected by HYBRID). Declaring it for either covers both layouts.
 */
#if defined(CONFIG_TCP_RTX_HYBRID) || defined(CONFIG_TCP_RTX_CACHE)
#define RTX_MODEL_TREE_CACHE 1
#endif

/* PR #33 STATS v2 port filter reads inet_sk(sk)->inet_{s,d}port. */
typedef uint16_t __be16;
struct inet_sock {
	__be16 inet_sport;
	__be16 inet_dport;
};

struct tcp_sock {
	struct sock sk;
	struct inet_sock model_inet;
#ifdef CONFIG_TCP_RTX_HYBRID
	struct list_head rtx_recent;
	u32 rtx_recent_len;
#endif
#ifdef RTX_MODEL_TREE_CACHE
	struct sk_buff *rtx_tree_first;
	struct sk_buff *rtx_tree_last;
#endif
#ifdef CONFIG_TCP_RTX_STATS
	u32 rtx_count;
#endif
#ifdef CONFIG_TCP_RTX_DEBUG
	u8 rtx_txn_open;
#endif
};

static inline struct tcp_sock *tcp_sk(const struct sock *sk)
{
	return (struct tcp_sock *)sk;
}

static inline struct inet_sock *inet_sk(const struct sock *sk)
{
	return &tcp_sk(sk)->model_inet;
}

/* From include/net/tcp.h. */
static inline bool before(__u32 seq1, __u32 seq2)
{
	return (__s32)(seq1 - seq2) < 0;
}
#define after(seq2, seq1) before(seq1, seq2)

#include <net/tcp_rtx.h>

#endif
