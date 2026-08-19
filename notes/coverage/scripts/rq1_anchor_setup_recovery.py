#!/usr/bin/env python3
"""Selected-contract setup reconciliation for retained RQ1 CE anchors."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from put_all import _matching_delimiter, _solidity_code_mask


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _strip_comments(value: str) -> str:
    """Remove comments while preserving quoted literal bytes exactly."""
    output = []
    index = 0
    quote = None
    while index < len(value):
        char = value[index]
        if quote is not None:
            output.append(char)
            if char == "\\" and index + 1 < len(value):
                index += 1
                output.append(value[index])
            elif char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
            output.append(char)
        elif value.startswith("//", index):
            output.append(" ")
            newline = value.find("\n", index + 2)
            index = len(value) if newline < 0 else newline - 1
        elif value.startswith("/*", index):
            output.append(" ")
            closing = value.find("*/", index + 2)
            index = len(value) if closing < 0 else closing + 1
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _compact_code(value: str) -> str:
    return re.sub(r"\s+", "", _strip_comments(value))


def solidity_tokens(value: str) -> tuple[str, ...]:
    """Lex Solidity source without erasing token boundaries or literal bytes."""
    source = _strip_comments(value)
    tokens = []
    index = 0
    multi = (">>=", "<<=", "**", "++", "--", "&&", "||", "==", "!=", "<=", ">=",
             "<<", ">>", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "=>", "->")
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if char in ("'", '"'):
            start = index
            quote = char
            index += 1
            while index < len(source):
                if source[index] == "\\" and index + 1 < len(source):
                    index += 2
                    continue
                index += 1
                if source[index - 1] == quote:
                    break
            tokens.append(source[start:index])
            continue
        match = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", source[index:])
        if match is None:
            match = re.match(r"(?:0[xX][0-9A-Fa-f_]+|[0-9][0-9A-Za-z_.]*)", source[index:])
        if match is not None:
            tokens.append(match.group(0))
            index += len(match.group(0))
            continue
        operator = next((item for item in multi if source.startswith(item, index)), None)
        if operator is not None:
            tokens.append(operator)
            index += len(operator)
            continue
        tokens.append(char)
        index += 1
    return tuple(tokens)


def token_stream_sha256(value: str) -> str:
    """Hash the unambiguous JSON encoding of a Solidity token stream."""
    encoded = json.dumps(solidity_tokens(value), separators=(",", ":"))
    return _sha256(encoded)


def _selected_contract_function_span(source: str, selected_test: str,
                                     function_name: str) -> tuple[int, int] | None:
    """Return one helper owned by the contract containing selected_test."""
    mask = _solidity_code_mask(source)
    tests = list(re.finditer(r"\bfunction\s+" + re.escape(selected_test) + r"\s*\(", mask))
    if len(tests) != 1:
        return None
    test_pos = tests[0].start()
    containers = []
    pattern = r"\b(?:contract|abstract\s+contract)\s+[A-Za-z_$][A-Za-z0-9_$]*[^{};]*\{"
    for match in re.finditer(pattern, mask):
        opening = mask.find("{", match.start())
        closing = _matching_delimiter(mask, opening, "{", "}")
        if closing is not None and opening < test_pos < closing:
            containers.append((opening, closing))
    if not containers:
        return None
    opening, closing = max(containers)
    matches = list(re.finditer(r"\bfunction\s+" + re.escape(function_name) + r"\s*\(",
                               mask[opening + 1:closing]))
    if len(matches) != 1:
        return None
    start = opening + 1 + matches[0].start()
    body_open = mask.find("{", start)
    body_close = _matching_delimiter(mask, body_open, "{", "}")
    if body_open < 0 or body_close is None:
        return None
    return start, body_close + 1


def _body(function_source: str) -> str | None:
    mask = _solidity_code_mask(function_source)
    opening = mask.find("{")
    closing = _matching_delimiter(mask, opening, "{", "}")
    if opening < 0 or closing is None:
        return None
    return function_source[opening + 1:closing]


def _declaration(function_source: str) -> str | None:
    mask = _solidity_code_mask(function_source)
    opening = mask.find("{")
    if opening < 0:
        return None
    declaration = _compact_code(function_source[:opening])
    if not re.fullmatch(r"functionsetUp\(\)(?:public|external)(?:virtual|override)*", declaration):
        return None
    return declaration


def _prank_deployment(value: str) -> tuple[str, str] | None:
    """Parse the only accepted prank window: one zero-argument deployment."""
    compact = _compact_code(value)
    match = re.fullmatch(
        r"vm\.startPrank\((.+)\);c0=new([A-Za-z_$][A-Za-z0-9_$]*)\(\);"
        r"vm\.stopPrank\(\);", compact)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _split_top_level_arguments(value: str) -> list[str] | None:
    depth = 0
    start = 0
    arguments = []
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif char == "," and depth == 0:
            arguments.append(value[start:index])
            start = index + 1
    if depth != 0:
        return None
    arguments.append(value[start:])
    return arguments


def _matching_deployment_context(put_body: str, emit_body: str) -> bool:
    put_deployment = _prank_deployment(put_body)
    emit_deployment = _prank_deployment(emit_body)
    if put_deployment is None or emit_deployment is None:
        return False
    put_arguments = _split_top_level_arguments(put_deployment[0])
    emit_arguments = _split_top_level_arguments(emit_deployment[0])
    same_target = put_deployment[1] == emit_deployment[1]
    same_sender = (put_arguments is not None and emit_arguments is not None
                   and len(put_arguments) == 2 and len(emit_arguments) == 1
                   and put_arguments[0] == emit_arguments[0]
                   and put_arguments[1] == emit_arguments[0])
    return same_target and same_sender


def _sealed_deployer_equivalence(selector: dict[str, Any], put_body: str,
                                 emit_body: str) -> bool:
    """Accept the narrow two-arg/one-arg prank setup equivalence proof."""
    setup = selector.get("semantic_setup") or {}
    facts = selector.get("deployment_safety_facts") or {}
    if selector.get("tier") != "deployer_context_only":
        return False
    if (_sha256(put_body) != setup.get("put_setup_sha256")
            or _sha256(emit_body) != setup.get("emit_setup_sha256")
            or token_stream_sha256(put_body) != setup.get("put_tokens_sha256")
            or token_stream_sha256(emit_body) != setup.get("emit_tokens_sha256")):
        return False
    if not _matching_deployment_context(put_body, emit_body):
        return False
    return (facts.get("constructor_count") == 0
            and facts.get("initializer_call_count") == 0
            and facts.get("nested_new_count") == 0
            and facts.get("deployment_environment_tokens") == [])


def _setup_parts(source: str, selected_test: str) -> tuple[tuple[int, int], str, str] | None:
    span = _selected_contract_function_span(source, selected_test, "setUp")
    if span is None:
        return None
    function = source[span[0]:span[1]]
    body = _body(function)
    declaration = _declaration(function)
    if body is None or declaration is None:
        return None
    return span, function, body


def _equivalence_kind(put_body: str, emit_body: str,
                      selector: dict[str, Any]) -> str | None:
    setup = selector.get("semantic_setup") or {}
    code_equal = (solidity_tokens(put_body) == solidity_tokens(emit_body)
                  and token_stream_sha256(put_body) == setup.get("put_tokens_sha256")
                  and token_stream_sha256(emit_body) == setup.get("emit_tokens_sha256"))
    if code_equal:
        return "selected-contract-code-equivalent/v1"
    if _sealed_deployer_equivalence(selector, put_body, emit_body):
        return "selected-contract-deployer-equivalent/v1"
    return None


def reconcile_selected_contract_setup(
    put_source: str,
    put_test: str,
    emit_source: str,
    emit_test: str,
    selector: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Replace only an authenticated equivalent emitted setUp with the PUT setUp."""
    put_parts = _setup_parts(put_source, put_test)
    emit_parts = _setup_parts(emit_source, emit_test)
    if put_parts is None or emit_parts is None:
        return None, "selected contract does not own exactly one setUp"
    put_setup, put_body = put_parts[1:]
    emit_span, emit_setup, emit_body = emit_parts
    if _declaration(put_setup) != _declaration(emit_setup):
        return None, "selected setUp body is malformed"
    kind = _equivalence_kind(put_body, emit_body, selector)
    if kind is None:
        return None, "setup equivalence is not hash-sealed"
    reconciled = emit_source[:emit_span[0]] + put_setup + emit_source[emit_span[1]:]
    return reconciled, kind
