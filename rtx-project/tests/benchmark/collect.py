#!/usr/bin/env python3
"""Validate kernel evidence before exposing samples to the D collector."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics

WORKLOADS = {
    'append_empty': (0, 4000), 'append_short': (4, 4000),
    'append_k16': (16, 4000), 'append_long': (64, 4000),
    'split_linear': (16, 4000), 'split_paged': (16, 0),
    'split_mixed': (16, 2000), 'split_clone': (16, 4000),
    'split_atomic': (16, 4000),
}
DETAIL_METRICS = ('rtx_split_prepare_ns', 'rtx_split_commit_ns',
                  'rtx_split_window_ns', 'rtx_split_core_ns')
BOOL_INPUTS = ('has_frag_list', 'head_frag', 'skb_cloned', 'skb_shared',
               'skb_header_cloned', 'nohdr')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_serial(text):
    require(not re.search(r'RTXBENCH_ERROR|WARNING:|BUG:|Oops:|Kernel panic|RTX_RUNTIME (FAIL|TIMEOUT)', text),
            'Kernel or benchmark error marker')
    records = {'CONFIG': [], 'INPUT': {}, 'SAMPLE': {}, 'DETAIL': {}, 'DONE': []}
    for line in text.splitlines():
        match = re.search(r'RTXBENCH_(\w+) (.*)$', line)
        if not match:
            continue
        kind, raw = match.groups()
        require(kind in records, 'Unknown record kind')
        record = json.loads(raw)
        if kind in ('CONFIG', 'DONE'):
            records[kind].append(record)
        else:
            key = (record['workload_id'], record['iteration'])
            require(key not in records[kind], 'Duplicate ' + kind)
            records[kind][key] = record
    require(len(records['CONFIG']) == len(records['DONE']) == 1, 'Missing/duplicate boundaries')
    config, done = records['CONFIG'][0], records['DONE'][0]
    require(config['protocol'] == 1 and config['contract_version'] == '0.2', 'Protocol mismatch')
    require(config['mode'] in ('performance', 'observation'), 'Unknown mode')
    require(not config['debug'] and not config['stats'], 'DEBUG/STATS enabled')
    require(all(config[k] in (0, 1) for k in ('hybrid', 'cache', 'tail')), 'Invalid config flags')
    require(not (config['hybrid'] or config['tail']) or config['cache'], 'Missing cache dependency')
    require(config['k'] >= 1 if config['hybrid'] else config['k'] == -1, 'Invalid K')
    require(1 <= config['iterations'] <= 10000 and 0 <= config['warmup_ms'] <= 60000,
            'Invalid execution parameters')
    workloads = dict(WORKLOADS)
    if config['mode'] == 'observation':
        workloads['split_alloc_failure'] = (16, 4000)
    expected = {(w, i) for w in workloads for i in range(config['iterations'])}
    require(config['workloads'] == len(workloads), 'Workload count mismatch')
    require(set(records['INPUT']) == set(records['SAMPLE']) == expected, 'Missing/unexpected samples')
    require(set(records['DETAIL']) == (expected if config['mode'] == 'observation' else set()),
            'Detail mode mismatch')
    require(done == {'rows': len(expected), 'failed': 0}, 'Incomplete/failed benchmark')
    require('RTX_RUNTIME PASS' in text, 'Missing guest smoke completion')
    rows = []
    for key in sorted(expected):
        name, iteration = key
        n, headlen = workloads[name]
        inp, sample = records['INPUT'][key], dict(records['SAMPLE'][key])
        split, failure = name.startswith('split_'), name == 'split_alloc_failure'
        require(all(inp[k] in (0, 1) for k in BOOL_INPUTS), 'Invalid boolean input')
        require(inp['skb_len_bytes'] == 4000 and inp['skb_headlen_bytes'] == headlen
                and inp['skb_data_len_bytes'] == 4000 - headlen, 'Input length mismatch')
        require(inp['nr_frags'] == bool(4000 - headlen) and
                inp['frag_sizes_bytes'] == ([4000 - headlen] if headlen < 4000 else []),
                'Fragment layout mismatch')
        require(inp['data_layout'] == ('linear' if headlen == 4000 else 'paged' if not headlen else 'mixed'),
                'Layout name mismatch')
        require(not inp['has_frag_list'] and not inp['skb_shared'] and
                inp['skb_cloned'] == (name == 'split_clone') and inp['nohdr'], 'Sharing state mismatch')
        require(inp['skb_users'] == 1 and inp['dataref_raw'] > 0, 'Invalid reference state')
        require(inp['gfp_flags'] == ['GFP_ATOMIC' if name == 'split_atomic' else 'GFP_KERNEL']
                and re.fullmatch(r'0x[0-9a-f]+', inp['gfp_mask']), 'GFP mismatch')
        require(sample['valid'] == 1 and sample['attempts'] == sample['actual_ops'] == 1,
                'Failed fixture or no executed work')
        require(sample['operation'] == ('split' if split else 'append'), 'Wrong operation')
        require(sample['successes'] == int(not failure) and sample['failures'] == int(failure)
                and sample['return_code'] == (-12 if failure else 0), 'Outcome mismatch')
        require(sample['n_before'] == n and sample['n_after'] == n + (not failure), 'N mismatch')
        expected_l = [min(n, config['k']), min(n + (not failure), config['k'])] if config['hybrid'] else [-1, -1]
        require([sample['l_before'], sample['l_after']] == expected_l, 'L mismatch')
        require(type(sample['elapsed_ns']) is int and sample['elapsed_ns'] >= 0 and
                type(sample['timer_overhead_ns']) is int and sample['timer_overhead_ns'] >= 0,
                'Invalid timing')
        sample['input'] = {k: bool(v) if k in BOOL_INPUTS else v for k, v in inp.items()
                           if k not in ('workload_id', 'iteration')}
        sample['input'].update(mss_bytes=1000, gso_segs=4, split_position_bytes=1500 if split else None,
                               split_queue_index=n // 2 if split else None,
                               call_context='process', socket_serialization='lock_sock')
        sample.update(valid=True, outcome='expected_failure' if failure else 'success',
                      failure_injection='allocation_call_boundary' if failure else None,
                      metric='tcp_fragment_total_ns' if split else 'rtx_append_total_ns',
                      unit='ns/op', value=sample['elapsed_ns'], k=config['k'] if config['hybrid'] else None,
                      cycles=None, cycles_reason='not_measured', null_reasons={})
        if not config['hybrid']:
            sample['l_before'] = sample['l_after'] = None
            sample['null_reasons'].update(k='not_applicable', l_before='not_applicable', l_after='not_applicable')
        sample['detail'] = {metric: None for metric in DETAIL_METRICS}
        sample['promotions'] = sample['tail_links'] = None
        if config['mode'] == 'observation':
            detail = records['DETAIL'][key]
            calls = int(split and not failure)
            require(detail['prepares'] == detail['commits'] == calls, 'Split was not reached as expected')
            promote = int(config['hybrid'] and not failure and n >= config['k'])
            tail = int(bool(config['tail']) and (promote if config['hybrid'] else not split))
            require(detail['promotions'] == promote and detail['tail_links'] == tail, 'Wrong Core path')
            if calls:
                require(all(type(detail[m]) is int and detail[m] >= 0 for m in DETAIL_METRICS), 'Bad detail timing')
                require(detail['rtx_split_core_ns'] == detail['rtx_split_prepare_ns'] + detail['rtx_split_commit_ns'],
                        'Core sum mismatch')
                require(sample['elapsed_ns'] >= detail['rtx_split_window_ns'] >= detail['rtx_split_core_ns'],
                        'Nested timing scope mismatch')
                sample['detail'] = {m: detail[m] for m in DETAIL_METRICS}
            else:
                require(all(detail[m] == -1 for m in DETAIL_METRICS), 'Timing of unexecuted scope')
            sample.update(promotions=detail['promotions'], tail_links=detail['tail_links'])
        for m, value in sample['detail'].items():
            if value is None:
                sample['null_reasons'][m] = ('not_collected' if config['mode'] == 'performance'
                                             else 'not_reached' if split else 'not_applicable')
        if config['mode'] == 'performance':
            sample['null_reasons'].update(promotions='not_collected', tail_links='not_collected')
        rows.append(sample)
    return config, rows


def collect(run):
    manifest_path = run / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    try:
        require(manifest['exit_code'] == 0, 'Guest process failed')
        config, rows = parse_serial((run / 'logs/serial.log').read_text())
        require(manifest['mode'] == config['mode'], 'Manifest mode mismatch')
        flags = {'rb-basic': (0, 0, 0), 'rb-cache': (0, 1, 0), 'rb-tail': (0, 1, 1),
                 'hybrid-ordered': (1, 1, 0), 'hybrid-tail': (1, 1, 1)}
        require(tuple(config[k] for k in ('hybrid', 'cache', 'tail')) == flags[manifest['variant']],
                'Variant label does not match executed code')
        for name in ('iterations', 'warmup_ms'):
            require(manifest[name] == config[name], 'Manifest parameter mismatch')
        kernel_config = (run / 'configs/kernel.config').read_bytes()
        require(hashlib.sha256(kernel_config).hexdigest() == manifest['config_sha256'], 'Config hash mismatch')
        for field, symbol in (('cache', 'CACHE'), ('tail', 'TAIL'), ('hybrid', 'HYBRID'),
                              ('debug', 'DEBUG'), ('stats', 'STATS')):
            require(bool(config[field]) == (f'CONFIG_TCP_RTX_{symbol}=y' in kernel_config.decode()),
                    'Runtime/build config mismatch: ' + field)
        if config['hybrid']:
            require(f'CONFIG_TCP_RTX_RECENT_MAX={config["k"]}\n' in kernel_config.decode(), 'Config K mismatch')
        for row in rows:
            row.update({k: manifest[k] for k in ('schema_version', 'contract_version', 'run_id', 'variant',
                       'source_sha', 'source_patch_sha256', 'config_sha256', 'benchmark_sha256',
                       'environment_id', 'host_arch', 'target_arch', 'execution_mode',
                       'mode', 'repeat', 'order', 'exit_code')})
            row.update(log_path='logs/serial.log', observation_run_id=manifest['observation_run_id'],
                       observation_link_reason=manifest['observation_link_reason'])
        (run / 'samples.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
        with (run / 'summary.csv').open('w', newline='') as output:
            fields = ['workload_id', 'metric', 'unit', 'operations', 'median', 'q1', 'q3', 'min', 'max']
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for workload in sorted({r['workload_id'] for r in rows}):
                group = [r for r in rows if r['workload_id'] == workload]
                values = sorted(r['value'] for r in group)
                quartiles = statistics.quantiles(values, n=4, method='inclusive') if len(values) > 1 else values * 3
                writer.writerow(dict(workload_id=workload, metric=group[0]['metric'], unit='ns/op',
                                     operations=len(values), median=statistics.median(values), q1=quartiles[0],
                                     q3=quartiles[2], min=min(values), max=max(values)))
        manifest.update(valid=True, kernel_benchmark=config, operations=len(rows),
                        inference='pilot_only_no_tcp_performance_claim')
        (run / 'result.txt').write_text('PASS\n')
    except (ValueError, KeyError, TypeError) as exc:
        manifest.update(valid=False, exclusion_reason=str(exc))
        (run / 'result.txt').write_text('FAIL\n')
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        paths = sorted(p for p in run.rglob('*') if p.is_file() and p.name != 'SHA256SUMS')
        (run / 'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest() + '  ' +
                                               str(p.relative_to(run)) + '\n' for p in paths))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    collect(parser.parse_args().run)
