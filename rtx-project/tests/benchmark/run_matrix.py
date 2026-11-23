#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Run independent QEMU boots in seeded order and preserve the D contract."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import statistics
import subprocess

from collect import collect, require

VARIANTS = ('rb-basic', 'rb-cache', 'rb-tail', 'hybrid-ordered', 'hybrid-tail')
MODES = ('performance', 'observation')


def options():
    result = {name: int(os.environ.get('RTX_BENCH_' + name.upper(), default))
              for name, default in dict(iterations=32, warmup_ms=10000, repeats=10, seed=16, k=16).items()}
    require(1 <= result['iterations'] <= 10000, 'iterations must be 1..10000')
    require(0 <= result['warmup_ms'] <= 60000, 'warmup_ms must be 0..60000')
    require(1 <= result['repeats'] <= 1000, 'repeats must be 1..1000')
    require(1 <= result['k'] <= 1024, 'K must be 1..1024')
    return result


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def interval(values, seed):
    if len(values) < 10:
        return None, None
    rng = random.Random(seed)
    bootstraps = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(2000))
    return bootstraps[49], bootstraps[1949]


def summarize(root, plan, seed):
    groups = {}
    for run in plan:
        path = root / 'runs' / run['run_id']
        manifest = json.loads((path / 'manifest.json').read_text())
        require(manifest['valid'], 'Invalid run cannot enter performance summary')
        for row in csv.DictReader((path / 'summary.csv').open()):
            key = (run['variant'], run['mode'], row['workload_id'], row['metric'])
            groups.setdefault(key, []).append((run['run_id'], float(row['median'])))
    with (root / 'independent-runs.csv').open('w', newline='') as output:
        fields = ['variant', 'mode', 'workload_id', 'metric', 'unit', 'independent_runs',
                  'median_of_run_medians', 'q1', 'q3', 'ci95_low', 'ci95_high', 'ci_reason', 'run_ids']
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for key, group in sorted(groups.items()):
            values = [value for _, value in group]
            quartiles = statistics.quantiles(values, n=4, method='inclusive') if len(values) > 1 else values * 3
            low, high = interval(values, seed)
            writer.writerow(dict(zip(fields[:4], key), unit='ns/op', independent_runs=len(values),
                                 median_of_run_medians=statistics.median(values), q1=quartiles[0], q3=quartiles[2],
                                 ci95_low=low, ci95_high=high,
                                 ci_reason='insufficient_runs' if low is None else 'bootstrap_2000_run_medians',
                                 run_ids=';'.join(run_id for run_id, _ in group)))


def run_matrix(root, opts):
    rng = random.Random(opts['seed'])
    plan = []
    prefix = os.environ.get('RTX_BENCH_RUN_PREFIX', root.name)
    for repeat in range(opts['repeats']):
        groups = [(v, m) for v in VARIANTS for m in MODES]
        rng.shuffle(groups)
        for variant, mode in groups:
            plan.append(dict(run_id=f'{prefix}-{len(plan):04d}-{variant}-{mode}',
                             repeat=repeat, order=len(plan), variant=variant, mode=mode))
    write_json(root / 'plan.json', dict(options=opts, runs=plan))
    environment_id = digest(root / 'environment.txt')
    harness_hashes = {p.name: digest(p) for p in sorted((root / 'harness').iterdir()) if p.is_file()}
    write_json(root / 'harness-hashes.json', harness_hashes)
    bench_hash = hashlib.sha256(json.dumps(harness_hashes, sort_keys=True).encode()).hexdigest()
    source_sha = (root / 'source-sha.txt').read_text().strip()
    patch_hash = digest(root / 'source.patch')
    image_hash = digest(root / 'image.json')
    initrd_hash = digest(root / 'initramfs.cpio')
    for run in plan:
        dest = root / 'runs' / run['run_id']
        dest.mkdir(parents=True, exist_ok=False)
        (dest / 'configs').mkdir()
        (dest / 'logs').mkdir()
        build = root / 'builds' / (run['variant'] + '-' + run['mode'])
        for source, target in [('kernel.config', 'configs/kernel.config'), ('config.diff', 'configs/common.diff'),
                               ('source-hashes.json', 'configs/source-hashes.json'),
                               ('instrumentation.patch', 'configs/instrumentation.patch')]:
            shutil.copy2(build / source, dest / target)
        command = ['qemu-system-x86_64', '-accel', 'tcg', '-m', '512', '-smp', '1',
                   '-kernel', str(build / 'bzImage'), '-initrd', str(root / 'initramfs.cpio'),
                   '-append', f'console=ttyS0 panic=1 oops=panic rdinit=/init rtx_bench_iterations={opts["iterations"]} rtx_bench_warmup_ms={opts["warmup_ms"]}',
                   '-nographic', '-no-reboot', '-nic', 'none']
        paired = next(p['run_id'] for p in plan if p['repeat'] == run['repeat'] and
                      p['variant'] == run['variant'] and p['mode'] == 'observation')
        manifest = dict(run, schema_version=1, contract_version='0.2', source_sha=source_sha,
                        source_patch_sha256=patch_hash, benchmark_sha256=bench_hash,
                        config_sha256=digest(build / 'kernel.config'), kernel_sha256=digest(build / 'bzImage'),
                        initramfs_sha256=initrd_hash, image_metadata_sha256=image_hash,
                        environment_id=environment_id, host_arch=platform.machine(), target_arch='x86_64',
                        execution_mode='qemu_tcg', virtual_cpus=1, memory_mib=512,
                        cpu_affinity='host_scheduler_unpinned', iterations=opts['iterations'],
                        warmup_ms=opts['warmup_ms'], seed=opts['seed'], command=command,
                        timer='ktime_get_ns', pmu_status='not_requested', cycles=None,
                        cycles_reason='not_measured', network='loopback_smoke_only; QEMU nic none',
                        offload='synthetic skb GSO 1000-byte MSS; no NIC',
                        observation_run_id=paired if run['mode'] == 'performance' else None,
                        observation_link_reason=None if run['mode'] == 'performance' else 'self_observation',
                        shared_evidence='../../', build_path='../../builds/' + build.name,
                        statistics='run median; independent boot is one replicate; no cross-variant decision',
                        performance_eligibility='pilot_only_unpinned_TCG_pending_C_and_D_native')
        write_json(dest / 'manifest.json', manifest)
        write_json(dest / 'workload.json', dict(suite='fixed_state_v1', seed=opts['seed'],
                   iterations=opts['iterations'], warmup_ms=opts['warmup_ms'], independent_repeat=run['repeat'],
                   k=opts['k'] if run['variant'].startswith('hybrid') else None,
                   reset='fresh socket and skb before every operation; preparation/validation outside timer',
                   measurement='append call or entire tcp_fragment; detail in observation only',
                   failure_injection='split allocation boundary; observation only',
                   unsupported=['frag_list', 'skb_shared mutation', 'multi-fragment page arrays', 'real TCP performance']))
        timeout = 600 + (10 * opts['warmup_ms'] + 999) // 1000
        with (dest / 'logs/serial.log').open('w') as output:
            try:
                result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=timeout, check=False)
                manifest['exit_code'] = result.returncode
            except subprocess.TimeoutExpired:
                manifest['exit_code'] = 124
        write_json(dest / 'manifest.json', manifest)
        collect(dest)
        print(f'RUN {run["run_id"]} PASS', flush=True)
    summarize(root, plan, opts['seed'])
    for run in plan:
        if run['mode'] == 'performance':
            manifest = json.loads((root / 'runs' / run['run_id'] / 'manifest.json').read_text())
            peer = json.loads((root / 'runs' / manifest['observation_run_id'] / 'manifest.json').read_text())
            require(peer['valid'] and peer['config_sha256'] == manifest['config_sha256'], 'Invalid observation pair')
    (root / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + str(p.relative_to(root)) + '\n'
        for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS' and '__pycache__' not in p.parts))
    (root / 'result.txt').write_text('PASS\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path, nargs='?')
    parser.add_argument('--check-options', action='store_true')
    args = parser.parse_args()
    opts = options()
    if not args.check_options:
        parser.error('root is required') if args.root is None else run_matrix(args.root, opts)
