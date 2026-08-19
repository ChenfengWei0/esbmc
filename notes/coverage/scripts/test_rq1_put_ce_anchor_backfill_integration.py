#!/usr/bin/env python3
"""Integration checks for partitioned, transactional CE-anchor backfill."""
# pylint: disable=protected-access,too-many-locals

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import rq1_put_ce_anchor_backfill as backfill


def check(condition, message):
    """Print one TAP-like assertion and return its failure count."""
    print(("ok - " if condition else "not ok - ") + message)
    return 0 if condition else 1


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _apply_fixture(root):
    """Create one complete strict recovery obligation in an isolated tree."""
    case = "suite/subject"
    path_function = "sol:@C@Probe@F@f#9"
    identity = [case, path_function, "f", "1", ""]
    digest = backfill._identity_digest(identity)
    subject = root / "results" / "suite" / "subjects" / "subject"
    project = subject / "put" / "f" / "certify-results"
    source = project / "test" / "Probe.t.sol"
    flat = project / "src" / "flat.sol"
    put_json = subject / "put" / "f" / "_wd" / "run" / "put.json"
    report = put_json.parent / "emit" / "cov-report.json"
    emitted = report.parent / "Probe.cov.t.sol"
    cert = subject / "cert" / "certify-results.jsonl"
    result_json = subject / "result.json"
    artifact = root / "failure-bundle.json"
    inventory = root / "inventory.json"
    progress = root / "progress.json"
    scratch = root / "scratch"
    (project / "test").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "lib").mkdir()
    (project / "lib" / "forge-std").symlink_to(Path(backfill.FORGE_STD).resolve())
    (project / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\ntest = "test"\nlibs = ["lib"]\n', encoding="utf-8")
    flat.write_text(
        "pragma solidity >=0.8.0; contract Probe { "
        "function f(uint256 x) external pure { require(x == 7); } }\n",
        encoding="utf-8")
    put_source = """pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Probe} from "../src/flat.sol";
contract ProbeTest is Test {
  Probe c0;
  function setUp() public { c0 = new Probe(); }
  function test_put_Probe_f_path1(uint256 x) public {
    vm.assume(x == 7);
    c0.f(x);
  }
}
"""
    source.write_text(put_source, encoding="utf-8")
    emitted_source = f"""pragma solidity >=0.8.0;
import {{Test}} from "forge-std/Test.sol";
import {{Probe}} from "./flat.sol";
contract ProbeReplay is Test {{
  Probe c0;
  function setUp() public {{ c0 = new Probe(); }}
  // claim: {path_function}:path:1
  function test_cov_0() public {{ c0.f(7); }}
}}
"""
    emitted.parent.mkdir(parents=True)
    emitted.write_text(emitted_source, encoding="utf-8")
    basis_source = emitted_source.replace(
        "function test_cov_0() public { c0.f(7); }",
        "function test_cov_0() public { bool _veriput_concrete_completed = false; "
        "c0.f(7); _veriput_concrete_completed = true; "
        "assertTrue(_veriput_concrete_completed, \"done\"); }")
    bundle_dir = root / "bundle" / digest
    basis = bundle_dir / "Probe.cov.t.sol"
    basis.parent.mkdir(parents=True)
    basis.write_text(basis_source, encoding="utf-8")
    claim = {
        "path_function": path_function,
        "path_id": "1",
        "exit_kind": "normal",
        "inputs": {
            "x": "7"
        },
        "env": {},
        "entry_storage": {},
        "events": [],
        "final_state": {},
        "return_value": None,
    }
    _write_json(report, {"claims": [claim]})
    detail = {"verdict": "CERTIFIED", "piece": None, "ce": {"x": "7"}}
    cert_record = {
        "path_function": path_function,
        "unit": "f",
        "certified": {
            "1": True
        },
        "certified_details": {
            "1": detail
        },
    }
    cert.parent.mkdir(parents=True)
    cert_line = json.dumps(cert_record, sort_keys=True, separators=(",", ":"))
    cert.write_text(cert_line + "\n", encoding="utf-8")
    put_doc = {
        "file": str(source),
        "test": "test_put_Probe_f_path1",
        "path_function": path_function,
        "unit": "f",
        "enc": 1,
        "piece": None,
    }
    _write_json(put_json, put_doc)
    physical_row = {**put_doc, "put_json": str(put_json)}
    _write_json(result_json, {"put": {"valid_artifacts": [physical_row]}})
    oracle = {
        "class": "R0",
        "kind": "normal-exit",
        "observed": "_veriput_concrete_completed",
        "expected": True,
        "provenance": "stage2-witness",
        "target_receiver": "c0",
        "assertion": 'assertTrue(_veriput_concrete_completed, "done");',
    }
    basis_body, _error = backfill._function_body(basis_source, "test_cov_0")
    basis_setup, _error = backfill._scoped_function_body(basis_source, "test_cov_0", "setUp")
    metadata = {
        "identity": identity,
        "test": "test_ce_anchor_fixture",
        "basis_test": "test_cov_0",
        "basis_source_sha256": _sha256(basis),
        "basis_test_body_sha256": backfill._sha256_text(basis_body),
        "basis_setup_sha256": backfill._sha256_text(basis_setup),
        "oracles": [oracle],
    }
    metadata_path = bundle_dir / "ce-anchor.json"
    _write_json(metadata_path, metadata)
    record = {
        "identity": dict(zip(("case", "path_function", "unit", "enc", "piece"), identity)),
        "identity_sha256": digest,
        "obligation_id": "\t".join(identity),
        "recovery_category": "directly-generatable",
        "observable_evidence": {
            "anchor_required_kinds": ["normal-exit"]
        },
        "ce": {
            "inputs": {
                "x": "7"
            },
            "env": {},
            "entry_state": {},
            "exit_kind": "normal",
            "return_value": None,
            "state_delta": {}
        },
        "selected_put": {
            "source_path": str(source),
            "source_sha256": _sha256(source),
            "put_json_path": str(put_json),
            "put_json_sha256": _sha256(put_json),
            "test": "test_put_Probe_f_path1",
        },
        "claim_provenance": {
            "report_path": str(report),
            "report_sha256": _sha256(report),
        },
        "certified_basis": {
            "source_path":
            str(cert),
            "source_line":
            1,
            "source_line_sha256":
            backfill._sha256_text(cert_line),
            "detail_sha256":
            backfill._sha256_text(json.dumps(detail, sort_keys=True, separators=(",", ":"))),
        },
    }
    _write_json(inventory, {"records": [record]})
    selector = {
        "identity": identity,
        "record_identity_sha256": digest,
        "canonical_source": str(source),
        "canonical_put_json": str(put_json),
        "canonical_source_expected_sha256": _sha256(source),
        "repaired_basis": str(basis),
        "repaired_basis_sha256": _sha256(basis),
        "ce_anchor_metadata": str(metadata_path),
        "anchor_test": "test_ce_anchor_fixture",
    }
    _write_json(artifact, {
        "schema": "veriput-anchor-failure-retry-bundle/v1",
        "ready": [selector],
    })
    return source, put_json, result_json, inventory, artifact, scratch, progress


def main():
    """Exercise contract scoping, rollback, and real staging Forge gates."""
    bad = 0
    multi = """contract HelperTest {
  function setUp() public { helper = 1; }
  function test_helper() public {}
}
contract SelectedTest {
  function setUp() public { selected = 2; }
  function test_put_selected(uint256 x) public { x; }
}
"""
    body, error = backfill._scoped_function_body(multi, "test_put_selected", "setUp")
    bad += check(error is None and "selected = 2" in body and "helper" not in body,
                 "setUp is selected from the PUT test's own contract")

    event_source = """contract EventTest {
  function test_cov_0() public {
    vm.recordLogs();
    vm.prank(address(7));
    c0.f(1);
    Vm.Log[] memory logs = vm.getRecordedLogs();
  }
}
"""
    tightened = backfill._tighten_event_recording_window(event_source, "test_cov_0", "f")
    semantic = backfill._solidity_code_mask(tightened).replace(" ", "").replace("\n", "")
    bad += check("vm.prank(address(7));vm.recordLogs();c0.f(1);" in semantic,
                 "event recording starts directly before the selected target call")
    normalized_oracle = backfill._normalize_event_oracle({
        "kind": "event-log",
        "provenance": "retained-ast-declaration-and-stage2-witness",
        "observed": "_veriputLogs[0]",
    })
    bad += check(
        normalized_oracle["provenance"] == "stage2-witness"
        and normalized_oracle["materializer_provenance"]
        == "retained-ast-declaration-and-stage2-witness"
        and normalized_oracle["observed"] == "_veriputLogs",
        "event oracle normalization preserves the unified Stage-2 provenance contract")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subject = root / "suite" / "subjects" / "subject"
        basis_json = subject / "put" / "basis" / "put.json"
        basis_source = subject / "put" / "basis" / "test" / "Probe.t.sol"
        basis_source.parent.mkdir(parents=True)
        source = """contract ProbeTest {
  Probe c0;
  function setUp() public { c0 = new Probe(); }
  function test_cov_0() public {
    (bool ok, ) = address(c0).call{value: 1}(abi.encodeWithSignature("f()"));
    assertFalse(ok, "value sent to a non-payable entry must revert");
  }
}
"""
        basis_source.write_text(source, encoding="utf-8")
        ce = {"msg.sender": "0", "msg.value": "1"}
        ce_sha = backfill.certified_ce_sha256(ce)
        projection = {
            "schema": "veriput-certified-ce-source-projection/v1",
            "ce_sha256": ce_sha,
            "coordinate_binding": {
                "schema": "veriput-certified-ce-source-binding/v1",
                "ce_sha256": ce_sha,
                "coordinates": {
                    "msg.sender": {
                        "kind": "path-irrelevant",
                        "certificate": "abi-value-gate-before-body/v1",
                        "certified": 0,
                        "rendered": 1,
                    },
                    "msg.value": {
                        "kind": "call-environment-literal",
                        "certified": 1,
                        "rendered": 1,
                        "source": "{value: 1}",
                    },
                },
            },
        }
        _write_json(basis_json, {
            "file": str(basis_source),
            "certification_source": "structural-abi-gate-no-coordinate",
            "certified_ce_binding": {
                "source_projection_preserved": projection,
            },
        })
        oracle = {
            "class": "R0",
            "kind": "call-status",
            "observed": "ok",
            "expected": False,
            "provenance": "stage2-witness",
            "target_receiver": "c0",
            "assertion": 'assertFalse(ok, "value sent to a non-payable entry must revert");',
        }
        entry = {
            "identity": ["suite/subject", "pf", "f", "1", ""],
            "subject_dir": str(subject),
            "recovery": {
                "partition": "structural-abi-gate",
                "structural_basis_put_json": str(basis_json),
                "structural_basis_put_json_sha256": _sha256(basis_json),
                "source_projection_sha256": backfill._sha256_text(
                    json.dumps(projection, sort_keys=True, separators=(",", ":"))),
            },
        }
        binding, error = backfill._report_binding(
            entry, {
                "certification_source": "structural-abi-gate-no-coordinate",
                "ce": ce,
            }, [oracle], source, "test_cov_0")
        bad += check(binding is not None and error is None
                     and binding["kind"] == "structural-abi-gate-certified-projection",
                     "structural ABI recovery binds an exact projected CE basis")
        entry["recovery"]["source_projection_sha256"] = "0" * 64
        refused, error = backfill._report_binding(
            entry, {
                "certification_source": "structural-abi-gate-no-coordinate",
                "ce": ce,
            }, [oracle], source, "test_cov_0")
        bad += check(refused is None and "projection seal" in str(error),
                     "structural ABI recovery rejects a changed projection seal")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        existing = root / "existing.json"
        created = root / "created.json"
        existing.write_text("original\n", encoding="utf-8")
        originals = {existing: existing.read_text(encoding="utf-8"), created: None}
        existing.write_text("changed\n", encoding="utf-8")
        created.write_text("new\n", encoding="utf-8")
        backfill._restore_transaction_files(originals)
        bad += check(
            existing.read_text(encoding="utf-8") == "original\n" and not created.exists(),
            "failed transactions restore old files and remove new records")
        existing.write_text("ours\n", encoding="utf-8")
        originals = {existing: "original\n"}
        written = {existing: "ours\n"}
        existing.write_text("concurrent\n", encoding="utf-8")
        conflicts = backfill._restore_transaction_files(originals, written)
        bad += check(
            conflicts == [str(existing)] and existing.read_text(encoding="utf-8") == "concurrent\n",
            "rollback preserves and reports a concurrent replacement")

        identity = ["suite/subject", "pf", "f", "1", ""]
        source_identity = {
            "file": str(root / "Probe.t.sol"),
            "test": "test_put",
            "path_function": "pf",
            "unit": "f",
            "enc": 1,
            "piece": None,
        }
        mismatch = dict(source_identity, unit="other")
        bad += check(
            backfill._put_document_identity_error(source_identity, {"test": "test_put"}, identity,
                                                  root / "Probe.t.sol") is None
            and "unit" in str(
                backfill._put_document_identity_error(mismatch, {"test": "test_put"}, identity,
                                                      root / "Probe.t.sol")),
            "put.json must state the complete recovery identity")
        traversal = {
            "identity": {
                "case": "suite/subject",
                "path_function": "pf",
                "unit": "f",
                "enc": "1",
                "piece": "",
            },
            "identity_sha256": "../../escape",
        }
        digest, digest_error = backfill._record_identity_digest(traversal)
        bad += check(digest is None and "digest mismatch" in str(digest_error),
                     "untrusted identity digests cannot become scratch path components")

        project = root / "canonical"
        (project / "src").mkdir(parents=True)
        (project / "test").mkdir()
        (project / "lib").mkdir()
        (project / "lib" / "forge-std").symlink_to(Path(backfill.FORGE_STD).resolve())
        (project / "foundry.toml").write_text(
            '[profile.default]\nsrc = "src"\ntest = "test"\nlibs = ["lib"]\n', encoding="utf-8")
        (project / "src" / "Probe.sol").write_text(
            "pragma solidity >=0.8.0;\n"
            "contract Probe { function f(uint256 x) external pure returns (uint256) "
            "{ return x + 1; } }\n",
            encoding="utf-8")
        source = project / "test" / "Probe.t.sol"
        original = """pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Probe} from "../src/Probe.sol";
contract ProbeTest is Test {
  Probe c0;
  function setUp() public { c0 = new Probe(); }
  function test_put_Probe_f_path1(uint256 x) public {
    vm.assume(x < type(uint256).max);
    assertEq(c0.f(x), x + 1);
  }
}
"""
        source.write_text(original, encoding="utf-8")
        merged = original.replace(
            "\n}\n", "\n  function test_ce_anchor_deadbeef() public {\n"
            "    assertEq(c0.f(7), 8);\n  }\n}\n")
        prepared = {
            "put_file": source,
            "put_test": "test_put_Probe_f_path1",
            "put_source": original,
            "metadata": {
                "identity": ["case", "pf", "f", "1", ""],
                "test": "test_ce_anchor_deadbeef",
            },
        }
        before = _sha256(source)
        result, error = backfill._validate_in_scratch(prepared, merged, project, root / "scratch",
                                                      32)
        bad += check(error is None and result["put_forge_ok"] and result["anchor_forge_ok"],
                     "staging validation requires two explicit real Forge successes")
        bad += check(
            _sha256(source) == before and source.read_text(encoding="utf-8") == original,
            "staging validation leaves the canonical source byte-identical")
        refused, error = backfill._validate_in_scratch(prepared, merged, project,
                                                       project / "nested-scratch", 1)
        bad += check(refused is None and "overlaps canonical" in str(error),
                     "staging validation refuses a destination inside the canonical project")
    with tempfile.TemporaryDirectory() as temporary:
        fixture = _apply_fixture(Path(temporary))
        source, put_json, result_json, inventory, artifact, scratch, progress = fixture
        command = [
            sys.executable,
            str(Path(backfill.__file__).resolve()),
            "--recovery-inventory",
            str(inventory),
            "--recovery-partition",
            "failures",
            "--partition-artifact",
            str(artifact),
            "--recovery-scratch-root",
            str(scratch),
            "--progress",
            str(progress),
            "--apply",
            "--limit",
            "1",
            "--record-limit",
            "1",
            "--fuzz-runs",
            "256",
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        source_text = source.read_text(encoding="utf-8")
        put_doc = json.loads(put_json.read_text(encoding="utf-8"))
        result_doc = json.loads(result_json.read_text(encoding="utf-8"))
        physical_row = result_doc["put"]["valid_artifacts"][0]
        confirmed, reason = backfill._anchor_strength_audit(physical_row,
                                                            identity=("suite/subject",
                                                                      "sol:@C@Probe@F@f#9", "f",
                                                                      "1", ""),
                                                            subject_dir=result_json.parent)
        bad += check(completed.returncode == 0 and "test_ce_anchor_" in source_text,
                     "main --apply commits one independently Forge-validated anchor")
        bad += check(
            isinstance(put_doc.get("ce_anchor"), dict)
            and isinstance(physical_row.get("ce_anchor"), dict),
            "main --apply commits both physical metadata documents")
        bad += check(confirmed and reason == "strength-confirmed",
                     "authoritative inventory accepts the canonical double-Forge evidence")
        audit_entry = {
            "identity": ["suite/subject", "sol:@C@Probe@F@f#9", "f", "1", ""],
            "subject_dir": str(result_json.parent),
            "put": physical_row,
        }
        bad += check(backfill._headline_anchor_strength_error(audit_entry, [physical_row]) is None,
                     "pre-commit headline gate accepts the exact written physical PUT")
        rejected_doc = json.loads(json.dumps(physical_row))
        rejected_doc["ce_anchor"]["forge_gate"]["anchor_status"] = "Failure"
        bad += check(
            "headline anchor strength audit failed" in str(
                backfill._headline_anchor_strength_error(audit_entry, [rejected_doc])),
            "pre-commit headline gate rejects non-green anchor metadata")
        project = source.parent.parent
        bad += check(not (project / "cache").exists() and not (project / "out").exists(),
                     "canonical Forge gates keep cache and out in external scratch")
    with tempfile.TemporaryDirectory() as temporary:
        fixture = _apply_fixture(Path(temporary))
        source, put_json, result_json, inventory, artifact, scratch, progress = fixture
        artifact_doc = json.loads(artifact.read_text(encoding="utf-8"))
        artifact_doc["ready"][0]["identity"][2] = "other"
        _write_json(artifact, artifact_doc)
        before = (_sha256(source), _sha256(put_json), _sha256(result_json))
        command = [
            sys.executable,
            str(Path(backfill.__file__).resolve()),
            "--recovery-inventory",
            str(inventory),
            "--recovery-partition",
            "failures",
            "--partition-artifact",
            str(artifact),
            "--recovery-scratch-root",
            str(scratch),
            "--progress",
            str(progress),
            "--apply",
            "--limit",
            "1",
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        after = (_sha256(source), _sha256(put_json), _sha256(result_json))
        bad += check(completed.returncode != 0 and before == after,
                     "main --apply rejects identity mismatch with no canonical mutation")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
