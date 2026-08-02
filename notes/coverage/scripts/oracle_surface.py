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


# ---- WHAT THE EMITTER CAN TURN INTO A FUZZ PARAMETER, TODAY ----
#
# Mirrors `lift_kind` in scripts/solidity_path_put.py, which accepts exactly
# `address` / `address payable` / `uintN` and returns None for everything else,
# whereupon the driver keeps the argument PINNED at the counterexample's literal
# and says so on the emitted test.
#
# ⚠ ONE FACT, TWO READERS -- this is a copy and copies drift. It is here rather
# than imported because that module runs esbmc at import-free cost but lives in a
# different tree and importing it would drag its argparse surface into a script
# that must stay AST-only. If `lift_kind` ever learns a new type and this is not
# updated, the "if extended" column below stops being the payoff of that change
# and becomes a lie. The regression that catches it: this function and that one
# must agree on the same list of type names.
LIFTABLE_TODAY_EXACT = {"address", "address payable"}


def lift_kind_today(tn):
    if not isinstance(tn, dict) or tn.get("nodeType") != "ElementaryTypeName":
        return False
    name = (tn.get("name") or "").strip()
    if name in LIFTABLE_TODAY_EXACT:
        return True
    return name.startswith("uint")


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
                # ---- THE OTHER HALF OF THE B CEILING: THE ARGUMENTS ----
                #
                # An oracle is gate 3. Gate 1 is a FUZZ PARAMETER, and a unit
                # that declares no argument the emitter can `bound()` cannot
                # carry one however good its region is. Measured on the corpus:
                # farming's three certified regions are decimals / distributor /
                # farmInfo, all ZERO-ARGUMENT, so they carry 9 assertions and 0
                # fuzz parameters and can never be B. Reporting the oracle
                # surface without this reported half a ceiling as a ceiling.
                #
                # TWO COUNTS, not one, because the difference IS the pending
                # emitter change. `lift_kind` in scripts/solidity_path_put.py
                # accepts `address` and `uintN` and nothing else, so today a
                # bytes32/bool/intN parameter is silently kept pinned at the
                # counterexample's literal. `liftable_now` is what the emitter
                # does; `liftable_if_extended` is what it would do if that
                # function learned the other three kinds. The gap between the
                # two columns is the payoff of that change, as a number rather
                # than as a guess.
                params = ((m.get("parameters") or {}).get("parameters") or [])
                now = ext = 0
                for p in params:
                    tn = p.get("typeName") or {}
                    ok, _kind = is_scalar_typename(tn, udvt, enums)
                    if ok:
                        ext += 1
                        if lift_kind_today(tn):
                            now += 1
                units.append((f"{cname}.{m['name']}()",
                              m.get("stateMutability") or "nonpayable",
                              len(rets), len(params), now, ext))
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


def roundtrip_units(bench):
    """The unit names the coverage round-trip actually measured, or None.

    This is the DENOMINATOR the rest of the pipeline is defined over --
    certify_all.py takes its unit list from the same file, so that stage-2 and
    coverage are statements about the same units. A unit in the AST and not in
    here is one the corpus never measured, and the B that was reported came from
    exactly such a unit (FarmingPool.transfer, inherited from ERC20 and not
    overridden, so the round-trip never enumerated it).
    """
    p = REPO / "notes/coverage/forge_roundtrip" / bench / "emit.jsonl"
    if not p.exists():
        return None
    out = set()
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        tag = r.get("tag") or ""
        if "__" in tag:
            out.add(tag.split("__", 1)[1])
    return out


def per_unit_report(bench, primary, slots, units, scope):
    """Which units could EVER be B, and what stops the rest.

    A ceiling, not a measurement: it says a unit CANNOT clear a gate, never that
    it will. Gate 2 (bound width > 1) and gate 4 (forge green) are properties of
    a run and are not predictable from the AST, so they are deliberately absent
    -- a "ceiling" that quietly included them would be a forecast.
    """
    print(f"## {bench} :: {primary} -- per-unit B ceiling")
    print(f"{'unit':<44}{'args':>5}{'lift':>5}{'lift+':>6}{'ret':>5}"
          f"  {'oracle?':<8}{'fuzz?':<7}{'scope':<9}could-be-B")
    n_now = n_ext = 0
    for name, mut, nrets, nargs, now, ext in units:
        has_oracle = bool(slots) or nrets > 0
        in_scope = "-" if scope is None else (
            "measured" if name.split(".", 1)[1].rstrip("()") in scope
            else "OUTSIDE")
        b_now = has_oracle and now > 0
        b_ext = has_oracle and ext > 0
        n_now += 1 if b_now else 0
        n_ext += 1 if b_ext else 0
        print(f"{name:<44}{nargs:>5}{now:>5}{ext:>6}{nrets:>5}"
              f"  {('yes' if has_oracle else 'NO'):<8}"
              f"{('yes' if now else 'NO'):<7}{in_scope:<9}"
              + ("yes" if b_now else ("only-if-lift-extended" if b_ext
                                      else "NO")))
    print(f"  units that could be B with the emitter AS IT IS : {n_now} of "
          f"{len(units)}")
    print(f"  ... if lift_kind also took bool/bytesN/intN     : {n_ext} of "
          f"{len(units)}")
    if scope is not None:
        inside = [u for u in units
                  if u[0].split(".", 1)[1].rstrip("()") in scope]
        b_inside = sum(1 for name, _m, nrets, _na, now, _e in inside
                       if (bool(slots) or nrets > 0) and now > 0)
        print(f"  ... and INSIDE the round-trip's measured unit set: "
              f"{b_inside} of {len(inside)} measured unit(s)")
        print(f"  ⚠ {len(units) - len(inside)} of this contract's {len(units)} "
              f"units are OUTSIDE that set, so nothing downstream can reach "
              f"them however good they look here")
    print()


def main():
    verbose = "--verbose" in sys.argv
    per_unit = "--per-unit" in sys.argv
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
        if per_unit:
            per_unit_report(bench, primary, slots, units,
                            roundtrip_units(bench))
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
