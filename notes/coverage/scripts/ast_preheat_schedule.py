#!/usr/bin/env python3
"""Schedule VeriPUT compact-AST preheat jobs without running compilers.

Input is a `veriput-unit-manifest/v1` document.  Output is a read-only plan for
rerunning `subject_unit_manifest.py --generate-ast` on missing-AST subjects,
preferably into an external AST cache.  This script never invokes solc, Forge,
fuzzing, or ESBMC.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SUBJECT_UNIT_MANIFEST = SCRIPT_DIR / "subject_unit_manifest.py"
DEFAULT_BENCHMARK_ORDER = ("peer182", "bugfix124", "stress243")


class PreheatScheduleError(ValueError):
    """The input manifest cannot be converted into AST preheat jobs."""


def _read_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PreheatScheduleError(f"{path} is not valid JSON: {exc}") from exc


def _parse_shard(text: str):
    if not text:
        return None
    try:
        left, right = text.split("/", 1)
        idx, total = int(left), int(right)
    except (AttributeError, ValueError):
        raise PreheatScheduleError("--shard must be in i/n form")
    if total <= 0 or idx < 0 or idx >= total:
        raise PreheatScheduleError("--shard needs 0 <= i < n")
    return idx, total


def _apply_shard(items, shard):
    if shard is None:
        return items
    idx, total = shard
    return [item for pos, item in enumerate(items) if pos % total == idx]


def _benchmark_rank(benchmark: str) -> int:
    try:
        return DEFAULT_BENCHMARK_ORDER.index(benchmark)
    except ValueError:
        return len(DEFAULT_BENCHMARK_ORDER)


def _solc_source(subject: dict) -> str:
    source = subject.get("solc_bin_source")
    if source in ("explicit", "inferred", "missing"):
        return source
    if subject.get("solc_bin"):
        return "explicit"
    if subject.get("inferred_solc_bin"):
        return "inferred"
    return "missing"


def _solc_path(subject: dict) -> str | None:
    return subject.get("solc_bin") or subject.get("inferred_solc_bin")


def _subject_root_parent(subject: dict) -> str:
    root = subject.get("root")
    if not root:
        raise PreheatScheduleError("missing-ast row subject has no root")
    return str(Path(root).parent)


def _base_argv(subject: dict, ast_cache_root: str, ast_timeout: float) -> list[str]:
    return [
        sys.executable,
        str(SUBJECT_UNIT_MANIFEST),
        "--benchmark",
        subject["benchmark"],
        "--subject-root",
        _subject_root_parent(subject),
        "--subject-id",
        subject["subject_id"],
        "--ast-cache-root",
        ast_cache_root,
        "--ast-timeout",
        str(ast_timeout),
    ]


def _job(row: dict, ordinal: int, ast_cache_root: str, ast_timeout: float) -> dict:
    subject = row.get("subject") or {}
    missing = [
        name for name in ("benchmark", "subject_id", "root", "contract") if not subject.get(name)
    ]
    if missing:
        raise PreheatScheduleError(f"missing-ast row {ordinal} subject is missing: " +
                                   ", ".join(missing))
    source = _solc_source(subject)
    argv = _base_argv(subject, ast_cache_root, ast_timeout)
    argv.append("--generate-ast")
    if source == "inferred":
        argv.append("--use-inferred-solc-bin")
    return {
        "schema": "veriput-ast-preheat-job/v1",
        "job_id": subject.get("benchmark_key")
        or f"{subject['benchmark']}__{subject['subject_id']}",
        "ordinal": ordinal,
        "priority": _benchmark_rank(subject["benchmark"]) * 10 + (1 if source == "inferred" else 0),
        "priority_reason": (f"{subject['benchmark']}:{source}-solc"),
        "benchmark": subject["benchmark"],
        "subject_id": subject["subject_id"],
        "contract": subject["contract"],
        "ast_cache_root": ast_cache_root,
        "ast_timeout_s": ast_timeout,
        "solc_source": source,
        "solc_path": _solc_path(subject),
        "subject": subject,
        "target": row.get("target"),
        "preheat_argv": argv,
        "inspect_argv": _base_argv(subject, ast_cache_root, ast_timeout),
    }


def build_schedule(unit_manifest: dict,
                   *,
                   ast_cache_root: str = "",
                   ast_timeout: float | None = None,
                   shard: str = "",
                   limit: int = 0) -> dict:
    if unit_manifest.get("schema") != "veriput-unit-manifest/v1":
        raise PreheatScheduleError(f"unsupported schema {unit_manifest.get('schema')!r}; expected "
                                   "veriput-unit-manifest/v1")
    cache_root = ast_cache_root or unit_manifest.get("ast_cache_root") or ""
    if not cache_root:
        raise PreheatScheduleError("pass --ast-cache-root or use a unit manifest produced with "
                                   "--ast-cache-root; refusing to schedule prepared-subject writes")
    timeout = ast_timeout if ast_timeout is not None else (unit_manifest.get("ast_timeout_s")
                                                           or 60.0)

    jobs = []
    unschedulable = []
    skipped_by_status = Counter()
    for row_pos, row in enumerate(unit_manifest.get("subjects") or []):
        status = row.get("status")
        if status != "missing-ast":
            skipped_by_status[str(status or "<missing>")] += 1
            continue
        subject = row.get("subject") or {}
        source = _solc_source(subject)
        if source == "missing" or not _solc_path(subject):
            unschedulable.append({
                "row": row_pos,
                "reason": "missing solc path",
                "subject": subject,
                "target": row.get("target"),
            })
            continue
        jobs.append(_job(row, row_pos, cache_root, timeout))

    total_jobs = len(jobs)
    jobs.sort(key=lambda item: (item["priority"], item["ordinal"]))
    jobs = _apply_shard(jobs, _parse_shard(shard))
    if limit:
        jobs = jobs[:limit]

    by_benchmark = Counter(job["benchmark"] for job in jobs)
    by_solc_source = Counter(job["solc_source"] for job in jobs)
    return {
        "schema": "veriput-ast-preheat-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schema": unit_manifest.get("schema"),
            "benchmark": unit_manifest.get("benchmark"),
            "target_manifest": unit_manifest.get("target_manifest"),
            "ast_cache_root": unit_manifest.get("ast_cache_root"),
            "summary": unit_manifest.get("summary"),
        },
        "ast_cache_root": cache_root,
        "ast_timeout_s": timeout,
        "shard": shard or None,
        "limit": limit or None,
        "summary": {
            "jobs": len(jobs),
            "jobs_before_shard": total_jobs,
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_solc_source": dict(sorted(by_solc_source.items())),
            "skipped_by_status": dict(sorted(skipped_by_status.items())),
            "unschedulable": len(unschedulable),
        },
        "unschedulable": unschedulable,
        "jobs": jobs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("unit_manifest", help="unit manifest JSON path, or '-' for stdin")
    ap.add_argument("--ast-cache-root",
                    default="",
                    help="external AST cache root to use in generated jobs")
    ap.add_argument("--ast-timeout",
                    type=float,
                    default=None,
                    help="per-subject solc timeout for generated jobs")
    ap.add_argument("--shard", default="", help="select job positions i/n after priority sorting")
    ap.add_argument("--limit",
                    type=int,
                    default=0,
                    help="keep only the first N jobs after sharding")
    ap.add_argument("--out", default="", help="write JSON schedule here. Without it, print stdout")
    args = ap.parse_args()
    try:
        doc = build_schedule(_read_json(args.unit_manifest),
                             ast_cache_root=args.ast_cache_root,
                             ast_timeout=args.ast_timeout,
                             shard=args.shard,
                             limit=args.limit)
    except PreheatScheduleError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        s = doc["summary"]
        print(f"wrote {out}; jobs={s['jobs']} "
              f"unschedulable={s['unschedulable']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
