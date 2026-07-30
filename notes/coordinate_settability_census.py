#!/usr/bin/env python3
"""Census: how many state variables in the corpus can a generated test set?

WHY THIS EXISTS. A coordinate the test cannot set gives the verifier an input
space WIDER than reality, so certification over it cannot succeed -- the witness
just moves the quantity every round until the shrink budget runs out, and the
failure reads as weak search. Measured on EscrowSrc.cancel: both of its free
coordinates were `immutable`, and its reported "shrink round budget exhausted"
was never a search-power result at all.

So before any yield number is quoted, the question "what fraction of the
coordinate list is even settable?" has to have an answer. This produces it.

READ, NOT INFERRED. solc states `mutability` on every VariableDeclaration
("mutable" / "immutable" / "constant"). Inferring it from counterexamples --
"the value is the same on every path" -- is equally true of ordinary storage
that happens not to vary, which is the inference this project has got wrong
repeatedly.

WHAT IT DOES NOT MEASURE, stated so the number is not over-read: this counts
DECLARATIONS per contract in the flattened input, not the coordinates any
particular unit actually got. A unit's coordinate list is the subset its
counterexample mentions, so these figures are an upper bound on how much of a
real coordinate list is settable, not the figure itself.
"""
import json
import os
import sys

INPUTS = "/home/samson/workspace/esbmc/notes/coverage/inputs"


def state_vars(ast_path):
    try:
        txt = open(ast_path).read()
        ast = json.loads(txt[txt.index("{"):])
    except (OSError, ValueError):
        return None
    out = {}

    def walk(n, contract=None):
        if isinstance(n, dict):
            if n.get("nodeType") == "ContractDefinition":
                contract = n.get("name")
            if (n.get("nodeType") == "VariableDeclaration"
                    and n.get("stateVariable")):
                out[(contract, n.get("name"))] = n.get("mutability")
            for v in n.values():
                walk(v, contract)
        elif isinstance(n, list):
            for v in n:
                walk(v, contract)

    walk(ast)
    return out


def main():
    asts = sorted(f for f in os.listdir(INPUTS) if f.endswith(".solast"))
    if not asts:
        print("no .solast under", INPUTS)
        return 1
    print(f"| input | state vars | mutable | immutable | constant | "
          f"settable % |")
    print(f"|---|---|---|---|---|---|")
    tot = {"mutable": 0, "immutable": 0, "constant": 0, "other": 0}
    for a in asts:
        sv = state_vars(os.path.join(INPUTS, a))
        if sv is None:
            print(f"| `{a}` | UNREADABLE | - | - | - | - |")
            continue
        c = {"mutable": 0, "immutable": 0, "constant": 0, "other": 0}
        for mu in sv.values():
            c[mu if mu in c else "other"] += 1
        n = sum(c.values())
        pct = f"{c['mutable'] * 100.0 / n:.0f}%" if n else "n/a"
        for k in c:
            tot[k] += c[k]
        print(f"| `{a.replace('.flat.sol.solast', '')}` | {n} | "
              f"{c['mutable']} | {c['immutable']} | {c['constant']} | {pct} |")
    n = sum(tot.values())
    pct = f"{tot['mutable'] * 100.0 / n:.0f}%" if n else "n/a"
    print(f"| **total** | **{n}** | **{tot['mutable']}** | "
          f"**{tot['immutable']}** | **{tot['constant']}** | **{pct}** |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
