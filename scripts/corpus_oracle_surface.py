#!/usr/bin/env python3
"""T0 -- the corpus admission gate: how many scalars can a TEST read back?

WHY THIS RUNS FIRST. A generated test's oracle has to read something after the
call. If a contract exposes no scalar a test can read, its oracle ceiling is
zero and no amount of path exploration changes that -- the runs are wasted
before they start. This number decides which contracts belong in the
denominator at all, and it was never computed.

WHAT IS COUNTED, and why it is counted THIS way.

  ZERO-ARG READERS -- readable with no key invented:
      * a public state variable of a scalar type (solc generates its getter);
      * a public/external view/pure function with NO parameters whose declared
        return values are all scalar.

  KEY-TAKING READERS -- readable only once a key or index is supplied:
      * a public mapping/array state variable whose ultimate value is scalar;
      * a public/external view/pure function WITH parameters returning scalars.
    Kept in their own column rather than folded in, because supplying the key
    is a separate piece of work and folding them would make a contract look
    readable today when it is not.

  TAUTOLOGIES -- a `constant` or `immutable` reader is subtracted. It has no
    storage slot and never changes, so an assertion over it is decided at
    compile time and is not an oracle. MEASURED: aqua's assertion ladder offers
    exactly one variable, `_DOCKED`, and it is immutable -- which is why that
    contract's emitted tests carried no oracle at all while looking healthy.

WHAT COUNTS AS SCALAR is not decided here. It is asked of `return_kind`, the
same whitelist the emitter uses to bind and cast a value, so this table means
"what THIS pipeline can read" rather than "what Solidity could in principle
express". Types it rejects are collected and printed, so the gap between the
two is visible instead of silently lowering every count.

Pure reading: no ESBMC, no solver, no forge. Input is the pinned AST of each
benchmark's flat plus the pinned own-contract set.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solidity_path_put import return_kind  # noqa: E402

INPUTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "notes", "coverage", "inputs")

# benchmark -> (flat stem, the contract that is DEPLOYED and focused)
FLATS = {
    "aqua_Aqua": ("aqua__Aqua.flat.sol", "Aqua"),
    "cross_chain_swap_EscrowDst": ("cross-chain-swap__EscrowDst.flat.sol",
                                   "EscrowDst"),
    "cross_chain_swap_EscrowSrc": ("cross-chain-swap__EscrowSrc.flat.sol",
                                   "EscrowSrc"),
    "farming": ("farming__FarmingPool.flat.sol", "FarmingPool"),
    "limit_order_protocol": ("limit-order-protocol__MakerTraitsLib.flat.sol",
                             "MakerTraitsLib"),
    "st1inch_St1inch": ("st1inch__St1inch.flat.sol", "St1inch"),
}


def load_ast(path):
    txt = open(path).read()
    return json.loads(txt[txt.index("{"):])


def index_contracts(ast):
    """{id: node} and {name: node} for every ContractDefinition in the flat."""
    by_id, by_name = {}, {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("nodeType") == "ContractDefinition":
                if n.get("id") is not None:
                    by_id[n["id"]] = n
                by_name.setdefault(n.get("name"), n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(ast)
    return by_id, by_name


def scope_chain(node, by_id):
    """The contract and its bases, BASE-FIRST -- a reader declared on a base is
    just as callable on the derived contract, and missing that would undercount
    exactly the contracts that inherit their getters."""
    chain = node.get("linearizedBaseContracts") or [node.get("id")]
    return [by_id[c] for c in reversed(chain) if c in by_id]


def type_string(n):
    return ((n.get("typeDescriptions") or {}).get("typeString") or "").strip()


def is_scalar(ts, rejected):
    """Asked of the emitter's own whitelist; anything it declines is RECORDED.

    A silently-declined type lowers the count with no trace, and a count that
    can be wrong downward without saying so is the kind of number this gate
    exists to stop producing.
    """
    if return_kind(ts) is not None:
        return True
    if ts:
        rejected.setdefault(ts, 0)
        rejected[ts] += 1
    return False


def mapping_value_of(ts):
    """The ultimate value type of `mapping(a => mapping(b => V))` or `V[]`."""
    s = ts
    while True:
        if s.startswith("mapping("):
            arrow = s.rfind("=>")
            if arrow < 0:
                return None
            s = s[arrow + 2:].strip().rstrip(")").strip()
            continue
        if s.endswith("[]"):
            s = s[:-2].strip()
            continue
        if s.endswith("]") and "[" in s:
            s = s[:s.rfind("[")].strip()
            continue
        return s


def surface(node, by_id, rejected):
    """(zero_arg, tautologies, key_taking, names) for one contract."""
    zero, taut, keyed, names = [], [], [], []
    seen = set()
    for sc in scope_chain(node, by_id):
        for n in sc.get("nodes", []) or []:
            if not isinstance(n, dict):
                continue
            nt = n.get("nodeType")
            nm = n.get("name") or ""
            if nt == "VariableDeclaration" and n.get("stateVariable"):
                if n.get("visibility") != "public" or nm in seen:
                    continue
                ts = type_string(n)
                # A mapping/array getter takes the key(s); a scalar one does not.
                if ts.startswith("mapping(") or ts.endswith("]"):
                    v = mapping_value_of(ts)
                    if v and is_scalar(v, rejected):
                        seen.add(nm)
                        keyed.append(nm)
                    continue
                if not is_scalar(ts, rejected):
                    continue
                seen.add(nm)
                # `mutability` is "mutable" / "immutable" / "constant"; older
                # ASTs carry the booleans instead, so both are read.
                mut = n.get("mutability")
                if mut in ("constant", "immutable") or n.get("constant"):
                    taut.append(nm)
                else:
                    zero.append(nm)
                names.append(nm)
            elif nt == "FunctionDefinition":
                if n.get("visibility") not in ("public", "external"):
                    continue
                if n.get("stateMutability") not in ("view", "pure"):
                    continue
                rets = ((n.get("returnParameters") or {}).get("parameters")
                        or [])
                if not rets:
                    continue
                if not all(is_scalar(type_string(r), rejected) for r in rets):
                    continue
                sig = nm + "/" + str(len(
                    (n.get("parameters") or {}).get("parameters") or []))
                if sig in seen:
                    continue
                seen.add(sig)
                if (n.get("parameters") or {}).get("parameters"):
                    keyed.append(nm + "()")
                else:
                    zero.append(nm + "()")

    # ---- A GETTER OVER A TAUTOLOGY IS STILL A TAUTOLOGY -------------------
    #
    # CAUGHT BY PRINTING THE NAMES, which is the only reason it was caught.
    # The escrow contracts reported three zero-arg readers each --
    # RESCUE_DELAY(), FACTORY(), PROXY_BYTECODE_HASH() -- beside three
    # constant/immutable state variables of EXACTLY THOSE NAMES. They are the
    # same three quantities reached through the generated getter instead of
    # through the variable, so admitting them would have declared six
    # contracts oracle-bearing on the strength of values fixed at deployment.
    #
    # That is the failure the `taut` column exists to prevent, arriving through
    # a second door -- and a gate with two doors and one guard is not a gate.
    taut_names = set(taut)
    shadow = [z for z in zero if z.rstrip("()") in taut_names]
    zero = [z for z in zero if z.rstrip("()") not in taut_names]
    taut = taut + shadow
    return zero, taut, keyed


def main():
    pinned = json.load(open(os.path.join(INPUTS, "own_contracts.json")))
    rejected = {}
    rows = []
    for bench, (flat, primary) in sorted(FLATS.items()):
        ast = load_ast(os.path.join(INPUTS, flat + ".solast"))
        by_id, by_name = index_contracts(ast)
        own = pinned["benchmarks"][bench]["ownContracts"]
        # R8: iterate the EXPECTED names. A pinned name the flat does not
        # declare is a hard failure, not a silently shorter table.
        for name in own:
            node = by_name.get(name)
            if node is None:
                print(f"FATAL: {bench}: pinned own-contract {name!r} is not "
                      f"declared in {flat}. The pinned set and the flat "
                      f"disagree; refusing to print a table that quietly "
                      f"drops a row")
                return 1
            zero, taut, keyed = surface(node, by_id, rejected)
            rows.append((bench, name, name == primary,
                         node.get("contractKind"), zero, taut, keyed))

    print(f"{'benchmark':<28} {'contract':<22} {'kind':<9} {'0-arg':>5} "
          f"{'taut':>5} {'keyed':>5}  verdict")
    print("-" * 96)
    admitted = []
    for bench, name, is_primary, kind, zero, taut, keyed in rows:
        verdict = "has-oracle-surface" if zero else "no-oracle-surface"
        star = "*" if is_primary else " "
        print(f"{bench:<28} {star}{name:<21} {kind or '?':<9} {len(zero):>5} "
              f"{len(taut):>5} {len(keyed):>5}  {verdict}")
        if zero:
            admitted.append(f"{bench}:{name}")

    print()
    print("* = the contract this benchmark deploys and focuses.")
    print("0-arg = scalars a test can read with no key invented; these are the "
          "ones an oracle can use today.")
    print("taut  = public scalars that are constant/immutable -- readable, but "
          "an assertion over them is decided at compile time, so they are NOT "
          "counted as surface.")
    print("keyed = scalars reachable only once a mapping key or array index is "
          "supplied. NOT counted: supplying the key is separate work. This "
          "column is what that work would buy.")
    print()
    print(f"ADMITTED TO THE DENOMINATOR ({len(admitted)} of {len(rows)}): "
          f"{', '.join(admitted) if admitted else '(none)'}")
    excluded = [f"{b}:{n}" for b, n, _p, _k, z, _t, _y in rows if not z]
    print(f"EXCLUDED as no-oracle-surface ({len(excluded)}): "
          f"{', '.join(excluded) if excluded else '(none)'}")
    # ---- THE NAMES, NOT JUST THE COUNTS ----------------------------------
    #
    # A count is not checkable. MEASURED WHY THIS MATTERS: the escrow contracts
    # report three zero-arg readers each alongside three constant/immutable
    # state variables, and if those readers are merely view functions RETURNING
    # the immutables then they are compile-time tautologies too -- the same
    # defect the `taut` column exists to catch, arriving through a different
    # door. The only way to know is to read the names, so they are printed.
    print("PER-CONTRACT NAMES (a count nobody can check is not a "
          "measurement):")
    for bench, name, _p, _k, zero, taut, keyed in rows:
        if not (zero or taut or keyed):
            continue
        print(f"   {bench}:{name}")
        if zero:
            print(f"      0-arg : {', '.join(zero)}")
        if taut:
            print(f"      taut  : {', '.join(taut)}")
        if keyed:
            print(f"      keyed : {', '.join(keyed)}")
    print()
    if rejected:
        print("TYPES THE EMITTER'S WHITELIST DECLINED (each one lowered some "
              "count above; listed so the gap is visible, not silent):")
        for ts, k in sorted(rejected.items(), key=lambda x: (-x[1], x[0])):
            print(f"   {k:>4}x  {ts}")
    else:
        print("no declared type was declined by the whitelist")
    return 0


if __name__ == "__main__":
    sys.exit(main())
