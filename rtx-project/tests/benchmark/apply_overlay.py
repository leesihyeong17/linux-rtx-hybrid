#!/usr/bin/env python3
"""Add a boot benchmark to disposable sources. Total mode leaves TCP bodies intact."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil


def apply(root, harness, evidence, mode):
    if mode not in ('performance', 'observation'):
        raise ValueError('Unknown benchmark mode')
    target = root / 'net/ipv4/tcp_output.c'
    header = root / 'include/net/tcp_rtx.h'
    original = target.read_text()
    original_header = header.read_text()
    if 'b_benchmark.c' in original or 'b_verify_hooks.h' in original:
        raise ValueError('Overlay already applied; use a fresh source copy')
    start = original.index('int tcp_fragment(')
    end = original.index('\n/* This is similar to __pskb_pull_tail()', start)
    body = original[start:end]
    modified_header = original_header
    if mode == 'observation':
        for old, new in (
            ('rtx_split_prepare(sk, skb, &tok)', 'bm_prepare(sk, skb, &tok)'),
            ('rtx_split_commit(sk, &tok, buff)', 'bm_commit(sk, &tok, buff)'),
            ('sk_stream_alloc_skb(sk, nsize, gfp, true)', 'bm_alloc(sk, nsize, gfp, true)'),
        ):
            if body.count(old) != 1:
                raise ValueError('Unexpected TCP anchor: ' + old)
            body = body.replace(old, new)
        anchor = '/* ---- backend internals (no transaction check) ---- */'
        if modified_header.count(anchor) != 1:
            raise ValueError('Unexpected Core declaration anchor')
        modified_header = modified_header.replace(anchor,
            'void rtx_bench_tail_event(struct sock *sk);\n'
            'void rtx_bench_promote_event(struct sock *sk);\n\n' + anchor, 1)
        for old, new in (
            ('rb_link_node(&skb->rbnode, parent, link);',
             'rtx_bench_tail_event(sk);\n\trb_link_node(&skb->rbnode, parent, link);'),
            ('rtx_stat_inc(sk, RTX_STAT_PROMOTE);',
             'rtx_bench_promote_event(sk);\n\trtx_stat_inc(sk, RTX_STAT_PROMOTE);'),
        ):
            if modified_header.count(old) != 1:
                raise ValueError('Unexpected Core anchor: ' + old)
            modified_header = modified_header.replace(old, new)
    prefix = '#define RTX_BENCH_OBSERVE\n' if mode == 'observation' else ''
    target.write_text(original[:start] + prefix + '#include "b_benchmark_hooks.h"\n\n'
                      + body + original[end:] + '\n#include "b_benchmark.c"\n')
    header.write_text(modified_header)
    shutil.copy2(harness / 'hooks.h', root / 'net/ipv4/b_benchmark_hooks.h')
    shutil.copy2(harness / 'benchmark.c', root / 'net/ipv4/b_benchmark.c')
    evidence.mkdir(parents=True, exist_ok=True)
    patch = ''
    hashes = {'mode': mode}
    for path, before, after in (
        ('net/ipv4/tcp_output.c', original, target.read_text()),
        ('include/net/tcp_rtx.h', original_header, modified_header),
    ):
        patch += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                         fromfile='a/' + path, tofile='b/' + path))
        hashes[path] = {'original': hashlib.sha256(before.encode()).hexdigest(),
                        'instrumented': hashlib.sha256(after.encode()).hexdigest()}
    (evidence / 'instrumentation.patch').write_text(patch)
    (evidence / 'source-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('harness', type=Path)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('--mode', choices=['performance', 'observation'], required=True)
    args = parser.parse_args()
    apply(args.root, args.harness, args.evidence, args.mode)
