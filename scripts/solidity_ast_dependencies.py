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


def _indexed_ast(ast):
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
    return by_id


def _unit_targets(nodes, unit, arity=None, declaration_id=None):
    targets = []
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            kind = declaration.get("kind", "function")
            declaration_name = declaration.get("name") or (
                kind if kind in ("fallback", "receive") else "")
            if (declaration.get("nodeType") == "FunctionDefinition"
                    and declaration_name == unit
                    and declaration.get("body") is not None):
                params = ((declaration.get("parameters") or {}).get("parameters") or [])
                if declaration_id is not None:
                    if declaration.get("id") == declaration_id:
                        targets.append(declaration)
                elif arity is None or len(params) == arity:
                    targets.append(declaration)
    return targets


def _state_declarations(nodes):
    declarations = []
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            if (declaration.get("nodeType") == "VariableDeclaration"
                    and declaration.get("stateVariable")
                    and declaration.get("name")):
                declarations.append(declaration)
    return declarations


def _public_state_getter(nodes, unit, arity=None, declaration_id=None):
    matches = []
    for declaration in _state_declarations(nodes):
        if declaration.get("name") != unit:
            continue
        if declaration.get("visibility") != "public":
            continue
        if (declaration_id is not None
                and declaration.get("id") != declaration_id):
            continue
        param_count = len(_public_getter_key_names(declaration))
        if arity is None or param_count == arity:
            matches.append(declaration)
    return matches[-1] if matches else None


def _mapping_type_parts(type_string):
    text = (type_string or "").strip()
    if not text.startswith("mapping(") or not text.endswith(")"):
        return None
    inner = text[len("mapping("):-1]
    depth = 0
    for i in range(len(inner) - 1):
        ch = inner[i]
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif ch == "=" and inner[i + 1] == ">" and depth == 0:
            return inner[:i].strip(), inner[i + 2:].strip()
    return None


def _array_type_part(type_string):
    text = (type_string or "").strip()
    if not text.endswith("]"):
        return None
    lb = text.rfind("[")
    if lb <= 0:
        return None
    return text[:lb].strip()


def _public_getter_key_names(declaration):
    names = []
    cur = ((declaration.get("typeDescriptions") or {}).get("typeString")
           or "")
    depth = 0
    while True:
        mapping_parts = _mapping_type_parts(cur)
        if mapping_parts is not None:
            _key_type, cur = mapping_parts
            names.append(f"key{depth}")
            depth += 1
            continue
        elem = _array_type_part(cur)
        if elem is not None:
            cur = elem
            names.append(f"index{depth}")
            depth += 1
            continue
        return names


def _callables(by_id):
    return {
        node_id: node
        for node_id, node in by_id.items()
        if node.get("nodeType") in ("FunctionDefinition",
                                    "ModifierDefinition") and node.get("body") is not None
    }


def _unit_not_found_reason(kind, contract, unit, arity=None, declaration_id=None):
    return [
        f"{kind} unavailable: no implemented function named {unit!r}" +
        (f" with declaration id {declaration_id}" if declaration_id is not None else
         ("" if arity is None else f" with arity {arity}")) +
        f" was found in contract {contract!r} or its linearized bases"
    ]


def unit_callable_facts(ast_path, contract, unit, arity=None,
                        declaration_id=None):
    """Return the source facts needed for a static unit replay candidate.

    This is deliberately a syntactic fact extractor, not a proof. It records
    the selected declaration's mutability and whether its own formal
    parameters occur in the body. Callers must combine it with the existing
    state/environment dependency walks and must still validate any emitted
    replay on the reference contract.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return None, ["callable facts unavailable: AST is absent or unreadable"]
    nodes = _chain_nodes(ast, contract)
    if nodes is None:
        return None, [
            f"callable facts unavailable: contract {contract!r} "
            "was not found in the AST"
        ]
    targets = _unit_targets(nodes, unit, arity, declaration_id)
    if not targets:
        return None, _unit_not_found_reason("callable facts", contract, unit,
                                            arity, declaration_id)

    target = targets[-1]
    parameters = ((target.get("parameters") or {}).get("parameters") or [])
    parameter_ids = {
        parameter.get("id"): parameter.get("name") or ""
        for parameter in parameters
        if isinstance(parameter.get("id"), int)
    }
    used_ids = set()

    def scan(node):
        if isinstance(node, dict):
            if (node.get("nodeType") == "Identifier"
                    and node.get("referencedDeclaration") in parameter_ids):
                used_ids.add(node["referencedDeclaration"])
            for value in node.values():
                scan(value)
        elif isinstance(node, list):
            for value in node:
                scan(value)

    scan(target.get("modifiers") or [])
    scan(target.get("body") or {})
    facts = {
        "declaration_id": target.get("id"),
        "name": target.get("name"),
        "state_mutability": target.get("stateMutability"),
        "parameters": [
            {
                "name": parameter.get("name") or "",
                "type": ((parameter.get("typeDescriptions") or {}).get(
                    "typeString") or ""),
            }
            for parameter in parameters
        ],
        "used_parameters": sorted(
            parameter_ids[parameter_id] for parameter_id in used_ids),
        "unused_parameters": sorted(
            parameter_ids[parameter_id]
            for parameter_id in parameter_ids
            if parameter_id not in used_ids),
    }
    evidence = [
        f"{contract}.{unit}#{target.get('id')} mutability="
        f"{facts['state_mutability'] or 'unknown'}",
        "callable parameter use: "
        + (", ".join(facts["used_parameters"]) or "none"),
    ]
    return facts, evidence


def contract_state_esbmc_store_names(ast_path, contract):
    """Return source state names mapped to ESBMC's contract-scope store names.

    The Solidity frontend disambiguates state fields by appending solc's
    declaration id to the merged contract struct field, e.g.
    ``_allowances`` becomes ``_allowances$496``. The source-level storage
    layout must keep using ``_allowances`` to compute the solc slot, but
    ``--path-cov-assert`` resolves the verifier-side store name.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return {}, ["state store aliases unavailable: AST is absent or unreadable"]
    nodes = _chain_nodes(ast, contract)
    if nodes is None:
        return {}, [
            f"state store aliases unavailable: contract {contract!r} "
            "was not found in the AST"
        ]

    declarations = []
    counts = {}
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            if (declaration.get("nodeType") == "VariableDeclaration"
                    and declaration.get("stateVariable")
                    and declaration.get("name")
                    and isinstance(declaration.get("id"), int)):
                name = declaration["name"]
                declarations.append((name, declaration["id"]))
                counts[name] = counts.get(name, 0) + 1

    aliases = {}
    skipped = []
    for name, declaration_id in declarations:
        if counts.get(name) != 1:
            skipped.append(name)
            continue
        aliases[name] = f"{name}${declaration_id}"
    evidence = []
    if skipped:
        evidence.append(
            "state store aliases skipped for duplicate source name(s): "
            + ", ".join(sorted(set(skipped))))
    return aliases, evidence


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

    by_id = _indexed_ast(ast)
    state_by_id = {}
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            if (declaration.get("nodeType") == "VariableDeclaration"
                    and declaration.get("stateVariable") and declaration.get("name")):
                state_by_id[declaration["id"]] = declaration["name"]
    targets = _unit_targets(nodes, unit, arity, declaration_id)
    if not targets:
        getter = _public_state_getter(nodes, unit, arity, declaration_id)
        if getter is None:
            return None, _unit_not_found_reason("dependency walk", contract, unit,
                                                arity, declaration_id)
        return [getter["name"]], [
            f"state.{getter['name']} dependency distance 0: public state "
            f"getter {contract}.{getter['name']}#{getter.get('id')}"
        ]

    callables = _callables(by_id)
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


def unit_contains_inline_assembly(ast_path, contract, unit, arity=None,
                                  declaration_id=None):
    """Return whether the target unit closure contains inline assembly.

    Source-level state dependency filtering is sound only when the dependency
    walk can see every state read. Inline assembly can read storage without a
    referencedDeclaration edge, so callers must fail closed for that closure.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return True, ["assembly check unavailable: AST is absent or unreadable"]
    nodes = _chain_nodes(ast, contract)
    if nodes is None:
        return True, [
            f"assembly check unavailable: contract {contract!r} was not found"
        ]
    by_id = _indexed_ast(ast)
    targets = _unit_targets(nodes, unit, arity, declaration_id)
    if not targets:
        getter = _public_state_getter(nodes, unit, arity, declaration_id)
        targets = [getter] if getter is not None else []
    if not targets:
        return True, [
            f"assembly check unavailable: target {contract}.{unit} was not found"
        ]
    callables = _callables(by_id)
    seen = set()

    def visit(node):
        if not isinstance(node, dict):
            return False
        node_id = node.get("id")
        if node_id in seen:
            return False
        seen.add(node_id)
        if node.get("nodeType") == "InlineAssembly":
            return True
        next_calls = []

        def scan(value):
            if isinstance(value, dict):
                if value.get("nodeType") == "InlineAssembly":
                    return True
                ref = value.get("referencedDeclaration")
                if ref in callables and ref != node_id:
                    next_calls.append(callables[ref])
                for child in value.values():
                    if scan(child):
                        return True
            elif isinstance(value, list):
                for child in value:
                    if scan(child):
                        return True
            return False

        if scan(node.get("modifiers") or []) or scan(node.get("body")):
            return True
        return any(visit(callee) for callee in next_calls)

    found = any(visit(target) for target in targets)
    return found, (["inline assembly is present in the target call closure"]
                   if found else [])


def unit_env_dependencies(ast_path, contract, unit, arity=None, declaration_id=None):
    """Return environment quantities read by a target-contract unit closure."""
    ast = _ast_root(ast_path)
    if ast is None:
        return None, ["env dependency walk unavailable: AST is absent or unreadable"]
    nodes = _chain_nodes(ast, contract)
    if nodes is None:
        return None, [
            f"env dependency walk unavailable: contract {contract!r} "
            "was not found in the AST"
        ]
    by_id = _indexed_ast(ast)
    targets = _unit_targets(nodes, unit, arity, declaration_id)
    if not targets:
        getter = _public_state_getter(nodes, unit, arity, declaration_id)
        if getter is None:
            return None, _unit_not_found_reason("env dependency walk", contract, unit,
                                                arity, declaration_id)
        return [], [
            f"env dependency walk: public state getter {contract}.{unit} "
            "does not read msg/tx/block itself"
        ]
    callables = _callables(by_id)
    best_callable_depth = {}
    found = {}

    def label(node):
        kind = ("modifier" if node.get("nodeType") == "ModifierDefinition" else "function")
        return f"{kind} {node.get('name') or '<anonymous>'}#{node.get('id')}"

    def env_name(node):
        if not isinstance(node, dict):
            return None
        if node.get("nodeType") != "MemberAccess":
            return None
        member = node.get("memberName")
        base = node.get("expression") or {}
        if base.get("nodeType") == "Identifier" and base.get("name") in ("msg", "tx", "block"):
            if member:
                return f"{base.get('name')}.{member}"
        return None

    def call_ref(value):
        if value.get("nodeType") != "FunctionCall":
            return None
        expr = value.get("expression") or {}
        while (isinstance(expr, dict)
               and expr.get("nodeType") == "FunctionCallOptions"):
            expr = expr.get("expression") or {}
        ref = expr.get("referencedDeclaration") if isinstance(expr, dict) else None
        return ref if ref in callables else None

    def visit(node, depth, chain):
        node_id = node.get("id")
        old_depth = best_callable_depth.get(node_id)
        if old_depth is not None and old_depth <= depth:
            return
        best_callable_depth[node_id] = depth
        next_calls = []

        def scan(value):
            if isinstance(value, dict):
                name = env_name(value)
                if name:
                    candidate = (depth, tuple(chain), value.get("src") or "")
                    if name not in found or candidate < found[name]:
                        found[name] = candidate
                ref = call_ref(value)
                if ref is not None and ref != node_id:
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
        evidence.append(f"env.{name} dependency distance {depth}: " +
                        " -> ".join(chain) +
                        (f" at AST src {src}" if src else ""))
    return ordered, evidence


def _expr_coord_name(expr, state_by_id=None, constant_by_id=None,
                     alias_by_id=None, seen=None, msg_sender_alias_ids=None):
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
        type_desc = ((type_name.get("typeDescriptions") or {}).get(
            "typeString") or (cast_expr.get("typeDescriptions") or {}).get(
                "typeString") or "")
        target = type_name.get("name") or type_desc or cast_expr.get("name")
        if target in ("address", "address payable", "payable", "uint",
                      "uint256", "bool") and len(args) == 1:
            return _expr_coord_name(args[0], state_by_id, constant_by_id,
                                    alias_by_id, seen, msg_sender_alias_ids)
    if expr.get("nodeType") == "FunctionCall" and not expr.get("arguments"):
        call_expr = expr.get("expression") or {}
        ref = call_expr.get("referencedDeclaration")
        if isinstance(ref, int) and ref in (msg_sender_alias_ids or set()):
            return "msg.sender"
    if expr.get("nodeType") == "Identifier" and expr.get("name"):
        ref = expr.get("referencedDeclaration")
        if alias_by_id and ref in alias_by_id:
            if ref in seen:
                return None
            seen.add(ref)
            return _expr_coord_name(alias_by_id[ref], state_by_id,
                                    constant_by_id, alias_by_id, seen,
                                    msg_sender_alias_ids)
        if constant_by_id and ref in constant_by_id:
            return _expr_coord_name(
                constant_by_id[ref], state_by_id, constant_by_id,
                alias_by_id, seen, msg_sender_alias_ids)
        if state_by_id and ref in state_by_id:
            return "state." + state_by_id[ref]
        return expr["name"]
    if expr.get("nodeType") == "MemberAccess" and expr.get("memberName"):
        base = expr.get("expression") or {}
        base_name = _expr_coord_name(
            base, state_by_id, constant_by_id, alias_by_id, seen,
            msg_sender_alias_ids)
        if base_name:
            return f"{base_name}.{expr['memberName']}"
    if expr.get("nodeType") == "IndexAccess":
        base_name = _expr_coord_name(
            expr.get("baseExpression"), state_by_id, constant_by_id,
            alias_by_id, seen, msg_sender_alias_ids)
        key_name = _expr_coord_name(
            expr.get("indexExpression"), state_by_id, constant_by_id,
            alias_by_id, seen, msg_sender_alias_ids)
        if base_name and key_name:
            return f"{base_name}[{key_name}]"
    return None


def _state_slot_coord_parts(coord):
    """Parse ``state.m[k].field[j]`` into (``m.field``, (``k``, ``j``))."""
    if not isinstance(coord, str) or not coord.startswith("state."):
        return None
    rest = coord[len("state."):]
    m = re.match(r"[A-Za-z_$][A-Za-z0-9_$]*", rest)
    if not m:
        return None
    name = m.group(0)
    fields = []
    keys = []
    i = len(name)
    while i < len(rest):
        if rest[i] == ".":
            m = re.match(r"\.[A-Za-z_$][A-Za-z0-9_$]*", rest[i:])
            if not m:
                return None
            fields.append(m.group(0)[1:])
            i += len(m.group(0))
            continue
        if rest[i] == "[":
            j = rest.find("]", i + 1)
            if j < 0:
                return None
            key = rest[i + 1:j]
            if not key:
                return None
            keys.append(key)
            i = j + 1
            continue
        return None
    return ".".join([name] + fields), tuple(keys)


def _index_access_chain(node, state_by_id=None, constant_by_id=None,
                        alias_by_id=None, msg_sender_alias_ids=None):
    keys = []
    cur = node
    while isinstance(cur, dict) and cur.get("nodeType") == "IndexAccess":
        key = _expr_coord_name(
            cur.get("indexExpression"), state_by_id, constant_by_id,
            alias_by_id, msg_sender_alias_ids=msg_sender_alias_ids)
        if key is None:
            return None
        keys.append(key)
        cur = cur.get("baseExpression")
    if isinstance(cur, dict) and cur.get("nodeType") == "Identifier":
        ref = cur.get("referencedDeclaration")
        if isinstance(ref, int):
            return ref, tuple(reversed(keys))
    return None


def _struct_member_slot_chain(node, state_by_id=None, constant_by_id=None,
                              alias_by_id=None, msg_sender_alias_ids=None):
    tail = []
    cur = node
    while isinstance(cur, dict) and cur.get("nodeType") == "MemberAccess":
        member = cur.get("memberName")
        if not member:
            return None
        tail.append(member)
        cur = cur.get("expression")
    if not tail or not isinstance(cur, dict) or cur.get("nodeType") != "IndexAccess":
        return None
    base_ty = ((cur.get("typeDescriptions") or {}).get("typeString") or "")
    if "struct " not in base_ty:
        return None
    got = _index_access_chain(cur, state_by_id, constant_by_id, alias_by_id,
                              msg_sender_alias_ids)
    if got is None:
        return None
    ref, keys = got
    return ref, keys, "." + ".".join(reversed(tail))


def unit_mapping_slot_accesses(
        ast_path, contract, unit, arity=None, declaration_id=None,
        access_mode="all"):
    """Return concrete parameter-keyed mapping slots reached by a unit.

    ``unit_state_dependencies`` says which mappings are in the callable
    closure. This stronger fact preserves the key expression chain solc
    resolved in source, e.g. ``_balances[maker][app][strategyHash][token]``.
    ``access_mode`` is ``"all"`` for oracle use and ``"read"`` for region
    coordinates.  A plain assignment LHS such as ``m[k] = v`` is a post-state
    write, not an entry-state condition that can help separate complete paths.
    ``None`` means the AST question could not be answered.
    """
    if access_mode not in ("all", "read"):
        raise ValueError("access_mode must be 'all' or 'read'")
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
    for declaration in _state_declarations(nodes):
        state_by_id[declaration["id"]] = declaration["name"]
        if declaration.get("constant"):
            constant_by_id[declaration["id"]] = declaration.get("value")
    for owner in nodes:
        for declaration in owner.get("nodes", []) or []:
            kind = declaration.get("kind", "function")
            declaration_name = declaration.get("name") or (
                kind if kind in ("fallback", "receive") else "")
            if (declaration.get("nodeType") == "FunctionDefinition"
                    and declaration_name == unit
                    and declaration.get("body") is not None):
                params = ((declaration.get("parameters") or {}).get("parameters") or [])
                if declaration_id is not None:
                    if declaration.get("id") == declaration_id:
                        targets.append(declaration)
                elif arity is None or len(params) == arity:
                    targets.append(declaration)
    if not targets:
        getter = _public_state_getter(nodes, unit, arity, declaration_id)
        if getter is None:
            return None, [
                f"slot-access walk unavailable: no implemented function "
                f"named {unit!r}" +
                (f" with declaration id {declaration_id}" if declaration_id is not None else
                 ("" if arity is None else f" with arity {arity}")) +
                f" was found in contract {contract!r} or its linearized bases"
            ]
        keys = tuple(_public_getter_key_names(getter))
        evidence = [
            f"state.{getter['name']}" +
            "".join(f"[{key}]" for key in keys) +
            f" slot-access distance 0: public state getter "
            f"{contract}.{getter['name']}#{getter.get('id')}"
        ]
        return ([(getter["name"], keys)] if keys else []), evidence

    callables = {
        node_id: node
        for node_id, node in by_id.items()
        if node.get("nodeType") in ("FunctionDefinition",
                                    "ModifierDefinition") and node.get("body") is not None
    }
    msg_sender_alias_ids = set()

    def direct_msg_sender(expr):
        return (isinstance(expr, dict)
                and expr.get("nodeType") == "MemberAccess"
                and expr.get("memberName") == "sender"
                and isinstance(expr.get("expression"), dict)
                and expr["expression"].get("nodeType") == "Identifier"
                and expr["expression"].get("name") == "msg")

    def single_return_expr(fn):
        body = fn.get("body") or {}
        statements = body.get("statements") or []
        if len(statements) != 1:
            return None
        stmt = statements[0]
        if not isinstance(stmt, dict) or stmt.get("nodeType") != "Return":
            return None
        return stmt.get("expression")

    for ref, fn in callables.items():
        if fn.get("nodeType") != "FunctionDefinition":
            continue
        if ((fn.get("parameters") or {}).get("parameters") or []):
            continue
        rets = (fn.get("returnParameters") or {}).get("parameters") or []
        if len(rets) != 1:
            continue
        rty = ((rets[0].get("typeDescriptions") or {}).get("typeString")
               or "")
        if rty.replace(" payable", "") != "address":
            continue
        if direct_msg_sender(single_return_expr(fn)):
            msg_sender_alias_ids.add(ref)

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

        def call_aliases(callee, arguments, receiver=None):
            formals = ((callee.get("parameters") or {}).get("parameters")
                       or [])
            actuals = arguments or []
            if receiver is not None and len(formals) == len(actuals) + 1:
                actuals = [receiver] + list(actuals)
            if len(formals) != len(actuals):
                return {}
            out = dict(alias_by_id)
            for formal, actual in zip(formals, actuals):
                ref = declaration_ref(formal)
                if ref is not None:
                    name = _expr_coord_name(
                        actual, state_by_id, constant_by_id, alias_by_id,
                        msg_sender_alias_ids=msg_sender_alias_ids)
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

        def call_receiver(value):
            expr = value.get("expression") or {}
            while (isinstance(expr, dict)
                   and expr.get("nodeType") == "FunctionCallOptions"):
                expr = expr.get("expression") or {}
            if (isinstance(expr, dict)
                    and expr.get("nodeType") == "MemberAccess"):
                return expr.get("expression")
            return None

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
                                init, state_by_id, constant_by_id,
                                alias_by_id,
                                msg_sender_alias_ids=msg_sender_alias_ids)
                            if name is not None:
                                alias_by_id[ref] = name
                    return
                if value.get("nodeType") == "IndexAccess":
                    slot_name = _expr_coord_name(
                        value, state_by_id, constant_by_id, alias_by_id,
                        msg_sender_alias_ids=msg_sender_alias_ids)
                    slot_parts = _state_slot_coord_parts(slot_name)
                    if slot_parts:
                        name, keys = slot_parts
                        candidate = (depth, tuple(chain), value.get("src") or "")
                        old = found.get((name, keys))
                        if old is None or candidate < old:
                            found[(name, keys)] = candidate
                        scan(value.get("indexExpression"))
                        return
                    chain_got = _index_access_chain(
                        value, state_by_id, constant_by_id, alias_by_id,
                        msg_sender_alias_ids)
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
                if value.get("nodeType") == "MemberAccess":
                    member_got = _struct_member_slot_chain(
                        value, state_by_id, constant_by_id, alias_by_id,
                        msg_sender_alias_ids)
                    if member_got:
                        ref, keys, tail = member_got
                        if ref in state_by_id:
                            name = state_by_id[ref] + tail
                            candidate = (depth, tuple(chain), value.get("src") or "")
                            old = found.get((name, keys))
                            if old is None or candidate < old:
                                found[(name, keys)] = candidate
                        return
                if value.get("nodeType") == "Assignment":
                    lhs = value.get("leftHandSide")
                    rhs = value.get("rightHandSide")
                    if access_mode == "read" and value.get("operator") == "=":
                        scan(rhs)
                    else:
                        scan(lhs)
                        scan(rhs)
                    ref = identifier_ref(value.get("leftHandSide"))
                    if ref is not None:
                        if value.get("operator") == "=":
                            name = _expr_coord_name(
                                value.get("rightHandSide"), state_by_id,
                                constant_by_id, alias_by_id,
                                msg_sender_alias_ids=msg_sender_alias_ids)
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
                            callables[ref], value.get("arguments") or [],
                            receiver=call_receiver(value))))
                    scan(value.get("arguments") or [])
                    return
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
