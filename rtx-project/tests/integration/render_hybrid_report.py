#!/usr/bin/env python3
"""Render verified #14 logs for browser capture; never synthesize test output."""
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from check_results import validate as validate_fixtures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from check_packet_trace import validate as validate_packets

run = Path(sys.argv[1]).resolve()
if (run / 'result.txt').read_text().strip() != 'PASS':
    raise SystemExit('Incomplete or failed run')
expected = json.loads((run / 'cases.json').read_text())
logs = {}
for name in ('serial-packets.log', 'serial.log', 'serial-k1.log'):
    text = (run / name).read_text()
    validate_packets(text)
    if re.search(r'WARNING:|BUG:|Oops:|Kernel panic|BVERIFY FAIL|rtx_selftest: FAIL', text):
        raise SystemExit('Kernel diagnostic: ' + name)
    if not re.search(r'rtx_selftest: [1-9][0-9]* passed, 0 failed', text):
        raise SystemExit('Missing core test result')
    if text.count('RTX_CASE ') != 4 or 'RTX_RUNTIME PASS' not in text:
        raise SystemExit('Missing smoke result')
    logs[name] = text
summaries = {n: validate_fixtures(logs[n], expected) for n in ('serial.log', 'serial-k1.log')}
out = run / 'report'
out.mkdir(exist_ok=True)
css = 'body{font:19px system-ui;margin:40px;color:#173044}pre{font:15px/1.55 monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#eef3f6;padding:20px}h1{font-size:32px}td,th{padding:10px;text-align:left;border-bottom:1px solid #ccd5dc}a{color:#175cac}'


def page(name, title, body):
    (out / name).write_text(f'<!doctype html><meta charset="utf-8"><title>{title}</title><style>{css}</style><h1>{title}</h1>' + body)


def excerpt(name, keys):
    rows = [f'{i:04d} | {line}' for i, line in enumerate(logs[name].splitlines(), 1)
            if any(k in line for k in keys)]
    digest = hashlib.sha256((run / name).read_bytes()).hexdigest()
    return f'<h2>{name}</h2><pre>{html.escape(chr(10).join(rows))}</pre><p>SHA-256: {digest}</p>'

rows = []
for mode in ('rb-off', 'rb-on', 'hybrid-off', 'hybrid-on'):
    result = (run / mode / 'result.txt').read_text().strip()
    if result != 'PASS':
        raise SystemExit('Build failed: ' + mode)
    rows.append(f'<tr><td>{mode}</td><td>IPv4 + IPv6 build</td><td>{result}</td></tr>')
for name, summary in summaries.items():
    rows.append(f'<tr><td>{name}</td><td>Kernel fixture assertions</td><td>{sum(v["checks"] for v in summary.values())} passed</td></tr>')
meta = 'Source HEAD: ' + (run / 'source-sha.txt').read_text().strip() + '\nSource includes saved source.patch\nEvidence: ' + run.name + '\n' + (run / 'environment.txt').read_text()
page('index.html', 'Issue #14 — build and runtime evidence', '<table><tr><th>Configuration / log</th><th>Check</th><th>Result</th></tr>' + ''.join(rows) + '</table><p>Each of three boots passed the core selftest, four TCP smoke cases, and SACK/TLP/RTO packet checks. Fixture results use a disposable instrumented kernel. No performance claim.</p><pre>' + html.escape(meta) + '</pre><p><a href="fixtures.html">Fixture evidence</a> · <a href="packets.html">Packet evidence</a></p>')
page('fixtures.html', 'Issue #14 — actual LIST split and promotion', '<p>Filtered real serial logs with original line numbers. Accounting checks execute actual tcp_fragment() on fixture sockets.</p>' + ''.join(excerpt(n, ('BVERIFY hybrid ', 'BVERIFY_CASE hybrid ', 'BVERIFY_TOTAL', 'rtx_selftest:', 'RTX_RUNTIME PASS')) for n in summaries))
page('packets.html', 'Issue #14 — production kernel packet regression', '<p>No fixture overlay in this boot. kprobe/kretprobe trace confirms RTX split and successful return. Core selftests still run at boot.</p>' + excerpt('serial-packets.log', ('rtx_selftest:', 'RTX_CASE ', 'RTX_PACKET ', 'rtx_fragment:', 'rtx_result:', 'RTX_RUNTIME PASS')))
print(out)
