#!/usr/bin/env python3
"""Transactionally remove unauthenticated RQ3 fallbacks from deliverables.

The generated tests and their Forge observations are retained as diagnostic
evidence.  Only their publication scope changes: they no longer contribute to
the RQ3 raw or valid deliverable sets.
"""

# pylint: disable=missing-function-docstring,too-many-locals,too-many-statements
# pylint: disable=wrong-import-position

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import rq3_persistence_republish as transaction  # noqa: E402
from rq1_veriput_run import _legacy_quality_bucket  # noqa: E402
from run_rq3_no_cer_reg import audit_output  # noqa: E402

DATASETS = ("bugfix124", "peer182", "real203")
DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg")
DEFAULT_PARTITION = Path("/home/samson/workspace/VeriPUT/Results/RQ3/adoption-bundles/"
                         "rq3-raw-authenticity-20260815/partition.json")
PARTITION_SHA256 = "c0ecd6798553f577d911be2e708dfaf04c3ba3b645449f938d2d580a46ddc6b1"
CLASSIFICATION = "unauthenticated_fallback_diagnostic"
EXPECTED = {
    "results": 547,
    "cases": 17,
    "diagnostics": 66,
    "raw_before": 2993,
    "valid_before": 2639,
    "raw_only_before": 354,
    "raw_after": 2927,
    "valid_after": 2639,
    "raw_only_after": 288,
}


class ScopeCorrectionError(RuntimeError):
    """A sealed input, transaction condition, or postcondition did not hold."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(payload).hexdigest()


def _artifact_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("file") or ""), str(row.get("test") or "")


def _target_key(row: dict[str, Any]) -> tuple[str, str]:
    artifact = row.get("artifact_identity") or {}
    return str(artifact.get("file") or ""), str(artifact.get("test") or "")


def _set_sha256(values: set[tuple[str, str]]) -> str:
    return transaction._set_sha256(values)  # pylint: disable=protected-access


def _require_descendant(path: Path, root: Path, label: str) -> Path:
    canonical = path.expanduser().resolve()
    try:
        canonical.relative_to(root)
    except ValueError as error:
        raise ScopeCorrectionError(f"{label} escapes the locked RQ3 root: {path}") from error
    if canonical != path:
        raise ScopeCorrectionError(f"{label} is not canonical: {path}")
    return canonical


def _load_partition(path: Path, root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    claimed = document.get("partition_sha256")
    digest_document = dict(document)
    digest_document.pop("partition_sha256", None)
    observed = _canonical_sha256(digest_document)
    if claimed != PARTITION_SHA256 or observed != PARTITION_SHA256:
        raise ScopeCorrectionError(
            f"partition seal mismatch: claimed={claimed} observed={observed}")
    if document.get("schema") != "veriput-rq3-raw-authenticity-partition/v1":
        raise ScopeCorrectionError("unexpected partition schema")
    if Path(document.get("root") or "").resolve() != root:
        raise ScopeCorrectionError("partition root differs from locked RQ3 root")
    rows = [
        row for row in document.get("rows") or []
        if isinstance(row, dict) and row.get("classification") == CLASSIFICATION
    ]
    if len(rows) != EXPECTED["diagnostics"]:
        raise ScopeCorrectionError(f"expected 66 diagnostic rows, found {len(rows)}")
    keys = [_target_key(row) for row in rows]
    if any(not file_name or not test_name for file_name, test_name in keys):
        raise ScopeCorrectionError("partition contains an empty artifact identity")
    if len(set(keys)) != len(keys):
        raise ScopeCorrectionError("partition diagnostic artifact identities are not unique")
    case_keys = {(str(row.get("identity", {}).get("dataset")
                      or ""), str(row.get("identity", {}).get("case") or ""))
                 for row in rows}
    if len(case_keys) != EXPECTED["cases"]:
        raise ScopeCorrectionError(f"expected 17 diagnostic cases, found {len(case_keys)}")
    return document, rows


def _scope_metadata(count: int) -> dict[str, Any]:
    return {
        "schema": "veriput-rq3-diagnostic-scope-correction/v1",
        "classification": CLASSIFICATION,
        "partition_sha256": PARTITION_SHA256,
        "diagnostic_count": count,
        "generated_test_retained": True,
        "forge_observation_retained": True,
        "published_as_deliverable": False,
    }


def _diagnostic_artifact(raw: dict[str, Any]) -> dict[str, Any]:
    diagnostic = copy.deepcopy(raw)
    diagnostic["diagnostic_original_kind"] = diagnostic.get("kind")
    diagnostic["kind"] = "diagnostic"
    diagnostic["diagnostic_classification"] = CLASSIFICATION
    diagnostic["diagnostic_partition_sha256"] = PARTITION_SHA256
    diagnostic["published_as_deliverable"] = False
    return diagnostic


def _counter(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for value in row.get(field) or []:
            counts[str(value)] += 1
    return dict(sorted(counts.items()))


def _combo_counter(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        combo = str(row.get("oracle_combo_tag") or "")
        if combo:
            counts[combo] += 1
    return dict(sorted(counts.items()))


def _recount_summary(summary: dict[str, Any]) -> dict[str, Any]:
    raw = [row for row in summary.get("raw_artifacts") or [] if isinstance(row, dict)]
    valid = [row for row in summary.get("valid_artifacts") or [] if isinstance(row, dict)]
    summary["raw_tests"] = copy.deepcopy(raw)
    summary["raw_artifacts"] = copy.deepcopy(raw)
    summary["valid_tests"] = copy.deepcopy(valid)
    summary["valid_artifacts"] = copy.deepcopy(valid)
    summary["raw"] = len(raw)
    summary["valid"] = len(valid)
    summary["put_raw"] = sum(row.get("kind") == "put" for row in raw)
    summary["put_valid"] = sum(row.get("kind") == "put" for row in valid)
    summary["concrete_raw"] = sum(row.get("kind") == "concrete" for row in raw)
    summary["concrete_valid"] = sum(row.get("kind") == "concrete" for row in valid)
    summary["valid_concrete"] = summary["concrete_valid"]
    summary["raw_oracle_tag_counts"] = _counter(raw, "oracle_tags")
    summary["valid_oracle_tag_counts"] = _counter(valid, "oracle_tags")
    summary["raw_oracle_combo_counts"] = _combo_counter(raw)
    summary["valid_oracle_combo_counts"] = _combo_counter(valid)
    summary["rq1_oracle_tag_counts"] = copy.deepcopy(summary["valid_oracle_tag_counts"])
    summary["rq1_oracle_combo_counts"] = copy.deepcopy(summary["valid_oracle_combo_counts"])
    summary["artifact_counts"] = {
        "raw": summary["raw"],
        "valid": summary["valid"],
        "put_raw": summary["put_raw"],
        "put_valid": summary["put_valid"],
        "concrete_raw": summary["concrete_raw"],
        "concrete_valid": summary["concrete_valid"],
        "valid_put_with_R1": int(summary.get("valid_put_with_R1") or 0),
        "valid_put_with_R2": int(summary.get("valid_put_with_R2") or 0),
        "valid_put_with_R1_or_R2": int(summary.get("valid_put_with_R1_or_R2") or 0),
        "valid_put_without_R1R2": int(summary.get("valid_put_without_R1R2") or 0),
    }
    summary["quality_bucket"] = _legacy_quality_bucket(summary)
    summary["raw_artifacts_retained"] = bool(raw)
    summary["valid_artifacts_retained"] = bool(valid)
    return summary


def scope_correct_result_summary(summary: dict[str, Any],
                                 target_keys: set[tuple[str, str]]) -> dict[str, Any]:
    """Move exact raw-only target artifacts into diagnostic metadata."""

    candidate = copy.deepcopy(summary)
    raw = [row for row in candidate.get("raw_artifacts") or [] if isinstance(row, dict)]
    raw_tests = [row for row in candidate.get("raw_tests") or [] if isinstance(row, dict)]
    valid = [row for row in candidate.get("valid_artifacts") or [] if isinstance(row, dict)]
    valid_tests = [row for row in candidate.get("valid_tests") or [] if isinstance(row, dict)]
    raw_hits = [row for row in raw if _artifact_key(row) in target_keys]
    raw_test_hits = [row for row in raw_tests if _artifact_key(row) in target_keys]
    if (len(raw_hits) != len(target_keys) or {_artifact_key(row)
                                              for row in raw_hits} != target_keys):
        raise ScopeCorrectionError("result raw artifacts do not contain the exact target set")
    if (len(raw_test_hits) != len(target_keys) or {_artifact_key(row)
                                                   for row in raw_test_hits} != target_keys):
        raise ScopeCorrectionError("result raw tests do not contain the exact target set")
    if any(_artifact_key(row) in target_keys for row in valid + valid_tests):
        raise ScopeCorrectionError("diagnostic partition unexpectedly intersects current valid set")
    if any(row.get("oracle_classes") for row in raw_hits):
        raise ScopeCorrectionError("diagnostic target unexpectedly carries an R1/R2 oracle class")
    candidate["raw_artifacts"] = [row for row in raw if _artifact_key(row) not in target_keys]
    candidate["valid_artifacts"] = valid
    unpublished = [
        row for row in candidate.get("unpublished_valid_tests") or [] if isinstance(row, dict)
    ]
    candidate["unpublished_valid_tests"] = [
        row for row in unpublished if _artifact_key(row) not in target_keys
    ]
    existing = [row for row in candidate.get("diagnostic_artifacts") or [] if isinstance(row, dict)]
    candidate["diagnostic_artifacts"] = existing + [_diagnostic_artifact(row) for row in raw_hits]
    candidate["diagnostic_scope_correction"] = _scope_metadata(len(raw_hits))
    candidate = _recount_summary(candidate)
    if (candidate["raw"] == 0 and candidate["valid"] == 0
            and candidate.get("status") == "persistence-error"):
        reason = ("no authenticated RQ3 deliverable; unauthenticated fallback "
                  "retained as diagnostic")
        candidate["status"] = "no-output"
        candidate["completion_status"] = "no-output"
        candidate["reason"] = reason
        candidate["failure_reason"] = reason
        candidate["partial_failure_reason"] = None
        candidate["persistence_failure_reason"] = None
    return candidate


def _refresh_persistence_coverage(case_dir: Path, old: dict[str, Any], corrected: dict[str, Any],
                                  target_keys: set[tuple[str, str]]) -> None:
    if not isinstance(old.get("concrete_replay_persistence"), dict):
        return
    candidates = [
        row for row in ((corrected.get("valid_artifacts") or []) +
                        (corrected.get("unpublished_valid_tests") or [])) if isinstance(row, dict)
    ]
    coverage = transaction._rebuild_coverage(  # pylint: disable=protected-access
        case_dir, old, candidates)
    coverage["persistence_errors"] = [
        error for error in coverage.get("persistence_errors") or []
        if not isinstance(error, dict) or _artifact_key(error) not in target_keys
    ]
    corrected["concrete_replay_persistence"] = coverage
    corrected["persistence_failure_reason"] = (transaction.persistence_publication_failure(coverage)
                                               if candidates else None)


def scope_correct_result_document(document: dict[str, Any], case_dir: Path,
                                  target_keys: set[tuple[str, str]]) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    candidate["put"] = scope_correct_result_summary(candidate.get("put") or {}, target_keys)
    old_row = candidate.get("row") or {}
    candidate["row"] = scope_correct_result_summary(old_row, target_keys)
    _refresh_persistence_coverage(case_dir, old_row, candidate["row"], target_keys)
    if "concrete_replay_persistence" in candidate["row"]:
        candidate["concrete_replay_persistence"] = copy.deepcopy(
            candidate["row"]["concrete_replay_persistence"])
        candidate["persistence_publication_failure"] = candidate["row"].get(
            "persistence_failure_reason")
    return candidate


def scope_correct_put_json(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    if candidate.get("kind") != "concrete" or _artifact_key(candidate) != _target_key(target):
        raise ScopeCorrectionError("put.json does not match its sealed concrete artifact identity")
    candidate["diagnostic_original_kind"] = "concrete"
    candidate["kind"] = "diagnostic"
    candidate["diagnostic_classification"] = CLASSIFICATION
    candidate["diagnostic_partition_sha256"] = PARTITION_SHA256
    candidate["published_as_deliverable"] = False
    return candidate


def scope_correct_put_summary(document: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(document)
    deliverable = candidate.get("deliverable_b") or {}
    rows = [row for row in deliverable.get("rows") or [] if isinstance(row, dict)]
    key = _target_key(target)
    matches = [row for row in rows if _artifact_key(row) == key]
    if len(matches) != 1 or matches[0].get("kind") != "concrete":
        raise ScopeCorrectionError("put-summary does not contain one exact concrete target row")
    target_row = matches[0]
    deliverable["rows"] = [row for row in rows if _artifact_key(row) != key]
    diagnostic = _diagnostic_artifact(target_row)
    diagnostics = [row for row in candidate.get("diagnostic_rows") or [] if isinstance(row, dict)]
    candidate["diagnostic_rows"] = diagnostics + [diagnostic]
    emission = candidate.get("emission") or {}
    concrete = int(emission.get("concrete_replays_emitted") or 0)
    if concrete <= 0:
        raise ScopeCorrectionError("put-summary concrete emission counter cannot be decremented")
    emission["concrete_replays_emitted"] = concrete - 1
    validity = deliverable.get("valid_reference_tests") or {}
    if target_row.get("valid_reference_test") is True:
        current = int(validity.get("concrete") or 0)
        total = int(validity.get("total") or 0)
        if current <= 0 or total <= 0:
            raise ScopeCorrectionError("put-summary valid counter cannot be decremented")
        validity["concrete"] = current - 1
        validity["total"] = total - 1
    candidate["diagnostic_scope_correction"] = _scope_metadata(1)
    return candidate


def _summary_path(put_json: Path) -> Path:
    return put_json.parents[2] / "put-summary.json"


def _result_snapshot(root: Path) -> dict[Path, dict[str, Any]]:
    snapshot: dict[Path, dict[str, Any]] = {}
    for dataset in DATASETS:
        for path in sorted((root / dataset / "subjects").glob("*/result.json")):
            snapshot[path] = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    if len(snapshot) != EXPECTED["results"]:
        raise ScopeCorrectionError(f"expected 547 result.json files, found {len(snapshot)}")
    return snapshot


def _journal_with_updates(data: bytes, updates: list[dict[str, Any]]) -> bytes:
    if data and not data.endswith(b"\n"):
        data += b"\n"
    return data + b"".join((json.dumps(row, sort_keys=True) + "\n").encode() for row in updates)


def _validate_partition_artifact(row: dict[str, Any], root: Path) -> tuple[Path, Path, Path]:
    identity = row.get("identity") or {}
    dataset = str(identity.get("dataset") or "")
    case = str(identity.get("case") or "")
    if dataset not in DATASETS or not case:
        raise ScopeCorrectionError("partition row has an invalid dataset/case identity")
    result_path = _require_descendant(Path(row.get("paths", {}).get("result_json") or ""), root,
                                      "result.json")
    expected_result = root / dataset / "subjects" / case / "result.json"
    if result_path != expected_result:
        raise ScopeCorrectionError("partition result path differs from dataset/case identity")
    put_json = _require_descendant(Path(row.get("paths", {}).get("put_json") or ""), root,
                                   "put.json")
    generated = _require_descendant(Path(row.get("paths", {}).get("generated_source") or ""), root,
                                    "generated source")
    hashes = row.get("hashes") or {}
    if transaction._sha256(put_json) != hashes.get("put_json_sha256"):  # pylint: disable=protected-access
        raise ScopeCorrectionError(f"sealed put.json hash mismatch: {put_json}")
    if transaction._sha256(generated) != hashes.get("generated_source_sha256"):  # pylint: disable=protected-access
        raise ScopeCorrectionError(f"sealed generated source hash mismatch: {generated}")
    return result_path, put_json, generated


def build_plan(root: Path, partition_path: Path, bundle: Path) -> dict[str, Any]:
    root = root.resolve()
    partition_path = partition_path.expanduser().resolve()
    bundle = bundle.expanduser().resolve()
    _, rows = _load_partition(partition_path, root)
    snapshot = _result_snapshot(root)
    replacements: dict[Path, dict[str, Any]] = {}
    writes: dict[Path, bytes] = {}
    preimages = {
        path: transaction._sha256(path)  # pylint: disable=protected-access
        for path in snapshot
    }
    preimages[partition_path] = transaction._sha256(  # pylint: disable=protected-access
        partition_path)
    grouped: dict[Path, list[dict[str, Any]]] = {}
    source_hashes = {}
    for row in rows:
        result_path, put_json, generated = _validate_partition_artifact(row, root)
        grouped.setdefault(result_path, []).append(row)
        source_hashes[str(generated)] = transaction._sha256(  # pylint: disable=protected-access
            generated)
        preimages[generated] = source_hashes[str(generated)]
        preimages[put_json] = transaction._sha256(  # pylint: disable=protected-access
            put_json)
        put_document = json.loads(put_json.read_text(encoding="utf-8", errors="strict"))
        writes[put_json] = transaction._json_bytes(  # pylint: disable=protected-access
            scope_correct_put_json(put_document, row))
        summary_path = _summary_path(put_json)
        preimages[summary_path] = transaction._sha256(  # pylint: disable=protected-access
            summary_path)
        summary = json.loads(summary_path.read_text(encoding="utf-8", errors="strict"))
        writes[summary_path] = transaction._json_bytes(  # pylint: disable=protected-access
            scope_correct_put_summary(summary, row))

    for result_path, case_rows in grouped.items():
        targets = {_target_key(row) for row in case_rows}
        document = scope_correct_result_document(snapshot[result_path], result_path.parent, targets)
        replacements[result_path] = document
        writes[result_path] = transaction._json_bytes(document)  # pylint: disable=protected-access

    old_raw, old_valid = transaction._global_sets(  # pylint: disable=protected-access
        root, snapshot=snapshot)
    new_raw, new_valid = transaction._global_sets(  # pylint: disable=protected-access
        root, replacements=replacements, snapshot=snapshot)
    target_keys = {_target_key(row) for row in rows}
    observed = {
        "results": len(snapshot),
        "cases": len(grouped),
        "diagnostics": len(target_keys),
        "raw_before": len(old_raw),
        "valid_before": len(old_valid),
        "raw_only_before": len(old_raw - old_valid),
        "raw_after": len(new_raw),
        "valid_after": len(new_valid),
        "raw_only_after": len(new_raw - new_valid),
    }
    if observed != EXPECTED:
        raise ScopeCorrectionError(f"frozen population mismatch: {observed}")
    if not target_keys <= old_raw - old_valid:
        raise ScopeCorrectionError(
            "diagnostic partition is not an exact subset of current raw-only")
    if old_raw - new_raw != target_keys or new_valid != old_valid:
        raise ScopeCorrectionError("scope correction changed identities outside the sealed targets")

    for dataset in DATASETS:
        journal = root / dataset / "results.jsonl"
        manifest = root / dataset / "manifest.json"
        preimages[journal] = transaction._sha256(journal)  # pylint: disable=protected-access
        preimages[manifest] = transaction._sha256(  # pylint: disable=protected-access
            manifest)
        updates = [
            replacements.get(path, document)["row"]
            for path, document in sorted(snapshot.items(), key=lambda item: str(item[0]))
            if path.parts[-4] == dataset
        ]
        journal_data = _journal_with_updates(journal.read_bytes(), updates)
        writes[journal] = journal_data
        new_manifest = transaction._dataset_manifest(  # pylint: disable=protected-access
            dataset, journal, journal_data)
        current_manifest = json.loads(manifest.read_text(encoding="utf-8", errors="strict"))
        new_manifest["generated_at"] = current_manifest.get("generated_at")
        writes[manifest] = transaction._json_bytes(new_manifest)  # pylint: disable=protected-access

    audit_path = root / "audit.json"
    preimages[audit_path] = transaction._sha256(  # pylint: disable=protected-access
        audit_path)
    result_seals = {str(path): preimages[path] for path in sorted(snapshot)}
    serializable = {
        "schema": "veriput-rq3-diagnostic-scope-correction-plan/v1",
        "root": str(root),
        "partition": str(partition_path),
        "partition_sha256": PARTITION_SHA256,
        "bundle": str(bundle),
        "expected": EXPECTED,
        "observed": observed,
        "target_keys_sha256": _set_sha256(target_keys),
        "old_raw_sha256": _set_sha256(old_raw),
        "old_valid_sha256": _set_sha256(old_valid),
        "old_raw_only_sha256": _set_sha256(old_raw - old_valid),
        "new_raw_sha256": _set_sha256(new_raw),
        "new_valid_sha256": _set_sha256(new_valid),
        "new_raw_only_sha256": _set_sha256(new_raw - new_valid),
        "result_snapshot": result_seals,
        "result_snapshot_sha256": transaction._mapping_sha256(result_seals),  # pylint: disable=protected-access
        "source_hashes": dict(sorted(source_hashes.items())),
        "preimages": {
            str(path): digest
            for path, digest in sorted(preimages.items(), key=lambda item: str(item[0]))
        },
        "writes": {
            str(path): {
                "sha256": transaction._sha256_bytes(data),  # pylint: disable=protected-access
                "bytes": len(data),
            }
            for path, data in sorted(writes.items(), key=lambda item: str(item[0]))
        },
    }
    serializable["plan_sha256"] = transaction._sha256_bytes(  # pylint: disable=protected-access
        transaction._json_bytes(serializable))  # pylint: disable=protected-access
    serializable["_write_bytes"] = writes
    return serializable


def _stage(plan: dict[str, Any], bundle: Path) -> None:
    root = Path(plan["root"])
    for path, data in plan["_write_bytes"].items():
        relative = path.relative_to(root)
        transaction._atomic_write(  # pylint: disable=protected-access
            bundle / "staged" / relative, data)
    serializable = {key: value for key, value in plan.items() if key != "_write_bytes"}
    transaction._atomic_write(  # pylint: disable=protected-access
        bundle / "plan.json", transaction._json_bytes(serializable))  # pylint: disable=protected-access


def _verify_plan(plan: dict[str, Any]) -> None:
    serializable = {key: value for key, value in plan.items() if key != "_write_bytes"}
    seal = serializable.pop("plan_sha256", None)
    if seal != transaction._sha256_bytes(  # pylint: disable=protected-access
            transaction._json_bytes(serializable)):  # pylint: disable=protected-access
        raise ScopeCorrectionError("plan seal mismatch")
    observed_writes = {
        str(path): {
            "sha256": transaction._sha256_bytes(data),  # pylint: disable=protected-access
            "bytes": len(data),
        }
        for path, data in sorted(plan["_write_bytes"].items(), key=lambda item: str(item[0]))
    }
    if observed_writes != plan.get("writes"):
        raise ScopeCorrectionError("write bytes differ from sealed plan")


def _verify_preimages(plan: dict[str, Any]) -> None:
    for raw_path, digest in plan["preimages"].items():
        path = Path(raw_path)
        if not path.is_file() or transaction._sha256(  # pylint: disable=protected-access
                path) != digest:
            raise ScopeCorrectionError(f"compare-before-write mismatch: {path}")
    result_snapshot = plan.get("result_snapshot") or {}
    if transaction._mapping_sha256(result_snapshot) != plan.get(  # pylint: disable=protected-access
            "result_snapshot_sha256"):
        raise ScopeCorrectionError("result snapshot seal mismatch")


def _backup(plan: dict[str, Any], bundle: Path) -> dict[str, dict[str, str]]:
    root = Path(plan["root"])
    targets = set(plan["_write_bytes"]) | {root / "audit.json"}
    backups = {}
    for path in sorted(targets):
        relative = path.relative_to(root)
        backup = bundle / "rollback" / relative
        expected = plan["preimages"].get(str(path))
        if expected is None or transaction._sha256(  # pylint: disable=protected-access
                path) != expected:
            raise ScopeCorrectionError(f"backup differs from sealed preimage: {path}")
        transaction._atomic_write(backup, path.read_bytes())  # pylint: disable=protected-access
        backups[str(path)] = {
            "path": str(backup),
            "sha256": transaction._sha256(backup),  # pylint: disable=protected-access
        }
    return backups


def _verify_journals(root: Path) -> None:
    for dataset in DATASETS:
        latest = transaction._latest_rows(  # pylint: disable=protected-access
            (root / dataset / "results.jsonl").read_bytes())
        results = sorted((root / dataset / "subjects").glob("*/result.json"))
        if len(latest) != len(results):
            raise ScopeCorrectionError(f"journal/result population mismatch for {dataset}")
        for result_path in results:
            row = json.loads(result_path.read_text(encoding="utf-8", errors="strict"))["row"]
            if latest.get(transaction._row_key(row)) != row:  # pylint: disable=protected-access
                raise ScopeCorrectionError(f"journal latest row differs from result: {result_path}")


def _post_audit(plan: dict[str, Any]) -> dict[str, Any]:
    root = Path(plan["root"])
    audit = audit_output(root)
    wanted = {
        "raw_tests": EXPECTED["raw_after"],
        "valid_tests": EXPECTED["valid_after"],
        "raw_only_count": EXPECTED["raw_only_after"],
        "valid_only_count": 0,
    }
    observed = {key: audit.get(key) for key in wanted}
    if observed != wanted or audit.get("ok") is not False:
        raise ScopeCorrectionError(f"post-correction audit mismatch: {observed}")
    raw, valid = transaction._global_sets(root)  # pylint: disable=protected-access
    if (_set_sha256(raw) != plan["new_raw_sha256"] or _set_sha256(valid) != plan["new_valid_sha256"]
            or _set_sha256(raw - valid) != plan["new_raw_only_sha256"]):
        raise ScopeCorrectionError("post-correction identity set hash mismatch")
    manifests = [
        json.loads((root / dataset / "manifest.json").read_text(encoding="utf-8", errors="strict"))
        for dataset in DATASETS
    ]
    if (sum(int(doc["summary"]["raw"]) for doc in manifests) != EXPECTED["raw_after"]
            or sum(int(doc["summary"]["valid"]) for doc in manifests) != EXPECTED["valid_after"]):
        raise ScopeCorrectionError("dataset manifests disagree with corrected global totals")
    _verify_journals(root)
    audit["diagnostic_scope_correction"] = {
        **_scope_metadata(EXPECTED["diagnostics"]),
        "plan_sha256": plan["plan_sha256"],
        "target_keys_sha256": plan["target_keys_sha256"],
        "raw_before": EXPECTED["raw_before"],
        "raw_after": EXPECTED["raw_after"],
        "valid_before": EXPECTED["valid_before"],
        "valid_after": EXPECTED["valid_after"],
    }
    return audit


def _apply_locked(plan: dict[str, Any], bundle: Path) -> dict[str, Any]:
    _verify_plan(plan)
    _verify_preimages(plan)
    backups = _backup(plan, bundle)
    tx_doc = {
        "schema": "veriput-rq3-diagnostic-scope-correction-transaction/v1",
        "state": "applying",
        "root": plan["root"],
        "partition_sha256": PARTITION_SHA256,
        "plan_sha256": plan["plan_sha256"],
        "backups": backups,
        "backups_sha256": transaction._mapping_sha256(backups),  # pylint: disable=protected-access
        "allowed_recovery_hashes": {
            target:
            sorted({
                record["sha256"],
                plan["writes"].get(target, {}).get("sha256", record["sha256"]),
            })
            for target, record in backups.items()
        },
    }
    transaction._atomic_write(  # pylint: disable=protected-access
        bundle / "transaction.json", transaction._json_bytes(tx_doc))  # pylint: disable=protected-access
    try:
        for path, data in plan["_write_bytes"].items():
            transaction._atomic_write(path, data)  # pylint: disable=protected-access
        audit = _post_audit(plan)
        audit_path = Path(plan["root"]) / "audit.json"
        audit_data = transaction._json_bytes(audit)  # pylint: disable=protected-access
        tx_doc["state"] = "committing"
        tx_doc["allowed_recovery_hashes"][str(audit_path)] = sorted({
            backups[str(audit_path)]["sha256"],
            transaction._sha256_bytes(audit_data),  # pylint: disable=protected-access
        })
        transaction._atomic_write(  # pylint: disable=protected-access
            bundle / "transaction.json", transaction._json_bytes(tx_doc))  # pylint: disable=protected-access
        transaction._atomic_write(audit_path, audit_data)  # pylint: disable=protected-access
        tx_doc.update({
            "state":
            "committed",
            "audit_sha256":
            transaction._sha256(  # pylint: disable=protected-access
                audit_path),
            "observed": {
                "raw": audit["raw_tests"],
                "valid": audit["valid_tests"],
                "raw_only": audit["raw_only_count"],
            },
            "postimages": {
                str(path): transaction._sha256(path)  # pylint: disable=protected-access
                for path in sorted(set(plan["_write_bytes"]) | {audit_path})
            },
        })
        transaction._atomic_write(  # pylint: disable=protected-access
            bundle / "transaction.json", transaction._json_bytes(tx_doc))  # pylint: disable=protected-access
        return tx_doc
    except Exception:
        tx_doc["state"] = "rolling-back"
        transaction._atomic_write(  # pylint: disable=protected-access
            bundle / "transaction.json", transaction._json_bytes(tx_doc))  # pylint: disable=protected-access
        transaction._restore(backups)  # pylint: disable=protected-access
        tx_doc["state"] = "rolled-back"
        transaction._atomic_write(  # pylint: disable=protected-access
            bundle / "transaction.json", transaction._json_bytes(tx_doc))  # pylint: disable=protected-access
        raise


def rollback(bundle: Path) -> None:
    # The shared transaction implementation accepts applying, committing,
    # committed, and rolling-back states.  Its per-target hash allowlist makes
    # a partially completed restore safely resumable without overwriting a
    # concurrently changed file.
    transaction.rollback(bundle.expanduser().resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--partition", type=Path, default=DEFAULT_PARTITION)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    bundle = args.bundle.expanduser().resolve()
    if args.rollback:
        rollback(bundle)
        return 0
    if bundle.exists():
        raise ScopeCorrectionError(f"refusing to overwrite an existing bundle: {bundle}")
    root = args.root.expanduser().resolve()
    transaction._assert_root(root)  # pylint: disable=protected-access
    with transaction._root_lock(root):  # pylint: disable=protected-access
        plan = build_plan(root, args.partition, bundle)
        _stage(plan, bundle)
        print(
            json.dumps(
                {
                    "apply": args.apply,
                    "partition_sha256": PARTITION_SHA256,
                    "plan_sha256": plan["plan_sha256"],
                    "observed": plan["observed"],
                    "target_keys_sha256": plan["target_keys_sha256"],
                    "new_raw_only_sha256": plan["new_raw_only_sha256"],
                },
                indent=2,
                sort_keys=True))
        if args.apply:
            print(json.dumps(_apply_locked(plan, bundle), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
