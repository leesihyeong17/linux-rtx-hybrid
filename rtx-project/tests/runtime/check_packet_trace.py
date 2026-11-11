#!/usr/bin/env python3
"""Fail closed unless each packet scenario reached successful RTX fragmentation."""
import re
import sys
from pathlib import Path


def validate(text):
    for case in ('SACK', 'LOSS', 'RTO'):
        if not re.search(rf'^RTX_PACKET {case} bytes=4000 retransmitted=\d+ PASS\r?$', text, re.M):
            raise ValueError(f'{case}: missing packet success')
    for case in ('LOSS', 'RTO'):
        if not re.search(rf'^RTX_PACKET {case} bytes=4000 retransmitted=[1-9]\d* PASS', text, re.M):
            raise ValueError(f'{case}: no retransmitted payload')
    if text.count('RTX_TRACE_BEGIN') != 1 or text.count('RTX_TRACE_END') != 1:
        raise ValueError('missing or duplicate trace delimiters')
    trace = text.split('RTX_TRACE_BEGIN', 1)[1].split('RTX_TRACE_END', 1)[0]
    counts = re.search(r'entries-in-buffer/entries-written:\s*(\d+)/(\d+)', trace)
    if not counts or counts[1] != counts[2]:
        raise ValueError('trace missing counters or overwritten events')
    current = None
    summary = {case: {'rtx_entries': 0, 'returns': 0} for case in ('SACK', 'LOSS', 'RTO')}
    seen = []
    for line in trace.splitlines():
        marker = re.search(r'tracing_mark_write: CASE (SACK|LOSS|RTO)', line)
        if marker:
            current = marker[1]
            seen.append(current)
        if 'rtx_fragment:' in line:
            event = re.search(r'queue=(0x[0-9a-f]+|\d+) len=(0x[0-9a-f]+|\d+)', line)
            if not current or not event:
                raise ValueError('unattributed or malformed fragment event')
            if int(event[1], 0) != 1 or not 0 < int(event[2], 0) < 4000:
                raise ValueError('unexpected queue or fragment length')
            summary[current]['rtx_entries'] += 1
        if 'rtx_result:' in line:
            event = re.search(r'result=(-?\d+)', line)
            if not current or not event or int(event[1]) != 0:
                raise ValueError('failed or unattributed fragment return')
            summary[current]['returns'] += 1
    if seen != ['SACK', 'LOSS', 'RTO']:
        raise ValueError('case markers missing or out of order')
    for case, count in summary.items():
        if not count['rtx_entries'] or count['rtx_entries'] != count['returns']:
            raise ValueError(f'{case}: no complete successful RTX fragment calls')
    return summary


if __name__ == '__main__':
    try:
        print(validate(Path(sys.argv[1]).read_text()))
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
