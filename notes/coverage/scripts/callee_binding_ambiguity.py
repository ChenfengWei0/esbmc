#!/usr/bin/env python3
"""Would a NAME-KEYED callee-argument map be ambiguous on this corpus?

WHY THIS EXISTS. `ce_consistency.py` refuses 703 of 929 corpus decisions, and its
single largest refusal class is "a decision inside an INLINED CALLEE reading a
name that is the callee's own local; the report publishes no argument mapping."
The obvious fix is to publish that mapping. Before writing it, this asks the one
question that decides whether the cheap version of that fix is CORRECT rather
than merely cheap.

THE SOURCE FACT THAT MAKES IT A QUESTION AT ALL. Internal calls are spliced by
`sol_path_inlinet::expand_here` (goto_coverage.cpp:3381-3426), which delegates
parameter binding to `goto_inlinet::parameter_assignments`
(goto_inline.cpp:29-117). That routine reads the formal's identifier straight off
the callee's own `code_typet`:

    goto_inline.cpp:43    const irep_idt &identifier = formal.cmt_identifier();
    goto_inline.cpp:109   code_assign2tc(symbol2tc(formal_type, identifier), actual)

There is NO renaming -- no prefix, no suffix, no per-call-site freshening. (The
generic inliner's location-rewrite block, goto_inline.cpp:245-261, is likewise
absent from `expand_here`.) So if one unit calls the same callee TWICE, both
calls assign the SAME symbol, and a mapping keyed on the bare parameter name
records whichever call came last. On such a path the published mapping would be
wrong for one of the two calls -- and wrong silently, which is the shape this
project has already shipped once.

WHAT THIS SCRIPT MEASURES, AND WHAT IT CANNOT DECIDE. It counts, per witnessed
(status F) path, whether any in-callee DECISION SITE -- keyed
(file, line, column, operand), i.e. the identity the report actually publishes --
occurs more than once in that path's `decisions` array.

    zero repeats     ==> on THIS corpus every callee body is traversed once per
                         path, so a name-keyed map is unambiguous HERE, and the
                         cheap fix is correct HERE. It says nothing about inputs
                         outside the corpus.
    any repeat       ==> the body was traversed twice, which is EITHER a second
                         call site (rebinds the parameter -> ambiguous) OR a loop
                         inside the callee (does NOT rebind -> harmless).
                         THE REPORT CANNOT TELL THESE APART, because the missing
                         datum is exactly the call-site identity this whole
                         exercise is about. So a repeat is NOT a proof of
                         ambiguity; it is a refusal to certify the cheap fix.

That asymmetry is the point and is printed in the output. A script that reported
repeats as "ambiguous" would be inventing the distinction it just measured the
absence of.

Usage:  python3 callee_binding_ambiguity.py [<pathcov-root>]
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent / "pathcov"


def unit_simple_name(claim):
    """`sol:@C@C@F@tern_call#92` -> `tern_call`. None when unrecognisable.

    Same parse as ce_consistency.py:86-92, deliberately -- if the two disagreed
    about what counts as "in a callee", this measurement would not be about the
    refusal it claims to be about.
    """
    pf = claim.get("path_function") or ""
    if "@F@" not in pf:
        return None
    tail = pf.split("@F@", 1)[1]
    return tail.split("#", 1)[0] or None


def site_key(dec):
    """The decision-site identity the REPORT publishes -- nothing more.

    Deliberately not including `branch_claim`: the two arms of one source
    decision print as `P` and `!(P)`, so keying on the text would split one site
    into two and hide exactly the repeat being looked for.
    """
    return (dec.get("file"), dec.get("line"), dec.get("column"),
            dec.get("operand"))


def scan_report(path, acc):
    d = json.loads(Path(path).read_text())
    for c in d.get("claims", []):
        if c.get("status") != "F":
            continue                       # only a witnessed path has a payload
        unit = unit_simple_name(c)
        acc["paths"] += 1
        # per-callee site occurrences on THIS path
        per_callee = defaultdict(Counter)
        in_callee_here = 0
        for dec in c.get("decisions", []):
            dfn = dec.get("function")
            if not (unit and dfn and dfn != unit):
                acc["decisions_in_unit"] += 1
                continue
            acc["decisions_in_callee"] += 1
            in_callee_here += 1
            per_callee[dfn][site_key(dec)] += 1
        if in_callee_here:
            acc["paths_with_callee_decisions"] += 1
        for fn, sites in per_callee.items():
            acc["callees_seen"].add(fn)
            repeats = {s: n for s, n in sites.items() if n > 1}
            if repeats:
                acc["paths_with_repeated_site"] += 1
                acc["repeat_examples"].append(
                    (path, c.get("condition"), fn,
                     sorted((n, s) for s, n in repeats.items())[-1]))
                break                      # one witness per path is enough

        # Does any in-callee decision read a name the UNIT's `inputs` also
        # binds? That is the collision ce_consistency.py:212-221 refuses on --
        # counted here because "how much of the 703 is a real name clash" has
        # never been measured, and the two have different fixes.
        inputs = set((c.get("inputs") or {}).keys())
        if inputs:
            for dec in c.get("decisions", []):
                dfn = dec.get("function")
                if not (unit and dfn and dfn != unit):
                    continue
                pred = dec.get("branch_claim") or ""
                for nm in inputs:
                    # crude containment: a name-clash CANDIDATE, not a proof.
                    if nm and nm in pred:
                        acc["callee_decisions_naming_a_unit_input"] += 1
                        break


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        sys.exit(f"no such directory: {root}")

    print("## Would a name-keyed callee-argument map be ambiguous?\n")
    print("Source basis: goto_inline.cpp:43 + :109 -- the inliner binds the "
          "callee's OWN\nformal identifier and never renames per call site "
          "(goto_coverage.cpp:3381-3426).\n")

    grand = Counter()
    any_repeat = False
    for bench in sorted(d for d in root.iterdir() if d.is_dir()):
        reps = sorted((bench / "reports").glob("*.json")) \
            if (bench / "reports").is_dir() else []
        acc = Counter()
        acc["callees_seen"] = set()
        acc["repeat_examples"] = []
        if not reps:
            print(f"  {bench.name:<32} NO REPORTS -- contributes nothing, "
                  f"not zero")
            continue
        for r in reps:
            try:
                scan_report(r, acc)
            except Exception as e:                       # noqa: BLE001
                print(f"  {bench.name:<32} READ FAILED on {r.name}: {e}")
                acc["read_failures"] += 1
        print(f"  {bench.name:<32} reports {len(reps):>3}  "
              f"F-paths {acc['paths']:>5}  "
              f"in-unit {acc['decisions_in_unit']:>6}  "
              f"in-callee {acc['decisions_in_callee']:>6}  "
              f"callees {len(acc['callees_seen']):>3}  "
              f"paths-with-repeated-site {acc['paths_with_repeated_site']:>4}")
        if acc["repeat_examples"]:
            any_repeat = True
            for ex in acc["repeat_examples"][:3]:
                p, cond, fn, (n, s) = ex
                print(f"      repeat: callee `{fn}` site {s} x{n}  "
                      f"on {cond}  [{Path(p).name}]")
        for k in ("paths", "decisions_in_unit", "decisions_in_callee",
                  "paths_with_callee_decisions", "paths_with_repeated_site",
                  "callee_decisions_naming_a_unit_input", "read_failures"):
            grand[k] += acc[k]

    print("\n" + "=" * 78)
    print(f"  F-paths                                  {grand['paths']}")
    print(f"  ... of which have >=1 in-callee decision {grand['paths_with_callee_decisions']}")
    print(f"  decisions in the unit itself             {grand['decisions_in_unit']}")
    print(f"  decisions inside an inlined callee       {grand['decisions_in_callee']}")
    print(f"  paths with a REPEATED callee site        {grand['paths_with_repeated_site']}")
    print(f"  in-callee decisions naming a unit input  {grand['callee_decisions_naming_a_unit_input']}"
          "   (containment heuristic: a CANDIDATE clash, not a proof)")
    if grand["read_failures"]:
        print(f"  reports that failed to read              {grand['read_failures']}")

    print()
    if not any_repeat:
        print("VERDICT: no repeated callee decision site anywhere in the "
              "corpus.\n"
              "  On THIS corpus a name-keyed argument map is unambiguous, so "
              "the cheap fix\n"
              "  (publish `name -> value`) is correct HERE. It is NOT shown "
              "correct in general:\n"
              "  the inliner still binds one symbol per callee formal "
              "(goto_inline.cpp:109),\n"
              "  so a contract that calls one internal function twice in a "
              "path would break it.\n"
              "  Any implementation must therefore SUPPRESS the field when a "
              "callee formal is\n"
              "  bound more than once on a path, rather than trust this "
              "measurement to hold.")
    else:
        print("VERDICT: at least one path traverses a callee decision site "
              "twice.\n"
              "  That is EITHER a second call site (rebinds the formal -> a "
              "name-keyed map is\n"
              "  wrong) OR a loop inside the callee (does not rebind -> "
              "harmless). The report\n"
              "  publishes no call-site identity, so THIS SCRIPT CANNOT TELL "
              "THEM APART --\n"
              "  the missing datum is the same one the whole fix is about.\n"
              "  ==> the cheap name-keyed fix is NOT certified. Next step is "
              "to read the\n"
              "      callee's source for the units listed above and see "
              "whether it loops.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
