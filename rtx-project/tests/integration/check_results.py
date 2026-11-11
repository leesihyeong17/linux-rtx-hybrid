#!/usr/bin/env python3
import json
from pathlib import Path
import re
import sys


def validate(text, expected):
    if re.search(r'BVERIFY FAIL|WARNING:|BUG:|Oops:|Kernel panic|RTX_RUNTIME (FAIL|TIMEOUT)', text):
        raise ValueError('Failure marker or kernel diagnostic')
    rows = re.findall(r'BVERIFY_CASE (\w+) checks=(\d+) failed=(\d+)', text)
    if [row[0] for row in rows] != expected:
        raise ValueError('Missing, duplicate or unexpected case')
    if any(int(n) == 0 or int(f) != 0 for _, n, f in rows):
        raise ValueError('Zero checks or case failure')
    totals = re.findall(r'BVERIFY_TOTAL checks=(\d+) failed=(\d+)', text)
    if len(totals) != 1 or int(totals[0][0]) != sum(int(row[1]) for row in rows) or int(totals[0][1]):
        raise ValueError('Invalid total')
    if 'RTX_RUNTIME PASS' not in text or not re.search(r'rtx_selftest: [1-9]\d* passed, 0 failed', text):
        raise ValueError('Missing guest or adapter success')
    return {name: {'checks': int(n), 'failed': int(f)} for name, n, f in rows}


if __name__ == '__main__':
    try:
        print(json.dumps(validate(Path(sys.argv[1]).read_text(), json.loads(Path(sys.argv[2]).read_text())), indent=2))
    except ValueError as exc:
        sys.exit(str(exc))
