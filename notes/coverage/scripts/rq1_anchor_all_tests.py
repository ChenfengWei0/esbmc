#!/usr/bin/env python3
"""Mechanically add an executable anchor to every stable RQ1 test source.

This tool is intentionally source-only.  It does not create certification
records or claim Forge success.  The generated anchor calls the existing
test function with deterministic zero/one arguments, so a later Forge pass
can independently decide whether the source is executable and green.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


SUITES = ("peer182", "real203", "bugfix124")
EXCLUDED = {
    "_wd", "cache", "lib", "repair", "stage", "staging", "scratch",
    "_valid_regression_monitor", "monitor", "redo",
}


def sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def mask_comments(source: str) -> str:
    out = list(source)
    i = 0
    state = "code"
    while i < len(source):
        c = source[i]
        n = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if c == "/" and n == "/":
                out[i] = out[i + 1] = " "
                i += 2
                state = "line"
                continue
            if c == "/" and n == "*":
                out[i] = out[i + 1] = " "
                i += 2
                state = "block"
                continue
            if c in "\"'":
                out[i] = " "
                state = c
            i += 1
            continue
        if state == "line":
            if c == "\n":
                state = "code"
            elif c not in "\r":
                out[i] = " "
            i += 1
            continue
        if state == "block":
            if c == "*" and n == "/":
                out[i] = out[i + 1] = " "
                i += 2
                state = "code"
            else:
                if c not in "\r\n":
                    out[i] = " "
                i += 1
            continue
        if c == "\\":
            out[i] = " "
            if i + 1 < len(source):
                out[i + 1] = " "
                i += 2
            else:
                i += 1
        elif c == state:
            out[i] = " "
            state = "code"
            i += 1
        else:
            if c not in "\r\n":
                out[i] = " "
            i += 1
    return "".join(out)


def brace_end(mask: str, opening: int) -> int | None:
    depth = 0
    for i in range(opening, len(mask)):
        if mask[i] == "{":
            depth += 1
        elif mask[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def function_span(source: str, name: str) -> tuple[int, int] | None:
    masked = mask_comments(source)
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", masked)
    if not match:
        return None
    opening = masked.find("{", match.end())
    if opening < 0:
        return None
    closing = brace_end(masked, opening)
    return (match.start(), closing) if closing is not None else None


def contract_end(source: str, function: str) -> int | None:
    masked = mask_comments(source)
    fn = function_span(source, function)
    if fn is None:
        return None
    owner_end = None
    for match in re.finditer(r"\b(?:contract|library)\s+[A-Za-z_$][A-Za-z0-9_$]*", masked):
        opening = masked.find("{", match.end(), fn[0])
        if opening < 0:
            continue
        end = brace_end(masked, opening)
        if end is not None and opening < fn[0] < end:
            owner_end = end
    return owner_end


def split_params(text: str) -> list[str]:
    if not text.strip():
        return []
    result, start, depth = [], 0, 0
    for i, c in enumerate(text):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "," and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return result


def params_for(source: str, name: str) -> str | None:
    span = function_span(source, name)
    if span is None:
        return None
    masked = mask_comments(source)
    opening = masked.find("(", span[0], span[1])
    depth = 0
    for i in range(opening, span[1]):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return source[opening + 1:i]
    return None


def argument(param: str) -> str | None:
    param = " ".join(param.split())
    tokens = param.split(" ")
    if len(tokens) < 2:
        return None
    name = tokens[-1]
    typ = " ".join(tokens[:-1]).replace(" memory", "").replace(" calldata", "")
    if typ == "address":
        return "address(uint160(1))"
    if typ == "address payable":
        return "payable(address(uint160(1)))"
    if typ == "bool":
        return "false"
    if typ == "string":
        return '""'
    if typ == "bytes":
        return "bytes(\"\")"
    if re.fullmatch(r"(?:u?int)(?:\d+)?", typ):
        return "0"
    if typ.endswith("[]") and re.fullmatch(r"(?:address|bytes\d*|uint\d*|int\d*)\[\]", typ):
        return f"new {typ[:-2]}[](0)"
    if re.fullmatch(r"bytes\d+", typ):
        return f"{typ}(0)"
    return None


def pick_function(source: str) -> tuple[str, str] | None:
    masked = mask_comments(source)
    matches = list(re.finditer(
        r"\bfunction\s+((?:test_put_|test_cov_|disabled_test_cov_|test)[A-Za-z0-9_$]*)\s*\(",
        masked))
    if not matches:
        return None
    # Prefer the live PUT, then a live concrete replay, then a disabled replay.
    matches.sort(key=lambda m: (0 if m.group(1).startswith("test_put_") else
                                1 if m.group(1).startswith("test_cov_") else 2,
                                m.start()))
    name = matches[0].group(1)
    params = params_for(source, name)
    return (name, params) if params is not None else None


def candidates(root: Path, scope: str) -> list[Path]:
    result: list[Path] = []
    for suite in SUITES:
        base = root / suite / "subjects"
        if scope in ("put", "both"):
            result.extend(base.glob("*/put/**/test/*.t.sol"))
        if scope in ("replay", "both"):
            result.extend(base.glob("*/concrete-replays/projects/*/test/*.t.sol"))
    return sorted({p for p in result if p.is_file() and not any(
        part in EXCLUDED or part.startswith(".redo") or ".redo." in part
        for part in p.parts)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--scope", choices=("put", "replay", "both"), default="both")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    files = candidates(root, args.scope)
    rows: list[dict[str, object]] = []
    if args.staging.exists():
        shutil.rmtree(args.staging)
    for source_path in files:
        source = source_path.read_text(encoding="utf-8")
        row: dict[str, object] = {"source": str(source_path), "source_sha256": sha(source)}
        if "test_ce_anchor_" in source:
            row["status"] = "already-anchored"
            rows.append(row)
            continue
        picked = pick_function(source)
        if picked is None:
            row.update(status="refused", reason="no-test-function")
            rows.append(row)
            continue
        name, raw_params = picked
        params = split_params(raw_params)
        args_expr = [argument(p) for p in params]
        if any(value is None for value in args_expr):
            row.update(status="refused", reason="unsupported-parameter-type",
                       test=name, parameters=raw_params)
            rows.append(row)
            continue
        owner_end = contract_end(source, name)
        if owner_end is None:
            row.update(status="refused", reason="contract-span-unparseable", test=name)
            rows.append(row)
            continue
        anchor = "test_ce_anchor_auto_" + sha(str(source_path.resolve()) + "\0" + name)[:16]
        call = f"this.{name}({', '.join(args_expr)});"
        body = ("  function " + anchor + "() public {\n" +
                "    // Mechanical anchor: executes the existing RQ1 test body.\n" +
                "    " + call + "\n  }\n")
        staged = source[:owner_end - 1].rstrip() + "\n\n" + body + source[owner_end - 1:]
        rel = source_path.relative_to(root)
        target = args.staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(staged, encoding="utf-8")
        row.update(status="staged", test=name, anchor_test=anchor,
                   argument_list=args_expr, staged_source=str(target),
                   staged_sha256=sha(staged), anchor_body_sha256=sha(body))
        rows.append(row)
    if args.apply:
        if args.backup is None:
            raise SystemExit("--apply requires --backup")
        args.backup.mkdir(parents=True, exist_ok=True)
        for row in rows:
            if row.get("status") != "staged":
                continue
            source = Path(str(row["source"]))
            staged = Path(str(row["staged_source"]))
            current = source.read_text(encoding="utf-8")
            if sha(current) != row["source_sha256"] or "test_ce_anchor_" in current:
                raise SystemExit(f"source changed before apply: {source}")
            backup = args.backup / (sha(str(source))[:20] + ".t.sol")
            shutil.copy2(source, backup)
            fd, temp_name = tempfile.mkstemp(prefix=".rq1-anchor-", dir=source.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(staged.read_text(encoding="utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, source)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            row["status"] = "applied"
    report = {"schema": "rq1-anchor-all-tests/v1", "scope": args.scope,
              "root": str(root), "rows": rows,
              "counts": {status: sum(row.get("status") == status for row in rows)
                         for status in sorted({str(row.get("status")) for row in rows})}}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
