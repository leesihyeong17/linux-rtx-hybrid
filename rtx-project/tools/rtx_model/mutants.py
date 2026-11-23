#!/usr/bin/env python3
"""Mutation check: does the model test reject a broken tcp_rtx.h?

Each mutant is one small, plausible bug applied to a copy of the real
include/net/tcp_rtx.h. Every mutant is built with build.sh -H and run on
the scenarios and on random seeds. A mutant that passes everything means
the test has a blind spot. Only mutants that built and ran count as
KILLED; a mutant that does not build is an error of the check itself.

  mutants.py --repo REPO --work DIR [--seeds 0:200]
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rtx_model  # noqa: E402

MUTANTS = [
    ("domain_first_l_is_tree",
     "domain: first(L) itself is classified as TREE",
     "if (first && !before(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(first)->seq))",
     "if (first && after(TCP_SKB_CB(skb)->seq, TCP_SKB_CB(first)->seq))"),
    ("unlink_keeps_tree_last",
     "unlink: rtx_tree_last not moved when the tree tail is removed",
     "\tif (tp->rtx_tree_last == skb)\n\t\ttp->rtx_tree_last = skb_rb_prev(skb);\n",
     ""),
    ("limit_k_minus_1",
     "promotion: L capped at K-1 instead of K",
     "if (tp->rtx_recent_len <= CONFIG_TCP_RTX_RECENT_MAX)",
     "if (tp->rtx_recent_len < CONFIG_TCP_RTX_RECENT_MAX)"),
    ("prev_of_first_l_null",
     "rtx_prev(first(L)) returns NULL instead of the tree tail",
     "\t\t\ttcp_sk(sk)->rtx_tree_last :",
     "\t\t\tNULL :"),
    ("next_of_tree_last_null",
     "rtx_next(tree tail) does not continue into L",
     "return skb_rb_next(skb) ?: __rtx_list_first(sk);",
     "return skb_rb_next(skb);"),
    ("cover_list_end_inclusive",
     "find_cover in L treats end_seq as covered",
     "\t\t\tif (before(seq, TCP_SKB_CB(skb)->end_seq)) {",
     "\t\t\tif (!after(seq, TCP_SKB_CB(skb)->end_seq)) {"),
    ("cover_list_from_boundary_minus",
     "find_cover searches L only when q is past first(L).seq",
     "if (skb && !before(seq, TCP_SKB_CB(skb)->seq)) {\n\t\tif (list_search)",
     "if (skb && after(seq, TCP_SKB_CB(skb)->seq)) {\n\t\tif (list_search)"),
    ("ordered_list_position",
     "ordered insert into L uses the wrong comparison",
     "\t\t\tif (before(TCP_SKB_CB(skb)->seq,\n\t\t\t\t   TCP_SKB_CB(pos)->seq)) {",
     "\t\t\tif (after(TCP_SKB_CB(skb)->seq,\n\t\t\t\t   TCP_SKB_CB(pos)->seq)) {"),
    ("split_list_before_skb",
     "split in L links buff before skb",
     "list_add(&buff->list, &tok->skb->list);",
     "list_add_tail(&buff->list, &tok->skb->list);"),
    ("split_ignores_token_domain",
     "split commit always inserts buff into T",
     "\tif (tok->domain == RTX_DOMAIN_LIST) {\n\t\tlist_add(",
     "\tif (0) {\n\t\tlist_add("),
    ("shift_recomputes_domain",
     "shift commit re-derives the domain after seq changed (no token)",
     "__rtx_unlink(sk, tok->skb, tok->domain);",
     "__rtx_unlink(sk, tok->skb, __rtx_domain(sk, tok->skb));"),
    ("replace_keeps_tree_first",
     "replace: rtx_tree_first not updated",
     "\tif (tcp_sk(sk)->rtx_tree_first == old)\n\t\ttcp_sk(sk)->rtx_tree_first = new;\n",
     ""),
    ("tree_insert_first_cache",
     "tree insert: rtx_tree_first only set on an empty tree",
     "\tif (!tcp_sk(sk)->rtx_tree_first ||\n\t    before(",
     "\tif (!tcp_sk(sk)->rtx_tree_first && before("),
    # PR #25 (CONFIG_TCP_RTX_TAIL) only; N/A on headers without it.
    ("tail_append_keeps_first",
     "TAIL append: rtx_tree_first not set when the tree was empty",
     "\tif (!tp->rtx_tree_first)\n\t\ttp->rtx_tree_first = skb;\n\ttp->rtx_tree_last = skb;",
     "\ttp->rtx_tree_last = skb;"),
    ("tail_append_left_child",
     "TAIL append links under rb_left of the tree tail",
     "struct rb_node **link = parent ? &parent->rb_right :",
     "struct rb_node **link = parent ? &parent->rb_left :"),
    # PR #33 (STATS v2) only.
    ("nbucket_exact_edge",
     "STATS v2: N == 64 falls into the first log bucket",
     "\tif (n <= RTX_N_EXACT_MAX)\n\t\treturn n;",
     "\tif (n < RTX_N_EXACT_MAX)\n\t\treturn n;"),
    ("empty_ignores_list",
     "rtx_empty() looks only at the tree",
     "\tif (!list_empty(&tcp_sk(sk)->rtx_recent))\n\t\treturn false;\n",
     ""),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--seeds", default="0:200")
    args = ap.parse_args()

    src = open(os.path.join(args.repo, "include/net/tcp_rtx.h")).read()
    os.makedirs(args.work, exist_ok=True)
    survived = 0
    build_failed = 0
    print("mutants=%d seeds=%s" % (len(MUTANTS), args.seeds))
    for name, desc, old, new in MUTANTS:
        if src.count(old) == 0:
            print("MUTANT %-32s N/A (code not in this header)" % name)
            continue
        if src.count(old) != 1:
            print("MUTANT %-32s ERROR pattern found %d times" % (
                name, src.count(old)))
            survived += 1
            continue
        hdr = os.path.join(args.work, name + ".h")
        with open(hdr, "w") as fp:
            fp.write(src.replace(old, new))
        out = os.path.join(args.work, name)
        env = dict(os.environ, RTX_MODEL_KS="1 4", RTX_MODEL_PLAIN_KS="4")
        b = subprocess.run([os.path.join(HERE, "build.sh"), "-r", args.repo,
                            "-H", hdr, out], capture_output=True, text=True,
                           env=env)
        if b.returncode:
            # A mutant that does not compile was never run, so it says
            # nothing about the harness. Report it and fail the check.
            build_failed += 1
            err = (b.stderr or b.stdout).strip().splitlines()
            print("MUTANT %-32s BUILD_FAILED rc=%d: %s" % (
                name, b.returncode, " | ".join(err[-3:])[:200]))
            continue
        bins = rtx_model.list_bins(out)
        ks = sorted({k for _, k in bins},
                    key=lambda k: -1 if k is None else k)
        fails = []
        for label, tr in rtx_model.scenarios(ks):
            fails += rtx_model.run_trace(out, bins, tr, label, None)
        n_scen = len(fails)
        for s in rtx_model.parse_seeds(args.seeds):
            fails += rtx_model.run_trace(out, bins,
                                         rtx_model.gen_random(s, ks),
                                         "seed%d" % s, None)
        if fails:
            by_bin = sorted({f["bin"] for f in fails})
            first = fails[0]
            how = first.get("actual") or first.get("error")
            print("MUTANT %-32s KILLED scen_fail=%d total_fail=%d bins=%s"
                  " first=%s/%s: %s" % (name, n_scen, len(fails),
                                        ",".join(by_bin), first["trace"],
                                        first["bin"], str(how)[:90].rstrip()))
        else:
            survived += 1
            print("MUTANT %-32s SURVIVED (%s)" % (name, desc))
    print("survived=%d build_failed=%d" % (survived, build_failed))
    return 1 if survived or build_failed else 0


if __name__ == "__main__":
    sys.exit(main())
