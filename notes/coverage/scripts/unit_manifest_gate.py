#!/usr/bin/env python3
"""Gate a VeriPUT unit manifest before certification scheduling.

This is the post-AST-preheat readiness check.  It consumes a
`veriput-unit-manifest/v1` document and reports whether the manifest is still
blocked on ASTs, degraded by prepared errors or missing changed-function hints,
or ready to schedule unique unit certification jobs.  It never invokes solc,
Forge, fuzzing, ESBMC, or `certify_all.py`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


class GateError(ValueError):
    """The unit manifest cannot be checked."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError(f"{path} is not valid JSON: {exc}") from exc


def _subject(row: dict) -> dict:
    return row.get("subject") or {}


def _target(row: dict) -> dict:
    return row.get("target") or {}


def _benchmark(row: dict) -> str:
    return _subject(row).get("benchmark") or _target(row).get("benchmark") \
        or "<unknown>"


def _reason_bucket(reason: str) -> str:
    if "target manifest contract disagrees" in reason:
        return "target-contract-mismatch"
    m = re.search(r"status='([^']+)'", reason)
    if m:
        return f"prepared-status:{m.group(1)}"
    if "does not exist" in reason:
        return "missing-file"
    return reason.split(":", 1)[0][:80] or "unknown"


def _sample(row: dict) -> dict:
    subject = _subject(row)
    target = _target(row)
    return {
        "benchmark": _benchmark(row),
        "subject_id": subject.get("subject_id") or target.get("subject_id"),
        "contract": subject.get("contract") or target.get("contract"),
        "status": row.get("status"),
    }


def build_gate(unit_manifest: dict, *, sample_limit: int = 10) -> dict:
    if unit_manifest.get("schema") != "veriput-unit-manifest/v1":
        raise GateError(f"unsupported schema {unit_manifest.get('schema')!r}; expected "
                        "veriput-unit-manifest/v1")

    rows = unit_manifest.get("subjects") or []
    status = Counter()
    by_benchmark = defaultdict(Counter)
    errors = defaultdict(Counter)
    hints = Counter()
    unique_unit_jobs = set()
    duplicate_unit_jobs = []
    duplicate_subject_rows = []
    seen_subject_rows = set()
    zero_unit_rows = []
    samples = {
        "missing_ast": [],
        "errors": [],
        "pending_hints": [],
        "missing_hints": [],
        "zero_unit_rows": [],
        "duplicate_unit_jobs": [],
        "duplicate_subject_rows": [],
    }

    for row_pos, row in enumerate(rows):
        row_status = row.get("status") or "<missing-status>"
        bench = _benchmark(row)
        status[row_status] += 1
        by_benchmark[bench][row_status] += 1

        subject = _subject(row)
        subject_key = (subject.get("benchmark"), subject.get("subject_id"))
        if all(subject_key):
            if subject_key in seen_subject_rows:
                item = {
                    "row": row_pos,
                    "reason": "duplicate prepared subject row",
                    "subject": subject,
                    "target": row.get("target"),
                }
                duplicate_subject_rows.append(item)
                if len(samples["duplicate_subject_rows"]) < sample_limit:
                    samples["duplicate_subject_rows"].append(item)
            else:
                seen_subject_rows.add(subject_key)

        unit_hints = row.get("unit_hints") or {}
        for key in ("hinted_units", "missing_unit_hints", "pending_unit_hints"):
            hints[key] += len(unit_hints.get(key) or [])

        if row_status == "missing-ast":
            if len(samples["missing_ast"]) < sample_limit:
                samples["missing_ast"].append(_sample(row))
            continue
        if row_status == "error":
            bucket = _reason_bucket(row.get("reason") or "")
            errors[bench][bucket] += 1
            if len(samples["errors"]) < sample_limit:
                item = _sample(row)
                item["reason_bucket"] = bucket
                item["reason"] = (row.get("reason") or "")[:240]
                samples["errors"].append(item)
            continue
        if row_status != "ok":
            continue

        units = (row.get("units") or {}).get("units") or []
        if not units:
            item = _sample(row)
            zero_unit_rows.append(item)
            if len(samples["zero_unit_rows"]) < sample_limit:
                samples["zero_unit_rows"].append(item)
        for unit in units:
            job_key = (subject.get("benchmark"), subject.get("subject_id"), unit)
            if job_key in unique_unit_jobs:
                item = {
                    "row": row_pos,
                    "unit": unit,
                    "subject": subject,
                    "target": row.get("target"),
                }
                duplicate_unit_jobs.append(item)
                if len(samples["duplicate_unit_jobs"]) < sample_limit:
                    samples["duplicate_unit_jobs"].append(item)
            else:
                unique_unit_jobs.add(job_key)

        if unit_hints.get("pending_unit_hints") and \
                len(samples["pending_hints"]) < sample_limit:
            item = _sample(row)
            item["pending_unit_hints"] = unit_hints["pending_unit_hints"]
            samples["pending_hints"].append(item)
        if unit_hints.get("missing_unit_hints") and \
                len(samples["missing_hints"]) < sample_limit:
            item = _sample(row)
            item["missing_unit_hints"] = unit_hints["missing_unit_hints"]
            samples["missing_hints"].append(item)

    blockers = []
    warnings = []
    if status.get("missing-ast", 0):
        blockers.append("missing compact AST rows remain")
    if hints.get("pending_unit_hints", 0):
        blockers.append("changed-function hints are still pending AST enumeration")
    if status.get("ok", 0) == 0:
        blockers.append("no ok subject rows with enumerated units")
    if status.get("error", 0):
        warnings.append("prepared target errors remain")
    if hints.get("missing_unit_hints", 0):
        warnings.append("some changed-function hints are absent from AST units")
    if zero_unit_rows:
        warnings.append("some ok subjects expose zero named public/external units")
    if duplicate_unit_jobs:
        warnings.append("duplicate prepared subject/unit jobs were deduplicated")

    if blockers:
        gate_status = "blocked"
    elif warnings:
        gate_status = "degraded"
    else:
        gate_status = "ready"

    return {
        "schema": "veriput-unit-manifest-gate/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate_status": gate_status,
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "rows": len(rows),
            "status": dict(sorted(status.items())),
            "by_benchmark": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(by_benchmark.items())
            },
            "errors": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(errors.items())
            },
            "hints": dict(sorted(hints.items())),
            "unique_unit_jobs": len(unique_unit_jobs),
            "duplicate_unit_jobs": len(duplicate_unit_jobs),
            "duplicate_subject_rows": len(duplicate_subject_rows),
            "zero_unit_rows": len(zero_unit_rows),
            "ready_for_unit_schedule": not blockers,
            "ready_for_full_denominator": not blockers and not warnings,
        },
        "samples": samples,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("unit_manifest", help="unit manifest JSON path, or '-' for stdin")
    ap.add_argument("--sample-limit", type=int, default=10, help="maximum sample rows per bucket")
    ap.add_argument("--out", default="", help="write JSON report here instead of stdout")
    args = ap.parse_args()
    try:
        doc = build_gate(_load_json(args.unit_manifest), sample_limit=args.sample_limit)
    except (OSError, GateError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
