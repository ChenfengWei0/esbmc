#!/usr/bin/env python3
"""Stage mechanical RQ3 anchors for the frozen RQ1 closure.

This script never edits RQ1 canonical files and never creates Forge records.
It copies a RQ1 materialized concrete test into a staging tree and appends an
identical, deterministically renamed test function.  Rows without an
unambiguous concrete test or claim binding are refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def function_span(source: str, name: str) -> tuple[int, int] | None:
    matches = list(re.finditer(r"\bfunction\s+" + re.escape(name) + r"\s*\(", source))
    if len(matches) != 1:
        return None
    start = matches[0].start()
    opening = source.find("{", matches[0].end())
    if opening < 0:
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(opening, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return start, index + 1
    return None


def source_index(root: Path) -> dict[tuple[str, str], list[Path]]:
    index: dict[tuple[str, str], list[Path]] = {}
    for source in root.glob("*/subjects/*/put/*/rq3-mechanical/*/test/*.t.sol"):
        relative_parts = source.relative_to(root).parts
        subject_marker = relative_parts.index("subjects")
        case = "/".join(relative_parts[:subject_marker] +
                           (relative_parts[subject_marker + 1],))
        text = source.read_text(encoding="utf-8", errors="replace")
        # Avoid parsing the Solidity identity grammar: it deliberately contains
        # colons.  The frozen identity is matched as a complete claim prefix.
        for line in text.splitlines():
            marker = "// claim:"
            if marker in line:
                claim = line.split(marker, 1)[1].strip().rsplit(":path:", 1)[0]
                if claim:
                    index.setdefault((case, claim), []).append(source)
    return index


def stage_row(row: dict[str, Any], index: dict[tuple[str, str], list[Path]],
              stage_root: Path) -> dict[str, Any]:
    selected = row.get("selected") or {}
    identity = selected.get("identity") or row.get("identity")
    if not isinstance(identity, list) or len(identity) != 5:
        return {"status": "refused", "reason": "malformed identity"}
    case, claim, unit, enc, piece = (str(value or "") for value in identity)
    candidates = index.get((case, claim), [])
    if len(candidates) != 1:
        return {"identity": identity, "status": "refused",
                "reason": f"claim source candidates={len(candidates)}"}
    source = candidates[0]
    test_name = str(selected.get("test") or "")
    if not test_name or function_span(source.read_text(encoding="utf-8"), test_name) is None:
        return {"identity": identity, "status": "refused", "source": str(source),
                "reason": "selected concrete test is absent or ambiguous"}
    original = source.read_text(encoding="utf-8")
    span = function_span(original, test_name)
    assert span is not None
    anchor_name = "test_ce_anchor_rq3_" + digest("\x00".join(identity))[:16]
    if re.search(r"\bfunction\s+" + re.escape(anchor_name) + r"\s*\(", original):
        return {"identity": identity, "status": "refused", "source": str(source),
                "reason": "anchor already present"}
    function = original[span[0]:span[1]]
    renamed = re.sub(r"(\bfunction\s+)" + re.escape(test_name) + r"(\s*\()",
                     r"\1" + anchor_name + r"\2", function, count=1)
    if renamed == function:
        return {"identity": identity, "status": "refused", "source": str(source),
                "reason": "test rename failed"}
    relative = source.relative_to(index_root(source))
    target = stage_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(original[:span[1]] + "\n\n  // RQ1/RQ3 mechanical anchor.\n  "
                         + renamed + original[span[1]:], encoding="utf-8")
    return {"identity": identity, "status": "staged", "source": str(source),
            "staged_source": str(target), "source_sha256": digest(original),
            "staged_sha256": digest(target.read_text(encoding="utf-8")),
            "test": test_name, "anchor_test": anchor_name, "unit": unit,
            "enc": enc, "piece": piece, "anchor_binding": "rq3-identical-test/v1"}


def index_root(path: Path) -> Path:
    # source_index stores absolute paths; derive the RQ1 root from its layout.
    parts = path.parts
    marker = parts.index("VeriPUT")
    return Path(*parts[:marker + 1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("binding", type=Path)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    binding = load(args.binding)
    rows = binding.get("rows")
    if not isinstance(rows, list):
        raise SystemExit("binding has no rows")
    if args.staging.exists():
        shutil.rmtree(args.staging)
    args.staging.mkdir(parents=True)
    index = source_index(args.rq1_root)
    staged = [stage_row(row, index, args.staging) for row in rows]
    report = {
        "schema": "rq1-rq3-anchor-staging/v1",
        "binding": str(args.binding.resolve()),
        "binding_sha256": digest(args.binding.read_text(encoding="utf-8")),
        "source_count": len(index),
        "rows": staged,
        "counts": {
            "total": len(staged),
            "staged": sum(item.get("status") == "staged" for item in staged),
            "refused": sum(item.get("status") != "staged" for item in staged),
        },
        "policy": "staging only; no canonical writes, Forge, or PUT credit",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if report["counts"]["refused"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
