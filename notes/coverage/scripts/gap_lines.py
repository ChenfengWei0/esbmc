#!/usr/bin/env python3
"""WHICH canonical decisions our F paths missed, by flat line and by source text.

`branch_gate.py` compares COUNTS, because METHODOLOGY 4 records the baseline's
reach as a count and the locked JSON carries no per-decision identity. That is
correct for the gate and useless for fixing it: "4 / 12 contracts/FarmingPool.sol"
does not say WHICH eight.

Our side does have the identity -- every F claim publishes a `decisions` array
with a flat line per step -- so the missing set is computable on our side alone.
It is NOT a claim about which decisions the BASELINE reached (that information
does not exist); it is "canonical in-scope decisions our witnessed paths never
walked", which is exactly the set to go after.

Prints, per in-scope file: reached lines, missing lines, and for each missing
line its ORIGINAL source text pulled out of the flat, so the eight are readable
rather than numeric.

Usage: python3 gap_lines.py <bench-key> [<bench-key> ...]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))       # notes/, for branch_gate

import ast_decisions                               # noqa: E402
import branch_gate as gate                         # noqa: E402


def flat_source_lines(flat_path):
    return Path(flat_path).read_text(errors="replace").splitlines()


def owner_of_each_decision(flat_path):
    """flat_line -> the FunctionDefinition that encloses it, from the AST.

    STRUCTURAL, not textual. Whether a missed decision sits in a CONSTRUCTOR is
    the difference between "we failed to reach it" and "the Problem Definition
    says it is not ours to reach": a unit is an externally callable ENTRY POINT
    and a path is one call from entry to return, so a constructor-scope decision
    is not in any unit's path set and no test can be emitted for it. Deciding
    that from the look of a source line (`stakingToken_` has a trailing
    underscore, so it is probably a constructor parameter) is exactly the kind of
    inference this project has been burned by, so it is read off the AST node
    instead: `kind: "constructor"` is stated by solc.

    Reuses ast_decisions' own byte->line map and file blocks so this cannot
    disagree with the canonical set it is being joined against.
    """
    flat = Path(flat_path)
    b2l = ast_decisions.byte_to_line(flat.read_bytes())
    solast = Path(str(flat) + ".solast")
    if not solast.exists():
        solast = flat.with_suffix(".sol.solast")
    ast = ast_decisions.extract_ast_json(solast)

    owner = {}

    def walk(node, ctx):
        if node is None:
            return
        if isinstance(node, list):
            for c in node:
                walk(c, ctx)
            return
        if not isinstance(node, dict):
            return
        nt = node.get("nodeType")
        if nt == "ContractDefinition":
            ctx = dict(ctx, contract=node.get("name"))
        elif nt == "FunctionDefinition":
            k = node.get("kind") or ("constructor" if node.get("isConstructor")
                                     else "function")
            ctx = dict(ctx, fn=node.get("name") or k, fnkind=k,
                       vis=node.get("visibility"))
        elif nt == "ModifierDefinition":
            ctx = dict(ctx, fn=node.get("name"), fnkind="modifier",
                       vis="modifier")
        src = node.get("src")
        if src and ctx.get("fn") is not None:
            ln = ast_decisions.src_to_line(b2l, src)
            # First writer wins: the outermost node that opened this function
            # context reached this line first, and re-tagging from a nested
            # node of a DIFFERENT function is impossible because ctx follows
            # the nesting.
            owner.setdefault(ln, ctx)
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk(v, ctx)

    walk(ast, {})
    return owner


def describe_owner(o):
    if not o:
        return "?"
    c, fn, k, v = (o.get("contract"), o.get("fn"), o.get("fnkind"),
                   o.get("vis"))
    return f"{c}.{fn} [{k}, {v}]"


def one(bench):
    base = gate.baseline(bench)
    meta, reports = gate.pathcov_reports_for(bench)
    if meta is None:
        print(f"## {bench}: not collected\n")
        return
    lines, st = gate.pathcov_reached_flat_lines(reports)
    canon, _blocks = gate.canonical_in_scope(base["flat"], base["project"])
    src = flat_source_lines(base["flat"])

    print(f"## {bench}")
    print(f"   {st['reports']} report(s), {st['f_claims']} F claim(s), "
          f"{st['decision_steps']} decision step(s), "
          f"{len(lines)} distinct flat line(s) walked\n")

    # A unit that was KILLED contributes no report, so its decisions cannot
    # appear in `lines` at all. Named here because otherwise its file reads as
    # "these decisions are hard to reach" when the truth is "nobody asked".
    killed = [r.get("tag") for r in meta["runs"]
              if r.get("killedByOuterTimeout")]
    if killed:
        print(f"   ⚠ {len(killed)} unit(s) left NO report (killed by the outer "
              f"timeout), so their decisions\n     cannot appear below: "
              f"{', '.join(killed)}\n")

    owner = owner_of_each_decision(base["flat"])
    killed_fns = {t.split("__", 1)[1] for t in killed if "__" in t}
    tally = {}

    for f in sorted(canon):
        c = canon[f]
        hit = c & lines
        miss = sorted(c - lines)
        print(f"   {f}   {len(hit)}/{len(c)} reached, {len(miss)} missing")
        for ln in miss:
            text = src[ln - 1].strip() if 0 < ln <= len(src) else "<out of range>"
            o = owner.get(ln)
            who = describe_owner(o)
            # ATTRIBUTE, do not just list. Three causes are distinguishable
            # here and they have completely different standings:
            #   CONSTRUCTOR-SCOPE  -- ruled out by the Problem Definition. Not a
            #                         failure to reach; a decision no emitted
            #                         test can walk. A REPORTABLE DENOMINATOR
            #                         DIFFERENCE against the baseline.
            #   KILLED UNIT        -- nobody asked. Reach we do not have because
            #                         the run did not finish.
            #   (blank)            -- a real unexplained miss, the only kind
            #                         worth calling a gap.
            if o and o.get("fnkind") == "constructor":
                why = "CONSTRUCTOR-SCOPE (not a unit path by definition)"
            elif o and o.get("fn") in killed_fns:
                why = "KILLED UNIT (no report; not measured)"
            else:
                why = ""
            tally[why or "UNEXPLAINED"] = tally.get(why or "UNEXPLAINED", 0) + 1
            print(f"       flat {ln:>6}   {text[:88]}")
            print(f"       {'':>11}   in {who}"
                  + (f"   <- {why}" if why else ""))
        print()

    if tally:
        print("   missing decisions by cause:")
        for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"     {v:>3}  {k}")
        print()


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    for b in argv[1:]:
        if b not in gate.BENCHES:
            sys.exit(f"unknown bench {b!r}; known: {', '.join(gate.BENCHES)}")
        one(b)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
