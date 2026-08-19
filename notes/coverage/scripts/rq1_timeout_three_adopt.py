#!/usr/bin/env python3
"""Transactionally adopt the three reviewed timeout-case RQ3 PUT bundles."""

# This case-specific transaction deliberately composes reviewed internal APIs.
# pylint: disable=protected-access,too-many-lines,too-many-locals

from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import rq1_put_ce_anchor_backfill as backfill
from rq1_concrete_replay_store import (
    _atomic_json,
    _solidity_function,
    _solidity_function_sources,
    annotate_generalization,
    audit_manifest,
    load_manifest,
    persist_concrete_replay,
    persistence_coverage,
)

CANONICAL_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
RESULTS = Path("/home/samson/workspace/VeriPUT/Results")
BUNDLES = RESULTS / "RQ1_KInduction_NoPUT600/adoption-bundles"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit_root_from_test(test_file: Path) -> Path:
    project = test_file.parent.parent
    if not (project / "foundry.toml").is_file():
        raise RuntimeError(f"test has no Foundry project: {test_file}")
    return project.parent


def _one(path: Path, pattern: str) -> Path:
    matches = sorted(path.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {pattern} below {path}, found {len(matches)}")
    return matches[0]


def _parameterized_put_json(unit_root: Path) -> Path:
    matches = [
        path for path in sorted((unit_root / "_wd").glob("*/put.json"))
        if "__basis_concrete" not in path.parent.name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one parameterized put.json below {unit_root}, found "
                           f"{len(matches)}")
    return matches[0]


def _specs() -> dict[str, dict]:
    reference_report = json.loads(
        (BUNDLES /
         "noput-timeout-three-20260815/ReferenceConsideration/isolation-report.json").read_text())
    vault_report = json.loads(
        (BUNDLES / "noput-timeout-three-20260815/VaultExtension/report.json").read_text())
    timelock_report = json.loads(
        (BUNDLES / "timelock-authorizer-migrator-rq3-20260815/validation-report.json").read_text())

    reference_test = Path(reference_report["staging"]["test_file"])
    reference_original = Path(reference_report["provenance"]["original_put_test"])
    vault_project = Path(vault_report["staging_project"])
    if not vault_project.is_absolute():
        vault_project = BUNDLES / "noput-timeout-three-20260815/VaultExtension" / vault_project
    vault_test = vault_project / vault_report["test_file"]
    vault_source = (
        RESULTS / "RQ1_KInduction_NoPUT600/budget-a-authoritative-20260815-vaultextension"
        "/real203/subjects/balancer__balancer-v3-monorepo__VaultExtension/put"
        "/stress243__balancer__balancer-v3-monorepo__VaultExtension__getPoolTokenRates__pf3777")
    specs = {
        "ReferenceConsideration": {
            "subject":
            "ProjectOpenSea__seaport__ReferenceConsideration",
            "units": [{
                "source_unit": _unit_root_from_test(reference_original),
                "staged_project": reference_test.parent.parent,
                "test_file": reference_test.name,
                "put_test": "test_put_ReferenceConsideration_name_path1",
                "anchor_test": "test_ce_anchor_name_path1",
                "rq3_source": Path(reference_report["provenance"]["rq3_concrete_basis"]),
                "rq3_test": "test_cov_0",
            }],
        },
        "VaultExtension": {
            "subject":
            "balancer__balancer-v3-monorepo__VaultExtension",
            "units": [{
                "source_unit": vault_source,
                "staged_project": vault_project,
                "test_file": vault_test.name,
                "put_test": vault_report["put_test"],
                "anchor_test": vault_report["anchor_test"],
                "rq3_source": Path(vault_report["rq3_basis"]),
                "rq3_test": "test_cov_0",
            }],
        },
        "TimelockAuthorizerMigrator": {
            "subject": "balancer__balancer-v3-monorepo__TimelockAuthorizerMigrator",
            "units": [],
        },
    }
    timelock_root = BUNDLES / "timelock-authorizer-migrator-rq3-20260815"
    for unit in timelock_report["units"]:
        source_unit = timelock_root / unit["unit"]
        project = _one(source_unit, "stress243*certify-results")
        test_file = _one(project / "test", "*.t.sol")
        specs["TimelockAuthorizerMigrator"]["units"].append({
            "source_unit": source_unit,
            "staged_project": project,
            "test_file": test_file.name,
            "put_test": unit["put_test"],
            "anchor_test": unit["anchor_test"],
            "rq3_source": Path(unit["rq3_source"]),
            "rq3_test": unit["rq3_test"],
        })
    return specs


def _rewrite(value, old: str, new: str):
    if isinstance(value, dict):
        return {key: _rewrite(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def _rewrite_json_tree(root: Path, old: str, new: str) -> None:
    for path in root.rglob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        _atomic_json(path, _rewrite(document, old, new))


def _forge(project: Path, test_file: str, test: str, fuzz: bool) -> dict:
    command = ["forge", "test", "--json", "--match-path", f"test/{test_file}", "--match-test", test]
    if fuzz:
        command.extend(["--fuzz-runs", "256"])
    completed = subprocess.run(command,
                               cwd=project,
                               text=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               timeout=180,
                               check=False)
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Forge emitted no JSON for {test}: {completed.stderr[-1000:]}") from exc
    matches = []
    for contract in document.values():
        for name, result in (contract.get("test_results") or {}).items():
            if name == test or name.startswith(f"{test}("):
                matches.append(result)
    if completed.returncode != 0 or len(matches) != 1 or matches[0].get("status") != "Success":
        raise RuntimeError(f"Forge gate failed for {test}: " +
                           (completed.stdout + completed.stderr)[-2000:])
    return {
        "status": "Success",
        "fuzz_runs": 256 if fuzz else None,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
    }


def _rq3_put_json(source: Path) -> Path:
    unit_root = source.parent.parent.parent
    return _one(unit_root / "_wd", "*/put.json")


def _validate_rq3_binding(unit: dict, staged_test: Path) -> dict:
    source = staged_test.read_text(encoding="utf-8")
    anchors = re.findall(r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_]+)\s*\(", source)
    if anchors != [unit["anchor_test"]]:
        raise RuntimeError(f"expected unique anchor {unit['anchor_test']}, found {anchors}")
    staged = _solidity_function(source, unit["anchor_test"])
    rq3 = _solidity_function(unit["rq3_source"].read_text(encoding="utf-8"), unit["rq3_test"])
    if not staged or not rq3 or staged[1] != rq3[1]:
        raise RuntimeError(f"anchor body is not byte-exact RQ3 closure for {unit['put_test']}")
    put_sources = _solidity_function_sources(source, unit["put_test"])
    anchor_sources = _solidity_function_sources(source, unit["anchor_test"])
    rq3_sources = _solidity_function_sources(unit["rq3_source"].read_text(encoding="utf-8"),
                                             unit["rq3_test"])
    if len(put_sources) != 1 or len(anchor_sources) != 1 or len(rq3_sources) != 1:
        raise RuntimeError("PUT/RQ3 function source is not unique")
    return {
        "rq3_source": str(unit["rq3_source"]),
        "rq3_source_sha256": _sha256(unit["rq3_source"]),
        "rq3_test": unit["rq3_test"],
        "rq3_function_sha256": hashlib.sha256(rq3_sources[0][0].encode()).hexdigest(),
        "rq3_body_sha256": hashlib.sha256(rq3[1].encode()).hexdigest(),
        "destination_source_sha256": _sha256(staged_test),
        "destination_put_function_sha256": hashlib.sha256(put_sources[0][0].encode()).hexdigest(),
        "anchor_function_sha256": hashlib.sha256(anchor_sources[0][0].encode()).hexdigest(),
    }


def _normalize_put_record(path: Path, project: Path, test_file: Path, unit: dict,
                          binding: dict) -> dict:
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update({
        "b": True,
        "file": str(test_file),
        "forge_status": "Success",
        "is_concrete": False,
        "is_put": True,
        "kind": "put",
        "test": unit["put_test"],
        "valid_reference_test": True,
        "ce_anchor": {
            "status": "embedded",
            "binding": "rq3-concrete-closure/v1",
            "basis_kind": "rq3-concrete-replay",
            "test": unit["anchor_test"],
            "destination_put_test": unit["put_test"],
            **binding,
            "forge_gate": {
                "put_test": unit["put_test"],
                "anchor_test": unit["anchor_test"],
                "put_status": "Success",
                "anchor_status": "Success",
                "source_sha256": binding["destination_source_sha256"],
            },
        },
    })
    _atomic_json(path, record)
    for summary_path in project.parent.glob("put-summary.json"):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for row in (summary.get("deliverable_b") or {}).get("rows") or []:
            if row.get("test") == unit["put_test"]:
                row.update(record)
                row["gates"] = {
                    "assert": True,
                    "corpus": True,
                    "fuzz": True,
                    "green": True,
                    "width": True
                }
        _atomic_json(summary_path, summary)
    return record


def _put_row(record: dict, put_json: Path, test_file: Path) -> dict:
    row = copy.deepcopy(record)
    materialization = record.get("materialization") or {}
    classes = record.get("oracle_classes") or materialization.get("oracle_classes") or []
    if not classes:
        raise RuntimeError("PUT record has no verifier-backed oracle classes")
    combinations = (record.get("oracle_class_combinations")
                    or materialization.get("oracle_class_combinations") or ["+".join(classes)])
    row.update({
        "b": True,
        "file": str(test_file),
        "forge_status": "Success",
        "gates": {
            "assert": True,
            "corpus": True,
            "fuzz": True,
            "green": True,
            "width": True
        },
        "is_concrete": False,
        "is_put": True,
        "kind": "put",
        "oracle_classes": classes,
        "oracle_tags": classes,
        "oracle_class_counts": dict(Counter(classes)),
        "oracle_class_combinations": combinations,
        "oracle_combo_tag": combinations[0],
        "oracle_class_combo_counts": dict(Counter(combinations)),
        "put_json": str(put_json),
        "valid_reference_test": True,
    })
    return row


def _anchor_row(put_row: dict, test_file: Path, unit: dict) -> dict:
    rq3_record = json.loads(_rq3_put_json(unit["rq3_source"]).read_text(encoding="utf-8"))
    return {
        **rq3_record,
        "b": True,
        "file": str(test_file),
        "forge_status": "Success",
        "is_concrete": True,
        "is_put": False,
        "kind": "concrete",
        "put_json": put_row["put_json"],
        "test": unit["anchor_test"],
        "valid_reference_test": True,
        "rq3_source": str(unit["rq3_source"]),
        "rq3_source_sha256": _sha256(unit["rq3_source"]),
        "rq3_test": unit["rq3_test"],
    }


def _merge_result(result: dict, put_rows: list[dict], target: Path) -> list[dict]:
    existing = list((result.get("row") or {}).get("raw_tests")
                    or (result.get("row") or {}).get("valid_tests") or [])
    # These three cases' legacy concrete rows predate authenticated structured
    # witness oracles. Preserve them as raw history, but do not carry the old
    # valid flag into the stricter post-adoption ledger.
    raw_history = []
    for item in existing:
        row = copy.deepcopy(item)
        if row.get("kind") == "concrete":
            row.update({
                "valid_reference_test":
                False,
                "refused":
                True,
                "refusal_reason":
                "legacy concrete lacks authenticated structured witness oracle",
            })
        raw_history.append(row)
    valid = list(put_rows)
    raw = raw_history + put_rows
    counts = {
        "raw":
        len(raw),
        "valid":
        len(valid),
        "put_raw":
        sum(row.get("kind") == "put" for row in raw),
        "put_valid":
        sum(row.get("kind") == "put" for row in valid),
        "concrete_raw":
        sum(row.get("kind") == "concrete" for row in raw),
        "concrete_valid":
        sum(row.get("kind") == "concrete" for row in valid),
        "valid_put_with_R1":
        sum(
            row.get("kind") == "put" and "R1" in (row.get("oracle_classes") or [])
            for row in valid),
        "valid_put_with_R2":
        sum(
            row.get("kind") == "put" and "R2" in (row.get("oracle_classes") or [])
            for row in valid),
    }
    counts["valid_put_with_R1_or_R2"] = sum(
        row.get("kind") == "put" and bool({"R1", "R2"} & set(row.get("oracle_classes") or []))
        for row in valid)
    counts["valid_put_without_R1R2"] = counts["put_valid"] - counts["valid_put_with_R1_or_R2"]

    def oracle_counts(rows: list[dict], combo: bool = False) -> dict[str, int]:
        values = Counter()
        for row in rows:
            keys = (row.get("oracle_class_combinations") or
                    ([row.get("oracle_combo_tag")] if row.get("oracle_combo_tag") else [])) \
                if combo else (row.get("oracle_classes") or row.get("oracle_tags") or [])
            values.update(str(key) for key in keys if key)
        return dict(sorted(values.items()))

    raw_tags = oracle_counts(raw)
    raw_combos = oracle_counts(raw, combo=True)
    valid_tags = oracle_counts(valid)
    valid_combos = oracle_counts(valid, combo=True)
    for owner_name in ("row", "put"):
        owner = result.setdefault(owner_name, {})
        owner.update(counts)
        owner["artifact_counts"] = dict(counts)
        owner["quality_bucket"] = "valid-PUT-no-R1R2"
        owner["raw_tests"] = copy.deepcopy(raw)
        owner["valid_tests"] = copy.deepcopy(valid)
        owner["raw_artifacts"] = copy.deepcopy(raw)
        owner["valid_artifacts"] = copy.deepcopy(valid)
        owner["put_json_count"] = counts["put_raw"]
        owner["oracle_class_counts"] = raw_tags
        owner["oracle_class_combo_counts"] = raw_combos
        owner["raw_oracle_tag_counts"] = raw_tags
        owner["raw_oracle_combo_counts"] = raw_combos
        owner["valid_oracle_tag_counts"] = valid_tags
        owner["valid_oracle_combo_counts"] = valid_combos
        owner["rq1_oracle_tag_counts"] = valid_tags
        owner["rq1_oracle_combo_counts"] = valid_combos
    result["row"]["raw_artifacts_retained"] = True
    result["row"]["valid_artifacts_retained"] = True
    adoption = dict(result.get("adoption") or {})
    adoption.update({
        "schema": "veriput-rq1-timeout-three-adoption/v1",
        "scope": "case-level-valid-no-put-clearance",
        "source": str(target),
        "target": str(target),
        "valid": int(counts["valid"] > 0),
        "valid_count": counts["valid"],
        "raw": counts["raw"],
        "raw_count": counts["raw"],
        "put_valid": int(counts["put_valid"] > 0),
        "put_valid_count": counts["put_valid"],
        "concrete_valid": int(counts["concrete_valid"] > 0),
        "concrete_valid_count": counts["concrete_valid"],
        "valid_put_with_R1_or_R2": int(counts["valid_put_with_R1_or_R2"] > 0),
        "valid_put_with_R1_or_R2_count": counts["valid_put_with_R1_or_R2"],
        "quality_bucket": "valid-PUT-no-R1R2",
        "has_R0": "R0" in valid_tags,
        "has_R1": "R1" in valid_tags,
        "has_R2": "R2" in valid_tags,
        "oracle_tags": sorted(valid_tags),
        "rq3_concrete_closure": True,
        "adopted_ts": time.time(),
    })
    result["adoption"] = adoption
    return valid


def _copy_unit(unit: dict, target: Path) -> tuple[dict, dict, Path]:
    source_unit = unit["source_unit"].resolve()
    destination = target / "put" / source_unit.name
    if destination.exists():
        raise RuntimeError(f"PUT destination already exists: {destination}")
    shutil.copytree(source_unit, destination)
    source_project = unit["staged_project"].resolve()
    source_project_name = _one(destination, "stress243*certify-results").name
    destination_project = destination / source_project_name
    shutil.rmtree(destination_project)
    shutil.copytree(source_project, destination_project)
    _rewrite_json_tree(destination, str(source_unit), str(destination))
    test_file = destination_project / "test" / unit["test_file"]
    binding = _validate_rq3_binding(unit, test_file)
    put_json = _parameterized_put_json(destination)
    record = _normalize_put_record(put_json, destination_project, test_file, unit, binding)
    row = _put_row(record, put_json, test_file)
    return row, _anchor_row(row, test_file, unit), destination_project


def _merge(spec: dict, target: Path, report_dir: Path, pre_result_sha: str,
           pre_manifest_sha: str | None) -> dict:
    result_path = target / "result.json"
    manifest_path = target / "concrete-replays/manifest.json"
    if _sha256(result_path) != pre_result_sha:
        raise RuntimeError("compare-before-write failed for result.json")
    if pre_manifest_sha is not None and _sha256(manifest_path) != pre_manifest_sha:
        raise RuntimeError("compare-before-write failed for replay manifest")
    put_rows, anchor_rows, forge = [], [], []
    for unit in spec["units"]:
        put_row, anchor_row, project = _copy_unit(unit, target)
        forge.append({
            "put": _forge(project, unit["test_file"], unit["put_test"], True),
            "anchor": _forge(project, unit["test_file"], unit["anchor_test"], False),
        })
        put_rows.append(put_row)
        anchor_rows.append(anchor_row)

    if _sha256(result_path) != pre_result_sha:
        raise RuntimeError("result.json changed before atomic publication")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    valid = _merge_result(result, put_rows, target)
    _atomic_json(result_path, result)

    # Preserve every old concrete replay, then add one exact RQ3 basis per PUT.
    for row in anchor_rows:
        persist_concrete_replay(target, row, forge_timeout=120)
    generalization = annotate_generalization(target, valid)
    manifest = load_manifest(target)
    errors = audit_manifest(target, manifest)
    coverage = persistence_coverage(valid, manifest.get("entries") or [], target)
    if errors or not coverage.get("complete") or coverage.get("put_basis_missing_count"):
        raise RuntimeError(f"persistence failed: errors={errors} coverage={coverage}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["concrete_replay_persistence"] = coverage
    result["row"]["concrete_replay_persistence"] = coverage
    result["put"]["concrete_replay_persistence"] = coverage
    _atomic_json(result_path, result)
    report = {
        "case": f"real203/{spec['subject']}",
        "forge": forge,
        "generalization": generalization,
        "persistence": coverage,
        "manifest_errors": errors,
        "put_count": len(put_rows),
        "anchor_count": len(anchor_rows),
        "quality_bucket": "valid-PUT-no-R1R2",
        "result_accounting": {
            "row": {
                key: result["row"].get(key)
                for key in ("raw", "valid", "put_raw", "put_valid", "concrete_raw",
                            "concrete_valid", "put_json_count", "valid_oracle_tag_counts",
                            "valid_oracle_combo_counts", "quality_bucket")
            },
            "adoption": {
                key: result["adoption"].get(key)
                for key in ("raw", "raw_count", "valid", "valid_count", "put_valid",
                            "put_valid_count", "concrete_valid", "concrete_valid_count", "has_R0",
                            "has_R1", "has_R2", "oracle_tags", "quality_bucket")
            },
        },
        "canonical_result_sha256": _sha256(result_path),
        "canonical_manifest_sha256": _sha256(manifest_path),
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(report_dir / f"{spec['subject']}-post-adoption.json", report)
    return report


def _run_case(spec: dict, canonical_root: Path, report_dir: Path, apply: bool) -> dict:
    canonical = canonical_root / "real203/subjects" / spec["subject"]
    if not apply:
        with tempfile.TemporaryDirectory(prefix="rq1-timeout-three-dry-") as temp:
            target = Path(temp) / spec["subject"]
            shutil.copytree(canonical, target)
            manifest = target / "concrete-replays/manifest.json"
            return _merge(spec, target, report_dir, _sha256(target / "result.json"),
                          _sha256(manifest) if manifest.is_file() else None)

    with backfill._transaction_lock(canonical / "result.json"):
        result_sha = _sha256(canonical / "result.json")
        manifest = canonical / "concrete-replays/manifest.json"
        manifest_sha = _sha256(manifest) if manifest.is_file() else None
        backup_root = Path(tempfile.mkdtemp(prefix="rq1-timeout-three-backup-"))
        backup = backup_root / spec["subject"]
        shutil.copytree(canonical, backup)
        try:
            report = _merge(spec, canonical, report_dir, result_sha, manifest_sha)
        except Exception:
            failed = canonical.with_name(canonical.name + ".failed_timeout_three_adopt")
            shutil.rmtree(failed, ignore_errors=True)
            os.replace(canonical, failed)
            os.replace(backup, canonical)
            shutil.rmtree(failed, ignore_errors=True)
            shutil.rmtree(backup_root, ignore_errors=True)
            raise
        shutil.rmtree(backup_root, ignore_errors=True)
    return report


def _repair_accounting(spec: dict, canonical_root: Path, report_dir: Path) -> dict:
    """Normalize adoption presence flags without changing retained artifacts."""
    canonical = canonical_root / "real203/subjects" / spec["subject"]
    result_path = canonical / "result.json"
    with backfill._transaction_lock(result_path):
        before = result_path.read_bytes()
        before_sha = hashlib.sha256(before).hexdigest()
        descriptor, backup_name = tempfile.mkstemp(prefix="rq1-timeout-accounting-", suffix=".json")
        os.close(descriptor)
        backup = Path(backup_name)
        backup.write_bytes(before)
        try:
            result = json.loads(before)
            row = result["row"]
            adoption = result["adoption"]
            tags = sorted((row.get("valid_oracle_tag_counts") or {}).keys())
            adoption.update({
                "valid":
                int(int(row.get("valid") or 0) > 0),
                "valid_count":
                int(row.get("valid") or 0),
                "put_valid":
                int(int(row.get("put_valid") or 0) > 0),
                "put_valid_count":
                int(row.get("put_valid") or 0),
                "concrete_valid":
                int(int(row.get("concrete_valid") or 0) > 0),
                "concrete_valid_count":
                int(row.get("concrete_valid") or 0),
                "valid_put_with_R1_or_R2":
                int(int(row.get("valid_put_with_R1_or_R2") or 0) > 0),
                "valid_put_with_R1_or_R2_count":
                int(row.get("valid_put_with_R1_or_R2") or 0),
                "raw":
                int(row.get("raw") or 0),
                "raw_count":
                int(row.get("raw") or 0),
                "has_R0":
                "R0" in tags,
                "has_R1":
                "R1" in tags,
                "has_R2":
                "R2" in tags,
                "oracle_tags":
                tags,
            })
            _atomic_json(result_path, result)
            check = json.loads(result_path.read_text(encoding="utf-8"))
            if check["adoption"]["put_valid"] != int(check["row"]["put_valid"] > 0):
                raise RuntimeError("adoption presence flag repair did not persist")
        except Exception:
            os.replace(backup, result_path)
            raise
        backup.unlink(missing_ok=True)
    report = {
        "case": f"real203/{spec['subject']}",
        "before_result_sha256": before_sha,
        "after_result_sha256": _sha256(result_path),
        "adoption": check["adoption"],
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(report_dir / f"{spec['subject']}-accounting-repair.json", report)
    return report


def main() -> int:
    """Run a dry-run or rollback-capable adoption for one reviewed case."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=["ReferenceConsideration", "VaultExtension", "TimelockAuthorizerMigrator"],
        required=True)
    parser.add_argument("--canonical-root", type=Path, default=CANONICAL_ROOT)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repair-accounting", action="store_true")
    args = parser.parse_args()
    spec = _specs()[args.case]
    if args.repair_accounting:
        if not args.apply:
            parser.error("--repair-accounting requires --apply")
        report = _repair_accounting(spec, args.canonical_root.resolve(), args.report_dir.resolve())
    else:
        report = _run_case(spec, args.canonical_root.resolve(), args.report_dir.resolve(),
                           args.apply)
    print(
        json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            **report
        },
                   indent=2,
                   sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
