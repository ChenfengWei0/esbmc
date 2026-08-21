#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import certify_all  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def main():
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        journal = workdir / "cov-ce-journal.json"
        journal.write_text(json.dumps({
            "kind":
            "solidity-complete-path-ce-journal",
            "version":
            3,
            "partial":
            True,
            "complete":
            False,
            "claims_decided":
            6,
            "claims_total":
            277,
            "witnesses": {
                "sol:@C@C@F@f#1:path:31\t": {
                    "condition": "f:path:31",
                    "path_id": "31",
                    "path_depth": 4,
                    "path_function": "sol:@C@C@F@f#1",
                    "witnesses": [{}, {}, {}],
                },
                "sol:@C@C@F@f#1:path:32\t": {
                    "condition": "f:path:32",
                    "path_depth": 5,
                    "witness_count": "2",
                },
            },
        }))
        since = time.time() - 1
        got = certify_all.result_partial_witness_journal(str(workdir), since)
        bad += check(got is not None, "partial journal is read")
        bad += check(got["source_stage"] is None
                     and got["source_context"] == "path-enumeration-or-probe",
                     f"unattributed journal keeps neutral source context: {got}")
        bad += check(got["path_count"] == 2 and got["witness_count"] == 5,
                     f"path/witness counts are compacted: {got}")
        bad += check(got["claims_decided"] == 6 and got["claims_total"] == 277,
                     f"claim progress is preserved: {got}")
        bad += check([p["path_id"] for p in got["paths"]] == ["31", "32"],
                     f"path ids are preserved or derived: {got['paths']}")
        stale_since = time.time() + 60
        bad += check(
            certify_all.result_partial_witness_journal(str(workdir),
                                                       stale_since) is None,
            "stale journal is ignored")

        os.remove(journal)
        bad += check(certify_all.result_partial_witness_journal(str(workdir)) is None,
                     "missing journal is absent rather than empty data")
        journal.write_text(json.dumps({
            "kind": "solidity-complete-path-ce-journal",
            "partial": True,
            "complete": False,
            "witnesses": {
                "sol:@C@C@F@f#1:path:31#nonvacuous": {
                    "condition": "f:path:31#nonvacuous",
                    "path_id": "31#nonvacuous",
                    "witness_count": 1,
                },
            },
        }))
        cert_journal = certify_all.result_partial_witness_journal(
            str(workdir),
            since,
            progress={"stage": "certify-query-started"})
        bad += check(cert_journal["source_stage"] == "certify-query-started"
                     and cert_journal["source_context"] == "certification-query",
                     f"certification journals are tagged separately: {cert_journal}")
        journal.unlink()

        report = workdir / "enumeration-report.json"
        report.write_text(json.dumps({
            "claims": [
                {
                    "path_id": "7",
                    "path_depth": 3,
                    "path_function": "sol:@C@C@F@f#11",
                    "env": {
                        "msg.sender": "0x2a",
                        "msg.value": "0",
                    },
                    "inputs": {
                        "amount": "0x10",
                    },
                    "entry_storage": {
                        "owner": "0x2a",
                    },
                    "return_value_known": True,
                    "return_value": "1",
                },
                {
                    "path_id": "26",
                    "path_depth": 5,
                    "path_function": "sol:@C@C@F@f#11",
                    "inputs": {
                        "amount": "0x20",
                    },
                },
            ],
        }))
        bad += check(
            certify_all.result_enumeration_report(
                str(workdir),
                str(workdir / "old-host" / "enumeration-report.json"),
                since) == str(report),
            "missing imported enumeration report falls back to workdir report")
        recovered = certify_all.complete_journal_concrete_fallback_details(
            {
                "complete": True,
                "claims_decided": 1,
                "claims_total": 1,
                "paths": [
                    {
                        "path_id": "7",
                        "path_depth": 3,
                        "path_function": "sol:@C@C@F@f#11",
                        "witness_count": 1,
                    },
                ],
            },
            str(report))
        detail = recovered["7"]
        bad += check(detail["concrete_fallback"] is True
                     and detail["witness_check"] ==
                     "COMPLETE-WITNESS-NO-COORDINATE",
                     f"complete journal becomes concrete fallback: {detail}")
        bad += check(detail["ce"]["msg.sender"] == "42"
                     and detail["ce"]["amount"] == "16"
                     and detail["ce"]["state.owner"] == "42"
                     and detail["ce"]["return"] == "1",
                     f"enumeration counterexample is normalized: {detail}")
        suffixed = certify_all.complete_journal_concrete_fallback_details(
            {
                "complete": True,
                "paths": [
                    {
                        "path_id": "26#exit0",
                        "path_function": "sol:@C@C@F@f#11",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "26#nonvacuous",
                        "path_function": "sol:@C@C@F@f#11",
                        "witness_count": 1,
                    },
                ],
            },
            str(report))
        bad += check(suffixed.get("26", {}).get("ce", {}).get("amount") == "32",
                     f"certification-query path-id suffixes map to base path: "
                     f"{suffixed}")
        ambiguous = certify_all.complete_journal_concrete_fallback_details(
            {
                "complete": True,
                "paths": [
                    {
                        "path_id": "7",
                        "path_function": "sol:@C@C@F@f#11",
                        "witness_count": 1,
                    },
                    {
                        "path_id": "7",
                        "path_function": "sol:@C@C@F@f#12",
                        "witness_count": 1,
                    },
                ],
            },
            str(report))
        bad += check(ambiguous == {},
                     f"overload path spaces are not collapsed by enc: {ambiguous}")
        mixed_not = {}
        mixed_cert = {
            "7#2": {
                "enc": 7,
                "piece": 2,
                "box": [{"name": "amount", "lo": "16", "hi": "16"}],
            },
        }
        mixed_added = certify_all.merge_complete_journal_concrete_fallbacks(
            mixed_not, mixed_cert, {
                "7": detail,
                "8": {
                    **detail,
                    "enc": 8,
                    "ce": {"amount": "17"},
                },
            })
        bad += check("7" not in mixed_not and "8" in mixed_not,
                     f"complete journal fills only unmeasured encs: {mixed_not}")
        bad += check(mixed_added == {"8": mixed_not["8"]},
                     f"complete journal reports newly added fallbacks: "
                     f"{mixed_added}")
        report.unlink()

        result = workdir / "generalise-result.json"
        result.write_text(json.dumps({
            "pins": {
                "msg.sender": "7",
                "block.timestamp": "9",
            },
            "not_certified": [
                {
                    "enc": 4,
                    "reason": "no generalisable coordinate",
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "ce": {
                        "msg.sender": "7",
                    },
                },
            ],
            "enumeration_source": {
                "salvage": {
                    "from": "cov-ce-journal.json",
                    "claims_decided": 6,
                    "claims_total": 277,
                    "path_count": 1,
                    "witness_count": 8,
                }
            }
        }))
        got_pins = certify_all.result_pins(str(workdir), since)
        bad += check(got_pins == {
            "msg.sender": "7",
            "block.timestamp": "9",
        }, f"machine-readable pins are preserved: {got_pins}")
        got_not_certified = certify_all.result_not_certified_details(
            str(workdir), since)
        row = {
            "not_certified": {},
            "not_certified_details": got_not_certified,
        }
        certify_all.merge_not_certified_details(row)
        bad += check(row["not_certified"] == {
            "4": "no generalisable coordinate",
        }, f"machine-readable not-certified rows feed Stage 4: {row}")
        got_salvage = certify_all.result_enumeration_salvage(str(workdir), since)
        bad += check(got_salvage["path_count"] == 1
                     and got_salvage["witness_count"] == 8,
                     f"enumeration salvage metadata is preserved: {got_salvage}")
        bad += check(certify_all.result_enumeration_salvage(str(workdir),
                                                            stale_since) is None,
                     "stale enumeration salvage metadata is ignored")
        result.unlink()
        sidecar = workdir / "enumeration-salvage.json"
        sidecar.write_text(json.dumps({
            "from": "cov-ce-journal.json",
            "claims_decided": 89,
            "claims_total": 116,
            "path_count": 5,
            "witness_count": 40,
        }))
        sidecar_salvage = certify_all.result_enumeration_salvage(str(workdir),
                                                                 since)
        bad += check(sidecar_salvage["path_count"] == 5
                     and sidecar_salvage["witness_count"] == 40,
                     f"sidecar salvage metadata is preserved after timeout: "
                     f"{sidecar_salvage}")

        initial = workdir / "initial-run"
        retry = workdir / "retry-run"
        initial.mkdir()
        retry.mkdir()
        (initial / "generalise-result.json").write_text(json.dumps({
            "pins": {
                "msg.sender": "7",
            },
            "certified": [
                {
                    "enc": 2,
                    "piece": 1,
                    "verdict": "CERTIFIED",
                    "box": [
                        {"name": "amount", "lo": "1", "hi": "9"},
                    ],
                    "ce": {
                        "amount": "3",
                    },
                },
            ],
            "not_certified": [
                {
                    "enc": 3,
                    "verdict": "NOT_CERTIFIED",
                    "reason": "no generalisable coordinate",
                    "concrete_fallback": True,
                    "witness_check": "COMPLETE-WITNESS-NO-COORDINATE",
                    "ce": {
                        "msg.sender": "7",
                    },
                },
            ],
        }))
        retry_since = time.time()
        runs = [
            ("initial", str(initial), since),
            ("retry", str(retry), retry_since),
        ]
        merged_cert = certify_all.merge_detail_sidecars(
            certify_all.result_certified_details, runs)
        merged_not = certify_all.merge_detail_sidecars(
            certify_all.result_not_certified_details, runs)
        retry_lost_output = certify_all.merge_parsed_driver_outputs([
            {
                "label": "initial",
                "out": "[enumerate] 2 witnessed path(s)\n"
                       "  enc=2: amount in [1, 9]\n",
            },
            {
                "label": "retry",
                "out": "[run] TIMEOUT after 60s\n",
            },
        ])
        retry_lost_output.update({
            "certified_details": merged_cert,
            "not_certified_details": merged_not,
        })
        certify_all.merge_certified_details(retry_lost_output)
        certify_all.merge_not_certified_details(retry_lost_output)
        # WHERE A SURVIVING CERTIFIED ROW LIVES. `merge_certified_details` was
        # made deliberately inert (A08: promoting sidecar detail into
        # `certified` before `bucket()` changes the classification), so the row
        # the initial run certified survives in the DETAIL sidecar and in
        # `observed_certified`, not in `certified`. This check asked for the
        # promoted spelling and had been failing invisibly ever since.
        bad += check(
            "2" in retry_lost_output["certified_details"]
            and retry_lost_output["observed_certified"].get("2") == "amount in [1, 9]",
            f"retry timeout does not erase prior certified rows: {retry_lost_output}")
        bad += check(
            retry_lost_output["not_certified_details"]["3"]
            ["concrete_fallback"] is True
            and retry_lost_output["not_certified"]["3"] ==
            "no generalisable coordinate",
            f"retry timeout preserves concrete fallback rows for Stage 4: "
            f"{retry_lost_output}")
        bad += check(certify_all.first_available_sidecar(
            certify_all.result_pins, runs) == {"msg.sender": "7"},
                     "retry without result falls back to prior machine pins")

        conflict = {
            "certified": {},
            "not_certified": {
                "5": "old failed retry text",
            },
            "certified_details": {
                "5": {
                    "enc": 5,
                    "box": [
                        {"name": "x", "lo": "0", "hi": "1"},
                    ],
                },
            },
        }
        before = json.dumps(conflict, sort_keys=True)
        certify_all.merge_certified_details(conflict)
        # INERT BY DECISION, not by accident: the same A08 review that stopped
        # the promotion is the reason a same-enc `not_certified` row must be
        # left standing here. Assert the decision that is in force.
        bad += check(
            json.dumps(conflict, sort_keys=True) == before and "5" not in conflict["certified"]
            and conflict["not_certified"]["5"] == "old failed retry text",
            f"merge_certified_details does not promote sidecar detail into the "
            f"classification: {conflict}")

        progress = workdir / "generalise-progress.json"
        progress.write_text(json.dumps({
            "schema": "path-generalise-progress/1",
            "stage": "certify-query-started",
            "enc": 31,
            "history": [{"stage": "coordinates-selected"}],
        }))
        progress_row = certify_all.result_generalise_progress(str(workdir),
                                                              since)
        bad += check(progress_row["stage"] == "certify-query-started"
                     and progress_row["enc"] == 31,
                     f"generalise progress sidecar is preserved: {progress_row}")
        bad += check(certify_all.result_generalise_progress(str(workdir),
                                                            stale_since) is None,
                     "stale generalise progress sidecar is ignored")

        enum_report = workdir / "enumeration-report.json"
        enum_report.write_text(json.dumps({"claims": []}))
        bad += check(certify_all.result_enumeration_report(str(workdir), None,
                                                           since)
                     == str(enum_report),
                     "direct enumeration report snapshot is preserved")
        bad += check(certify_all.result_enumeration_report(str(workdir), None,
                                                           stale_since) is None,
                     "stale enumeration report snapshot is ignored")
        imported_report = workdir / "imported-cov-report.json"
        imported_report.write_text(json.dumps({"claims": []}))
        bad += check(certify_all.result_enumeration_report(
            str(workdir), str(imported_report), stale_since)
                     == str(imported_report),
                     "imported enumeration report remains authoritative")

        report = workdir / "cov-report.json"
        report.write_text(json.dumps({
            "claims": [
                {
                    "condition": "transfer:path:1",
                    "u_reason": "named-obstacle",
                    "u_reason_detail":
                    "unit still calls another UNIT's own body unexpanded "
                    "(sol:@C@C@F@balanceOf#7); that body carries the ABI value gate",
                },
                {
                    "condition": "transfer:path:2",
                    "u_reason": "named-obstacle",
                    "u_reason_detail":
                    "unit still calls another UNIT's own body unexpanded "
                    "(sol:@C@C@F@balanceOf#7); that body carries the ABI value gate",
                },
                {
                    "condition": "approve:path:3",
                    "u_reason": "named-obstacle",
                    "u_reason_detail": "different unit",
                },
            ],
        }))
        obstacles = certify_all.result_empty_witness_obstacles(
            str(workdir), "transfer", since)
        obstacle_details = obstacles["named_obstacle"]["details"]
        bad += check(obstacles["named_obstacle"]["total"] == 2,
                     f"named obstacle total is filtered by unit: {obstacles}")
        bad += check(list(obstacle_details.values()) == [2],
                     f"named obstacle detail counts are compacted: {obstacles}")
        bad += check(certify_all.result_empty_witness_obstacles(
            str(workdir), "transfer", stale_since) is None,
                     "stale cov-report obstacle metadata is ignored")
        report.unlink()

        parsed = certify_all.parse_driver(
            "[coords] STATE PINNED (all 2 paths' counterexamples agree): "
            "state._owner==1\n"
            "[coords] STATE NOT PINNED because a complete-path decision can "
            "establish it from another coordinate: state.owner\n"
            "[coords] ESBMC query pins OMIT immutable/constant coordinate(s): "
            "state.asset, state.poolId. They remain semantic pins in the "
            "reported slice\n"
            "[coords] mapping dependency policy solc-reference-closure/3: "
            "state._owner dependency distance 3\n"
            "[coords] mapping READ slot access priority: "
            "state.credit[msg.sender] slot-access distance 0\n"
            "[coords] bytesN mapping key(s) fixed to the witnessed "
            "counterexample slice, not treated as fuzz coordinates: k->0\n"
            "[coords] --pin-agreed-state derived NOTHING: no state coordinate "
            "survived to this point. No pin was added\n"
            "[coords] NO mapping slot was added. This is a statement about "
            "the source and the budget, not about the tool\n"
            "[coords] NO GENERALISABLE COORDINATE — every coordinate was "
            "pinned by request\n")
        bad += check(parsed["coords"] == [] and parsed["coords_line"] is None,
                     f"mapping dependency prose is not parsed as coords: {parsed}")

        diagnostic = certify_all.result_driver_diagnostic(
            "--path-cov-probe: unit 'sol:@C@C@F@f#1' added 370 "
            "exit-latched claim(s) for 10 branch arm(s) at 37 physical "
            "exit(s); complete-path denominator remains 37\n"
            "[run] TIMEOUT after 60s: esbmc ... --path-cov-probe\n")
        bad += check(
            diagnostic and diagnostic["tag"] ==
            "path-coverage-probe-claim-explosion",
            f"probe claim explosion timeout is diagnosed: {diagnostic}")
        bad += check(diagnostic["probe_claims"] == 370
                     and diagnostic["branch_arms"] == 10
                     and diagnostic["physical_exits"] == 37
                     and diagnostic["complete_path_denominator"] == 37,
                     f"probe product dimensions are recorded: {diagnostic}")

        recursive = certify_all.result_driver_diagnostic(
            "[enumerate] no witnessed path for this unit, \u26d4 and it is NOT "
            "a result: target call closure reaches direct self-recursive "
            "function/helper wrapper(s): SafeMath.div/2, SafeMath.sub/2. "
            "This preflight starts no ESBMC process and proves nothing\n")
        bad += check(
            recursive and recursive["tag"] ==
            "recursive-helper-preflight-refused",
            f"recursive helper preflight is diagnosed: {recursive}")
        bad += check(recursive["helpers"] == ["SafeMath.div/2", "SafeMath.sub/2"],
                     f"recursive helper names are retained: {recursive}")

        overloaded = certify_all.result_driver_diagnostic(
            "[enumerate] 'f' names 2 overloads; their path-id spaces are "
            "independent and must not be merged. Re-run with --path-function "
            "set to one of:\n"
            "  sol:@C@C@F@f#11\n"
            "  sol:@C@C@F@f#12\n")
        overloaded_rec = {
            "driver_diagnostic": overloaded,
            "witnessed": None,
            "certified": {},
            "no_coordinate_reason": None,
            "driver_refusal": None,
            "empty_witness_verdict": None,
        }
        bad += check(
            overloaded
            and overloaded["tag"] == "overloaded-unit-path-function-required",
            f"overloaded unit refusal is diagnosed: {overloaded}")
        bad += check(overloaded["path_functions"] == [
            "sol:@C@C@F@f#11",
            "sol:@C@C@F@f#12",
        ], f"overload path functions are retained: {overloaded}")
        bad += check(certify_all.bucket(overloaded_rec, 1, "")
                     == "DRIVER-REFUSED",
                     "overload refusal is not filed as no-witness unknown")

        truncated_log = (
            "WARNING: Coverage may be UNDER-REPORTED: 1 loop(s) hit the unwind "
            "bound while --no-unwinding-assertions was active. Loops truncated:\n"
            "WARNING:   loop 19 at file string.c line 92 column 3 function strlen\n"
            "--solidity-path-coverage: 0 of 4 instrumented path claim(s) "
            "reached the solver across 2 unit(s)\n"
            "ERROR: --solidity-path-coverage: INTERNAL DEFECT -- NOT ONE of "
            "the 4 instrumented path claim(s) reached the solver.\n"
            "[run] EXIT -6\n")
        truncated = certify_all.result_driver_diagnostic(truncated_log)
        rec = {
            "driver_diagnostic": truncated,
            "witnessed": None,
            "certified": {},
            "no_coordinate_reason": None,
            "driver_refusal": None,
            "empty_witness_verdict": None,
        }
        bad += check(
            truncated and truncated["tag"] == "unwind-truncation",
            f"unwind truncation beats generic no-claims diagnostics: {truncated}")
        bad += check(truncated["loops"] == [
            "loop 19 at file string.c line 92 column 3 function strlen",
        ], f"truncated loop names are retained: {truncated}")
        # THE BOUND AND THE BASELINE BOTH MOVED. The retry raises the named
        # loop to 512 (a 16 left the same symbolic exponent truncated again),
        # and it must first make path coverage's implicit global unwind of 4
        # explicit, because a numeric --unwindset only takes effect after that.
        # This expectation still asked for the pre-change `19:16` with no
        # baseline and had been failing invisibly.
        bad += check(certify_all.unwindset_retry_args(truncated, []) == [
            "--unwind", "4", "--unwindset", "19:512",
        ], f"named truncation becomes one unwindset retry: "
           f"{certify_all.unwindset_retry_args(truncated, [])}")
        bad += check(certify_all.unwindset_retry_args(
            truncated, ["--unwindset", "19:256"]) == [],
                     "explicit caller unwindset is not duplicated")
        bad += check(certify_all.bucket(rec, -6, truncated_log)
                     == "UNWIND-TRUNCATED",
                     "unwind truncation is not filed as no-witness unknown")

        no_report = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "Starting Bounded Model Checking\n"
            "ERROR: function call: argument \"c:string.c@4751@F@memset@s\" "
            "type mismatch: got array, expected pointer\n"
            "[run] EXIT -6\n")
        bad += check(
            no_report
            and no_report["tag"] == "goto-inline-call-type-mismatch",
            f"ESBMC call type mismatch is diagnosed: {no_report}")
        bad += check(no_report["category"] == "no-cov-report",
                     f"ESBMC no-report category is retained: {no_report}")
        bad += check(no_report["exit"] == -6
                     and "type mismatch" in no_report["error"],
                     f"ESBMC no-report details are retained: {no_report}")

        tuple_report = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "ERROR: expecting struct type for tuple RHS, got symbol\n"
            "[run] EXIT 6\n")
        bad += check(
            tuple_report
            and tuple_report["tag"] == "frontend-tuple-rhs-symbol",
            f"tuple RHS frontend crash is diagnosed: {tuple_report}")

        tuple_ast = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "esbmc: /tmp/smt_tuple_node_ast.h:72: const tuple_node_smt_ast* "
            "to_tuple_node_ast(smt_astt): Assertion `ta != nullptr && "
            "\"Tuple AST mismatch\"' failed.\n"
            "[run] EXIT -6\n")
        bad += check(
            tuple_ast
            and tuple_ast["tag"] == "solver-tuple-ast-mismatch",
            f"solver tuple AST mismatch is diagnosed: {tuple_ast}")
        bad += check("Tuple AST mismatch" in tuple_ast.get("error", ""),
                     f"tuple AST assertion is retained: {tuple_ast}")

        member_assert = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "esbmc: /tmp/irep2_expr.h:2987: member2t::member2t"
            "(const type2tc&, const expr2tc&, const irep_idt&): Assertion "
            "`source->type->type_id == type2t::struct_id || "
            "source->type->type_id == type2t::union_id' failed.\n"
            "[run] EXIT -6\n")
        bad += check(
            member_assert
            and member_assert["tag"] == "irep2-member-source-not-struct",
            f"member source type assertion is diagnosed: {member_assert}")

        namespace_assert = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "esbmc: /tmp/namespace.cpp:60: const typet& "
            "namespacet::follow(const typet&) const: Assertion `symbol' "
            "failed.\n"
            "[run] EXIT -6\n")
        bad += check(
            namespace_assert
            and namespace_assert["tag"] ==
            "namespace-follow-missing-symbol-type",
            f"namespace missing symbol assertion is diagnosed: {namespace_assert}")

        selector_mismatch = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "function call: argument "
            "`sol:@C@VaultAdapter@F@setSlopes_checkAccess@_selector#1836' "
            "type mismatch: got unsigned int, expected struct\n"
            "[run] EXIT -6\n")
        bad += check(
            selector_mismatch
            and selector_mismatch["tag"] ==
            "frontend-selector-call-type-mismatch",
            f"selector call type mismatch is diagnosed: {selector_mismatch}")
        bad += check("_selector" in selector_mismatch.get("error", ""),
                     f"selector mismatch details are retained: {selector_mismatch}")

        unsupported_type_name = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "Got type-name typeString=function IVaultAdmin.setAuthorizer"
            "(contract IAuthorizer). Unsupported type-name type\n"
            "[run] EXIT -6\n")
        bad += check(
            unsupported_type_name
            and unsupported_type_name["tag"] ==
            "frontend-unsupported-type-name-type",
            f"unsupported type-name is diagnosed: {unsupported_type_name}")
        bad += check("Unsupported type-name" in
                     unsupported_type_name.get("error", ""),
                     f"unsupported type-name details are retained: "
                     f"{unsupported_type_name}")

        bad_alloc = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "terminate called after throwing an instance of 'std::bad_alloc'\n"
            "  what():  std::bad_alloc\n"
            "[run] EXIT -6\n")
        bad += check(
            bad_alloc
            and bad_alloc["tag"] == "path-coverage-bad-alloc-no-report",
            f"bad_alloc no-report is diagnosed: {bad_alloc}")

        signal_partial = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "[Coverage]\n"
            "Report Completeness: PARTIAL \u2014 terminated by signal before "
            "verification concluded\n"
            "Complete Paths : 4\n"
            "Claims Decided : 1 of 4\n"
            "Path Status: F 0 (partial: LOWER BOUND, no cov-report.json was "
            "written, and this line carries no counterexample payload. The "
            "payload for these paths is in cov-ce-journal.json when "
            "--cov-report-json was given)\n"
            "ERROR: Terminated\n")
        bad += check(
            signal_partial
            and signal_partial["tag"] == "path-coverage-partial-signal-no-report",
            f"partial signal no-report is diagnosed: {signal_partial}")
        bad += check(signal_partial["claims_decided"] == 1
                     and signal_partial["claims_total"] == 4,
                     f"partial signal claim progress is retained: {signal_partial}")

        bad_alloc_partial = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "ERROR: the per-claim solve loop did not finish (std::bad_alloc "
            "\u2014 the process ran out of memory during the per-claim solve). "
            "Writing a PARTIAL report with the 1 of 8 claim(s) decided so far, "
            "rather than discarding them. It is marked partial in the JSON\n"
            "[run] EXIT 2\n")
        bad += check(
            bad_alloc_partial
            and bad_alloc_partial["tag"] ==
            "path-coverage-per-claim-solve-died-no-report",
            f"per-claim bad_alloc no-report is diagnosed: {bad_alloc_partial}")
        bad += check("std::bad_alloc" in bad_alloc_partial["partial_reason"]
                     and bad_alloc_partial["claims_total"] == 8,
                     f"per-claim partial reason is retained: {bad_alloc_partial}")

        untokened_u = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "ERROR: --solidity-path-coverage: INTERNAL DEFECT \u2014 1 path(s) "
            "are reported U with NO reason token: "
            "sol:@C@P16_Mapping@F@put#31:path:7. The claim this pass makes is "
            "that every uncovered path carries a named reason\n"
            "[run] EXIT 1\n")
        bad += check(
            untokened_u
            and untokened_u["tag"] == "path-coverage-untokened-u-no-report",
            f"untokened U no-report is diagnosed: {untokened_u}")
        bad += check(untokened_u["untokened_u_paths"] == 1
                     and "sol:@C@P16_Mapping" in
                     untokened_u["untokened_u_examples"][0],
                     f"untokened U details are retained: {untokened_u}")

        refined = certify_all.refine_driver_diagnostic_with_sidecars(
            {"tag": "esbmc-no-cov-report",
             "reason": "ESBMC exited before producing cov-report.json"},
            {
                "source_stage": "started",
                "claims_decided": 34,
                "claims_total": 4015,
                "path_count": 1,
                "witness_count": 1,
            },
            {"stage": "started"})
        bad += check(
            refined["tag"] == "path-coverage-partial-journal-no-report",
            f"partial journal no-report row is refined: {refined}")
        bad += check(refined["category"] == "no-cov-report"
                     and refined["claims_total"] == 4015,
                     f"partial journal dimensions are retained: {refined}")
        journal_only = certify_all.refine_driver_diagnostic_with_sidecars(
            None,
            {
                "source_stage": "enumeration-started",
                "claims_decided": 2,
                "claims_total": 5,
                "path_count": 2,
                "witness_count": 3,
            },
            {"stage": "enumeration-started"})
        bad += check(
            journal_only["tag"] == "path-coverage-partial-journal-only",
            f"partial journal without stdout diagnostic is retained: "
            f"{journal_only}")
        bad += check(journal_only["claims_decided"] == 2
                     and journal_only["witness_count"] == 3,
                     f"journal-only dimensions are retained: {journal_only}")

        explicit = certify_all.refine_driver_diagnostic_with_sidecars(
            tuple_report, {"path_count": 1, "witness_count": 1}, {})
        bad += check(
            explicit["tag"] == "frontend-tuple-rhs-symbol",
            f"explicit frontend diagnostics are not overwritten: {explicit}")

        oom = certify_all.result_driver_diagnostic(
            "--path-cov-outer-box: unit 'sol:@C@C@F@f#1'\n"
            "ERROR: Out of memory\n"
            "\n"
            "ERROR: SMT solver failed\n"
            "[run] EXIT 2\n")
        bad += check(oom and oom["tag"] == "outer-box-solver-oom",
                     f"outer-box solver OOM is diagnosed: {oom}")
        thin = certify_all.thin_outer_box_retry_cmd([
            "driver.py", "--workdir", "/tmp/old", "--probes", "8",
            "--refine-rounds", "2", "--probe-ladder",
            "--probe-ladder-budget", "4",
        ], "/tmp/new")
        bad += check(thin[thin.index("--workdir") + 1] == "/tmp/new",
                     f"thin retry changes workdir: {thin}")
        bad += check(thin[thin.index("--probes") + 1] == "2"
                     and thin[thin.index("--refine-rounds") + 1] == "1"
                     and thin[thin.index("--probe-ladder-budget") + 1] == "1",
                     f"thin retry downsamples outer-box flags: {thin}")
        no_probe = certify_all.probe_goal_cap_retry_cmd([
            "driver.py", "--workdir", "/tmp/old", "--probe-witnesses", "8",
            "--probe-ladder", "--probe-ladder-budget", "4",
            "--probes", "8",
        ], "/tmp/no-probe")
        bad += check(no_probe[no_probe.index("--workdir") + 1] ==
                     "/tmp/no-probe",
                     f"probe-cap retry changes workdir: {no_probe}")
        bad += check("--probe-ladder" not in no_probe
                     and "--probe-ladder-budget" not in no_probe,
                     f"probe-cap retry removes ladder flags: {no_probe}")
        bad += check(no_probe[no_probe.index("--probe-witnesses") + 1] == "0",
                     f"probe-cap retry disables witness probe fallback: {no_probe}")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
