#!/usr/bin/env python3
"""Stage mechanically recoverable CE anchors for canonical RQ1 PUT tests.

This is deliberately a staging-only tool.  It does not run Forge and it never
rewrites a canonical RQ1 source.  A PUT is eligible only when one concrete
``test_cov_*`` basis in the same operation tree has the exact path-function,
unit and enc claim, and the basis has the same ``setUp`` body as the PUT.
Missing records, ambiguous bases, and parameterized/unsupported functions are
reported as refusals instead of being guessed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_STAGING = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_NoPUT600/"
    "adoption-bundles/rq1-anchor-mechanical-injector-20260815")
SUITES = ("peer182", "real203", "bugfix124")
SKIP_PARTS = frozenset({"_wd", "lib", "cache", "rq3-mechanical", "stage", "staging"})


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _code_mask(source: str) -> str:
    """Mask comments and literals while retaining offsets and newlines."""
    out = list(source)
    i = 0
    state = "code"
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                out[i] = out[i + 1] = " "
                i += 2
                state = "line"
                continue
            if ch == "/" and nxt == "*":
                out[i] = out[i + 1] = " "
                i += 2
                state = "block"
                continue
            if ch in ('"', "'"):
                state = ch
                out[i] = " "
            i += 1
            continue
        if state == "line":
            if ch == "\n":
                state = "code"
            elif ch != "\r":
                out[i] = " "
            i += 1
            continue
        if state == "block":
            if ch == "*" and nxt == "/":
                out[i] = out[i + 1] = " "
                i += 2
                state = "code"
            else:
                if ch not in "\r\n":
                    out[i] = " "
                i += 1
            continue
        # Solidity string/character literal.  Escaped quotes do not end it.
        if ch == "\\":
            out[i] = " "
            if i + 1 < len(source):
                if source[i + 1] not in "\r\n":
                    out[i + 1] = " "
                i += 2
            else:
                i += 1
            continue
        if ch == state:
            out[i] = " "
            state = "code"
        elif ch not in "\r\n":
            out[i] = " "
        i += 1
    return "".join(out)


def _matching_brace(mask: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(mask)):
        if mask[index] == "{":
            depth += 1
        elif mask[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                return None
    return None


def _function_span(source: str, name: str) -> tuple[int, int] | None:
    mask = _code_mask(source)
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", mask)
    if match is None:
        return None
    opening = mask.find("{", match.end())
    if opening < 0:
        return None
    closing = _matching_brace(mask, opening)
    return (match.start(), closing) if closing is not None else None


def _function_names(source: str, prefix: str) -> list[str]:
    mask = _code_mask(source)
    return re.findall(r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", mask)


def _function_body(source: str, name: str) -> str | None:
    span = _function_span(source, name)
    if span is None:
        return None
    opening = _code_mask(source).find("{", span[0], span[1])
    if opening < 0:
        return None
    return source[opening + 1:span[1] - 1]


def _function_params(source: str, name: str) -> str | None:
    span = _function_span(source, name)
    if span is None:
        return None
    mask = _code_mask(source)
    opening = mask.find("(", span[0], span[1])
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, span[1]):
        if mask[index] == "(":
            depth += 1
        elif mask[index] == ")":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    return None


def _contract_end_for_function(source: str, function: str) -> int | None:
    mask = _code_mask(source)
    span = _function_span(source, function)
    if span is None:
        return None
    contracts = list(re.finditer(r"\b(?:contract|library|interface)\s+[A-Za-z_$][A-Za-z0-9_$]*", mask))
    owner = None
    for match in contracts:
        opening = mask.find("{", match.end(), span[0])
        if opening < 0:
            continue
        end = _matching_brace(mask, opening)
        if end is not None and opening < span[0] < end:
            owner = end
    return owner


def _claims(source: str) -> set[tuple[str, int]]:
    result = set()
    for match in re.finditer(r"^\s*//\s*claim:\s*(.+):path:(\d+)(?:\s|$)", source, re.M):
        result.add((match.group(1).strip(), int(match.group(2))))
    return result


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _skip(path: Path) -> bool:
    return any(part != "tmp" and part.startswith(("_scratch", "scratch", "tmp", "repair"))
               or part in SKIP_PARTS for part in path.parts)


def _target_files(root: Path, suites: tuple[str, ...]) -> list[Path]:
    result = []
    for suite in suites:
        for path in sorted((root / suite).glob("subjects/*/put/**/test/*.t.sol")):
            if path.is_file() and not _skip(path.relative_to(root)):
                try:
                    source = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                # Concrete replay files are bases, not PUT destinations.  A
                # PUT emitter names its executable function test_put_* even
                # when the filename contains a concrete suffix.
                if re.search(r"\bfunction\s+test_put_[A-Za-z0-9_$]*\s*\(",
                             _code_mask(source)):
                    result.append(path)
    return result


def _record_for(target: Path, operation: Path) -> tuple[dict[str, Any] | None, str | None]:
    matches = []
    target_resolved = target.resolve()
    for path in operation.rglob("put.json"):
        document = _read_json(path)
        raw_file = document.get("file")
        if not isinstance(raw_file, str):
            continue
        try:
            if Path(raw_file).resolve() == target_resolved:
                matches.append((path, document))
        except OSError:
            continue
    if len(matches) != 1:
        return None, "missing-put-record" if not matches else "ambiguous-put-record"
    return matches[0][1], None


def _concrete_candidates(operation: Path, identity: tuple[str, int], target: Path) -> list[dict[str, Any]]:
    candidates = []
    for path in sorted(operation.glob("**/test/*.t.sol")):
        if path == target or _skip(path.relative_to(operation)):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not re.search(r"\bfunction\s+test_cov_[A-Za-z0-9_$]*\s*\(", _code_mask(source)):
            continue
        if identity not in _claims(source):
            continue
        names = [name for name in _function_names(source, "test_cov_") if name.startswith("test_cov_")]
        if len(names) != 1 or _function_params(source, names[0]).strip():
            continue
        candidates.append({
            "file": path,
            "test": names[0],
            "source": source,
            "sha256": _sha256(source),
        })
    return candidates


def _prepare(target: Path, root: Path) -> dict[str, Any]:
    try:
        source = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "refused", "reason": f"source-read:{exc}", "target": str(target)}
    existing = re.findall(r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_$]*)\s*\(", _code_mask(source))
    if len(existing) == 1:
        return {"status": "already-anchored", "target": str(target), "anchor": existing[0]}
    if len(existing) > 1:
        return {"status": "refused", "reason": "duplicate-anchor-functions", "target": str(target)}
    operation = target.parents[2]
    record, error = _record_for(target, operation)
    if record is None:
        return {"status": "refused", "reason": error, "target": str(target)}
    path_function = record.get("path_function")
    unit = str(record.get("unit") or "")
    raw_enc = record.get("enc")
    try:
        enc = int(str(raw_enc))
    except (TypeError, ValueError):
        return {"status": "refused", "reason": "non-numeric-enc", "target": str(target)}
    if not isinstance(path_function, str) or not path_function or not unit:
        return {"status": "refused", "reason": "incomplete-put-identity", "target": str(target)}
    piece = "" if record.get("piece") is None else str(record.get("piece"))
    identity = (path_function, enc)
    if identity not in _claims(source):
        return {"status": "refused", "reason": "put-claim-missing", "target": str(target),
                "identity": {"path_function": path_function, "unit": unit, "enc": enc,
                             "piece": piece}}
    candidates = _concrete_candidates(operation, identity, target)
    if len(candidates) != 1:
        return {
            "status": "refused",
            "reason": "missing-concrete-basis" if not candidates else "ambiguous-concrete-basis",
            "candidate_count": len(candidates),
            "target": str(target),
            "identity": {
                "path_function": path_function, "unit": unit, "enc": enc, "piece": piece
            },
        }
    candidate = candidates[0]
    basis_setup = _function_body(candidate["source"], "setUp")
    target_setup = _function_body(source, "setUp")
    if basis_setup is None or target_setup is None or _normal(basis_setup) != _normal(target_setup):
        return {"status": "refused", "reason": "setup-mismatch", "target": str(target)}
    basis_test = candidate["test"]
    basis_span = _function_span(candidate["source"], basis_test)
    if basis_span is None:
        return {"status": "refused", "reason": "basis-function-unparseable", "target": str(target)}
    basis_source = candidate["source"][basis_span[0]:basis_span[1]]
    digest = _sha256("\n".join((str(target.resolve()), path_function, unit, str(enc), piece,
                                 candidate["sha256"], basis_test)))[:16]
    anchor = "test_ce_anchor_mech_" + digest
    renamed = re.sub(r"(\bfunction\s+)" + re.escape(basis_test) + r"\b",
                     r"\1" + anchor, basis_source, count=1)
    if renamed == basis_source or not re.search(r"\bfunction\s+" + re.escape(anchor) + r"\s*\(", renamed):
        return {"status": "refused", "reason": "basis-rename-failed", "target": str(target)}
    insert_at = _contract_end_for_function(source, str(record.get("test") or ""))
    if insert_at is None:
        return {"status": "refused", "reason": "put-contract-unparseable", "target": str(target)}
    staged = source[:insert_at].rstrip() + "\n\n" + renamed.rstrip() + "\n" + source[insert_at:]
    rel = target.resolve().relative_to(root.resolve())
    return {
        "status": "ready",
        "target": str(target),
        "relative_target": str(rel),
        "identity": {"path_function": path_function, "unit": unit, "enc": enc, "piece": piece},
        "put_test": record.get("test"),
        "basis_file": str(candidate["file"]),
        "basis_test": basis_test,
        "basis_sha256": candidate["sha256"],
        "basis_function_sha256": _sha256(basis_source),
        "anchor_test": anchor,
        "anchor_function_sha256": _sha256(renamed),
        "source_before_sha256": _sha256(source),
        "source_after_sha256": _sha256(staged),
        "staged_source": staged,
    }


def _write_staging(entries: list[dict[str, Any]], staging: Path) -> None:
    for entry in entries:
        if entry.get("status") != "ready":
            continue
        destination = staging / str(entry["relative_target"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(str(entry["staged_source"]), encoding="utf-8")
        entry.pop("staged_source", None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq1-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--staging-root", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--suite", choices=SUITES, action="append")
    parser.add_argument("--limit", type=int, default=0, help="process at most N target files")
    args = parser.parse_args(argv)
    root = args.rq1_root.resolve()
    staging = args.staging_root.resolve()
    if staging == root or root in staging.parents:
        parser.error("staging root must not be inside canonical RQ1 root")
    suites = tuple(args.suite or SUITES)
    targets = _target_files(root, suites)
    if args.limit > 0:
        targets = targets[:args.limit]
    entries = [_prepare(target, root) for target in targets]
    _write_staging(entries, staging)
    counts = {key: sum(entry.get("status") == key for entry in entries)
              for key in ("ready", "refused", "already-anchored")}
    manifest = {
        "schema": "veriput-rq1-mechanical-anchor-staging/v1",
        "canonical_root": str(root),
        "staging_root": str(staging),
        "generated_at": int(time.time()),
        "selection": {"suites": list(suites), "limit": args.limit, "target_count": len(targets)},
        "summary": {**counts, "total": len(entries)},
        "entries": entries,
    }
    manifest_path = (args.manifest.resolve() if args.manifest else staging / "manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "summary": manifest["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
