#!/usr/bin/env python3
"""Run the VeriPUT RQ1 generator over prepared benchmark subjects.

This is the production wrapper around the existing Stage-2 (`certify_all.py`)
and Stage-4 (`put_all.py`) drivers.  It is deliberately subject-scoped:
benchmark inputs are read from `/home/samson/workspace/VeriPUT/Results/*/subjects`,
while all generated artifacts are retained under
`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT`.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import resource
import signal
import shutil
import socket
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import subject_unit_manifest  # noqa: E402
import target_manifest  # noqa: E402
import unit_schedule  # noqa: E402
from solidity_path_put import (  # noqa: E402
    _source_constructor_params_from_source,
    _source_custom_type_symbol,
    _source_type_default_expr,
)
from veriput_recipe import STRONG_RECIPE_VERSION  # noqa: E402
from veriput_subjects import PreparedSubject, SubjectError, resolve_subject  # noqa: E402

PUT_ALL = HERE / "put_all.py"
FORGE_STD = (REPO / "notes" / "coverage-comparison" / "_foundry_roundtrip"
             / "aqua_forge" / "lib" / "forge-std")
FOUNDRY_TOML = """[profile.default]
src = "src"
test = "test"
libs = ["lib"]
via_ir = true
optimizer = true
optimizer_runs = 200
"""
CONCRETE_FALLBACK_WITNESS_CHECKS = {
    "SUCCESSFUL",
    "COMPLETE-WITNESS-NO-COORDINATE",
    "PIN-EXCLUDED-NO-COORDINATE",
    "NOT-CERTIFIED-CE-FALLBACK",
}
CE_REPLAY_MANIFEST_SCHEMA = "veriput-ce-replay-manifest/1"
CE_REPLAY_CANDIDATE_SCHEMA = "veriput-ce-replay-candidate/1"
QUALITY_BUCKET_RANK = {
    "no-valid": 0,
    "valid-no-PUT": 1,
    "valid-PUT-no-R1R2": 2,
    "valid-PUT-with-R1R2": 3,
}

DEFAULT_VERIPUT_ROOT = Path(os.environ.get(
    "VERIPUT_ROOT", "/home/samson/workspace/VeriPUT"))
DEFAULT_RESULT_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1" / "VeriPUT"
DEFAULT_AST_CACHE_ROOT = Path("/tmp/veriput_rq1_ast_cache")
DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S = 0
DEFAULT_ADAPTIVE_STAGE2_UNIT_TIMEOUT_CAP_S = 120
DEFAULT_CONCRETE_ONLY_STAGE4_TIMEOUT_CAP_S = 0
DEFAULT_STAGE2_STAGE4_RESERVE_S = 120
ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD = 4
ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD = 65
ADAPTIVE_STAGE2_FAIR_SHARE_SLOTS = 8
DATASET_LABEL = {
    "peer182": "peer182",
    "bugfix124": "bugfix124",
    "stress243": "real203",
    "stress203": "real203",
    "real203": "real203",
}
TARGET_BENCHMARK_ARG = {
    "peer182": "peer182",
    "bugfix124": "bugfix124",
    "stress243": "stress243",
    "stress203": "stress243",
    "real203": "stress243",
}
PREPARED_DATASET_DIR = {
    "peer182": "Peer182",
    "bugfix124": "BugFix124",
    "stress243": "Stress243",
}


class RQ1RunError(ValueError):
    """The requested production run is unsafe or malformed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(text: str) -> str:
    keep = []
    for ch in str(text):
        keep.append(ch if ch.isalnum() or ch in "._-" else "_")
    return "".join(keep).strip("_") or "unnamed"


def _run_key(subject_id: str, *, ce_collection_only: bool = False) -> str:
    prefix = "ce-collection" if ce_collection_only else "gen:veriput"
    return f"{prefix}:{subject_id}"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def validate_roots(veriput_root: Path, result_root: Path, ast_cache_root: Path) -> None:
    allowed_result = veriput_root / "Results" / "RQ1" / "VeriPUT"
    if not _is_under(result_root, allowed_result):
        raise RQ1RunError(
            f"--result-root must be under {allowed_result}; got {result_root}")
    for protected in (veriput_root / "Datasets", veriput_root / "Results"):
        if _is_under(ast_cache_root, protected):
            raise RQ1RunError(
                f"--ast-cache-root must not be under {protected}; got {ast_cache_root}")


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _candidate_manifest_paths(raw_paths: list[str] | None) -> list[Path]:
    """Normalize explicit CE replay manifests without discovering siblings."""
    out = []
    for raw in raw_paths or []:
        path = Path(raw).expanduser().resolve()
        if path not in out:
            out.append(path)
    return out


def _candidate_value_map(values: object) -> dict[str, int] | None:
    """Convert lossless CE name/value lists to the Stage-4 integer CE shape."""
    if not isinstance(values, list):
        return None
    out = {}
    for item in values:
        if not isinstance(item, dict) or not item.get("name"):
            return None
        try:
            value = int(str(item.get("value")), 0)
        except (TypeError, ValueError):
            return None
        name = str(item["name"])
        previous = out.get(name)
        if previous is not None and previous != value:
            return None
        out[name] = value
    return out


def _candidate_replay_ce(candidate: dict) -> dict[str, int] | None:
    replay = candidate.get("replay") or {}
    if not isinstance(replay, dict):
        return None
    merged = {}
    for key in ("inputs", "entry_storage", "environment"):
        values = _candidate_value_map(replay.get(key))
        if values is None:
            return None
        for name, value in values.items():
            previous = merged.get(name)
            if previous is not None and previous != value:
                return None
            merged[name] = value
    return merged


def _candidate_source_is_local(candidate: dict, case_dir: Path) -> bool:
    source = candidate.get("source") or {}
    if not isinstance(source, dict):
        return False
    recorded_case = source.get("case_dir")
    if not recorded_case:
        return False
    try:
        if Path(recorded_case).expanduser().resolve() != case_dir.resolve():
            return False
        collection_root = (case_dir / "ce-collection").resolve()
        artifact = Path(source.get("artifact_dir", "")).expanduser().resolve()
        journal = Path(source.get("journal", "")).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return (_is_under(artifact, collection_root)
            and _is_under(journal, collection_root)
            and artifact.is_dir() and journal.is_file())


def _load_ce_replay_candidates(manifest_paths: list[Path],
                               target_row: dict, subject: PreparedSubject,
                               case_dir: Path) -> tuple[list[dict], list[dict]]:
    """Load only immutable, case-local CE candidates.

    A CE manifest is refutation evidence.  This function deliberately returns
    candidate records, never an RQ1 result row.  Formal accounting begins only
    after the isolated Stage-4 command passes both gates below.
    """
    candidates = []
    rejected = []
    seen = set()
    for manifest_path in manifest_paths:
        try:
            doc = json.loads(manifest_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError) as exc:
            rejected.append({"manifest": str(manifest_path),
                             "reason": f"invalid manifest: {exc}"})
            continue
        if not isinstance(doc, dict) or doc.get(
                "schema") != CE_REPLAY_MANIFEST_SCHEMA:
            rejected.append({"manifest": str(manifest_path),
                             "reason": "unexpected CE replay manifest schema"})
            continue
        if doc.get("formal_results_written") is not False:
            rejected.append({"manifest": str(manifest_path),
                             "reason": "manifest is not explicitly refutation-only"})
            continue
        for candidate in doc.get("candidates") or []:
            reason = None
            if not isinstance(candidate, dict):
                reason = "candidate is not an object"
            elif candidate.get("schema") != CE_REPLAY_CANDIDATE_SCHEMA:
                reason = "unexpected candidate schema"
            elif candidate.get("status") != "candidate-only":
                reason = "candidate is not candidate-only"
            elif candidate.get("proof_status") != "not-proven":
                reason = "candidate proof status is not not-proven"
            elif (candidate.get("source") or {}).get("refutation_only") is not True:
                reason = "candidate source is not marked refutation-only"
            elif not _candidate_source_is_local(candidate, case_dir):
                reason = "candidate source is not local to this case's CE archive"
            elif not candidate.get("candidate_id"):
                reason = "candidate has no stable id"
            elif candidate.get("candidate_id") in seen:
                reason = "duplicate candidate id"
            else:
                case = candidate.get("case") or {}
                if not isinstance(case, dict):
                    reason = "candidate case identity is malformed"
                elif (case.get("benchmark") != target_row.get("benchmark")
                      or case.get("subject_id") != target_row.get("subject_id")
                      or case.get("contract") != subject.contract):
                    reason = "candidate case identity does not match target"
                elif not case.get("unit"):
                    reason = "candidate has no unit"
                path = candidate.get("path") or {}
                if reason is None and (not isinstance(path, dict)
                                       or not path.get("path_function")
                                       or not re.fullmatch(r"\d+", str(path.get("path_id")))):
                    reason = "candidate path identity is malformed"
                if reason is None and _candidate_replay_ce(candidate) is None:
                    reason = "candidate replay contains non-integer or conflicting values"
            if reason is not None:
                rejected.append({"manifest": str(manifest_path),
                                 "candidate_id": (candidate.get("candidate_id")
                                                   if isinstance(candidate, dict)
                                                   else None),
                                 "reason": reason})
                continue
            seen.add(candidate["candidate_id"])
            candidate = copy.deepcopy(candidate)
            candidate["_manifest"] = str(manifest_path)
            candidates.append(candidate)
    return candidates, rejected


def _candidate_cert_row(candidate: dict, subject: PreparedSubject) -> dict:
    """Build an isolated Stage-4 input row; never a certified/result row."""
    path = candidate["path"]
    enc = str(path["path_id"])
    detail = {
        "concrete_fallback": True,
        "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
        "path_function": str(path["path_function"]),
        "certification_source": "ce-replay-candidate",
        "candidate_id": candidate["candidate_id"],
        "ce": _candidate_replay_ce(candidate),
    }
    return {
        "schema": "veriput-rq1-ce-replay-stage4-row/v1",
        "benchmark": subject.benchmark_key,
        "unit": candidate["case"]["unit"],
        "path_function": str(path["path_function"]),
        "bucket": "NOT-CERTIFIED",
        "certified": {},
        "certified_details": {},
        "not_certified": {enc: "CE replay candidate; no region proof"},
        "not_certified_details": {enc: detail},
        "coords": [],
        "pins": {},
        "scope": "focus",
        "max_tx": 1,
        "subject": subject.to_record(),
        "candidate_provenance": {
            "candidate_id": candidate["candidate_id"],
            "manifest": candidate.get("_manifest"),
            "formal_results_written": False,
            "theory_credit": 0,
        },
    }


def _candidate_gate(summary: dict, candidate: dict) -> dict:
    """Return the two independent gates required before promotion."""
    path = candidate.get("path") or {}
    expected_enc = str(path.get("path_id"))
    expected_unit = (candidate.get("case") or {}).get("unit")
    valid_rows = [row for row in summary.get("valid_tests") or []
                  if isinstance(row, dict)]
    rows = [row for row in valid_rows
            if isinstance(row, dict)
            and str(row.get("unit")) == str(expected_unit)
            and str(row.get("enc")) == expected_enc]
    verifier_passed = bool(rows) and all(
        bool(row.get("valid_reference_test"))
        and not row.get("refused")
        and not row.get("stale")
        for row in rows)
    foundry_passed = bool(rows) and all(
        row.get("forge_status") == "Success" for row in rows)
    # A refutation-only candidate may become a concrete replay, never a PUT or
    # an R1/R2 claim.  A PUT-shaped result here indicates an isolation failure.
    isolation_passed = (bool(rows) and len(rows) == len(valid_rows)
                        and all(row.get("kind") == "concrete"
                                for row in rows))
    return {
        "verifier_passed": verifier_passed,
        "foundry_double_oracle_passed": foundry_passed,
        "candidate_isolation_passed": isolation_passed,
        "promotable": verifier_passed and foundry_passed and isolation_passed,
        "matching_valid_tests": len(rows),
        "unexpected_valid_tests": len(valid_rows) - len(rows),
        "theory_delta": 0,
    }


def _rewrite_promoted_paths(root: Path, old_root: Path, new_root: Path) -> None:
    """Relocate absolute artifact paths in copied JSON ledgers."""
    old = str(old_root)
    new = str(new_root)
    for path in root.rglob("*.json"):
        try:
            text = path.read_text(errors="replace")
            updated = text.replace(old, new)
            if updated != text:
                path.write_text(updated)
        except OSError:
            continue


def _promote_candidate_artifacts(staging_root: Path, case_dir: Path,
                                 candidate_id: str) -> Path:
    """Copy an accepted isolated Stage-4 result into the formal artifact tree."""
    destination = (case_dir / "put" / "ce-replay" / _safe_name(candidate_id))
    if destination.exists():
        raise RQ1RunError(
            f"refusing to overwrite existing CE replay artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging_root, destination)
    _rewrite_promoted_paths(destination, staging_root, destination)
    return destination


def _candidate_rejection(candidate: dict, reason: str,
                         detail: str | None = None) -> dict:
    rejection = {
        "candidate_id": candidate.get("candidate_id"),
        "unit": (candidate.get("case") or {}).get("unit"),
        "path_id": (candidate.get("path") or {}).get("path_id"),
        "reason": reason,
    }
    if detail:
        rejection["detail"] = detail
    return rejection


def prepare_case_dir(case_dir: Path, *, force_fresh: bool = False) -> None:
    if not case_dir.exists():
        return
    if force_fresh:
        suffix = f".redo.{int(time.time())}.{os.getpid()}"
        target = case_dir.with_name(case_dir.name + suffix)
        case_dir.rename(target)
        return
    if case_dir.joinpath("result.json").exists():
        return
    try:
        has_content = any(case_dir.iterdir())
    except OSError:
        has_content = True
    if not has_content:
        return
    suffix = f".incomplete.{int(time.time())}.{os.getpid()}"
    target = case_dir.with_name(case_dir.name + suffix)
    case_dir.rename(target)


def _latest_rows(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = row.get("key")
        if key:
            out[key] = row
    return out


def _row_strength(row: dict | None) -> tuple[int, int, int, int, int]:
    if not row:
        return (0, 0, 0, 0, 0)
    if "valid_tests" in row and isinstance(row.get("valid_tests"), list):
        valid_tests = row.get("valid_tests") or []
        valid_tests = [
            test for test in valid_tests
            if isinstance(test, dict) and _is_valid_reference_test(test)
        ]
        valid = len(valid_tests)
        put_valid = sum(
            1 for test in valid_tests
            if test.get("kind") == "put")
        r1r2 = sum(
            1 for test in valid_tests
            if test.get("kind") == "put"
            and _has_oracle_class(test, "R1", "R2"))
    else:
        valid = int(row.get("valid") or 0)
        put_valid = int(row.get("put_valid") or 0)
        r1r2 = int(row.get("valid_put_with_R1_or_R2") or 0)
        if valid <= 0:
            valid = put_valid + int(row.get("concrete_valid") or 0)
    if valid <= 0:
        bucket = 0
    elif put_valid <= 0:
        bucket = 1
    elif r1r2 <= 0:
        bucket = 2
    else:
        bucket = 3
    return (
        bucket,
        r1r2,
        put_valid,
        valid,
        int(row.get("raw") or 0),
    )


def _load_subject_result_row(case_dir: Path) -> dict | None:
    path = case_dir / "result.json"
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    row = doc.get("row") if isinstance(doc, dict) else None
    if not isinstance(row, dict):
        row = doc if isinstance(doc, dict) else None
    if not isinstance(row, dict):
        return None
    certification = doc.get("certification") if isinstance(doc, dict) else None
    if isinstance(certification, dict):
        _merge_certification_summary_fields(row, certification)
    _merge_certification_summary_fields(
        row, summarize_certification(case_dir / "cert" / "certify-results.jsonl"))
    row = _normalize_result_row(row)
    row["artifact_root"] = str(case_dir)
    row["result_json"] = str(path)
    row = _merge_put_summary_into_row(row, case_dir)
    row["artifact_root"] = str(case_dir)
    row["result_json"] = str(path)
    return row


def _merge_certification_summary_fields(row: dict,
                                        cert_summary: dict | None) -> dict:
    if not isinstance(cert_summary, dict):
        return row
    mapping = {
        "bucket_counts": "cert_bucket_counts",
        "exit_counts": "cert_exit_counts",
        "witness_counts": "cert_witness_counts",
        "timed_out_units": "cert_timed_out_units",
        "oom_units": "cert_oom_units",
        "driver_refusal_tags": "driver_refusal_tags",
        "driver_diagnostic_tags": "driver_diagnostic_tags",
    }
    for src, dst in mapping.items():
        value = cert_summary.get(src)
        if value and not row.get(dst):
            row[dst] = value
    return row


def _merge_put_summary_into_row(row: dict, case_dir: Path) -> dict:
    put_summary = summarize_put_artifacts(case_dir / "put")
    if put_summary["raw"] <= 0:
        return _annotate_result_accounting(row)
    row = dict(row)
    # Once raw Stage-4 artifacts are present, the artifact scan is the
    # authoritative source for strength counters. Older result rows may carry
    # stale aggregate fields from a pre-normalization run, and keeping the max
    # would make those stale counts survive even when valid_tests says otherwise.
    for key in (
            "raw",
            "valid",
            "put_raw",
            "put_valid",
            "concrete_raw",
            "concrete_valid",
            "valid_put_with_R1",
            "valid_put_with_R2",
            "valid_put_with_R1_or_R2",
            "valid_put_without_R1R2",
            "valid_concrete",
            "put_json_count",
    ):
        row[key] = int(put_summary.get(key) or 0)
    for key in (
            "raw_tests",
            "valid_tests",
            "assertion_oracles",
            "raw_artifacts",
            "valid_artifacts",
            "artifact_counts",
            "time_stats",
            "stage4_storage_layout_counts",
    ):
        if put_summary.get(key):
            row[key] = put_summary[key]
    for key in ("oracle_class_counts", "oracle_class_combo_counts"):
        if put_summary.get(key):
            row[key] = put_summary[key]
    if put_summary.get("summary_paths"):
        row["put_summary_paths"] = put_summary["summary_paths"]
    for key in (
            "raw_oracle_tag_counts",
            "valid_oracle_tag_counts",
            "raw_oracle_combo_counts",
            "valid_oracle_combo_counts",
            "rq1_oracle_tag_counts",
            "rq1_oracle_combo_counts",
    ):
        if put_summary.get(key):
            row[key] = put_summary[key]
    for key in (
            "stage4_generation_wall_s",
            "stage4_emission_wall_s",
            "foundry_replay_wall_s",
            "put_all_wall_s",
    ):
        if float(put_summary.get(key) or 0.0) > float(row.get(key) or 0.0):
            row[key] = put_summary[key]
    row["quality_bucket"] = _legacy_quality_bucket(row)
    row["raw_artifacts_retained"] = True
    if put_summary["valid"] > 0:
        row["valid_artifacts_retained"] = True
    if put_summary["valid"] > 0 and row.get("status") != "ok":
        if row.get("reason") and not row.get("partial_failure_reason"):
            row["partial_failure_reason"] = row.get("reason")
        row["status"] = "ok"
        row["reason"] = None
    row["adopted_put_summary_artifacts"] = True
    return _annotate_result_accounting(row)


def _artifact_summary_row(target_row: dict,
                          dataset_label: str,
                          case_dir: Path,
                          current: dict | None = None) -> dict | None:
    put_summary = summarize_put_artifacts(case_dir / "put")
    if put_summary["raw"] <= 0:
        return None
    subject_id = target_row["subject_id"]
    row = dict(current or {})
    row.update({
        "key": f"gen:veriput:{subject_id}",
        "stage": "gen_veriput",
        "schema": "veriput-rq1-result-row/v1",
        "subject_id": subject_id,
        "benchmark": target_row.get("benchmark"),
        "dataset": dataset_label,
        "contract": target_row.get("contract"),
        "artifact_root": str(case_dir),
        "result_json": str(case_dir / "result.json")
        if (case_dir / "result.json").exists() else None,
        "raw_artifacts_retained": True,
        "valid_artifacts_retained": put_summary["valid"] > 0,
    })
    if not row.get("status"):
        row["status"] = "ok" if put_summary["valid"] > 0 else "no-output"
    if not row.get("completion_status"):
        row["completion_status"] = row["status"]
    _merge_certification_summary_fields(
        row, summarize_certification(case_dir / "cert" / "certify-results.jsonl"))
    return _merge_put_summary_into_row(row, case_dir)


def _normalize_result_row(row: dict) -> dict:
    row = dict(row)
    if "valid_tests" in row and isinstance(row.get("valid_tests"), list):
        valid_tests = row.get("valid_tests") or []
        valid_tests = [
            test for test in valid_tests
            if isinstance(test, dict) and _is_valid_reference_test(test)
        ]
        row["valid_tests"] = valid_tests
        valid_puts = [
            test for test in valid_tests
            if test.get("kind") == "put"
        ]
        valid_puts_with_r1 = [
            test for test in valid_puts if _has_oracle_class(test, "R1")
        ]
        valid_puts_with_r2 = [
            test for test in valid_puts if _has_oracle_class(test, "R2")
        ]
        valid_puts_with_r1r2 = [
            test for test in valid_puts if _has_oracle_class(test, "R1", "R2")
        ]
        valid_concrete = sum(
            1 for test in valid_tests
            if isinstance(test, dict) and test.get("kind") == "concrete")
        row["valid"] = len(valid_tests)
        row["put_valid"] = len(valid_puts)
        row["concrete_valid"] = valid_concrete
        row["valid_put_with_R1"] = len(valid_puts_with_r1)
        row["valid_put_with_R2"] = len(valid_puts_with_r2)
        row["valid_put_with_R1_or_R2"] = len(valid_puts_with_r1r2)
        row["valid_put_without_R1R2"] = (
            len(valid_puts) - len(valid_puts_with_r1r2))
        row["valid_concrete"] = valid_concrete
        row["quality_bucket"] = _legacy_quality_bucket(row)
    else:
        component_valid = (
            _row_count(row, "put_valid") + _row_count(row, "concrete_valid"))
        if row.get("valid") is None:
            valid = component_valid
            if valid <= 0:
                valid = len(row.get("valid_tests") or [])
            row["valid"] = valid
        elif component_valid > _row_count(row, "valid"):
            row["valid"] = component_valid
        if row.get("valid_concrete") is None and row.get(
                "concrete_valid") is not None:
            row["valid_concrete"] = _row_count(row, "concrete_valid")
        row["quality_bucket"] = _legacy_quality_bucket(row)
    if _row_count(row, "valid") > 0 and row.get("status") != "ok":
        if row.get("reason") and not row.get("partial_failure_reason"):
            row["partial_failure_reason"] = row.get("reason")
        row["status"] = "ok"
        row["reason"] = None
    return _annotate_result_accounting(row)


def _artifact_count_summary(row: dict) -> dict:
    valid = _row_count(row, "valid")
    raw = _row_count(row, "raw")
    return {
        "raw": raw,
        "valid": valid,
        "put_raw": _row_count(row, "put_raw"),
        "put_valid": _row_count(row, "put_valid"),
        "concrete_raw": _row_count(row, "concrete_raw"),
        "concrete_valid": _row_count(row, "concrete_valid"),
        "valid_put_with_R1": _row_count(row, "valid_put_with_R1"),
        "valid_put_with_R2": _row_count(row, "valid_put_with_R2"),
        "valid_put_with_R1_or_R2":
            _row_count(row, "valid_put_with_R1_or_R2"),
        "valid_put_without_R1R2":
            _row_count(row, "valid_put_without_R1R2"),
    }


def _row_time_stats(row: dict) -> dict:
    return {
        "generation_wall_s": float(row.get("generation_wall_s") or 0.0),
        "stage2_wall_s": float(row.get("stage2_wall_s") or 0.0),
        "stage4_wall_s": float(row.get("stage4_wall_s") or 0.0),
        "stage4_generation_wall_s":
            float(row.get("stage4_generation_wall_s") or 0.0),
        "stage4_emission_wall_s":
            float(row.get("stage4_emission_wall_s") or 0.0),
        "foundry_replay_wall_s":
            float(row.get("foundry_replay_wall_s") or 0.0),
        "put_all_wall_s": float(row.get("put_all_wall_s") or 0.0),
        "wall_total_s": float(row.get("wall_total_s") or row.get("wall") or 0.0),
    }


def _annotate_result_accounting(row: dict) -> dict:
    row = dict(row)
    row["failure_reason"] = (
        row.get("reason") or row.get("partial_failure_reason"))
    row["raw_artifacts"] = row.get("raw_artifacts") or row.get("raw_tests") or []
    row["valid_artifacts"] = (
        row.get("valid_artifacts") or row.get("valid_tests") or [])
    row["artifact_counts"] = _artifact_count_summary(row)
    row["time_stats"] = _row_time_stats(row)
    row["quality_bucket"] = row.get("quality_bucket") or _legacy_quality_bucket(row)
    return row


def _row_needs_normalized_adoption(current: dict | None,
                                   candidate: dict) -> bool:
    if not current:
        return True
    current = _normalize_result_row(current)
    candidate = _normalize_result_row(candidate)
    if _row_strength(candidate) > _row_strength(current):
        return True
    if current.get("valid") is None and candidate.get("valid") is not None:
        return True
    if not current.get("quality_bucket") and candidate.get("quality_bucket"):
        return True
    for key in (
            "raw",
            "valid",
            "put_raw",
            "put_valid",
            "concrete_raw",
            "concrete_valid",
            "valid_put_with_R1",
            "valid_put_with_R2",
            "valid_put_with_R1_or_R2",
            "valid_put_without_R1R2",
            "valid_concrete",
            "put_json_count",
    ):
        if _row_count(candidate, key) > _row_count(current, key):
            return True
    for key in (
            "raw_tests",
            "valid_tests",
            "assertion_oracles",
            "put_summary_paths",
    ):
        candidate_value = candidate.get(key)
        current_value = current.get(key)
        if candidate_value and candidate_value != current_value:
            return True
    for key in (
            "oracle_class_counts",
            "oracle_class_combo_counts",
            "stage4_storage_layout_counts",
    ):
        candidate_value = candidate.get(key)
        current_value = current.get(key)
        if isinstance(candidate_value, dict) and candidate_value:
            if candidate_value != (current_value or {}):
                return True
    for key in (
            "raw_artifacts_retained",
            "valid_artifacts_retained",
            "adopted_put_summary_artifacts",
    ):
        if candidate.get(key) and not current.get(key):
            return True
    for key in (
            "stage4_generation_wall_s",
            "stage4_emission_wall_s",
            "foundry_replay_wall_s",
            "put_all_wall_s",
    ):
        if float(candidate.get(key) or 0.0) > float(current.get(key) or 0.0):
            return True
    for key in (
            "cert_bucket_counts",
            "cert_exit_counts",
            "cert_witness_counts",
            "cert_timed_out_units",
            "cert_oom_units",
            "driver_refusal_tags",
            "driver_diagnostic_tags",
    ):
        if candidate.get(key) and not current.get(key):
            return True
    return False


def _historical_case_dirs(case_dir: Path) -> list[Path]:
    parent = case_dir.parent
    if not parent.exists():
        return []
    prefixes = (
        f"{case_dir.name}.redo.",
        f"{case_dir.name}.incomplete.",
    )
    candidates = [
        path for path in parent.iterdir()
        if path.is_dir() and any(path.name.startswith(prefix)
                                 for prefix in prefixes)
    ]
    return sorted(candidates,
                  key=lambda path: path.stat().st_mtime
                  if path.exists() else 0.0,
                  reverse=True)


def _stale_scope_matches_target(old_dir: Path, target_row: dict) -> bool:
    """Reject retained artifacts generated from inherited non-target units."""
    schedule_path = old_dir / "unit-schedule.json"
    if not schedule_path.exists():
        # Older artifacts predate the auditable schedule.  Keep the legacy
        # adoption path for them; new runs always write the scope evidence.
        return True
    try:
        schedule = json.loads(schedule_path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = str(target_row.get("contract") or "")
    for job in schedule.get("jobs") or []:
        owner = str((job.get("unit_info") or {}).get("contract") or "")
        if owner and expected and owner != expected:
            return False
    return True


def _best_stale_artifact_row(target_row: dict, dataset_label: str,
                             case_dir: Path, current: dict) -> dict | None:
    best = None
    for old_dir in _historical_case_dirs(case_dir):
        if not _stale_scope_matches_target(old_dir, target_row):
            continue
        row = _load_subject_result_row(old_dir)
        if row is None:
            row = _artifact_summary_row(target_row, dataset_label, old_dir)
        if row is None:
            continue
        row["stale_artifact_root"] = str(old_dir)
        if _row_strength(row) > _row_strength(best):
            best = row
    if best is None or _row_strength(best) <= _row_strength(current):
        return None
    return best


def _adopt_stale_artifacts(row: dict, stale: dict | None) -> dict:
    if stale is None:
        return _annotate_result_accounting(row)
    merged = dict(row)
    original_reason = (
        row.get("reason") or row.get("failure_reason")
        or row.get("partial_failure_reason"))
    for key in (
            "raw",
            "valid",
            "put_raw",
            "put_valid",
            "concrete_raw",
            "concrete_valid",
            "valid_put_with_R1",
            "valid_put_with_R2",
            "valid_put_with_R1_or_R2",
            "valid_put_without_R1R2",
            "valid_concrete",
            "quality_bucket",
            "raw_tests",
            "valid_tests",
            "raw_artifacts",
            "valid_artifacts",
            "put_summary_paths",
            "put_json_count",
            "oracle_class_counts",
            "oracle_class_combo_counts",
            "raw_oracle_tag_counts",
            "valid_oracle_tag_counts",
            "raw_oracle_combo_counts",
            "valid_oracle_combo_counts",
            "rq1_oracle_tag_counts",
            "rq1_oracle_combo_counts",
            "assertion_oracles",
            "stage4_storage_layout_counts",
            "artifact_counts",
    ):
        if key in stale:
            merged[key] = copy.deepcopy(stale[key])
    for key in (
            "stage4_generation_wall_s",
            "stage4_emission_wall_s",
            "foundry_replay_wall_s",
            "put_all_wall_s",
    ):
        if float(stale.get(key) or 0.0) > float(merged.get(key) or 0.0):
            merged[key] = stale[key]
    merged["adopted_stale_valid_artifacts"] = _row_count(stale, "valid") > 0
    merged["adopted_stale_artifacts"] = True
    merged["stale_artifact_root"] = stale.get("stale_artifact_root")
    merged["stale_result_json"] = stale.get("result_json")
    merged["stale_quality_bucket"] = stale.get("quality_bucket")
    merged["stale_adoption_reason"] = original_reason
    if _row_count(stale, "valid") > 0:
        merged["partial_failure_reason"] = original_reason
        merged["status"] = "ok"
        merged["reason"] = None
        merged["valid_artifacts_retained"] = True
    if _row_count(stale, "raw") > 0:
        merged["raw_artifacts_retained"] = True
    return _annotate_result_accounting(merged)


def _load_case_result_doc(case_dir: Path) -> dict | None:
    try:
        doc = json.loads((case_dir / "result.json").read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _case_result_row(doc: dict | None) -> dict | None:
    if not isinstance(doc, dict):
        return None
    row = doc.get("row")
    if isinstance(row, dict):
        return row
    return doc


def _case_result_needs_normalized_write(case_dir: Path, candidate: dict) -> bool:
    doc = _load_case_result_doc(case_dir)
    current = _case_result_row(doc)
    if not isinstance(current, dict):
        return True
    return (
        _row_strength(candidate) > _row_strength(current)
        or _row_needs_normalized_adoption(current, candidate))


def _write_normalized_case_result(case_dir: Path, row: dict, *,
                                  reason: str) -> bool:
    """Write recovered artifact strength back to subject result.json.

    Worker feedback and results_all.py use the per-subject result.json as the
    canonical RQ1 ledger.  A runner resume/adoption pass must therefore persist
    rows recovered from retained Stage-4 artifacts, not only update the dataset
    journal, otherwise valid PUTs are repeatedly reported as no-valid.
    """

    if not _case_result_needs_normalized_write(case_dir, row):
        return False
    doc = _load_case_result_doc(case_dir) or {
        "schema": "veriput-rq1-case-result/v1",
    }
    doc["row"] = _annotate_result_accounting(row)
    put_summary = summarize_put_artifacts(case_dir / "put")
    if put_summary.get("raw", 0) > 0:
        # Keep the human-facing top-level summary in lockstep with the
        # canonical row.  Older adoption runs updated only ``row``, leaving
        # stale put_valid/valid counters in result.json even though the
        # journal and retained artifacts were correct.
        doc["put"] = put_summary
        adoption = dict(doc.get("adoption") or {})
        for key in (
                "valid", "put_valid", "concrete_valid",
                "valid_put_with_R1", "valid_put_with_R2",
                "valid_put_with_R1_or_R2"):
            adoption[key] = row.get(key, 0)
        adoption["has_R0"] = bool(row.get("valid_tests"))
        adoption["has_R1"] = row.get("valid_put_with_R1", 0) > 0
        adoption["has_R2"] = row.get("valid_put_with_R2", 0) > 0
        adoption["oracle_tags"] = sorted(
            set(row.get("valid_oracle_tag_counts") or {}))
        adoption["source"] = "rq1_veriput_run.normalized_case_result"
        adoption["adopted_ts"] = time.time()
        doc["adoption"] = adoption
    normalization = dict(doc.get("normalization") or {})
    normalization.update({
        "normalized_at": _utc_now(),
        "reason": reason,
        "source": "rq1_veriput_run.adopt_existing_subject_results",
    })
    doc["normalization"] = normalization
    _write_json(case_dir / "result.json", doc)
    return True


def _row_needs_resume_retry(row: dict | None) -> bool:
    """Resume should not make old empty no-valid rows terminal.

    RQ1 resume exists to avoid repeating completed work.  Empty no-valid rows
    from older scheduler runs are not completed work when the current runner has
    stronger unit selection and timeout/candidate handling: keeping them in the
    `done` map prevents the new policy from ever reaching Stage 2/4.
    """
    if not isinstance(row, dict):
        return False
    row = _normalize_result_row(row)
    if _row_count(row, "valid") > 0 or _row_count(row, "raw") > 0:
        return False
    if _row_strength(row)[0] > 0:
        return False
    status = str(row.get("status") or row.get("completion_status") or "")
    if status not in (
            "no-output",
            "ok",
            "timeout",
            "oom",
            "budget-exhausted",
            "error",
            "no-units"):
        return False
    schedule_summary = row.get("schedule_summary") or {}
    if isinstance(schedule_summary, dict):
        skipped_by_status = schedule_summary.get("skipped_by_status") or {}
        if any(skipped_by_status.get(key) for key in ("missing-ast", "error")):
            return True
    if status == "error":
        reason = str(row.get("reason") or "")
        return (
            "runner exception" in reason
            or "unit schedule preparation failed" in reason
            or "missing compact AST" in reason
            or not reason)
    if status == "ok":
        return True
    if status == "no-units":
        return True
    reason = str(row.get("reason") or row.get("early_stop_reason") or "")
    if "no Stage-2 candidate after" in reason:
        return True
    if "certification timed out before PUT artifacts" in reason:
        return True
    diagnostics = row.get("driver_diagnostic_tags") or {}
    if isinstance(diagnostics, dict):
        if any(tag in NON_METHOD_NO_CANDIDATE_DIAGNOSTICS
               for tag in diagnostics):
            return True
    cert_bucket_counts = row.get("cert_bucket_counts") or {}
    if int(cert_bucket_counts.get("CERTIFIED") or 0) > 0:
        return True
    if row.get("put_summary_paths"):
        return True
    if row.get("stage4_candidate_units_attempted") == 0:
        return True
    if row.get("stage2_capped_timeout_unit_count"):
        return True
    return False


def _row_needs_quality_retry(row: dict | None, quality_floor: str) -> bool:
    if not isinstance(row, dict):
        return False
    floor = QUALITY_BUCKET_RANK.get(quality_floor)
    if floor is None or floor <= 0:
        return False
    row = _normalize_result_row(row)
    bucket = str(row.get("quality_bucket") or _legacy_quality_bucket(row))
    return QUALITY_BUCKET_RANK.get(bucket, 0) < floor


def retryable_resume_rows(done: dict[str, dict],
                          quality_floor: str = "valid-PUT-with-R1R2") -> dict[str, dict]:
    return {
        key: row for key, row in done.items()
        if (_row_needs_resume_retry(row)
            or _row_needs_quality_retry(row, quality_floor))
    }


def _empty_schedule_status_reason(schedule: dict) -> tuple[str, str]:
    summary = schedule.get("summary") or {}
    skipped_by_status = summary.get("skipped_by_status") or {}
    skipped_rows = schedule.get("skipped_rows") or []
    no_unit_rows = schedule.get("no_unit_rows") or []
    if skipped_by_status:
        parts = [
            f"{key}={value}"
            for key, value in sorted(skipped_by_status.items())
            if value
        ]
        detail = ", ".join(parts) or "unknown"
        first_reason = next(
            (str(row.get("reason")) for row in skipped_rows
             if isinstance(row, dict) and row.get("reason")),
            "")
        if first_reason:
            detail = f"{detail}: {first_reason}"
        return "error", f"unit schedule preparation failed: {detail}"
    try:
        no_unit_count = int(summary.get("no_unit_rows") or 0)
    except (TypeError, ValueError):
        no_unit_count = 0
    if no_unit_rows or no_unit_count > 0:
        first = (
            no_unit_rows[0]
            if no_unit_rows and isinstance(no_unit_rows[0], dict)
            else {})
        reason = str(first.get("reason") or
                     "target contract has no schedulable public/external units")
        return "no-units", reason
    if summary.get("unit_filter"):
        missing = ", ".join(str(u) for u in summary.get("unit_filter") or [])
        return "no-output", f"unit filter matched no scheduled units: {missing}"
    if int(summary.get("jobs") or 0) == 0 and int(summary.get("subjects") or 0) == 0:
        return "no-units", "legacy empty no-unit schedule"
    return "no-output", "unit schedule produced no jobs"


def _is_true_no_unit_schedule(schedule: dict) -> bool:
    if schedule.get("jobs"):
        return False
    summary = schedule.get("summary") or {}
    skipped_by_status = summary.get("skipped_by_status") or {}
    if any(skipped_by_status.get(key) for key in ("missing-ast", "error")):
        return False
    if schedule.get("no_unit_rows"):
        return True
    try:
        if int(summary.get("no_unit_rows") or 0) > 0:
            return True
        return (int(summary.get("jobs") or 0) == 0
                and int(summary.get("subjects") or 0) == 0
                and not summary.get("unit_filter"))
    except (TypeError, ValueError):
        return False


def _contract_decl_kind(source: str, contract: str) -> tuple[str | None, bool]:
    if not contract:
        return None, False
    rx = re.compile(
        r"\b(?:(abstract)\s+)?(contract|interface|library)\s+"
        + re.escape(contract) + r"\b")
    match = rx.search(source or "")
    if not match:
        return None, False
    return match.group(2), bool(match.group(1))


def _ensure_foundry_tools_on_path():
    path = os.environ.get("PATH", "")
    dirs = path.split(os.pathsep) if path else []
    extra = [
        str(Path.home() / ".foundry" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/home/administrator/.foundry/bin",
        "/home/administrator/.local/bin",
    ]
    prepend = [
        d for d in extra
        if d not in dirs and (Path(d) / "forge").exists()
    ]
    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + dirs)


def _subject_flat_sol_candidates(subject: PreparedSubject) -> list[Path]:
    out = []

    def add(path: str | Path | None):
        if not path:
            return
        p = Path(path).expanduser()
        if p not in out:
            out.append(p)

    add(subject.flat_sol)
    try:
        resolved = resolve_subject(
            subject.subject_id,
            benchmark=subject.benchmark,
            require_unit=False)
        add(resolved.flat_sol)
    except SubjectError:
        pass
    dirname = PREPARED_DATASET_DIR.get(subject.benchmark)
    if dirname:
        for base in (
                DEFAULT_VERIPUT_ROOT / "Results" / dirname / "subjects",
                DEFAULT_VERIPUT_ROOT / "scripts" / "Results" / "workdirs"
                / dirname / "subjects",
        ):
            add(base / subject.subject_id / "flat.sol")
            if subject.benchmark == "bugfix124":
                add(base / subject.subject_id / "fix.flat.sol")
    return out


def _existing_subject_flat_sol(subject: PreparedSubject) -> Path | None:
    for path in _subject_flat_sol_candidates(subject):
        if path.exists():
            return path
    return None


def _prepare_deploy_only_project(project: Path, subject: PreparedSubject,
                                 flat_sol: Path):
    for sub in ("src", "test", "lib"):
        (project / sub).mkdir(parents=True, exist_ok=True)
    (project / "foundry.toml").write_text(FOUNDRY_TOML)
    shutil.copyfile(flat_sol, project / "src" / "flat.sol")
    lib = project / "lib" / "forge-std"
    if lib.is_symlink():
        target = Path(os.readlink(lib))
        target_abs = target if target.is_absolute() else (lib.parent / target)
        if target_abs.resolve() != FORGE_STD.resolve() or not target_abs.exists():
            lib.unlink()
    if not lib.exists() and FORGE_STD.exists():
        lib.symlink_to(FORGE_STD)


def _forge_json_status(stdout: str, test_name: str) -> str | None:
    try:
        suites = json.loads(stdout or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(suites, dict):
        return None
    suite_failure = None
    for suite in suites.values():
        if not isinstance(suite, dict):
            continue
        results = suite.get("test_results") or {}
        setup = results.get("setUp()")
        if isinstance(setup, dict) and setup.get("status") == "Failure":
            suite_failure = "Failure"
        for name, result in results.items():
            if str(name).split("(", 1)[0] != test_name:
                continue
            if isinstance(result, dict):
                return result.get("status")
    return suite_failure


def _run_forge_json(project: Path, test_name: str,
                    timeout_s: int) -> tuple[str | None, bool, float, str]:
    _ensure_foundry_tools_on_path()
    start = time.monotonic()
    proc = subprocess.Popen(
        ["forge", "test", "--json", "--match-test", test_name],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        stdout, stderr = proc.communicate()
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    wall_s = round(time.monotonic() - start, 3)
    status = None if timed_out else _forge_json_status(stdout, test_name)
    return status, timed_out, wall_s, (stdout or "") + (stderr or "")


def _no_unit_deploy_test_source(subject: PreparedSubject,
                                source: str) -> tuple[str | None, str | None]:
    kind, is_abstract = _contract_decl_kind(source, subject.contract)
    if kind != "contract" or is_abstract:
        reason = "deploy-only fallback supports only concrete contract targets"
        if kind:
            reason += f"; got {'abstract ' if is_abstract else ''}{kind}"
        return None, reason
    params = _source_constructor_params_from_source(source, subject.contract)
    ctor_args = []
    import_symbols = [subject.contract]
    for idx, (_name, typ) in enumerate(params):
        expr = _source_type_default_expr(typ, 1000 + idx)
        if expr is None:
            return None, (
                "deploy-only fallback cannot synthesize constructor argument "
                f"{idx} of type `{typ}`")
        ctor_args.append(expr)
        custom = _source_custom_type_symbol(typ)
        if custom and custom not in import_symbols:
            import_symbols.append(custom)
    test_contract = f"{subject.contract}DeployOnlyCovTest"
    test_name = f"test_cov_{subject.contract}_deploy_only"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated by VeriPUT for a target with no focusable unit.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        f'import {{{", ".join(import_symbols)}}} from "../src/flat.sol";',
        "",
        f"contract {test_contract} is Test {{",
        f"  function {test_name}() public {{",
        f"    {subject.contract} c0 = new {subject.contract}"
        f"({', '.join(ctor_args)});",
        '    assertTrue(address(c0) != address(0), "deployment succeeded");',
        "  }",
        "}",
        "",
    ]), None


def _write_no_unit_deploy_refusal(out_root: Path, subject: PreparedSubject,
                                  reason: str) -> dict:
    out_root.mkdir(parents=True, exist_ok=True)
    wd = out_root / "_wd" / "deploy_only"
    wd.mkdir(parents=True, exist_ok=True)
    rec = {
        "kind": "concrete",
        "stage2_source": "no_unit_deploy_fallback",
        "stage4_kind": "deploy-only",
        "contract": subject.contract,
        "unit": "__deploy__",
        "enc": 0,
        "refused": "deploy-only-unavailable",
        "concrete_reason": reason,
        "stats": {
            "fuzz_params": 0,
            "lifted": [],
            "rendered_width": {},
            "wide_fuzz_coords": [],
            "dynamic_fuzz_coords": [],
            "asserts": 0,
            "verifier_asserts": 0,
            "state_asserts": 0,
            "return_asserts": 0,
            "exit_kind_asserts": 0,
            "guarded_asserts": 0,
            "oracle_classes": [],
            "oracle_class_counts": {},
            "oracle_class_combinations": [],
            "oracle_class_combo_counts": {},
            "assertion_oracles": [],
        },
        "notes": [reason],
    }
    (wd / "put.json").write_text(json.dumps(rec, indent=2, sort_keys=True))
    return {
        "stage": "no-unit-deploy-fallback",
        "status": "refused",
        "reason": reason,
        "put_out_root": str(out_root),
    }


def emit_no_unit_deploy_fallback(subject: PreparedSubject, case_dir: Path,
                                 schedule: dict, forge_timeout: int,
                                 forge_runner=_run_forge_json,
                                 force: bool = False,
                                 reason: str | None = None,
                                 out_name: str = "deploy_only") -> dict:
    out_root = case_dir / "put" / out_name
    if not force and not _is_true_no_unit_schedule(schedule):
        return {
            "stage": "no-unit-deploy-fallback",
            "status": "skipped",
            "reason": "schedule is not a true no-unit target",
        }
    flat_sol = _existing_subject_flat_sol(subject)
    if flat_sol is None:
        return _write_no_unit_deploy_refusal(
            out_root,
            subject,
            "flat source unavailable; tried: " + ", ".join(
                str(path) for path in _subject_flat_sol_candidates(subject)))
    try:
        source = flat_sol.read_text(errors="replace")
    except OSError as exc:
        return _write_no_unit_deploy_refusal(
            out_root, subject, f"flat source unavailable: {exc}")
    test_source, refusal = _no_unit_deploy_test_source(subject, source)
    if refusal:
        return _write_no_unit_deploy_refusal(out_root, subject, refusal)

    start = time.monotonic()
    project = out_root / "Project"
    _prepare_deploy_only_project(project, subject, flat_sol)
    test_name = f"test_cov_{subject.contract}_deploy_only"
    test_file = project / "test" / f"{subject.contract}DeployOnlyCovTest.t.sol"
    test_file.write_text(test_source)
    status, timed_out, forge_wall_s, forge_output = forge_runner(
        project, test_name, forge_timeout)
    (out_root / "forge.log").write_text(forge_output)
    valid = status == "Success"
    wd = out_root / "_wd" / "deploy_only"
    wd.mkdir(parents=True, exist_ok=True)
    put_json = {
        "kind": "concrete",
        "stage2_source": "no_unit_deploy_fallback",
        "stage4_kind": "deploy-only",
        "contract": subject.contract,
        "unit": "__deploy__",
        "enc": 0,
        "depth": 0,
        "file": str(test_file),
        "test": test_name,
        "piece": None,
        "concrete_reason": (
            reason or
            "target contract has no public/external FunctionDefinition units; "
            "VeriPUT emitted a deploy-only concrete reference test"),
        "forge_status": status,
        "valid_reference_test": valid,
        "stats": {
            "fuzz_params": 0,
            "lifted": [],
            "rendered_width": {},
            "wide_fuzz_coords": [],
            "dynamic_fuzz_coords": [],
            "asserts": 0,
            "verifier_asserts": 0,
            "state_asserts": 0,
            "return_asserts": 0,
            "exit_kind_asserts": 0,
            "guarded_asserts": 0,
            "oracle_classes": [],
            "oracle_class_counts": {},
            "oracle_class_combinations": [],
            "oracle_class_combo_counts": {},
            "assertion_oracles": [],
        },
        "notes": [
            "deploy-only fallback is concrete, not a PUT, and carries no "
            "verifier-backed oracle beyond Foundry deployment success"],
    }
    (wd / "put.json").write_text(
        json.dumps(put_json, indent=2, sort_keys=True))
    wall_s = round(time.monotonic() - start, 3)
    row = {
        "kind": "concrete",
        "stage2_source": "no_unit_deploy_fallback",
        "stage4_kind": "deploy-only",
        "benchmark": subject.benchmark_key,
        "unit": "__deploy__",
        "enc": 0,
        "piece": None,
        "test": test_name,
        "file": str(test_file),
        "forge_status": status,
        "valid_reference_test": valid,
        "b": False,
        "oracle_classes": [],
        "oracle_class_counts": {},
        "oracle_class_combinations": [],
        "oracle_class_combo_counts": {},
    }
    summary = {
        "schema": "veriput-put-summary/1",
        "emission": {
            "puts_emitted": 0,
            "concrete_replays_emitted": 1,
        },
        "deliverable_b": {
            "valid_reference_tests": {
                "total": 1 if valid else 0,
                "put": 0,
                "concrete": 1 if valid else 0,
            },
            "rows": [row],
        },
        "timing": {
            "generation_wall_s": 0.0,
            "emission_wall_s": wall_s,
            "foundry_replay_wall_s": forge_wall_s,
            "total_wall_s": wall_s,
        },
        "no_unit_deploy_fallback": {
            "enabled": True,
            "forge_timed_out": timed_out,
        },
    }
    (out_root / "put-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True))
    return {
        "stage": "no-unit-deploy-fallback" if not force
                 else "final-deploy-concrete-fallback",
        "status": "ok" if valid else ("timeout" if timed_out else "no-output"),
        "forge_status": status,
        "forge_timed_out": timed_out,
        "wall_s": wall_s,
        "forge_wall_s": forge_wall_s,
        "put_out_root": str(out_root),
        "test_file": str(test_file),
    }


def adopt_existing_subject_results(result_root: Path,
                                   dataset_label: str,
                                   target_rows_: list[dict],
                                   journal: Path,
                                   done: dict[str, dict]) -> dict[str, dict]:
    adopted = []
    updated = {}
    normalized = []
    for key, current in done.items():
        if not isinstance(current, dict):
            continue
        row = _normalize_result_row(current)
        updated[key] = row
        if row != current:
            normalized.append(str(row.get("subject_id") or key))
    for target_row in target_rows_:
        subject_id = target_row["subject_id"]
        key = f"gen:veriput:{subject_id}"
        case_dir = result_root / dataset_label / "subjects" / subject_id
        if not (case_dir / "result.json").exists():
            safe_dir = result_root / dataset_label / "subjects" / _safe_name(subject_id)
            if safe_dir != case_dir:
                case_dir = safe_dir
        row = _load_subject_result_row(case_dir)
        if row is None:
            row = _artifact_summary_row(target_row, dataset_label, case_dir,
                                        updated.get(key))
            if row is None:
                continue
        else:
            row = _merge_put_summary_into_row(row, case_dir)
        stale_row = _best_stale_artifact_row(
            target_row, dataset_label, case_dir, row)
        row = _adopt_stale_artifacts(row, stale_row)
        row["key"] = key
        row["subject_id"] = subject_id
        normalized_case_result = _write_normalized_case_result(
            case_dir,
            row,
            reason=(
                "retained Stage-4 artifacts or normalized result row are "
                "stronger than canonical subject result.json"),
        )
        if normalized_case_result:
            row["normalized_subject_result_json"] = True
        current = updated.get(key)
        if (_row_strength(row) <= _row_strength(current)
                and not _row_needs_normalized_adoption(current, row)):
            continue
        row["adopted_existing_result_json"] = True
        row["adopted_at"] = _utc_now()
        updated[key] = row
        adopted.append(subject_id)
    if not adopted and not normalized:
        return updated
    journal.parent.mkdir(parents=True, exist_ok=True)
    ordered_keys = [f"gen:veriput:{row['subject_id']}" for row in target_rows_]
    remaining = [key for key in updated if key not in set(ordered_keys)]
    tmp = journal.with_name(
        f".{journal.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with tmp.open("w") as stream:
        for key in ordered_keys + sorted(remaining):
            if key in updated:
                stream.write(json.dumps(updated[key], sort_keys=True) + "\n")
    os.replace(tmp, journal)
    if adopted:
        print("[rq1] adopted stronger existing subject result(s): "
              + ", ".join(adopted),
              flush=True)
    if normalized:
        print("[rq1] normalized existing journal row(s): "
              + ", ".join(normalized),
              flush=True)
    return updated


def target_rows(veriput_root: Path, benchmark: str, subject_ids: list[str],
                limit: int, order: str = "fast-first") -> tuple[str, list[dict]]:
    if benchmark not in TARGET_BENCHMARK_ARG:
        raise RQ1RunError(
            "--benchmark must be one of: " + ", ".join(sorted(TARGET_BENCHMARK_ARG)))
    target_arg = TARGET_BENCHMARK_ARG[benchmark]
    doc = target_manifest.build_manifest(veriput_root, [target_arg], "include")
    all_rows = list(doc.get("targets") or [])
    rows = [row for row in all_rows if row.get("status") == "ok"]
    if subject_ids:
        wanted = set(subject_ids)
        ok_by_id = {row.get("subject_id"): row for row in rows}
        all_by_id = {row.get("subject_id"): row for row in all_rows}
        rows = []
        for subject_id in subject_ids:
            row = ok_by_id.get(subject_id)
            if row is not None:
                rows.append(row)
                continue
            candidate = all_by_id.get(subject_id)
            if candidate is None:
                continue
            try:
                prepared = resolve_subject(
                    subject_id,
                    benchmark=candidate.get("benchmark") or target_arg,
                    require_unit=False)
            except SubjectError:
                continue
            recovered = dict(candidate)
            recovered.update({
                "status": "ok",
                "prepared_subject_fallback": True,
                "prepared_subject_root": prepared.root,
                "prepared_subject_status_original": candidate.get("status"),
                "prepared_subject_reason_original": candidate.get("reason"),
            })
            rows.append(recovered)
    if order == "fast-first":
        rows = sorted(rows, key=lambda row: _target_cost_key(veriput_root, row))
    elif order != "dataset":
        raise RQ1RunError("--order must be dataset or fast-first")
    if limit:
        rows = rows[:limit]
    return DATASET_LABEL[benchmark], rows


def _target_cost_key(veriput_root: Path, row: dict) -> tuple[int, int, str]:
    bench = row.get("benchmark")
    subject_id = row.get("subject_id") or ""
    dirname = PREPARED_DATASET_DIR.get(bench)
    size = 1 << 60
    if dirname and subject_id:
        candidates = [
            veriput_root / "Results" / dirname / "subjects" / subject_id / "flat.sol",
        ]
        if bench in ("bugfix124", "peer182"):
            candidates.append(
                veriput_root / "scripts" / "Results" / "workdirs"
                / dirname / "subjects" / subject_id / "flat.sol")
        for flat in candidates:
            try:
                size = flat.stat().st_size
                break
            except OSError:
                continue
    hints = len(row.get("units_hint") or [])
    # Hinted target rows tend to be narrower, but flat size dominates.
    return (size, -hints, subject_id)


def cached_subject(subject: PreparedSubject, ast_cache_root: Path,
                   dataset_label: str) -> PreparedSubject:
    ast_name = Path(subject.solast).name
    # certify_all.py re-applies --ast-cache-root using the prepared subject's
    # own benchmark key.  The cache must use that same namespace; the RQ1
    # dataset label (`real203`) is only an output label.
    _ = dataset_label
    cached = ast_cache_root / subject.benchmark / subject.benchmark_key / ast_name
    return subject.with_solast_path(str(cached.resolve()), source="rq1-cache")


def _unit_hints(row: dict, units: list[str]) -> dict:
    hints = list(row.get("units_hint") or [])
    unit_set = set(units)
    return {
        "source": "target-manifest.units_hint",
        "hinted_units": [name for name in hints if name in unit_set],
        "missing_unit_hints": [name for name in hints if name not in unit_set],
        "pending_unit_hints": [],
    }


def build_subject_schedule(subject: PreparedSubject, target_row: dict,
                           ast_cache_root: Path, case_dir: Path, *,
                           timeout_s: int, run_timeout_s: int,
                           memlimit_gib: int) -> dict:
    row = subject_unit_manifest.manifest_for_subject(
        subject,
        generate_ast=True,
        ast_timeout_s=60.0)
    if row.get("status") == "ok":
        units = (row.get("units") or {}).get("units") or []
        row["target"] = target_row
        row["unit_hints"] = _unit_hints(target_row, units)
    manifest = {
        "schema": "veriput-unit-manifest/v1",
        "generated_at": _utc_now(),
        "benchmark": subject.benchmark,
        "ast_cache_root": str(ast_cache_root),
        "summary": {
            "subjects": 1,
            "ok": 1 if row.get("status") == "ok" else 0,
            "missing_ast": 1 if row.get("status") == "missing-ast" else 0,
            "error": 1 if row.get("status") == "error" else 0,
            "units": len((row.get("units") or {}).get("units") or []),
        },
        "subjects": [row],
    }
    cert_out = str((case_dir / "cert" / "certify-results.jsonl").resolve())
    return unit_schedule.build_schedule(
        manifest,
        selection_strategy="priority",
        cert_out=cert_out,
        timeout_s=timeout_s,
        run_timeout_s=run_timeout_s,
        memlimit_gib=memlimit_gib,
        workdir=str((case_dir / "cert" / "work").resolve()))


def filter_schedule_units(schedule: dict, units: list[str]) -> dict:
    if not units:
        return schedule
    wanted = set(units)
    filtered = dict(schedule)
    jobs = [job for job in (schedule.get("jobs") or [])
            if job.get("unit") in wanted]
    filtered["jobs"] = jobs
    summary = dict(schedule.get("summary") or {})
    summary.update({
        "jobs_before_unit_filter": len(schedule.get("jobs") or []),
        "jobs": len(jobs),
        "unit_filter": sorted(wanted),
        "unit_filter_missing": sorted(wanted - {job.get("unit") for job in jobs}),
    })
    filtered["summary"] = summary
    filtered["unit_filter"] = sorted(wanted)
    return filtered


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _maxrss_mb() -> float:
    # Linux reports ru_maxrss in KiB.
    return round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024.0, 1)


def _proc_children(pid: int) -> list[int]:
    try:
        text = Path(f"/proc/{pid}/task/{pid}/children").read_text()
    except OSError:
        return []
    out = []
    for item in text.split():
        try:
            out.append(int(item))
        except ValueError:
            pass
    return out


def _proc_tree(pid: int) -> list[int]:
    seen = set()
    stack = [pid]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(_proc_children(current))
    return sorted(seen)


def _rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text(errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                return int(parts[1]) if len(parts) >= 2 else 0
    except OSError:
        return 0
    return 0


def _rss_tree_mb(pid: int) -> float:
    return round(sum(_rss_kb(child) for child in _proc_tree(pid)) / 1024.0, 1)


def _kill_process_tree(pid: int, sig: int) -> None:
    """Kill a wrapper and nested independent sessions before they detach."""
    pids = _proc_tree(pid)
    groups = set()
    for child in pids:
        try:
            groups.add(os.getpgid(child))
        except (OSError, ProcessLookupError):
            pass
    for pgid in groups:
        try:
            os.killpg(pgid, sig)
        except (OSError, ProcessLookupError):
            pass
    for child in pids:
        try:
            os.kill(child, sig)
        except (OSError, ProcessLookupError):
            pass


def _tail_file(path: Path, limit: int = 4000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > limit:
        data = data[-limit:]
    return data.decode("utf-8", errors="replace")


def _looks_oom(rc: int | None, text: str) -> bool:
    if rc in (-9, 137, -6, 134):
        return True
    lowered = text.lower()
    return any(token in lowered for token in (
        "std::bad_alloc",
        "bad_alloc",
        "out of memory",
        "cannot allocate memory",
        "memory exhausted",
        "enomem",
    ))


def run_command(argv: list[str], timeout_s: float, log_prefix: Path) -> dict:
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    stdout_path = log_prefix.with_suffix(".stdout.log")
    stderr_path = log_prefix.with_suffix(".stderr.log")
    timed_out = False
    maxrss_proc_mb = 0.0
    try:
        with stdout_path.open("w") as stdout_stream, stderr_path.open("w") as stderr_stream:
            proc = subprocess.Popen(argv,
                                    stdout=stdout_stream,
                                    stderr=stderr_stream,
                                    text=True,
                                    start_new_session=True)
            deadline = start + max(1.0, timeout_s)
            while proc.poll() is None:
                maxrss_proc_mb = max(maxrss_proc_mb, _rss_tree_mb(proc.pid))
                if time.monotonic() > deadline:
                    timed_out = True
                    _kill_process_tree(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        _kill_process_tree(proc.pid, signal.SIGKILL)
                        proc.wait()
                    break
                time.sleep(0.5)
            maxrss_proc_mb = max(maxrss_proc_mb, _rss_tree_mb(proc.pid))
            rc = proc.returncode
    except OSError as exc:
        rc = None
        stdout_path.write_text("")
        stderr_path.write_text(f"could not start: {exc}")
    wall_s = round(time.monotonic() - start, 3)
    stdout_tail = _tail_file(stdout_path)
    stderr_tail = _tail_file(stderr_path)
    combined = stdout_tail + "\n" + stderr_tail
    status = "timeout" if timed_out else ("ok" if rc == 0 else "error")
    if status == "error" and _looks_oom(rc, combined):
        status = "oom"
    return {
        "argv": argv,
        "rc": rc,
        "status": status,
        "timed_out": timed_out,
        "wall_s": wall_s,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
        "maxrss_proc_mb": maxrss_proc_mb,
        "maxrss_mb_after": _maxrss_mb(),
    }


def _cert_row_matches(row: dict, benchmark_key: str, unit: str,
                      path_function: str | None = None) -> bool:
    if row.get("unit") != unit:
        return False
    if (row.get("benchmark") or row.get("poc")) != benchmark_key:
        return False
    if path_function and not _same_path_function(
            _row_path_function(row), path_function):
        return False
    return True


def _row_path_function(row: dict) -> str | None:
    value = row.get("path_function")
    if isinstance(value, str) and value:
        return value
    progress = row.get("generalise_progress") or {}
    if not isinstance(progress, dict):
        return None
    history = progress.get("history") or []
    if not isinstance(history, list):
        return None
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        value = entry.get("path_function")
        if isinstance(value, str) and value:
            return value
    return None


def _path_function_declaration_id(path_function: str | None) -> str | None:
    if not path_function:
        return None
    match = re.search(r"#(\d+)$", str(path_function))
    return match.group(1) if match else None


def _same_path_function(actual: str | None, expected: str | None) -> bool:
    if not expected:
        return True
    if actual == expected:
        return True
    actual_id = _path_function_declaration_id(actual)
    expected_id = _path_function_declaration_id(expected)
    return actual_id is not None and actual_id == expected_id


def _certified_count(cert_path: Path, benchmark_key: str, unit: str,
                     path_function: str | None = None) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("bucket") != "CERTIFIED":
            continue
        if not _cert_row_matches(row, benchmark_key, unit, path_function):
            continue
        count += len(row.get("certified") or {})
    return count


def _claim_path_id_int(raw) -> int | None:
    if raw is None:
        return None
    match = re.match(r"^(\d+)(?:#.*)?$", str(raw))
    if not match:
        return None
    return int(match.group(1))


def _occupied_stage2_path_ids(row: dict) -> set[int]:
    occupied = set()
    for mapping_key in ("certified", "not_certified"):
        for enc in (row.get(mapping_key) or {}):
            parsed = _claim_path_id_int(enc)
            if parsed is not None:
                occupied.add(parsed)
    for details_key in ("certified_details", "not_certified_details"):
        details = row.get(details_key) or {}
        if isinstance(details, dict):
            items = details.items()
        elif isinstance(details, list):
            items = enumerate(details)
        else:
            continue
        for key, detail in items:
            if isinstance(detail, dict) and detail.get("enc") is not None:
                parsed = _claim_path_id_int(detail.get("enc"))
            else:
                parsed = _claim_path_id_int(key)
            if parsed is not None:
                occupied.add(parsed)
    return occupied


def _cleared_concrete_fallback_count(cert_path: Path, benchmark_key: str,
                                     unit: str,
                                     path_function: str | None = None) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _cert_row_matches(row, benchmark_key, unit, path_function):
            continue
        not_certified = row.get("not_certified") or {}
        details = row.get("not_certified_details") or {}
        if isinstance(details, list):
            detail_rows = {str(d.get("enc")): d for d in details
                           if isinstance(d, dict)}
        elif isinstance(details, dict):
            detail_rows = {str(k): v for k, v in details.items()
                           if isinstance(v, dict)}
        else:
            detail_rows = {}
        for enc in not_certified:
            detail = detail_rows.get(str(enc)) or {}
            if (detail.get("concrete_fallback") is True
                    and detail.get("witness_check")
                    in CONCRETE_FALLBACK_WITNESS_CHECKS):
                count += 1
    return count


def _timeout_concrete_fallback_count(cert_path: Path, benchmark_key: str,
                                     unit: str,
                                     path_function: str | None = None) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _cert_row_matches(row, benchmark_key, unit, path_function):
            continue
        if not _cert_row_timed_out(row):
            continue
        occupied = _occupied_stage2_path_ids(row)
        journal = row.get("partial_witness_journal") or {}
        if not isinstance(journal, dict):
            continue
        try:
            witness_count = int(journal.get("witness_count") or 0)
        except (TypeError, ValueError):
            witness_count = 0
        if witness_count <= 0:
            continue
        for path in journal.get("paths") or []:
            if not isinstance(path, dict):
                continue
            enc = _claim_path_id_int(path.get("path_id"))
            if enc is None or not path.get("path_function"):
                continue
            if enc in occupied:
                continue
            try:
                path_witnesses = int(path.get("witness_count") or 0)
            except (TypeError, ValueError):
                path_witnesses = 0
            if path_witnesses > 0:
                count += 1
    return count


def _partial_journal_concrete_fallback_count(cert_path: Path,
                                             benchmark_key: str,
                                             unit: str,
                                             path_function: str | None = None) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _cert_row_matches(row, benchmark_key, unit, path_function):
            continue
        if _cert_row_timed_out(row):
            continue
        bucket = str(row.get("bucket") or "").upper()
        journal = row.get("partial_witness_journal") or {}
        if not isinstance(journal, dict):
            continue
        if journal.get("complete") is True and bucket in (
                "NO-COORDINATE", "NO-WITNESS-UNKNOWN", "CERTIFIED"):
            continue
        try:
            witness_count = int(journal.get("witness_count") or 0)
        except (TypeError, ValueError):
            witness_count = 0
        if witness_count <= 0:
            continue
        if journal.get("partial") is not True:
            source_stage = str(journal.get("source_stage") or "")
            diagnostic = row.get("driver_diagnostic") or {}
            diagnostic_tag = (
                diagnostic.get("tag") if isinstance(diagnostic, dict) else None)
            if (source_stage != "partial-witness-journal"
                    and diagnostic_tag
                    != "path-coverage-partial-journal-no-report"):
                continue
        occupied = _occupied_stage2_path_ids(row)
        for path in journal.get("paths") or []:
            if not isinstance(path, dict):
                continue
            enc = _claim_path_id_int(path.get("path_id"))
            if enc is None or not path.get("path_function"):
                continue
            if enc in occupied:
                continue
            try:
                path_witnesses = int(path.get("witness_count") or 0)
            except (TypeError, ValueError):
                path_witnesses = 0
            if path_witnesses > 0:
                count += 1
    return count


def _complete_witness_concrete_fallback_count(cert_path: Path,
                                              benchmark_key: str,
                                              unit: str,
                                              path_function: str | None = None) -> int:
    if not cert_path.exists():
        return 0
    count = 0
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _cert_row_matches(row, benchmark_key, unit, path_function):
            continue
        occupied = _occupied_stage2_path_ids(row)
        journal = row.get("partial_witness_journal") or {}
        if not isinstance(journal, dict) or journal.get("complete") is not True:
            continue
        bucket = str(row.get("bucket") or "").upper()
        if bucket not in ("NO-COORDINATE", "NO-WITNESS-UNKNOWN", "CERTIFIED"):
            continue
        if bucket == "CERTIFIED" and journal.get(
                "source_stage") != "certified-no-coordinate":
            continue
        for path in journal.get("paths") or []:
            if not isinstance(path, dict):
                continue
            enc = _claim_path_id_int(path.get("path_id"))
            if enc is None or not path.get("path_function"):
                continue
            if enc in occupied:
                continue
            try:
                path_witnesses = int(path.get("witness_count") or 0)
            except (TypeError, ValueError):
                path_witnesses = 0
            if path_witnesses > 0:
                count += 1
    return count


def summarize_certification(cert_path: Path) -> dict:
    summary = {
        "rows": 0,
        "bucket_counts": {},
        "exit_counts": {},
        "witness_counts": {},
        "certified_regions": 0,
        "not_certified_regions": 0,
        "timed_out_units": [],
        "oom_units": [],
        "driver_refusal_tags": {},
        "driver_diagnostic_tags": {},
    }
    if not cert_path.exists():
        return summary
    buckets = Counter()
    exits = Counter()
    witnesses = Counter()
    refusals = Counter()
    diagnostics = Counter()
    timed_out_units = []
    oom_units = []
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        summary["rows"] += 1
        bucket = str(row.get("bucket") or "<missing>")
        buckets[bucket] += 1
        exit_code = row.get("exit")
        if exit_code is not None:
            exits[str(exit_code)] += 1
        witnessed = row.get("witnessed")
        if witnessed is None:
            witnesses["unknown"] += 1
        elif witnessed:
            witnesses["true"] += 1
        else:
            witnesses["false"] += 1
        summary["certified_regions"] += len(row.get("certified") or {})
        summary["not_certified_regions"] += len(row.get("not_certified") or {})
        unit = row.get("unit") or "<unknown>"
        if _cert_row_timed_out(row):
            timed_out_units.append(unit)
        if exit_code in (-9, 137) or str(bucket).upper() == "OOM":
            oom_units.append(unit)
        refusal = row.get("driver_refusal_tag")
        if refusal:
            refusals[str(refusal)] += 1
        diagnostic = row.get("driver_diagnostic") or {}
        if isinstance(diagnostic, dict):
            tag = diagnostic.get("tag")
            if tag:
                diagnostics[str(tag)] += 1
    summary["bucket_counts"] = dict(sorted(buckets.items()))
    summary["exit_counts"] = dict(sorted(exits.items()))
    summary["witness_counts"] = dict(sorted(witnesses.items()))
    summary["timed_out_units"] = sorted(set(timed_out_units))
    summary["oom_units"] = sorted(set(oom_units))
    summary["driver_refusal_tags"] = dict(sorted(refusals.items()))
    summary["driver_diagnostic_tags"] = dict(sorted(diagnostics.items()))
    return summary


def _cert_row_timed_out(row: dict) -> bool:
    if row.get("exit") == 124 or str(row.get("bucket") or "").upper() == "TIMEOUT":
        return True
    diagnostic = row.get("driver_diagnostic") or {}
    progress = row.get("generalise_progress") or {}
    run_timeout = row.get("run_timeout_s") or progress.get("timeout_s")
    try:
        run_timeout = float(run_timeout)
        wall_s = float(row.get("wall_s") or 0)
    except (TypeError, ValueError):
        return False
    if run_timeout <= 0 or wall_s < max(1.0, run_timeout * 0.9):
        return False
    no_report = (
        diagnostic.get("tag") == "esbmc-no-cov-report"
        or diagnostic.get("category") == "no-cov-report")
    return (
        str(row.get("bucket") or "").upper() == "KILLED"
        and row.get("witnessed") is None
        and no_report)


def _no_output_reason(cert_summary: dict) -> str:
    if cert_summary.get("timed_out_units"):
        units = ", ".join(cert_summary["timed_out_units"][:4])
        suffix = "" if len(cert_summary["timed_out_units"]) <= 4 else ", ..."
        return f"certification timed out before PUT artifacts: {units}{suffix}"
    if cert_summary.get("oom_units"):
        units = ", ".join(cert_summary["oom_units"][:4])
        suffix = "" if len(cert_summary["oom_units"]) <= 4 else ", ..."
        return f"certification OOM before PUT artifacts: {units}{suffix}"
    if cert_summary.get("rows") and not cert_summary.get("certified_regions"):
        diagnostics = cert_summary.get("driver_diagnostic_tags") or {}
        if diagnostics:
            detail = ", ".join(
                f"{key}={value}" for key, value in diagnostics.items())
            return f"no certified regions: diagnostics {detail}"
        buckets = cert_summary.get("bucket_counts") or {}
        if buckets:
            detail = ", ".join(f"{key}={value}" for key, value in buckets.items())
            return f"no certified regions: {detail}"
        return "no certified regions"
    return "no PUT or concrete replay emitted"


def _relocated_stage4_file(path: object, put_root: Path) -> str | None:
    if not path:
        return None
    text = str(path)
    p = Path(text)
    if p.exists():
        return text
    roots = (
        Path("/home/samson/workspace/VeriPUT"),
        Path("/home/administrator/VeriPUT"),
    )
    for root in roots:
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        moved = DEFAULT_VERIPUT_ROOT / rel
        if moved.exists():
            return str(moved)
    name = p.name
    if not name:
        return text
    matches = [
        candidate for candidate in put_root.rglob(name)
        if candidate.is_file()
    ]
    if len(matches) == 1:
        return str(matches[0])
    if matches:
        put_root_s = str(put_root)
        matches.sort(key=lambda candidate: (
            0 if str(candidate).startswith(put_root_s) else 1,
            len(str(candidate)),
            str(candidate),
        ))
        return str(matches[0])
    return text


def _load_put_jsons(put_root: Path) -> list[dict]:
    out = []
    for path in sorted(put_root.rglob("put.json")):
        try:
            rec = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        relocated = _relocated_stage4_file(rec.get("file"), put_root)
        if relocated:
            rec["file"] = relocated
        rec["_put_json_path"] = str(path)
        out.append(rec)
    return out


def _row_is_no_oracle_put(row: dict, rec: dict) -> bool:
    if row.get("kind") != "put":
        return False
    stats = rec.get("stats") or {}
    if "asserts" in stats or "guarded_asserts" in stats:
        asserts = int(stats.get("asserts") or 0)
        guarded = int(stats.get("guarded_asserts") or 0)
        return asserts - guarded <= 0
    gates = row.get("gates") or {}
    return gates.get("assert") is False


def _row_is_disabled_concrete(row: dict) -> bool:
    if row.get("kind") != "concrete":
        return False
    test = row.get("test")
    file_name = row.get("file")
    if not test or not file_name:
        return False
    try:
        text = Path(str(file_name)).read_text(errors="replace")
    except OSError:
        return False
    enabled_rx = re.compile(r"\bfunction\s+" + re.escape(str(test)) + r"\s*\(")
    disabled_rx = re.compile(r"\bfunction\s+disabled_"
                             + re.escape(str(test)) + r"\s*\(")
    return enabled_rx.search(text) is None and disabled_rx.search(text) is not None


def _row_is_unsupported_concrete(row: dict) -> bool:
    if row.get("kind") != "concrete":
        return False
    file_name = row.get("file")
    if not file_name:
        return False
    try:
        text = Path(str(file_name)).read_text(errors="replace")
    except OSError:
        return False
    test_name = row.get("test")
    if test_name:
        body = _solidity_function_body(text, str(test_name))
        if body is not None:
            return "UNSUPPORTED:" in body
    if row.get("forge_status") == "Success" or row.get("valid_reference_test"):
        return False
    return "UNSUPPORTED:" in text


def _solidity_function_body(source: str, name: str) -> str | None:
    match = re.search(
        r"\bfunction\s+" + re.escape(name) + r"\s*\([^)]*\)[^{;]*\{",
        source)
    if not match:
        return None
    depth = 1
    i = match.end()
    while i < len(source):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[match.end():i]
        i += 1
    return None


def _has_oracle_class(test: dict, *labels: str) -> bool:
    present = {str(label) for label in (test.get("oracle_classes") or [])}
    return any(label in present for label in labels)


def _rq1_oracle_tags(kind: str | None, oracle_classes: list[str]) -> list[str]:
    tags = {str(label) for label in oracle_classes if str(label) in ("R1", "R2")}
    if kind == "concrete" or not tags:
        tags.add("R0")
    return sorted(tags)


def _oracle_class_counts_from_stats(stats: dict) -> tuple[list[str], dict, list[str], dict]:
    labels = Counter()
    combos = Counter()
    if not isinstance(stats, dict):
        stats = {}
    for detail in stats.get("assertion_oracles") or []:
        classes = tuple(str(item) for item in (detail.get("classes") or []))
        if not classes:
            continue
        for label in classes:
            labels[label] += 1
        combos["+".join(classes)] += 1
    for label in stats.get("oracle_classes") or []:
        labels[str(label)] += 0
    for combo in stats.get("oracle_class_combinations") or []:
        combos[str(combo)] += 0
    return (
        sorted(labels),
        dict(sorted(labels.items())),
        sorted(combos),
        dict(sorted(combos.items())),
    )


def _merge_oracle_metadata(*sources: dict) -> tuple[list[str], dict, list[str], dict, list[dict]]:
    labels = Counter()
    combos = Counter()
    details: list[dict] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_details = source.get("assertion_oracles") or []
        detail_labels = Counter()
        detail_combos = Counter()
        for detail in source_details:
            if not isinstance(detail, dict):
                continue
            details.append(detail)
            classes = tuple(str(item) for item in (detail.get("classes") or []))
            if not classes:
                continue
            for label in classes:
                detail_labels[label] += 1
            detail_combos["+".join(classes)] += 1
        labels.update(detail_labels)
        combos.update(detail_combos)
        for label in source.get("oracle_classes") or source.get(
                "oracle_tags") or []:
            labels[str(label)] += 0
        class_counts = source.get("oracle_class_counts") or {}
        if isinstance(class_counts, dict):
            # `oracle_class_counts` is often the aggregate form of
            # `assertion_oracles` from the same source.  Use it only to fill
            # labels that were not already counted from the detailed oracle
            # records, otherwise R1/R2 metadata is double counted in summaries.
            for label, count in class_counts.items():
                label_s = str(label)
                try:
                    count_i = int(count)
                except (TypeError, ValueError):
                    count_i = 0
                if detail_labels.get(label_s):
                    labels[label_s] += 0
                else:
                    labels[label_s] += count_i
        for combo in source.get("oracle_class_combinations") or []:
            combos[str(combo)] += 0
        combo_counts = source.get("oracle_class_combo_counts") or {}
        if isinstance(combo_counts, dict):
            for combo, count in combo_counts.items():
                combo_s = str(combo)
                try:
                    count_i = int(count)
                except (TypeError, ValueError):
                    count_i = 0
                if detail_combos.get(combo_s):
                    combos[combo_s] += 0
                else:
                    combos[combo_s] += count_i
    return (
        sorted(labels),
        dict(sorted(labels.items())),
        sorted(combos),
        dict(sorted(combos.items())),
        details,
    )


def _is_valid_reference_test(row: dict) -> bool:
    # RQ1 uses the Foundry replay as a second oracle after verifier
    # certification.  Missing validity is unknown, not valid.
    return row.get("valid_reference_test") is True


def _put_json_artifact_row(rec: dict) -> dict:
    """Recover a raw artifact row from put.json when put-summary rows are absent."""

    return {
        "kind": rec.get("kind"),
        "stage4_kind": rec.get("stage4_kind"),
        "stage2_source": rec.get("stage2_source"),
        "stage2_witness_check": rec.get("stage2_witness_check"),
        "unit": rec.get("unit"),
        "enc": rec.get("enc"),
        "piece": rec.get("piece"),
        "test": rec.get("test"),
        "file": rec.get("file"),
        "forge_status": rec.get("forge_status"),
        "valid_reference_test": rec.get("valid_reference_test"),
        "b": rec.get("b"),
        "concrete_reason": rec.get("concrete_reason"),
        "oracle_classes": rec.get("oracle_classes"),
        "oracle_class_counts": rec.get("oracle_class_counts"),
        "oracle_class_combinations": rec.get("oracle_class_combinations"),
        "oracle_class_combo_counts": rec.get("oracle_class_combo_counts"),
        "assertion_oracles": (
            rec.get("assertion_oracles")
            or (rec.get("stats") or {}).get("assertion_oracles")),
        "_from_put_json_only": True,
    }


def _row_count(row: dict, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _legacy_quality_bucket(row: dict) -> str:
    valid = _row_count(row, "valid")
    if row.get("valid") is None:
        valid = (_row_count(row, "put_valid")
                 + _row_count(row, "concrete_valid"))
        if valid <= 0:
            valid = len(row.get("valid_tests") or [])
    put_valid = _row_count(row, "put_valid")
    if valid <= 0:
        return "no-valid"
    if put_valid <= 0:
        return "valid-no-PUT"
    valid_puts = [
        test for test in (row.get("valid_tests") or [])
        if test.get("kind") == "put"
        and _is_valid_reference_test(test)
    ]
    if valid_puts:
        if any(_has_oracle_class(test, "R1", "R2") for test in valid_puts):
            return "valid-PUT-with-R1R2"
        return "valid-PUT-no-R1R2"
    if (_row_count(row, "valid_put_with_R1_or_R2") > 0
            or _row_count(row, "valid_put_with_R1") > 0
            or _row_count(row, "valid_put_with_R2") > 0):
        return "valid-PUT-with-R1R2"
    return "valid-PUT-no-R1R2"


def _strength_quality(put_summary: dict) -> dict:
    valid_tests = [
        test for test in (put_summary.get("valid_tests") or [])
        if _is_valid_reference_test(test)
    ]
    valid_puts = [test for test in valid_tests if test.get("kind") == "put"]
    valid_puts_with_r1 = [
        test for test in valid_puts if _has_oracle_class(test, "R1")
    ]
    valid_puts_with_r2 = [
        test for test in valid_puts if _has_oracle_class(test, "R2")
    ]
    valid_puts_with_r1r2 = [
        test for test in valid_puts if _has_oracle_class(test, "R1", "R2")
    ]
    if not valid_tests:
        bucket = "no-valid"
    elif not valid_puts:
        bucket = "valid-no-PUT"
    elif not valid_puts_with_r1r2:
        bucket = "valid-PUT-no-R1R2"
    else:
        bucket = "valid-PUT-with-R1R2"
    return {
        "quality_bucket": bucket,
        "valid_put_with_R1": len(valid_puts_with_r1),
        "valid_put_with_R2": len(valid_puts_with_r2),
        "valid_put_with_R1_or_R2": len(valid_puts_with_r1r2),
        "valid_put_without_R1R2": (
            len(valid_puts) - len(valid_puts_with_r1r2)),
        "valid_concrete": sum(
            1 for test in valid_tests if test.get("kind") == "concrete"),
    }


def summarize_put_artifacts(put_root: Path) -> dict:
    emission = Counter()
    valid = Counter()
    timing = Counter()
    rows = []
    summary_paths = []
    for path in sorted(put_root.rglob("put-summary.json")):
        try:
            doc = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        summary_paths.append(str(path))
        em = doc.get("emission") or {}
        b = doc.get("deliverable_b") or {}
        v = b.get("valid_reference_tests") or {}
        emission["put"] += int(em.get("puts_emitted") or 0)
        emission["concrete"] += int(em.get("concrete_replays_emitted") or 0)
        valid["put"] += int(v.get("put") or 0)
        valid["concrete"] += int(v.get("concrete") or 0)
        tm = doc.get("timing") or {}
        generation_wall_s = tm.get("generation_wall_s")
        if generation_wall_s is None:
            generation_wall_s = tm.get("emission_wall_s")
        timing["stage4_generation_wall_s"] += float(generation_wall_s or 0.0)
        timing["stage4_emission_wall_s"] += float(
            tm.get("emission_wall_s") or 0.0)
        timing["foundry_replay_wall_s"] += float(
            tm.get("foundry_replay_wall_s") or 0.0)
        timing["put_all_wall_s"] += float(tm.get("total_wall_s") or 0.0)
        for row in b.get("rows") or []:
            if isinstance(row, dict):
                row = dict(row)
                relocated = _relocated_stage4_file(row.get("file"), put_root)
                if relocated:
                    row["file"] = relocated
            rows.append(row)

    put_jsons = _load_put_jsons(put_root)
    by_file_test = {}
    by_test_candidates = {}
    for rec in put_jsons:
        test = rec.get("test")
        file_name = rec.get("file")
        if test and file_name:
            by_file_test[(str(file_name), str(test))] = rec
        if test:
            by_test_candidates.setdefault(str(test), []).append(rec)
    by_unique_test = {
        test: rows[0] for test, rows in by_test_candidates.items()
        if len(rows) == 1
    }
    row_keys = {
        (str(row.get("file") or ""), str(row.get("test") or ""))
        for row in rows
        if row.get("kind") in ("put", "concrete")
    }
    for rec in put_jsons:
        if rec.get("kind") not in ("put", "concrete"):
            continue
        key = (str(rec.get("file") or ""), str(rec.get("test") or ""))
        if key in row_keys:
            continue
        if not rec.get("file") or not rec.get("test"):
            continue
        rows.append(_put_json_artifact_row(rec))
        row_keys.add(key)

    raw_tests = []
    valid_tests = []
    deliverable_raw = Counter()
    deliverable_valid = Counter()
    deliverable_tests = set()
    for row in rows:
        if row.get("kind") not in ("put", "concrete"):
            continue
        test_name = row.get("test")
        file_name = row.get("file")
        rec = by_file_test.get((str(file_name), str(test_name)), {})
        if not rec and not file_name:
            rec = by_unique_test.get(str(test_name), {})
        if (row.get("refused") or _row_is_no_oracle_put(row, rec)
                or _row_is_disabled_concrete(row)
                or _row_is_unsupported_concrete(row)):
            continue
        stats = rec.get("stats") or {}
        oracle_classes, oracle_class_counts, oracle_class_combinations, \
            oracle_class_combo_counts, assertion_details = (
                _merge_oracle_metadata(row, rec, stats))
        entry = {
            "kind": row.get("kind"),
            "stage4_kind": (
                row.get("stage4_kind") or rec.get("stage4_kind")),
            "stage2_source": (
                row.get("stage2_source") or rec.get("stage2_source")),
            "stage2_witness_check": (
                row.get("stage2_witness_check")
                or rec.get("stage2_witness_check")),
            "unit": row.get("unit"),
            "enc": row.get("enc"),
            "piece": row.get("piece"),
            "test": row.get("test"),
            "file": row.get("file"),
            "forge_status": row.get("forge_status"),
            "valid_reference_test": _is_valid_reference_test(row),
            "b": bool(row.get("b")),
            "concrete_reason": (
                row.get("concrete_reason") or rec.get("concrete_reason")),
            "oracle_classes": oracle_classes,
            "oracle_class_counts": oracle_class_counts,
            "oracle_class_combinations": oracle_class_combinations,
            "oracle_class_combo_counts": oracle_class_combo_counts,
            "assertion_oracles": assertion_details,
            "r2_requested": rec.get("r2_requested"),
            "r2_depth": rec.get("r2_depth"),
            "r2_term_budget": rec.get("r2_term_budget"),
            "r2_candidate_budget": rec.get("r2_candidate_budget"),
            "r2_fuzz_prefilter": rec.get("r2_fuzz_prefilter"),
            "slot_candidates": rec.get("slot_candidates"),
            "put_json": rec.get("_put_json_path"),
        }
        entry["oracle_tags"] = _rq1_oracle_tags(
            entry["kind"], entry["oracle_classes"])
        entry["oracle_combo_tag"] = "+".join(entry["oracle_tags"])
        entry["is_put"] = entry["kind"] == "put"
        entry["is_concrete"] = entry["kind"] == "concrete"
        raw_tests.append(entry)
        if entry["kind"]:
            deliverable_raw[entry["kind"]] += 1
        if entry["test"]:
            deliverable_tests.add(entry["test"])
        if entry["valid_reference_test"]:
            valid_tests.append(entry)
            if entry["kind"]:
                deliverable_valid[entry["kind"]] += 1

    if rows:
        emission = deliverable_raw
        valid = deliverable_valid

    oracle_label_counts = Counter()
    oracle_combo_counts = Counter()
    raw_oracle_tags = Counter()
    valid_oracle_tags = Counter()
    raw_oracle_combos = Counter()
    valid_oracle_combos = Counter()
    storage_layout_counts = Counter()
    assertion_oracles = []
    for entry in raw_tests:
        for tag in entry.get("oracle_tags") or []:
            raw_oracle_tags[tag] += 1
        if entry.get("oracle_combo_tag"):
            raw_oracle_combos[entry["oracle_combo_tag"]] += 1
    for entry in valid_tests:
        for tag in entry.get("oracle_tags") or []:
            valid_oracle_tags[tag] += 1
        if entry.get("oracle_combo_tag"):
            valid_oracle_combos[entry["oracle_combo_tag"]] += 1
    for rec in put_jsons:
        if rec.get("storage_layout_available") is True:
            storage_layout_counts["available"] += 1
        elif rec.get("storage_layout_available") is False:
            storage_layout_counts["unavailable"] += 1
            if rec.get("file"):
                storage_layout_counts["unavailable_with_artifact"] += 1
            if rec.get("refused"):
                storage_layout_counts["unavailable_refused"] += 1
        test = rec.get("test")
        if rows and test not in deliverable_tests:
            continue
        details = rec.get("stats", {}).get("assertion_oracles") or []
        for detail in details:
            classes = tuple(detail.get("classes") or [])
            if not classes:
                continue
            for label in classes:
                oracle_label_counts[label] += 1
            oracle_combo_counts["+".join(classes)] += 1
            enriched = dict(detail)
            enriched["test"] = test
            enriched["put_json"] = rec.get("_put_json_path")
            assertion_oracles.append(enriched)

    summary = {
        "raw": int(emission["put"] + emission["concrete"]),
        "valid": int(valid["put"] + valid["concrete"]),
        "put_raw": int(emission["put"]),
        "put_valid": int(valid["put"]),
        "concrete_raw": int(emission["concrete"]),
        "concrete_valid": int(valid["concrete"]),
        "summary_paths": summary_paths,
        "raw_tests": raw_tests,
        "valid_tests": valid_tests,
        "raw_artifacts": raw_tests,
        "valid_artifacts": valid_tests,
        "put_json_count": len(put_jsons),
        "stage4_generation_wall_s": round(
            timing["stage4_generation_wall_s"], 3),
        "stage4_emission_wall_s": round(
            timing["stage4_emission_wall_s"], 3),
        "foundry_replay_wall_s": round(
            timing["foundry_replay_wall_s"], 3),
        "put_all_wall_s": round(timing["put_all_wall_s"], 3),
        "oracle_class_counts": dict(sorted(oracle_label_counts.items())),
        "oracle_class_combo_counts": dict(sorted(oracle_combo_counts.items())),
        "raw_oracle_tag_counts": dict(sorted(raw_oracle_tags.items())),
        "valid_oracle_tag_counts": dict(sorted(valid_oracle_tags.items())),
        "raw_oracle_combo_counts": dict(sorted(raw_oracle_combos.items())),
        "valid_oracle_combo_counts": dict(sorted(valid_oracle_combos.items())),
        "rq1_oracle_tag_counts": dict(sorted(valid_oracle_tags.items())),
        "rq1_oracle_combo_counts": dict(sorted(valid_oracle_combos.items())),
        "assertion_oracles": assertion_oracles,
        "stage4_storage_layout_counts": dict(
            sorted(storage_layout_counts.items())),
    }
    summary.update(_strength_quality(summary))
    summary["artifact_counts"] = _artifact_count_summary(summary)
    summary["time_stats"] = _row_time_stats(summary)
    return summary


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _mem_available_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024.0 * 1024.0)
    except OSError:
        return 0.0
    return 0.0


def validate_jobs(args) -> None:
    if args.jobs <= 0:
        raise RQ1RunError("--jobs must be positive")
    if args.jobs == 1:
        return
    available = _mem_available_gib()
    committed = float(args.jobs * args.memlimit_gib)
    if available and committed > available * args.mem_fraction:
        raise RQ1RunError(
            f"--jobs {args.jobs} x --memlimit-gib {args.memlimit_gib} = "
            f"{committed:g}GiB exceeds {args.mem_fraction:.0%} of "
            f"MemAvailable ({available:.1f}GiB)")


def wait_for_mem_budget(memlimit_gib: int, deadline: float, *, fraction: float,
                        poll_s: float, min_remaining_s: float) -> dict:
    start = time.monotonic()
    required_gib = memlimit_gib / max(fraction, 0.01)
    available = _mem_available_gib()
    waited = False
    while (available and available < required_gib
           and _remaining(deadline) > min_remaining_s):
        waited = True
        sleep_s = min(max(0.5, poll_s), _remaining(deadline))
        time.sleep(sleep_s)
        available = _mem_available_gib()
    status = "ok"
    if available and available < required_gib:
        status = "insufficient-memory"
    return {
        "stage": "resource-wait",
        "status": status,
        "wall_s": round(time.monotonic() - start, 3),
        "waited": waited,
        "mem_available_gib": round(available, 3) if available else 0.0,
        "required_mem_available_gib": round(required_gib, 3),
        "memlimit_gib": memlimit_gib,
        "mem_wait_fraction": fraction,
    }


def _stage_wall_s(stages: list[dict], stage_name: str) -> float:
    if stage_name == "certify":
        return sum(stage.get("wall_s") or 0.0 for stage in stages
                   if str(stage.get("stage") or "").startswith("certify"))
    return sum(stage.get("wall_s") or 0.0 for stage in stages
               if stage.get("stage") == stage_name)


def _format_stage2_no_output_stop(stage2_wall_s: float) -> str:
    return (f"no output after {stage2_wall_s:.1f}s Stage 2; "
            "stopped before remaining units")


def _format_stage4_no_output_stop(stage4_wall_s: float) -> str:
    return (f"no output after {stage4_wall_s:.1f}s Stage 4; "
            "stopped before remaining units")


def _format_no_candidate_unit_stop(count: int) -> str:
    return (f"no Stage-2 candidate after {count} consecutive units; "
            "stopped before remaining units")


def _format_low_budget_concrete_only_skip(remaining_s: float,
                                          threshold_s: int) -> str:
    return (f"valid artifact already produced; {remaining_s:.1f}s remains "
            f"below the {threshold_s}s concrete-only Stage 4 floor")


def _format_low_budget_timeout_only_skip(remaining_s: float,
                                         threshold_s: int) -> str:
    return (f"{remaining_s:.1f}s remains below the {threshold_s}s "
            f"timeout-concrete-only Stage 4 floor")


def _format_put_saturated_concrete_only_skip(put_valid: int,
                                             threshold: int) -> str:
    return (f"{put_valid} valid PUT artifact(s) already produced; "
            f"concrete-only Stage 4 skipped at the {threshold}-PUT floor")


def _format_valid_saturated_concrete_only_skip(valid: int,
                                               put_valid: int) -> str:
    return (f"{valid} valid artifact(s) already produced "
            f"({put_valid} PUT); concrete-only Stage 4 skipped so the remaining "
            "subject budget can target PUT/R1/R2 units")


def _is_concrete_only_stage4(n_certified: int,
                             n_cleared_fallback: int,
                             n_timeout_fallback: int,
                             n_complete_witness_fallback: int = 0,
                             n_partial_journal_fallback: int = 0) -> bool:
    return (n_certified <= 0
            and (n_cleared_fallback + n_timeout_fallback
                 + n_complete_witness_fallback
                 + n_partial_journal_fallback) > 0)


def _should_skip_concrete_only_after_puts(put_summary: dict,
                                         threshold: int,
                                         n_certified: int,
                                         n_cleared_fallback: int,
                                         n_timeout_fallback: int,
                                         n_complete_witness_fallback: int = 0,
                                         n_partial_journal_fallback: int = 0) -> bool:
    if threshold <= 0:
        return False
    if int(put_summary.get("put_valid") or 0) < threshold:
        return False
    return _is_concrete_only_stage4(
        n_certified, n_cleared_fallback, n_timeout_fallback,
        n_complete_witness_fallback, n_partial_journal_fallback)


def _should_skip_concrete_only_after_any_valid(put_summary: dict,
                                               enabled: bool,
                                               n_certified: int,
                                               n_cleared_fallback: int,
                                               n_timeout_fallback: int,
                                               n_complete_witness_fallback: int = 0,
                                               n_partial_journal_fallback: int = 0) -> bool:
    if not enabled:
        return False
    if int(put_summary.get("valid") or 0) <= 0:
        return False
    return _is_concrete_only_stage4(
        n_certified, n_cleared_fallback, n_timeout_fallback,
        n_complete_witness_fallback, n_partial_journal_fallback)


def _should_skip_low_budget_concrete_only_stage4(put_summary: dict,
                                                remaining_s: float,
                                                threshold_s: int,
                                                n_certified: int,
                                                n_cleared_fallback: int,
                                                n_timeout_fallback: int,
                                                n_complete_witness_fallback: int = 0,
                                                n_partial_journal_fallback: int = 0) -> bool:
    if threshold_s <= 0:
        return False
    if int(put_summary.get("valid") or 0) <= 0:
        return False
    if not _is_concrete_only_stage4(
            n_certified, n_cleared_fallback, n_timeout_fallback,
            n_complete_witness_fallback, n_partial_journal_fallback):
        return False
    return remaining_s < float(threshold_s)


def _should_skip_low_budget_timeout_only_stage4(remaining_s: float,
                                                threshold_s: int,
                                                n_certified: int,
                                                n_cleared_fallback: int,
                                                n_timeout_fallback: int,
                                                n_complete_witness_fallback: int = 0,
                                                n_partial_journal_fallback: int = 0) -> bool:
    if threshold_s <= 0:
        return False
    if n_certified > 0 or n_cleared_fallback > 0:
        return False
    if (n_timeout_fallback + n_complete_witness_fallback
            + n_partial_journal_fallback) <= 0:
        return False
    return remaining_s < float(threshold_s)


def _should_stop_after_zero_output_stage4(stages: list[dict],
                                          put_summary: dict,
                                          threshold_s: int) -> bool:
    if threshold_s <= 0:
        return False
    if int(put_summary.get("raw") or 0) > 0:
        return False
    return _stage_wall_s(stages, "put") >= float(threshold_s)


def _should_stop_after_no_output_stage2(stages: list[dict],
                                        put_summary: dict,
                                        threshold_s: int,
                                        units_attempted: int,
                                        units_scheduled: int,
                                        min_attempted_units: int = 2) -> bool:
    if threshold_s <= 0:
        return False
    if int(put_summary.get("raw") or 0) > 0:
        return False
    effective_min = max(1, int(min_attempted_units))
    if units_scheduled > 0:
        effective_min = min(effective_min, units_scheduled)
        if units_attempted < units_scheduled:
            return False
    if units_attempted < effective_min:
        return False
    return _stage_wall_s(stages, "certify") >= float(threshold_s)


def _should_stop_after_no_candidate_units(consecutive_units: int,
                                          put_summary: dict,
                                          threshold_units: int,
                                          *,
                                          units_scheduled: int = 0,
                                          min_threshold_units: int = 0,
                                          pending_hinted_units: int = 0) -> bool:
    if threshold_units <= 0:
        return False
    if int(put_summary.get("raw") or 0) > 0:
        return False
    if pending_hinted_units > 0:
        return False
    if units_scheduled > 0 and consecutive_units < units_scheduled:
        return False
    effective_threshold = threshold_units
    if min_threshold_units > 0:
        effective_threshold = max(effective_threshold, min_threshold_units)
    if units_scheduled > 0:
        effective_threshold = min(effective_threshold, units_scheduled)
    return consecutive_units >= effective_threshold


NON_METHOD_NO_CANDIDATE_DIAGNOSTICS = {
    "esbmc-no-cov-report",
    "focus-function-matched-none",
    "frontend-address-member-tuple",
    "frontend-bitwise-static-bytes",
    "frontend-conversion-error",
    "frontend-selector-call-type-mismatch",
    "frontend-tuple-rhs-symbol",
    "frontend-unsupported-type-name-type",
    "frontend-unexpected-tuple",
    "goto-inline-call-type-mismatch",
    "irep2-arith-assert",
    "irep2-member-source-not-struct",
    "migrate-expr-failed",
    "namespace-follow-missing-symbol-type",
    "outer-box-solver-oom",
    "path-coverage-bad-alloc-no-report",
    "path-coverage-no-claims-reached-solver",
    "path-coverage-partial-journal-only",
    "path-coverage-partial-journal-no-report",
    "path-coverage-partial-signal-no-report",
    "path-coverage-per-claim-solve-died-no-report",
    "path-coverage-probe-goal-cap",
    "path-coverage-probe-claim-explosion",
    "path-coverage-untokened-u-no-report",
    "recursive-helper-preflight-refused",
    "solver-tuple-ast-mismatch",
    "unwind-truncation",
}


def _no_candidate_counts_against_stop(cert_row: dict | None) -> bool:
    """Whether a no-candidate unit is evidence to stop trying this subject.

    Tool/frontend/focus failures say the current unit did not produce a Stage-4
    candidate. They do not say that the remaining cheap units in the same
    contract cannot produce one, so they must not trigger the consecutive
    no-candidate early-stop gate.
    """
    if not isinstance(cert_row, dict):
        return True
    diagnostic = cert_row.get("driver_diagnostic") or {}
    if isinstance(diagnostic, dict):
        tag = diagnostic.get("tag")
        category = diagnostic.get("category")
        if tag in NON_METHOD_NO_CANDIDATE_DIAGNOSTICS:
            return False
        if category == "no-cov-report":
            return False
    bucket = str(cert_row.get("bucket") or "").upper()
    if bucket in ("CRASHED", "KILLED", "UNWIND-TRUNCATED"):
        return False
    return True


def _record_no_candidate_unit(consecutive_units: int,
                              max_consecutive_units: int) -> tuple[int, int]:
    consecutive_units += 1
    return consecutive_units, max(max_consecutive_units, consecutive_units)


def _pending_hinted_units(jobs: list[dict], attempted_units: list[str]) -> int:
    attempted = set(attempted_units)
    pending = set()
    for job in jobs:
        unit = job.get("unit")
        hinted = set((job.get("unit_hints") or {}).get("hinted_units") or [])
        if unit and unit in hinted and unit not in attempted:
            pending.add(unit)
    return len(pending)


def _pending_units_after(jobs: list[dict], idx: int) -> int:
    return max(0, len(jobs) - idx)


def _should_continue_after_stage2_no_output(jobs: list[dict], idx: int,
                                            n_stage4_candidates: int | None,
                                            cert_stage: dict) -> bool:
    if _pending_units_after(jobs, idx) <= 0:
        return False
    if int(n_stage4_candidates or 0) > 0:
        return False
    return cert_stage.get("status") in ("timeout", "error")


def _overload_retry_job_id(job: dict, path_function: str) -> str:
    base = str(job.get("job_id") or job.get("unit") or "unit")
    marker = _path_function_declaration_id(path_function)
    if marker is None:
        marker = re.sub(r"[^A-Za-z0-9]+", "_", path_function).strip("_")
    return f"{base}__pf{marker}"


def _workdir_with_suffix(workdir: str | None, suffix: str) -> str | None:
    if not workdir:
        return workdir
    path = Path(str(workdir))
    return str(path.with_name(path.name + suffix))


def _overload_path_function_retry_jobs(job: dict, cert_row: dict | None,
                                       existing_jobs: list[dict]) -> list[dict]:
    if not isinstance(cert_row, dict):
        return []
    if job.get("path_function"):
        return []
    diagnostic = cert_row.get("driver_diagnostic") or {}
    if not isinstance(diagnostic, dict):
        return []
    if diagnostic.get("tag") != "overloaded-unit-path-function-required":
        return []
    unit = job.get("unit")
    path_functions = [
        str(path_function)
        for path_function in (diagnostic.get("path_functions") or [])
        if path_function
    ]
    if not unit or not path_functions:
        return []
    existing = {
        (existing_job.get("unit"), existing_job.get("path_function"))
        for existing_job in existing_jobs
    }
    out = []
    for path_function in path_functions:
        key = (unit, path_function)
        if key in existing:
            continue
        clone = copy.deepcopy(job)
        clone["path_function"] = path_function
        clone["job_id"] = _overload_retry_job_id(job, path_function)
        clone["overload_retry_from_job_id"] = job.get("job_id")
        clone["overload_retry_reason"] = diagnostic.get("reason")
        clone["certify_argv"] = _argv_with_value(
            [str(arg) for arg in clone.get("certify_argv") or []],
            "--path-function",
            path_function)
        if clone.get("dry_run_argv"):
            clone["dry_run_argv"] = _argv_with_value(
                [str(arg) for arg in clone["dry_run_argv"]],
                "--path-function",
                path_function)
        budget = dict(clone.get("certification_budget") or {})
        budget["workdir"] = _workdir_with_suffix(
            budget.get("workdir"),
            "__" + _safe_name(_overload_retry_job_id(job, path_function)))
        clone["certification_budget"] = budget
        out.append(clone)
        existing.add(key)
    return out


def _argv_with_value(argv: list[str], flag: str, value: str) -> list[str]:
    out = []
    idx = 0
    replaced = False
    while idx < len(argv):
        item = argv[idx]
        if item == flag:
            out.extend([item, value])
            idx += 2
            replaced = True
            continue
        out.append(item)
        idx += 1
    if not replaced:
        out.extend([flag, value])
    return out


def _stage2_cert_shard_path(cert_path: Path, idx: int, unit: str) -> Path:
    return cert_path.parent / "shards" / f"{idx:03d}-{_safe_name(unit)}.jsonl"


def _stage2_retry_cert_shard_path(cert_path: Path, idx: int, unit: str,
                                  reason: str) -> Path:
    return cert_path.parent / "shards" / (
        f"{idx:03d}-{_safe_name(unit)}-retry-{_safe_name(reason)}.jsonl")


def _merge_jsonl_records(dst: Path, src: Path) -> dict:
    merged = 0
    invalid = 0
    if not src.exists():
        return {
            "src": str(src),
            "dst": str(dst),
            "merged": 0,
            "invalid": 0,
            "missing": True,
        }
    for line in src.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        _append_jsonl(dst, row)
        merged += 1
    return {
        "src": str(src),
        "dst": str(dst),
        "merged": merged,
        "invalid": invalid,
        "missing": False,
    }


def _stage4_reserve_s(args) -> int:
    explicit = int(getattr(args, "stage2_stage4_reserve_s", 0) or 0)
    if explicit > 0:
        return explicit
    return max(
        int(getattr(args, "min_timeout_only_stage4_s", 0) or 0),
        int(getattr(args, "min_concrete_only_stage4_s", 0) or 0),
        int(getattr(args, "min_remaining_s", 0) or 0),
    )


def _stage2_budget_before_stage4(remaining_s: float,
                                 reserve_s: int,
                                 unit_timeout_cap_s: int = 0) -> tuple[int, int]:
    """Budget Stage 2 without spending the Stage-4 salvage window.

    A timed-out Stage 2 can still leave a complete/partial witness journal that
    Stage 4 can render as a concrete replay. If Stage 2 receives the whole
    subject remainder, those rows become visible only after the subject budget
    is already exhausted and the runner never calls put_all.py.
    """
    budget_source = float(remaining_s)
    reserve_applied = 0
    if reserve_s > 0 and budget_source > float(reserve_s + 1):
        budget_source = max(1.0, budget_source - float(reserve_s))
        reserve_applied = int(reserve_s)
    if unit_timeout_cap_s > 0:
        budget_source = min(budget_source, float(unit_timeout_cap_s))
    return max(1, int(budget_source)), reserve_applied


def _certify_argv_for_remaining(job: dict, remaining_s: float, run_timeout_s: int,
                                memlimit_gib: int,
                                unit_timeout_cap_s: int = 0,
                                out_path: Path | None = None,
                                stage_mem_fraction: float | None = None,
                                esbmc_bin: str | None = None,
                                stage4_reserve_s: int = 0) -> list[str]:
    budget, _reserve_applied = _stage2_budget_before_stage4(
        remaining_s, stage4_reserve_s, unit_timeout_cap_s)
    run_budget = max(1, min(budget, int(run_timeout_s)))
    argv = unit_schedule.budgeted_certify_argv(
        [str(arg) for arg in job["certify_argv"]],
        timeout_s=budget,
        run_timeout_s=run_budget,
        memlimit_gib=memlimit_gib,
        workdir=job["certification_budget"]["workdir"])
    if out_path is not None:
        argv = _argv_with_value(argv, "--out", str(out_path))
    if stage_mem_fraction is not None:
        argv = _argv_with_value(argv, "--mem-fraction",
                                f"{stage_mem_fraction:g}")
    if esbmc_bin:
        argv = _argv_with_value(argv, "--esbmc", esbmc_bin)
    return argv


def _copy_ce_collection_artifacts(source: Path, destination: Path) -> list[str]:
    """Copy Stage-1 evidence into the subject result without promoting it."""
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("ce-collection.json", "ce-witness-journal.json",
                 "cov-ce-journal.json", "enumeration-report.json",
                 "generalise-progress.json", "run-config.json", "driver.log"):
        candidate = source / name
        if not candidate.is_file():
            continue
        target = destination / name
        shutil.copy2(candidate, target)
        copied.append(str(target))
    return copied


def _ce_artifact_workdir(result_path: Path, fallback: Path) -> Path:
    """Return this invocation's driver workdir, never a stale sibling run."""
    try:
        rows = [json.loads(line) for line in result_path.read_text().splitlines()
                if line.strip()]
    except (OSError, json.JSONDecodeError):
        return fallback
    for row in reversed(rows):
        evidence = row.get("failure_evidence") if isinstance(row, dict) else None
        active = evidence.get("active_workdir") if isinstance(evidence, dict) else None
        if isinstance(active, str) and active:
            candidate = Path(active)
            if candidate.is_dir():
                return candidate
    return fallback


def run_ce_collection_subject(subject: PreparedSubject, case_dir: Path,
                              jobs: list[dict], args) -> tuple[dict, dict]:
    """Collect one bounded CE for a subject without touching RQ1 test results."""
    collection_root = case_dir / "ce-collection"
    started = time.monotonic()
    stage = {
        "stage": "ce-collection",
        "status": "no-unit",
        "budget_s": 60,
        "unit": None,
        "path_function": None,
        "artifact_paths": [],
    }
    if jobs:
        job = jobs[0]
        unit = str(job["unit"])
        stage.update({"unit": unit,
                      "path_function": job.get("path_function")})
        out_path = collection_root / "certify-results.jsonl"
        argv = _certify_argv_for_remaining(
            job, remaining_s=60, run_timeout_s=60,
            memlimit_gib=args.memlimit_gib, unit_timeout_cap_s=60,
            out_path=out_path, stage_mem_fraction=args.stage_mem_fraction,
            esbmc_bin=getattr(args, "esbmc", "") or None,
            stage4_reserve_s=0)
        argv.append("--ce-collection-only")
        result = run_command(
            argv, 60 + args.wrapper_grace,
            case_dir / "logs" / f"ce-{_safe_name(unit)}")
        source = _ce_artifact_workdir(
            out_path, Path(job["certification_budget"]["workdir"]))
        artifact_dir = collection_root / _safe_name(unit)
        copied = _copy_ce_collection_artifacts(source, artifact_dir)
        stage.update(result)
        stage.update({
            "stage": "ce-collection",
            "unit": unit,
            "path_function": job.get("path_function"),
            "source_workdir": str(source),
            "artifact_paths": copied,
            "artifact_present": any(
                Path(path).name == "ce-collection.json" for path in copied),
        })

    elapsed = round(time.monotonic() - started, 3)
    summary = {
        "schema": "veriput-rq1-ce-collection/1",
        "subject_id": subject.subject_id,
        "benchmark": subject.benchmark,
        "contract": subject.contract,
        "budget_s": 60,
        "wall_s": elapsed,
        "stage": stage,
        "note": (
            "Refutation evidence only. This record is neither a valid test "
            "nor a PUT/region proof and must not enter RQ1 validity counts."),
    }
    _write_json(collection_root / "summary.json", summary)
    row = {
        "key": _run_key(subject.subject_id, ce_collection_only=True),
        "schema": "veriput-rq1-ce-collection-row/1",
        "stage": "ce_collection",
        "subject_id": subject.subject_id,
        "benchmark": subject.benchmark,
        "contract": subject.contract,
        "status": stage["status"],
        "artifact_present": stage.get("artifact_present", False),
        "unit": stage.get("unit"),
        "wall_total_s": elapsed,
        "raw": 0,
        "valid": 0,
        "put_raw": 0,
        "put_valid": 0,
        "concrete_raw": 0,
        "concrete_valid": 0,
        "quality_bucket": "ce-only",
        "summary": str(collection_root / "summary.json"),
    }
    return row, summary


def _append_esbmc_arg(argv: list[str], value: str) -> list[str]:
    return list(argv) + [f"--esbmc-arg={value}"]


def _bounded_holds_retry_argv(argv: list[str], *,
                              max_tx: int,
                              unwind: int,
                              out_path: Path) -> list[str]:
    out = _argv_with_value([str(arg) for arg in argv], "--max-tx", str(max_tx))
    out = _argv_with_value(out, "--out", str(out_path))
    out = _append_esbmc_arg(out, "--unwind")
    out = _append_esbmc_arg(out, str(unwind))
    return out


def _latest_cert_row(cert_path: Path, benchmark_key: str, unit: str,
                     path_function: str | None = None) -> dict | None:
    if not cert_path.exists():
        return None
    found = None
    for line in cert_path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _cert_row_matches(row, benchmark_key, unit, path_function):
            found = row
    return found


def _is_bounded_holds_retry_candidate(row: dict | None,
                                      max_initial_wall_s: int) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("bucket") != "NO-PATH":
        return False
    progress = row.get("generalise_progress") or {}
    if not isinstance(progress, dict):
        return False
    if progress.get("empty_witness_class") != "bounded-holds-only":
        return False
    if max_initial_wall_s > 0:
        try:
            if float(row.get("wall_s") or 0.0) > max_initial_wall_s:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _job_cost_tier(job: dict) -> int:
    rank = (job.get("schedule_rank") or {}).get("cheap_first") or []
    if not rank:
        return 50
    try:
        return int(rank[0])
    except (TypeError, ValueError):
        return 50


WEAK_STAGE2_BUCKETS = {
    "KILLED",
    "NO-COORDINATE",
    "NO-PATH",
    "NO-WITNESS-UNDECIDED",
    "UNWIND-TRUNCATED",
}
WEAK_STAGE2_DIAGNOSTICS = {
    "esbmc-no-cov-report",
    "path-coverage-no-claims-reached-solver",
    "path-coverage-partial-journal-only",
    "path-coverage-partial-journal-no-report",
}


def _is_weak_stage2_result(cert_row: dict | None) -> bool:
    if not isinstance(cert_row, dict):
        return False
    bucket = str(cert_row.get("bucket") or "").upper()
    if bucket in WEAK_STAGE2_BUCKETS:
        return True
    diagnostic = cert_row.get("driver_diagnostic") or {}
    if not isinstance(diagnostic, dict):
        return False
    if diagnostic.get("tag") in WEAK_STAGE2_DIAGNOSTICS:
        return True
    return diagnostic.get("category") == "no-cov-report"


def _continuation_job_key(job: dict) -> tuple:
    schedule_rank = job.get("schedule_rank") or {}
    coordinate_rank = schedule_rank.get("coordinate_first") or [3]
    put_rank = schedule_rank.get("put_potential_first") or [5]
    try:
        coordinate = int(coordinate_rank[0])
    except (TypeError, ValueError, IndexError):
        coordinate = 3
    try:
        put = int(put_rank[0])
    except (TypeError, ValueError, IndexError):
        put = 5
    return (
        0 if put <= 1 else 1,
        0 if coordinate <= 1 else 1,
        put,
        coordinate,
        _job_cost_tier(job),
        int(job.get("ordinal") or 0),
    )


def _requeue_weak_stage2_suffix(jobs: list[dict], next_index: int,
                                cert_row: dict | None) -> dict | None:
    """Put unattempted coordinate/PUT candidates ahead of a weak unit."""
    if not _is_weak_stage2_result(cert_row) or next_index >= len(jobs):
        return None
    pending = list(jobs[next_index:])
    before = [job.get("job_id") for job in pending]
    pending.sort(key=_continuation_job_key)
    after = [job.get("job_id") for job in pending]
    if before == after:
        return None
    jobs[next_index:] = pending
    return {
        "bucket": cert_row.get("bucket"),
        "driver_diagnostic_tag": (
            (cert_row.get("driver_diagnostic") or {}).get("tag")
            if isinstance(cert_row.get("driver_diagnostic") or {}, dict)
            else None),
        "pending_jobs_before": before,
        "pending_jobs_after": after,
        "reason": (
            "weak Stage-2 result requeued the unattempted suffix by "
            "coordinate and PUT potential"),
    }


def _effective_stage2_unit_timeout_cap_s(job: dict,
                                         args,
                                         units_scheduled: int,
                                         prior_no_candidate_units: int = 0,
                                         *,
                                         remaining_s: float = 0.0,
                                         stage4_reserve_s: int = 0) -> int:
    explicit = int(args.stage2_unit_timeout_cap_s or 0)
    if explicit > 0:
        return explicit
    adaptive = int(args.adaptive_stage2_unit_timeout_cap_s or 0)
    if adaptive <= 0:
        return 0
    needs_cap = (
        units_scheduled > 1 and prior_no_candidate_units > 0
        or units_scheduled >= ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD
        or _job_cost_tier(job) >= ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD)
    if not needs_cap:
        return 0
    if units_scheduled < ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD:
        return adaptive
    if remaining_s <= 0:
        return adaptive
    stage2_window_s = max(1.0, remaining_s - float(stage4_reserve_s))
    fair_slots = min(units_scheduled, ADAPTIVE_STAGE2_FAIR_SHARE_SLOTS)
    fair_share_s = max(30, int(stage2_window_s / max(1, fair_slots)))
    return min(adaptive, fair_share_s)


def _stage2_unit_timeout_cap_reason(args, effective_cap_s: int) -> str:
    if int(args.stage2_unit_timeout_cap_s or 0) > 0:
        return "explicit"
    if effective_cap_s > 0:
        return "adaptive"
    return "uncapped"


def _stage2_wrapper_timeout_s(remaining_s: float,
                              wrapper_grace_s: int,
                              effective_unit_cap_s: int,
                              stage4_reserve_s: int = 0) -> float:
    budget, _reserve_applied = _stage2_budget_before_stage4(
        remaining_s, stage4_reserve_s, effective_unit_cap_s)
    timeout_s = float(budget) + max(0, int(wrapper_grace_s))
    if stage4_reserve_s > 0:
        # Cleanup grace must not consume the subject-generation window that
        # Stage 4 is promised. The subprocess timeout is the hard boundary.
        timeout_s = min(timeout_s,
                        max(1.0, float(remaining_s) - stage4_reserve_s))
    return timeout_s


def _stage2_reserve_boundary_reached(remaining_s: float,
                                     stage4_reserve_s: int) -> bool:
    # _stage2_budget_before_stage4 guarantees a one-second minimum command
    # budget; stop before that quantum could cross the protected boundary.
    return (stage4_reserve_s > 0
            and remaining_s <= float(stage4_reserve_s + 1))


def _bounded_holds_retry_policy(args) -> dict:
    return {
        "bounded_holds_retry": bool(getattr(args, "bounded_holds_retry",
                                           False)),
        "bounded_holds_retry_max_tx":
            int(getattr(args, "bounded_holds_retry_max_tx", 2)),
        "bounded_holds_retry_unwind":
            int(getattr(args, "bounded_holds_retry_unwind", 8)),
        "bounded_holds_retry_max_initial_wall_s":
            int(getattr(args, "bounded_holds_retry_max_initial_wall_s", 45)),
    }


def annotate_stage2_runtime_policy(schedule: dict, args) -> dict:
    jobs = list(schedule.get("jobs") or [])
    units_scheduled = len(jobs)
    schedule["rq1_stage2_runtime_policy"] = {
        "stage2_unit_timeout_cap_s": args.stage2_unit_timeout_cap_s,
        "adaptive_stage2_unit_timeout_cap_s":
            args.adaptive_stage2_unit_timeout_cap_s,
        "stage2_stage4_reserve_s": _stage4_reserve_s(args),
        "stage2_stage4_reserve_reason":
            "explicit stage2_stage4_reserve_s or "
            "max(min_remaining_s, min_timeout_only_stage4_s, "
            "min_concrete_only_stage4_s)",
        "stage4_reserve_boundary_enforced": True,
        "adaptive_stage2_many_unit_threshold":
            ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
        "adaptive_stage2_expensive_tier_threshold":
            ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
        "wrapper_timeout":
            "min(subject_remaining_s - stage4_reserve_s, "
            "effective_unit_cap_s) + wrapper_grace_s",
        "stage4_reserve_s": _stage4_reserve_s(args),
        "stage4_reserve_reason":
            "explicit stage2_stage4_reserve_s or "
            "max(min_remaining_s, min_timeout_only_stage4_s, "
            "min_concrete_only_stage4_s)",
        "capped_timeout_advances_to_next_unit": True,
    }
    schedule["rq1_stage2_runtime_policy"].update(
        _bounded_holds_retry_policy(args))
    for job in jobs:
        initial_cap = _effective_stage2_unit_timeout_cap_s(
            job, args, units_scheduled, 0)
        after_no_candidate_cap = _effective_stage2_unit_timeout_cap_s(
            job, args, units_scheduled, 1)
        job["rq1_stage2_runtime_policy"] = {
            "unit_cost_tier": _job_cost_tier(job),
            "initial_effective_unit_timeout_cap_s": initial_cap,
            "initial_cap_reason":
                _stage2_unit_timeout_cap_reason(args, initial_cap),
            "after_no_candidate_effective_unit_timeout_cap_s":
                after_no_candidate_cap,
            "after_no_candidate_cap_reason":
                _stage2_unit_timeout_cap_reason(args, after_no_candidate_cap),
        }
    return schedule


def _put_argv(cert_path: Path, unit: str, benchmark_key: str, out_root: Path,
              remaining_s: float, memlimit_gib: int, forge_timeout: int,
              path_function: str | None = None,
              esbmc_bin: str | None = None) -> list[str]:
    budget = max(1, int(remaining_s))
    _ = path_function
    selector = f"{benchmark_key}.{unit}"
    argv = [
        sys.executable,
        str(PUT_ALL),
        "--cert",
        str(cert_path),
        "--only",
        selector,
        "--strong-recipe",
        "--emit-cleared-concrete-fallbacks",
        "--timeout",
        str(budget),
        "--forge-timeout",
        str(forge_timeout),
        "--memlimit-gib",
        str(memlimit_gib),
        "--out-root",
        str(out_root),
    ]
    if esbmc_bin:
        argv += ["--esbmc", esbmc_bin]
    return argv


def _run_ce_replay_candidate(subject: PreparedSubject, case_dir: Path,
                             candidate: dict, args,
                             deadline: float) -> dict:
    """Run one CE candidate through an isolated Stage-4 transaction.

    The candidate cert row and all generated files live below
    ``candidate-stage4`` until the final verifier/Foundry gate passes.  In
    particular, no candidate is merged into the canonical cert journal or
    result row merely because it was materialized.
    """
    candidate_id = str(candidate["candidate_id"])
    run_root = (case_dir / "candidate-stage4" / _safe_name(candidate_id)
                / f"run-{time.time_ns()}")
    cert_path = run_root / "candidate-cert.jsonl"
    output_root = run_root / "out"
    _append_jsonl(cert_path, _candidate_cert_row(candidate, subject))
    remaining = _remaining(deadline)
    unit = str(candidate["case"]["unit"])
    path_function = str(candidate["path"]["path_function"])
    argv = _put_argv(
        cert_path,
        unit,
        subject.benchmark_key,
        output_root,
        remaining,
        args.memlimit_gib,
        args.forge_timeout,
        path_function,
        getattr(args, "esbmc", "") or None)
    wrapper_timeout = max(1.0, remaining) + args.wrapper_grace + 2 * args.forge_timeout
    stage = run_command(
        argv, wrapper_timeout,
        run_root / "candidate-stage4")
    summary = summarize_put_artifacts(output_root)
    gate = _candidate_gate(summary, candidate)
    promotion = {
        "candidate_id": candidate_id,
        "formal_results_written_before_gate": False,
        "gate": gate,
        "promoted": False,
        "destination": None,
    }
    if stage["status"] == "ok" and gate["promotable"]:
        try:
            destination = _promote_candidate_artifacts(
                output_root, case_dir, candidate_id)
        except Exception as exc:  # noqa: BLE001 - isolate one candidate
            gate["promotable"] = False
            promotion.update({
                "reason": "promotion-failed",
                "error": str(exc),
            })
        else:
            promotion.update({
                "promoted": True,
                "destination": str(destination),
            })
    _write_json(run_root / "candidate-result.json", {
        "schema": "veriput-rq1-ce-replay-stage4-result/v1",
        "candidate": candidate,
        "stage": stage,
        "put": summary,
        "gate": gate,
        "promotion": promotion,
        "formal_results_written": bool(promotion["promoted"]),
        "theory_delta": 0,
    })
    stage.update({
        "stage": "candidate-stage4",
        "candidate_id": candidate_id,
        "unit": unit,
        "path_function": path_function,
        "candidate_root": str(run_root),
        "candidate_output_root": str(output_root),
        "put_summary": summary,
        "candidate_gate": gate,
        "candidate_promotion": promotion,
        "formal_results_written": bool(promotion["promoted"]),
        "theory_delta": 0,
    })
    return stage


def run_subject(target_row: dict, dataset_label: str, args) -> tuple[dict, dict]:
    start = time.monotonic()
    subject_id = target_row["subject_id"]
    case_dir = Path(args.result_root) / dataset_label / "subjects" / _safe_name(subject_id)
    # CE discovery may be launched by the existing worker command, which
    # carries --redo for full generation. It must never archive/replace the
    # canonical result directory while collecting refutation-only evidence.
    prepare_case_dir(
        case_dir,
        force_fresh=bool(args.redo and not args.ce_collection_only))
    cert_path = case_dir / "cert" / "certify-results.jsonl"
    ast_cache_root = Path(args.ast_cache_root).expanduser().resolve()
    subject = subject_unit_manifest.resolve_subject(
        subject_id,
        benchmark=target_row["benchmark"],
        require_unit=False)
    subject = cached_subject(subject.with_inferred_solc_bin(), ast_cache_root, dataset_label)
    deadline = start + float(args.timeout)
    stages = []
    units_attempted = []
    result_status = "ok"
    failure_reason = None
    early_stop_reason = None
    consecutive_no_candidate_units = 0
    max_consecutive_no_candidate_units = 0
    stage2_no_candidate_evidence_units = 0
    stage2_no_candidate_stop_skipped_units = []
    stage2_no_output_continuations = []
    stage4_candidate_units_attempted = 0
    low_budget_concrete_only_stage4_skips = []
    low_budget_timeout_only_stage4_skips = []
    put_saturated_concrete_only_stage4_skips = []
    valid_saturated_concrete_only_stage4_skips = []
    concrete_only_stage4_budget_caps = []
    concrete_only_stage4_soft_failures = []
    cert_shard_merges = []
    ce_replay_candidates = []
    ce_replay_rejected = []
    ce_replay_stages = []
    concrete_only_stage4_timeout_cap_s = int(
        getattr(args, "concrete_only_stage4_timeout_cap_s",
                DEFAULT_CONCRETE_ONLY_STAGE4_TIMEOUT_CAP_S) or 0)

    try:
        schedule = build_subject_schedule(subject,
                                          target_row,
                                          ast_cache_root,
                                          case_dir,
                                          timeout_s=args.timeout,
                                          run_timeout_s=args.esbmc_run_timeout,
                                          memlimit_gib=args.memlimit_gib)
        schedule = filter_schedule_units(schedule, getattr(args, "unit", []))
        annotate_stage2_runtime_policy(schedule, args)
    except Exception as exc:  # Fail-soft at subject granularity.
        result_status = "error"
        failure_reason = str(exc)
        schedule = {
            "schema": "veriput-unit-schedule/v1",
            "jobs": [],
            "summary": {},
        }

    _write_json(case_dir / "unit-schedule.json", schedule)
    jobs = list(schedule.get("jobs") or [])
    if args.ce_collection_only:
        return run_ce_collection_subject(subject, case_dir, jobs, args)
    if getattr(args, "ce_replay_manifest", None):
        ce_replay_candidates, ce_replay_rejected = (
            _load_ce_replay_candidates(
                _candidate_manifest_paths(args.ce_replay_manifest),
                target_row, subject, case_dir))
        requested_units = set(getattr(args, "unit", []) or [])
        if requested_units:
            admissible = []
            for candidate in ce_replay_candidates:
                unit = candidate["case"]["unit"]
                if unit in requested_units:
                    admissible.append(candidate)
                else:
                    ce_replay_rejected.append({
                        "manifest": candidate.get("_manifest"),
                        "candidate_id": candidate["candidate_id"],
                        "reason": "candidate unit excluded by --unit",
                        "unit": unit,
                    })
            ce_replay_candidates = admissible
        if getattr(args, "ce_replay_only", False):
            jobs = []
        for candidate in ce_replay_candidates:
            remaining = _remaining(deadline)
            if remaining < args.min_remaining_s:
                ce_replay_rejected.append(
                    _candidate_rejection(
                        candidate,
                        "case budget exhausted before candidate Stage 4"))
                continue
            mem_wait = wait_for_mem_budget(
                args.memlimit_gib,
                deadline,
                fraction=args.stage_mem_fraction,
                poll_s=args.mem_wait_poll_s,
                min_remaining_s=args.min_remaining_s)
            if mem_wait["status"] != "ok":
                ce_replay_rejected.append(
                    _candidate_rejection(
                        candidate,
                        "insufficient memory before candidate Stage 4",
                        f"{mem_wait['mem_available_gib']}GiB available"))
                continue
            candidate_stage = _run_ce_replay_candidate(
                subject, case_dir, candidate, args, deadline)
            ce_replay_stages.append(candidate_stage)
            stages.append(candidate_stage)
        if (getattr(args, "ce_replay_only", False)
                and not ce_replay_candidates):
            result_status = "no-output"
            failure_reason = "no admissible CE replay candidates"
    if (result_status == "ok" and not jobs
            and not getattr(args, "ce_replay_only", False)):
        result_status, failure_reason = _empty_schedule_status_reason(schedule)
        if (result_status == "no-units"
                and not getattr(args, "ce_replay_only", False)):
            fallback_stage = emit_no_unit_deploy_fallback(
                subject, case_dir, schedule, args.forge_timeout)
            stages.append(fallback_stage)

    for idx, job in enumerate(jobs, 1):
        remaining_before_stage2 = _remaining(deadline)
        stage2_stage4_reserve_s = _stage4_reserve_s(args)
        if _stage2_reserve_boundary_reached(
                remaining_before_stage2, stage2_stage4_reserve_s):
            result_status = "budget-exhausted"
            failure_reason = (
                "Stage-2 stopped at the hard Stage-4 reserve boundary before "
                "remaining units")
            break
        if remaining_before_stage2 < args.min_remaining_s:
            result_status = "budget-exhausted"
            failure_reason = "case budget exhausted before remaining units"
            break
        unit = job["unit"]
        path_function = job.get("path_function")
        units_attempted.append(unit)
        mem_wait = wait_for_mem_budget(
            args.memlimit_gib,
            deadline,
            fraction=args.stage_mem_fraction,
            poll_s=args.mem_wait_poll_s,
            min_remaining_s=args.min_remaining_s)
        if mem_wait["waited"] or mem_wait["status"] != "ok":
            mem_wait.update({"unit": unit, "before_stage": "certify"})
            stages.append(mem_wait)
        if mem_wait["status"] != "ok":
            result_status = "budget-exhausted"
            failure_reason = (
                f"insufficient memory before certify {unit}: "
                f"need MemAvailable >= "
                f"{mem_wait['required_mem_available_gib']}GiB for "
                f"{args.memlimit_gib}GiB at "
                f"{args.stage_mem_fraction:.0%}; have "
                f"{mem_wait['mem_available_gib']}GiB")
            break
        cert_shard_path = _stage2_cert_shard_path(cert_path, idx, unit)
        stage2_remaining_s = _remaining(deadline)
        if _stage2_reserve_boundary_reached(
                stage2_remaining_s, stage2_stage4_reserve_s):
            result_status = "budget-exhausted"
            failure_reason = (
                "Stage-2 stopped at the hard Stage-4 reserve boundary before "
                "remaining units")
            break
        effective_stage2_cap_s = _effective_stage2_unit_timeout_cap_s(
            job,
            args,
            len(jobs),
            consecutive_no_candidate_units,
            remaining_s=stage2_remaining_s,
            stage4_reserve_s=stage2_stage4_reserve_s)
        _stage2_budget_s, stage2_stage4_reserve_applied_s = (
            _stage2_budget_before_stage4(stage2_remaining_s,
                                         stage2_stage4_reserve_s,
                                         effective_stage2_cap_s))
        cert_argv = _certify_argv_for_remaining(job, stage2_remaining_s,
                                                args.esbmc_run_timeout,
                                                args.memlimit_gib,
                                                effective_stage2_cap_s,
                                                cert_shard_path,
                                                args.stage_mem_fraction,
                                                getattr(args, "esbmc", "") or None,
                                                stage2_stage4_reserve_s)
        cert_wrapper_timeout_s = _stage2_wrapper_timeout_s(
            stage2_remaining_s, args.wrapper_grace, effective_stage2_cap_s,
            stage2_stage4_reserve_s)
        n_stage4_candidates = None
        cert_stage = run_command(cert_argv,
                                 cert_wrapper_timeout_s,
                                 case_dir / "logs" / f"{idx:03d}-{_safe_name(unit)}-certify")
        cert_stage.update({
            "stage": "certify",
            "unit": unit,
            "path_function": path_function,
            "job_id": job.get("job_id"),
            "cert_shard_jsonl": str(cert_shard_path),
            "cert_canonical_jsonl": str(cert_path),
            "wrapper_timeout_s": round(cert_wrapper_timeout_s, 3),
            "subject_remaining_before_stage2_s":
                round(stage2_remaining_s, 3),
            "stage2_stage4_reserve_s": stage2_stage4_reserve_applied_s,
            "stage2_reserve_boundary_s": stage2_stage4_reserve_s,
            "stage2_reserve_boundary_enforced": True,
            "stage2_unit_timeout_cap_s_effective": effective_stage2_cap_s,
            "stage2_unit_timeout_cap_reason": (
                _stage2_unit_timeout_cap_reason(args, effective_stage2_cap_s)),
            "unit_cost_tier": _job_cost_tier(job),
        })
        merge_result = _merge_jsonl_records(cert_path, cert_shard_path)
        cert_stage["cert_shard_merge"] = merge_result
        cert_shard_merges.append(merge_result)
        stages.append(cert_stage)
        stage2_soft_timeout_s = max(
            int(effective_stage2_cap_s or 0),
            int(stage2_stage4_reserve_applied_s or 0))
        if cert_stage["status"] == "timeout" and stage2_soft_timeout_s > 0:
            n_certified = _certified_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_cleared_fallback = _cleared_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_timeout_fallback = _timeout_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_complete_witness_fallback = \
                _complete_witness_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
            n_partial_journal_fallback = \
                _partial_journal_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
            n_stage4_candidates = (
                n_certified + n_cleared_fallback + n_timeout_fallback
                + n_complete_witness_fallback + n_partial_journal_fallback)
            if n_stage4_candidates > 0:
                cert_stage["capped_timeout_stage4_candidates_retained"] = True
                cert_stage["stage2_soft_timeout_stage4_candidates_retained"] = True
            else:
                first_row = _latest_cert_row(
                    cert_path, subject.benchmark_key, unit, path_function)
                overload_retry_jobs = _overload_path_function_retry_jobs(
                    job, first_row, jobs)
                if overload_retry_jobs:
                    jobs.extend(overload_retry_jobs)
                    stages.append({
                        "stage": "schedule-overload-path-functions",
                        "unit": unit,
                        "path_function": path_function,
                        "job_id": job.get("job_id"),
                        "status": "ok",
                        "added_jobs": len(overload_retry_jobs),
                        "path_functions": [
                            retry.get("path_function")
                            for retry in overload_retry_jobs
                        ],
                        "reason": (
                            "Stage-2 refused an overloaded unit without an "
                            "explicit path function; appended per-overload "
                            "certification jobs"),
                    })
                    consecutive_no_candidate_units = 0
                    continue
                counts_for_stop = _no_candidate_counts_against_stop(first_row)
                weak_requeue = _requeue_weak_stage2_suffix(
                    jobs, idx, first_row)
                if weak_requeue:
                    stages.append({
                        "stage": "requeue-after-weak-certification",
                        "unit": unit,
                        "job_id": job.get("job_id"),
                        **weak_requeue,
                    })
                if counts_for_stop:
                    stage2_no_candidate_evidence_units += 1
                    consecutive_no_candidate_units, max_consecutive_no_candidate_units = (
                        _record_no_candidate_unit(
                            consecutive_no_candidate_units,
                            max_consecutive_no_candidate_units))
                else:
                    diagnostic = (first_row or {}).get("driver_diagnostic") or {}
                    stage2_no_candidate_stop_skipped_units.append({
                        "unit": unit,
                        "path_function": path_function,
                        "bucket": (first_row or {}).get("bucket"),
                        "driver_diagnostic_tag": (
                            diagnostic.get("tag")
                            if isinstance(diagnostic, dict) else None),
                        "reason": (
                            "capped Stage-2 timeout ended without a Stage-4 "
                            "candidate, but the row is a tool/frontend/focus "
                            "failure and not evidence that remaining units "
                            "lack candidates"),
                    })
                    consecutive_no_candidate_units = 0
                partial_put = summarize_put_artifacts(case_dir / "put")
                if _should_stop_after_no_candidate_units(
                        consecutive_no_candidate_units,
                        partial_put,
                        args.no_candidate_stage2_unit_stop_n,
                        units_scheduled=len(jobs),
                        min_threshold_units=(
                            args.min_no_candidate_stage2_unit_stop_n),
                        pending_hinted_units=_pending_hinted_units(
                            jobs, units_attempted)):
                    early_stop_reason = _format_no_candidate_unit_stop(
                        consecutive_no_candidate_units)
                    result_status = "early-stop-no-output"
                    failure_reason = early_stop_reason
                    break
                if (stage4_candidate_units_attempted == 0
                        and _should_stop_after_no_output_stage2(
                        stages,
                        partial_put,
                        args.no_output_stage2_stop_s,
                        stage2_no_candidate_evidence_units,
                        len(jobs),
                        args.min_no_output_stage2_unit_stop_n)):
                    early_stop_reason = _format_stage2_no_output_stop(
                        _stage_wall_s(stages, "certify"))
                    result_status = "early-stop-no-output"
                    failure_reason = early_stop_reason
                    break
                continue
        elif cert_stage["status"] == "timeout":
            if _should_continue_after_stage2_no_output(
                    jobs, idx, n_stage4_candidates, cert_stage):
                first_row = _latest_cert_row(
                    cert_path, subject.benchmark_key, unit, path_function)
                weak_requeue = _requeue_weak_stage2_suffix(
                    jobs, idx, first_row)
                if weak_requeue:
                    stages.append({
                        "stage": "requeue-after-weak-certification",
                        "unit": unit,
                        "job_id": job.get("job_id"),
                        **weak_requeue,
                    })
                stage2_no_output_continuations.append({
                    "unit": unit,
                    "path_function": path_function,
                    "job_id": job.get("job_id"),
                    "status": cert_stage["status"],
                    "pending_units_after_this": _pending_units_after(jobs, idx),
                    "cert_shard_jsonl": str(cert_shard_path),
                    "cert_shard_merge": merge_result,
                    "bucket": (first_row or {}).get("bucket"),
                    "reason": (
                        "Stage-2 produced no Stage-4 candidate before timeout; "
                        "continuing to later units instead of subject-level "
                        "early stop"),
                })
                consecutive_no_candidate_units = 0
                continue
            result_status = "timeout"
            failure_reason = f"certify {unit}: timeout"
            break
        cert_stage_can_feed_stage4 = (
            cert_stage["status"] == "ok"
            or cert_stage.get("capped_timeout_stage4_candidates_retained") is True)
        if cert_stage["status"] == "oom":
            result_status = "oom"
            failure_reason = f"certify {unit}: oom"
            break
        if not cert_stage_can_feed_stage4:
            if _should_continue_after_stage2_no_output(
                    jobs, idx, n_stage4_candidates, cert_stage):
                first_row = _latest_cert_row(
                    cert_path, subject.benchmark_key, unit, path_function)
                diagnostic = (first_row or {}).get("driver_diagnostic") or {}
                weak_requeue = _requeue_weak_stage2_suffix(
                    jobs, idx, first_row)
                if weak_requeue:
                    stages.append({
                        "stage": "requeue-after-weak-certification",
                        "unit": unit,
                        "job_id": job.get("job_id"),
                        **weak_requeue,
                    })
                stage2_no_output_continuations.append({
                    "unit": unit,
                    "path_function": path_function,
                    "job_id": job.get("job_id"),
                    "status": cert_stage["status"],
                    "pending_units_after_this": _pending_units_after(jobs, idx),
                    "cert_shard_jsonl": str(cert_shard_path),
                    "cert_shard_merge": merge_result,
                    "bucket": (first_row or {}).get("bucket"),
                    "driver_diagnostic_tag": (
                        diagnostic.get("tag")
                        if isinstance(diagnostic, dict) else None),
                    "reason": (
                        "Stage-2 failed before yielding a Stage-4 candidate; "
                        "continuing to later units instead of subject-level "
                        "early stop"),
                })
                consecutive_no_candidate_units = 0
                continue
            result_status = "error"
            failure_reason = f"certify {unit}: {cert_stage['status']}"
            break
        if n_stage4_candidates is None:
            n_certified = _certified_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_cleared_fallback = _cleared_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_timeout_fallback = _timeout_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_complete_witness_fallback = _complete_witness_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_partial_journal_fallback = \
                _partial_journal_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
            n_stage4_candidates = (
                n_certified + n_cleared_fallback + n_timeout_fallback
                + n_complete_witness_fallback + n_partial_journal_fallback)
        if (n_stage4_candidates <= 0 and args.bounded_holds_retry
                and _remaining(deadline) >= args.min_remaining_s):
            first_row = _latest_cert_row(
                cert_path, subject.benchmark_key, unit, path_function)
            if _is_bounded_holds_retry_candidate(
                    first_row, args.bounded_holds_retry_max_initial_wall_s):
                retry_shard_path = _stage2_retry_cert_shard_path(
                    cert_path, idx, unit, "bounded-holds")
                retry_argv = _bounded_holds_retry_argv(
                    cert_argv,
                    max_tx=args.bounded_holds_retry_max_tx,
                    unwind=args.bounded_holds_retry_unwind,
                    out_path=retry_shard_path)
                retry_remaining_s = _remaining(deadline)
                retry_stage4_reserve_s = _stage4_reserve_s(args)
                _retry_budget_s, retry_stage4_reserve_applied_s = (
                    _stage2_budget_before_stage4(retry_remaining_s,
                                                 retry_stage4_reserve_s,
                                                 effective_stage2_cap_s))
                retry_argv = _certify_argv_for_remaining(
                    {
                        "certify_argv": retry_argv,
                        "certification_budget": {
                            "workdir": job["certification_budget"]["workdir"],
                        },
                    },
                    retry_remaining_s,
                    args.esbmc_run_timeout,
                    args.memlimit_gib,
                    effective_stage2_cap_s,
                    retry_shard_path,
                    args.stage_mem_fraction,
                    getattr(args, "esbmc", "") or None,
                    retry_stage4_reserve_s)
                retry_wrapper_timeout_s = _stage2_wrapper_timeout_s(
                    retry_remaining_s, args.wrapper_grace,
                    effective_stage2_cap_s, retry_stage4_reserve_s)
                retry_stage = run_command(
                    retry_argv,
                    retry_wrapper_timeout_s,
                    case_dir / "logs" / (
                        f"{idx:03d}-{_safe_name(unit)}-bounded-retry"))
                retry_stage.update({
                    "stage": "certify-bounded-holds-retry",
                    "unit": unit,
                    "path_function": path_function,
                    "job_id": job.get("job_id"),
                    "cert_shard_jsonl": str(retry_shard_path),
                    "cert_canonical_jsonl": str(cert_path),
                    "wrapper_timeout_s": round(retry_wrapper_timeout_s, 3),
                    "subject_remaining_before_stage2_s":
                        round(retry_remaining_s, 3),
                    "stage2_stage4_reserve_s":
                        retry_stage4_reserve_applied_s,
                    "bounded_holds_retry": {
                        "max_tx": args.bounded_holds_retry_max_tx,
                        "unwind": args.bounded_holds_retry_unwind,
                        "max_initial_wall_s":
                            args.bounded_holds_retry_max_initial_wall_s,
                    },
                })
                retry_merge_result = _merge_jsonl_records(
                    cert_path, retry_shard_path)
                retry_stage["cert_shard_merge"] = retry_merge_result
                cert_shard_merges.append(retry_merge_result)
                stages.append(retry_stage)
                retry_can_feed_stage4 = retry_stage["status"] == "ok"
                if retry_stage["status"] == "timeout" and max(
                        int(effective_stage2_cap_s or 0),
                        int(retry_stage4_reserve_applied_s or 0)) > 0:
                    retry_can_feed_stage4 = True
                    retry_stage[
                        "stage2_soft_timeout_stage4_candidate_probe"] = True
                if retry_can_feed_stage4:
                    n_certified = _certified_count(
                        cert_path, subject.benchmark_key, unit, path_function)
                    n_cleared_fallback = _cleared_concrete_fallback_count(
                        cert_path, subject.benchmark_key, unit, path_function)
                    n_timeout_fallback = _timeout_concrete_fallback_count(
                        cert_path, subject.benchmark_key, unit, path_function)
                    n_complete_witness_fallback = \
                        _complete_witness_concrete_fallback_count(
                            cert_path, subject.benchmark_key, unit,
                            path_function)
                    n_partial_journal_fallback = \
                        _partial_journal_concrete_fallback_count(
                            cert_path, subject.benchmark_key, unit,
                            path_function)
                    n_stage4_candidates = (
                        n_certified + n_cleared_fallback
                        + n_timeout_fallback + n_complete_witness_fallback
                        + n_partial_journal_fallback)
                    if n_stage4_candidates > 0:
                        retry_stage[
                            "stage2_soft_timeout_stage4_candidates_retained"] = True
        concrete_only_stage4 = _is_concrete_only_stage4(
            n_certified,
            n_cleared_fallback,
            n_timeout_fallback,
            n_complete_witness_fallback,
            n_partial_journal_fallback)
        pending_units_after_this = max(0, len(jobs) - idx)
        if n_stage4_candidates <= 0:
            first_row = _latest_cert_row(
                cert_path, subject.benchmark_key, unit, path_function)
            weak_requeue = _requeue_weak_stage2_suffix(
                jobs, idx, first_row)
            if weak_requeue:
                stages.append({
                    "stage": "requeue-after-weak-certification",
                    "unit": unit,
                    "job_id": job.get("job_id"),
                    **weak_requeue,
                })
            overload_retry_jobs = _overload_path_function_retry_jobs(
                job, first_row, jobs)
            if overload_retry_jobs:
                jobs.extend(overload_retry_jobs)
                stages.append({
                    "stage": "schedule-overload-path-functions",
                    "unit": unit,
                    "path_function": path_function,
                    "job_id": job.get("job_id"),
                    "status": "ok",
                    "added_jobs": len(overload_retry_jobs),
                    "path_functions": [
                        retry.get("path_function")
                        for retry in overload_retry_jobs
                    ],
                    "reason": (
                        "Stage-2 refused an overloaded unit without an "
                        "explicit path function; appended per-overload "
                        "certification jobs"),
                })
                consecutive_no_candidate_units = 0
                continue
            counts_for_stop = _no_candidate_counts_against_stop(first_row)
            if counts_for_stop:
                stage2_no_candidate_evidence_units += 1
                consecutive_no_candidate_units, max_consecutive_no_candidate_units = (
                    _record_no_candidate_unit(
                        consecutive_no_candidate_units,
                        max_consecutive_no_candidate_units))
            else:
                diagnostic = (first_row or {}).get("driver_diagnostic") or {}
                stage2_no_candidate_stop_skipped_units.append({
                    "unit": unit,
                    "path_function": path_function,
                    "bucket": (first_row or {}).get("bucket"),
                    "driver_diagnostic_tag": (
                        diagnostic.get("tag")
                        if isinstance(diagnostic, dict) else None),
                    "reason": (
                        "tool/frontend/focus failure is not evidence that "
                        "remaining units lack Stage-4 candidates"),
                })
                consecutive_no_candidate_units = 0
            partial_put = summarize_put_artifacts(case_dir / "put")
            if _should_stop_after_no_candidate_units(
                    consecutive_no_candidate_units,
                    partial_put,
                    args.no_candidate_stage2_unit_stop_n,
                    units_scheduled=len(jobs),
                    min_threshold_units=(
                        args.min_no_candidate_stage2_unit_stop_n),
                    pending_hinted_units=_pending_hinted_units(
                        jobs, units_attempted)):
                early_stop_reason = _format_no_candidate_unit_stop(
                    consecutive_no_candidate_units)
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            stop_s = args.no_output_stage2_stop_s
            if (stage4_candidate_units_attempted == 0
                    and _should_stop_after_no_output_stage2(
                    stages,
                    partial_put,
                    stop_s,
                    stage2_no_candidate_evidence_units,
                    len(jobs),
                    args.min_no_output_stage2_unit_stop_n)):
                early_stop_reason = _format_stage2_no_output_stop(
                    _stage_wall_s(stages, "certify"))
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            continue
        consecutive_no_candidate_units = 0
        partial_put = summarize_put_artifacts(case_dir / "put")
        remaining_before_stage4 = _remaining(deadline)
        if _should_skip_low_budget_timeout_only_stage4(
                remaining_before_stage4,
                args.min_timeout_only_stage4_s,
                n_certified,
                n_cleared_fallback,
                n_timeout_fallback,
                n_complete_witness_fallback,
                n_partial_journal_fallback):
            skip_reason = _format_low_budget_timeout_only_skip(
                remaining_before_stage4, args.min_timeout_only_stage4_s)
            low_budget_timeout_only_stage4_skips.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "remaining_s": round(remaining_before_stage4, 3),
                "threshold_s": args.min_timeout_only_stage4_s,
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                    n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                    n_partial_journal_fallback,
                "raw_before_skip": partial_put.get("raw") or 0,
                "valid_before_skip": partial_put.get("valid") or 0,
                "reason": skip_reason,
                "pending_stage4_candidate": True,
            })
            consecutive_no_candidate_units = 0
            continue
        stage4_candidate_units_attempted += 1
        if _should_skip_concrete_only_after_puts(
                partial_put,
                args.skip_concrete_only_after_put_valid,
                n_certified,
                n_cleared_fallback,
                n_timeout_fallback,
                n_complete_witness_fallback,
                n_partial_journal_fallback):
            put_valid_before_skip = partial_put.get("put_valid") or 0
            skip_reason = _format_put_saturated_concrete_only_skip(
                put_valid_before_skip,
                args.skip_concrete_only_after_put_valid)
            put_saturated_concrete_only_stage4_skips.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "remaining_s": round(remaining_before_stage4, 3),
                "threshold_put_valid":
                    args.skip_concrete_only_after_put_valid,
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                    n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                    n_partial_journal_fallback,
                "raw_before_skip": partial_put.get("raw") or 0,
                "valid_before_skip": partial_put.get("valid") or 0,
                "put_valid_before_skip": put_valid_before_skip,
                "reason": skip_reason,
            })
            continue
        if _should_skip_concrete_only_after_any_valid(
                partial_put,
                getattr(args, "skip_concrete_only_after_any_valid", True),
                n_certified,
                n_cleared_fallback,
                n_timeout_fallback,
                n_complete_witness_fallback,
                n_partial_journal_fallback):
            valid_before_skip = int(partial_put.get("valid") or 0)
            put_valid_before_skip = int(partial_put.get("put_valid") or 0)
            skip_reason = _format_valid_saturated_concrete_only_skip(
                valid_before_skip, put_valid_before_skip)
            valid_saturated_concrete_only_stage4_skips.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "remaining_s": round(remaining_before_stage4, 3),
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                    n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                    n_partial_journal_fallback,
                "raw_before_skip": partial_put.get("raw") or 0,
                "valid_before_skip": valid_before_skip,
                "put_valid_before_skip": put_valid_before_skip,
                "reason": skip_reason,
            })
            continue
        if _should_skip_low_budget_concrete_only_stage4(
                partial_put,
                remaining_before_stage4,
                args.min_concrete_only_stage4_s,
                n_certified,
                n_cleared_fallback,
                n_timeout_fallback,
                n_complete_witness_fallback,
                n_partial_journal_fallback):
            skip_reason = _format_low_budget_concrete_only_skip(
                remaining_before_stage4, args.min_concrete_only_stage4_s)
            low_budget_concrete_only_stage4_skips.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "remaining_s": round(remaining_before_stage4, 3),
                "threshold_s": args.min_concrete_only_stage4_s,
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                    n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                    n_partial_journal_fallback,
                "raw_before_skip": partial_put.get("raw") or 0,
                "valid_before_skip": partial_put.get("valid") or 0,
                "reason": skip_reason,
            })
            continue
        if _remaining(deadline) < args.min_remaining_s:
            result_status = "budget-exhausted"
            failure_reason = "case budget exhausted before Stage 4"
            break
        mem_wait = wait_for_mem_budget(
            args.memlimit_gib,
            deadline,
            fraction=args.stage_mem_fraction,
            poll_s=args.mem_wait_poll_s,
            min_remaining_s=args.min_remaining_s)
        if mem_wait["waited"] or mem_wait["status"] != "ok":
            mem_wait.update({"unit": unit, "before_stage": "put"})
            stages.append(mem_wait)
        if mem_wait["status"] != "ok":
            result_status = "budget-exhausted"
            failure_reason = (
                f"insufficient memory before put {unit}: need "
                f"MemAvailable >= "
                f"{mem_wait['required_mem_available_gib']}GiB for "
                f"{args.memlimit_gib}GiB at "
                f"{args.stage_mem_fraction:.0%}; have "
                f"{mem_wait['mem_available_gib']}GiB")
            break
        put_root = case_dir / "put" / _safe_name(job.get("job_id") or unit)
        put_generation_budget_s = _remaining(deadline)
        stage4_budget_capped_for_concrete_only = False
        concrete_only_cap_s = concrete_only_stage4_timeout_cap_s
        if (concrete_only_stage4 and pending_units_after_this > 0
                and concrete_only_cap_s > 0
                and put_generation_budget_s > float(concrete_only_cap_s)):
            concrete_only_stage4_budget_caps.append({
                "unit": unit,
                "job_id": job.get("job_id"),
                "original_generation_budget_s": round(put_generation_budget_s, 3),
                "capped_generation_budget_s": concrete_only_cap_s,
                "pending_units_after_this": pending_units_after_this,
                "certified_regions_for_unit": n_certified,
                "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                    n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                    n_partial_journal_fallback,
            })
            put_generation_budget_s = float(concrete_only_cap_s)
            stage4_budget_capped_for_concrete_only = True
        put_argv = _put_argv(cert_path,
                             unit,
                             subject.benchmark_key,
                             put_root,
                             put_generation_budget_s,
                             args.memlimit_gib,
                             args.forge_timeout,
                             path_function,
                             getattr(args, "esbmc", "") or None)
        # Stage 4's ESBMC/emission work is budgeted by --timeout and the
        # remaining case deadline passed above.  put_all.py then runs Foundry
        # as a second, refutation-only replay oracle; let that finish outside
        # the generation timeout so a slow replay does not reclassify completed
        # generation as a tool timeout.
        put_wrapper_timeout_s = (put_generation_budget_s + args.wrapper_grace
                                 + 2 * args.forge_timeout)
        put_stage = run_command(put_argv,
                                put_wrapper_timeout_s,
                                case_dir / "logs" / f"{idx:03d}-{_safe_name(unit)}-put")
        put_stage.update({
            "stage": "put",
            "unit": unit,
            "path_function": path_function,
            "generation_budget_s": round(put_generation_budget_s, 3),
            "foundry_replay_outside_generation_timeout": True,
            "foundry_replay_timeout_s_per_run": args.forge_timeout,
            "certified_regions_for_unit": n_certified,
            "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
            "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
            "complete_witness_concrete_fallbacks_for_unit":
                n_complete_witness_fallback,
            "partial_journal_concrete_fallbacks_for_unit":
                n_partial_journal_fallback,
            "stage4_candidates_for_unit": n_stage4_candidates,
            "concrete_only_stage4": concrete_only_stage4,
            "pending_units_after_this": pending_units_after_this,
            "concrete_only_stage4_timeout_cap_s": concrete_only_cap_s,
            "stage4_budget_capped_for_concrete_only":
                stage4_budget_capped_for_concrete_only,
            "put_out_root": str(put_root),
        })
        stages.append(put_stage)
        if put_stage["status"] in ("timeout", "oom"):
            if (put_stage["status"] == "timeout" and concrete_only_stage4
                    and pending_units_after_this > 0):
                partial_put = summarize_put_artifacts(case_dir / "put")
                if int(partial_put.get("raw") or 0) <= 0:
                    concrete_only_stage4_soft_failures.append({
                        "unit": unit,
                        "job_id": job.get("job_id"),
                        "status": put_stage["status"],
                        "generation_budget_s":
                            round(put_generation_budget_s, 3),
                        "budget_capped":
                            stage4_budget_capped_for_concrete_only,
                        "pending_units_after_this": pending_units_after_this,
                        "certified_regions_for_unit": n_certified,
                        "cleared_concrete_fallbacks_for_unit":
                            n_cleared_fallback,
                        "timeout_concrete_fallbacks_for_unit":
                            n_timeout_fallback,
                        "complete_witness_concrete_fallbacks_for_unit":
                            n_complete_witness_fallback,
                        "partial_journal_concrete_fallbacks_for_unit":
                            n_partial_journal_fallback,
                        "raw_after_timeout": partial_put.get("raw") or 0,
                        "valid_after_timeout": partial_put.get("valid") or 0,
                        "pending_stage4_candidate": True,
                    })
                    consecutive_no_candidate_units = 0
                    continue
            result_status = put_stage["status"]
            failure_reason = f"put {unit}: {put_stage['status']}"
            break
        partial_put = summarize_put_artifacts(case_dir / "put")
        if _should_stop_after_zero_output_stage4(
                stages, partial_put, args.zero_output_stage4_stop_s):
            early_stop_reason = _format_stage4_no_output_stop(
                _stage_wall_s(stages, "put"))
            result_status = "early-stop-no-output"
            failure_reason = early_stop_reason
            break

    cert_summary = summarize_certification(cert_path)
    put_summary = summarize_put_artifacts(case_dir / "put")
    if (args.final_deploy_concrete_fallback and put_summary["valid"] <= 0
            and result_status not in {"error"}):
        fallback_stage = emit_no_unit_deploy_fallback(
            subject,
            case_dir,
            schedule,
            args.forge_timeout,
            force=True,
            reason=(
                "final safety-net concrete replay: Stage2/Stage4 produced no "
                "valid reference artifact for this target contract; this is "
                "kept as concrete quality debt, not as a PUT/R1/R2 claim"),
            out_name="final_deploy_concrete_fallback")
        fallback_stage["trigger"] = "no-valid-after-stage4"
        fallback_stage["valid_before_fallback"] = put_summary["valid"]
        fallback_stage["raw_before_fallback"] = put_summary["raw"]
        stages.append(fallback_stage)
        put_summary = summarize_put_artifacts(case_dir / "put")
    wall_total_s = round(time.monotonic() - start, 3)
    completion_status = result_status
    partial_failure_reason = None
    budget_exhausted = completion_status == "budget-exhausted"
    early_stopped_no_output = completion_status == "early-stop-no-output"
    if budget_exhausted and put_summary["raw"] > 0:
        result_status = "ok"
    if early_stopped_no_output:
        result_status = "no-output"
    if put_summary["valid"] > 0 and result_status != "ok":
        partial_failure_reason = failure_reason
        result_status = "ok"
        failure_reason = None
    if result_status == "ok" and put_summary["raw"] == 0:
        result_status = "no-output"
        failure_reason = _no_output_reason(cert_summary)
    stage2_wall_s = round(_stage_wall_s(stages, "certify"), 3)
    stage4_wall_s = round(_stage_wall_s(stages, "put"), 3)
    generation_wall_s = round(
        stage2_wall_s + put_summary["stage4_generation_wall_s"], 3)
    stage2_capped_timeout_units = [
        stage.get("unit")
        for stage in stages
        if (stage.get("stage") == "certify"
            and stage.get("status") == "timeout"
            and int(stage.get("stage2_unit_timeout_cap_s_effective") or 0) > 0)
    ]
    no_unit_deploy_fallback_stages = [
        stage for stage in stages
        if stage.get("stage") == "no-unit-deploy-fallback"
    ]
    overload_path_function_stages = [
        stage for stage in stages
        if stage.get("stage") == "schedule-overload-path-functions"
    ]
    row = {
        "key": f"gen:veriput:{subject_id}",
        "stage": "gen_veriput",
        "schema": "veriput-rq1-result-row/v1",
        "ts": round(time.time(), 3),
        "generated_at": _utc_now(),
        "host": socket.gethostname(),
        "n_concurrent": args.jobs,
        "mem_budget_mb": args.memlimit_gib * 1024,
        "tool_timeout_s": args.timeout,
        "esbmc_run_timeout_s": args.esbmc_run_timeout,
        "resume_quality_floor":
            getattr(args, "resume_quality_floor", "no-valid"),
        "stage2_unit_timeout_cap_s": args.stage2_unit_timeout_cap_s,
        "adaptive_stage2_unit_timeout_cap_s":
            args.adaptive_stage2_unit_timeout_cap_s,
        "stage2_stage4_reserve_s": _stage4_reserve_s(args),
        "stage4_reserve_boundary_enforced": True,
        "adaptive_stage2_many_unit_threshold":
            ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
        "adaptive_stage2_expensive_tier_threshold":
            ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
        "cleared_concrete_fallbacks_enabled": True,
        "timeout_concrete_fallbacks_enabled": True,
        "complete_witness_concrete_fallbacks_enabled": True,
        "partial_journal_concrete_fallbacks_enabled": True,
        "no_unit_deploy_fallback_enabled": True,
        "no_unit_deploy_fallback_count":
            len(no_unit_deploy_fallback_stages),
        "no_unit_deploy_fallback_statuses": [
            stage.get("status") for stage in no_unit_deploy_fallback_stages
        ],
        "no_unit_deploy_fallback_paths": [
            stage.get("put_out_root") for stage in no_unit_deploy_fallback_stages
            if stage.get("put_out_root")
        ],
        "no_output_stage2_stop_s": args.no_output_stage2_stop_s,
        "min_no_output_stage2_unit_stop_n":
            args.min_no_output_stage2_unit_stop_n,
        "no_candidate_stage2_unit_stop_n": args.no_candidate_stage2_unit_stop_n,
        "min_no_candidate_stage2_unit_stop_n":
            args.min_no_candidate_stage2_unit_stop_n,
        "max_consecutive_no_candidate_units": max_consecutive_no_candidate_units,
        "stage2_no_candidate_evidence_units":
            stage2_no_candidate_evidence_units,
        "stage2_no_candidate_stop_skipped_units":
            stage2_no_candidate_stop_skipped_units,
        "stage2_no_candidate_stop_skipped_unit_count":
            len(stage2_no_candidate_stop_skipped_units),
        "stage2_no_output_continuations": stage2_no_output_continuations,
        "stage2_no_output_continuation_count":
            len(stage2_no_output_continuations),
        "stage2_capped_timeout_units": stage2_capped_timeout_units,
        "stage2_capped_timeout_unit_count": len(stage2_capped_timeout_units),
        "overload_path_function_retry_count": sum(
            int(stage.get("added_jobs") or 0)
            for stage in overload_path_function_stages),
        "overload_path_function_retry_units": [
            stage.get("unit") for stage in overload_path_function_stages
        ],
        "stage4_candidate_units_attempted": stage4_candidate_units_attempted,
        "ce_replay_manifest_paths": [
            str(path) for path in _candidate_manifest_paths(
                getattr(args, "ce_replay_manifest", []))
        ],
        "ce_replay_only": bool(getattr(args, "ce_replay_only", False)),
        "ce_replay_candidates_discovered": len(ce_replay_candidates),
        "ce_replay_candidates_attempted": len(ce_replay_stages),
        "ce_replay_candidates_promoted": sum(
            1 for stage in ce_replay_stages
            if (stage.get("candidate_promotion") or {}).get("promoted")),
        "ce_replay_candidate_rejections": ce_replay_rejected,
        "ce_replay_theory_delta": 0,
        "zero_output_stage4_stop_s": args.zero_output_stage4_stop_s,
        "min_concrete_only_stage4_s": args.min_concrete_only_stage4_s,
        "min_timeout_only_stage4_s": args.min_timeout_only_stage4_s,
        "skip_concrete_only_after_put_valid":
            args.skip_concrete_only_after_put_valid,
        "skip_concrete_only_after_any_valid":
            getattr(args, "skip_concrete_only_after_any_valid", True),
        "low_budget_concrete_only_stage4_skips":
            low_budget_concrete_only_stage4_skips,
        "low_budget_concrete_only_stage4_skip_count":
            len(low_budget_concrete_only_stage4_skips),
        "low_budget_timeout_only_stage4_skips":
            low_budget_timeout_only_stage4_skips,
        "low_budget_timeout_only_stage4_skip_count":
            len(low_budget_timeout_only_stage4_skips),
        "put_saturated_concrete_only_stage4_skips":
            put_saturated_concrete_only_stage4_skips,
        "put_saturated_concrete_only_stage4_skip_count":
            len(put_saturated_concrete_only_stage4_skips),
        "valid_saturated_concrete_only_stage4_skips":
            valid_saturated_concrete_only_stage4_skips,
        "valid_saturated_concrete_only_stage4_skip_count":
            len(valid_saturated_concrete_only_stage4_skips),
        "concrete_only_stage4_timeout_cap_s":
            concrete_only_stage4_timeout_cap_s,
        "concrete_only_stage4_budget_caps":
            concrete_only_stage4_budget_caps,
        "concrete_only_stage4_budget_cap_count":
            len(concrete_only_stage4_budget_caps),
        "concrete_only_stage4_soft_failures":
            concrete_only_stage4_soft_failures,
        "concrete_only_stage4_soft_failure_count":
            len(concrete_only_stage4_soft_failures),
        "early_stop_reason": early_stop_reason,
        "wall_cap_s": args.timeout + args.wrapper_grace,
        "status": result_status,
        "completion_status": completion_status,
        "budget_exhausted": budget_exhausted,
        "reason": failure_reason,
        "partial_failure_reason": partial_failure_reason,
        "subject_id": subject_id,
        "benchmark": target_row["benchmark"],
        "dataset": dataset_label,
        "contract": target_row.get("contract"),
        "raw": put_summary["raw"],
        "valid": put_summary["valid"],
        "put_raw": put_summary["put_raw"],
        "put_valid": put_summary["put_valid"],
        "concrete_raw": put_summary["concrete_raw"],
        "concrete_valid": put_summary["concrete_valid"],
        "quality_bucket": put_summary["quality_bucket"],
        "valid_put_with_R1": put_summary["valid_put_with_R1"],
        "valid_put_with_R2": put_summary["valid_put_with_R2"],
        "valid_put_with_R1_or_R2": put_summary["valid_put_with_R1_or_R2"],
        "valid_put_without_R1R2": put_summary["valid_put_without_R1R2"],
        "raw_tests": put_summary["raw_tests"],
        "valid_tests": put_summary["valid_tests"],
        "oracle_class_counts": put_summary["oracle_class_counts"],
        "oracle_class_combo_counts": put_summary["oracle_class_combo_counts"],
        "assertion_oracles": put_summary["assertion_oracles"],
        "stage4_storage_layout_counts":
            put_summary["stage4_storage_layout_counts"],
        "put_json_count": put_summary["put_json_count"],
        "cert_bucket_counts": cert_summary["bucket_counts"],
        "cert_exit_counts": cert_summary["exit_counts"],
        "cert_witness_counts": cert_summary["witness_counts"],
        "cert_timed_out_units": cert_summary["timed_out_units"],
        "cert_oom_units": cert_summary["oom_units"],
        "driver_refusal_tags": cert_summary["driver_refusal_tags"],
        "driver_diagnostic_tags": cert_summary["driver_diagnostic_tags"],
        "units_attempted": units_attempted,
        "units_scheduled": len(jobs),
        "schedule_summary": schedule.get("summary") or {},
        "schedule_skipped_rows": schedule.get("skipped_rows") or [],
        "schedule_no_unit_rows": schedule.get("no_unit_rows") or [],
        "schedule_skipped_units": schedule.get("skipped_units") or [],
        "generation_wall_s": generation_wall_s,
        "stage2_wall_s": stage2_wall_s,
        "stage4_wall_s": stage4_wall_s,
        "stage4_generation_wall_s": put_summary["stage4_generation_wall_s"],
        "stage4_emission_wall_s": put_summary["stage4_emission_wall_s"],
        "foundry_replay_wall_s": put_summary["foundry_replay_wall_s"],
        "put_all_wall_s": put_summary["put_all_wall_s"],
        "foundry_replay_outside_generation_timeout": True,
        "wall": wall_total_s,
        "wall_total_s": wall_total_s,
        "maxrss_mb": max(
            [stage.get("maxrss_proc_mb") or 0.0 for stage in stages] or [0.0]),
        "artifact_root": str(case_dir),
        "result_json": str(case_dir / "result.json"),
        "cert_jsonl": str(cert_path),
        "cert_shard_merges": cert_shard_merges,
        "cert_shard_merge_count": len(cert_shard_merges),
        "cert_shard_rows_merged": sum(
            item.get("merged") or 0 for item in cert_shard_merges),
        "cert_shard_invalid_rows": sum(
            item.get("invalid") or 0 for item in cert_shard_merges),
        "put_summary_paths": put_summary["summary_paths"],
        "raw_artifacts_retained": put_summary["raw"] > 0,
        "valid_artifacts_retained": put_summary["valid"] > 0,
        "recipe_version": STRONG_RECIPE_VERSION,
    }
    row.update(_bounded_holds_retry_policy(args))
    # A replay-only invocation is a transaction over explicit CE candidates.
    # It must not turn a neighbouring .redo/.incomplete artifact into formal
    # credit when every candidate was rejected. Existing canonical artifacts
    # are still summarized normally; only cross-directory stale adoption is
    # disabled for this isolated entry point.
    stale_row = None
    if not getattr(args, "ce_replay_only", False):
        stale_row = _best_stale_artifact_row(target_row, dataset_label, case_dir,
                                             row)
        row = _adopt_stale_artifacts(row, stale_row)
    detail = {
        "schema": "veriput-rq1-case-result/v1",
        "row": row,
        "target": target_row,
        "subject": subject.to_record(),
        "schedule": {
            "path": str(case_dir / "unit-schedule.json"),
            "summary": schedule.get("summary") or {},
        },
        "stages": stages,
        "ce_replay": {
            "schema": "veriput-rq1-ce-replay-accounting/v1",
            "candidates_discovered": len(ce_replay_candidates),
            "candidates_attempted": len(ce_replay_stages),
            "candidates_promoted": sum(
                1 for stage in ce_replay_stages
                if (stage.get("candidate_promotion") or {}).get("promoted")),
            "rejections": ce_replay_rejected,
            "theory_delta": 0,
            "formal_results_written_only_after_gates": True,
        },
        "certification": cert_summary,
        "put": put_summary,
        "stale_artifact_adoption": {
            "adopted": bool(row.get("adopted_stale_artifacts")),
            "source": row.get("stale_artifact_root"),
            "source_result_json": row.get("stale_result_json"),
            "source_quality_bucket": row.get("stale_quality_bucket"),
            "disabled_for_ce_replay_only": bool(
                getattr(args, "ce_replay_only", False)),
        },
    }
    _write_json(case_dir / "result.json", detail)
    return row, detail


def run_selected_subjects(rows: list[dict], dataset_label: str, journal: Path,
                          done: dict[str, dict], args) -> int:
    selected = [row for row in rows
                if _run_key(
                    row["subject_id"],
                    ce_collection_only=args.ce_collection_only) not in done]
    if not selected:
        return 0
    if args.jobs <= 1:
        attempted = 0
        for target_row in selected:
            print(f"[rq1] {dataset_label} {target_row['subject_id']} "
                  f"contract={target_row.get('contract')}", flush=True)
            row, _detail = run_subject(target_row, dataset_label, args)
            _append_jsonl(journal, row)
            write_dataset_manifest(Path(args.result_root), dataset_label, journal)
            attempted += 1
            print(f"[rq1] -> status={row['status']} raw={row['raw']} "
                  f"valid={row['valid']} put={row['put_valid']}/"
                  f"{row['put_raw']} concrete={row['concrete_valid']}/"
                  f"{row['concrete_raw']} bucket={row.get('quality_bucket')} "
                  f"wall={row['wall_total_s']}s",
                  flush=True)
        return attempted

    attempted = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {}
        for target_row in selected:
            print(f"[rq1] queued {dataset_label} {target_row['subject_id']} "
                  f"contract={target_row.get('contract')}", flush=True)
            futures[executor.submit(run_subject, target_row, dataset_label, args)] = target_row
        for future in as_completed(futures):
            target_row = futures[future]
            try:
                row, _detail = future.result()
            except Exception as exc:  # Subject-level fail-soft.
                now = round(time.time(), 3)
                row = {
                    "key": f"gen:veriput:{target_row['subject_id']}",
                    "stage": "gen_veriput",
                    "schema": "veriput-rq1-result-row/v1",
                    "ts": now,
                    "generated_at": _utc_now(),
                    "host": socket.gethostname(),
                    "n_concurrent": args.jobs,
                    "mem_budget_mb": args.memlimit_gib * 1024,
                    "tool_timeout_s": args.timeout,
                    "esbmc_run_timeout_s": args.esbmc_run_timeout,
                    "resume_quality_floor":
                        getattr(args, "resume_quality_floor", "no-valid"),
                    "stage2_unit_timeout_cap_s": args.stage2_unit_timeout_cap_s,
                    "adaptive_stage2_unit_timeout_cap_s":
                        args.adaptive_stage2_unit_timeout_cap_s,
                    "stage2_stage4_reserve_s":
                        _stage4_reserve_s(args),
                    "adaptive_stage2_many_unit_threshold":
                        ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
                    "adaptive_stage2_expensive_tier_threshold":
                        ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
                    "no_unit_deploy_fallback_enabled": True,
                    "no_unit_deploy_fallback_count": 0,
                    "no_unit_deploy_fallback_statuses": [],
                    "no_unit_deploy_fallback_paths": [],
                    "wall_cap_s": args.timeout + args.wrapper_grace,
                    "status": "error",
                    "completion_status": "error",
                    "budget_exhausted": False,
                    "reason": f"runner exception: {exc}",
                    "subject_id": target_row["subject_id"],
                    "benchmark": target_row.get("benchmark"),
                    "dataset": dataset_label,
                    "contract": target_row.get("contract"),
                    "raw": 0,
                    "valid": 0,
                    "put_raw": 0,
                    "put_valid": 0,
                    "concrete_raw": 0,
                    "concrete_valid": 0,
                    "quality_bucket": "no-valid",
                    "valid_put_with_R1": 0,
                    "valid_put_with_R2": 0,
                    "valid_put_with_R1_or_R2": 0,
                    "valid_put_without_R1R2": 0,
                    "raw_tests": [],
                    "valid_tests": [],
                    "oracle_class_counts": {},
                    "oracle_class_combo_counts": {},
                    "assertion_oracles": [],
                    "put_json_count": 0,
                    "cert_bucket_counts": {},
                    "cert_exit_counts": {},
                    "cert_witness_counts": {},
                    "cert_timed_out_units": [],
                    "cert_oom_units": [],
                    "units_attempted": [],
                    "units_scheduled": 0,
                    "stage2_capped_timeout_units": [],
                    "stage2_capped_timeout_unit_count": 0,
                    "overload_path_function_retry_count": 0,
                    "overload_path_function_retry_units": [],
                    "stage4_candidate_units_attempted": 0,
                    "zero_output_stage4_stop_s": args.zero_output_stage4_stop_s,
                    "min_concrete_only_stage4_s":
                        args.min_concrete_only_stage4_s,
                    "min_timeout_only_stage4_s": args.min_timeout_only_stage4_s,
                    "skip_concrete_only_after_put_valid":
                        args.skip_concrete_only_after_put_valid,
                    "skip_concrete_only_after_any_valid":
                        getattr(args, "skip_concrete_only_after_any_valid", True),
                    "low_budget_concrete_only_stage4_skips": [],
                    "low_budget_concrete_only_stage4_skip_count": 0,
                    "low_budget_timeout_only_stage4_skips": [],
                    "low_budget_timeout_only_stage4_skip_count": 0,
                    "put_saturated_concrete_only_stage4_skips": [],
                    "put_saturated_concrete_only_stage4_skip_count": 0,
                    "valid_saturated_concrete_only_stage4_skips": [],
                    "valid_saturated_concrete_only_stage4_skip_count": 0,
                    "concrete_only_stage4_timeout_cap_s":
                        getattr(args,
                                "concrete_only_stage4_timeout_cap_s",
                                DEFAULT_CONCRETE_ONLY_STAGE4_TIMEOUT_CAP_S),
                    "concrete_only_stage4_budget_caps": [],
                    "concrete_only_stage4_budget_cap_count": 0,
                    "concrete_only_stage4_soft_failures": [],
                    "concrete_only_stage4_soft_failure_count": 0,
                    "generation_wall_s": 0.0,
                    "stage2_wall_s": 0.0,
                    "stage4_wall_s": 0.0,
                    "stage4_generation_wall_s": 0.0,
                    "stage4_emission_wall_s": 0.0,
                    "foundry_replay_wall_s": 0.0,
                    "put_all_wall_s": 0.0,
                    "foundry_replay_outside_generation_timeout": True,
                    "wall": 0.0,
                    "wall_total_s": 0.0,
                    "maxrss_mb": 0.0,
                    "artifact_root": None,
                    "result_json": None,
                    "cert_jsonl": None,
                    "cert_shard_merges": [],
                    "cert_shard_merge_count": 0,
                    "cert_shard_rows_merged": 0,
                    "cert_shard_invalid_rows": 0,
                    "put_summary_paths": [],
                    "raw_artifacts_retained": False,
                    "valid_artifacts_retained": False,
                    "recipe_version": STRONG_RECIPE_VERSION,
                }
                row.update(_bounded_holds_retry_policy(args))
                case_dir = (Path(args.result_root) / dataset_label / "subjects" /
                            _safe_name(target_row["subject_id"]))
                stale_row = _best_stale_artifact_row(
                    target_row, dataset_label, case_dir, row)
                row = _adopt_stale_artifacts(row, stale_row)
                if _write_normalized_case_result(
                        case_dir,
                        row,
                        reason=(
                            "subject-level runner exception recovered a "
                            "stronger retained Stage-4 artifact row")):
                    row["normalized_subject_result_json"] = True
            _append_jsonl(journal, row)
            write_dataset_manifest(Path(args.result_root), dataset_label, journal)
            attempted += 1
            print(f"[rq1] done {target_row['subject_id']} -> "
                  f"status={row['status']} raw={row['raw']} valid={row['valid']} "
                  f"put={row['put_valid']}/{row['put_raw']} "
                  f"concrete={row['concrete_valid']}/{row['concrete_raw']} "
                  f"bucket={row.get('quality_bucket')} wall={row['wall_total_s']}s",
                  flush=True)
    return attempted


def write_dataset_manifest(root: Path, dataset_label: str, journal: Path) -> None:
    latest = _latest_rows(journal)
    status = Counter(str(row.get("status") or "<missing>") for row in latest.values())
    quality = Counter(
        str(row.get("quality_bucket") or _legacy_quality_bucket(row))
        for row in latest.values())
    doc = {
        "schema": "veriput-rq1-dataset-manifest/v1",
        "generated_at": _utc_now(),
        "dataset": dataset_label,
        "journal": str(journal),
        "summary": {
            "rows": len(latest),
            "raw": sum(row.get("raw") or 0 for row in latest.values()),
            "valid": sum(row.get("valid") or 0 for row in latest.values()
                         if row.get("valid") is not None),
            "put_raw": sum(row.get("put_raw") or 0 for row in latest.values()),
            "put_valid": sum(row.get("put_valid") or 0 for row in latest.values()),
            "concrete_raw": sum(row.get("concrete_raw") or 0 for row in latest.values()),
            "concrete_valid": sum(row.get("concrete_valid") or 0
                                  for row in latest.values()),
            "valid_put_with_R1": sum(row.get("valid_put_with_R1") or 0
                                     for row in latest.values()),
            "valid_put_with_R2": sum(row.get("valid_put_with_R2") or 0
                                     for row in latest.values()),
            "valid_put_with_R1_or_R2": sum(
                row.get("valid_put_with_R1_or_R2") or 0
                for row in latest.values()),
            "valid_put_without_R1R2": sum(
                row.get("valid_put_without_R1R2") or 0
                for row in latest.values()),
            "status": dict(sorted(status.items())),
            "quality_bucket": dict(sorted(quality.items())),
        },
    }
    manifest_name = (
        "ce-collection-manifest.json"
        if journal.name == "ce-collection-results.jsonl" else "manifest.json")
    _write_json(root / dataset_label / manifest_name, doc)


def build_dry_run(args) -> dict:
    dataset_label, rows = target_rows(Path(args.veriput_root), args.benchmark,
                                      args.subject_id, args.limit, args.order)
    doc = {
        "schema": "veriput-rq1-dry-run/v1",
        "generated_at": _utc_now(),
        "dataset": dataset_label,
        "result_root": args.result_root,
        "ast_cache_root": args.ast_cache_root,
        "timeout_s": args.timeout,
        "esbmc_run_timeout_s": args.esbmc_run_timeout,
        "stage2_unit_timeout_cap_s": args.stage2_unit_timeout_cap_s,
        "adaptive_stage2_unit_timeout_cap_s":
            args.adaptive_stage2_unit_timeout_cap_s,
        "stage2_stage4_reserve_s": _stage4_reserve_s(args),
        "adaptive_stage2_many_unit_threshold":
            ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
        "adaptive_stage2_expensive_tier_threshold":
            ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
        "no_output_stage2_stop_s": args.no_output_stage2_stop_s,
        "min_no_output_stage2_unit_stop_n":
            args.min_no_output_stage2_unit_stop_n,
        "no_candidate_stage2_unit_stop_n": args.no_candidate_stage2_unit_stop_n,
        "min_no_candidate_stage2_unit_stop_n":
            args.min_no_candidate_stage2_unit_stop_n,
        "zero_output_stage4_stop_s": args.zero_output_stage4_stop_s,
        "min_concrete_only_stage4_s": args.min_concrete_only_stage4_s,
        "min_timeout_only_stage4_s": args.min_timeout_only_stage4_s,
        "skip_concrete_only_after_put_valid":
            args.skip_concrete_only_after_put_valid,
        "skip_concrete_only_after_any_valid":
            getattr(args, "skip_concrete_only_after_any_valid", True),
        "resume_quality_floor":
            getattr(args, "resume_quality_floor", "no-valid"),
        "memlimit_gib": args.memlimit_gib,
        "jobs": args.jobs,
        "stage_mem_fraction": args.stage_mem_fraction,
        "mem_wait_poll_s": args.mem_wait_poll_s,
        "order": args.order,
        "subjects": [{
            "subject_id": row.get("subject_id"),
            "benchmark": row.get("benchmark"),
            "contract": row.get("contract"),
            "units_hint": row.get("units_hint") or [],
        } for row in rows],
        "ce_replay_manifest_paths": [
            str(path) for path in _candidate_manifest_paths(
                getattr(args, "ce_replay_manifest", []))
        ],
        "ce_replay_only": bool(getattr(args, "ce_replay_only", False)),
        "ce_replay_theory_delta": 0,
    }
    doc.update(_bounded_holds_retry_policy(args))
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--veriput-root", default=str(DEFAULT_VERIPUT_ROOT))
    ap.add_argument("--benchmark", required=True,
                    choices=sorted(TARGET_BENCHMARK_ARG),
                    help="peer182, bugfix124, or real203/stress203")
    ap.add_argument("--subject-id", action="append", default=[],
                    help="restrict to one prepared subject id. Repeatable")
    ap.add_argument("--unit", action="append", default=[],
                    help="restrict selected subjects to this public/external "
                         "unit name. Repeatable")
    ap.add_argument("--limit", type=int, default=0,
                    help="run only the first N selected target subjects")
    ap.add_argument("--order", choices=("fast-first", "dataset"),
                    default="fast-first",
                    help="subject order before --limit. fast-first sorts by "
                         "prepared flat.sol size to get early throughput")
    ap.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    ap.add_argument("--ast-cache-root", default=str(DEFAULT_AST_CACHE_ROOT))
    ap.add_argument("--ce-collection-only", action="store_true",
                    help="collect at most one bounded 60-second CE artifact "
                         "per subject. This does not generate tests or update "
                         "canonical RQ1 validity results.")
    ap.add_argument("--ce-replay-manifest", action="append", default=[],
                    metavar="PATH",
                    help="consume explicit refutation-only CE replay candidate "
                         "manifest(s) through an isolated Stage-4 entry. A "
                         "candidate is never formal credit by itself; only a "
                         "reference-valid and Foundry-green replay is promoted.")
    ap.add_argument("--ce-replay-only", action="store_true",
                    help="run only the admitted CE replay candidates from "
                         "--ce-replay-manifest; do not run normal Stage 2 "
                         "jobs. Requires at least one manifest candidate.")
    ap.add_argument("--timeout", type=int, default=60,
                    help="whole subject generation budget, seconds; the RQ1 "
                         "first pass is intentionally CE-first and bounded")
    ap.add_argument("--esbmc-run-timeout", type=int, default=60,
                    help="per ESBMC invocation budget inside certification, "
                         "seconds. The whole subject still gets --timeout")
    ap.add_argument("--esbmc", default="",
                    help="ESBMC binary to pass through to Stage 2")
    ap.add_argument("--stage2-unit-timeout-cap-s", type=int,
                    default=DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S,
                    help="if positive, cap each Stage-2 unit's whole "
                         "certify_all.py budget to this many seconds while "
                         "leaving --esbmc-run-timeout as the per-ESBMC-run "
                         "cap. Default 0 leaves the cap decision to the "
                         "adaptive Stage-2 scheduler")
    ap.add_argument("--adaptive-stage2-unit-timeout-cap-s", type=int,
                    default=DEFAULT_ADAPTIVE_STAGE2_UNIT_TIMEOUT_CAP_S,
                    help="when --stage2-unit-timeout-cap-s is 0, cap Stage-2 "
                         "for multi-unit subjects or expensive-looking units "
                         "to this many seconds. Set 0 to disable adaptive "
                         "capping")
    ap.add_argument("--stage2-stage4-reserve-s", type=int,
                    default=DEFAULT_STAGE2_STAGE4_RESERVE_S,
                    help="reserve this many subject-generation seconds for "
                         "Stage 4 after each Stage-2 unit. Set 0 to derive "
                         "the reserve from the concrete/timeout Stage-4 "
                         "minimums. The default keeps a larger materialization "
                         "window so partial witness journals do not become "
                         "no-output rows")
    ap.add_argument("--wrapper-grace", type=int, default=60,
                    help="subprocess cleanup/writeout slack outside the tool budget")
    ap.add_argument("--min-remaining-s", type=int, default=20,
                    help="do not start another stage with less than this many seconds")
    ap.add_argument("--no-output-stage2-stop-s", type=int, default=0,
                    help="if positive, stop trying remaining units in a subject "
                         "after this many cumulative Stage-2 seconds when no "
                         "raw artifact has been produced")
    ap.add_argument("--min-no-output-stage2-unit-stop-n", type=int, default=4,
                    help="when --no-output-stage2-stop-s is positive, do not "
                         "apply that early stop before at least this many "
                         "units have been tried, capped by the subject's total "
                         "scheduled units. This prevents one or two slow units "
                         "from abandoning a large target contract")
    ap.add_argument("--no-candidate-stage2-unit-stop-n", type=int, default=0,
                    help="if positive, stop trying remaining units in a subject "
                         "after this many consecutive Stage-2 units produce no "
                         "certified region and no cleared concrete fallback, "
                         "provided no raw artifact has been produced. Default "
                         "0 preserves old scheduling")
    ap.add_argument("--min-no-candidate-stage2-unit-stop-n", type=int, default=8,
                    help="when --no-candidate-stage2-unit-stop-n is positive, "
                         "do not apply that early stop before at least this "
                         "many units have been tried, capped by the subject's "
                         "total scheduled units. This prevents large real "
                         "contracts from being abandoned after only a tiny "
                         "prefix of cheap no-candidate units")
    ap.add_argument("--bounded-holds-retry",
                    action=argparse.BooleanOptionalAction,
                    default=True,
                    help="after a fast NO-PATH row whose progress says every "
                         "claim was bounded-holds, retry that unit once with a "
                         "deeper bounded profile before giving up; use "
                         "--no-bounded-holds-retry to disable")
    ap.add_argument("--bounded-holds-retry-max-tx", type=int, default=2,
                    help="--max-tx value used by --bounded-holds-retry")
    ap.add_argument("--bounded-holds-retry-unwind", type=int, default=8,
                    help="ESBMC --unwind value appended by "
                         "--bounded-holds-retry")
    ap.add_argument("--bounded-holds-retry-max-initial-wall-s", type=int,
                    default=45,
                    help="only bounded-retry a first Stage-2 NO-PATH row whose "
                         "wall_s is at most this many seconds; 0 disables this "
                         "wall-time guard")
    ap.add_argument("--zero-output-stage4-stop-s", type=int, default=0,
                    help="if positive, stop trying remaining units in a subject "
                         "after this many cumulative Stage-4 seconds when "
                         "Stage 4 has run candidate rows but no raw artifact "
                         "has been produced. Default 0 preserves old scheduling")
    ap.add_argument("--min-concrete-only-stage4-s", type=int, default=90,
                    help="after at least one valid artifact exists, do not "
                         "start a Stage-4 pass whose only candidates are "
                         "concrete fallbacks unless at least this many "
                         "generation seconds remain. Set 0 to disable")
    ap.add_argument("--min-timeout-only-stage4-s", type=int, default=90,
                    help="do not start a Stage-4 pass whose only candidates "
                         "come from timed-out/complete partial witnesses, and "
                         "no certified or cleared fallback row, unless at "
                         "least this many generation seconds remain. Set 0 to "
                         "disable")
    ap.add_argument("--skip-concrete-only-after-put-valid", type=int, default=0,
                    help="after this many valid PUT artifacts have already "
                         "been emitted for a subject, do not start another "
                         "Stage-4 pass whose candidates are only concrete "
                         "fallbacks. Set 0 to disable")
    ap.add_argument("--skip-concrete-only-after-any-valid",
                    action=argparse.BooleanOptionalAction,
                    default=False,
                    help="after any valid reference artifact exists for a "
                         "subject, skip later Stage-4 passes whose candidates "
                         "are only concrete fallbacks so the remaining budget "
                         "continues toward PUT/R1/R2. Use "
                         "--no-skip-concrete-only-after-any-valid to keep "
                         "emitting every concrete fallback")
    ap.add_argument("--final-deploy-concrete-fallback",
                    action=argparse.BooleanOptionalAction,
                    default=True,
                    help="if a subject finishes Stage 2/4 without any valid "
                         "reference artifact, emit a target-contract deploy "
                         "concrete replay as a final safety net. This is "
                         "counted only as concrete quality debt, never as "
                         "PUT/R1/R2")
    ap.add_argument("--concrete-only-stage4-timeout-cap-s",
                    type=int,
                    default=DEFAULT_CONCRETE_ONLY_STAGE4_TIMEOUT_CAP_S,
                    help="when a Stage-4 pass only has concrete fallback "
                         "candidates and later units remain, cap its generation "
                         "budget to this many seconds and soft-continue on a "
                         "no-artifact timeout. Set 0 to disable")
    ap.add_argument("--memlimit-gib", type=int, default=12,
                    help="per-ESBMC memory budget passed to Stage 2/4")
    ap.add_argument("--jobs", type=int, default=1,
                    help="number of prepared subjects to run concurrently")
    ap.add_argument("--mem-fraction", type=float, default=0.70,
                    help="refuse --jobs when jobs*memlimit exceeds this "
                         "fraction of current MemAvailable")
    ap.add_argument("--stage-mem-fraction", type=float, default=0.60,
                    help="before starting each Stage-2/4 subprocess, wait "
                         "until memlimit fits this fraction of current "
                         "MemAvailable. This mirrors certify_all.py's guard")
    ap.add_argument("--mem-wait-poll-s", type=float, default=5.0,
                    help="seconds between memory-availability checks")
    ap.add_argument("--forge-timeout", type=int, default=180)
    ap.add_argument("--resume", action="store_true",
                    help="skip subject keys already present in results.jsonl")
    ap.add_argument("--resume-quality-floor",
                    choices=sorted(QUALITY_BUCKET_RANK),
                    default="valid-PUT-with-R1R2",
                    help="with --resume, retry recorded subjects whose best "
                         "quality bucket is below this floor. The default "
                         "keeps improving valid-no-PUT and valid-PUT-no-R1R2 "
                         "rows toward the RQ1 PUT/R1R2 target; use no-valid "
                         "to reproduce the old skip-most-valid resume policy")
    ap.add_argument("--redo", action="store_true",
                    help="run selected subjects even if results.jsonl already has a row")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        veriput_root = Path(args.veriput_root).expanduser().resolve()
        result_root = Path(args.result_root).expanduser().resolve()
        ast_cache_root = Path(args.ast_cache_root).expanduser().resolve()
        validate_roots(veriput_root, result_root, ast_cache_root)
        if (args.timeout <= 0 or args.esbmc_run_timeout <= 0
                or args.wrapper_grace < 0 or args.memlimit_gib <= 0
                or args.no_output_stage2_stop_s < 0
                or args.min_no_output_stage2_unit_stop_n < 0
                or args.no_candidate_stage2_unit_stop_n < 0
                or args.min_no_candidate_stage2_unit_stop_n < 0
                or args.bounded_holds_retry_max_tx <= 0
                or args.bounded_holds_retry_unwind <= 0
                or args.bounded_holds_retry_max_initial_wall_s < 0
                or args.stage2_unit_timeout_cap_s < 0
                or args.adaptive_stage2_unit_timeout_cap_s < 0
                or args.stage2_stage4_reserve_s < 0
                or args.zero_output_stage4_stop_s < 0
                or args.min_concrete_only_stage4_s < 0
                or args.min_timeout_only_stage4_s < 0
                or args.skip_concrete_only_after_put_valid < 0
                or args.concrete_only_stage4_timeout_cap_s < 0
                or args.stage_mem_fraction <= 0
                or args.mem_wait_poll_s <= 0):
            raise RQ1RunError("timeouts and --memlimit-gib must be positive; "
                              "--no-output-stage2-stop-s and "
                              "--min-no-output-stage2-unit-stop-n and "
                              "--no-candidate-stage2-unit-stop-n and "
                              "--min-no-candidate-stage2-unit-stop-n and "
                              "--bounded-holds-retry-max-initial-wall-s and "
                              "--stage2-unit-timeout-cap-s and "
                              "--adaptive-stage2-unit-timeout-cap-s and "
                              "--stage2-stage4-reserve-s and "
                              "--zero-output-stage4-stop-s and "
                              "--min-concrete-only-stage4-s and "
                              "--min-timeout-only-stage4-s and "
                              "--skip-concrete-only-after-put-valid and "
                              "--concrete-only-stage4-timeout-cap-s must be "
                              "non-negative; --stage-mem-fraction, "
                              "--mem-wait-poll-s, "
                              "--bounded-holds-retry-max-tx, and "
                              "--bounded-holds-retry-unwind must be positive")
        if args.esbmc_run_timeout > args.timeout:
            raise RQ1RunError("--esbmc-run-timeout must not exceed --timeout")
        validate_jobs(args)
        if args.ce_replay_only and not args.ce_replay_manifest:
            raise RQ1RunError("--ce-replay-only requires --ce-replay-manifest")
        if args.ce_collection_only and args.ce_replay_manifest:
            raise RQ1RunError(
                "--ce-collection-only and --ce-replay-manifest are mutually exclusive")
        if args.ce_collection_only:
            args.timeout = 60
            args.esbmc_run_timeout = 60
        args.veriput_root = str(veriput_root)
        args.result_root = str(result_root)
        args.ast_cache_root = str(ast_cache_root)
        if args.dry_run:
            print(json.dumps(build_dry_run(args), indent=2, sort_keys=True))
            return 0

        dataset_label, rows = target_rows(veriput_root, args.benchmark,
                                          args.subject_id, args.limit, args.order)
        journal_name = ("ce-collection-results.jsonl"
                        if args.ce_collection_only else "results.jsonl")
        journal = result_root / dataset_label / journal_name
        done = _latest_rows(journal) if args.resume and not args.redo else {}
        if args.resume and not args.redo and not args.ce_collection_only:
            done = adopt_existing_subject_results(
                result_root, dataset_label, rows, journal, done)
            retryable = retryable_resume_rows(done, args.resume_quality_floor)
            if retryable:
                for key in retryable:
                    done.pop(key, None)
                print("[rq1] retrying prior weak/no-valid result(s): "
                      + ", ".join(sorted(
                          str(row.get("subject_id") or key)
                          for key, row in retryable.items())),
                      flush=True)
        for target_row in rows:
            if _run_key(
                    target_row["subject_id"],
                    ce_collection_only=args.ce_collection_only) in done:
                print(f"[rq1] skip recorded {target_row['subject_id']}")
        attempted = run_selected_subjects(rows, dataset_label, journal, done, args)
        if attempted == 0:
            write_dataset_manifest(result_root, dataset_label, journal)
        return 0
    except (OSError, RQ1RunError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
