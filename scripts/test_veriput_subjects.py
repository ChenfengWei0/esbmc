#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import veriput_subjects  # noqa: E402
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
                        "stateMutability": "view",
                        "parameters": {"parameters": []},
                        "returnParameters": {"parameters": [
                            {"nodeType": "VariableDeclaration"},
                        ]},
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
                "id": 3,
                "name": "Iface",
                "contractKind": "interface",
                "linearizedBaseContracts": [3],
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "ifaceValue",
                        "visibility": "external",
                        "stateMutability": "view",
                        "implemented": False,
                        "parameters": {"parameters": []},
                        "returnParameters": {"parameters": [
                            {"nodeType": "VariableDeclaration"},
                        ]},
                    },
                ],
            },
            {
                "nodeType": "ContractDefinition",
                "id": 2,
                "name": "C",
                "contractKind": "contract",
                "linearizedBaseContracts": [2, 3, 1],
                "nodes": [
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "own",
                        "visibility": "external",
                        "stateMutability": "nonpayable",
                        "parameters": {"parameters": [
                            {"nodeType": "VariableDeclaration"},
                        ]},
                        "returnParameters": {"parameters": []},
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "function",
                        "name": "baseOnly",
                        "visibility": "public",
                        "stateMutability": "pure",
                        "parameters": {"parameters": []},
                        "returnParameters": {"parameters": [
                            {"nodeType": "VariableDeclaration"},
                        ]},
                    },
                    {
                        "nodeType": "FunctionDefinition",
                        "kind": "receive",
                        "name": "",
                        "visibility": "external",
                    },
                    {
                        "nodeType": "VariableDeclaration",
                        "name": "ifaceValue",
                        "visibility": "public",
                    },
                ],
            },
        ],
    }


def make_fake_solc(path, body):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
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


def test_resolve_subject_accepts_cleaned_result_dir_alias():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, "peer__C__1", subject_id="peer__C (1)")
        subject = resolve_subject("peer__C (1)", root=td, unit="f")
    bad = 0
    bad += check(subject.subject_id == "peer__C (1)",
                 f"recorded subject id is preserved: {subject.subject_id}")
    bad += check(subject.root.endswith("/peer__C__1"),
                 f"cleaned result dir alias resolves: {subject.root}")
    return bad


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


def test_subject_record_rehomes_veriput_root_paths():
    old_root = veriput_subjects.VERIPUT_ROOT
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subject_root = root / "Results" / "Peer182" / "subjects"
        d = make_subject(subject_root, "peer__C", benchmark="peer182")
        veriput_subjects.VERIPUT_ROOT = root
        try:
            record = {
                "schema": "veriput-subject/v1",
                "benchmark": "peer182",
                "subject_id": "peer__C",
                "benchmark_key": "peer182__peer__C",
                "root": "/home/samson/workspace/VeriPUT/Results/Peer182/subjects/peer__C",
                "flat_sol": "/home/samson/workspace/VeriPUT/Results/Peer182/subjects/peer__C/flat.sol",
                "solast": "/home/samson/workspace/VeriPUT/Results/Peer182/subjects/peer__C/flat.sol.solast",
                "contract": "C",
                "unit": "f",
                "solc_bin": "/bin/false",
                "solc_extra": [],
                "meta_status": "ok",
            }
            restored = subject_from_record({"subject": record})
        finally:
            veriput_subjects.VERIPUT_ROOT = old_root
    bad = 0
    bad += check(restored.root == str(d.resolve()),
                 f"record root is rehomed: {restored.root}")
    bad += check(restored.flat_sol == str((d / "flat.sol").resolve()),
                 f"record flat.sol is rehomed: {restored.flat_sol}")
    return bad


def test_subject_record_preserves_inferred_solc_bin():
    with tempfile.TemporaryDirectory() as td:
        solc = str(Path(td) / "toolchain" / "solc-0.8.17")
        make_fake_solc(solc, "exit 0\n")
        make_subject(
            td,
            "repo__C",
            solc_bin=None,
            compile={"cmd": f"{solc} --bin flat.sol"})
        subject = resolve_subject("repo__C", root=td, unit="f")
        record = subject.to_record()
        restored = subject_from_record({"subject": record})
    bad = 0
    bad += check(record["solc"] is None,
                 f"compile-only inference does not invent solc version: {record}")
    bad += check(record["solc_bin_source"] == "inferred",
                 f"solc source is retained: {record}")
    bad += check(record["inferred_solc_bin"] == solc,
                 f"compile command solc is inferred: {record}")
    promoted = subject.with_inferred_solc_bin().to_record()
    bad += check(promoted["solc_bin"] == solc,
                 "inferred solc can be promoted explicitly")
    bad += check(promoted["solc_bin_source"] == "inferred"
                 and promoted["inferred_solc_bin"] == solc,
                 f"promoted inferred solc keeps provenance: {promoted}")
    bad += check(restored.to_record() == record,
                 "inferred solc survives manifest round-trip")
    return bad


def test_resolve_subject_rehomes_missing_solc_select_binary():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        local_solc = home / ".solc-select" / "artifacts" / "solc-0.8.29" / "solc-0.8.29"
        make_fake_solc(local_solc, "exit 0\n")
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        try:
            make_subject(
                td,
                "repo__C",
                solc_bin="/home/olduser/.solc-select/artifacts/solc-0.8.29/solc-0.8.29")
            subject = resolve_subject("repo__C", root=td, unit="f")
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
    return check(subject.solc_bin == str(local_solc),
                 f"missing solc-select path is rehomed: {subject.solc_bin}")


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


def test_resolve_subject_uses_bugfix_fallback_root():
    old_primary = veriput_subjects.KNOWN_SUBJECT_ROOTS["bugfix124"]
    old_fallback = veriput_subjects.FALLBACK_SUBJECT_ROOTS.get("bugfix124", ())
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        primary = root / "Results" / "BugFix124" / "subjects"
        fallback = root / "scripts" / "Results" / "workdirs" / "BugFix124" / "subjects"
        make_subject(fallback, "bugfix__C", benchmark="bugfix124")
        veriput_subjects.KNOWN_SUBJECT_ROOTS["bugfix124"] = primary
        veriput_subjects.FALLBACK_SUBJECT_ROOTS["bugfix124"] = (fallback,)
        try:
            subject = resolve_subject(
                "bugfix__C", benchmark="bugfix124", unit="f")
            dirs = veriput_subjects.subject_dirs("bugfix124")
        finally:
            veriput_subjects.KNOWN_SUBJECT_ROOTS["bugfix124"] = old_primary
            veriput_subjects.FALLBACK_SUBJECT_ROOTS["bugfix124"] = old_fallback
    bad = 0
    bad += check(subject.root == str((fallback / "bugfix__C").resolve()),
                 f"bugfix fallback root resolves subject: {subject.root}")
    bad += check([p.name for p in dirs] == ["bugfix__C"],
                 f"bugfix fallback root is scanned: {dirs}")
    return bad


def test_resolve_subject_uses_bugfix_dataset_fix_source():
    old_primary = veriput_subjects.KNOWN_SUBJECT_ROOTS["bugfix124"]
    old_fallback = veriput_subjects.FALLBACK_SUBJECT_ROOTS.get("bugfix124", ())
    old_dataset = veriput_subjects.BUGFIX_DATASET_ROOT
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        primary = root / "Results" / "BugFix124" / "subjects"
        dataset = root / "Datasets" / "Patch-Bug-Bench"
        subject_dir = dataset / "class1_RealBug-RealRepair" / "pop_066_LRTDepositPool"
        subject_dir.mkdir(parents=True)
        (subject_dir / "fix.flat.sol").write_text(
            "contract LRTDepositPool { function depositAsset() public {} }\n")
        (subject_dir / "fix.flat.sol.solast").write_text("{}\n")
        (subject_dir / "meta.json").write_text(json.dumps({
            "id": "pop_066_LRTDepositPool",
            "target_contract": "LRTDepositPool",
            "solc_version": {"fix": "0.8.29"},
            "changed_functions": ["depositAsset"],
        }) + "\n")
        veriput_subjects.KNOWN_SUBJECT_ROOTS["bugfix124"] = primary
        veriput_subjects.FALLBACK_SUBJECT_ROOTS["bugfix124"] = ()
        veriput_subjects.BUGFIX_DATASET_ROOT = dataset
        try:
            subject = resolve_subject(
                "pop_066_LRTDepositPool", benchmark="bugfix124", unit="depositAsset")
            dirs = veriput_subjects.subject_dirs("bugfix124")
        finally:
            veriput_subjects.KNOWN_SUBJECT_ROOTS["bugfix124"] = old_primary
            veriput_subjects.FALLBACK_SUBJECT_ROOTS["bugfix124"] = old_fallback
            veriput_subjects.BUGFIX_DATASET_ROOT = old_dataset
    bad = 0
    bad += check(subject.contract == "LRTDepositPool",
                 f"dataset target_contract resolves: {subject.contract}")
    bad += check(subject.flat_sol.endswith("/pop_066_LRTDepositPool/fix.flat.sol"),
                 f"dataset fix source is used as reference: {subject.flat_sol}")
    bad += check(subject.metadata.get("source_layout") ==
                 "patch-bug-bench-dataset",
                 f"dataset provenance is recorded: {subject.metadata}")
    bad += check([p.name for p in dirs] == ["pop_066_LRTDepositPool"],
                 f"bugfix dataset root is scanned: {dirs}")
    return bad


def test_resolve_subject_prefers_primary_when_benchmark_is_known():
    old_primary = veriput_subjects.KNOWN_SUBJECT_ROOTS["peer182"]
    old_fallback = veriput_subjects.FALLBACK_SUBJECT_ROOTS.get("peer182", ())
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        primary = root / "Results" / "Peer182" / "subjects"
        fallback = root / "scripts" / "Results" / "workdirs" / "Peer182" / "subjects"
        make_subject(primary, "peer__C", benchmark="peer182", contract="PrimaryC")
        make_subject(fallback, "peer__C", benchmark="peer182", contract="FallbackC")
        veriput_subjects.KNOWN_SUBJECT_ROOTS["peer182"] = primary
        veriput_subjects.FALLBACK_SUBJECT_ROOTS["peer182"] = (fallback,)
        try:
            subject = resolve_subject(
                "peer__C", benchmark="peer182", unit="f")
        finally:
            veriput_subjects.KNOWN_SUBJECT_ROOTS["peer182"] = old_primary
            veriput_subjects.FALLBACK_SUBJECT_ROOTS["peer182"] = old_fallback
    bad = 0
    bad += check(subject.contract == "PrimaryC",
                 f"benchmark-scoped lookup prefers primary root: {subject.root}")
    bad += check(subject.root == str((primary / "peer__C").resolve()),
                 f"primary root path selected: {subject.root}")
    return bad


def test_resolve_subject_uses_peer_fallback_root():
    old_primary = veriput_subjects.KNOWN_SUBJECT_ROOTS["peer182"]
    old_fallback = veriput_subjects.FALLBACK_SUBJECT_ROOTS.get("peer182", ())
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        primary = root / "Results" / "Peer182" / "subjects"
        fallback = root / "scripts" / "Results" / "workdirs" / "Peer182" / "subjects"
        make_subject(fallback, "peer__C", benchmark="peer182")
        veriput_subjects.KNOWN_SUBJECT_ROOTS["peer182"] = primary
        veriput_subjects.FALLBACK_SUBJECT_ROOTS["peer182"] = (fallback,)
        try:
            subject = resolve_subject(
                "peer__C", benchmark="peer182", unit="f")
            dirs = veriput_subjects.subject_dirs("peer182")
        finally:
            veriput_subjects.KNOWN_SUBJECT_ROOTS["peer182"] = old_primary
            veriput_subjects.FALLBACK_SUBJECT_ROOTS["peer182"] = old_fallback
    bad = 0
    bad += check(subject.root == str((fallback / "peer__C").resolve()),
                 f"peer fallback root resolves subject: {subject.root}")
    bad += check([p.name for p in dirs] == ["peer__C"],
                 f"peer fallback root is scanned: {dirs}")
    return bad


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
    bad += check(not any(u == "ifaceValue" for u in enum.units),
                 "unimplemented inherited interface declaration is excluded")
    bad += check(sum(1 for u in enum.units if u == "baseOnly") == 1,
                 "override/base duplicate unit name is emitted once")
    info = {row["name"]: row for row in enum.unit_info}
    bad += check(info["own"]["state_mutability"] == "nonpayable"
                 and info["own"]["parameter_count"] == 1
                 and info["own"]["return_count"] == 0,
                 f"target unit metadata is retained: {info}")
    bad += check(info["baseOnly"]["state_mutability"] == "pure"
                 and info["baseOnly"]["return_count"] == 1,
                 f"inherited override metadata is retained: {info}")
    reasons = {row["kind"]: row["reason"] for row in enum.skipped}
    bad += check(reasons.get("receive") ==
                 "fallback/receive has no named focus-function",
                 f"receive is skipped with a reason: {enum.skipped}")
    bad += check(reasons.get("public-state-getter") ==
                 "public state getter is not a FunctionDefinition",
                 f"public getter is skipped with a reason: {enum.skipped}")
    bad += check(reasons.get("unimplemented-function") ==
                 "public/external declaration has no FunctionDefinition body",
                 f"unimplemented inherited declaration is skipped: {enum.skipped}")
    return bad


def test_no_unit_enumeration_records_auditable_reasons():
    ast = {
        "nodeType": "SourceUnit",
        "nodes": [{
            "nodeType": "ContractDefinition",
            "id": 1,
            "name": "LibOnly",
            "contractKind": "library",
            "linearizedBaseContracts": [1],
            "nodes": [{
                "nodeType": "FunctionDefinition",
                "kind": "function",
                "name": "changed",
                "visibility": "internal",
                "implemented": True,
                "stateMutability": "nonpayable",
                "parameters": {"parameters": []},
                "returnParameters": {"parameters": []},
            }],
        }],
    }
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td, contract="LibOnly")
        (d / "flat.sol.solast").write_text(json.dumps(ast) + "\n")
        subject = resolve_subject("repo__C", root=td, require_unit=False)
        enum = enumerate_subject_units(subject)
        record = enum.to_record()
    reasons = {row["kind"]: row["reason"] for row in enum.skipped}
    bad = 0
    bad += check(enum.units == (), f"library-only target has no units: {enum}")
    bad += check(reasons.get("library-contract") ==
                 "library target has no externally callable unit",
                 f"library target reason is retained: {enum.skipped}")
    bad += check(reasons.get("non-public-function") ==
                 "function is not public/external",
                 f"internal changed function is retained: {enum.skipped}")
    bad += check(record["schedulable"] is False
                 and "no public/external" in record["no_unit_reason"],
                 f"serialized record carries no-unit status: {record}")
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


def test_generate_solast_uses_inferred_solc_bin_directly():
    with tempfile.TemporaryDirectory() as td:
        script = make_fake_solc(
            Path(td) / "solc-0.8.17",
            "cat <<'JSON'\n" + json.dumps(compact_ast()) + "\nJSON\n")
        d = make_subject(
            td,
            "repo__C",
            solc_bin=None,
            compile={"cmd": f"{script} --bin flat.sol"})
        (d / "flat.sol.solast").unlink()
        subject = resolve_subject("repo__C", root=td, require_unit=False)
        row = manifest_for_subject(subject, generate_ast=True)
    bad = 0
    bad += check(row["status"] == "ok",
                 f"direct manifest generation uses inferred solc: {row}")
    bad += check(row["subject"]["solc_bin_source"] == "inferred"
                 and row["subject"]["solc_bin"] == script,
                 f"inferred solc provenance is serialized: {row['subject']}")
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


def test_unit_manifest_cli_generates_ast_with_inferred_solc():
    with tempfile.TemporaryDirectory() as td:
        script = make_fake_solc(
            Path(td) / "solc-0.8.17",
            "cat <<'JSON'\n" + json.dumps(compact_ast()) + "\nJSON\n")
        d = make_subject(
            td,
            "repo__C",
            solc_bin=None,
            compile={"cmd": f"{script} --bin flat.sol"})
        (d / "flat.sol.solast").unlink()
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
            "--subject-id", "repo__C",
            "--generate-ast",
            "--use-inferred-solc-bin",
        ], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    row = data["subjects"][0]
    bad = 0
    bad += check(row["status"] == "ok",
                 f"inferred solc generated an AST: {row}")
    bad += check(row["subject"]["solc_bin"] == script,
                 f"inferred solc was promoted for this run: {row['subject']}")
    bad += check(row["subject"]["solc_bin_source"] == "inferred"
                 and row["subject"]["inferred_solc_bin"] == script,
                 f"promoted solc provenance is explicit: {row['subject']}")
    return bad


def test_unit_manifest_cli_reads_ast_cache_without_touching_subject():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td, "repo__C")
        prepared_ast = d / "flat.sol.solast"
        prepared_ast.unlink()
        cache = Path(td) / "cache"
        cached_ast = cache / "stress243" / "stress243__repo__C" \
            / "flat.sol.solast"
        cached_ast.parent.mkdir(parents=True)
        cached_ast.write_text(json.dumps(compact_ast()) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
            "--subject-id", "repo__C",
            "--ast-cache-root", str(cache),
        ], capture_output=True, text=True)
        prepared_exists = prepared_ast.exists()
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    row = data["subjects"][0]
    bad = 0
    bad += check(row["status"] == "ok",
                 f"cached AST enumerates units: {row}")
    bad += check(row["subject"]["solast"] == str(cached_ast.resolve()),
                 f"subject points at cache AST: {row['subject']}")
    bad += check(row["subject"]["solast_source"] == "cache",
                 f"cache provenance is recorded: {row['subject']}")
    bad += check(not prepared_exists,
                 "prepared subject AST was not recreated")
    return bad


def test_unit_manifest_cli_generates_ast_into_cache_only():
    with tempfile.TemporaryDirectory() as td:
        script = make_fake_solc(
            Path(td) / "solc-ok",
            "cat <<'JSON'\n" + json.dumps(compact_ast()) + "\nJSON\n")
        d = make_subject(td, "repo__C", solc_bin=script)
        prepared_ast = d / "flat.sol.solast"
        prepared_ast.unlink()
        cache = Path(td) / "cache"
        cached_ast = cache / "stress243" / "stress243__repo__C" \
            / "flat.sol.solast"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
            "--subject-id", "repo__C",
            "--ast-cache-root", str(cache),
            "--generate-ast",
        ], capture_output=True, text=True)
        prepared_exists = prepared_ast.exists()
        cached_exists = cached_ast.exists()
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    row = data["subjects"][0]
    bad = 0
    bad += check(row["status"] == "ok",
                 f"cache AST generation row is ok: {row}")
    bad += check(row["ast"]["generated"] is True,
                 f"cache AST was generated: {row['ast']}")
    bad += check(cached_exists, "cache AST file was created")
    bad += check(not prepared_exists,
                 "prepared subject AST was not written")
    return bad


def test_unit_manifest_cli_refuses_real_results_ast_writes():
    with tempfile.TemporaryDirectory() as td:
        veriput_root = Path(td) / "VeriPUT"
        subjects = veriput_root / "Results" / "Stress243" / "subjects"
        script = make_fake_solc(
            Path(td) / "solc-ok",
            "cat <<'JSON'\n" + json.dumps(compact_ast()) + "\nJSON\n")
        d = make_subject(subjects, "repo__C", solc_bin=script)
        (d / "flat.sol.solast").unlink()
        base_cmd = [
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", str(subjects),
            "--subject-id", "repo__C",
            "--generate-ast",
        ]
        env = dict(os.environ)
        env["VERIPUT_ROOT"] = str(veriput_root)
        no_cache = subprocess.run(base_cmd, capture_output=True, text=True, env=env)
        results_cache = subprocess.run(
            base_cmd + ["--ast-cache-root", str(veriput_root / "Results" / "cache")],
            capture_output=True,
            text=True,
            env=env)
        ast_exists = (d / "flat.sol.solast").exists()
    bad = 0
    bad += check(no_cache.returncode == 1
                 and "requires external --ast-cache-root" in no_cache.stderr,
                 f"real Results subject generation without cache is refused: "
                 f"{no_cache.stderr.strip()}")
    bad += check(results_cache.returncode == 1
                 and "--ast-cache-root must not be under" in results_cache.stderr,
                 f"Results-local AST cache is refused: {results_cache.stderr.strip()}")
    bad += check(not ast_exists,
                 "refused real Results generation did not create a prepared AST")
    return bad


def test_unit_manifest_cli_refuses_protected_report_outputs():
    with tempfile.TemporaryDirectory() as td:
        veriput_root = Path(td) / "VeriPUT"
        root = Path(td) / "subjects"
        make_subject(root, "repo__C", with_ast=True)
        base_cmd = [
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", str(root),
            "--subject-id", "repo__C",
        ]
        env = dict(os.environ)
        env["VERIPUT_ROOT"] = str(veriput_root)
        out = subprocess.run(
            base_cmd + ["--out", str(veriput_root / "Results" / "manifest.json")],
            capture_output=True,
            text=True,
            env=env)
        journal = subprocess.run(
            base_cmd + ["--journal", str(veriput_root / "Results" / "manifest.jsonl")],
            capture_output=True,
            text=True,
            env=env)
    bad = 0
    bad += check(out.returncode == 1 and "--out must not be under" in out.stderr,
                 f"protected manifest --out is refused: {out.stderr.strip()}")
    bad += check(journal.returncode == 1 and "--journal must not be under" in journal.stderr,
                 f"protected manifest --journal is refused: {journal.stderr.strip()}")
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


def test_unit_manifest_cli_reads_target_manifest_hints():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td, "repo__C")
        (d / "flat.sol.solast").write_text(json.dumps(compact_ast()) + "\n")
        target_manifest = Path(td) / "targets.json"
        target_manifest.write_text(json.dumps({
            "schema": "veriput-eval/target/v1",
            "targets": [{
                "schema": "veriput-eval-target/v1",
                "benchmark": "stress243",
                "subject_id": "repo__C",
                "status": "ok",
                "contract": "C",
                "sources": [{"variant": "source", "path": "flat.sol"}],
                "units_hint": ["own", "missingChanged"],
            }],
        }) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--target-manifest", str(target_manifest),
            "--subject-root", td,
        ], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    row = data["subjects"][0]
    bad = 0
    bad += check(data["summary"]["hinted_units"] == 1,
                 f"one target hint matches enumerated units: {data['summary']}")
    bad += check(data["summary"]["missing_unit_hints"] == 1,
                 f"one target hint is absent from AST units: {data['summary']}")
    bad += check(row["unit_hints"]["hinted_units"] == ["own"],
                 f"matched hint is retained: {row['unit_hints']}")
    bad += check(row["unit_hints"]["missing_unit_hints"] == ["missingChanged"],
                 f"missing hint is retained: {row['unit_hints']}")
    bad += check(row["target"]["subject_id"] == "repo__C",
                 f"target row is attached: {row['target']}")
    return bad


def test_unit_manifest_cli_uses_prepared_changed_function_hints():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(
            td,
            "repo__C",
            changed_functions=["own", "missingChanged"])
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
    row = data["subjects"][0]
    bad = 0
    bad += check(data["summary"]["hinted_units"] == 1,
                 f"metadata changed function hint is counted: {data['summary']}")
    bad += check(data["summary"]["missing_unit_hints"] == 1,
                 f"missing metadata hint is counted: {data['summary']}")
    bad += check(row["unit_hints"]["source"] == "prepared-metadata.changed_functions",
                 f"hint source is explicit: {row['unit_hints']}")
    bad += check(row["unit_hints"]["hinted_units"] == ["own"],
                 f"metadata hint matched an enumerated unit: {row['unit_hints']}")
    bad += check(row["unit_hints"]["missing_unit_hints"] == ["missingChanged"],
                 f"metadata hint miss is retained: {row['unit_hints']}")
    return bad


def test_unit_manifest_cli_refuses_target_contract_mismatch():
    with tempfile.TemporaryDirectory() as td:
        d = make_subject(td, "repo__C")
        (d / "flat.sol.solast").write_text(json.dumps(compact_ast()) + "\n")
        target_manifest = Path(td) / "targets.json"
        target_manifest.write_text(json.dumps({
            "schema": "veriput-eval/target/v1",
            "targets": [{
                "schema": "veriput-eval-target/v1",
                "benchmark": "stress243",
                "subject_id": "repo__C",
                "status": "ok",
                "contract": "WrongC",
                "sources": [{"variant": "source", "path": "flat.sol"}],
                "units_hint": [],
            }],
        }) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--target-manifest", str(target_manifest),
            "--subject-root", td,
        ], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    row = data["subjects"][0]
    bad = 0
    bad += check(data["summary"]["error"] == 1,
                 f"mismatched target counts as error: {data['summary']}")
    bad += check("disagrees" in row["reason"],
                 f"mismatch reason is explicit: {row}")
    bad += check(row["subject_contract"] == "C",
                 f"prepared contract is recorded: {row}")
    return bad


def test_unit_manifest_cli_records_unusable_prepared_subject():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, "repo__C", status="compile-failed")
        target_manifest = Path(td) / "targets.json"
        target_manifest.write_text(json.dumps({
            "schema": "veriput-eval/target/v1",
            "targets": [{
                "schema": "veriput-eval-target/v1",
                "benchmark": "stress243",
                "subject_id": "repo__C",
                "status": "ok",
                "contract": "C",
                "sources": [{"variant": "source", "path": "flat.sol"}],
                "units_hint": ["f"],
            }],
        }) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--target-manifest", str(target_manifest),
            "--subject-root", td,
        ], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    row = data["subjects"][0]
    bad = 0
    bad += check(data["summary"]["error"] == 1,
                 f"unusable prepared subject is row-local: {data['summary']}")
    bad += check("status='compile-failed'" in row["reason"],
                 f"prepared status is recorded: {row}")
    bad += check(row["target"]["subject_id"] == "repo__C",
                 f"target row is retained: {row}")
    return bad


def test_unit_manifest_cli_continues_after_unusable_scanned_subject():
    with tempfile.TemporaryDirectory() as td:
        make_subject(td, "bad__C", status="compile-failed")
        good = make_subject(td, "good__C")
        (good / "flat.sol.solast").write_text(json.dumps(compact_ast()) + "\n")
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts"
                / "subject_unit_manifest.py"),
            "--benchmark", "stress243",
            "--subject-root", td,
        ], capture_output=True, text=True)
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    data = json.loads(cp.stdout)
    rows = {row["subject"]["subject_id"]: row for row in data["subjects"]}
    bad = 0
    bad += check(data["summary"]["subjects"] == 2,
                 f"both scanned subjects are represented: {data['summary']}")
    bad += check(data["summary"]["ok"] == 1 and data["summary"]["error"] == 1,
                 f"bad scanned subject is row-local: {data['summary']}")
    bad += check(rows["bad__C"]["status"] == "error"
                 and "status='compile-failed'" in rows["bad__C"]["reason"],
                 f"bad status is recorded without aborting: {rows['bad__C']}")
    bad += check(rows["good__C"]["status"] == "ok"
                 and rows["good__C"]["units"]["units"] == ["own", "baseOnly"],
                 f"good subject after bad one is still enumerated: {rows['good__C']}")
    return bad


def main():
    tests = [
        test_resolve_subject_from_root_and_unit,
        test_resolve_subject_requires_explicit_unit,
        test_resolve_subject_accepts_cleaned_result_dir_alias,
        test_subject_from_cert_record_round_trips,
        test_subject_record_rehomes_veriput_root_paths,
        test_subject_record_preserves_inferred_solc_bin,
        test_resolve_subject_rehomes_missing_solc_select_binary,
        test_bad_status_is_not_usable,
        test_resolve_subject_uses_bugfix_fallback_root,
        test_resolve_subject_uses_bugfix_dataset_fix_source,
        test_resolve_subject_prefers_primary_when_benchmark_is_known,
        test_resolve_subject_uses_peer_fallback_root,
        test_ast_unit_enumeration_is_target_contract_scoped,
        test_no_unit_enumeration_records_auditable_reasons,
        test_unit_manifest_records_missing_ast_without_solc,
        test_generate_solast_uses_inferred_solc_bin_directly,
        test_generate_ast_is_atomic_on_success,
        test_generate_ast_failure_leaves_no_partial_solast,
        test_generate_ast_start_failure_cleans_temp_file,
        test_unit_manifest_cli_generates_ast_with_inferred_solc,
        test_unit_manifest_cli_reads_ast_cache_without_touching_subject,
        test_unit_manifest_cli_generates_ast_into_cache_only,
        test_unit_manifest_cli_refuses_real_results_ast_writes,
        test_unit_manifest_cli_refuses_protected_report_outputs,
        test_unit_manifest_cli_lists_units_without_esbmc,
        test_unit_manifest_cli_shard_and_resume,
        test_unit_manifest_cli_reads_target_manifest_hints,
        test_unit_manifest_cli_uses_prepared_changed_function_hints,
        test_unit_manifest_cli_refuses_target_contract_mismatch,
        test_unit_manifest_cli_records_unusable_prepared_subject,
        test_unit_manifest_cli_continues_after_unusable_scanned_subject,
    ]
    bad = 0
    for test in tests:
        print("---", test.__name__)
        bad += test()
    print(f"\n{len(tests)} test(s) ran")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
