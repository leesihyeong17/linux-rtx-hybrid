// SPDX-License-Identifier: GPL-2.0
/*
 * TCP retransmit queue measurement counters (CONFIG_TCP_RTX_STATS).
 *
 *   cat /proc/rtx_stats         sum over all CPUs
 *   echo 0 > /proc/rtx_stats    reset (root; do it while idle)
 *
 * The file lives at /proc/rtx_stats, not /proc/net, so it is visible from
 * every network namespace (the counters are global anyway).
 */
#include <linux/init.h>
#include <linux/percpu.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/string.h>
#include <net/tcp.h>

DEFINE_PER_CPU(struct rtx_stats, rtx_stats);
EXPORT_PER_CPU_SYMBOL_GPL(rtx_stats);

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

static int rtx_stats_show(struct seq_file *m, void *v)
{
	int i, cpu;

	for (i = 0; i < __RTX_STAT_MAX; i++) {
		u64 sum = 0;

		for_each_possible_cpu(cpu)
			sum += per_cpu(rtx_stats, cpu).cnt[i];
		seq_printf(m, "%s %llu\n",
			   rtx_stat_names[i] ? rtx_stat_names[i] : "unnamed", sum);
	}

	for (i = 0; i < RTX_N_HIST_BUCKETS; i++) {
		u64 sum = 0;

		for_each_possible_cpu(cpu)
			sum += per_cpu(rtx_stats, cpu).n_hist[i];
		if (i == RTX_N_HIST_BUCKETS - 1)
			seq_printf(m, "n_hist %u- %llu\n", 1U << i, sum);
		else
			seq_printf(m, "n_hist %u-%u %llu\n",
				   1U << i, (2U << i) - 1, sum);
	}
	return 0;
}

static int rtx_stats_open(struct inode *inode, struct file *file)
{
	return single_open(file, rtx_stats_show, NULL);
}

static ssize_t rtx_stats_write(struct file *file, const char __user *buf,
			       size_t len, loff_t *ppos)
{
	int cpu;

	for_each_possible_cpu(cpu)
		memset(per_cpu_ptr(&rtx_stats, cpu), 0, sizeof(struct rtx_stats));
	return len;
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
	if (!proc_create("rtx_stats", 0644, NULL, &rtx_stats_fops))
		return -ENOMEM;
	return 0;
}
fs_initcall(rtx_stats_init);
