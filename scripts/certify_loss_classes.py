#!/usr/bin/env python3
"""PRINT THE CLASSIFICATION STAGE 2 ALREADY MAKES for every path it refused to
certify, instead of arguing about which lever is the big one.

---- WHY THIS EXISTS -------------------------------------------------------

The generalisation rate has been quoted for days as `10 certified / 113
witnessed` -- one number, with no breakdown, and every proposal for what to
build next was argued from a hand-written PoC rather than from the 93 refusals
themselves. `solidity_path_generalise.py` already writes a REASON STRING beside
every uncertified path; nobody had ever printed their histogram.

The first run of this script (results_pieces_corpus.jsonl, binary head
a01ffdc4eb) said:

    48  A_BUDGET_no_round_finished        <- 52%, and in only THREE units
    15  D_UNSEPARATED_payload_identical   <- 16%
    14  B_BUDGET_shrink_exhausted
    14  C_PINS_empty_region
     2  E_no_cut_witness_differs

i.e. 62 of 93 (67%) are BUDGET or PIN outcomes -- knobs on the calling side --
and the modelling class that the extcall-return work addresses is 15. That
inverted the priority that was about to be built.

---- THE TWO RULES THIS SCRIPT OBEYS ---------------------------------------

(1) ⛔ AN UNRECOGNISED REASON IS NEVER FOLDED INTO A BUCKET. It is printed
    VERBATIM under Z_UNCLASSIFIED and counted separately, and the script exits
    nonzero if any exist. A classifier whose default arm silently absorbs new
    shapes is an always-true reader: it would keep reporting a tidy five-row
    table while the thing that actually changed goes unseen.

(2) ⛔ EVERY ROW'S `binary` FIELD IS PRINTED. A corpus figure is undated until
    the binary that produced it has been named; re-running after a rebuild and
    hanging the old numbers under the new head is a failure this project has
    already had three times. If the rows disagree about the binary, that is
    printed as a WARNING at the top and the table must not be read as one
    measurement.

usage:  python3 scripts/certify_loss_classes.py <results.jsonl> [more.jsonl ...]
"""
import sys
import json
import collections

# (class key, predicate on the reason string). ORDER MATTERS: the first match
# wins, so the specific test for the identical-payload shape must come before
# the generic "refuted with no single-coordinate cut" prefix it shares.
CLASSES = [
    (
        "A_BUDGET_no_round_finished",
        lambda m: m.startswith("no outer-box round finished"),
        "the outer-box round never completed, so NOTHING was measured for this "
        "path -- 'a BUDGET outcome, not a property of the path', in the "
        "generaliser's own words. ⛔ BUT RAISING THE CLOCK IS MEASURED NOT TO "
        "BE THE FIX, and an earlier version of this very line said it was. The "
        "round is EMISSION-bound, not solve-bound: solidity_path_generalise.py "
        "--help records a geometric bracket on EscrowSrc.withdraw that put "
        "only 148 queries in front of the solver in 300s, of which 6.9s was "
        "solving, while ~15000 claims were being instrumented -- and that "
        "about half the wall clock is solving while the other half is "
        "UNACCOUNTED work ESBMC does not timestamp, which nothing has yet "
        "identified. The ladder is MULTIPLICATIVE in coords x values x paths x "
        "2 directions, and the geometric bracket lays 258 values per "
        "coordinate per direction while IGNORING --probes: for "
        "EscrowDst::cancel (7 coordinates, 12 paths) that is ~43000 probes, so "
        "the round cannot finish at any clock this project would accept. "
        "MEASURED, arm 1, --run-timeout 180: level-0 finished in 33.1s, the "
        "bracket and the refine round each ran to exactly the 180s cap and "
        "reported ROUND MEASURED NOTHING with `[bracket] {}`. The tool's own "
        "prescription is to SHRINK THE LADDER (--skip-bracket, a smaller "
        "--probes) and see whether the round finishes, not to raise the clock.",
    ),
    (
        "B_BUDGET_shrink_exhausted",
        lambda m: m.startswith("shrink round budget exhausted"),
        "a counterwitness WAS found and it DIFFERS on named coordinates, but "
        "the shrink ran out of rounds before the region was cut. Also a "
        "budget, but a strictly better position than A: the differing "
        "coordinate is already named.",
    ),
    (
        "C_PINS_empty_region",
        lambda m: m.startswith("region is EMPTY"),
        "lo > hi under the current pins: the region has no domain in this "
        "slice, so certifying it would hold vacuously. A CORRECT REFUSAL, not "
        "a failure, and it should be taken OUT of the denominator rather than "
        "counted as a generalisation loss. MEASURED: all 14 sit on path enc=2 "
        "and nowhere else, enc=2 is certified ZERO times anywhere in the "
        "corpus, and on farming::deposit enc=2 is `path_depth 1, exit revert, "
        "revert_kind rollback` whose sole decision is the synthetic ABI gate "
        "`msg.value == 0` taken FALSE -- its payload is the only witnessed one "
        "with `msg.value: \"1\"`. Stage 2 pins msg.value to 0, so the path has "
        "no satisfying assignment and every free coordinate comes back empty. "
        "⚠ That is also a stage-1/stage-2 configuration inconsistency: stage 1 "
        "enumerates and witnesses a path that stage 2's own pin has already "
        "decided cannot occur, costing one enumeration and one outer-box round "
        "per unit.",
    ),
    (
        "D_UNSEPARATED_payload_identical",
        lambda m: "agrees with this path's counterexample on EVERY scalar"
        in m,
        "⛔ THE MODELLING CLASS, AND IT IS NOT ONE MECHANISM. Joining these "
        "path ids to their decision arrays (scripts/ce_pair_diff.py) splits "
        "the 15: aqua::pull 58,59,62,63 and farming::deposit 26,27,246,247 "
        "are separated by `!success` vs `!(!success)` in SafeERC20's "
        "safeTransferFrom, i.e. the low-level `(bool ok, ) = a.call(...)` "
        "success bit -- 8 of 15. EscrowDst::rescueFunds 58,62 are separated by "
        "`return_value$_computeAddress$1 != $address`, an INTERNAL call's "
        "return -- 2 of 15. farming::withdraw's 12,13,252,253 and "
        "farming::stopFarming's 119 are NOT ESTABLISHED (5 of 15) because the "
        "only reports on disk for them have a stale env harvest. "
        "⚠ THREE DROP SITES WEAR THIS ONE CLASS and a repair at any one alone "
        "moves only part of it: (a) a result bound to a named local or written "
        "in an assembly block IS harvested and falls off the end of bmc.cpp's "
        "three-outcome classification (`else ++ce.dropped_internal`) -- the "
        "B2_ExtcallSuccess PoC shape, and the one whose price of 0->2 "
        "certified was measured; (b) the low-level destructuring, which never "
        "reaches that classification because get_nondet_symbol's switch has no "
        "member case and returns nil, so bmc.cpp skips the step with a bare "
        "`continue` that counts nothing; (c) `return_value$...`, which is in "
        "the trace and is named verbatim in the report's own `decisions`, and "
        "which bmc.cpp then drops BY NAME as ESBMC bookkeeping alongside "
        "`$address` and `_bind_cname` -- a filter that is simply too wide. "
        "(c) also separates class-B pairs (rescueFunds 28,30 and withdraw "
        "1804,1805), so it is worth more than the 2 paths counted here.",
    ),
    (
        "E_no_cut_witness_differs",
        lambda m: m.startswith("refuted with no single-coordinate cut"),
        "refuted, the witness DOES differ, but no SINGLE coordinate cut is "
        "available -- the separation needs more than one coordinate at once.",
    ),
]


def classify(msg):
    for key, pred, _ in CLASSES:
        if pred(msg):
            return key
    return None


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rows = []
    for p in argv[1:]:
        with open(p) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    r["_src"] = p
                    rows.append(r)
    if not rows:
        # ⛔ An empty input is a hard failure, not an empty table. A table with
        # no rows reads as "nothing was lost", which is the opposite of "the
        # measurement did not happen".
        print("FATAL: no rows read from " + " ".join(argv[1:]))
        return 3

    # ---- THE BINARY, FIRST -------------------------------------------------
    bins = collections.Counter()
    for r in rows:
        b = r.get("binary")
        bins[json.dumps(b, sort_keys=True) if b is not None else "<ABSENT>"] += 1
    print("=" * 78)
    print("BINARY THAT PRODUCED THESE ROWS")
    print("=" * 78)
    for b, n in bins.most_common():
        print(f"  {n:4d} row(s)  {b}")
    if len(bins) > 1:
        print()
        print("  ⚠ THE ROWS DISAGREE ABOUT THE BINARY. This table is NOT one")
        print("    measurement and its totals must not be quoted as one.")
    print()

    # ---- BUCKETS BEFORE PATHS ---------------------------------------------
    # A unit that never produced a witness contributes ZERO uncertified paths,
    # so it is invisible in the per-path table below -- and it is more than
    # half the corpus. Printing the unit buckets first stops the path table
    # from being read as the whole loss.
    buckets = collections.Counter(r.get("bucket") for r in rows)
    print("=" * 78)
    print("UNIT BUCKETS (stage-1 outcome, BEFORE any path can be generalised)")
    print("=" * 78)
    for b, n in buckets.most_common():
        print(f"  {n:4d}  {b}")
    print(f"  ----\n  {len(rows):4d}  units total")
    print()
    print("  ⛔ NO-WITNESS-UNKNOWN and KILLED units contribute NO row to the")
    print("     path table below. Their loss is at STAGE 1 and no amount of")
    print("     generaliser work can reach them.")
    print()

    tab = collections.defaultdict(collections.Counter)
    tot = collections.Counter()
    unclassified = []
    n_witnessed = 0
    n_certified = 0
    for r in rows:
        w = r.get("witnessed")
        if isinstance(w, int):
            n_witnessed += w
        cert = r.get("certified") or {}
        n_certified += len(cert)
        key = f"{r['benchmark']}::{r['unit']}"
        for enc, msg in (r.get("not_certified") or {}).items():
            k = classify(msg)
            if k is None:
                k = "Z_UNCLASSIFIED"
                unclassified.append((key, enc, msg))
            tab[key][k] += 1
            tot[k] += 1

    cols = [c[0] for c in CLASSES] + (
        ["Z_UNCLASSIFIED"] if "Z_UNCLASSIFIED" in tot else []
    )
    w_unit = max([len(k) for k in tab] + [15])
    print("=" * 78)
    print("UNCERTIFIED PATHS, BY UNIT AND BY REASON CLASS")
    print("=" * 78)
    head = f"{'benchmark::unit':{w_unit}s} " + " ".join(
        f"{c.split('_')[0]:>4s}" for c in cols
    )
    print(head + "   wall_s  unit_to  run_to  refine  shrink  probes")
    for r in rows:
        key = f"{r['benchmark']}::{r['unit']}"
        if key not in tab:
            continue
        c = tab[key]
        print(
            f"{key:{w_unit}s} "
            + " ".join(f"{c.get(k, 0):4d}" for k in cols)
            + f"   {str(r.get('wall_s')):>6s}  {str(r.get('unit_timeout_s')):>7s}"
            + f"  {str(r.get('run_timeout_s')):>6s}  {str(r.get('refine_rounds')):>6s}"
            + f"  {str(r.get('shrink_rounds')):>6s}  {str(r.get('probes')):>6s}"
        )
    print()
    print(
        f"{'TOTAL':{w_unit}s} " + " ".join(f"{tot.get(k, 0):4d}" for k in cols)
    )
    print()

    print("=" * 78)
    print("THE CLASSES, LARGEST FIRST -- AND WHAT EACH ONE MEANS")
    print("=" * 78)
    lookup = {k: d for k, _, d in CLASSES}
    total_paths = sum(tot.values())
    for k, n in tot.most_common():
        pct = (100.0 * n / total_paths) if total_paths else 0.0
        print(f"  {n:4d}  ({pct:4.1f}%)  {k}")
        if k in lookup:
            for chunk in _wrap(lookup[k], 68):
                print(f"              {chunk}")
        print()
    print(f"  {total_paths:4d}  uncertified paths total")
    print()
    print(f"  witnessed paths : {n_witnessed}")
    print(f"  certified paths : {n_certified}")
    print(f"  uncertified     : {total_paths}")
    if n_witnessed and n_certified + total_paths != n_witnessed:
        # NOT fatal, and named rather than hidden: a KILLED unit can report a
        # witnessed count while its certified/not_certified maps are empty,
        # because the kill landed between the two records.
        print(
            f"  ⚠ certified + uncertified = {n_certified + total_paths}, which"
            f" is NOT the witnessed total {n_witnessed}. The difference is"
            " paths witnessed by stage 1 whose unit died before stage 2"
            " recorded a verdict for them (see the KILLED bucket above)."
        )
    print(
        f"  generalisation rate = {n_certified}/{n_witnessed}"
        + (
            f" = {100.0 * n_certified / n_witnessed:.1f}%"
            if n_witnessed
            else ""
        )
    )
    print()

    if unclassified:
        print("=" * 78)
        print("⛔ UNRECOGNISED REASON SHAPES -- PRINTED VERBATIM, NOT BUCKETED")
        print("=" * 78)
        for key, enc, msg in unclassified:
            print(f"  {key}  enc={enc}")
            print(f"      {msg}")
        print()
        print(
            "  The classifier does not know these shapes. Either the"
            " generaliser gained a new refusal reason, or a class predicate"
            " above is wrong. Fix the predicate before quoting the table."
        )
        return 1
    return 0


def _wrap(s, n):
    out, cur = [], ""
    for word in s.split():
        if len(cur) + len(word) + 1 > n:
            out.append(cur)
            cur = word
        else:
            cur = (cur + " " + word).strip()
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv))
