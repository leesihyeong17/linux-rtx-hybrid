"""Plot per-boot samples and ACK-weighted queue occupancy from the exported CSVs."""
import argparse
import csv
import gzip
import json
from pathlib import Path
from statistics import median
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

COLORS = {"v3": "#275987", "v4": "#bc5733"}
CONDITIONS = {"clean": "Clean", "impaired": "Loss + reorder", "no-sack": "SACK off", "long": "Long queue"}
METRICS = [
    ("bulk", "vm_busy_ns_per_byte", "Guest busy CPU (ns/byte)"),
    ("bulk", "goodput_mbps", "TCP goodput (Mbit/s)"),
    ("rr", "p99_us", "64-byte echo p99 (ms)"),
]

def make(tables, output):
    with (tables/"tcp.csv").open() as f:
        tcp = list(csv.DictReader(f))
    with gzip.open(tables/"queue-histograms.json.gz", "rt") as f:
        histograms = json.load(f)
    fig, axes = plt.subplots(2, 2, figsize=(12, 8.2), layout="constrained")
    for ax, (workload, metric, title) in zip(axes.flat, METRICS):
        conditions = list(CONDITIONS) if workload=="bulk" else list(CONDITIONS)[:3]
        for i, cond in enumerate(conditions):
            for variant, offset in (("v3",-.13),("v4",.13)):
                vals = [float(r[metric])/(1000 if metric=="p99_us" else 1)
                        for r in tcp if r["variant"]==variant and r["workload"]==workload and r["condition"]==cond]
                if metric=="vm_busy_ns_per_byte":
                    # symlog keeps zero tick samples visible.
                    ax.set_yscale("symlog",linthresh=.1)
                else:
                    ax.set_yscale("log")
                x=i+offset
                ax.scatter([x]*len(vals),vals,s=23,alpha=.5,color=COLORS[variant],
                           label=variant.upper() if i==0 else None)
                ax.plot([x-.075,x+.075],[median(vals)]*2,lw=2.4,color=COLORS[variant])
        ax.set_xticks(range(len(conditions)),[CONDITIONS[c] for c in conditions])
        ax.set_title(title,loc="left")
        ax.grid(axis="y",alpha=.22)
    axes[0,0].legend(loc="best",frameon=False)
    ax=axes[1,1]
    for i,condition in enumerate(CONDITIONS):
        selected=[r["histograms"]["n_ack"] for r in histograms if r["condition"]==condition
                  and r["variant"]=="v4-stats" and r["workload"]=="bulk"]
        total=sum(sum(h.values()) for h in selected)
        cdf=[sum(h[str(v)] for h in selected for v in range(n+1))/total for n in range(65)]
        ax.step(range(65),cdf,where="post",label=CONDITIONS[condition],lw=1.8,ls=["-","--",":","-."][i])
    ax.axvline(16,color="gray",alpha=.6,lw=1)
    ax.set(xlim=(0,64),ylim=(0,1.03),xlabel="Queue length (skbs)",ylabel="Share of ACK samples")
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_title("V4 bulk queue length before ACK cleanup",loc="left")
    ax.legend(fontsize=8,loc="lower right",frameon=False)
    ax.grid(axis="y",alpha=.22)
    fig.suptitle("Linux RTX   |   ARM64 V3 and V4 (K=16)",fontweight="bold",fontsize=16)
    fig.savefig(output,dpi=160)
    plt.close(fig)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("tables",type=Path)
    p.add_argument("output",type=Path)
    args=p.parse_args()
    make(args.tables,args.output)
