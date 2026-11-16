#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Summarize a saved /proc/rtx_stats (STATS v2) dump.

Usage: a_rtx_stats_summary.py DUMP [DUMP ...] [-k 4,8,16] [--json]

DUMP is the plain output of `cat /proc/rtx_stats`. Lines may carry a prefix
(e.g. "A_STATS match ..."): only the last two or three fields are used.
Prints derived metrics from rtx-project/docs/A_N_HISTOGRAM.md section 3 and
the consistency checks of section 2.4. Exit status 1 if a check fails.
"""
import argparse
import json
import sys

HISTS = ('n_link', 'n_ack')
COUNTERS = (
    'filter_port', 'insert_append', 'insert_ordered', 'insert_split',
    'unlink_tree', 'unlink_list', 'unlink_purge', 'promote', 'find_cover',
    'find_cover_tree_steps', 'find_cover_list_steps', 'sack_skip_reuse',
    'sack_block', 'sack_walk_skb', 'shift_partial', 'shift_merge',
    'shift_fallback', 'ack_clean', 'ack_unlink', 'ack_trim',
)


def parse(path):
    cnt, hist = {}, {h: [] for h in HISTS}
    for line in open(path, errors='replace'):
        f = line.split()
        for i, tok in enumerate(f):
            if tok in HISTS and len(f) - i == 3:
                lo = f[i + 1].split('-')[0]
                hi = f[i + 1].split('-')[1] if '-' in f[i + 1] else lo
                hist[tok].append((int(lo), int(hi) if hi else None,
                                  int(f[i + 2])))
                break
            if tok in COUNTERS and len(f) - i == 2:
                cnt[tok] = int(f[i + 1])
                break
    missing = [c for c in COUNTERS if c not in cnt]
    if missing or any(len(hist[h]) != 74 for h in HISTS):
        sys.exit('%s: not a STATS v2 dump (missing %s)' % (path, missing or
                 'histogram buckets'))
    return cnt, hist


def ratio(a, b):
    return a / b if b else None


def share_le(buckets, k):
    total = sum(c for _, _, c in buckets)
    le = sum(c for lo, hi, c in buckets if hi is not None and hi <= k)
    return ratio(le, total)


def quantile(buckets, q):
    """Exact for N <= 64; for wider buckets returns the bucket's low edge."""
    total = sum(c for _, _, c in buckets)
    if not total:
        return None
    run = 0
    for lo, _, c in buckets:
        run += c
        if run >= q * total:
            return lo
    return None


def summarize(cnt, hist, ks):
    ins = cnt['insert_append'] + cnt['insert_ordered'] + cnt['insert_split']
    unl = cnt['unlink_tree'] + cnt['unlink_list']
    out = {
        'filter_port': cnt['filter_port'],
        'inserts': ins,
        'ack_clean': cnt['ack_clean'],
        'checks': {
            'sum_n_link_eq_inserts': sum(c for *_, c in hist['n_link']) == ins,
            'sum_n_ack_eq_ack_clean':
                sum(c for *_, c in hist['n_ack']) == cnt['ack_clean'],
            # holds once every measured connection has closed
            'inserts_eq_unlinks': ins == unl,
        },
        'unlink_tree': cnt['unlink_tree'],
        'unlink_list': cnt['unlink_list'],
        'promote': cnt['promote'],
        'list_removed_before_promote':
            ratio(cnt['unlink_list'], cnt['unlink_list'] + cnt['promote']),
        'ack_unlink_per_ack': ratio(cnt['ack_unlink'], cnt['ack_clean']),
        'find_cover': cnt['find_cover'],
        'find_cover_steps_avg':
            ratio(cnt['find_cover_tree_steps'] + cnt['find_cover_list_steps'],
                  cnt['find_cover']),
        'find_cover_list_step_share':
            ratio(cnt['find_cover_list_steps'],
                  cnt['find_cover_tree_steps'] + cnt['find_cover_list_steps']),
        'sack_walk_per_block': ratio(cnt['sack_walk_skb'], cnt['sack_block']),
    }
    for h in HISTS:
        out[h] = {
            'samples': sum(c for *_, c in hist[h]),
            'p50': quantile(hist[h], 0.5),
            'p90': quantile(hist[h], 0.9),
            'p99': quantile(hist[h], 0.99),
        }
        for k in ks:
            out[h]['le_%d' % k] = share_le(hist[h], k)
    return out


def fmt(v):
    if v is None:
        return '-'
    return '%.4f' % v if isinstance(v, float) else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dumps', nargs='+')
    ap.add_argument('-k', default='1,4,8,16,32,64',
                    help='K values for the N<=K share (each <= 64)')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    ks = [int(x) for x in a.k.split(',')]
    if any(k > 64 for k in ks):
        sys.exit('N<=K share is exact only for K <= 64')
    ok = True
    res = {}
    for d in a.dumps:
        res[d] = summarize(*parse(d), ks)
        ok &= all(res[d]['checks'].values())
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for d, r in res.items():
            print('== %s' % d)
            for key, v in r.items():
                if isinstance(v, dict):
                    print('  %-30s %s' % (key, '  '.join(
                        '%s=%s' % (k2, fmt(v2)) for k2, v2 in v.items())))
                else:
                    print('  %-30s %s' % (key, fmt(v)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
