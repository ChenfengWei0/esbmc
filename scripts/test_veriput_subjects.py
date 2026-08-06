#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

from veriput_subjects import (SubjectError, enumerate_subject_units,  # noqa: E402
                              manifest_for_subject, resolve_subject,
                              subject_from_record, unit_manifest)


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def make_subject(root, sid="repo__C", **meta_overrides):
    d = Path(root) / sid
    d.mkdir(parents=True)
    (d / "flat.sol").write_text("contract C { function f() public {} }\n")
    (d / "flat.sol.solast").write_text("{}\n")
    meta = {
        "subject_id": sid,
        "benchmark": "stress243",
        "contract": "C",
        "status": "ok",
        "solc_bin": "/bin/false",
    }
    meta.update(meta_overrides)
    (d / "meta.json").write_text(json.dumps(meta) + "\n")
    return d


def compact_ast():
    return {
        "nodeType": "SourceUnit",
        "nodes": [
            {
                "nodeType": "ContractDefinition",
                "id": 1,
                "name": "Base",
                "contractKind": "contract",
                "linearizedBaseContracts": [1],
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "baseOnly",
                        "visibility": "public",
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "hidden",
                        "visibility": "internal",
                    },
                    {
                        "nodeType": "VariableDeclaration",
                        "name": "stored",
                        "visibility": "public",
                    },
                ],
            },
            {
                "nodeType": "ContractDefinition",
                "id": 2,
                "name": "C",
                "contractKind": "contract",
                "linearizedBaseContracts": [2, 1],
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "own",
                        "visibility": "external",
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "baseOnly",
                        "visibility": "public",
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "receive",
                        "name": "",
                        "visibility": "external",
                    },
                ],
            },
        ],
    }


def make_fake_solc(path, body):
    p = Path(path)
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(0o755)
    return str(p)


def test_resolve_subject_from_root_and_unit():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, "repo__C")
        subject = resolve_subject("repo__C", root=td, unit="f")
    bad = 0
    bad += check(subject.benchmark == "stress243",
                 f"benchmark is read from meta.json: {subject.benchmark}")
    bad += check(subject.subject_id == "repo__C",
                 f"subject id is stable: {subject.subject_id}")
    bad += check(subject.contract == "C" and subject.unit == "f",
                 f"contract/unit resolved: {subject.contract}.{subject.unit}")
    bad += check(subject.flat_sol.endswith("/repo__C/flat.sol"),
                 f"flat path points at prepared source: {subject.flat_sol}")
    bad += check(subject.solast.endswith("/repo__C/flat.sol.solast"),
                 f"AST path uses prepared flat naming: {subject.solast}")
    return bad


def test_resolve_subject_requires_explicit_unit():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td)
        try:
            resolve_subject("repo__C", root=td)
        except SubjectError as exc:
            return check("explicit --unit" in str(exc),
                         f"missing unit fails closed: {exc}")
    print("FAIL: missing unit was accepted")
    return 1


def test_subject_from_cert_record_round_trips():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td)
        subject = resolve_subject("repo__C", root=td, unit="f")
        row = {"subject": subject.to_record()}
        restored = subject_from_record(row)
    bad = 0
    bad += check(restored is not None, "subject block is recognized")
    bad += check(restored.to_record() == subject.to_record(),
                 "cert row subject block round-trips")
    return bad


def test_bad_status_is_not_usable():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, status="compile-failed")
        try:
            resolve_subject("repo__C", root=td, unit="f")
        except SubjectError as exc:
            return check("status='compile-failed'" in str(exc),
                         f"bad prepared subject status refused: {exc}")
    print("FAIL: compile-failed subject was accepted")
    return 1


def test_ast_unit_enumeration_is_target_contract_scoped():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td)
        (d / "flat.sol.solast").write_text(
            "JSON AST (compact format):\n\n======= flat.sol =======\n"
            + json.dumps(compact_ast()) + "\n")
        subject = resolve_subject("repo__C", root=td, unit="own")
        enum = enumerate_subject_units(subject)
    bad = 0
    bad += check(enum.units == ("own", "baseOnly"),
                 f"target and inherited public/external units: {enum.units}")
    bad += check(not any(u == "hidden" for u in enum.units),
                 "internal inherited function is excluded")
    bad += check(sum(1 for u in enum.units if u == "baseOnly") == 1,
                 "override/base duplicate unit name is emitted once")
    reasons = {row["kind"]: row["reason"] for row in enum.skipped}
    bad += check(reasons.get("receive") ==
                 "fallback/receive has no named focus-function",
                 f"receive is skipped with a reason: {enum.skipped}")
    bad += check(reasons.get("public-state-getter") ==
                 "public state getter is not a FunctionDefinition",
                 f"public getter is skipped with a reason: {enum.skipped}")
    return bad


def test_unit_manifest_records_missing_ast_without_solc():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td, "repo__C")
        (d / "flat.sol.solast").unlink()
        subject = resolve_subject("repo__C", root=td, require_unit=False)
        manifest = unit_manifest("stress243", [subject], generate_ast=False)
    bad = 0
    bad += check(manifest["schema"] == "veriput-unit-manifest/v1",
                 f"manifest schema is stable: {manifest['schema']}")
    bad += check(manifest["summary"]["subjects"] == 1,
                 f"one subject counted: {manifest['summary']}")
    bad += check(manifest["summary"]["missing_ast"] == 1,
                 f"missing AST is counted: {manifest['summary']}")
    bad += check(manifest["subjects"][0]["status"] == "missing-ast",
                 f"row records missing AST: {manifest['subjects'][0]}")
    return bad


def test_generate_ast_is_atomic_on_success():
    with tempfile.TemporaryDirectory() as td:
        script = make_fake_solc(
            Path(td) / "solc-ok",
            "cat <<'JSON'\n" + json.dumps(compact_ast()) + "\nJSON\n")
        d = make_subject(td, "repo__C", solc_bin=script)
        (d / "flat.sol.solast").unlink()
        subject = resolve_subject("repo__C", root=td, require_unit=False)
        row = manifest_for_subject(
            subject,
            generate_ast=True,
            ast_timeout_s=5)
    bad = 0
    bad += check(row["status"] == "ok",
                 f"generated AST row is ok: {row}")
    bad += check(row["ast_generated"] is True,
                 f"legacy ast_generated flag is true: {row.get('ast')}")
    bad += check(row["ast"]["generated"] is True,
                 f"structured AST metadata says generated: {row['ast']}")
    bad += check(row["units"]["units"] == ["own", "baseOnly"],
                 f"generated AST is enumerated: {row.get('units')}")
    return bad


def test_generate_ast_failure_leaves_no_partial_solast():
    with tempfile.TemporaryDirectory() as td:
        script = make_fake_solc(
            Path(td) / "solc-fail",
            "printf '{\"partial\":'\nexit 2\n")
        d = make_subject(td, "repo__C", solc_bin=script)
        ast = d / "flat.sol.solast"
        ast.unlink()
        subject = resolve_subject("repo__C", root=td, require_unit=False)
        row = manifest_for_subject(
            subject,
            generate_ast=True,
            ast_timeout_s=5)
        tmp_left = list(d.glob("*.tmp.*"))
        exists = ast.exists()
    bad = 0
    bad += check(row["status"] == "error",
                 f"failed solc is an error row: {row}")
    bad += check("rc=2" in row["reason"],
                 f"return code is recorded: {row['reason']}")
    bad += check(not exists,
                 "failed solc did not leave flat.sol.solast")
    bad += check(not tmp_left,
                 f"failed solc cleaned temp files: {tmp_left}")
    return bad


def test_generate_ast_start_failure_cleans_temp_file():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(
            td,
            "repo__C",
            solc_bin=str(Path(td) / "missing-solc"))
        ast = d / "flat.sol.solast"
        ast.unlink()
        subject = resolve_subject("repo__C", root=td, require_unit=False)
        row = manifest_for_subject(
            subject,
            generate_ast=True,
            ast_timeout_s=5)
        tmp_left = list(d.glob("*.tmp.*"))
        exists = ast.exists()
    bad = 0
    bad += check(row["status"] == "error",
                 f"missing solc is an error row: {row}")
    bad += check("could not start" in row["reason"],
                 f"start failure is explicit: {row['reason']}")
    bad += check(not exists,
                 "missing solc did not leave flat.sol.solast")
    bad += check(not tmp_left,
                 f"missing solc cleaned temp files: {tmp_left}")
    return bad


def test_unit_manifest_cli_lists_units_without_esbmc():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td, "repo__C")
        (d / "flat.sol.solast").write_text(json.dumps(compact_ast()) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
            "--subject-id", "repo__C",
        ], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    bad = 0
    bad += check(data["summary"]["ok"] == 1,
                 f"CLI manifest has one ok subject: {data['summary']}")
    units = data["subjects"][0]["units"]["units"]
    bad += check(units == ["own", "baseOnly"],
                 f"CLI manifest carries units: {units}")
    return bad


def test_unit_manifest_cli_shard_and_resume():
    with tempfile.TemporaryDirectory() as td:
        for sid in ("s0", "s1", "s2", "s3"):
            d = make_subject(td, sid)
            (d / "flat.sol.solast").write_text(json.dumps(compact_ast()) + "\n")
        journal = Path(td) / "manifest.jsonl"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
            "--shard", "1/2",
            "--journal", str(journal),
        ], capture_output=True, text=True)
        if cp.returncode:
            print(cp.stdout)
            print(cp.stderr)
            return 1
        first = json.loads(cp.stdout)
        cp2 = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
            "--shard", "1/2",
            "--resume-journal", str(journal),
        ], capture_output=True, text=True)
    if cp2.returncode:
        print(cp2.stdout)
        print(cp2.stderr)
        return 1
    second = json.loads(cp2.stdout)
    bad = 0
    got_ids = [row["subject"]["subject_id"] for row in first["subjects"]]
    bad += check(got_ids == ["s1", "s3"],
                 f"shard 1/2 selects sorted odd positions: {got_ids}")
    bad += check(first["summary"]["ok"] == 2,
                 f"first shard processed two ok subjects: {first['summary']}")
    bad += check(second["summary"]["subjects"] == 0,
                 f"resume emits no already-ok rows: {second['summary']}")
    bad += check(second["summary"]["skipped_resume"] == 2,
                 f"resume counts skipped ok rows: {second['summary']}")
    return bad


def main():
    tests = [
        test_resolve_subject_from_root_and_unit,
        test_resolve_subject_requires_explicit_unit,
        test_subject_from_cert_record_round_trips,
        test_bad_status_is_not_usable,
        test_ast_unit_enumeration_is_target_contract_scoped,
        test_unit_manifest_records_missing_ast_without_solc,
        test_generate_ast_is_atomic_on_success,
        test_generate_ast_failure_leaves_no_partial_solast,
        test_generate_ast_start_failure_cleans_temp_file,
        test_unit_manifest_cli_lists_units_without_esbmc,
        test_unit_manifest_cli_shard_and_resume,
    ]
    bad = 0
    for test in tests:
        print("---", test.__name__)
        bad += test()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
