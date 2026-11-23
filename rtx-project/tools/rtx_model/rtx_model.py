#!/usr/bin/env python3
"""Reference model and trace runner for the rtx adapter (design 11.1).

The reference model is independent of the kernel code:
  - the queue is a Python list sorted by 64-bit extended sequence numbers
    (no serial-number arithmetic, no rbtree, no list_head);
  - the T/L split follows the rules written in design.md 3.1/5.1/5.5,
    tracked per K (K=None means the RB backend: everything is TREE).

The same trace is run on harness binaries built by build.sh from the real
include/net/tcp_rtx.h, and every output line is compared. The harness
also checks rbtree/list structure after each command on its own.

Usage:
  rtx_model.py scenarios --bins DIR
  rtx_model.py random --bins DIR --seeds 0:2000 [--fail-dir DIR]
  rtx_model.py replay --bins DIR TRACE
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter

MASK = 0xFFFFFFFF
RB = None  # K value meaning "RB backend"


def u32(x):
    return x & MASK


class Mismatch(Exception):
    pass


class Trace:
    """Builds a command trace and the expected output for every K at once."""

    def __init__(self, ks, isn):
        self.ks = list(ks)
        self.nodes = []          # [id, seq, end], sorted by seq (ext ints)
        self.dom = {k: {} for k in self.ks}
        self.cmds = []
        self.expect = []         # str, or ("D", prefix, {k: domstr})
        self.next_id = 1
        self.snd_nxt = isn
        self.cov = {k: Counter() for k in self.ks}
        self.stat = {k: Counter() for k in self.ks}   # CONFIG_TCP_RTX_STATS
        self.n_link = Counter()                       # N after each link
        self.ops = Counter()

    # ---- helpers ----
    def _idx(self, nid):
        for i, n in enumerate(self.nodes):
            if n[0] == nid:
                return i
        raise KeyError(nid)

    def _emit(self, cmd, out):
        self.cmds.append(cmd)
        self.expect.append(out)

    def _new(self, seq, end):
        assert seq < end, (seq, end)
        nid = self.next_id
        self.next_id += 1
        self._emit("NEW %d %d %d" % (nid, u32(seq), u32(end)), "OK")
        return nid

    def _insert_sorted(self, node):
        i = 0
        while i < len(self.nodes) and self.nodes[i][1] < node[1]:
            i += 1
        self.nodes.insert(i, node)
        return i

    def _first_l(self, k):
        for n in self.nodes:
            if self.dom[k][n[0]] == "L":
                return n
        return None

    def _limit(self, k):
        if k is RB:
            return
        while sum(1 for n in self.nodes if self.dom[k][n[0]] == "L") > k:
            n = self._first_l(k)
            self.dom[k][n[0]] = "T"
            self.cov[k]["promote"] += 1
            self.stat[k]["promote"] += 1

    def _check(self):
        prev = None
        for n in self.nodes:
            assert n[1] < n[2]
            if prev:
                assert prev[2] <= n[1], (prev, n)
            prev = n
        for k in self.ks:
            doms = "".join(self.dom[k][n[0]] for n in self.nodes)
            assert "LT" not in doms, doms                    # T before L
            if k is not RB:
                assert doms.count("L") <= k
            else:
                assert "L" not in doms
        if self.nodes:
            assert self.nodes[-1][2] - self.nodes[0][1] < 1 << 30

    def _bump_snd_nxt(self, end):
        self.snd_nxt = max(self.snd_nxt, end)

    def _linked(self, kind):
        for k in self.ks:
            self.stat[k][kind] += 1
        self.n_link[len(self.nodes)] += 1

    def _unlinked(self, nid):
        for k in self.ks:
            d = self.dom[k][nid]
            self.stat[k]["unlink_tree" if d == "T" else "unlink_list"] += 1

    # ---- operations ----
    def append(self, length, gap=0):
        seq = (self.nodes[-1][2] if self.nodes else self.snd_nxt) + gap
        seq = max(seq, self.snd_nxt) if not self.nodes else seq
        nid = self._new(seq, seq + length)
        self.nodes.append([nid, seq, seq + length])
        self._linked("append")
        for k in self.ks:
            self.dom[k][nid] = "T" if k is RB else "L"
            self._limit(k)
            self.cov[k]["wrap_inside"] += u32(seq) > u32(seq + length - 1)
        self._bump_snd_nxt(seq + length)
        self._emit("APPEND %d" % nid, "OK")
        self.ops["append"] += 1
        self._post()
        return nid

    def ordered(self, seq, end):
        for n in self.nodes:
            assert n[2] <= seq or end <= n[1], "overlap"
        nid = self._new(seq, end)
        node = [nid, seq, end]
        first_l = {k: (self._first_l(k) if k is not RB else None)
                   for k in self.ks}
        self._insert_sorted(node)
        self._linked("ordered")
        for k in self.ks:
            fl = first_l[k]
            if fl is not None and seq >= fl[1]:
                self.dom[k][nid] = "L"
                self.cov[k]["ord_list"] += 1
                self._limit(k)
            else:
                self.dom[k][nid] = "T"
                self.cov[k]["ord_tree"] += 1
        self._bump_snd_nxt(end)
        self._emit("ORD %d" % nid, "OK")
        self.ops["ordered"] += 1
        self._post()
        return nid

    def unlink(self, nid):
        i = self._idx(nid)
        for k in self.ks:
            self.cov[k]["unlink_" + self.dom[k][nid]] += 1
            if k is not RB and self._first_l(k) is self.nodes[i]:
                self.cov[k]["unlink_first_l"] += 1
        self._unlinked(nid)
        del self.nodes[i]
        self._emit("UNLINK %d" % nid, "OK")
        self.ops["unlink"] += 1
        self._post()

    def ack(self, p):
        removed = 0
        while self.nodes and self.nodes[0][2] <= p:
            nid = self.nodes[0][0]
            for k in self.ks:
                self.cov[k]["ack_" + self.dom[k][nid]] += 1
            self._unlinked(nid)
            del self.nodes[0]
            removed += 1
        if self.nodes and p > self.nodes[0][1]:
            self.nodes[0][1] = p
            for k in self.ks:
                self.cov[k]["trim_" + self.dom[k][self.nodes[0][0]]] += 1
        if not self.nodes:
            for k in self.ks:
                self.cov[k]["ack_to_empty"] += removed > 0
        self._emit("ACK %d" % u32(p), "ACK removed=%d" % removed)
        self.ops["ack"] += 1
        self._post()

    def split(self, nid, mid):
        i = self._idx(nid)
        node = self.nodes[i]
        assert node[1] < mid < node[2]
        bid = self.next_id
        self.next_id += 1
        end = node[2]
        node[2] = mid
        self.nodes.insert(i + 1, [bid, mid, end])
        self._linked("split")
        for k in self.ks:
            d = self.dom[k][nid]
            self.dom[k][bid] = d
            self.cov[k]["split_" + d] += 1
            before = self.cov[k]["promote"]
            self._limit(k)
            if self.cov[k]["promote"] > before:
                self.cov[k]["split_promote"] += 1
                if self.dom[k][nid] == "T" and d == "L":
                    self.cov[k]["split_promote_self"] += 1
            if u32(mid) < u32(node[1]) or u32(end - 1) < u32(mid):
                self.cov[k]["split_wrap"] += 1
        self._emit("SPLIT %d %d %d" % (nid, bid, u32(mid)), "OK")
        self.ops["split"] += 1
        self._post()
        return bid

    def shift(self, prev_id, cur_id, n):
        i = self._idx(cur_id)
        assert i > 0 and self.nodes[i - 1][0] == prev_id
        prev, cur = self.nodes[i - 1], self.nodes[i]
        assert prev[2] == cur[1] and 0 < n <= cur[2] - cur[1]
        for k in self.ks:
            pair = self.dom[k][prev_id] + self.dom[k][cur_id]
            kind = "merge_" if n == cur[2] - cur[1] else "shift_"
            self.cov[k][kind + pair] += 1
        prev[2] += n
        cur[1] += n
        gone = cur[1] == cur[2]
        if gone:
            self._unlinked(cur_id)
            del self.nodes[i]
        self._emit("SHIFT %d %d %d" % (prev_id, cur_id, n),
                   "SHIFT gone=%d" % gone)
        self.ops["merge" if gone else "shift"] += 1
        self._post()

    def replace(self, nid):
        i = self._idx(nid)
        new = self.next_id
        self.next_id += 1
        self.nodes[i] = [new, self.nodes[i][1], self.nodes[i][2]]
        for k in self.ks:
            d = self.dom[k][nid]
            self.dom[k][new] = d
            self.cov[k]["replace_" + d] += 1
        self._emit("REPLACE %d %d" % (nid, new), "OK")
        self.ops["replace"] += 1
        self._post()
        return new

    def purge(self):
        n = len(self.nodes)
        for node in self.nodes:
            self._unlinked(node[0])
        self.nodes = []
        self._emit("PURGE", "PURGE removed=%d" % n)
        self.ops["purge"] += 1
        self._post()

    def cover(self, q):
        res = -1
        for n in self.nodes:
            if n[1] <= q < n[2]:
                res = n[0]
        for k in self.ks:
            self.stat[k]["find_cover"] += 1
            if res == -1:
                self.cov[k]["cover_miss"] += 1
            else:
                self.cov[k]["cover_" + self.dom[k][res]] += 1
        self._emit("COVER %d" % u32(q), "C %d" % res)
        self.ops["cover"] += 1

    def overlap(self, s, e):
        assert s < e
        res = -1
        for n in self.nodes:
            if n[2] > s and n[1] < e:
                res = n[0]
                break
        self._emit("OVERLAP %d %d" % (u32(s), u32(e)), "O %d" % res)
        self.ops["overlap"] += 1

    def nbucket(self, n):
        """STATS v2 histogram bucket of N, checked against the spec."""
        self._emit("NBUCKET %d" % n, ("B", n))

    def dump(self):
        ids = [n[0] for n in self.nodes]
        first = ids[0] if ids else -1
        last = ids[-1] if ids else -1
        prefix = "D first=%d last=%d fwd=%s bwd=%s dom=" % (
            first, last, ",".join(map(str, ids)),
            ",".join(map(str, reversed(ids))))
        doms = {k: "".join(self.dom[k][i] for i in ids) for k in self.ks}
        for k in self.ks:
            d = doms[k]
            if "T" in d and "L" in d:
                self.cov[k]["state_both"] += 1
            elif "L" in d:
                self.cov[k]["state_list_only"] += 1
            elif "T" in d:
                self.cov[k]["state_tree_only"] += 1
            else:
                self.cov[k]["state_empty"] += 1
            if k is not RB and d.count("L") == k:
                self.cov[k]["state_list_full"] += 1
            # T/L boundary across the u32 wrap
            if "T" in d and "L" in d:
                j = d.index("L")
                if u32(self.nodes[j - 1][1]) > u32(self.nodes[j][1]):
                    self.cov[k]["boundary_wrap"] += 1
        self._emit("DUMP", ("D", prefix, doms, " empty=%d" % (not ids)))

    def _post(self):
        self._check()
        self.dump()

    # ---- output ----
    def text(self):
        return "".join(c + "\n" for c in self.cmds)

    def expected_for(self, k, nbucket=False):
        out = []
        for e in self.expect:
            if isinstance(e, tuple) and e[0] == "B":
                out.append("B %d" % hist_bucket(e[1], "v2")
                           if nbucket else "B -")
            elif isinstance(e, tuple):
                out.append(e[1] + e[2][k] + e[3])
            else:
                out.append(e)
        return out

    def expected_stats(self, k, hist):
        """STAT and HIST lines printed at END by a STATS build."""
        st = self.stat[k]
        line = "STAT " + " ".join("%s=%d" % (n, st[n]) for n in (
            "append", "ordered", "split", "unlink_tree", "unlink_list",
            "promote", "find_cover"))
        buckets = Counter()
        for n, c in self.n_link.items():
            buckets[hist_bucket(n, hist)] += c
        h = ",".join("%d:%d" % kv for kv in sorted(buckets.items()))
        return [line, "HIST" + (" " + h if h else "")]


def hist_bucket(n, hist):
    """N histogram bucket, written from the documented bucket ranges.

    v1 (main): bucket b holds N in [2^b, 2^(b+1)-1], 16 buckets, last open.
    v2 (PR #33): one bucket per N for 0..64, then [65,127], [128,255], ...
    up to an open [16384,) bucket (74 buckets).
    """
    if hist == "v1":
        b, lo = 0, 1
        while n >= lo * 2 and b < 15:
            b, lo = b + 1, lo * 2
        return b
    if n <= 64:
        return n
    idx, hi = 65, 127
    while n > hi and idx < 73:
        idx, hi = idx + 1, hi * 2 + 1
    return idx


# ---------------------------------------------------------------------------
# binaries

_EXPECTED = {}


def load_expected(bin_dir):
    """Return {name: {key: value}} from build.sh's expected.conf."""
    path = os.path.join(bin_dir, "expected.conf")
    if path not in _EXPECTED:
        if not os.path.exists(path):
            raise SystemExit("%s missing: rebuild with build.sh" % path)
        table = {}
        with open(path) as fp:
            for line in fp:
                name, *pairs = line.split()
                table[name] = dict(p.split("=", 1) for p in pairs)
        _EXPECTED[path] = table
    return _EXPECTED[path]


def list_bins(bin_dir):
    """Return [(name, k)] for harness binaries in bin_dir."""
    expected = load_expected(bin_dir)
    bins = []
    for name in sorted(os.listdir(bin_dir)):
        m = re.fullmatch(r"rb(-plain|-cache|-tail)?|hyb-k(\d+)(-plain|-tail)?",
                         name)
        if not m or not os.access(os.path.join(bin_dir, name), os.X_OK):
            continue
        if name not in expected:
            raise SystemExit("%s: no build configuration in expected.conf"
                             % name)
        bins.append((name, int(m.group(2)) if m.group(2) else RB))
    missing = sorted(set(expected) - {n for n, _ in bins})
    if missing:
        raise SystemExit("built but missing in %s: %s" % (
            bin_dir, ", ".join(missing)))
    if not bins:
        raise SystemExit("no harness binaries in %s (run build.sh)" % bin_dir)
    return bins


def run_bin(path, text):
    p = subprocess.run([path], input=text, capture_output=True, text=True,
                       timeout=300)
    return p.returncode, p.stdout.splitlines(), p.stderr


def check_output(name, k, rc, lines, tr, stderr, want=None):
    """Return None if OK, else a description of the first mismatch.

    want is the configuration build.sh used for this binary. The binary's
    CONF line must match it before its output is compared.
    """
    if not lines or not lines[0].startswith("CONF "):
        return ("no output (rc=%d%s) %s" % (
            rc, " signal %d" % -rc if rc < 0 else "",
            stderr.strip()[:200])).rstrip()
    conf = lines[0]
    got = dict(p.split("=", 1) for p in conf.split()[1:])
    if want is None:
        want = {"hybrid": "0", "k": "0"} if k is RB else {
            "hybrid": "1", "k": str(k)}
    bad = ["%s=%s (want %s)" % (key, got.get(key), val)
           for key, val in sorted(want.items()) if got.get(key) != val]
    if bad:
        return "CONF mismatch: %s: %s" % (", ".join(bad), conf)
    hist = re.search(r"hist=(\w+)", conf).group(1)
    expected = tr.expected_for(k, "nbucket=1" in conf)
    if hist != "none":
        expected = expected + tr.expected_stats(k, hist)
    body = lines[1:]
    for i, exp in enumerate(expected):
        got = body[i] if i < len(body) else "<missing rc=%d %s>" % (
            rc, stderr.strip()[:200])
        if got != exp:
            return {"index": i, "expected": exp, "actual": got}
    tail = body[len(expected):]
    if rc != 0 or len(tail) != 1 or not re.fullmatch(
            r"END cmds=%d warns=0" % len(tr.cmds), tail[0]):
        return {"index": len(expected), "expected": "END",
                "actual": " | ".join(tail[:3]) + " rc=%d" % rc}
    return None


def run_trace(bin_dir, bins, tr, label, fail_dir):
    """Run one trace on all binaries. Return list of failure dicts."""
    text = tr.text()
    expected = load_expected(bin_dir)
    fails = []
    for name, k in bins:
        if k not in tr.ks:
            continue
        rc, lines, err = run_bin(os.path.join(bin_dir, name), text)
        res = check_output(name, k, rc, lines, tr, err, expected[name])
        if res is None:
            continue
        f = {"trace": label, "bin": name}
        if isinstance(res, dict):
            f.update(res)
            f["command"] = tr.cmds[res["index"]] if res["index"] < len(
                tr.cmds) else "END/STAT"
        else:
            f["error"] = res
        fails.append(f)
        if fail_dir:
            os.makedirs(fail_dir, exist_ok=True)
            base = os.path.join(fail_dir, "%s_%s" % (label, name))
            with open(base + ".trace", "w") as fp:
                fp.write("# %s on %s\n" % (label, name))
                fp.write(text)
            with open(base + ".json", "w") as fp:
                json.dump(f, fp, indent=1)
    return fails


# ---------------------------------------------------------------------------
# random traces

PROFILES = {
    # name: (max skbs, ops)
    "small": (6, 250),
    "medium": (40, 400),
    "large": (300, 1200),
}


def gen_random(seed, ks):
    rnd = random.Random(seed)
    profile = ("small", "small", "medium", "medium", "large")[seed % 5]
    max_n, n_ops = PROFILES[profile]
    if rnd.random() < 0.5:
        isn = (1 << 32) - rnd.randint(0, 40 * 1448)     # wrap soon
    else:
        isn = rnd.randint(0, MASK)
    # extended sequence space starts at isn; keep it >= 2^32 so the
    # "prefix" inserts below never go negative.
    isn += 1 << 32
    tr = Trace(ks, isn)
    tr.profile = profile

    def rand_len():
        r = rnd.random()
        if r < 0.15:
            return 1                         # SYN/FIN-like 1-seq skb
        if r < 0.75:
            return rnd.choice((536, 1448, 2896, 4344))
        return rnd.randint(2, 64 * 1448)     # GSO-sized

    def pick_q():
        if not tr.nodes or rnd.random() < 0.1:
            return tr.snd_nxt + rnd.randint(-3000, 3000)
        n = rnd.choice(tr.nodes)
        return rnd.choice((n[1] - 1, n[1], n[1] + 1, n[2] - 1, n[2],
                           n[2] + 1, rnd.randint(n[1], n[2] - 1),
                           tr.nodes[0][1] - rnd.randint(1, 5000),
                           tr.nodes[-1][2] + rnd.randint(0, 5000)))

    for _ in range(n_ops):
        n = len(tr.nodes)
        w = {
            "append": 30 if n < max_n else 2,
            "ack": 12 if n < max_n else 40,
            "split": 12, "shift": 12, "unlink": 4, "ordered": 8,
            "replace": 4, "purge": 0.4,
        }
        op = rnd.choices(list(w), weights=list(w.values()))[0]
        if op == "append":
            gap = rnd.randint(1, 3000) if rnd.random() < 0.05 else 0
            tr.append(rand_len(), gap)
        elif op == "ack" and tr.nodes:
            head, tail = tr.nodes[0], tr.nodes[-1]
            m = rnd.random()
            if m < 0.4:     # node boundary
                p = rnd.choice(tr.nodes[:8])[2]
            elif m < 0.7:   # inside the head area
                p = rnd.randint(head[1], min(tail[2], head[2] + 3000))
            elif m < 0.8:   # everything
                p = tail[2]
            elif m < 0.9:   # stale ack
                p = head[1] - rnd.randint(0, 2000)
            else:
                p = rnd.randint(head[1], tail[2])
            tr.ack(p)
        elif op == "split":
            cands = [x for x in tr.nodes if x[2] - x[1] >= 2]
            if cands:
                x = rnd.choice(cands)
                mid = rnd.choice((x[1] + 1, x[2] - 1,
                                  rnd.randint(x[1] + 1, x[2] - 1)))
                tr.split(x[0], mid)
        elif op == "shift":
            pairs = [(a, b) for a, b in zip(tr.nodes, tr.nodes[1:])
                     if a[2] == b[1]]
            if pairs:
                a, b = rnd.choice(pairs)
                ln = b[2] - b[1]
                amount = ln if rnd.random() < 0.35 else (
                    rnd.randint(1, ln - 1) if ln > 1 else 1)
                tr.shift(a[0], b[0], amount)
        elif op == "unlink" and tr.nodes:
            tr.unlink(rnd.choice(tr.nodes)[0])
        elif op == "ordered":
            gaps = []
            for a, b in zip(tr.nodes, tr.nodes[1:]):
                if b[1] > a[2]:
                    gaps.append((a[2], b[1]))
            if tr.nodes:
                h = tr.nodes[0][1]
                gaps.append((h - rnd.randint(1, 3000), h))     # prefix
                t = tr.nodes[-1][2]
                gaps.append((t, t + rnd.randint(1, 3000)))     # past tail
            else:
                gaps.append((tr.snd_nxt, tr.snd_nxt + 3000))
            s, e = rnd.choice(gaps)
            a = rnd.randint(s, e - 1)
            b = rnd.randint(a + 1, e)
            if rnd.random() < 0.5:
                a, b = s, e if e - s < 70000 else s + 1448
            tr.ordered(a, b)
        elif op == "replace" and tr.nodes:
            tr.replace(rnd.choice(tr.nodes)[0])
        elif op == "purge":
            tr.purge()
        for _ in range(2):
            tr.cover(pick_q())
        s = pick_q()
        tr.overlap(s, s + rnd.randint(1, 6000))
    return tr


# ---------------------------------------------------------------------------
# scenarios (design 11.1 required cases)

def scenarios(ks):
    """Yield (name, Trace) for the hand-written required cases."""
    W = (1 << 32) + (1 << 32) - 3000     # 3000 bytes before a u32 wrap
    S = (1 << 32) + 1000

    def mk(isn=S):
        return Trace(ks, isn)

    def all_covers(t):
        for n in list(t.nodes):
            for q in (n[1] - 1, n[1], n[1] + 1, n[2] - 1, n[2], n[2] + 1):
                t.cover(q)
            t.overlap(n[1] - 5, n[1] + 1)
            t.overlap(n[2] - 1, n[2] + 5)
        if t.nodes:
            t.cover(t.nodes[0][1] - 10000)
            t.cover(t.nodes[-1][2] + 10000)
            t.overlap(t.nodes[0][1], t.nodes[-1][2])

    # STATS v2 bucket edges (no queue needed): exact 0..64, then powers
    # of two up to the open [16384,) bucket
    t = mk()
    for n in list(range(0, 66)) + [127, 128, 255, 256, 511, 512, 1023, 1024,
                                   2047, 2048, 4095, 4096, 8191, 8192,
                                   16383, 16384, 65535, 1 << 20, MASK]:
        t.nbucket(n)
    yield "stats_v2_buckets", t

    # empty queue
    t = mk()
    t.dump()
    t.cover(S)
    t.overlap(S, S + 10)
    t.ack(S + 5)
    t.purge()
    yield "empty", t

    # one skb, remove, reuse
    t = mk()
    a = t.append(100)
    all_covers(t)
    t.unlink(a)
    t.append(100)
    t.ack(t.nodes[-1][2])
    t.append(1)
    yield "single_reuse", t

    # K-1, K, K+1 ... up to 2K+2 appends (every K at once)
    t = mk()
    for _ in range(2 * max(k or 1 for k in ks) + 2):
        t.append(1448)
        t.cover(t.nodes[-1][1])
        t.cover(t.nodes[0][1])
    all_covers(t)
    yield "append_promote", t

    # tree only: ordered inserts into an empty queue go to T
    t = mk()
    t.ordered(S + 1000, S + 2000)
    t.ordered(S, S + 1)                  # SYN-like prefix
    t.ordered(S + 1, S + 1000)           # SYN data fills the hole
    all_covers(t)
    t.append(500)                        # first L after a tree-only queue
    t.ordered(t.nodes[-1][2] + 10, t.nodes[-1][2] + 20)   # past tail, L
    t.ordered(t.nodes[-2][2], t.nodes[-2][2] + 10)        # hole in L
    all_covers(t)
    yield "tree_only_then_list", t

    # ack: whole list, tree head/middle/tail, boundary nodes
    t = mk()
    ids = [t.append(1000) for _ in range(40)]
    t.ack(t.nodes[0][1] + 500)           # trim tree head
    t.ack(t.nodes[2][2])                 # remove 3 tree skbs
    t.unlink(t.nodes[5][0])              # tree middle (hole)
    all_covers(t)
    for k in ks:                         # remove T tail and first L per K
        if k is RB:
            continue
        fl = t._first_l(k)
        if fl is None:
            continue
        i = t.nodes.index(fl)
        if i > 0:
            t.unlink(t.nodes[i - 1][0])
        t.unlink(fl[0])
    all_covers(t)
    t.ack(t.nodes[-1][2])                # everything
    t.append(10)
    yield "ack_and_boundary_unlink", t
    del ids

    # split cases of B_DOMAIN_IMPLEMENTATION.md 6.1: T head/middle/tail,
    # L head/middle/tail, head of a full L (promotes the original skb),
    # both skbs next to the T/L boundary
    t = mk()
    for _ in range(20):
        t.append(1448)
    t.split(t.nodes[0][0], t.nodes[0][1] + 1)        # tree head
    t.split(t.nodes[5][0], t.nodes[5][1] + 700)      # tree middle
    t.split(t.nodes[-1][0], t.nodes[-1][2] - 1)      # list tail
    t.split(t.nodes[-3][0], t.nodes[-3][1] + 100)    # list middle
    for k in ks:
        if k is RB:
            continue
        fl = t._first_l(k)
        if fl and t.nodes.index(fl) > 0:
            tl = t.nodes[t.nodes.index(fl) - 1]          # tree tail
            if tl[2] - tl[1] >= 2:
                t.split(tl[0], tl[2] - 1)
        fl = t._first_l(k)
        if fl and fl[2] - fl[1] >= 3:
            b = t.split(fl[0], fl[1] + 1)                # first L
            bn = t.nodes[t._idx(b)]
            t.split(b, bn[2] - 1)
    all_covers(t)
    yield "split", t

    # partial shift and full merge on both sides of the boundary
    t = mk()
    for _ in range(24):
        t.append(1000)
    for k in sorted((k for k in ks if k is not RB), reverse=True):
        fl = t._first_l(k)
        if not fl:
            continue
        i = t.nodes.index(fl)
        if i == 0:
            continue
        prev = t.nodes[i - 1]
        t.shift(prev[0], fl[0], 300)                 # T -> L partial
        all_covers(t)
        fl = t._first_l(k)
        i = t.nodes.index(fl)
        prev = t.nodes[i - 1]
        t.shift(prev[0], fl[0], fl[2] - fl[1])       # T eats first L
        all_covers(t)
        fl = t._first_l(k)
        if fl and t.nodes.index(fl) + 1 < len(t.nodes):
            nxt = t.nodes[t.nodes.index(fl) + 1]
            t.shift(fl[0], nxt[0], nxt[2] - nxt[1])  # L eats L
    t.shift(t.nodes[0][0], t.nodes[1][0], 1)         # T -> T
    t.shift(t.nodes[0][0], t.nodes[1][0], t.nodes[1][2] - t.nodes[1][1])
    all_covers(t)
    yield "shift_merge", t

    # wraparound: inside a skb, between skbs, at the T/L boundary
    for off in (0, 1, 500, 1447, 1448, 3000):
        t = mk(W + off - 1448 * 2)
        for _ in range(36):
            t.append(1448)
            t.cover(t.nodes[-1][1])
        all_covers(t)
        t.split(t.nodes[1][0], t.nodes[1][1] + 724)
        mid = [n for n in t.nodes if u32(n[1]) > u32(n[2] - 1)]
        if mid:
            t.split(mid[0][0], (1 << 33))            # split exactly at wrap
        all_covers(t)
        t.ack((1 << 33) + 10)
        all_covers(t)
        yield "wrap_%d" % off, t

    # replace at every position
    t = mk()
    for _ in range(20):
        t.append(100)
    for i in (0, 5, len(t.nodes) - 1):
        t.replace(t.nodes[i][0])
    for k in ks:
        if k is RB:
            continue
        fl = t._first_l(k)
        if fl:
            j = t.nodes.index(fl)
            t.replace(fl[0])
            if j:
                t.replace(t.nodes[j - 1][0])
    all_covers(t)
    yield "replace", t

    # purge then reuse, repeated
    t = mk()
    for r in range(4):
        for _ in range(5 + 7 * r):
            t.append(700)
        t.split(t.nodes[1][0], t.nodes[1][1] + 1)
        t.purge()
        t.append(1)
        t.ack(t.nodes[-1][2])
    yield "purge_reuse", t

    # holes: cover/overlap in and across holes, ordered fill
    t = mk()
    for _ in range(30):
        t.append(1000)
    for i in (25, 20, 10, 3):
        t.unlink(t.nodes[i][0])
    all_covers(t)
    gaps = [(a[2], b[1]) for a, b in zip(t.nodes, t.nodes[1:])
            if b[1] > a[2]]
    for s, e in gaps:
        t.overlap(s, s + 1)                          # inside a hole
        t.overlap(s + 1, e + 1)                      # hole into next skb
    t.ordered(gaps[0][0] + 10, gaps[0][0] + 20)      # partial fill (T)
    t.ordered(gaps[-1][0], gaps[-1][1])              # exact fill
    all_covers(t)
    yield "holes", t


# ---------------------------------------------------------------------------
# CLI

def parse_seeds(s):
    a, _, b = s.partition(":")
    return range(int(a), int(b)) if b else range(int(a), int(a) + 1)


def summarize(results, bins, out):
    ops = Counter()
    cov = {}
    n_traces = 0
    for tr in results:
        n_traces += 1
        ops.update(tr.ops)
        for k, c in tr.cov.items():
            cov.setdefault(k, Counter()).update(c)
    out.write("traces=%d commands=%d\n" % (
        n_traces, sum(len(t.cmds) for t in results)))
    out.write("ops: %s\n" % " ".join(
        "%s=%d" % kv for kv in sorted(ops.items())))
    for name, k in bins:
        if name.endswith("-plain"):
            continue
        c = cov.get(k, Counter())
        out.write("coverage %-7s %s\n" % (name, " ".join(
            "%s=%d" % kv for kv in sorted(c.items()) if kv[1])))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("mode", choices=("scenarios", "random", "replay"))
    ap.add_argument("trace", nargs="?")
    ap.add_argument("--bins", required=True)
    ap.add_argument("--seeds", default="0:200")
    ap.add_argument("--fail-dir")
    ap.add_argument("--only", help="regex on binary names")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--max-fail", type=int, default=20)
    args = ap.parse_args()

    bins = list_bins(args.bins)
    if args.only:
        bins = [b for b in bins if re.search(args.only, b[0])]
    ks = sorted({k for _, k in bins}, key=lambda k: -1 if k is None else k)

    if args.mode == "replay":
        name = None
        for name, _ in bins:
            rc, lines, err = run_bin(os.path.join(args.bins, name),
                                     open(args.trace).read())
            print("== %s rc=%d" % (name, rc))
            print("\n".join(lines[-5:]))
        return 0

    if args.mode == "scenarios":
        traces = list(scenarios(ks))
        labels = [n for n, _ in traces]
        traces = [t for _, t in traces]
    else:
        seeds = list(parse_seeds(args.seeds))
        labels = ["seed%d" % s for s in seeds]
        traces = None

    fails = []
    done = []

    def work(i):
        tr = traces[i] if traces else gen_random(seeds[i], ks)
        return tr, run_trace(args.bins, bins, tr, labels[i], args.fail_dir)

    with cf.ThreadPoolExecutor(args.jobs) as ex:
        for tr, f in ex.map(work, range(len(labels))):
            done.append(tr)
            fails.extend(f)

    print("mode=%s bins=%s" % (args.mode, ",".join(n for n, _ in bins)))
    if args.mode == "random":
        prof = Counter(t.profile for t in done)
        print("seeds=%s profiles=%s" % (args.seeds, dict(sorted(prof.items()))))
    else:
        print("scenarios=%s" % ",".join(labels))
    summarize(done, bins, sys.stdout)
    print("failures=%d" % len(fails))
    for f in fails[:args.max_fail]:
        print("FAIL " + json.dumps(f, sort_keys=True))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
