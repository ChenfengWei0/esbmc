#!/usr/bin/env python3
"""T0 -- the corpus admission gate: how many scalars can a test read after a call?

If a contract exposes NO readable scalar, no post-vs-pre assertion can be built
over it, so its deliverable ceiling is ZERO and running the pipeline on it cannot
produce anything however good the paths or the regions are. That number should
have been computed before any sweep.

TWO COLUMNS, because they are different surfaces and the difference is the whole
finding on aqua:

  SLOTS   mutable state variables of scalar type that have a storage slot.
          This is what the pipeline reads TODAY -- the emitter works from solc's
          storage layout via vm.load/vm.store, so a `private` scalar counts (
          farming's readable slots are `_distributor`, `_owner`, `_totalSupply`,
          all non-public). `constant` and `immutable` are EXCLUDED: solc gives
          them no slot, no test can set one, and a rung over one is a
          compile-time tautology rather than an oracle -- the emitter already
          refuses them for exactly that reason.

  GETTERS public/external view or pure functions returning ONE scalar, plus the
          auto-getters of public scalar state variables. This is what a test
          could assert without cheatcodes. A contract can have 0 SLOTS and >0
          GETTERS -- aqua does, because its only state is a mapping and its
          getters return values read out of it. Those getters are the reason
          mapping support is a deliverable question and not a nicety.

Scalar means: address, bool, uintN, intN, bytesN, an enum, or a user-defined
value type. NOT string, NOT bytes, NOT a mapping, array or struct. Every state
variable is printed with the type kind it was classified as, so a
misclassification is visible rather than folded into a total.

Reads the solc AST only. Runs no esbmc, compiles nothing, touches no benchmark.
"""
import json
import sys
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))
from collect import BENCHES  # noqa: E402

INPUTS = REPO / "notes/coverage/inputs"

ELEMENTARY_SCALAR_PREFIX = ("uint", "int", "bytes")
ELEMENTARY_SCALAR_EXACT = {"address", "bool"}
NOT_SCALAR_EXACT = {"string", "bytes"}      # `bytes` is dynamic; `bytes32` is not


def is_scalar_typename(tn, udvt_ids, enum_ids):
    """(bool, kind-label). Label is printed, so a wrong call is visible."""
    if not isinstance(tn, dict):
        return False, "?"
    nt = tn.get("nodeType")
    if nt == "ElementaryTypeName":
        name = (tn.get("name") or "").strip()
        if name in NOT_SCALAR_EXACT:
            return False, f"elementary/dynamic({name})"
        if name in ELEMENTARY_SCALAR_EXACT:
            return True, f"elementary({name})"
        if name.startswith(ELEMENTARY_SCALAR_PREFIX):
            return True, f"elementary({name})"
        return False, f"elementary/other({name})"
    if nt == "UserDefinedTypeName":
        ref = tn.get("referencedDeclaration")
        if ref in udvt_ids:
            return True, "user-defined value type"
        if ref in enum_ids:
            return True, "enum"
        return False, "user-defined (struct/contract/interface)"
    if nt == "Mapping":
        return False, "mapping"
    if nt == "ArrayTypeName":
        return False, "array"
    return False, nt or "?"


def load_ast(path):
    txt = Path(path).read_text()
    return json.loads(txt[txt.index("{"):])


def index_nodes(ast):
    contracts, udvt, enums = {}, set(), set()

    def walk(n):
        if isinstance(n, dict):
            nt = n.get("nodeType")
            if nt == "ContractDefinition" and n.get("id") is not None:
                contracts[n["id"]] = n
            elif nt == "UserDefinedValueTypeDefinition" and n.get("id"):
                udvt.add(n["id"])
            elif nt == "EnumDefinition" and n.get("id"):
                enums.add(n["id"])
            for v in n.values():
                if isinstance(v, (list, dict)):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(ast)
    return contracts, udvt, enums


def surface(ast, primary):
    contracts, udvt, enums = index_nodes(ast)
    target = next((c for c in contracts.values()
                   if c.get("name") == primary), None)
    if target is None:
        return None
    chain = target.get("linearizedBaseContracts") or [target.get("id")]

    slots, dropped, getters, nonscalar, units = [], [], [], [], []
    for cid in reversed(chain):          # most-derived last, so it overrides
        node = contracts.get(cid)
        if node is None:
            continue
        cname = node.get("name")
        for m in node.get("nodes", []) or []:
            if not isinstance(m, dict):
                continue
            nt = m.get("nodeType")
            if nt == "VariableDeclaration" and m.get("stateVariable"):
                ok, kind = is_scalar_typename(m.get("typeName"), udvt, enums)
                mu = m.get("mutability") or "mutable"
                nm = f"{cname}.{m.get('name')}"
                if not ok:
                    nonscalar.append((nm, kind))
                elif mu in ("constant", "immutable"):
                    dropped.append((nm, f"{kind}, {mu}"))
                else:
                    slots.append((nm, kind))
                    if m.get("visibility") == "public":
                        getters.append((f"{nm}()", "auto-getter"))
            elif nt == "FunctionDefinition":
                if m.get("visibility") not in ("public", "external"):
                    continue
                if not m.get("name"):
                    continue
                rets = ((m.get("returnParameters") or {}).get("parameters")
                        or [])
                # ---- A UNIT IS AN ORACLE SITE EVEN WITH NO STATE TO READ ----
                #
                # R0 -- does this call revert, and with which reason -- needs no
                # readable storage at all, and `vm.expectRevert` expresses it.
                # EVERY unit therefore has at least one assertable observable,
                # so no contract's ceiling is zero because of storage.
                units.append((f"{cname}.{m['name']}()",
                              m.get("stateMutability") or "nonpayable",
                              len(rets)))
                # RETURN VALUES are a second source, also independent of
                # storage. Counted per RETURNED SCALAR, not per one-scalar
                # function: the first version skipped anything returning two
                # values, which threw away aqua's `rawBalances` -> (uint248,
                # uint8) -- both assertable -- and made the contract look like
                # it had no observable at all.
                if m.get("stateMutability") in ("view", "pure"):
                    for r in rets:
                        ok, kind = is_scalar_typename(r.get("typeName"),
                                                      udvt, enums)
                        if ok:
                            getters.append((f"{cname}.{m['name']}()", kind))
    return slots, dropped, getters, nonscalar, units


def main():
    verbose = "--verbose" in sys.argv
    rows = []
    print("# T0 -- oracle surface per benchmark contract\n")
    for bench, (flat_rel, primary, _solc, _proj) in sorted(BENCHES.items()):
        ast_path = INPUTS / (flat_rel + ".solast")
        if not ast_path.exists():
            rows.append((bench, primary, None, None, "NO-AST"))
            continue
        got = surface(load_ast(ast_path), primary)
        if got is None:
            rows.append((bench, primary, None, None, None, "CONTRACT-NOT-IN-AST"))
            continue
        slots, dropped, getters, nonscalar, units = got
        # THE CEILING IS NEVER ZERO WHILE A UNIT EXISTS. R0 -- reverts or does
        # not, and with which reason -- is assertable with no readable storage
        # whatsoever. The first version of this gate reported "deliverable
        # ceiling 0" for four contracts on the strength of the SLOT column
        # alone, which is one of three oracle sources reported as if it were all
        # of them.
        verdict = ("state+return+exit" if slots and getters
                   else ("state+exit" if slots
                         else ("return+exit" if getters
                               else ("exit-only" if units else "NO UNIT"))))
        rows.append((bench, primary, len(units), len(slots), len(getters),
                     verdict))
        if verbose:
            print(f"## {bench} :: {primary}")
            for label, items in (("SLOT (post-vs-pre, readable today)", slots),
                                 ("dropped: constant/immutable", dropped),
                                 ("RETURNED scalar (view/pure)", getters),
                                 ("state var, NOT scalar", nonscalar)):
                print(f"   {label}: {len(items)}")
                for n, k in items:
                    print(f"       {n}   [{k}]")
            print(f"   UNITS (each has an exit-kind observable): {len(units)}")
            print()

    print(f"| {'benchmark':<28} | {'contract':<15} | units | slots | returns | "
          f"oracle sources |")
    print("|" + "-" * 30 + "|" + "-" * 17 + "|-------|-------|---------|"
          + "-" * 18 + "|")
    for b, c, u, s, g, v in rows:
        f = (lambda x: "?" if x is None else str(x))
        print(f"| {b:<28} | {c:<15} | {f(u):>5} | {f(s):>5} | {f(g):>7} | "
              f"{v} |")

    print("\nTHREE oracle sources, and only the first needs readable storage:")
    print("  state   post-vs-pre over a scalar slot        -- needs `slots` > 0")
    print("  return  assert on a returned scalar           -- needs `returns` > 0")
    print("  exit    reverts / does not, and which reason  -- needs NOTHING,")
    print("          so every contract with a unit has at least this one.")
    noslot = [r for r in rows if r[3] == 0]
    print(f"\n{len(noslot)} of {len(rows)} contract(s) have zero readable "
          f"scalar SLOTS. That bounds the post-vs-pre ladder on them to nothing "
          f"-- it does NOT bound the deliverable to nothing, and reporting it "
          f"that way was wrong.")
    for b, c, u, _s, g, _v in noslot:
        print(f"    {b} :: {c}   units={u}, returned scalars={g} "
              f"-> exit-kind on all {u}, return-value on {g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
