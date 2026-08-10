#!/usr/bin/env python3
"""Build the bounded CE-discovery queue from canonical RQ1 no-valid rows."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


DEFAULT_RESULTS_ROOT = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_OUT = Path("/tmp/veriput_rq1_ce_collection_cases.tsv")
FIELDS = ("bench", "subject", "category", "ce_collection_id",
          "result_file", "reason")


def row_from_result(path: Path) -> dict | None:
    try:
        document = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    row = document.get("row") if isinstance(document, dict) else None
    row = row if isinstance(row, dict) else document
    if not isinstance(row, dict):
        return None
    try:
        valid = int(row.get("valid") or 0)
    except (TypeError, ValueError):
        valid = 0
    if valid > 0:
        return None
    subject = path.parent.name
    bench = path.parents[2].name
    if bench == "peer182" and subject != "contract080":
        return None
    return {
        "bench": bench,
        "subject": subject,
        "category": "CE_COLLECTION_NO_VALID",
        "ce_collection_id": f"{bench}/{subject}",
        "result_file": str(path),
        "reason": str(row.get("reason") or row.get("failure_reason") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.results_root.glob("*/subjects/*/result.json")):
        row = row_from_result(path)
        if row is not None:
            rows.append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"schema": "veriput-ce-collection-manifest/1",
                      "out": str(args.out), "cases": len(rows)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
