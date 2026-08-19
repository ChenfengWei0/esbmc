#!/usr/bin/env python3
"""Recover exact revert anchors missed by the first-pass materializer.

This partition accepts only retained claims whose sole required observable is
revert.  It either reuses an exact failing low-level call, repairs the legacy
revert-tolerant try/catch shape, or arms the one selected direct call with
``vm.expectRevert``.  The shared backfill driver still performs claim/CE
binding and both Forge gates before anything can be adopted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FROZEN_B85_IDENTITY_MANIFEST_SHA256 = (
    "52fa354c2836ec3248bc29f07c16e706e8fd1a33a0002a6af97f3d58fbd4790b")

import rq1_put_ce_anchor_backfill as backfill  # pylint: disable=wrong-import-position  # noqa: E402
from rq1_final_test_inventory import obligations  # pylint: disable=wrong-import-position  # noqa: E402
from solidity_path_put import (  # pylint: disable=import-error,wrong-import-position  # noqa: E402
    authenticated_concrete_oracle_error, find_unit_call)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revert_oracle(receiver: str, assertion: str, source: str,
                   observed: str | None = None) -> dict[str, Any]:
    return {
        "class": "R0",
        "kind": "revert" if source == "expectRevert" else "call-status",
        "source": source,
        "observed": "target call reverts" if source == "expectRevert" else observed,
        "expected": source == "expectRevert",
        "provenance": "stage2-witness",
        "target_receiver": receiver,
        "assertion": assertion,
    }


def materialize_revert_oracle(
    source: str, test_name: str, unit: str
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Render one exact revert oracle without changing the selected call."""
    # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches
    # pylint: disable=too-many-statements
    lines = source.splitlines()
    function = re.compile(r"^\s*function\s+" + re.escape(test_name) + r"\s*\(")
    start = next((index for index, line in enumerate(lines) if function.search(line)), None)
    if start is None:
        return source, [], "selected replay function is absent"
    depth = 0
    end = None
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if index > start and depth <= 0:
            end = index
            break
    if end is None:
        return source, [], "selected replay function is malformed"
    body = lines[start + 1:end]
    call_index = find_unit_call(body, unit)
    if call_index is None:
        return source, [], "selected replay has no target call"
    statement_start = call_index
    while statement_start > 0 and ";" not in body[statement_start - 1]:
        statement_start -= 1
    statement_end = call_index
    while statement_end + 1 < len(body) and ";" not in body[statement_end]:
        statement_end += 1
    statement = "\n".join(body[statement_start:statement_end + 1])
    suffix = "\n".join(body[statement_end + 1:])

    # The emitter already asserted these non-payable calls through their exact
    # low-level status.  Preserve that stronger executable shape verbatim.
    low_level = re.search(
        r"\(\s*bool\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*,[^)]*\)\s*=\s*"
        r"address\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*\)\s*\.\s*call"
        r"(?:\s*\{[^{}]*\})?\s*\([^;]*abi\s*\.\s*"
        r"encode(?:Call|WithSignature|WithSelector)\s*\([^;]*[\"']" +
        re.escape(unit) + r"\s*\([^;]*;", statement, re.S)
    if low_level is not None:
        status, receiver = low_level.groups()
        assertions = list(re.finditer(
            r"assertFalse\s*\(\s*" + re.escape(status) + r"\b[^;]*;", suffix, re.S))
        if len(assertions) != 1:
            return source, [], "low-level target call lacks one exact assertFalse"
        marker = "_veriput_revert_status"
        if re.search(r"\b" + marker + r"\b", "\n".join(body)):
            return source, [], "deterministic revert-status marker collides with replay"
        rewritten_statement = re.sub(
            r"(\bbool\s+)" + re.escape(status) + r"\b", r"\1" + marker,
            statement, count=1)
        raw_assertion = assertions[0].group(0)
        rewritten_assertion = re.sub(
            r"\b" + re.escape(status) + r"\b", marker, raw_assertion, count=1)
        rewritten_suffix = (suffix[:assertions[0].start()] + rewritten_assertion +
                            suffix[assertions[0].end():])
        rewritten_body = (body[:statement_start] + rewritten_statement.splitlines() +
                          rewritten_suffix.splitlines())
        lines[start + 1:end] = rewritten_body
        rendered = "\n".join(lines) + ("\n" if source.endswith("\n") else "")
        assertion = re.sub(r"\s+", " ", rewritten_assertion).strip()
        oracle = _revert_oracle(receiver, assertion, "low-level-call", marker)
        return rendered, [oracle], None

    direct = re.search(
        r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*" + re.escape(unit) +
        r"\s*(?:\{[^{}]*\}\s*)?\(", statement)
    if direct is None:
        return source, [], "selected replay target receiver is ambiguous"
    receiver = direct.group(1)

    prefix = "\n".join(body[:statement_start])
    armed = re.search(r"vm\s*\.\s*expectRevert\s*\([^;]*\)\s*;\s*$", prefix)
    if armed is not None:
        assertion = re.sub(r"\s+", " ", armed.group(0)).strip()
        return source, [_revert_oracle(receiver, assertion, "expectRevert")], None

    tolerant = re.search(
        r"\btry\s+(.+?)\s*\{\s*\}\s*catch\s*\{\s*\}\s*$", statement, re.S)
    indent = re.match(r"\s*", body[statement_start]).group(0)
    if tolerant is not None:
        call = re.sub(r"\s+", " ", tolerant.group(1)).strip()
        replacement = [f"{indent}vm.expectRevert();", f"{indent}{call};"]
        lines[start + 1 + statement_start:start + 1 + statement_end + 1] = replacement
    else:
        lines.insert(start + 1 + statement_start, f"{indent}vm.expectRevert();")
    assertion = "vm.expectRevert();"
    rendered = "\n".join(lines) + ("\n" if source.endswith("\n") else "")
    return rendered, [_revert_oracle(receiver, assertion, "expectRevert")], None


def recover_entry(record: dict[str, Any], scratch_root: Path
                 ) -> tuple[dict[str, Any] | None, str | None]:
    """Build a shared-driver entry for the exclusive revert-only partition."""
    # pylint: disable=too-many-locals,too-many-return-statements
    identity = backfill._recovery_identity(record)  # pylint: disable=protected-access
    kinds = tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or [])
    if identity is None or kinds != ("revert", ):
        return None, "record is outside the revert-only partition"
    digest, digest_error = backfill._record_identity_digest(record)  # pylint: disable=protected-access
    selected, selected_error = backfill._selected_put(record)  # pylint: disable=protected-access
    if digest is None or selected is None:
        return None, digest_error or selected_error
    put_source_path = Path(str(selected.get("source_path") or ""))
    emitted_path, emitted_test, error = backfill._find_recovery_emit_case(  # pylint: disable=protected-access
        record)
    if emitted_path is None or emitted_test is None:
        return None, error
    put_source = put_source_path.read_text(encoding="utf-8")
    emitted_source = emitted_path.read_text(encoding="utf-8")
    put_setup, _ = backfill._scoped_function_body(  # pylint: disable=protected-access
        put_source, str(selected.get("test") or ""), "setUp")
    emitted_setup, _ = backfill._scoped_function_body(  # pylint: disable=protected-access
        emitted_source, emitted_test, "setUp")
    if put_setup is None or emitted_setup is None or put_setup != emitted_setup:
        return None, "emitted exact CE setup differs from PUT setup"
    rendered, oracles, error = materialize_revert_oracle(
        emitted_source, emitted_test, identity[2])
    if error is not None:
        return None, error
    oracle_error = authenticated_concrete_oracle_error(oracles)
    if oracle_error:
        return None, oracle_error
    return backfill._entry_from_basis(  # pylint: disable=protected-access
        record, rendered, emitted_test, oracles, scratch_root, "revert-edge", {
            "emitted_source": str(emitted_path),
            "emitted_test": emitted_test,
            "identity_digest": digest,
        })


def _validated_selector(progress: dict[str, Any], inventory: dict[str, Any], reason: str,
                        frozen_unresolved: set[tuple[str, ...]]) -> tuple[
                            dict[tuple[str, ...], dict[str, Any]],
                            dict[tuple[str, ...], dict[str, Any]]]:
    """Require a duplicate-free selector with a one-to-one inventory join."""
    selected_rows = [
        row for row in progress.get("rows") or []
        if isinstance(row, dict) and row.get("status") == "refused"
        and row.get("reason") == reason
    ]
    identities = [tuple(str(value) for value in row.get("identity") or [])
                  for row in selected_rows]
    if any(len(identity) != 5 for identity in identities):
        raise ValueError("selector contains a malformed identity")
    if len(identities) != len(set(identities)):
        raise ValueError("selector contains a duplicate identity")
    if any(identity not in frozen_unresolved for identity in identities):
        raise ValueError("selector contains a non-frozen or resolved identity")
    selected = dict(zip(identities, selected_rows))

    records: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in inventory.get("records") or []:
        if not isinstance(record, dict):
            raise ValueError("inventory contains a non-object record")
        identity = backfill._recovery_identity(record)  # pylint: disable=protected-access
        if identity is None:
            raise ValueError("inventory contains a malformed identity")
        key = tuple(identity)
        if key in records:
            raise ValueError("inventory contains a duplicate identity")
        records[key] = record
    if set(selected) - set(records):
        raise ValueError("selector identity is absent from recovery inventory")
    for identity, row in selected.items():
        if row.get("record_identity_sha256") != records[identity].get("identity_sha256"):
            raise ValueError("selector and inventory record seals differ")
    return selected, records


def _output_path_error(output: Path, result_root: Path, protected: list[Path]) -> str | None:
    """Keep the generated report outside canonical and all sealed inputs."""
    if backfill._paths_overlap(output, result_root):  # pylint: disable=protected-access
        return "output overlaps canonical RQ1 results"
    if any(backfill._paths_overlap(output, path)  # pylint: disable=protected-access
           for path in protected):
        return "output overlaps a sealed input"
    return None


def main() -> int:
    """Materialize and seal the selected recovery partition in scratch."""
    # pylint: disable=too-many-locals,too-many-statements,too-many-boolean-expressions
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--identity-manifest", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--selector-progress", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    identity_manifest = json.loads(args.identity_manifest.read_text(encoding="utf-8"))
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    progress = json.loads(args.selector_progress.read_text(encoding="utf-8"))
    reason = "concrete replay lacks structured witness oracle provenance"
    if audit.get("schema") != "rq1-frozen905-mutually-exclusive-anchor-audit/v2":
        parser.error("audit is not the frozen-905 mutually exclusive selector")
    expected = next((row.get("count") for row in
                     ((audit.get("reason_counts") or {}).get("B") or [])
                     if row.get("reason") == reason), None)
    frozen = {
        tuple(str(value) for value in identity)
        for identity in ledger.get("obligations") or []
        if isinstance(identity, list) and len(identity) == 5
    }
    _generalized, unresolved, _no_put = obligations(args.result_root)
    frozen_unresolved = frozen & unresolved
    if len(frozen) != 1808 or audit.get("population_count") != 905:
        parser.error("ledger or audit does not state the frozen-905 population")
    try:
        selected, records = _validated_selector(
            progress, inventory, reason, frozen_unresolved)
    except ValueError as error:
        parser.error(str(error))
    if expected != len(selected):
        parser.error("progress selector does not equal the audited B85 parent class")
    manifest_identities = [
        tuple(str(value) for value in identity)
        for identity in identity_manifest.get("identities") or []
        if isinstance(identity, list) and len(identity) == 5
    ]
    expected_source_seals = {
        "audit": _sha256_file(args.audit),
        "selector_progress": _sha256_file(args.selector_progress),
        "ledger": _sha256_file(args.ledger),
    }
    if (_sha256_file(args.identity_manifest) != FROZEN_B85_IDENTITY_MANIFEST_SHA256
            or identity_manifest.get("schema") != "rq1-frozen905-b85-identity-manifest/v1"
            or identity_manifest.get("reason") != reason
            or identity_manifest.get("count") != len(manifest_identities)
            or len(manifest_identities) != len(set(manifest_identities))
            or identity_manifest.get("source_seals") != expected_source_seals
            or set(manifest_identities) != set(selected)):
        parser.error("B85 identity manifest is malformed, stale, or differs from selector")
    output_error = _output_path_error(
        args.output, args.result_root,
        [args.audit, args.identity_manifest, args.inventory, args.ledger,
         args.selector_progress])
    if output_error:
        parser.error(output_error)
    rows = []
    for key in sorted(selected):
        record = records[key]
        identity = list(key)
        entry, error = recover_entry(record, args.scratch_root)
        prepared = None
        if entry is not None:
            prepared, error = backfill._prepare(entry)  # pylint: disable=protected-access
        metadata = (prepared or {}).get("metadata") or {}
        report_binding = metadata.get("report_binding") or {}
        rows.append({
            "identity": identity,
            "record_identity_sha256": record.get("identity_sha256"),
            "status": "ready" if prepared is not None else "refused",
            "reason": error,
            "basis_source_sha256": (
                _sha256_file(Path(str(entry["basis"]["file"]))) if entry is not None else None),
            "certification_record_sha256": metadata.get("certification_record_sha256"),
            "certified_ce_sha256": metadata.get("certified_ce_sha256"),
            "claim_sha256": report_binding.get("claim_sha256"),
            "cov_report_sha256": report_binding.get("cov_report_sha256"),
        })
    if len(rows) != len(selected):
        parser.error("partition rows do not close over the selected identities")
    output = {
        "schema": "veriput-rq1-anchor-revert-edge-partition/v1",
        "definition": {
            "exclusive_parent_reason": reason,
            "anchor_required_kinds": ["revert"],
            "canonical_write": False,
        },
        "audit": str(args.audit),
        "audit_sha256": _sha256_file(args.audit),
        "inventory": str(args.inventory),
        "inventory_sha256": _sha256_file(args.inventory),
        "identity_manifest": str(args.identity_manifest),
        "identity_manifest_sha256": _sha256_file(args.identity_manifest),
        "ledger": str(args.ledger),
        "ledger_sha256": _sha256_file(args.ledger),
        "current_frozen_unresolved_count": len(frozen_unresolved),
        "audited_frozen_unresolved_count": audit.get("population_count"),
        "result_root": str(args.result_root),
        "selector_progress": str(args.selector_progress),
        "selector_progress_sha256": _sha256_file(args.selector_progress),
        "counts": {
            "selected": len(selected),
            "ready": sum(row["status"] == "ready" for row in rows),
            "refused": sum(row["status"] == "refused" for row in rows),
        },
        "rows": rows,
    }
    backfill._atomic_json(args.output, output)  # pylint: disable=protected-access
    print(json.dumps(output["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
