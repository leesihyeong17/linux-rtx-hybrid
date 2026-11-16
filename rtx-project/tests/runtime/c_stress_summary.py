#!/usr/bin/env python3
"""Summarize a c_stress serial log: verdict, TcpExt deltas, rtx counters."""
import json, re, sys
t = open(sys.argv[1], errors='replace').read()
def mib(tag):
    rows = [l.split(' ', 1)[1] for l in t.splitlines() if l.startswith(tag + ' TcpExt:')]
    h, v = rows[0].split()[1:], rows[1].split()[1:]
    return dict(zip(h, map(int, v)))
b, a = mib('CSTRESS_MIB_BEFORE'), mib('CSTRESS_MIB_AFTER')
keys = ['TCPSackShifted', 'TCPSackMerged', 'TCPSackShiftFallback', 'TCPDSACKRecv', 'TCPDSACKOfoRecv',
        'TCPLossProbes', 'TCPLossProbeRecovery', 'TCPTimeouts', 'TCPSACKReorder', 'TCPFastRetrans',
        'TCPSlowStartRetrans', 'TCPSackRecovery', 'TCPLostRetransmit', 'TCPSACKReneging', 'TCPRetransFail']
stat = dict(re.findall(r'^CSTRESS_STAT (\w+) (\d+)$', t, re.M))
out = {
    'pass': 'CSTRESS PASS' in t and not re.search(r'WARNING:|BUG:|Oops:|Kernel panic|CSTRESS FAIL', t),
    'conns': len(re.findall(r'^CSTRESS conn=\d+ .* OK$', t, re.M)),
    'mib_delta': {k: a[k] - b[k] for k in keys},
    'rtx_stats': {k: int(v) for k, v in stat.items()},
}
s, m = out['rtx_stats'], out['mib_delta']
out['counter_eq_mib'] = (s.get('shift_partial') == m['TCPSackShifted'] and
                         s.get('shift_merge') == m['TCPSackMerged'] and
                         s.get('shift_fallback') == m['TCPSackShiftFallback'])
print(json.dumps(out, indent=1))
sys.exit(0 if out['pass'] and out['counter_eq_mib'] else 1)
