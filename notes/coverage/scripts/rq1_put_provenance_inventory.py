#!/usr/bin/env python3
"""Inventory retained provenance for the fixed RQ1 canonical PUT claims."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_STATE = Path("notes/coverage/rq1_case_state.json")
DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_JSONL = Path("notes/coverage/rq1_put_provenance_inventory.jsonl")
DEFAULT_SUMMARY = Path("notes/coverage/rq1_put_provenance_inventory_summary.json")


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, returning an empty object for unusable input."""
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256(path: Path | None) -> str | None:
    """Return the content digest of an existing regular file."""
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def path_class(path: Path | None, subject_dir: Path, root: Path) -> str:
    """Classify an artifact path by its durability and canonical location."""
    if path is None:
        return "absent"
    resolved = path.resolve()
    if resolved == subject_dir.resolve() or subject_dir.resolve() in resolved.parents:
        return "canonical-subject"
    if resolved == root.resolve() or root.resolve() in resolved.parents:
        return "retained-results"
    if str(resolved).startswith("/tmp/"):
        return "temporary"
    return "external"


def artifact_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the most specific retained valid-artifact list in a result."""
    put = result.get("put") if isinstance(result.get("put"), dict) else {}
    row = result.get("row") if isinstance(result.get("row"), dict) else {}
    # Adopted canonical results intentionally override stale nested summaries.
    values = (row.get("valid_artifacts") or row.get("valid_tests") or
              put.get("valid_artifacts") or put.get("valid_tests") or [])
    return [item for item in values if isinstance(item, dict)]


def is_put(item: dict[str, Any]) -> bool:
    """Return whether an artifact row describes a parameterized unit test."""
    return bool(item.get("is_put") or item.get("kind") == "put")


def is_concrete(item: dict[str, Any]) -> bool:
    """Return whether an artifact row describes a concrete replay."""
    return bool(item.get("is_concrete") or item.get("kind") == "concrete")


def as_path(value: Any) -> Path | None:
    """Convert a non-empty JSON string to a path."""
    return Path(value) if isinstance(value, str) and value else None


def canonical_test_copy(original: Path | None, subject_dir: Path) -> Path | None:
    """Find a content-identical canonical copy of a recorded test."""
    if original is None:
        return None
    candidates = sorted(subject_dir.rglob(original.name))
    if not candidates:
        return None
    original_hash = sha256(original)
    if original_hash:
        equal = [candidate for candidate in candidates
                 if sha256(candidate) == original_hash]
        if equal:
            return equal[0]
    return candidates[0] if len(candidates) == 1 else None


def matching_put_jsons(subject_dir: Path, item: dict[str, Any]) -> list[Path]:
    """Find canonical put.json records with the artifact's full identity."""
    matches: list[Path] = []
    for path in subject_dir.rglob("put.json"):
        doc = read_json(path)
        if doc.get("kind") != "put":
            continue
        if doc.get("unit") != item.get("unit"):
            continue
        if doc.get("enc") != item.get("enc"):
            continue
        if item.get("test") and doc.get("test") != item.get("test"):
            continue
        matches.append(path)
    return sorted(matches)


def cert_unit_rows(cert_jsonl: Path | None, unit: Any) -> int:
    """Count retained Stage-2 certification rows for a Solidity unit."""
    if cert_jsonl is None or not cert_jsonl.is_file() or not unit:
        return 0
    count = 0
    try:
        with cert_jsonl.open() as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and row.get("unit") == unit:
                    count += 1
    except OSError:
        return 0
    return count


def main() -> int:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Build the claim-level JSONL inventory and aggregate summary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    cases = read_json(args.state).get("cases")
    if not isinstance(cases, dict) or len(cases) != 205:
        raise SystemExit("case state must contain the fixed 205 identities")

    records: list[dict[str, Any]] = []
    subject_gaps: list[dict[str, Any]] = []
    for key, case in sorted(cases.items()):
        if not isinstance(case, dict):
            continue
        subject_dir = args.root / str(case.get("bench")) / "subjects" / str(
            case.get("subject"))
        result_path = subject_dir / "result.json"
        result = read_json(result_path)
        row = result.get("row") if isinstance(result.get("row"), dict) else {}
        claimed = int(row.get("put_valid") or 0)
        artifacts = artifact_rows(result)
        puts = [item for item in artifacts if is_put(item)]
        concretes = [item for item in artifacts if is_concrete(item)]
        cert_jsonl = as_path(row.get("cert_jsonl"))
        if cert_jsonl is None:
            candidate = subject_dir / "cert" / "certify-results.jsonl"
            cert_jsonl = candidate if candidate.exists() else None

        for index, item in enumerate(puts, 1):
            original_test = as_path(item.get("file"))
            canonical_test = canonical_test_copy(original_test, subject_dir)
            selected_test = canonical_test or original_test
            original_put = as_path(item.get("put_json"))
            recovered_puts = matching_put_jsons(subject_dir, item)
            selected_put = (original_put if original_put and original_put.is_file()
                            else (recovered_puts[0] if len(recovered_puts) == 1
                                  else None))
            selected_put_doc = read_json(selected_put) if selected_put else {}
            embedded_test = as_path(selected_put_doc.get("file"))
            selected_put_identity = (
                selected_put_doc.get("kind"), selected_put_doc.get("unit"),
                selected_put_doc.get("enc"), selected_put_doc.get("test"))
            expected_put_identity = (
                "put", item.get("unit"), item.get("enc"), item.get("test"))
            emit_dir = selected_put.parent / "emit" if selected_put else None
            cov_report = emit_dir / "cov-report.json" if emit_dir else None
            journal = emit_dir / "cov-ce-journal.json" if emit_dir else None
            generalise = emit_dir / "generalise-result.json" if emit_dir else None
            sibling_concretes = [
                sibling for sibling in concretes
                if sibling.get("unit") == item.get("unit")
            ]
            sibling_path_rows = []
            for sibling in sibling_concretes:
                sibling_original = as_path(sibling.get("file"))
                sibling_canonical = canonical_test_copy(sibling_original,
                                                        subject_dir)
                sibling_selected = sibling_canonical or sibling_original
                sibling_path_rows.append({
                    "recorded_path": (str(sibling_original)
                                      if sibling_original else None),
                    "recorded_path_class": path_class(sibling_original,
                                                       subject_dir, args.root),
                    "canonical_copy": (str(sibling_canonical)
                                       if sibling_canonical else None),
                    "selected_retained_path": (str(sibling_selected)
                                               if sibling_selected else None),
                    "selected_exists": bool(sibling_selected and
                                            sibling_selected.is_file()),
                    "selected_path_class": path_class(sibling_selected,
                                                       subject_dir, args.root),
                })
            stable_test = (selected_test is not None and selected_test.is_file() and
                           path_class(selected_test, subject_dir, args.root) in
                           ("canonical-subject", "retained-results"))
            stable_put = (selected_put is not None and selected_put.is_file() and
                          path_class(selected_put, subject_dir, args.root) in
                          ("canonical-subject", "retained-results"))
            has_sidecar = bool((cov_report and cov_report.is_file()) or
                               (journal and journal.is_file()) or
                               (generalise and generalise.is_file()))
            if stable_test and stable_put and has_sidecar:
                reconstructability = "full-provenance-retained"
            elif stable_test and stable_put:
                reconstructability = "put-record-and-test-retained"
            elif stable_test:
                reconstructability = "final-test-only"
            else:
                reconstructability = "not-retained"
            records.append({
                "schema": "veriput-rq1-put-provenance/v1",
                "record_kind": "artifact",
                "case": key,
                "bench": case.get("bench"),
                "subject": case.get("subject"),
                "claim_index_within_artifacts": index,
                "authoritative_subject_put_valid": claimed,
                "artifact_put_records": len(puts),
                "result_json": str(result_path),
                "unit": item.get("unit"),
                "enc": item.get("enc"),
                "test": item.get("test"),
                "forge_status": item.get("forge_status"),
                "valid_reference_test": item.get("valid_reference_test"),
                "stage2_source": item.get("stage2_source"),
                "stage4_kind": item.get("stage4_kind"),
                "final_test": {
                    "recorded_path": str(original_test) if original_test else None,
                    "recorded_exists": bool(original_test and original_test.is_file()),
                    "recorded_path_class": path_class(original_test, subject_dir,
                                                       args.root),
                    "recorded_sha256": sha256(original_test),
                    "canonical_copy": str(canonical_test) if canonical_test else None,
                    "canonical_copy_exists": bool(canonical_test and
                                                  canonical_test.is_file()),
                    "canonical_copy_sha256": sha256(canonical_test),
                    "selected_retained_path": str(selected_test) if selected_test else None,
                    "selected_path_class": path_class(selected_test, subject_dir,
                                                       args.root),
                },
                "put_json": {
                    "recorded_path": str(original_put) if original_put else None,
                    "recorded_exists": bool(original_put and original_put.is_file()),
                    "recorded_path_class": path_class(original_put, subject_dir,
                                                       args.root),
                    "recovered_matches": [str(path) for path in recovered_puts],
                    "selected_retained_path": str(selected_put) if selected_put else None,
                    "selected_exists": bool(selected_put and selected_put.is_file()),
                    "selected_path_class": path_class(selected_put, subject_dir,
                                                       args.root),
                    "selected_sha256": sha256(selected_put),
                    "selected_identity_matches": (
                        selected_put_identity == expected_put_identity),
                    "embedded_generated_file": (str(embedded_test)
                                                if embedded_test else None),
                    "embedded_generated_file_exists": bool(
                        embedded_test and embedded_test.is_file()),
                },
                "certificate": {
                    "certify_results_jsonl": str(cert_jsonl) if cert_jsonl else None,
                    "certify_results_exists": bool(cert_jsonl and cert_jsonl.is_file()),
                    "matching_unit_rows": cert_unit_rows(cert_jsonl, item.get("unit")),
                    "cov_report": str(cov_report) if cov_report else None,
                    "cov_report_exists": bool(cov_report and cov_report.is_file()),
                    "cov_ce_journal": str(journal) if journal else None,
                    "cov_ce_journal_exists": bool(journal and journal.is_file()),
                    "generalise_result": str(generalise) if generalise else None,
                    "generalise_result_exists": bool(generalise and
                                                     generalise.is_file()),
                },
                "independent_concrete_replay": {
                    "exists_for_same_unit": bool(sibling_concretes),
                    "count_for_same_unit": len(sibling_concretes),
                    "artifacts": sibling_path_rows,
                    "all_selected_paths_exist": bool(sibling_path_rows) and all(
                        sibling["selected_exists"] for sibling in sibling_path_rows),
                    "all_selected_paths_retained": bool(sibling_path_rows) and all(
                        sibling["selected_path_class"] in
                        ("canonical-subject", "retained-results")
                        for sibling in sibling_path_rows),
                    "note": ("A sibling concrete replay is not the proof basis of the "
                             "PUT; certified-region Stage 2 provenance is."),
                },
                "reconstructability": reconstructability,
            })

        gap = claimed - len(puts)
        if gap > 0:
            subject_gaps.append({
                "case": key,
                "authoritative_subject_put_valid": claimed,
                "artifact_put_records": len(puts),
                "unresolved_claims": gap,
            })
            for ordinal in range(1, gap + 1):
                records.append({
                    "schema": "veriput-rq1-put-provenance/v1",
                    "record_kind": "aggregate-claim-gap",
                    "case": key,
                    "bench": case.get("bench"),
                    "subject": case.get("subject"),
                    "gap_ordinal": ordinal,
                    "authoritative_subject_put_valid": claimed,
                    "artifact_put_records": len(puts),
                    "result_json": str(result_path),
                    "final_test": None,
                    "put_json": None,
                    "certificate": None,
                    "independent_concrete_replay": None,
                    "reconstructability": "not-individually-identifiable",
                })

    counts: Counter[str] = Counter()
    artifact_records = [record for record in records
                        if record["record_kind"] == "artifact"]
    for record in artifact_records:
        counts[f"test_recorded_{record['final_test']['recorded_path_class']}"] += 1
        counts[f"test_selected_{record['final_test']['selected_path_class']}"] += 1
        counts[f"put_selected_{record['put_json']['selected_path_class']}"] += 1
        counts[f"reconstructability_{record['reconstructability']}"] += 1
        counts["final_test_selected_exists"] += int(bool(
            record["final_test"]["selected_retained_path"] and
            Path(record["final_test"]["selected_retained_path"]).is_file()))
        counts["put_json_selected_exists"] += int(
            record["put_json"]["selected_exists"])
        counts["put_json_identity_matches"] += int(
            record["put_json"]["selected_identity_matches"])
        counts["put_json_embedded_generated_file_exists"] += int(
            record["put_json"]["embedded_generated_file_exists"])
        counts["cov_report_exists"] += int(
            record["certificate"]["cov_report_exists"])
        counts["cov_ce_journal_exists"] += int(
            record["certificate"]["cov_ce_journal_exists"])
        counts["certify_results_exists"] += int(
            record["certificate"]["certify_results_exists"])
        counts["same_unit_concrete_replay"] += int(
            record["independent_concrete_replay"]["exists_for_same_unit"])
        counts["same_unit_concrete_replay_retained"] += int(
            record["independent_concrete_replay"][
                "all_selected_paths_retained"])

    concrete_by_case: dict[str, list[bool]] = {}
    for record in artifact_records:
        concrete_by_case.setdefault(record["case"], []).append(
            record["independent_concrete_replay"]["exists_for_same_unit"])

    summary = {
        "schema": "veriput-rq1-put-provenance-summary/v1",
        "case_state": str(args.state),
        "canonical_cases": len(cases),
        "authoritative_put_valid_claims": sum(
            int((read_json(args.root / str(case.get("bench")) / "subjects" /
                           str(case.get("subject")) / "result.json").get("row") or
                 {}).get("put_valid") or 0)
            for case in cases.values() if isinstance(case, dict)),
        "individually_identified_put_artifacts": len(artifact_records),
        "aggregate_claim_gaps": len(records) - len(artifact_records),
        "jsonl_records": len(records),
        "put_bearing_subjects": len(concrete_by_case),
        "same_unit_concrete_replay_subjects": {
            "all_puts_have_one": sum(all(values)
                                     for values in concrete_by_case.values()),
            "some_puts_have_one": sum(any(values) and not all(values)
                                      for values in concrete_by_case.values()),
            "no_put_has_one": sum(not any(values)
                                   for values in concrete_by_case.values()),
        },
        "counts": dict(sorted(counts.items())),
        "subjects_with_aggregate_claim_gaps": subject_gaps,
        "semantic_note": (
            "A PUT is derived from a Stage-2 certified region. A sibling concrete "
            "replay, when present, is a separate fallback artifact and is not the "
            "proof basis of that PUT."),
    }
    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.jsonl.write_text("".join(json.dumps(record, sort_keys=True) + "\n"
                                  for record in records))
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
