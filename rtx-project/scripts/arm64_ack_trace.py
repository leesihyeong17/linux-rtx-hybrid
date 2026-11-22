# SPDX-License-Identifier: GPL-2.0
"""Collect per-peer ACK windows in a private tracefs instance."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
from uuid import uuid4

from build_arm64 import sha256
from verify_arm64_trim_layout import parse_offsets


def probe_fields(layout, build, identity):
    report = json.loads((layout / "layout.json").read_text())
    assembly = layout / "offsets.s"
    if (report.get("status") != "PASS" or
            any(report.get(key) != identity[key] for key in ("source_sha", "config_sha256")) or
            report.get("vmlinux_sha256") != sha256(build / "vmlinux") or
            report.get("assembly_sha256") != sha256(assembly)):
        raise ValueError("layout does not match running build")
    offsets = parse_offsets(assembly.read_text())
    if offsets != report.get("offsets"):
        raise ValueError("layout differs from compiled offsets")
    base = offsets["SKB_CB"]
    return " ".join(f"{label}=+{base + offsets[key]}(%x1):{width}"
                    for label, key, width in (("seq", "CB_SEQ", "u32"),
                                               ("end_seq", "CB_END_SEQ", "u32"),
                                               ("segs", "CB_SEGS", "u16")))


def append_probe(control, command):
    # Python's append stream seeks to EOF on open. kprobe_events uses
    # seq_lseek, which rejects SEEK_END. Each definition is one tracefs write.
    data = (command + "\n").encode("ascii")
    try:
        fd = os.open(control, os.O_WRONLY | os.O_APPEND)
        try:
            if os.write(fd, data) != len(data):
                raise OSError("short kprobe_events write")
        finally:
            os.close(fd)
    except OSError as exc:
        raise RuntimeError(f"{control.name} rejected {command!r}: {exc}") from exc


def setting(path, value):
    try:
        path.write_text(value + "\n")
    except OSError as exc:
        raise RuntimeError(f"{path} rejected {value!r}: {exc}") from exc


@contextmanager
def ack_trace(layout, build, identity, run):
    fields = probe_fields(layout, build, identity)
    root = next((p for p in (Path("/sys/kernel/tracing"), Path("/sys/kernel/debug/tracing"))
                 if (p / "kprobe_events").is_file()), None)
    if root is None:
        raise RuntimeError("tracefs kprobe_events unavailable")
    group = "rtx_" + uuid4().hex[:12]
    instance = root / "instances" / group
    control = root / "kprobe_events"
    # At tcp_rate_skb_delivered in ACK cleanup the surviving skb already
    # reflects trim and TCP's packets_out adjustment.
    sk = "sk=%x0:x64"
    skb = "skb=%x1:x64 " + fields
    probes = (
        ("p", "ack", "tcp_ack " + sk),
        ("p", "trim", "tcp_trim_head len=%x2:u32 " + sk + " " + skb),
        ("r", "ret", "tcp_trim_head ret=$retval:s32"),
        ("p", "delivered", "tcp_rate_skb_delivered " + sk + " " + skb),
        ("p", "walk", "tcp_sacktag_walk sk=%x1:x64 start=%x4:u32 end=%x5:u32"),
        ("p", "tag", "tcp_sacktag_one " + sk + " start=%x3:u32 end=%x4:u32 dup=%x5:s32"),
    )
    events = [f"{group}_{name}" for _, name, _ in probes]
    definitions = [f"{kind}:{group}/{event} {spec}"
                   for (kind, _, spec), event in zip(probes, events)]
    (run / "probes.txt").write_text("\n".join(definitions) + "\n")
    with ExitStack() as cleanup:
        instance.mkdir()
        cleanup.callback(instance.rmdir)
        tracing = instance / "tracing_on"
        setting(tracing, "0")
        # Markers and ACK events may be recorded on different CPUs.
        setting(instance / "trace_clock", "global")
        for event, definition in zip(events, definitions):
            append_probe(control, definition)
            cleanup.callback(append_probe, control, f"-:{group}/{event}")
        event_root = instance / "events" / group
        trigger = event_root / f"{group}_trim" / "trigger"
        setting(trigger, "stacktrace")
        cleanup.callback(setting, trigger, "!stacktrace")
        enable = event_root / "enable"
        setting(enable, "1")
        cleanup.callback(setting, enable, "0")
        try:
            yield instance
        finally:
            setting(tracing, "0")
            (run / "ack-trace.log").write_bytes((instance / "trace").read_bytes())
            (run / "event-pid.txt").write_bytes((instance / "set_event_pid").read_bytes())
            stats = {p.parent.name: p.read_text() for p in (instance / "per_cpu").glob("cpu*/stats")}
            (run / "trace-buffer.json").write_text(json.dumps(stats, indent=2) + "\n")
            profile = (root / "kprobe_profile").read_text().splitlines()
            own = [row for row in profile if row.split() and row.split()[0] in events]
            (run / "kprobe-profile.txt").write_text("\n".join(own) + "\n")
