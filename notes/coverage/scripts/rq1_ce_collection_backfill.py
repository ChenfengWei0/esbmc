#!/usr/bin/env python3
"""Backfill bounded CE evidence from completed CE-stage result rows."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
ARTIFACTS = (
    "ce-collection.json",
    "ce-witness-journal.json",
    "cov-ce-journal.json",
    "enumeration-report.json",
    "generalise-progress.json",
    "run-config.json",
    "driver.log",
)


def latest_active_workdir(results: Path) -> Path | None:
    try:
        rows = [json.loads(line) for line in results.read_text().splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError):
        return None
    for row in reversed(rows):
        evidence = row.get("failure_evidence") if isinstance(row, dict) else None
        active = evidence.get("active_workdir") if isinstance(evidence, dict) else None
        if isinstance(active, str) and Path(active).is_dir():
            return Path(active)
    return None


def backfill_subject(summary_path: Path, *, dry_run: bool) -> tuple[str, int]:
    collection = summary_path.parent
    source = latest_active_workdir(collection / "certify-results.jsonl")
    if source is None:
        return "no-active-workdir", 0
    destination = collection / source.name
    copied = []
    for name in ARTIFACTS:
        candidate = source / name
        if candidate.is_file():
            copied.append(candidate)
            if not dry_run:
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(candidate, destination / name)
    if not copied:
        return "no-artifacts", 0
    if not dry_run:
        summary = json.loads(summary_path.read_text())
        stage = summary.setdefault("stage", {})
        stage["source_workdir"] = str(source)
        stage["artifact_paths"] = [str(destination / item.name) for item in copied]
        stage["artifact_present"] = (destination / "ce-collection.json").is_file()
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return "backfilled", len(copied)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    counts: dict[str, int] = {}
    files = 0
    for summary in sorted(args.result_root.glob("*/subjects/*/ce-collection/summary.json")):
        status, copied = backfill_subject(summary, dry_run=args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        files += copied
    print(json.dumps({"schema": "veriput-ce-backfill/v1", "subjects": counts,
                      "files": files, "dry_run": args.dry_run}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
