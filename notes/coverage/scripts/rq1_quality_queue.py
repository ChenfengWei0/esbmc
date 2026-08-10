#!/usr/bin/env python3
"""Build a TSV queue for valid-but-weak RQ1 subjects."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _as_int(doc: dict, key: str) -> int:
    try:
        return int(doc.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _row_for_result(result: Path, root: Path) -> dict | None:
    try:
        rel = result.relative_to(root).parts
    except ValueError:
        return None
    if len(rel) < 4 or rel[1] != "subjects":
        return None
    dataset, subject = rel[0], rel[2]
    doc = _json(result)
    row = doc.get("row") if isinstance(doc.get("row"), dict) else doc
    put = doc.get("put") if isinstance(doc.get("put"), dict) else {}
    valid = max(_as_int(row, "valid"), _as_int(put, "valid"))
    put_valid = max(_as_int(row, "put_valid"), _as_int(put, "put_valid"))
    r1r2 = max(
        _as_int(row, "valid_put_with_R1_or_R2"),
        _as_int(put, "valid_put_with_R1_or_R2"),
    )
    if valid <= 0:
        return None
    if put_valid <= 0:
        category = "NO_PUT_MATERIALIZATION"
    elif r1r2 <= 0:
        category = "NO_R1R2_ORACLE"
    else:
        return None
    return {
        "bench": dataset,
        "subject": subject,
        "category": category,
    }


def build_rows(root: Path, categories: set[str], limit: int) -> list[dict]:
    seen = set()
    rows = []
    for result in sorted(root.glob("*/subjects/*/result.json")):
        if any(marker in str(result) for marker in (
                ".redo.", ".superseded.", ".adopted_from_", ".incomplete.")):
            continue
        row = _row_for_result(result, root)
        if not row:
            continue
        if categories and row["category"] not in categories:
            continue
        key = (row["bench"], row["subject"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if limit > 0 and len(rows) >= limit:
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows = build_rows(args.results_root, set(args.category), args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["bench", "subject", "category"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "schema": "veriput-rq1-quality-queue/v1",
        "out": str(args.out),
        "count": len(rows),
        "categories": sorted(set(row["category"] for row in rows)),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
