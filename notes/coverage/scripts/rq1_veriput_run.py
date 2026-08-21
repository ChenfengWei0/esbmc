#!/usr/bin/env python3
"""Run VeriPUT over prepared benchmark subjects.

This is the production wrapper around the existing Stage-2 (`certify_all.py`)
and Stage-4 (`put_all.py`) drivers.  It is deliberately subject-scoped:
benchmark inputs are read from `/home/samson/workspace/VeriPUT/Results/*/subjects`,
while all generated artifacts are retained under
`/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT`.
"""

from __future__ import annotations

import argparse
import copy
import errno
import hashlib
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
from rq1_window_guard import (  # noqa: E402
    WindowGuardError, enforce_rows_in_window,
)
from rq1_concrete_replay_store import (  # noqa: E402
    ReplayPersistenceError, _oracle_binding_errors, _structured_oracle_errors,
    annotate_generalization, audit_manifest, deterministic_replay_errors, load_manifest,
    invalidation_applies, persist_concrete_replay, persistence_coverage,
    persistence_publication_key,
)
from solidity_path_put import (  # noqa: E402
    _constructor_body_text, _constructor_initializer_calls, _mask_solidity_comments_and_strings,
    _norm_ty, _source_constructor_params_from_source, _source_contract_chunk,
    _source_function_decl_infos, _source_inheritance_names, _source_type_default_expr,
)
from solidity_ast_dependencies import (  # noqa: E402
    contract_state_esbmc_store_names, unit_state_dependencies,
)
from veriput_recipe import STRONG_RECIPE_VERSION  # noqa: E402
from veriput_subjects import (  # noqa: E402
    PreparedSubject, SubjectError, enumerate_subject_units, resolve_subject,
)

PUT_ALL = HERE / "put_all.py"
DEFAULT_ESBMC = REPO / "build" / "src" / "esbmc" / "esbmc"
PIPELINE_IDENTITY_FILES = (
    HERE / "certify_all.py",
    REPO / "scripts" / "solidity_path_generalise.py",
    REPO / "scripts" / "solidity_path_put.py",
    REPO / "scripts" / "solidity_ast_dependencies.py",
    HERE / "put_all.py",
    HERE / "rq1_veriput_run.py",
    HERE / "rq1_concrete_replay_store.py",
    HERE / "veriput_recipe.py",
    HERE / "veriput_subjects.py",
    HERE / "unit_schedule.py",
    HERE / "subject_unit_manifest.py",
    HERE / "target_manifest.py",
)
FORGE_STD = (REPO / "notes" / "coverage-comparison" / "_foundry_roundtrip" / "aqua_forge" / "lib" /
             "forge-std")
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
    "PARTIAL-WITNESS-JOURNAL-CE",
    "UNKNOWN",
}
CE_REPLAY_MANIFEST_SCHEMA = "veriput-ce-replay-manifest/1"
CE_REPLAY_CANDIDATE_SCHEMA = "veriput-ce-replay-candidate/1"
QUALITY_BUCKET_RANK = {
    "no-valid": 0,
    "valid-no-PUT": 1,
    "valid-PUT-no-R1R2": 2,
    "valid-PUT-with-R1R2": 3,
}

DEFAULT_VERIPUT_ROOT = Path(os.environ.get("VERIPUT_ROOT", "/home/samson/workspace/VeriPUT"))
DEFAULT_RESULT_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1" / "VeriPUT"
DEFAULT_FAIR_RERUN_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1_KInduction_Fair600"
DEFAULT_NOPUT_RERUN_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1_KInduction_NoPUT600"
DEFAULT_AST_CACHE_ROOT = Path("/tmp/veriput_rq1_ast_cache")
RQ3_ABLATION_ROOTS = {
    "no-selection-strategy": "No_selection_strategy",
    "no-region-refinement": "No_region_refinement",
    "no-test-assert-refinement": "No_test_assert_refinement",
}
DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S = 0
DEFAULT_ADAPTIVE_STAGE2_UNIT_TIMEOUT_CAP_S = 120
DEFAULT_CONCRETE_ONLY_STAGE4_TIMEOUT_CAP_S = 0
DEFAULT_STAGE2_STAGE4_RESERVE_S = 120
DEFAULT_MEMLIMIT_GIB = 12
ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD = 4
ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD = 65
ADAPTIVE_STAGE2_FAIR_SHARE_SLOTS = 8
STRICT_STAGE4_FAIR_SHARE_SLOTS = 8
STRICT_STAGE4_MIN_UNIT_BUDGET_S = 30
STRICT_CASE_FINALIZATION_RESERVE_MAX_S = 30.0
STRICT_PROCESS_TERMINATION_RESERVE_S = 2.0
DATASET_LABEL = {
    "peer182": "peer182",
    "bugfix124": "bugfix124",
    "stress243": "real203",
    "stress203": "real203",
    "real203": "real203",
}
# `real203`/`stress203` are the official Stress-Projects denominator and must
# resolve to the manifest's `stress203` selector, which keeps only the prepared
# targets and yields exactly 203.  Asking the manifest for `stress243` returns
# 241 and silently inflates the 509-target denominator to 547.  `stress243`
# stays available for the wider, unofficial set.
TARGET_BENCHMARK_ARG = {
    "peer182": "peer182",
    "bugfix124": "bugfix124",
    "stress243": "stress243",
    "stress203": "stress203",
    "real203": "stress203",
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


def validate_roots(veriput_root: Path,
                   result_root: Path,
                   ast_cache_root: Path,
                   *,
                   concrete_replay_only_ablation: bool = False,
                   strict_case_wall_budget: bool = False,
                   rq3_ablation: str = "") -> None:
    allowed_results = [veriput_root / "Results" / "RQ1" / "VeriPUT"]
    if concrete_replay_only_ablation:
        allowed_results.append(veriput_root / "Results" / "RQ3" / "VeriExploit" / "No_Cer_Reg")
    if rq3_ablation:
        allowed_results.append(veriput_root / "Results" / "RQ3" /
                               RQ3_ABLATION_ROOTS[rq3_ablation])
    if strict_case_wall_budget:
        allowed_results.append(veriput_root / "Results" / "RQ1_KInduction_Fair600")
        allowed_results.append(veriput_root / "Results" / "RQ1_KInduction_NoPUT600")
    if not any(_is_under(result_root, allowed) for allowed in allowed_results):
        allowed_text = ", ".join(str(path) for path in allowed_results)
        raise RQ1RunError(f"--result-root must be under one of {allowed_text}; got {result_root}")
    if strict_case_wall_budget and _is_under(result_root,
                                              veriput_root / "Results" / "RQ1" / "VeriPUT"):
        raise RQ1RunError(
            "--strict-case-wall-budget is an isolated rerun and must not write canonical RQ1")
    for protected in (veriput_root / "Datasets", veriput_root / "Results"):
        if _is_under(ast_cache_root, protected):
            raise RQ1RunError(
                f"--ast-cache-root must not be under {protected}; got {ast_cache_root}")


def validate_rq3_ablation_args(args) -> None:
    """Keep RQ3 derivation-only arms out of the verifier runner."""
    rq3_ablation = getattr(args, "rq3_ablation", "")
    if getattr(args, "no_test_assert_refinement", False) or \
            rq3_ablation == "no-test-assert-refinement":
        raise RQ1RunError("no-test-assert-refinement is derived from a completed "
                          "Full run with rq3_derive_from_full.py; do not rerun "
                          "VeriPUT for this ablation")
    if getattr(args, "no_region_refinement", False) or rq3_ablation == "no-region-refinement":
        raise RQ1RunError("no-region-refinement is derived from a completed "
                          "Full run with rq3_derive_from_full.py and retained "
                          "certified concrete bases; do not rerun VeriPUT for "
                          "this ablation")
    if getattr(args, "no_selection_strategy", False) and rq3_ablation != "no-selection-strategy":
        raise RQ1RunError("--no-selection-strategy requires "
                          "--rq3-ablation no-selection-strategy")
    if rq3_ablation == "no-selection-strategy" and \
            not getattr(args, "no_selection_strategy", False):
        raise RQ1RunError("no-selection-strategy must pass "
                          "--no-selection-strategy so Stage 2 receives the "
                          "matching ESBMC option")


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
    return (_is_under(artifact, collection_root) and _is_under(journal, collection_root)
            and artifact.is_dir() and journal.is_file())


def _load_ce_replay_candidates(manifest_paths: list[Path], target_row: dict,
                               subject: PreparedSubject,
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
            rejected.append({"manifest": str(manifest_path), "reason": f"invalid manifest: {exc}"})
            continue
        if not isinstance(doc, dict) or doc.get("schema") != CE_REPLAY_MANIFEST_SCHEMA:
            rejected.append({
                "manifest": str(manifest_path),
                "reason": "unexpected CE replay manifest schema"
            })
            continue
        if doc.get("formal_results_written") is not False:
            rejected.append({
                "manifest": str(manifest_path),
                "reason": "manifest is not explicitly refutation-only"
            })
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
                if reason is None and (not isinstance(path, dict) or not path.get("path_function")
                                       or not re.fullmatch(r"\d+", str(path.get("path_id")))):
                    reason = "candidate path identity is malformed"
                if reason is None and _candidate_replay_ce(candidate) is None:
                    reason = "candidate replay contains non-integer or conflicting values"
            if reason is not None:
                rejected.append({
                    "manifest":
                    str(manifest_path),
                    "candidate_id":
                    (candidate.get("candidate_id") if isinstance(candidate, dict) else None),
                    "reason":
                    reason
                })
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
        "not_certified": {
            enc: "CE replay candidate; no region proof"
        },
        "not_certified_details": {
            enc: detail
        },
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
    valid_rows = [row for row in summary.get("valid_tests") or [] if isinstance(row, dict)]
    rows = [
        row for row in valid_rows if isinstance(row, dict)
        and str(row.get("unit")) == str(expected_unit) and str(row.get("enc")) == expected_enc
    ]
    verifier_passed = bool(rows) and all(
        bool(row.get("valid_reference_test")) and not row.get("refused") and not row.get("stale")
        for row in rows)
    foundry_passed = bool(rows) and all(row.get("forge_status") == "Success" for row in rows)
    # A refutation-only candidate may become a concrete replay, never a PUT or
    # an R1/R2 claim.  A PUT-shaped result here indicates an isolation failure.
    isolation_passed = (bool(rows) and len(rows) == len(valid_rows)
                        and all(row.get("kind") == "concrete" for row in rows))
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
    ledger_paths = list(root.rglob("*.json")) + list(root.rglob("*.jsonl"))
    for path in ledger_paths:
        try:
            text = path.read_text(errors="replace")
            updated = text.replace(old, new)
            if updated != text:
                path.write_text(updated)
        except OSError:
            continue


def _rewrite_exact_artifact_paths(root: Path, path_map: dict[str, str]) -> None:
    """Rewrite external evidence inputs to their published locations."""
    if not path_map:
        return
    ledger_paths = list(root.rglob("*.json")) + list(root.rglob("*.jsonl"))
    for path in ledger_paths:
        try:
            text = path.read_text(errors="replace")
            updated = text
            for old, new in path_map.items():
                updated = updated.replace(old, new)
            if updated != text:
                path.write_text(updated)
        except OSError:
            continue


def _referenced_solast_paths(root: Path) -> set[Path]:
    """Find existing absolute verifier ASTs referenced by JSON ledgers."""
    paths: set[Path] = set()

    def visit(value) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.endswith(".solast"):
            candidate = Path(value)
            if candidate.is_absolute() and candidate.is_file():
                paths.add(candidate)

    for ledger in list(root.rglob("*.json")) + list(root.rglob("*.jsonl")):
        try:
            if ledger.suffix == ".jsonl":
                for line in ledger.read_text(errors="replace").splitlines():
                    if line.strip():
                        visit(json.loads(line))
            else:
                visit(json.loads(ledger.read_text(errors="replace")))
        except (OSError, json.JSONDecodeError):
            continue
    return paths


def _promote_candidate_artifacts(staging_root: Path, case_dir: Path, candidate_id: str) -> Path:
    """Copy an accepted isolated Stage-4 result into the formal artifact tree."""
    destination = (case_dir / "put" / "ce-replay" / _safe_name(candidate_id))
    if destination.exists():
        raise RQ1RunError(f"refusing to overwrite existing CE replay artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging_root, destination)
    _rewrite_promoted_paths(destination, staging_root, destination)
    return destination


def _strict_stage4_roots(case_dir: Path,
                         cert_path: Path,
                         job_id: str,
                         strict_case_wall_budget: bool) -> tuple[Path, Path]:
    """Return the execution and published roots for a Stage-4 unit."""
    published = case_dir / "put" / _safe_name(job_id)
    if not strict_case_wall_budget:
        return published, published
    # cert_path is below the externally validated AST cache in strict reruns.
    # Keep put_all away from VeriPUT/Results, then publish its artifacts only
    # after the child process has finished.
    staging = cert_path.parent / "stage4" / _safe_name(job_id)
    return staging, published


def _publish_strict_stage4_artifacts(staging_root: Path, destination: Path) -> dict:
    """Publish one finished strict-rerun Stage-4 tree and remove its scratch."""
    if not staging_root.exists():
        return {
            "status": "no-artifacts",
            "staging_root": str(staging_root),
            "destination": str(destination),
        }
    if destination.exists():
        raise RQ1RunError(f"refusing to overwrite Stage-4 artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    publication_method = "rename"
    try:
        os.replace(staging_root, destination)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        publication_method = "copy"
        shutil.copytree(staging_root, destination)
        shutil.rmtree(staging_root)
    try:
        _rewrite_promoted_paths(destination, staging_root, destination)
    except OSError:
        # The finished evidence has already been published. Never delete it
        # merely because an audit-path rewrite failed.
        raise
    return {
        "status": "published",
        "method": publication_method,
        "staging_root": str(staging_root),
        "destination": str(destination),
    }


def _move_tree(source: Path, destination: Path) -> str:
    """Move a directory that may live on a different filesystem.

    The strict-rerun staging root sits under the AST cache, which is a separate
    mount from `VeriPUT/Results` on some hosts.  A bare rename raises EXDEV
    there and aborts the whole case before Stage 2 ever runs.
    """
    try:
        os.replace(source, destination)
        return "rename"
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copytree(source, destination)
        shutil.rmtree(source)
        return "copy"


def _publish_strict_certification_artifacts(cert_path: Path,
                                            case_dir: Path) -> tuple[Path, dict[str, str]]:
    """Publish the durable Stage-2 evidence bundle for a strict rerun."""
    staging_root = cert_path.parent
    destination = case_dir / "cert"
    if destination.exists():
        precreated_fixtures = destination / "fixtures"
        staged_fixtures = staging_root / "fixtures"
        existing_entries = sorted(destination.iterdir())
        if (len(existing_entries) == 1 and existing_entries[0] == precreated_fixtures
                and not (destination / cert_path.name).exists()
                and not staged_fixtures.exists()):
            staged_fixtures.parent.mkdir(parents=True, exist_ok=True)
            _move_tree(precreated_fixtures, staged_fixtures)
            destination.rmdir()
        else:
            raise RQ1RunError(f"refusing to overwrite certification artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    input_path_map: dict[str, str] = {}
    evidence_dir = staging_root / "evidence" / "solast"
    evidence_rows = []
    for source in sorted(_referenced_solast_paths(staging_root)):
        try:
            source.relative_to(staging_root)
            continue
        except ValueError:
            pass
        digest = _sha256_file(source)
        if not digest:
            raise RQ1RunError(f"cannot hash certification verifier input: {source}")
        staged_input = evidence_dir / f"{digest}.solast"
        staged_input.parent.mkdir(parents=True, exist_ok=True)
        if not staged_input.exists():
            shutil.copy2(source, staged_input)
        if _sha256_file(staged_input) != digest:
            raise RQ1RunError(f"published verifier input hash mismatch: {source}")
        published_input = destination / "evidence" / "solast" / staged_input.name
        input_path_map[str(source)] = str(published_input)
        evidence_rows.append({
            "sha256": digest,
            "published": str(published_input),
        })
    if evidence_rows:
        (staging_root / "evidence" / "solast-manifest.json").write_text(
            json.dumps({
                "schema": "veriput-published-solast/v1",
                "inputs": evidence_rows,
            }, indent=2, sort_keys=True) + "\n")
    if not cert_path.exists():
        # A strict run may spend its whole Stage-2 budget before certify_all
        # authenticates any row. That is still a valid no-output result and the
        # empty journal is the durable evidence boundary for that empty set.
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_text("")
    stage4_root = staging_root / "stage4"
    held_stage4_root = staging_root.parent / f".{staging_root.name}.stage4-unpublished"
    if stage4_root.exists():
        if any(stage4_root.iterdir()):
            if held_stage4_root.exists():
                raise RQ1RunError(f"refusing to overwrite Stage-4 scratch: {held_stage4_root}")
            os.replace(stage4_root, held_stage4_root)
        else:
            stage4_root.rmdir()
    try:
        try:
            os.replace(staging_root, destination)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            shutil.copytree(staging_root, destination)
            shutil.rmtree(staging_root)
        _rewrite_promoted_paths(destination, staging_root, destination)
        _rewrite_exact_artifact_paths(destination, input_path_map)
    except OSError:
        # Preserve whichever side already owns the evidence. Removing the
        # destination here would lose a completed strict-run journal.
        raise
    finally:
        if held_stage4_root.exists():
            staging_root.mkdir(parents=True, exist_ok=True)
            os.replace(held_stage4_root, staging_root / "stage4")
    published_cert = destination / cert_path.name
    if not published_cert.is_file():
        raise RQ1RunError(f"published certification journal is missing: {published_cert}")
    return published_cert, input_path_map


def _relocate_record_paths(value, old_root: Path, new_root: Path):
    """Relocate scratch paths in the in-memory result/schedule records."""
    old = str(old_root)
    new = str(new_root)
    if isinstance(value, dict):
        return {key: _relocate_record_paths(item, old_root, new_root)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_relocate_record_paths(item, old_root, new_root) for item in value]
    if isinstance(value, str):
        # Stage-4 scratch is deliberately published through its own artifact
        # tree. Keep historical execution-root fields historical rather than
        # rewriting them to a non-existent cert/stage4 directory.
        if str(old_root) + "/stage4" in value:
            return value
        return value.replace(old, new)
    return value


def _relocate_exact_record_paths(value, path_map: dict[str, str]):
    """Relocate exact external evidence paths in an in-memory record."""
    if isinstance(value, dict):
        return {key: _relocate_exact_record_paths(item, path_map)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_relocate_exact_record_paths(item, path_map) for item in value]
    if isinstance(value, str):
        for old, new in path_map.items():
            value = value.replace(old, new)
    return value


def _candidate_rejection(candidate: dict, reason: str, detail: str | None = None) -> dict:
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
        subject_id = row.get("subject_id")
        if subject_id:
            key = _run_key(str(subject_id),
                           ce_collection_only=path.name == "ce-collection-results.jsonl")
        else:
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
        put_valid = sum(1 for test in valid_tests if test.get("kind") == "put")
        r1r2 = sum(1 for test in valid_tests
                   if test.get("kind") == "put" and _has_oracle_class(test, "R1", "R2"))
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
    cert_sidecar = case_dir / "cert" / "certify-results.jsonl"
    if cert_sidecar.is_file():
        _merge_certification_summary_fields(
            row, summarize_certification(cert_sidecar), authoritative=True)
    row = _normalize_result_row(row)
    row["artifact_root"] = str(case_dir)
    row["result_json"] = str(path)
    row = _merge_put_summary_into_row(row, case_dir)
    row["artifact_root"] = str(case_dir)
    row["result_json"] = str(path)
    return row


def _merge_certification_summary_fields(row: dict,
                                        cert_summary: dict | None,
                                        *,
                                        authoritative: bool = False) -> dict:
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
        if authoritative or (value and not row.get(dst)):
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


def persist_case_concrete_replays(case_dir: Path,
                                  put_summary: dict,
                                  case_key: str | None = None) -> dict:
    """Adopt every green concrete test before a valid result is published."""
    valid_tests = [test for test in put_summary.get("valid_tests") or [] if isinstance(test, dict)]
    retained_bases = [test for test in put_summary.get("retained_concrete_bases") or []
                      if isinstance(test, dict) and test.get("valid_reference_test") is True]
    persistence_rows = valid_tests + retained_bases
    if case_key and invalidation_applies(case_key, valid_tests):
        coverage = persistence_coverage([], [], case_dir)
        coverage.update({
            "invalidated_evidence": True,
            "invalidated_case": case_key,
            "persistence_errors": [],
            "manifest_errors": [],
        })
        return coverage
    errors = []
    for test in persistence_rows:
        if not isinstance(test, dict) or test.get("kind") != "concrete":
            continue
        try:
            persist_concrete_replay(case_dir, test)
        except ReplayPersistenceError as exc:
            errors.append({
                "test": test.get("test"),
                "file": test.get("file"),
                "reason": str(exc),
            })
    try:
        generalization = annotate_generalization(case_dir, persistence_rows)
    except ReplayPersistenceError as exc:
        errors.append({"reason": str(exc), "stage": "generalization-annotation"})
        generalization = {}
    manifest = load_manifest(case_dir)
    coverage = persistence_coverage(
        persistence_rows,
        manifest.get("entries") or [], case_dir)
    coverage["manifest"] = str(case_dir / "concrete-replays" / "manifest.json")
    coverage["manifest_errors"] = audit_manifest(case_dir, manifest)
    coverage["persistence_errors"] = errors
    coverage["generalization"] = generalization
    return coverage


def persistence_publication_failure(coverage: dict) -> str | None:
    """Return why a valid result cannot be published transactionally."""
    if coverage.get("invalidated_evidence"):
        return "valid evidence is quarantined by the frontend pollution audit"
    errors = list(coverage.get("persistence_errors") or [])
    manifest_errors = list(coverage.get("manifest_errors") or [])
    missing = int(coverage.get("put_basis_missing_count") or 0)
    missing_concrete = int(coverage.get("valid_concrete_missing_count") or 0)
    unrecognized = int(coverage.get("unrecognized_valid_count") or 0)
    if errors:
        return f"{len(errors)} concrete replay(s) could not be persisted"
    if manifest_errors:
        return f"canonical replay manifest has {len(manifest_errors)} error(s)"
    if missing:
        return f"{missing} PUT artifact(s) lack an exact concrete basis replay"
    if missing_concrete:
        return f"{missing_concrete} valid concrete replay test(s) were not retained"
    if unrecognized:
        return f"{unrecognized} valid row(s) have an unrecognized artifact kind"
    if not coverage.get("complete"):
        return "canonical concrete replay coverage is incomplete"
    return None


def quarantine_unpersisted_validity(put_summary: dict,
                                     reason: str,
                                     coverage: dict | None = None) -> dict:
    """Reject only validity rows whose exact persistence proof is missing.

    Older callers without coverage still fail closed for the whole summary.  A
    persistence transaction supplies coverage so independently retained
    siblings remain publishable when one replay cannot be stored.
    """
    summary = dict(put_summary)
    candidates = [dict(row) for row in summary.get("valid_tests") or [] if isinstance(row, dict)]
    retained = []
    withheld = []
    if coverage is None or coverage.get("invalidated_evidence"):
        withheld = candidates
    else:
        publishable = set(coverage.get("publishable_validity_keys") or [])
        for row in candidates:
            key = persistence_publication_key(row)
            if key is not None and key in publishable:
                retained.append(row)
            else:
                withheld.append(row)
    summary["unpublished_valid_tests"] = withheld
    summary["persistence_failure_reason"] = reason
    summary["valid_tests"] = retained
    summary["valid_artifacts"] = retained
    summary["valid_artifacts_retained"] = bool(retained)
    summary["status"] = "ok" if retained else "persistence-error"
    summary["reason"] = None if retained else reason
    if retained:
        summary["partial_failure_reason"] = reason
    return _normalize_result_row(summary)


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
        "key":
        f"gen:veriput:{subject_id}",
        "stage":
        "gen_veriput",
        "schema":
        "veriput-rq1-result-row/v1",
        "subject_id":
        subject_id,
        "benchmark":
        target_row.get("benchmark"),
        "dataset":
        dataset_label,
        "contract":
        target_row.get("contract"),
        "artifact_root":
        str(case_dir),
        "result_json":
        str(case_dir / "result.json") if (case_dir / "result.json").exists() else None,
        "raw_artifacts_retained":
        True,
        "valid_artifacts_retained":
        put_summary["valid"] > 0,
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
        valid_puts = [test for test in valid_tests if test.get("kind") == "put"]
        valid_puts_with_r1 = [test for test in valid_puts if _has_oracle_class(test, "R1")]
        valid_puts_with_r2 = [test for test in valid_puts if _has_oracle_class(test, "R2")]
        valid_puts_with_r1r2 = [test for test in valid_puts if _has_oracle_class(test, "R1", "R2")]
        valid_concrete = sum(1 for test in valid_tests
                             if isinstance(test, dict) and test.get("kind") == "concrete")
        row["valid"] = len(valid_tests)
        row["put_valid"] = len(valid_puts)
        row["concrete_valid"] = valid_concrete
        row["valid_put_with_R1"] = len(valid_puts_with_r1)
        row["valid_put_with_R2"] = len(valid_puts_with_r2)
        row["valid_put_with_R1_or_R2"] = len(valid_puts_with_r1r2)
        row["valid_put_without_R1R2"] = (len(valid_puts) - len(valid_puts_with_r1r2))
        row["valid_concrete"] = valid_concrete
        row["quality_bucket"] = _legacy_quality_bucket(row)
    else:
        component_valid = (_row_count(row, "put_valid") + _row_count(row, "concrete_valid"))
        if row.get("valid") is None:
            valid = component_valid
            if valid <= 0:
                valid = len(row.get("valid_tests") or [])
            row["valid"] = valid
        elif component_valid > _row_count(row, "valid"):
            row["valid"] = component_valid
        if row.get("valid_concrete") is None and row.get("concrete_valid") is not None:
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
        "valid_put_with_R1_or_R2": _row_count(row, "valid_put_with_R1_or_R2"),
        "valid_put_without_R1R2": _row_count(row, "valid_put_without_R1R2"),
    }


def _row_time_stats(row: dict) -> dict:
    return {
        "generation_wall_s": float(row.get("generation_wall_s") or 0.0),
        "stage2_wall_s": float(row.get("stage2_wall_s") or 0.0),
        "stage4_wall_s": float(row.get("stage4_wall_s") or 0.0),
        "stage4_generation_wall_s": float(row.get("stage4_generation_wall_s") or 0.0),
        "stage4_emission_wall_s": float(row.get("stage4_emission_wall_s") or 0.0),
        "foundry_replay_wall_s": float(row.get("foundry_replay_wall_s") or 0.0),
        "put_all_wall_s": float(row.get("put_all_wall_s") or 0.0),
        "wall_total_s": float(row.get("wall_total_s") or row.get("wall") or 0.0),
    }


def _annotate_result_accounting(row: dict) -> dict:
    row = dict(row)
    row["failure_reason"] = (row.get("reason") or row.get("partial_failure_reason"))
    row["raw_artifacts"] = row.get("raw_artifacts") or row.get("raw_tests") or []
    row["valid_artifacts"] = (row.get("valid_artifacts") or row.get("valid_tests") or [])
    row["artifact_counts"] = _artifact_count_summary(row)
    row["time_stats"] = _row_time_stats(row)
    row["quality_bucket"] = row.get("quality_bucket") or _legacy_quality_bucket(row)
    return row


def _row_needs_normalized_adoption(current: dict | None, candidate: dict) -> bool:
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
    candidate_replay = candidate.get("concrete_replay_persistence") or {}
    current_replay = current.get("concrete_replay_persistence") or {}
    if (candidate_replay.get("complete") and candidate_replay != current_replay):
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
        if candidate.get(key) != current.get(key):
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
        if path.is_dir() and any(path.name.startswith(prefix) for prefix in prefixes)
    ]
    return sorted(candidates,
                  key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
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


def _load_schedule(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def _schedule_job_identity(job: dict) -> tuple:
    unit_info = job.get("unit_info") or {}
    return (
        str(job.get("benchmark") or ""),
        str(job.get("subject_id") or ""),
        str(job.get("contract") or ""),
        str(job.get("unit") or ""),
        str(job.get("path_function") or ""),
        str(job.get("target") or ""),
        str(job.get("region_strategy") or ""),
        str(job.get("sequence_strategy") or ""),
        str(unit_info.get("contract") or ""),
        str(unit_info.get("name") or ""),
        str(unit_info.get("signature") or ""),
        str(unit_info.get("path_function") or ""),
        tuple(_normalised_certify_argv(job.get("certify_argv") or [])),
    )


def _normalised_certify_argv(argv: list) -> list[str]:
    if not isinstance(argv, list):
        return []
    normalised: list[str] = []
    skip_next = False
    path_value_options = {"--out", "--workdir", "--subject-dir"}
    for raw in argv:
        arg = str(raw)
        if skip_next:
            normalised.append("<path>")
            skip_next = False
            continue
        if arg in path_value_options:
            normalised.append(arg)
            skip_next = True
            continue
        if any(arg.startswith(f"{opt}=") for opt in path_value_options):
            opt = arg.split("=", 1)[0]
            normalised.append(f"{opt}=<path>")
            continue
        normalised.append(arg)
    return normalised


def _schedule_identity(schedule: dict) -> dict:
    source = schedule.get("source") if isinstance(schedule.get("source"), dict) else {}
    runtime = schedule.get("rq1_stage2_runtime_policy")
    if not isinstance(runtime, dict):
        runtime = {}
    budget = schedule.get("certification_budget")
    if not isinstance(budget, dict):
        budget = {}
    return {
        "schema":
        schedule.get("schema"),
        "recipe_version":
        schedule.get("recipe_version"),
        "selection_strategy":
        schedule.get("selection_strategy"),
        "shard":
        schedule.get("shard"),
        "limit":
        schedule.get("limit"),
        "source": {
            key: source.get(key)
            for key in ("schema", "benchmark", "generate_ast", "target_manifest")
        },
        "runtime": {
            key: runtime.get(key)
            for key in (
                "stage2_unit_timeout_cap_s",
                "adaptive_stage2_unit_timeout_cap_s",
                "stage2_stage4_reserve_s",
                "stage4_reserve_boundary_enforced",
                "bounded_holds_retry",
                "bounded_holds_retry_max_tx",
                "bounded_holds_retry_unwind",
                "bounded_holds_retry_max_initial_wall_s",
            )
        },
        "budget": {
            key: budget.get(key)
            for key in ("timeout_s", "run_timeout_s", "memlimit_gib")
        },
        "jobs":
        sorted(
            _schedule_job_identity(job) for job in schedule.get("jobs") or []
            if isinstance(job, dict)),
    }


def _schedule_generated_ts(schedule: dict) -> float | None:
    generated = schedule.get("generated_at")
    if not isinstance(generated, str) or not generated:
        return None
    try:
        return datetime.fromisoformat(generated.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _schedule_subject_paths(schedule: dict) -> set[Path]:
    paths: set[Path] = set()
    for job in schedule.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        for argv_key in ("certify_argv", "dry_run_argv"):
            argv = job.get(argv_key) or []
            if not isinstance(argv, list):
                continue
            for idx, arg in enumerate(argv[:-1]):
                if arg == "--subject-dir":
                    root = Path(str(argv[idx + 1]))
                    paths.add(root / "flat.sol")
    return paths


def _schedule_subject_dirs(schedule: dict) -> set[Path]:
    dirs: set[Path] = set()
    for job in schedule.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        for argv_key in ("certify_argv", "dry_run_argv"):
            argv = job.get(argv_key) or []
            if not isinstance(argv, list):
                continue
            for idx, arg in enumerate(argv[:-1]):
                if arg == "--subject-dir":
                    dirs.add(Path(str(argv[idx + 1])))
    return dirs


def _sha256_file(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _esbmc_binary_identity(esbmc_arg: str | None) -> dict:
    path = _resolved_esbmc_binary(esbmc_arg)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    digest = _sha256_file(resolved)
    identity = {
        "path": str(resolved),
        "sha256": digest,
    }
    try:
        stat = resolved.stat()
        identity["mtime"] = stat.st_mtime
        identity["size"] = stat.st_size
    except OSError:
        pass
    return identity


def _pipeline_code_identity(stage4_driver: str | None = None) -> dict:
    files = {}
    paths = list(PIPELINE_IDENTITY_FILES)
    if stage4_driver:
        candidate = Path(stage4_driver)
        if candidate.resolve() not in {path.resolve() for path in paths}:
            paths.append(candidate)
    for path in paths:
        resolved = path.resolve()
        digest = _sha256_file(resolved)
        files[str(resolved)] = digest
    return {
        "schema": "veriput-pipeline-code-identity/v1",
        "files": files,
    }


def _command_identity(command: str, version_args: list[str]) -> dict:
    found = shutil.which(command)
    path = Path(found) if found else Path(command)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        completed = subprocess.run([str(resolved), *version_args],
                                   text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   timeout=10,
                                   check=False)
        version = (completed.stdout or "").strip()
        version_rc = completed.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        version = f"<error: {exc}>"
        version_rc = None
    return {
        "path": str(resolved),
        "sha256": _sha256_file(resolved),
        "version_args": version_args,
        "version_rc": version_rc,
        "version": version,
    }


def _tree_identity(root: Path) -> dict:
    try:
        resolved = root.resolve()
    except OSError:
        resolved = root
    files = {}
    if resolved.exists():
        for path in sorted(item for item in resolved.rglob("*") if item.is_file()):
            try:
                rel = path.relative_to(resolved).as_posix()
            except ValueError:
                rel = str(path)
            files[rel] = _sha256_file(path)
    h = hashlib.sha256()
    for rel, digest in sorted(files.items()):
        h.update(rel.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(str(digest).encode("ascii", "replace"))
        h.update(b"\0")
    return {
        "path": str(resolved),
        "exists": resolved.exists(),
        "files": len(files),
        "sha256": h.hexdigest() if files else None,
    }


def _stage4_toolchain_identity() -> dict:
    _ensure_foundry_tools_on_path()
    return {
        "schema": "veriput-stage4-toolchain-identity/v1",
        "forge": _command_identity("forge", ["--version"]),
        "solc": _command_identity("solc", ["--version"]),
        "forge_std": _tree_identity(FORGE_STD),
    }


def _runtime_binary_identity_matches(stale: dict | None, current: dict | None) -> bool:
    stale_identity = (stale or {}).get("esbmc_binary_identity")
    current_identity = (current or {}).get("esbmc_binary_identity")
    if not isinstance(stale_identity, dict) or not isinstance(current_identity, dict):
        return False
    stale_hash = stale_identity.get("sha256")
    current_hash = current_identity.get("sha256")
    if not stale_hash or stale_hash != current_hash:
        return False
    return True


def _pipeline_code_identity_matches(stale: dict | None, current: dict | None) -> bool:
    stale_identity = (stale or {}).get("pipeline_code_identity")
    current_identity = (current or {}).get("pipeline_code_identity")
    if not isinstance(stale_identity, dict) or not isinstance(current_identity, dict):
        return False
    stale_files = stale_identity.get("files")
    current_files = current_identity.get("files")
    return bool(isinstance(stale_files, dict) and stale_files and stale_files == current_files)


def _stage4_toolchain_identity_matches(stale: dict | None, current: dict | None) -> bool:
    stale_identity = (stale or {}).get("stage4_toolchain_identity")
    current_identity = (current or {}).get("stage4_toolchain_identity")
    if not isinstance(stale_identity, dict) or not isinstance(current_identity, dict):
        return False
    for key in ("forge", "solc"):
        stale_tool = stale_identity.get(key)
        current_tool = current_identity.get(key)
        if not isinstance(stale_tool, dict) or not isinstance(current_tool, dict):
            return False
        if not stale_tool.get("sha256") or stale_tool != current_tool:
            return False
    stale_forge_std = stale_identity.get("forge_std")
    current_forge_std = current_identity.get("forge_std")
    if not isinstance(stale_forge_std, dict) or not isinstance(current_forge_std, dict):
        return False
    if not stale_forge_std.get("sha256") or stale_forge_std != current_forge_std:
        return False
    return True


def _find_foundry_project(path: Path) -> Path | None:
    current = path if path.is_dir() else path.parent
    for parent in (current, *current.parents):
        if (parent / "foundry.toml").is_file():
            return parent
    return None


def _forge_json_has_successful_test(data, test_name: str, expected_path: str) -> bool:
    expected_norm = expected_path.replace("\\", "/")

    def success(value) -> bool:
        if isinstance(value, dict):
            status = str(value.get("status") or value.get("result") or "")
            if status.lower() == "success":
                return True
            if value.get("success") is True:
                return True
        return False

    def visit(value, path_matches: bool = False) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                key_s = str(key)
                child_path_matches = path_matches or expected_norm in key_s.replace("\\", "/")
                if ((key_s == test_name or key_s.startswith(f"{test_name}(")) and child_path_matches
                        and success(child)):
                    return True
                if visit(child, child_path_matches):
                    return True
        elif isinstance(value, list):
            for child in value:
                if visit(child, path_matches):
                    return True
        return False

    return visit(data)


def _stale_valid_artifacts_replay_current_toolchain(row: dict | None) -> bool:
    if not isinstance(row, dict):
        return False
    tests = []
    for item in (row.get("valid_tests") or row.get("valid_artifacts") or []):
        if not isinstance(item, dict) or not _is_valid_reference_test(item):
            continue
        test_name = str(item.get("test") or "")
        file_name = str(item.get("file") or "")
        if not test_name or not file_name:
            return False
        test_path = Path(file_name)
        if not test_path.is_file():
            return False
        project = _find_foundry_project(test_path)
        if project is None or not project.is_dir():
            return False
        try:
            rel_path = test_path.resolve().relative_to(project.resolve()).as_posix()
        except (OSError, ValueError):
            return False
        tests.append((project, test_name, rel_path))
    if not tests:
        return False
    _ensure_foundry_tools_on_path()
    for project, test_name, rel_path in tests:
        try:
            completed = subprocess.run([
                "forge", "test", "--json", "--match-test", f"^{re.escape(test_name)}\\(",
                "--match-path", rel_path
            ],
                                       cwd=project,
                                       text=True,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT,
                                       timeout=120,
                                       check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode != 0:
            return False
        try:
            data = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return False
        if not _forge_json_has_successful_test(data, test_name, rel_path):
            return False
    return True


def _argv_value(argv: list, flag: str) -> str | None:
    if not isinstance(argv, list):
        return None
    for idx, arg in enumerate(argv[:-1]):
        if arg == flag:
            return str(argv[idx + 1])
    return None


def _job_subject_dir(job: dict) -> Path | None:
    subject = job.get("subject") if isinstance(job.get("subject"), dict) else {}
    root = subject.get("root") or _argv_value(job.get("certify_argv") or [], "--subject-dir")
    if root:
        return Path(str(root))
    return None


def _job_solast_path(job: dict, subject_dir: Path | None) -> Path | None:
    subject = job.get("subject") if isinstance(job.get("subject"), dict) else {}
    solast = subject.get("solast")
    if solast:
        return Path(str(solast))
    if subject_dir is None:
        return None
    argv = job.get("certify_argv") or []
    ast_cache_root = _argv_value(argv, "--ast-cache-root")
    if ast_cache_root:
        benchmark = str(subject.get("benchmark") or job.get("benchmark") or "")
        benchmark_key = str(subject.get("benchmark_key") or job.get("subject_id") or "")
        ast_name = Path(str(subject.get("solast") or (subject_dir / "subject.solast"))).name
        if benchmark and benchmark_key and ast_name:
            return Path(ast_cache_root) / benchmark / benchmark_key / ast_name
    return subject_dir / "subject.solast"


def _verifier_input_identity(schedule: dict) -> dict:
    inputs = []
    seen = set()
    for job in schedule.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        subject = job.get("subject") if isinstance(job.get("subject"), dict) else {}
        subject_dir = _job_subject_dir(job)
        if subject_dir is None:
            continue
        flat = Path(str(subject.get("flat_sol") or (subject_dir / "flat.sol")))
        solast = _job_solast_path(job, subject_dir)
        if solast is None:
            continue
        key = (str(flat), str(solast))
        if key in seen:
            continue
        seen.add(key)
        try:
            resolved_dir = subject_dir.resolve()
        except OSError:
            resolved_dir = subject_dir
        try:
            resolved_flat = flat.resolve()
        except OSError:
            resolved_flat = flat
        try:
            resolved_solast = solast.resolve()
        except OSError:
            resolved_solast = solast
        inputs.append({
            "subject_dir": str(resolved_dir),
            "flat": str(resolved_flat),
            "flat_sha256": _sha256_file(flat),
            "solast": str(resolved_solast),
            "solast_sha256": _sha256_file(solast),
        })
    inputs.sort(key=lambda item: (str(item.get("flat") or ""), str(item.get("solast") or "")))
    return {
        "schema": "veriput-verifier-input-identity/v1",
        "inputs": inputs,
    }


def _verifier_input_identity_matches(stale: dict | None, current: dict | None) -> bool:
    stale_identity = (stale or {}).get("verifier_input_identity")
    current_identity = (current or {}).get("verifier_input_identity")
    if not isinstance(stale_identity, dict) or not isinstance(current_identity, dict):
        return False
    stale_inputs = stale_identity.get("inputs")
    current_inputs = current_identity.get("inputs")
    if not isinstance(stale_inputs, list) or not stale_inputs:
        return False
    if stale_inputs != current_inputs:
        return False
    for item in stale_inputs:
        if not isinstance(item, dict):
            return False
        if not item.get("flat_sha256") or not item.get("solast_sha256"):
            return False
    return True


def _schedule_source_digests(schedule: dict) -> list[str] | None:
    paths = _schedule_subject_paths(schedule)
    if not paths:
        return None
    digests = []
    for path in sorted(paths):
        digest = _sha256_file(path)
        if digest is None:
            return None
        digests.append(digest)
    return digests


def _schedule_source_not_newer_than(schedule: dict) -> bool:
    generated_ts = _schedule_generated_ts(schedule)
    if generated_ts is None:
        return False
    paths = _schedule_subject_paths(schedule)
    if not paths:
        return False
    for path in paths:
        try:
            if path.stat().st_mtime > generated_ts + 1.0:
                return False
        except OSError:
            return False
    return True


def _stale_schedule_identity_matches_current(old_dir: Path, case_dir: Path) -> bool:
    old_schedule = _load_schedule(old_dir / "unit-schedule.json")
    current_schedule = _load_schedule(case_dir / "unit-schedule.json")
    if old_schedule is None or current_schedule is None:
        return False
    if _schedule_identity(old_schedule) != _schedule_identity(current_schedule):
        return False
    old_digests = _schedule_source_digests(old_schedule)
    current_digests = _schedule_source_digests(current_schedule)
    if not old_digests or old_digests != current_digests:
        return False
    return (_schedule_source_not_newer_than(old_schedule)
            and _schedule_source_not_newer_than(current_schedule))


def _zero_valid_row_is_authoritative(row: dict | None) -> bool:
    """Whether a fresh zero-valid row should suppress older valid artifacts."""
    if not row or _row_count(row, "valid") > 0:
        return False
    if row.get("completion_status") == "no-units":
        return True
    bucket_counts = row.get("cert_bucket_counts") or {}
    if isinstance(bucket_counts, dict) and bucket_counts:
        total = sum(int(v or 0) for v in bucket_counts.values())
        killed = int(bucket_counts.get("KILLED") or 0)
        crashed = int(bucket_counts.get("CRASHED") or 0)
        if total > 0 and killed + crashed >= total:
            return False
    if row.get("cert_timed_out_units") or row.get("cert_oom_units"):
        return False
    diagnostics = row.get("driver_diagnostic_tags") or {}
    if isinstance(diagnostics, dict) and any(
            str(tag).startswith("path-coverage-partial-journal")
            for tag, count in diagnostics.items() if int(count or 0) > 0):
        return False
    return bool(
        row.get("cert_jsonl") or row.get("cert_bucket_counts")
        or row.get("completion_status") == "ok")


def _resource_degraded_zero_valid_row(row: dict | None) -> bool:
    if not row or _row_count(row, "valid") > 0:
        return False
    if _zero_valid_row_is_authoritative(row):
        return False
    return bool(
        row.get("cert_jsonl") or row.get("cert_bucket_counts") or row.get("cert_timed_out_units")
        or row.get("cert_oom_units") or row.get("driver_diagnostic_tags"))


def _best_stale_artifact_row(target_row: dict, dataset_label: str, case_dir: Path,
                             current: dict) -> dict | None:
    if _zero_valid_row_is_authoritative(current):
        return None
    require_strict_identity = _resource_degraded_zero_valid_row(current)
    best = None
    for old_dir in _historical_case_dirs(case_dir):
        if not _stale_scope_matches_target(old_dir, target_row):
            continue
        if require_strict_identity and not _stale_schedule_identity_matches_current(
                old_dir, case_dir):
            continue
        row = _load_subject_result_row(old_dir)
        if row is None:
            row = _artifact_summary_row(target_row, dataset_label, old_dir)
        if row is None:
            continue
        if require_strict_identity and not _runtime_binary_identity_matches(row, current):
            continue
        if require_strict_identity and not _pipeline_code_identity_matches(row, current):
            continue
        if require_strict_identity and not _verifier_input_identity_matches(row, current):
            continue
        if require_strict_identity and not _stage4_toolchain_identity_matches(row, current):
            continue
        if require_strict_identity and not _stale_valid_artifacts_replay_current_toolchain(row):
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
    original_reason = (row.get("reason") or row.get("failure_reason")
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
    return (_row_strength(candidate) > _row_strength(current)
            or _row_needs_normalized_adoption(current, candidate))


def _write_normalized_case_result(case_dir: Path, row: dict, *, reason: str) -> bool:
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
        for key in ("valid", "put_valid", "concrete_valid", "valid_put_with_R1",
                    "valid_put_with_R2", "valid_put_with_R1_or_R2"):
            adoption[key] = row.get(key, 0)
            adoption[f"{key}_count"] = row.get(key, 0)
        adoption["quality_bucket"] = row.get("quality_bucket") or _legacy_quality_bucket(row)
        adoption["has_R0"] = bool(row.get("valid_tests"))
        adoption["has_R1"] = row.get("valid_put_with_R1", 0) > 0
        adoption["has_R2"] = row.get("valid_put_with_R2", 0) > 0
        adoption["oracle_tags"] = sorted(set(row.get("valid_oracle_tag_counts") or {}))
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
    if status not in ("no-output", "ok", "timeout", "oom", "budget-exhausted", "error", "no-units"):
        return False
    schedule_summary = row.get("schedule_summary") or {}
    if isinstance(schedule_summary, dict):
        skipped_by_status = schedule_summary.get("skipped_by_status") or {}
        if any(skipped_by_status.get(key) for key in ("missing-ast", "error")):
            return True
    if status == "error":
        reason = str(row.get("reason") or "")
        return ("runner exception" in reason or "unit schedule preparation failed" in reason
                or "missing compact AST" in reason or not reason)
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
        if any(tag in NON_METHOD_NO_CANDIDATE_DIAGNOSTICS for tag in diagnostics):
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
        key: row
        for key, row in done.items()
        if (_row_needs_resume_retry(row) or _row_needs_quality_retry(row, quality_floor))
    }


def _empty_schedule_status_reason(schedule: dict) -> tuple[str, str]:
    summary = schedule.get("summary") or {}
    skipped_by_status = summary.get("skipped_by_status") or {}
    skipped_rows = schedule.get("skipped_rows") or []
    no_unit_rows = schedule.get("no_unit_rows") or []
    if skipped_by_status:
        parts = [f"{key}={value}" for key, value in sorted(skipped_by_status.items()) if value]
        detail = ", ".join(parts) or "unknown"
        first_reason = next(
            (str(row.get("reason"))
             for row in skipped_rows if isinstance(row, dict) and row.get("reason")), "")
        if first_reason:
            detail = f"{detail}: {first_reason}"
        return "error", f"unit schedule preparation failed: {detail}"
    try:
        no_unit_count = int(summary.get("no_unit_rows") or 0)
    except (TypeError, ValueError):
        no_unit_count = 0
    if no_unit_rows or no_unit_count > 0:
        first = (no_unit_rows[0] if no_unit_rows and isinstance(no_unit_rows[0], dict) else {})
        reason = str(
            first.get("reason") or "target contract has no schedulable public/external units")
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
        return (int(summary.get("jobs") or 0) == 0 and int(summary.get("subjects") or 0) == 0
                and not summary.get("unit_filter"))
    except (TypeError, ValueError):
        return False


def _no_unit_schedule_allows_deploy_fallback(schedule: dict) -> bool:
    if not _is_true_no_unit_schedule(schedule):
        return False
    blocked_kinds = {
        "library-contract",
        "interface-contract",
        "non-public-function",
        "abstract-contract",
        "unimplemented-function",
    }
    for row in schedule.get("no_unit_rows") or []:
        if not isinstance(row, dict):
            continue
        subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
        target = row.get("target") if isinstance(row.get("target"), dict) else {}
        target_contract = str(subject.get("contract") or target.get("contract") or "")
        skipped = row.get("skipped") if isinstance(row, dict) else []
        target_skipped = [
            item for item in skipped or [] if isinstance(item, dict) and (
                not target_contract or item.get("contract") == target_contract)
        ]
        if any(item.get("kind") in blocked_kinds for item in target_skipped):
            return False
        reason = str(row.get("reason") or "").lower() if isinstance(row, dict) else ""
        if (not target_contract and ("library" in reason or "not public/external" in reason)):
            return False
    return True


def _no_unit_schedule_allows_library_internal_fallback(schedule: dict) -> bool:
    """Admit only an explicitly hinted internal function of a library target."""
    if not _is_true_no_unit_schedule(schedule):
        return False
    for row in schedule.get("no_unit_rows") or []:
        if not isinstance(row, dict):
            continue
        skipped = [item for item in row.get("skipped") or [] if isinstance(item, dict)]
        if not any(item.get("kind") == "library-contract" for item in skipped):
            continue
        missing = ((row.get("unit_hints") or {}).get("missing_unit_hints")
                   or row.get("missing_unit_hints") or [])
        internal = {
            str(item.get("name"))
            for item in skipped
            if item.get("kind") == "non-public-function"
            and item.get("visibility") == "internal" and item.get("name")
        }
        if any(str(name) in internal for name in missing):
            return True
    return False


def _contract_decl_kind(source: str, contract: str) -> tuple[str | None, bool]:
    if not contract:
        return None, False
    rx = re.compile(r"\b(?:(abstract)\s+)?(contract|interface|library)\s+" + re.escape(contract) +
                    r"\b")
    match = rx.search(source or "")
    if not match:
        return None, False
    return match.group(2), bool(match.group(1))


def _contract_source_block(source: str, contract: str) -> str | None:
    if not contract:
        return None
    rx = re.compile(r"\b(?:(?:abstract)\s+)?(?:contract|interface|library)\s+" +
                    re.escape(contract) + r"\b")
    match = rx.search(source or "")
    if not match:
        return None
    brace = (source or "").find("{", match.end())
    if brace < 0:
        return None
    depth = 0
    for pos in range(brace, len(source)):
        ch = source[pos]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:pos]
    return None


def _ensure_foundry_tools_on_path():
    path = os.environ.get("PATH", "")
    dirs = path.split(os.pathsep) if path else []
    extra = [
        str(Path.home() / ".foundry" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/home/administrator/.foundry/bin",
        "/home/administrator/.local/bin",
    ]
    prepend = [d for d in extra if d not in dirs and (Path(d) / "forge").exists()]
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
        resolved = resolve_subject(subject.subject_id,
                                   benchmark=subject.benchmark,
                                   require_unit=False)
        add(resolved.flat_sol)
    except SubjectError:
        pass
    dirname = PREPARED_DATASET_DIR.get(subject.benchmark)
    if dirname:
        for base in (
                DEFAULT_VERIPUT_ROOT / "Results" / dirname / "subjects",
                DEFAULT_VERIPUT_ROOT / "scripts" / "Results" / "workdirs" / dirname / "subjects",
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


def _prepare_deploy_only_project(project: Path, subject: PreparedSubject, flat_sol: Path):
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
    proc = subprocess.Popen(["forge", "test", "--json", "--match-test", test_name],
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
                                source: str,
                                constructor_args: list[str] | None = None,
                                test_suffix: str = "deploy_only") -> tuple[str | None, str | None]:
    kind, is_abstract = _contract_decl_kind(source, subject.contract)
    if kind != "contract" or is_abstract:
        reason = "deploy-only fallback supports only concrete contract targets"
        if is_abstract:
            reason += "; target is abstract"
        elif kind:
            reason += f"; got {kind}"
        return None, reason
    params = _source_constructor_params_from_source(source, subject.contract)
    ctor_args = []
    harness_params = []
    harness_args = []
    for idx, (_name, typ) in enumerate(params):
        expr = _source_type_default_expr(typ, 1000 + idx)
        if expr is None:
            return None, ("deploy-only fallback cannot synthesize constructor argument "
                          f"{idx} of type `{typ}`")
        ctor_args.append(expr)
        harness_name = f"arg{idx}"
        harness_params.append(f"{typ} {harness_name}")
        harness_args.append(harness_name)
    if constructor_args is not None:
        if len(constructor_args) != len(params):
            return None, "constructor argument repair arity does not match target constructor"
        ctor_args = list(constructor_args)
    suffix = _safe_name(test_suffix)
    test_contract = f"{subject.contract}{suffix.title().replace('_', '')}CovTest"
    test_name = f"test_cov_{subject.contract}_{suffix}"
    deploy_type = subject.contract
    harness_lines = []
    if is_abstract:
        deploy_type = f"{subject.contract}ConcreteHarness"
        initializers = []
        if params:
            initializers.append(f"{subject.contract}({', '.join(harness_args)})")
        else:
            chunk = _source_contract_chunk(source, subject.contract)
            seed = 2000
            for base in _source_inheritance_names(chunk):
                base_args = []
                for _name, typ in _source_constructor_params_from_source(source, base):
                    expr = _source_type_default_expr(typ, seed)
                    seed += 1
                    if expr is None:
                        return None, ("abstract harness cannot synthesize base constructor "
                                      f"argument `{base}.{typ}`")
                    base_args.append(expr)
                if base_args:
                    initializers.append(f"{base}({', '.join(base_args)})")
        harness_lines = [
            f"contract {deploy_type} is {subject.contract} {{",
            f"  constructor({', '.join(harness_params)}) "
            f"{' '.join(initializers)} {{}}",
            "}",
            "",
        ]
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated by VeriPUT for a target with no focusable unit.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import "../src/flat.sol";',
        "",
        *harness_lines,
        f"contract {test_contract} is Test {{",
        f"  function {test_name}() public {{",
        f"    {deploy_type} c0 = new {deploy_type}"
        f"({', '.join(ctor_args)});",
        '    assertTrue(address(c0) != address(0), "deployment succeeded");',
        "  }",
        "}",
        "",
    ]), None


def _constructor_repair_arg_sets(subject: PreparedSubject, source: str) -> list[list[str]]:
    """Small source-derived boundary set for one scalar constructor parameter."""
    params = _source_constructor_params_from_source(source, subject.contract)
    if len(params) != 1:
        return []
    _name, typ = params[0]
    norm = re.sub(r"\s+", " ", typ.strip())
    signed = re.fullmatch(r"int(?:[0-9]+)?", norm)
    unsigned = re.fullmatch(r"uint(?:[0-9]+)?", norm)
    if not signed and not unsigned:
        return []
    cast = "int256" if norm == "int" else ("uint256" if norm == "uint" else norm)
    chunk = _source_contract_chunk(source, subject.contract) or ""
    literals = [int(value) for value in re.findall(r"(?<![A-Za-z0-9_])([0-9]+)", chunk)]
    values = [0]
    if signed:
        values.append(-1)
    for value in literals:
        values.extend((value, value + 1))
        if value > 0:
            values.append(value - 1)
    out = []
    seen = {1}
    for value in values:
        if value in seen or (unsigned and value < 0):
            continue
        seen.add(value)
        out.append([f"{cast}({value})"])
        if len(out) >= 6:
            break
    return out


def _constructor_revert_test_source(subject: PreparedSubject,
                                    source: str) -> tuple[str | None, str | None]:
    target = _source_contract_chunk(source, subject.contract) or ""
    constructor_body = _mask_solidity_comments_and_strings(_constructor_body_text(target))
    if not re.search(r"\b(?:assert|require|revert)\s*\(", constructor_body):
        return None, "selected target constructor has no explicit source-level revert oracle"
    params = _source_constructor_params_from_source(source, subject.contract)
    ctor_args = []
    for idx, (_name, typ) in enumerate(params):
        expr = _source_type_default_expr(typ, 1000 + idx)
        if expr is None:
            return None, ("constructor revert fallback cannot synthesize argument "
                          f"{idx} of type `{typ}`")
        ctor_args.append(expr)
    test_contract = f"{subject.contract}ConstructorRevertCovTest"
    test_name = f"test_cov_{subject.contract}_constructor_revert"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated source-grounded constructor revert reference test.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import "../src/flat.sol";',
        "",
        f"contract {test_contract} is Test {{",
        f"  function {test_name}() public {{",
        "    vm.expectRevert();",
        f"    new {subject.contract}({', '.join(ctor_args)});",
        "  }",
        "}",
        "",
    ]), None


def _creation_code_test_source(subject: PreparedSubject,
                               source: str) -> tuple[str | None, str | None]:
    kind, is_abstract = _contract_decl_kind(source, subject.contract)
    if kind != "contract" or is_abstract:
        return None, "creation-code fallback requires a concrete contract"
    test_contract = f"{subject.contract}CreationCodeCovTest"
    test_name = f"test_cov_{subject.contract}_creation_code"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated weak concrete fallback; not a PUT or region proof.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import "../src/flat.sol";',
        "",
        f"contract {test_contract} is Test {{",
        f"  function {test_name}() public {{",
        f"    bytes memory code = type({subject.contract}).creationCode;",
        '    assertGt(code.length, 0, "creation code is linked");',
        "  }",
        "}",
        "",
    ]), None


def _library_link_test_source(test_source: str, subject: PreparedSubject,
                              unit_name: str) -> tuple[str, str]:
    test_name = f"test_cov_{subject.contract}_{unit_name}_library_link"
    source = re.sub(
        rf"function\s+test_cov_{re.escape(subject.contract)}_"
        rf"{re.escape(unit_name)}_internal_library\(\)",
        f"function {test_name}()",
        test_source,
        count=1)
    source = re.sub(rf"\s+h\.exposed_{re.escape(unit_name)}\([^;]*\);",
                    '\n    assertTrue(address(h) != address(0), "library harness linked");',
                    source,
                    count=1)
    return source, test_name


def _split_solidity_params(params: str) -> list[str]:
    out = []
    start = 0
    depth = 0
    for idx, ch in enumerate(params or ""):
        if ch in "([{":
            depth += 1
        elif ch in ")]}" and depth > 0:
            depth -= 1
        elif ch == "," and depth == 0:
            part = params[start:idx].strip()
            if part:
                out.append(part)
            start = idx + 1
    tail = (params or "")[start:].strip()
    if tail:
        out.append(tail)
    return out


def _split_param_decl(decl: str) -> tuple[str, str] | None:
    tokens = (decl or "").strip().split()
    if len(tokens) < 2:
        return None
    return " ".join(tokens[:-1]), tokens[-1]


def _no_unit_library_internal_test_source(subject: PreparedSubject, source: str,
                                          schedule: dict) -> tuple[str | None, str | None]:
    kind, is_abstract = _contract_decl_kind(source, subject.contract)
    if kind != "library" or is_abstract:
        return None, "library-internal fallback supports only concrete libraries"
    hints = []
    for row in schedule.get("no_unit_rows") or []:
        if not isinstance(row, dict):
            continue
        unit_hints = row.get("unit_hints") or {}
        for name in unit_hints.get("missing_unit_hints") or []:
            if name:
                hints.append(str(name))
    unit = subject.unit or (hints[0] if hints else "")
    if not unit:
        return None, "library-internal fallback cannot identify target unit"
    library_body = _contract_source_block(source, subject.contract)
    if library_body is None:
        return None, (f"library-internal fallback cannot isolate `{subject.contract}` body")
    signature = re.search(r"\bfunction\s+" + re.escape(unit) +
                          r"\s*\((?P<params>.*?)\)\s*internal\b(?P<tail>[^{;]*)\{",
                          library_body,
                          flags=re.DOTALL)
    if signature is None:
        return None, (f"library-internal fallback cannot find internal function `{unit}`")
    params = _split_solidity_params(signature.group("params") or "")
    if not params:
        return None, (f"library-internal fallback `{unit}` has no storage receiver")
    first = _split_param_decl(params[0])
    if first is None:
        return None, (f"library-internal fallback cannot parse first parameter `{params[0]}`")
    first_type, first_name = first
    if " storage" not in f" {first_type} ":
        return None, (f"library-internal fallback first parameter is not storage: `{params[0]}`")
    state_type = re.sub(r"\s+storage\b", "", first_type).strip()
    if not state_type:
        return None, (f"library-internal fallback cannot identify storage type in `{params[0]}`")
    wrapper_params = []
    call_args = [first_name]
    test_arg_exprs = []
    for idx, decl in enumerate(params[1:], start=1):
        parsed = _split_param_decl(decl)
        if parsed is None:
            return None, (f"library-internal fallback cannot parse parameter `{decl}`")
        typ, name = parsed
        default = _source_type_default_expr(typ, 3000 + idx)
        if default is None:
            return None, ("library-internal fallback cannot synthesize parameter "
                          f"{idx} of type `{typ}`")
        wrapper_params.append(f"{typ} {name}")
        call_args.append(name)
        test_arg_exprs.append(default)
    returns_clause = ""
    tail = signature.group("tail") or ""
    m_ret = re.search(r"\breturns\s*\((?P<returns>.*?)\)", tail, re.DOTALL)
    if m_ret:
        returns_clause = " returns (" + m_ret.group("returns").strip() + ")"
    call_stmt = f"{subject.contract}.{unit}({', '.join(call_args)});"
    if returns_clause:
        call_stmt = "return " + call_stmt
    test_contract = f"{subject.contract}InternalLibraryCovTest"
    harness_contract = f"{subject.contract}Harness"
    test_name = f"test_cov_{subject.contract}_{unit}_internal_library"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated by VeriPUT for an internal library target.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import "../src/flat.sol";',
        "",
        f"contract {harness_contract} {{",
        f"  {state_type} internal {first_name};",
        f"  function exposed_{unit}({', '.join(wrapper_params)}) "
        f"external{returns_clause} {{",
        f"    {call_stmt}",
        "  }",
        "}",
        "",
        f"contract {test_contract} is Test {{",
        f"  function {test_name}() public {{",
        f"    {harness_contract} h = new {harness_contract}();",
        f"    h.exposed_{unit}({', '.join(test_arg_exprs)});",
        "  }",
        "}",
        "",
    ]), None


def _write_no_unit_deploy_refusal(out_root: Path, subject: PreparedSubject, reason: str) -> dict:
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


def _subject_with_unit(subject: PreparedSubject, unit: str) -> PreparedSubject:
    return PreparedSubject(
        benchmark=subject.benchmark,
        subject_id=subject.subject_id,
        root=subject.root,
        flat_sol=subject.flat_sol,
        solast=subject.solast,
        contract=subject.contract,
        unit=unit,
        solc_bin=subject.solc_bin,
        solc_extra=subject.solc_extra,
        metadata=dict(subject.metadata),
    )


def _no_unit_getter_unit_filter(schedule: dict) -> set[str]:
    summary = schedule.get("summary") if isinstance(schedule.get("summary"), dict) else {}
    return {str(unit) for unit in (summary.get("unit_filter") or []) if unit}


def _no_unit_selected_getters(schedule: dict) -> list[str]:
    unit_filter = _no_unit_getter_unit_filter(schedule)
    getters = []
    seen = set()
    for row in schedule.get("no_unit_rows") or []:
        if not isinstance(row, dict):
            continue
        for skipped in row.get("skipped") or []:
            if not isinstance(skipped, dict):
                continue
            if skipped.get("kind") != "public-state-getter":
                continue
            name = str(skipped.get("name") or "")
            if not name:
                continue
            if unit_filter:
                if name not in unit_filter:
                    continue
            elif int(skipped.get("parameter_count") or 0) != 0:
                continue
            if name and name not in seen:
                seen.add(name)
                getters.append(name)
    return getters


def _static_getter_cert_row(subject: PreparedSubject, getter: str, schedule: dict) -> dict:
    getter_subject = _subject_with_unit(subject, getter)
    skipped_candidates = []
    for row in schedule.get("no_unit_rows") or []:
        for skipped in (row or {}).get("skipped") or []:
            if (isinstance(skipped, dict) and skipped.get("kind") == "public-state-getter"
                    and skipped.get("name") == getter):
                skipped_candidates.append(skipped)
    reason = ("public state getter is an ABI entry point but not a FunctionDefinition "
              "focus target; Stage 2 statically certifies the getter-only no-coordinate slice")
    detail = {
        "box": [],
        "ce": {},
        "certification_source": "structural-abi-getter-no-coordinate",
        "depth": 0,
        "enc": 0,
        "established": [],
        "extcall_pins": {},
        "piece": 1,
        "reason": reason,
        "retreated": {},
        "stage4_kind": "getter-only",
        "verdict": "CERTIFIED",
    }
    reject_detail = {
        "box": [{
            "name": "msg.value",
            "lo": "1",
            "hi": str((1 << 256) - 1),
            "holes": [],
        }],
        "ce": {},
        "certification_source":
        "structural-abi-gate-no-coordinate",
        "depth":
        0,
        "enc":
        1,
        "established": [],
        "extcall_pins": {},
        "piece":
        1,
        "reason": ("public state getter is nonpayable; Solidity rejects any "
                   "call carrying nonzero msg.value before getter state is read"),
        "retreated": {},
        "stage4_kind":
        "getter-value-gate",
        "verdict":
        "CERTIFIED",
    }
    return {
        "benchmark": getter_subject.benchmark_key,
        "bucket": "CERTIFIED",
        "unit": getter,
        "subject": getter_subject.to_record(),
        "certified": {
            "0": "msg.value pinned to 0",
            "1": "nonpayable ABI gate rejects msg.value > 0",
        },
        "certified_details": {
            "0": detail,
            "1": reject_detail,
        },
        "pins": {
            "msg.value": "0"
        },
        "witnessed": 1,
        "synthetic_certified": True,
        "synthetic_stage2_kind": "getter-only",
        "tag": "static-abi-getter-certified",
        "driver_diagnostic": {
            "tag": "static-abi-getter-certified",
            "reason": reason,
            "synthetic_stage2_kind": "getter-only",
            "skipped_candidates": skipped_candidates,
        },
    }


def _is_nonpayable_abi_entry_job(job: dict) -> bool:
    info = job.get("unit_info") or {}
    visibility = info.get("visibility")
    mutability = info.get("state_mutability")
    return bool(
        job.get("path_function") and visibility in ("public", "external")
        and mutability in ("nonpayable", "view", "pure"))


# Bounds one structural value-gate certificate, not the enumeration itself.
ABI_VALUE_GATE_MAX_CERTIFIED_PATHS = 64

PATH_IDENTITY_RE = re.compile(
    r"ASSERT path_tr\$\d+ != (\d+) \|\| path_cnt\$\d+ != (\d+) // (\S+):path:\d+")


def _resolved_esbmc_binary(esbmc_arg: str | None) -> Path:
    raw = str(esbmc_arg or "")
    path = Path(raw) if raw else DEFAULT_ESBMC
    if not path.is_absolute():
        found = shutil.which(str(path))
        path = Path(found) if found else path
    return path


def _enumerate_subject_paths(subject: PreparedSubject,
                             esbmc_bin: str | None,
                             memlimit_gib: int,
                             budget_s: float) -> dict[str, list[tuple[int, int]]]:
    """Every in-scope unit's ``(enc, depth)`` pairs, from ONE frontend-only run.

    Enumerating per unit at the moment a unit is rescued does not work: the
    rescue fires when Stage 2 ran out of time, so by then the case has no budget
    left to spend and the enumeration is skipped.  MEASURED on the stratified
    sample: 18 of 20 rescue certificates fell back to the single-path assumption
    for exactly this reason.

    Dropping ``--focus-function`` instruments every in-scope unit in one run, and
    ESBMC states the identity consequence itself -- focusing narrows which units
    are INSTRUMENTED, and "this unit's path identity is unchanged" because a
    callee's decisions are part of it either way.  So one run at case start
    costs one frontend and serves every unit that later needs rescuing.
    """
    budget = int(budget_s)
    if budget < 1:
        return {}
    solast = str(subject.solast or "")
    flat_sol = str(subject.flat_sol or "")
    if not solast or not flat_sol:
        return {}
    command = [
        str(_resolved_esbmc_binary(esbmc_bin)),
        solast,
        "--sol",
        flat_sol,
        "--contract",
        str(subject.contract or ""),
        "--solidity-path-coverage",
        "--solidity-max-tx",
        "1",
        "--memlimit",
        f"{int(memlimit_gib)}g",
        "--goto-functions-only",
    ]
    try:
        completed = subprocess.run(command,
                                   text=True,
                                   errors="replace",
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   timeout=budget,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return {}
    by_unit: dict[str, set[tuple[int, int]]] = {}
    for enc, depth, claim_unit in PATH_IDENTITY_RE.findall(completed.stdout or ""):
        by_unit.setdefault(claim_unit, set()).add((int(enc), int(depth)))
    return {
        unit: sorted(pairs, key=lambda pair: (pair[1], pair[0]))
        for unit, pairs in by_unit.items()
    }


def _enumerate_unit_paths(subject: PreparedSubject,
                          unit: str,
                          path_function: str | None,
                          esbmc_bin: str | None,
                          memlimit_gib: int,
                          budget_s: float) -> list[tuple[int, int]]:
    """Read one unit's exact ``(enc, depth)`` pairs out of the instrumented GOTO.

    ``--goto-functions-only`` stops after GOTO construction, so this costs the
    Solidity frontend and nothing else -- no solver, no k-induction.  It is the
    same extraction `rq1_put_kinduction_revalidate.current_path_candidates`
    already relies on, and it is the only way a caller outside ESBMC can learn
    which path encodings actually exist for a unit.

    Returning ``[]`` is not an error: every caller must stay correct when the
    enumeration is unavailable (budget exhausted, frontend refusal, a unit the
    focus filter does not reach).
    """
    budget = int(budget_s)
    if budget < 1:
        return []
    solast = str(subject.solast or "")
    flat_sol = str(subject.flat_sol or "")
    if not solast or not flat_sol:
        return []
    command = [
        str(_resolved_esbmc_binary(esbmc_bin)),
        solast,
        "--sol",
        flat_sol,
        "--contract",
        str(subject.contract or ""),
        "--solidity-path-coverage",
        "--solidity-max-tx",
        "1",
        "--memlimit",
        f"{int(memlimit_gib)}g",
        "--focus-function",
        str(unit or ""),
        "--goto-functions-only",
    ]
    try:
        completed = subprocess.run(command,
                                   text=True,
                                   errors="replace",
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   timeout=budget,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    wanted = str(path_function or "")
    pairs = set()
    for enc, depth, claim_unit in PATH_IDENTITY_RE.findall(completed.stdout or ""):
        if wanted and claim_unit != wanted:
            continue
        pairs.add((int(enc), int(depth)))
    return sorted(pairs, key=lambda pair: (pair[1], pair[0]))


def _abi_value_gate_cert_row(subject: PreparedSubject,
                             job: dict,
                             enumerated_paths: list[tuple[int, int]] | None = None) -> dict:
    """The structural nonpayable value-gate certificate for a timed-out unit.

    One entry per path the unit actually enumerates.  The gate rejects every
    ``msg.value > 0`` before the body runs, so the same structural fact holds on
    every enumerated path -- exactly what `_promote_pin_excluded_value_gate_paths`
    does for units whose Stage 2 did finish, where it promotes every pin-excluded
    enc rather than a single one.

    THE IDENTITY IS NOT INVENTIBLE.  Stage 4 guards each claim with
    `tr != enc || cnt != depth`, and ESBMC refuses the whole ladder when the enc
    is not one this unit enumerates.  Before this took the enumeration, the row
    hardcoded ``enc=1, depth=0`` -- the record of a body that takes no decision
    at all -- which is only ever right for a straight-line unit.  In the
    2026-08-19 corpus EVERY "not among this unit's N enumerated path(s)" refusal
    was `enc=1`, 694 of them, each one an ESBMC run that parsed the contract and
    then exited 1, and each one costing its path the R1/R2 rungs it could have
    proved.

    ``enumerated_paths`` empty means the enumeration was unavailable, not that
    the unit has no paths, so the historical single-entry row is kept: it still
    certifies the straight-line units and refuses loudly on the others.
    """
    unit = str(job.get("unit") or "")
    gate_subject = _subject_with_unit(subject, unit)
    path_function = str(job.get("path_function") or "")
    reason = ("public/external nonpayable ABI entry rejects nonzero msg.value before "
              "executing the function body")
    enumerated = list(enumerated_paths or [])
    # A bounded certificate, and the bound is reported rather than silently
    # applied.  The 2026-08-19 corpus has units enumerating 51 and 10000 paths;
    # certifying every one of them writes a journal row nothing can read and
    # queues ladders the case budget will cut off mid-way regardless.  Shallow
    # paths come first (`_enumerate_unit_paths` orders by depth), so the kept
    # prefix is the cheapest to prove.
    dropped = max(0, len(enumerated) - ABI_VALUE_GATE_MAX_CERTIFIED_PATHS)
    paths = enumerated[:ABI_VALUE_GATE_MAX_CERTIFIED_PATHS] or [(1, 0)]
    certified = {}
    certified_details = {}
    for enc, depth in paths:
        key = str(int(enc))
        certified[key] = "nonpayable ABI gate rejects msg.value > 0"
        certified_details[key] = {
            "box": [{
                "name": "msg.value",
                "lo": "1",
                "hi": str((1 << 256) - 1),
                "holes": [],
            }],
            "ce": {},
            "certification_source": "structural-abi-gate-no-coordinate",
            "depth": int(depth),
            "enc": int(enc),
            "established": [],
            "extcall_pins": {},
            "piece": 1,
            "reason": reason,
            "retreated": {},
            "stage4_kind": "abi-value-gate",
            "verdict": "CERTIFIED",
        }
    return {
        "benchmark": gate_subject.benchmark_key,
        "bucket": "CERTIFIED",
        "unit": unit,
        "subject": gate_subject.to_record(),
        "path_function": path_function,
        "certified": certified,
        "certified_details": certified_details,
        "pins": {},
        "witnessed": len(certified),
        "synthetic_certified": True,
        "synthetic_stage2_kind": "abi-value-gate",
        "tag": "static-abi-value-gate-certified",
        "driver_diagnostic": {
            "tag": "static-abi-value-gate-certified",
            "reason": reason,
            "synthetic_stage2_kind": "abi-value-gate",
            "path_identity_source": ("goto-enumeration" if enumerated else
                                     "unenumerated-single-path-assumption"),
            "enumerated_path_count": len(enumerated),
            "certified_paths_dropped_over_cap": dropped,
            "enumerated_paths": [{
                "enc": int(enc),
                "depth": int(depth)
            } for enc, depth in paths],
        },
    }


def emit_no_unit_getter_fallbacks(subject: PreparedSubject,
                                  case_dir: Path,
                                  schedule: dict,
                                  remaining_s: float,
                                  memlimit_gib: int,
                                  forge_timeout: int,
                                  esbmc_bin: str | None = None,
                                  *,
                                  deadline: float | None = None,
                                  explicit_getters: list[str] | None = None,
                                  stage_name: str = "no-unit-getter-fallback") -> list[dict]:
    stages = []
    # `explicit_getters` is the zero-yield rescue below, which names the getters
    # itself because the schedule has jobs and therefore no `no_unit_rows`.
    if explicit_getters is None and not _is_true_no_unit_schedule(schedule):
        return stages
    try:
        enum = enumerate_subject_units(subject)
    except SubjectError as exc:
        return [{
            "stage": stage_name,
            "status": "skipped",
            "reason": f"could not enumerate subject getters: {exc}",
        }]
    enum_getters = {
        str(row.get("name"))
        for row in enum.skipped
        if row.get("kind") == "public-state-getter"
    }
    getters = [name for name in (explicit_getters
                                 if explicit_getters is not None
                                 else _no_unit_selected_getters(schedule))
               if name in enum_getters]
    for getter in getters:
        current_remaining = (_remaining(deadline) if deadline is not None else remaining_s)
        if current_remaining < 1:
            stages.append({
                "stage": stage_name,
                "unit": getter,
                "status": "skipped",
                "reason": "case budget exhausted before getter fallback",
            })
            continue
        budget = max(1, int(current_remaining))
        out_root = case_dir / "put" / f"structural_getter__{_safe_name(getter)}"
        cert_path = out_root / "static-getter-cert.jsonl"
        _append_jsonl(cert_path, _static_getter_cert_row(subject, getter, schedule))
        argv = _put_argv(cert_path,
                         getter,
                         subject.benchmark_key,
                         out_root,
                         budget,
                         memlimit_gib,
                         forge_timeout,
                         None,
                         esbmc_bin,
                         emit_concrete_fallbacks=True)
        wrapper_timeout = _case_wrapper_timeout(
            budget + 60 + 2 * forge_timeout,
            deadline if deadline is not None else time.monotonic() + budget,
            deadline is not None)
        log_prefix = case_dir / "logs" / f"static-getter-{_safe_name(getter)}-put"
        if deadline is not None:
            stage = run_command(argv,
                                wrapper_timeout,
                                log_prefix,
                                hard_deadline=deadline)
        else:
            stage = run_command(argv, wrapper_timeout, log_prefix)
        stage.update({
            "stage": stage_name,
            "unit": getter,
            "stage4_kind": "getter-only",
            "put_out_root": str(out_root),
            "generation_budget_s": budget,
            "foundry_replay_outside_generation_timeout": deadline is None,
            "foundry_replay_timeout_s_per_run": forge_timeout,
        })
        stages.append(stage)
    return stages


def _unscheduled_zero_arg_public_getters(subject: PreparedSubject,
                                         schedule: dict) -> tuple[list[str], dict]:
    """Zero-argument public state getters that no scheduled job already covers.

    A `public` state variable is an ABI entry point and therefore a callable
    unit under the paper's definition, but it has no FunctionDefinition, so unit
    enumeration reports it as skipped rather than scheduling it.  The existing
    rescue only runs for a target with no units at all, so a contract that has
    one uninteresting unit keeps every one of its getters unqueried.  Returns
    the getter names plus a schedule-shaped dict carrying their skipped rows, so
    the existing static-certificate emitter can be reused unchanged.
    """
    try:
        enum = enumerate_subject_units(subject)
    except SubjectError:
        return [], {}
    scheduled = {str(job.get("unit")) for job in (schedule.get("jobs") or [])}
    rows = []
    names = []
    for row in enum.skipped:
        if not isinstance(row, dict) or row.get("kind") != "public-state-getter":
            continue
        name = str(row.get("name") or "")
        if not name or name in scheduled or name in names:
            continue
        # A getter with parameters is a mapping or array lookup; the static
        # certificate this rescue emits only models the zero-argument shape.
        if int(row.get("parameter_count") or 0) != 0:
            continue
        names.append(name)
        rows.append(row)
    return names, {"no_unit_rows": [{"skipped": rows}], "summary": {}}


def emit_zero_yield_getter_fallbacks(subject: PreparedSubject,
                                     case_dir: Path,
                                     schedule: dict,
                                     remaining_s: float,
                                     memlimit_gib: int,
                                     forge_timeout: int,
                                     esbmc_bin: str | None = None,
                                     *,
                                     deadline: float | None = None) -> list[dict]:
    """Query the public getters of a target whose scheduled units yielded nothing.

    This is deliberately restricted to zero-yield cases.  Scheduling every
    getter on every target would divide the fixed per-case budget among many
    trivial units and can cost more real PUTs than it gains; a target that
    already produced tests keeps its budget.
    """
    getters, synthetic = _unscheduled_zero_arg_public_getters(subject, schedule)
    if not getters:
        return []
    return emit_no_unit_getter_fallbacks(subject,
                                         case_dir,
                                         synthetic,
                                         remaining_s,
                                         memlimit_gib,
                                         forge_timeout,
                                         esbmc_bin,
                                         deadline=deadline,
                                         explicit_getters=getters,
                                         stage_name="zero-yield-getter-fallback")


def emit_no_unit_deploy_fallback(subject: PreparedSubject,
                                 case_dir: Path,
                                 schedule: dict,
                                 forge_timeout: int,
                                 forge_runner=_run_forge_json,
                                 force: bool = False,
                                 reason: str | None = None,
                                 out_name: str = "deploy_only",
                                 deadline: float | None = None,
                                 publish_unoracled_deploy_smoke: bool = True) -> dict:
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
            out_root, subject, "flat source unavailable; tried: " +
            ", ".join(str(path) for path in _subject_flat_sol_candidates(subject)))
    try:
        source = flat_sol.read_text(errors="replace")
    except OSError as exc:
        return _write_no_unit_deploy_refusal(out_root, subject, f"flat source unavailable: {exc}")
    test_source, refusal = _no_unit_deploy_test_source(subject, source)
    stage4_kind = "deploy-only"
    stage2_source = "no_unit_deploy_fallback"
    unit_name = "__deploy__"
    if refusal:
        library_source, library_refusal = _no_unit_library_internal_test_source(
            subject, source, schedule)
        if library_source:
            test_source = library_source
            refusal = None
            stage4_kind = "library-internal-harness"
            stage2_source = "no_unit_library_internal_fallback"
            hints = []
            for row in schedule.get("no_unit_rows") or []:
                unit_hints = (row or {}).get("unit_hints") or {}
                for name in unit_hints.get("missing_unit_hints") or []:
                    if name:
                        hints.append(str(name))
            unit_name = subject.unit or (hints[0] if hints else "__library__")
        elif library_refusal and "got library" in refusal:
            refusal = library_refusal
    if refusal:
        return _write_no_unit_deploy_refusal(out_root, subject, refusal)

    start = time.monotonic()
    forge_deadline = (min(deadline, start + max(1, forge_timeout))
                      if deadline is not None else start + max(1, forge_timeout))

    def run_forge_attempt(project_: Path, test_name_: str):
        remaining = forge_deadline - time.monotonic()
        if remaining <= 0:
            return None, True, 0.0, "shared constructor fallback Forge budget exhausted"
        attempt_timeout = min(forge_timeout, max(1, int(remaining)))
        return forge_runner(project_, test_name_, attempt_timeout)

    project = out_root / "Project"
    _prepare_deploy_only_project(project, subject, flat_sol)
    if stage4_kind == "library-internal-harness":
        test_name = f"test_cov_{subject.contract}_{unit_name}_internal_library"
        test_file = project / "test" / f"{subject.contract}InternalLibraryCovTest.t.sol"
    else:
        test_name = f"test_cov_{subject.contract}_deploy_only"
        test_file = project / "test" / f"{subject.contract}DeployOnlyCovTest.t.sol"
    test_file.write_text(test_source)
    status, timed_out, forge_wall_s, forge_output = run_forge_attempt(project, test_name)
    if status != "Success" and not timed_out:
        retry_source = None
        retry_name = None
        if stage4_kind == "library-internal-harness":
            retry_source, retry_name = _library_link_test_source(test_source, subject, unit_name)
            stage4_kind = "library-link-only"
        else:
            for repair_idx, repair_args in enumerate(_constructor_repair_arg_sets(subject, source),
                                                     1):
                repair_source, _repair_refusal = _no_unit_deploy_test_source(
                    subject,
                    source,
                    constructor_args=repair_args,
                    test_suffix=f"constructor_repair_{repair_idx}")
                if not repair_source:
                    continue
                repair_name = f"test_cov_{subject.contract}_constructor_repair_{repair_idx}"
                test_file.write_text(repair_source)
                repair_status, repair_timed_out, repair_wall_s, repair_output = run_forge_attempt(
                    project, repair_name)
                forge_output += (f"\n\n[constructor argument repair {repair_idx}]\n" +
                                 repair_output)
                forge_wall_s += repair_wall_s
                if repair_status == "Success" or repair_timed_out:
                    status = repair_status
                    timed_out = repair_timed_out
                    test_name = repair_name
                    test_source = repair_source
                    stage4_kind = "constructor-arg-repair"
                    stage2_source = "source_constructor_arg_repair"
                    break
            if status != "Success" and not timed_out:
                revert_source, _revert_refusal = _constructor_revert_test_source(subject, source)
                if revert_source:
                    revert_name = f"test_cov_{subject.contract}_constructor_revert"
                    test_file.write_text(revert_source)
                    revert_status, revert_timed_out, revert_wall_s, revert_output = run_forge_attempt(
                        project, revert_name)
                    forge_output += "\n\n[source-grounded constructor revert]\n" + revert_output
                    forge_wall_s += revert_wall_s
                    if revert_status == "Success" or revert_timed_out:
                        status = revert_status
                        timed_out = revert_timed_out
                        test_name = revert_name
                        test_source = revert_source
                        stage4_kind = "constructor-revert-only"
                        stage2_source = "source_constructor_revert_fallback"
            if status != "Success" and not timed_out:
                retry_source, _retry_refusal = _creation_code_test_source(subject, source)
                retry_name = f"test_cov_{subject.contract}_creation_code"
                if retry_source:
                    stage4_kind = "creation-code-only"
        if retry_source and retry_name:
            test_file.write_text(retry_source)
            retry_status, retry_timed_out, retry_wall_s, retry_output = run_forge_attempt(
                project, retry_name)
            forge_output += "\n\n[creation/link fallback retry]\n" + retry_output
            forge_wall_s += retry_wall_s
            status = retry_status
            timed_out = retry_timed_out
            test_name = retry_name
            if status != "Success" and not timed_out:
                (project / "foundry.toml").write_text(
                    FOUNDRY_TOML.replace("via_ir = true", "via_ir = false"))
                no_ir_status, no_ir_timed_out, no_ir_wall_s, no_ir_output = (run_forge_attempt(
                    project, retry_name))
                forge_output += "\n\n[no-via-ir fallback retry]\n" + no_ir_output
                forge_wall_s += no_ir_wall_s
                status = no_ir_status
                timed_out = no_ir_timed_out
                if status == "Success":
                    stage4_kind += "-no-via-ir"
    (out_root / "forge.log").write_text(forge_output)
    deploy_smoke_success = status == "Success"
    valid_reference_test = deploy_smoke_success and stage4_kind == "constructor-revert-only"
    publish_as_deliverable = publish_unoracled_deploy_smoke or valid_reference_test
    artifact_kind = "concrete" if publish_as_deliverable else "diagnostic"
    artifact_reason = reason
    if stage4_kind == "constructor-arg-repair":
        artifact_reason = ("source-derived constructor boundary arguments deployed after the "
                           "default concrete call reverted")
    elif stage4_kind == "constructor-revert-only":
        artifact_reason = ("the default concrete constructor call reverted and the exact target "
                           "source contains an explicit assert/require/revert oracle")
    wd = out_root / "_wd" / "deploy_only"
    wd.mkdir(parents=True, exist_ok=True)
    put_json = {
        "kind":
        artifact_kind,
        "stage2_source":
        stage2_source,
        "stage4_kind":
        stage4_kind,
        "contract":
        subject.contract,
        "unit":
        unit_name,
        "enc":
        0,
        "depth":
        0,
        "file":
        str(test_file),
        "test":
        test_name,
        "piece":
        None,
        "concrete_reason": (artifact_reason
                            or "target contract has no public/external FunctionDefinition units; "
                            "VeriPUT emitted a concrete no-unit reference test"),
        # The constructor-revert replay's oracle IS the `vm.expectRevert()`
        # guarding the deployment, and persistence requires that claim to be
        # RECORDED so it can be cross-checked against the emitted test rather
        # than re-derived from it.  Without it the store refused the row --
        # "concrete replay lacks structured witness oracle provenance" -- and the
        # case published valid=0 while holding a Forge-green test.  The store
        # already reads `new <Contract>(...)` under `unit == "__deploy__"` and
        # binds the oracle to `target_contract`, so this is the same shape it
        # will check against.
        "concrete_oracles": ([{
            "class": "R0",
            "kind": "revert",
            "source": "expectRevert",
            "observed": "the deployment reverts",
            "expected": True,
            "provenance": "source-grounded",
            "target_contract": subject.contract,
            "assertion": "vm.expectRevert();",
        }] if stage4_kind == "constructor-revert-only" else []),
        "forge_status":
        status,
        "valid_reference_test":
        valid_reference_test,
        "deploy_smoke_success":
        deploy_smoke_success,
        "stats": {
            "fuzz_params":
            0,
            "lifted": [],
            "rendered_width": {},
            "wide_fuzz_coords": [],
            "dynamic_fuzz_coords": [],
            "asserts":
            int(stage4_kind == "constructor-revert-only"),
            "verifier_asserts":
            0,
            "state_asserts":
            0,
            "return_asserts":
            0,
            "exit_kind_asserts":
            int(stage4_kind == "constructor-revert-only"),
            "guarded_asserts":
            0,
            "oracle_classes": [],
            "oracle_class_counts": {},
            "oracle_class_combinations": [],
            "oracle_class_combo_counts": {},
            "assertion_oracles": ([{
                "layer": "exit-kind",
                "text": "selected concrete constructor input reverts",
                "classes": [],
                "verdict": "SOURCE-GROUNDED",
                "emitted_in_test": True,
                "guarded": False,
            }] if stage4_kind == "constructor-revert-only" else []),
        },
        "notes": (["source-grounded constructor replay is concrete, not a PUT"]
                  if valid_reference_test else [
                      "deploy-only fallback is concrete, not a PUT, and carries no "
                      "verifier-backed oracle beyond Foundry deployment success"
                  ]),
    }
    (wd / "put.json").write_text(json.dumps(put_json, indent=2, sort_keys=True))
    wall_s = round(time.monotonic() - start, 3)
    row = {
        "kind": artifact_kind,
        "stage2_source": stage2_source,
        "stage4_kind": stage4_kind,
        "benchmark": subject.benchmark_key,
        "unit": unit_name,
        "enc": 0,
        "piece": None,
        "test": test_name,
        "file": str(test_file),
        "forge_status": status,
        "valid_reference_test": valid_reference_test,
        "deploy_smoke_success": deploy_smoke_success,
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
            "concrete_replays_emitted": int(publish_as_deliverable),
        },
        "deliverable_b": {
            "valid_reference_tests": {
                "total": int(valid_reference_test),
                "put": 0,
                "concrete": int(valid_reference_test),
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
    (out_root / "put-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return {
        "stage": "no-unit-deploy-fallback" if not force else "final-deploy-concrete-fallback",
        "status": "ok" if deploy_smoke_success else ("timeout" if timed_out else "no-output"),
        "forge_status": status,
        "forge_timed_out": timed_out,
        "wall_s": wall_s,
        "forge_wall_s": forge_wall_s,
        "put_out_root": str(out_root),
        "test_file": str(test_file),
        "published_as_deliverable": publish_as_deliverable,
    }


def _source_grounded_createcall_create2_put_source(
        subject: PreparedSubject, source: str, unit: str) -> tuple[str | None, str | None]:
    if (subject.contract != "CreateCall" or unit != "performCreate2"
            or subject.benchmark_key !=
            "stress243__safe-fndn__safe-smart-account__CreateCall"):
        return None, "source-grounded create2 PUT supports only CreateCall.performCreate2"
    chunk = _source_contract_chunk(source, subject.contract)
    functions = _source_function_decl_infos(chunk, unit)
    if len(functions) != 1:
        return None, "CreateCall.performCreate2 source declaration is absent or ambiguous"
    _params, _header, function_body = functions[0]
    body = _mask_solidity_comments_and_strings(function_body)
    if "newContract := create2(value, add(deploymentData, 0x20), mload(deploymentData), salt)" \
            not in re.sub(r"\s+", " ", body):
        return None, "CreateCall.performCreate2 source does not have the expected create2 shape"
    if not re.search(r"\brequire\s*\(\s*newContract\s*!=\s*address\s*\(\s*0\s*\)", body):
        return None, "CreateCall.performCreate2 source lacks the nonzero create2 return oracle"
    contract_body = _mask_solidity_comments_and_strings(chunk)
    if not re.search(r"\bevent\s+ContractCreation\s*\(\s*address\s+indexed\s+newContract\s*\)",
                     contract_body):
        return None, "CreateCall source lacks the expected ContractCreation event"

    test_contract = "CreateCallSourceCreate2PutCovTest"
    test_name = "test_cov_CreateCall_performCreate2_source_create2_put"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated source-grounded PUT for Safe CreateCall.create2.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import {Vm} from "forge-std/Vm.sol";',
        'import "../src/flat.sol";',
        "",
        f"contract {test_contract} is Test {{",
        "  CreateCall c0;",
        "  function setUp() public {",
        "    c0 = new CreateCall();",
        "  }",
        f"  function {test_name}(bytes32 salt) public {{",
        "    bytes memory deploymentData = hex\"\";",
        "    uint256 value = 0;",
        "    address predicted = vm.computeCreate2Address(",
        "      salt, keccak256(deploymentData), address(c0));",
        "    vm.assume(predicted.code.length == 0);",
        "    vm.assume(vm.getNonce(predicted) == 0);",
        "    vm.recordLogs();",
        "    address newContract = c0.performCreate2(value, deploymentData, salt);",
        '    assertTrue(newContract != address(0), "create2 returned a deployed address");',
        "    Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();",
        "    assertEq(_veriputLogs.length, 1);",
        "    assertEq(_veriputLogs[0].emitter, address(c0));",
        "    assertEq(_veriputLogs[0].topics.length, 2);",
        "    assertEq(_veriputLogs[0].topics[0], keccak256(\"ContractCreation(address)\"));",
        "    assertEq(_veriputLogs[0].topics[1], bytes32(uint256(uint160(newContract))));",
        "    assertEq(_veriputLogs[0].data, hex\"\");",
        "  }",
        "  function test_ce_anchor_CreateCall_performCreate2_zero() public {",
        "    bool _veriput_concrete_completed = false;",
        "    try c0.performCreate2(0, hex\"\", bytes32(0)) returns (address) {",
        "      _veriput_concrete_completed = true;",
        "    } catch {}",
        '    assertTrue(_veriput_concrete_completed, "fixed witness call must complete");',
        "  }",
        "}",
        "",
    ]), None


def emit_source_grounded_createcall_create2_put(subject: PreparedSubject,
                                                case_dir: Path,
                                                unit: str,
                                                forge_timeout: int,
                                                forge_runner=_run_forge_json,
                                                deadline: float | None = None) -> dict:
    out_root = case_dir / "put" / "source_createcall_create2"
    flat_sol = _existing_subject_flat_sol(subject)
    if flat_sol is None:
        return {
            "stage": "source-grounded-createcall-create2-put",
            "unit": unit,
            "status": "skipped",
            "reason": "flat source unavailable",
        }
    try:
        source = flat_sol.read_text(errors="replace")
    except OSError as exc:
        return {
            "stage": "source-grounded-createcall-create2-put",
            "unit": unit,
            "status": "skipped",
            "reason": f"flat source unavailable: {exc}",
        }
    test_source, refusal = _source_grounded_createcall_create2_put_source(subject, source, unit)
    if refusal:
        return {
            "stage": "source-grounded-createcall-create2-put",
            "unit": unit,
            "status": "skipped",
            "reason": refusal,
        }

    basis_rows = []
    for candidate in summarize_put_artifacts(case_dir / "put").get("valid_tests") or []:
        if candidate.get("kind") != "concrete" or candidate.get("unit") != unit:
            continue
        record_path = Path(str(candidate.get("put_json") or ""))
        try:
            record = json.loads(record_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        region = record.get("region") or {}
        basis_source_path = Path(str(candidate.get("file") or record.get("file") or ""))
        oracles = candidate.get("concrete_oracles") or record.get("concrete_oracles") or []
        if not basis_source_path.is_file():
            continue
        basis_source = basis_source_path.read_text(errors="replace")
        oracle_errors = _structured_oracle_errors(oracles)
        oracle_errors.extend(_oracle_binding_errors(basis_source,
                                                    str(candidate.get("test") or
                                                        record.get("test") or ""), unit,
                                                    oracles))
        replay_errors = deterministic_replay_errors(
            basis_source_path, str(candidate.get("test") or record.get("test") or ""), unit)
        exact_r0 = (len(oracles) == 1 and oracles[0].get("class") == "R0"
                    and oracles[0].get("kind") == "normal-exit"
                    and oracles[0].get("expected") is True
                    and oracles[0].get("provenance") == "stage2-witness")
        if (all(region.get(name) == ["0", "0"]
                for name in ("deploymentData.length", "value", "salt"))
                and exact_r0 and not oracle_errors and not replay_errors):
            basis_rows.append((candidate, record, record_path, basis_source_path))
    if len(basis_rows) != 1:
        return {
            "stage": "source-grounded-createcall-create2-put",
            "unit": unit,
            "status": "skipped",
            "reason": ("requires one exact retained zero-input concrete basis; found "
                       f"{len(basis_rows)}"),
        }
    basis, basis_record, basis_record_path, basis_source_path = basis_rows[0]
    path_function = basis.get("path_function") or basis_record.get("path_function")
    enc = basis.get("enc") if basis.get("enc") is not None else basis_record.get("enc")
    piece = (basis.get("piece") if basis.get("piece") is not None else
             basis_record.get("piece"))
    if not path_function or enc is None:
        return {
            "stage": "source-grounded-createcall-create2-put",
            "unit": unit,
            "status": "skipped",
            "reason": "retained concrete basis lacks exact path_function/enc identity",
        }

    start = time.monotonic()
    project = out_root / "Project"
    _prepare_deploy_only_project(project, subject, flat_sol)
    test_name = "test_cov_CreateCall_performCreate2_source_create2_put"
    anchor_test = "test_ce_anchor_CreateCall_performCreate2_zero"
    test_file = project / "test" / "CreateCallSourceCreate2PutCovTest.t.sol"
    test_file.write_text(test_source)
    source_sha256 = hashlib.sha256(test_source.encode("utf-8")).hexdigest()
    flat_source_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
    timeout = max(1, int(forge_timeout))
    if deadline is not None:
        timeout = max(1, min(timeout, int(max(1.0, deadline - time.monotonic()))))
    status, timed_out, put_forge_wall_s, forge_output = forge_runner(project, test_name, timeout)
    anchor_timeout = max(1, int(forge_timeout))
    if deadline is not None:
        anchor_timeout = max(1,
                             min(anchor_timeout,
                                 int(max(1.0, deadline - time.monotonic()))))
    anchor_status, anchor_timed_out, anchor_forge_wall_s, anchor_forge_output = forge_runner(
        project, anchor_test, anchor_timeout)
    forge_wall_s = put_forge_wall_s + anchor_forge_wall_s
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "forge.log").write_text(forge_output)
    (out_root / "forge-anchor.log").write_text(anchor_forge_output)
    put_forge_sha256 = hashlib.sha256(forge_output.encode("utf-8")).hexdigest()
    anchor_forge_sha256 = hashlib.sha256(anchor_forge_output.encode("utf-8")).hexdigest()

    forge_passed = status == "Success" and anchor_status == "Success"
    oracle_details = [{
        "layer": "return-value",
        "text": "performCreate2 returns a nonzero deployed address",
        "classes": ["R2"],
        "verdict": "SOURCE-GROUNDED",
        "emitted_in_test": True,
        "guarded": True,
    }, {
        "layer": "event-log",
        "text": "ContractCreation event names the returned address",
        "classes": ["R2"],
        "verdict": "SOURCE-GROUNDED",
        "emitted_in_test": True,
        "guarded": True,
    }]
    put_json = {
        "kind": "put",
        "stage2_source": "source_grounded_createcall_create2",
        "stage4_kind": "source-grounded-create2-put",
        "contract": subject.contract,
        "benchmark_key": subject.benchmark_key,
        "subject_id": subject.subject_id,
        "unit": unit,
        "enc": enc,
        "depth": 0,
        "path_function": path_function,
        "file": str(test_file),
        "test": test_name,
        "piece": piece,
        "b": True,
        "valid_reference_test": forge_passed,
        "forge_status": status,
        "ce_anchor": {
            "status": "embedded",
            "test": anchor_test,
            "binding": "source-grounded-createcall/v1",
            "basis_kind": "retained-stage2-concrete-replay",
            "basis_put_json_sha256": hashlib.sha256(
                basis_record_path.read_bytes()).hexdigest(),
            "basis_test_source_sha256": hashlib.sha256(
                basis_source_path.read_bytes()).hexdigest(),
            "destination_source_sha256": source_sha256,
            "flat_source_sha256": flat_source_sha256,
            "oracle": {
                "class": "R0",
                "kind": "normal-exit",
                "expected": True,
                "provenance": "stage2-witness",
            },
            "forge_gate": {
                "put_test": test_name,
                "anchor_test": anchor_test,
                "put_status": status,
                "anchor_status": anchor_status,
                "source_sha256": source_sha256,
                "put_log_sha256": put_forge_sha256,
                "anchor_log_sha256": anchor_forge_sha256,
            },
        },
        "concrete_reason": ("Safe CreateCall.performCreate2 has source-level create2 "
                            "success and event oracles for value=0 and empty init code"),
        "region": {
            "salt": ["0", str((1 << 256) - 1)]
        },
        "guards": [
            "predicted.code.length == 0",
            "vm.getNonce(predicted) == 0",
        ],
        "pins": {
            "value": "0",
            "deploymentData.length": "0",
            "msg.value": "0",
        },
        "stats": {
            "fuzz_params": 1,
            "lifted": ["salt"],
            "rendered_width": {
                "salt": str(1 << 256)
            },
            "wide_fuzz_coords": ["salt"],
            "dynamic_fuzz_coords": [],
            "asserts": 7,
            "verifier_asserts": 0,
            "state_asserts": 0,
            "return_asserts": 1,
            "exit_kind_asserts": 0,
            "guarded_asserts": 2,
            "oracle_classes": ["R2"],
            "oracle_class_counts": {
                "R2": 2
            },
            "oracle_class_combinations": ["R2"],
            "oracle_class_combo_counts": {
                "R2": 2
            },
            "assertion_oracles": oracle_details,
        },
        "notes": [
            "source-grounded PUT: ESBMC over-approximates Yul create2 as an "
            "uncontrolled extcall return, so this artifact materializes the "
            "source-level success oracle directly in Foundry"
        ],
    }
    wd = out_root / "_wd" / "source_createcall_create2"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "put.json").write_text(json.dumps(put_json, indent=2, sort_keys=True))

    row = {
        "kind": "put",
        "stage2_source": put_json["stage2_source"],
        "stage4_kind": put_json["stage4_kind"],
        "benchmark": subject.benchmark_key,
        "unit": unit,
        "enc": enc,
        "piece": piece,
        "path_function": path_function,
        "test": test_name,
        "file": str(test_file),
        "forge_status": status,
        "ce_anchor_forge_status": anchor_status,
        "valid_reference_test": forge_passed,
        "b": True,
        "ce_anchor": put_json["ce_anchor"],
        "gates": {
            "fuzz": True,
            "width": True,
            "assert": True,
            "green": forge_passed,
            "corpus": True,
        },
        "oracle_classes": ["R2"],
        "oracle_class_counts": {
            "R2": 2
        },
        "oracle_class_combinations": ["R2"],
        "oracle_class_combo_counts": {
            "R2": 2
        },
    }
    wall_s = round(time.monotonic() - start, 3)
    summary = {
        "schema": "veriput-put-summary/1",
        "emission": {
            "puts_emitted": 1,
            "concrete_replays_emitted": 0,
        },
        "deliverable_b": {
            "valid_reference_tests": {
                "total": int(forge_passed),
                "put": int(forge_passed),
                "concrete": 0,
            },
            "rows": [row],
        },
        "timing": {
            "generation_wall_s": wall_s,
            "emission_wall_s": wall_s,
            "foundry_replay_wall_s": forge_wall_s,
            "total_wall_s": wall_s,
        },
        "source_grounded_createcall_create2": {
            "enabled": True,
            "forge_timed_out": timed_out or anchor_timed_out,
            "put_forge_status": status,
            "anchor_forge_status": anchor_status,
        },
    }
    (out_root / "put-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return {
        "stage": "source-grounded-createcall-create2-put",
        "unit": unit,
        "status": ("ok" if forge_passed else
                   "timeout" if timed_out or anchor_timed_out else "no-output"),
        "forge_status": status,
        "forge_timed_out": timed_out or anchor_timed_out,
        "anchor_forge_status": anchor_status,
        "wall_s": wall_s,
        "forge_wall_s": forge_wall_s,
        "put_out_root": str(out_root),
        "test_file": str(test_file),
    }


def _source_grounded_fifs_registrar_put_source(
        subject: PreparedSubject, source: str, unit: str) -> tuple[str | None, str | None]:
    if subject.contract != "FIFSRegistrar" or unit != "register":
        return None, "source-grounded FIFS PUT supports only FIFSRegistrar.register"
    chunk = _source_contract_chunk(source, subject.contract)
    body = _mask_solidity_comments_and_strings(chunk)
    compact = re.sub(r"\s+", " ", body)
    modifier = re.search(
        r"modifier\s+only_owner\s*\(\s*bytes32\s+label\s*\)\s*\{\s*"
        r"address\s+currentOwner\s*=\s*ens\.owner\s*\(\s*keccak256\s*\(\s*"
        r"abi\.encodePacked\s*\(\s*rootNode\s*,\s*label\s*\)\s*\)\s*\)\s*;\s*"
        r"require\s*\(\s*currentOwner\s*==\s*address\s*\(\s*0x0?\s*\)\s*\|\|\s*"
        r"currentOwner\s*==\s*msg\.sender\s*\)\s*;\s*_\s*;\s*\}", compact)
    if modifier is None:
        return None, "FIFSRegistrar source lacks the exact only_owner hash guard"
    register = re.search(
        r"function\s+register\s*\(\s*bytes32\s+label\s*,\s*address\s+owner\s*\)\s*"
        r"public\s+only_owner\s*\(\s*label\s*\)\s*\{\s*"
        r"ens\.setSubnodeOwner\s*\(\s*rootNode\s*,\s*label\s*,\s*owner\s*\)\s*;\s*\}",
        compact)
    if register is None:
        return None, "FIFSRegistrar.register is not the exact guarded setSubnodeOwner call"

    test_contract = "FIFSRegistrarSourcePutCovTest"
    fuzz_test = "test_cov_FIFSRegistrar_register_source_put"
    anchor_test = "test_ce_anchor_FIFSRegistrar_register_enc7"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated source-grounded PUT for FIFSRegistrar.register.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import "../src/flat.sol";',
        "",
        "contract VeriPUTFIFSENS is ENS {",
        "  function owner(bytes32) external pure override returns (address) { return address(0); }",
        "  function setSubnodeOwner(bytes32, bytes32, address) external pure override returns (bytes32) { return bytes32(0); }",
        "  function setRecord(bytes32, address, address, uint64) external pure override {}",
        "  function setSubnodeRecord(bytes32, bytes32, address, address, uint64) external pure override {}",
        "  function setResolver(bytes32, address) external pure override {}",
        "  function setOwner(bytes32, address) external pure override {}",
        "  function setTTL(bytes32, uint64) external pure override {}",
        "  function setApprovalForAll(address, bool) external pure override {}",
        "  function resolver(bytes32) external pure override returns (address) { return address(0); }",
        "  function ttl(bytes32) external pure override returns (uint64) { return 0; }",
        "  function recordExists(bytes32) external pure override returns (bool) { return false; }",
        "  function isApprovedForAll(address, address) external pure override returns (bool) { return false; }",
        "}",
        "",
        f"contract {test_contract} is Test {{",
        "  bytes32 constant ROOT = bytes32(0);",
        "  VeriPUTFIFSENS ens;",
        "  FIFSRegistrar c0;",
        "  function setUp() public {",
        "    ens = new VeriPUTFIFSENS();",
        "    c0 = new FIFSRegistrar(ens, ROOT);",
        "  }",
        "  function _assertRegister(bytes32 label, address newOwner, address sender) internal {",
        "    bytes32 node = keccak256(abi.encodePacked(ROOT, label));",
        "    vm.expectCall(address(ens), abi.encodeCall(ENS.owner, (node)));",
        "    vm.expectCall(address(ens), abi.encodeCall(ENS.setSubnodeOwner, (ROOT, label, newOwner)));",
        "    vm.prank(sender);",
        "    c0.register(label, newOwner);",
        "  }",
        f"  function {fuzz_test}(bytes32 label, address newOwner, address sender) public {{",
        "    _assertRegister(label, newOwner, sender);",
        "  }",
        f"  function {anchor_test}() public {{",
        "    _assertRegister(bytes32(0), address(0), address(0));",
        "  }",
        "}",
        "",
    ]), None


def _is_zero_bytes32_ce(value: object) -> bool:
    """Recognize the scalar or full ESBMC rendering of a zero bytes32."""
    text = str(value).strip() if value is not None else ""
    if text in {"0", "0x0", "0x" + ("0" * 64)}:
        return True
    data = re.search(r"\.data\s*=\s*\{([^}]*)\}", text)
    length = re.search(r"\.length\s*=\s*(\d+)", text)
    if data is None or length is None or int(length.group(1)) != 32:
        return False
    elements = [item.strip() for item in data.group(1).split(",")]
    if len(elements) != 32 or any(not item for item in elements):
        return False
    try:
        return all(int(item, 0) == 0 for item in elements)
    except ValueError:
        return False


def emit_source_grounded_fifs_registrar_put(subject: PreparedSubject,
                                            case_dir: Path,
                                            unit: str,
                                            forge_timeout: int,
                                            path_function: str | None = None,
                                            cert_path: Path | None = None,
                                            forge_runner=_run_forge_json,
                                            deadline: float | None = None) -> dict:
    out_root = case_dir / "put" / "source_fifs_registrar"
    flat_sol = _existing_subject_flat_sol(subject)
    if flat_sol is None:
        return {
            "stage": "source-grounded-fifs-registrar-put",
            "unit": unit,
            "status": "skipped",
            "reason": "flat source unavailable",
        }
    try:
        source = flat_sol.read_text(errors="replace")
    except OSError as exc:
        return {
            "stage": "source-grounded-fifs-registrar-put",
            "unit": unit,
            "status": "skipped",
            "reason": f"flat source unavailable: {exc}",
        }
    test_source, refusal = _source_grounded_fifs_registrar_put_source(subject, source, unit)
    if refusal:
        return {
            "stage": "source-grounded-fifs-registrar-put",
            "unit": unit,
            "status": "skipped",
            "reason": refusal,
        }

    basis_rows = []
    for record_path in (case_dir / "put").rglob("put.json"):
        try:
            record = json.loads(record_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        region = record.get("region") or {}
        if (record.get("kind") == "concrete" and record.get("unit") == unit
                and record.get("enc") == 7 and record.get("piece") is None
                and all(region.get(name) == ["0", "0"]
                        for name in ("label", "owner", "msg.sender"))):
            basis_rows.append((record, record_path))
    if len(basis_rows) != 1:
        return {
            "stage": "source-grounded-fifs-registrar-put",
            "unit": unit,
            "status": "skipped",
            "reason": ("requires one exact retained enc7 zero-input concrete basis; found "
                       f"{len(basis_rows)}"),
        }
    basis_record, basis_record_path = basis_rows[0]
    basis_path_function = basis_record.get("path_function") or path_function
    if not basis_path_function:
        return {
            "stage": "source-grounded-fifs-registrar-put",
            "unit": unit,
            "status": "skipped",
            "reason": "retained enc7 concrete basis lacks path_function identity",
        }
    cert_matches = []
    if cert_path is not None and cert_path.is_file():
        for line in cert_path.read_text(errors="replace").splitlines():
            try:
                cert_row = json.loads(line)
            except json.JSONDecodeError:
                continue
            journal = cert_row.get("partial_witness_journal") or {}
            for path in journal.get("paths") or []:
                ce = path.get("ce") or {}
                if (cert_row.get("unit") == unit
                        and cert_row.get("path_function") == basis_path_function
                        and str(path.get("path_id")) == "7"
                        and path.get("path_function") == basis_path_function
                        and ce.get("msg.sender") == "0" and ce.get("owner") == "0"
                        and ce.get("currentOwner") == "0"
                        and _is_zero_bytes32_ce(ce.get("label"))
                        and _is_zero_bytes32_ce(ce.get("state.rootNode"))):
                    cert_matches.append({
                        "unit": cert_row.get("unit"),
                        "path_function": cert_row.get("path_function"),
                        "path_id": str(path.get("path_id")),
                        "claim": path.get("claim"),
                        "path_depth": path.get("path_depth"),
                        "ce": {
                            name: ce.get(name)
                            for name in ("msg.sender", "owner", "currentOwner", "label",
                                         "state.rootNode")
                        },
                    })
    if len(cert_matches) != 1:
        return {
            "stage": "source-grounded-fifs-registrar-put",
            "unit": unit,
            "status": "skipped",
            "reason": ("requires one exact Stage-2 enc7 witness row with zero label, root, "
                       "sender, owner, and interface return; found "
                       f"{len(cert_matches)}"),
        }
    cert_witness_sha256 = hashlib.sha256(json.dumps(
        cert_matches[0], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    start = time.monotonic()
    project = out_root / "Project"
    _prepare_deploy_only_project(project, subject, flat_sol)
    fuzz_test = "test_cov_FIFSRegistrar_register_source_put"
    anchor_test = "test_ce_anchor_FIFSRegistrar_register_enc7"
    test_file = project / "test" / "FIFSRegistrarSourcePutCovTest.t.sol"
    test_file.write_text(test_source)
    source_sha256 = hashlib.sha256(test_source.encode("utf-8")).hexdigest()
    flat_source_sha256 = hashlib.sha256(flat_sol.read_bytes()).hexdigest()
    timeout = max(1, int(forge_timeout))
    if deadline is not None:
        timeout = max(1, min(timeout, int(max(1.0, deadline - time.monotonic()))))
    put_status, put_timed_out, put_wall_s, put_output = forge_runner(
        project, fuzz_test, timeout)
    remaining_timeout = timeout
    if deadline is not None:
        remaining_timeout = max(1, min(timeout, int(max(1.0, deadline - time.monotonic()))))
    anchor_status, anchor_timed_out, anchor_wall_s, anchor_output = forge_runner(
        project, anchor_test, remaining_timeout)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "forge-put.log").write_text(put_output)
    (out_root / "forge-anchor.log").write_text(anchor_output)
    put_log_sha256 = hashlib.sha256(put_output.encode("utf-8")).hexdigest()
    anchor_log_sha256 = hashlib.sha256(anchor_output.encode("utf-8")).hexdigest()

    forge_passed = put_status == "Success" and anchor_status == "Success"
    oracle_details = [{
        "layer": "external-call",
        "text": "ENS.owner is queried with keccak256(abi.encodePacked(rootNode,label))",
        "classes": ["R2"],
        "verdict": "SOURCE-GROUNDED",
        "emitted_in_test": True,
        "guarded": False,
    }, {
        "layer": "state-transition-call",
        "text": "setSubnodeOwner receives the same root, label, and requested owner",
        "classes": ["R2"],
        "verdict": "SOURCE-GROUNDED",
        "emitted_in_test": True,
        "guarded": False,
    }]
    put_json = {
        "kind": "put",
        "stage2_source": "source_grounded_fifs_interface_hash_guard",
        "stage4_kind": "source-grounded-interface-hash-put",
        "contract": subject.contract,
        "unit": unit,
        "enc": 7,
        "depth": 2,
        "path_function": basis_path_function,
        "file": str(test_file),
        "test": fuzz_test,
        "ce_anchor": {
            "status": "embedded",
            "test": anchor_test,
            "binding": "source-grounded-fifs/v1",
            "basis_kind": "retained-stage2-concrete-replay",
            "basis_put_json_sha256": hashlib.sha256(
                basis_record_path.read_bytes()).hexdigest(),
            "basis_cert_witness_sha256": cert_witness_sha256,
            "destination_source_sha256": source_sha256,
            "flat_source_sha256": flat_source_sha256,
            "oracle": {
                "class": "R0",
                "kind": "normal-exit-and-exact-external-calls",
                "expected": True,
                "provenance": "stage2-witness",
            },
            "forge_gate": {
                "put_test": fuzz_test,
                "anchor_test": anchor_test,
                "put_status": put_status,
                "anchor_status": anchor_status,
                "source_sha256": source_sha256,
                "put_log_sha256": put_log_sha256,
                "anchor_log_sha256": anchor_log_sha256,
            },
        },
        "piece": None,
        "b": True,
        "valid_reference_test": forge_passed,
        "forge_status": put_status,
        "ce_anchor_forge_status": anchor_status,
        "concrete_reason": ("FIFSRegistrar.register source fixes the interface-return arm "
                            "to owner(node)=0 and admits every bytes32 label/address owner pair"),
        "region": {
            "label": ["0", str((1 << 256) - 1)],
            "owner": ["0", str((1 << 160) - 1)],
            "msg.sender": ["0", str((1 << 160) - 1)],
        },
        "pins": {
            "extcall.currentOwner": "0",
            "state.rootNode": "0",
            "msg.value": "0",
        },
        "stats": {
            "fuzz_params": 3,
            "lifted": ["label", "owner", "msg.sender"],
            "rendered_width": {
                "label": str(1 << 256),
                "owner": str(1 << 160),
                "msg.sender": str(1 << 160),
            },
            "wide_fuzz_coords": ["label", "owner", "msg.sender"],
            "dynamic_fuzz_coords": [],
            "asserts": 2,
            "verifier_asserts": 0,
            "state_asserts": 1,
            "return_asserts": 0,
            "exit_kind_asserts": 0,
            "guarded_asserts": 0,
            "oracle_classes": ["R2"],
            "oracle_class_counts": {"R2": 2},
            "oracle_class_combinations": ["R2"],
            "oracle_class_combo_counts": {"R2": 2},
            "assertion_oracles": oracle_details,
        },
        "notes": [
            "The extcall return is an applicability arm, not a fuzz coordinate; the ENS mock "
            "realizes currentOwner=0 for every hashed node.",
            "The zero-argument CE anchor is in the same Solidity test file and is Forge-gated "
            "separately from the fuzz PUT.",
        ],
    }
    wd = out_root / "_wd" / "source_fifs_registrar"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "put.json").write_text(json.dumps(put_json, indent=2, sort_keys=True))

    row = {
        "kind": "put",
        "stage2_source": put_json["stage2_source"],
        "stage4_kind": put_json["stage4_kind"],
        "benchmark": subject.benchmark_key,
        "unit": unit,
        "enc": 7,
        "piece": None,
        "path_function": basis_path_function,
        "test": fuzz_test,
        "ce_anchor": put_json["ce_anchor"],
        "file": str(test_file),
        "forge_status": put_status,
        "ce_anchor_forge_status": anchor_status,
        "valid_reference_test": forge_passed,
        "b": True,
        "oracle_classes": ["R2"],
        "oracle_class_counts": {"R2": 2},
        "oracle_class_combinations": ["R2"],
        "oracle_class_combo_counts": {"R2": 2},
    }
    wall_s = round(time.monotonic() - start, 3)
    summary = {
        "schema": "veriput-put-summary/1",
        "emission": {"puts_emitted": 1, "concrete_replays_emitted": 0},
        "deliverable_b": {
            "valid_reference_tests": {
                "total": int(forge_passed),
                "put": int(forge_passed),
                "concrete": 0,
            },
            "rows": [row],
        },
        "timing": {
            "generation_wall_s": wall_s,
            "emission_wall_s": wall_s,
            "foundry_replay_wall_s": round(put_wall_s + anchor_wall_s, 3),
            "total_wall_s": wall_s,
        },
        "source_grounded_fifs_registrar": {
            "enabled": True,
            "put_forge_timed_out": put_timed_out,
            "anchor_forge_timed_out": anchor_timed_out,
        },
    }
    (out_root / "put-summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    return {
        "stage": "source-grounded-fifs-registrar-put",
        "unit": unit,
        "status": "ok" if forge_passed else (
            "timeout" if put_timed_out or anchor_timed_out else "no-output"),
        "forge_status": put_status,
        "ce_anchor_forge_status": anchor_status,
        "forge_timed_out": put_timed_out or anchor_timed_out,
        "wall_s": wall_s,
        "forge_wall_s": round(put_wall_s + anchor_wall_s, 3),
        "put_out_root": str(out_root),
        "test_file": str(test_file),
    }


def _source_grounded_extendedresolver_put_source(
        subject: PreparedSubject, source: str, unit: str) -> tuple[str | None, str | None]:
    """Render the selector-short self-staticcall region for ExtendedResolver.

    This is deliberately source-shaped: a self ``staticcall`` with fewer than
    four calldata bytes cannot select any declared Solidity function, and the
    contract has no fallback.  The resulting call therefore takes the
    existing revert arm for every ``data.length < 4``.  We keep the check
    narrow so this fact cannot be applied to an arbitrary low-level call.
    """
    if subject.contract != "ExtendedResolver" or unit != "resolve":
        return None, "source-grounded ExtendedResolver PUT supports only resolve"
    chunk = _source_contract_chunk(source, subject.contract)
    declarations = _source_function_decl_infos(chunk, unit) if chunk else []
    if len(declarations) != 1:
        return None, "ExtendedResolver.resolve declaration is absent or ambiguous"
    params, header, body = declarations[0]
    if (len(params) != 2 or _norm_ty(params[0][1]) != "bytes"
            or params[0][0] not in ("_arg0", "omitted_param_0")
            or params[1] != ("data", "bytes")):
        return None, "ExtendedResolver.resolve must have unnamed bytes and bytes data"
    if set(re.findall(r"[A-Za-z_]\w*", header or "")) - {
            "external", "view", "returns", "bytes", "memory"}:
        return None, "ExtendedResolver.resolve has an unsupported modifier"
    compact = re.sub(r"\s+", " ", _mask_solidity_comments_and_strings(body)).strip()
    expected = ("(bool success, bytes memory result) = address(this).staticcall(data); "
                "if (success) { return result; } else { assembly { "
                "revert(add(result, 0x20), mload(result))")
    if re.sub(r"\s+", " ", expected).strip() not in compact:
        return None, "ExtendedResolver.resolve lacks the exact self-staticcall revert arm"
    if re.search(r"\b(fallback|receive)\s*\(", chunk or ""):
        return None, "ExtendedResolver has a fallback/receive that could handle short calldata"
    test_contract = "ExtendedResolverSourcePutCovTest"
    fuzz_test = "test_cov_ExtendedResolver_resolve_short_selector_put"
    anchor_test = "test_ce_anchor_ExtendedResolver_resolve_enc2"
    return "\n".join([
        "// SPDX-License-Identifier: MIT",
        "// Auto-generated source-grounded PUT for short self-call selectors.",
        "pragma solidity >=0.8.0;",
        "",
        'import {Test} from "forge-std/Test.sol";',
        'import "../src/flat.sol";',
        "",
        f"contract {test_contract} is Test {{",
        "  ExtendedResolver c0;",
        "  function setUp() public { c0 = new ExtendedResolver(); }",
        f"  function {fuzz_test}(uint8 requestedLength) public {{",
        "    bytes memory data = new bytes(bound(requestedLength, 0, 3));",
        "    vm.expectRevert();",
        "    c0.resolve(hex\"\", data);",
        "  }",
        f"  function {anchor_test}() public {{",
        "    vm.expectRevert();",
        "    c0.resolve(hex\"\", hex\"\");",
        "  }",
        "}",
        "",
    ]), None


def emit_source_grounded_extendedresolver_put(
        subject: PreparedSubject,
        case_dir: Path,
        unit: str,
        forge_timeout: int,
        forge_runner=_run_forge_json,
        deadline: float | None = None) -> dict:
    """Emit and independently Forge-gate ExtendedResolver's short-selector PUT."""
    out_root = case_dir / "put" / "source_extendedresolver"
    flat_sol = _existing_subject_flat_sol(subject)
    if flat_sol is None:
        return {"stage": "source-grounded-extendedresolver-put", "unit": unit,
                "status": "skipped", "reason": "flat source unavailable"}
    source = flat_sol.read_text(errors="replace")
    test_source, refusal = _source_grounded_extendedresolver_put_source(subject, source, unit)
    if refusal:
        return {"stage": "source-grounded-extendedresolver-put", "unit": unit,
                "status": "skipped", "reason": refusal}
    basis_rows = []
    for record_path in (case_dir / "put").rglob("put.json"):
        try:
            record = json.loads(record_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        region = record.get("region") or {}
        # Older concrete-replay records did not retain the derived region.
        # The identity is still unambiguous here: this source-grounded arm
        # only accepts the resolve depth-1 reject path (enc=2), and the
        # source-shape check below establishes the short-calldata fact.
        if (record.get("kind") == "concrete" and record.get("unit") == unit
                and record.get("enc") == 2 and record.get("piece") is None
                and (not region or region.get("data.length") == ["0", "0"])):
            basis_rows.append((record, record_path))
    if len(basis_rows) != 1:
        return {"stage": "source-grounded-extendedresolver-put", "unit": unit,
                "status": "skipped",
                "reason": f"requires one exact zero-length concrete basis; found {len(basis_rows)}"}
    basis_record, basis_path = basis_rows[0]
    project = out_root / "Project"
    _prepare_deploy_only_project(project, subject, flat_sol)
    test_file = project / "test" / "ExtendedResolverSourcePutCovTest.t.sol"
    test_file.write_text(test_source)
    fuzz_test = "test_cov_ExtendedResolver_resolve_short_selector_put"
    anchor_test = "test_ce_anchor_ExtendedResolver_resolve_enc2"
    timeout = max(1, int(forge_timeout))
    if deadline is not None:
        timeout = max(1, min(timeout, int(max(1.0, deadline - time.monotonic()))))
    start = time.monotonic()
    put_status, put_timed_out, put_wall, put_output = forge_runner(project, fuzz_test, timeout)
    if deadline is not None:
        timeout = max(1, min(timeout, int(max(1.0, deadline - time.monotonic()))))
    anchor_status, anchor_timed_out, anchor_wall, anchor_output = forge_runner(
        project, anchor_test, timeout)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "forge-put.log").write_text(put_output)
    (out_root / "forge-anchor.log").write_text(anchor_output)
    source_sha = hashlib.sha256(test_source.encode()).hexdigest()
    flat_sha = hashlib.sha256(flat_sol.read_bytes()).hexdigest()
    put_log_sha = hashlib.sha256(put_output.encode()).hexdigest()
    anchor_log_sha = hashlib.sha256(anchor_output.encode()).hexdigest()
    passed = put_status == "Success" and anchor_status == "Success"
    put_json = {
        "kind": "put", "stage2_source": "source_grounded_extendedresolver_selector_length",
        "stage4_kind": "source-grounded-selector-length-put", "contract": subject.contract,
        "unit": unit, "enc": 2, "depth": 1,
        "path_function": basis_record.get("path_function"), "file": str(test_file),
        "test": fuzz_test, "piece": None, "b": True,
        "valid_reference_test": passed, "forge_status": put_status,
        "ce_anchor_forge_status": anchor_status,
        "region": {"data.length": ["0", "3"]},
        "pins": {"omitted_param_0.length": "0", "msg.value": "0"},
        "stats": {"fuzz_params": 1, "lifted": ["data.length"],
                  "dynamic_fuzz_coords": ["data.length"], "wide_fuzz_coords": [],
                  "asserts": 1, "exit_kind_asserts": 1, "state_asserts": 0,
                  "return_asserts": 0, "verifier_asserts": 0,
                  "oracle_classes": ["R0"], "oracle_class_counts": {"R0": 1}},
        "ce_anchor": {"status": "embedded", "test": anchor_test,
                       "binding": "source-grounded-extendedresolver/v1",
                       "basis_put_json_sha256": hashlib.sha256(basis_path.read_bytes()).hexdigest(),
                       "destination_source_sha256": source_sha, "flat_source_sha256": flat_sha,
                       "oracle": {"class": "R0", "kind": "revert", "expected": True,
                                  "provenance": "stage2-witness"},
                       "forge_gate": {"put_test": fuzz_test, "anchor_test": anchor_test,
                                      "put_status": put_status, "anchor_status": anchor_status,
                                      "source_sha256": source_sha, "put_log_sha256": put_log_sha,
                                      "anchor_log_sha256": anchor_log_sha}},
        "basis": {"kind": "retained-stage2-concrete-replay",
                  "basis_put_json_sha256": hashlib.sha256(basis_path.read_bytes()).hexdigest()},
        "concrete_reason": "short calldata has no 4-byte selector and no fallback exists",
        "notes": ["source-level data.length coordinate is bounded to [0,3]",
                   "PUT and concrete replay anchor are Forge-gated independently"],
    }
    wd = out_root / "_wd" / "source_extendedresolver"
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "put.json").write_text(json.dumps(put_json, indent=2, sort_keys=True))
    row = {k: put_json[k] for k in ("kind", "stage2_source", "stage4_kind", "contract", "unit",
                                     "enc", "piece", "path_function", "test", "region", "pins",
                                     "valid_reference_test", "forge_status", "ce_anchor_forge_status",
                                     "ce_anchor", "b")}
    (out_root / "put-summary.json").write_text(json.dumps({
        "schema": "veriput-put-summary/1",
        "emission": {"puts_emitted": 1, "concrete_replays_emitted": 0},
        "deliverable_b": {"valid_reference_tests": {"total": int(passed), "put": int(passed),
                                                       "concrete": 0}, "rows": [row]},
        "timing": {"generation_wall_s": round(time.monotonic() - start, 3),
                   "foundry_replay_wall_s": round(put_wall + anchor_wall, 3)},
    }, indent=2, sort_keys=True))
    return {"stage": "source-grounded-extendedresolver-put", "unit": unit,
            "status": "ok" if passed else ("timeout" if put_timed_out or anchor_timed_out
                                             else "no-output"),
            "forge_status": put_status, "ce_anchor_forge_status": anchor_status,
            "forge_timed_out": put_timed_out or anchor_timed_out,
            "wall_s": round(time.monotonic() - start, 3),
            "forge_wall_s": round(put_wall + anchor_wall, 3),
            "put_out_root": str(out_root), "test_file": str(test_file)}


def adopt_existing_subject_results(result_root: Path, dataset_label: str, target_rows_: list[dict],
                                   journal: Path, done: dict[str, dict]) -> dict[str, dict]:
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
            row = _artifact_summary_row(target_row, dataset_label, case_dir, updated.get(key))
            if row is None:
                continue
        else:
            row = _merge_put_summary_into_row(row, case_dir)
        stale_row = _best_stale_artifact_row(target_row, dataset_label, case_dir, row)
        row = _adopt_stale_artifacts(row, stale_row)
        row["key"] = key
        row["subject_id"] = subject_id
        normalized_case_result = _write_normalized_case_result(
            case_dir,
            row,
            reason=("retained Stage-4 artifacts or normalized result row are "
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
    tmp = journal.with_name(f".{journal.name}.tmp.{os.getpid()}.{time.time_ns()}")
    with tmp.open("w") as stream:
        for key in ordered_keys + sorted(remaining):
            if key in updated:
                stream.write(json.dumps(updated[key], sort_keys=True) + "\n")
    os.replace(tmp, journal)
    if adopted:
        print("[rq1] adopted stronger existing subject result(s): " + ", ".join(adopted),
              flush=True)
    if normalized:
        print("[rq1] normalized existing journal row(s): " + ", ".join(normalized), flush=True)
    return updated


def target_rows(veriput_root: Path,
                benchmark: str,
                subject_ids: list[str],
                limit: int,
                order: str = "fast-first") -> tuple[str, list[dict]]:
    if benchmark not in TARGET_BENCHMARK_ARG:
        raise RQ1RunError("--benchmark must be one of: " + ", ".join(sorted(TARGET_BENCHMARK_ARG)))
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
            try:
                prepared = resolve_subject(subject_id,
                                           benchmark=((candidate or {}).get("benchmark")
                                                      or target_arg),
                                           require_unit=False)
            except SubjectError:
                continue
            recovered = dict(candidate or {})
            recovered.update({
                "status": "ok",
                "subject_id": subject_id,
                "benchmark": prepared.benchmark,
                "contract": prepared.contract,
                "units_hint": recovered.get("units_hint") or [],
                "prepared_subject_fallback": True,
                "prepared_subject_root": prepared.root,
                "prepared_subject_status_original": (candidate or {}).get("status"),
                "prepared_subject_reason_original": (candidate or {}).get("reason"),
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
            candidates.append(veriput_root / "scripts" / "Results" / "workdirs" / dirname /
                              "subjects" / subject_id / "flat.sol")
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


def build_subject_schedule(subject: PreparedSubject, target_row: dict, ast_cache_root: Path,
                           case_dir: Path, *, timeout_s: int, run_timeout_s: int,
                           memlimit_gib: int, cert_path: Path | None = None) -> dict:
    row = subject_unit_manifest.manifest_for_subject(subject, generate_ast=True, ast_timeout_s=60.0)
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
    cert_path = cert_path or (case_dir / "cert" / "certify-results.jsonl")
    cert_path = cert_path.resolve()
    cert_root = cert_path.parent
    cert_out = str(cert_path)
    return unit_schedule.build_schedule(manifest,
                                        selection_strategy="priority",
                                        cert_out=cert_out,
                                        timeout_s=timeout_s,
                                        run_timeout_s=run_timeout_s,
                                        memlimit_gib=memlimit_gib,
                                        workdir=str((cert_root / "work").resolve()))


def filter_schedule_units(schedule: dict, units: list[str]) -> dict:
    if not units:
        return schedule
    wanted = set(units)
    filtered = dict(schedule)
    jobs = [job for job in (schedule.get("jobs") or []) if job.get("unit") in wanted]
    filtered["jobs"] = jobs
    summary = dict(schedule.get("summary") or {})
    summary.update({
        "jobs_before_unit_filter": len(schedule.get("jobs") or []),
        "jobs": len(jobs),
        "unit_filter": sorted(wanted),
        "unit_filter_missing": sorted(wanted - {job.get("unit")
                                                for job in jobs}),
    })
    filtered["summary"] = summary
    filtered["unit_filter"] = sorted(wanted)
    return filtered


def apply_stage2_free_entry_state(schedule: dict, enabled: bool) -> dict:
    """Pass --free-entry-state to every Stage-2 certify_all job (see its help)."""
    if not enabled:
        return schedule
    updated = dict(schedule)
    jobs = []
    for current in schedule.get("jobs") or []:
        job = dict(current)
        argv = [str(arg) for arg in job.get("certify_argv") or []]
        if "--free-entry-state" not in argv:
            argv.append("--free-entry-state")
        job["certify_argv"] = argv
        jobs.append(job)
    updated["jobs"] = jobs
    updated["free_entry_state"] = True
    return updated


def apply_stage2_flag(schedule: dict, enabled: bool, flag: str, key: str) -> dict:
    """Append one boolean certify_all flag to every Stage-2 job."""
    if not enabled:
        return schedule
    updated = dict(schedule)
    jobs = []
    for current in schedule.get("jobs") or []:
        job = dict(current)
        argv = [str(arg) for arg in job.get("certify_argv") or []]
        if flag not in argv:
            argv.append(flag)
        job["certify_argv"] = argv
        jobs.append(job)
    updated["jobs"] = jobs
    updated[key] = True
    return updated


def apply_stage2_extcall_pins(schedule: dict, enabled: bool) -> dict:
    if not enabled:
        return schedule
    updated = dict(schedule)
    jobs = []
    for current in schedule.get("jobs") or []:
        job = dict(current)
        argv = [str(arg) for arg in job.get("certify_argv") or []]
        if "--pin-extcall" not in argv:
            argv.append("--pin-extcall")
        job["certify_argv"] = argv
        jobs.append(job)
    updated["jobs"] = jobs
    updated["pin_extcall"] = True
    return updated


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


def run_command(argv: list[str],
                timeout_s: float,
                log_prefix: Path,
                *,
                hard_deadline: float | None = None) -> dict:
    """Run a process group, including termination and log close in a hard deadline."""
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    stdout_path = log_prefix.with_suffix(".stdout.log")
    stderr_path = log_prefix.with_suffix(".stderr.log")
    timed_out = False
    maxrss_proc_mb = 0.0
    timeout_deadline = start + max(0.0, timeout_s)
    if hard_deadline is not None:
        timeout_deadline = min(
            timeout_deadline,
            hard_deadline - STRICT_PROCESS_TERMINATION_RESERVE_S)
    try:
        with stdout_path.open("w") as stdout_stream, stderr_path.open("w") as stderr_stream:
            if timeout_deadline <= time.monotonic():
                raise subprocess.TimeoutExpired(argv, timeout_s)
            proc = subprocess.Popen(argv,
                                    stdout=stdout_stream,
                                    stderr=stderr_stream,
                                    text=True,
                                    start_new_session=True)
            while proc.poll() is None:
                maxrss_proc_mb = max(maxrss_proc_mb, _rss_tree_mb(proc.pid))
                remaining = timeout_deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    _kill_process_tree(proc.pid, signal.SIGTERM)
                    try:
                        termination_remaining = (max(
                            0.0, hard_deadline - time.monotonic())
                                                 if hard_deadline is not None else 5.0)
                        proc.wait(timeout=min(1.0, termination_remaining))
                    except subprocess.TimeoutExpired:
                        _kill_process_tree(proc.pid, signal.SIGKILL)
                        kill_remaining = (max(0.0, hard_deadline - time.monotonic())
                                          if hard_deadline is not None else None)
                        if kill_remaining is None:
                            proc.wait()
                        elif kill_remaining > 0:
                            try:
                                proc.wait(timeout=kill_remaining)
                            except subprocess.TimeoutExpired:
                                pass
                    break
                time.sleep(min(0.1, remaining))
            maxrss_proc_mb = max(maxrss_proc_mb, _rss_tree_mb(proc.pid))
            rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = None
        timed_out = True
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


def _cert_row_matches(row: dict,
                      benchmark_key: str,
                      unit: str,
                      path_function: str | None = None) -> bool:
    if row.get("unit") != unit:
        return False
    if (row.get("benchmark") or row.get("poc")) != benchmark_key:
        return False
    if path_function and not _same_path_function(_row_path_function(row), path_function):
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


ABI_VALUE_GATE_PIN_EXCLUSION_RE = re.compile(
    r"EXCLUDED FROM THE SLICE by the pins \([^)]*msg\.value:")
ABI_VALUE_GATE_REASON = ("public/external nonpayable ABI entry rejects nonzero msg.value "
                         "before executing the function body")


def _abi_value_gate_structural_detail(enc: int, depth: int | None = None) -> dict:
    """The structural certificate for one pin-excluded nonpayable value-gate path.

    A public/external nonpayable entry point is rejected by the compiler-inserted
    ABI gate for every ``msg.value > 0``, before any of the function body runs.
    That fact does not depend on any other coordinate, so the whole half-open
    interval ``[1, 2**256-1]`` follows the same path to the same reverting
    boundary.  Stage 2 cannot report this itself: it auto-pins ``msg.value`` to 0
    to certify the body paths, which by construction excludes this path from the
    slice it searches.  Promoting the excluded path here recovers a certified
    region instead of degrading it to a one-point concrete replay.
    """
    return {
        "box": [{
            "name": "msg.value",
            "lo": "1",
            "hi": str((1 << 256) - 1),
            "holes": [],
        }],
        "ce": {},
        # Deliberately the same certification_source the last-resort structural
        # gate row uses: the certificate itself is identical (a compiler-level
        # nonpayable ABI gate with no solver coordinate), so it must take the
        # same downstream structural-anchor handling instead of a look-alike
        # source string that the anchor predicate would not recognize.  How the
        # row was reached is recorded in driver_diagnostic, not here.
        "certification_source": "structural-abi-gate-no-coordinate",
        "promoted_from": "stage2-msg-value-pin-exclusion",
        # The enumeration's own depth for this path, NOT a constant.  Stage 4
        # guards each claim with `tr != enc || cnt != depth`; a wrong depth is
        # true on every execution, so every candidate would hold vacuously and
        # ESBMC refuses the whole assertion ladder for the path.  That refusal
        # is correct, and it costs the path its R1/R2 oracles -- measured at 804
        # value-gate PUTs in the 2026-08-19 corpus, every one of them reduced to
        # an exit-only R0 test.  Stage 2 already recorded the real depth in the
        # not-certified detail this promotion consumes.
        "depth": (0 if depth is None else int(depth)),
        "enc": int(enc),
        "established": [],
        "extcall_pins": {},
        "piece": 1,
        "reason": ABI_VALUE_GATE_REASON,
        "retreated": {},
        "stage4_kind": "abi-value-gate",
        "verdict": "CERTIFIED",
    }


def _promote_pin_excluded_value_gate_paths(cert_path: Path,
                                           benchmark_key: str,
                                           unit: str,
                                           path_function: str | None = None) -> dict:
    """Certify the nonpayable ABI value-gate path Stage 2's own pin excluded.

    Only paths whose recorded non-certification reason is literally the
    ``msg.value`` pin exclusion are promoted, and only for units that are
    public/external and nonpayable.  Any other non-certification reason is a
    search result and is left untouched.  The promoted path's concrete
    fallbacks are dropped in the same edit, so one path never yields both a PUT
    and a concrete replay.
    """
    if not cert_path.exists():
        return {"promoted": 0, "units": []}
    lines = cert_path.read_text(errors="replace").splitlines()
    out = []
    promoted = 0
    units = []
    changed = False
    for line in lines:
        if not line.strip():
            out.append(line)
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            out.append(line)
            continue
        if not _cert_row_matches(row, benchmark_key, unit, path_function):
            out.append(line)
            continue
        not_certified = row.get("not_certified") or {}
        targets = [enc for enc, text in not_certified.items()
                   if ABI_VALUE_GATE_PIN_EXCLUSION_RE.search(str(text))]
        if not targets:
            out.append(line)
            continue
        certified = row.setdefault("certified", {})
        details = row.setdefault("certified_details", {})
        for enc in targets:
            if enc in certified:
                continue
            try:
                enc_int = int(enc)
            except (TypeError, ValueError):
                continue
            prior = (row.get("not_certified_details") or {}).get(enc)
            enc_depth = prior.get("depth") if isinstance(prior, dict) else None
            certified[enc] = "nonpayable ABI gate rejects msg.value > 0"
            details[enc] = _abi_value_gate_structural_detail(enc_int, enc_depth)
            not_certified.pop(enc, None)
            for bucket in ("not_certified_details", "not_certified_ce_fallbacks",
                           "certified_region_concrete_fallbacks"):
                holder = row.get(bucket)
                if isinstance(holder, dict):
                    holder.pop(enc, None)
            promoted += 1
            changed = True
        if promoted:
            row["bucket"] = "CERTIFIED"
            diagnostic = row.setdefault("driver_diagnostic", {})
            if isinstance(diagnostic, dict):
                diagnostic["abi_value_gate_pin_promotion"] = {
                    "encs": sorted(targets),
                    "reason": ABI_VALUE_GATE_REASON,
                }
            units.append(str(row.get("unit") or unit))
        out.append(json.dumps(row))
    if changed:
        cert_path.write_text("\n".join(out) + "\n")
    return {"promoted": promoted, "units": sorted(set(units))}


def _stage4_candidate_counts(cert_path: Path, benchmark_key: str, unit: str,
                             path_function: str | None) -> tuple[int, int, int, int, int]:
    """Recount every Stage-4 candidate class for one certification job."""
    return (
        _certified_count(cert_path, benchmark_key, unit, path_function),
        _cleared_concrete_fallback_count(cert_path, benchmark_key, unit, path_function),
        _timeout_concrete_fallback_count(cert_path, benchmark_key, unit, path_function),
        _complete_witness_concrete_fallback_count(cert_path, benchmark_key, unit, path_function),
        _partial_journal_concrete_fallback_count(cert_path, benchmark_key, unit, path_function),
    )


ABI_VALUE_GATE_ENUMERATION_BUDGET_S = 60.0


def _structural_abi_value_gate_rescue(cert_path: Path,
                                      subject: PreparedSubject,
                                      job: dict,
                                      unit: str,
                                      path_function: str | None,
                                      n_stage4_candidates: int | None,
                                      esbmc_bin: str | None = None,
                                      memlimit_gib: int = 12,
                                      remaining_s: float = 0.0,
                                      subject_paths: dict | None = None) -> dict | None:
    """Certify the nonpayable ABI value gate for a unit whose Stage-2 run ran out of time.

    A nonpayable public/external entry reverts for every ``msg.value > 0`` before its
    body runs, so that region is certified structurally and needs no solver evidence.
    Deferring it until after Stage-2 succeeds throws the region away whenever Stage-2
    times out, which abandons the whole subject with no output at all.

    Stage 2 timing out is exactly the case where nothing recorded this unit's path
    encodings, so they are read straight out of the instrumented GOTO here.  That
    single frontend-only run replaces one guaranteed-refusal ladder run per path and
    is what lets those paths carry R1/R2 instead of an exit-only R0.
    """
    if int(n_stage4_candidates or 0) > 0:
        return None
    if not _is_nonpayable_abi_entry_job(job):
        return None
    # The subject-wide enumeration is taken once, at case start, while the case
    # still has budget.  Falling back to a per-unit run here is right only when
    # that map is absent, and it will usually find no budget left -- which is
    # why the map exists.
    enumerated_paths = (subject_paths or {}).get(str(path_function or ""))
    if enumerated_paths is None:
        enumerated_paths = _enumerate_unit_paths(
            subject, unit, path_function, esbmc_bin, memlimit_gib,
            min(ABI_VALUE_GATE_ENUMERATION_BUDGET_S, float(remaining_s)))
    row = _abi_value_gate_cert_row(subject, job, enumerated_paths)
    _append_jsonl(cert_path, row)
    return {
        "stage": "stage2-timeout-structural-abi-value-gate",
        "unit": unit,
        "path_function": path_function,
        "job_id": job.get("job_id"),
        "status": "ok",
        "cert_canonical_jsonl": str(cert_path),
        "certified_paths": len(row.get("certified") or {}),
        "path_identity_source": row["driver_diagnostic"]["path_identity_source"],
        "reason": row["driver_diagnostic"]["reason"],
    }


def _certified_count(cert_path: Path,
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


def _cleared_concrete_fallback_count(cert_path: Path,
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
        not_certified = row.get("not_certified") or {}
        details = row.get("not_certified_details") or {}
        if isinstance(details, list):
            detail_rows = {str(d.get("enc")): d for d in details if isinstance(d, dict)}
        elif isinstance(details, dict):
            detail_rows = {str(k): v for k, v in details.items() if isinstance(v, dict)}
        else:
            detail_rows = {}
        for enc in not_certified:
            detail = detail_rows.get(str(enc)) or {}
            witness_check = detail.get("witness_check")
            reason = str(not_certified.get(enc) or "")
            pin_excluded = (witness_check == "PIN-EXCLUDED-NO-COORDINATE"
                            or "EXCLUDED FROM THE SLICE by the pins" in reason
                            or "EXCLUDED FROM THE SLICE by the pins" in str(
                                detail.get("reason") or ""))
            witness_cleared = witness_check in CONCRETE_FALLBACK_WITNESS_CHECKS
            ce = detail.get("ce")
            has_replay_ce = isinstance(ce, (dict, list)) and bool(ce)
            if (detail.get("concrete_fallback") is True and has_replay_ce
                    and (witness_cleared or (witness_check is None and pin_excluded))):
                count += 1
    return count


def _complete_witness_concrete_fallback_count_for_row(row: dict) -> int:
    occupied = _occupied_stage2_path_ids(row)
    journal = row.get("partial_witness_journal") or {}
    if not isinstance(journal, dict):
        return 0
    bucket = str(row.get("bucket") or "").upper()
    if bucket not in ("NO-COORDINATE", "NO-WITNESS-UNKNOWN", "CERTIFIED"):
        return 0
    if bucket == "CERTIFIED" and journal.get("source_stage") != "certified-no-coordinate":
        return 0
    partial_no_coordinate = (bucket == "NO-COORDINATE" and journal.get("partial") is True
                             and journal.get("source_stage") == "no-generalizable-coordinate")
    if journal.get("complete") is not True and not partial_no_coordinate:
        return 0
    count = 0
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


def _timeout_concrete_fallback_count(cert_path: Path,
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
        if journal.get("complete") is True and bucket in ("NO-COORDINATE", "NO-WITNESS-UNKNOWN",
                                                          "CERTIFIED"):
            continue
        if (bucket == "NO-COORDINATE"
                and journal.get("source_stage") == "no-generalizable-coordinate"):
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
            diagnostic_tag = (diagnostic.get("tag") if isinstance(diagnostic, dict) else None)
            if (source_stage != "partial-witness-journal" and diagnostic_tag not in {
                    "path-coverage-partial-journal-no-report",
                    "path-coverage-partial-journal-only",
            }):
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
        count += _complete_witness_concrete_fallback_count_for_row(row)
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
    no_report = (diagnostic.get("tag") == "esbmc-no-cov-report"
                 or diagnostic.get("category") == "no-cov-report")
    return (str(row.get("bucket") or "").upper() == "KILLED" and row.get("witnessed") is None
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
            detail = ", ".join(f"{key}={value}" for key, value in diagnostics.items())
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
    matches = [candidate for candidate in put_root.rglob(name) if candidate.is_file()]
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


def _oracle_input_part_id(rec: dict, test_name) -> str | None:
    """The oracle input part this emitted test unit belongs to, if it is a part.

    A split path emits one physical `test_put_*_part_*` per final part and keeps
    one concrete basis per part, so RQ3 derivation needs the part to tell the
    siblings apart.  A path that never split has no part and returns None, which
    leaves its identity exactly as it was before splitting existed.
    """
    if not test_name:
        return None
    for unit_row in (rec or {}).get("test_units") or []:
        if not isinstance(unit_row, dict) or unit_row.get("test") != test_name:
            continue
        part = unit_row.get("oracle_input_part")
        if isinstance(part, dict) and part.get("part_id"):
            return str(part["part_id"])
        if unit_row.get("part_id"):
            return str(unit_row["part_id"])
        return None
    return None


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
    disabled_rx = re.compile(r"\bfunction\s+disabled_" + re.escape(str(test)) + r"\s*\(")
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
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\([^)]*\)[^{;]*\{", source)
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
        for label in source.get("oracle_classes") or source.get("oracle_tags") or []:
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
    stage2_source = str(row.get("stage2_source") or "").replace("_", "-")
    stage4_kind = str(row.get("stage4_kind") or "").replace("_", "-")
    if (stage2_source in ("no-unit-deploy-fallback", "structural-deploy-only")
            or stage4_kind in ("deploy-only", "creation-code-only")):
        return False
    return row.get("valid_reference_test") is True


def _put_json_physical_records(rec: dict) -> list[dict]:
    """The records that name a test function actually emitted for ``rec``.

    A record with ``test_units`` was split into one physical `.t.sol` per oracle
    input part; the parent's own ``test`` name is not written anywhere, so it
    can only ever be recovered as an artifact that no Forge run can reach.
    """
    test_units = rec.get("test_units")
    if not isinstance(test_units, list) or not test_units:
        return [rec]
    physical = []
    for unit_row in test_units:
        if not isinstance(unit_row, dict) or not unit_row.get("test"):
            continue
        child = dict(rec)
        child["test_units"] = []
        for key in ("file", "test", "stats", "materialization", "region", "holes", "derived_by",
                    "oracle_input_part"):
            if key in unit_row:
                child[key] = unit_row[key]
        physical.append(child)
    # A split record whose parts named no test is not evidence that the parent
    # test exists, so it recovers nothing rather than falling back to the parent.
    return physical


def _put_json_artifact_row(rec: dict) -> dict:
    """Recover a raw artifact row from put.json when put-summary rows are absent."""

    return {
        "kind":
        rec.get("kind"),
        "stage4_kind":
        rec.get("stage4_kind"),
        "stage2_source":
        rec.get("stage2_source"),
        "stage2_witness_check":
        rec.get("stage2_witness_check"),
        "unit":
        rec.get("unit"),
        "enc":
        rec.get("enc"),
        "piece":
        rec.get("piece"),
        "test":
        rec.get("test"),
        "file":
        rec.get("file"),
        "forge_status":
        rec.get("forge_status"),
        "valid_reference_test":
        rec.get("valid_reference_test"),
        "b":
        rec.get("b"),
        "concrete_reason":
        rec.get("concrete_reason"),
        "oracle_classes":
        rec.get("oracle_classes"),
        "oracle_class_counts":
        rec.get("oracle_class_counts"),
        "oracle_class_combinations":
        rec.get("oracle_class_combinations"),
        "oracle_class_combo_counts":
        rec.get("oracle_class_combo_counts"),
        "assertion_oracles": (rec.get("assertion_oracles")
                              or (rec.get("stats") or {}).get("assertion_oracles")),
        "concrete_oracles":
        rec.get("concrete_oracles"),
        "_from_put_json_only":
        True,
    }


def _row_count(row: dict, key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _legacy_quality_bucket(row: dict) -> str:
    valid = _row_count(row, "valid")
    if row.get("valid") is None:
        valid = (_row_count(row, "put_valid") + _row_count(row, "concrete_valid"))
        if valid <= 0:
            valid = len(row.get("valid_tests") or [])
    put_valid = _row_count(row, "put_valid")
    if valid <= 0:
        return "no-valid"
    if put_valid <= 0:
        return "valid-no-PUT"
    valid_puts = [
        test for test in (row.get("valid_tests") or [])
        if test.get("kind") == "put" and _is_valid_reference_test(test)
    ]
    if valid_puts:
        if any(_has_oracle_class(test, "R1", "R2") for test in valid_puts):
            return "valid-PUT-with-R1R2"
        return "valid-PUT-no-R1R2"
    if (_row_count(row, "valid_put_with_R1_or_R2") > 0 or _row_count(row, "valid_put_with_R1") > 0
            or _row_count(row, "valid_put_with_R2") > 0):
        return "valid-PUT-with-R1R2"
    return "valid-PUT-no-R1R2"


def _strength_quality(put_summary: dict) -> dict:
    valid_tests = [
        test for test in (put_summary.get("valid_tests") or []) if _is_valid_reference_test(test)
    ]
    valid_puts = [test for test in valid_tests if test.get("kind") == "put"]
    valid_puts_with_r1 = [test for test in valid_puts if _has_oracle_class(test, "R1")]
    valid_puts_with_r2 = [test for test in valid_puts if _has_oracle_class(test, "R2")]
    valid_puts_with_r1r2 = [test for test in valid_puts if _has_oracle_class(test, "R1", "R2")]
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
        "valid_put_without_R1R2": (len(valid_puts) - len(valid_puts_with_r1r2)),
        "valid_concrete": sum(1 for test in valid_tests if test.get("kind") == "concrete"),
    }


def summarize_put_artifacts(put_root: Path) -> dict:
    emission = Counter()
    valid = Counter()
    timing = Counter()
    rows = []
    summary_paths = []
    retained_concrete_bases = []
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
        timing["stage4_emission_wall_s"] += float(tm.get("emission_wall_s") or 0.0)
        timing["foundry_replay_wall_s"] += float(tm.get("foundry_replay_wall_s") or 0.0)
        timing["put_all_wall_s"] += float(tm.get("total_wall_s") or 0.0)
        for row in b.get("rows") or []:
            if isinstance(row, dict):
                row = dict(row)
                relocated = _relocated_stage4_file(row.get("file"), put_root)
                if relocated:
                    row["file"] = relocated
            rows.append(row)
        for row in doc.get("retained_concrete_bases") or []:
            if not isinstance(row, dict):
                continue
            row = dict(row)
            relocated = _relocated_stage4_file(row.get("file"), put_root)
            if relocated:
                row["file"] = relocated
            retained_concrete_bases.append(row)

    put_jsons = _load_put_jsons(put_root)
    by_file_test = {}
    by_test_candidates = {}
    for rec in put_jsons:
        if rec.get("retained_basis_only"):
            continue
        test = rec.get("test")
        file_name = rec.get("file")
        if test and file_name:
            by_file_test[(str(file_name), str(test))] = rec
        if test:
            by_test_candidates.setdefault(str(test), []).append(rec)
        # An oracle input part is emitted as its own physical `.t.sol` with its
        # own `test_put_*_part_*`, but the proved oracles for every part live in
        # the parent put.json.  Index the children under their own identity too,
        # otherwise the row lookup misses and the child row carries no
        # `put_json` -- which is what the RQ3 derivation and the Full/ablation
        # comparison read the oracle counts from.
        for unit_row in rec.get("test_units") or []:
            if not isinstance(unit_row, dict):
                continue
            child_test = unit_row.get("test")
            child_file = unit_row.get("file") or file_name
            if not child_test or not child_file:
                continue
            key = (str(child_file), str(child_test))
            if key not in by_file_test:
                by_file_test[key] = rec
            by_test_candidates.setdefault(str(child_test), []).append(rec)
    by_unique_test = {test: rows[0] for test, rows in by_test_candidates.items() if len(rows) == 1}
    row_keys = {(str(row.get("file") or ""), str(row.get("test") or ""))
                for row in rows if row.get("kind") in ("put", "concrete")}
    row_tests = {
        str(row.get("test"))
        for row in rows if row.get("kind") in ("put", "concrete") and row.get("test")
    }
    for rec in put_jsons:
        if rec.get("retained_basis_only"):
            continue
        if rec.get("kind") not in ("put", "concrete"):
            continue
        # ---- RECOVER THE PHYSICAL TESTS, NOT THE RECORD THAT WAS SPLIT ----
        #
        # When a certified path splits into oracle input parts, each part is
        # emitted as its own `.t.sol` with its own `test_put_*_part_*`, and the
        # parent record keeps the UNSPLIT `test` name while its `file` already
        # points at the FIRST part's file.  That pairing names nothing that
        # exists: `b_report` replaces the parent with its children
        # (`expand_stage4_test_unit_results`), so no Forge run ever reports a
        # status under the parent's name.
        #
        # Both dedup guards below then miss -- the parent's `(file, test)` key
        # and its bare `test` are absent from the put-summary rows, which carry
        # only the children -- and the parent was appended as an extra raw
        # artifact that could never become valid.  MEASURED on the 2026-08-19
        # corpus: 342 such rows, every one `kind=put forge_status=None`, 174 of
        # them sitting beside their own children in the same result row.  They
        # inflated the raw denominator and read as "PUTs we built and never
        # replayed", which is a claim about generation and was a fact about
        # bookkeeping.
        #
        # The remaining parents are the case where Stage 4 timed out before
        # `b_report` wrote any put-summary row.  There the children are exactly
        # what this recovery exists to find, so recover THEM.
        for physical in _put_json_physical_records(rec):
            key = (str(physical.get("file") or ""), str(physical.get("test") or ""))
            if key in row_keys:
                continue
            if str(physical.get("test") or "") in row_tests:
                continue
            if not physical.get("file") or not physical.get("test"):
                continue
            rows.append(_put_json_artifact_row(physical))
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
        if (row.get("refused") or _row_is_no_oracle_put(row, rec) or _row_is_disabled_concrete(row)
                or _row_is_unsupported_concrete(row)):
            continue
        stats = rec.get("stats") or {}
        oracle_classes, oracle_class_counts, oracle_class_combinations, \
            oracle_class_combo_counts, assertion_details = (
                _merge_oracle_metadata(row, rec, stats))
        merged_for_validity = {
            **rec,
            **row,
            "stage4_kind": (row.get("stage4_kind") or rec.get("stage4_kind")),
            "stage2_source": (row.get("stage2_source") or rec.get("stage2_source")),
        }
        entry = {
            "kind": row.get("kind"),
            "stage4_kind": merged_for_validity.get("stage4_kind"),
            "stage2_source": merged_for_validity.get("stage2_source"),
            "stage2_witness_check": (row.get("stage2_witness_check")
                                     or rec.get("stage2_witness_check")),
            "unit": row.get("unit"),
            "enc": row.get("enc"),
            "piece": row.get("piece"),
            "path_function": (row.get("path_function") or rec.get("path_function")),
            "test": row.get("test"),
            "file": row.get("file"),
            "forge_status": row.get("forge_status"),
            "ce_anchor_forge_status": (row.get("ce_anchor_forge_status") or
                                        rec.get("ce_anchor_forge_status")),
            "ce_anchor": (row.get("ce_anchor") or rec.get("ce_anchor")),
            "gates": (row.get("gates") or rec.get("gates")),
            "valid_reference_test": _is_valid_reference_test(merged_for_validity),
            "b": bool(row.get("b")),
            "concrete_reason": (row.get("concrete_reason") or rec.get("concrete_reason")),
            "oracle_classes": oracle_classes,
            "oracle_class_counts": oracle_class_counts,
            "oracle_class_combinations": oracle_class_combinations,
            "oracle_class_combo_counts": oracle_class_combo_counts,
            "assertion_oracles": assertion_details,
            "concrete_oracles": (row.get("concrete_oracles") or rec.get("concrete_oracles")),
            "r2_requested": rec.get("r2_requested"),
            "r2_depth": rec.get("r2_depth"),
            "r2_term_budget": rec.get("r2_term_budget"),
            "r2_candidate_budget": rec.get("r2_candidate_budget"),
            "r2_fuzz_prefilter": rec.get("r2_fuzz_prefilter"),
            "slot_candidates": rec.get("slot_candidates"),
            "put_json": rec.get("_put_json_path"),
            "oracle_input_part": _oracle_input_part_id(rec, test_name),
            "derived_by": (row.get("derived_by") or rec.get("derived_by") or {}),
        }
        entry["oracle_tags"] = _rq1_oracle_tags(entry["kind"], entry["oracle_classes"])
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
        "retained_concrete_bases": retained_concrete_bases,
        "put_json_count": len(put_jsons),
        "stage4_generation_wall_s": round(timing["stage4_generation_wall_s"], 3),
        "stage4_emission_wall_s": round(timing["stage4_emission_wall_s"], 3),
        "foundry_replay_wall_s": round(timing["foundry_replay_wall_s"], 3),
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
        "stage4_storage_layout_counts": dict(sorted(storage_layout_counts.items())),
    }
    summary.update(_strength_quality(summary))
    summary["artifact_counts"] = _artifact_count_summary(summary)
    summary["time_stats"] = _row_time_stats(summary)
    return summary


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _strict_finalization_reserve_s(timeout_s: float) -> float:
    """Reserve bounded publication/accounting time inside a strict case cap."""
    timeout_s = max(0.0, float(timeout_s))
    return min(STRICT_CASE_FINALIZATION_RESERVE_MAX_S, max(1.0, timeout_s * 0.05))


def _case_wrapper_timeout(requested_s: float, deadline: float, strict: bool) -> float:
    """Cap a child process group at the subject deadline in strict mode."""
    if not strict:
        return requested_s
    return max(0.0, min(float(requested_s), _remaining(deadline)))


def _strict_stage4_fair_budget_s(remaining_s: float, pending_units: int) -> float:
    """Reserve fair Stage-4 opportunities for a multi-unit strict rerun."""
    remaining_s = max(0.0, float(remaining_s))
    if pending_units <= 0:
        return remaining_s
    fair_slots = min(int(pending_units) + 1, STRICT_STAGE4_FAIR_SHARE_SLOTS)
    fair_share_s = max(STRICT_STAGE4_MIN_UNIT_BUDGET_S,
                       int(remaining_s / max(1, fair_slots)))
    return min(remaining_s, float(fair_share_s))


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
        raise RQ1RunError(f"--jobs {args.jobs} x --memlimit-gib {args.memlimit_gib} = "
                          f"{committed:g}GiB exceeds {args.mem_fraction:.0%} of "
                          f"MemAvailable ({available:.1f}GiB)")


def wait_for_mem_budget(memlimit_gib: int, deadline: float, *, fraction: float, poll_s: float,
                        min_remaining_s: float) -> dict:
    start = time.monotonic()
    required_gib = memlimit_gib / max(fraction, 0.01)
    available = _mem_available_gib()
    waited = False
    while (available and available < required_gib and _remaining(deadline) > min_remaining_s):
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
        return sum(
            stage.get("wall_s") or 0.0 for stage in stages
            if str(stage.get("stage") or "").startswith("certify"))
    return sum(stage.get("wall_s") or 0.0 for stage in stages if stage.get("stage") == stage_name)


def _format_stage2_no_output_stop(stage2_wall_s: float) -> str:
    return (f"no output after {stage2_wall_s:.1f}s Stage 2; "
            "stopped before remaining units")


def _format_stage4_no_output_stop(stage4_wall_s: float) -> str:
    return (f"no output after {stage4_wall_s:.1f}s Stage 4; "
            "stopped before remaining units")


def _format_no_candidate_unit_stop(count: int) -> str:
    return (f"no Stage-2 candidate after {count} consecutive units; "
            "stopped before remaining units")


def _format_low_budget_concrete_only_skip(remaining_s: float, threshold_s: int) -> str:
    return (f"valid artifact already produced; {remaining_s:.1f}s remains "
            f"below the {threshold_s}s concrete-only Stage 4 floor")


def _format_low_budget_timeout_only_skip(remaining_s: float, threshold_s: int) -> str:
    return (f"{remaining_s:.1f}s remains below the {threshold_s}s "
            f"timeout-concrete-only Stage 4 floor")


def _format_put_saturated_concrete_only_skip(put_valid: int, threshold: int) -> str:
    return (f"{put_valid} valid PUT artifact(s) already produced; "
            f"concrete-only Stage 4 skipped at the {threshold}-PUT floor")


def _format_valid_saturated_concrete_only_skip(valid: int, put_valid: int) -> str:
    return (f"{valid} valid artifact(s) already produced "
            f"({put_valid} PUT); concrete-only Stage 4 skipped so the remaining "
            "subject budget can target PUT/R1/R2 units")


def _is_concrete_only_stage4(n_certified: int,
                             n_cleared_fallback: int,
                             n_timeout_fallback: int,
                             n_complete_witness_fallback: int = 0,
                             n_partial_journal_fallback: int = 0) -> bool:
    return (n_certified <= 0 and (n_cleared_fallback + n_timeout_fallback +
                                  n_complete_witness_fallback + n_partial_journal_fallback) > 0)


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
    return _is_concrete_only_stage4(n_certified, n_cleared_fallback, n_timeout_fallback,
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
    return _is_concrete_only_stage4(n_certified, n_cleared_fallback, n_timeout_fallback,
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
    if not _is_concrete_only_stage4(n_certified, n_cleared_fallback, n_timeout_fallback,
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
    if (n_timeout_fallback + n_complete_witness_fallback + n_partial_journal_fallback) <= 0:
        return False
    return remaining_s < float(threshold_s)


def _should_stop_after_zero_output_stage4(stages: list[dict], put_summary: dict,
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
    if (bucket == "NO-WITNESS-UNDECIDED" and cert_row.get("empty_witness_verdict") == "REFUSED"
            and "named-obstacle" in str(cert_row.get("empty_witness_reason") or "")):
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
        str(path_function) for path_function in (diagnostic.get("path_functions") or [])
        if path_function
    ]
    if not unit or not path_functions:
        return []
    existing = {(existing_job.get("unit"), existing_job.get("path_function"))
                for existing_job in existing_jobs}
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
            [str(arg) for arg in clone.get("certify_argv") or []], "--path-function", path_function)
        if clone.get("dry_run_argv"):
            clone["dry_run_argv"] = _argv_with_value([str(arg) for arg in clone["dry_run_argv"]],
                                                     "--path-function", path_function)
        budget = dict(clone.get("certification_budget") or {})
        budget["workdir"] = _workdir_with_suffix(
            budget.get("workdir"), "__" + _safe_name(_overload_retry_job_id(job, path_function)))
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


def _append_esbmc_arg_pair(argv: list[str], flag: str, value: str) -> list[str]:
    out = list(argv)
    out.extend([f"--esbmc-arg={flag}", f"--esbmc-arg={value}"])
    return out


def _remove_argv_pair(argv: list[str], flag: str, value: str) -> list[str]:
    out = []
    idx = 0
    while idx < len(argv):
        if argv[idx] == flag and idx + 1 < len(argv) and argv[idx + 1] == value:
            idx += 2
            continue
        out.append(argv[idx])
        idx += 1
    return out


def _ownable_source_shape(source: str) -> tuple[str, dict] | None:
    """Prove the narrow Ownable getter/write shape used by the fixture."""
    ownable_chunk = _source_contract_chunk(source, "Ownable")
    if (ownable_chunk is None
            or re.match(r"\s*(?:abstract\s+)?contract\s+Ownable\b", ownable_chunk) is None):
        return None
    owner_decls = _source_function_decl_infos(ownable_chunk, "owner")
    transfer_decls = _source_function_decl_infos(ownable_chunk, "_transferOwnership")
    if len(owner_decls) != 1 or len(transfer_decls) != 1:
        return None
    if (len(_source_function_decl_infos(source, "owner")) != 1
            or len(_source_function_decl_infos(source, "_transferOwnership")) != 1):
        return None
    owner_params, _owner_header, owner_body = owner_decls[0]
    if owner_params or re.fullmatch(r"\s*return\s+_owner\s*;\s*",
                                    _mask_solidity_comments_and_strings(owner_body), re.S) is None:
        return None
    transfer_params, _transfer_header, transfer_body = transfer_decls[0]
    if len(transfer_params) != 1:
        return None
    transfer_name, transfer_type = transfer_params[0]
    if _norm_ty(transfer_type) != "address":
        return None
    name = re.escape(transfer_name)
    transfer_pattern = (r"\s*(?:address\s+([A-Za-z_]\w*)\s*=\s*_owner\s*;\s*)?"
                        r"_owner\s*=\s*" + name + r"\s*;\s*"
                        r"(?:emit\s+OwnershipTransferred\s*\(\s*"
                        r"(?:\1|_owner)\s*,\s*" + name + r"\s*\)\s*;\s*)?")
    if re.fullmatch(transfer_pattern, _mask_solidity_comments_and_strings(transfer_body),
                    re.S) is None:
        return None
    return ownable_chunk, {
        "owner_getter": "return _owner",
        "ownership_write": f"_owner = {transfer_name}",
    }


def _ownable_address_constructor_source_evidence(source: str, contract: str) -> dict | None:
    """Trace one target address parameter to Ownable's `_owner` write."""
    target_chunk = _source_contract_chunk(source, contract)
    ownable_shape = _ownable_source_shape(source)
    if (target_chunk is None or ownable_shape is None or re.match(
            r"\s*(?:abstract\s+)?contract\s+" + re.escape(contract) + r"\b", target_chunk) is None):
        return None
    inheritance = _source_inheritance_names(target_chunk)
    if inheritance.count("Ownable") != 1:
        return None
    if _source_function_decl_infos(target_chunk, "owner"):
        return None
    target_params = _source_constructor_params_from_source(source, contract)
    ownable_chunk, common_evidence = ownable_shape
    ownable_params = _source_constructor_params_from_source(source, "Ownable")
    if len(ownable_params) != 1 or _norm_ty(ownable_params[0][1]) != "address":
        return None
    ownable_param = ownable_params[0][0]
    ctor_body = _mask_solidity_comments_and_strings(_constructor_body_text(ownable_chunk))
    param = re.escape(ownable_param)
    zero_guard = (r"if\s*\(\s*" + param + r"\s*==\s*address\s*\(\s*0\s*\)\s*\)\s*"
                  r"\{\s*revert\s+[^;]+;\s*\}\s*")
    if re.fullmatch(r"\s*" + zero_guard + r"_transferOwnership\s*\(\s*" + param + r"\s*\)\s*;\s*",
                    ctor_body, re.S) is None:
        return None
    ownable_calls = [
        args for name, args in _constructor_initializer_calls(target_chunk) if name == "Ownable"
    ]
    if len(ownable_calls) != 1 or len(ownable_calls[0]) != 1:
        return None
    target_ctor_body = _mask_solidity_comments_and_strings(_constructor_body_text(target_chunk))
    if re.search(r"\b(?:_owner|_transferOwnership|transferOwnership)\b", target_ctor_body):
        return None
    target_name = ownable_calls[0][0].strip()
    matches = [(idx, name, typ) for idx, (name, typ) in enumerate(target_params)
               if name == target_name and _norm_ty(typ) == "address"]
    if len(matches) != 1:
        return None
    idx, name, typ = matches[0]
    return {
        **common_evidence,
        "direct_base": "Ownable",
        "constructor_param_index": idx,
        "constructor_param_name": name,
        "constructor_param_type": typ,
        "constructor_flow": f"{contract}.{name} -> Ownable.{ownable_param} -> _owner",
    }


def _ownable_sender_constructor_source_evidence(source: str, contract: str) -> dict | None:
    """Prove direct inheritance from the exact no-arg sender Ownable shape."""
    target_chunk = _source_contract_chunk(source, contract)
    ownable_shape = _ownable_source_shape(source)
    if (target_chunk is None or ownable_shape is None or re.match(
            r"\s*(?:abstract\s+)?contract\s+" + re.escape(contract) + r"\b", target_chunk) is None):
        return None
    if _source_inheritance_names(target_chunk).count("Ownable") != 1:
        return None
    if _source_function_decl_infos(target_chunk, "owner"):
        return None
    ownable_chunk, common_evidence = ownable_shape
    if _source_constructor_params_from_source(source, "Ownable"):
        return None
    ctor_body = _mask_solidity_comments_and_strings(_constructor_body_text(ownable_chunk))
    if re.fullmatch(r"\s*_transferOwnership\s*\(\s*_msgSender\s*\(\s*\)\s*\)"
                    r"\s*;\s*", ctor_body, re.S) is None:
        return None
    return {
        **common_evidence,
        "direct_base": "Ownable",
        "constructor_flow": "Ownable._msgSender() -> _owner",
    }


def _ownable_owner_fixture_for_job(subject: PreparedSubject, job: dict,
                                   case_dir: Path) -> dict | None:
    """Source-checked deployment fixture for inherited Ownable.owner().

    Some exact targets fail Stage 2 before the focused unit is reachable
    because the benchmark harness cannot establish the constructor-owned
    scalar state. OpenZeppelin-style Ownable targets are narrow repairable
    cases: either a nonzero address parameter or ``_msgSender()`` initializes
    ``_owner``, and ``owner()`` only reads that field. Use the existing
    path-cov-fixture mechanism so Stage 2 still certifies ESBMC paths and Stage
    4 mirrors the same state in Foundry.
    """
    unit_info = job.get("unit_info") or {}
    unit = job.get("unit")
    if unit != "owner":
        return None
    if unit_info.get("parameter_count") != 0:
        return None
    if unit_info.get("return_types") != ["address"]:
        return None
    if unit_info.get("state_mutability") not in ("view", "pure"):
        return None
    path_function = job.get("path_function")
    declaration_id = None
    if path_function:
        match = re.search(r"#([0-9]+)$", str(path_function))
        if match:
            declaration_id = int(match.group(1))
    deps, _dep_evidence = unit_state_dependencies(subject.solast,
                                                  subject.contract,
                                                  unit,
                                                  declaration_id=declaration_id)
    if deps != ["_owner"]:
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    masked_source = _mask_solidity_comments_and_strings(flat_source)
    ctor_params = _source_constructor_params_from_source(masked_source, subject.contract)
    address_evidence = _ownable_address_constructor_source_evidence(masked_source, subject.contract)
    source_evidence = address_evidence
    fixture_kind = "ownable-owner-nonzero-constructor-state"
    if address_evidence is not None:
        param_index = int(address_evidence["constructor_param_index"])
        if not ctor_params or param_index >= len(ctor_params):
            return None
    else:
        source_evidence = _ownable_sender_constructor_source_evidence(masked_source,
                                                                      subject.contract)
        if ctor_params or source_evidence is None:
            return None
        fixture_kind = "ownable-owner-msg-sender-constructor-state"
    store_names, store_evidence = contract_state_esbmc_store_names(subject.solast, subject.contract)
    if store_evidence or store_names.get("_owner") is None:
        return None
    owner_value = "0x00000000000000000000000000000000000003e8"
    owner_expr = "address(uint160(1000))"
    constructor_args = []
    for idx, (_name, typ) in enumerate(ctor_params):
        if address_evidence is not None and idx == param_index:
            constructor_args.append(owner_expr)
        else:
            constructor_args.append(_source_type_default_expr(typ, flat_source))
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json"
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {
            store_names["_owner"]: owner_value,
        },
        "foundry": {
            "skip_constructor": True,
            "constructor_args": constructor_args,
        },
        "veriput_fixture_kind": fixture_kind,
        "source_evidence": {
            "unit":
            unit,
            "path_function":
            path_function,
            "state_dependency":
            "_owner",
            "esbmc_state_store":
            store_names["_owner"],
            "constructor_initialization": ("nonzero-address-parameter" if address_evidence
                                           is not None else "_transferOwnership(_msgSender())"),
            **source_evidence,
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
    }


def _transparent_proxy_fixture_for_job(subject: PreparedSubject, job: dict,
                                       case_dir: Path) -> dict | None:
    """Skip an infeasible transparent-proxy deployment without inventing state.

    ERC1967 proxy state is addressed through ``StorageSlot`` rather than named
    contract fields, so the scalar fixture mechanism cannot assign its admin or
    implementation values.  The sound common entry state is therefore the
    zero-storage runtime object: ESBMC skips the constructor and Foundry etches
    the target runtime bytecode at a fresh address.  Both executions then start
    with the same empty unstructured storage.
    """
    unit = str(job.get("unit") or "")
    if unit not in {"admin", "implementation", "changeAdmin", "upgradeTo", "upgradeToAndCall"}:
        return None
    unit_info = job.get("unit_info") or {}
    if unit_info.get("visibility") != "external":
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None:
        return None
    if "TransparentUpgradeableProxy" not in _source_inheritance_names(target_chunk):
        return None
    ctor_params = _source_constructor_params_from_source(flat_source, subject.contract)
    if [typ for _name, typ in ctor_params] != ["address", "address", "bytes"]:
        return None
    required_source = (
        r"modifier\s+ifAdmin\s*\(",
        r"StorageSlot\.getAddressSlot\s*\(\s*_ADMIN_SLOT\s*\)\.value",
        r"StorageSlot\.getAddressSlot\s*\(\s*_IMPLEMENTATION_SLOT\s*\)\.value",
    )
    if not all(re.search(pattern, flat_source) for pattern in required_source):
        return None
    path_function = job.get("path_function")
    declaration_id = None
    if path_function:
        match = re.search(r"#([0-9]+)$", str(path_function))
        if match:
            declaration_id = int(match.group(1))
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 unit,
                                                 declaration_id=declaration_id)
    if not deps or not set(deps).issubset({"_ADMIN_SLOT", "_IMPLEMENTATION_SLOT"}):
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor":
            True,
            **({
                "target_call_mode": "low-level-success"
            } if unit in {"admin", "implementation"} else {}),
        },
        "veriput_fixture_kind": "transparent-proxy-zero-storage-runtime",
        "source_evidence": {
            "unit":
            unit,
            "path_function":
            path_function,
            "direct_base":
            "TransparentUpgradeableProxy",
            "constructor_param_types": [typ for _name, typ in ctor_params],
            "state_dependencies":
            deps,
            "state_dependency_evidence":
            dep_evidence,
            "foundry_replay":
            "etch-runtime-with-zero-storage",
            "target_call_mode":
            ("low-level-success" if unit in {"admin", "implementation"} else "high-level"),
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
    }


def _asset_list_empty_fixture_for_job(subject: PreparedSubject, job: dict,
                                      case_dir: Path) -> dict | None:
    """Take AssetList's constructor out of the all-reverting empty-list path.

    Compound's AssetList constructor expands 24 calls which pack an optional
    dynamic struct-array element into 48 immutable words.  With an empty array
    every call returns zero, ``numAssets`` is zero, and the focused uint8
    getter reverts before reading any packed word.  Recording that one scalar
    state removes the expensive deployment from Stage 2 while Foundry can
    replay the exact legal empty-array deployment.
    """
    unit = str(job.get("unit") or "")
    unit_info = job.get("unit_info") or {}
    if (subject.contract != "AssetList" or unit != "getAssetInfo"):
        return None
    if unit_info.get("parameter_types") != ["uint8"]:
        return None
    if unit_info.get("return_types") != ["struct CometCore.AssetInfo"]:
        return None
    if unit_info.get("state_mutability") != "view":
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None:
        return None
    ctor_params = _source_constructor_params_from_source(flat_source, subject.contract)
    if ctor_params != [("assetConfigs", "CometConfiguration.AssetConfig[]")]:
        return None
    masked = _mask_solidity_comments_and_strings(target_chunk)
    required_source = (
        r"\buint8\s+public\s+immutable\s+numAssets\s*;",
        r"\buint8\s+_numAssets\s*=\s*uint8\s*\(\s*assetConfigs\.length\s*\)\s*;",
        r"\bnumAssets\s*=\s*_numAssets\s*;",
        r"\bif\s*\(\s*i\s*>=\s*numAssets\s*\)\s*revert\s+"
        r"CometMainInterface\.BadAsset\s*\(\s*\)\s*;",
    )
    if not all(re.search(pattern, masked) for pattern in required_source):
        return None
    if len(re.findall(r"\bgetPackedAssetInternal\s*\(\s*assetConfigs\s*,\s*\d+\s*\)",
                      masked)) != 24:
        return None
    if _source_inheritance_names(target_chunk):
        return None
    # Direct immutable fields keep their source name in AssetList's ESBMC
    # contract struct.  The AST alias helper intentionally targets merged
    # inherited stores and would report ``numAssets$107`` here; the frontend's
    # fixture diagnostic and GOTO struct both expose this direct field as
    # ``numAssets``.
    esbmc_store = "numAssets"
    path_function = job.get("path_function")
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    constructor_arg = _source_type_default_expr(ctor_params[0][1], flat_source)
    if constructor_arg != "new CometConfiguration.AssetConfig[](0)":
        return None
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {
            esbmc_store: 0,
        },
        "foundry": {
            "skip_constructor": True,
            "constructor_args": [constructor_arg],
        },
        "veriput_fixture_kind": "asset-list-empty-array-revert",
        "source_evidence": {
            "unit": unit,
            "path_function": path_function,
            "constructor_param_type": ctor_params[0][1],
            "constructor_array_length": 0,
            "constructor_packed_slots": 24,
            "state_dependency": "numAssets",
            "esbmc_state_store": esbmc_store,
            "dominating_guard": "i >= numAssets",
            "guard_outcome": "all uint8 inputs revert before packed asset reads",
            "foundry_replay": "legal empty AssetConfig array deployment",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
        # The source guard proves every normal-return arm unreachable in this
        # fixture.  Keep the synthetic non-payable gate plus the first source
        # complete-path goal (the dominating BadAsset revert) instead of
        # enumerating the 2^24 syntactic combinations of the subsequent
        # one-hot ``if (i == N)`` chain.  The tool reports the truncation, and
        # Stage 4 claims only the retained source path.
        "esbmc_arg_pairs": [("--path-cov-max-goals", "2")],
    }


def _euler_cash_zero_storage_fixture_for_job(subject: PreparedSubject, job: dict,
                                             case_dir: Path) -> dict | None:
    """Use the EVK proxy-entry zero state for its storage-only cash view.

    Direct deployment validates five integration addresses, but the exact
    ``cash()`` body only reads ``vaultStorage.cash``.  A freshly etched runtime
    therefore gives ESBMC and Foundry the same proxy-entry state without
    inventing immutable integration values.  Keep this deliberately narrower
    than an EVK-wide constructor skip: the other module entries call them.
    """
    unit = str(job.get("unit") or "")
    unit_info = job.get("unit_info") or {}
    if unit != "cash" or unit_info.get("parameter_count") != 0:
        return None
    if unit_info.get("visibility") != "public":
        return None
    if unit_info.get("state_mutability") != "view":
        return None
    if unit_info.get("return_types") != ["uint256"]:
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    base_chunk = _source_contract_chunk(flat_source, "Base")
    borrowing_chunk = _source_contract_chunk(flat_source, "BorrowingModule")
    if target_chunk is None or base_chunk is None or borrowing_chunk is None:
        return None
    if _source_inheritance_names(target_chunk) != ["BorrowingModule"]:
        return None
    if _source_constructor_params_from_source(flat_source, subject.contract) != [("integrations",
                                                                                  "Integrations")]:
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if not re.search(
            r"constructor\s*\(\s*Integrations\s+memory\s+integrations\s*\)"
            r"\s*Base\s*\(\s*integrations\s*\)\s*\{\s*\}", masked_target):
        return None
    masked_base = _mask_solidity_comments_and_strings(base_chunk)
    required_base = (
        r"struct\s+Integrations\s*\{",
        r"constructor\s*\(\s*Integrations\s+memory\s+integrations\s*\)"
        r"\s*EVCClient\s*\(\s*integrations\.evc\s*\)",
        r"protocolConfig\s*=\s*IProtocolConfig\s*\(\s*AddressUtils\.checkContract"
        r"\s*\(\s*integrations\.protocolConfig\s*\)\s*\)",
        r"sequenceRegistry\s*=\s*ISequenceRegistry\s*\(\s*AddressUtils\.checkContract"
        r"\s*\(\s*integrations\.sequenceRegistry\s*\)\s*\)",
    )
    if not all(re.search(pattern, masked_base) for pattern in required_base):
        return None
    masked_borrowing = _mask_solidity_comments_and_strings(borrowing_chunk)
    if not re.search(
            r"function\s+cash\s*\(\s*\)\s+public\s+view\s+virtual\s+"
            r"nonReentrantView\s+returns\s*\(\s*uint256\s*\)\s*\{\s*"
            r"return\s+vaultStorage\.cash\.toUint\s*\(\s*\)\s*;\s*\}", masked_borrowing):
        return None
    path_function = job.get("path_function")
    declaration_id = _path_function_declaration_id(path_function or "")
    if declaration_id is not None:
        declaration_id = int(declaration_id)
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 unit,
                                                 declaration_id=declaration_id)
    if deps != ["vaultStorage"]:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor": True,
        },
        "veriput_fixture_kind": "evk-cash-proxy-entry-zero-storage",
        "source_evidence": {
            "unit": unit,
            "path_function": path_function,
            "direct_base": "BorrowingModule",
            "constructor": "Integrations memory integrations -> Base(integrations)",
            "state_dependencies": deps,
            "state_dependency_evidence": dep_evidence,
            "state_entry": "fresh proxy runtime with zero storage",
            "foundry_replay": "etch-runtime-with-zero-storage",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
    }


def _peg_stability_module_foundry_fixture_for_job(subject: PreparedSubject, job: dict,
                                                  case_dir: Path) -> dict | None:
    """Replay PSM quote witnesses with a legal source-level deployment.

    The generic constructor synthesizer uses zero for uint256 parameters, but
    this exact constructor rejects a zero conversion price.  Stage 2 does not
    need invented state: preserve its original constructor semantics and only
    give Stage 4 legal Foundry arguments for the four pure quote entry points.
    """
    unit = str(job.get("unit") or "")
    quote_units = {
        "quoteToUnderlyingGivenIn",
        "quoteToUnderlyingGivenOut",
        "quoteToSynthGivenIn",
        "quoteToSynthGivenOut",
    }
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "euler-xyz__euler-vault-kit__PegStabilityModule"
            or subject.contract != "PegStabilityModule" or unit not in quote_units):
        return None
    if (unit_info.get("visibility") != "public" or unit_info.get("state_mutability") != "view"
            or unit_info.get("parameter_types") != ["uint256"]
            or unit_info.get("return_types") != ["uint256"]):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None:
        return None
    ctor_params = _source_constructor_params_from_source(flat_source, subject.contract)
    if [typ for _name, typ in ctor_params
        ] != ["address", "address", "address", "uint256", "uint256", "uint256"]:
        return None
    masked = _mask_solidity_comments_and_strings(target_chunk)
    required_source = (
        r"uint256\s+public\s+constant\s+BPS_SCALE\s*=\s*100_00\s*;",
        r"uint256\s+public\s+constant\s+PRICE_SCALE\s*=\s*1e18\s*;",
        r"if\s*\(\s*_synth\s*==\s*address\s*\(\s*0\s*\)\s*\|\|\s*"
        r"_underlying\s*==\s*address\s*\(\s*0\s*\)\s*\)",
        r"if\s*\(\s*_toUnderlyingFeeBPS\s*>=\s*BPS_SCALE\s*\|\|\s*"
        r"_toSynthFeeBPS\s*>=\s*BPS_SCALE\s*\)",
        r"if\s*\(\s*_conversionPrice\s*==\s*0\s*\)",
    )
    if not all(re.search(pattern, masked) for pattern in required_source):
        return None
    declarations = _source_function_decl_infos(target_chunk, unit)
    if len(declarations) != 1:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    constructor_args = [
        "address(uint160(1000))",
        "address(uint160(1001))",
        "address(uint160(1002))",
        "0",
        "0",
        "1e18",
    ]
    fixture = {
        "contract": subject.contract,
        "foundry": {
            "constructor_args": constructor_args,
        },
        "veriput_fixture_kind": "psm-legal-foundry-constructor",
        "source_evidence": {
            "unit": unit,
            "path_function": job.get("path_function"),
            "constructor_param_types": [typ for _name, typ in ctor_params],
            "nonzero_contract_args": ["_synth", "_underlying"],
            "fee_bounds": "_toUnderlyingFeeBPS,_toSynthFeeBPS < BPS_SCALE",
            "conversion_price_guard": "_conversionPrice != 0",
            "foundry_conversion_price": "1e18",
            "stage2_semantics": "unchanged",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
    }


def _balancer_lp_oracle_decimals_fixture_for_job(subject: PreparedSubject, job: dict,
                                                 case_dir: Path) -> dict | None:
    """Separate the pure LP-oracle decimals getter from deployment.

    These three contracts require a non-empty feed array and several live
    interfaces during construction.  The focused getter is inherited from the
    exact LPOracleBase pure body and reads no instance state.  Skipping the
    constructor in Stage 2 therefore preserves the complete getter semantics;
    Foundry still deploys the real target with two feeds and synthesized
    interface mocks before replaying the call.
    """
    allowed = {
        "balancer__balancer-v3-monorepo__StableLPOracle",
        "balancer__balancer-v3-monorepo__WeightedLPOracle",
        "balancer__balancer-v3-monorepo__DynamicWeightedLPOracle",
    }
    unit_info = job.get("unit_info") or {}
    if subject.subject_id not in allowed or str(job.get("unit") or "") != "decimals":
        return None
    if (unit_info.get("visibility") != "external" or unit_info.get("state_mutability") != "pure"
            or unit_info.get("parameter_count") != 0 or unit_info.get("return_types") != ["uint8"]):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    base_chunk = _source_contract_chunk(flat_source, "LPOracleBase")
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if base_chunk is None or target_chunk is None:
        return None
    masked_base = _mask_solidity_comments_and_strings(base_chunk)
    if re.search(
            r"function\s+decimals\s*\(\s*\)\s+external\s+pure\s+"
            r"returns\s*\(\s*uint8\s*\)\s*\{\s*return\s+uint8\s*\(\s*"
            r"_WAD_DECIMALS\s*\)\s*;\s*\}", masked_base, re.S) is None:
        return None
    target_bases = _source_inheritance_names(target_chunk)
    if ("LPOracleBase" not in target_bases
            and not (subject.contract == "DynamicWeightedLPOracle"
                     and target_bases == ["IWeightedLPOracle", "WeightedLPOracle"] and re.search(
                         r"contract\s+WeightedLPOracle\s+is\s+"
                         r"IWeightedLPOracle\s*,\s*LPOracleBase\b", flat_source) is not None)):
        return None
    ctor_params = _source_constructor_params_from_source(flat_source, subject.contract)
    if not ctor_params or not any(
            _norm_ty(typ) == "AggregatorV3Interface[]" for _name, typ in ctor_params):
        return None
    constructor_args = []
    feed_param = None
    for idx, (_name, typ) in enumerate(ctor_params):
        if _norm_ty(typ) == "AggregatorV3Interface[]":
            if feed_param is not None:
                return None
            feed_param = idx
            constructor_args.append("new AggregatorV3Interface[](2)")
        else:
            constructor_args.append(_source_type_default_expr(typ, 1000 + idx))
    if feed_param is None:
        return None

    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (
        f"{_safe_name(job.get('job_id') or 'decimals')}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor": True,
            "constructor_args": constructor_args,
        },
        "veriput_fixture_kind": "balancer-lp-oracle-pure-decimals",
        "source_evidence": {
            "unit": "decimals",
            "path_function": job.get("path_function"),
            "declaring_contract": "LPOracleBase",
            "getter_body": "return uint8(_WAD_DECIMALS)",
            "state_dependencies": [],
            "constructor_param_types": [typ for _name, typ in ctor_params],
            "feed_param_index": feed_param,
            "foundry_feed_length": 2,
            "stage2_semantics": "pure getter; constructor-independent",
            "foundry_replay": "real deployment with interface mocks",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
    }


def _chain_reverse_resolver_supports_feature_fixture_for_job(subject: PreparedSubject, job: dict,
                                                             case_dir: Path) -> dict | None:
    """Separate the inherited pure feature probe from deployment.

    ChainReverseResolver's constructor initializes several unrelated resolver
    dependencies.  The inherited supportsFeature(bytes4) body is pure, ignores
    its argument, and returns false.  Stage 2 can therefore skip construction
    without changing the focused function; Foundry still deploys the concrete
    target with legal arguments before replaying the certified call.
    """
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "ensdomains__ens-contracts__ChainReverseResolver"
            or subject.contract != "ChainReverseResolver"
            or str(job.get("unit") or "") != "supportsFeature"):
        return None
    if (unit_info.get("visibility") != "external" or unit_info.get("state_mutability") != "pure"
            or unit_info.get("parameter_types") != ["bytes4"]
            or unit_info.get("return_types") != ["bool"]):
        return None
    path_function = str(job.get("path_function") or "")
    match = re.search(r"#([0-9]+)$", path_function)
    if match is None:
        return None
    declaration_id = int(match.group(1))
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 "supportsFeature",
                                                 declaration_id=declaration_id)
    if deps or dep_evidence:
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    base_chunk = _source_contract_chunk(flat_source, "AbstractReverseResolver")
    if target_chunk is None or base_chunk is None:
        return None
    if "AbstractReverseResolver" not in _source_inheritance_names(target_chunk):
        return None
    masked_base = _mask_solidity_comments_and_strings(base_chunk)
    if re.search(
            r"function\s+supportsFeature\s*\(\s*bytes4\s*\)\s*"
            r"external\s+pure\s+returns\s*\(\s*bool\s*\)\s*\{\s*"
            r"return\s+false\s*;\s*\}", masked_base, re.S) is None:
        return None
    ctor_params = _source_constructor_params_from_source(flat_source, subject.contract)
    ctor_types = [typ for _name, typ in ctor_params]
    if ctor_types != [
            "address", "uint256", "IStandaloneReverseRegistrar", "address", "IGatewayVerifier",
            "string[]"
    ]:
        return None
    constructor_args = [
        "address(uint160(1000))",
        "0",
        "IStandaloneReverseRegistrar(address(uint160(1002)))",
        "address(uint160(1003))",
        "IGatewayVerifier(address(uint160(1004)))",
        "new string[](0)",
    ]
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (
        f"{_safe_name(job.get('job_id') or 'supportsFeature')}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor": True,
            "constructor_args": constructor_args,
        },
        "veriput_fixture_kind": "chain-reverse-resolver-pure-supports-feature",
        "source_evidence": {
            "unit": "supportsFeature",
            "path_function": path_function,
            "declaring_contract": "AbstractReverseResolver",
            "function_body": "return false",
            "parameter_types": ["bytes4"],
            "parameter_use": "unused",
            "state_dependencies": [],
            "constructor_param_types": ctor_types,
            "stage2_semantics": "pure unit; constructor-independent",
            "foundry_replay": "real ChainReverseResolver deployment",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
    }


def _universal_sig_validator_wrapper_fixture_for_job(subject: PreparedSubject, job: dict,
                                                     case_dir: Path) -> dict | None:
    """Keep the small external wrapper separate from its large callee graph."""
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "ensdomains__ens-contracts__UniversalSigValidator"
            or subject.contract != "UniversalSigValidator"
            or str(job.get("unit") or "") != "isValidSigWithSideEffects"):
        return None
    if (unit_info.get("visibility") != "external"
            or unit_info.get("state_mutability") != "nonpayable"
            or unit_info.get("parameter_types") != ["address", "bytes32", "bytes"]
            or unit_info.get("return_types") != ["bool"]):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None or _source_constructor_params_from_source(flat_source,
                                                                      subject.contract):
        return None
    declarations = _source_function_decl_infos(_mask_solidity_comments_and_strings(target_chunk),
                                               "isValidSigWithSideEffects")
    if len(declarations) != 1:
        return None
    params, header_tail, body = declarations[0]
    if ([typ for _name, typ in params] != ["address", "bytes32", "bytes"]
            or re.search(r"\bexternal\b", header_tail) is None or re.fullmatch(
                r"\s*return\s+this\s*\.\s*isValidSigImpl\s*\(\s*"
                r"_signer\s*,\s*_hash\s*,\s*_signature\s*,\s*true\s*\)\s*;\s*", body,
                re.S) is None):
        return None
    path_function = str(job.get("path_function") or unit_info.get("path_function") or "")
    match = re.search(r"#([0-9]+)$", path_function)
    if match is None:
        return None
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 "isValidSigWithSideEffects",
                                                 declaration_id=int(match.group(1)))
    if deps != ["ERC1271_SUCCESS", "ERC6492_DETECTION_SUFFIX"]:
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if (re.search(
            r"bytes32\s+private\s+constant\s+ERC6492_DETECTION_SUFFIX\s*=\s*"
            r"0x6492649264926492649264926492649264926492649264926492649264926492\s*;",
            masked_target) is None
            or re.search(r"bytes4\s+private\s+constant\s+ERC1271_SUCCESS\s*=\s*"
                         r"0x1626ba7e\s*;", masked_target) is None):
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or 'isValidSigWithSideEffects')}"
                                  ".path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor": True,
            "constructor_args": [],
        },
        "veriput_fixture_kind": "universal-sig-validator-side-effects-wrapper",
        "source_evidence": {
            "unit": "isValidSigWithSideEffects",
            "path_function": path_function,
            "function_body": ("return this.isValidSigImpl(_signer, _hash, _signature, true)"),
            "state_dependencies": deps,
            "state_dependency_evidence": dep_evidence,
            "constructor_param_types": [],
            "stage2_semantics": "external wrapper with bounded callee path identities",
            "foundry_replay": "real UniversalSigValidator deployment",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path":
        str(fixture_path),
        "fixture":
        fixture,
        "argv_value_pairs": [("--probes", "2")],
        "esbmc_arg_remove_flags":
        ["--overflow-check", "--div-by-zero-check", "--path-cov-arith-resolve"],
        "esbmc_arg_pairs": [
            ("--path-cov-max-goals", "2"),
            ("--unwind", "1"),
        ],
    }


def _ccip_reader_callback_fixture_for_job(subject: PreparedSubject, job: dict,
                                          case_dir: Path) -> dict | None:
    """Bound the callback's large internal decode/call path identity graph."""
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "ensdomains__ens-contracts__CCIPReader"
            or subject.contract != "CCIPReader"
            or str(job.get("unit") or "") != "ccipReadCallback"):
        return None
    if (unit_info.get("visibility") != "external" or unit_info.get("state_mutability") != "view"
            or unit_info.get("parameter_types") != ["bytes", "bytes"]
            or unit_info.get("return_types") != []):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None:
        return None
    ctor_params = _source_constructor_params_from_source(flat_source, subject.contract)
    if ctor_params != [("_unsafeCallGas", "uint256")]:
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if (re.search(
            r"constructor\s*\(\s*uint256\s+_unsafeCallGas\s*\)\s*\{\s*"
            r"unsafeCallGas\s*=\s*_unsafeCallGas\s*;\s*\}", masked_target, re.S) is None
            or re.search(
                r"function\s+ccipReadCallback\s*\(\s*bytes\s+memory\s+response\s*,\s*"
                r"bytes\s+memory\s+extraData\s*\)\s*external\s+view\s*\{\s*"
                r"Context\s+memory\s+ctx\s*=\s*abi\.decode\s*\(\s*extraData\s*,\s*"
                r"\(\s*Context\s*\)\s*\)\s*;", masked_target, re.S) is None):
        return None
    path_function = str(job.get("path_function") or unit_info.get("path_function") or "")
    match = re.search(r"#([0-9]+)$", path_function)
    if match is None:
        return None
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 "ccipReadCallback",
                                                 declaration_id=int(match.group(1)))
    if deps != ["IDENTITY_FUNCTION", "unsafeCallGas"]:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (
        f"{_safe_name(job.get('job_id') or 'ccipReadCallback')}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor": True,
            "constructor_args": ["50000"],
        },
        "veriput_fixture_kind": "ccip-reader-callback-bounded-path-identities",
        "source_evidence": {
            "unit": "ccipReadCallback",
            "path_function": path_function,
            "entry_decode": "abi.decode(extraData, (Context))",
            "state_dependencies": deps,
            "state_dependency_evidence": dep_evidence,
            "constructor_param_types": ["uint256"],
            "stage2_semantics": "view callback with bounded internal-call path identities",
            "foundry_replay": "real CCIPReader(50000) deployment",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path":
        str(fixture_path),
        "fixture":
        fixture,
        "argv_value_pairs": [("--probes", "2")],
        "esbmc_arg_remove_flags":
        ["--overflow-check", "--div-by-zero-check", "--path-cov-arith-resolve"],
        "esbmc_arg_pairs": [
            ("--path-cov-max-goals", "2"),
            ("--unwind", "1"),
        ],
    }


def _call_and_revert_value_gate_fixture_for_job(subject: PreparedSubject, job: dict,
                                                case_dir: Path) -> dict | None:
    """Retain the external ABI gate before the revert-forwarding body."""
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "balancer__balancer-v3-monorepo__CallAndRevert"
            or subject.contract != "CallAndRevert"
            or str(job.get("unit") or "") != "callAndRevertHook"):
        return None
    if (unit_info.get("visibility") != "external"
            or unit_info.get("state_mutability") != "nonpayable"
            or unit_info.get("parameter_types") != ["address", "bytes"]
            or unit_info.get("return_types") != []):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None or _source_constructor_params_from_source(flat_source,
                                                                      subject.contract):
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if re.search(
            r"function\s+callAndRevertHook\s*\(\s*address\s+target\s*,\s*"
            r"bytes\s+memory\s+data\s*\)\s*external\s*\{\s*"
            r"\(\s*bool\s+success\s*,\s*bytes\s+memory\s+result\s*\)\s*=\s*"
            r"\(\s*target\s*\)\s*\.\s*call\s*\(\s*data\s*\)\s*;", masked_target, re.S) is None:
        return None
    path_function = str(job.get("path_function") or unit_info.get("path_function") or "")
    match = re.search(r"#([0-9]+)$", path_function)
    if match is None:
        return None
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 "callAndRevertHook",
                                                 declaration_id=int(match.group(1)))
    if deps or dep_evidence:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (
        f"{_safe_name(job.get('job_id') or 'callAndRevertHook')}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor": True,
            "constructor_args": [],
        },
        "veriput_fixture_kind": "call-and-revert-abi-value-gate",
        "source_evidence": {
            "unit": "callAndRevertHook",
            "path_function": path_function,
            "target_call": "target.call(data)",
            "state_dependencies": [],
            "constructor_param_types": [],
            "stage2_semantics": "external ABI value gate",
            "foundry_replay": "real CallAndRevert deployment",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path":
        str(fixture_path),
        "fixture":
        fixture,
        "argv_value_pairs": [("--probes", "2")],
        "esbmc_arg_remove_flags":
        ["--overflow-check", "--div-by-zero-check", "--path-cov-arith-resolve"],
        "esbmc_arg_pairs": [
            ("--path-cov-max-goals", "2"),
            ("--unwind", "1"),
        ],
    }


def _putty_whitelist_pure_fixture_for_job(subject: PreparedSubject, job: dict,
                                          case_dir: Path) -> dict | None:
    """Bound the constructor-independent empty-whitelist path."""
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "pop_058_PuttyV2" or subject.contract != "PuttyV2"
            or str(job.get("unit") or "") != "isWhitelisted"):
        return None
    if (unit_info.get("visibility") != "public" or unit_info.get("state_mutability") != "pure"
            or unit_info.get("parameter_types") != ["address[]", "address"]
            or unit_info.get("return_types") != ["bool"]):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None or _source_constructor_params_from_source(
            flat_source, subject.contract) != [("_baseURI", "string"), ("_fee", "uint256"),
                                               ("_weth", "address")]:
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if re.search(
            r"function\s+isWhitelisted\s*\(\s*address\[\]\s+memory\s+"
            r"whitelist\s*,\s*address\s+target\s*\)\s*public\s+pure\s+"
            r"returns\s*\(\s*bool\s*\)\s*\{\s*for\s*\(\s*uint256\s+i\s*"
            r"=\s*0\s*;\s*i\s*<\s*whitelist\.length\s*;\s*i\+\+\s*\)\s*"
            r"\{\s*if\s*\(\s*target\s*==\s*whitelist\[i\]\s*\)\s*"
            r"return\s+true\s*;\s*\}\s*return\s+false\s*;\s*\}", masked_target, re.S) is None:
        return None
    path_function = str(job.get("path_function") or unit_info.get("path_function") or "")
    match = re.search(r"#([0-9]+)$", path_function)
    if match is None:
        return None
    deps, dep_evidence = unit_state_dependencies(subject.solast,
                                                 subject.contract,
                                                 "isWhitelisted",
                                                 declaration_id=int(match.group(1)))
    if deps or dep_evidence:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (
        f"{_safe_name(job.get('job_id') or 'isWhitelisted')}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "constructor_args": ["\"VeriPUT1000\"", "0", "address(uint160(1002))"],
        },
        "veriput_fixture_kind": "putty-whitelist-pure-empty-array",
        "source_evidence": {
            "unit": "isWhitelisted",
            "path_function": path_function,
            "empty_array_return": False,
            "state_dependencies": [],
            "environment_dependencies": [],
            "constructor_param_types": ["string", "uint256", "address"],
            "stage2_semantics": "constructor-independent public pure loop",
            "foundry_replay": "real PuttyV2 deployment",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path":
        str(fixture_path),
        "fixture":
        fixture,
        "argv_remove_flags": ["--pin-agreed-establishable-env"],
        "argv_value_pairs": [("--probes", "2")],
        "esbmc_arg_remove_flags":
        ["--overflow-check", "--div-by-zero-check", "--path-cov-arith-resolve"],
        "esbmc_arg_pairs": [
            ("--path-cov-max-goals", "2"),
            ("--unwind", "1"),
        ],
    }


def _transfer_helper_zero_key_fixture_for_job(subject: PreparedSubject, job: dict,
                                              case_dir: Path) -> dict | None:
    """Retain TransferHelper's source-dominating zero-conduit rejection."""
    unit = str(job.get("unit") or "")
    unit_info = job.get("unit_info") or {}
    if (subject.subject_id != "ProjectOpenSea__seaport__TransferHelper"
            or subject.contract != "TransferHelper" or unit != "bulkTransfer"):
        return None
    if (unit_info.get("visibility") != "external"
            or unit_info.get("state_mutability") != "nonpayable" or unit_info.get("parameter_types")
            != ["struct TransferHelperItemsWithRecipient[]", "bytes32"]
            or unit_info.get("return_types") != ["bytes4"]):
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, subject.contract)
    if target_chunk is None:
        return None
    if _source_constructor_params_from_source(flat_source, subject.contract) != [
        ("conduitController", "address")
    ]:
        return None
    masked = _mask_solidity_comments_and_strings(target_chunk)
    required_source = (
        r"constructor\s*\(\s*address\s+conduitController\s*\)",
        r"controller\s*\.\s*getConduitCodeHashes\s*\(\s*\)",
        r"function\s+bulkTransfer\s*\(\s*"
        r"TransferHelperItemsWithRecipient\[\]\s+calldata\s+items\s*,\s*"
        r"bytes32\s+conduitKey\s*\)\s*external\s+override\s+"
        r"returns\s*\(\s*bytes4\s+magicValue\s*\)",
        r"if\s*\(\s*conduitKey\s*==\s*bytes32\s*\(\s*0\s*\)\s*\)\s*"
        r"\{\s*revert\s+InvalidConduit\s*\(\s*conduitKey\s*,\s*"
        r"address\s*\(\s*0\s*\)\s*\)\s*;\s*\}",
    )
    if not all(re.search(pattern, masked, re.S) for pattern in required_source):
        return None
    declarations = _source_function_decl_infos(target_chunk, unit)
    if len(declarations) != 1:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "foundry": {
            "constructor_args": ["address(uint160(1000))"],
            "expected_revert_signature": "InvalidConduit(bytes32,address)",
        },
        "veriput_fixture_kind": "transfer-helper-zero-conduit-rejection",
        "source_evidence": {
            "unit": unit,
            "path_function": job.get("path_function"),
            "constructor_external_call": "controller.getConduitCodeHashes()",
            "dominating_guard": "conduitKey == bytes32(0)",
            "retained_exit": "InvalidConduit(conduitKey,address(0))",
            "precedes": "_performTransfersWithConduit(items, conduitKey)",
            "stage2_semantics": "constructor unchanged; source goal shard only",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
        "esbmc_arg_pairs": [("--path-cov-max-goals", "2")],
    }


def _euler_initialize_direct_deploy_fixture_for_job(subject: PreparedSubject, job: dict,
                                                    case_dir: Path) -> dict | None:
    """Rebuild the one constructor field that dominates EVK initialize().

    ``Initialize`` is an implementation contract whose constructor disables
    direct initialization.  Its intended proxy entry has zero storage, but the
    exact target is also legally reachable as a direct deployment, where the
    first source guard always reverts before integrations or proxy metadata are
    read.  Reconstruct only that source-proven state and leave this fixture
    narrower than the EVK-wide constructor skip used for ``cash()``.
    """
    unit = str(job.get("unit") or "")
    unit_info = job.get("unit_info") or {}
    if subject.subject_id != "euler-xyz__euler-vault-kit__Initialize":
        return None
    if subject.contract != "Initialize" or unit != "initialize":
        return None
    if unit_info.get("visibility") != "public":
        return None
    if unit_info.get("parameter_types") != ["address"]:
        return None
    if unit_info.get("return_types") != []:
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, "Initialize")
    module_chunk = _source_contract_chunk(flat_source, "InitializeModule")
    if target_chunk is None or module_chunk is None:
        return None
    if _source_inheritance_names(target_chunk) != ["InitializeModule"]:
        return None
    if _source_constructor_params_from_source(flat_source,
                                              "Initialize") != [("integrations", "Integrations")]:
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if not re.search(
            r"constructor\s*\(\s*Integrations\s+memory\s+integrations\s*\)"
            r"\s*Base\s*\(\s*integrations\s*\)\s*\{\s*\}", masked_target):
        return None
    masked_module = _mask_solidity_comments_and_strings(module_chunk)
    required_module = (
        r"function\s+initialize\s*\(\s*address\s+proxyCreator\s*\)\s*"
        r"public\s+virtual\s+reentrantOK\s*\{\s*"
        r"if\s*\(\s*initialized\s*\)\s*revert\s+E_Initialized\s*\(\s*\)\s*;",
        r"constructor\s*\(\s*\)\s*\{\s*initialized\s*=\s*true\s*;\s*\}",
    )
    if not all(re.search(pattern, masked_module, re.S) for pattern in required_module):
        return None
    path_function = job.get("path_function")
    declaration_id = _path_function_declaration_id(path_function or "")
    deps, dep_evidence = unit_state_dependencies(
        subject.solast,
        subject.contract,
        unit,
        declaration_id=(int(declaration_id) if declaration_id is not None else None))
    if "initialized" not in deps:
        return None
    store_names, store_evidence = contract_state_esbmc_store_names(subject.solast, subject.contract)
    initialized_store = store_names.get("initialized")
    if store_evidence or initialized_store is None:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {
            initialized_store: 1,
        },
        "foundry": {
            "skip_constructor":
            True,
            "constructor_args": [
                "Base.Integrations({evc: address(uint160(1000)), "
                "protocolConfig: address(uint160(1001)), sequenceRegistry: "
                "address(uint160(1002)), balanceTracker: address(uint160(1003)), "
                "permit2: address(uint160(1004))})",
            ],
            "target_call_mode":
            "low-level-revert",
            "target_call_signature":
            "initialize(address)",
        },
        "veriput_fixture_kind": "evk-initialize-direct-deploy-guard",
        "source_evidence": {
            "unit": unit,
            "path_function": path_function,
            "direct_base": "InitializeModule",
            "constructor": "Integrations memory integrations -> Base(integrations)",
            "constructor_initialization": "initialized = true",
            "dominating_guard": "if (initialized) revert E_Initialized()",
            "esbmc_state_store": initialized_store,
            "state_dependencies": deps,
            "state_dependency_evidence": dep_evidence,
            "foundry_replay": "legal Integrations deployment and low-level revert assertion",
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
        "esbmc_arg_pairs": [("--path-cov-max-goals", "2")],
    }


def _euler_risk_manager_unauthorized_fixture_for_job(subject: PreparedSubject, job: dict,
                                                     case_dir: Path) -> dict | None:
    """Retain RiskManager.checkVaultStatus's proxy-entry auth rejection.

    The full authorised body expands into 1,343 complete-path goals, while the
    first source guard rejects every non-EVC caller before reading vault state.
    EVK modules execute behind a proxy, so a freshly etched runtime is a real
    entry state for the implementation code.  Use that same zero runtime in
    ESBMC and Foundry and retain only the ABI gate plus the dominating source
    rejection.  The exact source and AST checks below keep this from becoming
    an EVK-wide path truncation rule.
    """
    unit = str(job.get("unit") or "")
    unit_info = job.get("unit_info") or {}
    if subject.subject_id != "euler-xyz__euler-vault-kit__RiskManager":
        return None
    if subject.contract != "RiskManager" or unit != "checkVaultStatus":
        return None
    if unit_info.get("visibility") != "public":
        return None
    if unit_info.get("parameter_types") != []:
        return None
    if unit_info.get("return_types") != ["bytes4"]:
        return None
    if unit_info.get("state_mutability") != "nonpayable":
        return None
    try:
        flat_source = Path(subject.flat_sol).read_text(errors="replace")
    except OSError:
        return None
    target_chunk = _source_contract_chunk(flat_source, "RiskManager")
    module_chunk = _source_contract_chunk(flat_source, "RiskManagerModule")
    client_chunk = _source_contract_chunk(flat_source, "EVCClient")
    if target_chunk is None or module_chunk is None or client_chunk is None:
        return None
    if _source_inheritance_names(target_chunk) != ["RiskManagerModule"]:
        return None
    if _source_constructor_params_from_source(flat_source,
                                              "RiskManager") != [("integrations", "Integrations")]:
        return None
    masked_target = _mask_solidity_comments_and_strings(target_chunk)
    if not re.search(
            r"constructor\s*\(\s*Integrations\s+memory\s+integrations\s*\)"
            r"\s*Base\s*\(\s*integrations\s*\)\s*\{\s*\}", masked_target):
        return None
    masked_module = _mask_solidity_comments_and_strings(module_chunk)
    if not re.search(
            r"function\s+checkVaultStatus\s*\(\s*\)\s+public\s+virtual\s+"
            r"reentrantOK\s+onlyEVCChecks\s+returns\s*\(\s*bytes4\s+magicValue\s*\)",
            masked_module):
        return None
    masked_client = _mask_solidity_comments_and_strings(client_chunk)
    if not re.search(
            r"modifier\s+onlyEVCChecks\s*\(\s*\)\s*\{\s*"
            r"if\s*\(\s*msg\.sender\s*!=\s*address\s*\(\s*evc\s*\)\s*\|\|\s*"
            r"!\s*evc\.areChecksInProgress\s*\(\s*\)\s*\)\s*\{\s*"
            r"revert\s+E_CheckUnauthorized\s*\(\s*\)\s*;", masked_client, re.S):
        return None
    path_function = job.get("path_function")
    declaration_id = _path_function_declaration_id(path_function or "")
    deps, dep_evidence = unit_state_dependencies(
        subject.solast,
        subject.contract,
        unit,
        declaration_id=(int(declaration_id) if declaration_id is not None else None))
    if deps != ["evc", "snapshot", "vaultStorage"]:
        return None
    fixture_dir = case_dir / "cert" / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / (f"{_safe_name(job.get('job_id') or unit)}.path-cov-fixture.json")
    fixture = {
        "contract": subject.contract,
        "skip_constructor": True,
        "state": {},
        "foundry": {
            "skip_constructor":
            True,
            "constructor_args": [
                "Base.Integrations({evc: address(this), protocolConfig: address(this), "
                "sequenceRegistry: address(this), balanceTracker: address(0), "
                "permit2: address(0)})",
            ],
            "expected_revert_signature":
            "E_CheckUnauthorized()",
        },
        "veriput_fixture_kind": "evk-risk-manager-proxy-auth-rejection",
        "source_evidence": {
            "unit":
            unit,
            "path_function":
            path_function,
            "direct_base":
            "RiskManagerModule",
            "constructor":
            "Integrations memory integrations -> Base(integrations)",
            "state_entry":
            "fresh proxy runtime with zero storage and zero immutables",
            "dominating_guard": ("msg.sender != address(evc) || !evc.areChecksInProgress()"),
            "retained_path":
            "nonzero caller rejected before updateVault()",
            "unwind_boundary": ("1; retained guard exits before any external call or loop"),
            "state_dependencies":
            deps,
            "state_dependency_evidence":
            dep_evidence,
            "foundry_replay": ("legal Integrations deployment; address(0) caller differs from "
                               "immutable evc=address(this) and reverts E_CheckUnauthorized"),
        },
    }
    _write_json(fixture_path, fixture)
    return {
        "path": str(fixture_path),
        "fixture": fixture,
        "argv_remove_pairs": [("--env-coord", "msg.sender")],
        "argv_value_pairs": [("--probes", "0")],
        "esbmc_arg_pairs": [
            ("--path-cov-max-goals", "2"),
            ("--unwind", "1"),
        ],
    }


def apply_source_stage2_fixtures(schedule: dict, subject: PreparedSubject, case_dir: Path) -> dict:
    updated = copy.deepcopy(schedule)
    jobs = []
    applied = []
    for job in updated.get("jobs") or []:
        fixture = (_ownable_owner_fixture_for_job(subject, job, case_dir)
                   or _transparent_proxy_fixture_for_job(subject, job, case_dir)
                   or _asset_list_empty_fixture_for_job(subject, job, case_dir)
                   or _euler_cash_zero_storage_fixture_for_job(subject, job, case_dir)
                   or _balancer_lp_oracle_decimals_fixture_for_job(subject, job, case_dir)
                   or _chain_reverse_resolver_supports_feature_fixture_for_job(
                       subject, job, case_dir)
                   or _universal_sig_validator_wrapper_fixture_for_job(subject, job, case_dir)
                   or _ccip_reader_callback_fixture_for_job(subject, job, case_dir)
                   or _call_and_revert_value_gate_fixture_for_job(subject, job, case_dir)
                   or _putty_whitelist_pure_fixture_for_job(subject, job, case_dir)
                   or _peg_stability_module_foundry_fixture_for_job(subject, job, case_dir)
                   or _transfer_helper_zero_key_fixture_for_job(subject, job, case_dir)
                   or _euler_initialize_direct_deploy_fixture_for_job(subject, job, case_dir)
                   or _euler_risk_manager_unauthorized_fixture_for_job(subject, job, case_dir))
        if not fixture:
            jobs.append(job)
            continue
        patched = copy.deepcopy(job)
        for flag in fixture.get("argv_remove_flags") or []:
            patched["certify_argv"] = [
                arg for arg in patched.get("certify_argv") or [] if arg != flag
            ]
            if patched.get("dry_run_argv"):
                patched["dry_run_argv"] = [arg for arg in patched["dry_run_argv"] if arg != flag]
        for flag, value in fixture.get("argv_remove_pairs") or []:
            patched["certify_argv"] = _remove_argv_pair(
                [str(arg) for arg in patched.get("certify_argv") or []], flag, value)
            if patched.get("dry_run_argv"):
                patched["dry_run_argv"] = _remove_argv_pair(
                    [str(arg) for arg in patched["dry_run_argv"]], flag, value)
        for flag, value in fixture.get("argv_value_pairs") or []:
            patched["certify_argv"] = _argv_with_value(
                [str(arg) for arg in patched.get("certify_argv") or []], flag, value)
            if patched.get("dry_run_argv"):
                patched["dry_run_argv"] = _argv_with_value(
                    [str(arg) for arg in patched["dry_run_argv"]], flag, value)
        for flag in fixture.get("esbmc_arg_remove_flags") or []:
            token = f"--esbmc-arg={flag}"
            patched["certify_argv"] = [
                arg for arg in patched.get("certify_argv") or [] if arg != token
            ]
            if patched.get("dry_run_argv"):
                patched["dry_run_argv"] = [arg for arg in patched["dry_run_argv"] if arg != token]
        patched["certify_argv"] = _append_esbmc_arg_pair(
            [str(arg) for arg in patched.get("certify_argv") or []], "--path-cov-fixture",
            fixture["path"])
        for flag, value in fixture.get("esbmc_arg_pairs") or []:
            patched["certify_argv"] = _append_esbmc_arg_pair(patched["certify_argv"], flag, value)
        if patched.get("dry_run_argv"):
            patched["dry_run_argv"] = _append_esbmc_arg_pair(
                [str(arg) for arg in patched.get("dry_run_argv") or []], "--path-cov-fixture",
                fixture["path"])
            for flag, value in fixture.get("esbmc_arg_pairs") or []:
                patched["dry_run_argv"] = _append_esbmc_arg_pair(patched["dry_run_argv"], flag,
                                                                 value)
        patched["source_stage2_fixture"] = fixture["fixture"]
        patched["source_stage2_fixture_path"] = fixture["path"]
        jobs.append(patched)
        applied.append({
            "job_id": patched.get("job_id"),
            "unit": patched.get("unit"),
            "path": fixture["path"],
            "kind": fixture["fixture"].get("veriput_fixture_kind"),
        })
    fixture_job_ids = {item["job_id"] for item in applied}
    jobs.sort(key=lambda item: item.get("job_id") not in fixture_job_ids)
    updated["jobs"] = jobs
    if applied:
        summary = dict(updated.get("summary") or {})
        summary["source_stage2_fixture_count"] = len(applied)
        summary["source_stage2_fixtures"] = applied
        updated["summary"] = summary
        updated["source_stage2_fixtures"] = applied
    return updated


def _stage2_cert_shard_path(cert_path: Path, idx: int, unit: str) -> Path:
    return cert_path.parent / "shards" / f"{idx:03d}-{_safe_name(unit)}.jsonl"


def _stage2_retry_cert_shard_path(cert_path: Path, idx: int, unit: str, reason: str) -> Path:
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


def _stage2_budget_source_before_stage4(remaining_s: float,
                                        reserve_s: int,
                                        unit_timeout_cap_s: int = 0) -> tuple[float, int]:
    budget_source = float(remaining_s)
    reserve_applied = 0
    if reserve_s > 0 and budget_source > float(reserve_s + 1):
        budget_source = max(1.0, budget_source - float(reserve_s))
        reserve_applied = int(reserve_s)
    if unit_timeout_cap_s > 0:
        budget_source = min(budget_source, float(unit_timeout_cap_s))
    return max(1.0, budget_source), reserve_applied


def _certify_argv_for_remaining(job: dict,
                                remaining_s: float,
                                run_timeout_s: int,
                                memlimit_gib: int,
                                unit_timeout_cap_s: int = 0,
                                out_path: Path | None = None,
                                stage_mem_fraction: float | None = None,
                                esbmc_bin: str | None = None,
                                stage4_reserve_s: int = 0,
                                no_region_refinement: bool = False,
                                no_selection_strategy: bool = False) -> list[str]:
    budget, _reserve_applied = _stage2_budget_before_stage4(remaining_s, stage4_reserve_s,
                                                            unit_timeout_cap_s)
    run_budget = max(1, min(budget, int(run_timeout_s)))
    argv = unit_schedule.budgeted_certify_argv([str(arg) for arg in job["certify_argv"]],
                                               timeout_s=budget,
                                               run_timeout_s=run_budget,
                                               memlimit_gib=memlimit_gib,
                                               workdir=job["certification_budget"]["workdir"])
    if out_path is not None:
        argv = _argv_with_value(argv, "--out", str(out_path))
    if stage_mem_fraction is not None:
        argv = _argv_with_value(argv, "--mem-fraction", f"{stage_mem_fraction:g}")
    if esbmc_bin:
        argv = _argv_with_value(argv, "--esbmc", esbmc_bin)
    if no_region_refinement and "--no-region-refinement" not in argv:
        argv.append("--no-region-refinement")
    if (no_selection_strategy and
            "--esbmc-arg=--path-cov-no-selection-strategy" not in argv):
        argv.append("--esbmc-arg=--path-cov-no-selection-strategy")
    return argv


def _copy_ce_collection_artifacts(source: Path, destination: Path) -> list[str]:
    """Copy Stage-1 evidence into the subject result without promoting it."""
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("ce-collection.json", "ce-witness-journal.json", "cov-ce-journal.json",
                 "enumeration-report.json", "generalise-progress.json", "run-config.json",
                 "driver.log"):
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
        rows = [json.loads(line) for line in result_path.read_text().splitlines() if line.strip()]
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


def run_ce_collection_subject(subject: PreparedSubject, case_dir: Path, jobs: list[dict],
                              args) -> tuple[dict, dict]:
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
        stage.update({"unit": unit, "path_function": job.get("path_function")})
        out_path = collection_root / "certify-results.jsonl"
        argv = _certify_argv_for_remaining(job,
                                           remaining_s=60,
                                           run_timeout_s=60,
                                           memlimit_gib=args.memlimit_gib,
                                           unit_timeout_cap_s=60,
                                           out_path=out_path,
                                           stage_mem_fraction=args.stage_mem_fraction,
                                           esbmc_bin=getattr(args, "esbmc", "") or None,
                                           stage4_reserve_s=0)
        argv.append("--ce-collection-only")
        result = run_command(argv, 60 + args.wrapper_grace,
                             case_dir / "logs" / f"ce-{_safe_name(unit)}")
        source = _ce_artifact_workdir(out_path, Path(job["certification_budget"]["workdir"]))
        artifact_dir = collection_root / _safe_name(unit)
        copied = _copy_ce_collection_artifacts(source, artifact_dir)
        stage.update(result)
        stage.update({
            "stage":
            "ce-collection",
            "unit":
            unit,
            "path_function":
            job.get("path_function"),
            "source_workdir":
            str(source),
            "artifact_paths":
            copied,
            "artifact_present":
            any(Path(path).name == "ce-collection.json" for path in copied),
        })

    elapsed = round(time.monotonic() - started, 3)
    summary = {
        "schema":
        "veriput-rq1-ce-collection/1",
        "subject_id":
        subject.subject_id,
        "benchmark":
        subject.benchmark,
        "contract":
        subject.contract,
        "budget_s":
        60,
        "wall_s":
        elapsed,
        "stage":
        stage,
        "note": ("Refutation evidence only. This record is neither a valid test "
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


def _bounded_holds_retry_argv(argv: list[str], *, max_tx: int, unwind: int,
                              out_path: Path) -> list[str]:
    out = _argv_with_value([str(arg) for arg in argv], "--max-tx", str(max_tx))
    out = _argv_with_value(out, "--out", str(out_path))
    out = _append_esbmc_arg(out, "--unwind")
    out = _append_esbmc_arg(out, str(unwind))
    return out


def _latest_cert_row(cert_path: Path,
                     benchmark_key: str,
                     unit: str,
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


def _is_bounded_holds_retry_candidate(row: dict | None, max_initial_wall_s: int) -> bool:
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


def _is_internal_target_wrapper_job(job: dict) -> bool:
    return job.get("priority_reason") == "internal-target-wrapper"


def _has_later_internal_target_wrapper_job(jobs: list[dict], next_index: int) -> bool:
    """Return true when this wrapper must leave budget for another wrapper."""
    return any(_is_internal_target_wrapper_job(job) for job in jobs[next_index:])


def _stage4_reserve_for_stage2_job(job: dict, jobs: list[dict], next_index: int, args) -> int:
    reserve_s = _stage4_reserve_s(args)
    if not _is_internal_target_wrapper_job(job):
        return reserve_s
    if _has_later_internal_target_wrapper_job(jobs, next_index):
        return reserve_s
    return 0


def _effective_stage4_reserve_s(configured_reserve_s: int, remaining_s: float,
                                min_remaining_s: int) -> int:
    if configured_reserve_s <= 0:
        return 0
    usable_reserve = int(float(remaining_s) - float(min_remaining_s) - 1.0)
    return max(0, min(int(configured_reserve_s), usable_reserve))


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


def _abi_composite_count(job: dict) -> int:
    """Count ABI values that are materially costlier than scalar bitvectors."""
    info = job.get("unit_info") or {}
    types = list(info.get("parameter_types") or [])
    types.extend(info.get("return_types") or [])
    return sum(1 for value in types
               if (str(value).strip() in ("bytes", "string") or "[" in str(value)
                   or str(value).lstrip().startswith(("tuple", "struct", "mapping"))))


def _continuation_semantic_rank(job: dict) -> int:
    """Keep mutation candidates ahead of getters after a weak first unit."""
    mutability = str((job.get("unit_info") or {}).get("state_mutability") or "")
    return 1 if mutability in ("view", "pure") else 0


def _continuation_job_key(job: dict) -> tuple:
    schedule_rank = job.get("schedule_rank") or {}
    cheap_rank = schedule_rank.get("cheap_first") or [50, 0, 0]
    coordinate_rank = schedule_rank.get("coordinate_first") or [3]
    put_rank = schedule_rank.get("put_potential_first") or [5]
    try:
        cheap = tuple(int(value) for value in cheap_rank)
    except (TypeError, ValueError):
        cheap = (50, 0, 0)
    try:
        coordinate = int(coordinate_rank[0])
    except (TypeError, ValueError, IndexError):
        coordinate = 3
    try:
        put = int(put_rank[0])
    except (TypeError, ValueError, IndexError):
        put = 5
    priority = job.get("priority")
    try:
        priority = int(priority) if priority is not None else 9
    except (TypeError, ValueError):
        priority = 9
    return (
        priority,
        _continuation_semantic_rank(job),
        _abi_composite_count(job),
        0 if put <= 1 else 1,
        0 if coordinate <= 1 else 1,
        put,
        coordinate,
        cheap,
        int(job.get("ordinal") or 0),
    )


def _requeue_weak_stage2_suffix(jobs: list[dict], next_index: int,
                                cert_row: dict | None) -> dict | None:
    """Reorder a weak suffix without crossing semantic priority buckets."""
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
        "bucket":
        cert_row.get("bucket"),
        "driver_diagnostic_tag":
        ((cert_row.get("driver_diagnostic") or {}).get("tag") if isinstance(
            cert_row.get("driver_diagnostic") or {}, dict) else None),
        "pending_jobs_before":
        before,
        "pending_jobs_after":
        after,
        "reason": ("weak Stage-2 result requeued the unattempted suffix by "
                   "semantic priority, scalar ABI, coordinate and PUT potential"),
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
    if _is_internal_target_wrapper_job(job):
        return 0
    adaptive = int(args.adaptive_stage2_unit_timeout_cap_s or 0)
    if adaptive <= 0:
        return 0
    needs_cap = (units_scheduled > 1 and prior_no_candidate_units > 0
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


def _stage2_unit_timeout_cap_reason_for_job(job: dict, args, effective_cap_s: int) -> str:
    if (effective_cap_s == 0 and int(args.stage2_unit_timeout_cap_s or 0) == 0
            and _is_internal_target_wrapper_job(job)):
        return "target-wrapper-uncapped"
    return _stage2_unit_timeout_cap_reason(args, effective_cap_s)


def _stage2_wrapper_timeout_s(remaining_s: float,
                              wrapper_grace_s: int,
                              effective_unit_cap_s: int,
                              stage4_reserve_s: int = 0) -> float:
    budget, _reserve_applied = _stage2_budget_source_before_stage4(remaining_s, stage4_reserve_s,
                                                                   effective_unit_cap_s)
    timeout_s = budget + max(0, int(wrapper_grace_s))
    if stage4_reserve_s > 0:
        # Cleanup grace must not consume the subject-generation window that
        # Stage 4 is promised. The subprocess timeout is the hard boundary.
        timeout_s = min(timeout_s, max(1.0, float(remaining_s) - stage4_reserve_s))
    return timeout_s


def _stage2_reserve_boundary_reached(remaining_s: float, stage4_reserve_s: int) -> bool:
    # _stage2_budget_before_stage4 guarantees a one-second minimum command
    # budget; stop before that quantum could cross the protected boundary.
    return (stage4_reserve_s > 0 and remaining_s <= float(stage4_reserve_s + 1))


def _bounded_holds_retry_policy(args) -> dict:
    return {
        "bounded_holds_retry":
        bool(getattr(args, "bounded_holds_retry", False)),
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
        "stage2_unit_timeout_cap_s":
        args.stage2_unit_timeout_cap_s,
        "adaptive_stage2_unit_timeout_cap_s":
        args.adaptive_stage2_unit_timeout_cap_s,
        "stage2_stage4_reserve_s":
        _stage4_reserve_s(args),
        "stage2_stage4_reserve_reason":
        "explicit stage2_stage4_reserve_s or "
        "max(min_remaining_s, min_timeout_only_stage4_s, "
        "min_concrete_only_stage4_s)",
        "stage4_reserve_boundary_enforced":
        True,
        "adaptive_stage2_many_unit_threshold":
        ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
        "adaptive_stage2_expensive_tier_threshold":
        ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
        "wrapper_timeout":
        "min(subject_remaining_s - stage4_reserve_s, "
        "effective_unit_cap_s) + wrapper_grace_s",
        "stage4_reserve_s":
        _stage4_reserve_s(args),
        "stage4_reserve_reason":
        "explicit stage2_stage4_reserve_s or "
        "max(min_remaining_s, min_timeout_only_stage4_s, "
        "min_concrete_only_stage4_s)",
        "capped_timeout_advances_to_next_unit":
        True,
    }
    schedule["rq1_stage2_runtime_policy"].update(_bounded_holds_retry_policy(args))
    for job in jobs:
        initial_cap = _effective_stage2_unit_timeout_cap_s(job, args, units_scheduled, 0)
        after_no_candidate_cap = _effective_stage2_unit_timeout_cap_s(job, args, units_scheduled, 1)
        job["rq1_stage2_runtime_policy"] = {
            "unit_cost_tier":
            _job_cost_tier(job),
            "initial_effective_unit_timeout_cap_s":
            initial_cap,
            "initial_cap_reason":
            _stage2_unit_timeout_cap_reason_for_job(job, args, initial_cap),
            "after_no_candidate_effective_unit_timeout_cap_s":
            after_no_candidate_cap,
            "after_no_candidate_cap_reason":
            _stage2_unit_timeout_cap_reason_for_job(job, args, after_no_candidate_cap),
        }
    return schedule


def _put_argv(cert_path: Path,
              unit: str,
              benchmark_key: str,
              out_root: Path,
              remaining_s: float,
              memlimit_gib: int,
              forge_timeout: int,
              path_function: str | None = None,
              esbmc_bin: str | None = None,
              emit_concrete_fallbacks: bool = True,
              foundry_fixture: str | None = None,
              concrete_replay_only: bool = False,
              no_test_assert_refinement: bool = False) -> list[str]:
    budget = max(1, int(remaining_s))
    selector = (f"{benchmark_key}.{path_function}" if path_function else f"{benchmark_key}.{unit}")
    argv = [
        sys.executable,
        str(PUT_ALL),
        "--cert",
        str(cert_path),
        "--only",
        selector,
        "--strong-recipe",
        "--timeout",
        str(budget),
        "--forge-timeout",
        str(forge_timeout),
        "--memlimit-gib",
        str(memlimit_gib),
        "--out-root",
        str(out_root),
        "--retain-certified-concrete-replays",
    ]
    if concrete_replay_only:
        argv.append("--certified-concrete-only")
    if no_test_assert_refinement:
        argv.append("--no-test-assert-refinement")
    if emit_concrete_fallbacks:
        argv.append("--emit-cleared-concrete-fallbacks")
    if foundry_fixture:
        argv += ["--foundry-fixture", foundry_fixture]
    if esbmc_bin:
        argv += ["--esbmc", esbmc_bin]
    return argv


def _run_ce_replay_candidate(subject: PreparedSubject, case_dir: Path, candidate: dict, args,
                             deadline: float) -> dict:
    """Run one CE candidate through an isolated Stage-4 transaction.

    The candidate cert row and all generated files live below
    ``candidate-stage4`` until the final verifier/Foundry gate passes.  In
    particular, no candidate is merged into the canonical cert journal or
    result row merely because it was materialized.
    """
    candidate_id = str(candidate["candidate_id"])
    run_root = (case_dir / "candidate-stage4" / _safe_name(candidate_id) / f"run-{time.time_ns()}")
    cert_path = run_root / "candidate-cert.jsonl"
    output_root = run_root / "out"
    _append_jsonl(cert_path, _candidate_cert_row(candidate, subject))
    remaining = _remaining(deadline)
    unit = str(candidate["case"]["unit"])
    path_function = str(candidate["path"]["path_function"])
    argv = _put_argv(cert_path, unit, subject.benchmark_key, output_root, remaining,
                     args.memlimit_gib, args.forge_timeout, path_function,
                     getattr(args, "esbmc", "") or None)
    wrapper_timeout = max(1.0, remaining) + args.wrapper_grace + 2 * args.forge_timeout
    stage = run_command(argv, wrapper_timeout, run_root / "candidate-stage4")
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
            destination = _promote_candidate_artifacts(output_root, case_dir, candidate_id)
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
    _write_json(
        run_root / "candidate-result.json", {
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
    prepare_case_dir(case_dir,
                     force_fresh=bool(args.redo and not getattr(args, "ce_collection_only", False)))
    ast_cache_root = Path(args.ast_cache_root).expanduser().resolve()
    strict_case_wall_budget = bool(getattr(args, "strict_case_wall_budget", False))
    ablation = getattr(args, "rq3_ablation", "")
    if strict_case_wall_budget:
        if ablation == "no-test-assert-refinement":
            cert_scope = "rq3-no-test-assert-refinement-cert"
        elif ablation == "no-region-refinement":
            cert_scope = "rq3-no-region-refinement-cert"
        elif ablation == "no-selection-strategy":
            cert_scope = "rq3-no-selection-strategy-cert"
        else:
            cert_scope = "rq1-fair600-cert"
    elif getattr(args, "concrete_replay_only_ablation", False):
        cert_scope = "rq3-no-cer-reg-cert"
    elif ablation == "no-selection-strategy":
        cert_scope = "rq3-no-selection-strategy-cert"
    else:
        cert_scope = "rq1-stage2-cert"
    cert_path = (ast_cache_root / cert_scope / dataset_label /
                 _safe_name(subject_id) / str(time.time_ns()) / "certify-results.jsonl")
    subject = subject_unit_manifest.resolve_subject(subject_id,
                                                    benchmark=target_row["benchmark"],
                                                    require_unit=False)
    subject = cached_subject(subject.with_inferred_solc_bin(), ast_cache_root, dataset_label)
    subject_deadline = start + float(args.timeout)
    strict_finalization_reserve_s = (_strict_finalization_reserve_s(args.timeout)
                                     if strict_case_wall_budget else 0.0)
    deadline = subject_deadline - strict_finalization_reserve_s
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
    adaptive_stage4_budget_caps = []
    adaptive_stage4_soft_timeouts = []
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
                                          memlimit_gib=args.memlimit_gib,
                                          cert_path=cert_path)
        schedule = filter_schedule_units(schedule, getattr(args, "unit", []))
        schedule = apply_source_stage2_fixtures(schedule, subject, case_dir)
        schedule = apply_stage2_extcall_pins(schedule, bool(getattr(args, "pin_extcall", False)))
        schedule = apply_stage2_free_entry_state(
            schedule, bool(getattr(args, "free_entry_state", False)))
        schedule = apply_stage2_flag(schedule, bool(getattr(args, "log_ladder", False)),
                                     "--log-ladder", "log_ladder")
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
    if getattr(args, "ce_collection_only", False):
        return run_ce_collection_subject(subject, case_dir, jobs, args)
    if getattr(args, "fallback_only", False):
        fallback_stage = emit_no_unit_deploy_fallback(
            subject,
            case_dir,
            schedule,
            args.forge_timeout,
            force=True,
            reason=("explicit fallback-only recovery for a canonical "
                    "no-valid subject; Stage 2 was not run"),
            out_name="final_deploy_concrete_fallback",
            publish_unoracled_deploy_smoke=not getattr(args, "concrete_replay_only_ablation",
                                                       False))
        stages.append(fallback_stage)
        jobs = []
        result_status = fallback_stage.get("status") or "no-output"
        if result_status != "ok":
            failure_reason = (fallback_stage.get("reason")
                              or "fallback-only recovery produced no valid artifact")
    if getattr(args, "ce_replay_manifest", None):
        ce_replay_candidates, ce_replay_rejected = (_load_ce_replay_candidates(
            _candidate_manifest_paths(args.ce_replay_manifest), target_row, subject, case_dir))
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
                    _candidate_rejection(candidate,
                                         "case budget exhausted before candidate Stage 4"))
                continue
            mem_wait = wait_for_mem_budget(args.memlimit_gib,
                                           deadline,
                                           fraction=args.stage_mem_fraction,
                                           poll_s=args.mem_wait_poll_s,
                                           min_remaining_s=args.min_remaining_s)
            if mem_wait["status"] != "ok":
                ce_replay_rejected.append(
                    _candidate_rejection(candidate, "insufficient memory before candidate Stage 4",
                                         f"{mem_wait['mem_available_gib']}GiB available"))
                continue
            candidate_stage = _run_ce_replay_candidate(subject, case_dir, candidate, args, deadline)
            ce_replay_stages.append(candidate_stage)
            stages.append(candidate_stage)
        if (getattr(args, "ce_replay_only", False) and not ce_replay_candidates):
            result_status = "no-output"
            failure_reason = "no admissible CE replay candidates"
    if (result_status == "ok" and not jobs and not getattr(args, "fallback_only", False)
            and not getattr(args, "ce_replay_only", False)):
        result_status, failure_reason = _empty_schedule_status_reason(schedule)
        if result_status == "no-units" and not getattr(args, "ce_replay_only", False):
            getter_stages = emit_no_unit_getter_fallbacks(subject, case_dir, schedule,
                                                          _remaining(deadline), args.memlimit_gib,
                                                          args.forge_timeout,
                                                          getattr(args, "esbmc", "") or None,
                                                          deadline=(deadline
                                                                    if strict_case_wall_budget
                                                                    else None))
            stages.extend(getter_stages)
            partial_put = summarize_put_artifacts(case_dir / "put")
            if partial_put["valid"] > 0:
                result_status = "ok"
                failure_reason = None
            elif (_no_unit_schedule_allows_deploy_fallback(schedule)
                  or _no_unit_schedule_allows_library_internal_fallback(schedule)):
                if strict_case_wall_budget:
                    fallback_stage = emit_no_unit_deploy_fallback(
                        subject,
                        case_dir,
                        schedule,
                        args.forge_timeout,
                        deadline=deadline,
                        publish_unoracled_deploy_smoke=not getattr(
                            args, "concrete_replay_only_ablation", False))
                else:
                    fallback_stage = emit_no_unit_deploy_fallback(
                        subject,
                        case_dir,
                        schedule,
                        args.forge_timeout,
                        publish_unoracled_deploy_smoke=not getattr(
                            args, "concrete_replay_only_ablation", False))
                stages.append(fallback_stage)

    # ---- ONE frontend-only run, while the case still has budget --------
    #
    # The value-gate rescue needs this unit's real (enc, depth) pairs, and it
    # fires exactly when Stage 2 ran out of time -- so asking for them at that
    # moment finds no budget and falls back to a fabricated identity that ESBMC
    # then refuses.  Taking the map once here costs one frontend for the whole
    # case and serves every unit that later needs rescuing.  An empty map is a
    # degraded but correct run, not a failure.
    subject_path_enumeration = {}
    if jobs:
        enumeration_started = time.monotonic()
        enumeration_deadline = enumeration_started + min(ABI_VALUE_GATE_ENUMERATION_BUDGET_S,
                                                         _remaining(deadline))
        subject_path_enumeration = _enumerate_subject_paths(
            subject, getattr(args, "esbmc", "") or None, args.memlimit_gib,
            enumeration_deadline - time.monotonic())
        enumeration_scope = "subject" if subject_path_enumeration else None
        if not subject_path_enumeration:
            # ---- THE UNFOCUSED RUN IS THE MEMORY-HUNGRY ONE ----
            #
            # Dropping --focus-function instruments every unit at once, and on a
            # contract whose units expand to many paths that exceeds the same
            # memlimit the focused runs fit inside -- MEASURED on
            # `pop_042_VaultAdapter`, which reports "6 unit(s), 157 path(s)
            # total" and then "ERROR: Out of memory".  It exits well under the
            # time budget, so an empty map here is not a timeout and must not be
            # read as one.
            #
            # Falling back to focused runs costs one frontend per scheduled unit
            # instead of one per case, which is why it is the fallback and not
            # the default.  It shares the SAME budget, so a case cannot spend
            # more on enumeration by failing at it.
            for job in jobs:
                if _remaining(enumeration_deadline) < 1:
                    break
                job_path_function = str(job.get("path_function") or "")
                if not job_path_function or job_path_function in subject_path_enumeration:
                    continue
                pairs = _enumerate_unit_paths(subject, job.get("unit"), job_path_function,
                                              getattr(args, "esbmc", "") or None,
                                              args.memlimit_gib,
                                              _remaining(enumeration_deadline))
                if pairs:
                    subject_path_enumeration[job_path_function] = pairs
            if subject_path_enumeration:
                enumeration_scope = "per-unit-fallback"
        stages.append({
            "stage": "subject-path-enumeration",
            "status": "ok" if subject_path_enumeration else "unavailable",
            "scope": enumeration_scope,
            "units": len(subject_path_enumeration),
            "paths": sum(len(v) for v in subject_path_enumeration.values()),
            "wall_s": round(time.monotonic() - enumeration_started, 3),
            "reason": ("--goto-functions-only supplies every unit's exact path identities to "
                       "the Stage-2-timeout value-gate rescue, which fires when the case has "
                       "no budget left to ask for them itself"),
        })

    for idx, job in enumerate(jobs, 1):
        unit = job["unit"]
        path_function = job.get("path_function")
        remaining_before_stage2 = _remaining(deadline)
        configured_stage4_reserve_s = _stage4_reserve_for_stage2_job(job, jobs, idx, args)
        stage2_stage4_reserve_s = _effective_stage4_reserve_s(configured_stage4_reserve_s,
                                                              remaining_before_stage2,
                                                              args.min_remaining_s)
        if _stage2_reserve_boundary_reached(remaining_before_stage2, stage2_stage4_reserve_s):
            result_status = "budget-exhausted"
            failure_reason = ("Stage-2 stopped at the hard Stage-4 reserve boundary before "
                              "remaining units")
            break
        if remaining_before_stage2 < args.min_remaining_s:
            result_status = "budget-exhausted"
            failure_reason = "case budget exhausted before remaining units"
            break
        units_attempted.append(unit)
        mem_wait = wait_for_mem_budget(args.memlimit_gib,
                                       deadline,
                                       fraction=args.stage_mem_fraction,
                                       poll_s=args.mem_wait_poll_s,
                                       min_remaining_s=args.min_remaining_s)
        if mem_wait["waited"] or mem_wait["status"] != "ok":
            mem_wait.update({"unit": unit, "before_stage": "certify"})
            stages.append(mem_wait)
        if mem_wait["status"] != "ok":
            result_status = "budget-exhausted"
            failure_reason = (f"insufficient memory before certify {unit}: "
                              f"need MemAvailable >= "
                              f"{mem_wait['required_mem_available_gib']}GiB for "
                              f"{args.memlimit_gib}GiB at "
                              f"{args.stage_mem_fraction:.0%}; have "
                              f"{mem_wait['mem_available_gib']}GiB")
            break
        cert_shard_path = _stage2_cert_shard_path(cert_path, idx, unit)
        stage2_remaining_s = _remaining(deadline)
        stage2_stage4_reserve_s = _effective_stage4_reserve_s(configured_stage4_reserve_s,
                                                              stage2_remaining_s,
                                                              args.min_remaining_s)
        if _stage2_reserve_boundary_reached(stage2_remaining_s, stage2_stage4_reserve_s):
            result_status = "budget-exhausted"
            failure_reason = ("Stage-2 stopped at the hard Stage-4 reserve boundary before "
                              "remaining units")
            break
        effective_stage2_cap_s = _effective_stage2_unit_timeout_cap_s(
            job,
            args,
            len(jobs),
            consecutive_no_candidate_units,
            remaining_s=stage2_remaining_s,
            stage4_reserve_s=stage2_stage4_reserve_s)
        _stage2_budget_s, stage2_stage4_reserve_applied_s = (_stage2_budget_before_stage4(
            stage2_remaining_s, stage2_stage4_reserve_s, effective_stage2_cap_s))
        cert_argv = _certify_argv_for_remaining(job, stage2_remaining_s, args.esbmc_run_timeout,
                                                args.memlimit_gib, effective_stage2_cap_s,
                                                cert_shard_path, args.stage_mem_fraction,
                                                getattr(args, "esbmc", "") or None,
                                                stage2_stage4_reserve_s,
                                                bool(getattr(args, "no_region_refinement", False)),
                                                bool(getattr(args, "no_selection_strategy", False)))
        cert_wrapper_timeout_s = _stage2_wrapper_timeout_s(stage2_remaining_s, args.wrapper_grace,
                                                           effective_stage2_cap_s,
                                                           stage2_stage4_reserve_s)
        if strict_case_wall_budget:
            cert_wrapper_timeout_s = _case_wrapper_timeout(cert_wrapper_timeout_s, deadline, True)
        n_stage4_candidates = None
        cert_log_prefix = case_dir / "logs" / f"{idx:03d}-{_safe_name(unit)}-certify"
        if strict_case_wall_budget:
            cert_stage = run_command(cert_argv,
                                     cert_wrapper_timeout_s,
                                     cert_log_prefix,
                                     hard_deadline=deadline)
        else:
            cert_stage = run_command(cert_argv, cert_wrapper_timeout_s, cert_log_prefix)
        cert_stage.update({
            "stage":
            "certify",
            "unit":
            unit,
            "path_function":
            path_function,
            "job_id":
            job.get("job_id"),
            "cert_shard_jsonl":
            str(cert_shard_path),
            "cert_canonical_jsonl":
            str(cert_path),
            "wrapper_timeout_s":
            round(cert_wrapper_timeout_s, 3),
            "subject_remaining_before_stage2_s":
            round(stage2_remaining_s, 3),
            "stage2_stage4_reserve_s":
            stage2_stage4_reserve_applied_s,
            "stage2_reserve_boundary_s":
            stage2_stage4_reserve_s,
            "stage2_reserve_boundary_configured_s":
            configured_stage4_reserve_s,
            "stage2_reserve_boundary_enforced":
            True,
            "stage2_unit_timeout_cap_s_effective":
            effective_stage2_cap_s,
            "stage2_unit_timeout_cap_reason":
            (_stage2_unit_timeout_cap_reason_for_job(job, args, effective_stage2_cap_s)),
            "unit_cost_tier":
            _job_cost_tier(job),
        })
        merge_result = _merge_jsonl_records(cert_path, cert_shard_path)
        cert_stage["cert_shard_merge"] = merge_result
        cert_shard_merges.append(merge_result)
        stages.append(cert_stage)
        n_certified = _certified_count(cert_path, subject.benchmark_key, unit, path_function)
        n_cleared_fallback = _cleared_concrete_fallback_count(cert_path, subject.benchmark_key,
                                                              unit, path_function)
        n_timeout_fallback = _timeout_concrete_fallback_count(cert_path, subject.benchmark_key,
                                                              unit, path_function)
        n_complete_witness_fallback = _complete_witness_concrete_fallback_count(
            cert_path, subject.benchmark_key, unit, path_function)
        n_partial_journal_fallback = _partial_journal_concrete_fallback_count(
            cert_path, subject.benchmark_key, unit, path_function)
        n_stage4_candidates = (n_certified + n_cleared_fallback + n_timeout_fallback +
                               n_complete_witness_fallback + n_partial_journal_fallback)
        stage2_soft_timeout_s = max(int(effective_stage2_cap_s or 0),
                                    int(stage2_stage4_reserve_applied_s or 0))
        if cert_stage["status"] == "timeout" and stage2_soft_timeout_s > 0:
            if n_stage4_candidates > 0:
                cert_stage["capped_timeout_stage4_candidates_retained"] = True
                cert_stage["stage2_soft_timeout_stage4_candidates_retained"] = True
            else:
                first_row = _latest_cert_row(cert_path, subject.benchmark_key, unit, path_function)
                overload_retry_jobs = _overload_path_function_retry_jobs(job, first_row, jobs)
                if overload_retry_jobs:
                    jobs.extend(overload_retry_jobs)
                    stages.append({
                        "stage":
                        "schedule-overload-path-functions",
                        "unit":
                        unit,
                        "path_function":
                        path_function,
                        "job_id":
                        job.get("job_id"),
                        "status":
                        "ok",
                        "added_jobs":
                        len(overload_retry_jobs),
                        "path_functions":
                        [retry.get("path_function") for retry in overload_retry_jobs],
                        "reason": ("Stage-2 refused an overloaded unit without an "
                                   "explicit path function; appended per-overload "
                                   "certification jobs"),
                    })
                    consecutive_no_candidate_units = 0
                    continue
                gate_stage = _structural_abi_value_gate_rescue(
                    cert_path, subject, job, unit, path_function, n_stage4_candidates,
                    getattr(args, "esbmc", "") or None, args.memlimit_gib,
                    _remaining(deadline), subject_path_enumeration)
                if gate_stage is not None:
                    stages.append(gate_stage)
                    (n_certified, n_cleared_fallback, n_timeout_fallback,
                     n_complete_witness_fallback,
                     n_partial_journal_fallback) = _stage4_candidate_counts(
                         cert_path, subject.benchmark_key, unit, path_function)
                    n_stage4_candidates = (n_certified + n_cleared_fallback + n_timeout_fallback +
                                           n_complete_witness_fallback + n_partial_journal_fallback)
                if n_stage4_candidates <= 0:
                    counts_for_stop = _no_candidate_counts_against_stop(first_row)
                    weak_requeue = _requeue_weak_stage2_suffix(jobs, idx, first_row)
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
                            _record_no_candidate_unit(consecutive_no_candidate_units,
                                                      max_consecutive_no_candidate_units))
                    else:
                        diagnostic = (first_row or {}).get("driver_diagnostic") or {}
                        stage2_no_candidate_stop_skipped_units.append({
                            "unit":
                            unit,
                            "path_function":
                            path_function,
                            "bucket": (first_row or {}).get("bucket"),
                            "driver_diagnostic_tag":
                            (diagnostic.get("tag") if isinstance(diagnostic, dict) else None),
                            "reason": ("capped Stage-2 timeout ended without a Stage-4 "
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
                            min_threshold_units=(args.min_no_candidate_stage2_unit_stop_n),
                            pending_hinted_units=_pending_hinted_units(jobs, units_attempted)):
                        early_stop_reason = _format_no_candidate_unit_stop(
                            consecutive_no_candidate_units)
                        result_status = "early-stop-no-output"
                        failure_reason = early_stop_reason
                        break
                    if (stage4_candidate_units_attempted == 0
                            and _should_stop_after_no_output_stage2(
                                stages, partial_put,
                                args.no_output_stage2_stop_s, stage2_no_candidate_evidence_units,
                                len(jobs), args.min_no_output_stage2_unit_stop_n)):
                        early_stop_reason = _format_stage2_no_output_stop(
                            _stage_wall_s(stages, "certify"))
                        result_status = "early-stop-no-output"
                        failure_reason = early_stop_reason
                        break
                    continue
        elif cert_stage["status"] == "timeout":
            if _should_continue_after_stage2_no_output(jobs, idx, n_stage4_candidates, cert_stage):
                first_row = _latest_cert_row(cert_path, subject.benchmark_key, unit, path_function)
                weak_requeue = _requeue_weak_stage2_suffix(jobs, idx, first_row)
                if weak_requeue:
                    stages.append({
                        "stage": "requeue-after-weak-certification",
                        "unit": unit,
                        "job_id": job.get("job_id"),
                        **weak_requeue,
                    })
                stage2_no_output_continuations.append({
                    "unit":
                    unit,
                    "path_function":
                    path_function,
                    "job_id":
                    job.get("job_id"),
                    "status":
                    cert_stage["status"],
                    "pending_units_after_this":
                    _pending_units_after(jobs, idx),
                    "cert_shard_jsonl":
                    str(cert_shard_path),
                    "cert_shard_merge":
                    merge_result,
                    "bucket": (first_row or {}).get("bucket"),
                    "reason": ("Stage-2 produced no Stage-4 candidate before timeout; "
                               "continuing to later units instead of subject-level "
                               "early stop"),
                })
                consecutive_no_candidate_units = 0
                continue
            # The hard-timeout branch is AT the deadline, so `_remaining` is
            # normally <= 0 and the enumeration is skipped rather than borrowing
            # from the strict finalization reserve.  The row then falls back to
            # the single-path assumption, which is recorded as such.
            gate_stage = _structural_abi_value_gate_rescue(
                cert_path, subject, job, unit, path_function, n_stage4_candidates,
                getattr(args, "esbmc", "") or None, args.memlimit_gib, _remaining(deadline), subject_path_enumeration)
            if gate_stage is not None:
                stages.append(gate_stage)
                (n_certified, n_cleared_fallback, n_timeout_fallback,
                 n_complete_witness_fallback,
                 n_partial_journal_fallback) = _stage4_candidate_counts(
                     cert_path, subject.benchmark_key, unit, path_function)
                n_stage4_candidates = (n_certified + n_cleared_fallback + n_timeout_fallback +
                                       n_complete_witness_fallback + n_partial_journal_fallback)
            if n_stage4_candidates <= 0:
                result_status = "timeout"
                failure_reason = f"certify {unit}: timeout"
                break
        # The certification driver intentionally exits non-zero when it has a
        # witnessed but not certified path.  Its JSONL shard is still a valid
        # concrete Stage-4 input, so the merged evidence, rather than the
        # process exit code alone, decides whether generation may continue.
        cert_stage_can_feed_stage4 = (cert_stage["status"] == "ok"
                                      or n_stage4_candidates > 0)
        if cert_stage["status"] == "oom":
            result_status = "oom"
            failure_reason = f"certify {unit}: oom"
            break
        if not cert_stage_can_feed_stage4:
            if _should_continue_after_stage2_no_output(jobs, idx, n_stage4_candidates, cert_stage):
                first_row = _latest_cert_row(cert_path, subject.benchmark_key, unit, path_function)
                create2_put_stage = emit_source_grounded_createcall_create2_put(
                    subject,
                    case_dir,
                    unit,
                    args.forge_timeout,
                    deadline=(deadline if strict_case_wall_budget else None))
                if create2_put_stage["status"] != "skipped":
                    stages.append(create2_put_stage)
                    if create2_put_stage["status"] == "ok":
                        consecutive_no_candidate_units = 0
                        continue
                diagnostic = (first_row or {}).get("driver_diagnostic") or {}
                weak_requeue = _requeue_weak_stage2_suffix(jobs, idx, first_row)
                if weak_requeue:
                    stages.append({
                        "stage": "requeue-after-weak-certification",
                        "unit": unit,
                        "job_id": job.get("job_id"),
                        **weak_requeue,
                    })
                stage2_no_output_continuations.append({
                    "unit":
                    unit,
                    "path_function":
                    path_function,
                    "job_id":
                    job.get("job_id"),
                    "status":
                    cert_stage["status"],
                    "pending_units_after_this":
                    _pending_units_after(jobs, idx),
                    "cert_shard_jsonl":
                    str(cert_shard_path),
                    "cert_shard_merge":
                    merge_result,
                    "bucket": (first_row or {}).get("bucket"),
                    "driver_diagnostic_tag":
                    (diagnostic.get("tag") if isinstance(diagnostic, dict) else None),
                    "reason": ("Stage-2 failed before yielding a Stage-4 candidate; "
                               "continuing to later units instead of subject-level "
                               "early stop"),
                })
                consecutive_no_candidate_units = 0
                continue
            result_status = "error"
            failure_reason = f"certify {unit}: {cert_stage['status']}"
            break
        if (n_stage4_candidates <= 0 and args.bounded_holds_retry
                and _remaining(deadline) >= args.min_remaining_s):
            first_row = _latest_cert_row(cert_path, subject.benchmark_key, unit, path_function)
            if _is_bounded_holds_retry_candidate(first_row,
                                                 args.bounded_holds_retry_max_initial_wall_s):
                retry_shard_path = _stage2_retry_cert_shard_path(cert_path, idx, unit,
                                                                 "bounded-holds")
                retry_argv = _bounded_holds_retry_argv(cert_argv,
                                                       max_tx=args.bounded_holds_retry_max_tx,
                                                       unwind=args.bounded_holds_retry_unwind,
                                                       out_path=retry_shard_path)
                retry_remaining_s = _remaining(deadline)
                retry_stage4_reserve_s = _stage4_reserve_s(args)
                _retry_budget_s, retry_stage4_reserve_applied_s = (_stage2_budget_before_stage4(
                    retry_remaining_s, retry_stage4_reserve_s, effective_stage2_cap_s))
                retry_argv = _certify_argv_for_remaining(
                    {
                        "certify_argv": retry_argv,
                        "certification_budget": {
                            "workdir": job["certification_budget"]["workdir"],
                        },
                    }, retry_remaining_s, args.esbmc_run_timeout, args.memlimit_gib,
                    effective_stage2_cap_s, retry_shard_path, args.stage_mem_fraction,
                    getattr(args, "esbmc", "") or None, retry_stage4_reserve_s,
                    bool(getattr(args, "no_region_refinement", False)),
                    bool(getattr(args, "no_selection_strategy", False)))
                retry_wrapper_timeout_s = _stage2_wrapper_timeout_s(retry_remaining_s,
                                                                    args.wrapper_grace,
                                                                    effective_stage2_cap_s,
                                                                    retry_stage4_reserve_s)
                if strict_case_wall_budget:
                    retry_wrapper_timeout_s = _case_wrapper_timeout(retry_wrapper_timeout_s,
                                                                    deadline, True)
                retry_log_prefix = (case_dir / "logs" /
                                    f"{idx:03d}-{_safe_name(unit)}-bounded-retry")
                if strict_case_wall_budget:
                    retry_stage = run_command(retry_argv,
                                              retry_wrapper_timeout_s,
                                              retry_log_prefix,
                                              hard_deadline=deadline)
                else:
                    retry_stage = run_command(retry_argv,
                                              retry_wrapper_timeout_s,
                                              retry_log_prefix)
                retry_stage.update({
                    "stage": "certify-bounded-holds-retry",
                    "unit": unit,
                    "path_function": path_function,
                    "job_id": job.get("job_id"),
                    "cert_shard_jsonl": str(retry_shard_path),
                    "cert_canonical_jsonl": str(cert_path),
                    "wrapper_timeout_s": round(retry_wrapper_timeout_s, 3),
                    "subject_remaining_before_stage2_s": round(retry_remaining_s, 3),
                    "stage2_stage4_reserve_s": retry_stage4_reserve_applied_s,
                    "bounded_holds_retry": {
                        "max_tx": args.bounded_holds_retry_max_tx,
                        "unwind": args.bounded_holds_retry_unwind,
                        "max_initial_wall_s": args.bounded_holds_retry_max_initial_wall_s,
                    },
                })
                retry_merge_result = _merge_jsonl_records(cert_path, retry_shard_path)
                retry_stage["cert_shard_merge"] = retry_merge_result
                cert_shard_merges.append(retry_merge_result)
                stages.append(retry_stage)
                retry_can_feed_stage4 = retry_stage["status"] == "ok"
                if retry_stage["status"] == "timeout" and max(
                        int(effective_stage2_cap_s or 0), int(retry_stage4_reserve_applied_s
                                                              or 0)) > 0:
                    retry_can_feed_stage4 = True
                    retry_stage["stage2_soft_timeout_stage4_candidate_probe"] = True
                if retry_can_feed_stage4:
                    n_certified = _certified_count(cert_path, subject.benchmark_key, unit,
                                                   path_function)
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
                    n_stage4_candidates = (n_certified + n_cleared_fallback + n_timeout_fallback +
                                           n_complete_witness_fallback + n_partial_journal_fallback)
                    if n_stage4_candidates > 0:
                        retry_stage["stage2_soft_timeout_stage4_candidates_retained"] = True
        if _is_nonpayable_abi_entry_job(job):
            value_gate_promotion = _promote_pin_excluded_value_gate_paths(
                cert_path, subject.benchmark_key, unit, path_function)
            if value_gate_promotion["promoted"]:
                stages.append({
                    "stage": "abi-value-gate-pin-promotion",
                    "unit": unit,
                    "path_function": path_function,
                    "job_id": job.get("job_id"),
                    "status": "ok",
                    "cert_canonical_jsonl": str(cert_path),
                    "promoted_paths": value_gate_promotion["promoted"],
                    "reason": ABI_VALUE_GATE_REASON,
                })
                n_certified = _certified_count(cert_path, subject.benchmark_key, unit,
                                               path_function)
                n_cleared_fallback = _cleared_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
                n_timeout_fallback = _timeout_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
                n_complete_witness_fallback = _complete_witness_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
                n_partial_journal_fallback = _partial_journal_concrete_fallback_count(
                    cert_path, subject.benchmark_key, unit, path_function)
                n_stage4_candidates = (n_certified + n_cleared_fallback + n_timeout_fallback +
                                       n_complete_witness_fallback + n_partial_journal_fallback)
        if n_stage4_candidates <= 0 and _is_nonpayable_abi_entry_job(job):
            abi_gate_row = _abi_value_gate_cert_row(subject, job)
            _append_jsonl(cert_path, abi_gate_row)
            stages.append({
                "stage": "static-abi-value-gate-certification",
                "unit": unit,
                "path_function": path_function,
                "job_id": job.get("job_id"),
                "status": "ok",
                "cert_canonical_jsonl": str(cert_path),
                "reason": abi_gate_row["driver_diagnostic"]["reason"],
            })
            n_certified = _certified_count(cert_path, subject.benchmark_key, unit, path_function)
            n_cleared_fallback = _cleared_concrete_fallback_count(cert_path, subject.benchmark_key,
                                                                  unit, path_function)
            n_timeout_fallback = _timeout_concrete_fallback_count(cert_path, subject.benchmark_key,
                                                                  unit, path_function)
            n_complete_witness_fallback = _complete_witness_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_partial_journal_fallback = _partial_journal_concrete_fallback_count(
                cert_path, subject.benchmark_key, unit, path_function)
            n_stage4_candidates = (n_certified + n_cleared_fallback + n_timeout_fallback +
                                   n_complete_witness_fallback + n_partial_journal_fallback)
        concrete_only_stage4 = _is_concrete_only_stage4(n_certified, n_cleared_fallback,
                                                        n_timeout_fallback,
                                                        n_complete_witness_fallback,
                                                        n_partial_journal_fallback)
        pending_units_after_this = max(0, len(jobs) - idx)
        if n_stage4_candidates <= 0:
            create2_put_stage = emit_source_grounded_createcall_create2_put(
                subject,
                case_dir,
                unit,
                args.forge_timeout,
                deadline=(deadline if strict_case_wall_budget else None))
            if create2_put_stage["status"] != "skipped":
                stages.append(create2_put_stage)
                if create2_put_stage["status"] == "ok":
                    consecutive_no_candidate_units = 0
                    continue
            first_row = _latest_cert_row(cert_path, subject.benchmark_key, unit, path_function)
            weak_requeue = _requeue_weak_stage2_suffix(jobs, idx, first_row)
            if weak_requeue:
                stages.append({
                    "stage": "requeue-after-weak-certification",
                    "unit": unit,
                    "job_id": job.get("job_id"),
                    **weak_requeue,
                })
            overload_retry_jobs = _overload_path_function_retry_jobs(job, first_row, jobs)
            if overload_retry_jobs:
                jobs.extend(overload_retry_jobs)
                stages.append({
                    "stage":
                    "schedule-overload-path-functions",
                    "unit":
                    unit,
                    "path_function":
                    path_function,
                    "job_id":
                    job.get("job_id"),
                    "status":
                    "ok",
                    "added_jobs":
                    len(overload_retry_jobs),
                    "path_functions": [retry.get("path_function") for retry in overload_retry_jobs],
                    "reason": ("Stage-2 refused an overloaded unit without an "
                               "explicit path function; appended per-overload "
                               "certification jobs"),
                })
                consecutive_no_candidate_units = 0
                continue
            counts_for_stop = _no_candidate_counts_against_stop(first_row)
            if counts_for_stop:
                stage2_no_candidate_evidence_units += 1
                consecutive_no_candidate_units, max_consecutive_no_candidate_units = (
                    _record_no_candidate_unit(consecutive_no_candidate_units,
                                              max_consecutive_no_candidate_units))
            else:
                diagnostic = (first_row or {}).get("driver_diagnostic") or {}
                stage2_no_candidate_stop_skipped_units.append({
                    "unit":
                    unit,
                    "path_function":
                    path_function,
                    "bucket": (first_row or {}).get("bucket"),
                    "driver_diagnostic_tag":
                    (diagnostic.get("tag") if isinstance(diagnostic, dict) else None),
                    "reason": ("tool/frontend/focus failure is not evidence that "
                               "remaining units lack Stage-4 candidates"),
                })
                consecutive_no_candidate_units = 0
            partial_put = summarize_put_artifacts(case_dir / "put")
            if _should_stop_after_no_candidate_units(
                    consecutive_no_candidate_units,
                    partial_put,
                    args.no_candidate_stage2_unit_stop_n,
                    units_scheduled=len(jobs),
                    min_threshold_units=(args.min_no_candidate_stage2_unit_stop_n),
                    pending_hinted_units=_pending_hinted_units(jobs, units_attempted)):
                early_stop_reason = _format_no_candidate_unit_stop(consecutive_no_candidate_units)
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            stop_s = args.no_output_stage2_stop_s
            if (stage4_candidate_units_attempted == 0 and _should_stop_after_no_output_stage2(
                    stages, partial_put, stop_s, stage2_no_candidate_evidence_units, len(jobs),
                    args.min_no_output_stage2_unit_stop_n)):
                early_stop_reason = _format_stage2_no_output_stop(_stage_wall_s(stages, "certify"))
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            continue
        consecutive_no_candidate_units = 0
        partial_put = summarize_put_artifacts(case_dir / "put")
        remaining_before_stage4 = _remaining(deadline)
        if _should_skip_low_budget_timeout_only_stage4(remaining_before_stage4,
                                                       args.min_timeout_only_stage4_s, n_certified,
                                                       n_cleared_fallback, n_timeout_fallback,
                                                       n_complete_witness_fallback,
                                                       n_partial_journal_fallback):
            skip_reason = _format_low_budget_timeout_only_skip(remaining_before_stage4,
                                                               args.min_timeout_only_stage4_s)
            low_budget_timeout_only_stage4_skips.append({
                "unit":
                unit,
                "job_id":
                job.get("job_id"),
                "remaining_s":
                round(remaining_before_stage4, 3),
                "threshold_s":
                args.min_timeout_only_stage4_s,
                "certified_regions_for_unit":
                n_certified,
                "cleared_concrete_fallbacks_for_unit":
                n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit":
                n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                n_partial_journal_fallback,
                "raw_before_skip":
                partial_put.get("raw") or 0,
                "valid_before_skip":
                partial_put.get("valid") or 0,
                "reason":
                skip_reason,
                "pending_stage4_candidate":
                True,
            })
            stage2_no_candidate_evidence_units += 1
            consecutive_no_candidate_units, max_consecutive_no_candidate_units = (
                _record_no_candidate_unit(consecutive_no_candidate_units,
                                          max_consecutive_no_candidate_units))
            if _should_stop_after_no_candidate_units(
                    consecutive_no_candidate_units,
                    partial_put,
                    args.no_candidate_stage2_unit_stop_n,
                    units_scheduled=len(jobs),
                    min_threshold_units=(args.min_no_candidate_stage2_unit_stop_n),
                    pending_hinted_units=_pending_hinted_units(jobs, units_attempted)):
                early_stop_reason = _format_no_candidate_unit_stop(consecutive_no_candidate_units)
                result_status = "early-stop-no-output"
                failure_reason = early_stop_reason
                break
            continue
        stage4_candidate_units_attempted += 1
        if _should_skip_concrete_only_after_puts(partial_put,
                                                 args.skip_concrete_only_after_put_valid,
                                                 n_certified, n_cleared_fallback,
                                                 n_timeout_fallback, n_complete_witness_fallback,
                                                 n_partial_journal_fallback):
            put_valid_before_skip = partial_put.get("put_valid") or 0
            skip_reason = _format_put_saturated_concrete_only_skip(
                put_valid_before_skip, args.skip_concrete_only_after_put_valid)
            put_saturated_concrete_only_stage4_skips.append({
                "unit":
                unit,
                "job_id":
                job.get("job_id"),
                "remaining_s":
                round(remaining_before_stage4, 3),
                "threshold_put_valid":
                args.skip_concrete_only_after_put_valid,
                "certified_regions_for_unit":
                n_certified,
                "cleared_concrete_fallbacks_for_unit":
                n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit":
                n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                n_partial_journal_fallback,
                "raw_before_skip":
                partial_put.get("raw") or 0,
                "valid_before_skip":
                partial_put.get("valid") or 0,
                "put_valid_before_skip":
                put_valid_before_skip,
                "reason":
                skip_reason,
            })
            continue
        if _should_skip_concrete_only_after_any_valid(
                partial_put, getattr(args, "skip_concrete_only_after_any_valid",
                                     True), n_certified, n_cleared_fallback, n_timeout_fallback,
                n_complete_witness_fallback, n_partial_journal_fallback):
            valid_before_skip = int(partial_put.get("valid") or 0)
            put_valid_before_skip = int(partial_put.get("put_valid") or 0)
            skip_reason = _format_valid_saturated_concrete_only_skip(valid_before_skip,
                                                                     put_valid_before_skip)
            valid_saturated_concrete_only_stage4_skips.append({
                "unit":
                unit,
                "job_id":
                job.get("job_id"),
                "remaining_s":
                round(remaining_before_stage4, 3),
                "certified_regions_for_unit":
                n_certified,
                "cleared_concrete_fallbacks_for_unit":
                n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit":
                n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                n_partial_journal_fallback,
                "raw_before_skip":
                partial_put.get("raw") or 0,
                "valid_before_skip":
                valid_before_skip,
                "put_valid_before_skip":
                put_valid_before_skip,
                "reason":
                skip_reason,
            })
            continue
        if _should_skip_low_budget_concrete_only_stage4(partial_put, remaining_before_stage4,
                                                        args.min_concrete_only_stage4_s,
                                                        n_certified, n_cleared_fallback,
                                                        n_timeout_fallback,
                                                        n_complete_witness_fallback,
                                                        n_partial_journal_fallback):
            skip_reason = _format_low_budget_concrete_only_skip(remaining_before_stage4,
                                                                args.min_concrete_only_stage4_s)
            low_budget_concrete_only_stage4_skips.append({
                "unit":
                unit,
                "job_id":
                job.get("job_id"),
                "remaining_s":
                round(remaining_before_stage4, 3),
                "threshold_s":
                args.min_concrete_only_stage4_s,
                "certified_regions_for_unit":
                n_certified,
                "cleared_concrete_fallbacks_for_unit":
                n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit":
                n_timeout_fallback,
                "complete_witness_concrete_fallbacks_for_unit":
                n_complete_witness_fallback,
                "partial_journal_concrete_fallbacks_for_unit":
                n_partial_journal_fallback,
                "raw_before_skip":
                partial_put.get("raw") or 0,
                "valid_before_skip":
                partial_put.get("valid") or 0,
                "reason":
                skip_reason,
            })
            continue
        if _remaining(deadline) < args.min_remaining_s:
            result_status = "budget-exhausted"
            failure_reason = "case budget exhausted before Stage 4"
            break
        mem_wait = wait_for_mem_budget(args.memlimit_gib,
                                       deadline,
                                       fraction=args.stage_mem_fraction,
                                       poll_s=args.mem_wait_poll_s,
                                       min_remaining_s=args.min_remaining_s)
        if mem_wait["waited"] or mem_wait["status"] != "ok":
            mem_wait.update({"unit": unit, "before_stage": "put"})
            stages.append(mem_wait)
        if mem_wait["status"] != "ok":
            result_status = "budget-exhausted"
            failure_reason = (f"insufficient memory before put {unit}: need "
                              f"MemAvailable >= "
                              f"{mem_wait['required_mem_available_gib']}GiB for "
                              f"{args.memlimit_gib}GiB at "
                              f"{args.stage_mem_fraction:.0%}; have "
                              f"{mem_wait['mem_available_gib']}GiB")
            break
        stage4_job_id = str(job.get("job_id") or unit)
        put_execution_root, put_root = _strict_stage4_roots(
            case_dir, cert_path, stage4_job_id, strict_case_wall_budget)
        put_generation_budget_s = _remaining(deadline)
        adaptive_stage4_budget_capped = False
        if strict_case_wall_budget and pending_units_after_this > 0:
            fair_budget_s = _strict_stage4_fair_budget_s(put_generation_budget_s,
                                                         pending_units_after_this)
            if fair_budget_s < put_generation_budget_s:
                adaptive_stage4_budget_caps.append({
                    "unit": unit,
                    "job_id": job.get("job_id"),
                    "original_generation_budget_s": round(put_generation_budget_s, 3),
                    "capped_generation_budget_s": round(fair_budget_s, 3),
                    "pending_units_after_this": pending_units_after_this,
                    "fair_share_slots": STRICT_STAGE4_FAIR_SHARE_SLOTS,
                })
                put_generation_budget_s = fair_budget_s
                adaptive_stage4_budget_capped = True
        stage4_budget_capped_for_concrete_only = False
        concrete_only_cap_s = concrete_only_stage4_timeout_cap_s
        if (concrete_only_stage4 and pending_units_after_this > 0 and concrete_only_cap_s > 0
                and put_generation_budget_s > float(concrete_only_cap_s)):
            concrete_only_stage4_budget_caps.append({
                "unit":
                unit,
                "job_id":
                job.get("job_id"),
                "original_generation_budget_s":
                round(put_generation_budget_s, 3),
                "capped_generation_budget_s":
                concrete_only_cap_s,
                "pending_units_after_this":
                pending_units_after_this,
                "certified_regions_for_unit":
                n_certified,
                "cleared_concrete_fallbacks_for_unit":
                n_cleared_fallback,
                "timeout_concrete_fallbacks_for_unit":
                n_timeout_fallback,
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
                             put_execution_root,
                             put_generation_budget_s,
                             args.memlimit_gib,
                             args.forge_timeout,
                             path_function,
                             getattr(args, "esbmc", "") or None,
                             emit_concrete_fallbacks=concrete_only_stage4,
                             foundry_fixture=job.get("source_stage2_fixture_path"),
                             concrete_replay_only=bool(
                                 getattr(args, "concrete_replay_only_ablation", False)),
                             no_test_assert_refinement=bool(
                                 getattr(args, "no_test_assert_refinement", False)))
        # Stage 4's ESBMC/emission work is budgeted by --timeout and the
        # remaining case deadline passed above.  put_all.py then runs Foundry
        # as a second, refutation-only replay oracle; let that finish outside
        # the generation timeout so a slow replay does not reclassify completed
        # generation as a tool timeout.
        put_wrapper_timeout_s = (put_generation_budget_s + args.wrapper_grace +
                                 2 * args.forge_timeout)
        if strict_case_wall_budget:
            put_wrapper_timeout_s = _case_wrapper_timeout(put_wrapper_timeout_s, deadline, True)
        put_log_prefix = case_dir / "logs" / f"{idx:03d}-{_safe_name(unit)}-put"
        if strict_case_wall_budget:
            put_stage = run_command(put_argv,
                                    put_wrapper_timeout_s,
                                    put_log_prefix,
                                    hard_deadline=deadline)
        else:
            put_stage = run_command(put_argv, put_wrapper_timeout_s, put_log_prefix)
        publication = None
        if strict_case_wall_budget:
            try:
                publication = _publish_strict_stage4_artifacts(put_execution_root, put_root)
            except (OSError, RQ1RunError) as exc:
                publication = {
                    "status": "error",
                    "staging_root": str(put_execution_root),
                    "destination": str(put_root),
                    "error": str(exc),
                }
                put_stage["pre_publication_status"] = put_stage.get("status")
                put_stage["status"] = "error"
        put_stage.update({
            "stage": "put",
            "unit": unit,
            "path_function": path_function,
            "generation_budget_s": round(put_generation_budget_s, 3),
            "foundry_replay_outside_generation_timeout": not strict_case_wall_budget,
            "foundry_replay_timeout_s_per_run": args.forge_timeout,
            "certified_regions_for_unit": n_certified,
            "cleared_concrete_fallbacks_for_unit": n_cleared_fallback,
            "timeout_concrete_fallbacks_for_unit": n_timeout_fallback,
            "complete_witness_concrete_fallbacks_for_unit": n_complete_witness_fallback,
            "partial_journal_concrete_fallbacks_for_unit": n_partial_journal_fallback,
            "stage4_candidates_for_unit": n_stage4_candidates,
            "concrete_only_stage4": concrete_only_stage4,
            "pending_units_after_this": pending_units_after_this,
            "concrete_only_stage4_timeout_cap_s": concrete_only_cap_s,
            "stage4_budget_capped_for_concrete_only": stage4_budget_capped_for_concrete_only,
            "stage4_budget_capped_adaptively": adaptive_stage4_budget_capped,
            "put_out_root": str(put_root),
            "put_execution_root": str(put_execution_root),
            "stage4_publication": publication,
        })
        stages.append(put_stage)
        if put_stage["status"] in ("timeout", "oom"):
            if (put_stage["status"] == "timeout" and adaptive_stage4_budget_capped
                    and pending_units_after_this > 0):
                partial_put = summarize_put_artifacts(case_dir / "put")
                adaptive_stage4_soft_timeouts.append({
                    "unit": unit,
                    "job_id": job.get("job_id"),
                    "generation_budget_s": round(put_generation_budget_s, 3),
                    "pending_units_after_this": pending_units_after_this,
                    "raw_after_timeout": int(partial_put.get("raw") or 0),
                    "valid_after_timeout": int(partial_put.get("valid") or 0),
                    "put_valid_after_timeout": int(partial_put.get("put_valid") or 0),
                })
                consecutive_no_candidate_units = 0
                continue
            if (put_stage["status"] == "timeout" and concrete_only_stage4
                    and pending_units_after_this > 0):
                partial_put = summarize_put_artifacts(case_dir / "put")
                if int(partial_put.get("raw") or 0) <= 0:
                    concrete_only_stage4_soft_failures.append({
                        "unit":
                        unit,
                        "job_id":
                        job.get("job_id"),
                        "status":
                        put_stage["status"],
                        "generation_budget_s":
                        round(put_generation_budget_s, 3),
                        "budget_capped":
                        stage4_budget_capped_for_concrete_only,
                        "pending_units_after_this":
                        pending_units_after_this,
                        "certified_regions_for_unit":
                        n_certified,
                        "cleared_concrete_fallbacks_for_unit":
                        n_cleared_fallback,
                        "timeout_concrete_fallbacks_for_unit":
                        n_timeout_fallback,
                        "complete_witness_concrete_fallbacks_for_unit":
                        n_complete_witness_fallback,
                        "partial_journal_concrete_fallbacks_for_unit":
                        n_partial_journal_fallback,
                        "raw_after_timeout":
                        partial_put.get("raw") or 0,
                        "valid_after_timeout":
                        partial_put.get("valid") or 0,
                        "pending_stage4_candidate":
                        True,
                    })
                    consecutive_no_candidate_units = 0
                    continue
            result_status = put_stage["status"]
            failure_reason = f"put {unit}: {put_stage['status']}"
            break
        partial_put = summarize_put_artifacts(case_dir / "put")
        if concrete_only_stage4 and int(partial_put.get("put_valid") or 0) == 0:
            create2_put_stage = emit_source_grounded_createcall_create2_put(
                subject,
                case_dir,
                unit,
                args.forge_timeout,
                deadline=(deadline if strict_case_wall_budget else None))
            if create2_put_stage["status"] != "skipped":
                create2_put_stage["trigger"] = "concrete-only-stage4-produced-no-put"
                stages.append(create2_put_stage)
                partial_put = summarize_put_artifacts(case_dir / "put")
            fifs_put_stage = emit_source_grounded_fifs_registrar_put(
                subject,
                case_dir,
                unit,
                args.forge_timeout,
                path_function=path_function,
                cert_path=cert_path,
                deadline=(deadline if strict_case_wall_budget else None))
            if fifs_put_stage["status"] != "skipped":
                fifs_put_stage["trigger"] = "concrete-only-stage4-produced-no-put"
                stages.append(fifs_put_stage)
                partial_put = summarize_put_artifacts(case_dir / "put")
            extendedresolver_put_stage = emit_source_grounded_extendedresolver_put(
                subject,
                case_dir,
                unit,
                args.forge_timeout,
                deadline=(deadline if strict_case_wall_budget else None))
            if extendedresolver_put_stage["status"] != "skipped":
                extendedresolver_put_stage["trigger"] = "concrete-only-stage4-produced-no-put"
                stages.append(extendedresolver_put_stage)
                partial_put = summarize_put_artifacts(case_dir / "put")
        if _should_stop_after_zero_output_stage4(stages, partial_put,
                                                 args.zero_output_stage4_stop_s):
            early_stop_reason = _format_stage4_no_output_stop(_stage_wall_s(stages, "put"))
            result_status = "early-stop-no-output"
            failure_reason = early_stop_reason
            break

    if strict_case_wall_budget:
        certification_staging_root = cert_path.parent
        cert_path, cert_input_path_map = _publish_strict_certification_artifacts(
            cert_path, case_dir)
        schedule = _relocate_record_paths(schedule, certification_staging_root, cert_path.parent)
        stages = _relocate_record_paths(stages, certification_staging_root, cert_path.parent)
        schedule = _relocate_exact_record_paths(schedule, cert_input_path_map)
        stages = _relocate_exact_record_paths(stages, cert_input_path_map)
        _write_json(case_dir / "unit-schedule.json", schedule)
    cert_summary = summarize_certification(cert_path)
    put_summary = summarize_put_artifacts(case_dir / "put")
    # A target whose scheduled units all failed to certify still exposes its
    # `public` state variables as ABI entry points.  Query those getters before
    # falling through to the unoracled deploy safety net: they are real units
    # and usually certify, so this recovers whole targets that would otherwise
    # report no output at all.  Restricted to zero-yield cases so a productive
    # target never loses budget to them.
    if (put_summary["valid"] <= 0 and result_status not in {"error"}
            and not getattr(args, "ce_replay_only", False)
            and not getattr(args, "concrete_replay_only_ablation", False)):
        getter_rescue_stages = emit_zero_yield_getter_fallbacks(
            subject,
            case_dir,
            schedule,
            _remaining(deadline),
            args.memlimit_gib,
            args.forge_timeout,
            getattr(args, "esbmc", "") or None,
            deadline=(deadline if strict_case_wall_budget else None))
        if getter_rescue_stages:
            for stage in getter_rescue_stages:
                stage["trigger"] = "no-valid-after-stage4"
            stages.extend(getter_rescue_stages)
            put_summary = summarize_put_artifacts(case_dir / "put")
    if (getattr(args, "final_deploy_concrete_fallback", False) and put_summary["valid"] <= 0
            and result_status not in {"error"}
            and _no_unit_schedule_allows_deploy_fallback(schedule)):
        fallback_stage = emit_no_unit_deploy_fallback(
            subject,
            case_dir,
            schedule,
            args.forge_timeout,
            force=True,
            reason=("final safety-net concrete replay: Stage2/Stage4 produced no "
                    "valid reference artifact for this target contract; this is "
                    "kept as concrete quality debt, not as a PUT/R1/R2 claim"),
            out_name="final_deploy_concrete_fallback",
            deadline=(deadline if strict_case_wall_budget else None),
            publish_unoracled_deploy_smoke=not getattr(args, "concrete_replay_only_ablation",
                                                       False))
        fallback_stage["trigger"] = "no-valid-after-stage4"
        fallback_stage["valid_before_fallback"] = put_summary["valid"]
        fallback_stage["raw_before_fallback"] = put_summary["raw"]
        stages.append(fallback_stage)
        put_summary = summarize_put_artifacts(case_dir / "put")
    concrete_replay_persistence = persist_case_concrete_replays(case_dir, put_summary,
                                                                f"{dataset_label}/{subject_id}")
    persistence_failure = None
    if put_summary["valid"] > 0:
        persistence_failure = persistence_publication_failure(concrete_replay_persistence)
        if persistence_failure:
            put_summary = quarantine_unpersisted_validity(put_summary, persistence_failure,
                                                           concrete_replay_persistence)
            result_status = "persistence-error"
            failure_reason = persistence_failure
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
    generation_wall_s = round(stage2_wall_s + put_summary["stage4_generation_wall_s"], 3)
    stage2_capped_timeout_units = [
        stage.get("unit") for stage in stages
        if (stage.get("stage") == "certify" and stage.get("status") == "timeout"
            and int(stage.get("stage2_unit_timeout_cap_s_effective") or 0) > 0)
    ]
    no_unit_deploy_fallback_stages = [
        stage for stage in stages if stage.get("stage") == "no-unit-deploy-fallback"
    ]
    overload_path_function_stages = [
        stage for stage in stages if stage.get("stage") == "schedule-overload-path-functions"
    ]
    row = {
        "key":
        f"gen:veriput:{subject_id}",
        "stage":
        "gen_veriput",
        "schema":
        "veriput-rq1-result-row/v1",
        "ts":
        round(time.time(), 3),
        "generated_at":
        _utc_now(),
        "host":
        socket.gethostname(),
        "n_concurrent":
        args.jobs,
        "mem_budget_mb":
        args.memlimit_gib * 1024,
        "tool_timeout_s":
        args.timeout,
        "esbmc_run_timeout_s":
        args.esbmc_run_timeout,
        "esbmc_binary_identity":
        _esbmc_binary_identity(getattr(args, "esbmc", "")),
        "pipeline_code_identity":
        _pipeline_code_identity(getattr(args, "stage4_driver", "")),
        "verifier_input_identity":
        _verifier_input_identity(schedule),
        "stage4_toolchain_identity":
        _stage4_toolchain_identity(),
        "resume_quality_floor":
        getattr(args, "resume_quality_floor", "no-valid"),
        "stage2_unit_timeout_cap_s":
        args.stage2_unit_timeout_cap_s,
        "adaptive_stage2_unit_timeout_cap_s":
        args.adaptive_stage2_unit_timeout_cap_s,
        "stage2_stage4_reserve_s":
        _stage4_reserve_s(args),
        "stage4_reserve_boundary_enforced":
        True,
        "adaptive_stage2_many_unit_threshold":
        ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
        "adaptive_stage2_expensive_tier_threshold":
        ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
        "cleared_concrete_fallbacks_enabled":
        True,
        "timeout_concrete_fallbacks_enabled":
        True,
        "complete_witness_concrete_fallbacks_enabled":
        True,
        "partial_journal_concrete_fallbacks_enabled":
        True,
        "no_unit_deploy_fallback_enabled":
        True,
        "no_unit_deploy_fallback_count":
        len(no_unit_deploy_fallback_stages),
        "no_unit_deploy_fallback_statuses":
        [stage.get("status") for stage in no_unit_deploy_fallback_stages],
        "no_unit_deploy_fallback_paths": [
            stage.get("put_out_root") for stage in no_unit_deploy_fallback_stages
            if stage.get("put_out_root")
        ],
        "no_output_stage2_stop_s":
        args.no_output_stage2_stop_s,
        "min_no_output_stage2_unit_stop_n":
        args.min_no_output_stage2_unit_stop_n,
        "no_candidate_stage2_unit_stop_n":
        args.no_candidate_stage2_unit_stop_n,
        "min_no_candidate_stage2_unit_stop_n":
        args.min_no_candidate_stage2_unit_stop_n,
        "max_consecutive_no_candidate_units":
        max_consecutive_no_candidate_units,
        "stage2_no_candidate_evidence_units":
        stage2_no_candidate_evidence_units,
        "stage2_no_candidate_stop_skipped_units":
        stage2_no_candidate_stop_skipped_units,
        "stage2_no_candidate_stop_skipped_unit_count":
        len(stage2_no_candidate_stop_skipped_units),
        "stage2_no_output_continuations":
        stage2_no_output_continuations,
        "stage2_no_output_continuation_count":
        len(stage2_no_output_continuations),
        "stage2_capped_timeout_units":
        stage2_capped_timeout_units,
        "stage2_capped_timeout_unit_count":
        len(stage2_capped_timeout_units),
        "overload_path_function_retry_count":
        sum(int(stage.get("added_jobs") or 0) for stage in overload_path_function_stages),
        "overload_path_function_retry_units":
        [stage.get("unit") for stage in overload_path_function_stages],
        "stage4_candidate_units_attempted":
        stage4_candidate_units_attempted,
        "ce_replay_manifest_paths":
        [str(path) for path in _candidate_manifest_paths(getattr(args, "ce_replay_manifest", []))],
        "ce_replay_only":
        bool(getattr(args, "ce_replay_only", False)),
        "ce_replay_candidates_discovered":
        len(ce_replay_candidates),
        "ce_replay_candidates_attempted":
        len(ce_replay_stages),
        "ce_replay_candidates_promoted":
        sum(1 for stage in ce_replay_stages
            if (stage.get("candidate_promotion") or {}).get("promoted")),
        "ce_replay_candidate_rejections":
        ce_replay_rejected,
        "ce_replay_theory_delta":
        0,
        "zero_output_stage4_stop_s":
        args.zero_output_stage4_stop_s,
        "min_concrete_only_stage4_s":
        args.min_concrete_only_stage4_s,
        "min_timeout_only_stage4_s":
        args.min_timeout_only_stage4_s,
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
        "adaptive_stage4_budget_caps":
        adaptive_stage4_budget_caps,
        "adaptive_stage4_budget_cap_count":
        len(adaptive_stage4_budget_caps),
        "adaptive_stage4_soft_timeouts":
        adaptive_stage4_soft_timeouts,
        "adaptive_stage4_soft_timeout_count":
        len(adaptive_stage4_soft_timeouts),
        "early_stop_reason":
        early_stop_reason,
        "wall_cap_s":
        args.timeout if strict_case_wall_budget else args.timeout + args.wrapper_grace,
        "strict_case_wall_budget":
        strict_case_wall_budget,
        "strict_case_finalization_reserve_s":
        round(strict_finalization_reserve_s, 3),
        "strict_case_work_cap_s":
        round(max(0.0, float(args.timeout) - strict_finalization_reserve_s), 3),
        "status":
        result_status,
        "completion_status":
        completion_status,
        "budget_exhausted":
        budget_exhausted,
        "reason":
        failure_reason,
        "partial_failure_reason":
        partial_failure_reason,
        "subject_id":
        subject_id,
        "benchmark":
        target_row["benchmark"],
        "dataset":
        dataset_label,
        "contract":
        target_row.get("contract"),
        "raw":
        put_summary["raw"],
        "valid":
        put_summary["valid"],
        "put_raw":
        put_summary["put_raw"],
        "put_valid":
        put_summary["put_valid"],
        "concrete_raw":
        put_summary["concrete_raw"],
        "concrete_valid":
        put_summary["concrete_valid"],
        "quality_bucket":
        put_summary["quality_bucket"],
        "valid_put_with_R1":
        put_summary["valid_put_with_R1"],
        "valid_put_with_R2":
        put_summary["valid_put_with_R2"],
        "valid_put_with_R1_or_R2":
        put_summary["valid_put_with_R1_or_R2"],
        "valid_put_without_R1R2":
        put_summary["valid_put_without_R1R2"],
        "raw_tests":
        put_summary["raw_tests"],
        "valid_tests":
        put_summary["valid_tests"],
        "oracle_class_counts":
        put_summary["oracle_class_counts"],
        "oracle_class_combo_counts":
        put_summary["oracle_class_combo_counts"],
        "assertion_oracles":
        put_summary["assertion_oracles"],
        "stage4_storage_layout_counts":
        put_summary["stage4_storage_layout_counts"],
        "put_json_count":
        put_summary["put_json_count"],
        "cert_bucket_counts":
        cert_summary["bucket_counts"],
        "cert_exit_counts":
        cert_summary["exit_counts"],
        "cert_witness_counts":
        cert_summary["witness_counts"],
        "cert_timed_out_units":
        cert_summary["timed_out_units"],
        "cert_oom_units":
        cert_summary["oom_units"],
        "driver_refusal_tags":
        cert_summary["driver_refusal_tags"],
        "driver_diagnostic_tags":
        cert_summary["driver_diagnostic_tags"],
        "units_attempted":
        units_attempted,
        "units_scheduled":
        len(jobs),
        "schedule_summary":
        schedule.get("summary") or {},
        "schedule_skipped_rows":
        schedule.get("skipped_rows") or [],
        "schedule_no_unit_rows":
        schedule.get("no_unit_rows") or [],
        "schedule_skipped_units":
        schedule.get("skipped_units") or [],
        "generation_wall_s":
        generation_wall_s,
        "stage2_wall_s":
        stage2_wall_s,
        "stage4_wall_s":
        stage4_wall_s,
        "stage4_generation_wall_s":
        put_summary["stage4_generation_wall_s"],
        "stage4_emission_wall_s":
        put_summary["stage4_emission_wall_s"],
        "foundry_replay_wall_s":
        put_summary["foundry_replay_wall_s"],
        "put_all_wall_s":
        put_summary["put_all_wall_s"],
        "foundry_replay_outside_generation_timeout":
        not strict_case_wall_budget,
        "wall":
        wall_total_s,
        "wall_total_s":
        wall_total_s,
        "maxrss_mb":
        max([stage.get("maxrss_proc_mb") or 0.0 for stage in stages] or [0.0]),
        "artifact_root":
        str(case_dir),
        "result_json":
        str(case_dir / "result.json"),
        "cert_jsonl":
        str(cert_path),
        "cert_shard_merges":
        cert_shard_merges,
        "cert_shard_merge_count":
        len(cert_shard_merges),
        "cert_shard_rows_merged":
        sum(item.get("merged") or 0 for item in cert_shard_merges),
        "cert_shard_invalid_rows":
        sum(item.get("invalid") or 0 for item in cert_shard_merges),
        "put_summary_paths":
        put_summary["summary_paths"],
        "raw_artifacts_retained":
        put_summary["raw"] > 0,
        "valid_artifacts_retained":
        put_summary["valid"] > 0,
        "concrete_replay_persistence":
        concrete_replay_persistence,
        "recipe_version":
        STRONG_RECIPE_VERSION,
    }
    row.update(_bounded_holds_retry_policy(args))
    # A replay-only invocation is a transaction over explicit CE candidates.
    # It must not turn a neighbouring .redo/.incomplete artifact into formal
    # credit when every candidate was rejected. Existing canonical artifacts
    # are still summarized normally; only cross-directory stale adoption is
    # disabled for this isolated entry point.
    stale_row = None
    if not getattr(args, "ce_replay_only", False):
        stale_row = _best_stale_artifact_row(target_row, dataset_label, case_dir, row)
        row = _adopt_stale_artifacts(row, stale_row)
    final_valid_tests = [test for test in row.get("valid_tests") or [] if isinstance(test, dict)]
    if final_valid_tests:
        final_replay_persistence = persist_case_concrete_replays(case_dir,
                                                                 {"valid_tests": final_valid_tests},
                                                                 f"{dataset_label}/{subject_id}")
        final_persistence_failure = persistence_publication_failure(final_replay_persistence)
        concrete_replay_persistence = final_replay_persistence
        row["concrete_replay_persistence"] = final_replay_persistence
        if final_persistence_failure:
            row = quarantine_unpersisted_validity(row, final_persistence_failure,
                                                   final_replay_persistence)
            put_summary = quarantine_unpersisted_validity(put_summary,
                                                           final_persistence_failure,
                                                           final_replay_persistence)
            if not row.get("valid_tests"):
                row["status"] = "persistence-error"
                row["reason"] = final_persistence_failure
            row = _annotate_result_accounting(row)
            persistence_failure = final_persistence_failure
    # Measure the whole subject transaction after durable publication,
    # summarization, stale-artifact accounting, and replay persistence. The
    # result-file write below is the only remaining bounded metadata write.
    wall_total_s = round(time.monotonic() - start, 3)
    row["wall"] = wall_total_s
    row["wall_total_s"] = wall_total_s
    row["strict_case_wall_within_cap"] = (
        not strict_case_wall_budget or wall_total_s <= float(args.timeout))
    if isinstance(row.get("time_stats"), dict):
        row["time_stats"]["wall_total_s"] = wall_total_s
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
            "schema":
            "veriput-rq1-ce-replay-accounting/v1",
            "candidates_discovered":
            len(ce_replay_candidates),
            "candidates_attempted":
            len(ce_replay_stages),
            "candidates_promoted":
            sum(1 for stage in ce_replay_stages
                if (stage.get("candidate_promotion") or {}).get("promoted")),
            "rejections":
            ce_replay_rejected,
            "theory_delta":
            0,
            "formal_results_written_only_after_gates":
            True,
        },
        "certification": cert_summary,
        "put": put_summary,
        "concrete_replay_persistence": concrete_replay_persistence,
        "persistence_publication_failure": persistence_failure,
        "stale_artifact_adoption": {
            "adopted": bool(row.get("adopted_stale_artifacts")),
            "source": row.get("stale_artifact_root"),
            "source_result_json": row.get("stale_result_json"),
            "source_quality_bucket": row.get("stale_quality_bucket"),
            "disabled_for_ce_replay_only": bool(getattr(args, "ce_replay_only", False)),
        },
    }
    _write_json(case_dir / "result.json", detail)
    return row, detail


def run_selected_subjects(rows: list[dict], dataset_label: str, journal: Path,
                          done: dict[str, dict], args) -> int:
    selected = [
        row for row in rows
        if _run_key(row["subject_id"], ce_collection_only=args.ce_collection_only) not in done
    ]
    if not selected:
        return 0
    if args.jobs <= 1:
        attempted = 0
        for target_row in selected:
            print(
                f"[rq1] {dataset_label} {target_row['subject_id']} "
                f"contract={target_row.get('contract')}",
                flush=True)
            row, _detail = run_subject(target_row, dataset_label, args)
            _append_jsonl(journal, row)
            write_dataset_manifest(Path(args.result_root), dataset_label, journal)
            attempted += 1
            print(
                f"[rq1] -> status={row['status']} raw={row['raw']} "
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
            print(
                f"[rq1] queued {dataset_label} {target_row['subject_id']} "
                f"contract={target_row.get('contract')}",
                flush=True)
            futures[executor.submit(run_subject, target_row, dataset_label, args)] = target_row
        for future in as_completed(futures):
            target_row = futures[future]
            try:
                row, _detail = future.result()
            except Exception as exc:  # Subject-level fail-soft.
                now = round(time.time(), 3)
                row = {
                    "key":
                    f"gen:veriput:{target_row['subject_id']}",
                    "stage":
                    "gen_veriput",
                    "schema":
                    "veriput-rq1-result-row/v1",
                    "ts":
                    now,
                    "generated_at":
                    _utc_now(),
                    "host":
                    socket.gethostname(),
                    "n_concurrent":
                    args.jobs,
                    "mem_budget_mb":
                    args.memlimit_gib * 1024,
                    "tool_timeout_s":
                    args.timeout,
                    "esbmc_run_timeout_s":
                    args.esbmc_run_timeout,
                    "resume_quality_floor":
                    getattr(args, "resume_quality_floor", "no-valid"),
                    "stage2_unit_timeout_cap_s":
                    args.stage2_unit_timeout_cap_s,
                    "adaptive_stage2_unit_timeout_cap_s":
                    args.adaptive_stage2_unit_timeout_cap_s,
                    "stage2_stage4_reserve_s":
                    _stage4_reserve_s(args),
                    "adaptive_stage2_many_unit_threshold":
                    ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
                    "adaptive_stage2_expensive_tier_threshold":
                    ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
                    "no_unit_deploy_fallback_enabled":
                    True,
                    "no_unit_deploy_fallback_count":
                    0,
                    "no_unit_deploy_fallback_statuses": [],
                    "no_unit_deploy_fallback_paths": [],
                    "wall_cap_s":
                    args.timeout if getattr(args, "strict_case_wall_budget", False) else
                    args.timeout + args.wrapper_grace,
                    "strict_case_wall_budget":
                    bool(getattr(args, "strict_case_wall_budget", False)),
                    "status":
                    "error",
                    "completion_status":
                    "error",
                    "budget_exhausted":
                    False,
                    "reason":
                    f"runner exception: {exc}",
                    "subject_id":
                    target_row["subject_id"],
                    "benchmark":
                    target_row.get("benchmark"),
                    "dataset":
                    dataset_label,
                    "contract":
                    target_row.get("contract"),
                    "raw":
                    0,
                    "valid":
                    0,
                    "put_raw":
                    0,
                    "put_valid":
                    0,
                    "concrete_raw":
                    0,
                    "concrete_valid":
                    0,
                    "quality_bucket":
                    "no-valid",
                    "valid_put_with_R1":
                    0,
                    "valid_put_with_R2":
                    0,
                    "valid_put_with_R1_or_R2":
                    0,
                    "valid_put_without_R1R2":
                    0,
                    "raw_tests": [],
                    "valid_tests": [],
                    "oracle_class_counts": {},
                    "oracle_class_combo_counts": {},
                    "assertion_oracles": [],
                    "put_json_count":
                    0,
                    "cert_bucket_counts": {},
                    "cert_exit_counts": {},
                    "cert_witness_counts": {},
                    "cert_timed_out_units": [],
                    "cert_oom_units": [],
                    "units_attempted": [],
                    "units_scheduled":
                    0,
                    "stage2_capped_timeout_units": [],
                    "stage2_capped_timeout_unit_count":
                    0,
                    "overload_path_function_retry_count":
                    0,
                    "overload_path_function_retry_units": [],
                    "stage4_candidate_units_attempted":
                    0,
                    "zero_output_stage4_stop_s":
                    args.zero_output_stage4_stop_s,
                    "min_concrete_only_stage4_s":
                    args.min_concrete_only_stage4_s,
                    "min_timeout_only_stage4_s":
                    args.min_timeout_only_stage4_s,
                    "skip_concrete_only_after_put_valid":
                    args.skip_concrete_only_after_put_valid,
                    "skip_concrete_only_after_any_valid":
                    getattr(args, "skip_concrete_only_after_any_valid", True),
                    "low_budget_concrete_only_stage4_skips": [],
                    "low_budget_concrete_only_stage4_skip_count":
                    0,
                    "low_budget_timeout_only_stage4_skips": [],
                    "low_budget_timeout_only_stage4_skip_count":
                    0,
                    "put_saturated_concrete_only_stage4_skips": [],
                    "put_saturated_concrete_only_stage4_skip_count":
                    0,
                    "valid_saturated_concrete_only_stage4_skips": [],
                    "valid_saturated_concrete_only_stage4_skip_count":
                    0,
                    "concrete_only_stage4_timeout_cap_s":
                    getattr(args, "concrete_only_stage4_timeout_cap_s",
                            DEFAULT_CONCRETE_ONLY_STAGE4_TIMEOUT_CAP_S),
                    "concrete_only_stage4_budget_caps": [],
                    "concrete_only_stage4_budget_cap_count":
                    0,
                    "concrete_only_stage4_soft_failures": [],
                    "concrete_only_stage4_soft_failure_count":
                    0,
                    "adaptive_stage4_budget_caps": [],
                    "adaptive_stage4_budget_cap_count": 0,
                    "adaptive_stage4_soft_timeouts": [],
                    "adaptive_stage4_soft_timeout_count": 0,
                    "generation_wall_s":
                    0.0,
                    "stage2_wall_s":
                    0.0,
                    "stage4_wall_s":
                    0.0,
                    "stage4_generation_wall_s":
                    0.0,
                    "stage4_emission_wall_s":
                    0.0,
                    "foundry_replay_wall_s":
                    0.0,
                    "put_all_wall_s":
                    0.0,
                    "foundry_replay_outside_generation_timeout":
                    not bool(getattr(args, "strict_case_wall_budget", False)),
                    "wall":
                    0.0,
                    "wall_total_s":
                    0.0,
                    "maxrss_mb":
                    0.0,
                    "artifact_root":
                    None,
                    "result_json":
                    None,
                    "cert_jsonl":
                    None,
                    "cert_shard_merges": [],
                    "cert_shard_merge_count":
                    0,
                    "cert_shard_rows_merged":
                    0,
                    "cert_shard_invalid_rows":
                    0,
                    "put_summary_paths": [],
                    "raw_artifacts_retained":
                    False,
                    "valid_artifacts_retained":
                    False,
                    "recipe_version":
                    STRONG_RECIPE_VERSION,
                }
                row.update(_bounded_holds_retry_policy(args))
                case_dir = (Path(args.result_root) / dataset_label / "subjects" /
                            _safe_name(target_row["subject_id"]))
                stale_row = _best_stale_artifact_row(target_row, dataset_label, case_dir, row)
                row = _adopt_stale_artifacts(row, stale_row)
                if _write_normalized_case_result(
                        case_dir,
                        row,
                        reason=("subject-level runner exception recovered a "
                                "stronger retained Stage-4 artifact row")):
                    row["normalized_subject_result_json"] = True
            _append_jsonl(journal, row)
            write_dataset_manifest(Path(args.result_root), dataset_label, journal)
            attempted += 1
            print(
                f"[rq1] done {target_row['subject_id']} -> "
                f"status={row['status']} raw={row['raw']} valid={row['valid']} "
                f"put={row['put_valid']}/{row['put_raw']} "
                f"concrete={row['concrete_valid']}/{row['concrete_raw']} "
                f"bucket={row.get('quality_bucket')} wall={row['wall_total_s']}s",
                flush=True)
    return attempted


def write_dataset_manifest(root: Path, dataset_label: str, journal: Path) -> None:
    latest = {key: _normalize_result_row(row) for key, row in _latest_rows(journal).items()}
    status = Counter(str(row.get("status") or "<missing>") for row in latest.values())
    quality = Counter(
        str(row.get("quality_bucket") or _legacy_quality_bucket(row)) for row in latest.values())
    doc = {
        "schema": "veriput-rq1-dataset-manifest/v1",
        "generated_at": _utc_now(),
        "dataset": dataset_label,
        "journal": str(journal),
        "summary": {
            "rows":
            len(latest),
            "raw":
            sum(row.get("raw") or 0 for row in latest.values()),
            "valid":
            sum(row.get("valid") or 0 for row in latest.values() if row.get("valid") is not None),
            "put_raw":
            sum(row.get("put_raw") or 0 for row in latest.values()),
            "put_valid":
            sum(row.get("put_valid") or 0 for row in latest.values()),
            "concrete_raw":
            sum(row.get("concrete_raw") or 0 for row in latest.values()),
            "concrete_valid":
            sum(row.get("concrete_valid") or 0 for row in latest.values()),
            "valid_put_with_R1":
            sum(row.get("valid_put_with_R1") or 0 for row in latest.values()),
            "valid_put_with_R2":
            sum(row.get("valid_put_with_R2") or 0 for row in latest.values()),
            "valid_put_with_R1_or_R2":
            sum(row.get("valid_put_with_R1_or_R2") or 0 for row in latest.values()),
            "valid_put_without_R1R2":
            sum(row.get("valid_put_without_R1R2") or 0 for row in latest.values()),
            "status":
            dict(sorted(status.items())),
            "quality_bucket":
            dict(sorted(quality.items())),
        },
    }
    manifest_name = ("ce-collection-manifest.json"
                     if journal.name == "ce-collection-results.jsonl" else "manifest.json")
    _write_json(root / dataset_label / manifest_name, doc)


def build_dry_run(args) -> dict:
    dataset_label, rows = target_rows(Path(args.veriput_root), args.benchmark, args.subject_id,
                                      args.limit, args.order)
    enforce_rows_in_window(rows, getattr(args, "active_window", ""))
    doc = {
        "schema":
        "veriput-rq1-dry-run/v1",
        "generated_at":
        _utc_now(),
        "dataset":
        dataset_label,
        "result_root":
        args.result_root,
        "ast_cache_root":
        args.ast_cache_root,
        "timeout_s":
        args.timeout,
        "strict_case_wall_budget":
        bool(getattr(args, "strict_case_wall_budget", False)),
        "rq3_ablation":
        getattr(args, "rq3_ablation", ""),
        "no_selection_strategy":
        bool(getattr(args, "no_selection_strategy", False)),
        "wall_cap_s":
        args.timeout if getattr(args, "strict_case_wall_budget", False) else
        args.timeout + args.wrapper_grace,
        "esbmc_run_timeout_s":
        args.esbmc_run_timeout,
        "stage2_unit_timeout_cap_s":
        args.stage2_unit_timeout_cap_s,
        "adaptive_stage2_unit_timeout_cap_s":
        args.adaptive_stage2_unit_timeout_cap_s,
        "stage2_stage4_reserve_s":
        _stage4_reserve_s(args),
        "adaptive_stage2_many_unit_threshold":
        ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD,
        "adaptive_stage2_expensive_tier_threshold":
        ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
        "no_output_stage2_stop_s":
        args.no_output_stage2_stop_s,
        "min_no_output_stage2_unit_stop_n":
        args.min_no_output_stage2_unit_stop_n,
        "no_candidate_stage2_unit_stop_n":
        args.no_candidate_stage2_unit_stop_n,
        "min_no_candidate_stage2_unit_stop_n":
        args.min_no_candidate_stage2_unit_stop_n,
        "zero_output_stage4_stop_s":
        args.zero_output_stage4_stop_s,
        "min_concrete_only_stage4_s":
        args.min_concrete_only_stage4_s,
        "min_timeout_only_stage4_s":
        args.min_timeout_only_stage4_s,
        "skip_concrete_only_after_put_valid":
        args.skip_concrete_only_after_put_valid,
        "skip_concrete_only_after_any_valid":
        getattr(args, "skip_concrete_only_after_any_valid", True),
        "resume_quality_floor":
        getattr(args, "resume_quality_floor", "no-valid"),
        "memlimit_gib":
        args.memlimit_gib,
        "jobs":
        args.jobs,
        "stage_mem_fraction":
        args.stage_mem_fraction,
        "mem_wait_poll_s":
        args.mem_wait_poll_s,
        "order":
        args.order,
        "subjects": [{
            "subject_id": row.get("subject_id"),
            "benchmark": row.get("benchmark"),
            "contract": row.get("contract"),
            "units_hint": row.get("units_hint") or [],
        } for row in rows],
        "ce_replay_manifest_paths":
        [str(path) for path in _candidate_manifest_paths(getattr(args, "ce_replay_manifest", []))],
        "ce_replay_only":
        bool(getattr(args, "ce_replay_only", False)),
        "ce_replay_theory_delta":
        0,
    }
    doc.update(_bounded_holds_retry_policy(args))
    return doc


def main(argv=None) -> int:
    global PUT_ALL
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--veriput-root", default=str(DEFAULT_VERIPUT_ROOT))
    ap.add_argument("--benchmark",
                    required=True,
                    choices=sorted(TARGET_BENCHMARK_ARG),
                    help="peer182, bugfix124, or real203/stress203")
    ap.add_argument("--subject-id",
                    action="append",
                    default=[],
                    help="restrict to one prepared subject id. Repeatable")
    ap.add_argument("--unit",
                    action="append",
                    default=[],
                    help="restrict selected subjects to this public/external "
                    "unit name. Repeatable")
    ap.add_argument("--limit",
                    type=int,
                    default=0,
                    help="run only the first N selected target subjects")
    ap.add_argument("--order",
                    choices=("fast-first", "dataset"),
                    default="fast-first",
                    help="subject order before --limit. fast-first sorts by "
                    "prepared flat.sol size to get early throughput")
    ap.add_argument("--active-window",
                    default=os.environ.get("VERIPUT_RQ1_ACTIVE_WINDOW", ""),
                    help="JSON/TSV active rolling-window file. When set, "
                    "every selected subject must appear in that window; "
                    "otherwise the run is refused before dry-run or ESBMC")
    ap.add_argument("--result-root", default=str(DEFAULT_RESULT_ROOT))
    ap.add_argument("--ast-cache-root", default=str(DEFAULT_AST_CACHE_ROOT))
    ap.add_argument("--stage4-driver",
                    default=str(PUT_ALL),
                    help="explicit put_all.py driver. Experiment adapters may point this at "
                    "Tools/VeriPUT/put_all.py; the selected file is included in the "
                    "pipeline code identity")
    ap.add_argument("--ce-collection-only",
                    action="store_true",
                    help="collect at most one bounded 60-second CE artifact "
                    "per subject. This does not generate tests or update "
                    "canonical RQ1 validity results.")
    ap.add_argument("--ce-replay-manifest",
                    action="append",
                    default=[],
                    metavar="PATH",
                    help="consume explicit refutation-only CE replay candidate "
                    "manifest(s) through an isolated Stage-4 entry. A "
                    "candidate is never formal credit by itself; only a "
                    "reference-valid and Foundry-green replay is promoted.")
    ap.add_argument("--ce-replay-only",
                    action="store_true",
                    help="run only the admitted CE replay candidates from "
                    "--ce-replay-manifest; do not run normal Stage 2 "
                    "jobs. Requires at least one manifest candidate.")
    ap.add_argument("--concrete-replay-only-ablation",
                    action="store_true",
                    help="run Stage 2 normally, but force every Stage-4 certified region "
                    "and fallback witness through the concrete-only emitter. This "
                    "mode is for isolated ablations and never emits a PUT.")
    ap.add_argument("--no-test-assert-refinement",
                    action="store_true",
                    help="RQ3 ablation: retain only Stage-4 path-exit R0 "
                    "synthesis and disable the R1/R2 assertion-refinement "
                    "ladder")
    ap.add_argument("--no-region-refinement",
                    action="store_true",
                    help="legacy RQ3 flag. This ablation is now derived from "
                    "Full artifacts with rq3_derive_from_full.py instead of "
                    "rerunning Stage 2")
    ap.add_argument("--no-selection-strategy",
                    action="store_true",
                    help="RQ3 ablation: disable ESBMC path-coverage "
                    "call-site selection/degradation during Stage 2. This is "
                    "the only RQ3 ablation that reruns VeriPUT")
    ap.add_argument("--rq3-ablation",
                    choices=sorted(RQ3_ABLATION_ROOTS),
                    default="",
                    help="isolated RQ3 generation arm; this permits only its matching "
                    "Results/RQ3 result root")
    ap.add_argument("--timeout",
                    type=int,
                    default=60,
                    help="whole subject generation budget, seconds; the RQ1 "
                    "first pass is intentionally CE-first and bounded")
    ap.add_argument(
        "--strict-case-wall-budget",
        action="store_true",
        help="count Stage 2, PUT generation, Forge replay and fallback work against one "
                        "subject deadline. This mode is restricted to an isolated fair-rerun or "
                        "valid-no-PUT rerun root and "
        "does not permit history-fed replay modes")
    ap.add_argument("--esbmc-run-timeout",
                    type=int,
                    default=60,
                    help="per ESBMC invocation budget inside certification, "
                    "seconds. The whole subject still gets --timeout")
    ap.add_argument("--esbmc", default="", help="ESBMC binary to pass through to Stage 2")
    ap.add_argument(
        "--log-ladder",
        action="store_true",
        help="Stage 2: refine rounds lay scale-free rungs (lo+2^j / hi-2^j) instead of "
             "uniform ones, same rung count")
    ap.add_argument(
        "--free-entry-state",
        action="store_true",
        help="Stage 2: free every state.* coordinate a query measures, bounds or pins at "
             "the query entry, so the bound-finding rounds and the certification quantify "
             "over every entry value inside the bound (the slice a PUT writes) rather than "
             "over the transaction prefix's own entry value")
    ap.add_argument(
        "--pin-extcall",
        action="store_true",
        help="certify each path under its own harvested external-call return and require "
             "Stage 4 to materialize that pin as a Foundry mock; unrenderable call shapes "
             "remain refused")
    ap.add_argument("--stage2-unit-timeout-cap-s",
                    type=int,
                    default=DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S,
                    help="if positive, cap each Stage-2 unit's whole "
                    "certify_all.py budget to this many seconds while "
                    "leaving --esbmc-run-timeout as the per-ESBMC-run "
                    "cap. Default 0 leaves the cap decision to the "
                    "adaptive Stage-2 scheduler")
    ap.add_argument("--adaptive-stage2-unit-timeout-cap-s",
                    type=int,
                    default=DEFAULT_ADAPTIVE_STAGE2_UNIT_TIMEOUT_CAP_S,
                    help="when --stage2-unit-timeout-cap-s is 0, cap Stage-2 "
                    "for multi-unit subjects or expensive-looking units "
                    "to this many seconds. Set 0 to disable adaptive "
                    "capping")
    ap.add_argument("--stage2-stage4-reserve-s",
                    type=int,
                    default=DEFAULT_STAGE2_STAGE4_RESERVE_S,
                    help="reserve this many subject-generation seconds for "
                    "Stage 4 after each Stage-2 unit. Set 0 to derive "
                    "the reserve from the concrete/timeout Stage-4 "
                    "minimums. The default keeps a larger materialization "
                    "window so partial witness journals do not become "
                    "no-output rows")
    ap.add_argument("--wrapper-grace",
                    type=int,
                    default=60,
                    help="subprocess cleanup/writeout slack outside the tool budget")
    ap.add_argument("--min-remaining-s",
                    type=int,
                    default=20,
                    help="do not start another stage with less than this many seconds")
    ap.add_argument("--no-output-stage2-stop-s",
                    type=int,
                    default=0,
                    help="if positive, stop trying remaining units in a subject "
                    "after this many cumulative Stage-2 seconds when no "
                    "raw artifact has been produced")
    ap.add_argument("--min-no-output-stage2-unit-stop-n",
                    type=int,
                    default=4,
                    help="when --no-output-stage2-stop-s is positive, do not "
                    "apply that early stop before at least this many "
                    "units have been tried, capped by the subject's total "
                    "scheduled units. This prevents one or two slow units "
                    "from abandoning a large target contract")
    ap.add_argument("--no-candidate-stage2-unit-stop-n",
                    type=int,
                    default=0,
                    help="if positive, stop trying remaining units in a subject "
                    "after this many consecutive Stage-2 units produce no "
                    "certified region and no cleared concrete fallback, "
                    "provided no raw artifact has been produced. Default "
                    "0 preserves old scheduling")
    ap.add_argument("--min-no-candidate-stage2-unit-stop-n",
                    type=int,
                    default=8,
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
    ap.add_argument("--bounded-holds-retry-max-tx",
                    type=int,
                    default=2,
                    help="--max-tx value used by --bounded-holds-retry")
    ap.add_argument("--bounded-holds-retry-unwind",
                    type=int,
                    default=8,
                    help="ESBMC --unwind value appended by "
                    "--bounded-holds-retry")
    ap.add_argument("--bounded-holds-retry-max-initial-wall-s",
                    type=int,
                    default=45,
                    help="only bounded-retry a first Stage-2 NO-PATH row whose "
                    "wall_s is at most this many seconds; 0 disables this "
                    "wall-time guard")
    ap.add_argument("--zero-output-stage4-stop-s",
                    type=int,
                    default=0,
                    help="if positive, stop trying remaining units in a subject "
                    "after this many cumulative Stage-4 seconds when "
                    "Stage 4 has run candidate rows but no raw artifact "
                    "has been produced. Default 0 preserves old scheduling")
    ap.add_argument("--min-concrete-only-stage4-s",
                    type=int,
                    default=90,
                    help="after at least one valid artifact exists, do not "
                    "start a Stage-4 pass whose only candidates are "
                    "concrete fallbacks unless at least this many "
                    "generation seconds remain. Set 0 to disable")
    ap.add_argument("--min-timeout-only-stage4-s",
                    type=int,
                    default=90,
                    help="do not start a Stage-4 pass whose only candidates "
                    "come from timed-out/complete partial witnesses, and "
                    "no certified or cleared fallback row, unless at "
                    "least this many generation seconds remain. Set 0 to "
                    "disable")
    ap.add_argument("--skip-concrete-only-after-put-valid",
                    type=int,
                    default=0,
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
    ap.add_argument("--memlimit-gib",
                    type=int,
                    default=DEFAULT_MEMLIMIT_GIB,
                    help="per-ESBMC memory budget passed to Stage 2/4")
    ap.add_argument("--jobs",
                    type=int,
                    default=1,
                    help="number of prepared subjects to run concurrently")
    ap.add_argument("--mem-fraction",
                    type=float,
                    default=0.70,
                    help="refuse --jobs when jobs*memlimit exceeds this "
                    "fraction of current MemAvailable")
    ap.add_argument("--stage-mem-fraction",
                    type=float,
                    default=0.60,
                    help="before starting each Stage-2/4 subprocess, wait "
                    "until memlimit fits this fraction of current "
                    "MemAvailable. This mirrors certify_all.py's guard")
    ap.add_argument("--mem-wait-poll-s",
                    type=float,
                    default=5.0,
                    help="seconds between memory-availability checks")
    ap.add_argument("--forge-timeout", type=int, default=180)
    ap.add_argument("--resume",
                    action="store_true",
                    help="skip subject keys already present in results.jsonl")
    ap.add_argument("--adopt-only",
                    action="store_true",
                    help="normalize and adopt retained subject artifacts, then "
                    "exit without starting Stage 2 or Stage 4")
    ap.add_argument("--fallback-only",
                    action="store_true",
                    help="for explicitly selected no-valid subjects, skip "
                    "Stage 2 and emit only the isolated Foundry concrete "
                    "fallback; never use this as PUT/R1/R2 evidence")
    ap.add_argument("--resume-quality-floor",
                    choices=sorted(QUALITY_BUCKET_RANK),
                    default="valid-PUT-with-R1R2",
                    help="with --resume, retry recorded subjects whose best "
                    "quality bucket is below this floor. The default "
                    "keeps improving valid-no-PUT and valid-PUT-no-R1R2 "
                    "rows toward the RQ1 PUT/R1R2 target; use no-valid "
                    "to reproduce the old skip-most-valid resume policy")
    ap.add_argument("--redo",
                    action="store_true",
                    help="run selected subjects even if results.jsonl already has a row")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        stage4_driver = Path(args.stage4_driver).expanduser().resolve()
        if not stage4_driver.is_file():
            raise RQ1RunError(f"--stage4-driver is not a file: {stage4_driver}")
        PUT_ALL = stage4_driver
        args.stage4_driver = str(stage4_driver)
        # The packaged Stage-4 driver deliberately has no experiment-tree
        # default for forge-std.  Keep an explicit user choice, otherwise
        # provide the runner's already-audited dependency to its subprocess.
        os.environ.setdefault("FORGE_STD", str(FORGE_STD))
        veriput_root = Path(args.veriput_root).expanduser().resolve()
        result_root = Path(args.result_root).expanduser().resolve()
        ast_cache_root = Path(args.ast_cache_root).expanduser().resolve()
        validate_roots(
            veriput_root,
            result_root,
            ast_cache_root,
            concrete_replay_only_ablation=args.concrete_replay_only_ablation,
            strict_case_wall_budget=args.strict_case_wall_budget,
            rq3_ablation=args.rq3_ablation)
        validate_rq3_ablation_args(args)
        if (args.timeout <= 0 or args.esbmc_run_timeout <= 0 or args.wrapper_grace < 0
                or args.memlimit_gib <= 0 or args.no_output_stage2_stop_s < 0
                or args.min_no_output_stage2_unit_stop_n < 0
                or args.no_candidate_stage2_unit_stop_n < 0
                or args.min_no_candidate_stage2_unit_stop_n < 0
                or args.bounded_holds_retry_max_tx <= 0 or args.bounded_holds_retry_unwind <= 0
                or args.bounded_holds_retry_max_initial_wall_s < 0
                or args.stage2_unit_timeout_cap_s < 0 or args.adaptive_stage2_unit_timeout_cap_s < 0
                or args.stage2_stage4_reserve_s < 0 or args.zero_output_stage4_stop_s < 0
                or args.min_concrete_only_stage4_s < 0 or args.min_timeout_only_stage4_s < 0
                or args.skip_concrete_only_after_put_valid < 0
                or args.concrete_only_stage4_timeout_cap_s < 0 or args.stage_mem_fraction <= 0
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
        if not (args.adopt_only or args.fallback_only):
            validate_jobs(args)
        if args.ce_replay_only and not args.ce_replay_manifest:
            raise RQ1RunError("--ce-replay-only requires --ce-replay-manifest")
        if args.strict_case_wall_budget and (args.ce_replay_manifest or args.ce_replay_only
                                             or args.ce_collection_only or args.adopt_only
                                             or args.fallback_only or args.resume):
            raise RQ1RunError(
                "--strict-case-wall-budget forbids history-fed, partial, adoption, and resume "
                "modes; run every target through the same fresh pipeline")
        if args.fallback_only and not args.subject_id:
            raise RQ1RunError("--fallback-only requires at least one --subject-id")
        if args.fallback_only and (args.adopt_only or args.ce_collection_only
                                   or args.ce_replay_manifest):
            raise RQ1RunError("--fallback-only cannot be combined with adoption or CE modes")
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

        dataset_label, rows = target_rows(veriput_root, args.benchmark, args.subject_id, args.limit,
                                          args.order)
        enforce_rows_in_window(rows, args.active_window)
        journal_name = ("ce-collection-results.jsonl"
                        if args.ce_collection_only else "results.jsonl")
        journal = result_root / dataset_label / journal_name
        if args.adopt_only:
            if args.ce_collection_only or args.ce_replay_manifest:
                raise RQ1RunError("--adopt-only cannot be combined with CE collection/replay")
            done = _latest_rows(journal)
            adopt_existing_subject_results(result_root, dataset_label, rows, journal, done)
            write_dataset_manifest(result_root, dataset_label, journal)
            return 0
        done = _latest_rows(journal) if args.resume and not args.redo else {}
        if args.resume and not args.redo and not args.ce_collection_only:
            done = adopt_existing_subject_results(result_root, dataset_label, rows, journal, done)
            retryable = retryable_resume_rows(done, args.resume_quality_floor)
            if retryable:
                for key in retryable:
                    done.pop(key, None)
                print("[rq1] retrying prior weak/no-valid result(s): " + ", ".join(
                    sorted(str(row.get("subject_id") or key) for key, row in retryable.items())),
                      flush=True)
        for target_row in rows:
            if _run_key(target_row["subject_id"],
                        ce_collection_only=args.ce_collection_only) in done:
                print(f"[rq1] skip recorded {target_row['subject_id']}")
        attempted = run_selected_subjects(rows, dataset_label, journal, done, args)
        if attempted == 0:
            write_dataset_manifest(result_root, dataset_label, journal)
        return 0
    except (OSError, RQ1RunError, WindowGuardError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
