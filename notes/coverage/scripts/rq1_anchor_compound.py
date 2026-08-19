#!/usr/bin/env python3
"""Compose exact state and event replay oracles for one CE anchor.

The ordinary state materializer intentionally refuses a replay that already
has executable statements after its target call.  Compound claims therefore
need a small, deterministic coordinator: materialize all post-state reads
first, then insert the log window and event assertions.  No values are
inferred here; storage coordinates come from solc and event values from the
source-bound AST/claim materializer.
"""

from __future__ import annotations

import re
import hashlib
import json
from pathlib import Path
from typing import Any

from rq1_anchor_events import inject_event_oracles, render_event_oracles
from rq1_anchor_state_delta import materialize_state_delta_oracles
from solidity_path_put import _concrete_return_literal, find_unit_call


def _sha256_json(value: Any) -> str:
    """Hash the report's canonical JSON representation."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity(record: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    value = record.get("identity")
    if not isinstance(value, dict):
        return None
    keys = ("case", "path_function", "unit", "enc", "piece")
    if any(key not in value for key in keys[:4]):
        return None
    result = tuple(str(value.get(key, "")) for key in keys)
    # ``piece`` is canonically empty for the ordinary single-piece claim.
    return result if all(result[index] for index in range(4)) else None


def owns_record(record: dict[str, Any]) -> bool:
    """Return whether the record is exclusively owned by compound recovery.

    Compound materialization currently handles only a return projection (with
    or without the reverted-return projection).  State/event combinations are
    owned by their dedicated partitions and must fail closed here.
    """
    if not isinstance(record, dict) or record.get("recovery_category") != "directly-generatable":
        return False
    kinds = tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or ())
    if kinds == ("return", "revert"):
        return True
    if kinds != ("return",):
        return False
    # Scalar returns are handled by the ordinary return partition.  Compound
    # ownership is reserved for tuple-return materialization.
    value = (record.get("ce") or {}).get("return_value")
    if not (isinstance(value, str) and value.strip().startswith("(")
            and value.strip().endswith(")")):
        return False
    return "," in value.strip()[1:-1]


def obligation_key(record: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    """Return the stable five-field identity used by partition selectors."""
    return _identity(record)


def executable_claim(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Load and authenticate the one exact report claim for a compound row.

    This function deliberately reads only retained evidence.  It does not
    synthesize a claim from the CE row, and it rejects missing/ambiguous
    reports, identity mismatches, and digest mismatches.
    """
    if not owns_record(record):
        return None, "record is not owned by compound recovery"
    identity = _identity(record)
    if identity is None:
        return None, "compound record identity is malformed"
    provenance = record.get("claim_provenance") or {}
    # Tiny in-memory callers may provide only a CE projection.  Production
    # recovery rows always carry selected_put and claim_provenance, so this
    # compatibility path cannot authorize a canonical backfill.
    if not provenance and not record.get("selected_put"):
        ce = record.get("ce")
        if isinstance(ce, dict) and ce.get("exit_kind") in ("normal", "revert"):
            projected = dict(ce)
            if tuple((record.get("observable_evidence") or {}).get(
                    "anchor_required_kinds") or ()) == ("return", "revert"):
                projected.pop("return_value", None)
                projected["return_value_known"] = False
                projected["compound_projection"] = "exclude-reverted-return/v1"
            return projected, None
    report_path = Path(str(provenance.get("report_path") or ""))
    if not report_path.is_file():
        return None, "compound claim report is absent"
    report_sha = provenance.get("report_sha256")
    try:
        raw = report_path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, "compound claim report is unreadable or malformed"
    if report_sha and hashlib.sha256(raw).hexdigest() != report_sha:
        return None, "compound claim report hash mismatch"
    if not isinstance(document, dict) or not isinstance(document.get("claims"), list):
        return None, "compound claim report has no claims list"
    matches = [
        claim for claim in document["claims"]
        if isinstance(claim, dict)
        and claim.get("path_function") == identity[1]
        and str(claim.get("path_id")) == identity[3]
    ]
    if len(matches) != 1:
        return None, f"expected one exact compound claim, found {len(matches)}"
    claim = matches[0]
    claim_sha = provenance.get("claim_sha256")
    if claim_sha and _sha256_json(claim) != claim_sha:
        return None, "compound claim digest mismatch"
    if claim.get("exit_kind") not in ("normal", "revert"):
        return None, "compound claim has no definite exit kind"
    if not isinstance(claim.get("inputs"), dict):
        return None, "compound claim has no executable inputs"
    executable = dict(claim)
    if identity and tuple((record.get("observable_evidence") or {}).get(
            "anchor_required_kinds") or ()) == ("return", "revert"):
        # A return observed on a reverted path is a solver trace artifact, not
        # an executable post-revert value.  Keep the authenticated claim but
        # explicitly project that value out for coverage checking.
        executable.pop("return_value", None)
        executable["return_value_known"] = False
        executable["compound_projection"] = "exclude-reverted-return/v1"
    return executable, None


def strict_partition_artifact(raw_report: dict[str, Any], status_path: Path) -> dict[str, Any]:
    """Filter a compound report to identities exclusively assigned to it.

    This is an authorization-shaped transformation only: it never marks a
    row ready unless the upstream report already did so.
    """
    assignments = {}
    try:
        status = json.loads(Path(status_path).read_text(encoding="utf-8"))
        assignments = status.get("exclusive_identity_assignments", {}).get(
            "anchor_compound", [])
    except (OSError, json.JSONDecodeError):
        assignments = []
    owned = {tuple(str(item) for item in identity) for identity in assignments
             if isinstance(identity, list) and len(identity) == 5}
    rows = raw_report.get("rows") if isinstance(raw_report, dict) else []
    rows = rows if isinstance(rows, list) else []
    selected = []
    excluded = []
    for row in rows:
        identity = tuple(str(item) for item in row.get("identity", []))
        if identity in owned:
            selected.append(dict(row))
        else:
            excluded.append(identity)
    payload = {
        "schema": "veriput-rq1-anchor-compound-partition/v2",
        "rows": rows,
        "ready": [row for row in selected if row.get("status") == "ready"],
        "exclusive_owned": len(selected),
        "excluded_by_precedence": len(excluded),
        "excluded_identities": [list(identity) for identity in excluded],
        "inventory": raw_report.get("inventory"),
        "inventory_sha256": raw_report.get("inventory_sha256"),
        "ownership_status": str(status_path),
    }
    payload["ownership_status_sha256"] = hashlib.sha256(
        Path(status_path).read_bytes()).hexdigest() if Path(status_path).is_file() else None
    return payload


def bind_prepared_rows(strict: dict[str, Any], progress_path: Path) -> dict[str, Any]:
    """Bind prepared progress rows to the strict partition identities."""
    progress = json.loads(Path(progress_path).read_text(encoding="utf-8"))
    strict_ids = {tuple(row.get("identity", [])) for row in strict.get("ready", [])}
    prepared = [row for row in progress.get("rows", [])
                if tuple(row.get("identity", [])) in strict_ids
                and row.get("status") in ("ready", "validated", "embedded")]
    result = dict(strict)
    result["ready"] = prepared
    result["observable_ready"] = len(prepared)
    result["preparation_progress"] = str(progress_path)
    result["preparation_progress_sha256"] = hashlib.sha256(
        Path(progress_path).read_bytes()).hexdigest()
    return result


def add_indexed_return_oracles(source: str, test_name: str, unit: str,
                               rettypes: list[tuple[str, str]],
                               witness_value: Any) -> tuple[str, list[dict[str, Any]], str | None]:
    """Bind a tuple-return replay to each ABI component.

    The declaration order comes from the source-bound AST.  This helper only
    renders typed assertions; it never infers component types from witness
    text.  A malformed or arity-mismatched witness fails closed.
    """
    if not rettypes or not isinstance(witness_value, str):
        return source, [], "tuple return witness is absent"
    raw = witness_value.strip()
    if not (raw.startswith("(") and raw.endswith(")")):
        return source, [], "tuple return witness is not parenthesized"
    values = [item.strip() for item in raw[1:-1].split(",")]
    if len(values) != len(rettypes):
        return source, [], (f"tuple return witness arity {len(values)} differs from "
                            f"ABI arity {len(rettypes)}")
    lines = source.splitlines()
    fn_re = re.compile(r"^\s*function\s+" + re.escape(test_name) + r"\s*\(")
    start = next((i for i, line in enumerate(lines) if fn_re.search(line)), None)
    if start is None:
        return source, [], "selected test function is absent"
    depth = 0
    end = None
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if index > start and depth <= 0:
            end = index
            break
    if end is None:
        return source, [], "selected test function is unclosed"
    body = lines[start + 1:end]
    call_i = find_unit_call(body, unit)
    if call_i is None:
        return source, [], "selected target call is absent or ambiguous"
    receiver = (re.search(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*" +
                          re.escape(unit) + r"\s*\(", body[call_i]) or
                [None, "target"])[1]
    names = [f"_veriput_concrete_return_{index}" for index in range(len(rettypes))]
    lhs = ", ".join(f"{sol_type} {name}" for name, (_label, sol_type) in zip(names, rettypes))
    call = body[call_i]
    indent = re.match(r"\s*", call).group(0)
    body[call_i] = f"{indent}({lhs}) = {call.strip()}"
    oracles = []
    for index, ((_label, sol_type), value, name) in enumerate(zip(rettypes, values, names)):
        expected = _concrete_return_literal(sol_type, value)
        if expected is None:
            return source, [], f"tuple component {index} has no typed literal"
        assertion = (f'assertEq({name}, {expected}, '
                     f'"fixed witness return component {index} must match");')
        body.insert(call_i + 1 + index, indent + assertion)
        oracles.append({
            "class": "R0", "kind": "return-value", "return_index": index,
            "return_arity": len(rettypes), "solidity_type": sol_type,
            "observed": name, "expected": expected,
            "provenance": "stage2-witness", "target_receiver": receiver,
            "assertion": assertion,
        })
    lines[start + 1:end] = body
    return "\n".join(lines) + "\n", oracles, None


def materialize_compound_oracles(
    source: str,
    test_name: str,
    unit: str,
    state_delta: dict[str, Any],
    storage: tuple[dict[str, tuple[int, int, int]], dict[str, tuple[Any, ...]]],
    ast: dict[str, Any],
    claim: dict[str, Any],
    target_receiver: str = "c0",
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Materialize exact post-state and event assertions in one replay.

    State insertion is deliberately done before event insertion because the
    state materializer uses absence of post-call executable code as a safety
    boundary.  ``inject_event_oracles`` then adds only the log window and
    assertions around the same direct target call.
    """
    if not isinstance(ast, dict) or not ast:
        return source, [], "compound claim has no source-bound AST"
    try:
        event_oracles = render_event_oracles(ast, claim, target_receiver)
    except (TypeError, ValueError) as exc:
        return source, [], f"event materializer refused evidence: {exc}"
    if not event_oracles:
        return source, [], "compound claim has no event oracle"
    rewritten, state_oracles, error = materialize_state_delta_oracles(
        source, test_name, unit, state_delta, storage)
    if error:
        return source, [], f"state materializer refused evidence: {error}"
    try:
        rewritten = inject_event_oracles(rewritten, test_name, unit, event_oracles)
    except (TypeError, ValueError) as exc:
        return source, [], f"event injection refused evidence: {exc}"
    return rewritten, state_oracles + event_oracles, None


def oracle_kinds(oracles: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return stable oracle kinds for audit records."""
    return tuple(str(item.get("kind") or "") for item in oracles)
