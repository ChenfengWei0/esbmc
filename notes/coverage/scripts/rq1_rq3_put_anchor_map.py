#!/usr/bin/env python3
"""Map each canonical RQ1 PUT test unit to one RQ3 concrete anchor.

The population comes from ``rq1_put_provenance_inventory.py``.  Physical
copies are never population members: the key is the selected PUT test and
test function.  RQ3 candidates are ranked mechanically, and this command
only writes a staging tree and a report.  It never edits canonical RQ1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rq1_artifact_audit import canonical_subject  # pylint: disable=wrong-import-position
from rq1_anchor_all_tests import (  # pylint: disable=wrong-import-position
    contract_end,
    function_span,
)
from rq3_mechanical_match import identity, load_rq3  # pylint: disable=wrong-import-position


DEFAULT_RQ1 = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_RQ3 = Path(
    "/home/samson/workspace/VeriPUT/Results/RQ3/VeriExploit/No_Cer_Reg")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return None


def is_put(row: dict[str, Any]) -> bool:
    return bool(row.get("is_put") or row.get("kind") == "put")


def artifact_values(container: dict[str, Any]) -> list[dict[str, Any]]:
    values = container.get("valid_artifacts") or container.get("valid_tests") or []
    return [row for row in values if isinstance(row, dict) and is_put(row)]


def stable_subject(subject: str) -> bool:
    canonical, historical = canonical_subject(subject)
    return not historical and canonical == subject and ".redo." not in subject


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def target_source(record: dict[str, Any], root: Path) -> Path | None:
    final_test = record.get("final_test") or {}
    case = str(record.get("case") or "")
    try:
        benchmark, subject = case.split("/", 1)
    except ValueError:
        return None
    subject_dir = root / benchmark / "subjects" / subject
    values = (
        final_test.get("canonical_copy"), final_test.get("selected_retained_path"),
        final_test.get("recorded_path"))
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        path = Path(value)
        if path.is_file() and inside(path, root):
            return path.resolve()
    names = [Path(value).name for value in values
             if isinstance(value, str) and value]
    if len(set(names)) != 1:
        return None
    matches = [path.resolve() for path in subject_dir.rglob(names[0])
               if path.is_file() and "/put/" in str(path)
               and "/_wd/" not in str(path) and ".redo." not in str(path)]
    # Prefer a canonical Foundry test; an out/ copy is the final fallback.
    matches.sort(key=lambda path: (path.parent.name != "test", len(path.parts), str(path)))
    return matches[0] if matches else None


def selected_put(record: dict[str, Any]) -> Path | None:
    put = record.get("put_json") or {}
    values = [put.get("selected_retained_path"), put.get("recorded_path")]
    values.extend(put.get("recovered_matches") or [])
    for value in values:
        path = Path(value) if isinstance(value, str) and value else None
        if path and path.is_file():
            return path.resolve()
    return None


def make_target(record: dict[str, Any], root: Path,
                source: Path | None = None,
                put_path: Path | None = None) -> dict[str, Any] | None:
    source = source or target_source(record, root)
    put_path = put_path or selected_put(record)
    if source is None or put_path is None:
        return None
    put_doc = read_json(put_path)
    case = str(record.get("case") or "")
    unit = record.get("unit") or put_doc.get("unit")
    enc = record.get("enc") if record.get("enc") is not None else put_doc.get("enc")
    test = str(record.get("test") or put_doc.get("test") or "")
    path_function = put_doc.get("path_function")
    if not case or path_function is None or unit is None or enc is None or not test:
        return None
    flat = source.parent.parent / "src" / "flat.sol"
    return {
        "identity": list(identity(case, path_function, unit, enc,
                                  put_doc.get("piece"))),
        "case": case,
        "unit": str(unit),
        "enc": str(enc),
        "piece": str(put_doc.get("piece") or ""),
        "test": test,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "flat_source": str(flat) if flat.is_file() else None,
        "flat_source_sha256": sha256_file(flat),
        "put_json": str(put_path),
        "put_json_sha256": sha256_file(put_path),
    }


def canonical_cases(root: Path) -> list[tuple[str, Path, dict[str, Any]]]:
    rows = []
    for result_path in sorted(root.glob("*/subjects/*/result.json")):
        subject = result_path.parent.name
        if not stable_subject(subject):
            continue
        benchmark = result_path.parent.parent.parent.name
        rows.append((f"{benchmark}/{subject}", result_path.parent,
                     read_json(result_path)))
    return rows


def build_targets(root: Path, provenance: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = [json.loads(line) for line in provenance.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    artifacts = [row for row in records if row.get("record_kind") == "artifact"]
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    unresolved: list[dict[str, Any]] = []
    for record in artifacts:
        source = target_source(record, root)
        key = (str(source) if source else "", str(record.get("test") or ""))
        if source is not None and key in seen:
            continue
        target = make_target(record, root, source=source)
        if target is None:
            unresolved.append({"kind": "artifact", "case": record.get("case"),
                               "test": record.get("test"),
                               "reason": "target source or PUT record is not retained"})
            continue
        seen.add((target["source"], target["test"]))
        target["population_source"] = "row-valid-artifact"
        targets.append(target)

    # The row is the normal authority.  Some historical adoptions increased
    # row.put_valid without copying the full artifact list.  Recover only real,
    # green nested artifacts; a bare integer counter cannot create a test unit.
    for case, subject_dir, result in canonical_cases(root):
        row = result.get("row") if isinstance(result.get("row"), dict) else {}
        put = result.get("put") if isinstance(result.get("put"), dict) else {}
        claimed = int(row.get("put_valid") or 0)
        row_values = artifact_values(row)
        if len(row_values) >= claimed:
            continue
        candidates = []
        for item in artifact_values(put):
            if (item.get("forge_status") != "Success"
                    or not item.get("valid_reference_test") or not item.get("b")):
                continue
            source_value = item.get("file")
            put_value = item.get("put_json")
            source = Path(source_value).resolve() if isinstance(source_value, str) else None
            put_path = Path(put_value).resolve() if isinstance(put_value, str) else None
            if not source or not source.is_file() or not put_path or not put_path.is_file():
                continue
            key = (str(source), str(item.get("test") or ""))
            if key in seen:
                continue
            record = {
                "case": case,
                "unit": item.get("unit"),
                "enc": item.get("enc"),
                "test": item.get("test"),
                "put_json": {"selected_retained_path": str(put_path)},
                "final_test": {"selected_retained_path": str(source)},
            }
            target = make_target(record, root, source=source, put_path=put_path)
            if target is not None:
                candidates.append(target)
        needed = max(0, claimed - len(row_values))
        for target in candidates[:needed]:
            key = (target["source"], target["test"])
            seen.add(key)
            target["population_source"] = "nested-green-gap-recovery"
            targets.append(target)
        remaining = needed - min(needed, len(candidates))
        if remaining:
            unresolved.append({
                "kind": "counter-gap", "case": case,
                "claimed_put_valid": claimed,
                "identified_row_tests": len(row_values),
                "recovered_green_tests": min(needed, len(candidates)),
                "missing_test_units": remaining,
                "reason": "put_valid counter has no retained green PUT test unit",
                "subject_dir": str(subject_dir),
            })
    return sorted(targets, key=lambda row: (row["case"], row["source"], row["test"])), unresolved


def concrete_candidates(rq3_root: Path) -> list[dict[str, Any]]:
    rows = []
    for row in load_rq3(rq3_root):
        if (not row.get("is_concrete") or row.get("is_put")
                or not row.get("file_exists") or not row.get("test")):
            continue
        path = Path(str(row.get("file")))
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if test_function(source, str(row["test"])) is not None:
            rows.append(row)
    known = {candidate_key(row) for row in rows}
    for put_path in rq3_root.rglob("put.json"):
        put = read_json(put_path)
        if put.get("kind") != "concrete" or not put.get("unit"):
            continue
        parts = put_path.parts
        indexes = [index for index, value in enumerate(parts) if value == "subjects"]
        if not indexes:
            continue
        subject_index = indexes[-1]
        if subject_index == 0 or subject_index + 1 >= len(parts):
            continue
        case = f"{parts[subject_index - 1]}/{parts[subject_index + 1]}"
        path_function = str(put.get("path_function") or "")
        enc = put.get("enc")
        if not path_function or enc is None:
            continue
        for path in put_path.parent.rglob("*.t.sol"):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for test in re.findall(r"\bfunction\s+(test_cov_[A-Za-z0-9_$]*)\s*\(",
                                   source):
                if test_function(source, test) is None:
                    continue
                key = (str(path.resolve()), test)
                if key in known:
                    continue
                known.add(key)
                rows.append({
                    "identity": list(identity(case, path_function, put["unit"],
                                              enc, put.get("piece"))),
                    "case": case,
                    "path_function": path_function,
                    "unit": str(put["unit"]),
                    "enc": str(enc),
                    "piece": str(put.get("piece") or ""),
                    "file": str(path.resolve()),
                    "file_exists": True,
                    "test": test,
                    "is_concrete": True,
                    "is_put": False,
                    "forge_status": put.get("forge_status"),
                    "valid_reference_test": bool(put.get("valid_reference_test")),
                    "concrete_oracles": (put.get("materialization") or {}).get(
                        "oracle_classes") or [],
                    "physical_recovery": True,
                    "put_json": str(put_path.resolve()),
                })
    # Historical RQ3 Python exceptions sometimes left the adjacent put.json
    # with null unit/path fields even though the emitted concrete test exists.
    # The scheduled job directory still binds case and unit mechanically.
    for path in rq3_root.rglob("*.t.sol"):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        tests = re.findall(r"\bfunction\s+(test_cov_[A-Za-z0-9_$]*)\s*\(", source)
        tests = [test for test in tests if test_function(source, test) is not None]
        if not tests:
            continue
        parts = path.parts
        subject_indexes = [index for index, value in enumerate(parts)
                           if value == "subjects"]
        put_indexes = [index for index, value in enumerate(parts) if value == "put"]
        if not subject_indexes or not put_indexes:
            continue
        subject_index = subject_indexes[-1]
        put_index = next((index for index in put_indexes
                          if index > subject_index + 1), None)
        if put_index is None or put_index + 1 >= len(parts):
            continue
        case = f"{parts[subject_index - 1]}/{parts[subject_index + 1]}"
        job = parts[put_index + 1]
        match = re.search(r"__(.+?)__pf\d+(?:__.*)?$", job)
        unit = match.group(1).split("__")[-1] if match else job
        for test in tests:
            key = (str(path.resolve()), test)
            known.add(key)
            rows.append({
                "identity": list(identity(case, "", unit, "", "")),
                "case": case,
                "path_function": "",
                "unit": unit,
                "contract": path.name.split(".", 1)[0].split("CovTest", 1)[0],
                "enc": "",
                "piece": "",
                "file": str(path.resolve()),
                "file_exists": True,
                "test": test,
                "is_concrete": True,
                "is_put": False,
                "forge_status": None,
                "valid_reference_test": False,
                "concrete_oracles": [],
                "physical_path_recovery": True,
            })
    return rows


def candidate_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(Path(str(row.get("file"))).resolve()), str(row.get("test") or ""))


def published_candidate(row: dict[str, Any], rq3_root: Path) -> bool:
    path = Path(str(row.get("file") or ""))
    try:
        relative = path.resolve().relative_to(rq3_root.resolve())
    except (OSError, ValueError):
        return False
    return bool(relative.parts and relative.parts[0] in ("peer182", "real203", "bugfix124"))


def candidate_score(row: dict[str, Any], target: dict[str, Any],
                    rq3_root: Path) -> tuple[int, int, int, int, int, str]:
    try:
        distance = abs(int(str(row.get("enc"))) - int(target["enc"]))
    except (TypeError, ValueError):
        distance = 1 << 30
    return (
        int(row.get("forge_status") == "Success"),
        int(bool(row.get("valid_reference_test"))),
        int(bool(row.get("concrete_oracles"))),
        int(str(row.get("enc")) == target["enc"]),
        int(published_candidate(row, rq3_root)),
        f"{-distance:012d}:{row.get('file') or ''}:{row.get('test') or ''}",
    )


def contract_name(path_function: str) -> str:
    match = re.search(r"@C@([^@]+)@F@", path_function)
    if match:
        return match.group(1)
    return path_function.split(".", 1)[0] if "." in path_function else ""


def rq3_index(rows: list[dict[str, Any]]) -> tuple[dict, dict, dict, dict, dict]:
    exact: dict[tuple[str, ...], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    path_function: dict[tuple[str, str], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    unit: dict[tuple[str, str], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    global_path: dict[str, dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    global_unit: dict[tuple[str, str], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = tuple(str(value or "") for value in row["identity"])
        exact[key][candidate_key(row)] = row
        path_function[(key[0], key[1])][candidate_key(row)] = row
        unit[(key[0], key[2])][candidate_key(row)] = row
        if key[1]:
            global_path[key[1]][candidate_key(row)] = row
            contract = contract_name(key[1])
            if contract:
                global_unit[(contract, key[2])][candidate_key(row)] = row
        elif row.get("contract"):
            global_unit[(str(row["contract"]), key[2])][candidate_key(row)] = row
    return exact, path_function, unit, global_path, global_unit


def choose_candidate(target: dict[str, Any], indexes: tuple[dict, dict, dict],
                     rq3_root: Path) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    exact, path_function, unit, global_path, global_unit = indexes
    key = tuple(target["identity"])
    contract = contract_name(key[1])
    tiers = (
        ("exact", list(exact.get(key, {}).values())),
        ("same-path-function", list(path_function.get((key[0], key[1]), {}).values())),
        ("same-unit", list(unit.get((key[0], key[2]), {}).values())),
        ("global-path-function", list(global_path.get(key[1], {}).values())),
        ("global-contract-unit", list(global_unit.get((contract, key[2]), {}).values())),
    )
    for tier, rows in tiers:
        if not rows:
            continue
        ranked = sorted(rows, key=lambda row: candidate_score(row, target, rq3_root),
                        reverse=True)
        return tier, ranked, ranked[0]
    return "missing", [], None


def test_function(source: str, name: str) -> str | None:
    span = function_span(source, name)
    if span is None:
        return None
    function = source[span[0]:span[1]]
    signature = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(([^)]*)\)",
                          function)
    if signature is None or signature.group(1).strip():
        return None
    return function


def rename_function(function: str, old: str, new: str) -> str:
    return re.sub(r"(\bfunction\s+)" + re.escape(old) + r"(\s*\()",
                  r"\1" + new + r"\2", function, count=1)


def remove_generated_anchors(source: str) -> str:
    names = re.findall(
        r"\bfunction\s+(test_ce_anchor_(?:auto|rq3)_[A-Za-z0-9_$]*)\s*\(", source)
    spans = []
    for name in names:
        location = function_span(source, name)
        if location is not None:
            spans.append(location)
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + source[end:]
    return source


def contract_insert(source: str, target_test: str, function: str) -> str | None:
    test_span = function_span(source, target_test)
    if test_span is None:
        return None
    owner_end = contract_end(source, target_test)
    if owner_end is None:
        return None
    close = owner_end - 1
    return source[:close].rstrip() + "\n\n  // RQ3 concrete basis anchor.\n  " + function + "\n" + source[close:]


def stage_targets(targets: list[dict[str, Any]], candidates: list[dict[str, Any]],
                  root: Path, rq3_root: Path, staging: Path) -> list[dict[str, Any]]:
    indexes = rq3_index(candidates)
    rows = []
    for target in targets:
        tier, ranked, selected = choose_candidate(target, indexes, rq3_root)
        output = dict(target)
        output.update({
            "mapping_tier": tier,
            "candidate_count": len(ranked),
            "selected_rq3": selected,
        })
        if selected is None:
            output.update(status="refused", reason="no RQ3 concrete test")
            rows.append(output)
            continue
        rq3_source_path = Path(str(selected["file"]))
        rq3_source = rq3_source_path.read_text(encoding="utf-8")
        function = test_function(rq3_source, str(selected["test"]))
        if function is None:
            output.update(status="refused",
                          reason="RQ3 concrete test is absent, ambiguous, or parameterized")
            rows.append(output)
            continue
        source_path = Path(target["source"])
        original = source_path.read_text(encoding="utf-8")
        non_auto = [name for name in re.findall(
            r"\bfunction\s+(test_ce_anchor_[A-Za-z0-9_$]*)\s*\(", original)
                    if not name.startswith(("test_ce_anchor_auto_",
                                            "test_ce_anchor_rq3_"))]
        if len(non_auto) == 1:
            output.update(status="existing-anchor", anchor_test=non_auto[0],
                          reason="one non-generic anchor already exists")
            rows.append(output)
            continue
        if len(non_auto) > 1:
            output.update(status="refused", reason="multiple non-generic anchors exist",
                          existing_anchors=non_auto)
            rows.append(output)
            continue
        anchor = "test_ce_anchor_rq3_" + sha256_bytes(
            "\0".join(target["identity"] + [target["test"]]).encode())[:16]
        function = rename_function(function, str(selected["test"]), anchor)
        cleaned = remove_generated_anchors(original)
        staged = contract_insert(cleaned, target["test"], function)
        if staged is None:
            output.update(status="refused", reason="target test contract is not insertable")
            rows.append(output)
            continue
        relative = source_path.resolve().relative_to(root.resolve())
        staged_path = staging / relative
        staged_path.parent.mkdir(parents=True, exist_ok=True)
        staged_path.write_text(staged, encoding="utf-8")
        output.update({
            "status": "staged",
            "anchor_test": anchor,
            "staged_source": str(staged_path),
            "staged_source_sha256": sha256_file(staged_path),
            "rq3_source_sha256": sha256_file(rq3_source_path),
            "rq3_test_function_sha256": sha256_bytes(function.encode()),
        })
        rows.append(output)
    return rows


def apply_staged(rows: list[dict[str, Any]]) -> int:
    applied = 0
    for row in rows:
        if row.get("status") != "staged":
            continue
        target = Path(row["source"])
        staged = Path(row["staged_source"])
        if sha256_file(target) != row.get("source_sha256"):
            raise RuntimeError(f"source changed after staging: {target}")
        data = staged.read_bytes()
        if sha256_bytes(data) != row.get("staged_source_sha256"):
            raise RuntimeError(f"staged source seal changed: {staged}")
        temporary = target.with_name(target.name + ".rq3-anchor.tmp")
        temporary.write_bytes(data)
        os.chmod(temporary, target.stat().st_mode)
        os.replace(temporary, target)
        row["applied_source_sha256"] = sha256_file(target)
        row["status"] = "applied"
        applied += 1
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq1-root", type=Path, default=DEFAULT_RQ1)
    parser.add_argument("--rq3-root", type=Path, default=DEFAULT_RQ3)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.staging.exists():
        shutil.rmtree(args.staging)
    args.staging.mkdir(parents=True)
    targets, gaps = build_targets(args.rq1_root, args.provenance)
    candidates = concrete_candidates(args.rq3_root)
    rows = stage_targets(targets, candidates, args.rq1_root, args.rq3_root,
                         args.staging)
    applied = apply_staged(rows) if args.apply else 0
    status = Counter(str(row.get("status")) for row in rows)
    tiers = Counter(str(row.get("mapping_tier")) for row in rows)
    report = {
        "schema": "veriput-rq1-rq3-put-anchor-map/v1",
        "rq1_root": str(args.rq1_root.resolve()),
        "rq3_root": str(args.rq3_root.resolve()),
        "provenance": str(args.provenance.resolve()),
        "provenance_sha256": sha256_file(args.provenance),
        "population": {
            "test_units": len(targets),
            "counter_gap_test_units": sum(int(row.get("missing_test_units") or 0)
                                          for row in gaps),
            "policy": "one retained green PUT test function equals one anchor target",
        },
        "counts": {
            "targets": len(rows),
            "rq3_candidates": len(candidates),
            "staged": status["staged"],
            "applied": applied,
            "existing_anchor": status["existing-anchor"],
            "refused": status["refused"],
            "mapping_tiers": dict(sorted(tiers.items())),
        },
        "counter_gaps": gaps,
        "rows": rows,
        "policy": "staging only; no canonical writes and no Forge credit",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
