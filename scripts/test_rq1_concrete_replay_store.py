#!/usr/bin/env python3
"""Self-contained tests for canonical RQ1 concrete replay persistence."""

from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))

from rq1_concrete_replay_store import (  # noqa: E402
    MANIFEST_NAME, STORE_DIR, ReplayPersistenceError, _atomic_json, _entry_test_keys,
    _oracle_binding_errors, _storage_slot_recovery_errors, annotate_generalization, audit_manifest,
    deterministic_replay_errors, deterministic_replay_oracles, invalidation_applies, load_manifest,
    persist_concrete_replay, persistence_coverage, repair_manifest_independence,
)


def check(condition: bool, message: str) -> int:
    if condition:
        return 0
    print("FAIL:", message)
    return 1


def fixture(root: Path) -> tuple[Path, dict]:
    project = root / "producer"
    (project / "src").mkdir(parents=True)
    (project / "test").mkdir()
    (project / "lib" / "forge-std" / "src").mkdir(parents=True)
    (project /
     "foundry.toml").write_text('[profile.default]\nsrc = "src"\ntest = "test"\nlibs = ["lib"]\n')
    (project / "src" / "flat.sol").write_text(
        "pragma solidity >=0.8.0; contract C { uint256 public x; "
        "function f() public { x = 1; } }\n")
    (project / "lib" / "forge-std" / "src" / "Test.sol").write_text(
        "pragma solidity >=0.8.0; contract Test { "
        "function assertTrue(bool value) internal pure { require(value); } "
        "function assertEq(uint256 a, uint256 b) internal pure { require(a == b); } }\n")
    test = project / "test" / "CReplay.t.sol"
    test.write_text(
        'pragma solidity >=0.8.0; import {Test} from "forge-std/Test.sol"; '
        'import {C} from "../src/flat.sol"; contract CReplay is Test { '
        'function test_cov_0() public { C c = new C(); c.f(); assertEq(c.x(), 1); } }\n')
    put_json = project / "put.json"
    put_json.write_text(
        json.dumps({
            "kind": "concrete",
            "unit": "f",
            "enc": 2,
            "path_function": "sol:@C@C@F@f#1",
            "stage2_source": "certified-region-concrete-fallback",
        }))
    return root / "rq1" / "peer182" / "subjects" / "case", {
        "kind":
        "concrete",
        "valid_reference_test":
        True,
        "forge_status":
        "Success",
        "unit":
        "f",
        "enc":
        2,
        "test":
        "test_cov_0",
        "file":
        str(test),
        "put_json":
        str(put_json),
        "concrete_oracles": [{
            "class": "concrete-value",
            "kind": "post-state",
            "observed": "c.x()",
            "expected": "1",
            "provenance": "stage2-witness",
            "target_receiver": "c",
            "assertion": "assertEq(c.x(), 1);",
        }],
    }


def structural_basis_fixture(root: Path) -> dict:
    test_name = "test_put_C_gate_path1"
    anchor_name = "test_structural_anchor_0123456789abcdef"
    destination = (f"function {test_name}(address p_msg_sender, "
                   "uint256 p_msg_value, uint256 x) public {\n"
                   "    p_msg_sender; x;\n"
                   "    (bool ok, ) = address(this).call{value: p_msg_value}(hex\"\");\n"
                   "    assertFalse(ok);\n"
                   "  }")
    fixed = ["address(uint160(1))", "1", "0"]
    anchor_source = (f"  function {anchor_name}() public {{\n"
                     f"    this.{test_name}({', '.join(fixed)});\n"
                     "  }\n")
    source = "contract Structural {\n  " + destination + "\n\n" + anchor_source + "}\n"
    test_file = root / "Structural.t.sol"
    test_file.write_text(source)
    anchor = {
        "status": "embedded",
        "binding": "structural-abi-gate/v1",
        "basis_kind": "structural-certificate-not-solver-ce",
        "test": anchor_name,
        "destination_put_test": test_name,
        "destination_put_function_sha256": hashlib.sha256(destination.encode()).hexdigest(),
        "anchor_source_sha256": hashlib.sha256(anchor_source.encode()).hexdigest(),
        "destination_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "certification_source": "structural-abi-gate-no-coordinate",
        "fixed_arguments": fixed,
        "region": {
            "msg.value": [1, 9]
        },
    }
    record = {
        "kind": "put",
        "unit": "gate",
        "enc": 1,
        "piece": None,
        "path_function": "sol:@C@C@F@gate#1",
        "stage2_source": "certified-region",
        "stage4_kind": "abi-value-gate",
        "certification_source": "structural-abi-gate-no-coordinate",
        "certified_detail_source": "structural-abi-gate-no-coordinate",
        "certified_detail_stage4_kind": "abi-value-gate",
        "derived_by": {
            "region_refinement_used": True
        },
        "test": test_name,
        "file": str(test_file),
        "region": {
            "msg.value": ["1", "9"]
        },
        "ce_anchor": anchor,
    }
    put_json = root / "put.json"
    forge_suite = root / "forge-suite.json"
    forge_suite.write_text(
        json.dumps({
            "Structural": {
                "test_results": {
                    test_name: {
                        "status": "Success"
                    },
                    anchor_name: {
                        "status": "Success"
                    },
                }
            }
        }))
    anchor["forge_gate"] = {
        "put_test": test_name,
        "anchor_test": anchor_name,
        "put_status": "Success",
        "anchor_status": "Success",
        "source_sha256": hashlib.sha256(test_file.read_bytes()).hexdigest(),
        "suite_log": forge_suite.name,
        "suite_log_sha256": hashlib.sha256(forge_suite.read_bytes()).hexdigest(),
    }
    put_json.write_text(json.dumps(record))
    return {
        **record,
        "put_json": str(put_json),
        "is_put": True,
        "b": True,
        "valid_reference_test": True,
        "forge_status": "Success",
        "ce_anchor_forge_status": "Success",
        "refused": False,
        "stale": None,
        "gates": {
            name: True
            for name in ("fuzz", "width", "assert", "green", "corpus")
        },
    }


def test_structural_anchor_basis_is_strictly_authenticated() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        row = structural_basis_fixture(root)
        coverage = persistence_coverage([row], [], root)
        bad += check(
            coverage["complete"] and coverage["persisted_structural_basis_count"] == 1
            and coverage["put_basis_missing_count"] == 0,
            "an audited structural anchor satisfies its PUT basis obligation")

        forged_status = structural_basis_fixture(root)
        Path(forged_status["put_json"]).parent.joinpath("forge-suite.json").unlink()
        bad += check(
            persistence_coverage([forged_status], [], root)["put_basis_missing_count"] == 1,
            "structural summary status without retained Forge evidence is rejected")
        row = structural_basis_fixture(root)
        suite = Path(row["put_json"]).parent / "forge-suite.json"
        suite.write_text(suite.read_text().replace('"Success"', '"Failure"', 1))
        bad += check(
            persistence_coverage([row], [], root)["put_basis_missing_count"] == 1,
            "mutated structural Forge evidence is rejected")
        row = structural_basis_fixture(root)

        record = json.loads(Path(row["put_json"]).read_text())
        record["region"]["msg.sender"] = ["1", "9"]
        Path(row["put_json"]).write_text(json.dumps(record))
        bad += check(
            persistence_coverage([row], [], root)["put_basis_missing_count"] == 0,
            "a fixed structural sender may inhabit its widened record region")
        record["region"]["msg.sender"] = ["2", "9"]
        Path(row["put_json"]).write_text(json.dumps(record))
        bad += check(
            persistence_coverage([row], [], root)["put_basis_missing_count"] == 1,
            "a fixed structural sender outside its region is rejected")
        record["region"].pop("msg.sender")
        Path(row["put_json"]).write_text(json.dumps(record))

        red = {**row, "ce_anchor_forge_status": "Failure"}
        bad += check(
            persistence_coverage([red], [], root)["put_basis_missing_count"] == 1,
            "a Forge-red structural anchor remains a missing basis")

        record = json.loads(Path(row["put_json"]).read_text())
        broken_anchor = {**record["ce_anchor"], "anchor_source_sha256": "0" * 64}
        record["ce_anchor"] = broken_anchor
        Path(row["put_json"]).write_text(json.dumps(record))
        broken = {**row, "ce_anchor": broken_anchor}
        bad += check(
            persistence_coverage([broken], [], root)["put_basis_missing_count"] == 1,
            "structural anchor metadata cannot substitute for its source hash")

        ordinary = structural_basis_fixture(root)
        ordinary_anchor = {**ordinary["ce_anchor"], "binding": "certified-exact-basis/v1"}
        ordinary_record = json.loads(Path(ordinary["put_json"]).read_text())
        ordinary_record["ce_anchor"] = ordinary_anchor
        ordinary_record["derived_by"] = {"region_refinement_used": False}
        Path(ordinary["put_json"]).write_text(json.dumps(ordinary_record))
        ordinary["ce_anchor"] = ordinary_anchor
        ordinary["derived_by"] = {"region_refinement_used": False}
        bad += check(
            persistence_coverage([ordinary], [], root)["put_basis_missing_count"] == 0,
            "PUTs without region refinement do not require a concrete manifest basis")
        refined = {**ordinary, "derived_by": {"region_refinement_used": True}}
        bad += check(
            persistence_coverage([refined], [], root)["put_basis_missing_count"] == 1,
            "region-refined PUTs still require an exact concrete manifest basis")
    return bad


def test_constructor_revert_replay_needs_no_path_identity() -> int:
    """A source-grounded constructor revert has no certified path to name."""

    bad = 0
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subject, concrete = fixture(root)
        put_json = Path(concrete["put_json"])
        put_json.write_text(
            json.dumps({
                "kind": "concrete",
                "unit": "f",
                "stage2_source": "source_constructor_revert_fallback",
                "stage4_kind": "constructor-revert-only",
            }))
        revert_row = {
            key: value
            for key, value in concrete.items() if key not in {"enc", "path_function"}
        }
        entry = persist_concrete_replay(subject, revert_row, dry_run=True)
        bad += check(entry["action"] == "persist",
                     "a constructor-revert replay persists without a path identity")
        bad += check(entry["origin"].get("path_function") in (None, ""),
                     "the persisted origin carries no invented path identity")

        put_json.write_text(
            json.dumps({
                "kind": "concrete",
                "unit": "f",
                "stage2_source": "certified-region-concrete-fallback",
            }))
        try:
            persist_concrete_replay(subject, revert_row, dry_run=True)
            bad += check(False, "a verifier-derived replay still needs an exact path identity")
        except ReplayPersistenceError as exc:
            bad += check("lacks exact path_function/enc identity" in str(exc),
                         "a verifier-derived replay still needs an exact path identity")
    return bad


def main() -> int:
    bad = test_structural_anchor_basis_is_strictly_authenticated()
    bad += test_constructor_revert_replay_needs_no_path_identity()
    covered_entry = {
        "origin": {
            "path_function": "sol:@C@C@F@f#new",
            "unit": "f",
            "enc": 2,
            "piece": None,
            "covered_original_identity": {
                "path_function": "",
                "unit": "f",
                "enc": 2,
                "piece": None,
            },
        },
        "test": "test_cov_0",
        "test_sha256": "new-test",
        "flat_sha256": "flat",
        "covered_original_test_sha256": "old-test",
        "covered_original_flat_sha256": "flat",
    }
    bad += check((("", "f", "2", ""), "test_cov_0", "old-test", "flat")
                 in _entry_test_keys(covered_entry),
                 "covered original identity is counted as persisted concrete")
    storage_source = ('contract R { function test_cov_0() public { c0.Initialized(); '
                      'uint256 observed = (uint256(vm.load(address(c0), bytes32(uint256(2)))) '
                      '>> 160) & uint256(0xff); assertEq(observed, uint256(1)); } }')
    storage_oracle = [{
        "class": "concrete-value",
        "kind": "storage-slot-post-state",
        "observed": "observed",
        "expected": "uint256(1)",
        "provenance": "stage2-witness",
        "target_receiver": "c0",
        "storage_variable": "intitalized",
        "storage_slot": 2,
        "storage_offset_bytes": 20,
        "storage_width_bytes": 1,
        "assertion": "assertEq(observed, uint256(1));",
    }]
    bad += check(
        not _oracle_binding_errors(storage_source, "test_cov_0", "Initialized", storage_oracle),
        "packed private scalar binds to its exact solc-layout vm.load")
    low_level_storage_source = (
        'contract R { function test_cov_0() public { '
        '(bool ok,) = address(c0).call{value: 1}('
        'abi.encodeWithSignature("Initialized()")); '
        'assertFalse(ok, "nonpayable call must revert"); '
        'uint256 observed = (uint256(vm.load(address(c0), bytes32(uint256(2)))) '
        '>> 160) & uint256(0xff); assertEq(observed, uint256(1)); } }')
    low_level_storage_oracles = [{
        "class": "R0",
        "kind": "call-status",
        "observed": "ok",
        "expected": False,
        "provenance": "stage2-witness",
        "target_receiver": "c0",
        "assertion": 'assertFalse(ok, "nonpayable call must revert");',
    }, storage_oracle[0]]
    bad += check(
        not _oracle_binding_errors(low_level_storage_source, "test_cov_0", "Initialized",
                                   low_level_storage_oracles),
        "layout post-state binds to the same low-level ABI target call")
    for field, value in (("storage_slot", 3), ("storage_offset_bytes", 19),
                         ("storage_width_bytes", 2), ("target_receiver", "other")):
        wrong = [{**storage_oracle[0], field: value}]
        bad += check(
            bool(_oracle_binding_errors(storage_source, "test_cov_0", "Initialized", wrong)),
            f"storage-slot oracle rejects wrong {field}")
    delayed_storage = storage_source.replace("uint256 observed =",
                                             "uint256 unrelated = 0; uint256 observed =")
    bad += check(
        bool(_oracle_binding_errors(delayed_storage, "test_cov_0", "Initialized", storage_oracle)),
        "storage-slot oracle rejects a non-immediate read")
    storage_original = storage_source.replace(
        'uint256 observed = (uint256(vm.load(address(c0), bytes32(uint256(2)))) '
        '>> 160) & uint256(0xff); assertEq(observed, uint256(1)); ', '')
    bad += check(
        not _storage_slot_recovery_errors(storage_source, storage_original, "test_cov_0",
                                          "Initialized", storage_oracle[0]),
        "storage-slot recovery proves an assertion-only augmentation")
    bad += check(
        bool(
            _storage_slot_recovery_errors(
                storage_source.replace("c0.Initialized();", "c0.Initialized(); c0.Initialized();"),
                storage_original, "test_cov_0", "Initialized", storage_oracle[0])),
        "storage-slot recovery rejects any extra target invocation")
    with tempfile.TemporaryDirectory() as tmp:
        deploy_revert = Path(tmp) / "DeployRevert.t.sol"
        deploy_revert.write_text(
            'pragma solidity >=0.8.0; import {Test} from "forge-std/Test.sol"; '
            'contract C { constructor(int256 x) { require(x == 0); } } '
            'contract R is Test { function test_cov_ctor_revert() public { '
            'vm.expectRevert(); new C(int256(1)); } }\n')
        revert_oracles, revert_errors = deterministic_replay_oracles(deploy_revert,
                                                                     "test_cov_ctor_revert",
                                                                     "__deploy__")
        bad += check(
            not revert_errors and revert_oracles == [{
                "class": "R0",
                "kind": "revert",
                "source": "expectRevert",
                "observed": "target call reverts",
                "expected": True,
                "provenance": "stage2-witness",
                "assertion": "vm.expectRevert();",
                "target_contract": "C",
            }], "constructor expectRevert is detected as a concrete execution oracle")
        bad += check(
            not _oracle_binding_errors(deploy_revert.read_text(), "test_cov_ctor_revert",
                                       "__deploy__", revert_oracles),
            "constructor expectRevert binds to the deploy replay")
        deploy_revert_source = deploy_revert.read_text()
    intervening_revert = ('contract R { function test_cov_0() public { vm.expectRevert(); '
                          'helper.g(); c0.f(); } }')
    bad += check(
        any("immediately armed" in error for error in _oracle_binding_errors(
            intervening_revert, "test_cov_0", "f", [{
                "class": "R0",
                "kind": "revert",
                "source": "expectRevert",
                "target_receiver": "c0"
            }])), "expectRevert cannot be consumed by an intervening external call")
    wrong_receiver_revert = ('contract R { function test_cov_0() public { vm.expectRevert(); '
                             'helper.f(); c0.f(); } }')
    bad += check(
        bool(
            _oracle_binding_errors(wrong_receiver_revert, "test_cov_0", "f",
                                   [{
                                       "class": "R0",
                                       "kind": "revert",
                                       "source": "expectRevert",
                                       "target_receiver": "c0"
                                   }])),
        "same-unit helper cannot consume the selected receiver revert expectation")
    wrong_constructor = deploy_revert_source.replace("new C(int256(1))", "new D()")
    bad += check(
        bool(
            _oracle_binding_errors(wrong_constructor, "test_cov_ctor_revert", "__deploy__",
                                   [{
                                       "class": "R0",
                                       "kind": "revert",
                                       "source": "expectRevert",
                                       "target_contract": "C"
                                   }])),
        "another constructor cannot consume the selected deployment expectation")
    return_source = ('contract R { function test_cov_0() public { '
                     'uint8 got = c0.decimals(); assertEq(got, uint8(18)); } }')
    return_oracle = [{
        "class": "R0",
        "kind": "return-value",
        "solidity_type": "uint8",
        "observed": "got",
        "expected": "uint8(18)",
        "provenance": "stage2-witness",
        "target_receiver": "c0",
        "assertion": "assertEq(got, uint8(18));",
    }]
    bad += check(not _oracle_binding_errors(return_source, "test_cov_0", "decimals", return_oracle),
                 "typed return oracle binds to the exact selected target call")
    wrong_lhs = return_source.replace("uint8 got", "uint256 got")
    bad += check(
        any("exact typed" in error for error in _oracle_binding_errors(
            wrong_lhs, "test_cov_0", "decimals", return_oracle)),
        "return oracle rejects a mismatched LHS type")
    for extra_call_source in (return_source.replace("uint8 got =", "c0.decimals(); uint8 got ="),
                              return_source.replace("assertEq(got", "c0.decimals(); assertEq(got")):
        bad += check(
            any("exact typed" in error for error in _oracle_binding_errors(
                extra_call_source, "test_cov_0", "decimals", return_oracle)),
            "return oracle rejects an extra selected target call")
    for inert_binding in (('contract R { function test_cov_0() public { uint8 got = 18; '
                           '/* uint8 got = c0.decimals(); */ assertEq(got, uint8(18)); } }'),
                          ('contract R { function test_cov_0() public { uint8 got = 18; '
                           'string memory note = "uint8 got = c0.decimals();"; '
                           'assertEq(got, uint8(18)); } }')):
        bad += check(
            any("exact typed" in error for error in _oracle_binding_errors(
                inert_binding, "test_cov_0", "decimals", return_oracle)),
            "comment/string cannot impersonate an executed return binding")
    tuple_source = ('contract R { function test_cov_0() public { '
                    '(uint80 roundId, int256 answer, uint256 startedAt) = c0.latestRoundData(); '
                    'assertEq(roundId, uint80(0)); assertEq(answer, int256(-1)); '
                    'assertEq(startedAt, uint256(0)); } }')
    tuple_oracles = [{
        "class": "R0",
        "kind": "return-value",
        "solidity_type": sol_type,
        "return_index": index,
        "return_arity": 3,
        "observed": observed,
        "expected": expected,
        "provenance": "stage2-witness",
        "target_receiver": "c0",
        "assertion": assertion,
    } for index, (sol_type, observed, expected, assertion) in enumerate((
        ("uint80", "roundId", "uint80(0)", "assertEq(roundId, uint80(0));"),
        ("int256", "answer", "int256(-1)", "assertEq(answer, int256(-1));"),
        ("uint256", "startedAt", "uint256(0)", "assertEq(startedAt, uint256(0));"),
    ))]
    bad += check(
        not _oracle_binding_errors(tuple_source, "test_cov_0", "latestRoundData", tuple_oracles),
        "complete typed tuple return binds every fixed witness component")
    bad += check(
        any("every ABI return component" in error for error in _oracle_binding_errors(
            tuple_source, "test_cov_0", "latestRoundData", tuple_oracles[:-1])),
        "tuple return rejects a missing witness component")
    missing_tuple_index = [{**oracle} for oracle in tuple_oracles]
    missing_tuple_index[1].pop("return_index")
    bad += check(
        any("every ABI return component" in error for error in _oracle_binding_errors(
            tuple_source, "test_cov_0", "latestRoundData", missing_tuple_index)),
        "tuple return rejects missing component identity without crashing")
    wrong_tuple_type = [{**oracle} for oracle in tuple_oracles]
    wrong_tuple_type[1]["solidity_type"] = "uint256"
    bad += check(
        bool(_oracle_binding_errors(tuple_source, "test_cov_0", "latestRoundData",
                                    wrong_tuple_type)),
        "tuple return rejects a mismatched component type")
    bad += check(
        bool(
            _oracle_binding_errors(
                tuple_source.replace("(uint80 roundId,", "c0.latestRoundData(); (uint80 roundId,"),
                "test_cov_0", "latestRoundData", tuple_oracles)),
        "tuple return rejects an extra selected target call")
    bad += check(
        bool(
            _oracle_binding_errors(
                tuple_source.replace("assertEq(answer, int256(-1));",
                                     "answer = 0; assertEq(answer, int256(-1));"), "test_cov_0",
                "latestRoundData", tuple_oracles)),
        "tuple return rejects an observed component overwritten after the call")
    bad += check(
        bool(
            _oracle_binding_errors(
                tuple_source.replace("assertEq(roundId, uint80(0));",
                                     "helper(); assertEq(roundId, uint80(0));"), "test_cov_0",
                "latestRoundData", tuple_oracles)),
        "tuple return rejects assertions not immediately after the target call")
    wrong_tuple_expected = [{**oracle} for oracle in tuple_oracles]
    wrong_tuple_expected[0]["assertion"] = "assertEq(roundId, uint80(7));"
    bad += check(
        any("exact fixed" in error for error in _oracle_binding_errors(
            tuple_source.replace("assertEq(roundId, uint80(0));", "assertEq(roundId, uint80(7));"),
            "test_cov_0", "latestRoundData", wrong_tuple_expected)),
        "tuple return rejects an assertion with the wrong fixed expectation")
    boolean_state_source = ('contract R { function test_cov_0() public { '
                            'c0.setApprovalForAll(operator, true); '
                            'assertTrue(c0.isApprovedForAll(address(this), operator)); } }')
    boolean_state_oracle = [{
        "class":
        "concrete-value",
        "kind":
        "post-state",
        "observed":
        "c0.isApprovedForAll(address(this), operator)",
        "expected":
        True,
        "provenance":
        "source-grounded",
        "target_receiver":
        "c0",
        "assertion":
        "assertTrue(c0.isApprovedForAll(address(this), operator));",
    }]
    bad += check(
        not _oracle_binding_errors(boolean_state_source, "test_cov_0", "setApprovalForAll",
                                   boolean_state_oracle),
        "an exact receiver state getter binds a fixed boolean assertion")
    wrong_state_receiver = [{
        **boolean_state_oracle[0], "target_receiver":
        "helper",
        "observed":
        "helper.isApprovedForAll(address(this), operator)",
        "assertion":
        "assertTrue(helper.isApprovedForAll(address(this), operator));"
    }]
    bad += check(
        bool(
            _oracle_binding_errors(
                boolean_state_source.replace("assertTrue(c0.", "assertTrue(helper."), "test_cov_0",
                "setApprovalForAll", wrong_state_receiver)),
        "a boolean state oracle rejects another receiver")
    another_getter = [{**boolean_state_oracle[0], "observed": "c0.other(address(this), operator)"}]
    bad += check(
        bool(
            _oracle_binding_errors(boolean_state_source, "test_cov_0", "setApprovalForAll",
                                   another_getter)),
        "a boolean state oracle rejects another getter")
    self_state = [{
        **boolean_state_oracle[0], "assertion":
        ("assertEq(c0.isApprovedForAll(address(this), operator), "
         "c0.isApprovedForAll(address(this), operator));")
    }]
    bad += check(
        bool(
            _oracle_binding_errors(
                boolean_state_source.replace(
                    "assertTrue(c0.isApprovedForAll(address(this), operator));",
                    self_state[0]["assertion"]), "test_cov_0", "setApprovalForAll", self_state)),
        "a self-comparison is not a fixed boolean state oracle")
    assertion_before_call = boolean_state_source.replace(
        "c0.setApprovalForAll(operator, true); "
        "assertTrue(c0.isApprovedForAll(address(this), operator));",
        "assertTrue(c0.isApprovedForAll(address(this), operator)); "
        "c0.setApprovalForAll(operator, true);")
    bad += check(
        any("immediate exact" in error for error in _oracle_binding_errors(
            assertion_before_call, "test_cov_0", "setApprovalForAll", boolean_state_oracle)),
        "a boolean state assertion before the target call is rejected")
    compound_state = [{
        **boolean_state_oracle[0], "observed": ("c0.isApprovedForAll(address(this), operator) == "
                                                "c0.isApprovedForAll(address(this), operator)"),
        "assertion": ("assertTrue(c0.isApprovedForAll(address(this), operator) == "
                      "c0.isApprovedForAll(address(this), operator));")
    }]
    bad += check(
        bool(
            _oracle_binding_errors(
                boolean_state_source.replace(boolean_state_oracle[0]["assertion"],
                                             compound_state[0]["assertion"]), "test_cov_0",
                "setApprovalForAll", compound_state)),
        "a compound getter self-comparison is rejected")
    commented_call = ('contract R { function test_cov_0() public { '
                      '// helper.setApprovalForAll(operator, true);\n'
                      'assertTrue(helper.isApprovedForAll(address(this), operator)); '
                      'c0.setApprovalForAll(operator, true); '
                      'assertTrue(c0.isApprovedForAll(address(this), operator)); } }')
    helper_oracle = [{
        **boolean_state_oracle[0], "target_receiver": "helper",
        "observed": "helper.isApprovedForAll(address(this), operator)",
        "assertion": "assertTrue(helper.isApprovedForAll(address(this), operator));"
    }]
    bad += check(
        bool(
            _oracle_binding_errors(commented_call, "test_cov_0", "setApprovalForAll",
                                   helper_oracle)),
        "a target call written only in a comment cannot bind a receiver")
    second_target_call = boolean_state_source.replace(
        "assertTrue(c0.isApprovedForAll(address(this), operator));",
        "c0.setApprovalForAll(operator, false); "
        "assertTrue(c0.isApprovedForAll(address(this), operator));")
    bad += check(
        bool(
            _oracle_binding_errors(second_target_call, "test_cov_0", "setApprovalForAll",
                                   boolean_state_oracle)),
        "an intervening target state change is rejected")
    string_comment_delimiters = boolean_state_source.replace(
        "c0.setApprovalForAll(operator, true);", 'string memory start = "/*"; '
        "c0.setApprovalForAll(operator, false); "
        'string memory end = "*/"; '
        "c0.setApprovalForAll(operator, true);")
    bad += check(
        bool(
            _oracle_binding_errors(string_comment_delimiters, "test_cov_0", "setApprovalForAll",
                                   boolean_state_oracle)),
        "comment delimiters inside strings cannot hide another target call")
    adjacent_helper = ('contract R { function test_cov_0() public { string memory s = "{"; } '
                       'function helper() public { c0.setApprovalForAll(operator, true); '
                       'assertTrue(c0.isApprovedForAll(address(this), operator)); } }')
    bad += check(
        bool(
            _oracle_binding_errors(adjacent_helper, "test_cov_0", "setApprovalForAll",
                                   boolean_state_oracle)),
        "braces inside strings cannot merge a helper into the selected test")
    event_assertions = (
        "assertEq(_veriputLogs.length, 1);"
        "assertEq(_veriputLogs[0].emitter, address(c0));"
        "assertEq(_veriputLogs[0].topics.length, 2);"
        'assertEq(_veriputLogs[0].topics[0], keccak256("Updated(address,uint256)"));'
        "assertEq(_veriputLogs[0].topics[1], "
        "bytes32(uint256(uint160(address(uint160(7))))));"
        "assertEq(_veriputLogs[0].data, abi.encode(uint256(9)));")
    event_source = ("contract R { function test_cov_0() public { "
                    "vm.recordLogs(); c0.f(address(uint160(7)), 9); "
                    "Vm.Log[] memory _veriputLogs = vm.getRecordedLogs(); " + event_assertions +
                    " } }")
    event_oracle = [{
        "class": "concrete-value",
        "kind": "event-log",
        "observed": "_veriputLogs",
        "expected": {
            "log_count":
            1,
            "event_index":
            0,
            "emitter":
            "address(c0)",
            "topics": [
                'keccak256("Updated(address,uint256)")',
                "bytes32(uint256(uint160(address(uint160(7)))))",
            ],
            "data":
            "abi.encode(uint256(9))",
        },
        "provenance": "source-grounded",
        "target_receiver": "c0",
        "assertion": event_assertions,
    }]
    bad += check(not _oracle_binding_errors(event_source, "test_cov_0", "f", event_oracle),
                 "an exact recorded event binds emitter, topics, data, and log count")
    try_event_source = event_source.replace("c0.f(address(uint160(7)), 9);",
                                            "try c0.f(address(uint160(7)), 9) {} catch {}")
    bad += check(not _oracle_binding_errors(try_event_source, "test_cov_0", "f", event_oracle),
                 "an exact event assertion makes an empty try/catch replay strict")
    nonempty_event_catch = try_event_source.replace("catch {}", "catch { helper(); }")
    bad += check(
        any("catch body" in error for error in _oracle_binding_errors(
            nonempty_event_catch, "test_cov_0", "f", event_oracle)),
        "an event oracle rejects behavior in the catch branch")
    nonempty_event_success = try_event_source.replace(") {} catch", ") { helper(); } catch")
    bad += check(
        any("success body" in error for error in _oracle_binding_errors(
            nonempty_event_success, "test_cov_0", "f", event_oracle)),
        "an event oracle rejects behavior in the try success branch")
    wrong_event_emitter = [{
        **event_oracle[0], "expected": {
            **event_oracle[0]["expected"], "emitter": "address(helper)"
        }
    }]
    bad += check(bool(_oracle_binding_errors(event_source, "test_cov_0", "f", wrong_event_emitter)),
                 "an event from another emitter cannot certify the target call")
    missing_event_topic = [{
        **event_oracle[0], "assertion":
        event_assertions.replace(
            "assertEq(_veriputLogs[0].topics[1], "
            "bytes32(uint256(uint160(address(uint160(7))))));", "")
    }]
    bad += check(
        any("exact emitter" in error for error in _oracle_binding_errors(
            event_source.replace(
                "assertEq(_veriputLogs[0].topics[1], "
                "bytes32(uint256(uint160(address(uint160(7))))));", ""), "test_cov_0", "f",
            missing_event_topic)), "an event oracle cannot omit an indexed topic assertion")
    extra_event_call = event_source.replace("vm.recordLogs();",
                                            "vm.recordLogs(); c0.f(address(uint160(7)), 9);")
    bad += check(
        any("exactly one" in error
            for error in _oracle_binding_errors(extra_event_call, "test_cov_0", "f", event_oracle)),
        "an event oracle rejects an extra selected target call")
    late_event_read = event_source.replace(
        "Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();",
        "helper(); Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();")
    bad += check(
        any("immediately closed" in error
            for error in _oracle_binding_errors(late_event_read, "test_cov_0", "f", event_oracle)),
        "an intervening call cannot enter the recorded event window")
    early_event_helper = event_source.replace("vm.recordLogs();", "vm.recordLogs(); helper();")
    bad += check(
        any("immediately closed" in error for error in _oracle_binding_errors(
            early_event_helper, "test_cov_0", "f", event_oracle)),
        "a helper cannot emit the asserted event before the target call")
    self_referential_event = [{
        **event_oracle[0], "expected": {
            **event_oracle[0]["expected"],
            "topics": ["_veriputLogs[0].topics[0]"],
            "data": "_veriputLogs[0].data",
        }
    }]
    bad += check(
        bool(_oracle_binding_errors(event_source, "test_cov_0", "f", self_referential_event)),
        "event expectations cannot compare observed logs to themselves")
    injected_event_expression = [{
        **event_oracle[0], "expected": {
            **event_oracle[0]["expected"],
            "data": 'abi.encode(uint256(9)), "message"',
        }
    }]
    bad += check(
        bool(_oracle_binding_errors(event_source, "test_cov_0", "f", injected_event_expression)),
        "event expectations reject top-level expression injection")
    trailing_event_action = event_source.replace(event_assertions + " } }",
                                                 event_assertions + " helper(); } }")
    bad += check(
        any("unbound statements" in error for error in _oracle_binding_errors(
            trailing_event_action, "test_cov_0", "f", event_oracle)),
        "an event oracle rejects trailing unbound behavior")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subject, concrete = fixture(root)
        event_gate = root / "EventReplay.t.sol"
        event_gate.write_text(event_source)
        bad += check(not deterministic_replay_errors(event_gate, "test_cov_0", "f"),
                     "recorded exact logs are detected as an execution-result oracle")
        event_gate.write_text(try_event_source)
        bad += check(not deterministic_replay_errors(event_gate, "test_cov_0", "f"),
                     "a recorded log after try/catch still asserts target execution")
        original_event = root / "producer" / "test" / "OriginalEventReplay.t.sol"
        original_event.write_text("contract R { function test_cov_0() public { "
                                  "c0.f(address(uint160(7)), 9); } }")
        recovered_event = root / "producer" / "test" / "RecoveredEventReplay.t.sol"
        recovered_event.write_text('import {Vm} from "forge-std/Vm.sol"; ' + event_source)
        recovered_event_row = {
            **concrete,
            "file": str(recovered_event),
            "recovered_from_file": str(original_event),
            "concrete_oracles": event_oracle,
        }
        recovered_event_entry = persist_concrete_replay(subject, recovered_event_row, dry_run=True)
        bad += check(recovered_event_entry["origin"]["recovered_from_test_sha256"],
                     "an exact event-only augmentation retains its original test identity")
        recovered_event.write_text(recovered_event.read_text().replace(
            "c0.f(address(uint160(7)), 9);", "c0.f(address(uint160(8)), 9);"))
        try:
            persist_concrete_replay(subject, recovered_event_row, dry_run=True)
            bad += check(False, "event recovery cannot alter the witness call")
        except ReplayPersistenceError:
            pass
        dry = persist_concrete_replay(subject, concrete, dry_run=True)
        bad += check(dry["action"] == "persist", "dry-run reports the pending copy")
        bad += check(not (subject / "concrete-replays").exists(),
                     "dry-run does not create canonical storage")

        entry = persist_concrete_replay(subject, concrete)
        manifest = load_manifest(subject)
        project = subject / entry["project"]
        bad += check(
            project.joinpath(entry["test_file"]).is_file(),
            "the canonical project contains the replay")
        bad += check(
            entry["forge_command"][-2:] == ["--match-path", entry["test_file"]]
            and entry["forge_command"][3].endswith("\\("),
            "the replay command selects an exact executable signature and file")
        bad += check(
            project.joinpath(entry["test_file"]).stat().st_ino
            != Path(concrete["file"]).stat().st_ino,
            "the canonical replay is a private copy, not a hard link")
        bad += check((project / "src" / "flat.sol").is_file(), "the exact flat source is retained")
        bad += check((project / "lib" / "forge-std" / "src" / "Test.sol").is_file(),
                     "forge-std is vendored, not a temporary symlink")
        bad += check(not audit_manifest(subject, manifest), "manifest hashes and paths audit")

        fuzz = root / "Fuzz.t.sol"
        fuzz.write_text('contract Fuzz { function test_cov_0(uint256 x) public { '
                        'C c = new C(); c.f(); assert(x == x); } }\n')
        bad += check(
            any("fuzz parameters" in error
                for error in deterministic_replay_errors(fuzz, "test_cov_0", "f")),
            "a parameterized Forge fuzz test is not a concrete replay")
        assertion_free = root / "AssertionFree.t.sol"
        assertion_free.write_text('contract AssertionFree { function test_cov_0() public { '
                                  'C c = new C(); c.f(); } }\n')
        bad += check(
            any("no execution-result assertion" in error
                for error in deterministic_replay_errors(assertion_free, "test_cov_0", "f")),
            "an assertion-free call is not a concrete replay")
        invalid = {**concrete, "file": str(assertion_free)}
        try:
            persist_concrete_replay(subject, invalid)
        except ReplayPersistenceError:
            pass
        else:
            bad += check(False, "invalid concrete replay is rejected before persistence")
        unrelated = root / "Unrelated.t.sol"
        unrelated.write_text('contract Unrelated { function test_cov_0() public { '
                             'C c = new C(); c.f(); assertEq(1, 1); } }\n')
        bad += check(
            any("not data-dependent" in error
                for error in deterministic_replay_errors(unrelated, "test_cov_0", "f")),
            "a constant assertion cannot masquerade as an execution oracle")
        self_comparison = root / "SelfComparison.t.sol"
        self_comparison.write_text('contract SelfComparison { function test_cov_0() public { '
                                   'C c = new C(); c.f(); assertEq(c.x(), c.x()); } }\n')
        bad += check(
            any("not data-dependent" in error
                for error in deterministic_replay_errors(self_comparison, "test_cov_0", "f")),
            "an observable compared only with itself is not an exact witness oracle")
        wrong_revert = root / "WrongRevert.t.sol"
        wrong_revert.write_text('contract WrongRevert { function test_cov_0() public { '
                                'C c = new C(); c.f(); vm.expectRevert(); c.f(); } }\n')
        bad += check(
            any("not immediately before" in error
                for error in deterministic_replay_errors(wrong_revert, "test_cov_0", "f")),
            "expectRevert armed after the selected call is rejected")
        fake_call = root / "FakeCall.t.sol"
        fake_call.write_text('contract FakeCall { function test_cov_0() public { '
                             'string memory s = "c.f()"; assertTrue(bytes(s).length > 0); } }\n')
        bad += check(
            any("does not invoke target" in error
                for error in deterministic_replay_errors(fake_call, "test_cov_0", "f")),
            "a target call written only in a string is rejected")
        normal_exit = root / "NormalExit.t.sol"
        normal_exit.write_text('contract NormalExit { function test_cov_0() public { '
                               'bool _veriput_concrete_completed = false; c.f(); '
                               '_veriput_concrete_completed = true; '
                               'assertTrue(_veriput_concrete_completed); } }\n')
        bad += check(not deterministic_replay_errors(normal_exit, "test_cov_0", "f"),
                     "the generator completion marker is an explicit normal-exit R0")
        try_normal_exit = root / "TryNormalExit.t.sol"
        try_normal_exit.write_text('contract TryNormalExit { function test_cov_0() public { '
                                   'bool _veriput_concrete_completed = false; '
                                   'try c.f() { _veriput_concrete_completed = true; } catch {} '
                                   'assertTrue(_veriput_concrete_completed, '
                                   '"fixed witness call must complete"); } }\n')
        bad += check(not deterministic_replay_errors(try_normal_exit, "test_cov_0", "f"),
                     "a try completion marker is bound to the successful target call")
        bad_try = root / "BadTryNormalExit.t.sol"
        bad_try.write_text('contract BadTryNormalExit { function test_cov_0() public { '
                           'bool _veriput_concrete_completed = false; try c.f() {} catch {} '
                           '_veriput_concrete_completed = true; '
                           'assertTrue(_veriput_concrete_completed, '
                           '"fixed witness call must complete"); } }\n')
        bad += check(bool(deterministic_replay_errors(bad_try, "test_cov_0", "f")),
                     "a marker set after catch cannot authenticate normal exit")
        typed_try = root / "TypedTryNormalExit.t.sol"
        typed_input = ('contract TypedTryNormalExit {\n function test_cov_0() public {\n'
                       '  try c.f{value: 1}(1) returns (uint256 value) {}\n'
                       '  catch Error(string memory reason) {}\n'
                       '  catch Panic(uint256 code) {}\n'
                       '  catch (bytes memory data) {}\n }\n}\n')
        sys.path.insert(0, str(REPO / "scripts"))
        from solidity_path_put import add_concrete_normal_exit_oracle
        typed_source, typed_oracles = add_concrete_normal_exit_oracle(typed_input, "test_cov_0",
                                                                      "f")
        typed_try.write_text(typed_source)
        bad += check(typed_oracles and typed_oracles[0]["target_receiver"] == "c",
                     "producer emits exact call-options receiver provenance")
        bad += check(not deterministic_replay_errors(typed_try, "test_cov_0", "f"),
                     "typed multi-catch producer output passes semantic replay detection")
        typed_project = root / "producer" / "test" / "TypedTryNormalExit.t.sol"
        typed_project.write_text(typed_try.read_text())
        typed_row = {**concrete, "file": str(typed_project), "concrete_oracles": typed_oracles}
        typed_entry = persist_concrete_replay(subject, typed_row, dry_run=True)
        bad += check(typed_entry["concrete_oracles"] == typed_oracles,
                     "typed multi-catch producer provenance passes the store gate")
        returning_try = typed_try.read_text().replace('catch (bytes memory data) {}',
                                                      'catch (bytes memory data) { return; }')
        typed_project.write_text(returning_try)
        try:
            persist_concrete_replay(subject, typed_row, dry_run=True)
            bad += check(False, "a catch return that bypasses the assertion is rejected")
        except ReplayPersistenceError:
            pass
        producer_source = ('contract Produced {\n  function test_cov_0() public {\n'
                           '    C c = new C();\n'
                           '    bool _veriput_concrete_completed = false;\n'
                           '    c.f();\n'
                           '    _veriput_concrete_completed = true;\n'
                           '    assertTrue(_veriput_concrete_completed, '
                           '"fixed witness call must complete");\n  }\n}\n')
        producer_oracles = [{
            "class":
            "R0",
            "kind":
            "normal-exit",
            "observed":
            "_veriput_concrete_completed",
            "expected":
            True,
            "provenance":
            "stage2-witness",
            "target_receiver":
            "c",
            "assertion": ('assertTrue(_veriput_concrete_completed, '
                          '"fixed witness call must complete");'),
        }]
        produced = root / "Produced.t.sol"
        produced.write_text(producer_source)
        produced_row = {**concrete, "file": str(produced), "concrete_oracles": producer_oracles}
        # The source is outside a Foundry project, so dry persistence stops
        # after the oracle/identity gates. Reuse the fixture project for the
        # end-to-end persistence check below.
        produced_project = root / "producer" / "test" / "Produced.t.sol"
        produced_project.write_text(producer_source)
        produced_row["file"] = str(produced_project)
        produced_entry = persist_concrete_replay(subject, produced_row, dry_run=True)
        bad += check(produced_entry["concrete_oracles"] == producer_oracles,
                     "producer normal-exit provenance passes the store gate unchanged")
        low_level_source = ('contract Produced {\n  function test_cov_0() public {\n'
                            '    (bool ok,) = address(c).call{value: 1}('
                            'abi.encodeWithSignature("f()"));\n'
                            '    assertFalse(ok, "nonpayable call must revert");\n  }\n}\n')
        low_level_oracles = [{
            "class": "R0",
            "kind": "call-status",
            "observed": "ok",
            "expected": False,
            "provenance": "stage2-witness",
            "target_receiver": "c",
            "assertion": 'assertFalse(ok, "nonpayable call must revert");',
        }]
        produced_project.write_text(low_level_source)
        low_level_row = {
            **concrete, "file": str(produced_project),
            "concrete_oracles": low_level_oracles
        }
        low_level_entry = persist_concrete_replay(subject, low_level_row, dry_run=True)
        bad += check(low_level_entry["concrete_oracles"] == low_level_oracles,
                     "a fixed low-level call status is bound to its target and assertion")
        revert_completion_source = ('contract Produced {\n  function test_cov_0() public {\n'
                                    '    bool _veriput_concrete_completed = false;\n'
                                    '    try c.f() {\n      _veriput_concrete_completed = true;\n'
                                    '    } catch {}\n'
                                    '    assertFalse(_veriput_concrete_completed, '
                                    '"fixed witness call must revert");\n  }\n}\n')
        revert_completion_oracles = [{
            "class":
            "R0",
            "kind":
            "call-status",
            "observed":
            "_veriput_concrete_completed",
            "expected":
            False,
            "provenance":
            "stage2-witness",
            "target_receiver":
            "c",
            "assertion": ('assertFalse(_veriput_concrete_completed, '
                          '"fixed witness call must revert");'),
        }]
        produced_project.write_text(revert_completion_source)
        revert_completion_row = {
            **concrete,
            "file": str(produced_project),
            "concrete_oracles": revert_completion_oracles,
        }
        revert_completion_entry = persist_concrete_replay(subject,
                                                          revert_completion_row,
                                                          dry_run=True)
        bad += check(revert_completion_entry["concrete_oracles"] == revert_completion_oracles,
                     "a strict try/catch completion marker authenticates a fixed revert result")
        wrong_receiver = [{**low_level_oracles[0], "target_receiver": "helper"}]
        low_level_row["concrete_oracles"] = wrong_receiver
        try:
            persist_concrete_replay(subject, low_level_row, dry_run=True)
            bad += check(False, "a low-level oracle for another receiver is rejected")
        except ReplayPersistenceError:
            pass
        two_calls = low_level_source.replace(
            '    assertFalse(ok, "nonpayable call must revert");',
            '    (bool other,) = address(c).call('
            'abi.encodeWithSignature("g()"));\n'
            '    assertFalse(other, "other call must revert");')
        produced_project.write_text(two_calls)
        wrong_status = [{
            **low_level_oracles[0], "observed": "other",
            "assertion": 'assertFalse(other, "other call must revert");'
        }]
        low_level_row["concrete_oracles"] = wrong_status
        try:
            persist_concrete_replay(subject, low_level_row, dry_run=True)
            bad += check(False, "another low-level call status cannot certify the target call")
        except ReplayPersistenceError:
            pass
        fallback_source = ('contract Produced {\n  function test_cov_0() public {\n'
                           '    (bool ok,) = address(c).call(hex"deadbeef");\n'
                           '    assertTrue(ok, "fallback call must complete");\n  }\n}\n')
        fallback_oracles = [{
            "class": "R0",
            "kind": "call-status",
            "observed": "ok",
            "expected": True,
            "provenance": "stage2-witness",
            "target_receiver": "c",
            "assertion": 'assertTrue(ok, "fallback call must complete");',
        }]
        produced_project.write_text(fallback_source)
        fallback_row = {
            **concrete, "unit": "fallback",
            "file": str(produced_project),
            "concrete_oracles": fallback_oracles
        }
        fallback_entry = persist_concrete_replay(subject, fallback_row, dry_run=True)
        bad += check(fallback_entry["concrete_oracles"] == fallback_oracles,
                     "a raw fallback call status binds to its selected receiver")
        fallback_row["concrete_oracles"] = [{**fallback_oracles[0], "target_receiver": "helper"}]
        try:
            persist_concrete_replay(subject, fallback_row, dry_run=True)
            bad += check(False, "a raw fallback status for another receiver is rejected")
        except ReplayPersistenceError:
            pass
        produced_project.write_text(
            fallback_source.replace(
                '    assertTrue(ok, "fallback call must complete");',
                '    (bool other,) = address(c).call(hex"feedface");\n'
                '    assertTrue(other, "second fallback call must complete");'))
        fallback_row["concrete_oracles"] = [{
            **fallback_oracles[0], "observed":
            "other",
            "assertion": ('assertTrue(other, '
                          '"second fallback call must complete");')
        }]
        try:
            persist_concrete_replay(subject, fallback_row, dry_run=True)
            bad += check(False, "an ambiguous second fallback call cannot certify the target")
        except ReplayPersistenceError:
            pass
        linked_alias = root / "linked-alias.t.sol"
        os.link(project / entry["test_file"], linked_alias)
        bad += check(any("hard-linked" in error for error in audit_manifest(subject)),
                     "legacy inode sharing is detected")
        bad += check(not repair_manifest_independence(subject),
                     "legacy inode sharing is repaired before new adoption")
        bad += check((project / entry["test_file"]).stat().st_ino != linked_alias.stat().st_ino,
                     "the repair leaves the canonical replay with a private inode")
        stale_manifest = load_manifest(subject)
        stale_manifest["entries"][0]["forge_command"] = [
            "forge", "test", "--match-test", "^test_cov_0$"
        ]
        (subject / "concrete-replays" / "manifest.json").write_text(json.dumps(stale_manifest))
        bad += check(not repair_manifest_independence(subject),
                     "legacy replay command repair preserves an auditable manifest")
        repaired_command = load_manifest(subject)["entries"][0]["forge_command"]
        bad += check(
            repaired_command[-2:] == ["--match-path", entry["test_file"]]
            and repaired_command[3] == "^test_cov_0\\(",
            "legacy no-test match expressions are migrated")
        bad += check(
            str(root) not in json.dumps(manifest),
            "manifest retains no external or temporary absolute path")

        second = persist_concrete_replay(subject, concrete)
        bad += check(second["replay_id"] == entry["replay_id"],
                     "adoption is content-addressed and idempotent")
        bad += check(
            len(load_manifest(subject)["entries"]) == 1,
            "idempotent adoption does not duplicate the manifest")

        valid_put = {
            "kind": "put",
            "valid_reference_test": True,
            "unit": "f",
            "enc": 2,
            "test": "test_put_f",
            "put_json": concrete["put_json"],
        }
        generalized = annotate_generalization(subject, [valid_put, concrete])
        classified = load_manifest(subject)["entries"][0]
        bad += check(
            generalized["same_path_candidates"] == 1
            and classified["generalization_status"] == "same-path-candidate"
            and not classified["matching_put_tests"]
            and classified["same_path_put_candidates"] == ["test_put_f"],
            "same-path identity alone remains an unconfirmed candidate")
        manifest = load_manifest(subject)
        manifest["entries"][0]["origin"]["stage2_witness_check"] = ("CERTIFIED-BASIS-REPLAY")
        manifest["entries"][0]["origin"]["stage2_source"] = ("certified-region-concrete-fallback")
        _atomic_json(subject / STORE_DIR / MANIFEST_NAME, manifest)
        generalized = annotate_generalization(subject, [valid_put, concrete])
        classified = load_manifest(subject)["entries"][0]
        bad += check(
            generalized["confirmed_generalized_to_put"] == 1
            and classified["generalization_status"] == "confirmed-generalized-to-put"
            and classified["matching_put_tests"] == ["test_put_f"],
            "explicit producer provenance confirms the generalized PUT")
        coverage = persistence_coverage([valid_put, concrete],
                                        load_manifest(subject)["entries"], subject)
        bad += check(coverage["complete"],
                     "same-path canonical concrete replay covers the PUT basis")
        not_generalized = annotate_generalization(subject, [concrete])
        classified = load_manifest(subject)["entries"][0]
        bad += check(
            not_generalized["not_generalized"] == 1
            and classified["generalization_status"] == "not-generalized"
            and not classified["matching_put_tests"],
            "a concrete replay without an exact PUT is explicitly classified")
        refined_put = {**valid_put, "derived_by": {"region_refinement_used": True}}
        other_path = {**refined_put, "enc": 3}
        bad += check(
            persistence_coverage([other_path, concrete], manifest["entries"],
                                 subject)["put_basis_missing_count"] == 1,
            "same-unit replay from a different path cannot stand in for the PUT basis")
        missing = persistence_coverage([refined_put], [], subject)
        bad += check(missing["put_basis_missing_count"] == 1 and not missing["complete"],
                     "a region-refined PUT without retained concrete provenance is an explicit gap")
        missing_concrete = persistence_coverage([concrete], [], subject)
        bad += check(
            missing_concrete["valid_concrete_missing_count"] == 1
            and not missing_concrete["complete"],
            "every valid concrete test must itself be retained")
        invalid_entry_coverage = persistence_coverage(
            [refined_put, concrete], [{
                **manifest["entries"][0], "forge_log_sha256": "bad"
            }], subject)
        bad += check(
            invalid_entry_coverage["put_basis_missing_count"] == 1
            and invalid_entry_coverage["valid_concrete_missing_count"] == 1
            and invalid_entry_coverage["canonical_replay_count"] == 0,
            "invalid manifest entries never count as persisted evidence")
        same_name_different_file = root / "producer" / "test" / "CReplay2.t.sol"
        same_name_different_file.write_text(Path(concrete["file"]).read_text() + "// second\n")
        second_concrete = {**concrete, "file": str(same_name_different_file)}
        duplicate_name_coverage = persistence_coverage([concrete, second_concrete],
                                                       load_manifest(subject)["entries"], subject)
        bad += check(duplicate_name_coverage["valid_concrete_missing_count"] == 1,
                     "same-name tests with different content require separate retention")
        evidence_manifest = load_manifest(subject)
        evidence_entry = dict(evidence_manifest["entries"][0])
        copied_test = project / evidence_entry["test_file"]
        summary = project / "put-summary.evidence.json"
        summary.write_text(
            json.dumps({
                "deliverable_b": {
                    "rows": [{
                        "kind": "concrete",
                        "test": evidence_entry["test"],
                        "unit": evidence_entry["origin"]["unit"],
                        "enc": evidence_entry["origin"]["enc"],
                        "file": str(copied_test),
                        "forge_status": "Success",
                        "valid_reference_test": True,
                    }]
                }
            }))
        evidence_entry.pop("forge_log", None)
        evidence_entry.pop("forge_log_sha256", None)
        evidence_entry["execution_evidence"] = {
            "kind": "put-summary-row",
            "summary_file": summary.name,
            "summary_sha256": __import__("hashlib").sha256(summary.read_bytes()).hexdigest(),
            "test_sha256": evidence_entry["test_sha256"],
        }
        bad += check(not audit_manifest(subject, {"entries": [evidence_entry]}),
                     "exact put-summary concrete row can authenticate metadata-only recovery")
        bad_summary = dict(evidence_entry)
        bad_summary["execution_evidence"] = {
            **bad_summary["execution_evidence"], "test_sha256": "bad"
        }
        bad += check(
            any("execution evidence" in error
                for error in audit_manifest(subject, {"entries": [bad_summary]})),
            "put-summary execution evidence is hash-bound to the test")

        copied_test = project / entry["test_file"]
        copied_test.write_text(copied_test.read_text() + "// changed\n")
        bad += check(any("hash mismatch" in error for error in audit_manifest(subject)),
                     "post-adoption mutation is detected")

        ledger = root / "pollution.json"
        ledger.write_text(
            json.dumps({"error_then_success_evidence_audit": {
                "affected_cases": ["peer182/case"]
            }}))
        old = root / "old.t.sol"
        old.write_text("old")
        ledger.touch()
        bad += check(invalidation_applies("peer182/case", [{
            "file": str(old)
        }], ledger), "pre-audit evidence remains invalidated")
        fresh = root / "fresh.t.sol"
        fresh.write_text("fresh")
        future = ledger.stat().st_mtime + 1
        os.utime(fresh, (future, future))
        bad += check(not invalidation_applies("peer182/case", [{
            "file": str(fresh)
        }], ledger), "a fresh repaired test can leave quarantine")
    if bad == 0:
        print("all rq1 concrete replay store tests passed")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
