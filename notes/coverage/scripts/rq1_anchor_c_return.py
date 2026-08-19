#!/usr/bin/env python3
"""Materialize the C-history return partition without canonical writes.

The first-pass recovery driver historically recognized only a class-level
``Type c0;`` receiver declaration.  Historical replays frequently construct
the receiver inside the selected test (``Type c0 = new Type(...)``), so this
partition resolves the exact call receiver and its ABI declaration together.
Return components are retained in declaration order, including duplicate
types, and tuples receive one indexed R0 assertion per ABI component.
"""

# pylint: disable=import-error

from __future__ import annotations

import argparse
import copy
import concurrent.futures
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

import rq1_put_ce_anchor_backfill as backfill  # pylint: disable=wrong-import-position
from rq1_anchor_compound import add_indexed_return_oracles  # pylint: disable=wrong-import-position
from solidity_path_put import (  # pylint: disable=wrong-import-position
    _oracle_claim_coverage_error, add_concrete_fixed_return_oracle,
    authenticated_concrete_oracle_error, source_inherited_function_returns,
    split_top_level)  # pylint: disable=import-error


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(record: dict[str, Any]) -> tuple[str, ...] | None:
    value = backfill._recovery_identity(record)  # pylint: disable=protected-access
    return tuple(value) if value is not None else None


def _selected_test_source(record: dict[str, Any]) -> tuple[Path | None, str | None, str | None]:
    return backfill._find_recovery_emit_case(record)  # pylint: disable=protected-access


def rebind_exact_canonical_paths(record: dict[str, Any]) -> dict[str, Any]:
    """Relocate byte-identical selected artifacts from legacy scratch paths.

    Some historical result rows retain a scratch path even though the same
    immutable source and put.json were adopted under the canonical subject.
    Relocation is allowed only when each content hash has one canonical match.
    """
    identity = _identity(record)
    selected = record.get("selected_put") or {}
    source_path = Path(str(selected.get("source_path") or ""))
    put_json_path = Path(str(selected.get("put_json_path") or ""))
    if identity is None:
        return record
    case_parts = identity[0].split("/", 1)
    if len(case_parts) != 2:
        return record
    canonical_subject = (backfill.DEFAULT_RESULT_ROOT / case_parts[0] / "subjects" /
                         case_parts[1]).resolve()
    try:
        source_path.resolve().relative_to(canonical_subject)
        put_json_path.resolve().relative_to(canonical_subject)
        return record
    except ValueError:
        pass
    source_matches = [
        path for path in canonical_subject.rglob("*.t.sol")
        if path.is_file() and _sha256_file(path) == selected.get("source_sha256")
    ]
    put_json_matches = [
        path for path in canonical_subject.rglob("put.json")
        if path.is_file() and path.parent.name == put_json_path.parent.name
        and _sha256_file(path) == selected.get("put_json_sha256")
    ]
    if len(source_matches) != 1 or len(put_json_matches) != 1:
        return record
    rebound = copy.deepcopy(record)
    rebound_selected = rebound["selected_put"]
    rebound_selected["source_path"] = str(source_matches[0])
    rebound_selected["put_json_path"] = str(put_json_matches[0])
    rebound_selected["exact_canonical_relocation"] = {
        "schema": "veriput-exact-canonical-path-relocation/v1",
        "original_source_path": str(source_path),
        "original_put_json_path": str(put_json_path),
        "source_sha256": selected.get("source_sha256"),
        "put_json_sha256": selected.get("put_json_sha256"),
    }
    return rebound


def _target_call(source: str, test_name: str,
                 unit: str) -> tuple[str | None, int | None, str | None]:
    """Return the unique direct-call receiver and argument arity."""
    body, error = backfill._function_body(source, test_name)  # pylint: disable=protected-access
    if body is None:
        return None, None, error
    mask = backfill._solidity_code_mask(body)  # pylint: disable=protected-access
    pattern = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*" +
                         re.escape(unit) + r"\s*(?:\{[^{}]*\}\s*)?\(", re.S)
    calls = []
    for match in pattern.finditer(mask):
        opening = mask.rfind("(", match.start(), match.end())
        closing = backfill._matching_delimiter(mask, opening, "(", ")")  # pylint: disable=protected-access
        if closing is None:
            return None, None, "selected target call has an unclosed argument list"
        arguments = body[opening + 1:closing].strip()
        arity = 0 if not arguments else len(split_top_level(arguments))
        calls.append((match.group(1), arity))
    unique = list(dict.fromkeys(calls))
    if len(unique) != 1:
        return None, None, f"expected one target receiver/arity, found {len(unique)}"
    return unique[0][0], unique[0][1], None


def _receiver_type(source: str, receiver: str) -> tuple[str | None, str | None]:
    """Resolve a receiver from executable Solidity declarations, not comments."""
    mask = backfill._solidity_code_mask(source)  # pylint: disable=protected-access
    declaration = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$.]*)\s+" +
                             re.escape(receiver) + r"\s*(?:=|;)")
    types = list(dict.fromkeys(match.group(1) for match in declaration.finditer(mask)))
    if len(types) != 1:
        return None, f"expected one declared type for receiver {receiver}, found {len(types)}"
    return types[0].split(".")[-1], None


def _imported_flat(source_path: Path, source: str,
                   contract: str) -> tuple[Path | None, str | None, str | None]:
    """Resolve the exact local import which exports the receiver contract."""
    candidates = []
    pattern = re.compile(r"\bimport\s*\{([^{}]*)\}\s*from\s*(['\"])([^'\"]+)\2\s*;",
                         re.S)
    for match in pattern.finditer(source):
        masked_prefix = backfill._solidity_code_mask(source[:match.start() + 6])  # pylint: disable=protected-access
        if not masked_prefix.endswith("import"):
            continue
        for item in split_top_level(match.group(1)):
            names = re.split(r"\s+as\s+", item.strip())
            exposed = names[-1].strip()
            if exposed != contract:
                continue
            candidate = (source_path.parent / match.group(3)).resolve()
            if candidate.is_file():
                candidates.append((candidate, names[0].strip()))
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        return None, None, f"expected one local import for {contract}, found {len(candidates)}"
    return candidates[0][0], candidates[0][1], None


def _normalized_scalar_witness(solidity_type: str, witness: Any) -> Any:
    """Interpret an unsigned Stage-2 bit pattern as its signed ABI scalar."""
    match = re.fullmatch(r"int([0-9]+)?", solidity_type.strip())
    if match is None:
        return witness
    width = int(match.group(1) or "256")
    try:
        integer = int(str(witness).strip(), 0)
    except ValueError:
        return witness
    if 2**(width - 1) <= integer < 2**width:
        return str(integer - 2**width)
    return witness


def recover_return_types(record: dict[str, Any]) -> tuple[list[tuple[str, str]] | None,
                                                          str | None]:
    """Recover the selected external call's ABI return tuple in declaration order."""
    # pylint: disable=too-many-return-statements
    identity = _identity(record)
    selected = record.get("selected_put") or {}
    put_path = Path(str(selected.get("source_path") or ""))
    emit_path, test_name, error = _selected_test_source(record)
    if identity is None or not put_path.is_file() or emit_path is None or test_name is None:
        return None, error or "selected PUT or historical replay is absent"
    put_source = put_path.read_text(encoding="utf-8")
    emit_source = emit_path.read_text(encoding="utf-8")
    receiver, arity, error = _target_call(emit_source, test_name, identity[2])
    if receiver is None:
        return None, error
    contract, error = _receiver_type(emit_source + "\n" + put_source, receiver)
    if contract is None:
        return None, error
    flat_path, declared_contract, error = _imported_flat(put_path, put_source, contract)
    if flat_path is None:
        return None, error
    returns = source_inherited_function_returns(flat_path.read_text(encoding="utf-8"),
                                                declared_contract, identity[2], arity=arity)
    if not isinstance(returns, list) or not returns:
        return None, "could not recover one ABI declaration for the selected receiver/arity"
    if any(not isinstance(item, tuple) or len(item) != 2 or
           not all(isinstance(value, str) for value in item) for item in returns):
        return None, "recovered ABI return declaration is malformed"
    return returns, None


def materialize(record: dict[str, Any], scratch_root: Path) -> tuple[dict[str, Any] | None,
                                                                     str | None]:
    """Build one strict backfill entry for an exact return observation."""
    # pylint: disable=too-many-locals,too-many-return-statements
    identity = _identity(record)
    if identity is None:
        return None, "malformed recovery identity"
    kinds = tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or [])
    if record.get("recovery_category") != "directly-generatable" or kinds != ("return", ):
        return None, "record is outside the direct return partition"
    selected, selected_error = backfill._selected_put(record)  # pylint: disable=protected-access
    digest, digest_error = backfill._record_identity_digest(record)  # pylint: disable=protected-access
    if selected is None or digest is None:
        return None, selected_error or digest_error
    put_path = Path(str(selected["source_path"]))
    put_source = put_path.read_text(encoding="utf-8")
    emit_path, test_name, error = _selected_test_source(record)
    if emit_path is None or test_name is None:
        return None, error
    emit_source = emit_path.read_text(encoding="utf-8")
    history_report, history_report_sha256, error = backfill._sealed_history_report(  # pylint: disable=protected-access
        record, list(identity), put_path, emit_path, emit_source, test_name)
    if error:
        return None, error
    put_setup, _ = backfill._scoped_function_body(  # pylint: disable=protected-access
        put_source, str(selected.get("test") or ""), "setUp")
    emit_setup, _ = backfill._scoped_function_body(emit_source, test_name, "setUp")  # pylint: disable=protected-access
    if put_setup is None or put_setup != emit_setup:
        return None, "emitted exact CE setup differs from PUT setup"
    return_types, error = recover_return_types(record)
    if return_types is None:
        return None, error
    witness = (record.get("ce") or {}).get("return_value")
    if len(return_types) == 1:
        witness = _normalized_scalar_witness(return_types[0][1], witness)
        rendered, oracles = add_concrete_fixed_return_oracle(
            emit_source, test_name, identity[2], return_types, witness)
        error = None if oracles else "scalar return witness could not be rendered exactly"
    else:
        rendered, oracles, error = add_indexed_return_oracles(
            emit_source, test_name, identity[2], return_types, witness)
    claim, claim_error = backfill.executable_claim(record)
    error = (error or authenticated_concrete_oracle_error(oracles)
             or _oracle_claim_coverage_error(claim, oracles) if claim is not None else claim_error)
    if error:
        return None, error
    report_path = history_report or Path(str((record.get("claim_provenance") or {}).get(
        "report_path") or ""))
    return backfill._entry_from_basis(  # pylint: disable=protected-access
        record, rendered, test_name, oracles, scratch_root, "c18-return", {
            "emitted_source": str(emit_path),
            "emitted_test": test_name,
            "identity_digest": digest,
            "report_path": str(report_path),
            "report_sha256": history_report_sha256 or _sha256_file(report_path),
            "return_abi": [{"index": index, "name": name, "type": sol_type}
                           for index, (name, sol_type) in enumerate(return_types)],
        })


def _validate(entry: dict[str, Any], scratch_root: Path,
              fuzz_runs: int) -> dict[str, Any]:
    """Prepare, embed, and run both Forge gates in an isolated project copy."""
    row = {"identity": entry["identity"]}
    prepared, error = backfill._prepare(entry)  # pylint: disable=protected-access
    if prepared is None:
        return {**row, "status": "refused", "reason": error}
    merged, error = backfill._embed(prepared)  # pylint: disable=protected-access
    if merged is None:
        return {**row, "status": "refused", "reason": error}
    metadata, error = backfill._finalize_metadata(prepared, merged)  # pylint: disable=protected-access
    if metadata is None:
        return {**row, "status": "refused", "reason": error}
    project = backfill._project_root(prepared["put_file"])  # pylint: disable=protected-access
    if project is None:
        return {**row, "status": "refused", "reason": "Foundry project root is absent"}
    validation, error = backfill._validate_in_scratch(  # pylint: disable=protected-access
        prepared, merged, project, scratch_root, fuzz_runs)
    return {
        **row,
        "status": "validated" if error is None else "forge-failed",
        "reason": error,
        "anchor_test": metadata["test"],
        "put_forge_ok": bool(validation and validation.get("put_forge_ok")),
        "anchor_forge_ok": bool(validation and validation.get("anchor_forge_ok")),
        "validation": validation,
    }


def seal_ready_partition(progress_path: Path, consumer_path: Path,
                         output_path: Path) -> dict[str, Any]:
    """Seal only double-Forge-green rows for a separate canonical writer."""
    # pylint: disable=too-many-locals
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    consumer = json.loads(consumer_path.read_text(encoding="utf-8"))
    if (progress.get("schema") != "veriput-rq1-anchor-c18-return-validation/v1"
            or consumer.get("schema") != "veriput-rq1-anchor-c18-return-consumer/v1"
            or progress.get("consumer_inventory") != str(consumer_path)
            or progress.get("consumer_inventory_sha256") != _sha256_file(consumer_path)):
        raise ValueError("C18 progress and consumer inventory are not cross-bound")
    records = {_identity(record): record for record in consumer.get("records") or []}
    ready = []
    for row in progress.get("rows") or []:
        if row.get("status") != "validated":
            continue
        identity = tuple(str(value) for value in row.get("identity") or [])
        record = records.get(identity)
        validation = row.get("validation") or {}
        report_path = (Path(str(validation.get("staged_project") or "")) /
                       "ce-anchor-validation.json")
        if (record is None or not report_path.is_file()
                or validation.get("put_forge_ok") is not True
                or validation.get("anchor_forge_ok") is not True):
            raise ValueError("validated row lacks its exact consumer or Forge report")
        ready.append({
            "identity": list(identity),
            "record_identity_sha256": record.get("identity_sha256"),
            "status": "ready",
            "external_validation": {
                "schema": "veriput-anchor-external-double-forge/v1",
                "status": "validated",
                "staged_source_sha256": validation.get("staged_source_sha256"),
                "put_forge_ok": True,
                "anchor_forge_ok": True,
                "anchor_test": row.get("anchor_test"),
                "report": {
                    "path": str(report_path),
                    "sha256": _sha256_file(report_path),
                    "bytes": report_path.stat().st_size,
                },
                "put_run": validation.get("put_run"),
                "anchor_run": validation.get("anchor_run"),
            },
        })
    ready_identities = {tuple(row["identity"]) for row in ready}
    ready_consumer_path = output_path.with_name("ready-consumer-inventory.json")
    ready_consumer = {
        "schema": "veriput-rq1-anchor-c18-return-ready-consumer/v1",
        "source_consumer_inventory": str(consumer_path),
        "source_consumer_inventory_sha256": _sha256_file(consumer_path),
        "records": [record for record in consumer.get("records") or []
                    if _identity(record) in ready_identities],
    }
    backfill._atomic_json(ready_consumer_path, ready_consumer)  # pylint: disable=protected-access
    result = {
        "schema": "veriput-rq1-anchor-c18-return-ready/v1",
        "identity": ["case", "path_function", "unit", "enc", "piece"],
        "consumer_inventory": str(consumer_path),
        "consumer_inventory_sha256": _sha256_file(consumer_path),
        "ready_consumer_inventory": str(ready_consumer_path),
        "ready_consumer_inventory_sha256": _sha256_file(ready_consumer_path),
        "validation_progress": str(progress_path),
        "validation_progress_sha256": _sha256_file(progress_path),
        "canonical_writes": 0,
        "counts": {"ready": len(ready)},
        "ready": ready,
        "rows": ready,
    }
    backfill._atomic_json(output_path, result)  # pylint: disable=protected-access
    return result


def apply_ready_partition(sealed_path: Path, progress_path: Path, scratch_root: Path,
                          fuzz_runs: int, limit: int) -> int:
    """Delegate a sealed ready-only set to the transactional backfill writer."""
    # pylint: disable=too-many-locals,too-many-boolean-expressions
    sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    inventory_path = Path(str(sealed.get("ready_consumer_inventory") or ""))
    source_consumer = Path(str(sealed.get("consumer_inventory") or ""))
    validation_progress = Path(str(sealed.get("validation_progress") or ""))
    ready_rows = sealed.get("ready") or []
    if (not inventory_path.is_file() or not source_consumer.is_file()
            or not validation_progress.is_file()
            or _sha256_file(inventory_path) != sealed.get("ready_consumer_inventory_sha256")
            or _sha256_file(source_consumer) != sealed.get("consumer_inventory_sha256")
            or _sha256_file(validation_progress) != sealed.get("validation_progress_sha256")
            or sealed.get("counts") != {"ready": len(ready_rows)}):
        raise ValueError("sealed C18 ready inventory is absent, stale, or malformed")
    selectors = {tuple(str(value) for value in row["identity"]): row for row in ready_rows}
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    records = {_identity(record): record for record in inventory.get("records") or []}
    if (len(selectors) != len(ready_rows) or set(records) != set(selectors)
            or any(records[key].get("identity_sha256") !=
                   selectors[key].get("record_identity_sha256") for key in selectors)):
        raise ValueError("sealed C18 selectors differ from the ready consumer inventory")
    original = backfill._materialize_recovery_basis  # pylint: disable=protected-access

    def selected_materializer(record: dict[str, Any], root: Path):
        identity = _identity(record)
        if identity not in selectors:
            return None, "identity is outside the sealed C18 ready partition"
        entry, error = materialize(record, root)
        if entry is not None:
            entry["_prevalidated_selector"] = selectors[identity]
        return entry, error

    backfill._materialize_recovery_basis = selected_materializer  # pylint: disable=protected-access
    prior_argv = sys.argv
    try:
        sys.argv = [
            "rq1_put_ce_anchor_backfill.py", "--recovery-inventory", str(inventory_path),
            "--recovery-scratch-root", str(scratch_root), "--progress", str(progress_path),
            "--apply", "--limit", str(min(limit, len(selectors))), "--fuzz-runs", str(fuzz_runs)
        ]
        return backfill.main()
    finally:
        sys.argv = prior_argv
        backfill._materialize_recovery_basis = original  # pylint: disable=protected-access


def post_apply_audit(sealed_path: Path, output_path: Path) -> dict[str, Any]:
    """Compare every adopted C18 anchor with its sealed staged source and headline audit."""
    # pylint: disable=too-many-locals
    sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    inventory_path = Path(str(sealed.get("ready_consumer_inventory") or ""))
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    records = {_identity(record): record for record in inventory.get("records") or []}
    rows = []
    for selector in sealed.get("ready") or []:
        identity = tuple(str(value) for value in selector["identity"])
        record = records[identity]
        validation = selector["external_validation"]
        report = json.loads(Path(validation["report"]["path"]).read_text(encoding="utf-8"))
        canonical_source = Path(str(report["canonical_source"]))
        staged_source = Path(str(report["staged_source"]))
        put_json = Path(str((record.get("selected_put") or {}).get("put_json_path") or ""))
        source_text = canonical_source.read_text(encoding="utf-8")
        put_document = json.loads(put_json.read_text(encoding="utf-8"))
        anchor = put_document.get("ce_anchor") or {}
        subject = backfill._subject_dir_for_source(canonical_source)  # pylint: disable=protected-access
        current = backfill._current_strength_entry(record)  # pylint: disable=protected-access
        headline_ok = bool(current and subject and
                           backfill._already_strength_confirmed(current))  # pylint: disable=protected-access
        receiver, _arity, receiver_error = _target_call(
            Path(str((record.get("sealed_history_recovery") or {}).get(
                "staged_source_path") or "")).read_text(encoding="utf-8"),
            str((record.get("sealed_history_recovery") or {}).get("selected_test") or ""),
            identity[2])
        receiver_type, type_error = _receiver_type(source_text, receiver or "")
        _flat, declared_type, import_error = _imported_flat(canonical_source, source_text,
                                                            receiver_type or "")
        return_types, return_error = recover_return_types(record)
        alias_or_signed_path = (declared_type != receiver_type or any(
            re.fullmatch(r"int(?:[0-9]+)?", sol_type.strip())
            for _name, sol_type in (return_types or [])))
        exact_source = (canonical_source.is_file() and staged_source.is_file()
                        and canonical_source.read_bytes() == staged_source.read_bytes())
        metadata_ok = (anchor.get("status") == "embedded"
                       and anchor.get("test") == validation.get("anchor_test")
                       and (anchor.get("destination") or {}).get("source_after_sha256")
                       == _sha256_file(canonical_source)
                       and (anchor.get("forge_gate") or {}).get("put_status") == "Success"
                       and (anchor.get("forge_gate") or {}).get("anchor_status") == "Success")
        error = receiver_error or type_error or import_error or return_error
        status = ("exact" if exact_source and metadata_ok and headline_ok
                  and not alias_or_signed_path and error is None else "different")
        rows.append({
            "identity": list(identity),
            "status": status,
            "canonical_source": str(canonical_source),
            "canonical_source_sha256": _sha256_file(canonical_source),
            "sealed_staged_source": str(staged_source),
            "sealed_staged_source_sha256": _sha256_file(staged_source),
            "source_byte_identical": exact_source,
            "metadata_ok": metadata_ok,
            "headline_strength_confirmed": headline_ok,
            "post_review_materialization_path_changed": alias_or_signed_path,
            "error": error,
        })
    result = {
        "schema": "veriput-rq1-anchor-c18-return-post-apply-audit/v1",
        "sealed_partition": str(sealed_path),
        "sealed_partition_sha256": _sha256_file(sealed_path),
        "counts": {
            "exact": sum(row["status"] == "exact" for row in rows),
            "different": sum(row["status"] != "exact" for row in rows),
        },
        "rows": rows,
    }
    backfill._atomic_json(output_path, result)  # pylint: disable=protected-access
    return result


def main() -> int:
    """Write the sealed consumer inventory and isolated double-Forge report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path, nargs="?")
    parser.add_argument("selector", type=Path, nargs="?")
    parser.add_argument("--consumer-output", type=Path)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--apply-ready", type=Path)
    parser.add_argument("--fuzz-runs", type=int, default=256)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.apply_ready is not None:
        if (args.inventory is not None or args.selector is not None
                or args.consumer_output is not None):
            parser.error("--apply-ready does not accept inventory generation arguments")
        if args.limit < 1:
            parser.error("--limit must be positive")
        return apply_ready_partition(args.apply_ready, args.progress, args.scratch_root,
                                     args.fuzz_runs, args.limit)
    if args.inventory is None or args.selector is None or args.consumer_output is None:
        parser.error("inventory, selector, and --consumer-output are required for validation")
    document = json.loads(args.inventory.read_text(encoding="utf-8"))
    selectors = json.loads(args.selector.read_text(encoding="utf-8")).get("records") or []
    selected_ids = {tuple(str(value) for value in row["identity"]) for row in selectors}
    records = [rebind_exact_canonical_paths(record) for record in document.get("records") or []
               if _identity(record) in selected_ids]
    if len(records) != len(selected_ids):
        parser.error("selector does not map one-to-one onto the recovery inventory")
    consumer = {
        "schema": "veriput-rq1-anchor-c18-return-consumer/v1",
        "source_inventory": str(args.inventory),
        "source_inventory_sha256": _sha256_file(args.inventory),
        "selector": str(args.selector),
        "selector_sha256": _sha256_file(args.selector),
        "records": records,
    }
    backfill._atomic_json(args.consumer_output, consumer)  # pylint: disable=protected-access
    rows = []
    entries = []
    for record in records:
        entry, error = materialize(record, args.scratch_root)
        if entry is None:
            rows.append({"identity": list(_identity(record) or ()), "status": "refused",
                         "reason": error})
        else:
            entries.append(entry)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as executor:
        rows.extend(executor.map(lambda entry: _validate(entry, args.scratch_root,
                                                         args.fuzz_runs), entries))
    rows.sort(key=lambda row: tuple(row["identity"]))
    counts = {status: sum(row["status"] == status for row in rows)
              for status in sorted({row["status"] for row in rows})}
    progress = {
        "schema": "veriput-rq1-anchor-c18-return-validation/v1",
        "consumer_inventory": str(args.consumer_output),
        "consumer_inventory_sha256": _sha256_file(args.consumer_output),
        "canonical_writes": 0,
        "counts": counts,
        "rows": rows,
    }
    backfill._atomic_json(args.progress, progress)  # pylint: disable=protected-access
    print(json.dumps(counts, sort_keys=True))
    return 1 if any(row["status"] != "validated" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
