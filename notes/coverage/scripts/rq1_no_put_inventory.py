#!/usr/bin/env python3
"""Build a strict, evidence-grounded inventory of canonical RQ1 no-PUT cases.

This is deliberately a read-only audit. It discovers canonical subjects from
the RQ1 result tree and validates replay
manifests from their retained files rather than trusting cached counters.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from rq1_case_batch import (
    _detailed_test_rows,
    _is_valid_reference_test,
    result_numbers,
)
from rq1_concrete_replay_store import (
    audit_manifest,
    load_manifest,
    persistence_coverage,
)
from rq1_artifact_audit import canonical_subject


HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_JSON = HERE.parent / "rq1_no_put_inventory.json"
DEFAULT_TSV = HERE.parent / "rq1_no_put_inventory.tsv"

FRONTEND_RE = re.compile(
    r"(?:CONVERSION ERROR|conversion exception|solidity frontend|"
    r"cannot convert|tuple.*(?:error|mismatch)|symbol.*already exists)",
    re.IGNORECASE,
)
OOM_RE = re.compile(
    r"(?:out of memory|std::bad_alloc|oom[- ]kill|memory limit exceeded|"
    r"cannot allocate memory)",
    re.IGNORECASE,
)


def read_json(path: Path) -> dict:
    """Read a JSON object, returning an empty object for broken evidence."""
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict]:
    """Read valid object rows from an append-only evidence journal."""
    rows = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def log_signals(subject_dir: Path) -> dict:
    """Find bounded frontend and OOM diagnostics in canonical logs."""
    frontend_hits = []
    oom_hits = []
    # Certification logs are bounded in normal RQ1 artifacts.  Tail reads keep
    # this audit cheap even if a diagnostic accidentally became verbose.
    for path in sorted((subject_dir / "logs").glob("*")):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 512_000))
                text = stream.read().decode(errors="replace")
        except OSError:
            continue
        for pattern, hits in ((FRONTEND_RE, frontend_hits), (OOM_RE, oom_hits)):
            match = pattern.search(text)
            if match and len(hits) < 4:
                hits.append({"log": str(path), "match": match.group(0)[:160]})
    return {"frontend_hits": frontend_hits, "oom_hits": oom_hits}


def structural_kind(result: dict, subject_dir: Path) -> str | None:
    """Recognize source-grounded library and constructor-only subjects."""
    target = result.get("target") if isinstance(result.get("target"), dict) else {}
    subject = result.get("subject") if isinstance(result.get("subject"), dict) else {}
    contract = str(target.get("contract") or subject.get("contract") or "")
    flat = Path(str(subject.get("flat_sol") or ""))
    try:
        source = flat.read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    if contract and re.search(rf"\blibrary\s+{re.escape(contract)}\b", source):
        return "library"
    schedule = result.get("schedule") if isinstance(result.get("schedule"), dict) else {}
    summary = schedule.get("summary") if isinstance(schedule.get("summary"), dict) else {}
    jobs = int(summary.get("jobs") or 0)
    hints = target.get("units_hint") if isinstance(target.get("units_hint"), list) else []
    if jobs == 0 and not hints and "constructor" in subject_dir.name.lower():
        return "constructor-only/no-scheduled-callable-unit"
    return None


def certificate_evidence(subject_dir: Path, result: dict) -> dict:
    """Summarize retained certification rows without launching ESBMC."""
    rows = read_jsonl(subject_dir / "cert" / "certify-results.jsonl")
    certification = (result.get("certification")
                     if isinstance(result.get("certification"), dict) else {})
    certified_rows = [row for row in rows if row.get("bucket") == "CERTIFIED"
                      or bool(row.get("certified"))]
    witness_rows = [row for row in rows if row.get("witnessed") is True]
    complete_rows = []
    for row in rows:
        encoded = json.dumps(row, sort_keys=True)
        if "COMPLETE-WITNESS" in encoded:
            complete_rows.append(row)
    buckets = certification.get("bucket_counts") or {}
    certified_count = max(
        len(certified_rows),
        int(certification.get("certified_regions") or 0),
        int(buckets.get("CERTIFIED") or 0),
    )
    witness_counts = certification.get("witness_counts") or {}
    witnessed_count = max(len(witness_rows), int(witness_counts.get("true") or 0))
    return {
        "journal": str(subject_dir / "cert" / "certify-results.jsonl"),
        "journal_exists": (subject_dir / "cert" / "certify-results.jsonl").is_file(),
        "row_count": len(rows),
        "certified_count": certified_count,
        "certified_units": sorted({str(row.get("unit") or "")
                                   for row in certified_rows}),
        "witnessed_count": witnessed_count,
        "witnessed_units": sorted({str(row.get("unit") or "")
                                   for row in witness_rows}),
        "complete_witness_count": len(complete_rows),
        "bucket_counts": buckets,
        "driver_diagnostic_tags": certification.get("driver_diagnostic_tags") or {},
        "driver_refusal_tags": certification.get("driver_refusal_tags") or {},
        "timed_out_units": certification.get("timed_out_units") or [],
        "oom_units": certification.get("oom_units") or [],
    }


def replay_evidence(subject_dir: Path, valid_tests: list[dict]) -> dict:
    """Audit replay persistence against retained files and exact origins."""
    manifest_path = subject_dir / "concrete-replays" / "manifest.json"
    manifest = load_manifest(subject_dir)
    entries = [entry for entry in manifest.get("entries") or []
               if isinstance(entry, dict)]
    errors = audit_manifest(subject_dir, manifest)
    if manifest.get("schema") != "veriput-rq1-concrete-replay-manifest/v1":
        errors.append("unexpected or missing replay manifest schema")
    for entry in entries:
        replay_id = str(entry.get("replay_id") or "<missing-id>")
        if entry.get("schema") != "veriput-rq1-concrete-replay/v1":
            errors.append(f"{replay_id}: unexpected replay entry schema")
        if entry.get("valid_reference_test") is not True:
            errors.append(f"{replay_id}: not marked as a valid reference test")
        if entry.get("forge_status") != "Success":
            errors.append(f"{replay_id}: retained Forge status is not Success")
        command = entry.get("forge_command")
        if (not isinstance(command, list) or command[:2] != ["forge", "test"]
                or "--match-test" not in command or "--match-path" not in command):
            errors.append(f"{replay_id}: Forge command is not an exact replay")
    coverage = persistence_coverage(valid_tests, entries)
    complete = bool(coverage.get("complete")) and not errors
    return {
        "manifest": str(manifest_path),
        "manifest_exists": manifest_path.is_file(),
        "entry_count": len(entries),
        "audit_errors": errors,
        "put_basis_missing_count": int(coverage.get("put_basis_missing_count") or 0),
        "complete": complete,
    }


def stage4_evidence(subject_dir: Path) -> dict:
    """Summarize retained PUT candidates and their failed gates."""
    summaries = []
    candidates = []
    for path in sorted((subject_dir / "put").glob("*/put-summary.json")):
        doc = read_json(path)
        deliverable = (doc.get("deliverable_b")
                       if isinstance(doc.get("deliverable_b"), dict) else {})
        quality = (deliverable.get("quality")
                   if isinstance(deliverable.get("quality"), dict) else {})
        summaries.append({
            "path": str(path),
            "b": int(deliverable.get("b") or 0),
            "put_rows": int(quality.get("put_rows") or 0),
            "valid_reference_rows": int(quality.get("valid_reference_rows") or 0),
        })
        rows = deliverable.get("rows") if isinstance(deliverable.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict) or row.get("kind") != "put":
                continue
            gates = row.get("gates") if isinstance(row.get("gates"), dict) else {}
            candidates.append({
                "summary": str(path),
                "unit": row.get("unit"),
                "enc": row.get("enc"),
                "test": row.get("test"),
                "file": row.get("file"),
                "forge_status": row.get("forge_status"),
                "failed_gates": sorted(key for key, value in gates.items() if not value),
                "put_failure_reason": row.get("put_failure_reason"),
            })
    return {
        "summary_count": len(summaries),
        "summaries": summaries,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def classify(result: dict, subject_dir: Path, valid_tests: list[dict],
             cert: dict, logs: dict) -> tuple[str, str, int, dict]:
    """Return a remediation-oriented primary class plus overlapping flags."""
    # pylint: disable=too-many-locals,too-many-return-statements
    sources = {str(row.get("stage2_source") or "") for row in valid_tests}
    checks = {str(row.get("stage2_witness_check") or "") for row in valid_tests}
    kinds = {str(row.get("stage4_kind") or "") for row in valid_tests}
    structural = structural_kind(result, subject_dir)
    manual = any("manual" in value or "source_grounded_callable" in value
                 or "source-grounded-callable" in value
                 or "source_function_revert" in value
                 for value in sources)
    certified = cert["certified_count"] > 0 or any(
        "certified-region" in value for value in sources)
    complete = (cert["complete_witness_count"] > 0
                or any("COMPLETE-WITNESS" in value for value in checks))
    oom = bool(cert["oom_units"] or logs["oom_hits"])
    frontend = bool(logs["frontend_hits"] or any(
        "frontend" in str(tag).lower()
        for tag in cert["driver_diagnostic_tags"]))
    timeout = bool(cert["timed_out_units"]
                   or int(cert["bucket_counts"].get("KILLED") or 0))
    flags = {
        "certified_region": certified,
        "complete_witness_no_region": complete and not certified,
        "manual_replay_basis": manual,
        "structural": bool(structural),
        "timeout_or_killed": timeout,
        "frontend_error": frontend,
        "oom": oom,
        "stage2_sources": sorted(sources),
        "stage2_witness_checks": sorted(checks),
        "stage4_kinds": sorted(kinds),
        "structural_kind": structural,
    }
    if certified:
        return ("certified-ready-for-stage4",
                "certified region retained; inspect/redo Stage4 only", 0, flags)
    if structural:
        return ("structural-constructor-library",
                f"no ordinary callable unit: {structural}", 9, flags)
    if complete:
        return ("complete-witness-no-region",
                "complete witness retained but no certified region", 1, flags)
    if frontend:
        return ("frontend-error",
                "frontend diagnostic blocks trustworthy certification", 2, flags)
    if oom:
        return ("oom",
                "explicit memory exhaustion evidence", 3, flags)
    if timeout:
        return ("timeout",
                "certification killed/timed out without complete witness", 4, flags)
    if manual:
        return ("manual-replay",
                "validity comes from a source-grounded manual replay", 5, flags)
    return ("uncategorized-no-region",
            "valid concrete replay exists but no retained certified region", 6, flags)


def build(args: argparse.Namespace) -> dict:
    """Build a current snapshot over canonical RQ1 result directories."""
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    cases = {}
    for result_path in sorted(args.root.glob("*/subjects/*/result.json")):
        subject_dir = result_path.parent
        subject = subject_dir.name
        canonical, historical = canonical_subject(subject)
        if historical or canonical != subject:
            continue
        bench = subject_dir.parent.parent.name
        cases[f"{bench}/{subject}"] = {
            "bench": bench,
            "subject": subject,
            "last_result_json": str(result_path),
        }
    rows = []
    replay_rows = []
    quality_counts = Counter()
    result_fingerprints = {}
    for case, state_row in sorted(cases.items()):
        result_path = Path(str(state_row.get("last_result_json") or ""))
        result = read_json(result_path)
        if not result:
            raise SystemExit(f"missing/unreadable canonical result for {case}: {result_path}")
        result_fingerprints[result_path] = (
            result_path.stat().st_mtime_ns, result_path.stat().st_size)
        numbers = result_numbers(result)
        valid_count = int(numbers.get("valid") or 0)
        put_count = int(numbers.get("put") or 0)
        r1r2_count = int(numbers.get("r1r2") or 0)
        if valid_count <= 0:
            quality = "NO_VALID"
        elif put_count <= 0:
            quality = "VALID_NO_PUT"
        elif r1r2_count > 0:
            quality = "VALID_PUT_R1R2"
        else:
            quality = "VALID_PUT_NO_R1R2"
        quality_counts[quality] += 1
        detailed = _detailed_test_rows(result)
        valid_tests = [row for row in detailed if _is_valid_reference_test(row)]
        if valid_count <= 0:
            continue
        replay = replay_evidence(result_path.parent, valid_tests)
        replay_rows.append({"case": case, **replay})
        if put_count > 0:
            continue
        subject_doc = (result.get("subject")
                       if isinstance(result.get("subject"), dict) else {})
        flat_path = Path(str(subject_doc.get("flat_sol") or ""))
        try:
            flat_sha256 = hashlib.sha256(flat_path.read_bytes()).hexdigest()
        except OSError:
            flat_sha256 = None
        cert = certificate_evidence(result_path.parent, result)
        logs = log_signals(result_path.parent)
        stage4 = stage4_evidence(result_path.parent)
        category, reason, priority, flags = classify(
            result, result_path.parent, valid_tests, cert, logs)
        rows.append({
            "case": case,
            "bench": str(state_row.get("bench") or case.split("/", 1)[0]),
            "subject": str(state_row.get("subject") or case.split("/", 1)[1]),
            "result_json": str(result_path),
            "result_mtime_ns": result_path.stat().st_mtime_ns,
            "flat_source": str(flat_path),
            "flat_source_exists": flat_path.is_file(),
            "flat_source_sha256": flat_sha256,
            "valid_count": valid_count,
            "put_count": put_count,
            "primary_category": category,
            "primary_reason": reason,
            "priority": priority,
            "valid_units": sorted({str(row.get("unit") or "") for row in valid_tests}),
            "certificate": cert,
            "stage4": stage4,
            "signals": {**flags, **logs},
            "concrete_replay": replay,
        })
    rows.sort(key=lambda row: (row["priority"], row["case"]))
    changed = []
    for path, expected in result_fingerprints.items():
        try:
            observed = (path.stat().st_mtime_ns, path.stat().st_size)
        except OSError:
            observed = None
        if observed != expected:
            changed.append(str(path))
    if changed:
        raise SystemExit(
            "canonical results changed during audit; refusing a mixed snapshot: "
            + ", ".join(changed[:5]))
    category_counts = Counter(row["primary_category"] for row in rows)
    replay_incomplete = [row for row in replay_rows if not row["complete"]]
    return {
        "schema": "veriput-rq1-no-put-inventory/v1",
        "generated_at": time.time(),
        "scope": {
            "result_root": str(args.root),
            "canonical_case_count": len(cases),
            "quality_counts": dict(sorted(quality_counts.items())),
            "strict_valid_count": len(replay_rows),
            "no_put_count": len(rows),
        },
        "category_counts": dict(sorted(category_counts.items())),
        "replay_manifest_audit": {
            "valid_case_count": len(replay_rows),
            "entry_count": sum(row["entry_count"] for row in replay_rows),
            "complete_count": len(replay_rows) - len(replay_incomplete),
            "incomplete_count": len(replay_incomplete),
            "incomplete": replay_incomplete,
        },
        "priority_queue": rows,
    }


def write_tsv(path: Path, rows: list[dict]) -> None:
    """Write a flat priority queue suitable for shell and spreadsheet use."""
    fields = [
        "priority", "primary_category", "case", "valid_count", "valid_units",
        "certified_count", "witnessed_count", "complete_witness_count",
        "timed_out_units", "oom_units", "manual_replay_basis", "frontend_error",
        "replay_complete", "replay_entries", "result_json", "primary_reason",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            cert = row["certificate"]
            signals = row["signals"]
            replay = row["concrete_replay"]
            writer.writerow({
                "priority": row["priority"],
                "primary_category": row["primary_category"],
                "case": row["case"],
                "valid_count": row["valid_count"],
                "valid_units": ",".join(row["valid_units"]),
                "certified_count": cert["certified_count"],
                "witnessed_count": cert["witnessed_count"],
                "complete_witness_count": cert["complete_witness_count"],
                "timed_out_units": ",".join(cert["timed_out_units"]),
                "oom_units": ",".join(cert["oom_units"]),
                "manual_replay_basis": int(signals["manual_replay_basis"]),
                "frontend_error": int(signals["frontend_error"]),
                "replay_complete": int(replay["complete"]),
                "replay_entries": replay["entry_count"],
                "result_json": row["result_json"],
                "primary_reason": row["primary_reason"],
            })


def main() -> None:
    """Parse paths and materialize both machine-readable outputs."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--tsv", type=Path, default=DEFAULT_TSV)
    args = parser.parse_args()
    doc = build(args)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    write_tsv(args.tsv, doc["priority_queue"])
    print(json.dumps({
        "json": str(args.json),
        "tsv": str(args.tsv),
        "scope": doc["scope"],
        "category_counts": doc["category_counts"],
        "replay_manifest_audit": {
            key: value for key, value in doc["replay_manifest_audit"].items()
            if key != "incomplete"
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
