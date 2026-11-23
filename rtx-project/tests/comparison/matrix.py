"""Boot the pinned images serially on Apple Virtualization.framework."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import tarfile

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def plan():
    rng = random.Random(16)
    rows = []
    for repeat in range(1,11):
        pairs = [["v3","v4"],["v3-micro","v4-micro"]]
        if repeat <= 5:
            pairs += [["v0","v1"],["rb-basic","rb-cache"],["v4-k4"],["v3-stats","v4-stats"]]
        rng.shuffle(pairs)
        for group in pairs:
            if repeat % 2 == 0:
                group.reverse()
            rows.extend((repeat,v) for v in group)
    return rows

def batch_plan():
    return [(i,v) for i in range(1,6) for v in (
        ("v3-batch-micro","v4-batch-micro") if i%2 else ("v4-batch-micro","v3-batch-micro"))]

def long_plan():
    variants = ["v3", "v4", "v3-stats", "v4-stats"]
    return [(i, v) for i in range(1, 6)
            for v in (variants if i % 2 else variants[::-1])]

def boot(root, directory, variant, repeat, long_queue=False):
    directory.mkdir()
    image = root/"builds"/variant/"Image"
    guest = root/("long.gz" if long_queue else "guest.gz")
    serial = directory/"serial.log"
    args = f"console=hvc0 rdinit=/init panic=1 random.trust_cpu=on comparison={variant} rtx_bench_iterations=32 rtx_bench_warmup_ms=100"
    cmd = [str(root/"vfkit"),"--cpus","1","--memory","768","--bootloader",
           f"linux,kernel={image},initrd={guest},cmdline={args}","--device",
           f"virtio-serial,logFilePath={serial}","--device","virtio-rng"]
    record = dict(variant=variant,repeat=repeat,command=cmd,image_sha256=sha(image),
                  initrd_sha256=sha(guest),load_average=os.getloadavg())
    (directory/"manifest.json").write_text(json.dumps(record,indent=2))
    with (directory/"vm.log").open("w") as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=100,check=True)
    lines=serial.read_text(errors="replace").splitlines()
    if lines.count("RTX16 PASS") != 1 or any("Kernel panic" in s or "WARNING: CPU:" in s for s in lines):
        raise ValueError(f"{directory}: guest failed")
    payload,=[s[len("RTXDATA "):] for s in lines if s.startswith("RTXDATA ")]
    archive=directory/"results.tar.gz"
    archive.write_bytes(base64.b64decode(payload,validate=True))
    with tarfile.open(archive) as f:
        record["boot_id"]=f.extractfile("results/boot-id").read().decode().strip()
        if not variant.endswith("-micro"):
            rows=json.load(f.extractfile("results/metrics.json"))
            expected = {("long","bulk")} if long_queue else {
                (c,w) for c in ("clean","impaired","no-sack") for w in ("bulk","rr")}
            require(len(rows)==len(expected) and all(r["goodput_mbps"]>0 for r in rows), "empty TCP workload")
            require({(r["condition"],r["workload"]) for r in rows}==expected, "wrong TCP workload set")
        else:
            done=[json.loads(s.split("RTXBENCH_DONE ")[1]) for s in lines if "RTXBENCH_DONE " in s]
            require(done==[dict(rows=288,failed=0)], f"incomplete B fixture: {done}")
            samples=[json.loads(s.split("RTXBENCH_SAMPLE ")[1]) for s in lines if "RTXBENCH_SAMPLE " in s]
            require(len(samples)==288 and all(r["valid"] and r["actual_ops"]==1 and r["successes"]==1 for r in samples), "invalid benchmark operation")
            extra=[s for s in lines if "RTXOPS n=" in s]
            require(len(extra)==66 and all("hits=4096 " in s and s.endswith("error=0") for s in extra), "lookup or unlink verification failed")
            if variant.endswith("-batch-micro"):
                batch=[s for s in lines if "RTXAPPEND start=" in s]
                require(len(batch)==66 and all(s.endswith("error=0") for s in batch), "incomplete batch append")
    # Keep compressed endpoint evidence separately; avoid storing two copies.
    serial.write_text("\n".join(s for s in lines if not s.startswith("RTXDATA "))+"\n")
    record["archive_sha256"]=sha(archive)
    record["serial_sha256"]=sha(serial)
    (directory/"manifest.json").write_text(json.dumps(record,indent=2))
    print(repeat,variant,record["boot_id"],flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    parser.add_argument("runs",type=Path)
    parser.add_argument("--pilot",choices=["v3","v4-micro","v4-stats"])
    parser.add_argument("--batch",action="store_true")
    parser.add_argument("--long-queue",action="store_true")
    args=parser.parse_args()
    args.runs.mkdir(exist_ok=True)
    schedule=[(0,args.pilot)] if args.pilot else (long_plan() if args.long_queue else plan())
    if args.batch:
        schedule = batch_plan()
    for index,(repeat,variant) in enumerate(schedule):
        dest=args.runs/f"{index:03}-{repeat:02}-{variant}"
        boot(args.root.resolve(),dest.resolve(),variant,repeat,args.long_queue)
