#!/usr/bin/env python3
"""Recover fixed scalar concrete replay sources from retained RQ1 evidence.

This is deliberately an offline evidence tool.  It never invokes ESBMC, Forge,
or either PUT stage.  A replay is confirmed only when a canonical manifest
already binds the exact replay source and its successful historical Forge log
by SHA-256.  Newly rendered sources remain isolated, unverified candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from solidity_path_put import (  # pylint: disable=wrong-import-position,import-error
    _concrete_return_literal,
    add_concrete_fixed_return_oracle,
    bind_return,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _canonical_subject(results_root: Path, case: str) -> Path:
    benchmark, subject = case.split("/", 1)
    return results_root / benchmark / "subjects" / subject


def _identity(row: dict) -> dict:
    return {
        "case": row["case"],
        "unit": row["unit"],
        "path_function": row["path_function"],
        "enc": row["enc"],
        "piece": row.get("piece"),
    }


def _identity_matches(entry: dict, row: dict) -> bool:
    origin = entry.get("origin") or {}
    return (
        str(origin.get("unit") or "") == str(row.get("unit") or "")
        and str(origin.get("path_function") or "")
        == str(row.get("path_function") or "")
        and str(origin.get("enc")) == str(row.get("enc"))
        and str(origin.get("piece") or "") == str(row.get("piece") or "")
    )


def _return_oracle(entry: dict, expected: str) -> dict | None:
    matches = [
        oracle
        for oracle in entry.get("concrete_oracles") or []
        if isinstance(oracle, dict)
        and oracle.get("kind") == "return-value"
        and str(oracle.get("expected") or "") == expected
    ]
    return matches[0] if len(matches) == 1 else None


def _historical_binding(subject: Path, entry: dict, row: dict,
                        expected: str) -> dict | None:
    if not _identity_matches(entry, row) or _return_oracle(entry, expected) is None:
        return None
    project = subject / str(entry.get("project") or "")
    test_file = project / str(entry.get("test_file") or "")
    forge_log = project / str(entry.get("forge_log") or "")
    if not test_file.is_file() or not forge_log.is_file():
        return None
    if _sha256(test_file) != entry.get("test_sha256"):
        return None
    if _sha256(forge_log) != entry.get("forge_log_sha256"):
        return None
    if (entry.get("forge_status") != "Success"
            or int(entry.get("forge_passed_tests") or 0) < 1
            or entry.get("valid_reference_test") is not True):
        return None
    put_json = Path(str(row.get("put_json") or ""))
    if not put_json.is_file():
        return None
    put_sha = _sha256(put_json)
    origin_put = (entry.get("origin") or {}).get("put_json") or {}
    matching = entry.get("matching_put_artifacts") or []
    if (origin_put.get("sha256") != put_sha
            or entry.get("generalization_status")
            != "confirmed-generalized-to-put"
            or not any(isinstance(item, dict)
                       and item.get("put_json_sha256") == put_sha
                       and item.get("test") for item in matching)):
        return None
    return {
        "entry": entry,
        "manifest": subject / "concrete-replays" / "manifest.json",
        "project": project,
        "test_file": test_file,
        "forge_log": forge_log,
        "put_sha256": put_sha,
        "put_tests": sorted({str(item["test"]) for item in matching
                             if isinstance(item, dict)
                             and item.get("put_json_sha256") == put_sha
                             and item.get("test")}),
    }


def _claim_record(row: dict) -> dict:
    claim = Path(str(row.get("claim_source") or ""))
    original = Path(str(row.get("original_fixed_test_file") or ""))
    put_json = Path(str(row.get("put_json") or ""))
    return {
        "schema": "veriput-rq1-log-basis-claim/v1",
        "identity": _identity(row),
        "claim_source": str(claim),
        "claim_source_sha256": _sha256(claim) if claim.is_file() else None,
        "original_fixed_test": row.get("original_fixed_test"),
        "original_fixed_test_file": str(original),
        "original_fixed_test_sha256": (
            _sha256(original) if original.is_file() else None),
        "put_json": str(put_json),
        "put_json_sha256": _sha256(put_json) if put_json.is_file() else None,
        "return_types": row.get("return_types"),
        "return_value": row.get("return_value"),
    }


def _confirmed_provenance(row: dict, binding: dict) -> dict:
    claim = _claim_record(row)
    entry = binding["entry"]
    return {
        **claim,
        "status": "confirmed-from-historical-forge-log",
        "put_tests": binding["put_tests"],
        "replay_test": entry.get("test"),
        "replay_source": str(binding["test_file"]),
        "replay_source_sha256": entry.get("test_sha256"),
        "forge_log": str(binding["forge_log"]),
        "forge_log_sha256": entry.get("forge_log_sha256"),
        "forge_passed_tests": entry.get("forge_passed_tests"),
    }


def _render_candidate(row: dict, candidate_root: Path) -> dict:
    claim = _claim_record(row)
    original = Path(claim["original_fixed_test_file"])
    if not original.is_file():
        return {**claim, "status": "unverified-unrenderable",
                "reason": "retained fixed test source is missing"}
    source = original.read_text(errors="replace")
    rewritten, oracles = add_concrete_fixed_return_oracle(
        source, str(row.get("original_fixed_test") or ""),
        str(row.get("unit") or ""), row.get("return_types"),
        row.get("return_value"))
    # Some legacy emitters coalesced a normal witness and revert-tolerant
    # siblings into one test.  The generic binder deliberately selects the
    # last target call and therefore refuses that mixed shape.  It is still
    # unambiguous when the selected test contains exactly one non-try target
    # call: bind that direct call and leave all sibling calls untouched.
    if not oracles:
        lines = source.splitlines()
        test_name = str(row.get("original_fixed_test") or "")
        unit = str(row.get("unit") or "")
        start = next((index for index, line in enumerate(lines)
                      if f"function {test_name}(" in line), None)
        end = None
        depth = 0
        if start is not None:
            for index in range(start, len(lines)):
                depth += lines[index].count("{") - lines[index].count("}")
                if index > start and depth <= 0:
                    end = index
                    break
        direct = []
        if start is not None and end is not None:
            direct = [index for index in range(start + 1, end)
                      if f".{unit}(" in lines[index]
                      and "try " not in lines[index]
                      and not lines[index].lstrip().startswith("//")]
        return_types = row.get("return_types") or []
        expected = (_concrete_return_literal(return_types[0][1],
                                             row.get("return_value"))
                    if len(return_types) == 1 else None)
        if len(direct) == 1 and expected is not None:
            observed = "_veriput_concrete_return"
            rebound, _reason = bind_return(lines[direct[0]], unit,
                                            return_types[0][1], observed)
            if rebound is not None:
                assertion = (f'assertEq({observed}, {expected}, '
                             '"fixed witness return must match");')
                lines[direct[0]] = rebound
                indent = rebound[:len(rebound) - len(rebound.lstrip())]
                lines.insert(direct[0] + 1, indent + assertion)
                rewritten = "\n".join(lines) + "\n"
                receiver = rebound.split(f".{unit}(", 1)[0].split()[-1]
                oracles = [{
                    "class": "R0", "kind": "return-value",
                    "solidity_type": return_types[0][1],
                    "observed": observed, "expected": expected,
                    "provenance": "stage2-witness",
                    "target_receiver": receiver, "assertion": assertion,
                }]
    if len(oracles) != 1:
        return {**claim, "status": "unverified-unrenderable",
                "reason": "fixed scalar return could not bind to target call"}
    artifact_key = hashlib.sha256(json.dumps(
        {**_identity(row), "put_json_sha256": claim["put_json_sha256"]},
        sort_keys=True).encode()).hexdigest()[:20]
    destination = candidate_root / artifact_key
    destination.mkdir(parents=True, exist_ok=True)
    replay = destination / "replay.t.sol"
    replay.write_text(rewritten)
    flat = original.parent / "flat.sol"
    flat_record = None
    if flat.is_file():
        copied_flat = destination / "flat.sol"
        shutil.copy2(flat, copied_flat)
        flat_record = {"path": str(copied_flat), "sha256": _sha256(copied_flat)}
    record = {
        **claim,
        "status": "unverified-candidate",
        "reason": (
            "no historical successful Forge log is SHA-256-bound to this "
            "rewritten replay source; it is not a valid replay artifact"),
        "replay_test": row.get("original_fixed_test"),
        "candidate_source": str(replay),
        "candidate_source_sha256": _sha256(replay),
        "flat_source": flat_record,
        "concrete_oracles": oracles,
        "generalization_provenance": {
            "status": "candidate-for-existing-put",
            "put_tests": [],
            "put_json_sha256": claim["put_json_sha256"],
            "claim_source": claim["claim_source"],
            "claim_source_sha256": claim["claim_source_sha256"],
        },
    }
    _atomic_json(destination / "metadata.json", record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--annotate-confirmed", action="store_true")
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text())
    rows = [row for row in inventory.get("rows") or []
            if row.get("recovery_class") == "return_scalar_renderable"
            and row.get("exit_kind") == "normal"]
    confirmed = []
    unverified = []
    unrenderable = []
    touched_manifests = set()
    for row in rows:
        return_types = row.get("return_types") or []
        expected = (_concrete_return_literal(return_types[0][1],
                                             row.get("return_value"))
                    if len(return_types) == 1 else None)
        subject = _canonical_subject(args.results_root, row["case"])
        manifest_path = subject / "concrete-replays" / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            manifest = {"entries": []}
        binding = next((candidate for entry in manifest.get("entries") or []
                        if expected and (candidate := _historical_binding(
                            subject, entry, row, expected)) is not None), None)
        if binding is not None:
            provenance = _confirmed_provenance(row, binding)
            if args.annotate_confirmed:
                binding["entry"]["basis_recovery_provenance"] = provenance
                _atomic_json(manifest_path, manifest)
                touched_manifests.add(str(manifest_path))
            confirmed.append(provenance)
            continue
        record = _render_candidate(row, args.candidate_root)
        if record["status"] == "unverified-candidate":
            unverified.append(record)
        else:
            unrenderable.append(record)

    report = {
        "schema": "veriput-rq1-log-return-recovery/v1",
        "definitions": {
            "confirmed_from_log": (
                "exact PUT identity whose zero-parameter replay source and "
                "successful historical Forge log are both SHA-256-bound"),
            "unverified_candidate": (
                "CE-derived zero-parameter replay source with a structured "
                "return assertion but no historical Forge result bound to "
                "that exact source hash; excluded from valid counts"),
        },
        "input_inventory": str(args.inventory),
        "eligible_fixed_scalar_return_rows": len(rows),
        "confirmed_from_log_count": len(confirmed),
        "unverified_candidate_count": len(unverified),
        "unrenderable_count": len(unrenderable),
        "canonical_manifest_annotations": len(touched_manifests),
        "confirmed_from_log": confirmed,
        "unverified_candidates": unverified,
        "unrenderable": unrenderable,
    }
    _atomic_json(args.report, report)
    print(json.dumps({key: report[key] for key in (
        "eligible_fixed_scalar_return_rows", "confirmed_from_log_count",
        "unverified_candidate_count", "unrenderable_count",
        "canonical_manifest_annotations")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
