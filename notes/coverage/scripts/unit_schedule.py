#!/usr/bin/env python3
"""Expand a VeriPUT unit manifest into concrete certification jobs.

This script is intentionally read-only with respect to benchmark inputs.  It
does not invoke solc, Forge, fuzzing, or ESBMC.  Its output is an auditable
per-unit schedule that can be inspected before scarce proof attempts are spent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CERTIFY_ALL = SCRIPT_DIR / "certify_all.py"
sys.path.insert(0, str(SCRIPT_DIR))
from veriput_path_guard import ensure_path_not_protected  # noqa: E402
from veriput_recipe import STRONG_RECIPE_VERSION, strong_certify_args  # noqa: E402


class ScheduleError(ValueError):
    """The input manifest cannot be converted into unit jobs."""


def _read_json(path: str) -> dict:
    if path == "-":
        text = sys.stdin.read()
        name = "<stdin>"
    else:
        p = Path(path)
        text = p.read_text()
        name = str(p)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScheduleError(f"{name} is not valid JSON: {exc}") from exc


def _parse_shard(text: str):
    if not text:
        return None
    try:
        left, right = text.split("/", 1)
        idx, total = int(left), int(right)
    except (AttributeError, ValueError):
        raise ScheduleError("--shard must be in i/n form")
    if total <= 0 or idx < 0 or idx >= total:
        raise ScheduleError("--shard needs 0 <= i < n")
    return idx, total


def _apply_shard(items, shard):
    if shard is None:
        return items
    idx, total = shard
    return [item for pos, item in enumerate(items) if pos % total == idx]


def _certify_argv(subject: dict, unit: str, ast_cache_root: str | None, out_path: str | None,
                  dry_run: bool) -> list[str]:
    argv = [
        sys.executable,
        str(CERTIFY_ALL),
        "--subject-dir",
        subject["root"],
        "--subject-benchmark",
        subject["benchmark"],
        "--unit",
        unit,
    ]
    if ast_cache_root:
        argv.extend(["--ast-cache-root", ast_cache_root])
    if out_path:
        argv.extend(["--out", out_path])
    argv.extend(strong_certify_args())
    if dry_run:
        argv.append("--dry-run")
    return argv


def _unit_priority(unit: str, hinted: set[str], unit_info: dict | None) -> tuple[int, str]:
    if unit in hinted:
        return 0, "target-hint"
    if not unit_info:
        return 2, "enumerated"
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    returns = int(unit_info.get("return_count") or 0)
    if mutability not in ("view", "pure"):
        return 1, "state-changing"
    if params or returns:
        return 2, "pure/view-with-interface"
    return 3, "zero-arg-view"


def _job_for_unit(row: dict, unit: str, ordinal: int, ast_cache_root: str | None,
                  out_path: str | None, unit_info: dict | None) -> dict:
    subject = dict(row["subject"])
    subject["unit"] = unit
    hinted = set((row.get("unit_hints") or {}).get("hinted_units") or [])
    priority, reason = _unit_priority(unit, hinted, unit_info)
    return {
        "schema": "veriput-unit-job/v1",
        "job_id": (f"{subject.get('benchmark_key') or subject['subject_id']}__"
                   f"{unit}"),
        "priority": priority,
        "priority_reason": reason,
        "ordinal": ordinal,
        "benchmark": subject["benchmark"],
        "subject_id": subject["subject_id"],
        "contract": subject["contract"],
        "unit": unit,
        "subject": subject,
        "target": row.get("target"),
        "unit_hints": row.get("unit_hints"),
        "unit_info": unit_info,
        "certify_argv": _certify_argv(subject, unit, ast_cache_root, out_path, dry_run=False),
        "dry_run_argv": _certify_argv(subject, unit, ast_cache_root, out_path, dry_run=True),
    }


def build_schedule(manifest: dict, *, shard: str = "", limit: int = 0, cert_out: str = "") -> dict:
    if manifest.get("schema") != "veriput-unit-manifest/v1":
        raise ScheduleError(f"unsupported schema {manifest.get('schema')!r}; expected "
                            "veriput-unit-manifest/v1")

    ast_cache_root = manifest.get("ast_cache_root") or None
    try:
        ensure_path_not_protected("--ast-cache-root", ast_cache_root)
        ensure_path_not_protected("--cert-out", cert_out)
    except ValueError as exc:
        raise ScheduleError(str(exc)) from exc
    jobs = []
    skipped_rows = []
    duplicate_jobs = []
    seen_jobs = set()
    for row_pos, row in enumerate(manifest.get("subjects") or []):
        status = row.get("status")
        if status != "ok":
            skipped_rows.append({
                "row": row_pos,
                "status": status,
                "reason": row.get("reason"),
                "subject": row.get("subject"),
                "target": row.get("target"),
            })
            continue
        subject = row.get("subject") or {}
        units = (row.get("units") or {}).get("units") or []
        infos = {
            item.get("name"): item
            for item in (row.get("units") or {}).get("unit_info") or []
            if isinstance(item, dict) and item.get("name")
        }
        missing = [
            name for name in ("root", "benchmark", "subject_id", "contract")
            if not subject.get(name)
        ]
        if missing:
            raise ScheduleError(f"ok row {row_pos} subject is missing: {', '.join(missing)}")
        for unit in units:
            key = (subject.get("benchmark"), subject.get("subject_id"), unit)
            if key in seen_jobs:
                duplicate_jobs.append({
                    "row": row_pos,
                    "unit": unit,
                    "reason": "duplicate prepared subject unit",
                    "subject": subject,
                    "target": row.get("target"),
                })
                continue
            seen_jobs.add(key)
            jobs.append(_job_for_unit(row, unit, len(jobs), ast_cache_root,
                                      cert_out or None, infos.get(unit)))

    shard_spec = _parse_shard(shard)
    total_jobs = len(jobs)
    jobs.sort(key=lambda item: (item["priority"], item["ordinal"]))
    jobs = _apply_shard(jobs, shard_spec)
    if limit:
        jobs = jobs[:limit]

    by_benchmark = Counter(job["benchmark"] for job in jobs)
    by_priority = Counter(str(job["priority"]) for job in jobs)
    skipped_by_status = Counter(str(row.get("status") or "<missing>") for row in skipped_rows)
    return {
        "schema": "veriput-unit-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schema": manifest.get("schema"),
            "benchmark": manifest.get("benchmark"),
            "target_manifest": manifest.get("target_manifest"),
            "generate_ast": manifest.get("generate_ast"),
            "ast_cache_root": ast_cache_root,
            "summary": manifest.get("summary"),
        },
        "shard": shard or None,
        "limit": limit or None,
        "cert_out": cert_out or None,
        "recipe_version": STRONG_RECIPE_VERSION,
        "summary": {
            "jobs": len(jobs),
            "jobs_before_shard": total_jobs,
            "subjects": len({(job["benchmark"], job["subject_id"])
                             for job in jobs}),
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "skipped_rows": len(skipped_rows),
            "skipped_by_status": dict(sorted(skipped_by_status.items())),
            "duplicate_jobs": len(duplicate_jobs),
        },
        "skipped_rows": skipped_rows,
        "duplicate_jobs": duplicate_jobs,
        "jobs": jobs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", help="veriput-unit-manifest/v1 JSON path, or '-' for stdin")
    ap.add_argument("--shard", default="", help="select job positions i/n after priority sorting")
    ap.add_argument("--limit",
                    type=int,
                    default=0,
                    help="keep only the first N jobs after sharding")
    ap.add_argument("--cert-out",
                    default="",
                    help="append this --out path to generated certify_all argv")
    ap.add_argument("--out", default="", help="write JSON schedule here. Without it, print stdout")
    args = ap.parse_args()
    try:
        manifest = _read_json(args.manifest)
        doc = build_schedule(manifest, shard=args.shard, limit=args.limit, cert_out=args.cert_out)
    except ScheduleError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        try:
            ensure_path_not_protected("--out", args.out)
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        s = doc["summary"]
        print(f"wrote {out}; jobs={s['jobs']} "
              f"skipped_rows={s['skipped_rows']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
