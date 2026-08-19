#!/usr/bin/env python3
"""Recover exact event observables from retained RQ1 coverage evidence.

This module is intentionally independent of the anchor backfill driver.  It
only writes candidates below ``--scratch-root`` and never invokes ESBMC.  An
event is renderable only when its declaration id, ABI signature, emit site,
and every topic/data value are unambiguous in the retained AST and claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

# This is a fail-closed evidence parser; branching is preferable to hiding
# unsupported AST shapes behind generic coercions.
# pylint: disable=too-many-branches,too-many-locals,too-many-return-statements
# pylint: disable=too-many-statements,missing-function-docstring,redefined-outer-name

Identity = tuple[str, str, str, str, str]


def load_solast(path: Path) -> dict[str, Any]:
    """Load either raw compact JSON or solc's human-prefixed AST output."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("nodeType") == "SourceUnit":
            return value
    return {}


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _declarations(document: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        node["id"]: node for node in _walk(document)
        if isinstance(node.get("id"), int) and not isinstance(node.get("id"), bool)
    }


def _array_length(node: dict[str, Any]) -> str:
    length = node.get("length")
    if length is None:
        return ""
    if length.get("nodeType") == "Literal" and length.get("value") is not None:
        return str(length["value"])
    raise ValueError("event array length is not a literal")


def canonical_abi_type(type_name: dict[str, Any], declarations: dict[int, dict[str, Any]],
                       active: frozenset[int] = frozenset()) -> str:
    """Return the canonical ABI type using declaration identities, not names."""
    kind = type_name.get("nodeType")
    if kind == "ElementaryTypeName":
        name = str(type_name.get("name") or "")
        if name == "uint":
            return "uint256"
        if name == "int":
            return "int256"
        if name == "address":
            return "address"
        if re.fullmatch(r"(?:u?int(?:[0-9]+)|bool|string|bytes(?:[0-9]+)?)", name):
            return name
        raise ValueError(f"unsupported event elementary type {name!r}")
    if kind == "ArrayTypeName":
        return (canonical_abi_type(type_name.get("baseType") or {}, declarations, active)
                + "[" + _array_length(type_name) + "]")
    if kind == "FunctionTypeName":
        return "function"
    if kind != "UserDefinedTypeName":
        raise ValueError(f"unsupported event type node {kind!r}")
    declaration_id = type_name.get("referencedDeclaration")
    declaration = declarations.get(declaration_id)
    if declaration is None or declaration_id in active:
        raise ValueError("event user-defined type has no acyclic declaration binding")
    declaration_kind = declaration.get("nodeType")
    if declaration_kind == "ContractDefinition":
        return "address"
    if declaration_kind == "EnumDefinition":
        members = len(declaration.get("members") or [])
        if members < 1:
            raise ValueError("event enum has no members")
        bits = max(8, 8 * math.ceil(max(1, math.ceil(math.log2(members))) / 8))
        return f"uint{bits}"
    if declaration_kind == "StructDefinition":
        nested_active = active | {declaration_id}
        components = [
            canonical_abi_type(member.get("typeName") or {}, declarations, nested_active)
            for member in declaration.get("members") or []
        ]
        return "(" + ",".join(components) + ")"
    raise ValueError(f"unsupported event declaration kind {declaration_kind!r}")


def event_declaration(document: dict[str, Any], declaration_id: int) -> dict[str, Any]:
    declarations = _declarations(document)
    node = declarations.get(declaration_id)
    if not isinstance(node, dict) or node.get("nodeType") != "EventDefinition":
        raise ValueError(f"#{declaration_id} is not an EventDefinition")
    parameters = []
    for parameter in (node.get("parameters") or {}).get("parameters") or []:
        parameters.append({
            "declaration_id": parameter.get("id"),
            "name": str(parameter.get("name") or ""),
            "indexed": parameter.get("indexed") is True,
            "abi_type": canonical_abi_type(parameter.get("typeName") or {}, declarations),
        })
    signature = f"{node.get('name')}({','.join(item['abi_type'] for item in parameters)})"
    return {
        "declaration_id": declaration_id,
        "name": str(node.get("name") or ""),
        "anonymous": node.get("anonymous") is True,
        "signature": signature,
        "parameters": parameters,
    }


def declaration_id(label: str) -> int:
    match = re.fullmatch(r"sol:.*#([0-9]+)", str(label))
    if match is None:
        raise ValueError("event claim lacks a terminal declaration id")
    return int(match.group(1))


def _qualified_label(label: str) -> tuple[str, str, int]:
    match = re.fullmatch(r"sol:@C@([^@#]+)@F@([^#]+)#([0-9]+)", str(label))
    if match is None:
        raise ValueError("Solidity declaration label is not fully qualified")
    return match.group(1), match.group(2), int(match.group(3))


def _qualified_node(document: dict[str, Any], label: str, node_kind: str) -> dict[str, Any]:
    contract_name, declaration_name, node_id = _qualified_label(label)
    matches = []
    for contract in document.get("nodes") or []:
        if (contract.get("nodeType") != "ContractDefinition"
                or contract.get("name") != contract_name):
            continue
        matches.extend(node for node in contract.get("nodes") or []
                       if node.get("nodeType") == node_kind and node.get("id") == node_id
                       and node.get("name") == declaration_name)
    if len(matches) != 1:
        id_matches = [
            node for node in _walk(document)
            if node.get("nodeType") == node_kind and node.get("id") == node_id
            and node.get("name") == declaration_name
        ]
        if len(id_matches) == 1:
            return id_matches[0]
        raise ValueError(
            f"qualified {node_kind} {label!r} has {len(matches)} exact AST bindings")
    return matches[0]


def _state_key_name(name: str) -> str:
    return re.sub(r"\$[0-9]+(?=\.|\[|$)", "", str(name or ""))


def _state_scalar(claim: dict[str, Any], name: str, *, prefer_entry: bool = False) -> Any:
    def lookup(values: dict[str, Any]) -> Any:
        if name in values:
            return values[name]
        matches = [
            value for key, value in values.items()
            if _state_key_name(key) == name or _state_key_name(key).lstrip("_") == name.lstrip("_")
        ]
        return matches[0] if len(matches) == 1 else None

    entry_state = claim.get("entry_storage") or {}
    final_state = claim.get("final_state") or {}
    if prefer_entry:
        return lookup(entry_state)
    entry_value = lookup(entry_state)
    final_value = lookup(final_state)
    if entry_value is not None and final_value is not None and entry_value != final_value:
        raise ValueError(
            f"event state argument {name!r} changes during the call; emit-time value is ambiguous")
    return entry_value if entry_value is not None else final_value


def _claim_scalar(claim: dict[str, Any], name: str, *, prefer_entry: bool = False) -> Any:
    aliases = {
        "msg.sender": ("env", "msg.sender"),
        "msg.value": ("env", "msg.value"),
        "tx.origin": ("env", "tx.origin"),
        "block.timestamp": ("env", "block.timestamp"),
        "block.number": ("env", "block.number"),
    }
    if name in aliases:
        group, key = aliases[name]
        return (claim.get(group) or {}).get(key)
    for group in ("inputs", "env"):
        values = claim.get(group) or {}
        if name in values:
            return values[name]
    return _state_scalar(claim, name, prefer_entry=prefer_entry)


def _expression_name(node: dict[str, Any]) -> str | None:
    kind = node.get("nodeType")
    if kind == "Identifier":
        return str(node.get("name") or "")
    if kind == "MemberAccess":
        base = _expression_name(node.get("expression") or {})
        return f"{base}.{node.get('memberName')}" if base else None
    return None


def _expression_value(node: dict[str, Any],
                      claim: dict[str, Any],
                      declarations: dict[int, dict[str, Any]],
                      input_ids: set[int],
                      aliases: dict[Any, tuple[dict[str, Any], bool]] | None = None) -> Any:
    kind = node.get("nodeType")
    if kind == "Literal":
        literal_kind = node.get("kind")
        if literal_kind == "bool":
            return str(node.get("value")).lower()
        if literal_kind in ("number", "string", "hexString"):
            return node.get("value")
        raise ValueError(f"unsupported event literal {literal_kind!r}")
    name = _expression_name(node)
    if kind == "Identifier" and name:
        referenced = node.get("referencedDeclaration")
        aliases = aliases or {}
        alias = aliases.get(referenced)
        if alias is None and not isinstance(referenced, int):
            alias = aliases.get(name)
        if alias is not None:
            alias_node, prefer_entry = alias
            if prefer_entry:
                alias_name = _expression_name(alias_node)
                if alias_name:
                    return _claim_scalar(claim, alias_name, prefer_entry=True)
            return _expression_value(alias_node, claim, declarations, input_ids, aliases)
        declaration = declarations.get(referenced)
        if declaration is None or declaration.get("nodeType") != "VariableDeclaration":
            raise ValueError(f"event identifier {name!r} has no variable declaration binding")
        if declaration.get("stateVariable") is not True and referenced not in input_ids:
            raise ValueError(f"event identifier {name!r} is a local/computed value")
        value = _claim_scalar(claim, name)
        if value is None:
            raise ValueError(f"event argument {name!r} is absent from the retained claim")
        return value
    if name:
        value = _claim_scalar(claim, name)
        if value is None:
            raise ValueError(f"event argument {name!r} is absent from the retained claim")
        return value
    if kind == "FunctionCall" and node.get("kind") == "typeConversion":
        arguments = node.get("arguments") or []
        if len(arguments) != 1:
            raise ValueError("event type conversion does not have one argument")
        return _expression_value(arguments[0], claim, declarations, input_ids, aliases)
    if kind == "UnaryOperation" and node.get("operator") == "-":
        value = _expression_value(node.get("subExpression") or {}, claim,
                                  declarations, input_ids, aliases)
        return -int(str(value), 0)
    raise ValueError(f"unsupported event argument expression {kind!r}")


def _integer(value: Any) -> int:
    text = str(value).strip()
    if text.lower() == "true":
        return 1
    if text.lower() == "false":
        return 0
    return int(text, 0)


def solidity_literal(abi_type: str, value: Any) -> str:
    if abi_type == "address":
        return f"address(uint160({_integer(value)}))"
    if abi_type == "bool":
        integer = _integer(value)
        if integer not in (0, 1):
            raise ValueError("boolean event value is not 0 or 1")
        return "true" if integer else "false"
    if re.fullmatch(r"uint(?:[0-9]+)", abi_type):
        return f"{abi_type}({_integer(value)})"
    if re.fullmatch(r"int(?:[0-9]+)", abi_type):
        return f"{abi_type}({_integer(value)})"
    if abi_type == "string":
        return json.dumps(str(value))
    if abi_type == "bytes":
        text = str(value)
        if re.fullmatch(r"0x[0-9a-fA-F]*", text) and len(text) % 2 == 0:
            return f'hex"{text[2:]}"'
        raise ValueError("dynamic bytes event value is not retained as exact hex")
    match = re.fullmatch(r"bytes([0-9]+)", abi_type)
    if match:
        width = int(match.group(1))
        text = str(value)
        if re.fullmatch(r"0x[0-9a-fA-F]+", text):
            digits = text[2:]
        else:
            digits = f"{_integer(value):x}"
        if len(digits) > width * 2:
            raise ValueError(f"{abi_type} event value exceeds its width")
        return f"{abi_type}(hex\"{digits.zfill(width * 2)}\")"
    raise ValueError(f"event value type {abi_type!r} is not safely renderable")


def indexed_topic(abi_type: str, literal: str) -> str:
    if abi_type == "address":
        return f"bytes32(uint256(uint160({literal})))"
    if abi_type == "bool" or abi_type.startswith("uint"):
        return f"bytes32(uint256({literal}))"
    if abi_type.startswith("int"):
        return f"bytes32(uint256(int256({literal})))"
    if re.fullmatch(r"bytes[0-9]+", abi_type):
        return f"bytes32({literal})"
    # Indexed dynamic and aggregate values use Solidity's special in-place
    # encoding.  abi.encode is not equivalent, so refuse rather than guess.
    raise ValueError(f"indexed event type {abi_type!r} needs unsupported in-place encoding")


def _event_emit_sites(document: dict[str, Any], function_id: int,
                      event_id: int) -> list[dict[str, Any]]:
    function = next((node for node in _walk(document)
                     if node.get("nodeType") == "FunctionDefinition"
                     and node.get("id") == function_id), None)
    if function is None:
        raise ValueError(f"target function declaration #{function_id} is absent from AST")
    return [
        node for node in _walk(function.get("body") or {})
        if node.get("nodeType") == "EmitStatement"
        and ((node.get("eventCall") or {}).get("expression") or {}).get(
            "referencedDeclaration") == event_id
    ]


def _ordered_parameters(function: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        parameter for parameter in (function.get("parameters") or {}).get("parameters") or []
        if isinstance(parameter, dict)
    ]


def _input_ids(function: dict[str, Any]) -> set[int]:
    return {parameter.get("id") for parameter in _ordered_parameters(function)
            if isinstance(parameter.get("id"), int)}


def _writes_declaration(node: Any, declaration_id: int) -> bool:
    if isinstance(node, dict):
        if node.get("nodeType") == "Assignment":
            lhs = node.get("leftHandSide") or {}
            if (isinstance(lhs, dict) and lhs.get("nodeType") == "Identifier"
                    and lhs.get("referencedDeclaration") == declaration_id):
                return True
        return any(_writes_declaration(value, declaration_id) for value in node.values())
    if isinstance(node, list):
        return any(_writes_declaration(value, declaration_id) for value in node)
    return False


def _entry_state_alias(node: dict[str, Any], declarations: dict[int, dict[str, Any]],
                       previous: list[dict[str, Any]]) -> bool:
    if node.get("nodeType") != "Identifier":
        return False
    declaration_id = node.get("referencedDeclaration")
    declaration = declarations.get(declaration_id)
    if not isinstance(declaration, dict) or declaration.get("stateVariable") is not True:
        return False
    return not any(_writes_declaration(statement, declaration_id) for statement in previous)


def _local_aliases_before(statements: list[dict[str, Any]], stop: dict[str, Any],
                          declarations: dict[int, dict[str, Any]]
                          ) -> dict[Any, tuple[dict[str, Any], bool]]:
    aliases: dict[Any, tuple[dict[str, Any], bool]] = {}
    previous: list[dict[str, Any]] = []
    for statement in statements:
        if statement is stop:
            break
        if statement.get("nodeType") == "VariableDeclarationStatement":
            declared = statement.get("declarations") or []
            initial = statement.get("initialValue")
            if len(declared) == 1 and isinstance(declared[0], dict) and isinstance(initial, dict):
                prefer_entry = _entry_state_alias(initial, declarations, previous)
                declaration = declared[0]
                if isinstance(declaration.get("id"), int):
                    aliases[declaration["id"]] = (initial, prefer_entry)
                if declaration.get("name"):
                    aliases[str(declaration["name"])] = (initial, prefer_entry)
        previous.append(statement)
    return aliases


def _event_contexts(function: dict[str, Any], event_id: int,
                    declarations: dict[int, dict[str, Any]]
                    ) -> list[tuple[dict[str, Any], set[int],
                                    dict[Any, tuple[dict[str, Any], bool]]]]:
    contexts = []
    statements = (function.get("body") or {}).get("statements") or []
    for statement in statements:
        direct_sites = [
            node for node in _walk(statement)
            if node.get("nodeType") == "EmitStatement"
            and ((node.get("eventCall") or {}).get("expression") or {}).get(
                "referencedDeclaration") == event_id
        ]
        for site in direct_sites:
            contexts.append((site, _input_ids(function),
                             _local_aliases_before(statements, statement, declarations)))
        if statement.get("nodeType") != "ExpressionStatement":
            continue
        call = statement.get("expression") or {}
        if call.get("nodeType") != "FunctionCall" or call.get("kind") != "functionCall":
            continue
        callee_id = (call.get("expression") or {}).get("referencedDeclaration")
        callee = declarations.get(callee_id)
        if not isinstance(callee, dict) or callee.get("nodeType") != "FunctionDefinition":
            continue
        callee_statements = (callee.get("body") or {}).get("statements") or []
        callee_sites = [
            node for stmt in callee_statements for node in _walk(stmt)
            if node.get("nodeType") == "EmitStatement"
            and ((node.get("eventCall") or {}).get("expression") or {}).get(
                "referencedDeclaration") == event_id
        ]
        if not callee_sites:
            continue
        params = _ordered_parameters(callee)
        args = call.get("arguments") or []
        if len(params) != len(args):
            continue
        aliases: dict[Any, tuple[dict[str, Any], bool]] = {}
        for parameter, argument in zip(params, args):
            if isinstance(parameter.get("id"), int):
                aliases[parameter["id"]] = (argument, False)
            if parameter.get("name"):
                aliases[str(parameter["name"])] = (argument, False)
        for site in callee_sites:
            aliases_for_site = dict(aliases)
            aliases_for_site.update(_local_aliases_before(callee_statements, site, declarations))
            contexts.append((site, _input_ids(function), aliases_for_site))
    return contexts


def render_event_oracles(document: dict[str, Any], claim: dict[str, Any],
                         target_receiver: str = "c0") -> list[dict[str, Any]]:
    events = claim.get("events")
    if not isinstance(events, list) or not events:
        raise ValueError("retained claim has no event sequence")
    if claim.get("exit_kind") != "normal":
        raise ValueError("reverting claims cannot authenticate persisted EVM logs")
    function_label = str(claim.get("path_function") or "")
    function = _qualified_node(document, function_label, "FunctionDefinition")
    function_id = declaration_id(function_label)
    input_ids = _input_ids(function)
    declarations = _declarations(document)
    event_ids = [declaration_id(item) for item in events]
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("repeated event declarations need path-specific emit-site identity")
    rendered = []
    for index, (event_label, event_id) in enumerate(zip(events, event_ids)):
        _qualified_node(document, event_label, "EventDefinition")
        declaration = event_declaration(document, event_id)
        sites = _event_contexts(function, event_id, declarations)
        if len(sites) != 1:
            raise ValueError(
                f"event declaration #{event_id} has {len(sites)} target-function emit sites")
        site, site_input_ids, aliases = sites[0]
        arguments = (site.get("eventCall") or {}).get("arguments") or []
        parameters = declaration["parameters"]
        if len(arguments) != len(parameters):
            raise ValueError("event emit arguments differ from its declaration arity")
        values = []
        for parameter, argument in zip(parameters, arguments):
            value = _expression_value(argument, claim, declarations, site_input_ids, aliases)
            values.append(solidity_literal(parameter["abi_type"], value))
        topics = []
        if not declaration["anonymous"]:
            topics.append(f'keccak256("{declaration["signature"]}")')
        topics.extend(indexed_topic(parameter["abi_type"], literal)
                      for parameter, literal in zip(parameters, values)
                      if parameter["indexed"])
        data_values = [literal for parameter, literal in zip(parameters, values)
                       if not parameter["indexed"]]
        data = "abi.encode(" + ", ".join(data_values) + ")"
        expected = {
            "log_count": len(events),
            "event_index": index,
            "declaration_id": event_id,
            "signature": declaration["signature"],
            "emitter": f"address({target_receiver})",
            "topics": topics,
            "data": data,
        }
        assertions = [
            f"assertEq(_veriputLogs.length, {len(events)});",
            f"assertEq(_veriputLogs[{index}].emitter, address({target_receiver}));",
            f"assertEq(_veriputLogs[{index}].topics.length, {len(topics)});",
        ]
        assertions.extend(
            f"assertEq(_veriputLogs[{index}].topics[{topic_index}], {topic});"
            for topic_index, topic in enumerate(topics))
        assertions.append(f"assertEq(_veriputLogs[{index}].data, {data});")
        rendered.append({
            "class": "concrete-value",
            "kind": "event-log",
            "observed": "_veriputLogs",
            "expected": expected,
            "assertion": "\n".join(assertions),
            "provenance": "retained-ast-declaration-and-stage2-witness",
            "target_receiver": target_receiver,
        })
    return rendered


def _test_span(source: str, test_name: str) -> tuple[int, int]:
    match = re.search(r"\bfunction\s+" + re.escape(test_name) + r"\s*\([^)]*\)[^{]*\{", source)
    if match is None:
        raise ValueError("exact emitted test is absent")
    opening = source.find("{", match.start())
    depth = 0
    state = "code"
    quote = ""
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char in "\"'":
                state, quote = "string", char
            elif char == "/" and following == "/":
                state = "line"
                index += 1
            elif char == "/" and following == "*":
                state = "block"
                index += 1
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return opening, index + 1
        elif state == "string":
            if char == "\\":
                index += 1
            elif char == quote:
                state = "code"
        elif state == "line" and char == "\n":
            state = "code"
        elif state == "block" and char == "*" and following == "/":
            state = "code"
            index += 1
        index += 1
    raise ValueError("emitted test has no closing brace")


def inject_event_oracles(source: str, test_name: str, unit: str,
                         oracles: list[dict[str, Any]]) -> str:
    """Insert recordLogs and exact assertions around one direct target call."""
    if not re.search(r'import\s*\{[^}]*\bVm\b[^}]*\}\s*from\s*["\']forge-std/Vm.sol["\']',
                     source):
        imports = list(re.finditer(r'^\s*import\b[^;]*;\s*$', source, re.M))
        if not imports:
            raise ValueError("emitted replay has no import insertion point for Vm.Log")
        insertion = imports[-1].end()
        source = (source[:insertion] + '\nimport {Vm} from "forge-std/Vm.sol";'
                  + source[insertion:])
    start, end = _test_span(source, test_name)
    body = source[start:end]
    calls = list(
        re.finditer(
            r"(?m)^(?P<indent>[ \t]*)(?P<prefix>[^;\n{}]*?)(?P<receiver>[A-Za-z_$][\w$]*)\."
            + re.escape(unit) + r"\s*\(", body))
    if len(calls) != 1:
        raise ValueError(f"expected one direct .{unit} target call, found {len(calls)}")
    call = calls[0]
    if "." in call.group("prefix"):
        raise ValueError("event oracle target statement has another call before the target")
    receiver = call.group("receiver")
    if any(oracle.get("target_receiver") != receiver for oracle in oracles):
        raise ValueError("event oracle receiver differs from retained target call")
    statement_start = call.start()
    cursor = call.end() - 1
    depth = 0
    state = "code"
    quote = ""
    semicolon = None
    while cursor < len(body):
        char = body[cursor]
        if state == "code":
            if char in "\"'":
                state, quote = "string", char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == ";" and depth == 0:
                semicolon = cursor + 1
                break
        elif char == "\\":
            cursor += 1
        elif char == quote:
            state = "code"
        cursor += 1
    if semicolon is None:
        raise ValueError("target call statement is incomplete")
    indent = call.group("indent")
    assertion_lines = []
    seen = set()
    for oracle in oracles:
        for line in str(oracle["assertion"]).splitlines():
            if line not in seen:
                assertion_lines.append(indent + line)
                seen.add(line)
    record_prefix = body[:1] + "\n" + indent + "vm.recordLogs();" + body[1:statement_start]
    after = ("\n" + indent + "Vm.Log[] memory _veriputLogs = vm.getRecordedLogs();\n"
             + "\n".join(assertion_lines))
    rewritten_body = (record_prefix + body[statement_start:semicolon]
                      + after + body[semicolon:])
    return source[:start] + rewritten_body + source[end:]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _put_project(put_file: Path) -> Path | None:
    return next((parent for parent in put_file.parents
                 if (parent / "foundry.toml").is_file()), None)


def _canonical_flat(put_file: Path) -> Path | None:
    project = _put_project(put_file)
    flat = project / "src" / "flat.sol" if project else None
    return flat if flat is not None and flat.is_file() else None


def _exact_claim(report: dict[str, Any], identity: Identity) -> dict[str, Any]:
    matches = [claim for claim in report.get("claims") or []
               if isinstance(claim, dict) and claim.get("path_function") == identity[1]
               and str(claim.get("path_id")) == identity[3]]
    if len(matches) != 1:
        raise ValueError(f"expected one exact report claim, found {len(matches)}")
    return matches[0]


def _emitted_test(source: str, identity: Identity) -> str:
    label = re.escape(identity[1]) + r":path:" + re.escape(identity[3])
    matches = []
    for match in re.finditer(r"\bfunction\s+(test_[A-Za-z0-9_]+)\s*\(", source):
        prefix = source[:match.start()].splitlines()
        adjacent = []
        while prefix and (not prefix[-1].strip() or prefix[-1].lstrip().startswith("//")):
            adjacent.append(prefix.pop())
        if re.search(r"^\s*//\s*claim:\s*" + label + r"\s*$",
                     "\n".join(reversed(adjacent)), re.M):
            matches.append(match.group(1))
    if len(matches) != 1:
        raise ValueError(f"expected one exact emitted test, found {len(matches)}")
    return matches[0]


def recover_entry(entry: dict[str, Any], ast_paths: list[Path]) -> dict[str, Any]:
    raw_identity = entry.get("identity") or []
    if not isinstance(raw_identity, list) or len(raw_identity) != 5:
        raise ValueError("manifest entry has no five-part identity")
    identity: Identity = tuple(str(value or "") for value in raw_identity)  # type: ignore
    put_json = Path(str(entry.get("put_json") or ""))
    report_path = put_json.parent / "emit" / "cov-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    claim = _exact_claim(report, identity)
    if not claim.get("events"):
        raise ValueError("exact claim is outside the event partition")
    event_ids = [declaration_id(item) for item in claim["events"]]
    put_file = Path(str(entry.get("put_file") or ""))
    flat = _canonical_flat(put_file)
    if flat is None:
        raise ValueError("canonical PUT flat.sol is absent")
    flat_sha256 = _sha256(flat)
    compatible = []
    errors = []
    for ast_path in ast_paths:
        census_result = ast_path.parent / "result.json"
        try:
            census = json.loads(census_result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append("retained AST has no path-census source identity")
            continue
        if census.get("source_sha256") != flat_sha256:
            errors.append("retained AST source hash differs from canonical PUT flat.sol")
            continue
        document = load_solast(ast_path)
        try:
            oracles = render_event_oracles(document, claim)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        compatible.append((ast_path, document, oracles))
    by_hash = {_sha256(path): (path, document, oracles)
               for path, document, oracles in compatible}
    if len(by_hash) != 1:
        detail = sorted(set(errors))[:3]
        raise ValueError(f"expected one retained AST identity, found {len(by_hash)}; {detail}")
    ast_path, _document, oracles = next(iter(by_hash.values()))
    sources = sorted(report_path.parent.glob("*.cov.t.sol"))
    if len(sources) != 1:
        raise ValueError(f"expected one emitted replay source, found {len(sources)}")
    source = sources[0].read_text(encoding="utf-8")
    test_name = _emitted_test(source, identity)
    rewritten = inject_event_oracles(source, test_name, identity[2], oracles)
    entry_state = claim.get("entry_storage") or {}
    final_state = claim.get("final_state") or {}
    changed_state = {str(name): value for name, value in final_state.items()
                     if entry_state.get(name) != value}
    return {
        "identity": list(identity),
        "event_declaration_ids": event_ids,
        "claim": claim,
        "claim_source": str(report_path),
        "claim_source_sha256": _sha256(report_path),
        "ast": str(ast_path),
        "ast_sha256": _sha256(ast_path),
        "ast_source_identity": str(ast_path.parent / "result.json"),
        "canonical_flat_sha256": flat_sha256,
        "emitted_source": str(sources[0]),
        "emitted_source_sha256": _sha256(sources[0]),
        "test": test_name,
        "put_test": str(json.loads(put_json.read_text(encoding="utf-8")).get("test") or ""),
        "oracles": oracles,
        "event_oracle_complete": True,
        "changed_state_requiring_composition": changed_state,
        "anchor_boundary_complete": not changed_state,
        "rewritten_source": rewritten,
    }


def _subject_ast_index(ast_root: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in ast_root.rglob("flat.sol.solast"):
        result.setdefault(path.parent.name, []).append(path)
    return result


def _artifact_key(identity: list[str]) -> str:
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:20]


def _write_candidate(record: dict[str, Any], put_file: Path, scratch: Path) -> Path:
    destination = scratch / "dry-run" / _artifact_key(record["identity"])
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "test").mkdir(parents=True)
    (destination / "src").mkdir()
    replay = destination / "test" / "replay.t.sol"
    replay.write_text(record.pop("rewritten_source"), encoding="utf-8")
    project = _put_project(put_file)
    flat = project / "src" / "flat.sol" if project else None
    if flat is None or not flat.is_file():
        raise ValueError("canonical PUT project flat.sol is absent")
    shutil.copy2(flat, destination / "test" / "flat.sol")
    shutil.copy2(flat, destination / "src" / "flat.sol")
    shutil.copy2(put_file, destination / "test" / "original-put.t.sol")
    (destination / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\ntest = "test"\nlibs = ["lib"]\n', encoding="utf-8")
    lib = destination / "lib"
    lib.mkdir()
    forge_std = project / "lib" / "forge-std"
    if not forge_std.exists():
        raise ValueError("canonical PUT project forge-std is absent")
    (lib / "forge-std").symlink_to(forge_std.resolve(), target_is_directory=True)
    record.update({"candidate_source": str(replay),
                   "candidate_source_sha256": _sha256(replay),
                   "scratch_project": str(destination)})
    (destination / "metadata.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _forge_gate(project: Path, test_name: str) -> tuple[bool, str, int]:
    command = ["forge", "test", "--json", "--match-test", test_name]
    completed = subprocess.run(command, cwd=project, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=180, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return False, completed.stdout, completed.returncode
    results = []
    if isinstance(payload, dict):
        for suite in payload.values():
            if not isinstance(suite, dict):
                continue
            tests = suite.get("test_results") or {}
            if isinstance(tests, dict):
                results.extend((name, result) for name, result in tests.items()
                               if name.startswith(test_name + "("))
    success = (completed.returncode == 0 and len(results) == 1
               and isinstance(results[0][1], dict)
               and results[0][1].get("status") == "Success")
    return success, completed.stdout, completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ast-root", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--forge", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    ast_index = _subject_ast_index(args.ast_root)
    ready = []
    failed = []
    composition_required = []
    refused = []
    attempted = 0
    for entry in manifest.get("entries") or []:
        put_json = Path(str(entry.get("put_json") or ""))
        report_path = put_json.parent / "emit" / "cov-report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            identity = tuple(str(value or "") for value in entry["identity"])
            claim = _exact_claim(report, identity)  # type: ignore
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            continue
        if not claim.get("events"):
            continue
        if len(ready) >= args.limit:
            break
        attempted += 1
        subject = identity[0].split("/", 1)[-1]
        try:
            record = recover_entry(entry, ast_index.get(subject, []))
            if not record["anchor_boundary_complete"]:
                composition_required.append({
                    key: value for key, value in record.items()
                    if key != "rewritten_source"
                })
                continue
            project = _write_candidate(record, Path(str(entry.get("put_file") or "")),
                                       args.scratch_root)
            if args.forge:
                anchor_ok, anchor_output, anchor_returncode = _forge_gate(
                    project, record["test"])
                put_ok, put_output, put_returncode = _forge_gate(
                    project, record["put_test"])
                record["forge_status"] = (
                    "Success" if anchor_ok and put_ok else "Failed")
                record["anchor_forge_returncode"] = anchor_returncode
                record["anchor_forge_output_tail"] = anchor_output[-4000:]
                record["put_forge_returncode"] = put_returncode
                record["put_forge_output_tail"] = put_output[-4000:]
                (project / "metadata.json").write_text(
                    json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                if record["forge_status"] != "Success":
                    failed.append({key: value for key, value in record.items()
                                   if key != "rewritten_source"})
                    continue
            ready.append({key: value for key, value in record.items()
                          if key != "rewritten_source"})
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            refused.append({"identity": list(identity), "reason": str(exc)})
    report = {
        "schema": "veriput-rq1-anchor-events-dry-run/v1",
        "partition": "exact retained claims with a non-empty events list",
        "attempted": attempted,
        "ready": ready,
        "failed": failed,
        "composition_required": composition_required,
        "refused": refused,
    }
    args.scratch_root.mkdir(parents=True, exist_ok=True)
    (args.scratch_root / "inventory.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"attempted": attempted, "ready": len(ready), "failed": len(failed),
                      "composition_required": len(composition_required),
                      "refused": len(refused)}, sort_keys=True))
    return 0 if ready and (not args.forge or all(row.get("forge_status") == "Success"
                                                 for row in ready)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
