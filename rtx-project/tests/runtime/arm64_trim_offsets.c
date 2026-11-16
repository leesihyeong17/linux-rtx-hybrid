// SPDX-License-Identifier: GPL-2.0
/* Compile to assembly with the target kernel's Kbuild flags; never load. */
#include <linux/kbuild.h>
#include <linux/skbuff.h>
#include <net/tcp.h>

void rtx_trim_offsets(void)
{
	OFFSET(SKB_CB, sk_buff, cb);
	OFFSET(CB_SEQ, tcp_skb_cb, seq);
	OFFSET(CB_END_SEQ, tcp_skb_cb, end_seq);
	OFFSET(CB_SEGS, tcp_skb_cb, tcp_gso_segs);
	DEFINE(SEQ_SIZE, sizeof(((struct tcp_skb_cb *)0)->seq));
	DEFINE(END_SEQ_SIZE, sizeof(((struct tcp_skb_cb *)0)->end_seq));
	DEFINE(SEGS_SIZE, sizeof(((struct tcp_skb_cb *)0)->tcp_gso_segs));
}
