"""Validate the complete boot matrix and export one row per boot and workload."""
import argparse
from array import array
from collections import defaultdict
import csv
import gzip
import json
from pathlib import Path
import random
import re
import sys
from statistics import median, quantiles
import tarfile

from matrix import batch_plan, long_plan, plan, require, sha

CONDITIONS = ("clean", "impaired", "no-sack")
WORKLOADS = ("bulk", "rr")
MICRO = {"append_empty", "append_short", "append_k16", "append_long",
         "split_linear", "split_paged", "split_mixed", "split_clone", "split_atomic"}

def write_csv(path, rows):
    with path.open("w", newline="") as out:
        columns = list(dict.fromkeys(k for row in rows for k in row))
        writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

def text_from(archive, name):
    return archive.extractfile("results/" + name).read().decode()

def summarize_stats(text, k):
    counts, buckets = {}, defaultdict(dict)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            key, val = parts
            counts[key] = int(val)
        elif len(parts) == 3:
            key, interval, val = parts
            buckets[key][interval] = int(val)
    inserts = sum(counts[n] for n in ("insert_append","insert_ordered","insert_split"))
    acks = counts["ack_clean"]
    sizes = {key: sum(hist.values()) for key,hist in buckets.items()}
    require(sizes == {"n_ack":acks, "l_ack":acks, "n_link":inserts} and min(sizes.values())>0,
            "inconsistent N/L event counts")
    require(all(len(h)==74 for h in buckets.values()), "truncated histogram")
    n = buckets["n_ack"]
    l = buckets["l_ack"]
    require(not any(val for b,val in l.items() if "-" in b or int(b)>k), "invalid LIST length")
    full = sum(int(b)*val for b,val in l.items() if "-" not in b)
    steps = counts["find_cover_tree_steps"] + counts["find_cover_list_steps"]
    summary = dict(**counts, inserts=inserts, n_le4=sum(n[str(i)] for i in range(5)),
                   n_le16=sum(n[str(i)] for i in range(17)), n_gt64=sum(v for b,v in n.items() if "-" in b),
                   l_sum=full, l_eq16=l["16"], find_steps=steps)
    return summary, buckets

def read_micro(text, v, common):
    def records(name):
        prefix = "RTXBENCH_" + name + " "
        return [json.loads(line.split(prefix,1)[1]) for line in text.splitlines() if prefix in line]
    cfg, = records("CONFIG")
    require((cfg["mode"],cfg["debug"],cfg["stats"],cfg["cache"],cfg["tail"],cfg["hybrid"],cfg["k"])
            == ("performance",0,0,1,1,int(v.startswith("v4-")),16 if v.startswith("v4-") else -1), "wrong micro configuration")
    samples = records("SAMPLE")
    keys = {(name,i) for name in MICRO for i in range(32)}
    for rows in (samples,records("INPUT")):
        require(len(rows)==len(keys) and {(r["workload_id"],r["iteration"]) for r in rows}==keys, "micro key mismatch")
    values = defaultdict(list)
    overhead = []
    for sample in samples:
        require(sample["valid"]==sample["successes"]==sample["actual_ops"]==1 and
                sample["failures"]==sample["return_code"]==0 and sample["n_after"]==sample["n_before"]+1,
                "invalid micro operation")
        values[sample["workload_id"]].append(sample["elapsed_ns"])
        overhead.append(sample["timer_overhead_ns"])
    require(records("DONE")==[{"rows":288,"failed":0}], "incomplete micro fixture")
    data = re.findall(r"RTXOPS n=(\d+) hits=(\d+) lookup_ns=(\d+) unlink_ns=(\d+) error=(\d+)",text)
    for n in (4,64):
        subset=[tuple(map(int,row)) for row in data if int(row[0])==n]
        require(len(subset)==33 and all(row[1]==4096 and row[-1]==0 for row in subset), "bad lookup or unlink")
        values[f"lookup_{n}"]=[row[2]/4096 for row in subset[1:]]
        values[f"unlink_{n}"]=[row[3]/n for row in subset[1:]]
    if v.endswith("-batch-micro"):
        batches = re.findall(r"RTXAPPEND start=(\d+) count=(\d+) elapsed_ns=(\d+) error=(\d+)",text)
        for n,count in ((0,12),(64,32)):
            selected = [tuple(map(int,row)) for row in batches if int(row[0]) == n]
            require(len(selected)==33 and all(row[1]==count and row[3]==0 for row in selected),
                    "invalid batched append")
            values[f"append_batch_{n}_{count}"] = [row[2]/count for row in selected[1:]]
    return [dict(**common,operation=name,ns_per_op=median(vals),zero_samples=vals.count(0),
                 samples=len(vals),timer_ns=median(overhead),tcp_sock_bytes=cfg["tcp_sock_bytes"])
            for name,vals in values.items()]

def config_differences(root):
    builds = root / "builds"

    def read(variant):
        values = {}
        for line in (builds / variant / "kernel.config").read_text().splitlines():
            if line.startswith("CONFIG_"):
                key,value = line.split("=",1)
                values[key] = value
            elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
                values[line.split()[1]] = "n"
        return values

    pairs = [
        ("v3","v4",{"CONFIG_TCP_RTX_HYBRID","CONFIG_TCP_RTX_RECENT_MAX"}),
        ("v3-micro","v4-micro",{"CONFIG_TCP_RTX_HYBRID","CONFIG_TCP_RTX_RECENT_MAX"}),
        ("v3-batch-micro","v4-batch-micro",{"CONFIG_TCP_RTX_HYBRID","CONFIG_TCP_RTX_RECENT_MAX"}),
        ("v4","v4-k4",{"CONFIG_TCP_RTX_RECENT_MAX"}),
        ("rb-basic","rb-cache",{"CONFIG_TCP_RTX_CACHE","CONFIG_TCP_RTX_TAIL"}),
        ("rb-cache","v3",{"CONFIG_TCP_RTX_TAIL"}),
        ("v3","v3-stats",{"CONFIG_TCP_RTX_STATS"}),
        ("v4","v4-stats",{"CONFIG_TCP_RTX_STATS"}),
    ]
    rows = []
    for base,target,allowed in pairs:
        a,b=read(base),read(target)
        changed={k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}
        require(changed and changed <= allowed, f"unmatched configs: {base}, {target}: {changed}")
        for symbol in sorted(changed):
            rows.append(dict(base=base,target=target,symbol=symbol,before=a.get(symbol,"absent"),
                             after=b.get(symbol,"absent")))
    return rows

def boot_data(root):
    tcp,micro,stats,histograms,boots=[],[],[],[],[]
    ids=set()
    scheduled = [("standard",i,r,v) for i,(r,v) in enumerate(plan())]
    scheduled.extend(("long",i,r,v) for i,(r,v) in enumerate(long_plan()))
    scheduled.extend(("batch",i,r,v) for i,(r,v) in enumerate(batch_plan()))
    for phase,index,repeat,variant in scheduled:
        long_queue = phase == "long"
        common=dict(phase=phase,order=index,repeat=repeat,variant=variant)
        path=root/{"standard":"runs","long":"long-runs","batch":"batch-runs"}[phase]/f"{index:03}-{repeat:02}-{variant}"
        manifest=json.loads((path/"manifest.json").read_text())
        build=root/"builds"/variant
        require(manifest["variant"]==variant and manifest["repeat"]==repeat, str(path))
        for file,field in ((build/"Image","image_sha256"),(root/("long.gz" if long_queue else "guest.gz"),"initrd_sha256"),
                           (path/"serial.log","serial_sha256"),(path/"results.tar.gz","archive_sha256")):
            require(sha(file)==manifest[field],f"hash mismatch {file}")
        require(manifest["boot_id"] not in ids,"duplicate boot")
        ids.add(manifest["boot_id"])
        serial=(path/"serial.log").read_text()
        require("RTX16 PASS" in serial and not re.search(r"RTX16 FAIL|WARNING:|BUG:|Oops:|Kernel panic",serial),
                f"kernel or guest failure {path}")
        with tarfile.open(path/"results.tar.gz") as archive:
            require(text_from(archive,"boot-id").strip()==manifest["boot_id"],"boot identity mismatch")
            tcp_slab=next(l.split() for l in text_from(archive,"slabinfo").splitlines() if l.startswith("TCP "))
            boots.append(dict(**common,boot_id=manifest["boot_id"],revision=(build/"revision").read_text().strip(),
                              config_sha256=sha(build/"kernel.config"),kernel_sha256=manifest["image_sha256"],
                              initrd_sha256=manifest["initrd_sha256"],serial_sha256=manifest["serial_sha256"],
                              raw_sha256=manifest["archive_sha256"],tcp_slab_object_bytes=int(tcp_slab[3]),
                              pmu_probe=text_from(archive,"pmu.json"),host_load_1m=manifest["load_average"][0]))
            if variant.endswith("-micro"):
                micro.extend(read_micro(serial,variant,common))
                continue
            metrics=json.loads(text_from(archive,"metrics.json"))
            expected = {("long","bulk")} if long_queue else {(c,w) for c in CONDITIONS for w in WORKLOADS}
            require(len(metrics)==len(expected) and {(r["condition"],r["workload"]) for r in metrics}==expected,
                    "invalid TCP matrix")
            for row in metrics:
                prefix=row["condition"]+"-"+row["workload"]
                sender=json.loads(text_from(archive,prefix+"/client.json"))
                receiver=json.loads(text_from(archive,prefix+"/server.json"))
                require(sender["bytes"]==receiver["bytes"]>0 and receiver["elapsed_ns"]>0 and
                        receiver["vm_busy_ticks"]>=0, "receiver verification failed")
                expected_goodput = receiver["bytes"] * 8000 / receiver["elapsed_ns"]
                expected_cpu = receiver["vm_busy_ticks"] * 1e9 / receiver["clk_tck"] / receiver["bytes"]
                require(row["goodput_mbps"] == expected_goodput and row["vm_busy_ns_per_byte"] == expected_cpu,
                        "metrics differ from receiver data")
                if row["workload"]=="rr":
                    require(row["p99_us"] == sender["p99_us"] and row["p50_us"] == sender["p50_us"],
                            "latency differs from sender data")
                    require(sender["requests"]*64==sender["bytes"] and
                            archive.getmember("results/"+prefix+"/latency-ns.u64").size==sender["requests"]*8,
                            "RR sample count mismatch")
                    timings = array("Q")
                    timings.frombytes(archive.extractfile("results/"+prefix+"/latency-ns.u64").read())
                    if sys.byteorder != "little":
                        timings.byteswap()
                    ordered = sorted(timings)
                    for percentile in (50, 99):
                        index = (len(ordered)*percentile+99)//100-1
                        require(ordered[index]/1000 == row[f"p{percentile}_us"], "raw RR percentile mismatch")
                tcp.append(dict(**common,**row,received_bytes=receiver["bytes"],busy_ticks=receiver["vm_busy_ticks"],
                                clk_tck=receiver["clk_tck"],elapsed_ns=receiver["elapsed_ns"],requests=sender["requests"]))
                if variant.endswith("-stats"):
                    scope=dict(**common,condition=row["condition"],workload=row["workload"])
                    values,hist=summarize_stats(text_from(archive,prefix+".stats"),16 if variant=="v4-stats" else 0)
                    stats.append(dict(**scope,**values))
                    histograms.append(dict(**scope,histograms=hist))
    return tcp,micro,stats,histograms,boots

def paired(tcp):
    metrics=("goodput_mbps","vm_busy_ns_per_byte","p50_us","p99_us","sender_peak_wmem_queued_bytes")
    pairs=[("v3","v4"),("rb-basic","rb-cache"),("rb-cache","v3"),("v4","v4-k4"),
           ("v0","v1"),("v3","v3-stats"),("v4","v4-stats")]
    rows=[]
    for base,target in pairs:
        for condition in (*CONDITIONS, "long"):
            for workload in WORKLOADS:
                group=[r for r in tcp if r["condition"]==condition and r["workload"]==workload]
                if not group:
                    continue
                for metric in metrics:
                    if metric not in group[0]:
                        continue
                    sides=[{r["repeat"]:r[metric] for r in group if r["variant"]==v} for v in (base,target)]
                    reps=sorted(sides[0].keys() & sides[1].keys())
                    if not reps:
                        continue
                    a,b=([side[i] for i in reps] for side in sides)
                    ratios=[100*(y/x-1) for x,y in zip(a,b) if x>0]
                    rng=random.Random(16)
                    ci=sorted(median(rng.choices(ratios,k=len(ratios))) for _ in range(10000)) if ratios else []
                    qa, qb = (quantiles(series, n=4, method="inclusive") for series in (a, b))
                    rows.append(dict(base=base,target=target,condition=condition,workload=workload,metric=metric,
                                     pairs=len(reps),usable_pairs=len(ratios),base_median=median(a),target_median=median(b),
                                     base_q1=qa[0],base_q3=qa[2],target_q1=qb[0],target_q3=qb[2],
                                     base_min=min(a),base_max=max(a),target_min=min(b),target_max=max(b),
                                     delta_pct=median(ratios) if ratios else None,
                                     ci_low=ci[249] if ci else None,ci_high=ci[9749] if ci else None))
    return rows

if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    tcp,micro,stats,hist,boots=boot_data(args.root)
    differences=config_differences(args.root)
    args.output.mkdir(parents=True,exist_ok=True)
    for name,rows in [("config-diffs",differences),("tcp",tcp),("operations",micro),("queue-counters",stats),("boots",boots),("comparisons",paired(tcp))]:
        write_csv(args.output/(name+".csv"),rows)
    with gzip.GzipFile(str(args.output/"queue-histograms.json.gz"),"wb",mtime=0) as out:
        out.write(json.dumps(hist,separators=(",",":")).encode())
    print(f"Validated {len(boots)} independent boots, {len(tcp)} TCP workloads, {len(micro)} operation summaries")
