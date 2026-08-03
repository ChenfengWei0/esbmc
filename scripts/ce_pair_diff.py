#!/usr/bin/env python3
"""For every witnessed path in a coverage report: print its payload FINGERPRINT,
group paths whose fingerprints are IDENTICAL, and print the one decision that
separates each such group.

---- WHY -------------------------------------------------------------------

Stage 2 refuses 15 of its 93 uncertified paths with "the witness agrees with
this path's counterexample on EVERY scalar quantity in the payload", i.e. two
paths that the model distinguishes and the REPORT does not. Which decision the
model used is written in the report's `decisions` array and had never been read
off. Reading it on farming::deposit found three pairs separated by exactly one
decision -- `!success` vs `!(!success)` in SafeERC20's
`(bool success, ) = address(token).call(data)`.

That was ONE unit, 4 of the 15 paths. This script exists so the statement can be
made about the units it is actually true of, by name, instead of generalised
from a single sample.

It also prints, for every report, whether path enc=2 is the synthetic ABI value
gate -- the shape behind the 14 "region is EMPTY under the current pins"
refusals, which are CORRECT refusals that belong outside the denominator.

---- ⛔ THE CELL GUARD -----------------------------------------------------

Every report's `summary.bound` is printed, and if two reports disagree about
max_tx or unwind the script SAYS SO and exits nonzero. A path table built from a
focus/tx=1 report and a whole-contract/tx=2 report is two measurements wearing
one name; this project has a standing rule that those two command lines are
never cross-quoted, and a script that silently merged them would break it while
looking tidy.

usage:  python3 scripts/ce_pair_diff.py <cov-report.json> [more ...]
"""
import sys
import json
import collections


def fingerprint(c):
    """Everything a consumer of the report could use to tell paths apart.

    Deliberately INCLUSIVE: inputs, env, entry_storage, final_state,
    extcall_returns and the return value. If two paths agree on all of it then
    no coordinate exists for the generaliser to cut on, whatever it tries.
    """
    return json.dumps(
        {
            "inputs": c.get("inputs"),
            "env": c.get("env"),
            "entry_storage": c.get("entry_storage"),
            "final_state": c.get("final_state"),
            "extcall_returns": c.get("extcall_returns"),
            "return_value": c.get("return_value"),
            "state_written_value_unavailable": c.get(
                "state_written_value_unavailable"
            ),
        },
        sort_keys=True,
    )


def decisions_of(c):
    out = []
    for d in c.get("decisions") or []:
        out.append(
            (
                d.get("index"),
                d.get("function"),
                d.get("line"),
                d.get("arm"),
                d.get("branch_claim"),
                bool(d.get("synthetic_abi_gate")),
            )
        )
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    bounds = {}
    reports = []
    for p in argv[1:]:
        with open(p) as f:
            d = json.load(f)
        reports.append((p, d))
        b = d.get("summary", {}).get("bound", {})
        bounds[json.dumps({k: b.get(k) for k in ("kind", "max_tx", "unwind")},
                          sort_keys=True)] = bounds.get(
            json.dumps({k: b.get(k) for k in ("kind", "max_tx", "unwind")},
                       sort_keys=True), 0) + 1

    print("=" * 78)
    print("THE CELL EVERY REPORT WAS MEASURED IN")
    print("=" * 78)
    for b, n in bounds.items():
        print(f"  {n:3d} report(s)  {b}")
    mixed = len(bounds) > 1
    if mixed:
        print()
        print("  ⛔ MIXED CELLS. These reports are not comparable and this")
        print("     table must not be quoted. Re-run with one cell at a time.")
    print()

    n_pairs_total = 0
    n_extcall_pairs = 0
    n_stale = [0]
    for p, d in reports:
        F = [c for c in d["claims"] if c.get("status") == "F"]
        print("=" * 78)
        print(f"{p}")
        print(f"  witnessed: {len(F)}")
        by_fp = collections.defaultdict(list)
        for c in F:
            by_fp[fingerprint(c)].append(c)

        # enc=2: is it the synthetic ABI value gate?
        #
        # ⛔ AND IT DOUBLES AS THIS REPORT'S STALENESS TEST. enc=2 is the gate
        # condition `msg.value == 0` taken FALSE, so the path REQUIRES a nonzero
        # msg.value and a correct payload must publish one. A report that says
        # msg.value == 0 here was written before the last-write-wins fix to the
        # environment harvest (bmc.cpp: the environment is re-seeded per
        # transaction, and first-wins published the declaration-time value).
        # Its env block is WRONG, which makes paths look alike that are not --
        # so the fingerprint grouping below is unusable and only the DECISIONS,
        # which come from the instrumenter and not from the harvest, may be
        # quoted from it.
        stale = False
        for c in F:
            if str(c.get("path_id")) == "2":
                ds = decisions_of(c)
                gate = [x for x in ds if x[5]]
                env = c.get("env") or {}
                mv = env.get("msg.value") if isinstance(env, dict) else None
                if (
                    len(ds) == 1
                    and gate
                    and gate[0][3] == "fall-through"
                    and str(mv) == "0"
                ):
                    stale = True
                print(
                    f"  enc=2: depth={c.get('path_depth')} "
                    f"exit={c.get('exit_kind')} revert={c.get('revert_kind')} "
                    f"msg.value={mv!r} decisions={len(ds)} "
                    f"synthetic_abi_gate_steps={len(gate)}"
                )
                for x in ds:
                    print(
                        f"       [{x[0]}] {x[3]:>12s}  {x[4]}   "
                        f"({x[1]}:{x[2]}){'  <-- SYNTHETIC ABI GATE' if x[5] else ''}"
                    )

        if stale:
            print(
                "  ⛔ STALE ENV HARVEST. enc=2 takes the `msg.value == 0` gate "
                "FALSE, so this path requires msg.value != 0, yet the payload "
                "publishes msg.value == 0. This report predates the "
                "last-write-wins fix to the environment harvest. Its env block "
                "is wrong, paths that differ only in the environment collapse "
                "into one fingerprint, and THE GROUPING BELOW MUST NOT BE "
                "QUOTED -- only the `decisions`, which the instrumenter writes "
                "and the harvest never touches."
            )
            n_stale[0] += 1

        groups = [g for g in by_fp.values() if len(g) > 1]
        if not groups:
            print("  no two witnessed paths share a payload fingerprint")
            print()
            continue
        for g in groups:
            ids = [str(c.get("path_id")) for c in g]
            n_pairs_total += 1
            print(f"  ⛔ IDENTICAL PAYLOAD across paths {', '.join(ids)}")
            seqs = [decisions_of(c) for c in g]
            L = min(len(s) for s in seqs)
            differing = []
            for i in range(L):
                vals = {(s[i][1], s[i][2], s[i][3], s[i][4]) for s in seqs}
                if len(vals) > 1:
                    differing.append((i, [s[i] for s in seqs]))
            if len({len(s) for s in seqs}) > 1:
                print(
                    "     ⚠ the paths have DIFFERENT decision-sequence lengths "
                    f"({sorted({len(s) for s in seqs})}); only the common "
                    "prefix is compared"
                )
            if not differing:
                print(
                    "     ⚠ NO differing decision in the common prefix -- the "
                    "separation is not in the recorded decisions at all"
                )
            for i, rows in differing:
                print(f"     decision index {rows[0][0]}:")
                for r in rows:
                    print(
                        f"        {r[3]:>12s}  {r[4]}   ({r[1]}:{r[2]})"
                    )
                claims = " ".join(r[4] or "" for r in rows)
                if "success" in claims or "ok" in claims.split():
                    n_extcall_pairs += 1
                    print(
                        "        ^ names a call-result bit; this group is the "
                        "external-call class"
                    )
        print()

    print("=" * 78)
    print(
        f"groups with an identical payload: {n_pairs_total}; "
        f"of which separated by a call-result bit: {n_extcall_pairs}"
    )
    if n_stale[0]:
        print(
            f"⛔ {n_stale[0]} of {len(reports)} report(s) have a STALE env "
            "harvest (see above). Their groupings are not evidence; join the "
            "path ids to a CURRENT certification ledger instead, and quote "
            "only the decisions from here."
        )
    print("=" * 78)
    return 1 if (mixed or n_stale[0]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
