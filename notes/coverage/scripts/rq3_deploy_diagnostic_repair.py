#!/usr/bin/env python3
"""Transactionally reclassify two RQ3 deploy smokes as diagnostics."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from rq1_veriput_run import summarize_put_artifacts, write_dataset_manifest

DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg")
TARGETS = {
    "compound-finance__comet__CometStorage": {
        "contract": "CometStorage",
        "test": "test_cov_CometStorage_deploy_only",
        "result_sha256": "e16a730720f9445e6a301bac6f23981f83b32b4c5493fbf8b2628007157ea036",
        "put_sha256": "8da10bb60ef1b85103af7bd60cc00f7067cacc5ebfc5007c9e2172cc594bba2c",
        "summary_sha256": "90509c25bc322959e5660d0ed0a2f3c67596112d7dfa89f560598fa2b78ade30",
    },
    "compound-finance__comet__ConfiguratorStorage": {
        "contract": "ConfiguratorStorage",
        "test": "test_cov_ConfiguratorStorage_deploy_only",
        "result_sha256": "754b4a82dc4a1f3f9325f95659ed4a58dd355a3de079e63c6b938e9302bbd006",
        "put_sha256": "e05626825844ffb1be85c5a4c48940d2485bd5e6f63a32bd8d49bed415245c5d",
        "summary_sha256": "03d1a38c3405cf1e9a7aca7c0cfc3bd24b143b5bc77da0cb7c646a5e5bb180ab",
    },
}
JOURNAL_SHA256 = "dce016cb00b561fd34b7c5b6ddb3108ee7b1ffb646d76d4c99e0344a6e3a8dab"
MANIFEST_SHA256 = "69180785f0311f406021357ef29c04ed2ba12a1f2507b7204cb142c016081f77"


class RepairError(RuntimeError):
    """The frozen RQ3 input does not match the reviewed transaction."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _replace_prefix(value, old: Path, new: Path):
    if isinstance(value, dict):
        return {key: _replace_prefix(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_prefix(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(str(old), str(new))
    return value


def _assert_case(case_dir: Path, spec: dict) -> tuple[Path, Path, Path]:
    result = case_dir / "result.json"
    put_json = case_dir / "put" / "deploy_only" / "_wd" / "deploy_only" / "put.json"
    summary = case_dir / "put" / "deploy_only" / "put-summary.json"
    expected = ((result, spec["result_sha256"]), (put_json, spec["put_sha256"]),
                (summary, spec["summary_sha256"]))
    for path, digest in expected:
        if not path.is_file() or _sha256(path) != digest:
            raise RepairError(f"frozen hash mismatch: {path}")
    record = json.loads(put_json.read_text())
    if not (record.get("kind") == "concrete" and record.get("stage2_source")
            == "no_unit_deploy_fallback" and record.get("stage4_kind") == "deploy-only"
            and record.get("contract") == spec["contract"] and record.get("test") == spec["test"]
            and record.get("forge_status") == "Success"
            and record.get("valid_reference_test") is False):
        raise RepairError(f"unexpected deploy-only record: {put_json}")
    return result, put_json, summary


def _patch_case(staged_case: Path, canonical_case: Path) -> dict:
    put_json = staged_case / "put" / "deploy_only" / "_wd" / "deploy_only" / "put.json"
    summary_path = staged_case / "put" / "deploy_only" / "put-summary.json"
    record = json.loads(put_json.read_text())
    record["kind"] = "diagnostic"
    record["published_as_deliverable"] = False
    put_json.write_bytes(_json_bytes(record))

    stage_summary = json.loads(summary_path.read_text())
    stage_summary["emission"]["concrete_replays_emitted"] = 0
    for row in stage_summary["deliverable_b"]["rows"]:
        row["kind"] = "diagnostic"
    summary_path.write_bytes(_json_bytes(stage_summary))

    put_summary = summarize_put_artifacts(staged_case / "put")
    put_summary = _replace_prefix(put_summary, staged_case, canonical_case)
    if put_summary["raw"] != 0 or put_summary["valid"] != 0:
        raise RepairError(f"diagnostic still counted as deliverable: {canonical_case}")

    result_path = staged_case / "result.json"
    result = json.loads(result_path.read_text())
    row = result["row"]
    result["put"] = put_summary
    direct_keys = (
        "artifact_counts",
        "assertion_oracles",
        "concrete_raw",
        "concrete_valid",
        "foundry_replay_wall_s",
        "oracle_class_combo_counts",
        "oracle_class_counts",
        "put_all_wall_s",
        "put_json_count",
        "put_raw",
        "put_valid",
        "quality_bucket",
        "raw",
        "raw_artifacts",
        "raw_tests",
        "stage4_emission_wall_s",
        "stage4_generation_wall_s",
        "stage4_storage_layout_counts",
        "valid",
        "valid_artifacts",
        "valid_put_with_R1",
        "valid_put_with_R1_or_R2",
        "valid_put_with_R2",
        "valid_put_without_R1R2",
        "valid_tests",
    )
    for key in direct_keys:
        row[key] = copy.deepcopy(put_summary[key])
    row["put_summary_paths"] = copy.deepcopy(put_summary["summary_paths"])
    row["raw_artifacts_retained"] = False
    row["valid_artifacts_retained"] = False
    result_path.write_bytes(_json_bytes(result))
    return row


def build_transaction(root: Path,
                      staging: Path) -> tuple[dict[Path, bytes], dict[Path, bytes], dict]:
    dataset = root / "real203"
    journal = dataset / "results.jsonl"
    manifest = dataset / "manifest.json"
    if _sha256(journal) != JOURNAL_SHA256 or _sha256(manifest) != MANIFEST_SHA256:
        raise RepairError("real203 journal or manifest hash no longer matches the freeze")

    writes: dict[Path, bytes] = {}
    preimages: dict[Path, bytes] = {}
    patched_rows = {}
    for subject_id, spec in TARGETS.items():
        case_dir = dataset / "subjects" / subject_id
        result, put_json, summary = _assert_case(case_dir, spec)
        staged_case = staging / subject_id
        shutil.copytree(case_dir, staged_case)
        patched_rows[subject_id] = _patch_case(staged_case, case_dir)
        for canonical in (result, put_json, summary):
            relative = canonical.relative_to(case_dir)
            preimages[canonical] = canonical.read_bytes()
            writes[canonical] = (staged_case / relative).read_bytes()

    journal_rows = []
    seen = set()
    for line in journal.read_text().splitlines():
        row = json.loads(line)
        subject_id = row.get("subject_id")
        if subject_id in patched_rows:
            if subject_id in seen:
                raise RepairError(f"duplicate frozen journal row: {subject_id}")
            row = patched_rows[subject_id]
            seen.add(subject_id)
        journal_rows.append(row)
    if seen != set(TARGETS):
        raise RepairError(f"missing journal rows: {sorted(set(TARGETS) - seen)}")
    staged_journal = staging / "results.jsonl"
    staged_journal.write_text("".join(
        json.dumps(row, sort_keys=True) + "\n" for row in journal_rows))
    staged_root = staging / "result-root"
    staged_dataset = staged_root / "real203"
    staged_dataset.mkdir(parents=True)
    shutil.copy2(staged_journal, staged_dataset / "results.jsonl")
    write_dataset_manifest(staged_root, "real203", staged_dataset / "results.jsonl")
    staged_manifest = json.loads((staged_dataset / "manifest.json").read_text())
    staged_manifest["generated_at"] = json.loads(manifest.read_text()).get("generated_at")
    staged_manifest["journal"] = str(journal)
    (staged_dataset / "manifest.json").write_bytes(_json_bytes(staged_manifest))
    writes[journal] = staged_journal.read_bytes()
    writes[manifest] = (staged_dataset / "manifest.json").read_bytes()
    preimages[journal] = journal.read_bytes()
    preimages[manifest] = manifest.read_bytes()
    before_manifests = {
        dataset_name: json.loads((root / dataset_name / "manifest.json").read_text())
        for dataset_name in ("peer182", "bugfix124", "real203")
    }
    before_raw = sum(int(doc["summary"]["raw"]) for doc in before_manifests.values())
    before_valid = sum(int(doc["summary"]["valid"]) for doc in before_manifests.values())
    after_real = json.loads(writes[manifest])["summary"]
    before_real = before_manifests["real203"]["summary"]
    after_raw = before_raw - int(before_real["raw"]) + int(after_real["raw"])
    after_valid = before_valid - int(before_real["valid"]) + int(after_real["valid"])
    if (before_raw, before_valid) != (2995, 2140):
        raise RepairError(f"unexpected global RQ3 freeze: raw={before_raw} valid={before_valid}")
    if int(before_real["raw"]) - int(after_real["raw"]) != 2:
        raise RepairError("staged real203 manifest does not remove exactly two raw diagnostics")
    if int(before_real["valid"]) != int(after_real["valid"]):
        raise RepairError("staged real203 manifest unexpectedly changes valid tests")
    if (after_raw, after_valid) != (2993, 2140):
        raise RepairError(f"unexpected staged RQ3 totals: raw={after_raw} valid={after_valid}")
    report = {
        "schema": "veriput-rq3-deploy-diagnostic-repair/v1",
        "targets": sorted(TARGETS),
        "global_before": {
            "raw": before_raw,
            "valid": before_valid,
        },
        "global_after": {
            "raw": after_raw,
            "valid": after_valid,
        },
        "writes": {
            str(path): hashlib.sha256(data).hexdigest()
            for path, data in writes.items()
        },
    }
    return writes, preimages, report


def _atomic_restore(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.rq3-deploy-rollback.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def apply_transaction(writes: dict[Path, bytes], preimages: dict[Path, bytes],
                      archive: Path) -> None:
    if set(writes) != set(preimages):
        raise RepairError("write/preimage target sets differ")
    for path, expected in preimages.items():
        if not path.is_file() or path.read_bytes() != expected:
            raise RepairError(f"compare-before-write mismatch: {path}")
    archive.mkdir(parents=True, exist_ok=False)
    backups = {}
    try:
        for index, (path, data) in enumerate(sorted(writes.items(), key=lambda item: str(item[0]))):
            if path.read_bytes() != preimages[path]:
                raise RepairError(f"concurrent modification before replace: {path}")
            backup = archive / f"{index:02d}-{path.name}.preimage"
            backup.write_bytes(preimages[path])
            backups[path] = backup
            temporary = path.with_name(f".{path.name}.rq3-deploy-diagnostic.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
    except Exception:
        for path, backup in backups.items():
            if backup.exists():
                _atomic_restore(path, backup.read_bytes())
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="rq3-deploy-diagnostic-") as tmp:
        writes, preimages, report = build_transaction(root, Path(tmp))
    if args.apply:
        if args.archive is None:
            raise RepairError("--apply requires --archive")
        apply_transaction(writes, preimages, args.archive.expanduser().resolve())
        report["applied"] = True
    else:
        report["applied"] = False
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
