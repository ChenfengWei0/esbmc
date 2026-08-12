#!/usr/bin/env python3
import json
import argparse
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import rq1_veriput_run  # noqa: E402
import veriput_path_guard  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def test_latest_rows_coalesces_legacy_and_canonical_subject_keys():
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp) / "results.jsonl"
        rows = [{
            "key": "legacy-subject",
            "subject_id": "subject",
            "valid": 1,
        }, {
            "key": "gen:veriput:subject",
            "subject_id": "subject",
            "valid": 2,
        }]
        journal.write_text("".join(json.dumps(row) + "\n" for row in rows))
        latest = rq1_veriput_run._latest_rows(journal)
        assert list(latest) == ["gen:veriput:subject"]
        assert latest["gen:veriput:subject"]["valid"] == 2


def test_dataset_manifest_excludes_deploy_only_validity():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dataset = root / "real203"
        dataset.mkdir()
        journal = dataset / "results.jsonl"
        journal.write_text(
            json.dumps({
                "key": "subject",
                "subject_id": "subject",
                "valid": 1,
                "concrete_valid": 1,
                "quality_bucket": "valid-no-PUT",
                "valid_tests": [{
                    "kind": "concrete",
                    "stage2_source": "no_unit_deploy_fallback",
                    "stage4_kind": "deploy-only",
                    "valid_reference_test": True,
                }],
            }) + "\n")
        rq1_veriput_run.write_dataset_manifest(root, "real203", journal)
        manifest = json.loads((dataset / "manifest.json").read_text())
        assert manifest["summary"]["rows"] == 1
        assert manifest["summary"]["valid"] == 0
        assert manifest["summary"]["quality_bucket"] == {"no-valid": 1}


def write_minimal_schedule(case_dir,
                           *,
                           subject_dir,
                           generated_at="2030-01-01T00:00:00+00:00",
                           ast_cache_root=None,
                           solast_path=None):
    case_dir.mkdir(parents=True, exist_ok=True)
    Path(subject_dir).mkdir(parents=True, exist_ok=True)
    flat = Path(subject_dir) / "flat.sol"
    if not flat.exists():
        flat.write_text("contract Token {}\n")
    prepared_solast = Path(subject_dir) / "subject.solast"
    if not prepared_solast.exists():
        prepared_solast.write_text('{"nodeType":"SourceUnit","nodes":[]}\n')
    solast = Path(solast_path) if solast_path is not None else prepared_solast
    if not solast.exists():
        solast.parent.mkdir(parents=True, exist_ok=True)
        solast.write_text('{"nodeType":"SourceUnit","nodes":[]}\n')
    old_ts = 1893450000
    os.utime(flat, (old_ts, old_ts))
    os.utime(prepared_solast, (old_ts, old_ts))
    os.utime(solast, (old_ts, old_ts))
    certify_argv = [
        "python3",
        "certify_all.py",
        "--subject-dir",
        str(subject_dir),
        "--subject-benchmark",
        "peer182",
        "--unit",
        "transfer",
    ]
    if ast_cache_root is not None:
        certify_argv.extend(["--ast-cache-root", str(ast_cache_root)])
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "generated_at": generated_at,
        "recipe_version": "veriput-strong/27-proof-budgeted-r2",
        "selection_strategy": "priority",
        "limit": None,
        "shard": None,
        "source": {
            "schema": "veriput-unit-manifest/v1",
            "benchmark": "peer182",
            "generate_ast": None,
            "target_manifest": None,
            "ast_cache_root": str(ast_cache_root) if ast_cache_root is not None else None,
        },
        "rq1_stage2_runtime_policy": {
            "stage2_unit_timeout_cap_s": 120,
            "adaptive_stage2_unit_timeout_cap_s": 120,
            "stage2_stage4_reserve_s": 120,
            "stage4_reserve_boundary_enforced": True,
            "bounded_holds_retry": True,
            "bounded_holds_retry_max_tx": 2,
            "bounded_holds_retry_unwind": 8,
            "bounded_holds_retry_max_initial_wall_s": 45,
        },
        "certification_budget": {
            "timeout_s": 600,
            "run_timeout_s": 120,
            "memlimit_gib": 12,
        },
        "jobs": [{
            "benchmark": "peer182",
            "subject_id": "subject",
            "contract": "Token",
            "unit": "transfer",
            "path_function": "sol:@C@Token@F@transfer#1",
            "target": "peer182__subject.transfer",
            "subject": {
                "root": str(subject_dir),
                "flat_sol": str(flat),
                "solast": str(solast),
                "benchmark": "peer182",
                "benchmark_key": "subject",
                "subject_id": "subject",
                "contract": "Token",
            },
            "region_strategy": "strong",
            "sequence_strategy": "single",
            "certify_argv": certify_argv,
            "unit_info": {
                "contract": "Token",
                "name": "transfer",
                "signature": "transfer(address,uint256)",
                "path_function": "sol:@C@Token@F@transfer#1",
            },
        }],
    }
    (case_dir / "unit-schedule.json").write_text(json.dumps(schedule))


def verifier_input_identity(subject_dir, *, solast_path=None):
    solast = Path(solast_path) if solast_path is not None else Path(subject_dir) / "subject.solast"
    return {
        "schema":
        "veriput-verifier-input-identity/v1",
        "inputs": [{
            "subject_dir":
            str(Path(subject_dir).resolve()),
            "flat":
            str((Path(subject_dir) / "flat.sol").resolve()),
            "flat_sha256":
            rq1_veriput_run._sha256_file(Path(subject_dir) / "flat.sol"),
            "solast":
            str(solast.resolve()),
            "solast_sha256":
            rq1_veriput_run._sha256_file(solast),
        }],
    }


def stage4_toolchain_identity(*, forge_sha="forge-sha", solc_sha="solc-sha", forge_std_sha="std-sha"):
    return {
        "schema": "veriput-stage4-toolchain-identity/v1",
        "forge": {
            "path": "/tmp/forge",
            "sha256": forge_sha,
            "version_args": ["--version"],
            "version_rc": 0,
            "version": "forge test",
        },
        "solc": {
            "path": "/tmp/solc",
            "sha256": solc_sha,
            "version_args": ["--version"],
            "version_rc": 0,
            "version": "solc test",
        },
        "forge_std": {
            "path": "/tmp/forge-std",
            "exists": True,
            "files": 1,
            "sha256": forge_std_sha,
        },
    }


def write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def write_stale_valid_result(case_dir,
                             *,
                             binary_sha="same-binary",
                             pipeline_files=None,
                             verifier_identity=None,
                             stage4_identity=None):
    if pipeline_files is None:
        pipeline_files = {"pipeline.py": "same-pipeline"}
    row = {
        "subject_id": "subject",
        "contract": "Token",
        "benchmark": "peer182",
        "completion_status": "ok",
        "status": "ok",
        "raw": 1,
        "valid": 1,
        "put_raw": 0,
        "put_valid": 0,
        "concrete_raw": 1,
        "concrete_valid": 1,
        "quality_bucket": "valid-no-PUT",
        "esbmc_binary_identity": {
            "path": "/tmp/esbmc",
            "sha256": binary_sha,
        },
        "pipeline_code_identity": {
            "schema": "veriput-pipeline-code-identity/v1",
            "files": pipeline_files,
        },
    }
    if verifier_identity is not None:
        row["verifier_input_identity"] = verifier_identity
    if stage4_identity is not None:
        row["stage4_toolchain_identity"] = stage4_identity
    (case_dir / "result.json").write_text(json.dumps({"row": row}))


def test_path_guard_allows_only_veriput_rq1_result_tree():
    bad = 0
    allowed = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT/peer182/results.jsonl")
    blocked = Path("/home/samson/workspace/VeriPUT/Results/Peer182/logs/gen_veriput.jsonl")
    try:
        veriput_path_guard.ensure_path_not_protected("--out", allowed)
        allowed_ok = True
    except ValueError:
        allowed_ok = False
    try:
        veriput_path_guard.ensure_path_not_protected("--out", blocked)
        blocked_ok = False
    except ValueError:
        blocked_ok = True
    bad += check(allowed_ok, "RQ1 VeriPUT result tree is an allowed generated artifact root")
    bad += check(blocked_ok, "prepared Results trees remain protected")
    return bad


def test_pipeline_identity_includes_dependency_modules():
    identity = rq1_veriput_run._pipeline_code_identity()
    files = identity.get("files") or {}
    suffixes = {Path(path).name for path in files}
    required = {
        "certify_all.py",
        "solidity_path_generalise.py",
        "solidity_path_put.py",
        "solidity_ast_dependencies.py",
        "put_all.py",
        "rq1_veriput_run.py",
        "veriput_recipe.py",
        "veriput_subjects.py",
        "unit_schedule.py",
        "subject_unit_manifest.py",
        "target_manifest.py",
    }
    missing = sorted(required - suffixes)
    missing_hashes = sorted(path for path, digest in files.items() if not digest)
    return check(not missing and not missing_hashes,
                 f"pipeline identity covers dependency modules: missing={missing}, "
                 f"missing_hashes={missing_hashes}")


def test_stage4_toolchain_identity_uses_foundry_prepend_path():
    old_home = os.environ.get("HOME")
    old_path = os.environ.get("PATH")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        home = root / "home"
        parent_bin = root / "parent-bin"
        foundry_forge = home / ".foundry" / "bin" / "forge"
        parent_forge = parent_bin / "forge"
        parent_solc = parent_bin / "solc"
        write_executable(foundry_forge,
                         "#!/bin/sh\nprintf 'forge Version: foundry-prepended\\n'\n")
        write_executable(parent_forge, "#!/bin/sh\nprintf 'forge Version: parent-path\\n'\n")
        write_executable(parent_solc,
                         "#!/bin/sh\nprintf 'solc, test\\nVersion: parent-solc\\n'\n")
        os.environ["HOME"] = str(home)
        os.environ["PATH"] = str(parent_bin)
        try:
            identity = rq1_veriput_run._stage4_toolchain_identity()
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(identity.get("forge", {}).get("path") == str(foundry_forge.resolve()),
                 f"Stage4 identity uses Foundry-prepended forge path: {identity.get('forge')}")


def test_put_artifact_summary_counts_raw_valid_and_oracle_classes():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        unit = root / "put" / "approve"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest_Token_approve_put7.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T {}\n")
        disabled_file = unit / "Project" / "test" / "TokenCovTest_disabled.t.sol"
        disabled_file.write_text("contract T { function disabled_test_cov_disabled() public {} }\n")
        unsupported_file = (unit / "Project" / "test" / "TokenCovTest_unsupported.t.sol")
        unsupported_file.write_text("""\
contract T {
  function test_cov_unsupported() public {
    // UNSUPPORTED: Token.approve has an argument type ESBMC cannot yet render
  }
}
""")
        setup_warning_file = (unit / "Project" / "test" / "TokenCovTest_setup_warning.t.sol")
        setup_warning_file.write_text("""\
contract T {
  function setUp() public {
    // UNSUPPORTED: helper deployment is abstract
  }
  function test_cov_setup_warning() public {
    assertTrue(true);
  }
}
""")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "put",
                "test": "test_put_Token_approve_path7",
                "file": str(put_file),
                "stats": {
                    "oracle_classes": ["R1", "R2"],
                    "assertion_oracles": [
                        {
                            "layer": "post",
                            "var": "balance",
                            "text": "post >= pre",
                            "classes": ["R1"],
                            "verdict": "HOLDS",
                            "emitted_in_test": True,
                            "guarded": False,
                        },
                        {
                            "layer": "delta",
                            "var": "allowance",
                            "text": "post - pre in [0, amount]",
                            "classes": ["R1", "R2"],
                            "verdict": "HOLDS",
                            "emitted_in_test": True,
                            "guarded": False,
                        },
                    ],
                },
            }))
        zero_wd = unit / "_wd" / "zero"
        zero_wd.mkdir(parents=True)
        (zero_wd / "put.json").write_text(
            json.dumps({
                "kind": "put",
                "test": "test_put_Token_approve_path9",
                "file": "zero.t.sol",
                "stats": {
                    "oracle_classes": [],
                    "asserts": 0,
                    "guarded_asserts": 0,
                    "assertion_oracles": [],
                },
            }))
        colliding_concrete_wd = unit / "_wd" / "colliding-concrete"
        colliding_concrete_wd.mkdir(parents=True)
        (colliding_concrete_wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov_Token_approve_path8",
                "file": "different-concrete.t.sol",
                "stats": {
                    "oracle_classes": ["BAD-MATCH"],
                    "assertion_oracles": [],
                },
            }))
        recovered_concrete_file = (unit / "Project" / "test" / "TokenCovTest_recovered.t.sol")
        recovered_concrete_file.write_text("contract T {}\n")
        recovered_concrete_wd = unit / "_wd" / "recovered-concrete"
        recovered_concrete_wd.mkdir(parents=True)
        (recovered_concrete_wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov_recovered",
                "file": str(recovered_concrete_file),
                "stage2_source": "certified-region-concrete-fallback",
                "stage2_witness_check": "CERTIFIED-REGION-PUT-REFUSED:build-put-refused",
                "concrete_reason": "certified-region PUT refused as build-put-refused; "
                "emitted concrete replay only",
                "stats": {
                    "oracle_classes": [],
                    "assertion_oracles": [],
                },
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "emission": {
                    "puts_emitted": 2,
                    "concrete_replays_emitted": 4,
                },
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 3,
                        "put": 1,
                        "concrete": 2,
                    },
                    "rows": [
                        {
                            "kind": "put",
                            "unit": "approve",
                            "enc": 7,
                            "piece": None,
                            "test": "test_put_Token_approve_path7",
                            "file": str(put_file),
                            "forge_status": "Success",
                            "valid_reference_test": True,
                            "b": True,
                        },
                        {
                            "kind": "concrete",
                            "stage2_source": "no-coordinate-concrete-fallback",
                            "stage2_witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                            "concrete_reason": "Stage-2 complete witness has no coordinate",
                            "unit": "approve",
                            "enc": 8,
                            "piece": None,
                            "test": "test_cov_Token_approve_path8",
                            "file": "concrete.t.sol",
                            "forge_status": "Failure",
                            "valid_reference_test": False,
                            "b": False,
                        },
                        {
                            "kind": "put",
                            "unit": "approve",
                            "enc": 9,
                            "piece": None,
                            "test": "test_put_Token_approve_path9",
                            "file": "zero.t.sol",
                            "forge_status": "Success",
                            "valid_reference_test": False,
                            "b": False,
                        },
                        {
                            "kind": "concrete",
                            "unit": "approve",
                            "enc": 13,
                            "piece": None,
                            "test": "test_cov_recovered",
                            "file": str(recovered_concrete_file),
                            "forge_status": "Success",
                            "valid_reference_test": True,
                            "b": False,
                        },
                        {
                            "kind": "concrete",
                            "unit": "approve",
                            "enc": 14,
                            "piece": None,
                            "test": "test_cov_setup_warning",
                            "file": str(setup_warning_file),
                            "forge_status": "Success",
                            "valid_reference_test": True,
                            "b": False,
                        },
                        {
                            "kind": "concrete",
                            "unit": "approve",
                            "enc": 10,
                            "piece": None,
                            "test": "test_cov_disabled",
                            "file": str(disabled_file),
                            "forge_status": None,
                            "valid_reference_test": False,
                            "b": False,
                        },
                        {
                            "kind": "concrete",
                            "unit": "approve",
                            "enc": 11,
                            "piece": None,
                            "test": "test_cov_unsupported",
                            "file": str(unsupported_file),
                            "forge_status": "Success",
                            "valid_reference_test": True,
                            "b": False,
                        },
                        {
                            "kind": "refusal",
                            "unit": "approve",
                            "enc": 12,
                            "test": "test_refused_kind",
                            "file": "refused.t.sol",
                            "forge_status": "Success",
                            "valid_reference_test": True,
                            "b": False,
                        },
                    ],
                },
            }))
        summary = rq1_veriput_run.summarize_put_artifacts(root / "put")
        bad = 0
        bad += check(summary["raw"] == 4 and summary["valid"] == 3,
                     f"raw/valid split is retained: {summary}")
        bad += check(
            summary["put_raw"] == 1 and summary["put_valid"] == 1 and summary["concrete_raw"] == 3
            and summary["concrete_valid"] == 2, f"PUT/concrete split is retained: {summary}")
        bad += check(summary["oracle_class_counts"] == {
            "R1": 2,
            "R2": 1
        }, f"oracle labels counted: {summary['oracle_class_counts']}")
        bad += check(summary["oracle_class_combo_counts"] == {
            "R1": 1,
            "R1+R2": 1,
        }, f"oracle combinations counted: {summary['oracle_class_combo_counts']}")
        bad += check(
            len(summary["assertion_oracles"]) == 2
            and summary["raw_tests"][0]["oracle_classes"] == ["R1", "R2"],
            f"assertion metadata remains tied to artifacts: {summary}")
        concrete = [row for row in summary["raw_tests"] if row["kind"] == "concrete"][0]
        bad += check(concrete["oracle_classes"] == [] and concrete["put_json"] is None,
                     f"duplicate concrete test names do not cross-link put.json: {summary}")
        bad += check(
            concrete["stage2_source"] == "no-coordinate-concrete-fallback"
            and concrete["stage2_witness_check"] == "COMPLETE-WITNESS-NO-COORDINATE"
            and "complete witness" in concrete["concrete_reason"],
            f"concrete fallback provenance is retained: {summary}")
        recovered = [row for row in summary["raw_tests"] if row["enc"] == 13][0]
        bad += check(
            recovered["stage2_source"] == "certified-region-concrete-fallback" and
            recovered["stage2_witness_check"] == "CERTIFIED-REGION-PUT-REFUSED:build-put-refused"
            and "certified-region PUT refused" in recovered["concrete_reason"],
            f"put.json provenance fills sparse B rows: {summary}")
        bad += check(
            len(summary["raw_tests"]) == 4 and all(t["enc"] != 9 for t in summary["raw_tests"]),
            f"refused PUT rows are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 12 for t in summary["raw_tests"]),
                     f"non-deliverable kind rows are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 10 for t in summary["raw_tests"]),
                     f"disabled concrete replays are not raw deliverables: {summary}")
        bad += check(all(t["enc"] != 11 for t in summary["raw_tests"]),
                     f"unsupported concrete bodies are not raw deliverables: {summary}")
        bad += check(any(t["enc"] == 14 for t in summary["valid_tests"]),
                     f"green concrete replays with setup warnings are retained: {summary}")
        bad += check(
            summary["quality_bucket"] == "valid-PUT-with-R1R2" and summary["valid_put_with_R1"] == 1
            and summary["valid_put_with_R2"] == 1 and summary["valid_put_with_R1_or_R2"] == 1
            and summary["valid_put_without_R1R2"] == 0,
            f"methodology strength fields are counted: {summary}")
        return bad


def test_strength_quality_bucket_keeps_no_put_and_no_r1r2_visible():
    cases = [
        ({
            "valid_tests": [],
        }, "no-valid"),
        ({
            "valid_tests": [{
                "kind": "concrete",
                "valid_reference_test": True,
            }],
        }, "valid-no-PUT"),
        ({
            "valid_tests": [{
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R0"],
            }],
        }, "valid-PUT-no-R1R2"),
        ({
            "valid_tests": [{
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R1"],
            }],
        }, "valid-PUT-with-R1R2"),
    ]
    bad = 0
    for summary, bucket in cases:
        got = rq1_veriput_run._strength_quality(summary)
        bad += check(got["quality_bucket"] == bucket, f"{bucket} is reported distinctly: {got}")
    return bad


def test_normalize_result_row_trusts_valid_test_oracle_classes():
    row = rq1_veriput_run._normalize_result_row({
        "status":
        "timeout",
        "reason":
        "case timed out after producing artifacts",
        "valid":
        1,
        "put_valid":
        1,
        "valid_put_with_R1":
        0,
        "valid_put_with_R2":
        0,
        "valid_put_with_R1_or_R2":
        0,
        "valid_put_without_R1R2":
        1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R0", "R1"],
        }],
    })
    bad = 0
    bad += check(row["valid_put_with_R1"] == 1, f"R1 count is recomputed from valid_tests: {row}")
    bad += check(row["valid_put_with_R1_or_R2"] == 1,
                 f"R1/R2 count is recomputed from valid_tests: {row}")
    bad += check(row["valid_put_without_R1R2"] == 0,
                 f"stale no-R1/R2 aggregate is not retained: {row}")
    bad += check(
        row["status"] == "ok" and row["reason"] is None
        and row["partial_failure_reason"] == "case timed out after producing artifacts",
        f"valid normalized row is successful but keeps old reason: {row}")
    return bad


def test_normalize_result_row_requires_explicit_double_oracle_validity():
    row = rq1_veriput_run._normalize_result_row({
        "status":
        "ok",
        "valid":
        1,
        "put_valid":
        1,
        "valid_put_with_R1":
        1,
        "valid_put_with_R2":
        1,
        "valid_put_with_R1_or_R2":
        1,
        "valid_tests": [{
            "kind": "put",
            "oracle_classes": ["R1", "R2"],
        }],
    })
    bad = 0
    bad += check(
        row["valid"] == 0 and row["put_valid"] == 0 and row["valid_put_with_R1_or_R2"] == 0,
        f"missing replay validity is not counted: {row}")
    bad += check(row["quality_bucket"] == "no-valid",
                 f"unknown replay validity keeps row weak: {row}")
    bad += check(rq1_veriput_run._row_needs_resume_retry(row),
                 f"old empty ok rows are retryable: {row}")
    return bad


def test_row_strength_prioritizes_methodology_quality():
    r1_put = {
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
        }],
        "raw": 1,
    }
    many_r0_puts = {
        "valid_tests": [
            {
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R0"],
            },
            {
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R0"],
            },
        ],
        "raw":
        2,
    }
    one_put = {
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R0"],
        }],
        "raw": 1,
    }
    many_concrete = {
        "valid_tests": [
            {
                "kind": "concrete",
                "valid_reference_test": True
            },
            {
                "kind": "concrete",
                "valid_reference_test": True
            },
        ],
        "raw":
        2,
    }
    stale_empty = {
        "valid": 2,
        "put_valid": 1,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "valid-PUT-with-R1R2",
        "valid_tests": [],
        "raw": 2,
    }
    stale_false = {
        "valid":
        2,
        "put_valid":
        1,
        "valid_put_with_R1_or_R2":
        1,
        "quality_bucket":
        "valid-PUT-with-R1R2",
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": False,
            "oracle_classes": ["R1", "R2"],
        }],
        "raw":
        2,
    }
    bad = 0
    bad += check(
        rq1_veriput_run._row_strength(r1_put) > rq1_veriput_run._row_strength(many_r0_puts),
        "R1/R2 PUT quality outranks more R0-only PUTs")
    bad += check(
        rq1_veriput_run._row_strength(one_put) > rq1_veriput_run._row_strength(many_concrete),
        "PUT quality outranks more concrete-only replays")
    bad += check(
        rq1_veriput_run._row_strength(stale_empty)[0] == 0
        and rq1_veriput_run._row_strength(stale_false)[0] == 0,
        "explicit valid_tests evidence overrides stale aggregates")
    normalized = rq1_veriput_run._normalize_result_row(stale_empty)
    bad += check(
        normalized["valid"] == 0 and normalized["put_valid"] == 0
        and normalized["quality_bucket"] == "no-valid",
        f"normalization also trusts empty valid_tests: {normalized}")
    return bad


def test_resume_retries_empty_no_valid_rows_only():
    retryable = {
        "status": "no-output",
        "completion_status": "early-stop-no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no Stage-2 candidate after 4 consecutive units; "
        "stopped before remaining units",
        "stage4_candidate_units_attempted": 0,
    }
    timed_out_empty = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "certification timed out before PUT artifacts: approve",
    }
    valid = {
        "status": "ok",
        "quality_bucket": "valid-PUT-with-R1R2",
        "raw": 1,
        "valid": 1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
        }],
    }
    raw_invalid = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 1,
        "valid": 0,
        "reason": "no valid reference tests",
    }
    runner_error = {
        "status": "error",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "runner exception: NameError('old bug')",
    }
    missing_ast_schedule = {
        "status": "no-units",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "unit schedule preparation failed: missing-ast=1",
        "schedule_summary": {
            "jobs": 0,
            "skipped_by_status": {
                "missing-ast": 1
            },
        },
    }
    diagnostic_empty = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no certified regions: diagnostics esbmc-no-cov-report=1",
        "driver_diagnostic_tags": {
            "esbmc-no-cov-report": 1,
            "path-coverage-probe-goal-cap": 1,
        },
    }
    true_no_units = {
        "status": "no-units",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "target contract has no public/external FunctionDefinition units",
        "schedule_summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
    }
    done = {
        "retry": retryable,
        "timeout": timed_out_empty,
        "error": runner_error,
        "missing-ast": missing_ast_schedule,
        "diagnostic": diagnostic_empty,
        "true-no-units": true_no_units,
        "valid": valid,
        "raw-invalid": raw_invalid,
    }
    got = rq1_veriput_run.retryable_resume_rows(done, "no-valid")
    default = rq1_veriput_run.retryable_resume_rows(done)
    bad = 0
    bad += check(
        sorted(got) == ["diagnostic", "error", "missing-ast", "retry", "timeout", "true-no-units"],
        f"legacy no-valid resume retries only empty no-valid rows: {got}")
    bad += check("raw-invalid" in default,
                 f"default resume retries rows with raw but no valid: {default}")
    bad += check(not rq1_veriput_run._row_needs_resume_retry(valid),
                 "resume keeps valid rows terminal")
    bad += check(not rq1_veriput_run._row_needs_resume_retry(raw_invalid),
                 "resume keeps rows with retained raw artifacts terminal")
    bad += check(rq1_veriput_run._row_needs_resume_retry(true_no_units),
                 "resume retries constructor-only rows for deploy fallback")
    return bad


def test_resume_retries_certified_or_partial_zero_output_rows():
    certified_no_output = {
        "status": "no-output",
        "completion_status": "early-stop-no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no output after 44.5s Stage 4; stopped before remaining units",
        "cert_bucket_counts": {
            "CERTIFIED": 1,
            "KILLED": 1
        },
        "put_summary_paths": ["put/cancelOrders/put-summary.json"],
    }
    partial_stage4_no_output = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
        "reason": "no output after 91.0s Stage 4; stopped before remaining units",
        "put_summary_paths": ["put/getVault/put-summary.json"],
    }
    done = {
        "certified": certified_no_output,
        "partial": partial_stage4_no_output,
    }
    got = rq1_veriput_run.retryable_resume_rows(done)
    bad = 0
    bad += check(
        sorted(got) == ["certified", "partial"],
        f"certified/partial zero-output rows are retryable: {got}")
    return bad


def test_load_subject_result_row_recovers_certification_diagnostics():
    with tempfile.TemporaryDirectory() as td:
        case_dir = Path(td)
        (case_dir / "result.json").write_text(
            json.dumps({
                "row": {
                    "status": "no-output",
                    "quality_bucket": "no-valid",
                    "raw": 0,
                    "valid": 0,
                    "reason": "no certified regions: diagnostics esbmc-no-cov-report=1",
                },
                "certification": {
                    "bucket_counts": {
                        "NO-WITNESS-UNKNOWN": 1
                    },
                    "driver_diagnostic_tags": {
                        "esbmc-no-cov-report": 1,
                    },
                    "driver_refusal_tags": {
                        "path-coverage-probe-goal-cap": 1,
                    },
                },
            }))
        row = rq1_veriput_run._load_subject_result_row(case_dir)
        sidecar_dir = case_dir / "sidecar"
        (sidecar_dir / "cert").mkdir(parents=True)
        (sidecar_dir / "result.json").write_text(
            json.dumps({
                "row": {
                    "status": "no-output",
                    "quality_bucket": "no-valid",
                    "raw": 0,
                    "valid": 0,
                    "reason": "no certified regions",
                },
            }))
        (sidecar_dir / "cert" / "certify-results.jsonl").write_text(
            json.dumps({
                "benchmark": "bench",
                "unit": "f",
                "bucket": "NO-WITNESS-UNKNOWN",
                "driver_diagnostic": {
                    "tag": "path-coverage-probe-goal-cap",
                },
            }) + "\n")
        sidecar_row = rq1_veriput_run._load_subject_result_row(sidecar_dir)
    bad = 0
    bad += check(row["driver_diagnostic_tags"] == {
        "esbmc-no-cov-report": 1,
    }, f"certification diagnostic tags are recovered: {row}")
    bad += check(row["driver_refusal_tags"] == {
        "path-coverage-probe-goal-cap": 1,
    }, f"certification refusal tags are recovered: {row}")
    bad += check(rq1_veriput_run._row_needs_resume_retry(row),
                 f"recovered diagnostic row is retryable: {row}")
    bad += check(sidecar_row["driver_diagnostic_tags"] == {
        "path-coverage-probe-goal-cap": 1,
    }, f"cert sidecar diagnostic tags are recovered: {sidecar_row}")
    bad += check(rq1_veriput_run._row_needs_resume_retry(sidecar_row),
                 f"sidecar diagnostic row is retryable: {sidecar_row}")
    return bad


def test_adoption_updates_equal_strength_rows_with_cert_evidence():
    current = {
        "status": "no-output",
        "quality_bucket": "no-valid",
        "raw": 0,
        "valid": 0,
    }
    candidate = dict(current)
    candidate.update({
        "cert_bucket_counts": {
            "NO-WITNESS-UNKNOWN": 1
        },
        "driver_diagnostic_tags": {
            "path-coverage-probe-goal-cap": 1
        },
    })
    return check(rq1_veriput_run._row_needs_normalized_adoption(current, candidate),
                 "equal-strength rows adopt newly recovered certification evidence")


def test_normalize_result_row_recomputes_stale_aggregate_quality():
    put_row = rq1_veriput_run._normalize_result_row({
        "status": "no-output",
        "reason": "old stale no-valid row",
        "valid": 0,
        "put_valid": 1,
        "concrete_valid": 0,
        "valid_put_with_R1": 1,
        "valid_put_with_R2": 0,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "no-valid",
    })
    concrete_row = rq1_veriput_run._normalize_result_row({
        "status": "no-output",
        "reason": "old stale no-valid concrete row",
        "valid": 0,
        "put_valid": 0,
        "concrete_valid": 1,
        "quality_bucket": "no-valid",
    })
    bad = 0
    bad += check(put_row["valid"] == 1 and put_row["quality_bucket"] == "valid-PUT-with-R1R2",
                 f"aggregate PUT counts repair stale no-valid bucket: {put_row}")
    bad += check(
        put_row["status"] == "ok" and put_row["reason"] is None
        and put_row["partial_failure_reason"] == "old stale no-valid row",
        f"aggregate valid counts promote stale status: {put_row}")
    bad += check(
        concrete_row["valid"] == 1 and concrete_row["valid_concrete"] == 1
        and concrete_row["quality_bucket"] == "valid-no-PUT",
        f"aggregate concrete counts repair stale bucket: {concrete_row}")
    return bad


def test_adoption_updates_equal_strength_rows_with_artifact_evidence():
    current = {
        "status": "ok",
        "valid": 1,
        "put_valid": 1,
        "valid_put_with_R1": 1,
        "valid_put_with_R1_or_R2": 1,
        "quality_bucket": "valid-PUT-with-R1R2",
    }
    candidate = dict(current)
    candidate.update({
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
            "forge_status": "Success",
        }],
        "raw_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1"],
            "forge_status": "Success",
        }],
        "oracle_class_counts": {
            "R1": 1
        },
        "oracle_class_combo_counts": {
            "R1": 1
        },
        "assertion_oracles": [{
            "classes": ["R1"],
            "text": "post >= pre",
        }],
        "put_summary_paths": ["put/transfer/put-summary.json"],
        "foundry_replay_wall_s":
        0.25,
        "valid_artifacts_retained":
        True,
    })
    return check(rq1_veriput_run._row_needs_normalized_adoption(current, candidate),
                 "equal-strength rows adopt double-oracle artifact evidence")


def test_merge_put_summary_marks_valid_partial_artifacts_ok():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        unit = root / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_put() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "put",
                "test": "test_put",
                "file": str(put_file),
                "stats": {
                    "oracle_classes": ["R1"],
                    "assertion_oracles": [{
                        "classes": ["R1"],
                        "text": "post >= pre",
                    }],
                },
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "emission": {
                    "puts_emitted": 1,
                    "concrete_replays_emitted": 0,
                },
                "deliverable_b": {
                    "rows": [{
                        "kind": "put",
                        "unit": "transfer",
                        "enc": 3,
                        "test": "test_put",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": True,
                    }],
                },
            }))
        row = rq1_veriput_run._merge_put_summary_into_row(
            {
                "status": "budget-exhausted",
                "completion_status": "budget-exhausted",
                "reason": "subject budget exhausted",
                "raw": 0,
                "valid": 0,
                "quality_bucket": "no-valid",
            }, root)
        bad = 0
        bad += check(row["status"] == "ok" and row["completion_status"] == "budget-exhausted",
                     f"valid partial artifacts promote status only: {row}")
        bad += check(
            row["reason"] is None and row["partial_failure_reason"] == "subject budget exhausted",
            f"old failure reason is retained as partial: {row}")
        bad += check(
            row["valid"] == 1 and row["put_valid"] == 1
            and row["quality_bucket"] == "valid-PUT-with-R1R2",
            f"valid artifact strength is adopted: {row}")
        bad += check(row["raw_artifacts_retained"] and row["valid_artifacts_retained"],
                     f"artifact retention flags are adopted: {row}")
        return bad


def test_load_subject_result_row_adopts_put_artifacts():
    with tempfile.TemporaryDirectory() as td:
        case_dir = Path(td)
        unit = case_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_put() public {} }\n")
        (case_dir / "result.json").write_text(
            json.dumps({
                "row": {
                    "status": "budget-exhausted",
                    "reason": "case budget exhausted before Stage 4",
                    "raw": 0,
                    "valid": 0,
                    "put_valid": 0,
                    "quality_bucket": "no-valid",
                },
            }))
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "put",
                "test": "test_put",
                "file": str(put_file),
                "stats": {
                    "oracle_classes": ["R1", "R2"],
                    "assertion_oracles": [{
                        "classes": ["R1", "R2"],
                        "text": "post >= pre",
                    }],
                },
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "emission": {
                    "puts_emitted": 1,
                    "concrete_replays_emitted": 0,
                },
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 1,
                        "concrete": 0,
                    },
                    "rows": [{
                        "kind": "put",
                        "unit": "transfer",
                        "enc": 8,
                        "test": "test_put",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": True,
                    }],
                },
            }))
        row = rq1_veriput_run._load_subject_result_row(case_dir)
    bad = 0
    bad += check(
        row["status"] == "ok"
        and row["partial_failure_reason"] == "case budget exhausted before Stage 4",
        f"stale result row is promoted from put artifacts: {row}")
    bad += check(
        row["valid"] == 1 and row["put_valid"] == 1 and row["valid_put_with_R1_or_R2"] == 1
        and row["quality_bucket"] == "valid-PUT-with-R1R2",
        f"artifact counters replace stale no-valid result: {row}")
    bad += check(row["adopted_put_summary_artifacts"] and row["valid_artifacts_retained"],
                 f"artifact adoption is marked: {row}")
    return bad


def test_results_all_requires_double_oracle_validity():
    path = ROOT.parent / "VeriPUT" / "Results" / "results_all.py"
    spec = importlib.util.spec_from_file_location("results_all_for_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    row = mod.normalize_veriput_row({
        "valid_tests": [
            {
                "kind": "put",
                "oracle_classes": ["R1", "R2"],
            },
            {
                "kind": "concrete",
                "valid_reference_test": False,
            },
            {
                "kind": "put",
                "valid_reference_test": True,
                "oracle_classes": ["R1"],
            },
        ],
    })
    bad = 0
    bad += check(row["valid"] == 1 and row["put_valid"] == 1 and row["concrete_valid"] == 0,
                 f"only explicit double-oracle tests count as valid: {row}")
    bad += check(
        row["valid_put_with_R1_or_R2"] == 1 and row["quality_bucket"] == "valid-PUT-with-R1R2",
        f"R-class quality is computed from explicit valid tests: {row}")
    stale = mod.normalize_veriput_row({
        "valid":
        3,
        "put_valid":
        2,
        "concrete_valid":
        1,
        "quality_bucket":
        "valid-PUT-with-R1R2",
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": False,
            "oracle_classes": ["R1", "R2"],
        }],
    })
    bad += check(
        stale["valid"] == 0 and stale["put_valid"] == 0 and stale["concrete_valid"] == 0
        and stale["quality_bucket"] == "no-valid",
        f"explicit non-valid test evidence overrides stale "
        f"aggregate counters: {stale}")
    empty = mod.normalize_veriput_row({
        "valid": 1,
        "put_valid": 1,
        "quality_bucket": "valid-PUT-no-R1R2",
        "valid_tests": [],
    })
    bad += check(
        empty["valid"] == 0 and empty["put_valid"] == 0 and empty["quality_bucket"] == "no-valid",
        f"empty valid_tests overrides stale aggregate counters: {empty}")
    return bad


def test_adopt_existing_subject_results_promotes_stale_sidecar_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "stale"
        unit = case_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_put() public {} }\n")
        (case_dir / "result.json").write_text(
            json.dumps({
                "row": {
                    "status": "no-output",
                    "completion_status": "early-stop-no-output",
                    "reason": "old stale no-valid row",
                    "raw": 0,
                    "valid": 0,
                    "put_valid": 0,
                    "concrete_valid": 0,
                    "quality_bucket": "no-valid",
                    "stage4_generation_wall_s": 0.01,
                    "foundry_replay_wall_s": 0.01,
                },
            }))
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "put",
                "test": "test_put",
                "file": str(put_file),
                "stats": {
                    "oracle_classes": ["R1", "R2"],
                    "assertion_oracles": [{
                        "classes": ["R1", "R2"],
                        "text": "post == pre + amount",
                    }],
                },
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "emission": {
                    "puts_emitted": 1,
                    "concrete_replays_emitted": 0,
                },
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 1,
                        "concrete": 0,
                    },
                    "rows": [{
                        "kind": "put",
                        "unit": "transfer",
                        "enc": 8,
                        "test": "test_put",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                        "b": True,
                    }],
                },
                "timing": {
                    "generation_wall_s": 0.2,
                    "emission_wall_s": 0.3,
                    "foundry_replay_wall_s": 0.4,
                    "total_wall_s": 0.9,
                },
            }))
        journal = root / "peer182" / "results.jsonl"
        done = {
            "gen:veriput:stale": {
                "key": "gen:veriput:stale",
                "subject_id": "stale",
                "status": "no-output",
                "reason": "old stale no-valid row",
                "raw": 0,
                "valid": 0,
                "quality_bucket": "no-valid",
            },
        }
        updated = rq1_veriput_run.adopt_existing_subject_results(root, "peer182",
                                                                 [{
                                                                     "subject_id": "stale",
                                                                     "benchmark": "peer182",
                                                                     "contract": "Token",
                                                                 }], journal, done)
        row = updated["gen:veriput:stale"]
        journal_rows = [
            json.loads(line) for line in journal.read_text().splitlines() if line.strip()
        ]
    bad = 0
    bad += check(
        row["status"] == "ok" and row["partial_failure_reason"] == "old stale no-valid row",
        f"stale no-valid row is promoted from sidecar: {row}")
    bad += check(
        row["valid"] == 1 and row["put_valid"] == 1
        and row["quality_bucket"] == "valid-PUT-with-R1R2",
        f"sidecar strength counters are authoritative: {row}")
    bad += check(
        row["valid_tests"][0]["valid_reference_test"] is True
        and row["valid_tests"][0]["forge_status"] == "Success",
        f"double-oracle fields are retained: {row}")
    bad += check(
        row["oracle_class_counts"] == {
            "R1": 1,
            "R2": 1
        } and row["oracle_class_combo_counts"] == {"R1+R2": 1},
        f"R-class metadata is retained: {row}")
    bad += check(
        row["stage4_generation_wall_s"] == 0.2 and row["stage4_emission_wall_s"] == 0.3
        and row["foundry_replay_wall_s"] == 0.4 and row["put_all_wall_s"] == 0.9,
        f"Stage4 timing is retained: {row}")
    bad += check(
        row["raw_artifacts_retained"] and row["valid_artifacts_retained"]
        and row["adopted_put_summary_artifacts"],
        f"artifact retention/adoption flags are retained: {row}")
    bad += check(
        len(journal_rows) == 1 and journal_rows[0]["quality_bucket"] == "valid-PUT-with-R1R2",
        f"journal is rewritten with adopted row: {journal_rows}")
    return bad


def test_fresh_no_valid_run_does_not_adopt_stale_valid_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        unit = stale_dir / "put" / "approve"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        (unit / "Project" / "foundry.toml").write_text("[profile.default]\n")
        put_file.write_text("contract T { function test_cov() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov",
                "file": str(put_file),
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [{
                        "kind": "concrete",
                        "test": "test_cov",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }],
                },
            }))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_jsonl": str(case_dir / "cert" / "certify-results.jsonl"),
            "cert_bucket_counts": {
                "NO-WITNESS-UNKNOWN": 1,
            },
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(target, "peer182", case_dir, current)
    return check(stale is None, f"fresh no-valid evidence suppresses stale valid adoption: {stale}")


def test_fresh_no_unit_no_valid_run_does_not_adopt_stale_valid_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        unit = stale_dir / "put" / "deploy_only"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "DeployOnly.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_cov() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov",
                "file": str(put_file),
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [{
                        "kind": "concrete",
                        "test": "test_cov",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }],
                },
            }))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "no-units",
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(target, "peer182", case_dir, current)
    return check(stale is None, f"fresh no-unit/no-valid suppresses stale adoption: {stale}")


def test_resource_killed_zero_valid_run_can_adopt_stale_valid_artifacts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        input_identity = verifier_input_identity(subject_dir)
        toolchain_identity = stage4_toolchain_identity()
        write_stale_valid_result(stale_dir,
                                 verifier_identity=input_identity,
                                 stage4_identity=toolchain_identity)
        unit = stale_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        (unit / "Project" / "foundry.toml").write_text("[profile.default]\n")
        put_file.write_text("contract T { function test_cov() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov",
                "file": str(put_file),
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [{
                        "kind": "concrete",
                        "test": "test_cov",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }],
                },
            }))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_jsonl": str(case_dir / "cert" / "certify-results.jsonl"),
            "cert_bucket_counts": {
                "KILLED": 4,
            },
            "cert_exit_counts": {
                "124": 4,
            },
            "cert_timed_out_units": ["transfer"],
            "driver_diagnostic_tags": {
                "path-coverage-partial-journal-only": 4,
            },
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc",
                "sha256": "same-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "same-pipeline",
                },
            },
            "verifier_input_identity": input_identity,
            "stage4_toolchain_identity": toolchain_identity,
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(fake_home / ".foundry" / "bin" / "forge",
                         "#!/bin/sh\nprintf '{\"test/TokenCovTest.t.sol:Suite\":{\"test_cov\":{\"status\":\"Success\"}}}\\n'\n")
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            stale = rq1_veriput_run._best_stale_artifact_row(
                target, "peer182", case_dir, current)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(
        stale is not None and stale.get("valid") == 1,
        f"resource-killed zero-valid run can adopt stronger stale artifacts: {stale}")


def test_resource_killed_zero_valid_rejects_stale_when_replay_fails():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        input_identity = verifier_input_identity(subject_dir)
        toolchain_identity = stage4_toolchain_identity()
        put_file = stale_dir / "put" / "transfer" / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        (put_file.parents[1] / "foundry.toml").write_text("[profile.default]\n")
        put_file.write_text("contract T { function test_cov() public {} }\n")
        write_stale_valid_result(stale_dir,
                                 verifier_identity=input_identity,
                                 stage4_identity=toolchain_identity)
        doc = json.loads((stale_dir / "result.json").read_text())
        doc["row"]["valid_tests"] = [{
            "kind": "concrete",
            "test": "test_cov",
            "file": str(put_file),
            "forge_status": "Success",
            "valid_reference_test": True,
        }]
        (stale_dir / "result.json").write_text(json.dumps(doc))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc",
                "sha256": "same-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "same-pipeline",
                },
            },
            "verifier_input_identity": input_identity,
            "stage4_toolchain_identity": toolchain_identity,
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(fake_home / ".foundry" / "bin" / "forge",
                         "#!/bin/sh\nexit 1\n")
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            stale = rq1_veriput_run._best_stale_artifact_row(
                target, "peer182", case_dir, current)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(stale is None,
                 f"resource-killed stale adoption rejects failed current replay: {stale}")


def test_stale_replay_rejects_no_tests_exit_zero():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "Project"
        test_file = project / "test" / "TokenCovTest.t.sol"
        test_file.parent.mkdir(parents=True)
        (project / "foundry.toml").write_text("[profile.default]\n")
        test_file.write_text("contract T {}\n")
        row = {
            "valid_tests": [{
                "test": "test_cov",
                "file": str(test_file),
                "valid_reference_test": True,
            }],
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(fake_home / ".foundry" / "bin" / "forge",
                         "#!/bin/sh\nprintf '{}\\n'\nexit 0\n")
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            ok = rq1_veriput_run._stale_valid_artifacts_replay_current_toolchain(row)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(not ok, "stale replay rejects forge no-tests exit 0")


def test_stale_replay_runs_and_matches_exact_function_signature():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "Project"
        test_file = project / "test" / "TokenCovTest.t.sol"
        test_file.parent.mkdir(parents=True)
        (project / "foundry.toml").write_text("[profile.default]\n")
        test_file.write_text(
            "contract T { function test_cov(uint256 value) public {} }\n")
        row = {
            "valid_tests": [{
                "test": "test_cov",
                "file": str(test_file),
                "valid_reference_test": True,
            }],
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        argv_file = root / "forge-argv"
        write_executable(
            fake_home / ".foundry" / "bin" / "forge",
            "#!/bin/sh\nprintf '%s\\n' \"$@\" > '" + str(argv_file) + "'\n"
            "printf '%s\\n' '{\"test/TokenCovTest.t.sol:Suite\":"
            "{\"test_results\":{\"test_cov(uint256)\":{\"status\":\"Success\"}}}}'\n")
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            ok = rq1_veriput_run._stale_valid_artifacts_replay_current_toolchain(row)
            argv = argv_file.read_text().splitlines()
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(ok and "^test_cov\\(" in argv,
                 f"stale replay executes the exact Forge function signature: {argv}")


def test_stale_replay_rejects_similar_test_name_collision():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "Project"
        test_file = project / "test" / "TokenCovTest.t.sol"
        test_file.parent.mkdir(parents=True)
        (project / "foundry.toml").write_text("[profile.default]\n")
        test_file.write_text("contract T {}\n")
        row = {
            "valid_tests": [{
                "test": "test_cov",
                "file": str(test_file),
                "valid_reference_test": True,
            }],
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(
            fake_home / ".foundry" / "bin" / "forge",
            "#!/bin/sh\nprintf '{\"test/TokenCovTest.t.sol:Suite\":{\"test_cov_extra\":{\"status\":\"Success\"}}}\\n'\n")
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            ok = rq1_veriput_run._stale_valid_artifacts_replay_current_toolchain(row)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(not ok, "stale replay rejects similar test-name collision")


def test_stale_replay_rejects_missing_recorded_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "Project"
        existing_file = project / "test" / "OtherTest.t.sol"
        missing_file = project / "test" / "TokenCovTest.t.sol"
        existing_file.parent.mkdir(parents=True)
        (project / "foundry.toml").write_text("[profile.default]\n")
        existing_file.write_text("contract T { function test_cov() public {} }\n")
        row = {
            "valid_tests": [{
                "test": "test_cov",
                "file": str(missing_file),
                "valid_reference_test": True,
            }],
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(
            fake_home / ".foundry" / "bin" / "forge",
            "#!/bin/sh\nprintf '{\"test/OtherTest.t.sol:Suite\":{\"test_cov\":{\"status\":\"Success\"}}}\\n'\n"
        )
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            ok = rq1_veriput_run._stale_valid_artifacts_replay_current_toolchain(row)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(not ok, "stale replay rejects missing recorded test file")


def test_stale_replay_rejects_same_test_name_in_other_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "Project"
        recorded_file = project / "test" / "TokenCovTest.t.sol"
        other_file = project / "test" / "OtherTest.t.sol"
        recorded_file.parent.mkdir(parents=True)
        (project / "foundry.toml").write_text("[profile.default]\n")
        recorded_file.write_text("contract T {}\n")
        other_file.write_text("contract U { function test_cov() public {} }\n")
        row = {
            "valid_tests": [{
                "test": "test_cov",
                "file": str(recorded_file),
                "valid_reference_test": True,
            }],
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(
            fake_home / ".foundry" / "bin" / "forge",
            "#!/bin/sh\nprintf '{\"test/OtherTest.t.sol:Suite\":{\"test_cov\":{\"status\":\"Success\"}}}\\n'\n"
        )
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            ok = rq1_veriput_run._stale_valid_artifacts_replay_current_toolchain(row)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(not ok, "stale replay rejects same test name in another file")


def test_stale_replay_rejects_project_without_foundry_toml():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = root / "Project"
        test_file = project / "test" / "TokenCovTest.t.sol"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("contract T { function test_cov() public {} }\n")
        row = {
            "valid_tests": [{
                "test": "test_cov",
                "file": str(test_file),
                "valid_reference_test": True,
            }],
        }
        old_home = os.environ.get("HOME")
        old_path = os.environ.get("PATH")
        fake_home = root / "fake-home"
        write_executable(
            fake_home / ".foundry" / "bin" / "forge",
            "#!/bin/sh\nprintf '{\"test/TokenCovTest.t.sol:Suite\":{\"test_cov\":{\"status\":\"Success\"}}}\\n'\n"
        )
        os.environ["HOME"] = str(fake_home)
        os.environ["PATH"] = ""
        try:
            ok = rq1_veriput_run._stale_valid_artifacts_replay_current_toolchain(row)
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
    return check(not ok, "stale replay rejects Project without foundry.toml")


def test_resource_killed_zero_valid_requires_schedule_identity():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        unit = stale_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_cov() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov",
                "file": str(put_file),
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [{
                        "kind": "concrete",
                        "test": "test_cov",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }],
                },
            }))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption requires schedule identity: {stale}")


def test_resource_killed_zero_valid_rejects_newer_source():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        flat = subject_dir / "flat.sol"
        newer_ts = 1893459600
        os.utime(flat, (newer_ts, newer_ts))
        unit = stale_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_cov() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov",
                "file": str(put_file),
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [{
                        "kind": "concrete",
                        "test": "test_cov",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }],
                },
            }))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects newer source: {stale}")


def test_resource_killed_zero_valid_rejects_different_source_digest():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        old_subject_dir = root / "prepared-old" / "subject"
        current_subject_dir = root / "prepared-new" / "subject"
        write_minimal_schedule(stale_dir, subject_dir=old_subject_dir)
        write_minimal_schedule(case_dir, subject_dir=current_subject_dir)
        current_flat = current_subject_dir / "flat.sol"
        current_flat.write_text("contract Token { uint256 changed; }\n")
        old_ts = 1893450000
        os.utime(current_flat, (old_ts, old_ts))
        unit = stale_dir / "put" / "transfer"
        wd = unit / "_wd" / "row"
        wd.mkdir(parents=True)
        put_file = unit / "Project" / "test" / "TokenCovTest.t.sol"
        put_file.parent.mkdir(parents=True)
        put_file.write_text("contract T { function test_cov() public {} }\n")
        (wd / "put.json").write_text(
            json.dumps({
                "kind": "concrete",
                "test": "test_cov",
                "file": str(put_file),
            }))
        (unit / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [{
                        "kind": "concrete",
                        "test": "test_cov",
                        "file": str(put_file),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }],
                },
            }))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects source digest mismatch: {stale}")


def test_resource_killed_zero_valid_rejects_different_solast_digest():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        stale_input_identity = verifier_input_identity(subject_dir)
        solast = subject_dir / "subject.solast"
        solast.write_text('{"nodeType":"SourceUnit","nodes":[{"nodeType":"ContractDefinition"}]}\n')
        old_ts = 1893450000
        os.utime(solast, (old_ts, old_ts))
        current_input_identity = verifier_input_identity(subject_dir)
        toolchain_identity = stage4_toolchain_identity()
        write_stale_valid_result(stale_dir,
                                 verifier_identity=stale_input_identity,
                                 stage4_identity=toolchain_identity)
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc",
                "sha256": "same-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "same-pipeline",
                },
            },
            "verifier_input_identity": current_input_identity,
            "stage4_toolchain_identity": toolchain_identity,
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects solast digest mismatch: {stale}")


def test_resource_killed_zero_valid_rejects_different_cached_solast_digest():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        ast_cache_root = root / "ast-cache"
        cached_solast = ast_cache_root / "peer182" / "subject" / "subject.solast"
        write_minimal_schedule(case_dir,
                               subject_dir=subject_dir,
                               ast_cache_root=ast_cache_root,
                               solast_path=cached_solast)
        write_minimal_schedule(stale_dir,
                               subject_dir=subject_dir,
                               ast_cache_root=ast_cache_root,
                               solast_path=cached_solast)
        stale_input_identity = verifier_input_identity(subject_dir, solast_path=cached_solast)
        cached_solast.write_text(
            '{"nodeType":"SourceUnit","nodes":[{"nodeType":"ContractDefinition"}]}\n')
        old_ts = 1893450000
        os.utime(cached_solast, (old_ts, old_ts))
        current_input_identity = verifier_input_identity(subject_dir, solast_path=cached_solast)
        toolchain_identity = stage4_toolchain_identity()
        write_stale_valid_result(stale_dir,
                                 verifier_identity=stale_input_identity,
                                 stage4_identity=toolchain_identity)
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc",
                "sha256": "same-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "same-pipeline",
                },
            },
            "verifier_input_identity": current_input_identity,
            "stage4_toolchain_identity": toolchain_identity,
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects cached solast mismatch: {stale}")


def test_resource_killed_zero_valid_rejects_binary_mismatch():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        input_identity = verifier_input_identity(subject_dir)
        toolchain_identity = stage4_toolchain_identity()
        write_stale_valid_result(stale_dir,
                                 binary_sha="old-binary",
                                 verifier_identity=input_identity,
                                 stage4_identity=toolchain_identity)
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc-new",
                "sha256": "new-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "same-pipeline",
                },
            },
            "verifier_input_identity": input_identity,
            "stage4_toolchain_identity": toolchain_identity,
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects ESBMC binary mismatch: {stale}")


def test_resource_killed_zero_valid_rejects_pipeline_mismatch():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        input_identity = verifier_input_identity(subject_dir)
        toolchain_identity = stage4_toolchain_identity()
        write_stale_valid_result(stale_dir,
                                 pipeline_files={"pipeline.py": "old"},
                                 verifier_identity=input_identity,
                                 stage4_identity=toolchain_identity)
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc",
                "sha256": "same-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "new",
                },
            },
            "verifier_input_identity": input_identity,
            "stage4_toolchain_identity": toolchain_identity,
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects pipeline mismatch: {stale}")


def test_resource_killed_zero_valid_rejects_stage4_toolchain_mismatch():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        case_dir = root / "peer182" / "subjects" / "subject"
        stale_dir = root / "peer182" / "subjects" / "subject.redo.1"
        subject_dir = root / "prepared" / "subject"
        write_minimal_schedule(case_dir, subject_dir=subject_dir)
        write_minimal_schedule(stale_dir, subject_dir=subject_dir)
        input_identity = verifier_input_identity(subject_dir)
        write_stale_valid_result(stale_dir,
                                 verifier_identity=input_identity,
                                 stage4_identity=stage4_toolchain_identity(forge_sha="old-forge"))
        current = {
            "subject_id": "subject",
            "contract": "Token",
            "benchmark": "peer182",
            "completion_status": "ok",
            "cert_bucket_counts": {
                "KILLED": 1,
            },
            "cert_timed_out_units": ["transfer"],
            "raw": 0,
            "valid": 0,
            "put_valid": 0,
            "concrete_valid": 0,
            "quality_bucket": "no-valid",
            "esbmc_binary_identity": {
                "path": "/tmp/esbmc",
                "sha256": "same-binary",
            },
            "pipeline_code_identity": {
                "schema": "veriput-pipeline-code-identity/v1",
                "files": {
                    "pipeline.py": "same-pipeline",
                },
            },
            "verifier_input_identity": input_identity,
            "stage4_toolchain_identity": stage4_toolchain_identity(forge_sha="new-forge"),
        }
        target = {
            "subject_id": "subject",
            "benchmark": "peer182",
            "contract": "Token",
        }
        stale = rq1_veriput_run._best_stale_artifact_row(
            target, "peer182", case_dir, current)
    return check(stale is None,
                 f"resource-killed stale adoption rejects Stage4 toolchain mismatch: {stale}")


def test_resume_quality_floor_can_focus_no_put_and_no_r1r2():
    no_put = {
        "subject_id": "concrete",
        "status": "ok",
        "valid": 1,
        "concrete_valid": 1,
        "put_valid": 0,
        "quality_bucket": "valid-no-PUT",
    }
    no_r1r2 = {
        "subject_id": "r0",
        "status": "ok",
        "valid": 1,
        "put_valid": 1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R0"],
        }],
    }
    strong = {
        "subject_id":
        "strong",
        "status":
        "ok",
        "valid":
        1,
        "put_valid":
        1,
        "valid_tests": [{
            "kind": "put",
            "valid_reference_test": True,
            "oracle_classes": ["R1", "R2"],
        }],
    }
    done = {
        "gen:veriput:concrete": no_put,
        "gen:veriput:r0": no_r1r2,
        "gen:veriput:strong": strong,
    }
    default = rq1_veriput_run.retryable_resume_rows(done)
    focused = rq1_veriput_run.retryable_resume_rows(done, "valid-PUT-with-R1R2")
    bad = 0
    bad += check(
        set(default) == {"gen:veriput:concrete", "gen:veriput:r0"},
        f"default resume improves weak valid rows: {default}")
    bad += check(
        set(focused) == {"gen:veriput:concrete", "gen:veriput:r0"},
        f"quality floor focuses no-PUT/no-R1R2 rows: {focused}")
    legacy = rq1_veriput_run.retryable_resume_rows(done, "no-valid")
    bad += check(legacy == {}, f"legacy no-valid floor still preserves valid rows: {legacy}")
    return bad


def test_empty_schedule_status_preserves_preparation_failures():
    prep_failed = {
        "summary": {
            "jobs": 0,
            "skipped_by_status": {
                "missing-ast": 1
            },
        },
        "skipped_rows": [{
            "status": "missing-ast",
            "reason": "/tmp/cache/C.solast does not exist",
        }],
    }
    no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [{
            "reason":
            "target contract has no public/external FunctionDefinition units",
        }],
    }
    special_only = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
            "skipped_units": 2,
        },
        "no_unit_rows": [{
            "reason": ("target contract exposes only fallback/receive entries; "
                       "use deploy-only concrete fallback"),
        }],
    }
    summary_only_no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [],
    }
    filtered_empty = {
        "summary": {
            "jobs": 0,
            "jobs_before_unit_filter": 3,
            "unit_filter": ["missingUnit"],
        },
        "jobs": [],
        "no_unit_rows": [],
    }
    bad = 0
    status, reason = rq1_veriput_run._empty_schedule_status_reason(prep_failed)
    bad += check(status == "error", f"missing AST schedule is a preparation error: {status}")
    bad += check("missing-ast=1" in reason and "/tmp/cache/C.solast" in reason,
                 f"missing AST reason is retained: {reason}")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(no_units)
    bad += check(status == "no-units", f"true no-unit schedule remains no-units: {status}")
    bad += check("no public/external" in reason, f"true no-unit reason is retained: {reason}")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(special_only)
    bad += check(status == "no-units",
                 f"special-entry-only schedule remains deploy-fallback eligible: {status}")
    bad += check("fallback/receive" in reason, f"special-entry-only reason is retained: {reason}")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(summary_only_no_units)
    bad += check(status == "no-units", f"summary-only no-unit schedule remains eligible: {status}")
    bad += check(rq1_veriput_run._is_true_no_unit_schedule(summary_only_no_units),
                 "summary-only no-unit schedule triggers deploy fallback")
    library_no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [{
            "reason": ("target contract is a library, so no external transaction "
                       "unit is schedulable"),
            "skipped": [{
                "kind": "library-contract",
                "contract": "Multicall",
            }],
        }],
    }
    status, reason = rq1_veriput_run._empty_schedule_status_reason(library_no_units)
    bad += check(status == "no-units" and "library" in reason,
                 f"library no-unit reason is retained: {status}, {reason}")
    bad += check(not rq1_veriput_run._no_unit_schedule_allows_deploy_fallback(library_no_units),
                 "library target must not get deploy fallback")
    concrete_derived_no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [{
            "subject": {
                "contract": "Dv2",
            },
            "reason": "target only has constructor-level behavior and no named callable unit",
            "skipped": [{
                "kind": "constructor",
                "contract": "Dv2",
            }, {
                "kind": "abstract-contract",
                "contract": "Cv2",
            }],
        }],
    }
    bad += check(
        rq1_veriput_run._no_unit_schedule_allows_deploy_fallback(concrete_derived_no_units),
        "abstract base does not block concrete selected target deploy fallback")
    abstract_target_no_units = {
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1,
        },
        "no_unit_rows": [{
            "target": {
                "contract": "Cv2",
            },
            "reason": "target has no implemented public/external function body",
            "skipped": [{
                "kind": "abstract-contract",
                "contract": "Cv2",
            }],
        }],
    }
    bad += check(
        not rq1_veriput_run._no_unit_schedule_allows_deploy_fallback(abstract_target_no_units),
        "abstract selected target remains ineligible for deploy fallback")
    status, reason = rq1_veriput_run._empty_schedule_status_reason(filtered_empty)
    bad += check(status == "no-output" and "unit filter" in reason,
                 f"filtered-empty schedule is not mislabeled no-units: "
                 f"{status}, {reason}")
    bad += check(not rq1_veriput_run._is_true_no_unit_schedule(filtered_empty),
                 "filtered-empty schedule does not trigger deploy fallback")
    return bad


def test_no_unit_deploy_source_rejects_abstract_selected_target():
    subject = rq1_veriput_run.PreparedSubject(
        subject_id="abstract",
        benchmark="peer182",
        root="/tmp/abstract",
        flat_sol="/tmp/abstract/flat.sol",
        solast="/tmp/abstract/flat.sol.solast",
        contract="Cv2",
        unit="",
        solc_bin="solc",
        solc_extra=(),
        metadata={"status": "ok", "solc": "0.8.20"},
    )
    source, refusal = rq1_veriput_run._no_unit_deploy_test_source(
        subject, "abstract contract Cv2 { constructor(int256) {} }")
    bad = 0
    bad += check(source is None, "abstract selected target emits no deploy source")
    bad += check(refusal is not None and "target is abstract" in refusal,
                 f"abstract selected target refusal is explicit: {refusal}")
    concrete = rq1_veriput_run.PreparedSubject(
        subject_id="concrete",
        benchmark="peer182",
        root="/tmp/concrete",
        flat_sol="/tmp/concrete/flat.sol",
        solast="/tmp/concrete/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin="solc",
        solc_extra=(),
        metadata={"status": "ok", "solc": "0.8.20"},
    )
    revert_source, revert_refusal = rq1_veriput_run._constructor_revert_test_source(
        concrete, "contract C { constructor() {} function f() public { assert(false); } }")
    bad += check(revert_source is None and "constructor" in str(revert_refusal),
                 "assertion in an unrelated method cannot authorize constructor revert validity")
    return bad


def test_no_unit_deploy_fallback_writes_raw_concrete_artifact():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subj_root = root / "subject"
        subj_root.mkdir()
        flat = subj_root / "flat.sol"
        flat.write_text("""\
pragma solidity ^0.8.20;
contract C {
  address public owner;
  string public name;
  int256 public delta;
  constructor(address owner_, string memory name_, int256 delta_) {
    require(owner_ != address(0), "owner");
    require(bytes(name_).length != 0, "name");
    owner = owner_;
    name = name_;
    delta = delta_;
  }
}
""")
        subject = rq1_veriput_run.PreparedSubject(
            benchmark="peer182",
            subject_id="no_unit_C",
            root=str(subj_root),
            flat_sol=str(flat),
            solast=str(subj_root / "flat.sol.solast"),
            contract="C",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={"status": "ok"},
        )
        schedule = {
            "jobs": [],
            "summary": {
                "jobs": 0,
                "no_unit_rows": 1
            },
            "no_unit_rows": [{
                "reason":
                "target contract has no public/external FunctionDefinition units",
            }],
        }

        def fake_forge(_project, test_name, _timeout):
            out = json.dumps({
                "test/CDeployOnlyCovTest.t.sol:CDeployOnlyCovTest": {
                    "test_results": {
                        f"{test_name}()": {
                            "status": "Success"
                        },
                    },
                },
            })
            return "Success", False, 0.01, out

        stage = rq1_veriput_run.emit_no_unit_deploy_fallback(subject,
                                                             root / "case",
                                                             schedule,
                                                             1,
                                                             forge_runner=fake_forge)
        summary = rq1_veriput_run.summarize_put_artifacts(root / "case" / "put")
        test_file = Path(stage["test_file"])
        text = test_file.read_text()
    bad = 0
    bad += check(stage["status"] == "ok", f"deploy-only fallback stage is green: {stage}")
    bad += check("new C(address(uint160(1000)), \"VeriPUT1001\", int256(1))" in text,
                 f"constructor args are synthesized safely: {text}")
    bad += check('import "../src/flat.sol"' in text,
                 f"deploy-only test imports the copied source: {text}")
    bad += check(summary["raw"] == 1 and summary["valid"] == 0,
                 f"deploy-only artifact is raw but not RQ1-valid: {summary}")
    bad += check(
        summary["concrete_raw"] == 1 and summary["concrete_valid"] == 0 and summary["put_raw"] == 0,
        f"deploy-only artifact is concrete, not PUT: {summary}")
    bad += check(summary["quality_bucket"] == "no-valid",
                 f"deploy-only smoke test does not become valid-no-PUT: {summary}")
    return bad


def test_no_unit_deploy_fallback_uses_prepared_source_fallback():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subject_id = "relocated_no_unit_subject"
        old_flat = root / "Results" / "Peer182" / "subjects" / subject_id / "flat.sol"
        fallback = (root / "scripts" / "Results" / "workdirs" / "Peer182" / "subjects" /
                    subject_id / "flat.sol")
        fallback.parent.mkdir(parents=True)
        fallback.write_text("""\
pragma solidity ^0.8.20;
contract RelocatedNoUnit {
  constructor() {}
}
""")
        subject = rq1_veriput_run.PreparedSubject(
            benchmark="peer182",
            subject_id=subject_id,
            root=str(old_flat.parent),
            flat_sol=str(old_flat),
            solast=str(old_flat.with_name("flat.sol.solast")),
            contract="RelocatedNoUnit",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={"status": "ok"},
        )
        legacy_schedule = {
            "jobs": [],
            "summary": {
                "jobs": 0,
                "subjects": 0,
                "skipped_by_status": {},
            },
        }

        def fake_forge(_project, test_name, _timeout):
            out = json.dumps({
                "test/RelocatedNoUnitDeployOnlyCovTest.t.sol:"
                "RelocatedNoUnitDeployOnlyCovTest": {
                    "test_results": {
                        f"{test_name}()": {
                            "status": "Success"
                        },
                    },
                },
            })
            return "Success", False, 0.01, out

        old_root = rq1_veriput_run.DEFAULT_VERIPUT_ROOT
        rq1_veriput_run.DEFAULT_VERIPUT_ROOT = root
        try:
            stage = rq1_veriput_run.emit_no_unit_deploy_fallback(subject,
                                                                 root / "case",
                                                                 legacy_schedule,
                                                                 1,
                                                                 forge_runner=fake_forge)
        finally:
            rq1_veriput_run.DEFAULT_VERIPUT_ROOT = old_root

        copied = (root / "case" / "put" / "deploy_only" / "Project" / "src" / "flat.sol")
        copied_text = copied.read_text()
        summary = rq1_veriput_run.summarize_put_artifacts(root / "case" / "put")

    bad = 0
    bad += check(stage["status"] == "ok",
                 f"relocated prepared source still emits fallback: {stage}")
    bad += check("RelocatedNoUnit" in copied_text,
                 f"fallback flat.sol was copied from workdir source: {copied}")
    bad += check(summary["raw"] == 1 and summary["valid"] == 0 and summary["concrete_valid"] == 0,
                 f"relocated fallback remains raw-only: {summary}")
    return bad


def test_no_unit_constructor_revert_fallback_is_behavioral_valid():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subj_root = root / "subject"
        subj_root.mkdir()
        flat = subj_root / "flat.sol"
        flat.write_text("""\
pragma solidity ^0.8.20;
abstract contract Base {}
contract C is Base {
  uint256 x;
  constructor(int256 a) {
    x = a > 0 ? 2 : 3;
    assert(x == 1);
  }
}
""")
        subject = rq1_veriput_run.PreparedSubject(
            benchmark="peer182",
            subject_id="constructor_revert_C",
            root=str(subj_root),
            flat_sol=str(flat),
            solast=str(subj_root / "flat.sol.solast"),
            contract="C",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={"status": "ok"},
        )
        schedule = {
            "jobs": [],
            "summary": {
                "jobs": 0,
                "no_unit_rows": 1,
            },
            "no_unit_rows": [{
                "subject": {
                    "contract": "C",
                },
                "skipped": [{
                    "contract": "C",
                    "kind": "constructor",
                }, {
                    "contract": "Base",
                    "kind": "abstract-contract",
                }],
            }],
        }
        calls = []

        def fake_forge(_project, test_name, _timeout):
            calls.append(test_name)
            status = "Success" if test_name.endswith("_constructor_revert") else "Failure"
            return status, False, 0.01, json.dumps({"test": test_name, "status": status})

        stage = rq1_veriput_run.emit_no_unit_deploy_fallback(
            subject, root / "case", schedule, 1, forge_runner=fake_forge)
        summary = rq1_veriput_run.summarize_put_artifacts(root / "case" / "put")
        test_text = Path(stage["test_file"]).read_text()
        put_json = json.loads(next((root / "case" / "put").glob("*/_wd/*/put.json")).read_text())
    bad = 0
    bad += check(calls[0].endswith("_deploy_only")
                 and any("_constructor_repair_" in name for name in calls),
                 f"deployment and source-derived argument repair run first: {calls}")
    bad += check(calls[-1].endswith("_constructor_revert"),
                 f"negative constructor oracle is the final behavioral retry: {calls}")
    bad += check("vm.expectRevert();" in test_text and "new C(int256(1));" in test_text,
                 f"negative oracle replays the exact concrete constructor call: {test_text}")
    bad += check(stage["status"] == "ok" and summary["valid"] == 1
                 and summary["concrete_valid"] == 1 and summary["put_valid"] == 0,
                 f"behavioral constructor oracle is valid concrete, not PUT: {summary}")
    bad += check(put_json["stage4_kind"] == "constructor-revert-only"
                 and put_json["stage2_source"] == "source_constructor_revert_fallback",
                 f"behavioral fallback has a distinct auditable provenance: {put_json}")
    return bad


def test_constructor_arg_repair_deploy_is_still_smoke_only():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subj_root = root / "subject"
        subj_root.mkdir()
        flat = subj_root / "flat.sol"
        flat.write_text("contract C { constructor(int256 a) { require(a != 1); } }\n")
        subject = rq1_veriput_run.PreparedSubject(
            benchmark="peer182",
            subject_id="repair_C",
            root=str(subj_root),
            flat_sol=str(flat),
            solast=str(subj_root / "flat.sol.solast"),
            contract="C",
            unit="",
            solc_bin=None,
            solc_extra=(),
            metadata={"status": "ok"},
        )
        schedule = {
            "jobs": [],
            "summary": {"jobs": 0, "no_unit_rows": 1},
            "no_unit_rows": [{
                "subject": {"contract": "C"},
                "skipped": [{"contract": "C", "kind": "constructor"}],
            }],
        }
        calls = []

        def fake_forge(_project, test_name, timeout):
            calls.append((test_name, timeout))
            status = "Success" if "constructor_repair" in test_name else "Failure"
            return status, False, 0.01, ""

        rq1_veriput_run.emit_no_unit_deploy_fallback(
            subject, root / "case", schedule, 7, forge_runner=fake_forge)
        summary = rq1_veriput_run.summarize_put_artifacts(root / "case" / "put")
        put_json = json.loads(next((root / "case" / "put").glob("*/_wd/*/put.json")).read_text())
    bad = 0
    bad += check(put_json["stage4_kind"] == "constructor-arg-repair",
                 f"source-derived repair provenance is retained: {put_json}")
    bad += check(summary["raw"] == 1 and summary["valid"] == 0,
                 f"successful repaired deployment remains an invalid smoke test: {summary}")
    bad += check(all(1 <= timeout <= 7 for _name, timeout in calls),
                 f"constructor retries are bounded by the shared Forge cap: {calls}")
    return bad


def _prepared_subject_for_getter_test(root):
    subj_root = root / "subject"
    subj_root.mkdir(parents=True, exist_ok=True)
    flat = subj_root / "flat.sol"
    flat.write_text("contract C { uint256 public value; mapping(address => uint256) public balance; }\n")
    return rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="getter_subject",
        root=str(subj_root),
        flat_sol=str(flat),
        solast=str(subj_root / "flat.sol.solast"),
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={"status": "ok"},
    )


def _no_unit_getter_schedule(*skipped, jobs=None):
    return {
        "jobs": list(jobs or []),
        "summary": {
            "jobs": len(jobs or []),
            "no_unit_rows": 1,
        },
        "no_unit_rows": [{
            "reason": "target contract has no public/external FunctionDefinition units",
            "skipped": list(skipped),
        }],
    }


def test_no_unit_getter_fallback_selects_only_fresh_zero_arg_getters():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subject = _prepared_subject_for_getter_test(root)
        schedule = _no_unit_getter_schedule(
            {
                "kind": "public-state-getter",
                "name": "value",
                "parameter_count": 0,
            },
            {
                "kind": "public-state-getter",
                "name": "balance",
                "parameter_count": 1,
            },
            {
                "kind": "public-state-getter",
                "name": "stale",
                "parameter_count": 0,
            },
        )
        class GetterEnum:
            skipped = (
                {
                    "kind": "public-state-getter",
                    "name": "value",
                    "parameter_count": 0,
                },
                {
                    "kind": "public-state-getter",
                    "name": "balance",
                    "parameter_count": 1,
                },
            )

        enum = GetterEnum()
        calls = []
        old_enum = rq1_veriput_run.enumerate_subject_units
        old_run = rq1_veriput_run.run_command
        rq1_veriput_run.enumerate_subject_units = lambda _subject: enum

        def fake_run_command(argv, timeout_s, log_prefix):
            calls.append((argv, timeout_s, log_prefix))
            out_root = Path(argv[argv.index("--out-root") + 1])
            test_file = out_root / "Project" / "test" / "GetterCovTest.t.sol"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("contract T { function test_value() public {} }\n")
            wd = out_root / "_wd" / "getter"
            wd.mkdir(parents=True, exist_ok=True)
            (wd / "put.json").write_text(
                json.dumps({
                    "kind": "concrete",
                    "stage4_kind": "getter-only",
                    "unit": "value",
                    "test": "test_value",
                    "file": str(test_file),
                    "forge_status": "Success",
                    "valid_reference_test": True,
                }))
            (out_root / "put-summary.json").write_text(
                json.dumps({
                    "schema": "veriput-put-summary/1",
                    "emission": {
                        "puts_emitted": 0,
                        "concrete_replays_emitted": 1,
                    },
                    "deliverable_b": {
                        "valid_reference_tests": {
                            "total": 1,
                            "put": 0,
                            "concrete": 1,
                        },
                        "rows": [{
                            "kind": "concrete",
                            "stage4_kind": "getter-only",
                            "unit": "value",
                            "test": "test_value",
                            "file": str(test_file),
                            "forge_status": "Success",
                            "valid_reference_test": True,
                        }],
                    },
                }))
            return {
                "argv": argv,
                "rc": 0,
                "status": "ok",
                "timed_out": False,
                "wall_s": 0.1,
            }

        rq1_veriput_run.run_command = fake_run_command
        try:
            stages = rq1_veriput_run.emit_no_unit_getter_fallbacks(
                subject, root / "case", schedule, 30, 12, 3)
        finally:
            rq1_veriput_run.enumerate_subject_units = old_enum
            rq1_veriput_run.run_command = old_run

        cert_rows = [
            json.loads(line) for line in
            (root / "case" / "put" / "structural_getter__value" /
             "static-getter-cert.jsonl").read_text().splitlines() if line.strip()
        ]
        summary = rq1_veriput_run.summarize_put_artifacts(root / "case" / "put")

    bad = 0
    bad += check([stage.get("unit") for stage in stages] == ["value"],
                 f"only fresh zero-arg getter is attempted: {stages}")
    bad += check(len(calls) == 1 and calls[0][0][calls[0][0].index("--only") + 1].endswith(".value"),
                 f"getter Stage4 selector is name-scoped: {calls}")
    bad += check(cert_rows[0]["tag"] == "static-abi-getter-certified"
                 and cert_rows[0]["certified_details"]["0"]["stage4_kind"] == "getter-only",
                 f"getter cert row is structural getter-only: {cert_rows}")
    bad += check(summary["valid"] == 1 and summary["concrete_valid"] == 1
                 and summary["put_valid"] == 0
                 and summary["quality_bucket"] == "valid-no-PUT",
                 f"getter artifact is valid concrete, not deploy-only or PUT: {summary}")
    return bad


def test_no_unit_getter_fallback_rejects_non_no_unit_schedule():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subject = _prepared_subject_for_getter_test(root)
        schedule = _no_unit_getter_schedule(
            {
                "kind": "public-state-getter",
                "name": "value",
                "parameter_count": 0,
            },
            jobs=[{
                "unit": "existing"
            }],
        )
        old_enum = rq1_veriput_run.enumerate_subject_units
        rq1_veriput_run.enumerate_subject_units = lambda _subject: (_ for _ in ()).throw(
            AssertionError("getter enumeration must not run for non-no-unit schedule"))
        try:
            stages = rq1_veriput_run.emit_no_unit_getter_fallbacks(
                subject, root / "case", schedule, 30, 12, 3)
        finally:
            rq1_veriput_run.enumerate_subject_units = old_enum
    return check(stages == [], f"existing jobs disable getter fallback: {stages}")


def _ownable_fixture_subject(root, source, contract):
    subject_dir = root / "subject"
    subject_dir.mkdir()
    flat_sol = subject_dir / "flat.sol"
    flat_sol.write_text(source)
    solast = subject_dir / "flat.sol.solast"
    solast.write_text(json.dumps({
        "nodeType": "SourceUnit",
        "nodes": [{
            "nodeType": "ContractDefinition",
            "id": 10,
            "name": "Ownable",
            "linearizedBaseContracts": [10],
            "nodes": [{
                "nodeType": "VariableDeclaration",
                "id": 39,
                "name": "_owner",
                "stateVariable": True,
                "visibility": "private",
                "typeDescriptions": {"typeString": "address"},
            }, {
                "nodeType": "FunctionDefinition",
                "id": 93,
                "name": "owner",
                "stateMutability": "view",
                "parameters": {"parameters": []},
                "body": {
                    "nodeType": "Block",
                    "statements": [{
                        "nodeType": "Return",
                        "expression": {
                            "nodeType": "Identifier",
                            "name": "_owner",
                            "referencedDeclaration": 39,
                        },
                    }],
                },
            }],
        }, {
            "nodeType": "ContractDefinition",
            "id": 20,
            "name": contract,
            "linearizedBaseContracts": [20, 10],
            "nodes": [],
        }],
    }))
    return rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id=f"self-contained__{contract}",
        root=str(subject_dir),
        flat_sol=str(flat_sol),
        solast=str(solast),
        contract=contract,
        unit="owner",
        solc_bin=None,
        solc_extra=(),
        metadata={})


def _ownable_fixture_schedule(job_id):
    return {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": job_id,
            "unit": "owner",
            "path_function": "sol:@C@Ownable@F@owner#93",
            "certify_argv": ["python3", "certify_all.py", "--probes", "8",
                             "--env-coord", "msg.sender"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run",
                              "--probes", "8", "--env-coord", "msg.sender"],
            "unit_info": {
                "parameter_count": 0,
                "return_types": ["address"],
                "state_mutability": "view",
            },
        }],
    }


def test_ownable_owner_stage2_fixture_uses_esbmc_store_name():
    source = """
abstract contract Ownable {
    address private _owner;
    constructor(address initialOwner) {
        if (initialOwner == address(0)) {
            revert InvalidOwner(initialOwner);
        }
        _transferOwnership(initialOwner);
    }
    function owner() public view virtual returns (address) { return _owner; }
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
    error InvalidOwner(address ownerValue);
    event OwnershipTransferred(address indexed oldOwner, address indexed newOwner);
}
interface IGatewayProvider {}
contract GatewayProvider is Ownable, IGatewayProvider {
    string[] private _urls;
    constructor(address ownerValue, string[] memory urls) Ownable(ownerValue) {
        _urls = urls;
    }
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subject = _ownable_fixture_subject(root, source, "GatewayProvider")
        schedule = _ownable_fixture_schedule("gateway-owner")
        out = rq1_veriput_run.apply_source_stage2_fixtures(
            schedule, subject, root / "case")
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    bad = 0
    bad += check(fixture["contract"] == "GatewayProvider",
                 f"fixture names exact target contract: {fixture}")
    bad += check(fixture["skip_constructor"] is True,
                 f"fixture skips ESBMC constructor: {fixture}")
    bad += check(fixture["state"] == {
        "_owner$39": "0x00000000000000000000000000000000000003e8"
    }, f"fixture uses ESBMC store name, not source name: {fixture}")
    bad += check(fixture["foundry"]["constructor_args"] == [
        "address(uint160(1000))",
        "new string[](0)",
    ], f"Foundry replay uses legal constructor args: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"certify argv carries fixture: {job['certify_argv']}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["dry_run_argv"],
                 f"dry-run argv carries fixture: {job['dry_run_argv']}")
    bad += check(out["summary"]["source_stage2_fixture_count"] == 1,
                 f"schedule records fixture application: {out}")
    bad += check(
        fixture["source_evidence"]["constructor_flow"] ==
        "GatewayProvider.ownerValue -> Ownable.initialOwner -> _owner",
        f"fixture records the exact source initialization chain: {fixture}")
    return bad


def test_ownable_msg_sender_owner_stage2_fixture_uses_esbmc_store_name():
    source = """
abstract contract Ownable {
    address private _owner;
    constructor() { _transferOwnership(_msgSender()); }
    function _msgSender() internal view returns (address) { return msg.sender; }
    function owner() public view virtual returns (address) { return _owner; }
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
    event OwnershipTransferred(address indexed oldOwner, address indexed newOwner);
}
contract OwnedResolver is Ownable {}
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subject = _ownable_fixture_subject(root, source, "OwnedResolver")
        schedule = _ownable_fixture_schedule("owned-resolver-owner")
        out = rq1_veriput_run.apply_source_stage2_fixtures(
            schedule, subject, root / "case")
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    bad = 0
    bad += check(fixture["state"] == {
        "_owner$39": "0x00000000000000000000000000000000000003e8"
    }, f"fixture uses inherited Ownable store name: {fixture}")
    bad += check(fixture["foundry"]["constructor_args"] == [],
                 f"Foundry replay preserves zero-argument deployment: {fixture}")
    bad += check(
        fixture["veriput_fixture_kind"] ==
        "ownable-owner-msg-sender-constructor-state",
        f"fixture records the source-backed constructor shape: {fixture}")
    bad += check(
        fixture["source_evidence"]["constructor_initialization"] ==
        "_transferOwnership(_msgSender())",
        f"fixture records the constructor initialization evidence: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"certify argv carries fixture: {job['certify_argv']}")
    return bad


def test_ownable_fixture_rejects_unrelated_nonzero_address_parameter():
    source = """
abstract contract Ownable {
    address private _owner;
    constructor() { _transferOwnership(_msgSender()); }
    function _msgSender() internal view returns (address) { return msg.sender; }
    function owner() public view virtual returns (address) { return _owner; }
    function _transferOwnership(address newOwner) internal virtual {
        _owner = newOwner;
    }
}
contract GuardedResolver is Ownable {
    constructor(address guardian) {
        require(guardian != address(0), "zero guardian");
    }
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subject = _ownable_fixture_subject(root, source, "GuardedResolver")
        schedule = _ownable_fixture_schedule("guarded-resolver-owner")
        out = rq1_veriput_run.apply_source_stage2_fixtures(
            schedule, subject, root / "case")
    bad = 0
    bad += check("source_stage2_fixture_path" not in out["jobs"][0],
                 f"unrelated address guard cannot synthesize owner state: {out}")
    bad += check(out["summary"].get("source_stage2_fixture_count", 0) == 0,
                 f"rejected fixture is not counted: {out}")
    return bad


def test_transparent_proxy_stage2_fixture_uses_zero_storage_runtime():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "compound-finance__comet__ConfiguratorProxy")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__compound-finance__comet__ConfiguratorProxy/flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: ConfiguratorProxy prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="compound-finance__comet__ConfiguratorProxy",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="ConfiguratorProxy",
        unit="admin",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema":
        "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "configurator-admin",
            "unit": "admin",
            "path_function": "sol:@C@ConfiguratorProxy@F@admin#447",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "visibility": "external",
                "parameter_count": 0,
                "return_types": ["address"],
                "state_mutability": "nonpayable",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check(fixture["contract"] == "ConfiguratorProxy" and fixture["skip_constructor"] is True,
                 f"proxy fixture skips only the exact target constructor: {fixture}")
    bad += check(fixture["state"] == {},
                 f"proxy fixture does not confuse slot constants with values: {fixture}")
    bad += check(fixture["foundry"] == {
        "skip_constructor": True,
        "target_call_mode": "low-level-success",
    },
                 f"Foundry mirrors zero storage through runtime etching: {fixture}")
    bad += check(fixture["veriput_fixture_kind"] == "transparent-proxy-zero-storage-runtime",
                 f"fixture records its narrow source-backed kind: {fixture}")
    bad += check(fixture["source_evidence"]["state_dependencies"] == ["_ADMIN_SLOT"],
                 f"fixture records admin slot dependency: {fixture}")
    bad += check("constructor_args" not in fixture["foundry"],
                 f"fixture must not run a state-changing Foundry constructor: {fixture}")
    bad += check(
        "--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
        and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
        f"certify argv carries proxy fixture: {job['certify_argv']}")
    bad += check(out["summary"]["source_stage2_fixture_count"] == 1,
                 f"schedule records proxy fixture application: {out}")
    return bad


def test_asset_list_stage2_fixture_uses_empty_array_revert_path():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "compound-finance__comet__AssetList")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__compound-finance__comet__AssetList/flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: AssetList prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="compound-finance__comet__AssetList",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="AssetList",
        unit="getAssetInfo",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "asset-list-get-asset-info",
            "unit": "getAssetInfo",
            "path_function": "sol:@C@AssetList@F@getAssetInfo#1055",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "parameter_types": ["uint8"],
                "return_types": ["struct CometCore.AssetInfo"],
                "state_mutability": "view",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check(fixture["contract"] == "AssetList" and fixture["skip_constructor"] is True,
                 f"AssetList fixture skips only its expensive ESBMC deployment: {fixture}")
    bad += check(fixture["state"] == {"numAssets": 0},
                 f"empty-list state uses the exact ESBMC immutable store name: {fixture}")
    bad += check(fixture["foundry"] == {
        "skip_constructor": True,
        "constructor_args": ["new CometConfiguration.AssetConfig[](0)"],
    }, f"Foundry replays the legal source-level empty-array deployment: {fixture}")
    bad += check(fixture["veriput_fixture_kind"] == "asset-list-empty-array-revert",
                 f"fixture records its narrow source-backed kind: {fixture}")
    bad += check(fixture["source_evidence"]["constructor_packed_slots"] == 24
                 and fixture["source_evidence"]["dominating_guard"] == "i >= numAssets",
                 f"fixture records the constructor and dominating guard evidence: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"certify argv carries AssetList fixture: {job['certify_argv']}")
    bad += check("--esbmc-arg=--path-cov-max-goals" in job["certify_argv"]
                 and "--esbmc-arg=2" in job["certify_argv"],
                 f"AssetList fixture retains its ABI gate and dominating revert goal: "
                 f"{job['certify_argv']}")
    bad += check(out["summary"]["source_stage2_fixture_count"] == 1,
                 f"schedule records AssetList fixture application: {out}")
    return bad


def test_euler_cash_stage2_fixture_uses_proxy_entry_zero_storage():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "euler-xyz__euler-vault-kit__Borrowing")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__euler-xyz__euler-vault-kit__Borrowing/flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: Euler Borrowing prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="euler-xyz__euler-vault-kit__Borrowing",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="Borrowing",
        unit="cash",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "euler-borrowing-cash",
            "unit": "cash",
            "path_function": "sol:@C@Borrowing@F@cash#3547",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "visibility": "public",
                "parameter_count": 0,
                "return_types": ["uint256"],
                "state_mutability": "view",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check(fixture["contract"] == "Borrowing" and fixture["skip_constructor"] is True,
                 f"Euler fixture skips only Borrowing's direct deployment: {fixture}")
    bad += check(fixture["state"] == {} and fixture["foundry"] == {"skip_constructor": True},
                 f"ESBMC and Foundry both use fresh runtime zero storage: {fixture}")
    bad += check(fixture["veriput_fixture_kind"] == "evk-cash-proxy-entry-zero-storage",
                 f"fixture records its narrow EVK source-backed kind: {fixture}")
    bad += check(fixture["source_evidence"]["state_dependencies"] == ["vaultStorage"]
                 and fixture["source_evidence"]["direct_base"] == "BorrowingModule",
                 f"fixture records the exact storage and inheritance evidence: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"certify argv carries Euler fixture: {job['certify_argv']}")
    bad += check(out["summary"]["source_stage2_fixture_count"] == 1,
                 f"schedule records Euler fixture application: {out}")
    return bad


def test_peg_stability_module_fixture_uses_legal_foundry_constructor():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "euler-xyz__euler-vault-kit__PegStabilityModule")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__euler-xyz__euler-vault-kit__PegStabilityModule/"
                  "flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: PegStabilityModule prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="euler-xyz__euler-vault-kit__PegStabilityModule",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="PegStabilityModule",
        unit="quoteToSynthGivenIn",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "psm-quote-to-synth-given-in",
            "unit": "quoteToSynthGivenIn",
            "path_function": "sol:@C@PegStabilityModule@F@quoteToSynthGivenIn#671",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "visibility": "public",
                "parameter_types": ["uint256"],
                "return_types": ["uint256"],
                "state_mutability": "view",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(
            schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check("skip_constructor" not in fixture,
                 f"PSM fixture must not alter Stage-2 constructor semantics: {fixture}")
    bad += check(fixture["foundry"]["constructor_args"] == [
        "address(uint160(1000))",
        "address(uint160(1001))",
        "address(uint160(1002))",
        "0",
        "0",
        "1e18",
    ], f"PSM Foundry deployment satisfies all exact constructor guards: {fixture}")
    bad += check(fixture["veriput_fixture_kind"] ==
                 "psm-legal-foundry-constructor",
                 f"fixture records its narrow source-backed kind: {fixture}")
    evidence = fixture["source_evidence"]
    bad += check(evidence["conversion_price_guard"] == "_conversionPrice != 0"
                 and evidence["stage2_semantics"] == "unchanged",
                 f"fixture records the exact replay-only repair: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"Stage 4 can recover the Foundry fixture from the cert job: "
                 f"{job['certify_argv']}")
    return bad


def test_transfer_helper_fixture_retains_zero_conduit_rejection():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "ProjectOpenSea__seaport__TransferHelper")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__ProjectOpenSea__seaport__TransferHelper/flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: TransferHelper prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="ProjectOpenSea__seaport__TransferHelper",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="TransferHelper",
        unit="bulkTransfer",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "transfer-helper-bulk-transfer",
            "unit": "bulkTransfer",
            "path_function": "sol:@C@TransferHelper@F@bulkTransfer#145",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "visibility": "external",
                "parameter_types": [
                    "struct TransferHelperItemsWithRecipient[]", "bytes32"],
                "return_types": ["bytes4"],
                "state_mutability": "nonpayable",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(
            schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check("skip_constructor" not in fixture,
                 f"TransferHelper keeps constructor semantics: {fixture}")
    bad += check(fixture["foundry"] == {
        "constructor_args": ["address(uint160(1000))"],
        "expected_revert_signature": "InvalidConduit(bytes32,address)",
    }, f"Foundry legally deploys and asserts exact zero-key rejection: {fixture}")
    evidence = fixture["source_evidence"]
    bad += check(evidence["dominating_guard"] == "conduitKey == bytes32(0)"
                 and evidence["precedes"] ==
                 "_performTransfersWithConduit(items, conduitKey)",
                 f"fixture records source dominance: {fixture}")
    bad += check("--esbmc-arg=--path-cov-max-goals" in job["certify_argv"]
                 and "--esbmc-arg=2" in job["certify_argv"],
                 f"fixture shards to the ABI gate and dominating source goal: "
                 f"{job['certify_argv']}")
    return bad


def test_euler_initialize_fixture_rebuilds_direct_deploy_guard():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "euler-xyz__euler-vault-kit__Initialize")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__euler-xyz__euler-vault-kit__Initialize/flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: Euler Initialize prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="euler-xyz__euler-vault-kit__Initialize",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="Initialize",
        unit="initialize",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "euler-initialize",
            "unit": "initialize",
            "path_function": "sol:@C@Initialize@F@initialize#2623",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "visibility": "public",
                "parameter_types": ["address"],
                "return_types": [],
                "state_mutability": "nonpayable",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check(fixture["contract"] == "Initialize"
                 and fixture["skip_constructor"] is True,
                 f"Initialize fixture skips only its expensive direct deployment: {fixture}")
    bad += check(fixture["state"] == {"initialized$397": 1},
                 f"fixture rebuilds the exact inherited ESBMC store: {fixture}")
    bad += check(fixture["foundry"] == {
        "skip_constructor": True,
        "constructor_args": [
            "Base.Integrations({evc: address(uint160(1000)), protocolConfig: "
            "address(uint160(1001)), sequenceRegistry: address(uint160(1002)), "
            "balanceTracker: address(uint160(1003)), permit2: address(uint160(1004))})",
        ],
        "target_call_mode": "low-level-revert",
        "target_call_signature": "initialize(address)",
    },
                 f"Foundry legally deploys then asserts the target revert: {fixture}")
    bad += check(fixture["veriput_fixture_kind"] ==
                 "evk-initialize-direct-deploy-guard",
                 f"fixture records its narrow EVK source-backed kind: {fixture}")
    bad += check(fixture["source_evidence"]["constructor_initialization"] ==
                 "initialized = true"
                 and fixture["source_evidence"]["dominating_guard"] ==
                 "if (initialized) revert E_Initialized()",
                 f"fixture records constructor-to-guard evidence: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"certify argv carries Initialize fixture: {job['certify_argv']}")
    bad += check("--esbmc-arg=--path-cov-max-goals" in job["certify_argv"]
                 and "--esbmc-arg=2" in job["certify_argv"],
                 f"fixture retains only the ABI gate and dominating revert: "
                 f"{job['certify_argv']}")
    return bad


def test_euler_risk_manager_fixture_retains_proxy_auth_rejection():
    subject_dir = Path("/home/samson/workspace/VeriPUT/Results/Stress243/subjects/"
                       "euler-xyz__euler-vault-kit__RiskManager")
    solast = Path("/tmp/veriput_rq1_ast_cache/stress243/"
                  "stress243__euler-xyz__euler-vault-kit__RiskManager/flat.sol.solast")
    if not subject_dir.exists() or not solast.exists():
        print("skip: Euler RiskManager prepared subject or AST cache is absent")
        return 0
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="euler-xyz__euler-vault-kit__RiskManager",
        root=str(subject_dir),
        flat_sol=str(subject_dir / "flat.sol"),
        solast=str(solast),
        contract="RiskManager",
        unit="checkVaultStatus",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "schema": "veriput-unit-schedule/v1",
        "summary": {},
        "jobs": [{
            "job_id": "euler-risk-manager-check-vault-status",
            "unit": "checkVaultStatus",
            "path_function": "sol:@C@RiskManager@F@checkVaultStatus#3235",
            "certify_argv": ["python3", "certify_all.py"],
            "dry_run_argv": ["python3", "certify_all.py", "--dry-run"],
            "unit_info": {
                "visibility": "public",
                "parameter_types": [],
                "return_types": ["bytes4"],
                "state_mutability": "nonpayable",
            },
        }],
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = rq1_veriput_run.apply_source_stage2_fixtures(schedule, subject, Path(tmp))
        job = out["jobs"][0]
        fixture_path = Path(job["source_stage2_fixture_path"])
        fixture = json.loads(fixture_path.read_text())
    bad = 0
    bad += check(fixture["contract"] == "RiskManager"
                 and fixture["skip_constructor"] is True,
                 f"RiskManager fixture uses proxy-entry runtime state: {fixture}")
    bad += check(fixture["state"] == {}
                 and fixture["foundry"]["skip_constructor"] is True
                 and fixture["foundry"]["expected_revert_signature"] ==
                 "E_CheckUnauthorized()"
                 and "Base.Integrations" in
                 fixture["foundry"]["constructor_args"][0],
                 f"Foundry uses a legal deployment and exact auth revert: {fixture}")
    bad += check(fixture["veriput_fixture_kind"] ==
                 "evk-risk-manager-proxy-auth-rejection",
                 f"fixture records its narrow EVK source-backed kind: {fixture}")
    evidence = fixture["source_evidence"]
    bad += check(evidence["state_dependencies"] == ["evc", "snapshot", "vaultStorage"]
                 and evidence["dominating_guard"] ==
                 "msg.sender != address(evc) || !evc.areChecksInProgress()",
                 f"fixture records exact AST dependencies and source guard: {fixture}")
    bad += check("--esbmc-arg=--path-cov-fixture" in job["certify_argv"]
                 and f"--esbmc-arg={fixture_path}" in job["certify_argv"],
                 f"certify argv carries RiskManager fixture: {job['certify_argv']}")
    bad += check("--esbmc-arg=--path-cov-max-goals" in job["certify_argv"]
                 and "--esbmc-arg=2" in job["certify_argv"],
                 f"fixture retains only ABI gate and auth rejection: "
                 f"{job['certify_argv']}")
    bad += check("--esbmc-arg=--unwind" in job["certify_argv"]
                 and "--esbmc-arg=1" in job["certify_argv"]
                 and evidence["unwind_boundary"] ==
                 "1; retained guard exits before any external call or loop",
                 f"fixture records and applies the retained path's unwind boundary: "
                 f"{job['certify_argv']}")
    bad += check("msg.sender" not in job["certify_argv"]
                 and job["certify_argv"][job["certify_argv"].index("--probes") + 1] == "0",
                 f"exact concrete fixture skips sender generalisation and probe pre-run: "
                 f"{job['certify_argv']}")
    return bad


def test_run_subject_records_no_unit_deploy_fallback_schema():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="s",
        root="/tmp/s",
        flat_sol="/tmp/s/flat.sol",
        solast="/tmp/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    schedule = {
        "jobs": [],
        "summary": {
            "jobs": 0,
            "no_unit_rows": 1
        },
        "no_unit_rows": [{
            "reason":
            "target contract has no public/external FunctionDefinition units",
        }],
    }

    def fake_emit(_subject, case_dir, _schedule, _forge_timeout):
        out_root = case_dir / "put" / "deploy_only"
        out_root.mkdir(parents=True, exist_ok=True)
        row = {
            "kind": "concrete",
            "stage2_source": "no_unit_deploy_fallback",
            "unit": "__deploy__",
            "enc": 0,
            "test": "test_cov_C_deploy_only",
            "file": str(out_root / "Project" / "test" / "CDeployOnlyCovTest.t.sol"),
            "forge_status": "Success",
            "valid_reference_test": True,
            "b": False,
        }
        (out_root / "put-summary.json").write_text(
            json.dumps({
                "schema": "veriput-put-summary/1",
                "emission": {
                    "puts_emitted": 0,
                    "concrete_replays_emitted": 1,
                },
                "deliverable_b": {
                    "valid_reference_tests": {
                        "total": 1,
                        "put": 0,
                        "concrete": 1,
                    },
                    "rows": [row],
                },
                "timing": {
                    "generation_wall_s": 0.0,
                    "emission_wall_s": 0.02,
                    "foundry_replay_wall_s": 0.03,
                    "total_wall_s": 0.05,
                },
            }))
        return {
            "stage": "no-unit-deploy-fallback",
            "status": "ok",
            "put_out_root": str(out_root),
            "wall_s": 0.05,
            "forge_wall_s": 0.03,
        }

    old_resolve = rq1_veriput_run.subject_unit_manifest.resolve_subject
    old_build = rq1_veriput_run.build_subject_schedule
    old_emit = rq1_veriput_run.emit_no_unit_deploy_fallback
    rq1_veriput_run.subject_unit_manifest.resolve_subject = (lambda *_args, **_kwargs: subject)
    rq1_veriput_run.build_subject_schedule = (lambda *_args, **_kwargs: schedule)
    rq1_veriput_run.emit_no_unit_deploy_fallback = fake_emit
    try:
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            row, detail = rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                }, "peer182", args)
    finally:
        rq1_veriput_run.subject_unit_manifest.resolve_subject = old_resolve
        rq1_veriput_run.build_subject_schedule = old_build
        rq1_veriput_run.emit_no_unit_deploy_fallback = old_emit

    bad = 0
    bad += check(row["status"] == "no-units" and row["valid"] == 0,
                 f"deploy-only fallback keeps true no-unit out of valid: {row}")
    bad += check(row["concrete_raw"] == 1 and row["concrete_valid"] == 0 and row["put_valid"] == 0,
                 f"deploy-only fallback remains raw concrete only: {row}")
    bad += check(row["no_unit_deploy_fallback_count"] == 1,
                 f"fallback count is retained in row: {row}")
    bad += check(row["no_unit_deploy_fallback_statuses"] == ["ok"],
                 f"fallback status is retained in row: {row}")
    bad += check(row["foundry_replay_wall_s"] == 0.03, f"fallback replay timing is retained: {row}")
    bad += check(any(stage.get("stage") == "no-unit-deploy-fallback"
                     for stage in (detail.get("stages") or [])),
                 f"fallback stage is retained in detail: {detail}")
    return bad


def test_valid_reference_rejects_deploy_and_creation_aliases():
    bad = 0
    for source in ("no_unit_deploy_fallback", "structural_deploy_only",
                   "structural-deploy-only"):
        bad += check(
            rq1_veriput_run._is_valid_reference_test({
                "kind": "concrete",
                "valid_reference_test": True,
                "stage2_source": source,
            }) is False,
            f"stage2 deploy-only source is not valid: {source}")
    for kind in ("deploy_only", "deploy-only", "creation_code_only",
                 "creation-code-only"):
        bad += check(
            rq1_veriput_run._is_valid_reference_test({
                "kind": "concrete",
                "valid_reference_test": True,
                "stage4_kind": kind,
            }) is False,
            f"stage4 deploy/creation kind is not valid: {kind}")
    bad += check(
        rq1_veriput_run._is_valid_reference_test({
            "kind": "concrete",
            "valid_reference_test": True,
            "stage4_kind": "getter-only",
        }) is True,
        "getter-only concrete remains a valid reference test")
    return bad


def test_real203_cache_uses_prepared_benchmark_namespace():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="stress243",
        subject_id="repo__C",
        root="/prepared/repo__C",
        flat_sol="/prepared/repo__C/flat.sol",
        solast="/prepared/repo__C/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin="/bin/solc",
        solc_extra=(),
        metadata={
            "status": "ok",
        },
    )
    cached = rq1_veriput_run.cached_subject(subject, Path("/tmp/cache"), "real203")
    return check(cached.solast == "/tmp/cache/stress243/stress243__repo__C/flat.sol.solast",
                 f"real203 output label does not change AST cache namespace: "
                 f"{cached.solast}")


def test_jobs_admission_refuses_oversubscription():
    old = rq1_veriput_run._mem_available_gib
    rq1_veriput_run._mem_available_gib = lambda: 20.0
    args = argparse.Namespace(jobs=2, memlimit_gib=8, mem_fraction=0.70)
    try:
        try:
            rq1_veriput_run.validate_jobs(args)
            refused = False
        except rq1_veriput_run.RQ1RunError:
            refused = True
    finally:
        rq1_veriput_run._mem_available_gib = old
    return check(refused, "subject concurrency refuses memory oversubscription")


def test_target_rows_fast_first_sorts_before_limit():
    old = rq1_veriput_run.target_manifest.build_manifest
    rows = [
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "slow",
            "contract": "Slow",
        },
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "fast",
            "contract": "Fast",
        },
    ]
    rq1_veriput_run.target_manifest.build_manifest = lambda *_args: {
        "targets": rows,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fast = root / "Results" / "BugFix124" / "subjects" / "fast" / "flat.sol"
            slow = root / "Results" / "BugFix124" / "subjects" / "slow" / "flat.sol"
            fast.parent.mkdir(parents=True)
            slow.parent.mkdir(parents=True)
            fast.write_text("contract Fast {}\n")
            slow.write_text("contract Slow {\n" + ("uint256 x;\n" * 100) + "}\n")
            _label, dataset_rows = rq1_veriput_run.target_rows(root, "bugfix124", [], 1, "dataset")
            _label, fast_rows = rq1_veriput_run.target_rows(root, "bugfix124", [], 1, "fast-first")
    finally:
        rq1_veriput_run.target_manifest.build_manifest = old
    bad = 0
    bad += check(dataset_rows[0]["subject_id"] == "slow", "dataset order is preserved before limit")
    bad += check(fast_rows[0]["subject_id"] == "fast",
                 "fast-first sorts by prepared flat.sol size before limit")
    return bad


def test_target_rows_fast_first_uses_bugfix_fallback_size():
    old = rq1_veriput_run.target_manifest.build_manifest
    rows = [
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "slow",
            "contract": "Slow",
        },
        {
            "status": "ok",
            "benchmark": "bugfix124",
            "subject_id": "fast",
            "contract": "Fast",
        },
    ]
    rq1_veriput_run.target_manifest.build_manifest = lambda *_args: {
        "targets": rows,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "scripts" / "Results" / "workdirs" \
                / "BugFix124" / "subjects"
            fast = base / "fast" / "flat.sol"
            slow = base / "slow" / "flat.sol"
            fast.parent.mkdir(parents=True)
            slow.parent.mkdir(parents=True)
            fast.write_text("contract Fast {}\n")
            slow.write_text("contract Slow {\n" + ("uint256 x;\n" * 100) + "}\n")
            _label, fast_rows = rq1_veriput_run.target_rows(root, "bugfix124", [], 1, "fast-first")
    finally:
        rq1_veriput_run.target_manifest.build_manifest = old
    return check(fast_rows[0]["subject_id"] == "fast",
                 "bugfix fast-first uses fallback prepared flat.sol size")


def test_target_rows_fast_first_uses_peer_fallback_size():
    old = rq1_veriput_run.target_manifest.build_manifest
    rows = [
        {
            "status": "ok",
            "benchmark": "peer182",
            "subject_id": "slow",
            "contract": "Slow",
        },
        {
            "status": "ok",
            "benchmark": "peer182",
            "subject_id": "fast",
            "contract": "Fast",
        },
    ]
    rq1_veriput_run.target_manifest.build_manifest = lambda *_args: {
        "targets": rows,
    }
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = root / "scripts" / "Results" / "workdirs" \
                / "Peer182" / "subjects"
            fast = base / "fast" / "flat.sol"
            slow = base / "slow" / "flat.sol"
            fast.parent.mkdir(parents=True)
            slow.parent.mkdir(parents=True)
            fast.write_text("contract Fast {}\n")
            slow.write_text("contract Slow {\n" + ("uint256 x;\n" * 100) + "}\n")
            _label, fast_rows = rq1_veriput_run.target_rows(root, "peer182", [], 1, "fast-first")
    finally:
        rq1_veriput_run.target_manifest.build_manifest = old
    return check(fast_rows[0]["subject_id"] == "fast",
                 "peer fast-first uses fallback prepared flat.sol size")


def test_certification_summary_identifies_inner_timeouts():
    with tempfile.TemporaryDirectory() as td:
        cert = Path(td) / "certify-results.jsonl"
        rows = [
            {
                "bucket": "KILLED",
                "exit": 124,
                "unit": "transfer",
                "witnessed": None,
                "certified": {},
                "not_certified": {},
            },
            {
                "bucket": "KILLED",
                "exit": 1,
                "unit": "fallback",
                "witnessed": None,
                "wall_s": 120.3,
                "run_timeout_s": 120,
                "driver_diagnostic": {
                    "tag": "goto-inline-call-type-mismatch",
                    "category": "no-cov-report",
                },
                "certified": {},
                "not_certified": {},
            },
        ]
        cert.write_text("".join(json.dumps(row) + "\n" for row in rows))
        summary = rq1_veriput_run.summarize_certification(cert)
    bad = 0
    bad += check(summary["bucket_counts"] == {"KILLED": 2},
                 f"certification buckets retained: {summary}")
    bad += check(summary["exit_counts"] == {
        "1": 1,
        "124": 1
    }, f"certification exits retained: {summary}")
    bad += check(summary["witness_counts"] == {"unknown": 2}, f"witness status retained: {summary}")
    bad += check(summary["timed_out_units"] == ["fallback", "transfer"],
                 f"inner timeout unit identified: {summary}")
    bad += check(summary["driver_diagnostic_tags"] == {
        "goto-inline-call-type-mismatch": 1,
    }, f"driver diagnostic tags retained: {summary}")
    bad += check(
        rq1_veriput_run._no_output_reason(
            summary) == "certification timed out before PUT artifacts: fallback, transfer",
        "no-output reason distinguishes inner certification timeout")
    return bad


def test_certification_summary_uses_diagnostics_for_no_output_reason():
    summary = {
        "rows": 2,
        "certified_regions": 0,
        "driver_diagnostic_tags": {
            "frontend-tuple-rhs-symbol": 1,
            "path-coverage-no-claims-reached-solver": 1,
        },
        "bucket_counts": {
            "NO-WITNESS-UNKNOWN": 2
        },
    }
    return check(
        rq1_veriput_run._no_output_reason(
            summary) == "no certified regions: diagnostics frontend-tuple-rhs-symbol=1, "
        "path-coverage-no-claims-reached-solver=1",
        "diagnostics outrank coarse bucket counts in no-output reason")


def test_cleared_concrete_fallbacks_trigger_stage4():
    with tempfile.TemporaryDirectory() as td:
        cert = Path(td) / "certify-results.jsonl"
        rows = [
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {
                    "7": "single point cleared"
                },
                "not_certified_details": {
                    "7": {
                        "enc": 7,
                        "ce": {
                            "amount": 1
                        },
                        "concrete_fallback": True,
                        "witness_check": "SUCCESSFUL",
                    },
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {
                    "9": "no generalisable coordinate"
                },
                "not_certified_details": {
                    "9": {
                        "enc": 9,
                        "ce": {
                            "owner": "0x0000000000000000000000000000000000000001"
                        },
                        "concrete_fallback": True,
                        "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    },
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {
                    "10": "cleared but no replay payload"
                },
                "not_certified_details": {
                    "10": {
                        "enc": 10,
                        "concrete_fallback": True,
                        "witness_check": "SUCCESSFUL",
                    },
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NOT-CERTIFIED",
                "certified": {},
                "not_certified": {
                    "8": "unknown point"
                },
                "not_certified_details": {
                    "8": {
                        "enc": 8,
                        "concrete_fallback": True,
                        "witness_check": "UNKNOWN",
                        "ce": {
                            "owner": "0x0000000000000000000000000000000000000002"
                        },
                    },
                },
                "partial_witness_journal": {
                    "partial": True,
                    "witness_count": 1,
                    "paths": [{
                        "path_id": "8",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    }],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "KILLED",
                "exit": 124,
                "witnessed": 1,
                "certified": {},
                "not_certified": {},
                "partial_witness_journal": {
                    "partial":
                    True,
                    "witness_count":
                    1,
                    "paths": [{
                        "path_id": "15",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    }],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "KILLED",
                "exit": 1,
                "witnessed": 2,
                "certified": {
                    "41": "already certified"
                },
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "path-coverage-partial-journal-no-report",
                    "category": "no-cov-report",
                },
                "partial_witness_journal": {
                    "source_stage":
                    "partial-witness-journal",
                    "partial":
                    True,
                    "witness_count":
                    2,
                    "paths": [
                        {
                            "path_id": "41",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "42",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "KILLED",
                "exit": 124,
                "witnessed": 1,
                "certified": {
                    "15": "already measured"
                },
                "not_certified": {
                    "17": "already rejected"
                },
                "partial_witness_journal": {
                    "partial":
                    True,
                    "witness_count":
                    3,
                    "paths": [
                        {
                            "path_id": "15",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "16",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "17",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "NO-COORDINATE",
                "certified": {
                    "21": "already certified"
                },
                "not_certified": {},
                "partial_witness_journal": {
                    "complete":
                    True,
                    "witness_count":
                    2,
                    "paths": [
                        {
                            "path_id": "21",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "22",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
            {
                "benchmark": "bench",
                "unit": "approve",
                "bucket": "CERTIFIED",
                "certified": {},
                "not_certified": {},
                "partial_witness_journal": {
                    "source_stage":
                    "certified-no-coordinate",
                    "complete":
                    True,
                    "witness_count":
                    2,
                    "paths": [
                        {
                            "path_id": "31",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                        {
                            "path_id": "32",
                            "path_function": "sol:@C@Token@F@approve#972",
                            "witness_count": 1,
                        },
                    ],
                },
            },
        ]
        cert.write_text("".join(json.dumps(row) + "\n" for row in rows))
        cleared_count = rq1_veriput_run._cleared_concrete_fallback_count(cert, "bench", "approve")
        timeout_count = rq1_veriput_run._timeout_concrete_fallback_count(cert, "bench", "approve")
        complete_count = rq1_veriput_run._complete_witness_concrete_fallback_count(
            cert, "bench", "approve")
        partial_journal_count = \
            rq1_veriput_run._partial_journal_concrete_fallback_count(
                cert, "bench", "approve")
    argv = rq1_veriput_run._put_argv(cert, "approve", "bench", Path("/tmp/out"), 600, 12, 300)
    certified_argv = rq1_veriput_run._put_argv(cert,
                                               "approve",
                                               "bench",
                                               Path("/tmp/out"),
                                               600,
                                               12,
                                               300,
                                               path_function="sol:@C@C@F@approve#77",
                                               emit_concrete_fallbacks=False,
                                               foundry_fixture="/tmp/fixture.json")
    bad = 0
    bad += check(
        cleared_count == 3,
        "explicit UNKNOWN concrete fallback survives the occupied-journal boundary")
    bad += check(timeout_count == 2,
                 "timed-out witnessed certification rows skip only occupied encs")
    bad += check(complete_count == 3,
                 "complete witnessed rows include certified no-coordinate fallbacks")
    bad += check(partial_journal_count == 1,
                 "partial witness journals without verdict trigger Stage 4")
    bad += check(rq1_veriput_run._is_concrete_only_stage4(0, 0, 0, 0, 1),
                 "partial-journal-only candidates are concrete-only Stage 4")
    bad += check("--emit-cleared-concrete-fallbacks" in argv,
                 f"RQ1 Stage 4 enables cleared concrete fallback emission: {argv}")
    bad += check("--emit-cleared-concrete-fallbacks" not in certified_argv,
                 "certified Stage 4 skips unrelated concrete fallback work")
    bad += check(certified_argv[-2:] == ["--foundry-fixture", "/tmp/fixture.json"],
                 f"Stage 4 receives the source-checked fixture: {certified_argv}")
    only_idx = certified_argv.index("--only")
    bad += check(certified_argv[only_idx + 1] == "bench.sol:@C@C@F@approve#77",
                 "overloaded Stage 4 selects the exact path-function")
    return bad


def test_subject_schedule_uses_separate_esbmc_run_timeout():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="bugfix124",
        subject_id="s",
        root="/prepared/s",
        flat_sol="/prepared/s/flat.sol",
        solast="/prepared/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin="/bin/solc",
        solc_extra=(),
        metadata={
            "status": "ok",
        },
    )
    old_manifest = rq1_veriput_run.subject_unit_manifest.manifest_for_subject
    old_schedule = rq1_veriput_run.unit_schedule.build_schedule
    captured = {}
    rq1_veriput_run.subject_unit_manifest.manifest_for_subject = lambda *_a, **_kw: {
        "status": "ok",
        "units": {
            "units": ["f"],
        },
    }

    def fake_schedule(_manifest, **kwargs):
        captured.update(kwargs)
        return {
            "jobs": [],
            "summary": {},
        }

    rq1_veriput_run.unit_schedule.build_schedule = fake_schedule
    try:
        rq1_veriput_run.build_subject_schedule(subject, {
            "units_hint": ["f"],
        },
                                               Path("/tmp/cache"),
                                               Path("/tmp/case"),
                                               timeout_s=600,
                                               run_timeout_s=120,
                                               memlimit_gib=12)
    finally:
        rq1_veriput_run.subject_unit_manifest.manifest_for_subject = old_manifest
        rq1_veriput_run.unit_schedule.build_schedule = old_schedule
    bad = 0
    bad += check(captured.get("timeout_s") == 600, f"subject budget is preserved: {captured}")
    bad += check(
        captured.get("run_timeout_s") == 120, f"per-ESBMC run budget is separate: {captured}")
    return bad


def test_certify_argv_for_remaining_caps_only_run_timeout():
    job = {
        "certify_argv": [
            "python3",
            "certify_all.py",
            "--timeout",
            "600",
            "--run-timeout",
            "600",
            "--memlimit-gib",
            "8",
        ],
        "certification_budget": {
            "workdir": "/tmp/work",
        },
    }
    argv = rq1_veriput_run._certify_argv_for_remaining(job,
                                                       remaining_s=599.8,
                                                       run_timeout_s=120,
                                                       memlimit_gib=12,
                                                       stage_mem_fraction=0.70)
    pairs = dict(zip(argv, argv[1:]))
    bad = 0
    bad += check(
        pairs.get("--timeout") == "599",
        f"whole certify budget follows remaining case time: {argv}")
    bad += check(pairs.get("--run-timeout") == "120", f"per-ESBMC run budget is capped: {argv}")
    bad += check(pairs.get("--memlimit-gib") == "12", f"memory budget is authoritative: {argv}")
    bad += check(
        pairs.get("--mem-fraction") == "0.7",
        f"stage memory fraction is passed to certify_all: {argv}")
    return bad


def test_stage2_unit_timeout_cap_defaults_to_adaptive():
    bad = 0
    bad += check(rq1_veriput_run.DEFAULT_STAGE2_UNIT_TIMEOUT_CAP_S == 0,
                 "explicit Stage-2 unit timeout cap defaults to unset")
    bad += check(rq1_veriput_run.DEFAULT_ADAPTIVE_STAGE2_UNIT_TIMEOUT_CAP_S > 0,
                 "adaptive Stage-2 unit timeout cap is enabled by default")
    return bad


def test_adaptive_stage2_unit_timeout_cap_policy():
    cheap_job = {
        "schedule_rank": {
            "cheap_first": [10, 0, 0],
        },
    }
    expensive_job = {
        "schedule_rank": {
            "cheap_first": [
                rq1_veriput_run.ADAPTIVE_STAGE2_EXPENSIVE_TIER_THRESHOLD,
                1,
                0,
            ],
        },
    }
    args = argparse.Namespace(
        stage2_unit_timeout_cap_s=0,
        adaptive_stage2_unit_timeout_cap_s=120,
        bounded_holds_retry=False,
        bounded_holds_retry_max_tx=3,
        bounded_holds_retry_unwind=16,
        bounded_holds_retry_max_initial_wall_s=180,
    )
    explicit = argparse.Namespace(stage2_unit_timeout_cap_s=90,
                                  adaptive_stage2_unit_timeout_cap_s=120)
    disabled = argparse.Namespace(stage2_unit_timeout_cap_s=0, adaptive_stage2_unit_timeout_cap_s=0)
    cap = rq1_veriput_run._effective_stage2_unit_timeout_cap_s
    bad = 0
    bad += check(cap(cheap_job, args, 1) == 0, "single cheap unit remains uncapped")
    bad += check(
        cap(cheap_job, args, rq1_veriput_run.ADAPTIVE_STAGE2_MANY_UNIT_THRESHOLD) == 120,
        "multi-unit subject gets adaptive Stage-2 cap")
    bad += check(
        cap(expensive_job, args, 1) == 120, "expensive-looking unit gets adaptive Stage-2 cap")
    bad += check(
        cap(cheap_job, args, 3, prior_no_candidate_units=1) == 120,
        "later units are capped after a no-candidate prefix")
    bad += check(
        cap(expensive_job, explicit, 1) == 90, "explicit Stage-2 cap overrides adaptive policy")
    bad += check(cap(expensive_job, disabled, 10) == 0, "adaptive Stage-2 cap can be disabled")
    return bad


def test_stage2_wrapper_timeout_uses_effective_unit_cap():
    wrapper = rq1_veriput_run._stage2_wrapper_timeout_s
    bad = 0
    bad += check(
        wrapper(599.8, 60, 120) == 180.0,
        "Stage-2 wrapper timeout follows effective unit cap plus grace")
    bad += check(
        wrapper(91.2, 60, 120) == 151.2,
        "Stage-2 wrapper timeout never exceeds remaining budget plus grace")
    bad += check(
        wrapper(599.8, 60, 0) == 659.8,
        "uncapped Stage-2 wrapper keeps subject remaining budget plus grace")
    return bad


def test_schedule_annotation_records_runtime_stage2_caps():
    schedule = {
        "jobs": [
            {
                "unit": "cheap",
                "schedule_rank": {
                    "cheap_first": [10, 0, 0],
                },
            },
            {
                "unit": "expensive",
                "schedule_rank": {
                    "cheap_first": [70, 0, 0],
                },
            },
        ],
    }
    args = argparse.Namespace(
        stage2_unit_timeout_cap_s=0,
        adaptive_stage2_unit_timeout_cap_s=120,
        bounded_holds_retry=False,
        bounded_holds_retry_max_tx=3,
        bounded_holds_retry_unwind=16,
        bounded_holds_retry_max_initial_wall_s=180,
    )
    rq1_veriput_run.annotate_stage2_runtime_policy(schedule, args)
    cheap = schedule["jobs"][0]["rq1_stage2_runtime_policy"]
    expensive = schedule["jobs"][1]["rq1_stage2_runtime_policy"]
    policy = schedule["rq1_stage2_runtime_policy"]
    bad = 0
    bad += check(policy["capped_timeout_advances_to_next_unit"] is True,
                 f"schedule records capped-timeout continuation policy: {policy}")
    bad += check(cheap["initial_effective_unit_timeout_cap_s"] == 0,
                 f"cheap first unit starts uncapped in a two-unit subject: {cheap}")
    bad += check(cheap["after_no_candidate_effective_unit_timeout_cap_s"] == 120,
                 f"cheap later unit is capped after a no-candidate prefix: {cheap}")
    bad += check(expensive["initial_effective_unit_timeout_cap_s"] == 120,
                 f"expensive unit is capped immediately: {expensive}")
    return bad


def test_certify_argv_for_remaining_honors_unit_timeout_cap():
    job = {
        "certify_argv": [
            "python3",
            "certify_all.py",
            "--timeout",
            "600",
            "--run-timeout",
            "600",
            "--memlimit-gib",
            "8",
        ],
        "certification_budget": {
            "workdir": "/tmp/work",
        },
    }
    argv = rq1_veriput_run._certify_argv_for_remaining(job,
                                                       remaining_s=599.8,
                                                       run_timeout_s=120,
                                                       memlimit_gib=12,
                                                       unit_timeout_cap_s=90)
    pairs = dict(zip(argv, argv[1:]))
    bad = 0
    bad += check(pairs.get("--timeout") == "90", f"whole certify budget follows unit cap: {argv}")
    bad += check(
        pairs.get("--run-timeout") == "90",
        f"per-ESBMC run budget cannot exceed capped unit budget: {argv}")
    return bad


def test_stage2_cert_shard_argv_and_merge():
    job = {
        "certify_argv": [
            "python3",
            "certify_all.py",
            "--out",
            "/tmp/canonical.jsonl",
            "--timeout",
            "600",
        ],
        "certification_budget": {
            "workdir": "/tmp/work",
        },
    }
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        shard = root / "shard.jsonl"
        canonical = root / "canonical.jsonl"
        argv = rq1_veriput_run._certify_argv_for_remaining(job,
                                                           remaining_s=100,
                                                           run_timeout_s=100,
                                                           memlimit_gib=8,
                                                           unit_timeout_cap_s=20,
                                                           out_path=shard)
        pairs = dict(zip(argv, argv[1:]))
        shard.write_text(
            json.dumps({
                "unit": "a",
                "bucket": "KILLED"
            }) + "\n" + "{not json}\n" + json.dumps({
                "unit": "b",
                "bucket": "CERTIFIED"
            }) + "\n")
        merge = rq1_veriput_run._merge_jsonl_records(canonical, shard)
        rows = [json.loads(line) for line in canonical.read_text().splitlines() if line.strip()]
    bad = 0
    bad += check(
        pairs.get("--out") == str(shard), f"Stage-2 certify argv writes to per-unit shard: {argv}")
    bad += check(merge["merged"] == 2 and merge["invalid"] == 1,
                 f"cert shard merge counts valid and invalid rows: {merge}")
    bad += check([row["unit"] for row in rows] == ["a", "b"],
                 f"canonical cert JSONL receives shard rows: {rows}")
    return bad


def test_capped_stage2_timeout_advances_to_next_unit():
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="s",
        root="/tmp/s",
        flat_sol="/tmp/s/flat.sol",
        solast="/tmp/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    jobs = []
    for unit in ("slowA", "slowB"):
        jobs.append({
            "unit":
            unit,
            "job_id":
            f"job-{unit}",
            "schedule_rank": {
                "cheap_first": [70, 0, 0],
            },
            "certification_budget": {
                "workdir": f"/tmp/work/{unit}",
            },
            "certify_argv": [
                "python3",
                "certify_all.py",
                "--unit",
                unit,
                "--timeout",
                "600",
                "--run-timeout",
                "600",
            ],
        })
    schedule = {
        "jobs": jobs,
        "summary": {
            "jobs": 2,
        },
    }
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        out_path = None
        if "--out" in argv:
            out_i = argv.index("--out") + 1
            if out_i < len(argv):
                out_path = argv[out_i]
        unit = "slowA" if len(calls) == 1 else "slowB"
        if out_path:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(
                json.dumps({
                    "benchmark": subject.benchmark_key,
                    "unit": unit,
                    "bucket": "NO-WITNESS-UNKNOWN",
                    "certified": {},
                    "not_certified": {},
                    "driver_diagnostic": {
                        "tag": "frontend-tuple-rhs-symbol",
                        "category": "no-cov-report",
                    },
                }) + "\n")
        return {
            "argv": argv,
            "rc": None,
            "status": "timeout",
            "timed_out": True,
            "wall_s": round(timeout_s, 3),
            "maxrss_proc_mb": 1.0,
        }

    old_resolve = rq1_veriput_run.subject_unit_manifest.resolve_subject
    old_build = rq1_veriput_run.build_subject_schedule
    old_wait = rq1_veriput_run.wait_for_mem_budget
    old_run = rq1_veriput_run.run_command
    rq1_veriput_run.subject_unit_manifest.resolve_subject = (lambda *_args, **_kwargs: subject)
    rq1_veriput_run.build_subject_schedule = (lambda *_args, **_kwargs: schedule)
    rq1_veriput_run.wait_for_mem_budget = lambda *_args, **_kwargs: {
        "status": "ok",
        "waited": False,
    }
    rq1_veriput_run.run_command = fake_run_command
    try:
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(
                result_root=td,
                ast_cache_root=str(Path(td) / "ast"),
                redo=False,
                timeout=30,
                esbmc_run_timeout=30,
                memlimit_gib=1,
                stage2_unit_timeout_cap_s=0,
                adaptive_stage2_unit_timeout_cap_s=2,
                wrapper_grace=3,
                min_remaining_s=1,
                stage_mem_fraction=0.1,
                mem_wait_poll_s=0.1,
                no_candidate_stage2_unit_stop_n=1,
                min_no_candidate_stage2_unit_stop_n=1,
                no_output_stage2_stop_s=0,
                min_no_output_stage2_unit_stop_n=0,
                skip_concrete_only_after_put_valid=0,
                min_concrete_only_stage4_s=0,
                min_timeout_only_stage4_s=0,
                zero_output_stage4_stop_s=0,
                forge_timeout=1,
                jobs=1,
            )
            row, detail = rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                }, "peer182", args)
    finally:
        rq1_veriput_run.subject_unit_manifest.resolve_subject = old_resolve
        rq1_veriput_run.build_subject_schedule = old_build
        rq1_veriput_run.wait_for_mem_budget = old_wait
        rq1_veriput_run.run_command = old_run

    bad = 0
    bad += check(
        len(calls) == 2, f"capped Stage-2 tool timeout advances to the next unit: "
        f"{calls}")
    bad += check([call[1] for call in calls] == [5.0, 5.0],
                 f"wrapper timeout is unit cap plus grace: {calls}")
    bad += check(row["units_attempted"] == ["slowA", "slowB"],
                 f"both capped units are recorded as attempted: {row}")
    bad += check(row["stage2_capped_timeout_units"] == ["slowA", "slowB"],
                 f"capped timeout units are retained: {row}")
    bad += check(row["stage2_no_candidate_stop_skipped_unit_count"] == 2,
                 f"capped no-cov-report rows do not count as no-candidate "
                 f"stop evidence: {row}")
    bad += check(row["status"] == "no-output",
                 f"all-capped no-output subject is not a runner error: {row}")
    stages = detail.get("stages") or []
    bad += check(all(stage.get("wrapper_timeout_s") == 5.0 for stage in stages),
                 f"stage records keep wrapper cap: {stages}")
    return bad


def test_stage2_no_output_stop_ignores_tool_failure_units():
    subject, schedule = _mocked_subject_and_schedule(["badA", "badB", "badC"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        out_path = Path(argv[argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unit = argv[argv.index("--unit") + 1]
        out_path.write_text(
            json.dumps({
                "benchmark": subject.benchmark_key,
                "unit": unit,
                "bucket": "NO-WITNESS-UNKNOWN",
                "certified": {},
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "path-coverage-no-claims-reached-solver",
                    "category": "no-cov-report",
                },
            }) + "\n")
        return {
            "argv": argv,
            "rc": None,
            "status": "timeout",
            "timed_out": True,
            "wall_s": round(timeout_s, 3),
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            args.no_output_stage2_stop_s = 1
            args.min_no_output_stage2_unit_stop_n = 2
            return rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                }, "peer182", args)

    row, _detail = _with_mocked_run_subject(subject, schedule, fake_run_command, body)
    bad = 0
    bad += check(
        len(calls) == 3, f"tool/focus failures do not trip Stage-2 no-output stop: "
        f"{calls}")
    bad += check(row["stage2_no_candidate_evidence_units"] == 0,
                 f"tool failures are not no-candidate evidence: {row}")
    bad += check(row["stage2_no_candidate_stop_skipped_unit_count"] == 3,
                 f"tool failures are audited as skipped stop evidence: {row}")
    return bad


def test_overload_refusal_appends_path_function_jobs():
    subject, schedule = _mocked_subject_and_schedule(["f"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        out_path = Path(argv[argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unit = argv[argv.index("--unit") + 1]
        path_function = None
        if "--path-function" in argv:
            path_function = argv[argv.index("--path-function") + 1]
        if path_function is None:
            row = {
                "benchmark": subject.benchmark_key,
                "unit": unit,
                "bucket": "DRIVER-REFUSED",
                "certified": {},
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "overloaded-unit-path-function-required",
                    "reason": "explicit path function required",
                    "path_functions": [
                        "sol:@C@C@F@f#11",
                        "sol:@C@C@F@f#12",
                    ],
                },
            }
        else:
            row = {
                "benchmark": subject.benchmark_key,
                "unit": unit,
                "path_function": path_function,
                "bucket": "NO-WITNESS-UNKNOWN",
                "certified": {},
                "not_certified": {},
                "driver_diagnostic": {
                    "tag": "esbmc-no-cov-report",
                    "category": "no-cov-report",
                },
            }
        out_path.write_text(json.dumps(row) + "\n")
        return {
            "argv": argv,
            "rc": 0,
            "status": "ok",
            "timed_out": False,
            "wall_s": round(timeout_s, 3),
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            return rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                }, "peer182", args)

    row, detail = _with_mocked_run_subject(subject, schedule, fake_run_command, body)
    path_function_calls = [
        argv[argv.index("--path-function") + 1] for argv, _timeout_s, _log_prefix in calls
        if "--path-function" in argv
    ]
    overload_stages = [
        stage for stage in (detail.get("stages") or [])
        if stage.get("stage") == "schedule-overload-path-functions"
    ]
    bad = 0
    bad += check(len(calls) == 3, f"runner retries each overload path function: {calls}")
    bad += check(path_function_calls == [
        "sol:@C@C@F@f#11",
        "sol:@C@C@F@f#12",
    ], f"dynamic jobs pin the path functions: {path_function_calls}")
    bad += check(overload_stages and overload_stages[0]["added_jobs"] == 2,
                 f"overload expansion is audited: {overload_stages}")
    bad += check(row["units_scheduled"] == 3, f"row sees appended overload jobs: {row}")
    bad += check(
        row["overload_path_function_retry_count"] == 2
        and row["overload_path_function_retry_units"] == ["f"],
        f"row summarizes overload expansion: {row}")
    return bad


def _minimal_run_subject_args(result_root: str) -> argparse.Namespace:
    return argparse.Namespace(
        result_root=result_root,
        ast_cache_root=str(Path(result_root) / "ast"),
        redo=False,
        timeout=30,
        esbmc_run_timeout=30,
        memlimit_gib=1,
        stage2_unit_timeout_cap_s=0,
        adaptive_stage2_unit_timeout_cap_s=2,
        wrapper_grace=3,
        min_remaining_s=1,
        stage_mem_fraction=0.1,
        mem_wait_poll_s=0.1,
        no_candidate_stage2_unit_stop_n=0,
        min_no_candidate_stage2_unit_stop_n=0,
        no_output_stage2_stop_s=0,
        min_no_output_stage2_unit_stop_n=0,
        skip_concrete_only_after_put_valid=0,
        min_concrete_only_stage4_s=0,
        min_timeout_only_stage4_s=0,
        zero_output_stage4_stop_s=0,
        concrete_only_stage4_timeout_cap_s=0,
        skip_concrete_only_after_any_valid=True,
        forge_timeout=1,
        jobs=1,
        bounded_holds_retry=False,
        bounded_holds_retry_max_tx=3,
        bounded_holds_retry_unwind=16,
        bounded_holds_retry_max_initial_wall_s=180,
        unit=[],
    )


def _mocked_subject_and_schedule(units: list[str]) -> tuple:
    subject = rq1_veriput_run.PreparedSubject(
        benchmark="peer182",
        subject_id="s",
        root="/tmp/s",
        flat_sol="/tmp/s/flat.sol",
        solast="/tmp/s/flat.sol.solast",
        contract="C",
        unit="",
        solc_bin=None,
        solc_extra=(),
        metadata={},
    )
    jobs = []
    for unit in units:
        jobs.append({
            "unit":
            unit,
            "job_id":
            f"job-{unit}",
            "schedule_rank": {
                "cheap_first": [70, 0, 0],
            },
            "certification_budget": {
                "workdir": f"/tmp/work/{unit}",
            },
            "certify_argv": [
                "python3",
                "certify_all.py",
                "--unit",
                unit,
                "--timeout",
                "600",
                "--run-timeout",
                "600",
            ],
        })
    return subject, {
        "jobs": jobs,
        "summary": {
            "jobs": len(jobs),
        },
    }


def _with_mocked_run_subject(subject, schedule, fake_run_command, body):
    old_resolve = rq1_veriput_run.subject_unit_manifest.resolve_subject
    old_build = rq1_veriput_run.build_subject_schedule
    old_wait = rq1_veriput_run.wait_for_mem_budget
    old_run = rq1_veriput_run.run_command
    rq1_veriput_run.subject_unit_manifest.resolve_subject = (lambda *_args, **_kwargs: subject)
    rq1_veriput_run.build_subject_schedule = (lambda *_args, **_kwargs: schedule)
    rq1_veriput_run.wait_for_mem_budget = lambda *_args, **_kwargs: {
        "status": "ok",
        "waited": False,
    }
    rq1_veriput_run.run_command = fake_run_command
    try:
        return body()
    finally:
        rq1_veriput_run.subject_unit_manifest.resolve_subject = old_resolve
        rq1_veriput_run.build_subject_schedule = old_build
        rq1_veriput_run.wait_for_mem_budget = old_wait
        rq1_veriput_run.run_command = old_run


def test_capped_stage2_timeout_with_candidates_enters_stage4():
    subject, schedule = _mocked_subject_and_schedule(["slowA"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        if "put_all.py" not in " ".join(str(arg) for arg in argv):
            out_path = Path(argv[argv.index("--out") + 1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps({
                    "benchmark": subject.benchmark_key,
                    "unit": "slowA",
                    "bucket": "TIMEOUT",
                    "partial_witness_journal": {
                        "witness_count":
                        1,
                        "paths": [{
                            "path_id": "7",
                            "path_function": "sol:@C@C@F@slowA#1",
                            "witness_count": 1,
                        }],
                    },
                    "timed_out": True,
                }) + "\n")
            return {
                "argv": argv,
                "rc": None,
                "status": "timeout",
                "timed_out": True,
                "wall_s": round(timeout_s, 3),
                "maxrss_proc_mb": 1.0,
            }
        return {
            "argv": argv,
            "rc": 0,
            "status": "ok",
            "timed_out": False,
            "wall_s": 0.1,
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            return rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                }, "peer182", args)

    row, detail = _with_mocked_run_subject(subject, schedule, fake_run_command, body)
    stages = detail.get("stages") or []
    bad = 0
    bad += check(len(calls) == 2, f"capped timeout with candidates still runs Stage 4: {calls}")
    bad += check(stages[0].get("capped_timeout_stage4_candidates_retained") is True,
                 f"Stage-2 timeout records retained candidates: {stages}")
    bad += check(
        len(stages) > 1 and stages[1].get("stage") == "put",
        f"second stage is PUT generation: {stages}")
    bad += check(row["stage4_candidate_units_attempted"] == 1, f"Stage-4 attempt is counted: {row}")
    return bad


def test_timeout_only_stage4_skip_can_trigger_no_candidate_stop():
    subject, schedule = _mocked_subject_and_schedule(["slowA", "slowB"])
    calls = []

    def fake_run_command(argv, timeout_s, log_prefix):
        calls.append((argv, timeout_s, log_prefix))
        if "--unit" not in argv:
            return {
                "argv": argv,
                "rc": 0,
                "status": "ok",
                "timed_out": False,
                "wall_s": 0.1,
                "maxrss_proc_mb": 1.0,
            }
        out_path = Path(argv[argv.index("--out") + 1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        unit = argv[argv.index("--unit") + 1]
        out_path.write_text(
            json.dumps({
                "benchmark": subject.benchmark_key,
                "unit": unit,
                "bucket": "TIMEOUT",
                "partial_witness_journal": {
                    "witness_count":
                    1,
                    "paths": [{
                        "path_id": "7",
                        "path_function": f"sol:@C@C@F@{unit}#1",
                        "witness_count": 1,
                    }],
                },
                "timed_out": True,
            }) + "\n")
        return {
            "argv": argv,
            "rc": 0,
            "status": "ok",
            "timed_out": False,
            "wall_s": 0.1,
            "maxrss_proc_mb": 1.0,
        }

    def body():
        with tempfile.TemporaryDirectory() as td:
            args = _minimal_run_subject_args(td)
            args.min_timeout_only_stage4_s = 90
            args.no_candidate_stage2_unit_stop_n = 1
            args.min_no_candidate_stage2_unit_stop_n = 1
            args.timeout = 10
            return rq1_veriput_run.run_subject(
                {
                    "subject_id": "s",
                    "benchmark": "peer182",
                    "contract": "C",
                }, "peer182", args)

    row, detail = _with_mocked_run_subject(subject, schedule, fake_run_command, body)
    bad = 0
    bad += check(len(calls) == 2, f"timeout-only skip advances to the next unit: {calls}")
    bad += check(row["status"] == "no-output" and row["completion_status"] == "ok",
                 f"skip-driven no-candidate does not early-stop before "
                 f"remaining units: {row}")
    bad += check(row["max_consecutive_no_candidate_units"] == 1,
                 f"skip contributes to no-candidate evidence: {row}")
    bad += check(row["low_budget_timeout_only_stage4_skips"][0]["unit"] == "slowA",
                 f"skip detail is retained: {row}")
    return bad


def test_prepare_case_dir_preserves_complete_and_quarantines_partial():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        complete = root / "complete"
        complete.mkdir()
        (complete / "result.json").write_text("{}\n")
        partial = root / "partial"
        partial.mkdir()
        (partial / "unit-schedule.json").write_text("{}\n")
        redo = root / "redo"
        redo.mkdir()
        (redo / "result.json").write_text("{}\n")
        rq1_veriput_run.prepare_case_dir(complete)
        rq1_veriput_run.prepare_case_dir(partial)
        rq1_veriput_run.prepare_case_dir(redo, force_fresh=True)
        quarantined = list(root.glob("partial.incomplete.*"))
        redone = list(root.glob("redo.redo.*"))
        bad = 0
        bad += check(complete.exists() and complete.joinpath("result.json").exists(),
                     "complete case directory is preserved")
        bad += check(not partial.exists() and len(quarantined) == 1,
                     f"partial case directory is quarantined: {quarantined}")
        bad += check(not redo.exists() and len(redone) == 1,
                     f"redo case directory is archived fresh: {redone}")
        return bad


def test_stage2_no_output_stop_reason_is_audit_friendly():
    stages = [
        {
            "stage": "certify",
            "wall_s": 61.25,
        },
        {
            "stage": "put",
            "wall_s": 5.0,
        },
        {
            "stage": "certify",
            "wall_s": 43.75,
        },
    ]
    wall_s = rq1_veriput_run._stage_wall_s(stages, "certify")
    reason = rq1_veriput_run._format_stage2_no_output_stop(wall_s)
    bad = 0
    bad += check(wall_s == 105.0, f"Stage-2 wall clock sums only certification stages: {wall_s}")
    bad += check(reason == "no output after 105.0s Stage 2; "
                 "stopped before remaining units", f"early-stop reason is stable: {reason}")
    return bad


def test_stage2_no_output_stop_requires_multiple_no_candidate_units():
    stages = [
        {
            "stage": "certify",
            "wall_s": 180.0,
        },
    ]
    bad = 0
    bad += check(
        not rq1_veriput_run._should_stop_after_no_output_stage2(stages, {"raw": 0}, 90, 1, 4),
        "one heavy no-candidate unit does not end a multi-unit subject")
    bad += check(
        not rq1_veriput_run._should_stop_after_no_output_stage2(stages, {"raw": 0}, 90, 2, 4),
        "Stage-2 no-output stop does not skip remaining units")
    bad += check(rq1_veriput_run._should_stop_after_no_output_stage2(stages, {"raw": 0}, 90, 4, 4),
                 "Stage-2 no-output stop can fire after all scheduled units")
    bad += check(
        not rq1_veriput_run._should_stop_after_no_output_stage2(stages, {"raw": 1}, 90, 4, 4),
        "Stage-2 no-output stop keeps raw outputs")
    bad += check(
        not rq1_veriput_run._should_stop_after_no_output_stage2(
            stages, {"raw": 0}, 90, 1, 4, min_attempted_units=1),
        "explicit one-unit policy still cannot skip remaining units")
    bad += check(
        rq1_veriput_run._should_stop_after_no_output_stage2(stages, {"raw": 0},
                                                            90,
                                                            1,
                                                            1,
                                                            min_attempted_units=1),
        "one-unit subjects can still stop after their only miss")
    return bad


def test_tool_failures_do_not_count_as_no_candidate_stop_evidence():
    should_count = rq1_veriput_run._no_candidate_counts_against_stop
    bad = 0
    bad += check(
        not should_count({
            "bucket": "NO-WITNESS-UNKNOWN",
            "driver_diagnostic": {
                "tag": "path-coverage-no-claims-reached-solver",
            },
        }), "no-claims-reached is a tool/focus failure, not subject exhaustion")
    bad += check(
        not should_count({
            "bucket": "NO-WITNESS-UNKNOWN",
            "driver_diagnostic": {
                "tag": "frontend-tuple-rhs-symbol",
                "category": "no-cov-report",
            },
        }), "frontend no-report failure does not stop remaining cheap units")
    bad += check(should_count({
        "bucket": "NO-COORDINATE",
        "driver_diagnostic": None,
    }), "method-level no-coordinate still counts as no-candidate evidence")
    bad += check(should_count({
        "bucket": "NOT-CERTIFIED",
    }), "semantic refutation still counts as no-candidate evidence")
    return bad


def test_weak_stage2_requeue_preserves_mutator_priority_and_prefers_scalar_abi():
    def job(name, priority, parameter_types, ordinal):
        return {
            "job_id": f"job-{name}",
            "unit": name,
            "priority": priority,
            "ordinal": ordinal,
            "unit_info": {
                "parameter_types": parameter_types,
                "return_types": [],
            },
            "schedule_rank": {
                "cheap_first": [10 if name.startswith("set") else 5,
                                len(parameter_types), 0],
            },
        }

    jobs = [
        job("failed", 1, ["bytes32", "bytes"], 0),
        job("owner", 2, [], 1),
        job("setText", 1, ["bytes32", "string", "string"], 2),
        job("setAddr", 1, ["bytes32", "address"], 3),
        job("name", 2, ["bytes32"], 4),
    ]
    result = rq1_veriput_run._requeue_weak_stage2_suffix(
        jobs, 1, {
            "bucket": "NO-WITNESS-UNDECIDED",
        })
    got = [row["unit"] for row in jobs]
    bad = 0
    bad += check(got == ["failed", "setAddr", "setText", "owner", "name"],
                 f"weak continuation keeps mutators ahead and scalar ABI first: {got}")
    bad += check(result is not None and "semantic priority" in result["reason"],
                 f"requeue decision is auditable: {result}")
    return bad


def test_zero_output_stage4_stop_is_thresholded_and_raw_sensitive():
    stages = [
        {
            "stage": "put",
            "wall_s": 49.5,
        },
    ]
    bad = 0
    bad += check(not rq1_veriput_run._should_stop_after_zero_output_stage4(stages, {"raw": 0}, 0),
                 "Stage-4 zero-output stop defaults off")
    bad += check(not rq1_veriput_run._should_stop_after_zero_output_stage4(stages, {"raw": 1}, 30),
                 "Stage-4 zero-output stop keeps raw outputs")
    bad += check(not rq1_veriput_run._should_stop_after_zero_output_stage4(stages, {"raw": 0}, 60),
                 "Stage-4 zero-output stop waits for threshold")
    bad += check(rq1_veriput_run._should_stop_after_zero_output_stage4(stages, {"raw": 0}, 30),
                 "Stage-4 zero-output stop fires after threshold")
    bad += check(
        rq1_veriput_run._format_stage4_no_output_stop(
            49.5) == "no output after 49.5s Stage 4; stopped before remaining units",
        "Stage-4 early-stop reason is stable")
    return bad


def test_no_candidate_stage2_unit_stop_is_thresholded_and_raw_sensitive():
    bad = 0
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(4, {"raw": 0}, 0),
                 "no-candidate unit stop defaults off")
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(4, {"raw": 1}, 4),
                 "no-candidate unit stop keeps raw outputs")
    bad += check(not rq1_veriput_run._should_stop_after_no_candidate_units(3, {"raw": 0}, 4),
                 "no-candidate unit stop waits for threshold")
    bad += check(rq1_veriput_run._should_stop_after_no_candidate_units(4, {"raw": 0}, 4),
                 "no-candidate unit stop fires at threshold")
    bad += check(
        not rq1_veriput_run._should_stop_after_no_candidate_units(
            4, {"raw": 0}, 4, units_scheduled=6),
        "no-candidate unit stop does not skip remaining units")
    bad += check(
        rq1_veriput_run._should_stop_after_no_candidate_units(6, {"raw": 0}, 4, units_scheduled=6),
        "no-candidate unit stop can fire after all scheduled units")
    bad += check(
        not rq1_veriput_run._should_stop_after_no_candidate_units(
            4, {"raw": 0}, 4, pending_hinted_units=1),
        "no-candidate unit stop does not skip pending target hints")
    jobs = [
        {
            "unit": "prefix",
            "unit_hints": {
                "hinted_units": ["target"]
            }
        },
        {
            "unit": "target",
            "unit_hints": {
                "hinted_units": ["target"]
            }
        },
    ]
    bad += check(
        rq1_veriput_run._pending_hinted_units(jobs, ["prefix"]) == 1,
        "pending target hint is visible after a noisy prefix unit")
    bad += check(
        rq1_veriput_run._pending_hinted_units(jobs, ["prefix", "target"]) == 0,
        "attempted target hint no longer blocks early stop")
    bad += check(
        rq1_veriput_run._format_no_candidate_unit_stop(4) ==
        "no Stage-2 candidate after 4 consecutive units; "
        "stopped before remaining units", "no-candidate early-stop reason is stable")
    return bad


def test_low_budget_concrete_only_stage4_skip_is_valid_and_put_sensitive():
    should = rq1_veriput_run._should_skip_low_budget_concrete_only_stage4
    bad = 0
    bad += check(should({
        "raw": 4,
        "valid": 4
    }, 36.5, 90, 0, 0, 3), "low-budget timeout-concrete-only Stage 4 skips after valid")
    bad += check(should({
        "raw": 4,
        "valid": 4
    }, 36.5, 90, 0, 1, 0), "low-budget cleared-concrete-only Stage 4 skips after valid")
    bad += check(not should({
        "raw": 4,
        "valid": 4
    }, 120.0, 90, 0, 0, 3), "concrete-only Stage 4 keeps enough generation budget")
    bad += check(not should({
        "raw": 4,
        "valid": 0
    }, 36.5, 90, 0, 0, 3), "concrete-only Stage 4 is not skipped before a valid artifact")
    bad += check(not should({
        "raw": 4,
        "valid": 4
    }, 36.5, 90, 1, 0, 3), "certified regions are never skipped by the concrete-only floor")
    bad += check(not should({
        "raw": 4,
        "valid": 4
    }, 36.5, 0, 0, 0, 3), "concrete-only floor can be disabled")
    reason = rq1_veriput_run._format_low_budget_concrete_only_skip(36.456, 90)
    bad += check("36.5s remains below the 90s" in reason,
                 f"low-budget concrete-only reason is audit-friendly: {reason}")
    return bad


def test_low_budget_timeout_only_stage4_skip_is_candidate_sensitive():
    should = rq1_veriput_run._should_skip_low_budget_timeout_only_stage4
    bad = 0
    bad += check(should(36.5, 90, 0, 0, 1),
                 "low-budget timeout-only Stage 4 skips partial witnesses")
    bad += check(should(36.5, 90, 0, 0, 0, 1), "low-budget complete-witness-only Stage 4 skips")
    bad += check(not should(36.5, 90, 1, 0, 1),
                 "certified regions are never skipped by timeout-only floor")
    bad += check(not should(36.5, 90, 0, 1, 1), "cleared concrete fallback is not timeout-only")
    bad += check(not should(120.0, 90, 0, 0, 1),
                 "timeout-only Stage 4 keeps enough generation budget")
    bad += check(not should(36.5, 0, 0, 0, 1), "timeout-only floor can be disabled")
    reason = rq1_veriput_run._format_low_budget_timeout_only_skip(36.456, 90)
    bad += check("36.5s remains below the 90s" in reason,
                 f"timeout-only reason is audit-friendly: {reason}")
    return bad


def test_put_saturated_concrete_only_stage4_skip_keeps_put_work():
    should = rq1_veriput_run._should_skip_concrete_only_after_puts
    bad = 0
    bad += check(should({"put_valid": 2}, 2, 0, 1, 0),
                 "PUT-saturated cleared fallback Stage 4 is skipped")
    bad += check(should({"put_valid": 3}, 2, 0, 0, 4),
                 "PUT-saturated timeout fallback Stage 4 is skipped")
    bad += check(not should({"put_valid": 1}, 2, 0, 1, 4),
                 "PUT-saturated skip waits for enough valid PUT artifacts")
    bad += check(not should({"put_valid": 2}, 2, 1, 1, 4),
                 "PUT-saturated skip never drops certified regions")
    bad += check(not should({"put_valid": 2}, 0, 0, 1, 4), "PUT-saturated skip can be disabled")
    reason = rq1_veriput_run._format_put_saturated_concrete_only_skip(3, 2)
    bad += check("3 valid PUT artifact(s)" in reason and "2-PUT floor" in reason,
                 f"PUT-saturated skip reason is audit-friendly: {reason}")
    return bad


def test_valid_saturated_concrete_only_stage4_skip_preserves_put_budget():
    should = rq1_veriput_run._should_skip_concrete_only_after_any_valid
    bad = 0
    bad += check(should({
        "valid": 1,
        "put_valid": 0
    }, True, 0, 1, 0), "after any valid artifact, cleared concrete-only work is skipped")
    bad += check(should({
        "valid": 1,
        "put_valid": 1
    }, True, 0, 0, 1), "after a PUT, timeout concrete-only work is skipped")
    bad += check(not should({
        "valid": 0,
        "put_valid": 0
    }, True, 0, 1, 0), "before a valid artifact, concrete fallback can establish validity")
    bad += check(not should({
        "valid": 1,
        "put_valid": 0
    }, True, 1, 1, 0), "certified regions are never skipped by the any-valid rule")
    bad += check(not should({
        "valid": 1,
        "put_valid": 0
    }, False, 0, 1, 0), "any-valid concrete-only skip can be disabled")
    reason = rq1_veriput_run._format_valid_saturated_concrete_only_skip(2, 1)
    bad += check("2 valid artifact(s)" in reason and "1 PUT" in reason and "PUT/R1/R2" in reason,
                 f"any-valid skip reason is audit-friendly: {reason}")
    return bad


def main():
    tests = [
        test_latest_rows_coalesces_legacy_and_canonical_subject_keys,
        test_dataset_manifest_excludes_deploy_only_validity,
        test_path_guard_allows_only_veriput_rq1_result_tree,
        test_pipeline_identity_includes_dependency_modules,
        test_stage4_toolchain_identity_uses_foundry_prepend_path,
        test_put_artifact_summary_counts_raw_valid_and_oracle_classes,
        test_strength_quality_bucket_keeps_no_put_and_no_r1r2_visible,
        test_normalize_result_row_trusts_valid_test_oracle_classes,
        test_normalize_result_row_requires_explicit_double_oracle_validity,
        test_row_strength_prioritizes_methodology_quality,
        test_resume_retries_empty_no_valid_rows_only,
        test_resume_retries_certified_or_partial_zero_output_rows,
        test_load_subject_result_row_recovers_certification_diagnostics,
        test_adoption_updates_equal_strength_rows_with_cert_evidence,
        test_normalize_result_row_recomputes_stale_aggregate_quality,
        test_adoption_updates_equal_strength_rows_with_artifact_evidence,
        test_merge_put_summary_marks_valid_partial_artifacts_ok,
        test_load_subject_result_row_adopts_put_artifacts,
        test_results_all_requires_double_oracle_validity,
        test_adopt_existing_subject_results_promotes_stale_sidecar_artifacts,
        test_fresh_no_valid_run_does_not_adopt_stale_valid_artifacts,
        test_fresh_no_unit_no_valid_run_does_not_adopt_stale_valid_artifacts,
        test_resource_killed_zero_valid_run_can_adopt_stale_valid_artifacts,
        test_resource_killed_zero_valid_rejects_stale_when_replay_fails,
        test_stale_replay_runs_and_matches_exact_function_signature,
        test_stale_replay_rejects_no_tests_exit_zero,
        test_stale_replay_rejects_similar_test_name_collision,
        test_stale_replay_rejects_missing_recorded_file,
        test_stale_replay_rejects_same_test_name_in_other_file,
        test_stale_replay_rejects_project_without_foundry_toml,
        test_resource_killed_zero_valid_requires_schedule_identity,
        test_resource_killed_zero_valid_rejects_newer_source,
        test_resource_killed_zero_valid_rejects_different_source_digest,
        test_resource_killed_zero_valid_rejects_different_solast_digest,
        test_resource_killed_zero_valid_rejects_different_cached_solast_digest,
        test_resource_killed_zero_valid_rejects_binary_mismatch,
        test_resource_killed_zero_valid_rejects_pipeline_mismatch,
        test_resource_killed_zero_valid_rejects_stage4_toolchain_mismatch,
        test_resume_quality_floor_can_focus_no_put_and_no_r1r2,
        test_empty_schedule_status_preserves_preparation_failures,
        test_no_unit_deploy_source_rejects_abstract_selected_target,
        test_no_unit_deploy_fallback_writes_raw_concrete_artifact,
        test_no_unit_deploy_fallback_uses_prepared_source_fallback,
        test_no_unit_constructor_revert_fallback_is_behavioral_valid,
        test_constructor_arg_repair_deploy_is_still_smoke_only,
        test_no_unit_getter_fallback_selects_only_fresh_zero_arg_getters,
        test_no_unit_getter_fallback_rejects_non_no_unit_schedule,
        test_ownable_owner_stage2_fixture_uses_esbmc_store_name,
        test_ownable_msg_sender_owner_stage2_fixture_uses_esbmc_store_name,
        test_ownable_fixture_rejects_unrelated_nonzero_address_parameter,
        test_transparent_proxy_stage2_fixture_uses_zero_storage_runtime,
        test_asset_list_stage2_fixture_uses_empty_array_revert_path,
        test_euler_cash_stage2_fixture_uses_proxy_entry_zero_storage,
        test_peg_stability_module_fixture_uses_legal_foundry_constructor,
        test_transfer_helper_fixture_retains_zero_conduit_rejection,
        test_euler_initialize_fixture_rebuilds_direct_deploy_guard,
        test_euler_risk_manager_fixture_retains_proxy_auth_rejection,
        test_run_subject_records_no_unit_deploy_fallback_schema,
        test_valid_reference_rejects_deploy_and_creation_aliases,
        test_real203_cache_uses_prepared_benchmark_namespace,
        test_jobs_admission_refuses_oversubscription,
        test_target_rows_fast_first_sorts_before_limit,
        test_target_rows_fast_first_uses_bugfix_fallback_size,
        test_target_rows_fast_first_uses_peer_fallback_size,
        test_certification_summary_identifies_inner_timeouts,
        test_certification_summary_uses_diagnostics_for_no_output_reason,
        test_cleared_concrete_fallbacks_trigger_stage4,
        test_subject_schedule_uses_separate_esbmc_run_timeout,
        test_certify_argv_for_remaining_caps_only_run_timeout,
        test_stage2_unit_timeout_cap_defaults_to_adaptive,
        test_adaptive_stage2_unit_timeout_cap_policy,
        test_stage2_wrapper_timeout_uses_effective_unit_cap,
        test_schedule_annotation_records_runtime_stage2_caps,
        test_certify_argv_for_remaining_honors_unit_timeout_cap,
        test_stage2_cert_shard_argv_and_merge,
        test_capped_stage2_timeout_advances_to_next_unit,
        test_stage2_no_output_stop_ignores_tool_failure_units,
        test_overload_refusal_appends_path_function_jobs,
        test_capped_stage2_timeout_with_candidates_enters_stage4,
        test_timeout_only_stage4_skip_can_trigger_no_candidate_stop,
        test_prepare_case_dir_preserves_complete_and_quarantines_partial,
        test_stage2_no_output_stop_reason_is_audit_friendly,
        test_stage2_no_output_stop_requires_multiple_no_candidate_units,
        test_tool_failures_do_not_count_as_no_candidate_stop_evidence,
        test_weak_stage2_requeue_preserves_mutator_priority_and_prefers_scalar_abi,
        test_zero_output_stage4_stop_is_thresholded_and_raw_sensitive,
        test_no_candidate_stage2_unit_stop_is_thresholded_and_raw_sensitive,
        test_low_budget_concrete_only_stage4_skip_is_valid_and_put_sensitive,
        test_low_budget_timeout_only_stage4_skip_is_candidate_sensitive,
        test_put_saturated_concrete_only_stage4_skip_keeps_put_work,
        test_valid_saturated_concrete_only_stage4_skip_preserves_put_budget,
    ]
    bad = 0
    for test in tests:
        result = test()
        if result is None:
            result = 0
            continue
        bad += result
    if bad:
        print(f"{bad} failure(s)")
        return 1
    print(f"all {len(tests)} rq1 veriput tests passed")
    return 0


def test_adopt_only_never_runs_selected_subjects(monkeypatch, tmp_path):
    calls = {"adopt": 0, "run": 0}
    target = {
        "subject_id": "subject",
        "benchmark_key": "peer182",
        "contract": "C",
    }

    monkeypatch.setattr(rq1_veriput_run, "validate_roots", lambda *_args: None)
    monkeypatch.setattr(rq1_veriput_run, "validate_jobs", lambda _args: None)
    monkeypatch.setattr(rq1_veriput_run, "target_rows", lambda *_args: ("peer182", [target]))
    monkeypatch.setattr(rq1_veriput_run, "enforce_rows_in_window", lambda *_args: None)
    monkeypatch.setattr(rq1_veriput_run, "_latest_rows", lambda _path: {})

    def adopt(*_args):
        calls["adopt"] += 1
        return set()

    def run(*_args):
        calls["run"] += 1
        return 0

    monkeypatch.setattr(rq1_veriput_run, "adopt_existing_subject_results", adopt)
    monkeypatch.setattr(rq1_veriput_run, "run_selected_subjects", run)
    monkeypatch.setattr(rq1_veriput_run, "write_dataset_manifest", lambda *_args: None)

    rc = rq1_veriput_run.main([
        "--benchmark",
        "peer182",
        "--veriput-root",
        str(tmp_path / "VeriPUT"),
        "--result-root",
        str(tmp_path / "results"),
        "--ast-cache-root",
        str(tmp_path / "cache"),
        "--adopt-only",
    ])

    assert rc == 0
    assert calls == {"adopt": 1, "run": 0}


if __name__ == "__main__":
    raise SystemExit(main())
