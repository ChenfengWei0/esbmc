#!/usr/bin/env python3
import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from argparse import Namespace

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PUT_ALL = os.path.join(REPO, "notes", "coverage", "scripts", "put_all.py")
COLLECT = os.path.join(REPO, "notes", "coverage", "scripts", "collect.py")

spec = importlib.util.spec_from_file_location("put_all", PUT_ALL)
put_all = importlib.util.module_from_spec(spec)
spec.loader.exec_module(put_all)

collect_spec = importlib.util.spec_from_file_location("collect", COLLECT)
collect = importlib.util.module_from_spec(collect_spec)
collect_spec.loader.exec_module(collect)


def check(name, got, want):
    if got == want:
        print(f"ok - {name}")
        return 0
    print(f"not ok - {name}: got {got!r}, want {want!r}")
    return 1


class _GetterEnum:

    def __init__(self, skipped):
        self.units = ()
        self.skipped = tuple(skipped)


class _GetterSubject:

    def __init__(self):
        self.unit = ""


def main():
    bad = check("forge-relative-suite-path-is-not-relativized-twice",
                put_all.project_rel_file("/tmp/Project", "test/Probe.t.sol"), "test/Probe.t.sol")
    with tempfile.TemporaryDirectory() as td:
        put_file = os.path.join(td, "Put.t.sol")
        basis_file = os.path.join(td, "Basis.t.sol")
        put_source = """\
contract ProbeTest {
  function setUp() public {
    c0 = new Probe();
  }
  function test_put_Probe_f_path1(uint256 x) public {
    assertTrue(x <= type(uint256).max);
  }
}
"""
        basis_source = """\
contract ProbeTest {
  function setUp() public {
    c0 = new Probe();
  }
  // claim: sol:@C@Probe@F@f#9:path:1
  // witness-fingerprint-sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  function test_cov_0() public {
    uint256 observed = c0.f(7);
    assertEq(observed, 9, "fixed witness return must match");
  }
}
"""
        with open(put_file, "w", encoding="utf-8") as fh:
            fh.write(put_source)
        with open(basis_file, "w", encoding="utf-8") as fh:
            fh.write(basis_source)
        certified_detail = {"ce": {"x": "7", "return": "9"}}
        path_function = "sol:@C@Probe@F@f#9"

        def source_binding(source):
            body = put_all.solidity_function_body(source, "test_cov_0")
            setup = put_all.solidity_function_body(source, "setUp")
            return {
                "status": "exact",
                "source_preserved": True,
                "ce_sha256": put_all.certified_ce_sha256(certified_detail["ce"]),
                "rendered_source_verified": True,
                "rendered_source_ce_sha256": put_all.certified_ce_sha256(certified_detail["ce"]),
                "path_function": path_function,
                "enc": 1,
                "piece": None,
                "foundry_testcase_fingerprint_sha256": "a" * 64,
                "test_body_sha256": hashlib.sha256("\n".join(body).encode()).hexdigest(),
                "setup_body_sha256": hashlib.sha256("\n".join(setup).encode()).hexdigest(),
            }

        put_rec = {
            "file": put_file,
            "test": "test_put_Probe_f_path1",
            "path_function": path_function,
            "unit": "f",
            "enc": 1,
            "piece": None,
        }
        basis_rec = {
            "file":
            basis_file,
            "test":
            "test_cov_0",
            "unit":
            "f",
            "path_function":
            path_function,
            "enc":
            1,
            "piece":
            None,
            "certified_ce_binding":
            source_binding(basis_source),
            "concrete_oracles": [{
                "kind":
                "return-value",
                "observed":
                "observed",
                "expected":
                "9",
                "provenance":
                "stage2-witness",
                "assertion": ('assertEq(observed, 9, '
                              '"fixed witness return must match");'),
            }],
        }
        anchor, error = put_all.attach_certified_ce_anchor(put_rec, basis_rec, certified_detail)
        bad += check("certified-ce-anchor-embeds", error, None)
        with open(put_file, encoding="utf-8") as fh:
            anchored_source = fh.read()
        bad += check("certified-ce-anchor-preserves-fuzz-put", put_source[:-2] in anchored_source,
                     True)
        bad += check(
            "certified-ce-anchor-keeps-fixed-result-assertion",
            ('assertEq(observed, 9, "fixed witness return must match")' in anchored_source), True)
        anchor_again, error_again = put_all.attach_certified_ce_anchor(
            put_rec, basis_rec, certified_detail)
        with open(put_file, encoding="utf-8") as fh:
            anchored_again = fh.read()
        bad += check("certified-ce-anchor-is-idempotent",
                     (error_again, anchor_again, anchored_again), (None, anchor, anchored_source))
        fuzz_basis = dict(basis_rec)
        with open(basis_file, "w", encoding="utf-8") as fh:
            fh.write(basis_source.replace("test_cov_0()", "test_cov_0(uint256 x)"))
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, fuzz_basis, certified_detail)
        bad += check("certified-ce-anchor-refuses-fuzz-basis", error,
                     "basis replay test has fuzz parameters")
        with open(basis_file, "w", encoding="utf-8") as fh:
            assertion_free = basis_source.replace(
                'assertEq(observed, 9, "fixed witness return must match");', "observed;")
            fh.write(assertion_free)
        assertion_free_rec = dict(basis_rec, certified_ce_binding=source_binding(assertion_free))
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, assertion_free_rec,
                                                            certified_detail)
        bad += check("certified-ce-anchor-refuses-assertion-free-basis", error,
                     "certified basis replay has no executable assertion")
        with open(basis_file, "w", encoding="utf-8") as fh:
            fh.write(basis_source)
        wrong_unit = dict(basis_rec)
        wrong_unit["unit"] = "g"
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, wrong_unit, certified_detail)
        bad += check("certified-ce-anchor-refuses-wrong-target-unit", error,
                     "certified basis replay does not invoke its target unit")
        with open(basis_file, "w", encoding="utf-8") as fh:
            fh.write(
                basis_source.replace(
                    "contract ProbeTest {", "contract ProbeTest {\n"
                    "  function setUp() public { c0 = new Probe(); }"))
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, basis_rec, certified_detail)
        bad += check("certified-ce-anchor-refuses-different-setup", error,
                     "PUT and certified basis replay use different setup state")
        wrong_binding = dict(basis_rec)
        wrong_binding["certified_ce_binding"] = dict(basis_rec["certified_ce_binding"],
                                                     ce_sha256="0" * 64)
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, wrong_binding,
                                                            certified_detail)
        bad += check("certified-ce-anchor-refuses-different-ce", error,
                     "certified basis replay CE hash differs from the certified detail")
        with open(basis_file, "w", encoding="utf-8") as fh:
            fh.write(basis_source)
        wrong_body_hash = dict(basis_rec)
        wrong_body_hash["certified_ce_binding"] = dict(basis_rec["certified_ce_binding"],
                                                       test_body_sha256="0" * 64)
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, wrong_body_hash,
                                                            certified_detail)
        bad += check("certified-ce-anchor-refuses-wrong-final-body-hash", error,
                     "certified basis replay final body hash differs from its CE binding")
        with open(basis_file, "w", encoding="utf-8") as fh:
            wrong_call_source = basis_source.replace("c0.f(7)", "c0.f(8)")
            fh.write(wrong_call_source)
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, basis_rec, certified_detail)
        bad += check("certified-ce-anchor-refuses-source-call-change", error,
                     "certified basis replay final body hash differs from its CE binding")
        with open(basis_file, "w", encoding="utf-8") as fh:
            fh.write(basis_source)
        wrong_return_detail = {"ce": {"x": "7", "return": "8"}}
        wrong_return_rec = dict(basis_rec)
        wrong_return_rec["certified_ce_binding"] = dict(basis_rec["certified_ce_binding"],
                                                        ce_sha256=put_all.certified_ce_sha256(
                                                            wrong_return_detail["ce"]))
        _anchor, error = put_all.attach_certified_ce_anchor(put_rec, wrong_return_rec,
                                                            wrong_return_detail)
        bad += check("certified-ce-anchor-refuses-wrong-return", error,
                     "certified basis replay return differs from the certified CE")
    witness_record = {
        "pins": {
            "msg.sender": 7
        },
        "partial_witness_journal": {
            "paths": [{
                "path_id": "3",
                "path_function": "sol:@C@Oracle@F@decimals#480",
                "witness_count": 1,
                "ce": {
                    "x": "2",
                    "msg.sender": "7",
                    "return": "0"
                },
            }],
        },
    }
    certified_detail = {
        "box": [{
            "name": "x",
            "lo": "0",
            "hi": "4",
            "holes": []
        }],
        "ce": {
            "x": "2",
            "msg.sender": "7"
        },
    }
    bad += check(
        "exact-stage2-path-supplies-return-witness",
        put_all.stage2_witness_return(witness_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      certified_detail), "0")
    bad += check(
        "different-path-function-cannot-supply-return-witness",
        put_all.stage2_witness_return(witness_record, 3, "sol:@C@Other@F@decimals#480",
                                      certified_detail), None)
    conflicting_witness_record = json.loads(json.dumps(witness_record))
    conflicting_witness_record["partial_witness_journal"]["paths"].append({
        "path_id": 3,
        "path_function": "sol:@C@Oracle@F@decimals#480",
        "witness_count": 1,
        "ce": {
            "x": "2",
            "msg.sender": "7",
            "return": "1"
        },
    })
    bad += check(
        "conflicting-stage2-returns-fail-closed",
        put_all.stage2_witness_return(conflicting_witness_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      certified_detail), None)
    mismatched_witness_record = json.loads(json.dumps(witness_record))
    mismatched_witness_record["partial_witness_journal"]["paths"][0]["ce"]["x"] = "3"
    bad += check(
        "different-piece-point-cannot-supply-return-witness",
        put_all.stage2_witness_return(mismatched_witness_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      certified_detail), None)
    unwitnessed_record = json.loads(json.dumps(witness_record))
    unwitnessed_record["partial_witness_journal"]["paths"][0]["witness_count"] = 0
    bad += check(
        "zero-count-path-cannot-supply-return-witness",
        put_all.stage2_witness_return(unwitnessed_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      certified_detail), None)
    holed_detail = json.loads(json.dumps(certified_detail))
    holed_detail["box"][0]["holes"] = ["2"]
    bad += check(
        "witness-in-certified-hole-is-rejected",
        put_all.stage2_witness_return(witness_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      holed_detail), None)
    unknown_coord_record = json.loads(json.dumps(witness_record))
    unknown_coord_record["partial_witness_journal"]["paths"][0]["ce"]["unowned.input"] = "9"
    bad += check(
        "unowned-witness-coordinate-is-rejected",
        put_all.stage2_witness_return(unknown_coord_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      certified_detail), None)
    missing_piece_coord_record = json.loads(json.dumps(witness_record))
    del missing_piece_coord_record["partial_witness_journal"]["paths"][0]["ce"]["x"]
    bad += check(
        "missing-piece-coordinate-is-rejected",
        put_all.stage2_witness_return(missing_piece_coord_record, 3, "sol:@C@Oracle@F@decimals#480",
                                      certified_detail), None)
    bad += check("forge-absolute-suite-path-is-project-relative",
                 put_all.project_rel_file("/tmp/Project", "/tmp/Project/test/Probe.t.sol"),
                 "test/Probe.t.sol")
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "subject")
        os.makedirs(root)
        flat = os.path.join(root, "flat.sol")
        solast = os.path.join(root, "missing.solast")
        solc_bin = os.path.join(root, "solc")
        with open(flat, "w", encoding="utf-8") as fh:
            fh.write("contract C { function f() public {} }\n")
        with open(solc_bin, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(solc_bin, 0o755)
        subject = Namespace(flat_sol=flat, solast=solast, solc_bin=solc_bin)
        logs = []
        bad += check("missing-solast-is-not-regenerated",
                     put_all.ensure_row_subject_solast(subject, log=logs.append), False)
        bad += check("missing-solast-file-stays-missing", os.path.exists(solast), False)
    bad += check("missing-solast-reports-fail-closed",
                 any("refusing to regenerate" in msg for msg in logs), True)
    old_enumerate = put_all.enumerate_subject_units
    try:
        put_all.enumerate_subject_units = lambda _subject: _GetterEnum(
            [{
                "kind": "public-state-getter",
                "name": "balances",
                "parameter_count": 1,
                "parameter_types": ["address"],
                "return_count": 1,
                "return_types": ["uint256"],
            }])
        getter_rows = put_all.static_subject_concrete_fallback_rows(
            {
                "bucket": "NO-WITNESS-UNKNOWN",
                "unit": "balances",
            }, _GetterSubject())
        bad += check("parameterized-public-getter-fallback-is-structural",
                     (len(getter_rows), getter_rows[0]["stage4_kind"],
                      getter_rows[0]["detail"]["getter_parameter_count"]), (1, "getter-only", 1))
        stale_rows = put_all.static_subject_concrete_fallback_rows(
            {
                "bucket": "NO-WITNESS-UNKNOWN",
                "unit": "staleGetter",
            }, _GetterSubject())
        bad += check("stale-public-getter-fallback-is-rejected", stale_rows, [])
    finally:
        put_all.enumerate_subject_units = old_enumerate
    transfer_root = os.path.join("/home/samson/workspace/VeriPUT/Results/Stress243/subjects",
                                 "ProjectOpenSea__seaport__TransferHelper")
    transfer_flat = os.path.join(transfer_root, "flat.sol")
    transfer_ast = os.path.join("/tmp/veriput_rq1_ast_cache/stress243",
                                "stress243__ProjectOpenSea__seaport__TransferHelper",
                                "flat.sol.solast")
    if os.path.exists(transfer_flat) and os.path.exists(transfer_ast):
        transfer_subject = Namespace(
            subject_id="ProjectOpenSea__seaport__TransferHelper",
            contract="TransferHelper",
            unit="bulkTransfer",
            flat_sol=transfer_flat,
            solast=transfer_ast,
        )
        transfer_rows = put_all.static_subject_concrete_fallback_rows(
            {
                "bucket": "KILLED",
                "unit": "bulkTransfer",
            }, transfer_subject)
        bad += check(
            "transfer-helper-zero-key-fallback-is-concrete-only",
            (len(transfer_rows), transfer_rows[0]["region"], transfer_rows[0]["stage4_kind"]), (1, {
                "conduitKey": [0, 0]
            }, "source-guard-revert-only"))
        with tempfile.TemporaryDirectory() as td:
            changed_flat = os.path.join(td, "flat.sol")
            with open(transfer_flat, "rb") as src, open(changed_flat, "wb") as dst:
                dst.write(src.read() + b"\n")
            changed_subject = Namespace(**{
                **vars(transfer_subject),
                "flat_sol": changed_flat,
            })
            bad += check(
                "transfer-helper-source-drift-rejects-fallback",
                put_all.static_subject_concrete_fallback_rows(
                    {
                        "bucket": "KILLED",
                        "unit": "bulkTransfer",
                    }, changed_subject), [])
    else:
        print("skip - TransferHelper prepared source/AST unavailable")
    pure_subject = Namespace(
        unit="version",
        solast="/tmp/source.solast",
        contract="PhiNFT1155",
    )
    pure_record = {
        "bucket": "KILLED",
        "unit": "version",
        "path_function": "sol:@C@PhiNFT1155@F@version#7634",
    }
    old_callable_facts = put_all.unit_callable_facts
    old_contains_inline_assembly = put_all.unit_contains_inline_assembly
    old_state_dependencies = put_all.unit_state_dependencies
    old_env_dependencies = put_all.unit_env_dependencies
    try:
        put_all.unit_contains_inline_assembly = lambda *_args, **_kwargs: (False, [])
        put_all.unit_callable_facts = lambda *_args, **_kwargs: ({
            "state_mutability": "pure",
            "parameters": [],
            "used_parameters": [],
        }, {
            "declaration_id": 7634
        })
        put_all.unit_state_dependencies = lambda *_args, **_kwargs: ([], {
            "declaration_id": 7634,
        })
        put_all.unit_env_dependencies = lambda *_args, **_kwargs: ([], {
            "declaration_id": 7634,
        })
        pure_rows = put_all.static_pure_unit_concrete_fallback_rows(pure_record, pure_subject)
        bad += check("zero-parameter-pure-unit-has-source-grounded-fallback",
                     (len(pure_rows), pure_rows[0]["detail"]["witness_check"]),
                     (1, "STATIC-PURE-UNIT-NO-COORDINATE"))
        witnessed_timeout = dict(pure_record)
        witnessed_timeout["partial_witness_journal"] = {"witness_count": 1}
        bad += check(
            "witnessed-timeout-keeps-authenticated-fallback-only",
            put_all.static_pure_unit_concrete_fallback_rows(witnessed_timeout, pure_subject), [])

        put_all.unit_callable_facts = lambda *_args, **_kwargs: ({
            "state_mutability": "view",
            "parameters": [],
            "used_parameters": [],
        }, {})
        bad += check("zero-parameter-view-unit-has-no-pure-fallback",
                     put_all.static_pure_unit_concrete_fallback_rows(pure_record, pure_subject), [])

        put_all.unit_callable_facts = lambda *_args, **_kwargs: ({
            "state_mutability": "pure",
            "parameters": [{
                "name": "unused"
            }],
            "used_parameters": [],
        }, {})
        bad += check("unused-parameter-pure-unit-has-no-zero-arg-fallback",
                     put_all.static_pure_unit_concrete_fallback_rows(pure_record, pure_subject), [])

        put_all.unit_callable_facts = lambda *_args, **_kwargs: ({
            "state_mutability": "pure",
            "parameters": [],
            "used_parameters": [],
        }, {})
        put_all.unit_contains_inline_assembly = lambda *_args, **_kwargs: (True, [{
            "nodeType":
            "InlineAssembly",
        }])
        bad += check("inline-assembly-pure-unit-fails-closed",
                     put_all.static_pure_unit_concrete_fallback_rows(pure_record, pure_subject), [])

        put_all.unit_contains_inline_assembly = lambda *_args, **_kwargs: (False, [])
        put_all.unit_state_dependencies = lambda *_args, **_kwargs: (["owner"], {})
        bad += check("state-dependent-unit-has-no-pure-fallback",
                     put_all.static_pure_unit_concrete_fallback_rows(pure_record, pure_subject), [])

        put_all.unit_state_dependencies = lambda *_args, **_kwargs: ([], {})
        put_all.unit_env_dependencies = lambda *_args, **_kwargs: (["block.timestamp"], {})
        bad += check("environment-dependent-unit-has-no-pure-fallback",
                     put_all.static_pure_unit_concrete_fallback_rows(pure_record, pure_subject), [])
    finally:
        put_all.unit_callable_facts = old_callable_facts
        put_all.unit_contains_inline_assembly = old_contains_inline_assembly
        put_all.unit_state_dependencies = old_state_dependencies
        put_all.unit_env_dependencies = old_env_dependencies
    reject_detail = {
        "certification_source": "structural-abi-gate-no-coordinate",
        "box": [{
            "name": "msg.value",
            "lo": "1",
            "hi": str((1 << 256) - 1),
            "holes": [],
        }],
    }
    reject_region, _reject_holes, reject_pins = \
        put_all.parse_certified_detail_region(
            reject_detail, {
                "msg.value": 0,
                "state.immutableScale": 0,
                "block.timestamp": 7,
            })
    bad += check("abi-reject-region-keeps-full-nonzero-value-domain", reject_region["msg.value"],
                 [1, (1 << 256) - 1])
    bad += check("abi-reject-region-drops-unobserved-state-pins", reject_pins, {})
    records = [
        {
            "benchmark": "bench",
            "unit": "target",
            "coords": ["x"],
            "pins": "{'msg.sender': 5}",
            "witnessed": 4,
            "certified": {
                "1": "x in [0, 9]"
            },
            "not_certified": {
                "2": "refuted with concrete witness",
                "3": "STATICALLY INSEPARABLE: differs only on external-call behavior",
                "4": "no generalisable coordinate",
            },
            "not_certified_details": {
                "2": {
                    "enc": 2,
                    "concrete_fallback": True,
                    "witness_check": "SUCCESSFUL",
                    "ce": {
                        "x": "7",
                        "msg.sender": "5",
                        "amount": "11",
                        "return": "99",
                    },
                },
                "3": {
                    "enc": 3,
                    "concrete_fallback": False
                },
                "4": {
                    "enc": 4,
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "path_function": "sol:@C@Target@F@target#100",
                    "ce": {
                        "msg.sender": "5",
                        "block.timestamp": "1234"
                    },
                },
            },
        },
        {
            "benchmark": "bench",
            "unit": "other",
            "witnessed": 9,
            "certified": {},
            "not_certified": {
                "9": "not selected"
            },
        },
        {
            "benchmark": "bench",
            "unit": "legacy",
            "witnessed": 2,
            "certified": {},
            "not_certified": {
                "5": "STATICALLY INSEPARABLE: differs only on external-call behavior",
            },
            "static_extcall_inseparable": True,
        },
        {
            "benchmark": "bench",
            "unit": "timeout",
            "bucket": "KILLED",
            "exit": 124,
            "witnessed": 1,
            "pins": {
                "msg.sender": 1
            },
            "certified": {},
            "not_certified": {},
            "partial_witness_journal": {
                "source_stage":
                "certify-query-started",
                "partial":
                True,
                "claims_decided":
                1,
                "claims_total":
                9,
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
            "unit": "no_coord_journal",
            "bucket": "NO-COORDINATE",
            "witnessed": 1,
            "pins": {
                "msg.value": 0
            },
            "certified": {},
            "not_certified": {},
            "no_coordinate_reason": "every coordinate was pinned by request",
            "partial_witness_journal": {
                "source_stage":
                "no-generalizable-coordinate",
                "source_context":
                "path-enumeration-or-probe",
                "partial":
                False,
                "complete":
                True,
                "claims_decided":
                12,
                "claims_total":
                12,
                "witness_count":
                8,
                "paths": [{
                    "path_id": "7",
                    "path_function": "sol:@C@BadAuction@F@bid#42",
                    "witness_count": 8,
                }],
            },
        },
        {
            "benchmark": "bench",
            "unit": "partial_journal",
            "bucket": "KILLED",
            "exit": 1,
            "witnessed": 2,
            "pins": {
                "msg.sender": 9
            },
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
                "claims_decided":
                1,
                "claims_total":
                4,
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
            "unit": "mixed_timeout",
            "bucket": "KILLED",
            "exit": 124,
            "witnessed": 3,
            "pins": {
                "msg.sender": 2
            },
            "certified": {
                "1": "x in [0, 1]"
            },
            "not_certified": {
                "2": "refuted before timeout"
            },
            "certified_details": {
                "1": {
                    "enc": 1,
                    "piece": 1,
                    "box": [{
                        "name": "x",
                        "lo": "0",
                        "hi": "1"
                    }],
                },
            },
            "not_certified_details": {
                "2": {
                    "enc": 2,
                    "concrete_fallback": False
                },
            },
            "partial_witness_journal": {
                "source_stage":
                "certify-query-started",
                "partial":
                True,
                "claims_decided":
                2,
                "claims_total":
                5,
                "witness_count":
                3,
                "paths": [
                    {
                        "path_id": "1",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "2",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "3",
                        "path_function": "sol:@C@Token@F@approve#972",
                        "witness_count": 1,
                    },
                ],
            },
        },
        {
            "benchmark": "bench",
            "unit": "mixed_no_coord",
            "bucket": "NO-COORDINATE",
            "witnessed": 2,
            "pins": {
                "msg.value": 0
            },
            "certified": {},
            "not_certified": {
                "7": "structured no-coordinate detail already emitted",
            },
            "not_certified_details": {
                "7": {
                    "enc": 7,
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "ce": {
                        "msg.value": "0"
                    },
                },
            },
            "partial_witness_journal": {
                "source_stage":
                "no-generalizable-coordinate",
                "source_context":
                "path-enumeration-or-probe",
                "partial":
                False,
                "complete":
                True,
                "claims_decided":
                12,
                "claims_total":
                12,
                "witness_count":
                2,
                "paths": [
                    {
                        "path_id": "7",
                        "path_function": "sol:@C@BadAuction@F@bid#42",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "8",
                        "path_function": "sol:@C@BadAuction@F@bid#42",
                        "witness_count": 1,
                    },
                ],
            },
        },
        {
            "benchmark": "bench",
            "unit": "certified_no_coord",
            "bucket": "CERTIFIED",
            "witnessed": 2,
            "pins": {
                "msg.value": 0
            },
            "certified": {},
            "not_certified": {},
            "partial_witness_journal": {
                "source_stage":
                "certified-no-coordinate",
                "source_context":
                "path-enumeration-or-probe",
                "partial":
                False,
                "complete":
                True,
                "claims_decided":
                6,
                "claims_total":
                11,
                "witness_count":
                2,
                "paths": [
                    {
                        "path_id": "2",
                        "path_function": "sol:@C@Registry@F@getVault#442",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "3",
                        "path_function": "sol:@C@Registry@F@getVault#442",
                        "witness_count": 1,
                    },
                ],
            },
        },
        {
            "benchmark": "bench",
            "unit": "pin_conflict",
            "coords": ["x"],
            "pins": {
                "msg.sender": 5
            },
            "witnessed": 1,
            "certified": {},
            "not_certified": {
                "1": "conflicting stale witness detail"
            },
            "not_certified_details": {
                "1": {
                    "enc": 1,
                    "concrete_fallback": True,
                    "witness_check": "SUCCESSFUL",
                    "ce": {
                        "x": "3",
                        "msg.sender": "6"
                    },
                },
            },
        },
    ]
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        path = fh.name
        for record in records:
            fh.write(json.dumps(record) + "\n")
    try:
        bad += 0
        with tempfile.TemporaryDirectory() as td:
            old_out = put_all.OUT
            old_forge_std = put_all.FORGE_STD
            try:
                put_all.OUT = os.path.join(td, "out")
                stale_forge_std = os.path.join(td, "missing-forge-std")
                good_forge_std = os.path.join(td, "repo-forge-std")
                os.makedirs(good_forge_std)
                put_all.FORGE_STD = good_forge_std
                flat = os.path.join(td, "Flat.sol")
                with open(flat, "w") as fh:
                    fh.write("contract Flat {}\n")
                project = os.path.join(put_all.OUT, "bench")
                os.makedirs(os.path.join(project, "lib"), exist_ok=True)
                os.symlink(stale_forge_std, os.path.join(project, "lib", "forge-std"))
                bad += check("stage4-existing-broken-forge-std-symlink",
                             put_all.ensure_project("bench", flat), project)
                bad += check("stage4-broken-forge-std-symlink-repaired",
                             os.path.realpath(os.path.join(project, "lib", "forge-std")),
                             good_forge_std)
            finally:
                put_all.OUT = old_out
                put_all.FORGE_STD = old_forge_std
        target = put_all.stage2_path_accounting(path, "bench.target")
        bad += check("selected-record-count", target["records"], 1)
        bad += check("selected-witnessed-count", target["witnessed"], 4)
        bad += check("selected-certified-count", target["certified"], 1)
        bad += check("selected-not-certified-count", target["not_certified"], 3)
        bad += check("structured-concrete-fallback", target["concrete_fallback"], 2)
        fallback_rows = put_all.cleared_concrete_fallback_rows(records[0])
        bad += check("cleared-fallback-point-region",
                     [(r["enc"], r["path_function"], r["region"], r["pins"])
                      for r in fallback_rows], [("2", None, {
                          "x": [7, 7]
                      }, {
                          "amount": 11,
                          "msg.sender": 5
                      }),
                                                ("4", "sol:@C@Target@F@target#100", {}, {
                                                    "block.timestamp": 1234,
                                                    "msg.sender": 5
                                                })])
        conflict_rows = put_all.cleared_concrete_fallback_rows(records[9])
        bad += check("cleared-fallback-conflicting-pin-is-refused", conflict_rows, [])
        timeout_rows = put_all.timeout_concrete_fallback_rows(records[3])
        bad += check(
            "timeout-fallback-uses-partial-witness-path",
            [(r["enc"], r["path_function"], r["region"], r["pins"], r["detail"]["witness_check"])
             for r in timeout_rows], [("15", "sol:@C@Token@F@approve#972", {}, {
                 "msg.sender": 1
             }, "TIMEOUT-WITNESSED")])
        inferred_timeout = dict(records[3])
        inferred_timeout.update({
            "exit": 1,
            "witnessed": None,
            "wall_s": 119.5,
            "run_timeout_s": 120,
            "driver_diagnostic": {
                "tag": "goto-inline-call-type-mismatch",
                "category": "no-cov-report",
            },
        })
        inferred_rows = put_all.timeout_concrete_fallback_rows(inferred_timeout)
        bad += check("timeout-fallback-matches-runner-inferred-timeout",
                     [(r["enc"], r["path_function"]) for r in inferred_rows],
                     [("15", "sol:@C@Token@F@approve#972")])
        timeout_accounting = put_all.stage2_path_accounting(path, "bench.timeout")
        bad += check("timeout-fallback-counts-as-concrete-fallback",
                     timeout_accounting["concrete_fallback"], 1)
        bad += check("timeout-fallback-not-no-verdict", timeout_accounting["no_verdict"], 0)
        no_coord_rows = put_all.no_coordinate_concrete_fallback_rows(records[4])
        bad += check(
            "no-coordinate-complete-journal-fallback",
            [(r["enc"], r["path_function"], r["region"], r["pins"], r["detail"]["witness_check"])
             for r in no_coord_rows], [("7", "sol:@C@BadAuction@F@bid#42", {}, {
                 "msg.value": 0
             }, "COMPLETE-WITNESS-NO-COORDINATE")])
        no_coord_accounting = put_all.stage2_path_accounting(path, "bench.no_coord_journal")
        bad += check("no-coordinate-journal-counts-as-fallback",
                     no_coord_accounting["concrete_fallback"], 1)
        bad += check("no-coordinate-journal-not-no-verdict", no_coord_accounting["no_verdict"], 0)
        partial_journal_rows = put_all.partial_journal_concrete_fallback_rows(records[5])
        bad += check("partial-journal-fallback-skips-measured-paths",
                     [(r["enc"], r["path_function"], r["pins"], r["detail"]["witness_check"])
                      for r in partial_journal_rows], [("42", "sol:@C@Token@F@approve#972", {
                          "msg.sender": 9
                      }, "PARTIAL-JOURNAL-WITNESSED")])
        partial_journal_accounting = put_all.stage2_path_accounting(path, "bench.partial_journal")
        bad += check("partial-journal-counts-as-fallback",
                     (partial_journal_accounting["certified"],
                      partial_journal_accounting["concrete_fallback"],
                      partial_journal_accounting["no_verdict"]), (1, 1, 0))
        mixed_timeout_rows = put_all.timeout_concrete_fallback_rows(records[6])
        bad += check("mixed-timeout-fallback-skips-measured-paths",
                     [(r["enc"], r["path_function"]) for r in mixed_timeout_rows],
                     [("3", "sol:@C@Token@F@approve#972")])
        mixed_timeout_accounting = put_all.stage2_path_accounting(path, "bench.mixed_timeout")
        bad += check(
            "mixed-timeout-fallback-fills-gap",
            (mixed_timeout_accounting["certified"], mixed_timeout_accounting["not_certified"],
             mixed_timeout_accounting["concrete_fallback"], mixed_timeout_accounting["no_verdict"]),
            (1, 1, 1, 0))
        mixed_no_coord_rows = put_all.no_coordinate_concrete_fallback_rows(records[7])
        bad += check("mixed-no-coordinate-fallback-skips-measured-paths",
                     [(r["enc"], r["path_function"]) for r in mixed_no_coord_rows],
                     [("8", "sol:@C@BadAuction@F@bid#42")])
        mixed_no_coord_accounting = put_all.stage2_path_accounting(path, "bench.mixed_no_coord")
        bad += check("mixed-no-coordinate-fallback-fills-gap",
                     (mixed_no_coord_accounting["not_certified"],
                      mixed_no_coord_accounting["concrete_fallback"],
                      mixed_no_coord_accounting["no_verdict"]), (1, 2, 0))
        certified_no_coord_rows = put_all.no_coordinate_concrete_fallback_rows(records[8])
        bad += check("certified-no-coordinate-fallback-rows",
                     [(r["enc"], r["path_function"]) for r in certified_no_coord_rows],
                     [("2", "sol:@C@Registry@F@getVault#442"),
                      ("3", "sol:@C@Registry@F@getVault#442")])
        certified_no_coord_accounting = put_all.stage2_path_accounting(
            path, "bench.certified_no_coord")
        bad += check("certified-no-coordinate-counts-as-fallback",
                     (certified_no_coord_accounting["certified"],
                      certified_no_coord_accounting["concrete_fallback"],
                      certified_no_coord_accounting["no_verdict"]), (0, 2, 0))
        bad += check("structured-method-unsupported", target["method_unsupported"], 1)
        bad += check("selected-no-verdict", target["no_verdict"], 0)

        legacy = put_all.stage2_path_accounting(path, "bench.legacy")
        bad += check("legacy-extcall-attribution", legacy["method_unsupported"], 1)
        bad += check("legacy-detail-not-unknown", legacy["detail_unknown"], 0)
        bad += check("stage4-bench-table-covers-collector", sorted(put_all.BENCHES),
                     sorted(collect.BENCHES))
        args = Namespace(strong_recipe=True,
                         auto_unwind=0,
                         auto_partial_loops=False,
                         lift_unconstrained_calldata=False,
                         propose_r2=False,
                         r2_depth=0,
                         r2_term_budget=1,
                         r2_candidate_budget=1,
                         fuzz_r2_prefilter=False,
                         fuzz_runs=1,
                         fuzz_r2_candidate_budget=1)
        bad += check("stage4-strong-recipe-version", put_all.apply_strong_put_recipe(args),
                     put_all.STRONG_RECIPE_VERSION)
        bad += check("stage4-strong-recipe-auto-unwind", args.auto_unwind, 2)
        bad += check("stage4-strong-recipe-auto-partial-loops", args.auto_partial_loops, True)
        bad += check("stage4-strong-recipe-lift-unconstrained-calldata",
                     args.lift_unconstrained_calldata, True)
        bad += check(
            "stage4-strong-recipe-r2",
            (args.propose_r2, args.r2_depth, args.r2_term_budget, args.r2_candidate_budget),
            (True, put_all.STRONG_PUT_R2_DEPTH, put_all.STRONG_PUT_R2_TERM_BUDGET,
             put_all.STRONG_PUT_R2_CANDIDATE_BUDGET))
        bad += check(
            "stage4-strong-recipe-fuzz-refute",
            (args.fuzz_r2_prefilter, args.fuzz_runs, args.fuzz_r2_candidate_budget),
            (True, put_all.STRONG_PUT_FUZZ_RUNS, put_all.STRONG_PUT_FUZZ_R2_CANDIDATE_BUDGET))
        bad += check("stage4-v14-does-not-require-certified-details",
                     put_all.recipe_requires_certified_details("veriput-strong/14"), False)
        bad += check(
            "stage4-v15-requires-certified-details",
            put_all.recipe_requires_certified_details("veriput-strong/15-relation-establish"), True)
        bad += check(
            "stage4-v16-requires-certified-details",
            put_all.recipe_requires_certified_details(
                "veriput-strong/16-zero-interface-sender-arm"), True)
        bad += check("stage4-v17-requires-certified-details",
                     put_all.recipe_requires_certified_details("veriput-strong/17-split-r2-repair"),
                     True)
        bad += check("stage4-claim-path-id-suffix", put_all.claim_path_id_int("7#nonvacuous"), 7)
        bad += check("stage4-claim-path-id-nonnumeric",
                     put_all.claim_path_id_int("path:7#nonvacuous"), None)
        stage4_args = Namespace(foundry_fixture="/tmp/foundry.json",
                                auto_partial_loops=True,
                                lift_unconstrained_calldata=True,
                                propose_r2=True,
                                r2_depth=1,
                                r2_term_budget=96,
                                r2_candidate_budget=192,
                                fuzz_r2_prefilter=True,
                                fuzz_runs=256,
                                fuzz_r2_candidate_budget=192,
                                forge_timeout=660,
                                esbmc_arg=[
                                    "--path-cov-fixture",
                                    "/tmp/esbmc.json",
                                ])
        cmd = ["driver"]
        put_all.append_stage4_driver_options(cmd, stage4_args,
                                             "sol:@C@DCF@F@setDistributeAddress#1", "normal",
                                             "certified_region", None, None, {"state.owner": 7})
        bad += check("stage4-foundry-fixture-is-driver-option", cmd[:3],
                     ["driver", "--foundry-fixture", "/tmp/foundry.json"])
        bad += check(
            "stage4-esbmc-fixture-stays-esbmc-arg",
            ("--esbmc-arg=--path-cov-fixture" in cmd and "--esbmc-arg=/tmp/esbmc.json" in cmd),
            True)
        bad += check("stage4-foundry-fixture-not-esbmc-arg", "--esbmc-arg=/tmp/foundry.json" in cmd,
                     False)
        bad += check("stage4-driver-options-preserve-proof-switches",
                     ("--propose-r2" in cmd and "--fuzz-r2-prefilter" in cmd and "--pin" in cmd),
                     True)
        fb_cmd = ["driver"]
        put_all.append_stage4_driver_options(fb_cmd, stage4_args,
                                             "sol:@C@DCF@F@setDistributeAddress#1", "normal",
                                             "certified-region-concrete-fallback",
                                             "CERTIFIED-REGION-PUT-REFUSED:build-put-refused", None,
                                             {"state.owner": 7})
        bad += check("stage4-certified-region-fallback-is-concrete-only",
                     ("--concrete-only" in fb_cmd and "--propose-r2" not in fb_cmd
                      and "--fuzz-r2-prefilter" not in fb_cmd), True)
        bad += check("stage4-certified-region-fallback-driver-source",
                     fb_cmd[fb_cmd.index("--concrete-stage2-source") + 1],
                     "certified-region-concrete-fallback")
        no_coord_cmd = ["driver"]
        put_all.append_stage4_driver_options(no_coord_cmd, stage4_args,
                                             "sol:@C@DCF@F@setDistributeAddress#1", "normal",
                                             "no-coordinate-concrete-fallback",
                                             "COMPLETE-WITNESS-NO-COORDINATE", None,
                                             {"state.owner": 7})
        bad += check("stage4-no-coordinate-fallback-driver-source",
                     no_coord_cmd[no_coord_cmd.index("--concrete-stage2-source") + 1],
                     "no-coordinate-concrete-fallback")
        partial_cmd = ["driver"]
        put_all.append_stage4_driver_options(partial_cmd, stage4_args,
                                             "sol:@C@DCF@F@setDistributeAddress#1", "normal",
                                             "partial-journal-concrete-fallback",
                                             "PARTIAL-JOURNAL-WITNESSED", None, {"state.owner": 7})
        bad += check("stage4-partial-journal-fallback-driver-source",
                     partial_cmd[partial_cmd.index("--concrete-stage2-source") + 1],
                     "partial_journal_concrete_fallback")
        normalized_fb = put_all.normalize_stage2_concrete_fallback_record(
            {
                "kind": "put",
                "stage2_source": "stale"
            }, "cleared-concrete-fallback", "SUCCESSFUL")
        bad += check("stage4-cleared-fallback-normalized-as-concrete",
                     (normalized_fb["kind"], normalized_fb["stage2_source"],
                      normalized_fb["stage2_witness_check"]),
                     ("concrete", "cleared_not_certified_fallback", "SUCCESSFUL"))
        missing_cleared = put_all.stage4_missing_record("cleared-concrete-fallback", "SUCCESSFUL")
        bad += check("stage4-missing-cleared-source-normalized",
                     (missing_cleared["kind"], missing_cleared["stage2_source"],
                      missing_cleared["stage2_witness_check"]),
                     ("concrete", "cleared_not_certified_fallback", "SUCCESSFUL"))
        put_all.append_row_esbmc_args(
            cmd, ["--overflow-check", "--path-cov-fixture", "--path-cov-arith-resolve"],
            stage4_args.esbmc_arg)
        bad += check("stage4-row-esbmc-args-are-carried",
                     ("--esbmc-arg=--overflow-check" in cmd
                      and "--esbmc-arg=--path-cov-arith-resolve" in cmd), True)
        bad += check("stage4-row-esbmc-args-are-deduplicated",
                     cmd.count("--esbmc-arg=--path-cov-fixture"), 1)
        with tempfile.NamedTemporaryFile("w", delete=False) as report_fh:
            report_path = report_fh.name
            json.dump(
                {
                    "claims": [
                        {
                            "path_id": "7#nonvacuous",
                            "path_function": "sol:@C@Cb7@F@f#31",
                            "exit_kind": "normal",
                        },
                        {
                            "path_id": "8",
                            "path_function": "sol:@C@Cb7@F@f#31",
                            "exit_kind": "revert",
                        },
                        {
                            "path_id": "9",
                            "path_function": "sol:@C@Cb7@F@f#31",
                            "exit_kind": "undetermined",
                        },
                    ],
                }, report_fh)
        try:
            put_all.EXIT_KIND_CACHE.clear()
            bad += check("stage4-report-exit-kind-suffixed-path-id",
                         put_all.report_exit_kind(report_path, "sol:@C@Cb7@F@f#31", 7), "normal")
            bad += check("stage4-report-exit-kind-plain-path-id",
                         put_all.report_exit_kind(report_path, "sol:@C@Cb7@F@f#31", 8), "revert")
            bad += check("stage4-report-exit-kind-undetermined-normalized",
                         put_all.report_exit_kind(report_path, "sol:@C@Cb7@F@f#31", 9), "unknown")
        finally:
            os.unlink(report_path)
        with tempfile.NamedTemporaryFile() as selected_esbmc:
            selected_mtime = int(os.stat(selected_esbmc.name).st_mtime)
            bad += check("stage4-current-binary-uses-selected-esbmc",
                         put_all.current_binary_identity(selected_esbmc.name)["binaryMtime"],
                         selected_mtime)
        old_run_forge = put_all.run_forge
        old_binary = put_all.current_binary_identity
        try:
            put_all.current_binary_identity = lambda *_args: {
                "head": "test",
                "srcDirty": False,
                "binaryMtime": 123,
            }
            put_all.run_forge = lambda _proj, _timeout: (0,
                                                         json.dumps({
                                                             "Suite": {
                                                                 "test_results": {
                                                                     "test_put_C_target_path1()": {
                                                                         "status": "Success"
                                                                     },
                                                                     "test_ce_anchor_1()": {
                                                                         "status": "Success"
                                                                     },
                                                                     "test_cov_0()": {
                                                                         "status": "Success"
                                                                     },
                                                                     "test_cov_1()": {
                                                                         "status": "Success"
                                                                     },
                                                                     "test_put_C_target_path3()": {
                                                                         "status": "Success"
                                                                     },
                                                                 }
                                                             }
                                                         }), "", False, 0.01)
            tmpdir = tempfile.TemporaryDirectory()
            selfcheck_files = tmpdir.name
            concrete_ok = os.path.join(selfcheck_files, "concrete.t.sol")
            concrete_unsupported = os.path.join(selfcheck_files, "unsupported.t.sol")
            with open(concrete_ok, "w") as fh:
                fh.write("contract T { function test_cov_0() public {} }\n")
            with open(concrete_unsupported, "w") as fh:
                fh.write("""\
contract T {
  function test_cov_1() public {
    // UNSUPPORTED: C.target has an argument type ESBMC cannot yet render
  }
}
""")
            summary = put_all.b_report([
                ("bench", "target", 1, None, 0, {
                    "test": "test_put_C_target_path1",
                    "file": "/tmp/test.t.sol",
                    "ce_anchor": {
                        "status": "embedded",
                        "test": "test_ce_anchor_1",
                    },
                    "storage_layout_available": True,
                    "binary": {
                        "binaryMtime": 123
                    },
                    "stats": {
                        "fuzz_params": 1,
                        "asserts": 1,
                        "verifier_asserts": 1,
                        "exit_kind_asserts": 0,
                        "oracle_classes": ["R1"],
                        "guarded_asserts": 0,
                        "rendered_width": {
                            "x": 2
                        },
                    },
                }, "/tmp/forge-project", {
                    "x": [0, 2]
                }, True, "C"),
                ("bench", "target", 2, None, 0, {
                    "kind": "concrete",
                    "test": "test_cov_0",
                    "file": concrete_ok,
                    "storage_layout_available": False,
                    "storage_layout_error": "forge inspect failed",
                    "binary": {
                        "binaryMtime": 123
                    },
                    "stats": {
                        "fuzz_params": 0,
                        "asserts": 0,
                        "guarded_asserts": 0,
                        "rendered_width": {},
                    },
                }, "/tmp/forge-project", {}, True, "C"),
                ("bench", "target", 4, None, 0, {
                    "kind": "concrete",
                    "test": "test_cov_1",
                    "file": concrete_unsupported,
                    "storage_layout_available": False,
                    "storage_layout_error": "forge inspect failed",
                    "binary": {
                        "binaryMtime": 123
                    },
                    "stats": {
                        "fuzz_params": 0,
                        "asserts": 0,
                        "guarded_asserts": 0,
                        "rendered_width": {},
                    },
                }, "/tmp/forge-project", {}, True, "C"),
                ("bench", "target", 3, None, 0, {
                    "test": "test_put_C_target_path3",
                    "file": "/tmp/zero.t.sol",
                    "binary": {
                        "binaryMtime": 123
                    },
                    "stats": {
                        "fuzz_params": 1,
                        "asserts": 0,
                        "guarded_asserts": 0,
                        "rendered_width": {
                            "x": 2
                        },
                    },
                }, "/tmp/forge-project", {
                    "x": [0, 2]
                }, True, "C"),
            ], 10)
            tmpdir.cleanup()
            bad += check("stage4-b-summary-counts-b",
                         (summary["b"], summary["certified_region_rows"]), (1, 4))
            bad += check("stage4-b-summary-forge-seen",
                         (summary["forge_seen"]["put"]["Success"],
                          summary["forge_seen"]["concrete"]["Success"]), (1, 0))
            bad += check("stage4-b-summary-row-gates", summary["rows"][0]["gates"], {
                "fuzz": True,
                "width": True,
                "assert": True,
                "green": True,
                "corpus": True
            })
            bad += check("stage4-concrete-row-is-not-b",
                         (summary["rows"][1]["kind"], summary["rows"][1]["b"],
                          summary["rows"][1]["valid_reference_test"]), ("concrete", False, False))
            bad += check("stage4-valid-reference-test-split", summary["valid_reference_tests"], {
                "total": 1,
                "put": 1,
                "concrete": 0
            })
            bad += check("stage4-source-counts", summary["stage2_source_counts"],
                         {"certified_region": 4})
            bad += check("stage4-storage-layout-counts", summary["storage_layout_counts"], {
                "available": 1,
                "unavailable": 2,
                "unavailable_with_artifact": 2
            })
            bad += check("stage4-storage-layout-row-field",
                         (summary["rows"][1]["storage_layout_available"],
                          summary["rows"][1]["storage_layout_error"]),
                         (False, "forge inspect failed"))
            bad += check(
                "stage4-row-oracle-class-fields",
                (summary["rows"][0]["oracle_classes"], summary["rows"][0]["verifier_asserts"],
                 summary["rows"][0]["exit_kind_asserts"]), (["R1"], 1, 0))
            bad += check("stage4-unsupported-concrete-refused",
                         (summary["rows"][2]["refused"], summary["rows"][2]["valid_reference_test"],
                          bool(summary["rows"][2]["refusal_reason"])), (True, False, True))
            bad += check("stage4-zero-assert-put-refused",
                         (summary["rows"][3]["refused"], summary["rows"][3]["valid_reference_test"],
                          summary["rows"][3]["gates"]), (True, False, {
                              "fuzz": False,
                              "width": None,
                              "assert": None,
                              "green": None,
                              "corpus": None
                          }))
            bad += check(
                "stage4-certified-region-build-refusal-retries",
                put_all.certified_region_concrete_fallback_reason("certified-region", 2, {
                    "kind": "refusal",
                    "refused": "build-put-refused"
                }), "build-put-refused")
            bad += check(
                "stage4-certified-region-underscore-source-retries",
                put_all.certified_region_concrete_fallback_reason("certified_region", 2, {
                    "kind": "refusal",
                    "refused": "build-put-refused"
                }), "build-put-refused")
            bad += check(
                "stage4-certified-region-zero-assert-retries",
                put_all.certified_region_concrete_fallback_reason("certified-region", 0, {
                    "kind": "put",
                    "stats": {
                        "asserts": 1,
                        "guarded_asserts": 1
                    }
                }), "zero-unconditional-assertions")
            bad += check(
                "stage4-certified-region-vacuous-not-retried",
                put_all.certified_region_concrete_fallback_reason("certified-region", 2, {
                    "kind": "refusal",
                    "refused": "ladder-vacuous"
                }), None)
            normalized = put_all.normalize_certified_region_concrete_fallback_record(
                {
                    "kind": "concrete",
                    "stage2_source": "cleared_not_certified_fallback",
                    "concrete_reason": "Stage-2 fallback",
                    "notes": [],
                }, "build-put-refused")
            bad += check(
                "stage4-certified-region-fallback-normalized",
                (normalized["kind"], normalized["stage2_source"],
                 normalized["certified_region_fallback_reason"], "Stage-2 fallback"
                 in normalized["concrete_reason"]),
                ("concrete", "certified-region-concrete-fallback", "build-put-refused", True))
            missing = put_all.stage4_missing_record("no-coordinate-concrete-fallback")
            missing_summary = put_all.b_report([
                ("bench", "target", 6, None, 1, missing, "/tmp/forge-project", {}, True, "C"),
            ], 10)
            bad += check("stage4-missing-json-kind", missing_summary["rows"][0]["kind"], "concrete")
            bad += check("stage4-missing-json-stage2-source",
                         missing_summary["rows"][0]["stage2_source"],
                         "no-coordinate-concrete-fallback")
            bad += check("stage4-missing-json-source-counts",
                         missing_summary["stage2_source_counts"],
                         {"no-coordinate-concrete-fallback": 1})
            bad += check("stage4-missing-json-not-valid", missing_summary["valid_reference_tests"],
                         {
                             "total": 0,
                             "put": 0,
                             "concrete": 0
                         })
        finally:
            put_all.run_forge = old_run_forge
            put_all.current_binary_identity = old_binary
        with tempfile.TemporaryDirectory() as proj:
            os.makedirs(os.path.join(proj, "test"))
            test_path = os.path.join(proj, "test", "Probe.t.sol")
            with open(test_path, "w") as fh:
                fh.write("""\
contract Probe {
  function setUp() public {
  }
  function helper() public {
  }
  function test_cov_0() public {
  }
  function test_put_Probe_target_path1() public {
  }
}
""")
            old_run_forge = put_all.run_forge
            try:
                put_all.run_forge = lambda _proj, _timeout: (
                    0,
                    json.dumps({
                        "test/Probe.t.sol:Probe": {
                            "test_results": {
                                "setUp()": {
                                    "status": "Failure"
                                },
                                "helper()": {
                                    "status": "Failure"
                                },
                                "test_cov_0()": {
                                    "status": "Failure"
                                },
                                "test_put_Probe_target_path1()": {
                                    "status": "Failure"
                                },
                            }
                        }
                    }), "", False, 0.01)
                put_all.disable_red_replays([proj], 10)
            finally:
                put_all.run_forge = old_run_forge
            with open(test_path) as fh:
                disabled = fh.read()
            bad += check("stage4-red-selfcheck-keeps-setup", "function setUp() public" in disabled,
                         True)
            bad += check("stage4-red-selfcheck-keeps-helper", "function helper() public"
                         in disabled, True)
            bad += check("stage4-red-selfcheck-disables-only-concrete",
                         "function disabled_test_cov_0() public" in disabled, True)
            bad += check("stage4-red-selfcheck-keeps-put-red",
                         "function test_put_Probe_target_path1() public" in disabled, True)
        plain = Namespace(strong_recipe=False,
                          auto_unwind=0,
                          auto_partial_loops=False,
                          lift_unconstrained_calldata=False)
        bad += check("stage4-plain-recipe-unchanged",
                     (put_all.apply_strong_put_recipe(plain), plain.auto_unwind,
                      plain.auto_partial_loops, plain.lift_unconstrained_calldata),
                     (None, 0, False, False))
        cp = subprocess.run([
            sys.executable,
            PUT_ALL,
            "--out-root",
            "/home/samson/workspace/VeriPUT/Results/put-stage4",
            "--cert",
            path,
        ],
                            capture_output=True,
                            text=True)
        bad += check("stage4-refuses-protected-out-root",
                     (cp.returncode != 0 and "--out-root must not be under" in cp.stderr), True)
        return bad
    finally:
        os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())
