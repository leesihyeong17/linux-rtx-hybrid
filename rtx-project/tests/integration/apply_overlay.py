#!/usr/bin/env python3
"""Instrument only a disposable source tree; exact anchors fail closed."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import sys

root, harness, evidence = map(Path, sys.argv[1:])
target = root / 'net/ipv4/tcp_output.c'
original = target.read_text()
start = original.index('int tcp_fragment(')
end = original.index('\n/* This is similar to __pskb_pull_tail()', start)
body = original[start:end]
for old, new in [
    ('skb_unclone(skb, gfp)', 'b_unclone(sk, skb, gfp)'),
    ('sk_stream_alloc_skb(sk, nsize, gfp, true)', 'b_alloc(sk, nsize, gfp, true)'),
    ('rtx_split_prepare(sk, skb, &tok)', 'b_prepare(sk, skb, &tok)'),
    ('rtx_split_commit(sk, &tok, buff)', 'b_commit(sk, &tok, buff)'),
]:
    if body.count(old) != 1:
        raise SystemExit(f'Unexpected source: {old}')
    body = body.replace(old, new)
cases = sorted(harness.glob('case_*.c'))
if not cases:
    raise SystemExit('No integration cases')
c_cases = [p for p in cases if p.stem.startswith('case_c')]
headers = [harness / 'hooks.h', harness / 'common.h']
if c_cases:
    headers.append(harness / 'c_common.h')
for path in [*headers, *cases]:
    shutil.copy2(path, root / 'net/ipv4' / ('b_verify_' + path.name))
append = '\n#include "b_verify_common.h"\n'
if c_cases:
    append += '#include "b_verify_c_common.h"\n'
for case in cases:
    append += f'#include "b_verify_{case.name}"\n'
append += '\nstatic int __init b_verify_init(void)\n{\n\tunsigned int checks, failures;\n'
for case in cases:
    name = case.stem.removeprefix('case_')
    append += f'''\tchecks = b_checks; failures = b_failures;
\tb_case_{name}();
\tpr_info("BVERIFY_CASE {name} checks=%u failed=%u\\n", b_checks - checks, b_failures - failures);
'''
append += '\tpr_info("BVERIFY_TOTAL checks=%u failed=%u\\n", b_checks, b_failures);\n\treturn 0;\n}\nlate_initcall(b_verify_init);\n'
modified = original[:start] + '#include "b_verify_hooks.h"\n\n' + body + original[end:] + append
target.write_text(modified)
input_file = root / 'net/ipv4/tcp_input.c'
input_original = input_file.read_text()
input_modified = input_original
if any(p.stem == 'case_callers' for p in cases):
    input_modified += '''\nvoid b_test_mark_head_lost(struct sock *sk);
void b_test_mark_head_lost(struct sock *sk)
{
\ttcp_mark_head_lost(sk, 1, 0);
}
'''
if any(p.stem == 'case_sack' for p in cases):
    input_modified += '''\nstruct sk_buff *b_test_sack_walk(struct sock *sk, u32 start, u32 end);
struct sk_buff *b_test_sack_walk(struct sock *sk, u32 start, u32 end)
{
\tstruct rate_sample rate = {};
\tstruct tcp_sacktag_state state = {
\t\t.reord = tcp_sk(sk)->snd_nxt,
\t\t.rate = &rate,
\t\t.mss_now = 1000,
\t};
\tstruct sk_buff *skb;

\tif (!tcp_sk(sk)->sacked_out)
\t\ttcp_highest_sack_reset(sk);
\tskb = tcp_sacktag_skip(rtx_first(sk), sk, start);
\treturn tcp_sacktag_walk(skb, sk, NULL, &state, start, end, false);
}
'''
if c_cases:
    input_modified += '''\nint c_test_ack(struct sock *sk, struct sk_buff *skb);
int c_test_ack(struct sock *sk, struct sk_buff *skb)
{
\ttcp_mstamp_refresh(tcp_sk(sk));
\treturn tcp_ack(sk, skb, FLAG_SLOWPATH);
}

void c_test_enter_recovery(struct sock *sk);
void c_test_enter_recovery(struct sock *sk)
{
\ttcp_enter_recovery(sk, false);
}
'''
input_file.write_text(input_modified)
patch = ''.join(difflib.unified_diff(original.splitlines(True), modified.splitlines(True), fromfile='a/net/ipv4/tcp_output.c', tofile='b/net/ipv4/tcp_output.c'))
patch += ''.join(difflib.unified_diff(input_original.splitlines(True), input_modified.splitlines(True), fromfile='a/net/ipv4/tcp_input.c', tofile='b/net/ipv4/tcp_input.c'))
(evidence / 'instrumentation.patch').write_text(patch)
(evidence / 'cases.json').write_text(json.dumps([p.stem.removeprefix('case_') for p in cases]))
(evidence / 'source-hashes.json').write_text(json.dumps({
    'tcp_output_original': hashlib.sha256(original.encode()).hexdigest(),
    'tcp_output_instrumented': hashlib.sha256(modified.encode()).hexdigest(),
    'tcp_input_original': hashlib.sha256(input_original.encode()).hexdigest(),
    'tcp_input_instrumented': hashlib.sha256(input_modified.encode()).hexdigest(),
}, indent=2))
