#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Render actual saved logs with their locations and hashes for evidence captures."""
import argparse
import hashlib
import html
import json
from pathlib import Path

from collect import parse_serial, require


def excerpt(path, markers):
    lines = [f'{i}: {line}' for i, line in enumerate(path.read_text().splitlines(), 1)
             if any(marker in line for marker in markers)]
    return ('<p>' + html.escape(str(path)) + '<br>SHA-256 ' + hashlib.sha256(path.read_bytes()).hexdigest()
            + '</p><pre>' + html.escape('\n'.join(lines)) + '</pre>')


def page(title, body):
    return '<!doctype html><meta charset="utf-8"><title>' + title + '</title>' + '''
<style>body{font:18px/1.5 system-ui;margin:40px;color:#152738}h1{font-size:30px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #bdcad5;padding:6px;text-align:left}
pre{background:#eef3f6;padding:16px;font:13px/1.5 monospace;white-space:pre-wrap;overflow-wrap:anywhere}
p{overflow-wrap:anywhere}.pass{color:#08713b;font-weight:700}</style>''' + '<h1>' + title + '</h1>' + body


def render(bench, correctness, output):
    output.mkdir(parents=True, exist_ok=True)
    table, all_rows = [], []
    runs = sorted((bench / 'runs').iterdir())
    for run in runs:
        manifest = json.loads((run / 'manifest.json').read_text())
        require(manifest['valid'] and manifest['exit_code'] == 0, 'Invalid run')
        config, rows = parse_serial((run / 'logs/serial.log').read_text())
        all_rows.extend(rows)
        table.append(f'<tr><td>{manifest["order"]}</td><td>{manifest["variant"]}</td>'
                     f'<td>{config["mode"]}</td><td>{manifest["repeat"]}</td>'
                     f'<td>{len(rows)}</td><td>{sum(r["failures"] for r in rows)}</td><td>PASS</td></tr>')
    body = (f'<p class="pass">{len(runs)} independent boots / {len(all_rows)} validated operations</p>'
            '<p>ARM64 Docker host → x86_64 QEMU TCG. Functional pilot, K=16, 3 operations per condition, '
            '0-second warmup, 2 boots per variant/mode. No TCP performance or speedup conclusion.</p>'
            '<table><tr><th>Order</th><th>Variant</th><th>Mode</th><th>Repeat</th>'
            '<th>Operations</th><th>Expected failures</th><th>Validation</th></tr>' + ''.join(table) + '</table>')
    chosen = next(r for r in runs if 'hybrid-tail-observation' in r.name)
    body += '<h2>Actual raw event excerpts</h2>' + excerpt(chosen / 'logs/serial.log',
                ('RTXBENCH_CONFIG', 'RTXBENCH_DONE', 'RTXBENCH_DETAIL {"workload_id":"append_long","iteration":0',
                 'RTXBENCH_DETAIL {"workload_id":"split_alloc_failure","iteration":0', 'RTX_RUNTIME PASS'))
    body += '<p>This is an HTML view of preserved logs with original line numbers, not a screenshot of the guest console.</p>'
    (output / 'benchmark.html').write_text(page('Issue #16 benchmark verification', body))
    body = '<p>Core cache/tail regression. Instrumented correctness runs are separate from benchmark runs.</p>'
    for name in ('serial.log', 'serial-k1.log', 'serial-rb-tail.log'):
        body += excerpt(correctness / name, ('rtx_selftest:', 'BVERIFY_TOTAL', 'RTX_RUNTIME PASS'))
    body += '<p>Original line numbers and SHA-256 accompany each excerpt. Actual loopback packet traces are preserved separately.</p>'
    (output / 'regression.html').write_text(page('Issue #16 tail correctness regression', body))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('benchmark', type=Path)
    parser.add_argument('correctness', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    render(args.benchmark.resolve(), args.correctness.resolve(), args.output.resolve())
