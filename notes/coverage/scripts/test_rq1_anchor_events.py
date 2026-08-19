#!/usr/bin/env python3
"""Focused tests for declaration-bound RQ1 event anchors."""

# pylint: disable=missing-function-docstring

from copy import deepcopy

from rq1_anchor_events import event_declaration, inject_event_oracles, render_event_oracles


def document() -> dict:
    return {
        "nodeType": "SourceUnit", "id": 100, "nodes": [{
            "nodeType": "ContractDefinition", "id": 90, "name": "C", "nodes": [{
                "nodeType": "EventDefinition", "id": 42, "name": "Updated",
                "anonymous": False, "parameters": {"parameters": [
                    {"nodeType": "VariableDeclaration", "id": 1, "name": "who",
                     "indexed": True, "typeName": {"nodeType": "ElementaryTypeName",
                                                      "name": "address"}},
                    {"nodeType": "VariableDeclaration", "id": 2, "name": "amount",
                     "indexed": False, "typeName": {"nodeType": "ElementaryTypeName",
                                                       "name": "uint"}},
                ]}}, {
                "nodeType": "EventDefinition", "id": 43, "name": "Updated",
                "anonymous": False, "parameters": {"parameters": [
                    {"nodeType": "VariableDeclaration", "id": 3, "name": "who",
                     "indexed": True, "typeName": {"nodeType": "ElementaryTypeName",
                                                      "name": "address"}},
                    {"nodeType": "VariableDeclaration", "id": 4, "name": "other",
                     "indexed": False, "typeName": {"nodeType": "ElementaryTypeName",
                                                       "name": "address"}},
                ]}}, {
                "nodeType": "FunctionDefinition", "id": 9, "name": "f",
                "parameters": {"parameters": [
                    {"nodeType": "VariableDeclaration", "id": 7, "name": "who",
                     "stateVariable": False},
                    {"nodeType": "VariableDeclaration", "id": 8, "name": "amount",
                     "stateVariable": False},
                ]}, "body": {
                    "nodeType": "Block", "statements": [{
                        "nodeType": "EmitStatement", "eventCall": {
                            "expression": {"referencedDeclaration": 42}, "arguments": [
                                {"nodeType": "Identifier", "name": "who",
                                 "referencedDeclaration": 7},
                                {"nodeType": "Identifier", "name": "amount",
                                 "referencedDeclaration": 8},
                            ]}
                    }]}
                }
            ]}
        ]
    }


def claim() -> dict:
    return {
        "path_function": "sol:@C@C@F@f#9", "path_id": "7",
        "exit_kind": "normal", "events": ["sol:@C@C@F@Updated#42"],
        "inputs": {"who": "5", "amount": "9"}, "env": {},
        "entry_storage": {}, "final_state": {},
    }


def must_fail(callable_, message: str) -> None:
    try:
        callable_()
    except ValueError:
        return
    raise AssertionError(message)


def main() -> int:
    declaration = event_declaration(document(), 42)
    assert declaration["signature"] == "Updated(address,uint256)"
    assert event_declaration(document(), 43)["signature"] == "Updated(address,address)"

    oracles = render_event_oracles(document(), claim())
    expected = oracles[0]["expected"]
    assert expected["declaration_id"] == 42
    assert expected["topics"] == [
        'keccak256("Updated(address,uint256)")',
        "bytes32(uint256(uint160(address(uint160(5)))))",
    ]
    assert expected["data"] == "abi.encode(uint256(9))"

    source = """import {Test} from "forge-std/Test.sol";
contract T {
  C c0;
  // claim: sol:@C@C@F@f#9:path:7
  function test_cov_0() public {
    c0.f(address(5), 9);
  }
}
"""
    rewritten = inject_event_oracles(source, "test_cov_0", "f", oracles)
    assert rewritten.count("vm.recordLogs();") == 1
    assert rewritten.count("vm.getRecordedLogs();") == 1
    assert 'import {Vm} from "forge-std/Vm.sol";' in rewritten
    assert 'keccak256("Updated(address,uint256)")' in rewritten
    assert "abi.encode(uint256(9))" in rewritten

    overloaded = deepcopy(claim())
    overloaded["events"] = ["sol:@C@C@F@Updated#43"]
    must_fail(lambda: render_event_oracles(document(), overloaded),
              "same-name overload without an exact emit binding was accepted")

    reverting = deepcopy(claim())
    reverting["exit_kind"] = "revert"
    must_fail(lambda: render_event_oracles(document(), reverting),
              "reverting event claim was accepted as a persisted log")
    print("ok - declaration-bound signature, topics, data, overload, and rollback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
