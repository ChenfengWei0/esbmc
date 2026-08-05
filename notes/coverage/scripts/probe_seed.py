#!/usr/bin/env python3
"""PROBE SEEDING: turn a path-coverage report's MULTIPLE WITNESSES per path into
a per-path per-coordinate KNOWN-INSIDE interval, so the ladder never bisects a
range it already has two members of.

THE IDEA
--------
Region generalisation starts its ladder from the whole type range and bisects;
every step is a solver query, and on a 256-bit coordinate the step count is what
the budget goes on. But a SECOND input that walks the SAME path is a free lower
bound on the region: if x = 1 and x = 100 both walk path pi, then [1, 100] is
known to be inside pi's domain before any query, and the search only has to look
OUTSIDE it. The unknown interval shrinks from the whole type to two tails.

Until now the only extra inputs the pipeline had were the SIBLINGS'
counterexamples, and those are used exclusively to build EXCLUSION bounds --
they say where a path is NOT. This says where a path IS. The two are different
information and neither substitutes for the other.

WHERE THE PROBES COME FROM, AND WHY THEY COST NOTHING TO ATTRIBUTE
------------------------------------------------------------------
`--all-witnesses --max-witnesses N` makes each REFUTED claim report N distinct
input tuples instead of one. Under `--solidity-path-coverage` a refuted claim IS
a path, so every witness arrives already attributed -- there is no "which path
does this input walk" question to answer and no query to pay for it.

MEASURED on a 17-line contract, same unit and same bound, one flag apart:
    without:  witnesses_total  6,  F_with_multiple_witnesses 0
    with -8:  witnesses_total 48,  F_with_multiple_witnesses 6
and `U_undecided` stayed 2 in both -- the two paths that merely held inside the
bound gained NO witnesses, which is the negative control: the flag cannot
manufacture evidence for a path nothing reaches.

⛔ ONE DIRECTION ONLY IS EVIDENCE
A coordinate that takes MORE THAN ONE value across the witnesses is proof it is
not a point -- and a proof that any region reporting it as a point was reporting
a coordinate set chosen too narrow, not a domain. A coordinate that takes ONE
value proves NOTHING: the solver returns whatever model it likes and is under no
obligation to vary anything it was not asked about. Measured in exactly that
shape on a branch-coverage run -- one coordinate varied over four values while
every other stayed at 0 in the same eight witnesses. Every field below that
carries a single value is therefore labelled `varied: false` and never
`is_point`.

⛔ THE SEED IS NOT A CERTIFIED REGION
`[min, max]` here means "both endpoints are known members", not "everything
between them is". For a path whose domain is disconnected the interior may hold
non-members. The seed is a STARTING BRACKET for the ladder and an argument for
where NOT to spend queries; the certification query is still what decides.
"""
import argparse
import json
import os
import sys


def coord_values(node):
    """Every scalar a witness (or a claim) offers, under the coordinate names
    the generalise driver uses. Kept as one function so the witness and the
    claim's own counterexample are read by the SAME code -- two readers of one
    fact is how this project has already made two ledgers disagree."""
    out = {}
    for k, v in (node.get("inputs") or {}).items():
        out[k] = v
    for group, vals in (node.get("env") or {}).items():
        if isinstance(vals, dict):
            for k, v in vals.items():
                out[f"{group}.{k}"] = v
        else:
            out[group] = vals
    for k, v in (node.get("entry_storage") or {}).items():
        out[f"state.{k}"] = v
    return out


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", help="a cov-report.json produced WITH "
                                   "--all-witnesses")
    ap.add_argument("--out", default=None,
                    help="write the seed JSON here; without it, print only")
    ap.add_argument("--unit", default=None,
                    help="restrict to one unit's claims (path_function)")
    args = ap.parse_args()

    d = json.load(open(args.report))
    claims = d.get("claims", [])
    summary = d.get("summary", {})

    # REFUSE rather than produce an empty seed. A report taken without
    # --all-witnesses has exactly one witness per path, so every seed would be a
    # single point and the caller would read "the probes bought nothing" when
    # the truth is "the probes were never collected".
    multi = summary.get("F_with_multiple_witnesses")
    if multi is None:
        print("[seed] REFUSED: this report has no F_with_multiple_witnesses "
              "field, so it predates multi-witness reporting and cannot say "
              "whether probes were collected.", file=sys.stderr)
        return 2
    if multi == 0:
        print(f"[seed] REFUSED: F_with_multiple_witnesses is 0 in "
              f"{args.report}. Re-run the collection with "
              f"--all-witnesses --max-witnesses N; without it every path has "
              f"one witness and every seed would be a point that means "
              f"'not collected', not 'not wide'.", file=sys.stderr)
        return 2

    seeds = {}
    skipped = 0
    for c in claims:
        if c.get("status") != "F":
            skipped += 1
            continue
        if args.unit and c.get("path_function") != args.unit:
            skipped += 1
            continue
        vecs = [coord_values(c)]
        for w in (c.get("witnesses") or []):
            vecs.append(coord_values(w))
        names = set()
        for v in vecs:
            names |= set(v)
        per = {}
        for n in sorted(names):
            vals = []
            for v in vecs:
                if n in v:
                    try:
                        vals.append(int(v[n]))
                    except (TypeError, ValueError):
                        pass
            if not vals:
                continue
            distinct = sorted(set(vals))
            per[n] = {
                "lo": str(min(vals)),
                "hi": str(max(vals)),
                "distinct": len(distinct),
                # POSITIVE DIRECTION ONLY. See the module docstring: one value
                # is not evidence of a point, so there is deliberately no
                # `is_point` field anywhere in this output for anyone to read
                # the wrong way.
                "varied": len(distinct) > 1,
                "members": [str(x) for x in distinct],
            }
        # THE KEY IS (unit, path), NEVER path ALONE. A path number is an
        # ordinal within ONE unit's enumeration, so two units of the same
        # contract both have a path 2. Keyed on the number alone the second
        # claim SILENTLY overwrites the first: measured on a two-function
        # contract, 6 refuted claims and 54 vectors came out as 4 seeds and 36
        # vectors, and nothing in the output said so. The collision is a hard
        # failure rather than a merge, because a merged seed spans two units'
        # domains and is a member of neither.
        key = f"{c.get('path_function')}:{c.get('path_id')}"
        if key in seeds:
            print(f"[seed] REFUSED: duplicate key {key!r}. Two claims of the "
                  f"same unit share a path id, so this report cannot be keyed "
                  f"this way and a seed built from it would belong to neither "
                  f"claim.", file=sys.stderr)
            return 2
        seeds[key] = {
            "path_function": c.get("path_function"),
            "path_id": c.get("path_id"),
            "path_depth": c.get("path_depth"),
            "exit_kind": c.get("exit_kind"),
            "witnesses": len(vecs),
            "coords": per,
        }

    result = {
        "schema": "probe-seed/2",
        "source_report": os.path.abspath(args.report),
        "bound": summary.get("bound"),
        "witnesses_total": summary.get("witnesses_total"),
        "F_with_multiple_witnesses": multi,
        "paths_seeded": len(seeds),
        "claims_skipped": skipped,
        "seeds": seeds,
    }

    print(f"[seed] {len(seeds)} path(s) seeded from "
          f"{summary.get('witnesses_total')} witness(es); "
          f"{skipped} claim(s) skipped (not F, or another unit)")
    total_wide = 0
    for pid, s in seeds.items():
        wide = [(n, v) for n, v in s["coords"].items() if v["varied"]]
        total_wide += len(wide)
        head = f"[seed]   {pid} ({s['witnesses']} vector(s), {s['exit_kind']})"
        if not wide:
            print(head + ": no coordinate varied "
                         "-- NOT evidence that any of them is a point")
            continue
        print(head + ":")
        for n, v in wide:
            span = int(v["hi"]) - int(v["lo"])
            print(f"[seed]     {n}: KNOWN-INSIDE [{v['lo']}, {v['hi']}] "
                  f"({v['distinct']} distinct member(s), span {span})")
    print(f"[seed] {total_wide} (path, coordinate) pair(s) proved NOT a point")

    if args.out:
        tmp = args.out + ".tmp"
        with open(tmp, "w") as f:
            json.dump(result, f, indent=2)
        os.replace(tmp, args.out)
        print(f"[seed] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
