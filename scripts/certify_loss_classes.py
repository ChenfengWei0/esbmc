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
        "path. The generaliser says so itself: 'a BUDGET outcome, not a "
        "property of the path'. Raising the budget is the whole fix.",
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
        "⛔ THE MODELLING CLASS, AND IT IS THE EXTERNAL CALL'S SUCCESS BIT. "
        "MEASURED on farming::deposit (4 of these 15 paths): its six non-gate "
        "witnessed paths are THREE PAIRS -- (26,27) (246,247) (3622,3623) -- "
        "and each pair differs on EXACTLY ONE decision, the same one in all "
        "three: `!success` vs `!(!success)` in safeTransferFrom at flat.sol "
        "line 532, i.e. SafeERC20's `(bool success, ) = token.call(data)`. "
        "Every one of those paths carries `extcall_returns: []`, so the "
        "payloads are byte-identical and no coordinate can separate the pair. "
        "⚠ TWO DIFFERENT DROP SITES WEAR THIS ONE CLASS, and a repair at "
        "either alone moves only half the corpus: (a) a result bound to a "
        "named local or written in an assembly block IS harvested and then "
        "falls off the end of bmc.cpp's three-outcome classification (`else "
        "++ce.dropped_internal`) -- this is the B2_ExtcallSuccess PoC shape; "
        "(b) the low-level `(bool ok, ) = a.call(...)` destructuring, which is "
        "what safeTransferFrom uses, never reaches that classification at all "
        "because get_nondet_symbol returns nil and the step is skipped "
        "earlier. The corpus needs (b); the PoC that priced the repair at "
        "0->2 certified exercises (a).",
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
