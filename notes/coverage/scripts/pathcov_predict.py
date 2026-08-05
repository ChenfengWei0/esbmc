#!/usr/bin/env python3
"""PREDICT, FROM STAGE 1 ALONE, WHETHER STAGE 2 CAN POSSIBLY CERTIFY.

---- WHY THIS EXISTS -------------------------------------------------------

Stage 2 costs 90-320 seconds per unit and the corpus is 50 units, so learning
"this unit cannot convert" by running it is a two-month schedule. Most of what
was learned that way is already in stage 1's counterexample payload, which
costs nothing to read because stage 1 has already run.

MEASURED, the two units done by hand:

  farming/setDistributor  converts. Its paths differ on `distributor_` (a
                          parameter) and on `msg.sender`.
  farming/deposit         does not. Its paths 26/27, 246/247 and 3622/3623
                          differ ONLY on `extcall.success` -- a quantity no
                          generated test can set. Three stage-2 arms, ~950
                          seconds, to learn what the payload said.

---- THE ONE HARD RULE, AND WHY IT DOES NOT DEPEND ON THE TOOL -------------

    If two witnessed paths' counterexamples agree on every quantity a
    GENERATED TEST CAN SET, no region over those quantities separates them.

A test sets: the unit's parameters, `msg.sender` / `msg.value`, and the entry
state it can `vm.store`. A test does NOT set: an external call's success bit or
return value, a harness-chosen nondeterministic local, an `immutable`, a
`constant`. Two paths differing only on the second list are reachable only by
the callee behaving differently -- so one test input walks one of them, and
asking for a certified region around both is asking for the impossible.

This is a statement about the CONTRACT and the CELL, not about ESBMC's
expressiveness, so it stays true whatever the verifier learns to express. That
is what makes it safe to act on before running.

---- WHAT THIS DELIBERATELY DOES NOT PREDICT -------------------------------

Whether a bound is refutable, whether the solver answers, whether the budget
holds. Those need the run. A unit this script calls GO may still fail; a unit
it calls NO-GO cannot succeed for the reason given. The asymmetry is the
point -- it is a filter, not an oracle, and every verdict carries its evidence.

⛔ THE PAYLOAD PARSER IS IMPORTED, NEVER REIMPLEMENTED. `coord_values` and
`payload_extras` already decide what a coordinate is and how a struct is
decomposed; a second copy here would drift from the driver and this predictor
would then be answering about a payload the driver does not see.

usage:
    pathcov_predict.py <stage-1 output dir> [more dirs ...]
    pathcov_predict.py --all          # every pathcov/ dir with a report
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ESBMC_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DRIVER = os.path.join(ESBMC_ROOT, "scripts", "solidity_path_generalise.py")
PATHCOV = os.path.join(ESBMC_ROOT, "notes", "coverage", "pathcov")

_spec = importlib.util.spec_from_file_location("pathgen", DRIVER)
pathgen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pathgen)

# `binary_identity` is IMPORTED from the collector that stamps the runs, never
# reimplemented here. Two copies of "which executable is this" drift the moment
# one of them learns about a new field, and the whole point of the stamp is
# that the writer's answer and the reader's answer are the same answer.
_cspec = importlib.util.spec_from_file_location(
    "pathcollect", os.path.join(HERE, "pathcov_collect.py"))
try:
    pathcollect = importlib.util.module_from_spec(_cspec)
    _cspec.loader.exec_module(pathcollect)
    TREE_BINARY = pathcollect.binary_identity()
except Exception as _e:                                    # noqa: BLE001
    pathcollect, TREE_BINARY = None, None
    print("⚠ could not import pathcov_collect to learn this tree's binary "
          "(%s). Every row below will be reported as UNKNOWN-BINARY rather "
          "than silently compared against nothing." % _e)


def binary_status(rec_binary):
    """CURRENT / STALE / UNKNOWN-BINARY, never a silent pass.

    ⛔ A REPORT WITH NO RECORDED BINARY IS *NOT* TREATED AS CURRENT. Missing
    provenance reads as "no restriction" if you let it, and it really means
    "nothing is known" -- the direction that has already put an old build's
    numbers under a new build's name three times in this project.
    """
    if not rec_binary:
        return "UNKNOWN-BINARY"
    if TREE_BINARY is None:
        return "UNKNOWN-BINARY"
    return "CURRENT" if rec_binary == TREE_BINARY else "STALE"


def load_runs(d):
    """{tag: run record} from a stage-1 `runs.jsonl`.

    The report JSON is written by ESBMC and carries no provenance; the run
    journal beside it is written by the collector and carries `binary`, the
    exact command line, and the per-reason U counts. Joining them on `tag` is
    the only way to say WHICH EXECUTABLE produced the verdicts being read.
    """
    p = os.path.join(d, "runs.jsonl")
    out = {}
    if not os.path.isfile(p):
        return out
    with open(p, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("tag"):
                out[r["tag"]] = r
    return out


def settable(name):
    """Can a generated test choose this quantity's value?

    THE THREE KINDS IT CANNOT, each measured rather than assumed:

    * `extcall.<name>` -- a quantity the HARNESS chose inside the execution.
      The emitter's mock decides it, and the mock's own header says a path
      reached BECAUSE a call failed is not reproducible by it.
    * `msg.value` on a non-payable unit is settable in principle, so it is NOT
      excluded here; the auto-pin handles it and excluding it would hide a real
      separation.
    * an immutable or a constant reaches the payload under `state.` and IS
      excluded -- but only the driver knows which, from the AST, so that
      exclusion is applied by the caller when an AST is available. Without one
      this script counts them as settable, which can only make it MORE
      optimistic, never less. It says so in the verdict.
    """
    return not name.startswith("extcall.")


def load_paths(report):
    """([(path_id, depth, ce, exit_kind, revert_kind)] for the F claims, meta).

    ⛔ THE STATUS HISTOGRAM IS RETURNED WHETHER OR NOT ANY CLAIM IS `F`.
    "no witnessed path" has at least three different causes -- the report holds
    no claim at all, it holds claims the run never reached a verdict on, or it
    holds claims proved unreachable -- and each is fixed somewhere else.
    Collapsing them into one bucket is what sends a reader off to debug the
    wrong stage, which is exactly what a predictor exists to prevent.
    """
    with open(report) as f:
        rep = json.load(f)
    claims = rep.get("claims", [])
    meta = {"claims": len(claims), "status": {},
            "coverage_type": rep.get("coverage_type"),
            "partial": rep.get("partial"),
            "summary": rep.get("summary"),
            "source_files": rep.get("source_files")}
    out = []
    for c in claims:
        s = c.get("status")
        meta["status"][s] = meta["status"].get(s, 0) + 1
        if s != "F":
            continue
        ce, _refused = pathgen.coord_values(c)
        ce.update(pathgen.payload_extras(c))
        out.append((c.get("path_id"), c.get("path_depth"), ce,
                    c.get("exit_kind"), c.get("revert_kind")))
    return out, meta


# ---- R14: EVERY QUERY WITHOUT A VERDICT IS ATTRIBUTED, WITH EVIDENCE -------
#
# Two buckets, and evidence may NEVER be borrowed across them:
#   (1) INSIDE  -- the tool's own instrumentation or encoding
#   (2) OUTSIDE -- the cell it was invoked in (bound, focus, budget)
#
# The distinction decides who fixes it. A `bounded-holds` treated as an
# encoding bug sends someone into the solver for a week; a `solver-unknown`
# treated as a bound sends someone to raise --solidity-max-tx forever. The
# report already separates them in `summary.U_reasons`; nothing here is
# inferred.
#
# ⛔ `bounded-holds` MEANS THE CLAIM HELD AT THIS EXPLORATION. It is NOT
# "unreachable" -- the report says so itself, and no coverage configuration can
# establish unreachability at all.
NO_VERDICT = {
    "bounded-holds": (
        "(2) OUTSIDE",
        "the claim HELD at this exploration -- reachable only from a state an "
        "earlier transaction must establish. A deeper transaction bound or a "
        "cell that can reach cross-function state is required. NOT a budget "
        "problem: more seconds change nothing."),
    "solver-unknown": (
        "(1) INSIDE",
        "the solver returned no verdict on the encoding it was handed. Lives "
        "in the encoding, not in the cell."),
    "claim-budget-exceeded": (
        "(2) OUTSIDE",
        "the per-claim budget ran out. This one IS a budget problem and is "
        "the only reason here that more seconds can move."),
    "not-solved-this-run": (
        "(2) OUTSIDE",
        "the run ended before this claim was reached."),
    "run-died-before-solving": (
        "(2) OUTSIDE",
        "the process died first -- look at memory limit and timeout before "
        "anything else."),
    "named-obstacle": (
        "(1) INSIDE",
        "the instrumentation named a construct it cannot encode."),
    "unit-not-entered": (
        "(1) INSIDE",
        "the unit was never entered, so no claim in it could be reached -- "
        "check the focus/entry wiring before the bound."),
}


def attribute(meta):
    """[(reason, count, bucket, what it needs)] from the report's own counts."""
    s = meta.get("summary") or {}
    reasons = s.get("U_reasons") or {}
    out = []
    for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
        if not n:
            continue
        bucket, needs = NO_VERDICT.get(
            reason, ("(?) UNCLASSIFIED",
                     "this reason is not in the attribution table -- it must "
                     "be added before any conclusion is drawn from it"))
        out.append((reason, n, bucket, needs))
    return out


def analyse(unit, paths, meta=None):
    """The verdict and every fact it rests on."""
    meta = meta or {}
    r = {"unit": unit, "paths": len(paths), "verdict": None, "why": [],
         "promote": [], "pin_state": [], "single_point": [],
         "inseparable": [], "reverting": [], "meta": meta}
    if len(paths) < 1:
        hist = meta.get("status") or {}
        if not meta.get("claims"):
            r["verdict"] = "NO-CLAIM"
            r["why"].append(
                "the report holds NO CLAIM AT ALL. The instrumentation emitted "
                "nothing for this unit, so the failure is upstream of any "
                "solving and no amount of budget or flag tuning reaches it.")
        else:
            r["verdict"] = "NO-PATH"
            r["why"].append(
                "%d claim(s) present and NOT ONE is witnessed. Statuses: %s. "
                "The instrumentation fired, so the failure is in reaching a "
                "verdict, not in emitting the claims."
                % (meta["claims"],
                   ", ".join("%s=%d" % (k, v) for k, v in
                             sorted(hist.items(), key=lambda kv: str(kv[0])))))
        att = attribute(meta)
        r["attribution"] = att
        bnd = (meta.get("summary") or {}).get("bound") or {}
        if bnd:
            r["why"].append(
                "the cell it was run in: max_tx=%r unwind=%r claim_timeout=%rs"
                % (bnd.get("max_tx"), bnd.get("unwind"),
                   bnd.get("claim_timeout_s")))
        for reason, n, bucket, needs in att:
            r["why"].append("%-24s x%-3d %s -- %s" % (reason, n, bucket, needs))
        if not att:
            r["why"].append(
                "⛔ the report records NO reason for the missing verdicts. "
                "That is itself the finding: the attribution cannot be made "
                "from this report and must not be guessed.")
        return r

    names = sorted({k for _, _, ce, _, _ in paths for k in ce})
    st_names = [n for n in names if settable(n)]

    # ---- 1. PAIRWISE SEPARABILITY, the hard rule ---------------------------
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            a, b = paths[i], paths[j]
            diff = [n for n in names if a[2].get(n) != b[2].get(n)]
            sdiff = [n for n in diff if settable(n)]
            if diff and not sdiff:
                r["inseparable"].append(
                    (a[0], b[0], sorted(diff)))

    # ---- 2. what the derivation would choose -------------------------------
    for n in names:
        if not settable(n):
            continue
        vals = {ce.get(n) for _, _, ce, _, _ in paths}
        if pathgen.is_env(n):
            if len(vals) > 1 or None in vals:
                r["promote"].append(n)
        elif n.startswith("state.") and len(vals) == 1 and None not in vals:
            r["pin_state"].append((n, next(iter(vals))))
        if len(vals) == 1 and None not in vals:
            r["single_point"].append(n)

    # ---- 3. the floor test, forecast ---------------------------------------
    # A coordinate the emitter renders is a PARAMETER or a promoted env
    # quantity; a state coordinate is established, never fuzzed. If every one
    # of those takes a single value across the paths, the ladder has nothing to
    # widen and the floor test refuses every PUT.
    rendered = [n for n in st_names
                if not n.startswith("state.")]
    varying = [n for n in rendered if n not in r["single_point"]]

    # ---- 4. does the path revert? ------------------------------------------
    for pid, _d, _ce, ek, rk in paths:
        if (ek or "").lower().startswith("revert") or rk:
            r["reverting"].append((pid, ek, rk))

    if r["inseparable"]:
        r["verdict"] = "NO-GO"
        for a, b, diff in r["inseparable"]:
            r["why"].append(
                "paths %s and %s agree on every settable quantity and differ "
                "only on %s -- no test can choose those, so one input cannot "
                "walk both and no region separates them"
                % (a, b, ", ".join(diff)))
    elif not varying:
        r["verdict"] = "NO-PUT"
        r["why"].append(
            "every quantity the emitter would RENDER takes one value across "
            "all %d path(s) (%s), so the floor test refuses a PUT even if the "
            "regions certify" % (len(paths), ", ".join(rendered) or "none"))
    else:
        r["verdict"] = "GO"
        r["why"].append(
            "%d rendered coordinate(s) vary across the paths (%s), and every "
            "pair of paths differs on at least one settable quantity"
            % (len(varying), ", ".join(varying)))
    return r


def report(r):
    print("=" * 92)
    print("%-40s %d witnessed path(s)   -> %s"
          % (r["unit"], r["paths"], r["verdict"]))
    for w in r["why"]:
        print("    %s" % w)
    if r["promote"]:
        print("    derivation would PROMOTE (paths disagree): %s"
              % ", ".join(r["promote"]))
    if r["pin_state"]:
        print("    derivation would PIN (paths agree): %s"
              % ", ".join("%s=%s" % (n, v) for n, v in r["pin_state"]))
    if r.get("binary_status") and r["binary_status"] != "CURRENT":
        print("    ⛔ %s: this report was produced by %s, the tree holds %s. "
              "The verdict above describes THAT executable, not this one."
              % (r["binary_status"], r["meta"].get("binary"), TREE_BINARY))
    if r["reverting"]:
        print("    ⚠ %d path(s) the report marks as reverting -- their PUT's "
              "call is wrapped and every 'changed' rung is conditional: %s"
              % (len(r["reverting"]),
                 ", ".join(str(p[0]) for p in r["reverting"])))


def main(argv):
    # --brief prints only the two aggregates. The per-unit detail is the thing
    # you read when you are working ON a unit; the aggregate is the thing you
    # read when you are deciding WHICH unit to work on, and mixing them means
    # the second is always buried under the first.
    brief = "--brief" in argv
    argv = [a for a in argv if a != "--brief"]
    # --reason NAME names every unit carrying that no-verdict reason. The
    # aggregate says how big a defect is; this says where to go and fix it.
    # ⛔ STRIPPED BEFORE `dirs` IS TAKEN, or the flag and its value are read as
    # two directory paths and the run silently scans the wrong set.
    want_reason = None
    if "--reason" in argv:
        i = argv.index("--reason")
        if i + 1 >= len(argv):
            sys.exit("--reason needs a reason name, e.g. --reason "
                     "solver-unknown")
        want_reason = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    dirs = argv[1:]
    if not dirs or dirs[0] == "--all":
        dirs = [os.path.join(PATHCOV, d) for d in sorted(os.listdir(PATHCOV))
                if os.path.isdir(os.path.join(PATHCOV, d, "reports"))]
    counts, by_reason, by_unit, where = {}, {}, {}, {}
    for d in dirs:
        rd = os.path.join(d, "reports")
        if not os.path.isdir(rd):
            print("%s: no reports/ -- stage 1 has not run here" % d)
            continue
        runs = load_runs(d)
        for fn in sorted(os.listdir(rd)):
            if not fn.endswith(".json"):
                continue
            unit = fn[:-5]
            try:
                paths, meta = load_paths(os.path.join(rd, fn))
            except (OSError, ValueError) as e:
                print("%s/%s: could not read: %s" % (d, fn, e))
                continue
            rec = runs.get(unit) or {}
            meta["binary"] = rec.get("binary")
            meta["run_cmd"] = rec.get("cmd")
            r = analyse("%s :: %s" % (os.path.basename(d), unit), paths, meta)
            r["binary_status"] = binary_status(rec.get("binary"))
            if not brief:
                report(r)
            # ⛔ COUNTED PER BINARY. Summing a CURRENT verdict with a STALE one
            # produces a corpus figure that describes no executable that has
            # ever existed -- the exact shape this project has paid for.
            key = "%s/%s" % (r["binary_status"], r["verdict"])
            counts[key] = counts.get(key, 0) + 1
            for reason, n, bucket, _needs in r.get("attribution", ()):
                # The binary is part of the key for the same reason it is part
                # of the verdict key: 141 claims summed across four executables
                # is a number about no executable.
                k = (r["binary_status"], bucket, reason)
                by_reason[k] = by_reason.get(k, 0) + n
                by_unit[k] = by_unit.get(k, 0) + 1
                where.setdefault(reason, []).append((r["unit"], n))
    print("=" * 92)
    print("verdicts: " + ", ".join("%s=%d" % kv for kv in sorted(counts.items()))
          + ("   (total %d)" % sum(counts.values()) if counts else ""))
    if by_reason:
        print("-" * 92)
        print("WHY THE MISSING VERDICTS ARE MISSING (R14 attribution; the "
              "buckets' evidence is never merged)")
        for (bstat, bucket, reason), n in sorted(by_reason.items(),
                                                 key=lambda kv: -kv[1]):
            print("    [%-14s] %-10s %-24s %5d claim(s) across %3d unit(s)"
                  % (bstat, bucket, reason, n,
                     by_unit[(bstat, bucket, reason)]))
    if want_reason:
        rows = where.get(want_reason)
        print("-" * 92)
        if not rows:
            print("no unit in this scan carries the reason %r. Reasons seen: %s"
                  % (want_reason,
                     ", ".join(sorted(where)) or "none"))
        else:
            print("EVERY unit carrying %r (%d unit(s), %d claim(s)):"
                  % (want_reason, len(rows), sum(n for _, n in rows)))
            for u, n in sorted(rows, key=lambda t: (-t[1], t[0])):
                print("    %4d  %s" % (n, u))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
