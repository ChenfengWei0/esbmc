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
import re
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
           "project": rep.get("project"),
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
# THE PRODUCT SIDE
# --------------------------------------------------------------------------
def pathcov_reached_flat_lines(report_paths):
    """Flat line numbers of the decisions traversed by WITNESSED (F) paths.

    Reads the `decisions` array that --cov-report-json now publishes per F
    claim. Before that field existed this was impossible from outside the tool:
    `path_id` is `enc`, a pure bit accumulator, and no source location is mixed
    into it, so the bit->site mapping cannot be recovered from the report.

    Returns (set_of_flat_lines, stats). The stats are not decoration -- each
    one names a way this numerator could be quietly wrong, and they are printed
    with the result:

      * `f_without_sequence` -- an F carrying no `decisions` array. It witnesses
        decisions that this projection cannot see, so it depresses our number.
        Must be 0.
      * `unrecorded_steps` -- a decision whose prefix key was missing from the
        recorder. Same effect, finer grain.
      * `synthetic_dropped` -- the synthesised ABI non-payable gate. Path
        coverage HAS this decision and branch coverage does not, and its
        location is COPIED from the unit's first body instruction, so counting
        it would credit us with whatever real decision sits on that line. It is
        dropped on the producer's own flag (`synthetic_abi_gate`), not by
        matching its condition text.
      * `killed_runs` -- a path-coverage run killed by a timeout emits NOTHING
        (the partial-result rescue is gated on branch coverage), so a killed run
        contributes zero and that zero must not read as a measurement.

    TWO HAZARDS THAT ARE HANDLED ELSEWHERE, recorded so they are not re-derived:

      * POLARITY. `assert(guard)` covers the FALL-THROUGH edge and
        `assert(!guard)` the TAKEN edge, so a path bit of TRUE maps to the claim
        keyed on the NEGATED guard. The producer already emits both arm texts
        pre-inverted. It does not matter here, because this projection is by
        LINE -- and it must not be done by text at all: measured, a `require`
        lowers to a guard one `not` deeper under path coverage than under branch
        coverage (the revert-observation gate), so a text join would silently
        drop every `require` decision while working fine on `if`s.
      * SCOPE. `branch_coverage()` filters per decision through `location_pool`
        / `scope_contract` / `exclude_contracts`; the path DFS filters at UNIT
        level only. The two are reconciled NOT by replicating the filters but by
        intersecting with the canonical project-own decision lines in
        `per_file_capped` below -- which is exactly what collect.py does to the
        baseline's numerator (`union_lines & c_lines`), so both sides are
        narrowed by the same operation.

    STILL NOT HANDLED. Four mechanisms move our numerator and none of them is
    visible in this script's output. Three DEFLATE it, one INFLATES it:

      * internal calls withdrawn by degradation or by the call-depth bound
        remove those decisions from every path of the unit while branch
        coverage still counts them (`degraded_call_sites`, goto_coverage.cpp);
      * a short-circuit site above `SC_DECISION_MAX = 12` operands is dropped
        entirely (`sc_sites_over_cap`);
      * a residual unexpanded unit callee marks every path of its unit a named
        obstacle;
      * and in the other direction, `bmc.cpp` emits the obstacle detail ONLY
        under `tri == "U"`, so an F claim inside an obstructed unit carries a
        full `decisions` array and no marker at all -- this projection counts it
        like any other, although the tool's own rule is that such a path "must
        not be turned into a test".

    THE PREVIOUS VERSION OF THIS PARAGRAPH CLAIMED THE FIRST ONE WAS "reported
    beside the gate rather than folded into it". That was false. Traced end to
    end: `degraded_call_sites` is surfaced only by `log_warning`, never reaches
    the `summary` block of cov-report.json, is not captured by
    `pathcov_collect.one_run`, and is read nowhere here. The same holds for
    `sc_sites_over_cap` and for `named_obstacle_paths`. A disclosure that is
    promised in a docstring and not implemented is worse than an
    acknowledged gap, because it reads as handled.

    Making it real needs a producer-side change (either add the four counters
    to the report's `summary`, or have `pathcov_collect.one_run` capture the
    three existing warning lines into `runs.jsonl`), after which they belong in
    the "What the product side actually saw" table below.

    MEASURED, so that the size of the remaining doubt is known rather than
    argued: on the three benchmarks collected so far, a per-file SET comparison
    of `lines(union_pair2.json) & canon` against `lines(F decisions) & canon`
    gives `only-product = 0` on EVERY file -- our reached set is a strict subset
    of the baseline's. The inflating mechanism above therefore contributed
    nothing on this corpus, and the count comparison this gate performs is not
    hiding an over-count behind the cap. The deflating three remain unquantified.
    """
    lines = set()
    stats = {"reports": 0, "f_claims": 0, "f_without_sequence": 0,
             "decision_steps": 0, "unrecorded_steps": 0,
             "synthetic_dropped": 0, "missing_reports": [],
             "partial_reports": 0, "unstated_reports": 0}
    for p in report_paths:
        p = Path(p)
        if not p.exists():
            stats["missing_reports"].append(str(p))
            continue
        d = json.loads(p.read_text())
        stats["reports"] += 1

        # A PARTIAL REPORT IS NOT A MEASUREMENT, AND IT IS NOW A FILE THAT
        # EXISTS. Before the partial path was built, a run that died wrote
        # nothing, so `reportPresent` in the collector's index was a sound proxy
        # for "this run measured something" -- that is exactly what
        # `killed_runs` in this docstring relied on. It no longer is: a killed
        # run writes a real cov-report.json to the same filename, with real
        # claims in it, and the only thing separating it from a complete one is
        # the `partial` field.
        #
        # Counted rather than excluded. Its F claims ARE genuine witnesses and
        # their decisions were genuinely walked, so dropping them would throw
        # away real reach; what must not happen is the row reading as a full
        # measurement. So the numerator keeps them and the verdict says the
        # measurement is partial -- the same treatment `killed_runs` already
        # gets, applied to the case that now produces data instead of silence.
        #
        # A report with no `partial` field at all predates the marker and is
        # counted separately: "cannot say" is not "complete", and this project
        # has a standing rule that no-verdict must be an explicit third state.
        _partial = d.get("partial", d.get("summary", {}).get("partial"))
        if _partial is True:
            stats["partial_reports"] += 1
        elif _partial is None:
            stats["unstated_reports"] += 1

        for c in d.get("claims", []):
            if c.get("status") != "F":
                continue
            stats["f_claims"] += 1
            seq = c.get("decisions")
            if seq is None:
                stats["f_without_sequence"] += 1
                continue
            for e in seq:
                stats["decision_steps"] += 1
                if "unrecorded_prefix_enc" in e:
                    stats["unrecorded_steps"] += 1
                    continue
                if e.get("synthetic_abi_gate"):
                    stats["synthetic_dropped"] += 1
                    continue
                ln = e.get("line")
                if isinstance(ln, int) and ln > 0:
                    lines.add(ln)
    return lines, stats


def per_file_capped(reached_flat_lines, canon_by_file, blocks):
    """Apply METHODOLOGY 4/5 to our side, IDENTICALLY to how collect.py applied
    it to the baseline.

    `canon_by_file` maps an in-scope original file to the SET of canonical
    decision flat lines in it -- not to a count. The set is load-bearing: the
    baseline's numerator is `union_lines & c_lines`, an intersection, and
    bucketing our lines by file without intersecting would count decisions the
    baseline never had in either column. That is also what makes the two sides'
    different scoping mechanisms irrelevant (see above).
    """
    out = {}
    for f, c_lines in canon_by_file.items():
        out[f] = min(len(reached_flat_lines & c_lines), len(c_lines))
    return out


def canonical_in_scope(flat_path, project):
    """The canonical decision flat-lines per PROJECT-OWN file, plus the flat's
    file blocks. Same scope rule as collect.py, imported from it rather than
    restated, so the denominator here cannot drift from the locked one."""
    import collect as _c
    by_file, blocks = ast_decisions.canonical_decisions(Path(flat_path))
    own = sorted({m for _, _, m in blocks
                  if _c.is_project_own_marker(m, project)})
    return ({m: by_file.get(m, set()) for m in own
             if len(by_file.get(m, set())) > 0}, blocks)


PATHCOV = HERE / "coverage" / "pathcov"

# THE GATE'S COMMENSURABILITY PREMISE, AS A RUNTIME CHECK RATHER THAN A NOTE.
#
# This gate compares our numerator against a LOCKED baseline. The comparison is
# only a comparison if both sides ran at the same transaction depth, and that
# depth is now MEASURED rather than inferred: notes/coverage/D25-baseline-is-
# one-transaction.md ran the baseline's own command shape on poc/Tiny.sol, whose
# line 41 is reachable only after a preceding call.
#
#   baseline verbatim / plain BMC / either + --solidity-max-tx 1
#                                 -> Reached 5 of 8, line 41 NOT reached
#   plain BMC + --solidity-max-tx 2
#                                 -> Reached 8 of 8, line 41 reached
#
# So the baseline sits at ONE transaction, and it gains 5/8 -> 8/8 at two. It is
# LOCKED and cannot be re-run at two to restore parity. A product-side collection
# made at tx>=2 would therefore be running DEEPER than the thing it is compared
# to, and every row printed from it would be a number nobody could defend.
#
# WHY THIS IS CODE AND NOT A SENTENCE. INVOCATION_DECISIONS.md rows 1 and 2
# overturned tx=1 for the METHOD -- correctly, on reach -- and now the file
# deliberately prints TWO command lines: enumeration/artefact at whole-contract
# tx=2, gate at tx=1. Two legitimate configurations exist and only prose keeps
# them apart, which in this project is the shape that has already failed: a
# protection a note claims and the code does not implement. The consumer that
# prints the headline number is the right place to enforce it.
#
# An index with no `config` block at all is REFUSED, not waved through: it
# predates the recording and cannot say which cell it came from. "Cannot say" is
# not "complete" -- the same third-state rule this file already applies to
# `unstated_reports`.
GATE_SOLIDITY_MAX_TX = 1


def assert_gate_config(bench, meta):
    """Refuse to print a gate row from a collection made in another cell.

    Returns a short description of the cell for the audit table, or exits.
    """
    cfg = (meta or {}).get("config")
    if not cfg:
        sys.exit(
            f"{bench}: its index.json records no `config` block, so the cell "
            f"this collection came from is unknown. A gate row whose "
            f"configuration cannot be stated is not a measurement -- re-collect "
            f"with the current pathcov_collect.py, which records it.")
    tx = cfg.get("solidityMaxTx")
    if tx != GATE_SOLIDITY_MAX_TX:
        sys.exit(
            f"{bench}: collected at --solidity-max-tx {tx!r}, but the gate "
            f"requires {GATE_SOLIDITY_MAX_TX}. The locked baseline is MEASURED "
            f"to run at one transaction and to gain 5/8 -> 8/8 at two "
            f"(notes/coverage/D25-baseline-is-one-transaction.md); it cannot be "
            f"re-run to match. Comparing a deeper product run against it is not "
            f"a comparison. INVOCATION_DECISIONS.md prints TWO command lines for "
            f"exactly this reason -- whole-contract tx=2 is the ENUMERATION "
            f"line and may not be quoted into this table.")
    return (f"mode={cfg.get('mode')} tx={tx} goals={cfg.get('pathCovMaxGoals')} "
            f"solver={' '.join(cfg.get('solverFlags') or []) or 'default'}")


def _self_test():
    """Prove the refusal FIRES, in both directions, without touching the corpus.

    A guard that has never been seen to refuse is indistinguishable from one
    that cannot. This runs three synthetic indexes through `assert_gate_config`
    in a subprocess (it exits the process on refusal, by design) and reports
    which of them it stopped.
    """
    import subprocess
    cases = [
        ("tx=1, the gate cell", {"config": {"solidityMaxTx": 1, "mode":
                                            "per-method"}}, False),
        ("tx=2, the enumeration cell", {"config": {"solidityMaxTx": 2, "mode":
                                                   "whole"}}, True),
        ("no config block at all", {"runs": []}, True),
    ]
    ok = True
    print("## self-test: does the configuration guard actually refuse?\n")
    for name, meta, want_refusal in cases:
        prog = ("import json,sys; sys.path.insert(0, %r); "
                "import branch_gate as g; "
                "g.assert_gate_config('SELFTEST', json.loads(%r)); "
                "print('ACCEPTED')" % (str(HERE), json.dumps(meta)))
        cp = subprocess.run([sys.executable, "-c", prog],
                            capture_output=True, text=True)
        refused = cp.returncode != 0
        good = refused == want_refusal
        ok = ok and good
        print(f"  [{'ok ' if good else 'BAD'}] {name}: "
              f"{'refused' if refused else 'accepted'} "
              f"(expected {'refusal' if want_refusal else 'acceptance'})")
        if refused:
            print("        " + (cp.stdout + cp.stderr).strip().splitlines()[0])
    print()
    if not ok:
        print("  ⛔ the guard does not behave as specified.")
        return 1
    print("  ✅ it refuses the two wrong cells and accepts the gate cell.")
    return 0


def pathcov_reports_for(bench):
    """Every cov-report.json the path-coverage collector produced for `bench`,
    plus its index. A missing index is reported, never treated as zero reach.

    THE GLOB IS CROSS-CHECKED AGAINST THE INDEX, and that check is the point.
    `pathcov_collect.py` cleans its `work/` directory per run but NEVER empties
    `reports/`, so reports from an earlier collection survive into the next one.
    The numerator here is a union over whatever `reports/*.json` contains, so a
    stale file silently inflates it -- and the result is a well-formed PASS row
    with nothing anywhere marking it. Measured occasion: a pre-fix collection's
    reports were left in place when its `index.json` and `runs.jsonl` were
    quarantined, which would have credited the fixed build with the buggy
    build's witnessed paths.

    A file count that disagrees with the index is not a warning, because a
    numerator that includes runs the index never recorded is not a measurement.
    """
    idx = PATHCOV / bench / "index.json"
    if not idx.exists():
        return None, []
    meta = json.loads(idx.read_text())
    rdir = Path(meta.get("reportsDir", PATHCOV / bench / "reports"))
    files = sorted(rdir.glob("*.json"))
    expected = sum(1 for r in meta.get("runs", []) if r.get("reportPresent"))
    if len(files) != expected:
        sys.exit(
            f"{bench}: {rdir} holds {len(files)} report(s) but index.json "
            f"records {expected} run(s) with a report. A report the index does "
            f"not know about is almost certainly left over from an earlier "
            f"collection; including it would inflate the numerator invisibly. "
            f"Empty (or quarantine) {rdir} and re-collect.")
    return meta, files


DEPTH_RE = re.compile(
    r"(\d+) call site\(s\) are deeper than the call depth bound \((\d+)\)")
SC_RE = re.compile(r"(\d+) short-circuit site\(s\)[^.\n]*cap")


def truncation_from_logs(bench, runs):
    """The three deflating counters, read from the logs of JOURNAL-NAMED runs.

    Used only for collections made before `pathcov_collect.one_run` recorded
    them.

    ---- THE REASON THIS DOCSTRING USED TO GIVE WAS INVERTED ----

    It said: keyed on the journal's `tag` rather than on a glob, "so a directory
    left behind by an earlier collection cannot contribute: those belong to units
    the current collection SKIPS, which never call `one_run` and so never have
    their workdir cleared."

    The second half is true and it is the reason the leftovers EXIST; it is not a
    reason they are excluded. `pathcov_collect.py`'s skip branch calls
    `record(rec)` for every skipped unit, so a SKIPPED TAG IS IN THE JOURNAL --
    keying on `tag` therefore SELECTS such a directory rather than avoiding it.
    MEASURED on this corpus: 60 leftover `work/<tag>/run.log` files from
    2026-07-30 sit under skipped tags (aqua 2, EscrowDst 14, EscrowSrc 14,
    farming 16, limit_order 14), and calling this function with the journal's
    full `runs` list returns 60 extra rows.

    The protection is real but it lives in the CALLER, which filters
    `not r.get("skipped")` before calling. So no published number is or was
    wrong. What was wrong is that the guarantee was asserted HERE, next to code
    that does not implement it -- which is this project's own "a disclosure
    promised in a comment and not implemented reads as handled" shape, and it
    would trap the next caller.

    So the filter is now applied here TOO, as defence in depth, and the reason is
    stated accurately. On this corpus the three totals are unchanged by it (the
    leftovers are `--function`-routed library runs that instrumented nothing and
    printed none of the three warning lines, so every one contributed 0); the row
    COUNT drops by exactly those 60. Both directions are checked rather than
    assumed.

    Returns [] when no log is readable, which the caller renders as `?` rather
    than as 0.
    """
    work = PATHCOV / bench / "work"
    out = []
    for r in runs:
        # Defence in depth: the caller already does this, and a reader of THIS
        # function must not have to know that to use it safely.
        if r.get("skipped"):
            continue
        log = work / str(r.get("tag", "")) / "run.log"
        if not log.exists():
            continue
        try:
            text = log.read_text(errors="replace")
        except OSError:
            continue
        m = DEPTH_RE.search(text)
        sc = SC_RE.search(text)
        out.append({
            "depthBoundUnexpandedSites": int(m.group(1)) if m else 0,
            "depthBound": int(m.group(2)) if m else None,
            "degradedUnits": text.count("DEGRADED unit "),
            "scSitesOverCap": int(sc.group(1)) if sc else 0,
        })
    return out


def main():
    print("# Branch-coverage gate\n")
    print("Unit: canonical decision (METHODOLOGY 2), identified by flat line.")
    print("Bar:  the locked dataset's own `esbmcReached`, not `nativeReached`.")
    print("Ours: flat lines of the decisions walked by WITNESSED (F) paths, "
          "intersected with the canonical in-scope decision lines and capped "
          "per file -- the same two operations collect.py applies to the "
          "baseline numerator.\n")

    header = ("| bench | denom | baseline P1 | baseline P2 | native | "
              "ours | gate vs P2 |")
    print(header)
    print("|" + "---|" * 7)

    notes = []
    for b in BENCHES:
        try:
            base = baseline(b)
        except (OSError, ValueError, KeyError) as e:
            print(f"| `{b}` | - | - | - | - | - | READ FAILED: {e} |")
            continue

        p1 = base.get("no_function", {})
        p2 = base.get("per_function", {})
        # `or` on a denominator would let a P2 denominator of 0 silently show
        # P1's, so the fallback is on ABSENCE, not on falsiness.
        denom = p2.get("denom") if p2.get("denom") is not None else p1.get("denom")
        bar = p2.get("esbmc")
        nat = p2.get("native")

        # A BAR OF ZERO IS NOT A BAR. `ours >= 0` is a tautology, so a baseline
        # that measured nothing would be cleared by anything, and the row would
        # look exactly like a real PASS. This is the completed loop of the
        # collect.py defect fixed in 4bd98cd328: that bug could write
        # `esbmcReached: 0` while `branchesTotal` stayed correct (the
        # denominator comes from an AST walk the bug never touched), so the
        # JSON looked fully populated. Refuse rather than pass.
        if bar is not None and bar == 0 and denom:
            sys.exit(
                f"{b}: baseline esbmcReached is 0 against a denominator of "
                f"{denom}. A zero bar is cleared by anything, so this is not a "
                f"gate. Re-collect the baseline before using it.")

        meta, reports = pathcov_reports_for(b)
        if meta is None:
            print(f"| `{b}` | {denom} | {p1.get('esbmc')} | {bar} | {nat} | - "
                  f"| not collected |")
            continue

        # BEFORE any number is computed from these reports: was this collection
        # made in the cell the gate is entitled to read? Refuses rather than
        # annotates -- an unusable row printed with a caveat still gets quoted.
        cell = assert_gate_config(b, meta)

        lines, st = pathcov_reached_flat_lines(reports)
        canon, blocks = canonical_in_scope(base["flat"], base["project"])
        capped = per_file_capped(lines, canon, blocks)
        ours = sum(capped.values())

        killed = sum(1 for r in meta["runs"] if r.get("killedByOuterTimeout"))
        # A DELIBERATE REFUSAL IS NOT A MISSING REPORT, and reading it as one
        # turns a stated scope limit into a collection failure.
        #
        # pathcov_collect.py refuses to run a library through `--function` (it
        # verifies in isolation from an arbitrary state, which can yield a
        # counterexample no reachable state supports -- a RED generated test)
        # and records each such unit with `skipped:
        # "library-has-no-dispatcher"` plus the full reasoning. Those records
        # have `reportPresent: false`, and this file counted only that field,
        # so a benchmark that was deliberately not measured came out as
        # "NO MEASUREMENT: 14 without a report".
        #
        # MEASURED, and it is why this was noticed: re-collecting
        # limit_order_protocol with the current build flipped its verdict from
        # "N/A: 0 units (in-scope code is internal-only)" to that failure text.
        # The collection did not get worse -- the older one ran those library
        # functions through the unsound `--function` route and got 14 reports
        # with 0 F claims in them. The producer keeps its zeros apart and this
        # consumer was collapsing them.
        skipped = [r for r in meta["runs"] if r.get("skipped")]
        noreport = sum(1 for r in meta["runs"]
                       if not r.get("reportPresent") and not r.get("skipped"))
        units = sum(r.get("unitsEnumerated", 0) for r in meta["runs"])

        if skipped and len(skipped) == len(meta["runs"]):
            # Every unit was refused, so there is nothing to have measured. The
            # reason is quoted from the record rather than restated, because the
            # record names WHY the configuration cannot reach it -- which is the
            # reportable finding, and a different one from "reached nothing".
            reasons = sorted({r.get("skipped") for r in skipped})
            verdict = (f"REFUSED: all {len(skipped)} unit(s) skipped "
                       f"({', '.join(reasons)}) -- not measurable under this "
                       f"configuration, not a reach result")
            ours = "-"
        elif units == 0 and (killed or noreport or not meta["runs"]):
            # `unitsEnumerated` is set only when the collector's regex matched
            # the run's "instrumented N complete path(s) across M unit(s)" line
            # (pathcov_collect.py), and it defaults to 0. So 0 means EITHER
            # "there are no units" OR "no run got far enough to say". Claiming
            # the first when the second happened dresses a total collection
            # failure as a methodological scope exemption -- and the row is
            # byte-identical to the legitimate library-only one. Near-miss on
            # record: st1inch's pre-fix index has 22 runs, every one exit -6
            # (SIGABRT) with no report, and it escaped this only because ESBMC
            # happened to print the instrumentation line before aborting.
            verdict = (f"NO MEASUREMENT: {killed} killed, {noreport} without a "
                       f"report, {len(meta['runs'])} run(s)")
            ours = "-"
        elif units == 0:
            # NOT A MEASURED ZERO, and printing FAIL here would be a false
            # result rather than a weak one. A UNIT is an externally-callable
            # function; a benchmark whose in-scope code is a pure `internal`
            # library has an EMPTY unit set, so complete-path coverage has
            # nothing to enumerate and says so ("in-scope function(s) are
            # internal/private and are therefore not units; ... they appear
            # inside the paths of the units that call them"). Branch coverage
            # has no such notion and instruments the library directly, which is
            # why it reports 3/3 on the same file.
            #
            # That is a SCOPE difference between the two metrics, not a reach
            # difference, and it is the honest thing to report. It also has a
            # real consequence for the paper: this method cannot serve a
            # library-only compilation unit at all.
            verdict = "N/A: 0 units (in-scope code is internal-only)"
            ours = "-"
        else:
            verdict = "PASS" if (bar is not None and ours >= bar) else "FAIL"
            # A run that produced nothing is NOT a measured zero either. Saying
            # so in the verdict cell is the difference between a result and a
            # lower bound dressed up as one.
            #
            # `partial_reports` joins this list for the same reason and is the
            # newer hazard: a killed run used to leave no file, so `noreport`
            # caught it; it now leaves a real report with real claims, and
            # nothing but the `partial` field distinguishes it. `unstated` is
            # here too -- a report that cannot say whether it is complete must
            # not be counted as one that says it is.
            if killed or noreport or st["f_without_sequence"] or \
                    st["unrecorded_steps"] or st["partial_reports"] or \
                    st["unstated_reports"]:
                verdict += " (partial)"

        print(f"| `{b}` | {denom} | {p1.get('esbmc')} | {bar} | {nat} | "
              f"{ours} | {verdict} |")
        notes.append((b, st, meta, killed, noreport, capped, canon,
                      len(skipped), cell))

    print("\n## What the product side actually saw\n")
    # `reports` is printed because it is the size of the numerator's input set.
    # It was computed and discarded before, which is how a reports/ directory
    # holding files from an earlier collection could inflate the numerator with
    # nothing in the output to show for it.
    # `skipped` is its own column and not folded into `no report`: a unit the
    # collector refused on soundness grounds and a run that died are different
    # findings, and one column for both is how the first reads as the second.
    # `cell` travels with the number, because a configuration that is checked
    # once and then not shown is a configuration the reader has to take on
    # trust. Every row here has passed `assert_gate_config`, and this column is
    # what it passed WITH.
    print("| bench | cell | runs | reports read | PARTIAL | completeness "
          "unstated | skipped | no report | killed | F claims | "
          "F w/o sequence | steps | unrecorded | ABI-gate dropped |")
    print("|" + "---|" * 14)
    for b, st, meta, killed, noreport, _c, _k, nskipped, cell in notes:
        print(f"| `{b}` | {cell} | {len(meta['runs'])} | {st['reports']} | "
              f"{st['partial_reports']} | {st['unstated_reports']} | "
              f"{nskipped} | {noreport} | "
              f"{killed} | {st['f_claims']} | {st['f_without_sequence']} | "
              f"{st['decision_steps']} | {st['unrecorded_steps']} | "
              f"{st['synthetic_dropped']} |")

    # ---- THE THREE MECHANISMS THAT DEFLATE THIS NUMERATOR ----
    #
    # The docstring above lists them and says none is visible here. That was
    # true, and it is the shape this file explicitly calls worse than an
    # acknowledged gap -- a disclosure promised in a comment and not
    # implemented. `pathcov_collect.one_run` now captures them off each run's
    # stdout, which is the only place they exist (`degraded_call_sites` never
    # reaches `cov-report.json`).
    #
    # UNRECORDED IS NOT ZERO. A journal record written before those fields
    # existed carries no key, and printing 0 for it would say "nothing was
    # truncated" about a run nobody asked. Rendered as `?`, per the standing
    # rule that no-verdict is an explicit third state.
    print("\n## What was TRUNCATED away from our numerator "
          "(`?` = this run predates the recording, which is not 0)\n")
    print("| bench | runs w/ a depth-bound truncation | max sites past the "
          "bound | runs w/ a DEGRADED unit | max degraded units | "
          "short-circuit sites over cap |")
    print("|" + "---|" * 6)
    for b, _st, meta, _k, _n, _c, _canon, _s, _cell in notes:
        runs = [r for r in meta["runs"] if not r.get("skipped")]
        rec = [r for r in runs if "depthBoundUnexpandedSites" in r]
        src = "journal"
        if not rec:
            # FALLBACK: read the runs' own logs, but ONLY for tags the journal
            # names -- which is the whole difference between this and the ad-hoc
            # census that found these numbers first. `work/` is NOT reconciled
            # against the journal: `one_run` clears a workdir per run and a unit
            # the collection SKIPS never calls it, so a pre-ban directory from an
            # earlier collection survives untouched. Globbing `work/*` would
            # therefore mix vintages, which is the exact hazard
            # `pathcov_reports_for` already guards `reports/` against.
            rec, src = truncation_from_logs(b, runs), "logs"
        if not rec:
            print(f"| `{b}` | ? | ? | ? | ? | ? |")
            continue
        db = [r.get("depthBoundUnexpandedSites", 0) for r in rec]
        dg = [r.get("degradedUnits", 0) for r in rec]
        sc = [r.get("scSitesOverCap", 0) for r in rec]
        print(f"| `{b}` ({src}) | {sum(1 for x in db if x)}/{len(rec)} | "
              f"{max(db)} | {sum(1 for x in dg if x)}/{len(rec)} | {max(dg)} | "
              f"{max(sc)} |")

    print("\n## Per-file, ours (capped) vs that file's canonical decisions\n")
    for b, _st, _m, _k, _n, capped, canon, _s, _cell in notes:
        print(f"- `{b}`:")
        for f in sorted(canon):
            print(f"    {capped.get(f, 0):>4} / {len(canon[f]):<4}  {f}")

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
    if "--self-test" in sys.argv:
        sys.exit(_self_test())
    sys.exit(main())
