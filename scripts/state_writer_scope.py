#!/usr/bin/env python3
"""Derive the dispatcher SCOPE a unit needs when its guard reads state it never writes.

WHY THIS EXISTS. A getter behind an existence guard --
``require(_conduits[conduit].key != 0); return _conduits[conduit].owner`` --
is certified at ``--solidity-max-tx 1`` against a contract whose storage is
whatever the constructor left. Nothing has created a conduit, so ``key`` is 0,
the guard rejects every input, and the region the query certifies admits no
execution that walks the success path. `solidity_path_generalise` already
states this in the certify call: "a path whose guard needs an EARLIER
transaction's write is only witnessed [at max_tx>1]; certifying it at 1 tx runs
the setup transaction not at all ... the query answers VACUOUS".

The cure is the contract's OWN writer, run as the earlier transaction --
``--scope ownerOf,createConduit --solidity-max-tx 2`` -- NOT a synthesized
``vm.store``. A store writes a state the contract's own code may never produce;
the writer produces exactly the states the contract can reach, which is the
only entry state a test is entitled to assume.

WHAT THIS MODULE ANSWERS. For one target unit: which externally-callable units
of the same contract ASSIGN to the state variables the target READS. That set,
plus the target, is the scope alphabet. The answer is derived from the AST --
assignment/delete/compound-assignment sites resolved through
``referencedDeclaration`` -- so it holds for any contract and never encodes a
subject or file name.

⚠ WHAT THIS DOES NOT DO. It does not prove the writer can actually reach the
state the guard wants (the writer may have guards of its own), and it does not
order the transactions. It narrows the alphabet; the verifier still has to
witness the path. A unit for which no writer is found gets an empty list, which
callers must read as "escalation would not help", not as "the state is
unreachable".
"""

import json
import sys

_WRITE_OPERATORS = {"=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>="}
_UNARY_WRITE = {"++", "--", "delete"}


def _ast_root(ast_path):
    # solc's `--ast-compact-json` output carries a "JSON AST (compact format):"
    # banner and a `======= path =======` line before the object, so the parse
    # starts at the first brace -- the same read `solidity_ast_dependencies`
    # performs, kept identical so both see the same tree.
    try:
        with open(ast_path, "r", errors="replace") as stream:
            text = stream.read()
        return json.loads(text[text.index("{"):])
    except (OSError, ValueError):
        return None


def _walk(node):
    """Yield every dict node of an AST subtree."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _contract_nodes(ast, contract):
    """The named contract's definition plus its linearized bases, if present."""
    by_id = {}
    definitions = []
    for node in _walk(ast):
        if node.get("nodeType") == "ContractDefinition":
            by_id[node.get("id")] = node
            if node.get("name") == contract:
                definitions.append(node)
    if not definitions:
        return None
    target = definitions[-1]
    chain = []
    for base_id in target.get("linearizedBaseContracts") or [target.get("id")]:
        base = by_id.get(base_id)
        if base is not None:
            chain.append(base)
    return chain or [target]


def _state_declaration_ids(chain):
    """id -> name for every state variable declared anywhere in the chain."""
    out = {}
    for owner in chain:
        for declaration in owner.get("nodes") or []:
            if (declaration.get("nodeType") == "VariableDeclaration"
                    and declaration.get("stateVariable") and declaration.get("name")):
                out[declaration.get("id")] = declaration.get("name")
    return out


def _root_state_name(expr, state_ids):
    """The state variable a possibly-indexed/membered lvalue is rooted at."""
    node = expr
    for _ in range(32):
        if not isinstance(node, dict):
            return None
        kind = node.get("nodeType")
        if kind == "Identifier":
            return state_ids.get(node.get("referencedDeclaration"))
        if kind == "IndexAccess":
            node = node.get("baseExpression")
            continue
        if kind == "MemberAccess":
            # `_conduits[c].owner` roots at `_conduits`; `this.x` does not root
            # at a state variable and falls out through the Identifier branch.
            node = node.get("expression")
            continue
        if kind == "TupleExpression":
            components = [c for c in (node.get("components") or []) if c]
            if len(components) != 1:
                return None
            node = components[0]
            continue
        return None
    return None


def _function_definitions(chain):
    """Externally-callable function definitions of the chain, by name."""
    out = []
    for owner in chain:
        for declaration in owner.get("nodes") or []:
            if declaration.get("nodeType") != "FunctionDefinition":
                continue
            if declaration.get("kind") not in ("function", None):
                continue
            if not declaration.get("name"):
                continue
            if declaration.get("visibility") not in ("public", "external"):
                continue
            if not declaration.get("implemented", True):
                continue
            out.append(declaration)
    return out


def _storage_pointer_aliases(definition, state_ids):
    """local id -> state variable name, for `T storage p = <state expr>;`.

    ⛔ WITHOUT THIS THE ANSWER IS WRONG, NOT MERELY INCOMPLETE. Solidity's
    idiomatic writer takes a storage reference first and assigns through it:

        ConduitProperties storage conduitProperties = _conduits[conduit];
        conduitProperties.owner = initialOwner;

    The assignment's left-hand side roots at a LOCAL declaration, so a walk
    that only resolves `referencedDeclaration` to state variables sees no write
    at all. MEASURED on ReferenceConduitController: without this the derivation
    returned acceptOwnership/cancelOwnershipTransfer/transferOwnership and
    MISSED `createConduit` -- the only unit that sets `.key`, i.e. the only one
    that can satisfy `ownerOf`'s existence guard. A scope built from that
    answer would run an earlier transaction that still cannot open the path.
    """
    aliases = {}
    for node in _walk(definition):
        if (node.get("nodeType") != "VariableDeclarationStatement"
                or not node.get("initialValue")):
            continue
        declarations = [d for d in (node.get("declarations") or []) if d]
        if len(declarations) != 1:
            continue
        declaration = declarations[0]
        if declaration.get("storageLocation") != "storage":
            continue
        root = _root_state_name(node.get("initialValue"), state_ids)
        if root and declaration.get("id") is not None:
            aliases[declaration.get("id")] = root
    return aliases


def unit_state_writes(definition, state_ids):
    """State variable names this function definition assigns to.

    Writes through a `storage` pointer count as writes to the state variable
    the pointer was taken from -- see `_storage_pointer_aliases`.
    """
    aliases = _storage_pointer_aliases(definition, state_ids)
    # A local storage pointer resolves to its state root exactly as a state
    # identifier does, so the two are merged into one lookup table rather than
    # threaded as a second argument through every branch of `_root_state_name`.
    resolved = dict(state_ids)
    resolved.update(aliases)
    written = set()
    for node in _walk(definition):
        kind = node.get("nodeType")
        if kind == "Assignment" and node.get("operator") in _WRITE_OPERATORS:
            name = _root_state_name(node.get("leftHandSide"), resolved)
            if name:
                written.add(name)
        elif kind == "UnaryOperation" and node.get("operator") in _UNARY_WRITE:
            name = _root_state_name(node.get("subExpression"), resolved)
            if name:
                written.add(name)
    return written


def unit_state_reads(definition, state_ids):
    """State variable names this function definition mentions at all.

    Deliberately wider than "reads": a name that appears only on the left of an
    assignment is also returned. The caller intersects this with another
    unit's WRITE set, and a target that writes the slot itself does not need an
    earlier transaction -- that case is filtered by the caller, not here.
    """
    seen = set()
    for node in _walk(definition):
        if node.get("nodeType") == "Identifier":
            name = state_ids.get(node.get("referencedDeclaration"))
            if name:
                seen.add(name)
    return seen


def writer_scope_for_unit(ast_path, contract, unit):
    """(scope_names, evidence) -- the alphabet `unit` needs, target first.

    `scope_names` is empty when no other externally-callable unit writes any
    state the target reads: escalating the transaction bound would then change
    nothing, and the caller must not spend a run on it.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return [], [f"scope derivation unavailable: AST {ast_path!r} unreadable"]
    chain = _contract_nodes(ast, contract)
    if chain is None:
        return [], [f"scope derivation unavailable: contract {contract!r} absent from AST"]
    state_ids = _state_declaration_ids(chain)
    if not state_ids:
        return [], ["scope derivation: contract declares no state variable"]
    definitions = _function_definitions(chain)
    targets = [d for d in definitions if d.get("name") == unit]
    if not targets:
        return [], [f"scope derivation: unit {unit!r} is not an externally-callable function"]

    read = set()
    for target in targets:
        read |= unit_state_reads(target, state_ids)
    self_written = set()
    for target in targets:
        self_written |= unit_state_writes(target, state_ids)
    # State the target both reads and writes needs no earlier transaction: the
    # target's own execution establishes it within the one transaction.
    wanted = read - self_written
    if not wanted:
        return [], [f"scope derivation: {unit} writes every state variable it reads"]

    writers = {}
    for definition in definitions:
        name = definition.get("name")
        if name == unit:
            continue
        written = unit_state_writes(definition, state_ids)
        overlap = written & wanted
        if overlap:
            writers.setdefault(name, set()).update(overlap)
    if not writers:
        return [], [
            f"scope derivation: no other unit writes the state {unit} reads "
            f"({', '.join(sorted(wanted))})"
        ]
    ordered = sorted(writers, key=lambda n: (-len(writers[n]), n))
    evidence = [
        f"{name} writes " + ", ".join(sorted(writers[name])) for name in ordered
    ]
    return [unit] + ordered, evidence



# ---- SETUP SCOPE FOR A TRANSACTION BUDGET T > 1 ----------------------------
#
# `writer_scope_for_unit` above answers a narrower question (which units a
# bounded-holds RETRY needs) and keeps its answer unchanged, because the
# published T=1 runs depend on it.  This function answers the question a run at
# T > 1 asks for every unit: which externally-callable units may run BEFORE the
# target so that the state the target reads can differ from the constructor's.
#
# It differs from `writer_scope_for_unit` in three ways, each a miss there:
#
# 1. State the target reads AND writes is still wanted.  `withdraw` both reads
#    and writes `deposits`; its guard reads the value an earlier `deposit`
#    left, which its own write cannot supply.  (FeeVault: the old answer was
#    {withdraw, setDiscount}, missing `deposit`.)
# 2. Reads and writes are collected over the CALL CLOSURE: modifiers, internal
#    and private functions, `super` calls and overrides with the same name in
#    the linearized chain.  `transfer` that delegates to `_transfer` reads and
#    writes nothing in its own body.
# 3. A state variable handed to a function through a `storage` parameter
#    (library `using for`, internal helpers) counts as read and written, and
#    `.push()` / `.pop()` on a state array count as writes.
#
# A read is any mention of a state variable other than as the root of the
# left-hand side of a plain `=` or the operand of `delete`.

def _callables_by_id(ast):
    """id -> FunctionDefinition/ModifierDefinition node, over the whole AST."""
    out = {}
    for node in _walk(ast):
        if node.get("nodeType") in ("FunctionDefinition", "ModifierDefinition"):
            if node.get("id") is not None:
                out[node.get("id")] = node
    return out


def _chain_callables_by_name(chain):
    """name -> [definitions] for every function/modifier of the chain."""
    out = {}
    for owner in chain:
        for declaration in owner.get("nodes") or []:
            if declaration.get("nodeType") in ("FunctionDefinition", "ModifierDefinition"):
                name = declaration.get("name")
                if name:
                    out.setdefault(name, []).append(declaration)
    return out


def _referenced_callables(definition, callables, chain_by_name):
    """Definitions `definition` may enter: modifiers, called functions, overrides."""
    found = []
    for node in _walk(definition):
        ref = node.get("referencedDeclaration")
        if ref is None or ref == definition.get("id"):
            continue
        target = callables.get(ref)
        if target is None:
            continue
        found.append(target)
        # A virtual function called from a base runs the most-derived override;
        # every same-named definition of the chain is kept (a superset).
        for same in chain_by_name.get(target.get("name") or "", []):
            if same is not target:
                found.append(same)
    return found


def _storage_parameter_positions(definition):
    params = ((definition.get("parameters") or {}).get("parameters")) or []
    return [i for i, p in enumerate(params) if p.get("storageLocation") == "storage"]


def _local_reads_writes(definition, state_ids, callables):
    """(reads, writes) of state variables in one definition's own body."""
    aliases = _storage_pointer_aliases(definition, state_ids)
    resolved = dict(state_ids)
    resolved.update(aliases)
    writes = set(unit_state_writes(definition, state_ids))
    pure_lhs = set()  # AST ids of Identifiers that are only written, not read

    def lhs_root_identifier(expr):
        node = expr
        for _ in range(32):
            if not isinstance(node, dict):
                return None
            kind = node.get("nodeType")
            if kind == "Identifier":
                return node
            if kind == "IndexAccess":
                node = node.get("baseExpression")
            elif kind == "MemberAccess":
                node = node.get("expression")
            elif kind == "TupleExpression":
                comps = [c for c in (node.get("components") or []) if c]
                if len(comps) != 1:
                    return None
                node = comps[0]
            else:
                return None
        return None

    for node in _walk(definition):
        kind = node.get("nodeType")
        if kind == "Assignment" and node.get("operator") == "=":
            ident = lhs_root_identifier(node.get("leftHandSide"))
            if ident is not None:
                pure_lhs.add(ident.get("id"))
        elif kind == "UnaryOperation" and node.get("operator") == "delete":
            ident = lhs_root_identifier(node.get("subExpression"))
            if ident is not None:
                pure_lhs.add(ident.get("id"))
        elif kind == "FunctionCall":
            callee = node.get("expression") or {}
            if callee.get("nodeType") == "MemberAccess":
                base = callee.get("expression")
                root = _root_state_name(base, resolved)
                if root and callee.get("memberName") in ("push", "pop"):
                    writes.add(root)
                target = callables.get(callee.get("referencedDeclaration"))
                # `x.f(args)` with `using L for T`: x is the first parameter.
                if root and target is not None and 0 in _storage_parameter_positions(target):
                    writes.add(root)
            target = callables.get(callee.get("referencedDeclaration"))
            if target is not None:
                positions = _storage_parameter_positions(target)
                offset = 1 if (callee.get("nodeType") == "MemberAccess"
                               and target.get("kind") == "function"
                               and _bound_receiver(callee, callables)) else 0
                args = node.get("arguments") or []
                for pos in positions:
                    idx = pos - offset
                    if 0 <= idx < len(args):
                        root = _root_state_name(args[idx], resolved)
                        if root:
                            writes.add(root)
    reads = set()
    for node in _walk(definition):
        if node.get("nodeType") == "Identifier" and node.get("id") not in pure_lhs:
            name = resolved.get(node.get("referencedDeclaration"))
            if name:
                reads.add(name)
    return reads, writes


def _bound_receiver(member_access, callables):
    """True when `a.f(...)` passes `a` as f's first argument (`using for`)."""
    base = member_access.get("expression") or {}
    # `Lib.f(...)` names the library itself: no bound receiver.
    if base.get("nodeType") == "Identifier":
        type_string = ((base.get("typeDescriptions") or {}).get("typeString") or "")
        if type_string.startswith("type(library ") or type_string.startswith("type(contract "):
            return False
    if base.get("nodeType") == "Identifier" and base.get("name") == "super":
        return False
    return True


def _closure_reads_writes(definition, state_ids, callables, chain_by_name, memo):
    """(reads, writes) over everything `definition` may execute."""
    key = definition.get("id")
    if key in memo:
        return memo[key]
    memo[key] = (set(), set())  # recursion guard
    reads, writes = _local_reads_writes(definition, state_ids, callables)
    reads, writes = set(reads), set(writes)
    stack = list(_referenced_callables(definition, callables, chain_by_name))
    seen = {key}
    while stack:
        current = stack.pop()
        cid = current.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        r, w = _local_reads_writes(current, state_ids, callables)
        reads |= r
        writes |= w
        stack.extend(_referenced_callables(current, callables, chain_by_name))
    memo[key] = (reads, writes)
    return reads, writes


def setup_scope_for_unit(ast_path, contract, unit):
    """(scope_names, evidence) for a run at transaction budget T > 1.

    scope_names = [unit] + every other externally-callable unit whose call
    closure writes a state variable that the target's call closure reads.
    Returns [unit] alone when no such writer exists.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return [], [f"setup scope unavailable: AST {ast_path!r} unreadable"]
    chain = _contract_nodes(ast, contract)
    if chain is None:
        return [], [f"setup scope unavailable: contract {contract!r} absent from AST"]
    state_ids = _state_declaration_ids(chain)
    definitions = _function_definitions(chain)
    targets = [d for d in definitions if d.get("name") == unit]
    if not targets:
        return [], [f"setup scope: unit {unit!r} is not an externally-callable function"]
    if not state_ids:
        return [unit], ["setup scope: contract declares no state variable"]
    callables = _callables_by_id(ast)
    chain_by_name = _chain_callables_by_name(chain)
    memo = {}
    read = set()
    for target in targets:
        read |= _closure_reads_writes(target, state_ids, callables, chain_by_name, memo)[0]
    if not read:
        return [unit], [f"setup scope: {unit} reads no state variable"]
    writers = {}
    for definition in definitions:
        name = definition.get("name")
        if name == unit:
            continue
        if definition.get("stateMutability") in ("view", "pure"):
            continue
        written = _closure_reads_writes(definition, state_ids, callables, chain_by_name, memo)[1]
        overlap = written & read
        if overlap:
            writers.setdefault(name, set()).update(overlap)
    ordered = sorted(writers, key=lambda n: (-len(writers[n]), n))
    evidence = [f"{unit} reads " + ", ".join(sorted(read))]
    evidence += [f"{name} writes " + ", ".join(sorted(writers[name])) for name in ordered]
    return [unit] + ordered, evidence


def nonview_scope_for_unit(ast_path, contract, unit):
    """(scope_names, evidence) for a run at transaction budget T > 1.

    scope_names = [unit] + every externally-callable entry of the contract whose
    most-derived definition is not `view`/`pure` (fallback and receive included,
    under ESBMC's names "fallback"/"receive").  Dropping view/pure entries from
    the dispatcher cannot change the reachable entry states: under solc >= 0.8 a
    view/pure body cannot write storage, emit, transfer value or call a
    state-changing function, and its external calls compile to STATICCALL.  The
    focus list matches by NAME, so a name is kept when ANY of its overloads is
    state-changing (conservative), and the target is kept even when it is view.
    """
    ast = _ast_root(ast_path)
    if ast is None:
        return [], [f"nonview scope unavailable: AST {ast_path!r} unreadable"]
    chain = _contract_nodes(ast, contract)
    if chain is None:
        return [], [f"nonview scope unavailable: contract {contract!r} absent from AST"]
    by_signature = {}
    for owner in chain:  # most-derived first: the first definition seen wins
        for declaration in owner.get("nodes") or []:
            if declaration.get("nodeType") != "FunctionDefinition":
                continue
            kind = declaration.get("kind") or "function"
            if kind == "constructor":
                continue
            if kind in ("fallback", "receive"):
                name = kind
            else:
                name = declaration.get("name")
                if not name or declaration.get("visibility") not in ("public", "external"):
                    continue
            params = tuple(str((p.get("typeDescriptions") or {}).get("typeString"))
                           for p in (declaration.get("parameters") or {}).get("parameters") or [])
            by_signature.setdefault((name, params), declaration.get("stateMutability"))
    keep, dropped = set(), set()
    for (name, _params), mutability in by_signature.items():
        (dropped if mutability in ("view", "pure") else keep).add(name)
    dropped -= keep
    names = [unit] + sorted(keep - {unit})
    evidence = [f"nonview scope: {len(keep - {unit})} state-changing entries kept, "
                f"{len(dropped - {unit})} view/pure entries dropped: "
                + ",".join(sorted(dropped - {unit}))]
    return names, evidence


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ast", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--setup", action="store_true",
                    help="use setup_scope_for_unit (runs at T > 1)")
    args = ap.parse_args()
    fn = setup_scope_for_unit if args.setup else writer_scope_for_unit
    scope, evidence = fn(args.ast, args.contract, args.unit)
    if args.json:
        print(json.dumps({"scope": scope, "evidence": evidence}, indent=2))
        return 0
    print("scope: " + (",".join(scope) if scope else "(none -- escalation would not help)"))
    for line in evidence:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
