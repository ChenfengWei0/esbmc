#!/usr/bin/env python3
"""Focused tests for the compound state/event materializer."""

from rq1_anchor_compound import materialize_compound_oracles, oracle_kinds


def main():
    event = {
        "nodeType": "EventDefinition",
        "id": 10,
        "name": "E",
        "anonymous": False,
        "parameters": {"parameters": [
            {"id": 11, "name": "x", "indexed": True,
             "typeName": {"nodeType": "ElementaryTypeName", "name": "uint"}},
            {"id": 12, "name": "y", "indexed": False,
             "typeName": {"nodeType": "ElementaryTypeName", "name": "uint"}},
        ]},
    }
    function = {
        "nodeType": "FunctionDefinition", "id": 20, "name": "f",
        "parameters": {"parameters": [{"nodeType": "VariableDeclaration",
                                         "id": 30, "name": "x"}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "EmitStatement",
            "eventCall": {"expression": {"referencedDeclaration": 10},
                           "arguments": [
                               {"nodeType": "Identifier", "name": "x",
                                "referencedDeclaration": 30},
                               {"nodeType": "Literal", "kind": "number", "value": "7"},
                           ]},
        }]},
    }
    document = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "T", "id": 1,
        "nodes": [event, function],
    }]}
    claim = {
        "path_function": "sol:@C@T@F@f#20", "path_id": "1",
        "events": ["sol:@C@T@F@E#10"], "exit_kind": "normal",
        "inputs": {"x": "5"},
    }
    source = ('import "forge-std/Test.sol";\n'
              'contract T { function test_cov_0() public {\n'
              '    c0.f();\n  }\n}\n')
    rewritten, oracles, error = materialize_compound_oracles(
        source, "test_cov_0", "f", {"packed": {"after": "1"}},
        ({"packed": (2, 20, 1)}, {}), document, claim)
    assert error is None, error
    assert oracle_kinds(oracles) == ("storage-slot-post-state", "event-log")
    assert "vm.recordLogs();" in rewritten
    assert "vm.getRecordedLogs()" in rewritten
    assert rewritten.index("vm.recordLogs();") < rewritten.index("c0.f();")
    assert rewritten.index("c0.f();") < rewritten.index("_veriput_state_packed_0")
    assert "_veriputLogs[0].topics[0]" in rewritten
    assert "abi.encode(uint256(7))" in rewritten


if __name__ == "__main__":
    main()
