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
import subprocess
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
        "path_function": row.get("path_function") or record.get("path_function"),
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


def _concrete_test_key(row: dict) -> tuple:
    """Identify one concrete test, not merely its path or Solidity name."""
    test_file = Path(str(row.get("file") or ""))
    flat_file = test_file.parent.parent / "src" / "flat.sol"
    return (_artifact_key(row), str(row.get("test") or ""),
            _sha256(test_file) if test_file.is_file() else None,
            _sha256(flat_file) if flat_file.is_file() else None)


def _entry_test_key(entry: dict) -> tuple:
    origin = entry.get("origin") if isinstance(entry, dict) else {}
    origin = origin if isinstance(origin, dict) else {}
    key = (str(origin.get("path_function") or ""), str(origin.get("unit") or ""),
           str(origin.get("enc") if origin.get("enc") is not None else ""),
           str(origin.get("piece") if origin.get("piece") is not None else ""))
    return (key, str(entry.get("test") or ""), entry.get("test_sha256"),
            entry.get("flat_sha256"))


def _solidity_function(source: str, name: str) -> tuple[str, str] | None:
    """Return one generated test's parameter list and body."""
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", source)
    if match is None:
        return None
    start = match.end()
    depth = 1
    index = start
    while index < len(source) and depth:
        depth += (source[index] == "(") - (source[index] == ")")
        index += 1
    if depth:
        return None
    params = source[start:index - 1]
    body_start = source.find("{", index)
    if body_start < 0:
        return None
    depth = 1
    body_end = body_start + 1
    while body_end < len(source) and depth:
        depth += (source[body_end] == "{") - (source[body_end] == "}")
        body_end += 1
    if depth:
        return None
    return params, source[body_start + 1:body_end - 1]


def deterministic_replay_oracles(test_file: Path, test: str, unit: str) -> tuple[list[dict], list[str]]:
    """Conservatively identify assertions tied to the target invocation."""
    try:
        source = test_file.read_text(errors="replace")
    except OSError as exc:
        return [], [f"cannot read replay test: {exc}"]
    function = _solidity_function(source, test)
    if function is None:
        return [], [f"replay function is absent or malformed: {test}"]
    params, body = function
    errors = []
    if params.strip():
        errors.append("replay function has Forge fuzz parameters")
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    assertion = re.search(r"\bassert(?:Eq|True|False|Gt|Ge|Lt|Le)?\s*\(", code)
    expect_revert = re.search(r"\bvm\s*\.\s*expectRevert\s*\(", code)
    oracles = []
    if re.search(r"\bassertTrue\s*\(\s*true\s*(?:,|\))", code):
        errors.append("replay uses a tautological assertion instead of an execution result")
    if unit == "__deploy__":
        invoked = re.search(r"\bnew\s+[A-Za-z_$][A-Za-z0-9_$.]*\s*\(", code)
    elif unit in ("fallback", "receive"):
        invoked = re.search(r"\.\s*(?:call|send|transfer)\s*(?:\{|\()", code)
    else:
        invoked = re.search(r"\.\s*" + re.escape(unit) + r"\s*\(", code)
        if invoked is None:
            invoked = re.search(
                r"abi\s*\.\s*encode(?:Call|WithSignature|WithSelector)\s*\([^;]*\b" +
                re.escape(unit) + r"\b", code, re.S)
    if invoked is None:
        errors.append(f"replay does not invoke target unit {unit}")
    if invoked is not None:
        statement_start = code.rfind(";", 0, invoked.start()) + 1
        statement_end = code.find(";", invoked.end())
        statement_end = len(code) if statement_end < 0 else statement_end + 1
        statement = code[statement_start:statement_end]
        receiver = re.search(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*" +
                             re.escape(unit) + r"\s*\(", statement)
        assigned = set(re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b\s*(?:,|\))?\s*=",
                                  statement[:max(0, invoked.start() - statement_start)]))
        low_level = re.search(r"\b(bool\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:,|\))?\s*=.*\.call",
                              statement, re.S)
        if low_level:
            assigned.add(low_level.group(2))
        prefix = code[:statement_start]
        armed = re.search(r"vm\s*\.\s*expectRevert\s*\([^;]*\)\s*;\s*$", prefix, re.S)
        if armed:
            oracles.append({"class": "R0", "kind": "revert", "source": "expectRevert"})
        elif expect_revert is not None:
            errors.append("revert oracle is not immediately before the target call")
        suffix = code[statement_end:]
        if (re.search(r"\bbool\s+_veriput_concrete_completed\s*=\s*false\s*;",
                      code[:statement_start]) and
                re.search(r"^\s*_veriput_concrete_completed\s*=\s*true\s*;\s*"
                          r"assertTrue\s*\(\s*_veriput_concrete_completed\b",
                          suffix)):
            oracles.append({"class": "R0", "kind": "normal-exit",
                            "source": "generated-completion-marker"})
        fixed_names = set(re.findall(
            r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:true|false|"
            r"0x[0-9A-Fa-f]+|[0-9]+|bytes\d*\s*\([^;]+\))\s*;", code[:statement_start]))
        for match in re.finditer(
                r"\b(assert(?:Eq|True|False|Gt|Ge|Lt|Le)?)\s*\((.*?)\)\s*;",
                suffix, re.S):
            expression = match.group(2)
            observes_return = any(re.search(r"\b" + re.escape(name) + r"\b", expression)
                                  for name in assigned)
            observes_state = bool(receiver and re.search(
                r"\b" + re.escape(receiver.group(1)) +
                r"\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\(", expression))
            fixed_expected = bool(re.search(
                r"(?:^|[,=!<>\s(])(?:true|false|0x[0-9A-Fa-f]+|[0-9]+|"
                r"bytes\d*\s*\([^)]*\))(?=$|[,=!<>\s)])", expression))
            fixed_expected = fixed_expected or any(
                re.search(r"\b" + re.escape(name) + r"\b", expression)
                for name in fixed_names)
            boolean_status = (match.group(1) in ("assertTrue", "assertFalse")
                              and observes_return)
            if observes_return and (fixed_expected or boolean_status):
                oracles.append({"class": "R0", "kind": "return-or-call-status",
                                "source": match.group(1)})
            elif observes_state and fixed_expected:
                oracles.append({"class": "concrete-value", "kind": "post-state",
                                "source": match.group(1)})
    if assertion is None and expect_revert is None:
        errors.append("replay has no execution-result assertion or revert oracle")
    if not oracles:
        errors.append("replay assertions are not data-dependent on the target result")
    return oracles, errors


def deterministic_replay_errors(test_file: Path, test: str, unit: str) -> list[str]:
    return deterministic_replay_oracles(test_file, test, unit)[1]


def _structured_oracle_errors(oracles: object) -> list[str]:
    if not isinstance(oracles, list) or not oracles:
        return ["concrete replay lacks structured witness oracle provenance"]
    errors = []
    for index, oracle in enumerate(oracles):
        if not isinstance(oracle, dict):
            errors.append(f"concrete oracle {index} is not an object")
            continue
        if oracle.get("class") not in ("R0", "concrete-value"):
            errors.append(f"concrete oracle {index} has no supported class")
        if not oracle.get("kind") or not oracle.get("observed"):
            errors.append(f"concrete oracle {index} lacks its observed boundary value")
        if not oracle.get("target_receiver") or not oracle.get("assertion"):
            errors.append(f"concrete oracle {index} lacks its target-bound assertion")
        if oracle.get("kind") != "normal-exit" and oracle.get("expected") is None:
            errors.append(f"concrete oracle {index} lacks its fixed witness expectation")
        if oracle.get("provenance") not in ("stage2-witness", "source-grounded"):
            errors.append(f"concrete oracle {index} lacks witness/source provenance")
    return errors


def _oracle_binding_errors(source: str, test: str, unit: str, oracles: object) -> list[str]:
    function = _solidity_function(source, test)
    if function is None or not isinstance(oracles, list):
        return ["cannot bind concrete oracle provenance to the selected test"]
    _params, body = function
    compact_body = re.sub(r"\s+", "", body)
    errors = []
    for index, oracle in enumerate(oracles):
        if not isinstance(oracle, dict):
            continue
        assertion = re.sub(r"\s+", "", str(oracle.get("assertion") or ""))
        receiver = str(oracle.get("target_receiver") or "")
        if assertion and assertion not in compact_body:
            errors.append(f"concrete oracle {index} assertion is absent from selected test")
            continue
        if receiver and not re.search(
                r"\b" + re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) +
                r"\s*\(", body):
            errors.append(f"concrete oracle {index} is not bound to selected target call")
        if oracle.get("kind") == "normal-exit":
            observed = re.sub(r"\s+", "", str(oracle.get("observed") or ""))
            call_pos = compact_body.find(re.sub(r"\s+", "", f"{receiver}.{unit}("))
            call_end = compact_body.find(";", call_pos) if call_pos >= 0 else -1
            assertion_pos = compact_body.find(assertion)
            initialization = f"bool{observed}=false;"
            completion = f"{observed}=true;"
            if (compact_body.count(initialization) != 1
                    or compact_body.count(completion) != 1
                    or call_end < 0 or assertion_pos < 0
                    or compact_body[call_end + 1:assertion_pos] != completion
                    or not assertion.startswith(f"assertTrue({observed},")):
                errors.append(
                    f"concrete oracle {index} is not the strict normal-exit marker shape")
        elif oracle.get("kind") != "revert":
            observed = re.sub(r"\s+", "", str(oracle.get("observed") or ""))
            expected = re.sub(r"\s+", "", str(oracle.get("expected") or ""))
            if observed not in assertion or expected not in assertion:
                errors.append(
                    f"concrete oracle {index} assertion does not encode observed/expected values")
            call_pos = compact_body.find(re.sub(r"\s+", "", f"{receiver}.{unit}("))
            assertion_pos = compact_body.find(assertion)
            between = compact_body[call_pos:assertion_pos] if (
                call_pos >= 0 and assertion_pos > call_pos) else ""
            if re.search(r"(?:^|;)" + re.escape(observed) + r"=", between):
                errors.append(f"concrete oracle {index} observed value is overwritten after call")
    return errors


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
        if test_file.is_file():
            replay_errors = deterministic_replay_errors(
                test_file, str(entry.get("test") or ""),
                str((entry.get("origin") or {}).get("unit") or ""))
            errors.extend(f"{entry.get('replay_id')}: {error}"
                          for error in replay_errors)
        errors.extend(f"{entry.get('replay_id')}: {error}"
                      for error in _structured_oracle_errors(entry.get("concrete_oracles")))
        if test_file.is_file():
            errors.extend(f"{entry.get('replay_id')}: {error}" for error in
                          _oracle_binding_errors(
                              test_file.read_text(errors="replace"),
                              str(entry.get("test") or ""),
                              str((entry.get("origin") or {}).get("unit") or ""),
                              entry.get("concrete_oracles")))
        replay_log = project / str(entry.get("forge_log") or "")
        if int(entry.get("forge_passed_tests") or 0) < 1:
            errors.append(f"{entry.get('replay_id')}: no executed Forge replay test")
        elif not replay_log.is_file():
            errors.append(f"{entry.get('replay_id')}: missing Forge replay log")
        elif entry.get("forge_log_sha256") != _sha256(replay_log):
            errors.append(f"{entry.get('replay_id')}: Forge replay log hash mismatch")
        if entry.get("generalization_status") not in (
                "generalized-to-put", "not-generalized"):
            errors.append(f"{entry.get('replay_id')}: missing generalization classification")
        if not entry.get("concrete_oracles"):
            errors.append(f"{entry.get('replay_id')}: missing concrete execution oracle metadata")
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


def persist_concrete_replay(subject_dir: Path, row: dict, *, dry_run: bool = False,
                            forge_timeout: int = 20) -> dict:
    """Atomically adopt one already-green concrete artifact."""
    if row.get("kind") != "concrete" or row.get("valid_reference_test") is not True:
        raise ReplayPersistenceError("only a valid concrete reference test may be persisted")
    source_test = Path(str(row.get("file") or "")).expanduser().resolve()
    if not source_test.is_file():
        raise ReplayPersistenceError(f"concrete test is not retained: {source_test}")
    identity = replay_identity(row)
    if not identity.get("unit"):
        raise ReplayPersistenceError("concrete replay identity has no unit")
    if identity.get("stage2_source") != "source-grounded-manual-concrete-replay" and (
            not identity.get("path_function") or identity.get("enc") is None):
        raise ReplayPersistenceError(
            "verifier-derived concrete replay lacks exact path_function/enc identity")
    _detected_oracles, replay_errors = deterministic_replay_oracles(
        source_test, str(row.get("test") or ""), str(identity.get("unit") or ""))
    record = _load_record(row.get("put_json"))
    replay_oracles = row.get("concrete_oracles") or record.get("concrete_oracles")
    replay_errors.extend(_structured_oracle_errors(replay_oracles))
    replay_errors.extend(_oracle_binding_errors(
        source_test.read_text(errors="replace"), str(row.get("test") or ""),
        str(identity.get("unit") or ""), replay_oracles))
    if replay_errors:
        raise ReplayPersistenceError("; ".join(replay_errors))
    source_project = _foundry_project(source_test)
    try:
        test_relative = source_test.relative_to(source_project)
    except ValueError as exc:
        raise ReplayPersistenceError("test is outside its Foundry project") from exc
    flat_source = source_project / "src" / "flat.sol"
    if not flat_source.is_file():
        raise ReplayPersistenceError(f"Foundry project has no src/flat.sol: {source_project}")
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
        "generalization_status": "not-generalized",
        "matching_put_tests": [],
        "concrete_oracles": replay_oracles,
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

    try:
        completed = subprocess.run(
            entry["forge_command"], cwd=destination, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=forge_timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ReplayPersistenceError(
            f"canonical concrete replay timed out after {forge_timeout}s") from exc
    output = completed.stdout or ""
    passed = max((int(count) for count in re.findall(r"\b(\d+) passed\b", output)),
                 default=0)
    if completed.returncode != 0 or passed < 1 or "No tests found" in output:
        raise ReplayPersistenceError(
            "canonical concrete replay did not execute a passing test: " +
            output[-1000:])
    replay_log = destination / "forge-replay.log"
    replay_log.write_text(output)
    entry.update({
        "forge_status": "Success",
        "forge_passed_tests": passed,
        "forge_log": replay_log.relative_to(destination).as_posix(),
        "forge_log_sha256": _sha256(replay_log),
        "forge_verified_at": time.time(),
    })

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
    concrete_keys = {_artifact_key(row) for row in concretes
                     if replay_identity(row).get("path_function")
                     and replay_identity(row).get("enc") is not None}
    persisted_concrete_tests = set()
    for entry in entries:
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if isinstance(origin, dict):
            key = (str(origin.get("path_function") or ""),
                   str(origin.get("unit") or ""),
                   str(origin.get("enc") if origin.get("enc") is not None else ""),
                   str(origin.get("piece") if origin.get("piece") is not None else ""))
            if origin.get("path_function") and origin.get("enc") is not None:
                concrete_keys.add(key)
            persisted_concrete_tests.add(_entry_test_key(entry))
    missing_puts = []
    for row in puts:
        key = _artifact_key(row)
        identity = replay_identity(row)
        if (not identity.get("path_function") or identity.get("enc") is None
                or key not in concrete_keys):
            missing_puts.append({
                **replay_identity(row),
                "test": row.get("test"),
                "put_json": row.get("put_json"),
            })
    missing_concretes = []
    for row in concretes:
        identity = _concrete_test_key(row)
        if identity not in persisted_concrete_tests:
            missing_concretes.append({
                **replay_identity(row),
                "test": row.get("test"),
                "file": row.get("file"),
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
        "valid_concrete_missing_count": len(missing_concretes),
        "valid_concrete_missing": missing_concretes,
        "complete": (bool(valid) and bool(entries) and not missing_puts
                     and not missing_concretes),
    }


def annotate_generalization(subject_dir: Path, valid_tests: list[dict]) -> dict:
    """Classify every retained concrete replay against exact valid PUT identities."""
    put_tests = {}
    for row in valid_tests:
        if (not isinstance(row, dict) or row.get("kind") != "put"
                or row.get("valid_reference_test") is not True):
            continue
        identity = replay_identity(row)
        if not identity.get("path_function") or identity.get("enc") is None:
            continue
        put_tests.setdefault(_artifact_key(row), []).append(str(row.get("test") or ""))
    manifest = load_manifest(subject_dir)
    counts = {"generalized-to-put": 0, "not-generalized": 0}
    for entry in manifest.get("entries") or []:
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if not isinstance(origin, dict):
            continue
        key = (str(origin.get("path_function") or ""),
               str(origin.get("unit") or ""),
               str(origin.get("enc") if origin.get("enc") is not None else ""),
               str(origin.get("piece") if origin.get("piece") is not None else ""))
        exact_identity = bool(origin.get("path_function") and origin.get("enc") is not None)
        matching = sorted(set(put_tests.get(key) or [])) if exact_identity else []
        status = "generalized-to-put" if matching else "not-generalized"
        entry["generalization_status"] = status
        entry["matching_put_tests"] = matching
        counts[status] += 1
    manifest["generalization"] = {
        "schema": "veriput-rq1-concrete-generalization/v1",
        "generalized_to_put": counts["generalized-to-put"],
        "not_generalized": counts["not-generalized"],
    }
    manifest["updated_at"] = time.time()
    errors = audit_manifest(subject_dir, manifest)
    if errors:
        raise ReplayPersistenceError("; ".join(errors))
    _atomic_json(subject_dir / STORE_DIR / MANIFEST_NAME, manifest)
    return manifest["generalization"]
