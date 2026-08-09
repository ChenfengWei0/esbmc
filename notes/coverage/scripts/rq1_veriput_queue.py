#!/usr/bin/env python3
"""Build prioritized VeriPUT RQ1 recovery queues from existing artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import rq1_veriput_triage


DEFAULT_RESULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def _load_result(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _result_rows(result_root: Path) -> list[dict]:
    return rq1_veriput_triage.triage_rows(
        result_root, ["peer182", "bugfix124", "real203"])


def _bucket_text(text: str) -> str:
    text = text or ""
    if "not a simple binary decision" in text:
        return "guard-parser"
    if "is not nameable in this PUT" in text:
        return "guard-nameability"
    if "ROLLBACK revert" in text:
        return "rollback-unobservable"
    if "path exits through revert" in text:
        return "revert-unobservable"
    if "no storage slot" in text:
        return "no-storage-slot"
    if "mapping" in text and "slot address cannot be computed" in text:
        return "mapping-slot-unrendered"
    if "rung shape not rendered" in text:
        return "rung-renderer"
    if "all return rungs DROPPED" in text:
        return "return-no-holding-rung"
    if "no return rung HOLDS" in text:
        return "return-no-holding-rung"
    if "no coordinate this test RENDERS is left more than one value" in text:
        return "no-rendered-width"
    return "other"


def _put_artifact_facts(subject_dir: Path) -> dict:
    facts = {
        "put_json_count": 0,
        "path_guard_skipped": [],
        "oracle_skipped": [],
        "oracle_classes": Counter(),
        "path_guard_reason_counts": Counter(),
        "oracle_skip_reason_counts": Counter(),
    }
    for put_json in sorted(subject_dir.glob("put/**/put.json")):
        doc = _load_result(put_json)
        stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
        facts["put_json_count"] += 1
        for item in stats.get("path_guard_skipped") or []:
            text = str(item)
            facts["path_guard_skipped"].append(text)
            facts["path_guard_reason_counts"][_bucket_text(text)] += 1
        for item in stats.get("oracle_skipped") or []:
            text = str(item)
            facts["oracle_skipped"].append(text)
            facts["oracle_skip_reason_counts"][_bucket_text(text)] += 1
        for klass in stats.get("oracle_classes") or []:
            facts["oracle_classes"][str(klass)] += 1
    return facts


def _primary_reason(row: dict, facts: dict) -> str:
    if facts["path_guard_reason_counts"]:
        return facts["path_guard_reason_counts"].most_common(1)[0][0]
    if facts["oracle_skip_reason_counts"]:
        return facts["oracle_skip_reason_counts"].most_common(1)[0][0]
    cause = row.get("triage_cause") or row.get("early_stop_reason") or ""
    if cause:
        return str(cause)
    status = row.get("completion_status") or row.get("status") or ""
    if status:
        return str(status)
    return "unknown"


def _queue(row: dict, facts: dict) -> tuple[str, int, str]:
    bucket = row.get("quality_bucket") or "unknown"
    status = row.get("completion_status") or ""
    cause = row.get("triage_cause") or ""
    has_guard = bool(facts["path_guard_skipped"])
    has_oracle_drop = bool(facts["oracle_skipped"])
    has_raw = (row.get("raw") or 0) > 0 or (row.get("put_raw") or 0) > 0

    if bucket in {"valid-no-PUT", "valid-PUT-no-R1R2",
                  "PUT-with-R1R2-but-no-width"}:
        if has_guard:
            return "P0", 0, "valid-weak-with-dropped-guard"
        if has_oracle_drop:
            return "P0", 1, "valid-weak-with-dropped-oracle"
        return "P0", 2, "valid-weak"

    if bucket == "no-valid":
        if has_raw or has_guard or has_oracle_drop:
            return "P1", 0, "no-valid-with-artifact"
        if status in {"no-output", "error"}:
            return "P2", 1, f"no-valid-{status}"
        if cause.startswith("cert-no-witness") or status == "ok":
            return "P2", 2, "no-valid-cert-no-witness"
        if status == "budget-exhausted":
            return "Archive", 8, "budget-exhausted-no-new-hypothesis"
        return "Archive", 9, "no-valid-low-evidence"

    return "Done", 99, "already-strong-enough"


def _row_for_output(row: dict, facts: dict, queue: str, rank: int,
                    queue_reason: str) -> dict:
    subject_dir = Path(row["result_json"]).parent
    return {
        "queue": queue,
        "rank": rank,
        "dataset": row.get("dataset"),
        "subject_id": row.get("subject_id") or subject_dir.name,
        "contract": row.get("contract"),
        "quality_bucket": row.get("quality_bucket"),
        "queue_reason": queue_reason,
        "primary_reason": _primary_reason(row, facts),
        "completion_status": row.get("completion_status"),
        "triage_cause": row.get("triage_cause"),
        "raw": row.get("raw"),
        "valid": row.get("valid"),
        "put_raw": row.get("put_raw"),
        "put_valid": row.get("put_valid"),
        "concrete_raw": row.get("concrete_raw"),
        "concrete_valid": row.get("concrete_valid"),
        "stage2_wall_s": row.get("stage2_wall_s"),
        "stage4_wall_s": row.get("stage4_wall_s"),
        "wall_total_s": row.get("wall_total_s"),
        "path_guard_skipped": len(facts["path_guard_skipped"]),
        "oracle_skipped": len(facts["oracle_skipped"]),
        "oracle_classes": ",".join(sorted(facts["oracle_classes"])),
        "sample_path_guard_skip": (
            facts["path_guard_skipped"][0] if facts["path_guard_skipped"]
            else ""),
        "sample_oracle_skip": (
            facts["oracle_skipped"][0] if facts["oracle_skipped"] else ""),
        "result_json": row.get("result_json"),
    }


def build_queues(result_root: Path) -> tuple[list[dict], dict]:
    out_rows = []
    summary = {
        "queue_counts": Counter(),
        "dataset_queue_counts": defaultdict(Counter),
        "quality_counts": Counter(),
        "primary_reason_counts": Counter(),
        "queue_reason_counts": Counter(),
    }
    for row in _result_rows(result_root):
        subject_dir = Path(row["result_json"]).parent
        facts = _put_artifact_facts(subject_dir)
        queue, rank, queue_reason = _queue(row, facts)
        rec = _row_for_output(row, facts, queue, rank, queue_reason)
        out_rows.append(rec)
        summary["queue_counts"][queue] += 1
        summary["dataset_queue_counts"][row.get("dataset")][queue] += 1
        summary["quality_counts"][row.get("quality_bucket")] += 1
        summary["primary_reason_counts"][rec["primary_reason"]] += 1
        summary["queue_reason_counts"][queue_reason] += 1
    out_rows.sort(key=lambda r: (
        r["rank"], r["dataset"] or "", r["subject_id"] or ""))
    return out_rows, summary


def _jsonable_summary(summary: dict) -> dict:
    return {
        "queue_counts": dict(summary["queue_counts"]),
        "dataset_queue_counts": {
            key: dict(value)
            for key, value in summary["dataset_queue_counts"].items()
        },
        "quality_counts": dict(summary["quality_counts"]),
        "primary_reason_counts": dict(summary["primary_reason_counts"]),
        "queue_reason_counts": dict(summary["queue_reason_counts"]),
    }


def write_outputs(rows: list[dict], summary: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    all_path = out_dir / "queue_all.tsv"
    with all_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    for queue in ("P0", "P1", "P2", "Archive", "Done"):
        qrows = [r for r in rows if r["queue"] == queue]
        with (out_dir / f"queue_{queue.lower()}.tsv").open("w",
                                                            newline="") as f:
            writer = csv.DictWriter(f, fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(qrows)
    (out_dir / "queue_summary.json").write_text(
        json.dumps(_jsonable_summary(summary), indent=2, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    ap.add_argument("--out-dir", type=Path,
                    default=DEFAULT_RESULT_ROOT / "triage-queues")
    args = ap.parse_args(argv)
    rows, summary = build_queues(args.result_root)
    write_outputs(rows, summary, args.out_dir)
    print(json.dumps(_jsonable_summary(summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
