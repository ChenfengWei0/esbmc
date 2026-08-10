#!/usr/bin/env python3
"""Build and optionally launch the RQ1 high-memory OOM rerun queue."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path


DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_OUT = Path("/tmp/veriput_rq1_oom_highmem.tsv")
DEFAULT_REMOTE_ESBMC = Path("/home/administrator/veriput_esbmc/repo")
DEFAULT_REMOTE_VERIPUT = Path("/home/administrator/VeriPUT")
OOM_BUCKET = "OOM_OR_MEMORY_PRESSURE"


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _row_is_oom(row: dict) -> bool:
    fields = (
        row.get("result_bucket"),
        row.get("bucket"),
        row.get("category"),
        row.get("failure_reason"),
        row.get("status"),
    )
    haystack = " ".join(str(item or "") for item in fields).lower()
    return OOM_BUCKET.lower() in haystack or any(
        needle in haystack for needle in (
            "out of memory",
            "std::bad_alloc",
            "cannot allocate memory",
            "memory exhausted",
            "killed-over-rss",
            "oom",
        ))


def repair_ticket_rows(path: Path) -> list[dict]:
    out = []
    for row in _jsonl(path):
        if not _row_is_oom(row):
            continue
        bench = str(row.get("bench") or "")
        subject = str(row.get("subject") or "")
        if not bench or not subject:
            continue
        out.append({
            "bench": bench,
            "subject": subject,
            "category": OOM_BUCKET,
            "source": "repair_ticket",
            "source_file": str(path),
        })
    return out


def canonical_result_rows(root: Path) -> list[dict]:
    out = []
    for result in sorted(root.glob("*/subjects/*/result.json")):
        path_s = str(result)
        if any(marker in path_s for marker in (
                ".redo.", ".superseded.", ".adopted_from_", ".incomplete.")):
            continue
        rel = result.relative_to(root).parts
        if len(rel) < 4 or rel[1] != "subjects":
            continue
        doc = _json(result)
        row = doc.get("row") if isinstance(doc.get("row"), dict) else doc
        put = doc.get("put") if isinstance(doc.get("put"), dict) else {}
        if not (_row_is_oom(row) or _row_is_oom(put) or _row_is_oom(doc)):
            continue
        out.append({
            "bench": rel[0],
            "subject": rel[2],
            "category": OOM_BUCKET,
            "source": "canonical_result",
            "source_file": str(result),
        })
    return out


def build_rows(repair_tickets: Path, results_root: Path, limit: int) -> list[dict]:
    dedup = {}
    for row in repair_ticket_rows(repair_tickets) + canonical_result_rows(results_root):
        dedup[(row["bench"], row["subject"])] = row
    rows = sorted(dedup.values(), key=lambda r: (r["bench"], r["subject"]))
    if limit > 0:
        rows = rows[:limit]
    return rows


def write_tsv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["bench", "subject", "category"],
            delimiter="\t",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "bench": row["bench"],
                "subject": row["subject"],
                "category": row["category"],
            })


def remote_command(args: argparse.Namespace) -> list[str]:
    return [
        "python3",
        "notes/coverage/scripts/rq1_remote_pump.py",
        "--host",
        args.host,
        "--remote-esbmc",
        str(args.remote_esbmc),
        "--remote-veriput",
        str(args.remote_veriput),
        "--tsv",
        str(args.out),
        "--category",
        OOM_BUCKET,
        "--limit",
        str(args.limit if args.limit > 0 else len(build_rows(
            args.repair_tickets, args.results_root, args.limit))),
        "--timeout",
        str(args.timeout),
        "--esbmc-run-timeout",
        str(args.esbmc_run_timeout),
        "--wrapper-grace",
        str(args.wrapper_grace),
        "--forge-timeout",
        str(args.forge_timeout),
        "--memlimit-gib",
        str(args.high_memlimit_gib),
        "--case-parallel",
        "1",
        "--max-case-parallel",
        "1",
        "--reserve-mem-gib",
        str(args.reserve_mem_gib),
        "--remote-min-mem-gib",
        str(args.remote_min_mem_gib),
        "--esbmc-rss-limit-gib",
        str(args.high_esbmc_rss_limit_gib),
        "--loop",
        "--start-pull-loop",
        "--pull-sleep-s",
        str(args.pull_sleep_s),
        "--pull-postprocess-every",
        "1",
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair-tickets",
                        type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--host", default="invmut-w2")
    parser.add_argument("--remote-esbmc", type=Path, default=DEFAULT_REMOTE_ESBMC)
    parser.add_argument("--remote-veriput", type=Path, default=DEFAULT_REMOTE_VERIPUT)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--esbmc-run-timeout", type=int, default=60)
    parser.add_argument("--wrapper-grace", type=int, default=60)
    parser.add_argument("--forge-timeout", type=int, default=180)
    parser.add_argument("--high-memlimit-gib", type=float, default=10.0)
    parser.add_argument("--high-esbmc-rss-limit-gib", type=float, default=12.0)
    parser.add_argument("--reserve-mem-gib", type=float, default=4.0)
    parser.add_argument("--remote-min-mem-gib", type=float, default=8.0)
    parser.add_argument("--pull-sleep-s", type=int, default=20)
    parser.add_argument("--launch-remote", action="store_true")
    args = parser.parse_args()

    rows = build_rows(args.repair_tickets, args.results_root, args.limit)
    write_tsv(args.out, rows)
    cmd = remote_command(args)
    doc = {
        "schema": "veriput-rq1-oom-highmem-queue/v1",
        "out": str(args.out),
        "count": len(rows),
        "rows": rows[:80],
        "launch_remote": bool(args.launch_remote),
        "remote_command": cmd,
        "rule": (
            "Only subjects with explicit OOM/memory-pressure evidence enter "
            "this queue. Non-OOM subjects keep the normal memory budget."),
    }
    if args.launch_remote and rows:
        proc = subprocess.run(cmd, check=False)
        doc["launch_returncode"] = proc.returncode
    print(json.dumps(doc, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
