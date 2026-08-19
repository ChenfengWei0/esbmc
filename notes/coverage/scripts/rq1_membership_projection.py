#!/usr/bin/env python3
"""Certify that a retained concrete replay is a member of a Fair600 PUT.

This is deliberately narrower than anchor recovery.  It does not reconstruct
an old ESBMC claim or pretend that one exists.  Instead it seals two independent
artifacts: the Fair600 region certificate and the retained executable replay,
then proves that the replay's fixed environment and entry setup are members of
that region.  Unsupported Solidity shapes are refused.
"""

from __future__ import annotations

import argparse
from collections import Counter
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from put_all import _solidity_code_mask, _solidity_function_spans, forge_json_status_map
from rq1_put_ce_anchor_backfill import (_code_contains_statement, _contract_close_for_function,
                                        _function_body, _renamed_function, _same_recovery_piece,
                                        _sha256_file, _sha256_text, _source_function)

SCHEMA = "veriput-fair600-membership-projection/v1"
SETUP_MATERIALIZED_SCHEMA = "veriput-fair600-membership-projection-setup-materialized/v1"
UINT256_MAX = 2**256 - 1
ADDRESS_MAX = 2**160 - 1


class Refusal(ValueError):
    """The retained evidence is insufficient for a sound projection."""


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Refusal(f"JSON object required: {path}")
    return value


def _sealed(path: Path, expected: str | None = None) -> str:
    digest = _sha256_file(path)
    if digest is None:
        raise Refusal(f"evidence file is absent: {path}")
    if expected is not None and digest != expected:
        raise Refusal(f"evidence hash mismatch: {path}")
    return digest


def _project_root(test_file: Path) -> Path:
    for parent in (test_file.parent, *test_file.parents):
        if (parent / "foundry.toml").is_file():
            return parent
    raise Refusal(f"Foundry project is absent for {test_file}")


def _cert_record(put_json: Path, identity: list[str]) -> tuple[Path, dict[str, Any]]:
    subject = Path(str(put_json).split("/put/", 1)[0])
    ledger = subject / "cert" / "certify-results.jsonl"
    _sealed(ledger)
    matches = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Refusal(f"malformed certification ledger: {exc}") from exc
        details = row.get("certified_details") or {}
        detail = details.get(str(identity[3]))
        if (row.get("unit") == identity[2] and row.get("path_function") == identity[1]
                and isinstance(detail, dict) and str(identity[3]) in (row.get("certified") or {})
                and _same_recovery_piece(identity[4], detail.get("piece"))):
            matches.append((row, detail))
    if len(matches) != 1:
        raise Refusal(f"expected one exact Fair600 certification record, found {len(matches)}")
    row, detail = matches[0]
    if (row.get("bucket") != "CERTIFIED" or detail.get("verdict") != "CERTIFIED"
            or detail.get("certification_source") != "structural-abi-gate-no-coordinate"):
        raise Refusal("Fair600 record is not a certified structural ABI value gate")
    return ledger, {"record": row, "detail": detail}


def _manifest_entry(
        manifest: dict[str, Any], original: dict[str, Any], replay_sha: str,
        flat_sha: str) -> dict[str, Any]:  # pylint: disable=too-many-boolean-expressions
    """Select and validate the retained, previously Forge-green replay record."""
    matches = [
        row for row in manifest.get("entries", [])
        if row.get("replay_id") == original["replay_id"] and row.get("test") == original["test"]
    ]
    if len(matches) != 1:
        raise Refusal(f"expected one exact retained replay manifest entry, found {len(matches)}")
    row = matches[0]
    origin = row.get("origin") or {}
    # pylint: disable=too-many-boolean-expressions
    if (row.get("schema") != "veriput-rq1-concrete-replay/v1"
            or row.get("forge_status") != "Success" or not row.get("valid_reference_test")
            or row.get("test_sha256") != replay_sha or row.get("flat_sha256") != flat_sha
            or row.get("concrete_oracles") != original.get("oracles")):
        raise Refusal("retained replay manifest entry is stale or differs from frozen evidence")
    # pylint: enable=too-many-boolean-expressions
    return {"entry": row, "origin": origin}


def _fair600_run(put_json: Path, identity: list[str], put: dict[str, Any]) -> dict[str, Any]:  # pylint: disable=too-many-locals
    """Bind the PUT to its strict 600-second case result and frozen run policy."""
    subject = Path(str(put_json).split("/put/", 1)[0])
    result_path = subject / "result.json"
    result = _load(result_path)
    matches = [
        row for row in (result.get("row") or {}).get("valid_tests", [])
        if row.get("put_json") == str(put_json) and row.get("file") == put.get("file")
        and row.get("test") == put.get("test") and row.get("kind") == "put"
        and str(row.get("enc")) == str(identity[3]) and row.get("unit") == identity[2]
        and str(row.get("piece") or "") == identity[4]
    ]
    wall = (result.get("row") or {}).get("wall_total_s")
    result_row = result.get("row") or {}
    dataset, subject_id = identity[0].split("/", 1)
    if len(matches) != 1 or matches[0].get("forge_status") != "Success":
        raise Refusal("PUT is not an exact Forge-green row in the Fair600 case result")
    if not isinstance(wall, (int, float)) or wall > 600:
        raise Refusal("case result is outside the Fair600 wall budget")
    prefix = str(put_json).split("/runs/", 1)
    if len(prefix) != 2:
        raise Refusal("PUT is outside a Fair600 frozen run root")
    freeze_path = Path(prefix[0]) / "fair600-case-freeze.json"
    freeze = _load(freeze_path)
    policy = freeze.get("policy") or {}
    frozen_binary = (freeze.get("inputs") or {}).get("esbmc") or {}
    run_binary = result_row.get("esbmc_binary_identity") or {}
    frozen_runner = str((freeze.get("inputs") or {}).get("runner") or "")
    frozen_runner_sha = (freeze.get("inputs") or {}).get("runner_sha256")
    pipeline_files = (result_row.get("pipeline_code_identity") or {}).get("files") or {}
    expected_proof = [
        "--solidity-max-tx", "1", "--k-induction", "--enable-forward-condition", "--max-k-step",
        "30"
    ]
    if (freeze.get("schema") != "veriput-rq1-noput-fair600-case-freeze/v1"
            or policy.get("case_wall_budget_s") != 600
            or policy.get("region_proof") != expected_proof
            or identity not in freeze.get("obligations", [])):
        raise Refusal("Fair600 freeze policy does not bind this obligation and k-induction run")
    # pylint: disable=too-many-boolean-expressions
    if (result_row.get("benchmark") != dataset or result_row.get("subject_id") != subject_id
            or result_row.get("status") != "ok"
            or result_row.get("completion_status") not in {"complete", "budget-exhausted"}
            or result_row.get("strict_case_wall_budget") is not True
            or result_row.get("strict_case_wall_within_cap") is not True
            or (result.get("stale_artifact_adoption") or {}).get("adopted") is not False
            or run_binary.get("sha256") != frozen_binary.get("sha256")
            or pipeline_files.get(frozen_runner) != frozen_runner_sha
            or _sealed(Path(frozen_binary.get("path") or ""),
                       frozen_binary.get("sha256")) != frozen_binary.get("sha256")):
        raise Refusal("Fair600 result provenance or corrected ESBMC binary binding is invalid")
    # pylint: enable=too-many-boolean-expressions
    return {
        "freeze":
        str(freeze_path),
        "freeze_sha256":
        _sealed(freeze_path),
        "result":
        str(result_path),
        "result_sha256":
        _sealed(result_path),
        "result_valid_row_sha256":
        _sha256_text(json.dumps(matches[0], sort_keys=True, separators=(",", ":"))),
        "case_wall_s":
        wall,
    }


def _fixed_environment(function_source: str, unit: str, oracle: dict[str, Any]) -> dict[str, Any]:  # pylint: disable=too-many-locals
    """Extract the one supported fixed ABI-value-gate replay shape."""
    masked = _solidity_code_mask(function_source)
    compact = re.sub(r"\s+", "", masked)
    receiver = str(oracle.get("target_receiver") or "")
    observed = str(oracle.get("observed") or "")
    if (oracle.get("kind") != "call-status" or oracle.get("expected") is not False
            or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", receiver)
            or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", observed)):
        raise Refusal("only a fixed call-status false oracle is supported")
    call_pattern = (r"\(bool\s+" + re.escape(observed) + r"\s*,\s*\)\s*=\s*address\s*\(\s*" +
                    re.escape(receiver) + r"\s*\)\s*\.call\s*\{\s*value\s*:\s*([0-9]+)\s*\}")
    calls = list(re.finditer(call_pattern, masked))
    if len(calls) != 1 or compact.count(".call") != 1:
        raise Refusal("exactly one target-bound literal low-level value call is required")
    call = calls[0]
    semicolon = masked.find(";", call.end())
    if semicolon < 0:
        raise Refusal("target call statement is unclosed")
    call_source = function_source[call.start():semicolon + 1]
    selector = r'abi\s*\.\s*encodeWithSignature\s*\(\s*"' + re.escape(unit) + r'\(\)"\s*\)'
    if re.search(selector, call_source) is None:
        raise Refusal("target call does not encode the frozen zero-parameter unit")
    if re.search(r"\b(?:startPrank|prank)\s*\(", masked):
        raise Refusal("sender overrides are unsupported by this exact replay projection")
    if re.search(r"\b(?:if|for|while|assembly|try|catch)\b", masked):
        raise Refusal("replay control flow is unsupported by this exact projection")
    statements = compact.count(";")
    if statements not in (2, 3):
        raise Refusal("replay contains unsupported statements or helper dependencies")
    value = int(call.group(1))
    if statements == 3 and f"vm.deal(address(this),{value});" not in compact:
        raise Refusal("the only supported auxiliary statement is matching test-contract funding")
    suffix = re.sub(r"\s+", "", masked[semicolon + 1:])
    if (f"assertFalse({observed}," not in suffix and f"assertFalse({observed});" not in suffix):
        raise Refusal("call-status observable is not asserted after the target call")
    sender = {
        "kind": "test-contract-address",
        "lower_bound": 1,
        "upper_bound": ADDRESS_MAX,
    }
    return {"msg.value": value, "msg.sender": sender}


def _membership(region: dict[str, Any], holes: dict[str, Any], env: dict[str, Any]) -> dict:
    if holes:
        raise Refusal("membership projection with holes is not implemented")
    if set(region) != {"msg.sender", "msg.value"}:
        raise Refusal(f"unsupported projected coordinates: {sorted(region)}")
    value_lo, value_hi = map(int, region["msg.value"])
    value = int(env["msg.value"])
    if not value_lo <= value <= value_hi <= UINT256_MAX:
        raise Refusal("fixed msg.value is outside the certified region")
    sender_lo, sender_hi = map(int, region["msg.sender"])
    sender = env["msg.sender"]
    if sender["kind"] == "literal":
        if not sender_lo <= sender["value"] <= sender_hi:
            raise Refusal("fixed msg.sender is outside the certified region")
    elif not sender_lo <= sender["lower_bound"] <= sender["upper_bound"] <= sender_hi:
        raise Refusal("test-contract sender domain is not contained in the certified region")
    return {
        "coordinates": {
            "msg.value": {
                "fixed": value,
                "region": [str(value_lo), str(value_hi)]
            },
            "msg.sender": {
                "fixed_semantics": sender,
                "region": [str(sender_lo), str(sender_hi)]
            },
        },
        "holes": {},
        "verdict": "MEMBER",
    }


def _run_forge(project: Path, source: Path, test: str) -> dict[str, Any]:
    """Require one exact suite/test Success; process return code is insufficient."""
    try:
        relative = source.resolve().relative_to(project.resolve()).as_posix()
    except ValueError as exc:
        raise Refusal("Forge source is outside its project") from exc
    match_test = r"^" + re.escape(test) + r"(\(|$)"
    command = [
        "forge", "test", "--json", "--match-path", relative, "--match-test", match_test,
        "--fuzz-runs", "256"
    ]
    completed = subprocess.run(command,
                               cwd=project,
                               text=True,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,
                               timeout=180,
                               check=False)
    log = completed.stdout
    statuses, _names, suite_failures = forge_json_status_map(log)
    source_function_count = len(_solidity_function_spans(source.read_text(encoding="utf-8"), test))
    matches = [
        status for (suite, name), status in statuses.items()
        if os.path.normpath(suite) == os.path.normpath(relative) and (
            name == test or name.startswith(test + "("))
    ]
    success = (source_function_count == 1 and completed.returncode == 0 and not suite_failures
               and len(matches) == 1 and matches[0] == "Success" and "No tests found" not in log)
    return {
        "command": command,
        "source": relative,
        "source_sha256": _sealed(source),
        "test_statuses": matches,
        "source_function_count": source_function_count,
        "status": "Success" if success else "Failure",
        "returncode": completed.returncode,
        "log_sha256": _sha256_text(log),
        "log_tail": log[-2000:],
    }


def _replace_function(source: str, name: str, replacement: str) -> str:
    """Replace one Solidity function selected by the fail-closed source scanner."""
    function, error = _source_function_any(source, name)
    if function is None:
        raise Refusal(error)
    if source.count(function) != 1:
        raise Refusal(f"function is not unique: {name}")
    return source.replace(function, replacement, 1)


def _source_function_any(source: str, name: str) -> tuple[str | None, str]:
    """Return one exact function while permitting PUT fuzz parameters."""
    spans = _solidity_function_spans(source, name)
    if not spans:
        return None, f"function is absent: {name}"
    if len(spans) != 1:
        return None, f"function is ambiguous: {name}"
    span, error = spans[0]
    if span is None:
        return None, error or f"function is malformed: {name}"
    return source[span[0]:span[1]], ""


def _function_with_body_prefix(function: str, prefix: str) -> str:
    """Materialize a formerly global setUp body inside one test function."""
    masked = _solidity_code_mask(function)
    opening = masked.find("{")
    if opening < 0:
        raise Refusal("test function body is absent")
    return function[:opening + 1] + prefix + function[opening + 1:]


def _empty_function_body(function: str) -> str:
    """Preserve a setUp declaration while moving its body into each test."""
    masked = _solidity_code_mask(function)
    opening = masked.find("{")
    closing = masked.rfind("}")
    if opening < 0 or closing <= opening:
        raise Refusal("setUp function body is malformed")
    return function[:opening + 1] + "\n  " + function[closing:]


def _setup_difference_signature(entry: dict[str, Any]) -> str:
    """Hash the statement-multiset delta for deterministic setup clustering."""
    replay = Path(entry["original_ce"]["basis_path"]).read_text(encoding="utf-8")
    put = Path(entry["fair_put"]["source_path"]).read_text(encoding="utf-8")
    replay_setup, replay_error = _function_body(replay, "setUp")
    put_setup, put_error = _function_body(put, "setUp")
    if replay_setup is None or put_setup is None:
        raise Refusal(replay_error or put_error or "setup function is absent")

    def statements(body: str) -> Counter[str]:
        return Counter(
            re.sub(r"\s+", " ", statement.strip()) for statement in body.split(";")
            if statement.strip())

    replay_statements = statements(replay_setup)
    put_statements = statements(put_setup)
    delta = {
        "replay_only": sorted((replay_statements - put_statements).elements()),
        "put_only": sorted((put_statements - replay_statements).elements()),
    }
    return _sha256_text(json.dumps(delta, sort_keys=True, separators=(",", ":")))


def _require_plain_public_function(function: str, name: str) -> None:
    """Reject modifiers whose ordering would change after setup materialization."""
    spans = _solidity_function_spans(function, name)
    if len(spans) != 1 or spans[0][0] is None:
        raise Refusal(f"function is not uniquely materializable: {name}")
    span = spans[0][0]
    qualifiers = re.sub(r"\s+", " ", _solidity_code_mask(function)[span[4] + 1:span[5]]).strip()
    if qualifiers != "public":
        raise Refusal(f"setup materialization requires an unmodified public function: {name}")


# pylint: disable=too-many-locals
def _setup_target_deployment(setup: str, receiver: str, contract: str) -> dict[str, str]:
    """Seal the unique receiver deployment that reaches the certified dispatcher."""
    masked = _solidity_code_mask(setup)
    assignments = list(
        re.finditer(r"(?<![A-Za-z0-9_$.])" + re.escape(receiver) + r"\s*(?<![=!<>])=(?!=)", masked))
    deployments = list(
        re.finditer(
            r"(?<![A-Za-z0-9_$.])" + re.escape(receiver) + r"\s*=\s*new\s+" + re.escape(contract) +
            r"\s*\(", masked))
    if len(assignments) != 1 or len(deployments) != 1:
        raise Refusal("setup does not uniquely deploy the certified contract into the receiver")
    forbidden = re.search(
        r"\b(?:return|break|continue|assembly|if|for|while|try|catch|prank|startPrank|"
        r"changePrank|hoax|startHoax|stopPrank|broadcast|startBroadcast|stopBroadcast)\b", masked)
    if forbidden:
        raise Refusal(f"setup contains unsupported control transfer: {forbidden.group(0)}")
    statement_end = masked.find(";", deployments[0].start())
    if statement_end < 0:
        raise Refusal("receiver deployment statement is unclosed")
    statement = setup[deployments[0].start():statement_end + 1]
    deployment_code = masked[deployments[0].start():statement_end + 1].strip()
    address_literal = r"address\s*\(\s*uint160\s*\(\s*[0-9]+\s*\)\s*\)"
    deployment_shape = (re.escape(receiver) + r"\s*=\s*new\s+" + re.escape(contract) +
                        r"\s*\(\s*(?:" + address_literal + r"(?:\s*,\s*" + address_literal +
                        r")*)?\s*\)\s*;")
    if re.fullmatch(deployment_shape, deployment_code) is None:
        raise Refusal("receiver deployment contains a non-literal constructor argument")
    remainder = (masked[:deployments[0].start()] + " " *
                 (statement_end + 1 - deployments[0].start()) + masked[statement_end + 1:])
    receiver_token = r"(?<![A-Za-z0-9_$])" + re.escape(receiver) + r"(?![A-Za-z0-9_$])"
    if re.search(receiver_token, remainder):
        raise Refusal("setup observes or mutates the receiver outside its unique deployment")
    fixtures: set[str] = set()
    identifier = r"[A-Za-z_$][A-Za-z0-9_$]*"
    for raw_statement in remainder.split(";"):
        candidate = raw_statement.strip()
        if not candidate:
            continue
        declaration = re.fullmatch(
            r"address\s+(" + identifier + r")\s*=\s*address\s*\(\s*uint160\s*\(\s*0\s*\)\s*\)",
            candidate)
        if declaration:
            fixtures.add(declaration.group(1))
            continue
        etch = re.fullmatch(r"vm\s*\.\s*etch\s*\(\s*(" + identifier + r")\s*,\s*hex\s*\)",
                            candidate)
        if etch and etch.group(1) in fixtures:
            continue
        mock_bytes = re.fullmatch(
            r"vm\s*\.\s*mockCall\s*\(\s*(" + identifier +
            r")\s*,\s*abi\s*\.\s*encodeWithSignature\s*\(\s*\)\s*,\s*bytes\s*\(\s*\)\s*\)",
            candidate)
        mock_address = re.fullmatch(
            r"vm\s*\.\s*mockCall\s*\(\s*(" + identifier +
            r")\s*,\s*abi\s*\.\s*encodeWithSignature\s*\(\s*\)\s*,\s*abi\s*\.\s*"
            r"encode\s*\(\s*address\s*\(\s*0\s*\)\s*\)\s*\)", candidate)
        fixture = ((mock_bytes or mock_address).group(1)
                   if mock_bytes is not None or mock_address is not None else None)
        if fixture in fixtures:
            continue
        raise Refusal("setup contains a non-whitelisted helper or fixture statement")
    return {"contract": contract, "receiver": receiver, "statement_sha256": _sha256_text(statement)}


# pylint: enable=too-many-locals


def _exact_prefunding(function: str, value: int) -> dict[str, str | int]:
    """Require the retained replay to fund the literal value before its only call."""
    masked = _solidity_code_mask(function)
    funding_pattern = (r"\bvm\s*\.\s*deal\s*\(\s*address\s*\(\s*this\s*\)\s*,\s*" +
                       re.escape(str(value)) + r"\s*\)\s*;")
    funding = list(re.finditer(funding_pattern, masked))
    call = list(
        re.finditer(r"\.\s*call\s*\{\s*value\s*:\s*" + re.escape(str(value)) + r"\s*\}", masked))
    if len(funding) != 1 or len(call) != 1 or funding[0].end() >= call[0].start():
        raise Refusal("replay must uniquely pre-fund the exact value before the target call")
    statement = function[funding[0].start():funding[0].end()]
    return {"value": value, "statement_sha256": _sha256_text(statement)}


# pylint: disable=too-many-locals,too-many-statements,too-many-branches
def certify(entry: dict[str, Any],
            run_forge: bool = True,
            setup_mode: str = "exact") -> dict[str, Any]:
    """Build one sealed projection certificate and optionally run both Forge gates."""
    identity = entry.get("identity")
    if (not isinstance(identity, list) or len(identity) != 5
            or entry.get("classification") != "exact-identity"):
        raise Refusal("entry is not an exact frozen identity")
    if setup_mode not in {"exact", "materialized-abi-gate"}:
        raise Refusal(f"unsupported setup mode: {setup_mode}")
    if setup_mode == "exact" and not entry.get("setup_exact"):
        raise Refusal("entry setup was not classified exact")
    if setup_mode == "materialized-abi-gate" and entry.get("setup_exact"):
        raise Refusal("setup materialization requires a setup-different entry")
    original = entry["original_ce"]
    fair = entry["fair_put"]
    replay_file = Path(original["basis_path"])
    put_file = Path(fair["source_path"])
    put_json_path = Path(fair["put_json_path"])
    identity_sha256 = _sha256_text("\t".join(identity))
    if identity_sha256 != entry.get("identity_sha256"):
        raise Refusal("exact-map identity digest is invalid")
    frozen_path = Path(__file__).resolve().parent.parent / "rq1_ce_obligations.frozen.json"
    frozen = _load(frozen_path)
    if identity not in frozen.get("obligations", []):
        raise Refusal("identity is absent from the frozen CE obligation ledger")
    dataset, subject_id = identity[0].split("/", 1)
    replay_parts = replay_file.resolve().parts
    expected_parts = (dataset, "subjects", subject_id, "concrete-replays")
    if not any(
            tuple(replay_parts[index:index + 4]) == expected_parts
            for index in range(len(replay_parts) - 3)):
        raise Refusal("retained replay path differs from the frozen case")
    replay_sha = _sealed(replay_file, original["basis_sha256"])
    manifest_path = Path(original["manifest"])
    manifest_sha = _sealed(manifest_path, original["manifest_sha256"])
    put_sha = _sealed(put_file, fair["source_sha256"])
    put_json_sha = _sealed(put_json_path, fair["put_json_sha256"])
    put = _load(put_json_path)
    if Path(str(put.get("file") or "")).resolve() != put_file.resolve():
        raise Refusal("exact-map PUT source differs from the certified PUT document")
    expected = (identity[1], identity[2], str(identity[3]), identity[4])
    actual = (put.get("path_function"), put.get("unit"), str(put.get("enc")),
              str(put.get("piece") or ""))
    if actual != expected or put.get("kind") != "put" or put.get("test") != fair["test"]:
        raise Refusal("Fair600 PUT identity differs from the frozen obligation")
    fair600_run = _fair600_run(put_json_path, identity, put)
    strategy = put.get("proof_strategy") or {}
    if (strategy.get("kind") != "k-induction"
            or "--k-induction" not in strategy.get("esbmc_args", [])
            or "--enable-forward-condition" not in strategy.get("esbmc_args", [])):
        raise Refusal("Fair600 PUT is not bound to the k-induction strategy")
    ledger, certification = _cert_record(put_json_path, identity)
    detail = certification["detail"]
    if (str(detail.get("enc")) != str(identity[3]) or detail.get("depth") != put.get("depth")
            or detail.get("box") != [{
                "name": "msg.value",
                "lo": put["region"]["msg.value"][0],
                "hi": put["region"]["msg.value"][1],
                "holes": []
            }]):
        raise Refusal("PUT region is not the exact structural certificate projection")
    sender_derivation = (put.get("derived_by") or {}).get("region_derivation") or {}
    if sender_derivation != {
            "kind": "structural-getter-unconstrained-sender",
            "coordinate": "msg.sender",
            "lo": 1,
            "hi": ADDRESS_MAX,
            "source": "structural-abi-gate-no-coordinate",
            "dependency_check": "unit_env_dependencies == []",
            "unit_parameters": 0,
    }:
        raise Refusal("msg.sender region lacks the exact dependency-free structural derivation")
    if (put.get("region") or {}).get("msg.sender") != ["1", str(ADDRESS_MAX)]:
        raise Refusal("msg.sender region differs from its exact structural derivation")

    replay_source = replay_file.read_text(encoding="utf-8")
    put_source = put_file.read_text(encoding="utf-8")
    replay_test, error = _source_function(replay_source, original["test"])
    if replay_test is None:
        raise Refusal(error or "retained replay function is absent")
    replay_setup, error = _function_body(replay_source, "setUp")
    put_setup, put_error = _function_body(put_source, "setUp")
    if replay_setup is None or put_setup is None:
        raise Refusal(error or put_error or "replay or PUT entry setup is absent")
    if setup_mode == "exact" and replay_setup != put_setup:
        raise Refusal("replay and PUT entry setups differ")
    if setup_mode == "materialized-abi-gate" and replay_setup == put_setup:
        raise Refusal("setup-different entry unexpectedly has equal setup bodies")
    replay_root = _project_root(replay_file)
    put_root = _project_root(put_file)
    replay_flat = replay_root / "src" / "flat.sol"
    put_flat = put_root / "src" / "flat.sol"
    replay_flat_sha = _sealed(replay_flat)
    if replay_flat_sha != _sealed(put_flat):
        raise Refusal("replay and Fair600 PUT execute different flat sources")
    manifest_binding = _manifest_entry(_load(manifest_path), original, replay_sha, replay_flat_sha)
    origin = manifest_binding["origin"]
    if (origin.get("path_function") != identity[1] or origin.get("unit") != identity[2]
            or str(origin.get("enc")) != identity[3]
            or str(origin.get("piece") or "") != identity[4]):
        raise Refusal("retained replay manifest origin differs from the frozen identity")
    oracles = original.get("oracles", [])
    if not isinstance(oracles, list) or len(oracles) != 1:
        raise Refusal("exactly one authenticated replay oracle is required")
    for oracle in oracles:
        if not _code_contains_statement(replay_test, str(oracle.get("assertion") or "")):
            raise Refusal("retained replay function does not contain its sealed observable")
    environment = _fixed_environment(replay_test, identity[2], oracles[0])
    membership = _membership(put.get("region") or {}, put.get("holes") or {}, environment)
    setup_projection = None
    if setup_mode == "materialized-abi-gate":
        if environment["msg.value"] <= 0:
            raise Refusal("setup independence requires a positive ABI-gate value")
        contract_match = re.search(r"@C@([^@]+)@F@", identity[1])
        if contract_match is None:
            raise Refusal("path identity does not name one target contract")
        contract = contract_match.group(1)
        receiver = str(oracles[0].get("target_receiver") or "")
        replay_setup_function, replay_setup_error = _source_function(replay_source, "setUp")
        put_setup_function, put_setup_error = _source_function(put_source, "setUp")
        put_test_function, put_test_error = _source_function_any(put_source, fair["test"])
        if (replay_setup_function is None or put_setup_function is None
                or put_test_function is None):
            raise Refusal(replay_setup_error or put_setup_error or put_test_error)
        _require_plain_public_function(replay_setup_function, "setUp")
        _require_plain_public_function(put_setup_function, "setUp")
        _require_plain_public_function(replay_test, original["test"])
        _require_plain_public_function(put_test_function, fair["test"])
        replay_deployment = _setup_target_deployment(replay_setup, receiver, contract)
        put_deployment = _setup_target_deployment(put_setup, receiver, contract)
        prefunding = _exact_prefunding(replay_test, environment["msg.value"])
        setup_projection = {
            "kind":
            "materialized-structural-abi-value-gate",
            "verdict":
            "SETUP-UNOBSERVABLE-BEFORE-DISPATCH",
            "reason": ("the certified nonpayable ABI value gate rejects positive msg.value "
                       "before function-body state can be observed"),
            "replay_setup_body_sha256":
            _sha256_text(replay_setup),
            "put_setup_body_sha256":
            _sha256_text(put_setup),
            "replay_target_deployment":
            replay_deployment,
            "put_target_deployment":
            put_deployment,
            "replay_prefunding":
            prefunding,
        }

    anchor = "test_ce_membership_" + entry["identity_sha256"][:16]
    renamed = _renamed_function(replay_test, original["test"], anchor)
    if renamed == replay_test:
        raise Refusal("retained replay function could not be renamed")
    validation_anchor = (_function_with_body_prefix(renamed, replay_setup)
                         if setup_mode == "materialized-abi-gate" else renamed)
    forge_gate: dict[str, Any] = {"status": "not-run", "strict_certificate": False}
    if run_forge:
        with tempfile.TemporaryDirectory(prefix="rq1-membership-") as scratch:
            project = Path(scratch) / "project"
            replay_project = Path(scratch) / "replay-project"
            shutil.copytree(put_root, project, symlinks=False)
            shutil.copytree(replay_root, replay_project, symlinks=False)
            relative = put_file.relative_to(put_root)
            scratch_source = project / relative
            replay_relative = replay_file.relative_to(replay_root)
            scratch_replay = replay_project / replay_relative
            if project.resolve() not in scratch_source.resolve().parents:
                raise Refusal("scratch validation source escapes its isolated project")
            if replay_project.resolve() not in scratch_replay.resolve().parents:
                raise Refusal("scratch replay source escapes its isolated project")
            if _sealed(scratch_replay) != replay_sha:
                raise Refusal("scratch replay source differs from retained evidence")
            text = scratch_source.read_text(encoding="utf-8")
            if _solidity_function_spans(text, anchor):
                raise Refusal("membership anchor name already exists in validation source")
            if setup_mode == "materialized-abi-gate":
                setup_function, setup_error = _source_function(text, "setUp")
                put_test, test_error = _source_function_any(text, fair["test"])
                if setup_function is None or put_test is None:
                    raise Refusal(setup_error or test_error or "validation function is absent")
                text = _replace_function(text, "setUp", _empty_function_body(setup_function))
                text = _replace_function(text, fair["test"],
                                         _function_with_body_prefix(put_test, put_setup))
            close = _contract_close_for_function(text, fair["test"])
            if close is None:
                raise Refusal("destination test contract is ambiguous")
            scratch_source.write_text(text[:close] + "\n  " + validation_anchor.strip() + "\n" +
                                      text[close:],
                                      encoding="utf-8")
            original_run = _run_forge(replay_project, scratch_replay, original["test"])
            put_run = _run_forge(project, scratch_source, fair["test"])
            anchor_run = _run_forge(project, scratch_source, anchor)
            forge_gate = {
                "status":
                "Success"
                if original_run["status"] == put_run["status"] == anchor_run["status"] == "Success"
                else "Failure",
                "strict_certificate":
                True,
                "original_replay_run":
                original_run,
                "put_run":
                put_run,
                "anchor_run":
                anchor_run,
                "validation_source_sha256":
                _sealed(scratch_source),
            }
            if setup_projection is not None:
                forge_gate["setup_materialization"] = {
                    "global_setup": "empty",
                    "put_test_setup_body_sha256": _sha256_text(put_setup),
                    "anchor_test_setup_body_sha256": _sha256_text(replay_setup),
                    "validation_anchor_function_sha256": _sha256_text(validation_anchor),
                }
            if forge_gate["status"] != "Success":
                raise Refusal("PUT/anchor double Forge gate failed")

    cert = {
        "schema": SCHEMA if setup_mode == "exact" else SETUP_MATERIALIZED_SCHEMA,
        "identity": identity,
        "identity_sha256": identity_sha256,
        "methodology": {
            "claim": "membership projection, not recovered original-CE certification",
            "no_per_ce_solver_run": True,
            "fairness_basis": "Fair600 full-case artifact",
        },
        "frozen_obligation_ledger": {
            "path": str(frozen_path),
            "sha256": _sealed(frozen_path),
            "identity_sha256": identity_sha256,
        },
        "fair600_region_proof": {
            "put_json":
            str(put_json_path),
            "put_json_sha256":
            put_json_sha,
            "put_source":
            str(put_file),
            "put_source_sha256":
            put_sha,
            "certification_ledger":
            str(ledger),
            "certification_ledger_sha256":
            _sealed(ledger),
            "certification_record_sha256":
            _sha256_text(json.dumps(certification["record"], sort_keys=True,
                                    separators=(",", ":"))),
            "certified_detail_sha256":
            _sha256_text(json.dumps(detail, sort_keys=True, separators=(",", ":"))),
            "proof_strategy":
            strategy,
            "certification_source":
            detail["certification_source"],
            "sender_derivation_sha256":
            _sha256_text(json.dumps(sender_derivation, sort_keys=True, separators=(",", ":"))),
            "fair600_run":
            fair600_run,
        },
        "retained_replay": {
            "manifest":
            original["manifest"],
            "manifest_sha256":
            manifest_sha,
            "manifest_entry_sha256":
            _sha256_text(
                json.dumps(manifest_binding["entry"], sort_keys=True, separators=(",", ":"))),
            "source":
            str(replay_file),
            "source_sha256":
            replay_sha,
            "test":
            original["test"],
            "test_function_sha256":
            _sha256_text(replay_test),
            "setup_body_sha256":
            _sha256_text(replay_setup),
            "flat_source_sha256":
            replay_flat_sha,
            "oracles":
            copy.deepcopy(original["oracles"]),
        },
        "membership": membership,
        "setup_projection": setup_projection,
        "anchor": {
            "test": anchor,
            "function_sha256": _sha256_text(validation_anchor),
            "retained_replay_renamed_function_sha256": _sha256_text(renamed),
        },
        "forge_gate": forge_gate,
    }
    cert["certificate_sha256"] = _sha256_text(
        json.dumps(cert, sort_keys=True, separators=(",", ":")))
    return cert


# pylint: enable=too-many-locals,too-many-statements,too-many-branches


def _write_isolated_report(path: Path, report: dict[str, Any]) -> None:
    """Atomically write reports while refusing experiment and canonical roots."""
    resolved = path.resolve()
    rendered = resolved.as_posix()
    if "/Results/RQ1/VeriPUT/" in rendered or "/fair600-cases202-freeze-" in rendered:
        raise Refusal("report output may not overwrite canonical or Fair600 frozen artifacts")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=resolved.parent,
                                     encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        temporary = Path(stream.name)
    os.replace(temporary, resolved)


def main() -> int:
    """Validate a bounded batch and write only the isolated certificate report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--exact-map", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--skip-setup-exact", type=int, default=4)
    parser.add_argument("--no-forge", action="store_true")
    parser.add_argument("--setup-mode", choices=("exact", "materialized-abi-gate"), default="exact")
    parser.add_argument("--largest-setup-class", action="store_true")
    args = parser.parse_args()
    exact_map = _load(args.exact_map)
    want_exact = args.setup_mode == "exact"
    selected = [
        row for row in exact_map.get("queue", []) if bool(row.get("setup_exact")) is want_exact
    ]
    selection = None
    if args.largest_setup_class:
        if want_exact:
            raise SystemExit("--largest-setup-class requires materialized setup mode")
        eligible = [
            row for row in selected if len(row.get("original_ce", {}).get("oracles", [])) == 1
            and row["original_ce"]["oracles"][0].get("kind") == "call-status"
            and row["original_ce"]["oracles"][0].get("expected") is False
        ]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in eligible:
            signature = _setup_difference_signature(row)
            grouped.setdefault(signature, []).append(row)
        if not grouped:
            raise SystemExit("no supported setup-different class exists")
        signature, selected = min(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
        selection = {
            "kind": "largest-isomorphic-setup-difference-class",
            "signature_sha256": signature,
            "class_size": len(selected),
            "eligible_size": len(eligible),
        }
    selected = selected[args.skip_setup_exact:args.skip_setup_exact + args.limit]
    report = {
        "schema": "veriput-fair600-membership-projection-batch/v1",
        "certificates": [],
        "provisional": [],
        "refusals": [],
        "selection": selection,
    }
    for entry in selected:
        try:
            certificate = certify(entry, run_forge=not args.no_forge, setup_mode=args.setup_mode)
            bucket = "certificates" if certificate["forge_gate"].get(
                "strict_certificate") else "provisional"
            report[bucket].append(certificate)
        except (OSError, KeyError, Refusal, subprocess.TimeoutExpired) as exc:
            report["refusals"].append({"identity": entry.get("identity"), "reason": str(exc)})
    report["counts"] = {
        "selected": len(selected),
        "certified": len(report["certificates"]),
        "provisional": len(report["provisional"]),
        "refused": len(report["refusals"])
    }
    _write_isolated_report(args.output, report)
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if not report["refusals"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
