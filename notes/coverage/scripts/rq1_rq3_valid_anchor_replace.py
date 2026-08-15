#!/usr/bin/env python3
"""Freeze valid RQ3 concrete tests and replace generated RQ1 anchors exactly.

The replacement command never reads an RQ3 result tree.  Its only candidate
input is a sealed snapshot produced by ``freeze`` from the three published RQ3
shards.  Canonical writes additionally require the snapshot to prove
``raw_count == valid_count``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

PUBLISHED_SHARDS = ("peer182", "bugfix124", "real203")
GENERATED_ANCHOR = re.compile(r"\bfunction\s+(test_ce_anchor_(?:auto|rq3)_[A-Za-z0-9_$]*)\s*\(")
ANY_ANCHOR = re.compile(r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_$]*)\s*\(")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def key_set_digest(keys: Iterable[tuple[str, str]]) -> str:
    return digest_bytes(canonical_json(sorted(keys)))


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def identity(case: Any,
             path_function: Any,
             unit: Any,
             enc: Any,
             piece: Any = None) -> tuple[str, str, str, str, str]:
    return tuple("" if value is None else str(value)
                 for value in (case, path_function, unit, enc, piece))


def artifact_values(result: dict[str, Any], prefix: str) -> Iterable[dict[str, Any]]:
    """Read only the named raw/valid sets, never generic artifact sets."""
    seen: set[tuple[str, str]] = set()
    for container_name in ("row", "put"):
        container = result.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in (f"{prefix}_artifacts", f"{prefix}_tests"):
            values = container.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, dict):
                    continue
                marker = (str(value.get("file") or ""), str(value.get("test") or ""))
                if marker in seen:
                    continue
                seen.add(marker)
                yield value


def concrete(value: dict[str, Any]) -> bool:
    return bool(value.get("is_concrete") or value.get("kind")
                == "concrete") and not bool(value.get("is_put") or value.get("kind") == "put")


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _code_mask(source: str) -> str:
    """Blank comments and strings while retaining offsets and braces."""
    output = list(source)
    index = 0
    state = "code"
    quote = ""
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code" and char == "/" and following == "/":
            output[index] = output[index + 1] = " "
            index += 2
            state = "line"
            continue
        if state == "code" and char == "/" and following == "*":
            output[index] = output[index + 1] = " "
            index += 2
            state = "block"
            continue
        if state == "code" and char in ('"', "'"):
            quote = char
            output[index] = " "
            index += 1
            state = "string"
            continue
        if state == "line":
            if char == "\n":
                state = "code"
            else:
                output[index] = " "
            index += 1
            continue
        if state == "block":
            output[index] = "\n" if char == "\n" else " "
            if char == "*" and following == "/":
                output[index + 1] = " "
                index += 2
                state = "code"
            else:
                index += 1
            continue
        if state == "string":
            output[index] = "\n" if char == "\n" else " "
            if char == "\\" and following:
                output[index + 1] = " "
                index += 2
            elif char == quote:
                index += 1
                state = "code"
            else:
                index += 1
            continue
        index += 1
    return "".join(output)


def function_span(source: str, name: str) -> tuple[int, int] | None:
    masked = _code_mask(source)
    matches = list(re.finditer(r"\bfunction\s+" + re.escape(name) + r"\s*\(", masked))
    if len(matches) != 1:
        return None
    start = matches[0].start()
    opening = masked.find("{", matches[0].end())
    semicolon = masked.find(";", matches[0].end())
    if opening < 0 or (semicolon >= 0 and semicolon < opening):
        return None
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
    return None


def zero_parameter_function(source: str, name: str) -> str | None:
    span = function_span(source, name)
    if span is None:
        return None
    function = source[span[0]:span[1]]
    signature = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(([^)]*)\)",
                          _code_mask(function))
    if signature is None or signature.group(1).strip():
        return None
    return function


def freeze_snapshot(rq3_root: Path, expected_valid: int) -> dict[str, Any]:
    valid: dict[tuple[str, str], dict[str, Any]] = {}
    raw: set[tuple[str, str]] = set()
    result_paths: list[Path] = []
    for shard in PUBLISHED_SHARDS:
        for result_path in sorted((rq3_root / shard / "subjects").glob("*/result.json")):
            result_paths.append(result_path)
            result = read_object(result_path)
            for value in artifact_values(result, "raw"):
                if concrete(value):
                    raw_file = str(value.get("file") or "")
                    raw_test = str(value.get("test") or "")
                    if not raw_file or not raw_test:
                        raise ValueError(f"raw concrete test lacks file/test: {result_path}")
                    raw.add((str(Path(raw_file).resolve()), raw_test))
            case = f"{shard}/{result_path.parent.name}"
            subject_root = result_path.parent.resolve()
            for value in artifact_values(result, "valid"):
                if not concrete(value):
                    continue
                if value.get("forge_status") != "Success" or value.get(
                        "valid_reference_test") is not True:
                    raise ValueError(f"non-green entry in valid set: {result_path}")
                source = Path(str(value.get("file") or "")).resolve()
                put_path = Path(str(value.get("put_json") or "")).resolve()
                test = str(value.get("test") or "")
                if not source.is_file() or not put_path.is_file():
                    raise ValueError(f"valid evidence file missing: {source} / {put_path}")
                if not inside(source, subject_root) or not inside(put_path, subject_root):
                    raise ValueError(f"valid evidence escapes published subject: {source}")
                oracles = value.get("concrete_oracles")
                if not isinstance(oracles, list) or not oracles:
                    raise ValueError(f"valid concrete test has no oracle: {source}:{test}")
                source_text = source.read_text(encoding="utf-8")
                function = zero_parameter_function(source_text, test)
                if function is None:
                    raise ValueError(f"not exactly one zero-parameter test: {source}:{test}")
                put = read_object(put_path)
                if put.get("kind") != "concrete":
                    raise ValueError(f"valid artifact references non-concrete put.json: {put_path}")
                if (Path(str(put.get("file") or "")).resolve() != source
                        or str(put.get("test") or "") != test):
                    raise ValueError(f"artifact/put.json file or test mismatch: {put_path}")
                for field in ("unit", "enc", "piece", "path_function"):
                    if (value.get(field) is not None and str(value[field]) != str(put.get(field))):
                        raise ValueError(f"artifact/put.json {field} mismatch: {put_path}")
                key = (str(source), test)
                row = {
                    "identity":
                    list(
                        identity(case, put.get("path_function"), put.get("unit"), put.get("enc"),
                                 put.get("piece"))),
                    "file":
                    str(source),
                    "test":
                    test,
                    "function_sha256":
                    digest_bytes(function.encode()),
                    "source_sha256":
                    digest_file(source),
                    "put_json":
                    str(put_path),
                    "put_json_sha256":
                    digest_file(put_path),
                    "result_json":
                    str(result_path.resolve()),
                    "result_json_sha256":
                    digest_file(result_path),
                    "forge_status":
                    "Success",
                    "valid_reference_test":
                    True,
                    "concrete_oracles":
                    oracles,
                }
                previous = valid.get(key)
                if previous is not None and previous != row:
                    raise ValueError(f"conflicting valid duplicate: {key}")
                valid[key] = row
    if len(valid) != expected_valid:
        raise ValueError(f"expected {expected_valid} valid concrete tests, got {len(valid)}")
    valid_keys = set(valid)
    payload = {
        "schema": "veriput-rq3-valid-concrete-snapshot/v1",
        "rq3_root": str(rq3_root.resolve()),
        "published_shards": list(PUBLISHED_SHARDS),
        "raw_count": len(raw),
        "valid_count": len(valid),
        "raw_keys_sha256": key_set_digest(raw),
        "valid_keys_sha256": key_set_digest(valid_keys),
        "raw_equals_valid": raw == valid_keys,
        "result_json_count": len(result_paths),
        "rows": sorted(valid.values(), key=lambda row: (row["identity"], row["file"], row["test"])),
    }
    payload["seal_sha256"] = digest_bytes(canonical_json(payload))
    return payload


def load_snapshot(path: Path) -> dict[str, Any]:
    snapshot = read_object(path)
    seal = snapshot.pop("seal_sha256", None)
    actual = digest_bytes(canonical_json(snapshot))
    snapshot["seal_sha256"] = seal
    if not isinstance(seal, str) or seal != actual:
        raise ValueError(f"snapshot seal mismatch: {path}")
    if snapshot.get("schema") != "veriput-rq3-valid-concrete-snapshot/v1":
        raise ValueError("unsupported snapshot schema")
    if tuple(snapshot.get("published_shards") or ()) != PUBLISHED_SHARDS:
        raise ValueError("snapshot does not contain exactly the published shards")
    rows = snapshot.get("rows")
    if not isinstance(rows, list) or len(rows) != snapshot.get("valid_count"):
        raise ValueError("snapshot valid count mismatch")
    keys = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("forge_status") != "Success" or row.get(
                "valid_reference_test") is not True:
            raise ValueError("snapshot contains a non-valid candidate")
        row_identity = row.get("identity")
        if (not isinstance(row_identity, list) or len(row_identity) != 5
                or any(not str(value) for value in row_identity[:4])):
            raise ValueError("snapshot contains an incomplete five-field identity")
        if not isinstance(row.get("concrete_oracles"), list) or not row["concrete_oracles"]:
            raise ValueError("snapshot contains a concrete test without an oracle")
        required = ("file", "test", "function_sha256", "source_sha256", "put_json",
                    "put_json_sha256", "result_json", "result_json_sha256")
        if any(not isinstance(row.get(key), str) or not row[key] for key in required):
            raise ValueError("snapshot row lacks sealed evidence fields")
        key = (row["file"], row["test"])
        if key in keys:
            raise ValueError("snapshot contains duplicate concrete tests")
        keys.add(key)
    if key_set_digest(keys) != snapshot.get("valid_keys_sha256"):
        raise ValueError("snapshot valid key digest mismatch")
    equal = (snapshot.get("raw_count") == snapshot.get("valid_count")
             and snapshot.get("raw_keys_sha256") == snapshot.get("valid_keys_sha256"))
    if snapshot.get("raw_equals_valid") is not equal:
        raise ValueError("snapshot raw/valid equality claim is inconsistent")
    return snapshot


def rename_function(function: str, old: str, new: str) -> str:
    return re.sub(r"(\bfunction\s+)" + re.escape(old) + r"(\s*\()",
                  r"\1" + new + r"\2",
                  function,
                  count=1)


def target_rows(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    rows = mapping.get("rows")
    if not isinstance(rows, list):
        raise ValueError("mapping has no rows")
    result = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "applied":
            continue
        selected = row.get("selected_rq3")
        selected = selected if isinstance(selected, dict) else {}
        if (selected.get("forge_status") == "Success"
                and selected.get("valid_reference_test") is True):
            continue
        result.append(row)
    return result


def stage_replacements(snapshot: dict[str, Any], mapping: dict[str, Any],
                       staging: Path) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in snapshot["rows"]:
        candidates[tuple(row["identity"])].append(row)
    rows = []
    seen_sources: set[str] = set()
    for target in target_rows(mapping):
        output = {
            key: target.get(key)
            for key in ("identity", "case", "source", "test", "anchor_test", "source_sha256")
        }
        exact = candidates.get(tuple(target["identity"]), [])
        output["exact_candidate_count"] = len(exact)
        if len(exact) != 1:
            output.update(status="refused",
                          reason=("no exact valid candidate"
                                  if not exact else "ambiguous exact valid candidates"))
            rows.append(output)
            continue
        source_path = Path(str(target["source"])).resolve()
        source = source_path.read_text(encoding="utf-8")
        source_hash = digest_bytes(source.encode())
        if str(source_path) in seen_sources:
            output.update(status="refused", reason="multiple replacement targets share one source")
            rows.append(output)
            continue
        seen_sources.add(str(source_path))
        anchors = ANY_ANCHOR.findall(source)
        generated = GENERATED_ANCHOR.findall(source)
        if len(anchors) != 1 or len(generated) != 1 or anchors != generated:
            output.update(status="refused",
                          reason="source lacks exactly one generated-only anchor",
                          anchors=anchors)
            rows.append(output)
            continue
        put_before = zero_parameter_function(source, str(target["test"]))
        if put_before is None:
            # PUTs are parameterized; preserve their full function bytes regardless.
            put_span = function_span(source, str(target["test"]))
            put_before = source[put_span[0]:put_span[1]] if put_span else None
        if put_before is None:
            output.update(status="refused", reason="target PUT function is not unique")
            rows.append(output)
            continue
        candidate = exact[0]
        rq3_path = Path(candidate["file"])
        if digest_file(rq3_path) != candidate["source_sha256"]:
            output.update(status="refused", reason="RQ3 snapshot source hash mismatch")
            rows.append(output)
            continue
        rq3_source = rq3_path.read_text(encoding="utf-8")
        function = zero_parameter_function(rq3_source, candidate["test"])
        if function is None or digest_bytes(function.encode()) != candidate["function_sha256"]:
            output.update(status="refused", reason="RQ3 snapshot function hash mismatch")
            rows.append(output)
            continue
        anchor_name = "test_ce_anchor_rq3_" + digest_bytes(
            "\0".join(target["identity"] + [str(target["test"])]).encode())[:16]
        replacement = rename_function(function, candidate["test"], anchor_name)
        anchor_span = function_span(source, generated[0])
        assert anchor_span is not None
        staged_source = source[:anchor_span[0]] + replacement + source[anchor_span[1]:]
        put_after_span = function_span(staged_source, str(target["test"]))
        put_after = (staged_source[put_after_span[0]:put_after_span[1]] if put_after_span else None)
        if put_after != put_before:
            output.update(status="refused", reason="PUT bytes changed during replacement")
            rows.append(output)
            continue
        relative = Path(*source_path.parts[1:]) if source_path.is_absolute() else source_path
        staged_path = staging / relative
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(staged_source, encoding="utf-8")
        output.update({
            "status": "staged",
            "anchor_test": anchor_name,
            "original_anchor_test": generated[0],
            "source_sha256": source_hash,
            "staged_source": str(staged_path),
            "staged_source_sha256": digest_file(staged_path),
            "put_function_sha256": digest_bytes(put_before.encode()),
            "selected_rq3": candidate,
        })
        rows.append(output)
    return rows


def foundry_root(source: Path) -> Path | None:
    return next((parent for parent in source.parents if (parent / "foundry.toml").is_file()), None)


def forge_gate(source: Path, put_test: str, anchor_test: str, timeout: int) -> dict[str, Any]:
    root = foundry_root(source)
    if root is None:
        return {"status": "Failure", "reason": "foundry root absent"}
    pattern = "^(" + "|".join(re.escape(name) for name in (put_test, anchor_test)) + r")(\(|$)"
    command = [
        "forge", "test", "--root",
        str(root), "--match-path",
        str(source.relative_to(root)), "--match-test", pattern, "--fuzz-runs", "256", "--json"
    ]
    try:
        process = subprocess.run(command,
                                 capture_output=True,
                                 text=True,
                                 check=False,
                                 timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "Timeout", "command": command}
    executed: dict[str, str] = {}
    try:
        document = json.loads(process.stdout)
        for suite in document.values():
            for name, result in suite.get("test_results", {}).items():
                for expected in (put_test, anchor_test):
                    if re.match(r"^" + re.escape(expected) + r"(?:\(|$)", name):
                        if expected in executed:
                            executed[expected] = "Duplicate"
                        else:
                            executed[expected] = str(result.get("status"))
    except (AttributeError, json.JSONDecodeError):
        pass
    success = (process.returncode == 0 and executed == {
        put_test: "Success",
        anchor_test: "Success"
    })
    return {
        "status": "Success" if success else "Failure",
        "command": command,
        "returncode": process.returncode,
        "executed": executed,
        "stdout_sha256": digest_bytes(process.stdout.encode()),
        "stderr_tail": process.stderr[-2000:],
    }


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".valid-anchor.tmp")
    temporary.write_bytes(data)
    os.chmod(temporary, path.stat().st_mode)
    os.replace(temporary, path)


def apply_replacements(rows: list[dict[str, Any]], snapshot: dict[str, Any], timeout: int) -> int:
    if (not snapshot.get("raw_equals_valid")
            or snapshot.get("raw_keys_sha256") != snapshot.get("valid_keys_sha256")):
        raise RuntimeError("canonical replacement blocked: frozen RQ3 raw != valid")
    staged_rows = [row for row in rows if row.get("status") == "staged"]
    originals: dict[Path, bytes] = {}
    for row in staged_rows:
        target = Path(row["source"])
        original = target.read_bytes()
        if digest_bytes(original) != row["source_sha256"]:
            raise RuntimeError(f"source changed after staging: {target}")
        if digest_file(Path(row["staged_source"])) != row["staged_source_sha256"]:
            raise RuntimeError(f"staged source seal mismatch: {target}")
        originals[target] = original

    changed: list[tuple[dict[str, Any], Path]] = []
    failure: BaseException | None = None
    try:
        for row in staged_rows:
            target = Path(row["source"])
            atomic_write(target, Path(row["staged_source"]).read_bytes())
            changed.append((row, target))
            gate = forge_gate(target, row["test"], row["anchor_test"], timeout)
            row["forge_gate"] = gate
            if gate["status"] != "Success":
                raise RuntimeError(f"Forge gate failed: {target}")
            row.update(status="applied", applied_source_sha256=digest_file(target))
    except BaseException as error:  # Roll back even when the run is interrupted.
        failure = error
    if failure is not None:
        for row, target in reversed(changed):
            atomic_write(target, originals[target])
            row.update(status="rolled-back", rollback_source_sha256=digest_file(target))
        if not isinstance(failure, (OSError, RuntimeError)):
            raise failure
        return 0
    return len(changed)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def freeze_command(args: argparse.Namespace) -> int:
    snapshot = freeze_snapshot(args.rq3_root, args.expected_valid)
    write_json_atomic(args.output, snapshot)
    print(
        json.dumps(
            {
                key: snapshot[key]
                for key in ("raw_count", "valid_count", "raw_equals_valid", "seal_sha256")
            },
            sort_keys=True))
    return 0


def replace_command(args: argparse.Namespace) -> int:
    snapshot = load_snapshot(args.snapshot)
    mapping = read_object(args.mapping)
    args.staging.mkdir(parents=True, exist_ok=True)
    rows = stage_replacements(snapshot, mapping, args.staging)
    refused_before_apply = any(row.get("status") == "refused" for row in rows)
    applied = (apply_replacements(rows, snapshot, args.timeout)
               if args.apply and not refused_before_apply else 0)
    counts = Counter(str(row.get("status")) for row in rows)
    report = {
        "schema": "veriput-rq1-valid-anchor-replacement/v1",
        "snapshot": str(args.snapshot.resolve()),
        "snapshot_sha256": digest_file(args.snapshot),
        "snapshot_seal_sha256": snapshot["seal_sha256"],
        "mapping": str(args.mapping.resolve()),
        "mapping_sha256": digest_file(args.mapping),
        "raw_equals_valid_gate": snapshot["raw_equals_valid"],
        "counts": {
            "nonvalid_current_anchors": len(rows),
            "replaceable_exact_valid": counts["staged"] + counts["applied"],
            "refused": counts["refused"],
            "rolled_back": counts["rolled-back"],
            "applied": applied,
        },
        "rows": rows,
    }
    write_json_atomic(args.report, report)
    print(json.dumps(report["counts"], sort_keys=True))
    failed_apply = args.apply and (counts["refused"] != 0 or counts["rolled-back"] != 0
                                   or applied != len(rows))
    return int(failed_apply)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--rq3-root", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--expected-valid", type=int, default=2140)
    freeze.set_defaults(handler=freeze_command)
    replace = subparsers.add_parser("replace")
    replace.add_argument("--snapshot", type=Path, required=True)
    replace.add_argument("--mapping", type=Path, required=True)
    replace.add_argument("--staging", type=Path, required=True)
    replace.add_argument("--report", type=Path, required=True)
    replace.add_argument("--timeout", type=int, default=180)
    replace.add_argument("--apply", action="store_true")
    replace.set_defaults(handler=replace_command)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
