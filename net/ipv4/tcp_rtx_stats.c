// SPDX-License-Identifier: GPL-2.0
/*
 * TCP retransmit queue measurement counters (CONFIG_TCP_RTX_STATS).
 *
 *   cat /proc/rtx_stats              sum over all CPUs
 *   echo 0 > /proc/rtx_stats         reset counters and histograms
 *   echo "port 5001" > /proc/rtx_stats
 *                                    count only sockets with that local or
 *                                    remote port ("port 0" = all sockets)
 *
 * Writing needs root. Reset while the measured traffic is stopped.
 * The file lives at /proc/rtx_stats, not /proc/net, so it is visible from
 * every network namespace (the counters are global anyway).
 * See include/net/tcp_rtx.h for what each histogram samples.
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/percpu.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/string.h>
#include <linux/uaccess.h>
#include <net/tcp.h>

DEFINE_PER_CPU(struct rtx_stats, rtx_stats);
EXPORT_PER_CPU_SYMBOL_GPL(rtx_stats);

__be16 rtx_stats_port __read_mostly;
EXPORT_SYMBOL_GPL(rtx_stats_port);

static const char * const rtx_stat_names[__RTX_STAT_MAX] = {
	[RTX_STAT_INSERT_APPEND]	 = "insert_append",
	[RTX_STAT_INSERT_ORDERED]	 = "insert_ordered",
	[RTX_STAT_INSERT_SPLIT]		 = "insert_split",
	[RTX_STAT_UNLINK_TREE]		 = "unlink_tree",
	[RTX_STAT_UNLINK_LIST]		 = "unlink_list",
	[RTX_STAT_UNLINK_PURGE]		 = "unlink_purge",
	[RTX_STAT_PROMOTE]		 = "promote",
	[RTX_STAT_FIND_COVER]		 = "find_cover",
	[RTX_STAT_FIND_COVER_TREE_STEPS] = "find_cover_tree_steps",
	[RTX_STAT_FIND_COVER_LIST_STEPS] = "find_cover_list_steps",
	[RTX_STAT_SACK_SKIP_REUSE]	 = "sack_skip_reuse",
	[RTX_STAT_SACK_BLOCK]		 = "sack_block",
	[RTX_STAT_SACK_WALK_SKB]	 = "sack_walk_skb",
	[RTX_STAT_SHIFT_PARTIAL]	 = "shift_partial",
	[RTX_STAT_SHIFT_MERGE]		 = "shift_merge",
	[RTX_STAT_SHIFT_FALLBACK]	 = "shift_fallback",
	[RTX_STAT_ACK_CLEAN]		 = "ack_clean",
	[RTX_STAT_ACK_UNLINK]		 = "ack_unlink",
	[RTX_STAT_ACK_TRIM]		 = "ack_trim",
};

static const char * const rtx_n_names[__RTX_N_MAX] = {
	[RTX_N_LINK]	= "n_link",
	[RTX_N_ACK]	= "n_ack",
};

/* Print "<name> <N> <count>" for exact buckets and "<name> <lo>-<hi>" or
 * "<name> <lo>-" for the power-of-two ones, so every line has three fields.
 */
static void rtx_stats_show_hist(struct seq_file *m, enum rtx_n_hist h)
{
	int b, cpu;

	for (b = 0; b < RTX_N_HIST_BUCKETS; b++) {
		unsigned int shift = b - (RTX_N_EXACT_MAX + 1) +
				     ilog2(RTX_N_EXACT_MAX);
		u64 sum = 0;

		for_each_possible_cpu(cpu)
			sum += per_cpu(rtx_stats, cpu).n_hist[h][b];

		if (b <= RTX_N_EXACT_MAX)
			seq_printf(m, "%s %d %llu\n", rtx_n_names[h], b, sum);
		else if (b == RTX_N_HIST_BUCKETS - 1)
			seq_printf(m, "%s %u- %llu\n", rtx_n_names[h],
				   1U << shift, sum);
		else
			seq_printf(m, "%s %u-%u %llu\n", rtx_n_names[h],
				   b == RTX_N_EXACT_MAX + 1 ?
				   RTX_N_EXACT_MAX + 1 : 1U << shift,
				   (2U << shift) - 1, sum);
	}
}

static int rtx_stats_show(struct seq_file *m, void *v)
{
	int i, cpu;

	seq_printf(m, "filter_port %u\n", ntohs(READ_ONCE(rtx_stats_port)));

	for (i = 0; i < __RTX_STAT_MAX; i++) {
		u64 sum = 0;

		for_each_possible_cpu(cpu)
			sum += per_cpu(rtx_stats, cpu).cnt[i];
		seq_printf(m, "%s %llu\n",
			   rtx_stat_names[i] ? rtx_stat_names[i] : "unnamed", sum);
	}

	for (i = 0; i < __RTX_N_MAX; i++)
		rtx_stats_show_hist(m, i);
	return 0;
}

static int rtx_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, rtx_stats_show, NULL);
}

static ssize_t rtx_stats_write(struct file *file, const char __user *ubuf,
			       size_t len, loff_t *ppos)
{
	char buf[16], *s;
	u16 port;
	int cpu;

	if (len >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, len))
		return -EFAULT;
	buf[len] = '\0';
	s = strim(buf);

	if (!strcmp(s, "0")) {
		for_each_possible_cpu(cpu)
			memset(per_cpu_ptr(&rtx_stats, cpu), 0,
			       sizeof(struct rtx_stats));
		return len;
	}
	if (!strncmp(s, "port ", 5) &&
	    !kstrtou16(skip_spaces(s + 5), 10, &port)) {
		WRITE_ONCE(rtx_stats_port, htons(port));
		return len;
	}
	return -EINVAL;
}

static const struct file_operations rtx_stats_fops = {
	.open		= rtx_stats_open,
	.read		= seq_read,
	.write		= rtx_stats_write,
	.llseek		= seq_lseek,
	.release	= single_release,
};

static int __init rtx_stats_init(void)
{
	BUILD_BUG_ON(rtx_n_bucket(1U << (ilog2(RTX_N_EXACT_MAX) +
					  RTX_N_LOG_BUCKETS - 1)) !=
		     RTX_N_HIST_BUCKETS - 1);
	if (!proc_create("rtx_stats", 0644, NULL, &rtx_stats_fops))
		return -ENOMEM;
	return 0;
}
fs_initcall(rtx_stats_init);
