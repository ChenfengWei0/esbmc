#!/usr/bin/env python3
"""Materialize the sealed C-history structured-provenance partition.

This is intentionally an isolated consumer.  Its normal mode never writes
canonical RQ1 artifacts; the guarded ``--apply-ready`` mode accepts only the
sealed, double-Forge-green handoff and delegates writes to the shared
transactional backfill driver.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rq1_anchor_revert as revert_partition  # pylint: disable=wrong-import-position
import rq1_put_ce_anchor_backfill as backfill  # pylint: disable=wrong-import-position

FROZEN_C27_VALIDATED_INVENTORY_SHA256 = (
    "e21a4805e7a65c7dc39b36495bfecf2c9516dbedbad60076dfd4186f49da3cb7")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(record: dict[str, Any]) -> tuple[str, ...] | None:
    value = record.get("identity")
    if isinstance(value, list) and len(value) == 5:
        return tuple(str(item) for item in value)
    if isinstance(value, dict):
        fields = ("case", "path_function", "unit", "enc", "piece")
        if all(field in value for field in fields):
            return tuple(str(value[field]) for field in fields)
    return None


def _index(records: object, label: str) -> dict[tuple[str, ...], dict[str, Any]]:
    if not isinstance(records, list):
        raise ValueError(f"{label} has no records list")
    result: dict[tuple[str, ...], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{label} contains a non-object record")
        key = _identity(record)
        if key is None or key in result:
            raise ValueError(f"{label} has a malformed or duplicate identity")
        result[key] = record
    return result


def _binding_error(selector: dict[str, Any], sealed: dict[str, Any],
                   consumer: dict[str, Any]) -> str | None:
    """Require every historical evidence layer to state identical values."""
    # pylint: disable=too-many-locals,too-many-boolean-expressions
    if selector.get("status") != "refused" or selector.get("reason") != (
            "concrete replay lacks structured witness oracle provenance"):
        return "selector is outside the structured-provenance partition"
    identity_sha256 = selector.get("record_identity_sha256")
    if identity_sha256 != sealed.get("identity_sha256") or identity_sha256 != consumer.get(
            "identity_sha256"):
        return "selector/sealed/consumer identity digests differ"

    certification = sealed.get("certification_seal") or {}
    source = sealed.get("selected_source") or {}
    report = sealed.get("selected_report") or {}
    basis = consumer.get("certified_basis") or {}
    claim = consumer.get("claim_provenance") or {}
    history = consumer.get("sealed_history_recovery") or {}
    selected_put = sealed.get("selected_put") or {}
    consumed_put = consumer.get("selected_put") or {}
    checks = {
        "certified CE": (certification.get("ce_sha256"), source.get("certified_ce_sha256"),
                         basis.get("ce_sha256"), history.get("certified_ce_sha256")),
        "certified detail": (certification.get("detail_sha256"), basis.get("detail_sha256"),
                             history.get("certification_detail_sha256")),
        "certification path": (certification.get("path"), basis.get("source_path")),
        "certification line": (certification.get("source_line"), basis.get("source_line")),
        "certification line digest":
        (certification.get("source_line_sha256"), basis.get("source_line_sha256")),
        "claim": (report.get("claim_sha256"), source.get("claim_sha256"), claim.get("claim_sha256"),
                  history.get("claim_sha256")),
        "report": (report.get("report_sha256"), history.get("original_report_sha256")),
        "evidence chain":
        (source.get("evidence_chain_sha256"), history.get("evidence_chain_sha256")),
        "source path": (source.get("path"), history.get("original_source_path")),
        "source digest": (source.get("source_sha256"), history.get("original_source_sha256")),
        "test": (source.get("test"), history.get("selected_test")),
        "test digest": (source.get("test_function_sha256"),
                        history.get("selected_test_function_sha256")),
        "setup digest": (source.get("setup_sha256"), history.get("selected_setup_sha256")),
        "PUT source": (selected_put.get("source_sha256"), consumed_put.get("source_sha256")),
        "PUT json": (selected_put.get("put_json_sha256"), consumed_put.get("put_json_sha256")),
        "PUT test": (selected_put.get("test"), consumed_put.get("test")),
        "PUT source path": (selected_put.get("source_path"), consumed_put.get("source_path")),
        "PUT json path": (selected_put.get("put_json_path"), consumed_put.get("put_json_path")),
        "original report path": (report.get("path"), history.get("original_report_path")),
        "original source path": (source.get("path"), history.get("original_source_path")),
        "staged report path": (claim.get("report_path"), history.get("staged_report_path")),
    }
    for label, values in checks.items():
        if not values[0] or len(set(values)) != 1:
            return f"{label} binding differs across sealed evidence"
    if sealed.get("required_observable_kinds") != [
            "revert"
    ] or (consumer.get("observable_evidence") or {}).get("anchor_required_kinds") != ["revert"]:
        return "identity is not a revert-only obligation"
    staged_source = Path(str(history.get("staged_source_path") or ""))
    staged_report = Path(str(history.get("staged_report_path") or ""))
    if (not staged_source.is_file() or not staged_report.is_file()
            or staged_source.parent != staged_report.parent
            or history.get("staged_source_sha256") != _sha256_file(staged_source)
            or history.get("staged_report_sha256") != _sha256_file(staged_report)
            or claim.get("report_sha256") != history.get("staged_report_sha256")):
        return "staged source/report paths do not bind one sealed evidence directory"
    return None


def _output_path_error(paths: list[Path], protected: list[Path], result_root: Path) -> str | None:
    """Keep every mutable output disjoint from canonical and sealed inputs."""
    resolved = [path.resolve() for path in paths]
    if any(
            backfill._paths_overlap(path, result_root)  # pylint: disable=protected-access
            for path in resolved):
        return "mutable output overlaps canonical RQ1 results"
    if any(
            backfill._paths_overlap(path, sealed)  # pylint: disable=protected-access
            for path in resolved for sealed in protected):
        return "mutable output overlaps a sealed input"
    for index, path in enumerate(resolved):
        if any(
                backfill._paths_overlap(path, other)  # pylint: disable=protected-access
                for other in resolved[index + 1:]):
            return "mutable outputs overlap each other"
    return None


def _consumer_document(selector_path: Path, sealed_path: Path,
                       source_consumer_path: Path) -> dict[str, Any]:
    selector_document = json.loads(selector_path.read_text(encoding="utf-8"))
    sealed_document = json.loads(sealed_path.read_text(encoding="utf-8"))
    consumer_document = json.loads(source_consumer_path.read_text(encoding="utf-8"))
    selectors = _index(selector_document.get("records"), "selector")
    sealed = _index(sealed_document.get("records"), "sealed inventory")
    consumers = _index(consumer_document.get("records"), "source consumer inventory")
    if len(selectors) != 27:
        raise ValueError(f"selector has {len(selectors)} identities, expected 27")
    records = []
    for key, selector in selectors.items():
        if key not in sealed or key not in consumers:
            raise ValueError("selector identity is absent from a consumed inventory")
        error = _binding_error(selector, sealed[key], consumers[key])
        if error:
            raise ValueError(f"{' | '.join(key)}: {error}")
        records.append(consumers[key])
    return {
        "schema": "veriput-rq1-anchor-c-provenance-consumer/v1",
        "records": records,
        "summary": {
            "canonical_writes": False,
            "records": len(records),
            "selector": str(selector_path),
            "selector_sha256": _sha256_file(selector_path),
            "sealed_inventory": str(sealed_path),
            "sealed_inventory_sha256": _sha256_file(sealed_path),
            "source_consumer_inventory": str(source_consumer_path),
            "source_consumer_inventory_sha256": _sha256_file(source_consumer_path),
        },
    }


def _dry_run(document: dict[str, Any],
             scratch_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries = []
    rows = []
    for record in document["records"]:
        key = _identity(record) or ()
        entry, error = revert_partition.recover_entry(record, scratch_root)
        prepared = None
        if entry is not None:
            prepared, error = backfill._prepare(entry)  # pylint: disable=protected-access
        metadata = (prepared or {}).get("metadata") or {}
        report = metadata.get("report_binding") or {}
        row = {
            "identity": list(key),
            "record_identity_sha256": record.get("identity_sha256"),
            "status": "ready" if prepared is not None else "refused",
            "reason": error,
            "basis_source_sha256": metadata.get("basis_source_sha256"),
            "certification_record_sha256": metadata.get("certification_record_sha256"),
            "certified_ce_sha256": metadata.get("certified_ce_sha256"),
            "claim_sha256": report.get("claim_sha256"),
            "cov_report_sha256": report.get("cov_report_sha256"),
            "anchor_test": metadata.get("test"),
            "oracle_kinds": [oracle.get("kind") for oracle in metadata.get("oracles") or []],
        }
        rows.append(row)
        if prepared is not None:
            entries.append({"record": record, "entry": entry, "prepared": prepared, "row": row})
    return entries, rows


def _validate_one(item: dict[str, Any], scratch_root: Path, fuzz_runs: int) -> dict[str, Any]:
    prepared = item["prepared"]
    row = dict(item["row"])
    merged, error = backfill._embed(prepared)  # pylint: disable=protected-access
    if merged is None:
        row.update(status="refused", reason=error)
        return row
    metadata, error = backfill._finalize_metadata(  # pylint: disable=protected-access
        prepared, merged)
    if metadata is None:
        row.update(status="refused", reason=error)
        return row
    put_file = Path(str(prepared["put_file"]))
    project = backfill._project_root(put_file)  # pylint: disable=protected-access
    if project is None:
        row.update(status="refused", reason="Foundry project root is absent")
        return row
    validation, error = backfill._validate_in_scratch(  # pylint: disable=protected-access
        prepared, merged, project, scratch_root, fuzz_runs)
    row.update(
        status="validated" if error is None else "forge-failed",
        reason=error,
        put_forge_ok=bool(validation and validation.get("put_forge_ok")),
        anchor_forge_ok=bool(validation and validation.get("anchor_forge_ok")),
        validation=validation,
    )
    return row


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def apply_ready_partition(sealed_path: Path, progress_path: Path, scratch_root: Path,
                          fuzz_runs: int, limit: int) -> int:
    """Apply only the identities bound to the sealed validated C27 handoff."""
    # pylint: disable=too-many-locals,too-many-boolean-expressions
    sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    summary = sealed.get("summary") or {}
    consumer_path = Path(str(summary.get("source_consumer_inventory") or ""))
    validation_path = Path(str(summary.get("validation_progress") or ""))
    if (_sha256_file(sealed_path) != FROZEN_C27_VALIDATED_INVENTORY_SHA256
            or sealed.get("schema") != "veriput-rq1-anchor-c-provenance-validated-consumer/v1"
            or summary.get("canonical_writes") is not False
            or summary.get("records") != 12
            or summary.get("required_status") != "validated"
            or summary.get("required_put_forge_ok") is not True
            or summary.get("required_anchor_forge_ok") is not True
            or not consumer_path.is_file() or not validation_path.is_file()
            or _sha256_file(consumer_path) != summary.get("source_consumer_inventory_sha256")
            or _sha256_file(validation_path) != summary.get("validation_progress_sha256")):
        raise ValueError("sealed C27 validated inventory is absent, stale, or malformed")
    output_error = _output_path_error([progress_path, scratch_root],
                                      [sealed_path, consumer_path, validation_path],
                                      backfill.DEFAULT_RESULT_ROOT.resolve())
    if output_error:
        raise ValueError(output_error)

    consumer = json.loads(consumer_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if (consumer.get("schema") != "veriput-rq1-anchor-c-provenance-consumer/v1"
            or validation.get("schema") != "veriput-rq1-anchor-c-provenance-progress/v1"
            or Path(str(validation.get("consumer_inventory") or "")).resolve()
            != consumer_path.resolve()
            or validation.get("consumer_inventory_sha256") != _sha256_file(consumer_path)):
        raise ValueError("C27 consumer and validation progress bindings differ")
    records = _index(sealed.get("records"), "sealed validated inventory")
    consumer_records = _index(consumer.get("records"), "source consumer inventory")
    selectors = {
        tuple(str(value) for value in row.get("identity") or []): row
        for row in validation.get("rows") or []
        if isinstance(row, dict) and row.get("status") == "validated"
        and row.get("put_forge_ok") is True and row.get("anchor_forge_ok") is True
    }
    if (len(records) != 12 or len(selectors) != 12 or set(records) != set(selectors)
            or any(key not in consumer_records or record != consumer_records[key]
                   for key, record in records.items())
            or any(record.get("identity_sha256") !=
                   selectors[key].get("record_identity_sha256")
                   for key, record in records.items())):
        raise ValueError("sealed C27 records differ from consumer or double-Forge validation")

    original = backfill._materialize_recovery_basis  # pylint: disable=protected-access

    def selected_materializer(record: dict[str, Any], root: Path):
        identity = _identity(record)
        if identity not in selectors:
            return None, "identity is outside the sealed C27 validated partition"
        entry, error = revert_partition.recover_entry(record, root)
        if entry is None:
            return None, error
        prepared, error = backfill._prepare(entry)  # pylint: disable=protected-access
        if prepared is None:
            return None, error
        metadata = prepared.get("metadata") or {}
        report = metadata.get("report_binding") or {}
        selector = selectors[identity]
        seals = {
            "basis_source_sha256": metadata.get("basis_source_sha256"),
            "certification_record_sha256": metadata.get("certification_record_sha256"),
            "certified_ce_sha256": metadata.get("certified_ce_sha256"),
            "claim_sha256": report.get("claim_sha256"),
            "cov_report_sha256": report.get("cov_report_sha256"),
            "anchor_test": metadata.get("test"),
            "oracle_kinds": [oracle.get("kind") for oracle in metadata.get("oracles") or []],
        }
        if any(selector.get(key) != value for key, value in seals.items()):
            return None, "re-materialized C27 provenance differs from validation seals"
        entry["_prevalidated_selector"] = selector
        return entry, error

    backfill._materialize_recovery_basis = selected_materializer  # pylint: disable=protected-access
    prior_argv = sys.argv
    try:
        sys.argv = [
            "rq1_put_ce_anchor_backfill.py", "--recovery-inventory", str(sealed_path),
            "--recovery-scratch-root", str(scratch_root), "--progress", str(progress_path),
            "--apply", "--limit", str(min(limit, len(selectors))), "--fuzz-runs",
            str(fuzz_runs)
        ]
        return backfill.main()
    finally:
        sys.argv = prior_argv
        backfill._materialize_recovery_basis = original  # pylint: disable=protected-access


def main() -> int:
    """Build the sealed consumer, then dry-run or validate its strict entries."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--selector", type=Path)
    parser.add_argument("--sealed-inventory", type=Path)
    parser.add_argument("--source-consumer-inventory", type=Path)
    parser.add_argument("--result-root", type=Path, default=backfill.DEFAULT_RESULT_ROOT)
    parser.add_argument("--consumer-output", type=Path)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--apply-ready", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--fuzz-runs", type=int, default=256)
    args = parser.parse_args()
    if args.apply_ready is not None:
        if any(value is not None for value in (args.selector, args.sealed_inventory,
                                               args.source_consumer_inventory,
                                               args.consumer_output)):
            parser.error("--apply-ready does not accept inventory generation arguments")
        if args.limit < 1:
            parser.error("--limit must be positive")
        return apply_ready_partition(args.apply_ready, args.progress, args.scratch_root,
                                     args.fuzz_runs, args.limit)
    if any(value is None for value in (args.selector, args.sealed_inventory,
                                       args.source_consumer_inventory,
                                       args.consumer_output)):
        parser.error("selector and inventory generation arguments are required")
    protected = [
        args.selector.resolve(),
        args.sealed_inventory.resolve(),
        args.source_consumer_inventory.resolve()
    ]
    output_error = _output_path_error([args.consumer_output, args.progress, args.scratch_root],
                                      protected, args.result_root.resolve())
    if output_error:
        parser.error(output_error)
    document = _consumer_document(args.selector, args.sealed_inventory,
                                  args.source_consumer_inventory)
    _write_json(args.consumer_output, document)
    entries, rows = _dry_run(document, args.scratch_root)
    if args.validate:
        selected = entries[:args.limit] if args.limit else entries
        selected_ids = {tuple(item["row"]["identity"]) for item in selected}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(_validate_one, item, args.scratch_root, args.fuzz_runs)
                for item in selected
            ]
            validated = [future.result() for future in futures]
        replacements = {tuple(row["identity"]): row for row in validated}
        rows = [replacements.get(tuple(row["identity"]), row) for row in rows]
        if len(replacements) != len(selected_ids):
            raise RuntimeError("validation results do not close over the selected identities")
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in sorted({row["status"]
                              for row in rows})
    }
    report = {
        "schema": "veriput-rq1-anchor-c-provenance-progress/v1",
        "canonical_writes": False,
        "consumer_inventory": str(args.consumer_output),
        "consumer_inventory_sha256": _sha256_file(args.consumer_output),
        "counts": counts,
        "rows": rows,
    }
    _write_json(args.progress, report)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
