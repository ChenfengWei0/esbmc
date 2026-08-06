#!/usr/bin/env python3
import csv
import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

from target_manifest import build_manifest  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_bugfix(root):
    base = root / "Datasets" / "Patch-Bug-Bench"
    case = base / "class1" / "case_a"
    case.mkdir(parents=True)
    (case / "bug.flat.sol").write_text("contract C {}\n")
    (case / "fix.flat.sol").write_text("contract C {}\n")
    write_csv(base / "summary.csv", [{
        "id": "case_a",
        "class": "class1",
        "tier": "real",
        "source_dataset": "PoP",
        "target_contract": "C",
        "changed_functions": "setX;getX",
        "modification_kind": "control_flow",
        "bug_solc": "0.8.29",
        "fix_solc": "0.8.29",
        "bug": "Datasets/Patch-Bug-Bench/class1/case_a/bug.flat.sol",
        "fix": "Datasets/Patch-Bug-Bench/class1/case_a/fix.flat.sol",
    }])


def make_stress(root):
    base = root / "Datasets" / "Stress-Projects"
    src = base / "org__repo" / "src" / "C.sol"
    src.parent.mkdir(parents=True)
    src.write_text("contract C {}\n")
    src2 = base / "org__repo" / "src" / "Readonly.sol"
    src2.write_text("contract Readonly {}\n")
    write_csv(base / "TARGETS.csv", [
        {
            "repo": "org/repo",
            "contract": "C",
            "path": "src/C.sol",
            "named_entry_points": "2",
            "public_state_vars": "0",
            "storage_vars": "1",
            "immutable_vars": "0",
            "constant_vars": "0",
            "writing_entry_points": "1",
            "state_class": "STATEFUL",
            "unresolved_bases": "",
            "include": "yes",
            "test_frameworks": "forge",
            "test_files_forge": "1",
            "test_files_hardhat": "0",
            "referenced_by_dev_tests": "yes",
        },
        {
            "repo": "org/repo",
            "contract": "Readonly",
            "path": "src/Readonly.sol",
            "named_entry_points": "1",
            "public_state_vars": "0",
            "storage_vars": "0",
            "immutable_vars": "1",
            "constant_vars": "0",
            "writing_entry_points": "0",
            "state_class": "CONFIG_ONLY",
            "unresolved_bases": "",
            "include": "yes",
            "test_frameworks": "-",
            "test_files_forge": "0",
            "test_files_hardhat": "0",
            "referenced_by_dev_tests": "no",
        },
    ])


def make_peer(root):
    d = root / "Results" / "Peer182" / "subjects" / "peer_tool__Thing"
    d.mkdir(parents=True)
    (d / "flat.sol").write_text("contract Thing {}\n")
    (d / "meta.json").write_text(json.dumps({
        "subject_id": "peer_tool__Thing",
        "status": "ok",
        "contract": "Thing",
        "peer_tool": "Tool",
        "peer_arm": "tool",
        "source_file": "Tool/contracts_080/Thing.sol",
        "target_rule": "file-stem",
        "target_alternatives": ["Thing"],
        "source_080": True,
        "has_assert": False,
    }) + "\n")
    old = root / "Results" / "Peer182" / "subjects" / "peer_tool__OldThing"
    old.mkdir(parents=True)
    (old / "flat.sol").write_text("contract OldThing {}\n")
    (old / "meta.json").write_text(json.dumps({
        "subject_id": "peer_tool__OldThing",
        "status": "ok",
        "contract": "OldThing",
        "peer_tool": "Tool",
        "peer_arm": "tool",
        "source_file": "Tool/contracts/OldThing.sol",
        "target_rule": "file-stem",
        "target_alternatives": ["OldThing"],
        "source_080": False,
        "has_assert": False,
    }) + "\n")


def test_manifest_from_three_target_sources():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_bugfix(root)
        make_stress(root)
        make_peer(root)
        doc = build_manifest(
            root,
            ["peer182", "bugfix124", "stress243"],
            "include")
    bad = 0
    bad += check(doc["schema"] == "veriput-eval/target/v1",
                 f"schema is stable: {doc['schema']}")
    bad += check(doc["summary"]["ok"] == 4,
                 f"all fixture targets are ok: {doc['summary']}")
    bad += check(doc["summary"]["skipped"] == 1,
                 f"non-080 peer subject is skipped: {doc['summary']}")
    rows = {(r["benchmark"], r["subject_id"]): r for r in doc["targets"]}
    bad += check(rows[("bugfix124", "case_a")]["contract"] == "C",
                 "bugfix target contract is preserved")
    bad += check(rows[("bugfix124", "case_a")]["units_hint"] ==
                 ["setX", "getX"],
                 "bugfix changed functions become unit hints")
    bad += check(rows[("stress243", "org__repo__C")]["metadata"]
                 ["state_class"] == "STATEFUL",
                 "stress state class is recorded")
    bad += check(rows[("peer182", "peer_tool__Thing")]["metadata"]
                 ["target_rule"] == "file-stem",
                 "peer target rule is retained")
    return bad


def test_stress_stateful_scope_and_alias():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        make_stress(root)
        doc = build_manifest(root, ["stress203"], "stateful")
    bad = 0
    bad += check(doc["benchmarks"] == ["stress243"],
                 f"stress203 aliases to current prepared key: {doc['benchmarks']}")
    bad += check(doc["summary"]["ok"] == 1,
                 f"stateful stress scope drops config-only rows: {doc['summary']}")
    bad += check(doc["targets"][0]["contract"] == "C",
                 f"stateful target remains: {doc['targets']}")
    return bad


def main():
    tests = [
        test_manifest_from_three_target_sources,
        test_stress_stateful_scope_and_alias,
    ]
    bad = 0
    for test in tests:
        print("---", test.__name__)
        bad += test()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
