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
        bad += check(certify_all.result_enumeration_report(
            str(workdir), "/tmp/imported-cov-report.json", stale_since)
                     == "/tmp/imported-cov-report.json",
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
            "[coords] mapping dependency policy solc-reference-closure/3: "
            "state._owner dependency distance 3\n"
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

        no_report = certify_all.result_driver_diagnostic(
            "[enumerate] ESBMC produced no cov-report.json. Its output was:\n"
            "Starting Bounded Model Checking\n"
            "ERROR: function call: argument \"c:string.c@4751@F@memset@s\" "
            "type mismatch: got array, expected pointer\n"
            "[run] EXIT -6\n")
        bad += check(no_report and no_report["tag"] == "esbmc-no-cov-report",
                     f"ESBMC no-report failure is diagnosed: {no_report}")
        bad += check(no_report["exit"] == -6
                     and "type mismatch" in no_report["error"],
                     f"ESBMC no-report details are retained: {no_report}")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
