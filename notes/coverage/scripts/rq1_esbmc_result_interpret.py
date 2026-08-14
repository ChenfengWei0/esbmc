#!/usr/bin/env python3
"""Deterministically classify VeriPUT/ESBMC RQ1 artifacts.

This is the hard result decoder used after local or remote runs.  It maps
logs/results to actionable buckets instead of relying on ad-hoc reading.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from pathlib import Path


DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_REPAIR_TICKETS = Path("/tmp/veriput_rq1_repair_tickets.jsonl")


PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ESBMC_NO_COV_REPORT_FRONTEND_OR_COVERAGE",
     ("no cov-report", "cov-report.json missing", "migrate expr failed",
      "arith_2ops", "type mismatch", "failed to migrate")),
    ("CERTIFY_COUNTEREXAMPLE_REJECTED_NOT_CERTIFIED",
     ("NOT_CERTIFIED", "not certified", "counterexample rejected")),
    ("CERTIFY_PATH_COV_GOAL_CAP_REFUSAL",
     ("path-cov-max-goals", "max goals")),
    ("CERTIFY_NO_COORDINATE_NO_PUT_REGION",
     ("NO_COORDINATE", "no coordinate", "getter-no-coordinate")),
    ("CERTIFY_NO_PATH_OR_BOUNDED_HOLDS_EMPTY",
     ("NO_PATH", "bounded holds empty", "no path")),
    ("CERTIFY_NO_WITNESS_UNDECIDED",
     ("NO_WITNESS", "no witness", "UNKNOWN")),
    ("RUNNER_STAGE2_NO_OUTPUT_EARLY_STOP",
     ("stage2 no output", "no-output", "empty shard")),
    ("RUNNER_EARLY_STOP_AFTER_NO_CANDIDATE_PREFIX",
     ("no candidate", "early stop", "no Stage4 candidate")),
    ("RUNNER_STAGE2_CONSUMED_STAGE4_BUDGET",
     ("stage4 budget", "consumed stage4", "budget exhausted before stage4")),
    ("OOM_OR_MEMORY_PRESSURE",
     ("out of memory", "Cannot allocate memory", "Killed", "std::bad_alloc",
      "memory exhausted", "OOM")),
    ("TIMEOUT",
     ("timeout", "timed out", "SIGTERM", "run-timeout")),
    ("NO_PUT_MATERIALIZATION",
     ("valid-no-PUT", "no PUT", "concrete fallback", "put_valid\": 0")),
    ("NO_R1R2_ORACLE",
     ("valid-PUT-no-R1R2", "no R1", "no R2", "oracle skipped",
      "valid_put_with_R1_or_R2\": 0")),
)


def _read_text(path: Path, limit_bytes: int) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit_bytes:
        data = data[-limit_bytes:]
    return data.decode("utf-8", errors="replace")


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _as_int(obj: dict, key: str) -> int:
    try:
        return int(obj.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _metric_docs(result_json: dict, put_json: dict) -> list[dict]:
    docs = [result_json, put_json]
    for key in ("row", "put", "adoption"):
        value = result_json.get(key)
        if isinstance(value, dict):
            docs.append(value)
    return docs


def _summary_references(result_json: dict, put_json: dict) -> list[Path]:
    """Return referenced PUT summaries, including canonical relocated copies."""
    docs = [result_json, put_json]
    for key in ("row", "put", "adoption"):
        value = result_json.get(key)
        if isinstance(value, dict):
            docs.append(value)
    references: list[Path] = []
    for doc in docs:
        for key in ("summary_paths", "put_summary_paths"):
            paths = doc.get(key)
            if not isinstance(paths, list):
                continue
            references.extend(Path(str(path)) for path in paths if path)
    return references


def _summary_matches_subject(summary: dict, subject_dir: Path) -> bool:
    subject_id = subject_dir.name
    only = str(summary.get("only") or "")
    if subject_id in only:
        return True
    deliverable = summary.get("deliverable_b")
    if not isinstance(deliverable, dict):
        return False
    for row in deliverable.get("rows", []):
        if not isinstance(row, dict):
            continue
        benchmark = str(row.get("benchmark") or "")
        if subject_id in benchmark:
            return True
    return False


def _relocated_summary_metrics(subject_dir: Path, result_json: dict,
                                put_json: dict) -> tuple[list[dict], list[dict], list[str]]:
    """Recover only forge-backed valid rows from a relocated PUT summary.

    Remote runs leave absolute ``/home/administrator`` references in the
    canonical result.  A local summary is usable only when its row still
    carries the two independent validity signals: the emitter marked the
    reference test valid and Foundry reported Success.  A certificate alone,
    a missing forge status, a refused row, or a stale row is never promoted.
    """
    refs = _summary_references(result_json, put_json)
    candidates: list[Path] = []
    for ref in refs:
        if ref.is_file():
            candidates.append(ref)
            continue
        if ref.name != "put-summary.json":
            continue
        candidates.extend(sorted(subject_dir.glob("put/**/put-summary.json")))

    seen: set[Path] = set()
    docs: list[dict] = []
    recovered: list[dict] = []
    relocated_paths: list[str] = []
    fallback_tags = set()
    stats = put_json.get("stats")
    if isinstance(stats, dict):
        for key in ("oracle_classes", "oracle_tags"):
            values = stats.get(key)
            if isinstance(values, list):
                fallback_tags.update(str(value) for value in values)

    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        summary = _json(path)
        if (not summary
                or summary.get("schema") != "veriput-put-summary/1"
                or not _summary_matches_subject(summary, subject_dir)):
            continue
        deliverable = summary.get("deliverable_b")
        if not isinstance(deliverable, dict):
            continue
        valid_rows = []
        for row in deliverable.get("rows", []):
            if not isinstance(row, dict):
                continue
            if (row.get("stage2_source") == "no_unit_deploy_fallback"
                    or row.get("stage4_kind") in
                    ("deploy-only", "creation-code-only")):
                continue
            if row.get("valid_reference_test") is not True:
                continue
            if row.get("forge_status") != "Success":
                continue
            if row.get("stale") or row.get("refused") is True:
                continue
            valid_rows.append(row)
        if not valid_rows:
            continue
        row_tags: dict[int, set[str]] = {}
        for index, row in enumerate(valid_rows):
            tags = set()
            for key in ("oracle_classes", "oracle_tags"):
                values = row.get(key)
                if isinstance(values, list):
                    tags.update(str(value) for value in values)
            row_tags[index] = tags
        put_rows = [row for row in valid_rows if row.get("kind") == "put"]
        concrete_rows = [row for row in valid_rows if row.get("kind") == "concrete"]
        if len(put_rows) == 1:
            put_index = valid_rows.index(put_rows[0])
            if not row_tags[put_index]:
                row_tags[put_index].update(fallback_tags)
        r1r2_rows = []
        for index, row in enumerate(valid_rows):
            if (row.get("kind") == "put"
                    and any(tag.upper() in {"R1", "R2"}
                            for tag in row_tags[index])):
                r1r2_rows.append(row)
        docs.append({
            "valid": len(valid_rows),
            "put_valid": len(put_rows),
            "concrete_valid": len(concrete_rows),
            "valid_put_with_R1_or_R2": len(r1r2_rows),
        })
        relocated_paths.append(str(path))
        for index, row in enumerate(valid_rows):
            recovered.append({
                "benchmark": row.get("benchmark"),
                "unit": row.get("unit"),
                "test": row.get("test"),
                "kind": row.get("kind"),
                "stage2_source": row.get("stage2_source"),
                "forge_status": row.get("forge_status"),
                "valid_reference_test": True,
                "oracle_tags": sorted(row_tags[index]),
                "source": str(path),
            })
    return docs, recovered, relocated_paths


def _max_metric(docs: list[dict], key: str) -> int:
    values: list[int] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        values.append(_as_int(doc, key))
        artifact_counts = doc.get("artifact_counts")
        if isinstance(artifact_counts, dict):
            values.append(_as_int(artifact_counts, key))
    return max(values or [0])


def _bench_subject(subject_dir: Path) -> tuple[str, str]:
    parts = subject_dir.parts
    for index in range(len(parts) - 5):
        if parts[index:index + 4] == (
                "VeriPUT", "Results", "RQ1", "VeriPUT"):
            if len(parts) > index + 6 and parts[index + 5] == "subjects":
                return parts[index + 4], parts[index + 6]
    return "", subject_dir.name


def _suggested_scope(bucket: str) -> list[str]:
    if bucket in {"NO_PUT_MATERIALIZATION", "NO_R1R2_ORACLE"}:
        return ["scripts/solidity_path_put.py", "notes/coverage/scripts/put_all.py"]
    if bucket.startswith("ESBMC_"):
        return [
            "src/solidity-frontend/*.cpp",
            "src/goto-programs/goto_coverage.cpp",
        ]
    if bucket == "UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING":
        return ["notes/coverage/scripts/rq1_veriput_run.py"]
    return ["notes/coverage/scripts/certify_all.py"]


def _repair_ticket(row: dict) -> dict | None:
    if row.get("valid") and row.get("put_valid") and row.get("r1r2"):
        return None
    bucket = str(row.get("bucket") or "UNKNOWN")
    subject_dir = Path(str(row.get("subject_dir") or ""))
    return {
        "schema": "veriput-rq1-repair-ticket/v1",
        "ts": time.time(),
        "bench": row.get("bench"),
        "subject": row.get("subject"),
        "category": bucket,
        "result_bucket": bucket,
        "valid": int(row.get("valid") or 0),
        "put_valid": int(row.get("put_valid") or 0),
        "r1r2": int(row.get("r1r2") or 0),
        "priority": "high" if bucket.startswith("ESBMC_")
        or bucket == "UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING" else "normal",
        "trigger_reason": "interpreted-actual-result-below-valid-put-r1r2",
        "subject_dir": str(subject_dir),
        "result_file": row.get("result_file"),
        "logs": [
            str(subject_dir / "driver.log"),
            str(subject_dir / "result.json"),
            str(subject_dir / "put.json"),
        ],
        "suggested_write_scope": _suggested_scope(bucket),
        "theoretical_progress_effect": (
            "If this case is in a covered category and remains no-valid after "
            "the current progress deadline, rq1_no_valid_progress.py subtracts "
            "it from net theoretical/provisional coverage. valid-no-PUT and "
            "PUT-no-R1/R2 subtract from quality progress."),
        "subagent_rule": (
            "Inspect listed failure records and owning source code before "
            "editing; do not run ESBMC/RQ1 as root-cause discovery."),
    }


def _ticket_key(ticket: dict) -> tuple[str, str, str, str]:
    return (
        str(ticket.get("bench") or ""),
        str(ticket.get("subject") or ""),
        str(ticket.get("result_bucket") or ""),
        str(ticket.get("result_file") or ""),
    )


def append_repair_tickets(path: Path, tickets: list[dict]) -> int:
    if not tickets:
        return 0
    existing = set()
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            existing.add(_ticket_key(row))
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a") as stream:
        for ticket in tickets:
            key = _ticket_key(ticket)
            if key in existing:
                continue
            stream.write(json.dumps(ticket, sort_keys=True) + "\n")
            existing.add(key)
            written += 1
    return written


def classify_text(text: str) -> str:
    lowered = text.lower()
    for bucket, needles in PATTERNS:
        for needle in needles:
            if needle.lower() in lowered:
                return bucket
    return "UNCLASSIFIED_RESULT_SCHEMA_OR_ARTIFACT_MISSING"


def classify_subject(subject_dir: Path, limit_bytes: int) -> dict:
    result_json = _json(subject_dir / "result.json")
    put_json = _json(subject_dir / "put.json")
    relocated_docs, recovered_tests, relocated_paths = (
        _relocated_summary_metrics(subject_dir, result_json, put_json))
    texts = []
    for name in ("driver.log", "stderr.log", "stdout.log", "result.json",
                 "put.json"):
        texts.append(_read_text(subject_dir / name, limit_bytes))
    for log in subject_dir.glob("**/*.log"):
        texts.append(_read_text(log, limit_bytes // 4))
    text = "\n".join(part for part in texts if part)
    bucket = classify_text(text)

    metric_docs = _metric_docs(result_json, put_json)
    metric_docs.extend(relocated_docs)
    valid = int(bool(_max_metric(metric_docs, "valid")))
    put_valid = int(bool(_max_metric(metric_docs, "put_valid")))
    r1r2 = int(bool(_max_metric(metric_docs, "valid_put_with_R1_or_R2")))
    bench, subject = _bench_subject(subject_dir)
    if valid and not put_valid:
        bucket = "NO_PUT_MATERIALIZATION"
    elif put_valid and not r1r2:
        bucket = "NO_R1R2_ORACLE"
    elif valid and put_valid and r1r2:
        bucket = "VALID_PUT_R1R2"
    return {
        "bench": bench,
        "subject": subject,
        "subject_dir": str(subject_dir),
        "result_file": str(subject_dir / "result.json"),
        "bucket": bucket,
        "valid": valid,
        "put_valid": put_valid,
        "r1r2": r1r2,
        "has_result_json": (subject_dir / "result.json").exists(),
        "has_put_json": (subject_dir / "put.json").exists(),
        "relocated_summary_paths": relocated_paths,
        "recovered_valid_tests": recovered_tests,
    }


def iter_subject_dirs(root: Path) -> list[Path]:
    return sorted(path.parent for path in root.glob("*/*/*/result.json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--subject-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--repair-tickets", type=Path,
                        default=DEFAULT_REPAIR_TICKETS)
    parser.add_argument("--emit-repair-tickets", action="store_true")
    parser.add_argument("--limit-bytes", type=int, default=256_000)
    args = parser.parse_args()

    if args.subject_dir:
        rows = [classify_subject(args.subject_dir, args.limit_bytes)]
    else:
        rows = [
            classify_subject(subject_dir, args.limit_bytes)
            for subject_dir in iter_subject_dirs(args.results_root)
        ]
    counts = Counter(row["bucket"] for row in rows)
    doc = {
        "schema": "veriput-rq1-esbmc-result-interpret/v1",
        "results_root": str(args.results_root),
        "count": len(rows),
        "bucket_counts": dict(sorted(counts.items())),
        "rows": rows,
    }
    tickets = [
        ticket for ticket in (_repair_ticket(row) for row in rows)
        if ticket is not None
    ]
    doc["repair_ticket_candidates"] = tickets
    if args.emit_repair_tickets:
        doc["repair_tickets_path"] = str(args.repair_tickets)
        doc["repair_tickets_written"] = append_repair_tickets(
            args.repair_tickets, tickets)
    payload = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
