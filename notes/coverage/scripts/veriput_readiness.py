#!/usr/bin/env python3
"""Summarize VeriPUT target->unit readiness without running compilers.

Input is a `veriput-unit-manifest/v1` document, typically produced by
`target_manifest.py | subject_unit_manifest.py --target-manifest /dev/stdin`.
This script is intentionally read-only: it never invokes solc, Forge, fuzzers,
or ESBMC.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _load_json(path: str):
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    return json.loads(text)


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


def _unit_hints(row: dict) -> dict:
    return row.get("unit_hints") or {}


def _solc_key(row: dict) -> str:
    subject = _subject(row)
    solc = subject.get("solc_bin") or subject.get("inferred_solc_bin") \
        or "<missing-solc-bin>"
    extra = " ".join(subject.get("solc_extra") or [])
    version = subject.get("solc")
    suffix = f" {extra}".rstrip()
    if subject.get("solc_bin"):
        return f"{Path(solc).name}{suffix}".strip()
    if subject.get("inferred_solc_bin"):
        label = f"inferred:{Path(solc).name}"
        if version:
            label = f"{label}({version})"
        return f"{label}{suffix}".strip()
    if version:
        return f"<missing-solc-bin>({version}){suffix}".strip()
    return f"{Path(solc).name}{suffix}".strip()


def _sample(row: dict) -> dict:
    subject = _subject(row)
    target = _target(row)
    return {
        "benchmark": _benchmark(row),
        "subject_id": subject.get("subject_id") or target.get("subject_id"),
        "contract": subject.get("contract") or target.get("contract"),
        "status": row.get("status"),
    }


def build_readiness(unit_manifest: dict, *, sample_limit: int = 10) -> dict:
    if unit_manifest.get("schema") != "veriput-unit-manifest/v1":
        raise ValueError(
            f"unsupported unit manifest schema {unit_manifest.get('schema')!r}")
    rows = unit_manifest.get("subjects") or []
    status_by_benchmark = defaultdict(Counter)
    hint_by_benchmark = defaultdict(Counter)
    error_by_benchmark = defaultdict(Counter)
    ast_by_solc = defaultdict(Counter)
    preheat_by_benchmark = defaultdict(Counter)
    samples = {
        "missing_ast": [],
        "prepared_errors": [],
        "pending_hint_rows": [],
        "missing_hint_rows": [],
    }

    for row in rows:
        bench = _benchmark(row)
        status = row.get("status") or "<missing-status>"
        status_by_benchmark[bench][status] += 1

        hints = _unit_hints(row)
        for key in ("hinted_units", "missing_unit_hints",
                    "pending_unit_hints"):
            hint_by_benchmark[bench][key] += len(hints.get(key) or [])

        if status == "missing-ast":
            ast_by_solc[bench][_solc_key(row)] += 1
            subject = _subject(row)
            if subject.get("solc_bin"):
                preheat_by_benchmark[bench]["preheatable_missing_ast"] += 1
            elif subject.get("inferred_solc_bin"):
                preheat_by_benchmark[bench]["inferable_solc_bin"] += 1
            else:
                preheat_by_benchmark[bench]["missing_solc_bin"] += 1
            if len(samples["missing_ast"]) < sample_limit:
                samples["missing_ast"].append(_sample(row))
        elif status == "error":
            bucket = _reason_bucket(row.get("reason") or "")
            error_by_benchmark[bench][bucket] += 1
            if len(samples["prepared_errors"]) < sample_limit:
                item = _sample(row)
                item["reason_bucket"] = bucket
                item["reason"] = (row.get("reason") or "")[:240]
                samples["prepared_errors"].append(item)

        if hints.get("pending_unit_hints") and \
                len(samples["pending_hint_rows"]) < sample_limit:
            item = _sample(row)
            item["pending_unit_hints"] = hints["pending_unit_hints"]
            samples["pending_hint_rows"].append(item)
        if hints.get("missing_unit_hints") and \
                len(samples["missing_hint_rows"]) < sample_limit:
            item = _sample(row)
            item["missing_unit_hints"] = hints["missing_unit_hints"]
            samples["missing_hint_rows"].append(item)

    total_status = Counter()
    for counter in status_by_benchmark.values():
        total_status.update(counter)

    return {
        "schema": "veriput-readiness/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "unit_manifest_generated_at": unit_manifest.get("generated_at"),
            "target_manifest": unit_manifest.get("target_manifest"),
        },
        "summary": {
            "rows": len(rows),
            "status": dict(sorted(total_status.items())),
            "benchmarks": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(status_by_benchmark.items())
            },
            "hints": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(hint_by_benchmark.items())
            },
            "prepared_errors": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(error_by_benchmark.items())
            },
            "missing_ast_by_solc": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(ast_by_solc.items())
            },
            "preheat": {
                bench: dict(sorted(counter.items()))
                for bench, counter in sorted(preheat_by_benchmark.items())
            },
        },
        "next_actions": [
            "Fix or exclude prepared error rows before treating the target set "
            "as a unit denominator.",
            "Preheat compact ASTs for missing-ast rows in shards, then rerun "
            "subject_unit_manifest.py to classify pending_unit_hints.",
            "Prioritize bugfix rows with changed-function hints when building "
            "strong PUT candidates; hints are priorities, not filters.",
        ],
        "samples": samples,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("unit_manifest",
                    help="unit manifest JSON path, or '-' for stdin")
    ap.add_argument("--sample-limit", type=int, default=10,
                    help="maximum sample rows per readiness bucket")
    ap.add_argument("--out", default="",
                    help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)
    try:
        report = build_readiness(
            _load_json(args.unit_manifest),
            sample_limit=args.sample_limit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
