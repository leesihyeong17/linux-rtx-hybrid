#!/usr/bin/env python3
"""Render saved, validated serial evidence for browser screenshots (not simulated output)."""
import hashlib
import html
import json
from pathlib import Path
import sys
from check_results import validate

run = Path(sys.argv[1]).resolve()
serial = (run / 'serial.log').read_text()
results = validate(serial, json.loads((run / 'cases.json').read_text()))
out = run / 'report'
out.mkdir(exist_ok=True)
metadata = '\n'.join([
    'Source HEAD: ' + (run / 'source-sha.txt').read_text().strip(),
    'Evidence: ' + run.name,
    'serial.log SHA-256: ' + hashlib.sha256((run / 'serial.log').read_bytes()).hexdigest(),
    'Kernel: disposable instrumented Linux; x86_64 QEMU TCG; TCP_RTX_DEBUG=y',
    (run / 'environment.txt').read_text().strip(),
])
patterns = {
    'hybrid': ('BVERIFY hybrid ',),
    'failures': ('failure-boundary=',),
    'boundaries': ('boundary base=', 'single-resplit'),
    'callers': ('write-queue caller=', 'BVERIFY caller='),
    'invariants': ('BVERIFY invariants ', 'BVERIFY RACK '),
}
for case, result in results.items():
    lines = [f'{i:04d} | {line}' for i, line in enumerate(serial.splitlines(), 1)
             if any(p in line for p in (*patterns[case], f'BVERIFY_CASE {case} ',
                                       'BVERIFY_TOTAL ', 'rtx_selftest:', 'RTX_RUNTIME PASS'))]
    body = f'''<!doctype html><meta charset="utf-8"><title>B verification — {case}</title>
<style>body{{font:20px system-ui;margin:48px;color:#122538;max-width:1400px}}h1{{font-size:36px}}pre{{font:16px/1.6 monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#eef3f6;padding:24px}}.pass{{color:#08713b;font-weight:700}}p{{line-height:1.6}}</style>
<h1>B domain / {html.escape(case)}</h1>
<p class="pass">PASS — {result['checks']} assertions, {result['failed']} failures</p>
<p>Actual QEMU serial output, filtered with original line numbers. Instrumented-kernel fixtures;
not a claim of uninstrumented end-to-end coverage or allocator exhaustion.</p>
<pre>{html.escape(chr(10).join(lines))}</pre>
<h2>Provenance</h2><pre>{html.escape(metadata)}</pre>
<p><a href="../serial.log">Complete serial.log</a> · <a href="../SHA256SUMS">Artifact checksums</a> · <a href="../instrumentation.patch">Instrumentation patch</a></p>
'''
    (out / f'{case}.html').write_text(body)
(out / 'index.html').write_text('<h1>B verification evidence</h1>' + ''.join(
    f'<p><a href="{name}.html">{name}</a></p>' for name in results))
print(out)
