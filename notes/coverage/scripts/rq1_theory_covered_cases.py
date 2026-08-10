#!/usr/bin/env python3
"""Write the exact RQ1 cases that current theory is allowed to validate.

Workers must consume this manifest instead of the broad no-valid root-cause TSV.
The default output includes only patch_ids that passed independent review and
recorded a commit sha.  Provisional rows are available only with an explicit
flag for review/debug queues.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from rq1_no_valid_progress import (
    DEFAULT_EXTRA_SUBAGENTS,
    DEFAULT_SUBAGENTS,
    DEFAULT_TSV,
    FIXED_MICRO_PATCH_CATEGORIES,
    FIXED_MICRO_PATCH_COVERAGE,
    PATCH_BATCHES,
    load_json_file,
    merge_subagent_docs,
    subagent_patch_review_sets,
)


DEFAULT_OUT = Path("/tmp/veriput_rq1_theory_covered_cases.tsv")


def _load_rows(tsv: Path) -> list[dict]:
    with tsv.open(newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _patch_category_claims(patch_ids: set[str]) -> list[dict]:
    claims = []
    for batch in PATCH_BATCHES:
        if batch.patch_id not in patch_ids:
            continue
        for category in batch.categories:
            claims.append({
                "patch_id": batch.patch_id,
                "category": category,
                "limit": 0,
                "kind": "batch",
                "fix_target": batch.fix_target,
            })
    for patch_id, (limit, reason) in FIXED_MICRO_PATCH_COVERAGE.items():
        if patch_id not in patch_ids:
            continue
        claims.append({
            "patch_id": patch_id,
            "category": FIXED_MICRO_PATCH_CATEGORIES.get(patch_id, ""),
            "limit": int(limit),
            "kind": "micro",
            "fix_target": reason,
        })
    return [claim for claim in claims if claim["category"]]


def build_manifest(rows: list[dict], patch_ids: set[str]) -> tuple[list[dict], dict]:
    claims = _patch_category_claims(patch_ids)
    out = []
    seen_subjects: set[tuple[str, str]] = set()
    used_by_patch: dict[str, int] = {}
    for claim in claims:
        used = 0
        for row in rows:
            if row.get("category") != claim["category"]:
                continue
            key = (str(row.get("bench") or ""), str(row.get("subject") or ""))
            if not key[0] or not key[1] or key in seen_subjects:
                continue
            limit = int(claim.get("limit") or 0)
            if limit > 0 and used >= limit:
                break
            marked = dict(row)
            marked["theory_patch_id"] = claim["patch_id"]
            marked["theory_patch_kind"] = claim["kind"]
            marked["theory_review_status"] = "accepted-with-commit"
            marked["theory_fix_target"] = claim["fix_target"]
            out.append(marked)
            seen_subjects.add(key)
            used += 1
        used_by_patch[claim["patch_id"]] = used_by_patch.get(
            claim["patch_id"], 0) + used
    return out, {
        "claims": claims,
        "used_by_patch": used_by_patch,
        "subject_count": len(out),
    }


def write_tsv(path: Path, rows: list[dict]) -> None:
    fieldnames = [
        "bench",
        "subject",
        "category",
        "fix_target",
        "theory_patch_id",
        "theory_patch_kind",
        "theory_review_status",
        "theory_fix_target",
    ]
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, delimiter="\t", fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--subagents", type=Path, default=DEFAULT_SUBAGENTS)
    parser.add_argument("--extra-subagents",
                        type=Path,
                        default=DEFAULT_EXTRA_SUBAGENTS)
    parser.add_argument("--include-provisional", action="store_true")
    args = parser.parse_args()

    rows = _load_rows(args.tsv)
    subagents = merge_subagent_docs(
        load_json_file(args.subagents, {"agents": []}),
        load_json_file(args.extra_subagents, {"agents": []}),
    )
    review_sets = subagent_patch_review_sets(subagents)
    patch_ids = set(review_sets["accepted"])
    review_status = "accepted-with-commit"
    if args.include_provisional:
        patch_ids.update(review_sets["provisional"])
        review_status = "accepted-with-commit-or-provisional"
    manifest, details = build_manifest(rows, patch_ids)
    for row in manifest:
        row["theory_review_status"] = review_status
    write_tsv(args.out, manifest)
    print(json.dumps({
        "schema": "veriput-rq1-theory-covered-cases/v1",
        "out": str(args.out),
        "case_count": len(manifest),
        "accepted_patch_ids": sorted(review_sets["accepted"]),
        "provisional_patch_ids_included": (
            sorted(review_sets["provisional"])
            if args.include_provisional else []),
        "rejected_patch_ids_excluded": sorted(review_sets["rejected"]),
        "details": details,
        "worker_rule": (
            "rq1_local_pump.py and rq1_remote_pump.py must run this TSV by "
            "default. If case_count is zero, do not start ESBMC workers."),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
