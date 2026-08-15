#!/usr/bin/env python3
"""Inventory the RQ3 raw tests that still require a real repair."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

DATASETS = ("bugfix124", "peer182", "real203")


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def concrete_rows(row: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = row.get(key) or row.get(key.replace("_artifacts", "_tests")) or []
    return [
        value for value in values if isinstance(value, dict) and (
            value.get("kind") == "concrete" or value.get("is_concrete") is True)
    ]


def artifact_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("file") or ""), str(row.get("test") or "")


def project_root(test_file: Path) -> Path | None:
    for parent in test_file.parents:
        if (parent / "foundry.toml").is_file():
            return parent
    return None


def test_contract(source: str, test: str) -> str | None:
    match = re.search(r"\bcontract\s+([A-Za-z_$][A-Za-z0-9_$]*)[^{}]*\{", source)
    if not match or not re.search(rf"\bfunction\s+{re.escape(test)}\s*\(", source):
        return None
    return match.group(1)


def oracle_cluster(reason: str) -> str:
    if "lacks exact path_function/enc identity" in reason:
        return "identity-missing"
    if "not immediately before the target call" in reason:
        return "revert-oracle-ordering"
    if "not the strict normal-exit marker shape" in reason:
        return "normal-exit-marker-shape"
    if "not bound to selected target call" in reason:
        return "selected-call-binding"
    if "not a strict revert oracle" in reason:
        return "weak-revert-oracle"
    if "lacks structured witness oracle provenance" in reason:
        return "missing-result-oracle"
    return "other-structured-oracle"


def forge_diagnostic(output: str, returncode: int | None, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if "No tests found" in output:
        return "no-tests-found"
    if "Compiler run failed" in output or "Compilation failed" in output:
        if "Identifier already declared" in output:
            return "compile-duplicate-identifier"
        if "Explicit type conversion not allowed from non-payable" in output:
            return "compile-payable-cast"
        if '"runtimeCode" is not available for contracts containing immutable variables' in output:
            return "compile-immutable-runtime-code"
        if "Declaration " in output and "not found in" in output:
            return "compile-missing-import-symbol"
        if "Expected type name" in output or "Expected primary expression" in output:
            return "compile-invalid-generated-syntax"
        if "Undeclared identifier" in output:
            return "compile-undeclared-identifier"
        if "Invalid type for argument" in output:
            return "compile-invalid-argument-type"
        if "Member " in output and "not found" in output:
            return "compile-missing-member"
        return "compile-other"
    if returncode == 0 and re.search(r"\b[1-9][0-9]* passed\b", output):
        return "success-on-recheck"
    if re.search(r"\[FAIL:[^\n]*\]\s+setUp\(\)", output):
        if "call to non-contract address" in output:
            return "setup-unmocked-contract"
        if "Only owner can perform this operation" in output:
            return "setup-caller-mismatch"
        if "InvalidAddress" in output or "zero address" in output.lower() \
                or "ZeroAddress" in output:
            return "setup-zero-address"
        if any(marker in output for marker in ("ZeroConversionPrice", "MaxCommitmentAgeTooLow",
                                               "PoolPauseWindowDurationOverflow",
                                               "Delay must exceed minimum delay")):
            return "setup-domain-constraint"
        return "setup-other-revert"
    if "only current implementationAuthority can call" in output:
        return "runtime-implementation-authority"
    if "assertion failed" in output.lower() or "asserttrue" in output.lower() \
            or "assertfalse" in output.lower():
        return "runtime-oracle-mismatch"
    if re.search(r"\[FAIL:[^\n]*(?:Revert|OutOfGas|OutOfFunds|EvmError)", output):
        return "runtime-target-revert"
    if returncode:
        return "runtime-other"
    return "unknown"


def run_forge(entry: dict[str, Any], timeout: int) -> dict[str, Any]:
    command = entry.get("repair", {}).get("forge_exact_command") or []
    if not command:
        return {"classification": "unavailable", "reason": "no Foundry project"}
    try:
        completed = subprocess.run(command,
                                   capture_output=True,
                                   text=True,
                                   timeout=timeout,
                                   check=False,
                                   env={
                                       **os.environ, "NO_COLOR": "1"
                                   })
        output = (completed.stdout or "") + (completed.stderr or "")
        timed_out = False
        returncode: int | None = completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(
            errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode(
            errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        output = (stdout or "") + (stderr or "")
        timed_out = True
        returncode = None
    diagnostic_lines = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(("Error ", "[FAIL:")) and line not in diagnostic_lines:
            diagnostic_lines.append(line)
    diagnostic_text = "\n".join(diagnostic_lines[:8])
    return {
        "classification": forge_diagnostic(output, returncode, timed_out),
        "returncode": returncode,
        "timed_out": timed_out,
        "diagnostic_lines": diagnostic_lines[:8],
        "diagnostic_sha256": hashlib.sha256(diagnostic_text.encode()).hexdigest(),
    }


def entry_for(result_file: Path, case: dict[str, Any], artifact: dict[str, Any], group: str,
              reason: str | None) -> dict[str, Any]:
    test_file = Path(str(artifact.get("file") or ""))
    put_file = Path(str(artifact.get("put_json") or ""))
    try:
        put = json.loads(put_file.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        put = {}
    source = test_file.read_text(errors="replace") if test_file.is_file() else ""
    root = project_root(test_file)
    test_name = str(artifact.get("test") or put.get("test") or "")
    contract = test_contract(source, test_name)
    command = []
    if root and test_name:
        relative_test = test_file.relative_to(root)
        # Foundry matches against the full function signature, including `(`.
        command = [
            "forge", "test", "--root",
            str(root), "--match-path",
            str(relative_test), "--match-test", f"^{re.escape(test_name)}\\(", "-vvv"
        ]
    identity = {
        "dataset": case.get("dataset"),
        "case": case.get("subject_id"),
        "contract": case.get("contract") or put.get("contract"),
        "unit": artifact.get("unit") or put.get("unit"),
        "path_function": artifact.get("path_function") or put.get("path_function"),
        "enc": artifact.get("enc") if artifact.get("enc") is not None else put.get("enc"),
        "piece": artifact.get("piece") if artifact.get("piece") is not None else put.get("piece"),
    }
    return {
        "repair_group": group,
        "root_cause_cluster":
        oracle_cluster(reason or "") if group == "structured-oracle" else group,
        "reason": reason,
        "identity": identity,
        "file": str(test_file),
        "test": test_name,
        "test_contract": contract,
        "result_json": str(result_file),
        "put_json": str(put_file),
        "source_exists": test_file.is_file(),
        "forge_status": artifact.get("forge_status"),
        "valid_reference_test": artifact.get("valid_reference_test"),
        "completion_status": case.get("completion_status"),
        "stage2_source": artifact.get("stage2_source"),
        "stage2_witness_check": artifact.get("stage2_witness_check"),
        "concrete_oracles": artifact.get("concrete_oracles") or [],
        "hashes": {
            "source_sha256": sha256(test_file),
            "put_json_sha256": sha256(put_file),
            "result_json_sha256": sha256(result_file),
        },
        "repair": {
            "forge_project": str(root) if root else None,
            "forge_exact_command": command,
            "forge_exact_shell": shlex.join(command) if command else None,
        },
    }


def load_entries(root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    result_files = [
        path for dataset in DATASETS
        for path in sorted((root / dataset / "subjects").glob("*/result.json"))
    ]
    entries: list[dict[str, Any]] = []
    totals = {
        "results": len(result_files),
        "raw": 0,
        "valid": 0,
        "gap": 0,
        "recoverable_persisted_siblings": 0
    }
    for result_file in result_files:
        document = json.loads(result_file.read_text(errors="replace"))
        row = document.get("row") or document
        raw = concrete_rows(row, "raw_artifacts")
        valid = concrete_rows(row, "valid_artifacts")
        totals["raw"] += len(raw)
        totals["valid"] += len(valid)
        valid_keys = {artifact_key(item) for item in valid}
        gap = [item for item in raw if artifact_key(item) not in valid_keys]
        totals["gap"] += len(gap)
        errors = {
            (str(item.get("file") or ""), str(item.get("test") or "")): item
            for item in (
                row.get("concrete_replay_persistence") or {}).get("persistence_errors", [])
            if isinstance(item, dict)
        }
        for artifact in gap:
            key = artifact_key(artifact)
            error = errors.get(key)
            if error:
                entries.append(
                    entry_for(result_file, row, artifact, "structured-oracle",
                              str(error.get("reason") or "")))
            elif artifact.get("forge_status") == "Success" \
                    and artifact.get("valid_reference_test") is True:
                totals["recoverable_persisted_siblings"] += 1
            elif artifact.get("forge_status") == "Failure":
                entries.append(entry_for(result_file, row, artifact, "forge-failure", None))
            elif artifact.get("forge_status") is None:
                entries.append(entry_for(result_file, row, artifact, "forge-not-run", None))
            else:
                entries.append(entry_for(result_file, row, artifact, "deploy-policy", None))
    entries.sort(key=lambda item: (str(item["identity"]["dataset"]), str(item["identity"]["case"]),
                                   item["file"], item["test"]))
    return entries, totals


def markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    clusters = report["root_cause_clusters"]
    lines = [
        "# RQ3 Raw-to-Valid Real Repair Inventory",
        "",
        f"Inventory SHA-256: `{report['inventory_sha256']}`",
        "",
        "This inventory is read-only with respect to canonical RQ3/RQ1 artifacts. It excludes "
        "the 499 Forge-green, already-persisted siblings that only need publication accounting.",
        "",
        "## Population",
        "",
        f"- RQ3 raw concrete tests: {summary['raw']}",
        f"- RQ3 published valid concrete tests: {summary['valid']}",
        f"- Raw-valid gap: {summary['gap']}",
        f"- Recoverable persisted siblings: {summary['recoverable_persisted_siblings']}",
        f"- Tests requiring real repair: {summary['real_repairs']}",
        "",
        "## Root-cause clusters",
        "",
        "| Cluster | Tests | Mechanical next action |",
        "|---|---:|---|",
    ]
    actions = report["mechanical_actions"]
    for name, count in sorted(clusters.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| `{name}` | {count} | {actions.get(name, actions['default'])} |")
    lines.extend([
        "",
        "## Reproduction",
        "",
        "Each JSON entry records an argv-safe `forge_exact_command`, exact identity, source, "
        "put/result paths, and SHA-256 hashes. Re-run this inventory with `--revalidate-forge` "
        "to refresh the diagnostic class without publishing any result.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--revalidate-forge", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--forge-timeout", type=int, default=30)
    args = parser.parse_args()

    entries, totals = load_entries(args.root)
    if args.revalidate_forge:
        candidates = [
            entry for entry in entries
            if entry["repair_group"] in ("forge-failure", "forge-not-run")
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
            results = executor.map(lambda entry: run_forge(entry, args.forge_timeout), candidates)
            for entry, result in zip(candidates, results):
                entry["forge_recheck"] = result
                entry["root_cause_cluster"] = result["classification"]

    clusters: dict[str, int] = {}
    groups: dict[str, int] = {}
    for entry in entries:
        cluster = entry["root_cause_cluster"]
        clusters[cluster] = clusters.get(cluster, 0) + 1
        group = entry["repair_group"]
        groups[group] = groups.get(group, 0) + 1
    totals["real_repairs"] = len(entries)
    if totals != {
            "results": 547,
            "raw": 2995,
            "valid": 2140,
            "gap": 855,
            "recoverable_persisted_siblings": 499,
            "real_repairs": 356
    }:
        raise SystemExit(f"unexpected frozen population: {totals}")
    if groups != {
            "structured-oracle": 106,
            "forge-failure": 115,
            "forge-not-run": 133,
            "deploy-policy": 2
    }:
        raise SystemExit(f"unexpected real-repair partition: {groups}")

    actions = {
        "missing-result-oracle": "Regenerate the selected call with a result-bound R0 oracle.",
        "selected-call-binding": "Bind oracle metadata to the selected target call result symbol.",
        "weak-revert-oracle": "Regenerate a strict revert assertion for the selected call.",
        "revert-oracle-ordering":
        "Move the strict revert oracle immediately before its target call.",
        "normal-exit-marker-shape":
        "Regenerate the strict normal-exit marker around the target call.",
        "identity-missing": "Publish exact path_function and enc from the CE/certification record.",
        "compile-duplicate-identifier": "Allocate unique generated locals per emitted call.",
        "compile-payable-cast":
        "Emit payable(address) before casting to a payable proxy/interface.",
        "compile-immutable-runtime-code":
        "Do not query type(C).runtimeCode for immutable-bearing contracts.",
        "compile-missing-import-symbol":
        "Import file-level symbols without contract-scoped selective imports.",
        "compile-invalid-generated-syntax":
        "Repair ABI type rendering for the generated call/fixture.",
        "compile-undeclared-identifier": "Materialize the referenced generated local or fixture.",
        "compile-invalid-argument-type":
        "Regenerate the call using the ABI-declared argument type.",
        "compile-missing-member": "Regenerate against the exact target ABI/contract type.",
        "setup-unmocked-contract": "Etch/mock the fixed constructor dependency before deployment.",
        "setup-caller-mismatch": "Deploy/call under the fixed CE owner sender.",
        "setup-zero-address": "Materialize the CE's nonzero constructor address.",
        "setup-domain-constraint":
        "Materialize constructor values satisfying the fixed CE constraints.",
        "setup-other-revert": "Repair constructor/setup materialization from the fixed CE state.",
        "runtime-implementation-authority":
        "Prank the fixed implementationAuthority caller for the target call.",
        "runtime-oracle-mismatch": "Repair CE-to-oracle translation, then exact-test Forge.",
        "runtime-target-revert": "Repair target-call fixture/value/sender translation.",
        "success-on-recheck": "Publish the exact successful Forge result after persistence gates.",
        "deploy-policy": "Add an authenticated deployment oracle or explicitly exclude the row.",
        "default": "Inspect the recorded exact-test diagnostic and repair its generator cluster.",
    }
    inventory_bytes = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    report = {
        "schema": "veriput-rq3-real-repair-inventory/v1",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "root": str(args.root.resolve()),
        "summary": totals,
        "repair_groups": groups,
        "root_cause_clusters": clusters,
        "mechanical_actions": actions,
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "entries": entries,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.markdown.write_text(markdown(report))
    print(
        json.dumps(
            {
                "summary": totals,
                "repair_groups": groups,
                "root_cause_clusters": clusters,
                "inventory_sha256": report["inventory_sha256"]
            },
            indent=2,
            sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
