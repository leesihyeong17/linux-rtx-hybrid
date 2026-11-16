#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Reject bad evidence and prove total-only overlays do not instrument TCP bodies."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from apply_overlay import apply
from collect import collect, parse_serial, WORKLOADS
from run_matrix import interval

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def records(mode='observation', hybrid=True, tail=True):
    config = dict(protocol=1, contract_version='0.2', mode=mode, iterations=1, warmup_ms=0,
                  workloads=10 if mode == 'observation' else 9, hybrid=int(hybrid), cache=1,
                  tail=int(tail), debug=0, stats=0, k=16 if hybrid else -1, tcp_sock_bytes=2240)
    result = [('CONFIG', config)]
    workloads = dict(WORKLOADS)
    if mode == 'observation':
        workloads['split_alloc_failure'] = (16, 4000)
    for name, (n, head) in workloads.items():
        split, fail = name.startswith('split_'), name == 'split_alloc_failure'
        base = dict(workload_id=name, iteration=0)
        inp = dict(base, skb_len_bytes=4000, skb_headlen_bytes=head, skb_data_len_bytes=4000-head,
                   nr_frags=int(head < 4000), frag_sizes_bytes=[4000-head] if head < 4000 else [],
                   data_layout='linear' if head == 4000 else 'paged' if head == 0 else 'mixed',
                   has_frag_list=0, head_frag=0, skb_cloned=int(name == 'split_clone'), skb_shared=0,
                   skb_header_cloned=0, nohdr=1, skb_users=1, dataref_raw=65537,
                   gfp_flags=['GFP_ATOMIC' if name == 'split_atomic' else 'GFP_KERNEL'], gfp_mask='0xcc0')
        sample = dict(base, valid=1, attempts=1, actual_ops=1, operation='split' if split else 'append',
                      successes=int(not fail), failures=int(fail), return_code=-12 if fail else 0,
                      n_before=n, n_after=n+(not fail), l_before=min(n, 16) if hybrid else -1,
                      l_after=min(n+(not fail), 16) if hybrid else -1, elapsed_ns=100, timer_overhead_ns=10)
        result.extend([('INPUT', inp), ('SAMPLE', sample)])
        if mode == 'observation':
            calls = int(split and not fail)
            promote = int(hybrid and not fail and n >= 16)
            result.append(('DETAIL', dict(base, prepares=calls, commits=calls, promotions=promote,
                          tail_links=int(tail and (promote if hybrid else not split)),
                          rtx_split_prepare_ns=5 if calls else -1, rtx_split_commit_ns=10 if calls else -1,
                          rtx_split_core_ns=15 if calls else -1, rtx_split_window_ns=30 if calls else -1)))
    result.append(('DONE', dict(rows=len(workloads), failed=0)))
    return result


def serial(items):
    return '\n'.join('RTXBENCH_' + kind + ' ' + json.dumps(row) for kind, row in items) + '\nRTX_RUNTIME PASS\n'


class EvidenceTests(unittest.TestCase):
    def test_modes_and_backends(self):
        for mode in ('performance', 'observation'):
            for hybrid in (True, False):
                for tail in (True, False):
                    with self.subTest(mode=mode, hybrid=hybrid, tail=tail):
                        _, rows = parse_serial(serial(records(mode, hybrid, tail)))
                        self.assertEqual(len(rows), 10 if mode == 'observation' else 9)
                        self.assertTrue(all(r['cycles'] is None for r in rows))
                        if not hybrid:
                            self.assertTrue(all(r['k'] is None and r['l_before'] is None for r in rows))
                        if mode == 'performance':
                            self.assertTrue(all(r['promotions'] is None for r in rows))
                        else:
                            failed = next(r for r in rows if r['failures'])
                            self.assertEqual(failed['null_reasons']['rtx_split_core_ns'], 'not_reached')

    def test_reject_corrupted_evidence(self):
        changes = [('CONFIG', None, 'debug', 1), ('CONFIG', None, 'stats', 1),
                   ('CONFIG', None, 'cache', 0), ('CONFIG', None, 'iterations', 0),
                   ('SAMPLE', 'append_empty', 'valid', 0), ('SAMPLE', 'append_empty', 'actual_ops', 0),
                   ('SAMPLE', 'append_empty', 'n_after', 0), ('SAMPLE', 'append_empty', 'elapsed_ns', -1),
                   ('INPUT', 'split_mixed', 'nr_frags', 0), ('INPUT', 'split_clone', 'skb_cloned', 0),
                   ('INPUT', 'split_atomic', 'gfp_flags', ['GFP_KERNEL']),
                   ('INPUT', 'split_linear', 'skb_shared', 1),
                   ('DETAIL', 'split_linear', 'prepares', 0), ('DETAIL', 'split_linear', 'commits', 0),
                   ('DETAIL', 'split_linear', 'rtx_split_core_ns', 16),
                   ('DETAIL', 'split_linear', 'rtx_split_window_ns', 1),
                   ('DETAIL', 'split_alloc_failure', 'rtx_split_core_ns', 0),
                   ('DETAIL', 'append_long', 'promotions', 0), ('DETAIL', 'append_long', 'tail_links', 0),
                   ('DONE', None, 'failed', 1)]
        for kind, name, field, value in changes:
            with self.subTest(kind=kind, name=name, field=field):
                items = records()
                row = next(r for k, r in items if k == kind and (name is None or r['workload_id'] == name))
                row[field] = value
                with self.assertRaises(ValueError):
                    parse_serial(serial(items))

    def test_missing_duplicate_truncated_and_kernel_failure(self):
        good = records()
        for text in (serial(good[1:]), serial(good[:-1]), serial(good + [copy.deepcopy(good[1])]),
                     serial(good).replace('RTX_RUNTIME PASS', ''), serial(good) + 'WARNING: corrupt\n',
                     serial(good) + 'RTXBENCH_SAMPLE {', serial(good[:-2] + good[-1:])):
            with self.assertRaises(ValueError):
                parse_serial(text)

    def test_no_ci_from_operations_inside_one_run(self):
        self.assertEqual(interval([1.0, 2.0], 16), (None, None))
        self.assertEqual(interval([1.0] * 10, 16), (1.0, 1.0))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'logs').mkdir()
        (self.root / 'configs').mkdir()
        (self.root / 'logs/serial.log').write_text(serial(records(hybrid=False)))
        config = b'CONFIG_TCP_RTX_CACHE=y\nCONFIG_TCP_RTX_TAIL=y\n'
        (self.root / 'configs/kernel.config').write_bytes(config)
        self.manifest = dict(schema_version=1, contract_version='0.2', run_id='test', variant='rb-tail',
                             source_sha='fixture', source_patch_sha256='fixture',
                             config_sha256=hashlib.sha256(config).hexdigest(), benchmark_sha256='fixture',
                             environment_id='fixture', host_arch='aarch64', target_arch='x86_64',
                             execution_mode='qemu_tcg', mode='observation', repeat=0, order=0, exit_code=0,
                             iterations=1, warmup_ms=0, observation_run_id=None,
                             observation_link_reason='self_observation')

    def save(self):
        (self.root / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_valid_output_preserves_environment_and_nulls(self):
        self.save()
        collect(self.root)
        rows = [json.loads(line) for line in (self.root / 'samples.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[0]['target_arch'], 'x86_64')
        self.assertEqual(rows[0]['k'], None)
        self.assertTrue((self.root / 'SHA256SUMS').exists())

    def test_reject_mislabeled_variant_and_hash(self):
        for field, value in [('variant', 'hybrid-tail'), ('config_sha256', 'bad'), ('exit_code', 124)]:
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                self.save()
                with self.assertRaises(ValueError):
                    collect(self.root)
                result = json.loads((self.root / 'manifest.json').read_text())
                self.assertFalse(result['valid'])
                self.assertTrue((self.root / 'logs/serial.log').exists())
                self.assertFalse((self.root / 'samples.jsonl').exists())
                self.manifest[field] = original


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ('net/ipv4/tcp_output.c', 'include/net/tcp_rtx.h'):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / relative).read_bytes())

    @staticmethod
    def fragment(text):
        start = text.index('int tcp_fragment(')
        return text[start:text.index('\n/* This is similar to __pskb_pull_tail()', start)]

    def test_performance_preserves_core_and_fragment(self):
        before = (self.root / 'net/ipv4/tcp_output.c').read_text()
        core = (self.root / 'include/net/tcp_rtx.h').read_bytes()
        apply(self.root, HERE, self.root / 'evidence', 'performance')
        self.assertEqual(core, (self.root / 'include/net/tcp_rtx.h').read_bytes())
        self.assertEqual(self.fragment(before), self.fragment((self.root / 'net/ipv4/tcp_output.c').read_text()))
        with self.assertRaises(ValueError):
            apply(self.root, HERE, self.root / 'evidence', 'observation')

    def test_observation_rejects_changed_anchor_before_writing(self):
        path = self.root / 'net/ipv4/tcp_output.c'
        before = path.read_text().replace('rtx_split_prepare(sk, skb, &tok)', 'changed_prepare(sk, skb, &tok)')
        path.write_text(before)
        with self.assertRaises(ValueError):
            apply(self.root, HERE, self.root / 'evidence', 'observation')
        self.assertEqual(before, path.read_text())

    def test_observation_has_single_hooks(self):
        apply(self.root, HERE, self.root / 'evidence', 'observation')
        text = (self.root / 'net/ipv4/tcp_output.c').read_text()
        self.assertEqual(text.count('bm_prepare(sk, skb, &tok)'), 1)
        self.assertEqual(text.count('bm_commit(sk, &tok, buff)'), 1)
        self.assertEqual(text.count('bm_alloc(sk, nsize, gfp, true)'), 1)


if __name__ == '__main__':
    unittest.main()
