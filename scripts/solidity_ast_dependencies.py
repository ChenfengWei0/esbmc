"""Solc-AST dependency facts shared by VeriPUT's region and oracle stages."""

import json
import os
import re

SLOT_DEPENDENCY_POLICY = "solc-reference-closure/3"


def path_function_declaration_id(path_function):
    """Return the solc declaration id encoded in a path-function identity."""
    if not isinstance(path_function, str):
        return None
    match = re.search(r"#([0-9]+)$", path_function)
    return int(match.group(1)) if match else None


def path_function_artifact_suffix(path_function):
    """Stable filesystem suffix for an explicitly selected path function."""
    declaration_id = path_function_declaration_id(path_function)
    return f"__pf{declaration_id}" if declaration_id is not None else ""


def _ast_root(ast_path):
    if not ast_path or not os.path.exists(ast_path):
        return None
    try:
        text = open(ast_path).read()
        return json.loads(text[text.index("{"):])
    except (OSError, ValueError):
        return None


def _chain_nodes(ast, contract):
    by_id, target = {}, None

    def index(node):
        nonlocal target
        if isinstance(node, dict):
            if node.get("nodeType") == "ContractDefinition":
                if node.get("id") is not None:
                    by_id[node["id"]] = node
                if node.get("name") == contract:
                    target = node
            for value in node.values():
                index(value)
        elif isinstance(node, list):
            for value in node:
                index(value)

    index(ast)
    if target is None:
        return None
    chain = target.get("linearizedBaseContracts") or [target.get("id")]
    return [by_id[node_id] for node_id in reversed(chain) if node_id in by_id]


def unit_state_dependencies(ast_path, contract, unit, arity=None, declaration_id=None):
    """Return state declarations reached by a unit, ordered by call distance.

    The walk follows solc's ``referencedDeclaration`` links through modifiers
    and transitive implemented calls. ``None`` means the dependency question
    could not be answered, which lets callers fail closed instead of silently
    reverting to an all-mappings cross product.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return None, ["dependency walk unavailable: AST is absent or unreadable"]
    nodes = _chain_nodes(ast, contract)
    if nodes is None:
        return None, [
            f"dependency walk unavailable: contract {contract!r} "
            "was not found in the AST"
        ]

    by_id = {}

    def index(node):
        if isinstance(node, dict):
            if isinstance(node.get("id"), int):
                by_id[node["id"]] = node
            for value in node.values():
                index(value)
        elif isinstance(node, list):
            for value in node:
                index(value)

    index(ast)
    state_by_id = {}
    targets = []
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            if (declaration.get("nodeType") == "VariableDeclaration"
                    and declaration.get("stateVariable") and declaration.get("name")):
                state_by_id[declaration["id"]] = declaration["name"]
            if (declaration.get("nodeType") == "FunctionDefinition"
                    and declaration.get("name") == unit and declaration.get("body") is not None):
                params = ((declaration.get("parameters") or {}).get("parameters") or [])
                if declaration_id is not None:
                    if declaration.get("id") == declaration_id:
                        targets.append(declaration)
                elif arity is None or len(params) == arity:
                    targets.append(declaration)
    if not targets:
        return None, [
            f"dependency walk unavailable: no implemented function "
            f"named {unit!r}" +
            (f" with declaration id {declaration_id}" if declaration_id is not None else
             ("" if arity is None else f" with arity {arity}")) +
            f" was found in contract {contract!r} or its linearized bases"
        ]

    callables = {
        node_id: node
        for node_id, node in by_id.items()
        if node.get("nodeType") in ("FunctionDefinition",
                                    "ModifierDefinition") and node.get("body") is not None
    }
    best_callable_depth = {}
    found = {}

    def label(node):
        kind = ("modifier" if node.get("nodeType") == "ModifierDefinition" else "function")
        return f"{kind} {node.get('name') or '<anonymous>'}#{node.get('id')}"

    def visit(node, depth, chain):
        node_id = node.get("id")
        old_depth = best_callable_depth.get(node_id)
        if old_depth is not None and old_depth <= depth:
            return
        best_callable_depth[node_id] = depth
        next_calls = []

        def scan(value):
            if isinstance(value, dict):
                ref = value.get("referencedDeclaration")
                if ref in state_by_id:
                    name = state_by_id[ref]
                    candidate = (depth, tuple(chain), value.get("src") or "")
                    if name not in found or candidate < found[name]:
                        found[name] = candidate
                if ref in callables and ref != node_id:
                    next_calls.append(callables[ref])
                for child in value.values():
                    scan(child)
            elif isinstance(value, list):
                for child in value:
                    scan(child)

        scan(node.get("modifiers") or [])
        scan(node.get("body"))
        for callee in next_calls:
            visit(callee, depth + 1, chain + [label(callee)])

    for target in targets:
        visit(target, 0, [label(target)])

    ordered = sorted(found, key=lambda name: (found[name][0], name))
    evidence = []
    for name in ordered:
        depth, chain, src = found[name]
        evidence.append(f"state.{name} dependency distance {depth}: " + " -> ".join(chain) +
                        (f" at AST src {src}" if src else ""))
    return ordered, evidence
