#!/usr/bin/env python3
"""Compare C fixture TCP state (CSTATE lines) of a hybrid boot with RB.

The RB kernel runs every C scenario for both queue shapes (k16 and k1);
a hybrid kernel runs its own K. TCP-visible state must be identical.
"""
import json
from pathlib import Path
import re
import sys

LINE = re.compile(r'CSTATE k(\d+) (\S+) (.*)')


def states(text, shape):
    out = {}
    for line in text.splitlines():
        m = LINE.search(line)
        if m and int(m[1]) == shape:
            out.setdefault(m[2], []).append(m[3].strip())
    return out


def compare(hybrid, reference, shape):
    a, b = states(hybrid, shape), states(reference, shape)
    if not a or not b:
        raise ValueError(f'no CSTATE lines for k{shape}')
    if set(a) != set(b) or any(len(a[k]) != len(b[k]) for k in a):
        raise ValueError('different scenario steps')
    diffs = [k for k in a if a[k] != b[k]]
    return {'shape': shape, 'steps': sum(len(v) for v in a.values()),
            'diffs': diffs}


if __name__ == '__main__':
    hyb, ref, shape = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    try:
        result = compare(hyb.read_text(errors='replace'),
                         ref.read_text(errors='replace'), shape)
    except ValueError as exc:
        sys.exit(str(exc))
    print(json.dumps(result, indent=2))
    sys.exit(1 if result['diffs'] else 0)
