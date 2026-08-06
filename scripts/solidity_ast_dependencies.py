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


def _expr_coord_name(expr, state_by_id=None, constant_by_id=None,
                     alias_by_id=None, seen=None):
    if isinstance(expr, str):
        return expr
    if not isinstance(expr, dict):
        return None
    seen = set() if seen is None else set(seen)
    if expr.get("nodeType") == "Literal":
        if expr.get("kind") == "number":
            value = str(expr.get("value") or "")
            return value if value.isdigit() else None
        if expr.get("kind") == "bool":
            value = expr.get("value")
            if value is True or str(value).lower() == "true":
                return "1"
            if value is False or str(value).lower() == "false":
                return "0"
        if expr.get("kind") == "hexString":
            value = str(expr.get("hexValue") or expr.get("value") or "")
            if re.fullmatch(r"[0-9a-fA-F]+", value):
                return "0x" + value
        return None
    if (expr.get("nodeType") == "FunctionCall"
            and expr.get("kind") == "typeConversion"):
        args = expr.get("arguments") or []
        cast_expr = expr.get("expression") or {}
        type_name = cast_expr.get("typeName") or {}
        if len(args) == 1 and type_name.get("name") == "address":
            return _expr_coord_name(args[0], state_by_id, constant_by_id,
                                    alias_by_id, seen)
    if expr.get("nodeType") == "Identifier" and expr.get("name"):
        ref = expr.get("referencedDeclaration")
        if alias_by_id and ref in alias_by_id:
            if ref in seen:
                return None
            seen.add(ref)
            return _expr_coord_name(alias_by_id[ref], state_by_id,
                                    constant_by_id, alias_by_id, seen)
        if constant_by_id and ref in constant_by_id:
            return _expr_coord_name(
                constant_by_id[ref], state_by_id, constant_by_id,
                alias_by_id, seen)
        if state_by_id and ref in state_by_id:
            return "state." + state_by_id[ref]
        return expr["name"]
    if expr.get("nodeType") == "MemberAccess" and expr.get("memberName"):
        base = expr.get("expression") or {}
        base_name = _expr_coord_name(
            base, state_by_id, constant_by_id, alias_by_id, seen)
        if base_name:
            return f"{base_name}.{expr['memberName']}"
    return None


def _index_access_chain(node, state_by_id=None, constant_by_id=None,
                        alias_by_id=None):
    keys = []
    cur = node
    while isinstance(cur, dict) and cur.get("nodeType") == "IndexAccess":
        key = _expr_coord_name(
            cur.get("indexExpression"), state_by_id, constant_by_id,
            alias_by_id)
        if key is None:
            return None
        keys.append(key)
        cur = cur.get("baseExpression")
    if isinstance(cur, dict) and cur.get("nodeType") == "Identifier":
        ref = cur.get("referencedDeclaration")
        if isinstance(ref, int):
            return ref, tuple(reversed(keys))
    return None


def unit_mapping_slot_accesses(
        ast_path, contract, unit, arity=None, declaration_id=None):
    """Return concrete parameter-keyed mapping slots reached by a unit.

    ``unit_state_dependencies`` says which mappings are in the callable
    closure. This stronger fact preserves the key expression chain solc
    resolved in source, e.g. ``_balances[maker][app][strategyHash][token]``.
    ``None`` means the AST question could not be answered.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return None, ["slot-access walk unavailable: AST is absent or unreadable"]
    nodes = _chain_nodes(ast, contract)
    if nodes is None:
        return None, [
            f"slot-access walk unavailable: contract {contract!r} "
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
    constant_by_id = {}
    targets = []
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            if (declaration.get("nodeType") == "VariableDeclaration"
                    and declaration.get("stateVariable")
                    and declaration.get("name")):
                state_by_id[declaration["id"]] = declaration["name"]
                if declaration.get("constant"):
                    constant_by_id[declaration["id"]] = declaration.get("value")
            if (declaration.get("nodeType") == "FunctionDefinition"
                    and declaration.get("name") == unit
                    and declaration.get("body") is not None):
                params = ((declaration.get("parameters") or {}).get("parameters") or [])
                if declaration_id is not None:
                    if declaration.get("id") == declaration_id:
                        targets.append(declaration)
                elif arity is None or len(params) == arity:
                    targets.append(declaration)
    if not targets:
        return None, [
            f"slot-access walk unavailable: no implemented function "
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

    def visit(node, depth, chain, initial_aliases=None):
        node_id = node.get("id")
        alias_fingerprint = json.dumps(initial_aliases or {}, sort_keys=True)
        call_key = (node_id, alias_fingerprint)
        old_depth = best_callable_depth.get(call_key)
        if old_depth is not None and old_depth <= depth:
            return
        best_callable_depth[call_key] = depth
        next_calls = []
        alias_by_id = dict(initial_aliases or {})

        def declaration_ref(declaration):
            if not isinstance(declaration, dict):
                return None
            ref = declaration.get("id")
            return ref if isinstance(ref, int) else None

        def identifier_ref(expr):
            if not isinstance(expr, dict) or expr.get("nodeType") != "Identifier":
                return None
            ref = expr.get("referencedDeclaration")
            return ref if isinstance(ref, int) else None

        def call_aliases(callee, arguments):
            formals = ((callee.get("parameters") or {}).get("parameters")
                       or [])
            actuals = arguments or []
            if len(formals) != len(actuals):
                return {}
            out = dict(alias_by_id)
            for formal, actual in zip(formals, actuals):
                ref = declaration_ref(formal)
                if ref is not None:
                    name = _expr_coord_name(
                        actual, state_by_id, constant_by_id, alias_by_id)
                    if name is not None:
                        out[ref] = name
            return out

        def call_expression_ref(expr):
            while (isinstance(expr, dict)
                   and expr.get("nodeType") == "FunctionCallOptions"):
                expr = expr.get("expression") or {}
            if not isinstance(expr, dict):
                return None
            ref = expr.get("referencedDeclaration")
            return ref if isinstance(ref, int) else None

        def callable_ref(value):
            if value.get("nodeType") == "FunctionCall":
                ref = call_expression_ref(value.get("expression") or {})
                if ref in callables and ref != node_id:
                    return ref
            for key in ("modifierName", "modifierNamePath"):
                expr = value.get(key) or {}
                ref = expr.get("referencedDeclaration")
                if ref in callables and ref != node_id:
                    return ref
            return None

        def scan(value):
            if isinstance(value, dict):
                if value.get("nodeType") == "Block":
                    old_aliases = dict(alias_by_id)
                    for stmt in value.get("statements") or []:
                        scan(stmt)
                    alias_by_id.clear()
                    alias_by_id.update(old_aliases)
                    return
                if value.get("nodeType") == "VariableDeclarationStatement":
                    scan(value.get("initialValue"))
                    decls = [d for d in (value.get("declarations") or [])
                             if isinstance(d, dict)]
                    init = value.get("initialValue")
                    if len(decls) == 1 and init is not None:
                        ref = declaration_ref(decls[0])
                        if ref is not None:
                            name = _expr_coord_name(
                                init, state_by_id, constant_by_id, alias_by_id)
                            if name is not None:
                                alias_by_id[ref] = name
                    return
                if value.get("nodeType") == "IndexAccess":
                    chain_got = _index_access_chain(
                        value, state_by_id, constant_by_id, alias_by_id)
                    if chain_got:
                        ref, keys = chain_got
                        if ref in state_by_id:
                            name = state_by_id[ref]
                            candidate = (depth, tuple(chain), value.get("src") or "")
                            old = found.get((name, keys))
                            if old is None or candidate < old:
                                found[(name, keys)] = candidate
                    # Do not recurse through baseExpression here; otherwise a
                    # four-level slot also emits its three partial sub-stores.
                    scan(value.get("indexExpression"))
                    return
                if value.get("nodeType") == "Assignment":
                    for child in value.values():
                        scan(child)
                    ref = identifier_ref(value.get("leftHandSide"))
                    if ref is not None:
                        if value.get("operator") == "=":
                            name = _expr_coord_name(
                                value.get("rightHandSide"), state_by_id,
                                constant_by_id, alias_by_id)
                            if name is not None:
                                alias_by_id[ref] = name
                            elif ref in alias_by_id:
                                del alias_by_id[ref]
                        elif ref in alias_by_id:
                            del alias_by_id[ref]
                    return
                ref = callable_ref(value)
                if ref is not None:
                    next_calls.append(
                        (callables[ref], call_aliases(
                            callables[ref], value.get("arguments") or [])))
                for child in value.values():
                    scan(child)
            elif isinstance(value, list):
                for child in value:
                    scan(child)

        scan(node.get("modifiers") or [])
        scan(node.get("body"))
        for callee, aliases in next_calls:
            visit(callee, depth + 1, chain + [label(callee)], aliases)

    for target in targets:
        visit(target, 0, [label(target)])

    ordered_items = sorted(found, key=lambda item: (found[item][0], item[0], item[1]))
    accesses = [(name, keys) for name, keys in ordered_items]
    evidence = []
    for name, keys in accesses:
        depth, chain, src = found[(name, keys)]
        evidence.append(
            f"state.{name}" + "".join(f"[{k}]" for k in keys) +
            f" slot-access distance {depth}: " + " -> ".join(chain) +
            (f" at AST src {src}" if src else ""))
    return accesses, evidence
