#!/usr/bin/env python3
"""The branch-coverage gate: does complete-path enumeration reach at least as
many canonical decisions as the locked branch-coverage dataset did?

THE COUNTING UNIT, STATED BEFORE ANY NUMBER IS PRINTED
------------------------------------------------------
Not branch arms. Not paths. The unit is a **canonical decision**, defined by
notes/coverage/METHODOLOGY.md (LOCKED 2026-05-20) §2:

  * derived from an AST walk of the ORIGINAL Solidity source, never from any
    tool's runtime output;
  * node kinds: IfStatement, Conditional, WhileStatement, ForStatement,
    DoWhileStatement, FunctionCall(require), FunctionCall(assert),
    BinaryOperation('&&'), BinaryOperation('||');
  * each decision contributes ONE (file, line) entry -- two decisions on the
    same line collapse to one;
  * explicitly EXCLUDED: try/catch, virtual dispatch, implicit reverts
    (overflow, division-by-zero), inline-assembly Yul branches.

`ast_decisions.canonical_decisions()` returns exactly this set, keyed by
original file, with each decision identified by its FLAT LINE NUMBER. That
flat line number is the identity used on both sides here.

Two earlier framings of this gate were wrong and are recorded so they are not
re-derived:

  * "branch arms" -- wrong unit. The locked dataset's `rawBranches: 16` on aqua
    is ESBMC's raw arm count; the comparison denominator is `branchesTotal: 8`,
    which is decisions. Comparing our arms against their decisions would have
    been a factor-of-two error dressed up as a result.
  * "compare the SET of reached decisions" -- impossible against this dataset.
    METHODOLOGY §4 measures ESBMC reach as "the number of unique flat-lines
    reached inside F's block, capped by the file's canonical decision count".
    It is a COUNT, not an identified set; the JSON records no per-decision
    identity. So the comparison is count vs count, and the same capping rule
    must be applied to our side or the two numbers are not commensurable.

WHY THE BASELINE IS NOT THE NATIVE TEST SUITE
---------------------------------------------
The bar is the LOCKED dataset's `esbmcReached`, i.e. what ESBMC's own branch
coverage achieved -- not `nativeReached`. METHODOLOGY §10 documents two reach
gaps that depress the ESBMC column (crypto-inversion-guarded paths;
constructor invariants narrowing post-construction state), so the ESBMC column
is itself below the native one on some benchmarks. Clearing the ESBMC column is
the stated gate; clearing the native column is a different, harder question.

Usage:
    python3 notes/branch_gate.py                 # baseline side only
    python3 notes/branch_gate.py <cov-report.json> [...]   # both sides
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "coverage" / "scripts"))
import ast_decisions  # noqa: E402

DATA = HERE / "coverage" / "data"

BENCHES = [
    "aqua_Aqua",
    "cross_chain_swap_EscrowDst",
    "cross_chain_swap_EscrowSrc",
    "farming",
    "limit_order_protocol",
    "st1inch_St1inch",
]


def baseline(bench):
    """The locked dataset's own numbers, read out rather than recomputed.

    `no_function` is the whole-contract Pair-1 run; `per_function` is the
    union over per-method --focus-function runs (Pair 2). Both report against
    the same canonical denominator by construction (METHODOLOGY 8.1), so a
    mismatch between the two `branchesTotal` values is a pipeline bug, not a
    datum -- it is checked rather than averaged over.
    """
    with open(DATA / f"esbmc_{bench}.json") as f:
        rep = json.load(f)

    out = {"bench": bench, "flat": rep["flatInput"],
           "primary": rep["primary"]["name"], "kind": rep["primary"]["kind"]}

    for key in ("per_function", "no_function"):
        sec = rep.get(key)
        if not sec:
            continue
        tot = sec.get("total", {})
        out[key] = {
            "denom": tot.get("branchesTotal"),
            "esbmc": tot.get("esbmcReached"),
            "native": tot.get("nativeReached"),
            "per_file": {pf["file"]: {
                "denom": pf.get("astDecisions"),
                "esbmc": pf.get("esbmc", {}).get("reached"),
                "native": pf.get("native", {}).get("reached"),
            } for pf in sec.get("perFile", [])},
        }

    a, b = out.get("per_function", {}), out.get("no_function", {})
    if a and b and a["denom"] != b["denom"]:
        out["DENOM_MISMATCH"] = (a["denom"], b["denom"])
    return out


def recomputed_denominator(flat_path, in_scope=None):
    """The denominator recomputed from source, as a cross-check on the JSON.

    MUST BE SCOPED, AND THE FIRST VERSION OF THIS FUNCTION WAS NOT. A flat
    contains the whole dependency tree; METHODOLOGY 3 restricts the denominator
    to the project's own production source, in scope for this entry. Summing
    `canonical_decisions()` over EVERY block therefore counts all of
    OpenZeppelin as well and inflates the figure several-fold -- it reported
    aqua as 85 against the recorded 8 and read as "the locked dataset is
    stale", which would have been a wrong and loud accusation. The authoritative
    in-scope list is the JSON's own `perFile` keys, so the check is per file
    against those keys, not a whole-flat sum.

    METHODOLOGY 8.2 says re-running must not change `branchesTotal`; a genuine
    per-file disagreement means one side is stale and neither is usable.
    """
    p = Path(flat_path)
    if not p.exists():
        return None, f"flat missing: {p}"
    try:
        by_file, _blocks = ast_decisions.canonical_decisions(p)
    except Exception as e:  # noqa: BLE001 -- report, do not mask
        return None, f"{type(e).__name__}: {e}"
    counts = {f: len(s) for f, s in by_file.items()}
    if in_scope is None:
        return counts, None
    return {f: counts.get(f) for f in in_scope}, None


# --------------------------------------------------------------------------
# THE PRODUCT SIDE -- NOT YET IMPLEMENTABLE, AND DELIBERATELY LEFT TO FAIL LOUD
# --------------------------------------------------------------------------
def pathcov_reached_flat_lines(cov_report_path):
    """Flat line numbers of the canonical decisions traversed by FEASIBLE paths.

    STILL BLOCKED, but no longer unknown. Resolved by notes/probe-enc-decode.md:

      * `enc` is `enc_0 = 1`, `enc_{k+1} = 2*enc_k + bit`, so the ARMS decode
        arithmetically: bit k is `(enc >> (depth-1-k)) & 1`.
      * The decision SITES are not a function of `enc` -- no location is mixed
        in -- so they can only be recovered by replaying the enumeration DFS
        driven by those bits. That replay is deterministic, and the tool
        already does it: `decision_site` (goto_coverage.cpp:3785) plus the
        decode loop at :5116-5139. But it is gated on --path-cov-outer-box,
        is a loop-body local, and is only ever printed as a log line.
      * cov-report.json carries `path_id` (enc, as a decimal string) and
        `path_depth` -- and NOT the decision sequence, the per-decision
        locations, or the occurrence indices.

    So this cannot be written against the current report. The fix is in the
    producer, and it is small: emit the decoded decision list into the claim
    entry, FOR STATUS-F CLAIMS ONLY. F is what the projection consumes and F is
    tiny (single digits per unit), which sidesteps the memory ceiling that the
    existing gate exists to respect (the comment at goto_coverage.cpp:3781-3784
    cites a 2733-path unit; one measured benchmark had 120166 paths, so an
    all-paths decision list is O(paths x depth) and not affordable).

    FIVE HAZARDS THAT MUST BE HANDLED WHEN THIS IS FILLED IN. Each of them
    silently biases the numerator rather than failing, which is the dangerous
    kind:

      1. POLARITY IS INVERTED relative to branch-coverage claim keys.
         goto_coverage.cpp:1677-1689: `assert(guard)` covers the FALL-THROUGH
         edge and `assert(!guard)` the TAKEN edge. So a path bit of TRUE maps to
         branch key `(not cond, loc)` and FALSE to `(cond, loc)`. Getting this
         backwards still produces a number.
      2. PATH COVERAGE HAS A DECISION BRANCH COVERAGE DOES NOT: the synthesised
         ABI non-payable gate `msg_value == 0` (:3535-3564), whose location is
         COPIED from the unit's first body instruction (:3523). Since the
         canonical denominator is keyed by flat line, counting it would make us
         "reach" whatever real decision sits on that line. Match on
         `(cond, loc)`, never on `loc` alone, and drop this gate explicitly.
      3. INTERNAL CALLS ARE PHYSICALLY INLINED before enumeration (:2209-2254),
         so one callee decision appears in many units' path sets -- the mapping
         to canonical decisions is many-to-one. Locations survive inlining, so
         they do match; just deduplicate.
      4. DEGRADATION AND THE CALL-DEPTH BOUND WITHDRAW CALL POINTS
         (:3069-3155, :3158-3195), removing those decisions from every path of
         the unit while branch coverage still counts them in the denominator.
         `degraded_call_sites` names them; a gate result must state how many
         decisions were unreachable for this reason, or the comparison is unfair
         in our favour on the denominator and against us on the numerator.
      5. THE TWO SIDES DO NOT SCOPE ALIKE. `branch_coverage()` filters each
         decision through `location_pool`, `scope_contract` and
         `exclude_contracts` (:1568-1619). The path DFS filters at UNIT level
         only (:3409-3426) and then branches on every conditional GOTO in the
         expanded body. The locked runs carry a long
         `--coverage-exclude-contract` list (BalanceLib, IAqua, SafeERC20, ...),
         so OUR side must apply the same exclusion before counting or we will
         count decisions the baseline deliberately dropped from both its
         numerator and its denominator.
    """
    raise NotImplementedError(
        "cov-report.json does not carry the decision sequence (only `path_id` "
        "and `path_depth`); see notes/probe-enc-decode.md. The producer change "
        "is ~20 lines in emit_exit, F-claims only. Refusing to return an empty "
        "set that would print as a measurement.")


def per_file_capped(reached_flat_lines, denom_by_file, blocks):
    """Apply METHODOLOGY 4/5 to our side, identically to how it was applied to
    the baseline: bucket reached flat lines by original file, count unique ones
    per file, cap at that file's canonical decision count, then sum."""
    by_file = defaultdict(set)
    for ln in reached_flat_lines:
        by_file[ast_decisions.file_at_flat_line(blocks, ln)].add(ln)
    out = {}
    for f, d in denom_by_file.items():
        out[f] = min(len(by_file.get(f, ())), d)
    return out


def main():
    reports = sys.argv[1:]
    print("# Branch-coverage gate\n")
    print("Unit: canonical decision (METHODOLOGY 2), identified by flat line.")
    print("Bar:  the locked dataset's own `esbmcReached`, not `nativeReached`.\n")

    header = ("| bench | denom | baseline esbmc | baseline native | "
              "ours | gate |")
    print(header)
    print("|" + "---|" * 6)

    rows = []
    for b in BENCHES:
        try:
            base = baseline(b)
        except (OSError, ValueError, KeyError) as e:
            print(f"| `{b}` | - | - | - | - | READ FAILED: {e} |")
            continue

        sec = base.get("no_function") or base.get("per_function") or {}
        denom, esb, nat = sec.get("denom"), sec.get("esbmc"), sec.get("native")

        ours = "-"
        verdict = "not measured"
        if reports:
            try:
                lines = set()
                for r in reports:
                    lines |= pathcov_reached_flat_lines(r)
                dn, err = recomputed_denominator(base["flat"])
                if err:
                    ours, verdict = "-", f"denominator: {err}"
                else:
                    _b2l = None
                    blocks = ast_decisions.parse_flat_file_blocks(
                        Path(base["flat"]))
                    capped = per_file_capped(lines, dn, blocks)
                    ours = sum(capped.values())
                    verdict = "PASS" if (esb is not None and ours >= esb) \
                        else "FAIL"
            except NotImplementedError as e:
                ours, verdict = "-", f"BLOCKED: {e}"

        rows.append((b, denom, esb, nat, ours, verdict))
        print(f"| `{b}` | {denom} | {esb} | {nat} | {ours} | {verdict} |")

    print("\n## Pair 1 (whole contract) vs Pair 2 (union of per-method runs)\n")
    print("| bench | P1 denom | P1 esbmc | P2 denom | P2 esbmc |")
    print("|" + "---|" * 5)
    for b in BENCHES:
        try:
            base = baseline(b)
        except (OSError, ValueError, KeyError):
            continue
        p1 = base.get("no_function", {})
        p2 = base.get("per_function", {})
        print(f"| `{b}` | {p1.get('denom')} | {p1.get('esbmc')} | "
              f"{p2.get('denom')} | {p2.get('esbmc')} |")

    print("\n## Denominator cross-check, PER FILE and IN SCOPE\n")
    for b in BENCHES:
        try:
            base = baseline(b)
        except (OSError, ValueError, KeyError):
            continue
        if "DENOM_MISMATCH" in base:
            print(f"- `{b}`: **Pair1/Pair2 denominators differ** "
                  f"{base['DENOM_MISMATCH']} -- pipeline bug per METHODOLOGY 8.1")
        sec = base.get("no_function") or base.get("per_function") or {}
        scope = list(sec.get("per_file", {}).keys())
        if not scope:
            print(f"- `{b}`: no perFile entries; cannot scope the check")
            continue
        dn, err = recomputed_denominator(base["flat"], in_scope=scope)
        if err:
            print(f"- `{b}`: could not recompute ({err})")
            continue
        bad = []
        for f in scope:
            want = sec["per_file"][f]["denom"]
            got = dn.get(f)
            if got != want:
                bad.append(f"{f}: JSON {want} vs source {got}")
        if bad:
            print(f"- `{b}`: **MISMATCH** -- " + "; ".join(bad))
        else:
            print(f"- `{b}`: agrees on all {len(scope)} in-scope file(s) "
                  f"(total {sec.get('denom')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
