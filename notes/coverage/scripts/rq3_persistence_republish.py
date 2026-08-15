#!/usr/bin/env python3
"""Transactionally republish retained RQ3 concrete replay siblings.

This migration does not run ESBMC or Forge.  It grants validity only to raw
rows already bound to an audited canonical replay manifest entry.  Every
input and evidence file is hash-pinned before any canonical metadata write.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import copy
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Iterator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from rq1_concrete_replay_store import (  # noqa: E402
    audit_manifest, load_manifest, persistence_coverage, persistence_publication_key,
)
from rq1_veriput_run import (  # noqa: E402
    _legacy_quality_bucket, _normalize_result_row, persistence_publication_failure,
    quarantine_unpersisted_validity,
)
from run_rq3_no_cer_reg import audit_output  # noqa: E402

DATASETS = ("bugfix124", "peer182", "real203")
EXPECTED_ROOT_SUFFIX = Path("Results/RQ3/VeriExploit/No_Cer_Reg")
EXPECTED = {
    "cases": 81,
    "candidates": 605,
    "publishable": 499,
    "rejected": 106,
    # Two deploy-only diagnostic records were removed by the independently
    # reviewed scope correction before this metadata transaction.
    "raw": 2993,
    "valid_before": 2140,
    "valid_after": 2639,
    "raw_only_after": 354,
}


class MigrationError(RuntimeError):
    """The frozen migration precondition or postcondition did not hold."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=path.parent) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _logical(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(row.get("file") or ""), str(row.get("test") or ""))
            for row in rows if isinstance(row, dict) and row.get("kind") == "concrete"}


def _set_sha256(values: set[tuple[str, str]]) -> str:
    return _sha256_bytes(_json_bytes(sorted([list(value) for value in values])))


def _mapping_sha256(values: dict[str, Any]) -> str:
    return _sha256_bytes(_json_bytes(values))


def _row_key(row: dict[str, Any]) -> str:
    return f"gen:veriput:{row.get('subject_id')}"


def _latest_rows(data: bytes) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for line in data.decode(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("subject_id"):
            latest[_row_key(row)] = row
    return latest


def _dataset_manifest(dataset: str, journal: Path, data: bytes) -> dict[str, Any]:
    latest = {key: _normalize_result_row(row) for key, row in _latest_rows(data).items()}
    status = Counter(str(row.get("status") or "<missing>") for row in latest.values())
    quality = Counter(
        str(row.get("quality_bucket") or _legacy_quality_bucket(row)) for row in latest.values())
    return {
        "schema": "veriput-rq1-dataset-manifest/v1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dataset": dataset,
        "journal": str(journal),
        "summary": {
            "rows":
            len(latest),
            "raw":
            sum(int(row.get("raw") or 0) for row in latest.values()),
            "valid":
            sum(int(row.get("valid") or 0) for row in latest.values()),
            "put_raw":
            sum(int(row.get("put_raw") or 0) for row in latest.values()),
            "put_valid":
            sum(int(row.get("put_valid") or 0) for row in latest.values()),
            "concrete_raw":
            sum(int(row.get("concrete_raw") or 0) for row in latest.values()),
            "concrete_valid":
            sum(int(row.get("concrete_valid") or 0) for row in latest.values()),
            "valid_put_with_R1":
            sum(int(row.get("valid_put_with_R1") or 0) for row in latest.values()),
            "valid_put_with_R2":
            sum(int(row.get("valid_put_with_R2") or 0) for row in latest.values()),
            "valid_put_with_R1_or_R2":
            sum(int(row.get("valid_put_with_R1_or_R2") or 0) for row in latest.values()),
            "valid_put_without_R1R2":
            sum(int(row.get("valid_put_without_R1R2") or 0) for row in latest.values()),
            "status":
            dict(sorted(status.items())),
            "quality_bucket":
            dict(sorted(quality.items())),
        },
    }


def _green_candidates(document: dict[str, Any]) -> list[dict[str, Any]]:
    put = document.get("put") or {}
    raw = put.get("raw_artifacts") or put.get("raw_tests") or []
    return [
        copy.deepcopy(row) for row in raw if isinstance(row, dict) and row.get("kind") == "concrete"
        and row.get("forge_status") == "Success" and row.get("valid_reference_test") is True
    ]


def _rebuild_coverage(case_dir: Path, row: dict[str, Any],
                      candidates: list[dict[str, Any]]) -> dict[str, Any]:
    manifest = load_manifest(case_dir)
    entries = manifest.get("entries") or []
    coverage = persistence_coverage(candidates, entries, case_dir)
    old = row.get("concrete_replay_persistence") or {}
    coverage.update({
        "manifest": str(case_dir / "concrete-replays" / "manifest.json"),
        "manifest_errors": audit_manifest(case_dir, manifest),
        "persistence_errors": copy.deepcopy(old.get("persistence_errors") or []),
        "generalization": copy.deepcopy(old.get("generalization") or {}),
    })
    return coverage


def _republish_summary(summary: dict[str, Any], candidates: list[dict[str, Any]],
                       coverage: dict[str, Any], reason: str) -> dict[str, Any]:
    candidate = copy.deepcopy(summary)
    candidate["valid_tests"] = copy.deepcopy(candidates)
    candidate["valid_artifacts"] = copy.deepcopy(candidates)
    candidate = _normalize_result_row(candidate)
    published = quarantine_unpersisted_validity(candidate, reason, coverage)
    published["persistence_republication"] = {
        "schema": "veriput-rq3-persistence-republication/v1",
        "source": "retained-hash-bound-canonical-replays",
        "esbmc_rerun": False,
        "candidate_count": len(candidates),
        "publishable_count": len(published.get("valid_tests") or []),
        "rejected_count": len(published.get("unpublished_valid_tests") or []),
    }
    return published


def _case_candidate(
        result_path: Path,
        document: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if document is None:
        document = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
    row = document.get("row") or {}
    if row.get("completion_status") != "persistence-error":
        raise MigrationError(f"not a frozen persistence-error row: {result_path}")
    candidates = _green_candidates(document)
    coverage = _rebuild_coverage(result_path.parent, row, candidates)
    reason = persistence_publication_failure(coverage)
    if not reason:
        raise MigrationError(f"expected per-test rejection evidence: {result_path}")
    updated_row = _republish_summary(row, candidates, coverage, reason)
    updated_put = _republish_summary(document.get("put") or {}, candidates, coverage, reason)
    published = updated_row.get("valid_tests") or []
    rejected = updated_row.get("unpublished_valid_tests") or []
    if _logical(published) & _logical(rejected):
        raise MigrationError(f"published/rejected overlap: {result_path}")
    if _logical(published) | _logical(rejected) != _logical(candidates):
        raise MigrationError(f"candidate partition mismatch: {result_path}")
    if _logical(updated_put.get("valid_tests") or []) != _logical(published):
        raise MigrationError(f"row/put publication mismatch: {result_path}")
    updated = copy.deepcopy(document)
    updated["row"] = updated_row
    updated["put"] = updated_put
    updated["concrete_replay_persistence"] = coverage
    updated["persistence_publication_failure"] = reason
    metadata = {
        "result": str(result_path),
        "dataset": updated_row.get("dataset"),
        "subject_id": updated_row.get("subject_id"),
        "candidates": len(candidates),
        "publishable": len(published),
        "rejected": len(rejected),
        "candidate_keys": sorted([list(key) for key in _logical(candidates)]),
        "publishable_keys": sorted([list(key) for key in _logical(published)]),
        "rejected_keys": sorted([list(key) for key in _logical(rejected)]),
    }
    return updated, metadata


def _evidence_paths(case_dir: Path, row: dict[str, Any], candidates: list[dict[str,
                                                                               Any]]) -> list[Path]:
    paths = [case_dir / "concrete-replays" / "manifest.json"]
    paths.extend(Path(str(path)) for path in row.get("put_summary_paths") or [])
    for candidate in candidates:
        test_file = Path(str(candidate.get("file") or ""))
        paths.extend((test_file, test_file.parent.parent / "src" / "flat.sol",
                      Path(str(candidate.get("put_json") or ""))))
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise MigrationError("missing compare-before-write evidence: " + ", ".join(missing))
    return paths


def _candidate_source_seals(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    seals = []
    for candidate in candidates:
        test_file = Path(str(candidate.get("file") or ""))
        flat_file = test_file.parent.parent / "src" / "flat.sol"
        if not test_file.is_file() or not flat_file.is_file():
            raise MigrationError(f"missing candidate source evidence: {test_file}, {flat_file}")
        seals.append({
            "file": str(test_file),
            "test": str(candidate.get("test") or ""),
            "test_file_sha256": _sha256(test_file),
            "flat_file": str(flat_file),
            "flat_file_sha256": _sha256(flat_file),
        })
    return sorted(seals, key=lambda seal: (seal["file"], seal["test"]))


def _result_snapshot(root: Path) -> dict[str, str]:
    return {
        str(path): _sha256(path)
        for dataset in DATASETS
        for path in sorted((root / dataset / "subjects").glob("*/result.json"))
    }


def _global_sets(
    root: Path,
    replacements: dict[Path, dict[str, Any]] | None = None,
    snapshot: dict[Path, dict[str, Any]] | None = None,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    raw: set[tuple[str, str]] = set()
    valid: set[tuple[str, str]] = set()
    replacements = replacements or {}
    snapshot = snapshot or {}
    for dataset in DATASETS:
        for result_path in sorted((root / dataset / "subjects").glob("*/result.json")):
            document = replacements.get(result_path)
            if document is None:
                document = snapshot.get(result_path)
            if document is None:
                document = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
            put = document.get("put") or {}
            raw.update(_logical(put.get("raw_artifacts") or []))
            valid.update(_logical(put.get("valid_artifacts") or []))
    return raw, valid


def _assert_root(root: Path) -> None:
    parts = root.resolve().parts
    suffix = EXPECTED_ROOT_SUFFIX.parts
    if tuple(parts[-len(suffix):]) != suffix:
        raise MigrationError(f"refusing non-published RQ3 root: {root}")


@contextmanager
def _root_lock(root: Path) -> Iterator[Path]:
    canonical_root = root.expanduser().resolve()
    _assert_root(canonical_root)
    if not canonical_root.is_dir():
        raise MigrationError(f"canonical RQ3 root does not exist: {canonical_root}")
    lock_path = canonical_root / ".rq3-persistence-republish.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield canonical_root


def build_plan(root: Path, bundle: Path, expected: dict[str, int] | None = None) -> dict[str, Any]:
    root = root.resolve()
    bundle = bundle.expanduser().resolve()
    expected = dict(EXPECTED if expected is None else expected)
    replacements: dict[Path, dict[str, Any]] = {}
    snapshot: dict[Path, dict[str, Any]] = {}
    cases = []
    preimages: dict[Path, str] = {}
    for dataset in DATASETS:
        subject_root = root / dataset / "subjects"
        for result_path in sorted(subject_root.glob("*/result.json")):
            result_data = result_path.read_bytes()
            preimages[result_path] = _sha256_bytes(result_data)
            document = json.loads(result_data.decode(errors="replace"))
            snapshot[result_path] = document
            row = document.get("row") or {}
            if row.get("completion_status") != "persistence-error":
                continue
            updated, metadata = _case_candidate(result_path, document)
            replacements[result_path] = updated
            cases.append(metadata)
            candidates = _green_candidates(document)
            metadata["candidate_source_seals"] = _candidate_source_seals(candidates)
            for evidence in _evidence_paths(result_path.parent, row, candidates):
                preimages[evidence] = _sha256(evidence)

    old_raw, old_valid = _global_sets(root, snapshot=snapshot)
    new_raw, new_valid = _global_sets(root, replacements, snapshot)
    publishable_keys = set()
    rejected_keys = set()
    for case in cases:
        publishable_keys.update(tuple(key) for key in case["publishable_keys"])
        rejected_keys.update(tuple(key) for key in case["rejected_keys"])
    totals = {
        "cases": len(cases),
        "candidates": sum(case["candidates"] for case in cases),
        "publishable": len(publishable_keys),
        "rejected": len(rejected_keys),
        "raw": len(new_raw),
        "valid_before": len(old_valid),
        "valid_after": len(new_valid),
        "raw_only_after": len(new_raw - new_valid),
    }
    if totals != expected:
        raise MigrationError(f"frozen population mismatch: got {totals}, expected {expected}")
    if new_raw != old_raw or not old_valid < new_valid:
        raise MigrationError("raw changed or valid did not strictly increase")
    if new_valid - old_valid != publishable_keys:
        raise MigrationError("exact newly-valid set differs from publishable replay set")
    if new_raw - new_valid != rejected_keys | ((old_raw - old_valid) - publishable_keys):
        raise MigrationError("exact raw-only remainder differs from rejected/unrepaired set")

    writes: dict[Path, bytes] = {
        path: _json_bytes(document)
        for path, document in replacements.items()
    }
    for dataset in DATASETS:
        journal = root / dataset / "results.jsonl"
        manifest = root / dataset / "manifest.json"
        journal_data = journal.read_bytes()
        preimages[journal] = _sha256_bytes(journal_data)
        preimages[manifest] = _sha256(manifest)
        updates = sorted((document["row"]
                          for path, document in replacements.items() if path.parts[-4] == dataset),
                         key=lambda row: str(row.get("subject_id") or ""))
        if journal_data and not journal_data.endswith(b"\n"):
            journal_data += b"\n"
        journal_data += b"".join(
            (json.dumps(row, sort_keys=True) + "\n").encode() for row in updates)
        writes[journal] = journal_data
        writes[manifest] = _json_bytes(_dataset_manifest(dataset, journal, journal_data))

    audit_path = root / "audit.json"
    preimages[audit_path] = _sha256(audit_path)
    result_snapshot = {str(path): preimages[path] for path in sorted(snapshot)}
    plan = {
        "schema": "veriput-rq3-persistence-republish-plan/v1",
        "root": str(root),
        "bundle": str(bundle),
        "expected": expected,
        "totals": totals,
        "cases": cases,
        "old_raw_only_sha256": _set_sha256(old_raw - old_valid),
        "old_raw_sha256": _set_sha256(old_raw),
        "old_valid_sha256": _set_sha256(old_valid),
        "new_raw_only_sha256": _set_sha256(new_raw - new_valid),
        "new_raw_sha256": _set_sha256(new_raw),
        "new_valid_sha256": _set_sha256(new_valid),
        "newly_valid_sha256": _set_sha256(new_valid - old_valid),
        "result_snapshot": result_snapshot,
        "result_snapshot_sha256": _mapping_sha256(result_snapshot),
        "preimages": {
            str(path): digest
            for path, digest in sorted(preimages.items(), key=lambda item: str(item[0]))
        },
        "writes": {
            str(path): {
                "sha256": _sha256_bytes(data),
                "bytes": len(data),
            }
            for path, data in sorted(writes.items(), key=lambda item: str(item[0]))
        },
    }
    plan["plan_sha256"] = _sha256_bytes(_json_bytes(plan))
    plan["_write_bytes"] = writes
    return plan


def _stage(plan: dict[str, Any], bundle: Path) -> None:
    writes = plan["_write_bytes"]
    root = Path(plan["root"])
    for path, data in writes.items():
        relative = path.relative_to(root)
        _atomic_write(bundle / "staged" / relative, data)
    serializable = {key: value for key, value in plan.items() if key != "_write_bytes"}
    _atomic_write(bundle / "plan.json", _json_bytes(serializable))


def _backup(plan: dict[str, Any], bundle: Path) -> dict[str, dict[str, str]]:
    root = Path(plan["root"])
    backups = {}
    targets = set(plan["_write_bytes"])
    targets.add(root / "audit.json")
    for path in sorted(targets):
        relative = path.relative_to(root)
        backup = bundle / "rollback" / relative
        data = path.read_bytes()
        expected = plan["preimages"].get(str(path))
        if expected is None or _sha256_bytes(data) != expected:
            raise MigrationError(f"backup source differs from sealed preimage: {path}")
        _atomic_write(backup, data)
        backups[str(path)] = {
            "path": str(backup),
            "sha256": _sha256(backup),
        }
    return backups


def _verify_preimages(plan: dict[str, Any]) -> None:
    for raw_path, expected in plan["preimages"].items():
        path = Path(raw_path)
        if not path.is_file() or _sha256(path) != expected:
            raise MigrationError(f"compare-before-write mismatch: {path}")


def _verify_plan_seal(plan: dict[str, Any]) -> None:
    serializable = {key: value for key, value in plan.items() if key != "_write_bytes"}
    expected = serializable.pop("plan_sha256", None)
    if expected != _sha256_bytes(_json_bytes(serializable)):
        raise MigrationError("plan seal mismatch")
    writes = plan.get("_write_bytes") or {}
    observed = {
        str(path): {
            "sha256": _sha256_bytes(data),
            "bytes": len(data),
        }
        for path, data in sorted(writes.items(), key=lambda item: str(item[0]))
    }
    if observed != plan.get("writes"):
        raise MigrationError("staged write bytes differ from sealed plan")


def _verify_result_snapshot(plan: dict[str, Any]) -> None:
    expected = plan.get("result_snapshot") or {}
    if _mapping_sha256(expected) != plan.get("result_snapshot_sha256"):
        raise MigrationError("result snapshot seal mismatch")
    observed = _result_snapshot(Path(plan["root"]))
    if observed != expected:
        raise MigrationError("full result snapshot changed before apply")


def _verify_candidate_source_seals(plan: dict[str, Any]) -> None:
    for case in plan["cases"]:
        seals = case.get("candidate_source_seals")
        expected_keys = {tuple(key) for key in case.get("candidate_keys") or []}
        if (not isinstance(seals, list) or len(seals) != case.get("candidates")
                or not all(isinstance(seal, dict) for seal in seals)
                or len({(seal.get("file"), seal.get("test"))
                        for seal in seals}) != len(seals) or {(seal.get("file"), seal.get("test"))
                                                              for seal in seals} != expected_keys):
            raise MigrationError(f"candidate source seal population mismatch: {case['result']}")
        for seal in seals:
            test_file = Path(seal["file"])
            flat_file = Path(seal["flat_file"])
            if (not test_file.is_file() or _sha256(test_file) != seal["test_file_sha256"]):
                raise MigrationError(f"candidate test source seal mismatch: {test_file}")
            if (not flat_file.is_file() or _sha256(flat_file) != seal["flat_file_sha256"]):
                raise MigrationError(f"candidate flat source seal mismatch: {flat_file}")


def _restore(backups: dict[str, dict[str, str]]) -> None:
    validated = {}
    for target, record in backups.items():
        backup = Path(record["path"])
        if not backup.is_file() or _sha256(backup) != record["sha256"]:
            raise MigrationError(f"rollback backup hash mismatch: {backup}")
        validated[Path(target)] = backup.read_bytes()
    for target, data in validated.items():
        _atomic_write(target, data)


def _require_canonical_descendant(path: Path, root: Path, label: str) -> None:
    canonical = path.resolve()
    try:
        canonical.relative_to(root)
    except ValueError as error:
        raise MigrationError(f"{label} escapes its transaction root: {path}") from error
    if canonical != path:
        raise MigrationError(f"{label} is not canonical: {path}")


def _verify_rollback_paths(backups: dict[str, dict[str, str]], root: Path, bundle: Path) -> None:
    rollback_root = (bundle / "rollback").resolve()
    for raw_target, record in backups.items():
        _require_canonical_descendant(Path(raw_target), root, "rollback target")
        _require_canonical_descendant(Path(record.get("path") or ""), rollback_root,
                                      "rollback backup")


def _reauthenticate(plan: dict[str, Any]) -> None:
    publishable = set()
    for case in plan["cases"]:
        result_path = Path(case["result"])
        document = json.loads(result_path.read_text(encoding="utf-8", errors="replace"))
        candidates = _green_candidates(document)
        coverage = _rebuild_coverage(result_path.parent, document.get("row") or {}, candidates)
        allowed = set(coverage.get("publishable_validity_keys") or [])
        publishable.update(
            _logical([row for row in candidates if persistence_publication_key(row) in allowed]))
    expected = {tuple(key) for case in plan["cases"] for key in case["publishable_keys"]}
    if publishable != expected or _set_sha256(publishable) != plan["newly_valid_sha256"]:
        raise MigrationError("manifest binding changed after staging")


def _apply_plan_locked(plan: dict[str, Any], bundle: Path) -> dict[str, Any]:
    root = Path(plan["root"])
    _verify_plan_seal(plan)
    _verify_preimages(plan)
    _verify_result_snapshot(plan)
    _verify_candidate_source_seals(plan)
    _reauthenticate(plan)
    backups = _backup(plan, bundle)
    transaction = {
        "schema": "veriput-rq3-persistence-republish-transaction/v1",
        "state": "applying",
        "root": str(root),
        "plan_sha256": plan["plan_sha256"],
        "backups": backups,
        "backups_sha256": _mapping_sha256(backups),
        "allowed_recovery_hashes": {
            target:
            sorted({
                record["sha256"],
                plan["writes"].get(target, {}).get("sha256", record["sha256"]),
            })
            for target, record in backups.items()
        },
    }
    _atomic_write(bundle / "transaction.json", _json_bytes(transaction))
    try:
        for path, data in plan["_write_bytes"].items():
            _atomic_write(path, data)
        audit = audit_output(root)
        observed = {
            "raw": audit["raw_tests"],
            "valid_after": audit["valid_tests"],
            "raw_only_after": audit["raw_only_count"],
        }
        wanted = {
            "raw": plan["expected"]["raw"],
            "valid_after": plan["expected"]["valid_after"],
            "raw_only_after": plan["expected"]["raw_only_after"],
        }
        if observed != wanted or audit["valid_only_count"] != 0 or audit["ok"]:
            raise MigrationError(
                f"post-publication RQ3 audit mismatch: got {observed}, expected {wanted}")
        raw_set, valid_set = _global_sets(root)
        if (_set_sha256(raw_set) != plan["new_raw_sha256"]
                or _set_sha256(valid_set) != plan["new_valid_sha256"]):
            raise MigrationError("post-publication full raw/valid identity set mismatch")
        raw_only_hash = _set_sha256(raw_set - valid_set)
        if raw_only_hash != plan["new_raw_only_sha256"]:
            raise MigrationError("post-publication raw-only identity set mismatch")
        _reauthenticate(plan)
        audit["persistence_republication"] = {
            "plan_sha256": plan["plan_sha256"],
            "cases": plan["totals"]["cases"],
            "publishable": plan["totals"]["publishable"],
            "rejected": plan["totals"]["rejected"],
        }
        audit_data = _json_bytes(audit)
        audit_path = root / "audit.json"
        transaction["state"] = "committing"
        transaction["allowed_recovery_hashes"][str(audit_path)] = sorted({
            backups[str(audit_path)]["sha256"],
            _sha256_bytes(audit_data),
        })
        _atomic_write(bundle / "transaction.json", _json_bytes(transaction))
        _atomic_write(audit_path, audit_data)
        transaction.update({
            "state": "committed",
            "audit_sha256": _sha256(root / "audit.json"),
            "observed": observed,
            "postimages": {
                str(path): _sha256(path)
                for path in sorted(set(plan["_write_bytes"]) | {root / "audit.json"})
            },
        })
        _atomic_write(bundle / "transaction.json", _json_bytes(transaction))
        return transaction
    except Exception:
        _restore(backups)
        transaction["state"] = "rolled-back"
        _atomic_write(bundle / "transaction.json", _json_bytes(transaction))
        raise


def apply_plan(plan: dict[str, Any], bundle: Path) -> dict[str, Any]:
    bundle = bundle.expanduser().resolve()
    if bundle != Path(plan["bundle"]):
        raise MigrationError("apply bundle differs from sealed plan bundle")
    root = Path(plan["root"]).resolve()
    if root != Path(plan["root"]):
        raise MigrationError("plan root is not canonical")
    with _root_lock(root):
        return _apply_plan_locked(plan, bundle)


def rollback(bundle: Path) -> None:
    bundle = bundle.expanduser().resolve()
    transaction_path = bundle / "transaction.json"
    initial = json.loads(transaction_path.read_text(encoding="utf-8", errors="replace"))
    root = Path(initial.get("root") or "").resolve()
    with _root_lock(root) as locked_root:
        transaction = json.loads(transaction_path.read_text(encoding="utf-8", errors="replace"))
        if Path(transaction.get("root") or "").resolve() != locked_root:
            raise MigrationError("transaction root changed while acquiring rollback lock")
        backups = transaction.get("backups") or {}
        if not backups:
            raise MigrationError("transaction has no rollback map")
        if _mapping_sha256(backups) != transaction.get("backups_sha256"):
            raise MigrationError("rollback backup map seal mismatch")
        state = transaction.get("state")
        if state not in ("applying", "committing", "committed", "rolling-back"):
            raise MigrationError(f"transaction state is not rollbackable: {state}")
        _verify_rollback_paths(backups, locked_root, bundle)
        if state == "committed":
            permitted = {
                path: {digest}
                for path, digest in (transaction.get("postimages") or {}).items()
            }
        else:
            permitted = {
                path: set(digests)
                for path, digests in (transaction.get("allowed_recovery_hashes") or {}).items()
            }
        if set(permitted) != set(backups):
            raise MigrationError("rollback target set differs from the transaction backup set")
        for raw_path, allowed in permitted.items():
            path = Path(raw_path)
            if not path.is_file() or _sha256(path) not in allowed:
                raise MigrationError(f"refusing rollback over a changed postimage: {path}")
        transaction["state"] = "rolling-back"
        transaction["allowed_recovery_hashes"] = {
            path: sorted(set(permitted[path]) | {backups[path]["sha256"]})
            for path in backups
        }
        _atomic_write(transaction_path, _json_bytes(transaction))
        _restore(backups)
        transaction["state"] = "rolled-back-manually"
        _atomic_write(transaction_path, _json_bytes(transaction))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    args.bundle = args.bundle.expanduser().resolve()
    if args.rollback:
        rollback(args.bundle)
        return 0
    if args.root is None:
        parser.error("--root is required unless --rollback is used")
    root = args.root.expanduser().resolve()
    _assert_root(root)
    with _root_lock(root):
        plan = build_plan(root, args.bundle)
        _stage(plan, args.bundle)
        print(
            json.dumps(
                {
                    "plan_sha256": plan["plan_sha256"],
                    "totals": plan["totals"],
                    "newly_valid_sha256": plan["newly_valid_sha256"],
                    "new_raw_only_sha256": plan["new_raw_only_sha256"],
                    "apply": args.apply,
                },
                indent=2,
                sort_keys=True))
        if args.apply:
            transaction = _apply_plan_locked(plan, args.bundle)
            print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
