#!/usr/bin/env python3
"""Recover exact state-delta anchors from retained Stage-2 evidence.

The public materializer is deliberately independent of the RQ1 backfill
driver.  It accepts an already authenticated report state delta plus solc's
storage layout, and changes only the retained zero-argument replay body.  A
state name that cannot be mapped exactly to a scalar storage word is refused.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

from solidity_path_put import (  # pylint: disable=import-error,wrong-import-position  # noqa: E402
    find_unit_call, map_value_slot_expr, parse_slot_name, slot_read_expr_at, storage_layout,
    target_instance_for_call)
from put_all import (  # pylint: disable=wrong-import-position  # noqa: E402
    FORGE_STD, _solidity_code_mask, _solidity_test_span)


def _scalar(raw: Any) -> int | None:
    """Normalize a report scalar without assigning meaning to aggregates."""
    text = str(raw).strip()
    while True:
        cast = re.fullmatch(r"[A-Za-z_]\w*(?:\s+payable)?\s*\((.*)\)", text, re.S)
        if cast is None:
            break
        text = cast.group(1).strip()
    if text == "true":
        return 1
    if text == "false":
        return 0
    try:
        return int(text, 0)
    except ValueError:
        return None


def _mapping_key_expr(raw: str, solidity_type: str) -> str | None:
    """Render one literal mapping key with the exact value-type ABI shape."""
    try:
        value = int(raw.strip(), 0)
    except ValueError:
        return None
    label = solidity_type.strip()
    if re.fullmatch(r"uint(?:\d+)?|int(?:\d+)?|enum\s+.+", label):
        return f"uint256({value})"
    if label == "address":
        return f"address(uint160({value}))"
    if label == "bool" and value in (0, 1):
        return "true" if value else "false"
    match = re.fullmatch(r"bytes([1-9]|[12]\d|3[0-2])", label)
    if match is not None:
        width = int(match.group(1)) * 8
        return f"bytes{match.group(1)}(uint{width}({value}))"
    return None


def _state_read(variable: str, receiver: str, layout: dict[str, tuple[int, int, int]],
                maps: dict[str, tuple[Any, ...]]) -> tuple[str, dict[str, int] | None, str | None]:
    """Return a uint256 storage read and its auditable layout coordinates."""
    # pylint: disable=too-many-locals
    mapping, keys, tail = parse_slot_name(variable)
    if mapping is None:
        spec = layout.get(variable)
        if spec is None:
            return "", None, f"state variable {variable!r} has no exact scalar solc layout"
        slot, offset, width = spec
        load = (f"uint256(vm.load(address({receiver}),"
                f"bytes32(uint256({slot}))))")
        expression = load if offset == 0 and width == 32 else (
            f"({load}>>{offset * 8})&uint256({hex((1 << (width * 8)) - 1)})")
        return expression, {
            "storage_slot": slot,
            "storage_offset_bytes": offset,
            "storage_width_bytes": width,
            "storage_expression": expression,
        }, None

    query = mapping + tail
    spec = maps.get(query)
    if spec is None or len(spec) < 6:
        return "", None, f"mapping state variable {variable!r} has no scalar solc layout"
    key_types = [spec[1]] if isinstance(spec[1], str) else list(spec[1])
    if len(keys) != len(key_types):
        return "", None, f"mapping state variable {variable!r} has the wrong key arity"
    key_exprs = []
    for key, key_type in zip(keys, key_types):
        expression = _mapping_key_expr(key, key_type)
        if expression is None:
            return "", None, f"mapping key {key!r} is not an exact value-type literal"
        key_exprs.append(expression)
    slot, _key_type, width, offset, _base, _member = spec[:6]
    slot_expression = map_value_slot_expr(key_exprs, spec)
    expression = slot_read_expr_at(f"address({receiver})", slot_expression, offset, width)
    return expression, {
        "storage_slot": slot,
        "storage_offset_bytes": offset,
        "storage_width_bytes": width,
        "storage_expression": expression,
    }, None


def materialize_state_delta_oracles(
    source: str,
    test_name: str,
    unit: str,
    state_delta: dict[str, Any],
    storage: tuple[dict[str, tuple[int, int, int]], dict[str, tuple[Any, ...]]],
) -> tuple[str, list[dict[str, Any]], str | None]:
    """Append complete exact post-state assertions to one retained replay."""
    # pylint: disable=too-many-locals,too-many-return-statements
    layout, maps = storage
    if not isinstance(state_delta, dict) or not state_delta:
        return source, [], "retained claim has no state delta"
    span, error = _solidity_test_span(source, test_name)
    if span is None:
        return source, [], error
    function = source[span[0]:span[1]]
    mask = _solidity_code_mask(function)
    opening = mask.find("{")
    closing = mask.rfind("}")
    if opening < 0 or closing <= opening:
        return source, [], "retained replay function body is malformed"
    body = function[opening + 1:closing]
    lines = body.splitlines()
    call_index = find_unit_call(lines, unit)
    if call_index is None:
        return source, [], "retained replay has no selected target call"
    receiver = target_instance_for_call(lines, call_index, unit)
    if receiver is None:
        return source, [], "retained replay target receiver is ambiguous"
    semantic_suffix = _solidity_code_mask("\n".join(lines[call_index + 1:])).strip()
    if semantic_suffix:
        return source, [], "retained replay has executable statements after the target call"
    if ";" not in lines[call_index]:
        return source, [], "selected target call does not end in one retained statement"

    injected = []
    oracles = []
    for index, (variable, delta) in enumerate(sorted(state_delta.items())):
        if not isinstance(delta, dict) or "after" not in delta:
            return source, [], f"state delta {variable!r} has no exact after value"
        expected = _scalar(delta.get("after"))
        if expected is None or expected < 0:
            return source, [], f"state delta {variable!r} is not an unsigned scalar"
        expression, coordinates, read_error = _state_read(str(variable), receiver, layout, maps)
        if read_error is not None or coordinates is None:
            return source, [], read_error
        observed = "_veriput_state_" + re.sub(r"[^A-Za-z0-9_]", "_",
                                              str(variable)).strip("_") + f"_{index}"
        declaration = f"uint256 {observed} = {expression};"
        assertion = f"assertEq({observed}, uint256({expected}));"
        injected.extend(("    " + declaration, "    " + assertion))
        oracles.append({
            "class": "concrete-value",
            "kind": "storage-slot-post-state",
            "source": "vm.load",
            "observed": observed,
            "expected": f"uint256({expected})",
            "provenance": "stage2-witness",
            "target_receiver": receiver,
            "storage_variable": str(variable),
            **coordinates,
            "assertion": assertion,
        })

    lines[call_index + 1:call_index + 1] = injected
    rewritten_body = "\n".join(lines)
    if body.endswith("\n"):
        rewritten_body += "\n"
    rewritten_function = function[:opening + 1] + rewritten_body + function[closing:]
    rewritten = source[:span[0]] + rewritten_function + source[span[1]:]
    return rewritten, oracles, None


def isolated_storage_layout(
    put_source: Path, scratch: Path, contract: str
) -> tuple[dict[str, tuple[int, int, int]] | None, dict[str, tuple[Any, ...]] | None, str | None]:
    """Compile retained sources only in scratch and return solc's layout."""
    project = next((parent for parent in (put_source.parent, *put_source.parents)
                    if (parent / "foundry.toml").is_file()), None)
    if project is None:
        return None, None, "retained PUT has no Foundry project"
    # The caller gives this function an obligation-private directory.  Clear
    # prior generated tests and build output so a second dry-run cannot make
    # forge inspect see duplicate contracts from its own earlier artifacts.
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    for name in ("src", ):
        source_dir = project / name
        if source_dir.is_dir():
            shutil.copytree(source_dir, scratch / name, dirs_exist_ok=True)
    shutil.copy2(project / "foundry.toml", scratch / "foundry.toml")
    forge_std = project / "lib" / "forge-std"
    forge_std = forge_std.resolve() if forge_std.exists() else Path(FORGE_STD).resolve()
    if forge_std.exists():
        (scratch / "lib").mkdir(exist_ok=True)
        link = scratch / "lib" / "forge-std"
        if not link.exists():
            link.symlink_to(forge_std)
    return storage_layout(scratch, contract)
