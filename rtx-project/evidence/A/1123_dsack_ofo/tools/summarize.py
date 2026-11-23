#!/usr/bin/env python3
"""Summarize DSACK_OFO QEMU runs: lost/retrans at 10ms, later snapshot, RACK path, polling."""
import re, sys, collections
for path in sys.argv[1:]:
    t = open(path, errors='replace').read().replace('\r', '')
    iters = len(re.findall(r'^DS iter', t, re.M))
    cfg = re.search(r'^DS uname (.*)$', t, re.M)
    acc = collections.Counter(re.findall(r'RTX_ACCOUNT DSACK_OFO unacked=4 sacked=2 (lost=\d+ retrans=\d+)', t))
    later = collections.Counter(re.findall(r'RTX_LATER DSACK_OFO (\+\d+ms lost=\d+ retrans=\d+ ca_state=\d+)', t))
    timing = collections.Counter(re.findall(r'RTX_TIMING DSACK_OFO (first_ca_state=\d+)', t))
    drained = collections.Counter(re.findall(r'RTX_ACCOUNT DSACK_OFO drained=1 (total_retrans=\d+)', t))
    rack = collections.Counter()
    for c in t.split('RTX_MARK CASE ')[1:]:
        if c.startswith('DSACK_OFO'):
            d = re.search(r'RACKDBG detect .*?remaining=(-?\d+)', c)
            if d:
                rack['immediate' if int(d.group(1)) <= 0 else 'timer'] += 1
    complete = len(re.findall(r'PACKETS_COMPLETE', t))
    fail = len(re.findall(r'RTX_PACKET FAIL|RTX_NATIVE TIMEOUT', t))
    warn = len(re.findall(r'WARNING:|BUG:|Oops', t))
    print(f"{path}: iters={iters} complete={complete} fail={fail} kernel_warn={warn}")
    print(f"  10ms: {dict(acc)}")
    if later: print(f"  later: {dict(later)}")
    if timing: print(f"  poll first state (3=Recovery/loss already marked, 1=Disorder/timer pending): {dict(timing)}")
    if rack: print(f"  RACK first decision (printk build): {dict(rack)}")
    print(f"  drained: {dict(drained)}")
