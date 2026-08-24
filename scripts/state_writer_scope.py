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


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ast", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    scope, evidence = writer_scope_for_unit(args.ast, args.contract, args.unit)
    if args.json:
        print(json.dumps({"scope": scope, "evidence": evidence}, indent=2))
        return 0
    print("scope: " + (",".join(scope) if scope else "(none -- escalation would not help)"))
    for line in evidence:
        print("  " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
