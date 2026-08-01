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
    # modifier name -> the FunctionDefinitions that APPLY it.
    #
    # A modifier body's decisions belong, for attribution purposes, to whatever
    # units invoke it -- a modifier is not itself callable, so "which unit is
    # responsible for reaching this decision" is answered by its call sites, not
    # by the definition that encloses it. Without this, a decision inside the
    # modifier of a unit that was KILLED reads as unattributed, because the
    # enclosing definition is the modifier and the killed-unit rule keys on the
    # function name. MEASURED: that is exactly what happened to
    # `BaseEscrow.onlyAccessTokenHolder`, the modifier of EscrowDst's killed
    # `publicWithdraw`.
    modifier_users = {}

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
            ctx = dict(ctx, contract=node.get("name"),
                       ckind=node.get("contractKind"))
        elif nt == "FunctionDefinition":
            k = node.get("kind") or ("constructor" if node.get("isConstructor")
                                     else "function")
            ctx = dict(ctx, fn=node.get("name") or k, fnkind=k,
                       vis=node.get("visibility"))
            for mi in node.get("modifiers") or []:
                mn = (mi.get("modifierName") or {}).get("name")
                if mn:
                    modifier_users.setdefault(mn, []).append(dict(ctx))
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
    return owner, modifier_users


UNEXPANDED_NEEDLE = "deeper than the call depth bound"


def unexpanded_functions(bench, meta):
    """Function names the run REFUSED to expand, read from each unit's own log.

    The producer prints, once per run:

      WARNING: 8 call site(s) are deeper than the call depth bound (4) and were
      NOT expanded (sol:@C@EscrowDst@F@_ethTransfer#1708,
      sol:@C@EscrowDst@F@_withdraw_onlyValidImmutables#0, ...); paths through
      them are MERGED rather than enumerated.

    An unexpanded callee contributes NO decisions to its caller's path identity,
    so its branches cannot appear in any `decisions` array however many paths are
    witnessed. That makes this list the difference between "we failed to reach
    it" and "it was never in the path identity to reach".

    Keyed on the JOURNAL's tags, never a glob over work/ -- a skipped unit's
    directory can hold an earlier collection's log (D38 section 4a), and mixing
    vintages here would attribute one run's truncation to another's decisions.

    The mangled name is `sol:@C@<contract>@F@<fn>#<id>`, and `<fn>` may be a
    function CONCATENATED with a modifier (`_withdraw_onlyValidImmutables`), so
    a plain equality test misses the function it is really about. Matching is
    therefore `== fn` or `startswith(fn + "_")`.
    """
    work = gate.PATHCOV / bench / "work"
    names = set()
    for r in meta.get("runs", []):
        if r.get("skipped"):
            continue
        log = work / str(r.get("tag", "")) / "run.log"
        if not log.exists():
            continue
        for ln in log.read_text(errors="replace").splitlines():
            if UNEXPANDED_NEEDLE not in ln:
                continue
            for tok in ln.replace("(", " ").replace(")", " ").split():
                tok = tok.strip(",;")
                if "@F@" in tok:
                    names.add(tok.split("@F@", 1)[1].split("#", 1)[0])
    return names


def past_depth_bound(fn, unexpanded):
    if not fn:
        return False
    return any(u == fn or u.startswith(fn + "_") for u in unexpanded)


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

    owner, modifier_users = owner_of_each_decision(base["flat"])
    killed_fns = {t.split("__", 1)[1] for t in killed if "__" in t}
    # The units the collection actually RAN, so "every applier was killed" can be
    # distinguished from "some applier ran and still did not reach it". Taken
    # from the journal rather than from the reports, because a killed unit has no
    # report and would otherwise not appear as having been attempted at all.
    ran_fns = {r.get("function") for r in meta["runs"]
               if r.get("function") and not r.get("skipped")
               and r.get("reportPresent")}
    # Contracts every one of whose units the collector refused. Taken from the
    # journal's own `skipped` records rather than from the contract kind alone:
    # "this is a library" and "this collection therefore measured none of it"
    # are different statements, and only the second licenses the attribution.
    skipped_libs = {r.get("contract") for r in meta["runs"] if r.get("skipped")}
    unexpanded = unexpanded_functions(bench, meta)
    # unit -> how many U paths it has, IF every one of them is `bounded-holds`.
    # Read off the journal's own uReasons breakdown, which publishes every token
    # including the zeros -- so "all of them" is checked against the other six
    # buckets being zero, not inferred from the one that is non-zero.
    bounded_only = {}
    for r in meta["runs"]:
        ur = r.get("uReasons") or {}
        if not ur or not r.get("function"):
            continue
        bh = ur.get("bounded-holds", 0)
        others = sum(v for k, v in ur.items() if k != "bounded-holds")
        if bh and others == 0:
            bounded_only[r["function"]] = bh
    if unexpanded:
        print(f"   call site(s) the run(s) refused to expand (past the depth "
              f"bound): {len(unexpanded)}\n     "
              + ", ".join(sorted(unexpanded)) + "\n")
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
            extra = ""
            if o and o.get("fnkind") == "constructor":
                why = "CONSTRUCTOR-SCOPE (not a unit path by definition)"
            elif o and o.get("fn") in killed_fns:
                why = "KILLED UNIT (no report; not measured)"
            elif o and o.get("fnkind") == "modifier":
                # A modifier is not callable, so responsibility for its
                # decisions belongs to the units that APPLY it. Three cases,
                # kept apart because they have different standings.
                users = modifier_users.get(o.get("fn"), [])
                names = sorted({u.get("fn") for u in users})
                ran = [n for n in names if n in ran_fns]
                dead = [n for n in names if n in killed_fns]
                if names and not ran and dead:
                    why = (f"KILLED UNIT via modifier (every applier died: "
                           f"{', '.join(dead)})")
                elif not names:
                    why = "MODIFIER APPLIED BY NOTHING (dead code in this flat)"
                elif any(past_depth_bound(n, unexpanded) for n in names):
                    # The modifier body rides on whatever applies it. If every
                    # applier is a callee the run refused to expand, the
                    # modifier's decisions were never in any path identity
                    # either -- the same cause as the applier's own decisions,
                    # reached one level out.
                    blocked = [n for n in names
                               if past_depth_bound(n, unexpanded)]
                    why = (f"PAST DEPTH BOUND via its applier "
                           f"({', '.join(blocked)}) -- D28: raising it buys 0")
                else:
                    why = ""
                    extra = (f"applied by {', '.join(names)}"
                             + (f"; ran: {', '.join(ran)}" if ran else "")
                             + (f"; killed: {', '.join(dead)}" if dead else ""))
            elif o and past_depth_bound(o.get("fn"), unexpanded):
                why = ("PAST DEPTH BOUND (not expanded, so never in any path "
                       "identity) -- D28: raising it buys 0")
            elif o and o.get("ckind") == "library" and o.get("contract") in skipped_libs:
                # A LIBRARY whose every unit the collector REFUSED. Not a reach
                # failure and not a knob: a library has no dispatcher harness,
                # so `--contract <Lib>` finds no verification targets, and the
                # only other route is `--function`, which verifies from an
                # ARBITRARY contract state and can produce a counterexample no
                # reachable state supports -- a RED generated test. The baseline
                # reaches these decisions precisely by taking that route
                # (collect.py routes library units through --function), which is
                # why the two sides differ here.
                #
                # D28 additionally MEASURED that the other candidate -- these
                # sitting past the call-depth bound -- is not the fix: 4 -> 6
                # buys 8 more paths, 4 more witnesses and ZERO decisions, and
                # bound 8 does not finish.
                why = ("LIBRARY, --function BANNED (#33/D28: a stated "
                       "applicability limit, and the depth bound is measured "
                       "not to be the fix)")
            elif o and o.get("fn") in ran_fns and bounded_only.get(o.get("fn")):
                # The unit RAN, and every path of it that stayed U did so with
                # `bounded-holds` -- "no witness at this exploration", not a
                # solver failure and not a scope refusal. The report's own
                # `known_limitation_entry_state` names what that usually is:
                #
                #   "transaction entry state is the post-constructor state;
                #    contract state is not havoc'd, so paths guarded by state
                #    that an earlier transaction would have to establish are
                #    reported U at this tx bound"
                #
                # DELIBERATELY WORDED AS CONSISTENT-WITH, NOT AS THE CAUSE. All
                # this observes is that no U in the unit carries any other
                # reason; it does not read the decision's guard and prove it
                # needs a second transaction. Calling it established would be
                # the same move as the `onlyValidSecret` crypto story -- a
                # plausible explanation sitting next to the evidence rather than
                # derived from it.
                n = bounded_only[o.get("fn")]
                why = (f"NO WITNESS AT tx=1 — the unit ran and all {n} of its "
                       f"U path(s) are `bounded-holds`; consistent with the "
                       f"report's own known_limitation_entry_state (state a "
                       f"prior tx must establish), NOT established as the cause")
            else:
                why = ""
            tally[why or "UNEXPLAINED"] = tally.get(why or "UNEXPLAINED", 0) + 1
            print(f"       flat {ln:>6}   {text[:88]}")
            print(f"       {'':>11}   in {who}"
                  + (f"   <- {why}" if why else ""))
            if extra:
                print(f"       {'':>11}   {extra}")
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
