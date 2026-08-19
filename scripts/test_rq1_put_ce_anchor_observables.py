#!/usr/bin/env python3
"""Regression for fail-closed CE-anchor observable preservation."""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))

import rq1_put_ce_anchor_backfill as backfill  # noqa: E402


def oracle(kind, observed, expected, assertion):
    return {
        "class": "R0" if kind in ("return-value", "call-status", "normal-exit")
        else "concrete-value",
        "kind": kind,
        "observed": observed,
        "expected": expected,
        "assertion": assertion,
        "provenance": "stage2-witness",
        "target_receiver": "c0",
    }


def check(condition, message):
    if condition:
        print("ok - " + message)
        return 0
    print("not ok - " + message)
    return 1


def main():
    bad = 0
    fingerprint = "a" * 64
    path_function = "sol:@C@Probe@F@f#9"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        emit = root / "emit"
        emit.mkdir()
        put_json = root / "put.json"
        put_json.write_text("{}\n", encoding="utf-8")
        solast = root / "flat.sol.solast"
        solast.write_text(json.dumps({"nodes": [{
            "nodeType": "EventDefinition", "id": 42, "name": "Updated",
            "parameters": {"parameters": [
                {"typeDescriptions": {"typeString": "address"}},
                {"typeDescriptions": {"typeString": "uint256"}},
            ]},
        }, {
            "nodeType": "EventDefinition", "id": 43, "name": "Updated",
            "parameters": {"parameters": [
                {"typeDescriptions": {"typeString": "address"}},
                {"typeDescriptions": {"typeString": "address"}},
            ]},
        }]}), encoding="utf-8")
        result_json = root / "result.json"
        result_json.write_text(json.dumps({
            "verifier_input_identity": {"inputs": [{"solast": str(solast)}]},
        }), encoding="utf-8")
        entry = {
            "identity": ["case", path_function, "f", "1", "None"],
            "subject_dir": str(root),
            "result_json": str(result_json),
            "basis": {"put_json": str(put_json)},
        }
        def source_with(body):
            return (f"// claim: {path_function}:path:1\n"
                    f"// witness-fingerprint-sha256: {fingerprint}\n"
                    "function test_cov_0() public {\n" + body + "}\n")

        def bind(oracles, source_text=None, **claim_fields):
            claim = {
                "path_function": path_function,
                "path_id": "1",
                "exit_kind": "normal",
                "inputs": {"x": "7"},
                "env": {},
                "entry_storage": {},
                "foundry_testcase_fingerprint_sha256": fingerprint,
                **claim_fields,
            }
            (emit / "cov-report.json").write_text(
                json.dumps({"claims": [claim]}), encoding="utf-8")
            return backfill._report_binding(
                entry, {"ce": {"x": "7"}}, oracles,
                source_text or source_with("  bool _veriput_concrete_completed = false;\n"
                                           "  c0.f(7);\n"
                                           "  _veriput_concrete_completed = true;\n"
                                           "  assertTrue(_veriput_concrete_completed, \"done\");\n"),
                "test_cov_0")

        scalar = oracle("return-value", "ret", "9", "assertEq(ret, 9);")
        scalar["solidity_type"] = "uint256"
        result, error = bind(
            [scalar],
            source_text=source_with("  uint256 ret = c0.f(7);\n"
                                    "  assertEq(ret, 9);\n"),
            return_value="9")
        bad += check(result is not None and error is None,
                     "scalar return remains report-bound")

        tuple_oracles = []
        for index, (observed, expected, solidity_type) in enumerate(
                (("a", "uint8(1)", "uint8"), ("b", "uint16(2)", "uint16"))):
            item = oracle("return-value", observed, expected,
                          f"assertEq({observed}, {expected});")
            item.update(return_index=index, return_arity=2,
                        solidity_type=solidity_type)
            tuple_oracles.append(item)
        result, error = bind(
            tuple_oracles,
            source_text=source_with("  (uint8 a, uint16 b) = c0.f(7);\n"
                                    "  assertEq(a, uint8(1));\n"
                                    "  assertEq(b, uint16(2));\n"),
            return_value="(1,2)")
        bad += check(result is not None and error is None,
                     "tuple return remains report-bound")

        event_assertions = (
            "assertEq(_veriputLogs.length, 1);"
            "assertEq(_veriputLogs[0].emitter, address(c0));"
            "assertEq(_veriputLogs[0].topics.length, 1);"
            "assertEq(_veriputLogs[0].topics[0], keccak256(\"Updated(address,uint256)\"));"
            "assertEq(_veriputLogs[0].data, abi.encode(uint256(9)));")
        for item, fields, source_text in (
                ({**oracle("storage-slot-post-state", "observed", "uint256(7)",
                           "assertEq(observed, uint256(7));"),
                  "storage_variable": "value", "storage_slot": 0,
                  "storage_offset_bytes": 0, "storage_width_bytes": 32},
                 {"final_state": {"value": "7"}},
                 source_with("  c0.f(7);\n"
                             "  uint256 observed = uint256(vm.load(address(c0), bytes32(uint256(0))));\n"
                             "  assertEq(observed, uint256(7));\n")),
                ({**oracle("event-log", "_veriputLogs", {
                    "log_count": 1,
                    "event_index": 0,
                    "emitter": "address(c0)",
                    "topics": ['keccak256("Updated(address,uint256)")'],
                    "data": "abi.encode(uint256(9))",
                }, event_assertions),
                  "provenance": "source-grounded"},
                 {"events": ["sol:@C@Probe@F@Updated#42"]},
                 source_with("  vm.recordLogs();\n"
                             "  c0.f(7);\n"
                             "  Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();\n"
                             f"  {event_assertions}\n")),
                (oracle("call-status", "ok", True,
                        "assertTrue(ok, \"target call succeeds\");"),
                 {"call_status": True},
                 source_with("  (bool ok, bytes memory data) = address(c0).call(\n"
                             "    abi.encodeWithSignature(\"f(uint256)\", 7)\n"
                             "  );\n"
                             "  data;\n"
                             "  assertTrue(ok, \"target call succeeds\");\n"))):
            result, error = bind([item], source_text=source_text, **fields)
            bad += check(result is not None and error is None,
                         item["kind"] + " remains report-bound")

        revert_oracle = {
            "class": "R0",
            "kind": "revert",
            "source": "expectRevert",
            "target_receiver": "c0",
        }
        claim = {
            "path_function": path_function,
            "path_id": "1",
            "exit_kind": "revert",
            "inputs": {"x": "7"},
            "env": {},
            "entry_storage": {},
            "foundry_testcase_fingerprint_sha256": fingerprint,
        }
        (emit / "cov-report.json").write_text(
            json.dumps({"claims": [claim]}), encoding="utf-8")
        revert_source = (f"// claim: {path_function}:path:1\n"
                         f"// witness-fingerprint-sha256: {fingerprint}\n"
                         "function test_cov_0() public {\n"
                         "  vm.expectRevert();\n"
                         "  c0.f(7);\n"
                         "}\n")
        result, error = backfill._report_binding(
            entry, {"ce": {"x": "7"}}, [revert_oracle], revert_source,
            "test_cov_0")
        bad += check(result is not None and error is None,
                     "revert remains report-bound")
        helper_revert_source = revert_source.replace(
            "  c0.f(7);", "  helper.f();\n  c0.f(7);")
        result, error = backfill._report_binding(
            entry, {"ce": {"x": "7"}}, [revert_oracle], helper_revert_source,
            "test_cov_0")
        bad += check(result is None and "expectRevert" in str(error),
                     "revert cannot bind through an intervening helper call")

        completion = oracle(
            "normal-exit", "_veriput_concrete_completed", True,
            "assertTrue(_veriput_concrete_completed);")
        result, error = bind([completion], return_value="9")
        bad += check(result is None and "return" in str(error),
                     "known return cannot degrade to completion")
        result, error = bind([completion], events=[{"topic0": "topic9"}])
        bad += check(result is None and "event" in str(error),
                     "known event cannot degrade to completion")
        state_only = {**oracle(
            "storage-slot-post-state", "observed", "uint256(7)",
            "assertEq(observed, uint256(7));"),
                      "storage_variable": "a", "storage_slot": 0,
                      "storage_offset_bytes": 0, "storage_width_bytes": 32}
        result, error = bind([state_only], return_value="9",
                             final_state={"a": "7"})
        bad += check(result is None and "return" in str(error),
                     "a state oracle cannot silently omit the retained return")
        result, error = bind([state_only], final_state={"a": "7", "b": "2"})
        bad += check(result is None and "coverage" in str(error),
                     "one state oracle cannot omit another changed final-state value")
        failed_status = oracle("call-status", "ok", False, "assertFalse(ok);")
        result, error = bind([failed_status])
        bad += check(result is None and "normal exit" in str(error),
                     "normal exit cannot carry a failed target call status")
        declared_event = {**oracle(
            "event-log", "_veriputLogs", {
                "log_count": 1, "event_index": 0, "emitter": "address(c0)",
                "topics": ['keccak256("Updated(address,uint256)")'],
                "data": "abi.encode(uint256(9))",
            }, event_assertions),
                          "provenance": "source-grounded"}
        result, error = bind(
            [declared_event],
            source_text=source_with("  vm.recordLogs();\n"
                                    "  c0.f(7);\n"
                                    "  Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();\n"
                                    f"  {event_assertions}\n"),
            events=["sol:@C@Probe@F@Updated#42"])
        bad += check(result is not None and error is None,
                     "declaration-id events normalize to exact event-log topics")
        wrong_overload = {**declared_event, "expected": {
            **declared_event["expected"],
            "topics": ['keccak256("Updated(address,address)")'],
        }}
        result, error = bind(
            [wrong_overload],
            source_text=source_with("  vm.recordLogs();\n"
                                    "  c0.f(7);\n"
                                    "  Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();\n"
                                    f"  {event_assertions}\n"),
            events=["sol:@C@Probe@F@Updated#42"])
        bad += check(result is None and "event declaration" in str(error),
                     "same-name overloaded event with wrong signature is rejected")

    return bad


if __name__ == "__main__":
    raise SystemExit(main())
