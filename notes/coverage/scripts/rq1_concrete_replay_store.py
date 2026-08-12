#!/usr/bin/env python3
"""Persist RQ1 concrete replays as self-contained canonical Forge projects.

The Stage-4 working tree is provenance, not durable replay storage: old rows
can name a temporary directory and a later adoption can move the producing
tree.  This module copies a green concrete artifact, its exact flat source,
Foundry configuration and forge-std into the canonical subject directory.
The manifest contains only subject-relative executable paths plus hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path


SCHEMA = "veriput-rq1-concrete-replay-manifest/v1"
STORE_DIR = "concrete-replays"
MANIFEST_NAME = "manifest.json"
DEFAULT_INVALIDATION_LEDGER = Path(__file__).resolve().parents[1] / \
    "rq1_tuple_frontend_pollution_audit.json"


class ReplayPersistenceError(ValueError):
    """A claimed replay cannot be persisted without weakening its evidence."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or ""))
    return text.strip("_") or "unnamed"


def _atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            "w", delete=False, dir=path.parent, encoding="utf-8") as stream:
        json.dump(doc, stream, indent=2, sort_keys=True)
        stream.write("\n")
        tmp = Path(stream.name)
    os.replace(tmp, path)


def _foundry_project(test_file: Path) -> Path:
    for parent in (test_file.parent, *test_file.parents):
        if (parent / "foundry.toml").is_file() and (parent / "src").is_dir():
            return parent
    raise ReplayPersistenceError(f"no Foundry project owns {test_file}")


def _copy_file(source: Path, destination: Path) -> None:
    """Copy bytes and metadata without preserving a source inode link."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    shutil.copystat(source, destination, follow_symlinks=True)


def _privatize_tree(root: Path) -> None:
    """Break legacy hard links so the canonical project owns every byte."""
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink <= 1:
            continue
        with tempfile.NamedTemporaryFile(delete=False, dir=path.parent) as stream:
            replacement = Path(stream.name)
        try:
            _copy_file(path, replacement)
            os.replace(replacement, path)
        finally:
            replacement.unlink(missing_ok=True)


def _copy_tree(source: Path, destination: Path) -> None:
    source = source.resolve()
    if not source.is_dir():
        raise ReplayPersistenceError(f"missing replay dependency tree: {source}")
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_symlink():
            resolved = item.resolve()
            if resolved.is_dir():
                _copy_tree(resolved, target)
            elif resolved.is_file():
                _copy_file(resolved, target)
        elif item.is_file():
            _copy_file(item, target)


def _load_record(path: object) -> dict:
    if not path:
        return {}
    try:
        doc = json.loads(Path(str(path)).read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def replay_identity(row: dict) -> dict:
    record = _load_record(row.get("put_json"))
    return {
        "unit": row.get("unit") or record.get("unit"),
        "enc": row.get("enc") if row.get("enc") is not None else record.get("enc"),
        "piece": row.get("piece") if row.get("piece") is not None else record.get("piece"),
        "path_function": record.get("path_function"),
        "stage2_source": row.get("stage2_source") or record.get("stage2_source"),
        "stage2_witness_check": (row.get("stage2_witness_check") or
                                 record.get("stage2_witness_check")),
    }


def _relative_provenance(subject_dir: Path, path: object) -> dict:
    """Record a durable relative path, or only a hash for an external source."""
    if not path:
        return {"path_class": "missing"}
    candidate = Path(str(path)).expanduser().resolve()
    record = {
        "path_class": "external",
        "sha256": _sha256(candidate) if candidate.is_file() else None,
    }
    try:
        record["path"] = candidate.relative_to(subject_dir.resolve()).as_posix()
    except ValueError:
        return record
    record["path_class"] = "canonical-subject"
    return record


def _artifact_key(row: dict) -> tuple:
    identity = replay_identity(row)
    return (str(identity.get("path_function") or ""),
            str(identity.get("unit") or ""),
            str(identity.get("enc") if identity.get("enc") is not None else ""),
            str(identity.get("piece") if identity.get("piece") is not None else ""))


def _entry_project(subject_dir: Path, entry: dict) -> Path:
    relative = Path(str(entry.get("project") or ""))
    project = (subject_dir / relative).resolve()
    root = (subject_dir / STORE_DIR).resolve()
    try:
        project.relative_to(root)
    except ValueError as exc:
        raise ReplayPersistenceError(f"manifest project escapes replay store: {relative}") from exc
    return project


def load_manifest(subject_dir: Path) -> dict:
    path = subject_dir / STORE_DIR / MANIFEST_NAME
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        doc = {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        doc = {"schema": SCHEMA, "entries": []}
    if not isinstance(doc.get("entries"), list):
        doc["entries"] = []
    doc.pop("subject_dir", None)
    for entry in doc["entries"]:
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin")
        if not isinstance(origin, dict):
            continue
        for field in ("test_file", "put_json"):
            value = origin.get(field)
            if isinstance(value, str):
                origin[field] = _relative_provenance(subject_dir, value)
            elif isinstance(value, dict) and value.get("path_class") == "external":
                value.pop("path", None)
    return doc


def invalidated_cases(path: Path = DEFAULT_INVALIDATION_LEDGER) -> set[str]:
    """Canonical cases whose apparent validity is explicitly revoked."""
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return set()
    audit = doc.get("error_then_success_evidence_audit") or {}
    return {str(case) for case in audit.get("affected_cases") or []}


def invalidation_applies(case: str, tests: list[dict],
                         path: Path = DEFAULT_INVALIDATION_LEDGER) -> bool:
    """Whether current evidence still predates the pollution quarantine."""
    if case not in invalidated_cases(path):
        return False
    try:
        ledger_mtime = path.stat().st_mtime
    except OSError:
        return True
    retained_mtimes = []
    for test in tests:
        try:
            retained_mtimes.append(Path(str(test.get("file") or "")).stat().st_mtime)
        except OSError:
            continue
    # A repaired canonical rerun writes a fresh test after the frozen audit.
    # Merely rewriting result.json is deliberately insufficient.
    return not retained_mtimes or max(retained_mtimes) <= ledger_mtime


def audit_manifest(subject_dir: Path, manifest: dict | None = None) -> list[str]:
    manifest = manifest or load_manifest(subject_dir)
    errors = []
    for entry in manifest.get("entries") or []:
        try:
            project = _entry_project(subject_dir, entry)
        except ReplayPersistenceError as exc:
            errors.append(str(exc))
            continue
        test_file = project / str(entry.get("test_file") or "")
        flat_file = project / str(entry.get("flat_source") or "")
        for label, path, expected in (
                ("test", test_file, entry.get("test_sha256")),
                ("flat source", flat_file, entry.get("flat_sha256"))):
            if not path.is_file():
                errors.append(f"{entry.get('replay_id')}: missing {label} {path}")
            elif expected != _sha256(path):
                errors.append(f"{entry.get('replay_id')}: {label} hash mismatch")
            elif path.stat().st_nlink > 1:
                errors.append(f"{entry.get('replay_id')}: {label} is hard-linked")
        if not (project / "foundry.toml").is_file():
            errors.append(f"{entry.get('replay_id')}: missing foundry.toml")
        if not (project / "lib" / "forge-std" / "src" / "Test.sol").is_file():
            errors.append(f"{entry.get('replay_id')}: forge-std is not vendored")
        linked = [path.relative_to(project).as_posix() for path in project.rglob("*")
                  if path.is_file() and not path.is_symlink()
                  and path.stat().st_nlink > 1]
        if linked:
            errors.append(f"{entry.get('replay_id')}: hard-linked dependency {linked[0]}")
    return errors


def repair_manifest_independence(subject_dir: Path, manifest: dict | None = None) -> list[str]:
    """Repair legacy inode links and make every replay command exact."""
    manifest = manifest or load_manifest(subject_dir)
    changed = False
    for entry in manifest.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        try:
            project = _entry_project(subject_dir, entry)
        except ReplayPersistenceError:
            continue
        if project.is_dir():
            _privatize_tree(project)
        command = ["forge", "test", "--match-test",
                   f"^{re.escape(str(entry.get('test') or ''))}\\(",
                   "--match-path", str(entry.get("test_file") or "")]
        if entry.get("forge_command") != command:
            entry["forge_command"] = command
            changed = True
    errors = audit_manifest(subject_dir, manifest)
    if changed and not errors:
        manifest["updated_at"] = time.time()
        _atomic_json(subject_dir / STORE_DIR / MANIFEST_NAME, manifest)
    return errors


def persist_concrete_replay(subject_dir: Path, row: dict, *, dry_run: bool = False) -> dict:
    """Atomically adopt one already-green concrete artifact."""
    if row.get("kind") != "concrete" or row.get("valid_reference_test") is not True:
        raise ReplayPersistenceError("only a valid concrete reference test may be persisted")
    source_test = Path(str(row.get("file") or "")).expanduser().resolve()
    if not source_test.is_file():
        raise ReplayPersistenceError(f"concrete test is not retained: {source_test}")
    source_project = _foundry_project(source_test)
    try:
        test_relative = source_test.relative_to(source_project)
    except ValueError as exc:
        raise ReplayPersistenceError("test is outside its Foundry project") from exc
    flat_source = source_project / "src" / "flat.sol"
    if not flat_source.is_file():
        raise ReplayPersistenceError(f"Foundry project has no src/flat.sol: {source_project}")
    identity = replay_identity(row)
    digest_seed = json.dumps({
        "identity": identity,
        "test": row.get("test"),
        "test_sha256": _sha256(source_test),
        "flat_sha256": _sha256(flat_source),
    }, sort_keys=True).encode()
    replay_id = (_safe_name(identity.get("unit")) + "-" +
                 hashlib.sha256(digest_seed).hexdigest()[:16])
    relative_project = Path(STORE_DIR) / "projects" / replay_id
    entry = {
        "schema": "veriput-rq1-concrete-replay/v1",
        "replay_id": replay_id,
        "project": relative_project.as_posix(),
        "test_file": test_relative.as_posix(),
        "test": row.get("test"),
        "test_sha256": _sha256(source_test),
        "flat_source": "src/flat.sol",
        "flat_sha256": _sha256(flat_source),
        "forge_command": ["forge", "test", "--match-test",
                          f"^{re.escape(str(row.get('test') or ''))}\\(",
                          "--match-path", test_relative.as_posix()],
        "forge_status": row.get("forge_status"),
        "valid_reference_test": True,
        "origin": {
            **identity,
            "test_file": _relative_provenance(subject_dir, source_test),
            "put_json": _relative_provenance(subject_dir, row.get("put_json")),
        },
    }
    if dry_run:
        entry["action"] = "already-present" if (
            subject_dir / relative_project).is_dir() else "persist"
        return entry

    store = subject_dir / STORE_DIR
    destination = subject_dir / relative_project
    if not destination.is_dir():
        store.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=".replay-stage-", dir=store))
        staging = staging_root / replay_id
        try:
            (staging / "src").mkdir(parents=True)
            (staging / "test").mkdir(parents=True)
            _copy_file(source_project / "foundry.toml", staging / "foundry.toml")
            _copy_file(flat_source, staging / "src" / "flat.sol")
            _copy_file(source_test, staging / test_relative)
            forge_std = (source_project / "lib" / "forge-std").resolve()
            _copy_tree(forge_std, staging / "lib" / "forge-std")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
    _privatize_tree(destination)

    manifest = load_manifest(subject_dir)
    entries = {item.get("replay_id"): item for item in manifest["entries"]
               if isinstance(item, dict) and item.get("replay_id")}
    entry["persisted_at"] = time.time()
    entries[replay_id] = entry
    manifest.update({
        "schema": SCHEMA,
        "updated_at": time.time(),
        "entries": [entries[key] for key in sorted(entries)],
    })
    errors = audit_manifest(subject_dir, manifest)
    if errors:
        raise ReplayPersistenceError("; ".join(errors))
    _atomic_json(store / MANIFEST_NAME, manifest)
    return entry


def persistence_coverage(valid_tests: list[dict], entries: list[dict]) -> dict:
    """Report case coverage and exact PUT-to-concrete provenance gaps."""
    valid = [row for row in valid_tests if isinstance(row, dict)
             and row.get("valid_reference_test") is True]
    puts = [row for row in valid if row.get("kind") == "put"]
    concretes = [row for row in valid if row.get("kind") == "concrete"]
    concrete_keys = {_artifact_key(row) for row in concretes}
    for entry in entries:
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if isinstance(origin, dict):
            concrete_keys.add((str(origin.get("path_function") or ""),
                               str(origin.get("unit") or ""),
                               str(origin.get("enc") if origin.get("enc") is not None else ""),
                               str(origin.get("piece") if origin.get("piece") is not None else "")))
    missing_puts = []
    for row in puts:
        key = _artifact_key(row)
        if key not in concrete_keys:
            missing_puts.append({
                **replay_identity(row),
                "test": row.get("test"),
                "put_json": row.get("put_json"),
            })
    return {
        "schema": "veriput-rq1-concrete-replay-coverage/v1",
        "strict_valid": bool(valid),
        "canonical_replay_count": len(entries),
        "case_replay_persisted": bool(entries),
        "valid_put_count": len(puts),
        "valid_concrete_count": len(concretes),
        "put_basis_missing_count": len(missing_puts),
        "put_basis_missing": missing_puts,
        "complete": bool(valid) and bool(entries) and not missing_puts,
    }
