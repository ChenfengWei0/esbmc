#!/usr/bin/env python3
"""Backfill authenticated fixed-CE anchors into canonical RQ1 PUTs.

This tool never invents an oracle.  It accepts only a retained concrete basis
whose exact assertion metadata is present in the strict-valid artifact row,
whose target identity and setup match the PUT, and whose assertion text occurs
in the retained zero-parameter test body.  The original basis function is
copied byte-for-byte except for its function name.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

from put_all import (  # noqa: E402
    FORGE_STD, _matching_delimiter, _solidity_code_mask, _solidity_test_span, certified_ce_sha256,
    certified_source_projection_error, forge_json_status_map, project_rel_file,
    _solidity_function_spans)
from solidity_path_put import (  # pylint: disable=import-error  # noqa: E402
    _oracle_claim_coverage_error, add_concrete_fixed_return_oracle, add_concrete_normal_exit_oracle,
    authenticated_concrete_oracle_error, event_signatures_from_ast, find_unit_call,
    source_inherited_function_returns)
from rq1_concrete_replay_migrate import (  # noqa: E402
    DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests)
from rq1_concrete_replay_store import (  # noqa: E402
    _artifact_key, _oracle_binding_errors, _physical_test_kind, _structured_oracle_errors)
from rq1_final_test_inventory import _anchor_strength_audit  # noqa: E402
from rq1_anchor_compound import (  # noqa: E402
    add_indexed_return_oracles, executable_claim, owns_record)
from rq1_anchor_events import (  # noqa: E402
    load_solast, recover_entry as recover_event_entry)
from rq1_anchor_state_delta import (  # noqa: E402
    isolated_storage_layout, materialize_state_delta_oracles)
from rq1_anchor_setup_recovery import reconcile_selected_contract_setup  # noqa: E402

DEFAULT_MANIFEST = HERE.parent / "rq1_put_ce_anchor_backfill.frozen.json"
DEFAULT_PROGRESS = HERE.parent / "rq1_put_ce_anchor_backfill.progress.json"
DEFAULT_RECOVERY_SCRATCH = Path("/tmp/rq1-put-ce-anchor-recovery")

SUPPORTED_PARTITIONS = frozenset(("failures", "events", "state", "compound", "setup",
                                  "b340-state", "revert-edge"))

FROZEN_B85_IDENTITY_MANIFEST_SHA256 = (
    "52fa354c2836ec3248bc29f07c16e706e8fd1a33a0002a6af97f3d58fbd4790b")
FROZEN_B214_SETUP_PARTITION_SHA256 = (
    "20f9aab7f7f4d1eb514f201f53a37dfafeec0aa0359a96087a39a6dd121c5377")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                     encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _render_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                     encoding="utf-8") as stream:
        stream.write(value)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _restore_transaction_files(originals: dict[Path, str | None],
                               written: dict[Path, str | None] | None = None) -> list[str]:
    """Restore our writes only; preserve any concurrent replacement."""
    conflicts = []
    for path, original in originals.items():
        if written is not None and path not in written:
            continue
        if written is not None:
            current = path.read_text(encoding="utf-8") if path.is_file() else None
            if current != written[path]:
                conflicts.append(str(path))
                continue
        if original is None:
            path.unlink(missing_ok=True)
        else:
            _atomic_text(path, original)
    return conflicts


@contextlib.contextmanager
def _transaction_lock(shared_result: Path):
    """Serialize commits that update the same shared result.json."""
    lock_root = Path("/tmp/rq1-put-ce-anchor-locks")
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_name = _sha256_text(str(shared_result.resolve())) + ".lock"
    lock_path = lock_root / lock_name
    with lock_path.open("w", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity(case: str, row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    key = _artifact_key(row)
    return case, *(str(item) for item in key)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _entry_event_signatures(entry: dict[str, Any]) -> dict[int, str]:
    """Recover EventDefinition identities from retained verifier inputs."""
    sealed = entry.get("event_signatures")
    if isinstance(sealed, dict) and all(
            type(key) is int and isinstance(value, str) for key, value in sealed.items()):
        return sealed
    result_path = str(entry.get("result_json") or "")
    result = _load_json(Path(result_path)) if result_path else {}
    paths = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str) and value.endswith(".solast"):
            paths.add(value)

    collect(result.get("verifier_input_identity") or result)
    signatures: dict[int, str] = {}
    for raw_path in sorted(paths):
        document = _load_json(Path(raw_path))
        for node_id, signature in event_signatures_from_ast(document).items():
            previous = signatures.get(node_id)
            if previous is not None and previous != signature:
                return {}
            signatures[node_id] = signature
    return signatures


def _subject_dir_for_source(source: Path) -> Path | None:
    for parent in (source.parent, *source.parents):
        if (parent / "result.json").is_file() and (parent / "cert").is_dir():
            return parent
    return None


def _recovery_identity(record: dict[str, Any]) -> list[str] | None:
    identity = record.get("identity")
    if not isinstance(identity, dict):
        return None
    values = [
        identity.get("case"),
        identity.get("path_function"),
        identity.get("unit"),
        identity.get("enc"),
        identity.get("piece"),
    ]
    if any(value is None for value in values[:4]):
        return None
    return [
        str(values[0]),
        str(values[1]),
        str(values[2]),
        str(values[3]), "" if values[4] is None else str(values[4])
    ]


def _piece_value(identity: list[str]) -> str | None:
    return None if identity[4] == "" else identity[4]


def _same_recovery_piece(artifact_piece: str, detail_piece: Any) -> bool:
    normalized_detail = "" if detail_piece is None else str(detail_piece)
    return artifact_piece == normalized_detail or (artifact_piece == ""
                                                   and normalized_detail == "1")


def _identity_list(record: dict[str, Any]) -> list[str] | None:
    """Accept either recovery-record or artifact-row identity shapes."""
    identity = record.get("identity")
    if isinstance(identity, list) and len(identity) == 5:
        return [str(value or "") for value in identity]
    return _recovery_identity(record)


def _identity_digest(identity: list[str]) -> str:
    return _sha256_text("\t".join(identity))


def _record_identity_digest(record: dict[str, Any]) -> tuple[str | None, str | None]:
    identity = _recovery_identity(record)
    if identity is None:
        return None, "malformed recovery identity"
    digest = _identity_digest(identity)
    if record.get("identity_sha256") != digest:
        return None, "recovery identity digest mismatch"
    return digest, None


def _paths_overlap(first: Path, second: Path) -> bool:
    left = first.resolve()
    right = second.resolve()
    return left.is_relative_to(right) or right.is_relative_to(left)


def _scratch_root_error(scratch_root: Path) -> str | None:
    if _paths_overlap(scratch_root, DEFAULT_RESULT_ROOT):
        return "recovery scratch root overlaps canonical RQ1 results"
    return None


def _canonical_scratch_error(scratch_root: Path, source: Path) -> str | None:
    subject = _subject_dir_for_source(source)
    project = _project_root(source)
    for label, canonical in (("subject", subject), ("project", project)):
        if canonical is not None and _paths_overlap(scratch_root, canonical):
            return f"recovery scratch root overlaps canonical {label}"
    return None


def _load_partition_rows(path: Path, partition: str) -> list[dict[str, Any]]:
    """Load only the independently audited ready selector for one partition.

    These rows authorize candidate selection, never adoption.  The retained
    evidence and both Forge gates are rechecked by this driver.
    """
    document = _load_json(path)
    schema = str(document.get("schema") or "")
    rows: Any
    if partition == "failures":
        if schema != "veriput-anchor-failure-retry-bundle/v1":
            raise RuntimeError("failure bundle has an unexpected schema")
        rows = document.get("ready")
    elif partition == "events":
        if schema != "veriput-rq1-anchor-events-dry-run/v1":
            raise RuntimeError("event inventory has an unexpected schema")
        rows = document.get("ready")
    elif partition == "state":
        rows = [
            row for row in document.get("rows") or []
            if isinstance(row, dict) and row.get("status") == "green"
        ]
    elif partition == "b340-state":
        if schema != "veriput-rq1-anchor-b340-state-partition/v1":
            raise RuntimeError("B340 state partition has an unexpected schema")
        inputs = document.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise RuntimeError("B340 state partition has no sealed inputs")
        for item in inputs:
            path = Path(str((item or {}).get("path") or ""))
            if (_sha256_file(path) != (item or {}).get("sha256")
                    or path.stat().st_size != (item or {}).get("bytes")):
                raise RuntimeError("B340 state partition input seal is invalid")
        rows = document.get("rows")
        if (not isinstance(rows, list) or document.get("exclusive_owned") != len(rows) or any(
                row.get("status") != "selected" or row.get("required_kinds") != ["state-delta"]
                or not row.get("record_identity_sha256") for row in rows)):
            raise RuntimeError("B340 state partition ownership is invalid")
    elif partition == "revert-edge":
        if schema != "veriput-rq1-anchor-revert-edge-partition/v1":
            raise RuntimeError("revert-edge partition has an unexpected schema")
        sealed_inputs = (
            ("audit", "audit_sha256"),
            ("identity_manifest", "identity_manifest_sha256"),
            ("inventory", "inventory_sha256"),
            ("ledger", "ledger_sha256"),
            ("selector_progress", "selector_progress_sha256"),
        )
        if any(_sha256_file(Path(str(document.get(path_field) or "")))
               != document.get(hash_field) for path_field, hash_field in sealed_inputs):
            raise RuntimeError("revert-edge partition input seal is invalid")
        counts = document.get("counts") or {}
        all_rows = document.get("rows")
        manifest = _load_json(Path(str(document.get("identity_manifest") or "")))
        manifest_identities = [
            tuple(str(value) for value in identity)
            for identity in manifest.get("identities") or []
            if isinstance(identity, list) and len(identity) == 5
        ]
        row_identities = [tuple(_identity_list(row) or ()) for row in all_rows or []]
        if (document.get("identity_manifest_sha256")
                != FROZEN_B85_IDENTITY_MANIFEST_SHA256
                or manifest.get("schema") != "rq1-frozen905-b85-identity-manifest/v1"
                or manifest.get("count") != 29
                or len(manifest_identities) != len(set(manifest_identities))
                or not isinstance(all_rows, list)
                or counts != {"selected": 29, "ready": 17, "refused": 12}
                or len(all_rows) != 29
                or set(row_identities) != set(manifest_identities)
                or any((row.get("status") not in ("ready", "refused")
                        or not row.get("record_identity_sha256")) for row in all_rows)):
            raise RuntimeError("revert-edge partition ownership is invalid")
        rows = [row for row in all_rows if row.get("status") == "ready"]
        required_seals = ("basis_source_sha256", "certification_record_sha256",
                          "certified_ce_sha256", "claim_sha256", "cov_report_sha256")
        if len(rows) != 17 or any(not all(row.get(field) for field in required_seals)
                                  for row in rows):
            raise RuntimeError("revert-edge ready evidence seals are incomplete")
    elif partition == "compound":
        if schema != "veriput-rq1-anchor-compound-partition/v2":
            raise RuntimeError("compound report has an unexpected schema")
        if (document.get("exclusive_owned") != len(document.get("rows") or [])
                or document.get("inventory_sha256") != _sha256_file(
                    Path(str(document.get("inventory") or "")))
                or document.get("ownership_status_sha256") != _sha256_file(
                    Path(str(document.get("ownership_status") or "")))):
            raise RuntimeError("compound partition ownership or input seal is invalid")
        preparation = document.get("preparation_progress")
        if (preparation is not None and document.get("preparation_progress_sha256") != _sha256_file(
                Path(str(preparation)))):
            raise RuntimeError("compound partition preparation seal is invalid")
        rows = document.get("ready")
    elif partition == "setup":
        if schema != "veriput-rq1-anchor-setup-mismatch-classification/v1":
            raise RuntimeError("setup classification has an unexpected schema")
        if _sha256_file(path) != FROZEN_B214_SETUP_PARTITION_SHA256:
            raise RuntimeError("setup partition differs from the frozen B214 authorization")
        if (document.get("partition_contract") != "frozen905-b214-selected-contract-setup/v1"
                or document.get("semantic_classifier") !=
                "solidity-token-stream-literals-and-boundaries-preserved/v2"
                or (document.get("selection") or {}).get("count") != 214):
            raise RuntimeError("setup partition scope is not the frozen B214 selector")
        rows_payload = json.dumps(document.get("rows") or [], sort_keys=True,
                                  separators=(",", ":"))
        if document.get("rows_payload_sha256") != _sha256_text(rows_payload):
            raise RuntimeError("setup partition row payload seal is invalid")
        inputs = document.get("inputs") or []
        expected_roles = {
            "frozen905-audit", "first-pass-progress", "source-recovery-inventory",
            "source-setup-classification"
        }
        if (len(inputs) != len(expected_roles)
                or {str((item or {}).get("role") or "") for item in inputs} != expected_roles):
            raise RuntimeError("setup partition does not have the fixed input roles")
        for item in inputs:
            input_path = Path(str((item or {}).get("path") or ""))
            if (_sha256_file(input_path) != (item or {}).get("sha256")
                    or input_path.stat().st_size != (item or {}).get("bytes")):
                raise RuntimeError("setup partition input seal is invalid")
        for report in document.get("validation_reports") or []:
            path = Path(str((report or {}).get("path") or ""))
            if (_sha256_file(path) != (report or {}).get("sha256")
                    or path.stat().st_size != (report or {}).get("bytes")):
                raise RuntimeError("setup external validation report seal is invalid")
        rows = [
            row for row in document.get("rows") or []
            if isinstance(row, dict) and row.get("decision") == "safe_without_esbmc"
        ]
    else:
        raise RuntimeError(f"unsupported recovery partition: {partition}")
    if not isinstance(rows, list):
        raise RuntimeError(f"{partition} artifact has no candidate rows")
    identities = [_identity_list(row) for row in rows]
    if any(identity is None for identity in identities):
        raise RuntimeError(f"{partition} artifact has a malformed identity")
    keys = [tuple(identity or []) for identity in identities]
    if len(keys) != len(set(keys)):
        raise RuntimeError(f"{partition} artifact has duplicate identities")
    return rows


def _recovery_record_index(inventory_path: Path) -> dict[tuple[str, ...], dict[str, Any]]:
    document = _load_json(inventory_path)
    records = document.get("records")
    if not isinstance(records, list):
        raise RuntimeError("recovery inventory has no records list")
    result = {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError("recovery inventory contains a non-object record")
        identity = _recovery_identity(record)
        _digest, digest_error = _record_identity_digest(record)
        if identity is None or digest_error or tuple(identity) in result:
            raise RuntimeError("recovery inventory identity is malformed or duplicated")
        result[tuple(identity)] = record
    return result


def _sealed_partition_inventory_error(partition: str, artifact_path: Path,
                                      inventory_path: Path) -> str | None:
    """Bind the consumed inventory to the file sealed by strict partitions."""
    if partition not in ("b340-state", "setup", "revert-edge"):
        return None
    document = _load_json(artifact_path)
    if partition == "revert-edge":
        sealed_path = Path(str(document.get("inventory") or ""))
        if (sealed_path.resolve() != inventory_path.resolve()
                or document.get("inventory_sha256") != _sha256_file(inventory_path)):
            return "consumed recovery inventory differs from the revert-edge seal"
        return None
    if partition == "setup":
        sealed = document.get("consumer_inventory") or {}
        if (Path(str(sealed.get("path") or "")).resolve() != inventory_path.resolve()
                or sealed.get("sha256") != _sha256_file(inventory_path)
                or sealed.get("bytes") != inventory_path.stat().st_size):
            return "consumed recovery inventory differs from the setup partition seal"
        return None
    sealed = [
        item for item in document.get("inputs") or []
        if isinstance(item, dict) and item.get("role") == "recovery-inventory"
    ]
    if len(sealed) != 1:
        return "B340 state partition has no unique recovery-inventory seal"
    item = sealed[0]
    if (Path(str(item.get("path") or "")).resolve() != inventory_path.resolve()
            or item.get("sha256") != _sha256_file(inventory_path)
            or item.get("bytes") != inventory_path.stat().st_size):
        return "consumed recovery inventory differs from the B340 state seal"
    return None


def _selected_put(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    selected = record.get("selected_put")
    if not isinstance(selected, dict):
        return None, "recovery record has no selected PUT"
    source = Path(str(selected.get("source_path") or ""))
    put_json = Path(str(selected.get("put_json_path") or ""))
    if not source.is_file() or not put_json.is_file():
        return None, "selected PUT source or put.json is absent"
    if (not selected.get("source_sha256") or _sha256_file(source) != selected.get("source_sha256")):
        return None, "selected PUT source hash mismatch"
    if (not selected.get("put_json_sha256")
            or _sha256_file(put_json) != selected.get("put_json_sha256")):
        return None, "selected PUT put.json hash mismatch"
    return selected, None


def _put_document_identity_error(document: dict[str, Any], selected: dict[str, Any],
                                 identity: list[str], source: Path) -> str | None:
    """Require the physical put.json to state the same full obligation."""
    expected_piece = _piece_value(identity)
    checks = (
        (str(document.get("test") or ""), str(selected.get("test") or ""), "test"),
        (str(document.get("path_function") or ""), identity[1], "path_function"),
        (str(document.get("unit") or ""), identity[2], "unit"),
        (str(document.get("enc") or ""), identity[3], "enc"),
        ("" if document.get("piece") is None else str(document.get("piece")),
         "" if expected_piece is None else str(expected_piece), "piece"),
    )
    for actual, expected, field in checks:
        if actual != expected:
            return f"selected PUT put.json differs on {field}"
    try:
        recorded_source = Path(str(document.get("file") or ""))
        if recorded_source.resolve() != source.resolve():
            if recorded_source.exists() or recorded_source.name != source.name:
                return "selected PUT put.json differs on physical source"
    except OSError:
        return "selected PUT put.json physical source is invalid"
    return None


def _current_strength_entry(record: dict[str, Any]) -> dict[str, Any] | None:
    """Build the minimum current row needed for idempotent strength audit."""
    identity = _recovery_identity(record)
    selected = record.get("selected_put") or {}
    source = Path(str(selected.get("source_path") or ""))
    put_json = Path(str(selected.get("put_json_path") or ""))
    subject = _subject_dir_for_source(source) if source.is_file() else None
    document = _load_json(put_json) if put_json.is_file() else {}
    if identity is None or subject is None or not document:
        return None
    if _put_document_identity_error(document, selected, identity, source):
        return None
    put = dict(document)
    put.update({
        "file": str(source),
        "put_json": str(put_json),
        "test": selected.get("test") or document.get("test"),
        "path_function": identity[1],
        "unit": identity[2],
        "enc": int(identity[3]) if identity[3].isdigit() else identity[3],
        "piece": _piece_value(identity),
    })
    return {"identity": identity, "subject_dir": str(subject), "put": put}


def _entry_from_basis(record: dict[str, Any], basis_source: str, basis_test: str,
                      oracles: list[dict[str, Any]], scratch_root: Path, partition: str,
                      evidence: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Seal a rewritten retained replay into the legacy strict entry shape."""
    identity = _recovery_identity(record)
    identity_digest, digest_error = _record_identity_digest(record)
    selected, error = _selected_put(record)
    if identity is None or identity_digest is None or selected is None:
        return None, digest_error or error or "malformed recovery identity"
    put_source = Path(str(selected["source_path"]))
    put_json = Path(str(selected["put_json_path"]))
    detail, error = _recovery_certified_detail(record, identity)
    if detail is None:
        return None, error
    subject_dir = _subject_dir_for_source(put_source)
    if subject_dir is None:
        return None, "canonical subject directory is absent"
    scratch_error = _canonical_scratch_error(scratch_root, put_source)
    if scratch_error:
        return None, scratch_error
    case_parts = identity[0].split("/", 1)
    if (len(case_parts) != 2 or subject_dir.name != case_parts[1]
            or subject_dir.parent.name != "subjects"
            or subject_dir.parent.parent.name != case_parts[0]):
        return None, "selected PUT physical subject differs from recovery case"
    scratch_dir = scratch_root / partition / identity_digest
    scratch_dir.mkdir(parents=True, exist_ok=True)
    basis_file = scratch_dir / (
        Path(str((record.get("claim_provenance") or {}).get("report_path") or "basis")).stem +
        ".cov.t.sol")
    _atomic_text(basis_file, basis_source)
    put_doc = _load_json(put_json)
    identity_error = _put_document_identity_error(put_doc, selected, identity, put_source)
    if identity_error:
        return None, identity_error
    put_row = dict(put_doc)
    put_row.update({
        "file": str(put_source),
        "put_json": str(put_json),
        "test": selected.get("test") or put_doc.get("test"),
        "path_function": identity[1],
        "unit": identity[2],
        "enc": int(identity[3]) if identity[3].isdigit() else identity[3],
        "piece": _piece_value(identity),
    })
    return {
        "identity": identity,
        "case": identity[0],
        "subject_dir": str(subject_dir),
        "result_json": str(subject_dir / "result.json"),
        "put": put_row,
        "basis": {
            "file": str(basis_file),
            "test": basis_test,
            "put_json": str(put_json),
            "path_function": identity[1],
            "unit": identity[2],
            "enc": put_row["enc"],
            "piece": put_row["piece"],
            "concrete_oracles": oracles,
        },
        "basis_ambiguity": 0,
        "recovery_certified_detail": detail,
        "recovery": {
            "record_identity_sha256":
            record.get("identity_sha256"),
            "required_kinds":
            list((record.get("observable_evidence") or {}).get("anchor_required_kinds") or []),
            "partition":
            partition,
            **evidence,
        },
    }, None


def _find_recovery_emit_case(record: dict[str, Any]) -> tuple[Path | None, str | None, str | None]:
    identity = _recovery_identity(record)
    if identity is None:
        return None, None, "malformed recovery identity"
    report_path = Path(str(((record.get("claim_provenance") or {}).get("report_path")) or ""))
    if not report_path.is_file():
        return None, None, "recovery report is absent"
    sources = sorted(report_path.parent.glob("*.cov.t.sol"))
    if len(sources) != 1:
        return None, None, f"expected one emitted source, found {len(sources)}"
    source_path = sources[0]
    source = source_path.read_text(encoding="utf-8")
    claim_label = re.escape(identity[1]) + r":path:" + re.escape(identity[3])
    matches = []
    for match in re.finditer(r"\bfunction\s+(test_[A-Za-z0-9_]+)\s*\(", source):
        test_name = match.group(1)
        span, _reason = _solidity_test_span(source, test_name)
        if span is None:
            continue
        lines = source[:span[0]].splitlines()
        adjacent = []
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("//")):
            adjacent.append(lines.pop())
        block = "\n".join(reversed(adjacent))
        if re.search(r"^\s*//\s*claim:\s*" + claim_label + r"\s*$", block, re.M):
            matches.append(test_name)
    if len(matches) != 1:
        return source_path, None, f"expected one emitted exact test, found {len(matches)}"
    return source_path, matches[0], None


def _put_contract_and_flat(put_source_path: Path,
                           put_source: str) -> tuple[str | None, Path | None]:
    receiver = re.search(r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s+c0\s*(?:=|;)", put_source,
                         re.M)
    if receiver is None:
        return None, None
    contract = receiver.group(1)
    imports = re.finditer(
        r'import\s*\{[^{}]*\b' + re.escape(contract) +
        r'\b[^{}]*\}\s*from\s*["\']([^"\']+)["\']\s*;', put_source)
    for match in imports:
        candidate = (put_source_path.parent / match.group(1)).resolve()
        if candidate.is_file():
            return contract, candidate
    candidate = (put_source_path.parent / "../src/flat.sol").resolve()
    return contract, candidate if candidate.is_file() else None


def _recovery_return_types(record: dict[str, Any], put_source_path: Path,
                           put_source: str) -> list[tuple[str, str]] | None:
    # pylint: disable=not-an-iterable
    contract, flat_path = _put_contract_and_flat(put_source_path, put_source)
    if contract is None or flat_path is None:
        return None
    try:
        flat_source = flat_path.read_text(encoding="utf-8")
    except OSError:
        return None
    returns: Any = source_inherited_function_returns(
        flat_source, contract, str((record.get("identity") or {}).get("unit") or ""))
    if not isinstance(returns, list):
        return None
    declared: list[tuple[str, str]] = []
    for item in returns:
        if not (isinstance(item, tuple) and len(item) == 2
                and all(isinstance(component, str) for component in item)):
            return None
        declared.append(item)
    return declared or None


def _recovery_certified_detail(record: dict[str, Any],
                               identity: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    basis = record.get("certified_basis") or {}
    path = Path(str(basis.get("source_path") or ""))
    if not path.is_file():
        return None, "recovery certification record is absent"
    lines = path.read_text(encoding="utf-8").splitlines()
    candidates = []
    source_line = basis.get("source_line")
    if type(source_line) is int and 1 <= source_line <= len(lines):
        line = lines[source_line - 1]
        if (basis.get("source_line_sha256")
                and _sha256_text(line) != basis.get("source_line_sha256")):
            return None, "recovery certification line hash mismatch"
        candidates.append(line)
    else:
        candidates.extend(lines)
    matches = []
    for line in candidates:
        try:
            record_json = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record_json, dict):
            continue
        if record_json.get("path_function") != identity[1] or record_json.get(
                "unit") != identity[2]:
            continue
        details = (record_json.get("stage2_observed_certified_details")
                   or record_json.get("certified_details") or {})
        detail = details.get(identity[3]) if isinstance(details, dict) else None
        if not isinstance(detail, dict) or detail.get("verdict") != "CERTIFIED":
            continue
        if not _same_recovery_piece(identity[4], detail.get("piece")):
            continue
        if basis.get("detail_sha256") and _sha256_text(
                json.dumps(detail, sort_keys=True,
                           separators=(",", ":"))) != basis.get("detail_sha256"):
            continue
        detail = dict(detail)
        detail["_certification_record_sha256"] = _sha256_text(
            json.dumps(record_json, sort_keys=True, separators=(",", ":")))
        matches.append(detail)
    unique = {_sha256_text(json.dumps(item, sort_keys=True)): item for item in matches}
    if len(unique) != 1:
        return None, f"expected one recovery certified detail, found {len(unique)}"
    return next(iter(unique.values())), None


def _sealed_history_report(
        record: dict[str, Any], identity: list[str], put_source_path: Path,
        emit_source_path: Path, emit_source: str,
        emit_test: str) -> tuple[Path | None, str | None, str | None]:
    """Authenticate an isolated historical source and its canonical report."""
    history = record.get("sealed_history_recovery") or {}
    if not history:
        return None, None, None
    staged_source = Path(str(history.get("staged_source_path") or ""))
    history_report = Path(str(history.get("original_report_path") or ""))
    subject_dir = _subject_dir_for_source(put_source_path)
    try:
        history_report.resolve().relative_to((subject_dir or Path()).resolve())
    except ValueError:
        return None, None, "sealed history report is outside its canonical subject"
    if staged_source.resolve() != emit_source_path.resolve():
        return None, None, "sealed history source path mismatch"
    if _sha256_text(emit_source) != history.get("staged_source_sha256"):
        return None, None, "sealed history source content mismatch"
    if history.get("selected_test") != emit_test:
        return None, None, "sealed history test mismatch"
    try:
        report_bytes = history_report.read_bytes()
        report = json.loads(report_bytes)
    except (OSError, json.JSONDecodeError):
        return None, None, "sealed history report is absent or malformed"
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    if report_sha256 != history.get("original_report_sha256"):
        return None, None, "sealed history report content mismatch"
    if not isinstance(report, dict):
        return None, None, "sealed history report is malformed"
    matches = [
        claim for claim in report.get("claims") or []
        if isinstance(claim, dict) and claim.get("path_function") == identity[1]
        and str(claim.get("path_id")) == identity[3]
    ]
    if len(matches) != 1:
        return None, None, "sealed history report lacks one exact identity claim"
    claim_sha256 = _sha256_text(
        json.dumps(matches[0], sort_keys=True, separators=(",", ":")))
    if (claim_sha256 != history.get("claim_sha256")
            or claim_sha256 != (record.get("claim_provenance") or {}).get("claim_sha256")):
        return None, None, "sealed history report claim digest mismatch"
    if history.get("certified_ce_sha256") != (record.get("certified_basis") or {}).get(
            "ce_sha256"):
        return None, None, "sealed history certified CE digest mismatch"
    return history_report, report_sha256, None


def _add_recovery_revert_oracle(source: str, test_name: str,
                                unit: str) -> tuple[str, list[dict[str, Any]]]:
    lines = source.splitlines()
    fn_re = re.compile(r"^\s*function\s+" + re.escape(test_name) + r"\s*\(")
    start = next((i for i, line in enumerate(lines) if fn_re.search(line)), None)
    if start is None:
        return source, []
    depth = 0
    end = None
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if index > start and depth <= 0:
            end = index
            break
    if end is None:
        return source, []
    body = lines[start + 1:end]
    call_i = find_unit_call(body, unit)
    if call_i is None:
        return source, []
    statement_start = call_i
    while statement_start > 0 and ";" not in body[statement_start - 1]:
        statement_start -= 1
    statement_end = call_i
    while statement_end + 1 < len(body) and ";" not in body[statement_end]:
        statement_end += 1
    statement = "\n".join(body[statement_start:statement_end + 1])
    receiver_match = re.search(
        r"\btry\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*" + re.escape(unit) + r"\s*\(", statement)
    if receiver_match is None:
        return source, []
    receiver = receiver_match.group(1)
    call_match = re.search(r"\btry\s+(.+?)\s*\{\s*\}\s*catch\s*\{\s*\}\s*$", statement, re.S)
    if call_match is None:
        return source, []
    call = re.sub(r"\s+", " ", call_match.group(1)).strip()
    indent = re.match(r"\s*", body[statement_start]).group(0)
    replacement = [f"{indent}vm.expectRevert();", f"{indent}{call};"]
    lines[start + 1 + statement_start:start + 1 + statement_end + 1] = replacement
    assertion = "vm.expectRevert();"
    return "\n".join(lines) + "\n", [{
        "class": "R0",
        "kind": "revert",
        "source": "expectRevert",
        "observed": "target call reverts",
        "expected": True,
        "provenance": "stage2-witness",
        "target_receiver": receiver,
        "assertion": assertion,
    }]


def _materialize_recovery_basis(record: dict[str, Any],
                                scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Build a scratch retained-basis entry from exact report/journal evidence."""
    identity = _recovery_identity(record)
    if identity is None:
        return None, "malformed recovery identity"
    if record.get("recovery_category") == "structural-abi-gate":
        basis = record.get("structural_basis") or {}
        source_path = Path(str(basis.get("source_path") or ""))
        put_json_path = Path(str(basis.get("put_json_path") or ""))
        if not source_path.is_file() or not put_json_path.is_file():
            return None, "structural ABI basis source or put.json is absent"
        if (_sha256_file(source_path) != basis.get("source_sha256")
                or _sha256_file(put_json_path) != basis.get("put_json_sha256")):
            return None, "structural ABI basis hash mismatch"
        document = _load_json(put_json_path)
        binding = document.get("certified_ce_binding")
        projection = binding.get("source_projection_preserved") if isinstance(binding,
                                                                              dict) else None
        detail, detail_error = _recovery_certified_detail(record, identity)
        ce_sha256 = certified_ce_sha256((detail or {}).get("ce") or {})
        if (detail is None
                or detail.get("certification_source") != "structural-abi-gate-no-coordinate"):
            return None, detail_error or "basis is not a certified structural ABI gate"
        if (document.get("certification_source") != "structural-abi-gate-no-coordinate"
                or not isinstance(binding, dict) or binding.get("status") != "exact"
                or binding.get("projection_certificate") != "abi-value-gate-before-body/v1"
                or binding.get("rendered_source_verified") is not True
                or binding.get("ce_sha256") != ce_sha256
                or binding.get("rendered_source_ce_sha256") != ce_sha256
                or not isinstance(projection, dict)
                or projection.get("schema") != "veriput-certified-ce-source-projection/v1"
                or projection.get("ce_sha256") != ce_sha256):
            return None, "structural ABI basis lacks an exact certified source projection"
        coordinates = (projection.get("coordinate_binding") or {}).get("coordinates") or {}
        value = coordinates.get("msg.value") or {}
        if (value.get("kind") != "call-environment-literal" or value.get("certified") != 1
                or value.get("rendered") != 1 or value.get("source") != "{value: 1}"):
            return None, "structural ABI basis does not bind the exact call value"
        if (document.get("path_function") != identity[1] or document.get("unit") != identity[2]
                or str(document.get("enc")) != identity[3]
                or not _same_recovery_piece(identity[4], document.get("piece"))
                or document.get("test") != basis.get("test")):
            return None, "structural ABI basis identity mismatch"
        oracles = document.get("concrete_oracles") or []
        if (len(oracles) != 1 or oracles[0].get("kind") != "call-status"
                or oracles[0].get("expected") is not False):
            return None, "structural ABI basis lacks one rejecting call-status oracle"
        source = source_path.read_text(encoding="utf-8")
        selected, selected_error = _selected_put(record)
        if selected is None:
            return None, selected_error
        put_source = Path(str(selected["source_path"])).read_text(encoding="utf-8")
        put_setup, _put_error = _scoped_function_body(put_source, str(selected.get("test") or ""),
                                                      "setUp")
        basis_setup, _basis_error = _scoped_function_body(source, str(basis.get("test") or ""),
                                                          "setUp")
        if put_setup is None or put_setup != basis_setup:
            return None, "structural ABI basis setUp differs from PUT setUp"
        entry, error = _entry_from_basis(
            record, source, str(basis["test"]), oracles, scratch_root, "structural-abi-gate", {
                "structural_basis_put_json":
                str(put_json_path),
                "structural_basis_put_json_sha256":
                _sha256_file(put_json_path),
                "source_projection_sha256":
                _sha256_text(json.dumps(projection, sort_keys=True, separators=(",", ":"))),
            })
        return entry, error
    if record.get("recovery_category") != "directly-generatable":
        return None, f"recovery category is {record.get('recovery_category')}"
    kinds = tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or [])
    if kinds not in (("return", ), ("normal-exit", ), ("revert", )):
        return None, "first-pass recovery supports only return, normal-exit, or revert"
    identity_digest, digest_error = _record_identity_digest(record)
    selected, selected_error = _selected_put(record)
    if identity_digest is None or selected is None:
        return None, digest_error or selected_error
    put_source_path = Path(str(selected.get("source_path") or ""))
    put_source = put_source_path.read_text(encoding="utf-8")
    emit_source_path, emit_test, error = _find_recovery_emit_case(record)
    if emit_source_path is None or emit_test is None:
        return None, error
    emit_source = emit_source_path.read_text(encoding="utf-8")
    emit_source_sha256 = _sha256_text(emit_source)
    history_report, history_report_sha256, history_error = _sealed_history_report(
        record, identity, put_source_path, emit_source_path, emit_source, emit_test)
    if history_error:
        return None, history_error
    if history_report is not None:
        # The retained basis is the narrowed staged source, but the report
        # binding must name the canonical emitted source whose cov-report was
        # sealed with the history record.  Using the staged-source hash here
        # makes the final subject audit fail even when both Forge gates pass.
        history = record.get("sealed_history_recovery") or {}
        original_source = Path(str(history.get("original_source_path") or ""))
        original_source_sha256 = str(history.get("original_source_sha256") or "")
        if (not original_source.is_file()
                or _sha256_file(original_source) != original_source_sha256):
            return None, "sealed history original source is absent or stale"
        emit_source_sha256 = original_source_sha256
    put_setup, _put_setup_error = _scoped_function_body(put_source, str(selected.get("test") or ""),
                                                        "setUp")
    emit_setup, _emit_setup_error = _scoped_function_body(emit_source, emit_test, "setUp")
    if put_setup is None or emit_setup is None or put_setup != emit_setup:
        return None, "emitted exact CE setup differs from PUT setup"
    unit = identity[2]
    if kinds == ("return", ):
        return_types = _recovery_return_types(record, put_source_path, put_source)
        if not return_types or len(return_types) != 1:
            return None, "could not recover one declared return type"
        witness = (record.get("ce") or {}).get("return_value")
        emit_source, oracles = add_concrete_fixed_return_oracle(emit_source, emit_test, unit,
                                                                return_types, witness)
    elif kinds == ("normal-exit", ):
        emit_source, oracles = add_concrete_normal_exit_oracle(emit_source, emit_test, unit)
    else:
        emit_source, oracles = _add_recovery_revert_oracle(emit_source, emit_test, unit)
    oracle_error = authenticated_concrete_oracle_error(oracles)
    if oracle_error:
        return None, oracle_error
    report_path = Path(str((record.get("claim_provenance") or {}).get("report_path") or ""))
    report_sha256 = (record.get("claim_provenance") or {}).get("report_sha256")
    if history_report is not None:
        report_path = history_report
        report_sha256 = history_report_sha256
    return _entry_from_basis(
        record, emit_source, emit_test, oracles, scratch_root, "direct", {
            "emitted_source":
            str(emit_source_path),
            "emitted_source_sha256":
            emit_source_sha256,
            "emitted_test":
            emit_test,
            "identity_digest":
            identity_digest,
            "report_path":
            str(report_path),
            "report_sha256": report_sha256,
        })


def _materialize_failure_bundle(record: dict[str, Any], row: dict[str, Any],
                                scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Re-authenticate a repaired failure basis; prior Forge is not trusted."""
    identity = _recovery_identity(record)
    if identity is None or _identity_list(row) != identity:
        return None, "failure bundle identity differs from recovery inventory"
    selected, error = _selected_put(record)
    if selected is None:
        return None, error
    if (row.get("canonical_source") != selected.get("source_path")
            or row.get("canonical_put_json") != selected.get("put_json_path")
            or row.get("canonical_source_expected_sha256") != selected.get("source_sha256")):
        return None, "failure bundle does not bind the selected canonical PUT"
    basis_file = Path(str(row.get("repaired_basis") or ""))
    metadata_file = Path(str(row.get("ce_anchor_metadata") or ""))
    if (_sha256_file(basis_file) != row.get("repaired_basis_sha256")
            or not metadata_file.is_file()):
        return None, "failure bundle basis or metadata hash is invalid"
    metadata = _load_json(metadata_file)
    if (metadata.get("identity") != identity
            or metadata.get("basis_source_sha256") != row.get("repaired_basis_sha256")
            or metadata.get("test") != row.get("anchor_test")):
        return None, "failure bundle metadata does not bind its repaired basis"
    basis_test = str(metadata.get("basis_test") or "")
    basis_source = basis_file.read_text(encoding="utf-8")
    body, body_error = _function_body(basis_source, basis_test)
    setup, setup_error = _function_body(basis_source, "setUp")
    if (body is None or setup is None
            or _sha256_text(body) != metadata.get("basis_test_body_sha256")
            or _sha256_text(setup) != metadata.get("basis_setup_sha256")):
        return None, body_error or setup_error or "failure bundle body seal mismatch"
    emitted, _test, emit_error = _find_recovery_emit_case(record)
    if emitted is None:
        return None, emit_error
    return _entry_from_basis(
        record, basis_source, basis_test, list(metadata.get("oracles") or []), scratch_root,
        "failures", {
            "emitted_source": str(emitted),
            "bundle_metadata_sha256": _sha256_file(metadata_file),
            "bundle_basis_sha256": _sha256_file(basis_file),
        })


def _normalize_event_oracle(oracle: dict[str, Any]) -> dict[str, Any]:
    """Retain materializer detail under the unified Stage-2 oracle contract."""
    normalized = dict(oracle)
    normalized["materializer_provenance"] = normalized.get("provenance")
    normalized["provenance"] = "stage2-witness"
    normalized["materializer_observed"] = normalized.get("observed")
    if normalized.get("kind") == "event-log":
        normalized["observed"] = re.sub(r"\[[0-9]+\]$", "", str(normalized.get("observed") or ""))
    return normalized


def _materialize_event_partition(record: dict[str, Any], row: dict[str, Any],
                                 scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Re-run the event materializer from its source-hash-bound AST."""
    if row.get("forge_status") != "Success" or not row.get("anchor_boundary_complete"):
        return None, "event selector is not a boundary-complete prior success"
    ast_path = Path(str(row.get("ast") or ""))
    if _sha256_file(ast_path) != row.get("ast_sha256"):
        return None, "event selector AST hash mismatch"
    selected, error = _selected_put(record)
    if selected is None:
        return None, error
    event_entry = {
        "identity": _recovery_identity(record),
        "put_json": selected["put_json_path"],
        "put_file": selected["source_path"],
    }
    try:
        recovered = recover_event_entry(event_entry, [ast_path])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"event materializer refused evidence: {exc}"
    for field in ("identity", "ast_sha256", "claim_source_sha256", "emitted_source_sha256",
                  "canonical_flat_sha256"):
        if recovered.get(field) != row.get(field):
            return None, f"event materializer changed the audited {field}"
    rewritten = _tighten_event_recording_window(recovered["rewritten_source"], recovered["test"],
                                                str((_recovery_identity(record) or [""] * 3)[2]))
    if rewritten is None:
        return None, "event recording window cannot be bound directly to the target call"
    oracles = [_normalize_event_oracle(oracle) for oracle in recovered["oracles"]]
    entry, error = _entry_from_basis(
        record, rewritten, recovered["test"], oracles, scratch_root, "events", {
            "emitted_source": recovered["emitted_source"],
            "ast": str(ast_path),
            "ast_sha256": recovered["ast_sha256"],
            "claim_source_sha256": recovered["claim_source_sha256"],
        })
    if entry is not None:
        entry["event_signatures"] = event_signatures_from_ast(load_solast(ast_path))
        entry["required_imports"] = ['import {Vm} from "forge-std/Vm.sol";']
    return entry, error


def _tighten_event_recording_window(source: str, test_name: str, unit: str) -> str | None:
    """Place recordLogs after setup cheatcodes and directly before the target."""
    span, _error = _solidity_test_span(source, test_name)
    if span is None:
        return None
    function = source[span[0]:span[1]]
    mask = _solidity_code_mask(function)
    opening = mask.find("{")
    closing = mask.rfind("}")
    if opening < 0 or closing <= opening:
        return None
    body = function[opening + 1:closing]
    lines = body.splitlines()
    records = [
        index for index, line in enumerate(lines)
        if re.sub(r"\s+", "", _solidity_code_mask(line)) == "vm.recordLogs();"
    ]
    if len(records) != 1:
        return None
    record_line = lines.pop(records[0])
    call_index = find_unit_call(lines, unit)
    if call_index is None:
        return None
    lines.insert(call_index, record_line)
    rewritten_body = "\n".join(lines) + ("\n" if body.endswith("\n") else "")
    rewritten_function = function[:opening + 1] + rewritten_body + function[closing:]
    return source[:span[0]] + rewritten_function + source[span[1]:]


def _materialize_state_partition(record: dict[str, Any], row: dict[str, Any],
                                 scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Recompute solc layout and all state assertions from retained evidence."""
    identity = _recovery_identity(record)
    identity_digest, digest_error = _record_identity_digest(record)
    if identity is None or identity_digest is None or _identity_list(row) != identity:
        if digest_error:
            return None, digest_error
        return None, "state selector identity differs from recovery inventory"
    selected, error = _selected_put(record)
    if selected is None:
        return None, error
    emit_path, test_name, error = _find_recovery_emit_case(record)
    if emit_path is None or test_name is None:
        return None, error
    put_path = Path(str(selected["source_path"]))
    put_source = put_path.read_text(encoding="utf-8")
    emit_source = emit_path.read_text(encoding="utf-8")
    put_setup, _put_error = _function_body(put_source, "setUp")
    emit_setup, _emit_error = _function_body(emit_source, "setUp")
    if put_setup is None or emit_setup is None or put_setup != emit_setup:
        return None, "PUT and emitted state basis setUp bodies differ"
    contract, flat_path = _put_contract_and_flat(put_path, put_source)
    if contract is None or flat_path is None:
        return None, "state materializer target contract or flat source is absent"
    layout, mappings, error = isolated_storage_layout(
        put_path, scratch_root / "state-layout" / identity_digest, contract)
    if layout is None or mappings is None:
        return None, error
    state_delta = (record.get("ce") or {}).get("state_delta")
    rewritten, oracles, error = materialize_state_delta_oracles(emit_source, test_name, identity[2],
                                                                state_delta, (layout, mappings))
    if error:
        return None, error
    return _entry_from_basis(
        record, rewritten, test_name, oracles, scratch_root, "state", {
            "emitted_source": str(emit_path),
            "flat_source": str(flat_path),
            "flat_source_sha256": _sha256_file(flat_path),
        })


def _materialize_b340_state_partition(
        record: dict[str, Any], row: dict[str, Any],
        scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Materialize one sealed B340 state-only selector without widening ownership."""
    if (row.get("record_identity_sha256") != record.get("identity_sha256")
            or tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or
                     ()) != ("state-delta", )):
        return None, "B340 state selector record binding is invalid"
    return _materialize_state_partition(record, row, scratch_root)


def _materialize_compound_partition(record: dict[str, Any], row: dict[str, Any],
                                    scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Materialize only compound rows independently classified as ready."""
    if _identity_list(row) != _recovery_identity(record):
        return None, "compound selector identity differs from recovery inventory"
    if not owns_record(record):
        return None, "compound selector does not belong to the compound partition"
    claim, error = executable_claim(record)
    if claim is None:
        return None, error
    emit_path, test_name, error = _find_recovery_emit_case(record)
    selected, selected_error = _selected_put(record)
    if emit_path is None or test_name is None or selected is None:
        return None, error or selected_error
    identity = _recovery_identity(record)
    kinds = tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or [])
    source = emit_path.read_text(encoding="utf-8")
    if kinds == ("return", "revert"):
        rewritten, oracles = _add_recovery_revert_oracle(source, test_name, identity[2])
        error = None if oracles else "compound revert materializer produced no oracle"
    elif kinds == ("return", ):
        put_path = Path(str(selected["source_path"]))
        put_source = put_path.read_text(encoding="utf-8")
        contract, flat_path = _put_contract_and_flat(put_path, put_source)
        if contract is None or flat_path is None:
            return None, "compound return declaration source is absent"
        return_types = source_inherited_function_returns(flat_path.read_text(encoding="utf-8"),
                                                         contract, identity[2])
        rewritten, oracles, error = add_indexed_return_oracles(source, test_name, identity[2],
                                                               return_types,
                                                               (record.get("ce")
                                                                or {}).get("return_value"))
    else:
        return None, "compound composition is not independently ready"
    if error:
        return None, error
    coverage_error = _oracle_claim_coverage_error(claim, oracles)
    if coverage_error:
        return None, coverage_error
    entry, error = _entry_from_basis(record, rewritten, test_name, oracles, scratch_root,
                                     "compound", {
                                         "emitted_source": str(emit_path),
                                         "compound_projection": claim.get("compound_projection"),
                                     })
    if entry is not None and claim.get("compound_projection"):
        entry["exclude_reverted_return"] = True
    return entry, error


def _containing_contract_span(source: str, function_name: str) -> tuple[int, int] | None:
    spans = _solidity_function_spans(source, function_name)
    if len(spans) != 1 or spans[0][0] is None:
        return None
    function_start = spans[0][0][0]
    mask = _solidity_code_mask(source)
    candidates = []
    for match in re.finditer(r"\b(?:abstract\s+)?contract\s+[A-Za-z_$][A-Za-z0-9_$]*[^{};]*\{",
                             mask):
        opening = mask.find("{", match.start(), match.end())
        closing = _matching_delimiter(mask, opening, "{", "}")
        if closing is not None and match.start() < function_start < closing:
            candidates.append((match.start(), closing + 1))
    return max(candidates) if candidates else None


def _scoped_function(source: str, selected_test: str,
                     function_name: str) -> tuple[tuple[int, int] | None, str | None]:
    """Select a helper only from the contract owning the selected test."""
    contract = _containing_contract_span(source, selected_test)
    if contract is None:
        return None, "selected test is not inside one parseable contract"
    contract_start, contract_end = contract
    fragment = source[contract_start:contract_end]
    spans = _solidity_function_spans(fragment, function_name)
    if len(spans) != 1 or spans[0][0] is None:
        return None, f"selected test contract has {len(spans)} {function_name} functions"
    start, end = spans[0][0][:2]
    return (contract_start + start, contract_start + end), None


def _scoped_function_body(source: str, selected_test: str,
                          function_name: str) -> tuple[str | None, str | None]:
    span, error = _scoped_function(source, selected_test, function_name)
    if span is None:
        return None, error
    return _function_source_body(source[span[0]:span[1]])


def _render_simple_recovery_oracles(record: dict[str, Any], source: str, test_name: str,
                                    put_path: Path) -> tuple[str, list[dict[str, Any]], str | None]:
    identity = _recovery_identity(record)
    if identity is None:
        return source, [], "malformed recovery identity"
    kinds = tuple((record.get("observable_evidence") or {}).get("anchor_required_kinds") or [])
    if kinds == ("return", ):
        return_types = _recovery_return_types(record, put_path,
                                              put_path.read_text(encoding="utf-8"))
        if not return_types or len(return_types) != 1:
            return source, [], "could not recover one declared return type"
        rendered, oracles = add_concrete_fixed_return_oracle(source, test_name, identity[2],
                                                             return_types,
                                                             (record.get("ce")
                                                              or {}).get("return_value"))
    elif kinds == ("normal-exit", ):
        rendered, oracles = add_concrete_normal_exit_oracle(source, test_name, identity[2])
    elif kinds == ("revert", ):
        rendered, oracles = _add_recovery_revert_oracle(source, test_name, identity[2])
    else:
        return source, [], "setup partition does not have a simple executable oracle"
    error = authenticated_concrete_oracle_error(oracles)
    return rendered, oracles, error


def _materialize_setup_partition(record: dict[str, Any], row: dict[str, Any],
                                 scratch_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Reconcile only hash-sealed, selected-contract-equivalent setUp code."""
    identity = _recovery_identity(record)
    if (identity is None or _identity_list(row) != identity
            or row.get("decision") != "safe_without_esbmc"):
        return None, "setup selector is not an audited safe identity"
    selected, error = _selected_put(record)
    emit_path, emit_test, emit_error = _find_recovery_emit_case(record)
    if selected is None or emit_path is None or emit_test is None:
        return None, error or emit_error
    put_path = Path(str(selected["source_path"]))
    if (row.get("put_source") != str(put_path) or row.get("emit_source") != str(emit_path)
            or row.get("put_source_sha256_observed") != _sha256_file(put_path)
            or row.get("emit_source_sha256_observed") != _sha256_file(emit_path)):
        return None, "setup selector source seal mismatch"
    put_source = put_path.read_text(encoding="utf-8")
    emit_source = emit_path.read_text(encoding="utf-8")
    reconciled, reconciliation = reconcile_selected_contract_setup(
        put_source, str(selected.get("test") or ""), emit_source, emit_test, row)
    if reconciled is None:
        return None, reconciliation
    rendered, oracles, error = _render_simple_recovery_oracles(record, reconciled, emit_test,
                                                               put_path)
    if error:
        return None, error
    return _entry_from_basis(
        record, rendered, emit_test, oracles, scratch_root, "setup", {
            "emitted_source": str(emit_path),
            "setup_classification_identity_sha256": row.get("identity_sha256"),
            "setup_reconciliation": reconciliation,
            "semantic_setup_sha256": _sha256_text(
                json.dumps(row.get("semantic_setup") or {}, sort_keys=True, separators=(",", ":"))),
        })


def _materialize_revert_edge_partition(
    record: dict[str, Any], row: dict[str, Any], scratch_root: Path
) -> tuple[dict[str, Any] | None, str | None]:
    """Delegate only the frozen, hash-sealed B85 revert-only selector."""
    identity = _recovery_identity(record)
    if (identity is None or _identity_list(row) != identity or row.get("status") != "ready"
            or row.get("record_identity_sha256") != record.get("identity_sha256")):
        return None, "revert-edge selector differs from recovery inventory"
    # This local import avoids a cycle while the independent partition builder
    # imports this shared driver for evidence primitives.
    from rq1_anchor_revert import recover_entry  # pylint: disable=import-outside-toplevel
    entry, error = recover_entry(record, scratch_root)
    if entry is None:
        return None, error
    basis = Path(str((entry.get("basis") or {}).get("file") or ""))
    if _sha256_file(basis) != row.get("basis_source_sha256"):
        return None, "revert-edge materialized basis differs from sealed source"
    return entry, None


def _revert_edge_prepared_error(prepared: dict[str, Any], selector: dict[str, Any]) -> str | None:
    """Bind all ready-row evidence seals to the final prepared metadata."""
    metadata = prepared.get("metadata") or {}
    report = metadata.get("report_binding") or {}
    checks = (
        (metadata.get("basis_source_sha256"), selector.get("basis_source_sha256")),
        (metadata.get("certification_record_sha256"),
         selector.get("certification_record_sha256")),
        (metadata.get("certified_ce_sha256"), selector.get("certified_ce_sha256")),
        (report.get("claim_sha256"), selector.get("claim_sha256")),
        (report.get("cov_report_sha256"), selector.get("cov_report_sha256")),
    )
    if any(actual != expected for actual, expected in checks):
        return "revert-edge prepared evidence differs from selector seals"
    return None


def _partition_dry_run(
    inventory_path: Path,
    artifact_path: Path,
    partition: str,
    scratch_root: Path,
    record_limit: int,
    record_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build strict legacy entries from one independent partition artifact."""
    inventory_error = _sealed_partition_inventory_error(partition, artifact_path, inventory_path)
    if inventory_error:
        raise RuntimeError(inventory_error)
    records = _recovery_record_index(inventory_path)
    selectors = _load_partition_rows(artifact_path, partition)
    selectors = selectors[record_offset:]
    if record_limit:
        selectors = selectors[:record_limit]
    materializers = {
        "failures": _materialize_failure_bundle,
        "events": _materialize_event_partition,
        "state": _materialize_state_partition,
        "b340-state": _materialize_b340_state_partition,
        "revert-edge": _materialize_revert_edge_partition,
        "compound": _materialize_compound_partition,
        "setup": _materialize_setup_partition,
    }
    entries = []
    rows = []
    for selector in selectors:
        identity = _identity_list(selector) or []
        record = records.get(tuple(identity))
        if record is None:
            rows.append({
                "identity": identity,
                "status": "refused",
                "reason": "selector identity is absent from recovery inventory"
            })
            continue
        selector_record_digest = (selector.get("record_identity_sha256")
                                  if partition in ("compound", "revert-edge")
                                  else selector.get("authoritative_record_identity_sha256"))
        if (partition in ("compound", "setup", "revert-edge")
                and selector_record_digest != record.get("identity_sha256")):
            rows.append({
                "identity": identity,
                "status": "refused",
                "reason": "selector record digest differs from recovery inventory"
            })
            continue
        current = _current_strength_entry(record)
        if current is not None and _already_strength_confirmed(current):
            rows.append({
                "identity": identity,
                "partition": partition,
                "status": "already-embedded",
                "reason": None,
                "record_identity_sha256": record.get("identity_sha256"),
            })
            continue
        selected = record.get("selected_put") or {}
        selected_source = Path(str(selected.get("source_path") or ""))
        scratch_error = _canonical_scratch_error(scratch_root, selected_source)
        if scratch_error:
            rows.append({
                "identity": identity,
                "partition": partition,
                "status": "refused",
                "reason": scratch_error,
                "record_identity_sha256": record.get("identity_sha256"),
            })
            continue
        prepared = None
        entry, error = materializers[partition](record, selector, scratch_root)
        if entry is not None:
            prepared, error = _prepare(entry)
        if partition == "revert-edge" and prepared is not None and error is None:
            error = _revert_edge_prepared_error(prepared, selector)
        row = {
            "identity": identity,
            "partition": partition,
            "status": "ready" if entry is not None and error is None else "refused",
            "reason": error,
            "record_identity_sha256": record.get("identity_sha256"),
        }
        if entry is not None and error is None:
            entry["_prevalidated_selector"] = selector
            row["anchor_test"] = prepared["metadata"]["test"]
            row["oracle_kinds"] = [
                oracle.get("kind") for oracle in prepared["metadata"].get("oracles") or []
            ]
            entry["_recovery_progress_row"] = dict(row)
            entries.append(entry)
        rows.append(row)
    return entries, rows


def _recovery_dry_run(inventory_path: Path, scratch_root: Path,
                      limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    document = _load_json(inventory_path)
    records = document.get("records")
    if not isinstance(records, list):
        raise RuntimeError("recovery inventory has no records list")
    rows = []
    entries = []
    selected_records = records[:limit] if limit else records
    for record in selected_records:
        identity = _recovery_identity(record) or []
        entry, error = _materialize_recovery_basis(record, scratch_root)
        if entry is None:
            rows.append({
                "identity": identity,
                "status": "refused",
                "reason": error,
                "record_identity_sha256": record.get("identity_sha256"),
            })
            continue
        prepared, error = _prepare(entry)
        row = {
            "identity": entry["identity"],
            "status": "refused" if error else "ready",
            "reason": error,
            "record_identity_sha256": record.get("identity_sha256"),
            "scratch_basis": (entry.get("recovery") or {}).get("scratch_basis"),
        }
        if prepared is not None:
            row["anchor_test"] = prepared["metadata"]["test"]
            row["oracle_kinds"] = [
                oracle.get("kind") for oracle in prepared["metadata"].get("oracles") or []
            ]
            entry["_recovery_progress_row"] = dict(row)
        rows.append(row)
        entries.append(entry)
    return entries, rows


def _already_strength_confirmed(entry: dict[str, Any]) -> bool:
    """Skip only anchors that pass the same full audit as headline reporting."""
    put = entry.get("put")
    subject_dir = Path(str(entry.get("subject_dir") or ""))
    identity = tuple(entry.get("identity") or ())
    if not isinstance(put, dict) or len(identity) != 5 or not subject_dir.is_dir():
        return False
    confirmed, _reason = _anchor_strength_audit(put, identity=identity, subject_dir=subject_dir)
    return confirmed


def _deduplicated_puts(result_root: Path) -> list[dict[str, Any]]:
    """Return one deterministic strict-valid PUT row per physical identity."""
    selected: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for case, subject_dir in _case_dirs(result_root):
        rows = _strict_valid_tests(subject_dir)
        concretes: dict[tuple, list[dict[str, Any]]] = {}
        for row in rows:
            if _physical_test_kind(row) == "concrete":
                concretes.setdefault(_artifact_key(row), []).append(row)
        for row in rows:
            if _physical_test_kind(row) != "put":
                continue
            identity = _identity(case, row)
            basis_seals = {}
            for basis in concretes.get(_artifact_key(row), []):
                basis_file = Path(str(basis.get("file") or ""))
                source_hash = (_sha256_text(basis_file.read_text(
                    encoding="utf-8")) if basis_file.is_file() else None)
                seal = _sha256_text(
                    json.dumps(
                        {
                            "source_sha256": source_hash,
                            "test": basis.get("test"),
                            "oracles": basis.get("concrete_oracles") or [],
                        },
                        sort_keys=True,
                        separators=(",", ":")))
                basis_seals.setdefault(seal, basis)
            candidate = {
                "identity": list(identity),
                "case": case,
                "subject_dir": str(subject_dir),
                "result_json": str(subject_dir / "result.json"),
                "put": row,
                "basis": (next(iter(basis_seals.values())) if len(basis_seals) == 1 else None),
                "basis_ambiguity": len(basis_seals) if len(basis_seals) > 1 else 0,
            }
            previous = selected.get(identity)
            # Prefer a retained basis with an explicit execution-result oracle,
            # then stable lexical paths.  Duplicate retry rows never become new
            # CE obligations.
            rank = (bool((candidate.get("basis")
                          or {}).get("concrete_oracles")), str(row.get("file") or ""))
            old_rank = (bool(((previous or {}).get("basis") or {}).get("concrete_oracles")),
                        str(((previous or {}).get("put") or {}).get("file") or ""))
            if previous is None or rank > old_rank:
                selected[identity] = candidate
    return [selected[key] for key in sorted(selected)]


def _certified_detail(entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Find the unique certified detail matching a PUT's full path identity."""
    recovery_detail = entry.get("recovery_certified_detail")
    if isinstance(recovery_detail, dict):
        return recovery_detail, None
    put = entry["put"]
    cert_path = Path(entry["subject_dir"]) / "cert" / "certify-results.jsonl"
    matches = []
    try:
        content = cert_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"certification ledger unavailable: {exc}"
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
    for record in records:
        if not isinstance(record, dict):
            continue
        if (record.get("unit") != put.get("unit")
                or record.get("path_function") != entry["identity"][1]):
            continue
        details = record.get("stage2_observed_certified_details") or record.get(
            "certified_details") or {}
        detail = details.get(str(put.get("enc")))
        if not isinstance(detail, dict):
            continue
        if detail.get("verdict") != "CERTIFIED":
            continue
        if str(put.get("enc")) not in (record.get("certified") or {}):
            continue
        wanted_piece = put.get("piece")
        detail_piece = detail.get("piece")
        # A single unsplit region is keyed by its bare path id in the Stage-2
        # ledger, while the generaliser labels its sole internal box as piece
        # 1.  Accept only that exact alias; real split regions use `<enc>#<n>`
        # keys and must retain their piece identity end to end.
        sole_piece_alias = (wanted_piece is None and detail_piece == 1
                            and str(put.get("enc")) in (record.get("certified") or {})
                            and f"{put.get('enc')}#1" not in
                            (record.get("certified") or {}))
        if (not sole_piece_alias
                and ((wanted_piece is None) != (detail_piece is None)
                     or (wanted_piece is not None
                         and str(wanted_piece) != str(detail_piece)))):
            continue
        matches.append({
            **detail, "_certification_record_sha256":
            _sha256_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
        })
    unique = {_sha256_text(json.dumps(item, sort_keys=True)): item for item in matches}
    if len(unique) != 1:
        return None, f"expected one certified detail, found {len(unique)}"
    return next(iter(unique.values())), None


def _source_function(source: str, name: str) -> tuple[str | None, str | None]:
    span, reason = _solidity_test_span(source, name)
    if span is None:
        return None, reason
    return source[span[0]:span[1]], None


def _function_body(source: str, name: str) -> tuple[str | None, str | None]:
    """Return an exact function body using comment/string-aware delimiters."""
    function_source, reason = _source_function(source, name)
    if function_source is None:
        return None, reason
    return _function_source_body(function_source)


def _function_body_any(source: str, name: str) -> tuple[str | None, str | None]:
    """Return an exact body for one function, allowing PUT fuzz parameters."""
    spans = _solidity_function_spans(source, name)
    if not spans:
        return None, "function is absent"
    if len(spans) != 1:
        return None, "function name is ambiguous"
    span, error = spans[0]
    if span is None:
        return None, error
    return _function_source_body(source[span[0]:span[1]])


def _function_source_body(function_source: str) -> tuple[str | None, str | None]:
    """Return the body of an already isolated Solidity function."""
    mask = _solidity_code_mask(function_source)
    opening = mask.find("{")
    if opening < 0:
        return None, "function body opener is absent"
    depth = 0
    for index in range(opening, len(mask)):
        if mask[index] == "{":
            depth += 1
        elif mask[index] == "}":
            depth -= 1
            if depth == 0:
                return function_source[opening + 1:index], None
    return None, "function body is unclosed"


def _code_contains_statement(source: str, statement: str) -> bool:
    """Match one metadata assertion as Solidity code, never prose/string text."""
    code = re.sub(r"\s+", "", _solidity_code_mask(source))
    wanted = re.sub(r"\s+", "", _solidity_code_mask(statement))
    return bool(wanted) and wanted in code


def _named_function_count(source: str, name: str) -> int:
    mask = _solidity_code_mask(source)
    return len(re.findall(r"\bfunction\s+" + re.escape(name) + r"\s*\(", mask))


def _contract_close_for_function(source: str, function_name: str) -> int | None:
    """Return the close brace of the contract containing a named test."""
    spans = _solidity_function_spans(source, function_name)
    if len(spans) != 1 or spans[0][0] is None:
        return None
    span = spans[0][0]
    mask = _solidity_code_mask(source)
    containing = []
    for match in re.finditer(r"\bcontract\s+[A-Za-z_$][A-Za-z0-9_$]*[^{};]*\{", mask):
        opening = mask.find("{", match.start(), match.end())
        closing = _matching_delimiter(mask, opening, "{", "}")
        if closing is not None and match.start() < span[0] < closing:
            containing.append((match.start(), closing))
    return max(containing)[1] if containing else None


def _claim_scalar(raw: Any, want_length: bool) -> int | None:
    """Project one retained report value onto a certified scalar coordinate."""
    text = str(raw).strip()
    if want_length:
        match = re.search(r"\.length\s*=\s*([0-9]+)", text)
        return int(match.group(1)) if match else None
    try:
        return int(text, 0)
    except ValueError:
        pass
    data = re.search(r"\.data\s*=\s*\{([^{}]*)\}", text)
    if data is None:
        return None
    try:
        octets = [int(value.strip(), 0) for value in data.group(1).split(",") if value.strip()]
    except ValueError:
        return None
    if any(value < 0 or value > 255 for value in octets):
        return None
    value = 0
    for octet in octets:
        value = (value << 8) | octet
    return value


def _claim_ce_matches(claim: dict[str, Any], ce: dict[str, Any]) -> tuple[bool, str | None]:
    """Require complete equality between certified coordinates and a claim."""
    expected = {}
    for raw_name, raw_value in ce.items():
        if raw_name == "return":
            continue
        try:
            expected[str(raw_name)] = int(str(raw_value), 0)
        except (TypeError, ValueError):
            return False, f"certified CE coordinate {raw_name} is not scalar"
    actual = {}
    groups = ((claim.get("inputs") or {}, ""), (claim.get("env")
                                                or {}, ""), (claim.get("entry_storage")
                                                             or {}, "state."))
    for values, prefix in groups:
        if not isinstance(values, dict):
            return False, "report claim coordinate group is malformed"
        for raw_name, raw_value in values.items():
            name = prefix + str(raw_name)
            scalar = _claim_scalar(raw_value, False)
            length = _claim_scalar(raw_value, True)
            represented = False
            if name in expected and scalar is not None:
                actual[name] = scalar
                represented = True
            length_name = name + ".length"
            if length_name in expected and length is not None:
                actual[length_name] = length
                represented = True
            if not represented:
                return False, f"report claim has extra/unrepresented coordinate {name}"
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        different = sorted(name for name in set(actual) & set(expected)
                           if actual[name] != expected[name])
        return False, ("report claim and certified CE maps differ: "
                       f"missing={missing}, extra={extra}, different={different}")
    return True, None


def _report_binding(entry: dict[str, Any], detail: dict[str, Any], oracles: list[dict[str, Any]],
                    basis_source: str, basis_test: str) -> tuple[dict[str, Any] | None, str | None]:
    """Bind the basis oracle and source to the retained Stage-2 claim."""
    recovery = entry.get("recovery") or {}
    structural_path = Path(str(recovery.get("structural_basis_put_json") or ""))
    if recovery.get("partition") == "structural-abi-gate":
        if (detail.get("certification_source") != "structural-abi-gate-no-coordinate"
                or not structural_path.is_file() or _sha256_file(structural_path)
                != recovery.get("structural_basis_put_json_sha256")):
            return None, "structural ABI certification basis is absent or stale"
        document = _load_json(structural_path)
        projection = ((document.get("certified_ce_binding")
                       or {}).get("source_projection_preserved"))
        projection_sha256 = _sha256_text(
            json.dumps(projection, sort_keys=True, separators=(",", ":")))
        if (not isinstance(projection, dict)
                or projection_sha256 != recovery.get("source_projection_sha256")
                or projection.get("ce_sha256") != certified_ce_sha256(detail.get("ce") or {})):
            return None, "structural ABI source projection seal mismatch"
        try:
            relative = structural_path.resolve().relative_to(Path(
                entry["subject_dir"]).resolve()).as_posix()
        except ValueError:
            return None, "structural ABI basis put.json is outside its canonical subject"
        binding_errors = _oracle_binding_errors(basis_source, basis_test, str(entry["identity"][2]),
                                                oracles)
        if binding_errors:
            return None, "; ".join(binding_errors)
        return {
            "kind": "structural-abi-gate-certified-projection",
            "certification_source": "structural-abi-gate-no-coordinate",
            "claim_exit_kind": "revert",
            "claim_return_value": None,
            "basis_put_json_path": relative,
            "basis_put_json_sha256": _sha256_file(structural_path),
            "source_projection_sha256": projection_sha256,
        }, None
    recovered_report = Path(str(recovery.get("report_path") or ""))
    report_sha256 = None
    if recovery.get("report_path"):
        report_path = recovered_report
        try:
            report_bytes = report_path.read_bytes()
            report = json.loads(report_bytes)
        except (OSError, json.JSONDecodeError):
            return None, "recovery report is absent or malformed"
        report_sha256 = hashlib.sha256(report_bytes).hexdigest()
        if report_sha256 != recovery.get("report_sha256"):
            return None, "recovery report is absent or stale"
    else:
        basis_json = Path(str((entry.get("basis") or {}).get("put_json") or ""))
        report_path = basis_json.parent / "emit" / "cov-report.json"
        report = _load_json(report_path)
        report_sha256 = _sha256_file(report_path)
    if not isinstance(report, dict) or not report:
        return None, "retained basis cov-report.json is absent"
    try:
        report_relative = report_path.resolve().relative_to(Path(
            entry["subject_dir"]).resolve()).as_posix()
    except ValueError:
        return None, "retained basis report is outside its canonical subject"
    matches = [
        claim for claim in report.get("claims") or []
        if isinstance(claim, dict) and claim.get("path_function") == entry["identity"][1]
        and str(claim.get("path_id")) == entry["identity"][3]
    ]
    if len(matches) != 1:
        return None, f"expected one exact retained report claim, found {len(matches)}"
    claim = matches[0]
    agrees, error = _claim_ce_matches(claim, detail.get("ce") or {})
    if not agrees:
        return None, error
    test_span, span_error = _solidity_test_span(basis_source, basis_test)
    if test_span is None:
        return None, span_error
    # The emitter puts the claim and witness fingerprint in one contiguous
    # //-comment block immediately above the replay.  Never borrow metadata
    # from another function earlier in the file.
    lines = basis_source[:test_span[0]].splitlines()
    adjacent = []
    while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith("//")):
        adjacent.append(lines.pop())
    metadata_block = "\n".join(reversed(adjacent))
    claim_label = re.escape(entry["identity"][1]) + r":path:" + re.escape(entry["identity"][3])
    source_claims = re.findall(r"^\s*//\s*claim:[^\n]*" + claim_label + r"[^\n]*$", metadata_block,
                               re.M)
    if len(source_claims) != 1:
        return None, ("basis source does not have exactly one adjacent retained "
                      "report claim identity")
    report_fingerprint = str(claim.get("foundry_testcase_fingerprint_sha256") or "")
    source_fingerprints = re.findall(r"^\s*// witness-fingerprint-sha256: ([0-9a-f]{64})\s*$",
                                     metadata_block, re.M)
    basis_json = Path(str((entry.get("basis") or {}).get("put_json") or ""))
    basis_document = _load_json(basis_json)
    certified_binding = basis_document.get("certified_ce_binding") or {}
    projection = certified_binding.get("source_projection_preserved")
    if isinstance(projection, dict):
        projection_error = certified_source_projection_error(
            certified_binding, detail, basis_file=str((entry.get("basis") or {}).get("file") or ""),
            unit=str(entry["identity"][2]))
        try:
            basis_relative = basis_json.resolve().relative_to(Path(
                entry["subject_dir"]).resolve()).as_posix()
        except ValueError:
            basis_relative = ""
        if projection_error or not basis_relative:
            return None, projection_error or "projected basis put.json is outside its subject"
        fingerprint_binding = {
            "kind": "certified-source-projection",
            "basis_put_json_path": basis_relative,
            "basis_put_json_sha256": _sha256_file(basis_json),
            "source_projection_sha256": _sha256_text(
                json.dumps(projection, sort_keys=True, separators=(",", ":"))),
            "projection_certificate": certified_binding.get("projection_certificate"),
        }
    elif report_fingerprint:
        if len(source_fingerprints) != 1 or source_fingerprints[0] != report_fingerprint:
            return None, "report and basis source lack the same solver witness fingerprint"
        fingerprint_binding = {
            "kind": "solver-witness-fingerprint",
            "solver_witness_fingerprint_sha256": report_fingerprint,
        }
    elif entry.get("recovery"):
        emitted = Path(str((entry.get("recovery") or {}).get("emitted_source") or ""))
        basis_file = Path(str((entry.get("basis") or {}).get("file") or ""))
        emitted_sha256 = str((entry.get("recovery") or {}).get("emitted_source_sha256")
                             or _sha256_file(emitted) or "")
        if (not emitted.is_file() or not basis_file.is_file() or not emitted_sha256
                or emitted_sha256 == _sha256_file(basis_file)):
            return None, "legacy emitted source was not transformed into a sealed basis"
        fingerprint_binding = {
            "kind": "legacy-adjacent-claim",
            "emitted_source_sha256": emitted_sha256,
            "basis_source_sha256": _sha256_file(basis_file),
        }
    else:
        return None, "report and basis source lack the same solver witness fingerprint"
    exit_kind = claim.get("exit_kind")
    if exit_kind not in ("normal", "revert"):
        return None, "retained report claim has no definite exit kind"
    oracle_error = authenticated_concrete_oracle_error(oracles)
    if oracle_error:
        return None, oracle_error
    coverage_claim = claim
    projection_binding = {}
    if entry.get("exclude_reverted_return"):
        if claim.get("exit_kind") != "revert" or claim.get("return_value") is None:
            return None, "reverted-return projection has no phantom report return"
        coverage_claim = dict(claim)
        coverage_claim.pop("return_value", None)
        coverage_claim["return_value_known"] = False
        projection_binding = {
            "executable_claim_projection":
            "exclude-reverted-return/v1",
            "executable_claim_sha256":
            _sha256_text(json.dumps(coverage_claim, sort_keys=True, separators=(",", ":"))),
        }
    coverage_error = _oracle_claim_coverage_error(coverage_claim, oracles,
                                                  _entry_event_signatures(entry))
    if coverage_error:
        return None, coverage_error
    binding_errors = _oracle_binding_errors(basis_source, basis_test, str(entry["identity"][2]),
                                            oracles)
    if binding_errors:
        return None, "; ".join(binding_errors)
    claim_json = json.dumps(claim, sort_keys=True, separators=(",", ":"))
    return {
        "cov_report_path": report_relative,
        "cov_report_sha256": report_sha256,
        "claim_sha256": _sha256_text(claim_json),
        "claim_exit_kind": exit_kind,
        "claim_return_value": claim.get("return_value"),
        **projection_binding,
        **fingerprint_binding,
    }, None


def _renamed_function(function_source: str, old: str, new: str) -> str:
    return re.sub(r"(\bfunction\s+)" + re.escape(old) + r"(\s*\()",
                  lambda match: match.group(1) + new + match.group(2),
                  function_source,
                  count=1)


def _has_executable_target_call(body: str, unit: str) -> bool:
    """Recognize direct and exact ABI-encoded calls in executable code only."""
    mask = _solidity_code_mask(body)
    direct = r"\b" + re.escape(unit) + r"\s*(?:\{[^{}]*\}\s*)?\("
    if re.search(direct, mask, re.S):
        return True
    for call in re.finditer(r"\.\s*call\s*(?:\{[^{}]*\}\s*)?\(", mask, re.S):
        opening = mask.rfind("(", call.start(), call.end())
        closing = _matching_delimiter(mask, opening, "(", ")")
        if closing is None:
            continue
        encoded = re.search(r"\babi\s*\.\s*encodeWithSignature\s*\(", mask[opening + 1:closing])
        if encoded is None:
            continue
        literal_start = opening + 1 + encoded.end()
        literal = body[literal_start:closing]
        signature = r"\s*(['\"])" + re.escape(unit) + r"\([^'\"]*\)\1\s*(?:,|\))"
        if re.match(signature, literal):
            return True
    return False


def _prepare(entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Authenticate retained evidence and construct the exact anchor payload."""
    put = entry["put"]
    if entry.get("basis_ambiguity"):
        return None, ("multiple retained concrete bases have different sealed "
                      "source/oracle evidence")
    basis = entry.get("basis") or {}
    oracles = basis.get("concrete_oracles") or []
    oracle_error = authenticated_concrete_oracle_error(oracles)
    if oracle_error:
        return None, oracle_error
    for field in ("unit", "enc", "piece"):
        if basis.get(field) != put.get(field):
            return None, f"basis and PUT differ on {field}"
    put_file = Path(str(put.get("file") or ""))
    basis_file = Path(str(basis.get("file") or ""))
    if not put_file.is_file() or not basis_file.is_file():
        return None, "PUT or retained basis source is absent"
    put_source = put_file.read_text(encoding="utf-8")
    basis_source = basis_file.read_text(encoding="utf-8")
    basis_test = str(basis.get("test") or "")
    put_setup, _put_setup_error = _scoped_function_body(put_source, str(put.get("test") or ""),
                                                        "setUp")
    basis_setup, _basis_setup_error = _scoped_function_body(basis_source, basis_test, "setUp")
    if put_setup is None or put_setup != basis_setup:
        return None, "PUT and basis setUp bodies differ"
    function_source, reason = _source_function(basis_source, basis_test)
    if function_source is None:
        return None, reason
    body, _body_error = _function_body(basis_source, basis_test)
    if body is None:
        return None, "basis function body is absent"
    structural_gate = (entry.get("recovery") or {}).get("partition") == "structural-abi-gate"
    if not structural_gate and not _has_executable_target_call(body, str(put.get("unit"))):
        return None, "basis body contains no executable target call"
    for oracle in oracles:
        assertion = str(oracle.get("assertion") or "")
        if not assertion or not _code_contains_statement(body, assertion):
            return None, "basis oracle assertion is absent from its final body"
        if not oracle.get("kind") or "expected" not in oracle:
            return None, "basis oracle lacks kind or expected value"
    structured_errors = _structured_oracle_errors(oracles)
    if structured_errors:
        return None, "; ".join(structured_errors)
    binding_errors = _oracle_binding_errors(basis_source, basis_test, str(put.get("unit")), oracles)
    if binding_errors:
        return None, "; ".join(binding_errors)
    detail, error = _certified_detail(entry)
    if detail is None:
        return None, error
    ce_hash = certified_ce_sha256(detail.get("ce") or {})
    if ce_hash is None:
        return None, "certified detail has no exact scalar CE claim"
    report_binding, error = _report_binding(entry, detail, oracles, basis_source, basis_test)
    if report_binding is None:
        return None, error
    evidence = {
        "schema": "veriput-certified-ce-anchor-evidence/v1",
        "identity": entry["identity"],
        "certification_record_sha256": detail["_certification_record_sha256"],
        "certified_ce_sha256": ce_hash,
        "basis_source_sha256": _sha256_text(basis_source),
        "basis_setup_sha256": _sha256_text(basis_setup),
        "basis_test_body_sha256": _sha256_text(body),
        "oracles": oracles,
        "report_binding": report_binding,
    }
    evidence_hash = _sha256_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    anchor_test = "test_ce_anchor_" + evidence_hash[:16]
    anchor_source = _renamed_function(function_source, basis_test, anchor_test)
    metadata = {
        "status": "embedded",
        "test": anchor_test,
        "basis_test": basis_test,
        "binding": "certified-exact-basis/v1",
        "identity": entry["identity"],
        "evidence_sha256": evidence_hash,
        "certification_record_sha256": evidence["certification_record_sha256"],
        "certified_ce_sha256": ce_hash,
        "basis_source_sha256": evidence["basis_source_sha256"],
        "basis_setup_sha256": evidence["basis_setup_sha256"],
        "basis_test_body_sha256": evidence["basis_test_body_sha256"],
        "oracles": oracles,
        "report_binding": report_binding,
    }
    return {
        "put_file": put_file,
        "put_test": str(put.get("test")),
        "put_source": put_source,
        "anchor_source": anchor_source,
        "metadata": metadata,
        "required_imports": list(entry.get("required_imports") or []),
    }, None


def _embed(prepared: dict[str, Any]) -> tuple[str | None, str | None]:
    put_source = prepared["put_source"]
    anchor_source = prepared["anchor_source"]
    anchor_test = prepared["metadata"]["test"]
    orphan_names = sorted(set(re.findall(
        r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_]+)\s*\(",
        _solidity_code_mask(put_source))) - {anchor_test})
    orphan_spans = []
    for orphan in orphan_names:
        spans = _solidity_function_spans(put_source, orphan)
        if len(spans) != 1 or spans[0][0] is None:
            return None, "PUT contains an ambiguous orphan anchor function"
        orphan_spans.append(spans[0][0][:2])
    for start, end in sorted(orphan_spans, reverse=True):
        put_source = put_source[:start].rstrip() + "\n" + put_source[end:].lstrip("\n")
    count = _named_function_count(put_source, anchor_test)
    if count > 1:
        return None, "PUT contains duplicate anchor function definitions"
    existing, _reason = _source_function(put_source, anchor_test)
    if count == 1:
        if existing != anchor_source:
            return None, "existing anchor body differs from authenticated basis"
        return put_source, None
    if count != 0:
        return None, "anchor function parser disagrees with function inventory"
    for required_import in prepared.get("required_imports") or []:
        if required_import != 'import {Vm} from "forge-std/Vm.sol";':
            return None, "anchor requested an unsupported import"
        if required_import not in put_source:
            pragma = re.search(r"\bpragma\s+solidity\s+[^;]+;", _solidity_code_mask(put_source))
            if pragma is None:
                return None, "PUT source has no Solidity pragma for required import"
            put_source = (put_source[:pragma.end()] + "\n\n" + required_import +
                          put_source[pragma.end():])
    insert_at = _contract_close_for_function(put_source, str(prepared["put_test"]))
    if insert_at is None:
        return None, "PUT test is not inside a parseable contract"
    return (put_source[:insert_at].rstrip() + "\n\n" + anchor_source.rstrip() + "\n" +
            put_source[insert_at:]), None


def _finalize_metadata(prepared: dict[str, Any],
                       merged: str) -> tuple[dict[str, Any] | None, str | None]:
    """Seal and reparse the exact destination after anchor insertion."""
    anchor_test = prepared["metadata"]["test"]
    put_test = prepared["put_test"]
    if _named_function_count(merged, anchor_test) != 1:
        return None, "final destination does not contain exactly one anchor"
    anchor_source, error = _source_function(merged, anchor_test)
    if anchor_source is None or anchor_source != prepared["anchor_source"]:
        return None, error or "final destination anchor body differs from basis"
    destination_setup, error = _scoped_function_body(merged, put_test, "setUp")
    if destination_setup is None:
        return None, error
    put_before, error = _function_body_any(prepared["put_source"], put_test)
    if put_before is None:
        return None, error
    put_after, error = _function_body_any(merged, put_test)
    if put_after is None or put_after != put_before:
        return None, error or "parameterized PUT body changed during anchor insertion"
    anchor_body, error = _function_body(merged, anchor_test)
    if anchor_body is None:
        return None, error
    metadata = dict(prepared["metadata"])
    metadata["destination"] = {
        "anchor_function_sha256": _sha256_text(anchor_source),
        "anchor_body_sha256": _sha256_text(anchor_body),
        "setup_body_sha256": _sha256_text(destination_setup),
        "put_body_before_sha256": _sha256_text(put_before),
        "put_body_after_sha256": _sha256_text(put_after),
        "source_before_sha256": _sha256_text(prepared["put_source"]),
        "source_after_sha256": _sha256_text(merged),
    }
    return metadata, None


def _project_root(source: Path) -> Path | None:
    for parent in (source.parent, *source.parents):
        if (parent / "foundry.toml").is_file():
            return parent
    return None


def _forge(project: Path,
           source: Path,
           test: str,
           fuzz_runs: int,
           artifact_root: Path | None = None,
           repair_forge_std: bool = True) -> tuple[bool, str, dict[str, Any]]:
    """Require one explicit Success for the exact source/test pair."""
    relative = project_rel_file(str(project), str(source))
    if relative is None:
        return False, "PUT source is outside the Foundry project", {}
    repaired_forge_std = _ensure_forge_std(project) if repair_forge_std else False
    match_test = r"^" + re.escape(test) + r"(\(|$)"
    command = ["forge", "test", "--json", "--match-path", relative, "--match-test", match_test]
    if test.startswith("test_put_"):
        command += ["--fuzz-runs", str(fuzz_runs)]
    environment = os.environ.copy()
    recorded_environment = {}
    if not repair_forge_std:
        forge_std_source = Path(FORGE_STD).resolve() / "src"
        environment["FOUNDRY_REMAPPINGS"] = f"forge-std/={forge_std_source}/"
        recorded_environment["FOUNDRY_REMAPPINGS"] = environment["FOUNDRY_REMAPPINGS"]
    if artifact_root is not None:
        artifact_root.mkdir(parents=True, exist_ok=True)
        environment["FOUNDRY_CACHE_PATH"] = str((artifact_root / "cache").resolve())
        environment["FOUNDRY_OUT"] = str((artifact_root / "out").resolve())
        recorded_environment.update({
            "FOUNDRY_CACHE_PATH": environment["FOUNDRY_CACHE_PATH"],
            "FOUNDRY_OUT": environment["FOUNDRY_OUT"],
        })
    try:
        process = subprocess.run(command,
                                 cwd=project,
                                 env=environment,
                                 capture_output=True,
                                 text=True,
                                 timeout=600,
                                 check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Forge execution failed: {exc}", {}
    output = process.stdout + process.stderr
    statuses, _names, suite_failures = forge_json_status_map(process.stdout)
    normalized = os.path.normpath(relative)
    matches = [
        status for (suite, name), status in statuses.items()
        if (name == test or name.startswith(test + "(")) and os.path.normpath(suite) == normalized
    ]
    ok = (process.returncode == 0 and not suite_failures and len(matches) == 1
          and matches[0] == "Success")
    record = {
        "schema": "veriput-exact-forge-run/v1",
        "command": command,
        "project": str(project.resolve()),
        "source": relative,
        "source_sha256": _sha256_text(source.read_text(encoding="utf-8")),
        "test": test,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "environment": recorded_environment,
        "repaired_forge_std": repaired_forge_std,
    }
    return ok, output[-4000:], record


def _ensure_forge_std(project: Path) -> bool:
    """Repair a generated Foundry project that lost its forge-std symlink."""
    lib = project / "lib" / "forge-std"
    target = Path(FORGE_STD)
    if not target.exists():
        return False
    lib.parent.mkdir(parents=True, exist_ok=True)
    if lib.is_symlink():
        current = Path(os.readlink(lib))
        current_abs = current if current.is_absolute() else (lib.parent / current).resolve()
        if current_abs == target.resolve() and current_abs.exists():
            return False
        lib.unlink()
    if lib.exists():
        return False
    lib.symlink_to(target)
    return True


def _validate_in_scratch(prepared: dict[str, Any], merged: str, project: Path, scratch_root: Path,
                         fuzz_runs: int) -> tuple[dict[str, Any] | None, str | None]:
    """Run both exact Forge gates on an external copy, never canonical files."""
    identity_hash = _sha256_text(json.dumps(prepared["metadata"]["identity"],
                                            separators=(",", ":")))
    destination = scratch_root / "validation" / identity_hash
    subject = _subject_dir_for_source(prepared["put_file"])
    overlaps = (_paths_overlap(destination, project)
                or (subject is not None and _paths_overlap(destination, subject))
                or _paths_overlap(destination, DEFAULT_RESULT_ROOT))
    if overlaps:
        return None, "staging validation destination overlaps canonical results"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(project,
                    destination,
                    symlinks=True,
                    ignore=shutil.ignore_patterns("cache", "out", "broadcast"))
    relative = prepared["put_file"].resolve().relative_to(project.resolve())
    staged_source = destination / relative
    _atomic_text(staged_source, merged)
    forge_artifacts = destination.parent / (destination.name + "-forge-artifacts")
    put_ok, put_tail, put_record = _forge(destination, staged_source, prepared["put_test"],
                                          fuzz_runs, forge_artifacts)
    anchor_ok, anchor_tail, anchor_record = _forge(destination, staged_source,
                                                   prepared["metadata"]["test"], fuzz_runs,
                                                   forge_artifacts)
    result = {
        "schema": "veriput-anchor-staging-validation/v1",
        "identity": prepared["metadata"]["identity"],
        "canonical_source": str(prepared["put_file"]),
        "canonical_source_sha256": _sha256_text(prepared["put_source"]),
        "staged_project": str(destination),
        "staged_source": str(staged_source),
        "staged_source_sha256": _sha256_text(merged),
        "put_forge_ok": put_ok,
        "anchor_forge_ok": anchor_ok,
        "put_forge_tail": put_tail,
        "anchor_forge_tail": anchor_tail,
        "put_run": put_record,
        "anchor_run": anchor_record,
    }
    _atomic_json(destination / "ce-anchor-validation.json", result)
    if not put_ok or not anchor_ok:
        return result, "PUT or anchor staging Forge gate failed"
    if prepared["put_file"].read_text(encoding="utf-8") != prepared["put_source"]:
        return result, "canonical PUT changed during staging validation"
    return result, None


def _sealed_external_validation(prepared: dict[str, Any], merged: str,
                                selector: dict[str, Any]) -> dict[str, Any] | None:
    """Accept a sealed staging result; canonical Forge still runs during commit."""
    validation = selector.get("external_validation")
    if (not isinstance(validation, dict)
            or validation.get("schema") != "veriput-anchor-external-double-forge/v1"
            or validation.get("status") != "validated"
            or validation.get("staged_source_sha256") != _sha256_text(merged)
            or validation.get("put_forge_ok") is not True
            or validation.get("anchor_forge_ok") is not True
            or validation.get("anchor_test") != prepared["metadata"]["test"]):
        return None
    report = validation.get("report") or {}
    report_path = Path(str(report.get("path") or ""))
    if (_sha256_file(report_path) != report.get("sha256") or not report_path.is_file()
            or report_path.stat().st_size != report.get("bytes")):
        return None
    for role, test in (("put_run", prepared["put_test"]), ("anchor_run",
                                                           prepared["metadata"]["test"])):
        record = validation.get(role)
        if (not isinstance(record, dict) or record.get("schema") != "veriput-exact-forge-run/v1"
                or record.get("returncode") != 0 or record.get("test") != test
                or record.get("source_sha256") != _sha256_text(merged)):
            return None
        statuses, _names, failures = forge_json_status_map(str(record.get("stdout") or ""))
        suite = os.path.normpath(str(record.get("source") or ""))
        matches = [
            status for (candidate_suite, candidate_test), status in statuses.items()
            if os.path.normpath(candidate_suite) == suite and (
                candidate_test == test or candidate_test.startswith(test + "("))
        ]
        if failures or matches != ["Success"]:
            return None
    return validation


def _forge_record_metadata(put_json: Path, role: str,
                           record: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    """Return a deterministic durable Forge record and its content binding."""
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    digest = _sha256_text(rendered)
    path = put_json.parent / "ce-anchor-forge" / f"{role}-{digest[:16]}.json"
    return path, {
        "record_path": path.relative_to(put_json.parent).as_posix(),
        "record_sha256": digest,
    }


def _metadata_documents(
        entry: dict[str, Any],
        metadata: dict[str, Any]) -> tuple[Path, dict[str, Any], Path, dict[str, Any], list[dict]]:
    """Build exact physical-row metadata updates without writing files."""
    put_json = Path(str(entry["put"].get("put_json") or ""))
    doc = _load_json(put_json)
    if not doc:
        raise RuntimeError("PUT put.json is absent")
    identity_error = _put_document_identity_error(doc, {"test": entry["put"].get("test")},
                                                  list(entry["identity"]),
                                                  Path(str(entry["put"].get("file") or "")))
    if identity_error:
        raise RuntimeError(identity_error)
    doc["file"] = str(entry["put"].get("file"))
    doc["ce_anchor"] = metadata
    result_path = Path(entry["result_json"])
    result = _load_json(result_path)
    changed = 0
    changed_rows = []
    containers = []
    for owner in (result.get("row"), result.get("put")):
        if isinstance(owner, dict):
            for key in ("valid_artifacts", "valid_tests"):
                if isinstance(owner.get(key), list):
                    containers.append(owner[key])
    for rows in containers:
        for row in rows:
            if (isinstance(row, dict) and _physical_test_kind(row) == "put"
                    and row.get("test") == entry["put"].get("test")
                    and Path(str(row.get("file") or "")).resolve() == Path(
                        str(entry["put"].get("file") or "")).resolve()
                    and Path(str(row.get("put_json") or "")).resolve() == put_json.resolve()):
                identity_error = _put_document_identity_error(
                    {
                        **row,
                        "path_function": row.get("path_function") or entry["identity"][1],
                    }, {"test": entry["put"].get("test")}, list(entry["identity"]),
                    Path(str(entry["put"].get("file") or "")))
                if identity_error:
                    raise RuntimeError(f"result physical row {identity_error}")
                row.update({
                    "path_function": entry["identity"][1],
                    "unit": entry["identity"][2],
                    "enc": entry["put"].get("enc"),
                    "piece": entry["put"].get("piece"),
                })
                row["ce_anchor"] = metadata
                changed_rows.append(row)
                changed += 1
    if changed == 0:
        raise RuntimeError("exact physical PUT row is absent from result.json")
    physical_keys = {
        (str(row.get("test") or ""), str(row.get("file") or ""),
         str(row.get("put_json") or "")) for row in changed_rows
    }
    if len(physical_keys) != 1:
        raise RuntimeError("result.json does not contain exactly one physical PUT identity")
    return put_json, doc, result_path, result, changed_rows


def _headline_anchor_strength_error(entry: dict[str, Any],
                                    physical_rows: list[dict[str, Any]]) -> str | None:
    """Run the reporting audit against every just-written result physical row."""
    if not physical_rows:
        return "headline anchor strength audit failed: no result physical row"
    for row in physical_rows:
        confirmed, reason = _anchor_strength_audit(
            row, identity=tuple(entry["identity"]), subject_dir=Path(entry["subject_dir"]))
        if not confirmed:
            return f"headline anchor strength audit failed: {reason}"
    return None


def _manifest(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema":
        "veriput-rq1-put-ce-anchor-backfill/v1",
        "identity": ["target", "path_function", "unit", "enc", "piece"],
        "generalized_ce_obligations":
        len(entries),
        "entries": [{
            "identity":
            entry["identity"],
            "put_file":
            entry["put"].get("file"),
            "put_json":
            entry["put"].get("put_json"),
            "basis_file": (entry.get("basis") or {}).get("file"),
            "basis_oracle_kinds": [
                oracle.get("kind")
                for oracle in ((entry.get("basis") or {}).get("concrete_oracles") or [])
            ],
        } for entry in entries],
    }


def _progress_report(entries: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "veriput-rq1-put-ce-anchor-progress/v1",
        "frozen_generalized_ce_obligations": len(entries),
        "counts": {
            status: sum(item["status"] == status for item in rows)
            for status in sorted({item["status"]
                                  for item in rows})
        },
        "rows": rows,
    }


def _frozen_identities(manifest: dict[str, Any]) -> set[tuple[str, ...]] | None:
    rows = manifest.get("entries")
    if not isinstance(rows, list):
        return None
    identities = []
    for row in rows:
        identity = row.get("identity") if isinstance(row, dict) else None
        if not isinstance(identity, list) or len(identity) != 5:
            return None
        identities.append(tuple(str(value) for value in identity))
    if len(set(identities)) != len(identities):
        return None
    return set(identities)


def main() -> int:
    """Run a dry-run, staging validation, or transactional canonical apply."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--progress", type=Path, default=DEFAULT_PROGRESS)
    parser.add_argument("--recovery-inventory", type=Path)
    parser.add_argument("--recovery-scratch-root", type=Path, default=DEFAULT_RECOVERY_SCRATCH)
    parser.add_argument("--recovery-partition", choices=sorted(SUPPORTED_PARTITIONS))
    parser.add_argument("--partition-artifact", type=Path)
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("--isolated-ledger", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--record-limit", type=int, default=0)
    parser.add_argument("--record-offset", type=int, default=0)
    parser.add_argument("--fuzz-runs", type=int, default=256)
    args = parser.parse_args()
    if args.apply and args.validate_only:
        parser.error("--apply and --validate-only are mutually exclusive")
    if (args.isolated_ledger
            and args.result_root.resolve() == DEFAULT_RESULT_ROOT.resolve()):
        parser.error("--isolated-ledger refuses the canonical result root")
    if args.record_offset < 0:
        parser.error("--record-offset must be non-negative")
    if bool(args.recovery_partition) != bool(args.partition_artifact):
        parser.error("--recovery-partition and --partition-artifact must be used together")
    if args.recovery_partition and args.recovery_inventory is None:
        parser.error("a partition run requires --recovery-inventory")
    scratch_error = _scratch_root_error(args.recovery_scratch_root)
    if args.recovery_inventory is not None and scratch_error:
        parser.error(scratch_error)
    if args.recovery_inventory is not None:
        if args.freeze:
            parser.error("--recovery-inventory cannot freeze the canonical PUT ledger")
        if args.recovery_partition:
            entries, rows = _partition_dry_run(args.recovery_inventory, args.partition_artifact,
                                               args.recovery_partition, args.recovery_scratch_root,
                                               args.record_limit, args.record_offset)
        else:
            entries, rows = _recovery_dry_run(args.recovery_inventory, args.recovery_scratch_root,
                                              args.record_limit)
        if not args.apply and not args.validate_only:
            report = _progress_report(entries, rows)
            report["recovery_inventory"] = str(args.recovery_inventory)
            report["recovery_scratch_root"] = str(args.recovery_scratch_root)
            if args.recovery_partition:
                report["recovery_partition"] = args.recovery_partition
                report["partition_artifact"] = str(args.partition_artifact)
            _atomic_json(args.progress, report)
            print(json.dumps(report["counts"], sort_keys=True))
            return 0
        entries = [
            entry for entry in entries
            if (entry.get("_recovery_progress_row") or {}).get("status") == "ready"
        ]
        rows = [row for row in rows if row.get("status") != "ready"]
        pending = []
        for entry in entries:
            if _already_strength_confirmed(entry):
                row = dict(entry.get("_recovery_progress_row") or {"identity": entry["identity"]})
                row.update(status="already-embedded", reason=None)
                rows.append(row)
            else:
                pending.append(entry)
        entries = pending
    else:
        entries = _deduplicated_puts(args.result_root)
        current = _manifest(entries)
        expected_obligations = len(entries) if args.isolated_ledger else 1263
        if args.freeze:
            if len(entries) != expected_obligations:
                parser.error(f"refusing to freeze {len(entries)} identities; expected "
                             f"{expected_obligations}")
            _atomic_json(args.manifest, current)
        else:
            frozen = _load_json(args.manifest)
            if (frozen.get("generalized_ce_obligations") != expected_obligations
                    or _frozen_identities(frozen) is None
                    or _frozen_identities(frozen) != _frozen_identities(current)):
                parser.error("current generalized identity inventory differs from frozen manifest")

    initial_rows = list(rows) if args.recovery_inventory is not None else []
    rows = initial_rows
    applied = 0
    attempted = 0
    for entry in entries:
        prepared, error = _prepare(entry)
        row = dict(entry.get("_recovery_progress_row") or {"identity": entry["identity"]})
        row.update(status="refused" if error else "ready", reason=error)
        if error or (not args.apply and not args.validate_only) or attempted >= args.limit:
            rows.append(row)
            continue
        attempted += 1
        merged, error = _embed(prepared)
        if merged is None:
            row.update(status="refused", reason=error)
            rows.append(row)
            continue
        metadata, error = _finalize_metadata(prepared, merged)
        if metadata is None:
            row.update(status="refused", reason=error)
            rows.append(row)
            continue
        put_file = prepared["put_file"]
        project = _project_root(put_file)
        if project is None:
            row.update(status="refused", reason="Foundry project root is absent")
            rows.append(row)
            continue
        if args.validate_only:
            validation, validation_error = _validate_in_scratch(prepared, merged, project,
                                                                args.recovery_scratch_root,
                                                                args.fuzz_runs)
            row.update(status="validated" if validation_error is None else "forge-failed",
                       reason=validation_error,
                       put_forge_ok=bool(validation and validation.get("put_forge_ok")),
                       anchor_forge_ok=bool(validation and validation.get("anchor_forge_ok")),
                       validation=validation)
            rows.append(row)
            if validation_error is None:
                applied += 1
            continue
        validation = _sealed_external_validation(prepared, merged,
                                                 entry.get("_prevalidated_selector") or {})
        validation_error = None
        if validation is None:
            validation, validation_error = _validate_in_scratch(prepared, merged, project,
                                                                args.recovery_scratch_root,
                                                                args.fuzz_runs)
        if validation is None or validation_error:
            row.update(status="forge-failed",
                       reason=validation_error or "staging Forge validation is absent",
                       put_forge_ok=bool(validation and validation.get("put_forge_ok")),
                       anchor_forge_ok=bool(validation and validation.get("anchor_forge_ok")))
            rows.append(row)
            continue
        put_json_path = Path(str(entry["put"].get("put_json") or ""))
        result_path = Path(entry["result_json"])
        committed = False
        transaction_originals: dict[Path, str | None] = {}
        transaction_written: dict[Path, str | None] = {}
        with _transaction_lock(result_path):
            try:
                transaction_originals = {
                    put_file: put_file.read_text(encoding="utf-8"),
                    put_json_path: put_json_path.read_text(encoding="utf-8"),
                    result_path: result_path.read_text(encoding="utf-8"),
                }
                if transaction_originals[put_file] != prepared["put_source"]:
                    raise RuntimeError("PUT source changed after evidence preparation")
                _atomic_text(put_file, merged)
                transaction_written[put_file] = merged
                identity_hash = _sha256_text(json.dumps(entry["identity"], separators=(",", ":")))
                canonical_artifacts = (args.recovery_scratch_root / "canonical-forge" /
                                       identity_hash)
                put_ok, put_tail, put_record = _forge(project, put_file, prepared["put_test"],
                                                      args.fuzz_runs, canonical_artifacts, False)
                anchor_ok, anchor_tail, anchor_record = _forge(project, put_file, metadata["test"],
                                                               args.fuzz_runs, canonical_artifacts,
                                                               False)
                row.update(put_forge_ok=put_ok,
                           anchor_forge_ok=anchor_ok,
                           put_forge_tail=put_tail,
                           anchor_forge_tail=anchor_tail)
                if not put_ok or not anchor_ok:
                    raise RuntimeError("canonical PUT or anchor Forge gate failed")
                if put_file.read_text(encoding="utf-8") != merged:
                    raise RuntimeError("canonical PUT changed during final Forge gates")
                put_record_path, put_record_binding = _forge_record_metadata(
                    put_json_path, "put", put_record)
                anchor_record_path, anchor_record_binding = _forge_record_metadata(
                    put_json_path, "anchor", anchor_record)
                for record_path, record in ((put_record_path, put_record), (anchor_record_path,
                                                                            anchor_record)):
                    transaction_originals[record_path] = (record_path.read_text(
                        encoding="utf-8") if record_path.is_file() else None)
                    _atomic_json(record_path, record)
                    transaction_written[record_path] = _render_json(record)
                metadata["forge_gate"] = {
                    "schema": "veriput-put-anchor-forge-gate/v1",
                    "put_test": str(entry["put"].get("test")),
                    "anchor_test": metadata["test"],
                    "put_status": "Success",
                    "anchor_status": "Success",
                    "source_sha256": _sha256_text(merged),
                    "put_run": put_record_binding,
                    "anchor_run": anchor_record_binding,
                }
                if (put_json_path.read_text(encoding="utf-8")
                        != transaction_originals[put_json_path]
                        or result_path.read_text(encoding="utf-8")
                        != transaction_originals[result_path]):
                    raise RuntimeError("canonical metadata changed during anchor commit")
                put_json, put_doc, result_json, result_doc, physical_rows = _metadata_documents(
                    entry, metadata)
                _atomic_json(put_json, put_doc)
                transaction_written[put_json] = _render_json(put_doc)
                _atomic_json(result_json, result_doc)
                transaction_written[result_json] = _render_json(result_doc)
                headline_error = _headline_anchor_strength_error(entry, physical_rows)
                if headline_error:
                    raise RuntimeError(headline_error)
                embedded_row = dict(row)
                embedded_row.update(status="embedded",
                                    reason=None,
                                    put_forge_ok=True,
                                    anchor_forge_ok=True,
                                    ce_anchor=metadata)
                _atomic_json(args.progress, _progress_report(entries, rows + [embedded_row]))
                committed = True
            except (OSError, RuntimeError, ValueError) as exc:
                row.update(status="transaction-failed", reason=str(exc))
            finally:
                if not committed:
                    conflicts = _restore_transaction_files(transaction_originals,
                                                           transaction_written)
                    if conflicts:
                        row.update(
                            status="transaction-failed",
                            reason=(str(row.get("reason") or "transaction failed") +
                                    "; concurrent changes preserved: " + ", ".join(conflicts)))
        if committed:
            row = embedded_row
            applied += 1
        rows.append(row)
    report = _progress_report(entries, rows)
    _atomic_json(args.progress, report)
    print(json.dumps(report["counts"], sort_keys=True))
    failed = any(
        item.get("status") in ("forge-failed", "transaction-failed", "refused") for item in rows)
    return 1 if (args.apply or args.validate_only) and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
