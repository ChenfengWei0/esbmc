#!/usr/bin/env python3
"""Transactionally adopt the Proxy extcall piece-1 PUTs.

This intentionally supports one reviewed case and two new identities.  The
default is a full dry-run against a temporary clone of the canonical subject.
"""

# This case-specific transaction intentionally composes the reviewed internal
# APIs of the anchor backfill module instead of duplicating their logic.
# pylint: disable=protected-access,too-many-locals,too-many-boolean-expressions
# pylint: disable=too-many-branches,too-many-statements

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

import rq1_put_ce_anchor_backfill as backfill
from rq1_concrete_replay_migrate import _strict_valid_tests
from rq1_concrete_replay_store import (
    _atomic_json, annotate_generalization, audit_manifest, load_manifest,
    persistence_coverage,
)
from rq1_final_test_inventory import _anchor_strength_audit


DATASET = "bugfix124"
SUBJECT = "rc_access_control__proxy__SolGPT__proxy_4round"
CASE = f"{DATASET}/{SUBJECT}"
PATH_FUNCTION = "sol:@C@Proxy@F@forward#35"
UNIT = "forward"
NEW_IDENTITIES = {
    (CASE, PATH_FUNCTION, UNIT, "6", "1"),
    (CASE, PATH_FUNCTION, UNIT, "7", "1"),
}
DEFAULT_CANONICAL_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _subject(root: Path) -> Path:
    return root / DATASET / "subjects" / SUBJECT


def _cert_preimage(subject: Path) -> dict[str, bytes]:
    cert = subject / "cert"
    return {str(path.relative_to(cert)): path.read_bytes()
            for path in cert.rglob("*") if path.is_file()}


def _audit_cert_preimage(subject: Path, preimage: dict[str, bytes]) -> None:
    cert = subject / "cert"
    ledgers = {"certify-results.jsonl", "shards/001-forward.jsonl"}
    for relative, before in preimage.items():
        path = cert / relative
        if not path.is_file():
            raise RuntimeError(f"canonical cert preimage disappeared: {relative}")
        after = path.read_bytes()
        if relative in ledgers:
            if not after.startswith(before):
                raise RuntimeError(f"canonical cert ledger prefix changed: {relative}")
        elif hashlib.sha256(after).digest() != hashlib.sha256(before).digest():
            raise RuntimeError(f"canonical frozen cert evidence changed: {relative}")


def _rewrite(value, old: str, new: str):
    if isinstance(value, dict):
        return {key: _rewrite(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def _rewrite_json_tree(root: Path, old: str, new: str) -> None:
    for path in root.rglob("*.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        _atomic_json(path, _rewrite(document, old, new))
    for path in root.rglob("*.jsonl"):
        records = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(_rewrite(json.loads(line), old, new))
        except (OSError, json.JSONDecodeError):
            continue
        path.write_text("".join(json.dumps(record, sort_keys=True) + "\n"
                                for record in records), encoding="utf-8")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _absolute_paths(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _absolute_paths(item)
    elif isinstance(value, list):
        for item in value:
            yield from _absolute_paths(item)
    elif isinstance(value, str) and Path(value).is_absolute():
        yield Path(value)


def _identity(row: dict) -> tuple[str, str, str, str, str]:
    piece = "" if row.get("piece") is None else str(row.get("piece"))
    return (CASE, str(row.get("path_function") or ""), str(row.get("unit") or ""),
            str(row.get("enc")), piece)


def _physical_key(row: dict) -> tuple[str, ...]:
    """Return the complete result-row identity used for owner consistency."""
    return (str(row.get("file") or ""), str(row.get("test") or ""),
            str(row.get("kind") or ""), str(row.get("unit") or ""),
            str(row.get("path_function") or ""), str(row.get("enc")),
            str(row.get("piece") or ""))


def _remove_anchors(source: str) -> str:
    names = sorted(set(re.findall(
        r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_]+)\s*\(",
        backfill._solidity_code_mask(source))))
    spans = []
    for name in names:
        matches = backfill._solidity_function_spans(source, name)
        if len(matches) != 1 or matches[0][0] is None:
            raise RuntimeError(f"ambiguous pre-adoption anchor {name}")
        spans.append(matches[0][0][:2])
    for start, end in sorted(spans, reverse=True):
        source = source[:start].rstrip() + "\n" + source[end:].lstrip("\n")
    return source


def _new_rows(source_subject: Path, target_subject: Path) -> list[dict]:
    rows = []
    for row in _strict_valid_tests(source_subject):
        if _identity(row) not in NEW_IDENTITIES:
            continue
        source_paths = list(_absolute_paths(row))
        if (not source_paths
                or any(not _inside(path, source_subject) for path in source_paths)):
            raise RuntimeError("selected source row has a path outside its subject")
        rebound = _rewrite(copy.deepcopy(row), str(source_subject), str(target_subject))
        target_paths = list(_absolute_paths(rebound))
        if (len(target_paths) != len(source_paths)
                or any(not _inside(path, target_subject) for path in target_paths)):
            raise RuntimeError("selected row did not rebase inside the canonical subject")
        if rebound.get("kind") == "put":
            rebound.pop("ce_anchor", None)
            rebound.pop("ce_anchor_forge_status", None)
        rows.append(rebound)
    keys = {(row.get("kind"), str(row.get("enc")), str(row.get("piece"))) for row in rows}
    expected = {("put", "6", "1"), ("concrete", "6", "1"),
                ("put", "7", "1"), ("concrete", "7", "1")}
    if keys != expected or len(rows) != 4:
        raise RuntimeError(f"source physical rows differ from fixed selector: {sorted(keys)}")
    return rows


def _append_rows(result: dict, additions: list[dict]) -> None:
    for owner_name in ("row", "put"):
        if not isinstance(result.get(owner_name), dict):
            raise RuntimeError(f"canonical result lacks {owner_name} object")
    authoritative = result["row"]
    for key in ("raw_tests", "valid_tests", "raw_artifacts", "valid_artifacts"):
        existing = [row for row in authoritative.get(key) or []
                    if isinstance(row, dict)]
        deduped = {_physical_key(row): row
                   for row in existing + copy.deepcopy(additions)}
        if len(deduped) != 7:
            raise RuntimeError(f"merged {key} does not contain 7 physical rows")
        rows = list(deduped.values())
        result["row"][key] = copy.deepcopy(rows)
        result["put"][key] = copy.deepcopy(rows)


def _update_counts(result: dict, target_subject: Path) -> None:
    rows = []
    seen = set()
    for row in (result["row"].get("valid_tests") or []):
        key = (row.get("file"), row.get("test"), row.get("kind"), row.get("unit"),
               str(row.get("enc")), str(row.get("piece") or ""))
        if key not in seen and row.get("valid_reference_test") is True:
            seen.add(key)
            rows.append(row)
    puts = [row for row in rows if row.get("kind") == "put"]
    concretes = [row for row in rows if row.get("kind") == "concrete"]
    if len(rows) != 7 or len(puts) != 2 or len(concretes) != 5:
        raise RuntimeError(
            "merged strict counts are not 7 valid / 2 PUT / 5 concrete: "
            f"valid={len(rows)} put={len(puts)} concrete={len(concretes)}")
    counts = {
        "raw": 7, "valid": 7, "put_raw": 2, "put_valid": 2,
        "concrete_raw": 5, "concrete_valid": 5,
        "valid_put_with_R1": 0, "valid_put_with_R2": 0,
        "valid_put_with_R1_or_R2": 0, "valid_put_without_R1R2": 2,
    }
    for owner_name in ("row", "put"):
        owner = result[owner_name]
        owner.update(counts)
        owner["artifact_counts"] = dict(counts)
        owner["quality_bucket"] = "valid-PUT-no-R1R2"
    frozen = [[PATH_FUNCTION, UNIT, enc, None] for enc in ("2", "6", "7")]
    new = [[PATH_FUNCTION, UNIT, enc, "1"] for enc in ("6", "7")]
    result["adoption"] = {
        "schema": "veriput-rq1-proxy-extcall-piece1-adoption/v1",
        "source": str(target_subject),
        "target": str(target_subject),
        "scope": "case-level-only-new-identity",
        "frozen_ledger_credited": False,
        "preserved_frozen_obligations": frozen,
        "new_obligations": new,
    }


def _audit_result_schema(result: dict, target_subject: Path) -> None:
    expected_counts = {
        "raw": 7, "valid": 7, "put_raw": 2, "put_valid": 2,
        "concrete_raw": 5, "concrete_valid": 5,
    }
    reference = None
    for owner_name in ("row", "put"):
        owner = result.get(owner_name) or {}
        for key in ("raw_tests", "valid_tests", "raw_artifacts", "valid_artifacts"):
            rows = owner.get(key) or []
            keys = {_physical_key(row) for row in rows if isinstance(row, dict)}
            if len(rows) != 7 or len(keys) != 7:
                raise RuntimeError(f"final {owner_name}.{key} is not 7 unique rows")
            if reference is None:
                reference = keys
            elif keys != reference:
                raise RuntimeError("final result owner arrays have different identities")
            for row in rows:
                if str(row.get("piece") or "") == "1" and any(
                        not _inside(path, target_subject)
                        for path in _absolute_paths(row)):
                    raise RuntimeError("final piece-1 row points outside canonical subject")
        for field, value in expected_counts.items():
            if owner.get(field) != value or (owner.get("artifact_counts") or {}).get(
                    field) != value:
                raise RuntimeError(f"final {owner_name} count mismatch for {field}")
    adoption = result.get("adoption") or {}
    expected_frozen = [[PATH_FUNCTION, UNIT, enc, None] for enc in ("2", "6", "7")]
    expected_new = [[PATH_FUNCTION, UNIT, enc, "1"] for enc in ("6", "7")]
    if (adoption.get("schema") != "veriput-rq1-proxy-extcall-piece1-adoption/v1"
            or adoption.get("scope") != "case-level-only-new-identity"
            or adoption.get("frozen_ledger_credited") is not False
            or adoption.get("preserved_frozen_obligations") != expected_frozen
            or adoption.get("new_obligations") != expected_new
            or adoption.get("source") != str(target_subject)
            or adoption.get("target") != str(target_subject)):
        raise RuntimeError("final case-level adoption metadata mismatch")


def _copy_inputs(source_subject: Path, target_subject: Path) -> None:
    source_put = source_subject / "put" / (
        "bugfix124__rc_access_control__proxy__SolGPT__proxy_4round__forward__pf35")
    target_put = target_subject / "put" / source_put.name
    if target_put.exists():
        raise RuntimeError("piece-1 PUT destination already exists")
    shutil.copytree(source_put, target_put)
    source_evidence = source_subject / "cert/evidence"
    if source_evidence.exists():
        for source in source_evidence.rglob("*"):
            if not source.is_file():
                continue
            target = target_subject / source.relative_to(source_subject)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix in (".json", ".jsonl"):
                content = source.read_text(encoding="utf-8")
                if source.suffix == ".json":
                    document = _rewrite(json.loads(content), str(source_subject),
                                        str(target_subject))
                    rendered = json.dumps(document, indent=2, sort_keys=True) + "\n"
                else:
                    rendered = "".join(
                        json.dumps(_rewrite(json.loads(line), str(source_subject),
                                            str(target_subject)), sort_keys=True) + "\n"
                        for line in content.splitlines() if line.strip())
                if target.exists() and target.read_text(encoding="utf-8") != rendered:
                    raise RuntimeError(f"refusing to overwrite cert evidence: {target}")
                target.write_text(rendered, encoding="utf-8")
            elif target.exists():
                if target.read_bytes() != source.read_bytes():
                    raise RuntimeError(f"refusing to overwrite cert evidence: {target}")
            else:
                shutil.copy2(source, target)
    for relative in ("cert/certify-results.jsonl", "cert/shards/001-forward.jsonl"):
        source = source_subject / relative
        if not source.is_file():
            raise RuntimeError(f"source certification file is absent: {relative}")
        target = target_subject / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        old = target.read_bytes() if target.is_file() else b""
        additions = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                additions.append(json.dumps(
                    _rewrite(json.loads(line), str(source_subject), str(target_subject)),
                    sort_keys=True).encode("utf-8") + b"\n")
        separator = b"" if not old or old.endswith(b"\n") else b"\n"
        target.write_bytes(old + separator + b"".join(additions))

    source_manifest = load_manifest(source_subject)
    target_manifest = load_manifest(target_subject)
    original_entries = list(target_manifest.get("entries") or [])
    frozen_ids = {
        (str((entry.get("origin") or {}).get("path_function") or ""),
         str((entry.get("origin") or {}).get("unit") or ""),
         str((entry.get("origin") or {}).get("enc")),
         str((entry.get("origin") or {}).get("piece") or ""))
        for entry in original_entries
    }
    expected_frozen = {(PATH_FUNCTION, UNIT, value, "") for value in ("2", "6", "7")}
    if frozen_ids != expected_frozen:
        raise RuntimeError(f"canonical frozen replay identities differ: {sorted(frozen_ids)}")
    for entry in source_manifest.get("entries") or []:
        project = str(entry.get("project") or "")
        if project:
            project_path = Path(project)
            if project_path.parts[:1] != ("concrete-replays",):
                project_path = Path("concrete-replays") / project_path
            target_project = target_subject / project_path
            if target_project.exists():
                raise RuntimeError(f"replay project destination exists: {project_path}")
            shutil.copytree(source_subject / project_path, target_project)
    additions = [_rewrite(copy.deepcopy(entry), str(source_subject), str(target_subject))
                 for entry in source_manifest.get("entries") or []]
    target_manifest.update(entries=original_entries + additions, legacy_entries=[])
    _atomic_json(target_subject / "concrete-replays" / "manifest.json", target_manifest)


def _sanitize_new_puts(target_subject: Path) -> None:
    for put_json in target_subject.glob("put/*/_wd/*p1__certify-results/put.json"):
        document = json.loads(put_json.read_text(encoding="utf-8"))
        if _identity(document) not in NEW_IDENTITIES:
            raise RuntimeError(f"unexpected copied PUT identity: {put_json}")
        source = Path(str(document.get("file") or ""))
        if not source.is_file():
            raise RuntimeError(f"relocated PUT source is absent: {source}")
        source.write_text(_remove_anchors(source.read_text(encoding="utf-8")),
                          encoding="utf-8")
        document.pop("ce_anchor", None)
        _atomic_json(put_json, document)
        evidence = put_json.parent / "ce-anchor-forge"
        if evidence.exists():
            shutil.rmtree(evidence)


def _backfill_two(result_root: Path, target_subject: Path,
                  scratch_root: Path) -> None:
    # The canonical root contains every subject (and retained scratch trees).
    # Scope this transaction to the one subject under adoption; otherwise the
    # global inventory can hide the two selected piece-1 rows among unrelated
    # PUTs and make a valid merge fail closed.
    entries = [entry for entry in backfill._deduplicated_puts(result_root)
               if Path(str(entry.get("subject_dir") or "")).resolve()
               == target_subject.resolve()]
    if {tuple(entry["identity"]) for entry in entries} != NEW_IDENTITIES:
        raise RuntimeError("canonical merge did not select exactly the two piece-1 PUTs")
    for entry in entries:
        prepared, error = backfill._prepare(entry)
        if prepared is None:
            raise RuntimeError(error)
        merged, error = backfill._embed(prepared)
        if merged is None:
            raise RuntimeError(error)
        metadata, error = backfill._finalize_metadata(prepared, merged)
        if metadata is None:
            raise RuntimeError(error)
        project = backfill._project_root(prepared["put_file"])
        validation, error = backfill._validate_in_scratch(
            prepared, merged, project, scratch_root, 256)
        if validation is None or error:
            raise RuntimeError(error or "staging double Forge validation failed")
        prepared["put_file"].write_text(merged, encoding="utf-8")
        put_json = Path(str(entry["put"]["put_json"]))
        records = {}
        for role, test in (("put", prepared["put_test"]), ("anchor", metadata["test"])):
            ok, tail, record = backfill._forge(project, prepared["put_file"], test, 256,
                                               scratch_root / "canonical-forge", False)
            if not ok:
                raise RuntimeError(f"canonical {role} Forge failed: {tail}")
            path, binding = backfill._forge_record_metadata(put_json, role, record)
            _atomic_json(path, record)
            records[role] = binding
        metadata["forge_gate"] = {
            "schema": "veriput-put-anchor-forge-gate/v1",
            "put_test": prepared["put_test"], "anchor_test": metadata["test"],
            "put_status": "Success", "anchor_status": "Success",
            "source_sha256": backfill._sha256_text(merged),
            "put_run": records["put"], "anchor_run": records["anchor"],
        }
        put_path, put_doc, result_path, result_doc, physical = backfill._metadata_documents(
            entry, metadata)
        _atomic_json(put_path, put_doc)
        _atomic_json(result_path, result_doc)
        error = backfill._headline_anchor_strength_error(entry, physical)
        if error:
            raise RuntimeError(error)


def _final_audit(root: Path, target_subject: Path, frozen_ids: set[tuple],
                 cert_preimage: dict[str, bytes]) -> dict:
    subject = target_subject
    rows = _strict_valid_tests(subject)
    generalization = annotate_generalization(subject, rows)
    manifest = load_manifest(subject)
    manifest_errors = audit_manifest(subject, manifest)
    coverage = persistence_coverage(rows, manifest.get("entries") or [], subject)
    current_frozen = {
        (str((entry.get("origin") or {}).get("path_function") or ""),
         str((entry.get("origin") or {}).get("unit") or ""),
         str((entry.get("origin") or {}).get("enc")),
         str((entry.get("origin") or {}).get("piece") or ""))
        for entry in manifest.get("entries") or []
        if str((entry.get("origin") or {}).get("piece") or "") == ""
    }
    if current_frozen != frozen_ids:
        raise RuntimeError("frozen replay identity set changed")
    strengths = []
    entries = [entry for entry in backfill._deduplicated_puts(root)
               if Path(str(entry.get("subject_dir") or "")).resolve()
               == subject.resolve()]
    for entry in entries:
        verdict = _anchor_strength_audit(entry["put"], tuple(entry["identity"]), subject)
        strengths.append({"identity": entry["identity"], "verdict": list(verdict)})
        source = Path(str(entry["put"]["file"]))
        anchors = re.findall(
            r"\bfunction\s+test_ce_anchor_", source.read_text(encoding="utf-8"))
        records = list(Path(str(entry["put"]["put_json"])).parent.glob(
            "ce-anchor-forge/*.json"))
        if verdict != (True, "strength-confirmed") or len(anchors) != 1 or len(records) != 2:
            raise RuntimeError("final anchor/Forge evidence audit failed")
    if (manifest_errors or not coverage.get("complete")
            or coverage.get("put_basis_missing_count") != 0
            or coverage.get("valid_concrete_missing_count") != 0
            or generalization.get("confirmed_generalized_to_put") != 2
            or generalization.get("not_generalized") != 3):
        raise RuntimeError("final manifest/persistence audit failed")
    result_path = subject / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    _audit_result_schema(result, subject)
    _audit_cert_preimage(subject, cert_preimage)
    result["concrete_replay_persistence"] = coverage
    result["row"]["concrete_replay_persistence"] = coverage
    _atomic_json(result_path, result)
    return {"generalization": generalization, "persistence": coverage,
            "strength": strengths, "manifest_errors": manifest_errors}


def _merge(source_root: Path, target_root: Path, scratch_root: Path) -> dict:
    source_subject = _subject(source_root)
    target_subject = _subject(target_root)
    if not source_subject.is_dir() or not target_subject.is_dir():
        raise RuntimeError("source or canonical subject is absent")
    original_manifest = load_manifest(target_subject)
    frozen_ids = {
        (str((entry.get("origin") or {}).get("path_function") or ""),
         str((entry.get("origin") or {}).get("unit") or ""),
         str((entry.get("origin") or {}).get("enc")),
         str((entry.get("origin") or {}).get("piece") or ""))
        for entry in original_manifest.get("entries") or []
    }
    additions = _new_rows(source_subject, target_subject)
    cert_preimage = _cert_preimage(target_subject)
    _copy_inputs(source_subject, target_subject)
    _rewrite_json_tree(target_subject / "put", str(source_subject), str(target_subject))
    _sanitize_new_puts(target_subject)
    result_path = target_subject / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    _append_rows(result, additions)
    _update_counts(result, target_subject)
    _atomic_json(result_path, result)
    _backfill_two(target_root, target_subject, scratch_root)
    return _final_audit(target_root, target_subject, frozen_ids, cert_preimage)


def main() -> int:
    """Run the fixed-selector dry-run or transactional canonical adoption."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--canonical-root", type=Path, default=DEFAULT_CANONICAL_ROOT)
    parser.add_argument("--scratch-root", type=Path,
                        default=Path("/tmp/rq1-proxy-extcall-adopt"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.source_root.resolve() == args.canonical_root.resolve():
        parser.error("source and canonical roots must differ")
    canonical_subject = _subject(args.canonical_root)
    if not args.apply:
        with tempfile.TemporaryDirectory(prefix="rq1-proxy-adopt-dry-") as temp:
            dry_root = Path(temp) / "VeriPUT"
            dry_subject = _subject(dry_root)
            dry_subject.parent.mkdir(parents=True)
            shutil.copytree(canonical_subject, dry_subject)
            report = _merge(args.source_root, dry_root, Path(temp) / "scratch")
            print(json.dumps({"mode": "dry-run", **report}, indent=2, sort_keys=True))
        return 0
    with backfill._transaction_lock(canonical_subject / "result.json"):
        backup_root = Path(tempfile.mkdtemp(prefix="rq1-proxy-adopt-backup-"))
        backup_subject = backup_root / SUBJECT
        shutil.copytree(canonical_subject, backup_subject)
        try:
            report = _merge(args.source_root, args.canonical_root, args.scratch_root)
        except Exception:
            failed = canonical_subject.with_name(
                canonical_subject.name + ".failed_proxy_adopt")
            if failed.exists():
                shutil.rmtree(failed)
            canonical_subject.rename(failed)
            backup_subject.rename(canonical_subject)
            shutil.rmtree(failed)
            shutil.rmtree(backup_root, ignore_errors=True)
            raise
        shutil.rmtree(backup_root, ignore_errors=True)
    print(json.dumps({"mode": "apply", **report}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
