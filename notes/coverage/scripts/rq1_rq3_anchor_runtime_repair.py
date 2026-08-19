#!/usr/bin/env python3
"""Make runtime-red RQ3 replay anchors assert their observed revert."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq1_anchor_all_tests import function_span  # pylint: disable=wrong-import-position

DEFAULT_RQ1 = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def trace_proves_revert(trace: dict[str, Any], name: str) -> bool:
    """Check that the anchor trace reaches its target and that call reverts."""
    output = str(trace.get("output") or "")
    unit = str(trace.get("unit") or "")
    anchor = output.find("::" + name + "()")
    suite = output.find("Suite result:", anchor)
    if anchor < 0 or not unit:
        return False
    frame = output[anchor:suite if suite >= 0 else None]
    call = re.search(r"::" + re.escape(unit) + r"(?:\{|\()", frame)
    return bool(
        call
        and re.search(r"<- \[(?:Revert|OutOfFunds)\]", frame[call.end():].replace("\u2190", "<-")))


def repair(function: str) -> str:
    """Replace a false normal-exit oracle with the observed revert oracle."""
    if "vm.expectRevert();" in function:
        return function
    calls = list(re.finditer(r"(?m)^([ \t]*)c0\.[A-Za-z_$][A-Za-z0-9_$]*", function))
    if len(calls) != 1:
        raise ValueError(f"expected one concrete c0 call, found {len(calls)}")
    call = calls[0]
    prefix = function[:call.start()]
    prank = list(re.finditer(r"(?m)^[ \t]*vm\.prank\([^\n]+\);\n", prefix))
    insert = prank[-1].start() if prank else call.start()
    indent = call.group(1)
    function = function[:insert] + indent + "vm.expectRevert();\n" + function[insert:]
    function = re.sub(
        r"(?m)^[ \t]*(?:bool _veriput_concrete_completed = false;|"
        r"_veriput_concrete_completed = true;|"
        r"assertTrue\(_veriput_concrete_completed,[^\n]+\);)\n?", "", function)
    function = function.replace("// [asserted] path exits normally; a revert fails the test",
                                "// RQ3 fixed replay outcome: the target call reverts.")
    return function


def main() -> int:  # pylint: disable=too-many-locals,too-many-statements
    """Repair every runtime failure recorded by the Forge anchor report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forge-report", type=Path, required=True)
    parser.add_argument("--trace-report", type=Path, required=True)
    parser.add_argument("--rq1-root", type=Path, default=DEFAULT_RQ1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.forge_report.read_text(encoding="utf-8"))
    traces = json.loads(args.trace_report.read_text(encoding="utf-8"))
    evidence = {(row.get("source"), row.get("anchor_test")): row for row in traces.get("rows", [])}
    plans: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in report["rows"]:
        if row.get("status") != "Failure" or not row.get("test_statuses"):
            continue
        path = Path(row["source"]).resolve()
        try:
            path.relative_to(args.rq1_root.resolve())
        except ValueError as error:
            raise ValueError(f"source is outside RQ1: {path}") from error
        source = path.read_text(encoding="utf-8")
        name = str(row["anchor_test"])
        key = (str(path), name)
        if key in seen:
            raise ValueError(f"duplicate repair row: {path}:{name}")
        seen.add(key)
        trace = evidence.get(key)
        if trace is None or not trace_proves_revert(trace, name):
            raise ValueError(f"target-call revert is not trace-proven: {path}:{name}")
        if not name.startswith("test_ce_anchor_rq3_"):
            raise ValueError(f"refusing non-RQ3 anchor: {path}:{name}")
        anchors = re.findall(r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_$]+)\s*\(", source)
        if anchors != [name]:
            raise ValueError(f"expected exactly anchor {name} in {path}; got {anchors}")
        span = function_span(source, name)
        if span is None:
            raise ValueError(f"anchor function absent: {path}:{name}")
        function = source[span[0]:span[1]]
        fixed = repair(function)
        updated = source[:span[0]] + fixed + source[span[1]:]
        plans.append({
            "path": path,
            "name": name,
            "source": source,
            "updated": updated,
            "changed": updated != source
        })

    rows: list[dict[str, Any]] = []
    for plan in plans:
        path = plan["path"]
        if not plan["changed"]:
            rows.append({
                "source": str(path),
                "anchor_test": plan["name"],
                "status": "already-repaired"
            })
            continue
        temporary = path.with_name(path.name + ".runtime-repair.tmp")
        temporary.write_text(plan["updated"], encoding="utf-8")
        os.chmod(temporary, path.stat().st_mode)
        os.replace(temporary, path)
        rows.append({
            "source": str(path),
            "anchor_test": plan["name"],
            "status": "repaired-revert-oracle"
        })
    result = {
        "schema": "veriput-rq3-anchor-runtime-repair/v1",
        "repaired": sum(row["status"] == "repaired-revert-oracle" for row in rows),
        "already_repaired": sum(row["status"] == "already-repaired" for row in rows),
        "rows": rows,
    }
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("repaired", "already_repaired")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
