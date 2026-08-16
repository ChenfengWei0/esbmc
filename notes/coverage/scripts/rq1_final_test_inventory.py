#!/usr/bin/env python3
"""Report the disjoint RQ1 path/CE obligation inventory.

One obligation is one instrumented path and its counterexample. A valid PUT
changes that obligation from not-generalized to generalized; it does not create
another obligation. Retry rows, PUT basis replays, same-path candidates, and
manifest-entry counts are therefore deliberately absent from this report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rq1_concrete_replay_migrate import (  # noqa: E402
    DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests)
from rq1_concrete_replay_store import (  # noqa: E402
    _artifact_key, _concrete_test_key, _entry_is_currently_not_generalized, _entry_test_keys,
    _oracle_binding_errors, _physical_test_kind, _solidity_function, _structured_oracle_errors,
    _source_grounded_createcall_basis_error, audit_manifest, load_manifest)
from put_all import (  # noqa: E402
    _matching_delimiter, _solidity_code_mask, _solidity_function_spans, _solidity_test_span,
    _strict_extcall_source_projection_error, certified_ce_sha256, forge_json_status_map)

DEFAULT_LEDGER = HERE.parent / "rq1_ce_obligations.frozen.json"
DEFAULT_RECOVERY_POOL = HERE.parent / "rq1_recovery_pool_521.frozen.json"


def _obligation_id(case: str, key: tuple) -> tuple[str, str, str, str, str]:
    """Return the immutable target-local identity of one instrumented CE."""
    return (case, str(key[0]), str(key[1]), str(key[2]), str(key[3]))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _scoped_solidity_function(source: str, selected_test: str,
                              function_name: str) -> tuple[str, str] | None:
    """Select a helper only from the contract that owns the physical PUT."""
    selected = _solidity_function_spans(source, selected_test)
    if len(selected) != 1 or selected[0][0] is None:
        return None
    selected_start = selected[0][0][0]
    mask = _solidity_code_mask(source)
    contracts = []
    for match in re.finditer(r"\b(?:abstract\s+)?contract\s+[A-Za-z_$][A-Za-z0-9_$]*[^{};]*\{",
                             mask):
        opening = mask.find("{", match.start(), match.end())
        closing = _matching_delimiter(mask, opening, "{", "}")
        if closing is not None and match.start() < selected_start < closing:
            contracts.append((match.start(), closing + 1))
    if not contracts:
        return None
    start, end = max(contracts)
    return _solidity_function(source[start:end], function_name)


def _is_sha256(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _forge_run_audit(binding: object, put_json_path: Path, source_path: Path, source_sha256: str,
                     test: str) -> bool:
    if not isinstance(binding, dict):
        return False
    relative = Path(str(binding.get("record_path") or ""))
    record_path = (put_json_path.parent / relative).resolve()
    try:
        record_path.relative_to(put_json_path.parent.resolve())
        raw = record_path.read_text(encoding="utf-8")
        record = json.loads(raw)
    except (ValueError, OSError, json.JSONDecodeError):
        return False
    if binding.get("record_sha256") != _sha256(raw):
        return False
    if (not isinstance(record, dict) or record.get("schema") != "veriput-exact-forge-run/v1"
            or record.get("returncode") != 0 or record.get("test") != test
            or record.get("source_sha256") != source_sha256):
        return False
    project = Path(str(record.get("project") or "")).resolve()
    suite = os.path.normpath(str(record.get("source") or ""))
    try:
        if (project / suite).resolve() != source_path.resolve():
            return False
    except OSError:
        return False
    command = record.get("command")
    match_test = r"^" + re.escape(test) + r"(\(|$)"
    expected = [
        "forge", "test", "--json", "--match-path",
        str(record.get("source") or ""), "--match-test", match_test
    ]
    if not isinstance(command, list):
        return False
    if test.startswith("test_put_"):
        if (command[:len(expected)] != expected
                or command[len(expected):len(expected) + 1] != ["--fuzz-runs"]
                or len(command) != len(expected) + 2):
            return False
        try:
            if int(command[-1]) < 256:
                return False
        except (TypeError, ValueError):
            return False
    elif command != expected:
        return False
    stdout = record.get("stdout")
    if not isinstance(stdout, str):
        return False
    statuses, _names, suite_failures = forge_json_status_map(stdout)
    matches = [
        status for (candidate_suite, candidate_test), status in statuses.items()
        if os.path.normpath(candidate_suite) == suite and (
            candidate_test == test or candidate_test.startswith(test + "("))
    ]
    return not suite_failures and len(matches) == 1 and matches[0] == "Success"


def _certification_record_audit(subject_dir: Path, expected_sha256: str, identity: tuple,
                                expected_ce_sha256: str) -> bool:
    path = subject_dir / "cert" / "certify-results.jsonl"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    try:
        whole = json.loads(content)
        records = [whole] if isinstance(whole, dict) else whole
    except json.JSONDecodeError:
        records = []
        for line in content.splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    matches = [
        record for record in records if isinstance(record, dict)
        and _sha256(json.dumps(record, sort_keys=True, separators=(",", ":"))) == expected_sha256
    ]
    if len(matches) != 1:
        return False
    record = matches[0]
    if record.get("path_function") != identity[1] or record.get("unit") != identity[2]:
        return False
    details = (record.get("stage2_observed_certified_details") or record.get("certified_details")
               or {})
    detail = details.get(str(identity[3]))
    certified = record.get("certified")
    if isinstance(certified, dict):
        certified_ids = {str(value) for value in certified}
    elif isinstance(certified, list):
        certified_ids = {
            str(value.get("enc") if isinstance(value, dict) else value)
            for value in certified
        }
    else:
        certified_ids = set()
    if (not isinstance(detail, dict) or detail.get("verdict") != "CERTIFIED"
            or str(identity[3]) not in certified_ids):
        return False
    actual_piece = "" if detail.get("piece") is None else str(detail.get("piece"))
    expected_piece = str(identity[4])
    piece_matches = (expected_piece == actual_piece
                     or (expected_piece == "" and actual_piece == "1"))
    return (piece_matches and certified_ce_sha256(detail.get("ce") or {}) == expected_ce_sha256)


def _membership_certification_record_audit(subject_dir: Path, expected_sha256: str,
                                           identity: tuple, certificate: dict) -> bool:
    """Bind a membership projection to one local structural certificate row."""
    path = subject_dir / "cert" / "certify-results.jsonl"
    try:
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    except (OSError, StopIteration, json.JSONDecodeError):
        return False
    matches = [
        record for record in records if isinstance(record, dict)
        and _sha256(json.dumps(record, sort_keys=True, separators=(",", ":"))) == expected_sha256
    ]
    if len(matches) != 1:
        return False
    record = matches[0]
    detail = (record.get("certified_details") or {}).get(str(identity[3]))
    fair = certificate.get("fair600_region_proof") or {}
    membership = certificate.get("membership") or {}
    value = (membership.get("coordinates") or {}).get("msg.value") or {}
    actual_piece = "" if (detail or {}).get("piece") is None else str(detail.get("piece"))
    expected_piece = str(identity[4])
    return (record.get("path_function") == identity[1] and record.get("unit") == identity[2]
            and isinstance(detail, dict) and detail.get("verdict") == "CERTIFIED"
            and detail.get("certification_source") == "structural-abi-gate-no-coordinate"
            and fair.get("certification_source") == "structural-abi-gate-no-coordinate"
            and fair.get("certification_record_sha256") == expected_sha256
            and _sha256(json.dumps(detail, sort_keys=True, separators=(",", ":")))
            == fair.get("certified_detail_sha256")
            and str(identity[3]) in {str(key) for key in (record.get("certified") or {})}
            and (actual_piece == expected_piece
                 or (expected_piece == "" and actual_piece == "1"))
            and detail.get("box") == [{
                "name": "msg.value",
                "lo": str((value.get("region") or [None, None])[0]),
                "hi": str((value.get("region") or [None, None])[1]),
                "holes": [],
            }])


def _membership_forge_json_audit(run: object, expected_test: str,
                                 expected_source_sha256: str,
                                 require_unique_source: bool = False) -> bool:
    """Require the persisted Forge JSON to contain exactly one successful test."""
    if not isinstance(run, dict):
        return False
    stdout = run.get("log_tail")
    command = run.get("command")
    source = os.path.normpath(str(run.get("source") or ""))
    expected_match = r"^" + re.escape(expected_test) + r"(\(|$)"
    expected_command = [
        "forge", "test", "--json", "--match-path", source, "--match-test",
        expected_match, "--fuzz-runs", "256"
    ]
    if (not isinstance(stdout, str) or _sha256(stdout) != run.get("log_sha256")
            or run.get("returncode") != 0 or run.get("status") != "Success"
            or run.get("source_sha256") != expected_source_sha256
            or command != expected_command or Path(source).name == ""
            or (require_unique_source and run.get("source_function_count") != 1)):
        return False
    statuses, _names, suite_failures = forge_json_status_map(stdout)
    matches = [
        status for (suite, test), status in statuses.items()
        if os.path.normpath(suite) == source
        and (test == expected_test or test.startswith(expected_test + "("))
    ]
    return not suite_failures and len(statuses) == 1 and matches == ["Success"]


def _membership_replay_anchor_matches(replay_function: tuple[str, str] | None,
                                      anchor_function: tuple[str, str] | None) -> bool:
    """The embedded anchor may change only the retained replay's function name."""
    return (replay_function is not None and anchor_function is not None
            and replay_function[0].strip() == "" and anchor_function[0].strip() == ""
            and replay_function[1] == anchor_function[1])


def _membership_flat_binding(retained_sha256: str, verifier_inputs: object,
                             fair_flat_sha256: str, canonical_flat_sha256: str) -> bool:
    """Require all three executions to use the one verifier-input contract source."""
    if not isinstance(verifier_inputs, list) or len(verifier_inputs) != 1:
        return False
    verifier_sha256 = (verifier_inputs[0] or {}).get("flat_sha256")
    return (isinstance(verifier_sha256, str)
            and verifier_sha256 == retained_sha256 == fair_flat_sha256
            == canonical_flat_sha256)


def _membership_environment_matches(environment: dict, value: int, sender: dict) -> bool:
    """Compare the source-derived replay environment with the certified fixed point."""
    return environment.get("msg.value") == value and environment.get("msg.sender") == sender


def _membership_wall_binding(certificate_wall: object, result_wall: object) -> bool:
    """The sealed case result, not certificate self-report, is the fairness source."""
    numeric = (int, float)
    return (isinstance(certificate_wall, numeric) and not isinstance(certificate_wall, bool)
            and isinstance(result_wall, numeric) and not isinstance(result_wall, bool)
            and math.isfinite(certificate_wall) and math.isfinite(result_wall)
            and certificate_wall == result_wall and 0 <= result_wall <= 600)


def _membership_anchor_strength_audit(row: dict, metadata: dict, identity: tuple,
                                      subject_dir: Path) -> tuple[bool, str]:
    """Audit a retained replay as an exact member of a Fair600 PUT region."""
    relative = Path(str(metadata.get("certificate_path") or ""))
    certificate_path = (subject_dir / relative).resolve()
    try:
        certificate_path.relative_to(subject_dir.resolve())
        raw = certificate_path.read_text(encoding="utf-8")
        certificate = json.loads(raw)
    except (ValueError, OSError, json.JSONDecodeError):
        return False, "missing-membership-certificate"
    internal = dict(certificate)
    internal_sha = internal.pop("certificate_sha256", None)
    certificate_schema = certificate.get("schema")
    setup_materialized = (certificate_schema ==
                          "veriput-fair600-membership-projection-setup-materialized/v1")
    ledger_binding = certificate.get("frozen_obligation_ledger") or {}
    expected_identity_sha = _sha256("\t".join(map(str, identity)))
    if (metadata.get("status") != "embedded"
            or metadata.get("binding") != "veriput-fair600-membership-projection/v1"
            or metadata.get("certificate_file_sha256") != _sha256(raw)
            or internal_sha != _sha256(json.dumps(internal, sort_keys=True, separators=(",", ":")))
            or metadata.get("certificate_sha256") != internal_sha
            or certificate_schema not in {
                "veriput-fair600-membership-projection/v1",
                "veriput-fair600-membership-projection-setup-materialized/v1",
            }
            or certificate.get("identity") != list(identity)
            or metadata.get("identity") != list(identity)
            or (setup_materialized and (
                Path(str(ledger_binding.get("path") or "")).resolve()
                != DEFAULT_LEDGER.resolve()
                or ledger_binding.get("sha256") != _sha256_file(DEFAULT_LEDGER)
                or ledger_binding.get("identity_sha256") != expected_identity_sha))):
        return False, "invalid-membership-certificate"

    methodology = certificate.get("methodology") or {}
    membership = certificate.get("membership") or {}
    coordinates = membership.get("coordinates") or {}
    value = coordinates.get("msg.value") or {}
    sender = coordinates.get("msg.sender") or {}
    sender_fixed = sender.get("fixed_semantics") or {}
    try:
        value_fixed = int(value["fixed"])
        value_lo, value_hi = map(int, value["region"])
        sender_lo, sender_hi = map(int, sender["region"])
        sender_fixed_lo = int(sender_fixed["lower_bound"])
        sender_fixed_hi = int(sender_fixed["upper_bound"])
    except (KeyError, TypeError, ValueError):
        return False, "invalid-membership-region"
    if (methodology != {
            "claim": "membership projection, not recovered original-CE certification",
            "no_per_ce_solver_run": True,
            "fairness_basis": "Fair600 full-case artifact",
    } or membership.get("verdict") != "MEMBER" or membership.get("holes") != {}
            or set(coordinates) != {"msg.sender", "msg.value"}
            or not value_lo <= value_fixed <= value_hi <= 2**256 - 1
            or sender_fixed.get("kind") != "test-contract-address"
            or not sender_lo <= sender_fixed_lo <= sender_fixed_hi <= sender_hi <= 2**160 - 1):
        return False, "invalid-membership-region"

    fair = certificate.get("fair600_region_proof") or {}
    run = fair.get("fair600_run") or {}
    proof = fair.get("proof_strategy") or {}
    required_hashes = (("put_json", "put_json_sha256"),
                       ("put_source", "put_source_sha256"),
                       ("certification_ledger", "certification_ledger_sha256"))
    try:
        fair_hashes_ok = all(_sha256(Path(str(fair[path])).read_text(encoding="utf-8"))
                             == fair[digest] for path, digest in required_hashes)
        fair_put = json.loads(Path(str(fair["put_json"])).read_text(encoding="utf-8"))
        fair_result = json.loads(Path(str(run["result"])).read_text(encoding="utf-8"))
        fair_freeze = json.loads(Path(str(run["freeze"])).read_text(encoding="utf-8"))
        frozen_inputs = fair_freeze.get("inputs") or {}
        frozen_binary = frozen_inputs.get("esbmc") or {}
        frozen_binary_sha = hashlib.sha256(
            Path(str(frozen_binary["path"])).read_bytes()).hexdigest()
        fair_hashes_ok = (fair_hashes_ok
                          and _sha256(Path(str(run["freeze"])).read_text(encoding="utf-8"))
                          == run["freeze_sha256"]
                          and _sha256(Path(str(run["result"])).read_text(encoding="utf-8"))
                          == run["result_sha256"])
    except (KeyError, OSError, json.JSONDecodeError):
        return False, "stale-fair600-membership-evidence"
    fair_rows = [
        item for item in ((fair_result.get("row") or {}).get("valid_tests") or [])
        if item.get("kind") == "put" and item.get("put_json") == fair.get("put_json")
        and item.get("file") == fair.get("put_source") and item.get("test") == fair_put.get("test")
        and item.get("forge_status") == "Success" and item.get("unit") == identity[2]
        and str(item.get("enc")) == identity[3]
        and str(item.get("piece") or "") == str(identity[4])
    ]
    expected_policy = [
        "--solidity-max-tx", "1", "--k-induction", "--enable-forward-condition",
        "--max-k-step", "30"
    ]
    sender_derivation = (fair_put.get("derived_by") or {}).get("region_derivation") or {}
    result_row = fair_result.get("row") or {}
    dataset, subject_name = identity[0].split("/", 1)
    run_binary = result_row.get("esbmc_binary_identity") or {}
    pipeline_files = (result_row.get("pipeline_code_identity") or {}).get("files") or {}
    verifier_inputs = (result_row.get("verifier_input_identity") or {}).get("inputs")
    put_text = str(Path(str(fair.get("put_json") or "")).resolve())
    subject_text = put_text.split(os.sep + "put" + os.sep, 1)
    expected_subject = Path(subject_text[0]) if len(subject_text) == 2 else Path()
    run_text = put_text.split(os.sep + "runs" + os.sep, 1)
    expected_freeze = (Path(run_text[0]) / "fair600-case-freeze.json"
                       if len(run_text) == 2 else Path())
    if (not fair_hashes_ok
            or not _membership_wall_binding(run.get("case_wall_s"),
                                            result_row.get("wall_total_s"))
            or proof.get("kind") != "k-induction"
            or "--k-induction" not in (proof.get("esbmc_args") or [])
            or "--enable-forward-condition" not in (proof.get("esbmc_args") or [])
            or proof.get("solidity_max_tx") != 1
            or fair_put.get("kind") != "put" or fair_put.get("path_function") != identity[1]
            or fair_put.get("unit") != identity[2] or str(fair_put.get("enc")) != identity[3]
            or str(fair_put.get("piece") or "") != str(identity[4])
            or fair_put.get("proof_strategy") != proof
            or fair_put.get("region") != {
                name: item.get("region") for name, item in coordinates.items()
            } or fair_put.get("holes") != {}
            or len(fair_rows) != 1
            or _sha256(json.dumps(fair_rows[0], sort_keys=True, separators=(",", ":")))
            != run.get("result_valid_row_sha256")
            or fair_freeze.get("schema") != "veriput-rq1-noput-fair600-case-freeze/v1"
            or (fair_freeze.get("policy") or {}).get("case_wall_budget_s") != 600
            or (fair_freeze.get("policy") or {}).get("region_proof") != expected_policy
            or list(identity) not in (fair_freeze.get("obligations") or [])
            or result_row.get("benchmark") != dataset
            or result_row.get("subject_id") != subject_name
            or result_row.get("status") != "ok"
            or result_row.get("completion_status") not in {"complete", "budget-exhausted"}
            or result_row.get("strict_case_wall_budget") is not True
            or result_row.get("strict_case_wall_within_cap") is not True
            or (fair_result.get("stale_artifact_adoption") or {}).get("adopted") is not False
            or run_binary.get("sha256") != frozen_binary.get("sha256")
            or frozen_binary_sha != frozen_binary.get("sha256")
            or pipeline_files.get(str(frozen_inputs.get("runner") or ""))
            != frozen_inputs.get("runner_sha256")
            or Path(str(run.get("result") or "")).resolve() != expected_subject / "result.json"
            or Path(str(fair.get("put_source") or "")).resolve().is_relative_to(
                (expected_subject / "put").resolve()) is not True
            or Path(str(fair.get("certification_ledger") or "")).resolve()
            != expected_subject / "cert" / "certify-results.jsonl"
            or Path(str(run.get("freeze") or "")).resolve() != expected_freeze
            or sender_derivation != {
                "kind": "structural-getter-unconstrained-sender",
                "coordinate": "msg.sender",
                "lo": 1,
                "hi": 2**160 - 1,
                "source": "structural-abi-gate-no-coordinate",
                "dependency_check": "unit_env_dependencies == []",
                "unit_parameters": 0,
            }
            or _sha256(json.dumps(sender_derivation, sort_keys=True, separators=(",", ":")))
            != fair.get("sender_derivation_sha256")
            or not _membership_certification_record_audit(
                subject_dir, str(fair.get("certification_record_sha256") or ""), identity,
                certificate)):
        return False, "stale-fair600-membership-evidence"

    retained = certificate.get("retained_replay") or {}
    try:
        replay_source_path = Path(str(retained["source"]))
        replay_source = replay_source_path.read_text(encoding="utf-8")
        manifest_raw = Path(str(retained["manifest"])).read_text(encoding="utf-8")
        flat_raw = (replay_source_path.parents[1] / "src" / "flat.sol").read_text(
            encoding="utf-8")
    except (KeyError, OSError):
        return False, "stale-retained-replay"
    replay_function = _solidity_function(replay_source, str(retained.get("test") or ""))
    replay_span, _replay_reason = _solidity_test_span(replay_source,
                                                     str(retained.get("test") or ""))
    replay_function_source = (replay_source[replay_span[0]:replay_span[1]]
                              if replay_span else None)
    replay_setup = _scoped_solidity_function(replay_source, str(retained.get("test") or ""),
                                             "setUp")
    oracles = retained.get("oracles")
    if (_sha256(replay_source) != retained.get("source_sha256")
            or _sha256(manifest_raw) != retained.get("manifest_sha256")
            or _sha256(flat_raw) != retained.get("flat_source_sha256")
            or replay_function is None or replay_function_source is None
            or _sha256(replay_function_source)
            != retained.get("test_function_sha256") or replay_setup is None
            or _sha256(replay_setup[1]) != retained.get("setup_body_sha256")
            or not isinstance(oracles, list) or len(oracles) != 1
            or _structured_oracle_errors(oracles)
            or _oracle_binding_errors(replay_source, retained["test"], identity[2], oracles)):
        return False, "stale-retained-replay"
    try:
        manifest = json.loads(manifest_raw)
    except json.JSONDecodeError:
        return False, "stale-retained-replay"
    manifest_matches = [
        entry for entry in (manifest.get("entries") or []) if isinstance(entry, dict)
        and _sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")))
        == retained.get("manifest_entry_sha256")
    ]
    manifest_entry = manifest_matches[0] if len(manifest_matches) == 1 else {}
    origin = manifest_entry.get("origin") or {}
    if (len(manifest_matches) != 1
            or manifest_entry.get("schema") != "veriput-rq1-concrete-replay/v1"
            or manifest_entry.get("forge_status") != "Success"
            or not manifest_entry.get("valid_reference_test")
            or manifest_entry.get("test") != retained.get("test")
            or manifest_entry.get("test_sha256") != retained.get("source_sha256")
            or manifest_entry.get("flat_sha256") != retained.get("flat_source_sha256")
            or manifest_entry.get("concrete_oracles") != oracles
            or origin.get("path_function") != identity[1] or origin.get("unit") != identity[2]
            or str(origin.get("enc")) != identity[3]
            or str(origin.get("piece") or "") != str(identity[4])):
        return False, "stale-retained-replay"
    try:
        from rq1_membership_projection import _fixed_environment  # pylint: disable=import-outside-toplevel
        replay_environment = _fixed_environment(replay_function_source, identity[2], oracles[0])
    except (KeyError, TypeError, ValueError):
        return False, "replay-membership-mismatch"
    if not _membership_environment_matches(replay_environment, value_fixed, sender_fixed):
        return False, "replay-membership-mismatch"

    source_path = Path(str(row.get("file") or ""))
    put_json_path = Path(str(row.get("put_json") or ""))
    try:
        source = source_path.read_text(encoding="utf-8")
        put_json = json.loads(put_json_path.read_text(encoding="utf-8"))
        fair_source_path = Path(str(fair["put_source"]))
        fair_source = fair_source_path.read_text(encoding="utf-8")
        fair_project = next(parent for parent in fair_source_path.parents
                            if (parent / "foundry.toml").is_file())
        canonical_project = next(parent for parent in source_path.parents
                                 if (parent / "foundry.toml").is_file())
        fair_flat_sha = _sha256((fair_project / "src" / "flat.sol").read_text(
            encoding="utf-8"))
        canonical_flat_sha = _sha256((canonical_project / "src" / "flat.sol").read_text(
            encoding="utf-8"))
    except (OSError, StopIteration, json.JSONDecodeError):
        return False, "missing-membership-put"
    anchor_test = str(metadata.get("test") or "")
    anchor = _solidity_function(source, anchor_test)
    anchor_span, _anchor_reason = _solidity_test_span(source, anchor_test)
    anchor_source = source[anchor_span[0]:anchor_span[1]] if anchor_span else None
    put_function = _solidity_function(source, str(row.get("test") or ""))
    setup = _scoped_solidity_function(source, str(row.get("test") or ""), "setUp")
    source_sha = _sha256(source)
    setup_projection = certificate.get("setup_projection")
    setup_materialization = (certificate.get("forge_gate")
                             or {}).get("setup_materialization")
    if setup_materialized:
        try:
            from rq1_membership_projection import (  # pylint: disable=import-outside-toplevel
                _exact_prefunding, _renamed_function, _require_plain_public_function,
                _setup_target_deployment, _source_function_any)
            fair_put_function = _solidity_function(fair_source, str(row.get("test") or ""))
            fair_setup = _scoped_solidity_function(fair_source, str(row.get("test") or ""),
                                                   "setUp")
            replay_named = _source_function_any(replay_source, retained["test"])[0]
            replay_setup_named = _source_function_any(replay_source, "setUp")[0]
            fair_put_named = _source_function_any(fair_source, str(row.get("test") or ""))[0]
            fair_setup_named = _source_function_any(fair_source, "setUp")[0]
            for function_source, function_name in (
                    (replay_named, retained["test"]), (replay_setup_named, "setUp"),
                    (fair_put_named, str(row.get("test") or "")),
                    (fair_setup_named, "setUp")):
                _require_plain_public_function(function_source, function_name)
            renamed_replay = _renamed_function(replay_named, retained["test"], anchor_test)
            contract_match = re.search(r"@C@([^@]+)@F@", identity[1])
            if contract_match is None:
                return False, "invalid-membership-setup-materialization"
            target_contract = contract_match.group(1)
            target_receiver = str(oracles[0].get("target_receiver") or "")
            replay_deployment = _setup_target_deployment(replay_setup[1], target_receiver,
                                                         target_contract)
            put_deployment = _setup_target_deployment(fair_setup[1], target_receiver,
                                                      target_contract)
            replay_prefunding = _exact_prefunding(replay_named, value_fixed)
        except (KeyError, TypeError, ValueError):
            return False, "invalid-membership-setup-materialization"
        expected_projection = {
            "kind": "materialized-structural-abi-value-gate",
            "verdict": "SETUP-UNOBSERVABLE-BEFORE-DISPATCH",
            "reason": ("the certified nonpayable ABI value gate rejects positive msg.value "
                       "before function-body state can be observed"),
            "replay_setup_body_sha256": _sha256(replay_setup[1]),
            "put_setup_body_sha256": _sha256(fair_setup[1]) if fair_setup else None,
            "replay_target_deployment": replay_deployment,
            "put_target_deployment": put_deployment,
            "replay_prefunding": replay_prefunding,
        }
        expected_anchor = ((replay_function[0], replay_setup[1] + replay_function[1])
                           if replay_function else None)
        expected_put = ((fair_put_function[0], fair_setup[1] + fair_put_function[1])
                        if fair_put_function and fair_setup else None)
        expected_materialization = {
            "global_setup": "empty",
            "put_test_setup_body_sha256": _sha256(fair_setup[1]) if fair_setup else None,
            "anchor_test_setup_body_sha256": _sha256(replay_setup[1]),
            "validation_anchor_function_sha256": _sha256(
                source[anchor_span[0]:anchor_span[1]]) if anchor_span else None,
        }
        setup_binding_ok = (
            value_fixed > 0 and setup_projection == expected_projection
            and setup_materialization == expected_materialization
            and setup is not None and setup[1].strip() == ""
            and anchor == expected_anchor and put_function == expected_put
            and certificate.get("anchor", {}).get("function_sha256") == _sha256(anchor_source or "")
            and certificate.get("anchor", {}).get("retained_replay_renamed_function_sha256")
            == _sha256(renamed_replay))
    else:
        setup_binding_ok = (setup_projection is None and setup_materialization is None
                            and setup is not None
                            and _sha256(setup[1]) == retained.get("setup_body_sha256")
                            and _membership_replay_anchor_matches(replay_function, anchor)
                            and _sha256(anchor_source or "")
                            == certificate.get("anchor", {}).get("function_sha256"))
    if (not anchor_test.startswith("test_ce_membership_") or anchor is None
            or anchor_source is None or put_function is None or setup is None
            or not setup_binding_ok
            or _oracle_binding_errors(source, anchor_test, identity[2], oracles)
            or source_sha != certificate.get("forge_gate", {}).get("validation_source_sha256")
            or not _membership_flat_binding(retained["flat_source_sha256"], verifier_inputs,
                                            fair_flat_sha, canonical_flat_sha)
            or put_json.get("ce_anchor") != metadata):
        return False, "membership-anchor-body-mismatch"
    certificate_gate = certificate.get("forge_gate") or {}
    original_run = certificate_gate.get("original_replay_run")
    if not _membership_forge_json_audit(original_run, retained["test"],
                                        retained["source_sha256"], setup_materialized):
        return False, "invalid-membership-original-forge"
    if (certificate_gate.get("status") != "Success"
            or certificate_gate.get("strict_certificate") is not True
            or not _membership_forge_json_audit(certificate_gate.get("put_run"),
                                                str(row.get("test") or ""), source_sha,
                                                setup_materialized)
            or not _membership_forge_json_audit(certificate_gate.get("anchor_run"), anchor_test,
                                                source_sha, setup_materialized)):
        return False, "invalid-membership-validation-forge"

    gate = metadata.get("forge_gate")
    if (not isinstance(gate, dict) or gate.get("schema") != "veriput-put-anchor-forge-gate/v1"
            or gate.get("put_test") != row.get("test") or gate.get("anchor_test") != anchor_test
            or gate.get("put_status") != "Success" or gate.get("anchor_status") != "Success"
            or gate.get("source_sha256") != source_sha
            or not _forge_run_audit(gate.get("put_run"), put_json_path, source_path, source_sha,
                                    str(row.get("test") or ""))
            or not _forge_run_audit(gate.get("anchor_run"), put_json_path, source_path, source_sha,
                                    anchor_test)):
        return False, "invalid-membership-double-forge"
    return True, "strength-confirmed"


def _report_binding_audit(subject_dir: Path, binding: dict, identity: tuple,
                          basis_source_sha256: str, certified_ce_sha256_: str) -> bool:
    if binding.get("kind") == "structural-abi-gate-certified-projection":
        relative = Path(str(binding.get("basis_put_json_path") or ""))
        path = (subject_dir / relative).resolve()
        try:
            path.relative_to(subject_dir.resolve())
            raw = path.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (ValueError, OSError, json.JSONDecodeError):
            return False
        certified_binding = document.get("certified_ce_binding")
        projection = (certified_binding.get("source_projection_preserved")
                      if isinstance(certified_binding, dict) else None)
        projection_sha256 = _sha256(
            json.dumps(projection, sort_keys=True, separators=(",", ":")))
        coordinates = ((projection or {}).get("coordinate_binding") or {}).get(
            "coordinates") or {}
        coordinate_binding = (projection or {}).get("coordinate_binding") or {}
        value = coordinates.get("msg.value") or {}
        source_path = Path(str(document.get("file") or ""))
        try:
            source_matches = _sha256(source_path.read_text(
                encoding="utf-8")) == basis_source_sha256
        except OSError:
            source_matches = False
        return (binding.get("certification_source") == "structural-abi-gate-no-coordinate"
                and binding.get("claim_exit_kind") == "revert"
                and binding.get("claim_return_value") is None
                and binding.get("basis_put_json_sha256") == _sha256(raw)
                and binding.get("source_projection_sha256") == projection_sha256
                and document.get("certification_source") == "structural-abi-gate-no-coordinate"
                and isinstance(certified_binding, dict)
                and certified_binding.get("status") == "exact"
                and certified_binding.get("projection_certificate")
                == "abi-value-gate-before-body/v1"
                and certified_binding.get("rendered_source_verified") is True
                and certified_binding.get("ce_sha256") == certified_ce_sha256_
                and certified_binding.get("rendered_source_ce_sha256")
                == certified_ce_sha256_
                and isinstance(projection, dict)
                and projection.get("schema") == "veriput-certified-ce-source-projection/v1"
                and projection.get("ce_sha256") == certified_ce_sha256_
                and coordinate_binding.get("schema")
                == "veriput-certified-ce-source-binding/v1"
                and coordinate_binding.get("ce_sha256") == certified_ce_sha256_
                and document.get("path_function") == identity[1]
                and document.get("unit") == identity[2]
                and str(document.get("enc")) == identity[3]
                and ("" if document.get("piece") is None else str(document.get("piece")))
                in (str(identity[4]), "1" if str(identity[4]) == "" else str(identity[4]))
                and value.get("kind") == "call-environment-literal"
                and value.get("certified") == 1 and value.get("rendered") == 1
                and value.get("source") == "{value: 1}" and source_matches)
    relative = Path(str(binding.get("cov_report_path") or ""))
    path = (subject_dir / relative).resolve()
    try:
        path.relative_to(subject_dir.resolve())
        raw = path.read_text(encoding="utf-8")
        report = json.loads(raw)
    except (ValueError, OSError, json.JSONDecodeError):
        return False
    if binding.get("cov_report_sha256") != _sha256(raw):
        return False
    matching_claims = []
    for claim in report.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        digest = _sha256(json.dumps(claim, sort_keys=True, separators=(",", ":")))
        if digest == binding.get("claim_sha256"):
            matching_claims.append(claim)
    if len(matching_claims) != 1:
        return False
    claim = matching_claims[0]
    if not (claim.get("path_function") == identity[1] and str(claim.get("path_id")) == identity[3]
            and claim.get("exit_kind") == binding.get("claim_exit_kind")
            and claim.get("return_value") == binding.get("claim_return_value")):
        return False
    kind = binding.get("kind")
    if kind == "legacy-adjacent-claim":
        emitted_sha = binding.get("emitted_source_sha256")
        if (not _is_sha256(emitted_sha) or binding.get("basis_source_sha256") != basis_source_sha256
                or emitted_sha == basis_source_sha256):
            return False
        emitted_sources = []
        try:
            emitted_sources = [
                _sha256(candidate.read_text(encoding="utf-8"))
                for candidate in path.parent.glob("*.cov.t.sol") if candidate.is_file()
            ]
        except OSError:
            return False
        return emitted_sources.count(emitted_sha) == 1
    if kind == "certified-source-projection":
        relative = Path(str(binding.get("basis_put_json_path") or ""))
        basis_path = (subject_dir / relative).resolve()
        try:
            basis_path.relative_to(subject_dir.resolve())
            basis_raw = basis_path.read_text(encoding="utf-8")
            document = json.loads(basis_raw)
        except (ValueError, OSError, json.JSONDecodeError):
            return False
        certified_binding = document.get("certified_ce_binding") or {}
        projection = certified_binding.get("source_projection_preserved")
        coordinate_binding = ((projection or {}).get("coordinate_binding") or {})
        coordinates = coordinate_binding.get("coordinates") or {}
        proof = coordinates.get("callee") or {}
        basis_file = Path(str(document.get("file") or ""))
        projection_sha256 = _sha256(json.dumps(
            projection, sort_keys=True, separators=(",", ":")))
        return (binding.get("basis_put_json_sha256") == _sha256(basis_raw)
                and binding.get("source_projection_sha256") == projection_sha256
                and binding.get("projection_certificate") ==
                "strict-low-level-call-fixture/v1"
                and certified_binding.get("status") == "exact"
                and certified_binding.get("projection_certificate") ==
                "strict-low-level-call-fixture/v1"
                and certified_binding.get("rendered_source_verified") is True
                and certified_binding.get("ce_sha256") == certified_ce_sha256_
                and certified_binding.get("rendered_source_ce_sha256") ==
                certified_ce_sha256_
                and isinstance(projection, dict)
                and projection.get("schema") ==
                "veriput-certified-ce-source-projection/v1"
                and projection.get("ce_sha256") == certified_ce_sha256_
                and coordinate_binding.get("schema") ==
                "veriput-certified-ce-source-binding/v1"
                and coordinate_binding.get("ce_sha256") == certified_ce_sha256_
                and document.get("path_function") == identity[1]
                and document.get("unit") == identity[2]
                and str(document.get("enc")) == identity[3]
                and ("" if document.get("piece") is None else str(document.get("piece")))
                in (str(identity[4]), "1" if str(identity[4]) == "" else str(identity[4]))
                and _sha256_file(basis_file) == basis_source_sha256
                and _strict_extcall_source_projection_error(
                    basis_file, str(identity[2]), proof) is None)
    if kind in (None, "solver-witness-fingerprint"):
        return (claim.get("foundry_testcase_fingerprint_sha256") == binding.get(
            "solver_witness_fingerprint_sha256"))
    return False


def _anchor_strength_audit(row: dict,
                           identity: tuple | None = None,
                           subject_dir: Path | None = None) -> tuple[bool, str]:
    """Accept only an exact, source-bound CE anchor with a double-green gate."""
    metadata = row.get("ce_anchor")
    if not isinstance(metadata, dict):
        return False, "missing-ce-anchor"
    if metadata.get("binding") == "fair-rerun-rq3-closure/v1":
        if identity is None or list(identity) != metadata.get("identity"):
            return False, "fair-rerun-identity-mismatch"
        source_path = Path(str(row.get("file") or ""))
        record_path = Path(str(metadata.get("membership_record") or ""))
        try:
            source = source_path.read_text(encoding="utf-8")
            record_raw = record_path.read_text(encoding="utf-8")
            record = json.loads(record_raw)
        except (OSError, json.JSONDecodeError):
            return False, "missing-fair-rerun-membership"
        if (hashlib.sha256(record_path.read_bytes()).hexdigest() !=
                metadata.get("membership_record_sha256")
                or record.get("schema") != "rq1-fair-rerun-rq3-membership/v1"
                or record.get("identity") != list(identity)
                or _sha256(source) != metadata.get("destination_source_sha256")
                or record.get("source_sha256") != metadata.get("destination_source_sha256")
                or _solidity_function(source, str(metadata.get("destination_put_test") or ""))
                is None
                or _solidity_function(source, str(metadata.get("test") or "")) is None
                or row.get("forge_status") != "Success"
                or row.get("ce_anchor_forge_status") != "Success"):
            return False, "invalid-fair-rerun-membership"
        for key, test in (("put_run", metadata.get("destination_put_test")),
                          ("anchor_run", metadata.get("test"))):
            run = record.get(key) or {}
            statuses, _names, failures = forge_json_status_map(str(run.get("stdout") or ""))
            selected = [status for (_suite, name), status in statuses.items()
                        if name == test or name.startswith(str(test) + "(")]
            if (run.get("returncode") != 0 or run.get("success") is not True or failures
                    or selected != ["Success"]):
                return False, "fair-rerun-forge-gate-mismatch"
        return True, "strength-confirmed"
    if metadata.get("binding") == "structural-abi-getter/v1":
        if identity is None:
            return False, "missing-structural-getter-identity"
        expected_piece = "" if row.get("piece") is None else str(row.get("piece"))
        if (str(row.get("path_function") or "") != str(identity[1])
                or str(row.get("unit") or "") != str(identity[2])
                or str(row.get("enc")) != str(identity[3])
                or expected_piece != str(identity[4])
                or metadata.get("status") != "embedded"
                or metadata.get("basis_kind") != "structural-certificate-not-solver-ce"
                or metadata.get("certification_source") !=
                "structural-abi-getter-no-coordinate"):
            return False, "structural-getter-identity-mismatch"
        source_path = Path(str(row.get("file") or ""))
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError:
            return False, "missing-structural-getter-source"
        put_test = str(row.get("test") or "")
        anchor_test = str(metadata.get("test") or "")
        put_function = _solidity_function(source, put_test)
        anchor_function = _solidity_function(source, anchor_test)
        put_spans = _solidity_function_spans(source, put_test)
        put_source = (source[put_spans[0][0][0]:put_spans[0][0][1]]
                      if len(put_spans) == 1 and put_spans[0][0] is not None else None)
        fixed_arguments = metadata.get("fixed_arguments")
        region = metadata.get("region") or {}
        sender_region = region.get("msg.sender")
        gate = metadata.get("forge_gate") or {}
        if (metadata.get("destination_put_test") != put_test
                or put_function is None or anchor_function is None
                or anchor_function[0].strip()
                or not isinstance(fixed_arguments, list) or not fixed_arguments
                or sender_region != [1, 2**160 - 1]
                or gate.get("put_test") != put_test
                or gate.get("anchor_test") != anchor_test
                or gate.get("put_status") != "Success"
                or gate.get("anchor_status") != "Success"
                or row.get("forge_status") != "Success"
                or row.get("ce_anchor_forge_status") != "Success"
                or _sha256(source) != metadata.get("destination_source_sha256")
                or put_source is None or _sha256(put_source) !=
                metadata.get("destination_put_function_sha256")
                or not re.search(r"\bthis\." + re.escape(put_test) + r"\s*\(",
                                 anchor_function[1])):
            return False, "invalid-structural-getter-anchor"
        suite_path = Path(str(row.get("put_json") or "")).parent / str(
            gate.get("suite_log") or "")
        try:
            suite_raw = suite_path.read_text(encoding="utf-8")
        except OSError:
            return False, "missing-structural-getter-forge-suite"
        if _sha256(suite_raw) != gate.get("suite_log_sha256"):
            return False, "structural-getter-forge-suite-mismatch"
        statuses, _names, failures = forge_json_status_map(suite_raw)
        matched = {
            test: status for (_suite, test), status in statuses.items()
            if test in {put_test, anchor_test}
        }
        if failures or matched != {put_test: "Success", anchor_test: "Success"}:
            return False, "structural-getter-forge-gate-mismatch"
        return True, "strength-confirmed"
    if metadata.get("binding") == "source-grounded-createcall/v1":
        error = _source_grounded_createcall_basis_error(row)
        return (False, error) if error else (True, "strength-confirmed")
    if metadata.get("binding") == "veriput-fair600-membership-projection/v1":
        if identity is None or subject_dir is None:
            return False, "missing-canonical-subject"
        return _membership_anchor_strength_audit(row, metadata, identity, subject_dir)
    if metadata.get("binding") == "rq3-exact-source-grounded-constructor/v1":
        return _source_grounded_constructor_anchor_audit(row, metadata, identity)
    if (metadata.get("status") != "embedded"
            or metadata.get("binding") != "certified-exact-basis/v1"):
        return False, "invalid-anchor-provenance"
    if identity is None:
        identity = tuple(metadata.get("identity") or ())
    if list(identity) != metadata.get("identity"):
        return False, "anchor-identity-mismatch"
    if any(not _is_sha256(metadata.get(field))
           for field in ("evidence_sha256", "certified_ce_sha256", "basis_source_sha256",
                         "basis_setup_sha256", "basis_test_body_sha256",
                         "certification_record_sha256")):
        return False, "incomplete-anchor-provenance"
    report_binding = metadata.get("report_binding")
    if (not isinstance(report_binding, dict)
            or report_binding.get("claim_exit_kind") not in ("normal", "revert")):
        return False, "invalid-report-binding"
    binding_kind = report_binding.get("kind")
    if binding_kind == "structural-abi-gate-certified-projection":
        if (report_binding.get("certification_source")
                != "structural-abi-gate-no-coordinate"
                or not _is_sha256(report_binding.get("basis_put_json_sha256"))
                or not _is_sha256(report_binding.get("source_projection_sha256"))):
            return False, "invalid-report-binding"
    elif (not _is_sha256(report_binding.get("cov_report_sha256"))
          or not _is_sha256(report_binding.get("claim_sha256"))):
        return False, "invalid-report-binding"
    elif binding_kind == "legacy-adjacent-claim":
        if (not _is_sha256(report_binding.get("emitted_source_sha256"))
                or report_binding.get("basis_source_sha256") != metadata.get("basis_source_sha256")
                or report_binding.get("emitted_source_sha256")
                == metadata.get("basis_source_sha256")):
            return False, "invalid-report-binding"
    elif binding_kind == "certified-source-projection":
        if (not _is_sha256(report_binding.get("basis_put_json_sha256"))
                or not _is_sha256(report_binding.get("source_projection_sha256"))
                or report_binding.get("projection_certificate") !=
                "strict-low-level-call-fixture/v1"):
            return False, "invalid-report-binding"
    elif binding_kind in (None, "solver-witness-fingerprint"):
        if not _is_sha256(report_binding.get("solver_witness_fingerprint_sha256")):
            return False, "invalid-report-binding"
    else:
        return False, "invalid-report-binding"
    if subject_dir is None:
        return False, "missing-canonical-subject"
    if not _certification_record_audit(subject_dir, metadata["certification_record_sha256"],
                                       identity, metadata["certified_ce_sha256"]):
        return False, "certification-record-mismatch"
    if not _report_binding_audit(subject_dir, report_binding, identity,
                                 metadata["basis_source_sha256"],
                                 metadata["certified_ce_sha256"]):
        return False, "report-binding-mismatch"
    oracles = metadata.get("oracles")
    if (_structured_oracle_errors(oracles)
            or any(oracle.get("provenance") != "stage2-witness" for oracle in oracles)):
        return False, "invalid-anchor-oracles"
    evidence = {
        "schema": "veriput-certified-ce-anchor-evidence/v1",
        "identity": list(identity),
        "certification_record_sha256": metadata["certification_record_sha256"],
        "certified_ce_sha256": metadata["certified_ce_sha256"],
        "basis_source_sha256": metadata["basis_source_sha256"],
        "basis_setup_sha256": metadata["basis_setup_sha256"],
        "basis_test_body_sha256": metadata["basis_test_body_sha256"],
        "oracles": oracles,
        "report_binding": report_binding,
    }
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    if metadata.get("evidence_sha256") != _sha256(canonical):
        return False, "anchor-evidence-hash-mismatch"

    source_path = Path(str(row.get("file") or ""))
    try:
        source = source_path.read_text(encoding="utf-8")
    except OSError:
        return False, "missing-put-source"
    anchor_test = str(metadata.get("test") or "")
    function = _solidity_function(source, anchor_test)
    if not anchor_test.startswith("test_ce_anchor_") or function is None:
        return False, "missing-anchor-body"
    params, body = function
    if params.strip():
        return False, "non-concrete-anchor"
    if (_oracle_binding_errors(source, anchor_test, str(row.get("unit") or ""), oracles)):
        return False, "anchor-oracle-binding-failed"
    destination = metadata.get("destination")
    put_function = _solidity_function(source, str(row.get("test") or ""))
    setup_function = _scoped_solidity_function(source, str(row.get("test") or ""), "setUp")
    anchor_span, _reason = _solidity_test_span(source, anchor_test)
    anchor_source = source[anchor_span[0]:anchor_span[1]] if anchor_span else None
    if (not isinstance(destination, dict) or destination.get("anchor_body_sha256") != _sha256(body)
            or anchor_source is None
            or destination.get("anchor_function_sha256") != _sha256(anchor_source)
            or destination.get("source_after_sha256") != _sha256(source) or put_function is None
            or destination.get("put_body_before_sha256") != _sha256(put_function[1])
            or destination.get("put_body_after_sha256") != _sha256(put_function[1])
            or setup_function is None
            or destination.get("setup_body_sha256") != _sha256(setup_function[1])
            or metadata.get("basis_setup_sha256") != _sha256(setup_function[1])):
        return False, "anchor-body-hash-mismatch"

    gate = metadata.get("forge_gate")
    if (not isinstance(gate, dict) or gate.get("schema") != "veriput-put-anchor-forge-gate/v1"
            or gate.get("put_test") != row.get("test") or gate.get("anchor_test") != anchor_test
            or gate.get("put_status") != "Success" or gate.get("anchor_status") != "Success"
            or gate.get("source_sha256") != _sha256(source)):
        return False, "missing-or-stale-double-forge-gate"

    put_json_path = Path(str(row.get("put_json") or ""))
    try:
        put_json = json.loads(put_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "missing-or-corrupt-put-json"
    if put_json.get("ce_anchor") != metadata:
        return False, "anchor-metadata-mismatch"
    if (not _forge_run_audit(gate.get("put_run"), put_json_path, source_path, _sha256(source),
                             str(row.get("test") or ""))
            or not _forge_run_audit(gate.get("anchor_run"), put_json_path, source_path,
                                    _sha256(source), anchor_test)):
        return False, "invalid-double-forge-evidence"
    return True, "strength-confirmed"


_SOURCE_GROUNDED_CONSTRUCTOR_PROOFS = {
    "3c3b901e0836c87c5446cbee754013c7cf87b8e47fd7c7d72226438ca213c933": {
        "identity": ["peer182/peer_soltg__constructor_state_variable_init", "",
                     "__deploy__", "0", ""],
        "contract": "Cv1",
        "put_test": "test_put_Cv1_constructor_revert",
        "anchor_test": "test_ce_anchor_Cv1_constructor_revert",
        "basis_test": "test_cov_Cv1_constructor_revert",
        "basis_source": "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/peer182/subjects/peer_soltg__constructor_state_variable_init/put/deploy_only/Project/test/Cv1DeployOnlyCovTest.t.sol",
        "basis_source_sha256": "73545333948fb5f5541f77ec377985245ee7211bd88c8ec1a14bc2b0c0812b33",
        "basis_record": "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/peer182/subjects/peer_soltg__constructor_state_variable_init/put/deploy_only/_wd/deploy_only/put.json",
        "basis_record_sha256": "55cdbb086fd070430bb604fe56c88cb8a1e86b89807a76da32d5f3ee39a6730c",
        "region": {"a": ["1", str((1 << 255) - 1)]},
        "source_sha256": "ad14d203382a2f3eafcf5d07bb2b5b2995deac4c85264ac77e2dd06eef5d6cc0",
        "put_function_sha256": "c2b00f33f32ba21065dc3e27e8c4e03ef0ad02b2354f44d80bd130e2e85c9b22",
        "put_body_sha256": "637453dc1e006586a7d880bc1ee6a202402d7c02d16f9ead37fa3f2eea04874c",
        "anchor_function_sha256": "bca69cbfab0f169dc18a3f22b8b5a33b87aea2b33e44f80a906f0862fa8141e0",
        "anchor_body_sha256": "bd7356068ac2a1d602ba3adabced7a52074a20a1191815496212f9f4ca5cee9c",
        "concrete_ce_sha256": "1319bed311a18d6e5b781daef450b055b11b0442c32f979028068a64e3466931",
        "evidence_sha256": "855468023e4ac262e037b1cd83e70309437a708d5d7f74a060efac952659f2af",
    },
    "a1aebed7f0ef5ea1b49376150cd0ba2ac2976fbd6ebf91e493b8af4fb9906836": {
        "identity": ["peer182/peer_soltg__constructor_state_variable_init_chain_alternate", "",
                     "__deploy__", "0", ""],
        "contract": "Dv2",
        "put_test": "test_put_Dv2_constructor_revert",
        "anchor_test": "test_ce_anchor_Dv2_constructor_revert",
        "basis_test": "test_cov_Dv2_constructor_revert",
        "basis_source": "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/peer182/subjects/peer_soltg__constructor_state_variable_init_chain_alternate/put/deploy_only/Project/test/Dv2DeployOnlyCovTest.t.sol",
        "basis_source_sha256": "e82a3eb16ab1cae23e52605dd92eb53a43777c2894034cc856d0d3aa3e81cd8a",
        "basis_record": "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/peer182/subjects/peer_soltg__constructor_state_variable_init_chain_alternate/put/deploy_only/_wd/deploy_only/put.json",
        "basis_record_sha256": "6b2011bb8b3718271b3783ff7a4e411456a61a53c2d2a0022a0f0938c542d5f8",
        "region": {"a": [str(-(1 << 255)), str((1 << 255) - 1)]},
        "source_sha256": "1d8c89bc060499d6f2f531c35d26275148e40df95654cc155dd2d7c45aa0a2b2",
        "put_function_sha256": "5ae1812b5db550f5364e0b8f8f2d47ff41cf373d9d96e06fc7e64607146bed54",
        "put_body_sha256": "394156932aad78d252f3f71698bd3f7c00520422acf93275f2b45281df3aaf98",
        "anchor_function_sha256": "751f43e185d85e5a9fd14a78cf7df5a07ccbe1bfd51221a210965530f4973eb9",
        "anchor_body_sha256": "195e3a135489fc577dfd3f4c6e1ce0270a60cf30de35af82768291708286d683",
        "concrete_ce_sha256": "bfd606e5b6a79ef9f448b22199796f196ac892251e6351d385663195da7daa34",
        "evidence_sha256": "1ab6198a70a8f0653735dd9c5fe9065085c1280aeb2517361e9028855b120f8a",
    },
    "30e7193b2425b777f0de82a6d9f940163ff122882fb49ca09585b5a9f9e21556": {
        "identity": ["peer182/peer_soltg__constructors", "", "__deploy__", "0", ""],
        "contract": "Ccs",
        "put_test": "test_put_Ccs_constructor_revert",
        "anchor_test": "test_ce_anchor_Ccs_constructor_revert",
        "basis_test": "test_cov_Ccs_constructor_revert",
        "basis_source": "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/peer182/subjects/peer_soltg__constructors/put/deploy_only/Project/test/CcsDeployOnlyCovTest.t.sol",
        "basis_source_sha256": "1a737e6e240f3be314ffcb281f2303e1a356a598b1b4a09e28b6eae23e6d87ea",
        "basis_record": "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg/peer182/subjects/peer_soltg__constructors/put/deploy_only/_wd/deploy_only/put.json",
        "basis_record_sha256": "27aaead9190a03add84840c52e9ae3be1105190e7f18d8e5781d9f205fde1914",
        "region": {"a": [str(-(1 << 255)), str((1 << 255) - 1)]},
        "source_sha256": "b9300c6407cdbd155fff4208239cb042f1e247839ea6b78d477141100225c5de",
        "put_function_sha256": "aea5baddc8dcd2a670970b371744b6e2ba3cb134512c34f2f560243e6e6d3668",
        "put_body_sha256": "0cce903d245e452da1b8edadeba7f1ce99e93fb779e4ac2614288f28709d1859",
        "anchor_function_sha256": "ebd187ab1d9c4d0f5f17190b06644b1c07831be5991652841291ecc828795d00",
        "anchor_body_sha256": "31def810b3fbee9d6bc9a5655b2ddc9bbf51017f5209731b1e64637021b1efb7",
        "concrete_ce_sha256": "7d903ed79dc5c7efd08a44d29c56bb2aede85241a26477ae851714d59df88919",
        "evidence_sha256": "13a5097f92190d85fac68ce2731b4258892ca59e64e571501261af7c2e3ce322",
    },
}


def _source_grounded_constructor_anchor_audit(row: dict, metadata: dict,
                                                identity: tuple | None) -> tuple[bool, str]:
    """Validate the three reviewed constructor-only, source-grounded PUTs."""
    source_proof = row.get("source_proof") or {}
    proof = _SOURCE_GROUNDED_CONSTRUCTOR_PROOFS.get(source_proof.get("flat_source_sha256"))
    if proof is None:
        return False, "unreviewed-constructor-source"
    if (metadata.get("status") != "embedded" or identity is None
            or list(identity) != proof["identity"]
            or metadata.get("identity") != list(identity)
            or row.get("contract") != proof["contract"]
            or row.get("unit") != "__deploy__" or str(row.get("enc")) != "0"
            or row.get("test") != proof["put_test"]
            or metadata.get("test") != proof["anchor_test"]
            or metadata.get("basis_test") != proof["basis_test"]
            or row.get("region") != proof["region"]):
        return False, "constructor-identity-or-region-mismatch"
    stats = row.get("stats") or {}
    materialization = row.get("materialization") or {}
    if (row.get("kind") != "put" or materialization.get("is_put") is not True
            or stats.get("fuzz_params") != 1 or stats.get("oracle_classes") != ["R0"]
            or source_proof.get("oracle_classes") != ["R0"]):
        return False, "invalid-constructor-put-classification"

    basis_source_path = Path(str(metadata.get("basis_source") or ""))
    basis_record_path = Path(str(metadata.get("basis_record") or ""))
    if (basis_source_path.resolve() != Path(proof["basis_source"]).resolve()
            or basis_record_path.resolve() != Path(proof["basis_record"]).resolve()
            or metadata.get("basis_source_sha256") != proof["basis_source_sha256"]
            or metadata.get("basis_record_sha256") != proof["basis_record_sha256"]):
        return False, "constructor-basis-mismatch"

    source_path = Path(str(row.get("file") or ""))
    put_json_path = Path(str(row.get("put_json") or ""))
    try:
        source = source_path.read_text(encoding="utf-8")
        put_document = json.loads(put_json_path.read_text(encoding="utf-8"))
        basis_source = basis_source_path.read_text(encoding="utf-8")
        basis_record = json.loads(basis_record_path.read_text(encoding="utf-8"))
        flat_source = source_path.parent.parent / "src" / "flat.sol"
    except (OSError, json.JSONDecodeError):
        return False, "missing-constructor-evidence"
    source_sha256 = _sha256(source)
    if (_sha256_file(flat_source) != source_proof.get("flat_source_sha256")
            or _sha256_file(basis_source_path) != proof["basis_source_sha256"]
            or _sha256_file(basis_record_path) != proof["basis_record_sha256"]
            or basis_record.get("kind") != "concrete"
            or basis_record.get("unit") != "__deploy__"
            or str(basis_record.get("enc")) != "0"
            or basis_record.get("test") != metadata.get("basis_test")
            or basis_record.get("forge_status") != "Success"
            or basis_record.get("valid_reference_test") is not True):
        return False, "constructor-basis-mismatch"

    basis_function = _solidity_function(basis_source, metadata["basis_test"])
    anchor_function = _solidity_function(source, metadata["test"])
    put_function = _solidity_function(source, str(row.get("test") or ""))
    destination = metadata.get("destination") or {}
    if (basis_function is None or anchor_function is None or put_function is None
            or anchor_function[0].strip() or basis_function[0].strip()
            or _sha256(basis_function[1]) != metadata.get("basis_test_body_sha256")
            or _sha256(anchor_function[1]) != metadata.get("basis_test_body_sha256")
            or source_sha256 != proof["source_sha256"]
            or _sha256(put_function[1]) != proof["put_body_sha256"]
            or _sha256(anchor_function[1]) != proof["anchor_body_sha256"]
            or destination.get("source_sha256") != proof["source_sha256"]
            or destination.get("put_function_sha256") != proof["put_function_sha256"]
            or destination.get("put_body_sha256") != proof["put_body_sha256"]
            or destination.get("anchor_function_sha256") != proof["anchor_function_sha256"]
            or destination.get("anchor_body_sha256") != proof["anchor_body_sha256"]):
        return False, "constructor-body-hash-mismatch"
    oracles = metadata.get("oracles")
    if oracles != [{
            "class": "R0", "kind": "exit-kind", "expected": "revert",
            "provenance": "rq3-source-grounded-concrete"
    }]:
        return False, "invalid-constructor-anchor-oracle"
    report_binding = metadata.get("report_binding") or {}
    if (metadata.get("fixed_input") != {"a": "1"}
            or metadata.get("source_grounding_record_sha256") !=
            metadata.get("basis_record_sha256")
            or metadata.get("concrete_ce_sha256") != proof["concrete_ce_sha256"]
            or metadata.get("evidence_sha256") != proof["evidence_sha256"]
            or report_binding != {
                "kind": "rq3-source-grounded-constructor-claim",
                "claim_exit_kind": "revert",
                "record_sha256": metadata.get("basis_record_sha256"),
            }):
        return False, "invalid-constructor-evidence-envelope"

    gate = row.get("forge_gate") or {}
    if (gate.get("schema") != "veriput-put-anchor-forge-gate/v1"
            or gate.get("source_sha256") != source_sha256
            or gate.get("put_test") != row.get("test")
            or gate.get("anchor_test") != metadata.get("test")
            or gate.get("put_status") != "Success" or gate.get("anchor_status") != "Success"
            or put_document.get("ce_anchor") != metadata
            or not _forge_run_audit(gate.get("put_run"), put_json_path, source_path,
                                    source_sha256, str(row.get("test") or ""))
            or not _forge_run_audit(gate.get("anchor_run"), put_json_path, source_path,
                                    source_sha256, str(metadata.get("test") or ""))):
        return False, "invalid-constructor-double-forge-evidence"
    return True, "strength-confirmed"


def obligations(result_root: Path) -> tuple[set[tuple], set[tuple], set[tuple]]:
    """Return strength-confirmed, unresolved, and not-generalized identities."""
    generalized = set()
    unresolved_strength = set()
    not_generalized = set()
    for case, subject_dir in _case_dirs(result_root):
        rows = _strict_valid_tests(subject_dir)

        put_rows = [row for row in rows if _physical_test_kind(row) == "put"]
        put_keys = {_artifact_key(row) for row in put_rows}
        confirmed_put_keys = set()
        for row in put_rows:
            key = _artifact_key(row)
            if _anchor_strength_audit(row, _obligation_id(case, key), subject_dir)[0]:
                confirmed_put_keys.add(key)
        generalized.update(_obligation_id(case, key) for key in confirmed_put_keys)
        unresolved_strength.update(
            _obligation_id(case, key) for key in put_keys - confirmed_put_keys)

        concrete_rows = {
            _concrete_test_key(row): row
            for row in rows if _physical_test_kind(row) == "concrete"
        }
        not_generalized_test_keys = set()
        for entry in load_manifest(subject_dir).get("entries") or []:
            if (not isinstance(entry, dict)
                    or not _entry_is_currently_not_generalized(entry, put_keys)
                    or audit_manifest(subject_dir, {"entries": [entry]})):
                continue
            not_generalized_test_keys.update(_entry_test_keys(entry))
        confirmed_keys = {
            _artifact_key(concrete_rows[key])
            for key in concrete_rows.keys() & not_generalized_test_keys
        }
        not_generalized.update(_obligation_id(case, key) for key in confirmed_keys)
    partitions = (generalized, unresolved_strength, not_generalized)
    if any(partitions[left] & partitions[right] for left in range(len(partitions))
           for right in range(left + 1, len(partitions))):
        raise RuntimeError("CE obligation classified into multiple strength partitions")
    return generalized, unresolved_strength, not_generalized


def freeze_ledger(path: Path, obligation_ids: set[tuple]) -> None:
    """Atomically freeze the path population; later runs may only reclassify it."""
    doc = {
        "schema": "veriput-rq1-ce-obligation-ledger/v1",
        "identity": ["target", "path_function", "unit", "enc", "piece"],
        "total_ce_obligations": len(obligation_ids),
        "obligations": [list(item) for item in sorted(obligation_ids)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as stream:
        json.dump(doc, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def validate_ledger(path: Path, obligation_ids: set[tuple]) -> None:
    """Fail loudly if the frozen CE population gains or loses an identity."""
    doc = json.loads(path.read_text())
    frozen = {tuple(item) for item in doc.get("obligations") or []}
    if frozen == obligation_ids:
        return
    added = sorted(obligation_ids - frozen)
    missing = sorted(frozen - obligation_ids)
    raise RuntimeError(f"frozen CE ledger drift: added={len(added)}, missing={len(missing)}; "
                       "use --freeze-ledger only after explicitly approving a new population")


def reconcile_ledger(path: Path, generalized: set[tuple], unresolved_strength: set[tuple],
                     not_generalized: set[tuple]) -> dict:
    """Keep frozen identities whose current physical evidence became unreadable."""
    doc = json.loads(path.read_text())
    frozen = {tuple(item) for item in doc.get("obligations") or []}
    observed = generalized | unresolved_strength | not_generalized
    added = observed - frozen
    missing = frozen - observed
    generalized.intersection_update(frozen)
    unresolved_strength.intersection_update(frozen)
    not_generalized.intersection_update(frozen)
    unresolved_strength.update(missing)
    return {"observed_added": len(added), "frozen_missing": len(missing)}


def recovery_pool_counts(path: Path, generalized: set[tuple], frozen: set[tuple]) -> dict:
    """Report progress over the immutable 521-item valid-no-PUT recovery pool."""
    document = json.loads(path.read_text())
    rows = document.get("rows") or []
    pool = {tuple(row.get("identity") or ()) for row in rows}
    if (document.get("schema") != "veriput-rq1-recovery-pool/v1"
            or len(pool) != 521 or not pool <= frozen):
        raise RuntimeError("invalid frozen RQ1 recovery pool")
    recovered = pool & generalized
    return {
        "initial_valid_no_put": len(pool),
        "generalized": len(recovered),
        "remaining_valid_no_put": len(pool - recovered),
    }


def inventory(result_root: Path, ledger: Path | None = None) -> dict:
    """Partition unique CE identities into generalized and not-generalized."""
    generalized, unresolved_strength, not_generalized = obligations(result_root)
    drift = {"observed_added": 0, "frozen_missing": 0}
    frozen = generalized | unresolved_strength | not_generalized
    if ledger is not None:
        frozen = {tuple(item) for item in json.loads(ledger.read_text()).get("obligations") or []}
        drift = reconcile_ledger(ledger, generalized, unresolved_strength, not_generalized)
    population = generalized | unresolved_strength | not_generalized

    counts = {
        "generalized_ce_obligations": len(generalized),
        "unresolved_strength_ce_obligations": len(unresolved_strength),
        "not_generalized_ce_obligations": len(not_generalized),
        "total_ce_obligations": len(population),
    }
    return {
        "schema": "veriput-rq1-ce-obligation-inventory/v2",
        "scope": "canonical-current",
        "grain": "instrumented path / CE obligation",
        "artifact_counts": counts,
        "frozen_population": {
            "total": len(frozen),
            "drift": drift,
        },
        "recovery_pool": recovery_pool_counts(DEFAULT_RECOVERY_POOL, generalized, frozen),
        "definitions": {
            "generalized_ce_obligations":
            ("Unique target/path_function/unit/enc/piece identities backed by a current "
             "strict-valid parameterized PUT, an audited exact CE anchor body and Stage-2 "
             "provenance, and fresh source-bound green Forge runs for both tests."),
            "unresolved_strength_ce_obligations":
            ("Unique CE identities with a current strict-valid PUT whose exact CE anchor "
             "strength evidence is missing, corrupt, stale, or not double-Forge-green, plus "
             "frozen identities whose current physical artifact is unreadable."),
            "not_generalized_ce_obligations":
            ("Unique CE identities backed by an existing zero-parameter Solidity test, "
             "an audited execution-result oracle, Forge execution evidence, and no current "
             "valid PUT."),
            "total_ce_obligations":
            ("generalized_ce_obligations + unresolved_strength_ce_obligations + "
             "not_generalized_ce_obligations. Retry rows, PUT basis replays, same-path "
             "candidates, and manifest entries are excluded."),
        },
        "consistency_checks": {
            "ce_obligation_partition":
            counts["total_ce_obligations"] == (counts["generalized_ce_obligations"] +
                                               counts["unresolved_strength_ce_obligations"] +
                                               counts["not_generalized_ce_obligations"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--freeze-ledger", action="store_true")
    args = parser.parse_args()
    generalized, unresolved_strength, not_generalized = obligations(args.result_root)
    if args.freeze_ledger:
        freeze_ledger(args.ledger, generalized | unresolved_strength | not_generalized)
    elif not args.ledger.is_file():
        parser.error(f"missing frozen CE ledger: {args.ledger}")
    report = inventory(args.result_root, args.ledger)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered)
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
