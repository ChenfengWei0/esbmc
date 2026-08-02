#!/usr/bin/env python3
"""WHICH UNIT CAN CARRY A post-vs-pre ORACLE? -- one level finer than T0.

T0 (`oracle_surface.py`) answers it per CONTRACT: how many readable scalar slots
are there at all. That bounded aqua's post-vs-pre ladder to nothing. It does NOT
identify a unit that can actually carry such an oracle, and that is the question
the deliverable is stuck on:

  the stage-4 PUT driver emits a post-vs-pre assertion only for a state variable
  solc gives a storage slot, read through vm.load. So a PUT with a state oracle
  needs a unit that WRITES such a slot. Every certified region in the corpus so
  far sat on a unit that writes nothing -- three of them are no-argument getters,
  whose `post == pre` rung is a compile-time tautology rather than an oracle.

So this prints, per contract that has readable slots at all, EVERY unit and the
readable slots it writes. The intersection of "writes a readable slot" with
"has a witnessed path" is the candidate set for the first corpus deliverable.

TWO KINDS OF WRITE, KEPT APART, because the pipeline treats them the same and a
reader must not have to guess which one a row is:

  direct    the unit's own body assigns the slot;
  via       an INTERNAL function the unit calls (transitively) assigns it.
            `--solidity-path-coverage` physically expands internal calls into
            the caller, so such a write is inside the unit's own path -- it is
            just as usable, and invisible if you only read the unit's body.

WHAT IS AND IS NOT DETECTED, stated rather than left to be discovered:
  * detected: `x = e`, `x += e` (any compound), `x++`, `--x`, and `delete x`,
    where `x` resolves to a readable scalar slot's declaration id.
  * NOT detected: a write through a storage pointer alias, assembly `sstore`,
    and a write in a callee reached through a function-type variable. Each would
    be a FALSE NEGATIVE -- this script would say a unit writes nothing when it
    does -- so a unit it clears is not proven inert. It is a CANDIDATE FINDER,
    not a proof of absence, and the rows it prints are the ones worth spending a
    certification run on.

The scalar classification is imported from oracle_surface rather than repeated:
"what counts as a readable slot" is one fact and lives in one place.

Reads the solc AST only. Runs no esbmc, compiles nothing.
"""
import sys
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))
from collect import BENCHES  # noqa: E402
from oracle_surface import (  # noqa: E402
    INPUTS,
    index_nodes,
    is_scalar_typename,
    load_ast,
)

COMPOUND = {"=", "+=", "-=", "*=", "/=", "%=", "|=", "&=", "^=", "<<=", ">>="}
INCDEC = {"++", "--", "delete"}


def walk(node, fn):
    """Every dict node of the subtree, in no particular order."""
    if isinstance(node, dict):
        fn(node)
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk(v, fn)
    elif isinstance(node, list):
        for v in node:
            walk(v, fn)


def lhs_target_id(expr):
    """The declaration id an assignment target resolves to, or None.

    Only a bare Identifier is followed. An IndexAccess / MemberAccess target is
    a mapping, array or struct field -- not a readable scalar slot -- so
    returning None there is correct rather than a gap.
    """
    if not isinstance(expr, dict):
        return None
    if expr.get("nodeType") == "Identifier":
        return expr.get("referencedDeclaration")
    return None


def direct_writes(fn_node, slot_ids):
    """Slot ids this function's own body assigns."""
    found = set()

    def visit(n):
        nt = n.get("nodeType")
        if nt == "Assignment" and n.get("operator") in COMPOUND:
            t = lhs_target_id(n.get("leftHandSide"))
            if t in slot_ids:
                found.add(t)
        elif nt == "UnaryOperation" and n.get("operator") in INCDEC:
            t = lhs_target_id(n.get("subExpression"))
            if t in slot_ids:
                found.add(t)

    body = fn_node.get("body")
    if body:
        walk(body, visit)
    # A modifier body also runs as part of the unit; its writes are the unit's.
    for mi in fn_node.get("modifiers") or []:
        walk(mi, visit)
    return found


def direct_callees(fn_node, fn_ids):
    """Declaration ids of functions this one calls by name."""
    found = set()

    def visit(n):
        if n.get("nodeType") != "FunctionCall":
            return
        callee = n.get("expression")
        if not isinstance(callee, dict):
            return
        ref = callee.get("referencedDeclaration")
        if ref in fn_ids:
            found.add(ref)

    body = fn_node.get("body")
    if body:
        walk(body, visit)
    for mi in fn_node.get("modifiers") or []:
        walk(mi, visit)
    return found


def analyse(ast, primary):
    contracts, udvt, enums = index_nodes(ast)
    target = next((c for c in contracts.values()
                   if c.get("name") == primary), None)
    if target is None:
        return None
    chain = target.get("linearizedBaseContracts") or [target.get("id")]

    slot_name = {}          # decl id -> "Contract.var"
    fns = {}                # decl id -> (contract, node)
    for cid in reversed(chain):
        node = contracts.get(cid)
        if node is None:
            continue
        cname = node.get("name")
        for m in node.get("nodes", []) or []:
            if not isinstance(m, dict):
                continue
            nt = m.get("nodeType")
            if nt == "VariableDeclaration" and m.get("stateVariable"):
                ok, _kind = is_scalar_typename(m.get("typeName"), udvt, enums)
                mu = m.get("mutability") or "mutable"
                if ok and mu not in ("constant", "immutable"):
                    slot_name[m.get("id")] = f"{cname}.{m.get('name')}"
            elif nt == "FunctionDefinition" and m.get("id") is not None:
                fns[m["id"]] = (cname, m)

    slot_ids = set(slot_name)
    fn_ids = set(fns)

    own = {fid: direct_writes(n, slot_ids) for fid, (_c, n) in fns.items()}
    calls = {fid: direct_callees(n, fn_ids) for fid, (_c, n) in fns.items()}

    # Transitive closure over the internal call graph. Bounded by |fns|
    # iterations, so a cycle terminates rather than recursing.
    reach = {fid: set(cs) for fid, cs in calls.items()}
    for _ in range(len(fns)):
        grew = False
        for fid in reach:
            add = set()
            for c in reach[fid]:
                add |= reach.get(c, set())
            if not add <= reach[fid]:
                reach[fid] |= add
                grew = True
        if not grew:
            break

    rows = []
    for fid, (cname, n) in fns.items():
        if n.get("visibility") not in ("public", "external"):
            continue
        if not n.get("name"):
            continue
        d = own[fid]
        v = set()
        for c in reach[fid]:
            v |= own.get(c, set())
        v -= d
        rows.append((f"{cname}.{n['name']}",
                     n.get("stateMutability") or "nonpayable",
                     sorted(slot_name[s] for s in d),
                     sorted(slot_name[s] for s in v)))
    rows.sort(key=lambda r: (-(len(r[2]) + len(r[3])), r[0]))
    return sorted(slot_name.values()), rows


def main():
    print("# Which UNIT can carry a post-vs-pre oracle?\n")
    any_candidate = False
    for bench, (flat_rel, primary, _solc, _proj) in sorted(BENCHES.items()):
        ast_path = INPUTS / (flat_rel + ".solast")
        if not ast_path.exists():
            print(f"## {bench} :: {primary}   NO AST\n")
            continue
        got = analyse(load_ast(ast_path), primary)
        if got is None:
            print(f"## {bench} :: {primary}   CONTRACT NOT IN AST\n")
            continue
        slots, rows = got
        print(f"## {bench} :: {primary}")
        if not slots:
            print("   NO readable scalar slot at all -- no unit here can carry "
                  "a post-vs-pre oracle, whatever it writes.\n")
            continue
        print(f"   readable scalar slots ({len(slots)}): {', '.join(slots)}")
        writers = [r for r in rows if r[2] or r[3]]
        print(f"   units that write at least one of them: {len(writers)} "
              f"of {len(rows)}")
        for name, mut, d, v in writers:
            parts = []
            if d:
                parts.append("direct=" + ",".join(d))
            if v:
                parts.append("via-internal=" + ",".join(v))
            print(f"       {name}  [{mut}]  " + "  ".join(parts))
            any_candidate = True
        if not writers:
            print("       (none -- every unit here is read-only w.r.t. the "
                  "readable slots)")
        print()

    if not any_candidate:
        print("NO CANDIDATE ANYWHERE. The post-vs-pre oracle is unreachable on "
              "this corpus and the deliverable must come from the return-value "
              "or exit-kind source instead.")
    else:
        print("A row above is a CANDIDATE, not a result: it still needs a "
              "witnessed path, a certified region and a HOLDS rung. What it "
              "rules out is spending any of those on a unit that writes "
              "nothing readable -- which is where all seven certified regions "
              "in this corpus went.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
