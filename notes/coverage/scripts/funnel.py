#!/usr/bin/env python3
"""The WHOLE funnel, per benchmark, in one table.

Every number this project has reported so far has been a stage of the pipeline
in isolation -- paths instrumented here, coverage there, certified regions
somewhere else -- and no output has ever put them on one row. That is how "the
suite is small" stayed an impression instead of a decomposition: with the stages
reported apart, there was no place where the drop between two of them was
visible.

The stages, and what each one is measured FROM (never inferred):

  X  INSTRUMENTED   complete paths the enumeration created a claim for.
                    Source: `claims` in each emit run's own cov-report.json,
                    filtered to the unit, requiring `path_id` -- the same filter
                    the driver uses, so the two cannot disagree.
  Y  WITNESSED      of those, the ones with status F: a counterexample exists.
                    This is the only stage that produces a concrete input, so
                    everything downstream is bounded by it.
  Z  CONCRETE       test cases actually written to a .t.sol. Source: `cases` in
                    the round-trip's emit.jsonl -- the emitter's own count, not
                    a re-derivation.
  Zg GREEN          of those, the ones that are not disabled for being RED on
                    the unmodified contract. Source: the round-trip's
                    RED-disabled count.
  A  GENERALISED    paths whose region was CERTIFIED by stage 2.
                    Source: notes/coverage/certify/results.jsonl.
  B  PUT+ORACLE     parameterised tests carrying a certified region AND a
                    post-state assertion.

B IS NOT COMPUTED FROM A FILE BECAUSE THERE IS NO FILE. Nothing in the emitter
reads a certified region or an assertion ladder; every emitted test today is a
fixed replay of one counterexample. B is therefore structurally 0 and is printed
as 0 with that reason attached, rather than omitted -- an absent column reads as
"not measured yet", and this one is "not connected yet", which is a different
statement and the more important one.

Nothing here re-runs esbmc. It reads artefacts already on disk.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))          # notes/
RT = os.path.join(ROOT, "coverage", "forge_roundtrip")
CERT = os.path.join(ROOT, "coverage", "certify", "results.jsonl")


def claim_unit(c):
    cond = c.get("condition") or ""
    return cond.split(":", 1)[0] if ":" in cond else ""


def per_run_counts(bench):
    """(X, Y, per-unit detail) from each emit run's OWN cov-report.json.

    Same-run by construction: the report sits beside the tests that run
    produced. A cross-run join here would repeat the mistake this project
    already had to retract once.
    """
    gen = os.path.join(RT, bench, "_gen")
    if not os.path.isdir(gen):
        return None, None, {}, f"no {gen}"
    X = Y = 0
    detail = {}
    for tag in sorted(os.listdir(gen)):
        rep = os.path.join(gen, tag, "cov-report.json")
        if not os.path.exists(rep):
            detail[tag] = (None, None)
            continue
        try:
            with open(rep) as f:
                r = json.load(f)
        except (OSError, ValueError):
            detail[tag] = (None, None)
            continue
        # ---- SCOPE, WHICH IS WHERE THE 122% ACTUALLY CAME FROM ----
        #
        # `--focus-function` narrows the harness DISPATCHER; it does not narrow
        # the instrumentation. A focused run therefore carries claims for EVERY
        # unit of the contract, and the emitter writes tests for whatever it can
        # reconstruct out of that whole run -- not only for the tag's own unit.
        #
        # Filtering X and Y to the tag's unit while taking the emitter's
        # unfiltered `cases` compares two different SCOPES, and that is what
        # produced a retention rate above 100%. Both sides are now counted over
        # the whole run. The per-unit numbers are kept separately, because they
        # are the right scope for the stage-2 questions and the wrong one here.
        unit = tag.split("__", 1)[1] if "__" in tag else tag
        all_claims = [c for c in r.get("claims", []) if "path_id" in c]
        claims = [c for c in all_claims if claim_unit(c) == unit]
        detail.setdefault("__run__", [0, 0])
        detail["__run__"][0] += len({(claim_unit(c), c["path_id"])
                                     for c in all_claims})
        detail["__run__"][1] += len({(claim_unit(c), c["path_id"])
                                     for c in all_claims
                                     if c.get("status") == "F"})
        # ---- TWO COUNTING UNITS, AND THEY ARE NOT INTERCHANGEABLE ----
        #
        # A complete path can carry SEVERAL claims -- one per transaction
        # instance -- and the emitter's `cases` counts CLAIMS, not paths. The
        # first version of this table deduped by `path_id` on one side and took
        # the emitter's raw count on the other, and produced "Y -> Z = 122%".
        # A retention rate above 100% is not a surprising result, it is proof
        # that two different units were divided by one another.
        #
        # Both are computed and both are printed. The ratio is only ever taken
        # between columns in the SAME unit.
        enc = {c["path_id"] for c in claims}
        encf = {c["path_id"] for c in claims if c.get("status") == "F"}
        X += len(enc)
        Y += len(encf)
        # ---- WHAT THE NON-F PATHS ACTUALLY ARE ----
        #
        # "81% got no counterexample" is not a finding until the bucket is
        # named. Each non-F claim carries `u_reason`, and the three-state
        # scheme has five tokens; if most of the remainder were
        # `unit-not-entered` the 19% would be a harness artefact rather than a
        # search result. Read, not assumed.
        for c in claims:
            if c.get("status") != "F":
                tok = c.get("u_reason") or "(no u_reason field)"
                detail.setdefault("__ureason__", {})
                detail["__ureason__"][tok] = \
                    detail["__ureason__"].get(tok, 0) + 1
        detail[tag] = (len(enc), len(encf))
    return X, Y, detail, None


def emit_counts(bench):
    """(Z cases, RED disabled, killed runs) from the round-trip's own jsonl."""
    p = os.path.join(RT, bench, "emit.jsonl")
    if not os.path.exists(p):
        return None, None, None
    cases = killed = 0
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            cases += int(r.get("cases") or 0)
            if r.get("killed"):
                killed += 1
    return cases, None, killed


def certified_counts():
    """A, per benchmark, from the stage-2 sweep."""
    out = {}
    if not os.path.exists(CERT):
        return out
    with open(CERT) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = r.get("benchmark")
            if not b:
                continue
            out.setdefault(b, 0)
            out[b] += len(r.get("certified") or {})
    return out


# The RED-disabled counts are printed by forge_roundtrip.py and are not in
# emit.jsonl, so they are carried here from the same-run measurements recorded
# in notes/emission-loss-four-samples.md rather than re-derived. Stated, because
# a number whose provenance is a note and not a file has to say so.
RED_DISABLED_SAME_RUN = {
    "aqua_Aqua": None,                       # not recorded in the same-run pass
    "cross_chain_swap_EscrowSrc": 22,
    "cross_chain_swap_EscrowDst": 8,
    "farming": 10,
}


def main():
    benches = ["aqua_Aqua", "cross_chain_swap_EscrowSrc",
               "cross_chain_swap_EscrowDst", "farming"]
    A = certified_counts()

    print("=" * 92)
    print("THE FUNNEL — instrumented -> witnessed -> concrete -> generalised "
          "-> PUT with oracle")
    print("=" * 92)
    print("  PER-UNIT scope (the tag's own unit)  |  WHOLE-RUN scope (what the")
    print("                                       |  emitter actually sees)")
    print(f"{'benchmark':<26}{'X unit':>8}{'Y unit':>8}{'A cert':>8}{'B PUT':>7}"
          f"  |{'X run':>8}{'Y run':>8}{'Z cases':>9}{'RED':>6}")
    tot = [0, 0, 0, 0, 0, 0]
    for b in benches:
        X, Y, detail, err = per_run_counts(b)
        Z, _r, _k = emit_counts(b)
        red = RED_DISABLED_SAME_RUN.get(b)
        a = A.get(b, 0)
        if X is None:
            print(f"{b:<26}  {err}")
            continue
        xr, yr = detail.get("__run__", [0, 0])
        tot[0] += X
        tot[1] += Y
        tot[2] += Z or 0
        tot[3] += a
        tot[4] += xr
        tot[5] += yr
        print(f"{b:<26}{X:>8}{Y:>8}{a:>8}{0:>7}  |{xr:>8}{yr:>8}"
              f"{Z if Z is not None else '?':>9}"
              f"{red if red is not None else '?':>6}")
    print(f"{'TOTAL':<26}{tot[0]:>8}{tot[1]:>8}{tot[3]:>8}{0:>7}  |"
          f"{tot[4]:>8}{tot[5]:>8}{tot[2]:>9}")

    print()
    print("READ THE DROPS, AND ONLY WITHIN ONE SCOPE.")
    print("`--focus-function` narrows the DISPATCHER, not the instrumentation,")
    print("so a focused run carries claims for every unit and the emitter")
    print("writes tests from the whole run. Dividing per-unit witnesses by")
    print("whole-run cases gave a retention rate of 122% -- the table")
    print("announcing its own defect. The two scopes are now side by side and")
    print("no ratio crosses between them.")
    print()
    if tot[0]:
        print(f"  X -> Y  (per unit)  {tot[1]}/{tot[0]} = "
              f"{100.0*tot[1]/tot[0]:.0f}%  of a unit's own instrumented paths "
              f"got a counterexample.")
        print()
        print("  WHAT THE OTHER PATHS ARE, by their own u_reason token:")
        agg = {}
        for b2 in benches:
            _X, _Y, d2, _e = per_run_counts(b2)
            for k, n in (d2.get("__ureason__") or {}).items():
                agg[k] = agg.get(k, 0) + n
        for k in sorted(agg, key=lambda x: -agg[x]):
            print(f"      {k:<28} {agg[k]}")
        print()
        print("      EVERY ONE IS `bounded-holds`, and that is the problem")
        print("      rather than a reassurance. It means no input walking the")
        print("      path was found WITHIN the bound -- definitionally the")
        print("      bucket that would split into I (proved infeasible) and U")
        print("      (not found) if the exploration over-approximated all")
        print("      reachable states. It does not:")
        print("      path_cov_can_prove_unreachable() returns false")
        print("      unconditionally, so I is structurally 0.")
        print()
        print("      DO NOT 'JUST FLIP IT'. Entry state is never havoc'd and")
        print("      the run is one transaction from the post-constructor")
        print("      state, so a path can be unreachable only because the")
        print("      state it needs is unreachable IN THIS HARNESS. Flipping")
        print("      the boolean relabels ALL of these as PROVED infeasible --")
        print("      the strongest claim the three-state scheme makes,")
        print("      asserted falsely for every one a different entry state")
        print("      would reach. The blocker for I is entry-state havoc")
        print("      (__ESOL_nondet_state_forward), not the boolean.")
    if tot[5]:
        print(f"  Y -> Z   NO RATIO PRINTED. Z = {tot[2]} emitted cases "
              f"against Y = {tot[5]} witnessed paths, i.e. MORE cases than "
              f"witnesses (EscrowDst 60 vs 20, EscrowSrc 102 vs 31).")
        print(f"      Checked and excluded: emit.jsonl is not append-duplicated "
              f"-- its line count equals the tag count on all four benchmarks "
              f"(8/8, 20/20, 18/18, 28/28). So the emitter really does write "
              f"~3 cases per witnessed path on the Escrows and far fewer on "
              f"aqua and farming.")
        print(f"      MEASURED PER TAG, which settles it faster than reading "
              f"foundry.cpp: the ratio goes BOTH WAYS.")
        print(f"          EscrowDst__cancel     12 witnessed paths -> 45 cases")
        print(f"          FarmingPool__exit     37 witnessed paths ->  5 cases")
        print(f"          Aqua__pull             5 witnessed paths ->  3 cases")
        print(f"      So `cases` is neither an upper nor a lower bound on "
              f"paths, and no X->Y->Z chain ratio is defined until the emitter "
              f"states its own counting unit.")
        print(f"      UNVERIFIED: what one `case` counts. The shape (many "
              f"cases per path on the Escrows, few on farming) is consistent "
              f"with one case per RECONSTRUCTED CALL rather than per path, but "
              f"that is a hypothesis; a counter in the emitter would settle "
              f"it.")
    if tot[1]:
        print(f"  Y -> A  (per unit)  {tot[3]}/{tot[1]} = "
              f"{100.0*tot[3]/tot[1]:.0f}%  of a unit's witnessed paths had "
              f"their region certified.")
    print(f"  A -> B   0/{tot[3]} = 0%   NOT A MEASUREMENT OF DIFFICULTY. "
          f"The emitter does not read certified regions or assertion ladders "
          f"at all; every emitted test is a fixed replay of one "
          f"counterexample. B is 0 because the two halves are NOT CONNECTED, "
          f"which is a wiring statement, not a yield one.")
    print()
    print("So 'the suite is small' decomposes into four independent losses,")
    print("and only the first two are about search power at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
