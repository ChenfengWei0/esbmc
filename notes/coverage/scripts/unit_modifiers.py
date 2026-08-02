#!/usr/bin/env python3
"""Print EVERY externally-callable unit of a contract with its modifiers.

WHY THIS EXISTS. `slot_writers.py` answers "which unit can carry a post-vs-pre
oracle", and stage 2 then answers "can its region be certified". Between the two
sits a question neither asks and that decides the answer in advance: is the unit
GATED by a predicate the region representation cannot express?

MEASURED on two units, and it is the reason B is 0 on the corpus's only
oracle-capable contract. `onlyOwner` separates a unit's paths by
`msg.sender == _owner`. Definition 6 makes a region a PRODUCT of per-coordinate
sets, so it cannot hold an equality BETWEEN two coordinates: any interval wide
enough to contain the owner also contains non-owners, whose executions leave the
path, so the certification query is refuted every round and each cut removes one
value. FarmingPool.transferOwnership and FarmingPool.setDistributor both come
back 0 certified / 5 not with exactly that witness, and promoting msg.sender with
--env-coord does NOT fix it -- the coordinate becomes bounded and still cannot
converge. The driver's own --level0 help states the limit: "`coordinate A ==
coordinate B` is a cross-coordinate relation, changes definition 6, and is an
open method-layer item -- it is not attempted here."

So a unit's modifier list is a PREDICTION of whether stage 2 can succeed, and it
costs an AST read rather than a 900-second driver run.

EVERY unit is printed, gated or not. A listing that showed only the gated ones
would be a filter whose complement nobody can check, and the interesting number
here is the RATIO -- how much of the oracle-capable surface is behind a relation
the method cannot express.

⚠ WHAT THIS DOES NOT DO. It reports the modifier NAMES on the declaration. It
does not evaluate them, does not follow a modifier that calls another, and does
not see a `require(msg.sender == owner)` written inline in the BODY -- which
gates the unit exactly as hard and is invisible here. A unit printed with no
modifier is therefore "no modifier", never "not gated". The inline case is the
one to check by hand before spending a driver run on a unit this script clears.
"""

import argparse
import json
import sys

# Modifier names whose predicate is known to be an equality between msg.sender
# and a state variable. Kept as a NAMED list rather than a substring test on
# "owner": `onlyOwnerOrDistributor` is a disjunction and is not the same shape,
# and a substring test would silently claim it is.
RELATIONAL_GATES = {"onlyOwner", "onlyDistributor", "onlyOwnerOrDistributor"}


def walk(node, out):
    if isinstance(node, dict):
        if node.get("nodeType") == "FunctionDefinition":
            out.append(node)
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ast", help="the .solast solc AST")
    a = ap.parse_args()

    # `.solast` is solc's `--ast-compact-json`, which prefixes the JSON with a
    # `======= <path> =======` banner. json.load on the raw file fails at char 0
    # with "Expecting value", which reads like a corrupt AST and is a header.
    raw = open(a.ast, encoding="utf-8", errors="replace").read()
    start = raw.find("{")
    if start < 0:
        sys.exit(f"{a.ast}: no JSON object in the file at all")
    fns = []
    walk(json.loads(raw[start:]), fns)

    rows = []
    for f in fns:
        if f.get("kind") == "constructor":
            continue
        if f.get("visibility") not in ("public", "external"):
            continue
        mods = [m.get("modifierName", {}).get("name", "?")
                for m in (f.get("modifiers") or [])]
        rows.append((f.get("name") or "<fallback>",
                     f.get("stateMutability") or "nonpayable", mods))

    rows.sort()
    gated = 0
    print(f"{'unit':<32}{'mutability':<14}modifiers")
    for name, mu, mods in rows:
        rel = [m for m in mods if m in RELATIONAL_GATES]
        if rel:
            gated += 1
        print(f"{name:<32}{mu:<14}"
              + (", ".join(mods) if mods else "(none)")
              + ("   <-- RELATIONAL GATE: msg.sender == <state>, "
                 "stage 2 cannot certify this" if rel else ""))
    print()
    print(f"  {gated} of {len(rows)} externally-callable unit(s) carry a "
          f"modifier whose predicate is an equality between msg.sender and a "
          f"state variable.")
    print("  Such a unit's paths ARE enumerated and witnessed -- what fails is "
          "the REGION, so its concrete counterexample test is still produced. "
          "What is lost is the generalisation, i.e. gate 1 of deliverable B.")
    print("  ⚠ A unit listed with no modifier is NOT thereby ungated: an inline "
          "`require(msg.sender == owner)` in the body has the same effect and is "
          "invisible to this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
