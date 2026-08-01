#!/usr/bin/env python3
"""Was a KILLED unit STUCK, or did it simply run out of budget?

The two call for opposite next actions -- reduce it to a PoC versus give it more
time -- and the sweep's own bucket cannot tell them apart, by design: `bucket()`
files anything with a TIMEOUT as KILLED and says so ("a budget outcome filed as a
search result is the failure-as-result pattern this corpus keeps hitting").
Deciding which it was needs the driver's log, and this reads it.

WHY A SCRIPT RATHER THAN READING THEM. The evidence was there the whole time and
nobody had looked: of the six KILLED units in the corpus sweep, FIVE have logs --
aqua.ship 15418 lines, EscrowDst.publicWithdraw 1441, EscrowDst.withdraw 1279,
farming.rescueFunds 1192, farming.startFarming 117 -- and the task list said they
had "no minimal reproduction at all". At 15418 lines the answer is not reachable
by eye, and the classification that answers it is already in the driver's own
line prefixes.

WHAT COUNTS AS PROGRESS, stated rather than eyeballed. The driver's loop is
enumerate -> level-0 -> geometric bracket -> refine -> per-path certify+shrink.
A run that reached the per-path stage and completed K of N paths was WORKING when
it died; a run that never got past enumeration, or whose rounds all report
measuring nothing, was not. So the verdict is read off four counts:

    rounds finished        `[round] <name>: <wall>s`
    rounds that measured   a round NOT followed by "ROUND MEASURED NOTHING"
    paths certified        distinct enc in `[certify enc=N]`
    shrink steps           `[shrink enc=N]` -- the innermost work item

and the LAST LINE, which says where it actually stopped.

THE BUFFERING CAVEAT IS PART OF THE OUTPUT, not a footnote. A killed run loses
whatever was still in Python's stdout pipe buffer, i.e. up to the last ~8 KiB.
That is nothing for a 15418-line log and EVERYTHING for a two-line one, so a log
at or under one buffer is reported as EVIDENCE LOST rather than as a run that
printed nothing. Both certify sweeps now pass `-u`, so future runs do not have
this hole; the logs already on disk still do.
"""
import re
import sys
from collections import Counter
from pathlib import Path

RE_ROUND = re.compile(r"^\[round\] ([^:]+): ([0-9.]+)s wall, (\d+) coordinate")
RE_ACCT = re.compile(r"^\[round\] accounting: (.*)$")
RE_NOTHING = re.compile(r"ROUND MEASURED NOTHING")
RE_CERTIFY = re.compile(r"^\[certify enc=(\d+)\]")
RE_SHRINK = re.compile(r"^\[shrink enc=(\d+)\]")
RE_ENUM = re.compile(r"^\[enumerate\] (\d+) witnessed path\(s\)")
RE_TIMEOUT = re.compile(r"^\[run\] TIMEOUT after (\d+)s")
RE_VACUITY = re.compile(r"ONE-VALUE candidate list")

# ---- THE INNER KILL, which is a DIFFERENT failure from the outer one ----
#
# Four of the big logs turned out to be the driver quoting esbmc verbatim: their
# line 2 is `[enumerate] ESBMC produced no cov-report.json. Its output was:`, so
# the run that died is the ENUMERATION, before the generalisation loop existed to
# have rounds at all. Reporting that as "stuck in enumeration" is true and
# useless; what decides the next action is how far the enumeration itself got,
# and esbmc says so in its own output.
RE_INNER = re.compile(r"^\[enumerate\] ESBMC produced no cov-report\.json")
RE_SYMEX = re.compile(r"^Symex completed in: ([0-9.]+)s")
RE_VCC = re.compile(r"^Generated (\d+) VCC\(s\), (\d+) remaining")
# The mid-solve journal line. This is the fact that overturns "a killed run
# produced nothing": it names how many claims were DECIDED and how many paths
# are already ON DISK with their payloads.
RE_JOURNAL = re.compile(
    r"CE journal \S+ updated after claim (\d+) of (\d+): (\d+) witnessed")
RE_UNWIND_REC = re.compile(r"^Unwinding recursion (\S+) iteration")

# One stdio buffer. A log at or below this had its whole content at risk.
BUFFER_BYTES = 8192


def triage(path: Path):
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace")
    lines = text.splitlines()
    rounds, nothing, certified, shrinks, vacuity = [], 0, set(), 0, 0
    witnessed = None
    timeout_s = None
    inner = False
    symex_s = None
    vccs = None
    journal = None          # (claims decided, claims total, paths on disk)
    recursions = Counter()  # which callee's re-entry model dominates symex
    for ln in lines:
        if RE_INNER.match(ln):
            inner = True
        m = RE_SYMEX.match(ln)
        if m:
            symex_s = float(m.group(1))
        m = RE_VCC.match(ln)
        if m:
            vccs = int(m.group(1))
        m = RE_JOURNAL.search(ln)
        if m:
            # LAST wins: the journal line is re-printed per witness, and the
            # question is how far it got, not where it started.
            journal = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = RE_UNWIND_REC.match(ln)
        if m:
            recursions[m.group(1)] += 1
    for ln in lines:
        m = RE_ROUND.match(ln)
        if m:
            rounds.append((m.group(1), float(m.group(2))))
        if RE_NOTHING.search(ln):
            nothing += 1
        m = RE_CERTIFY.match(ln)
        if m:
            certified.add(m.group(1))
        if RE_SHRINK.match(ln):
            shrinks += 1
        m = RE_ENUM.match(ln)
        if m:
            witnessed = int(m.group(1))
        m = RE_TIMEOUT.match(ln)
        if m:
            timeout_s = int(m.group(1))
        if RE_VACUITY.search(ln):
            vacuity += 1

    # THE VERDICT, and it is deliberately conservative in one direction: only a
    # run that reached the per-path stage is called a budget outcome. A run that
    # never got there might still be merely slow, but it might also be stuck, and
    # calling it "just needs more time" is the reading that stops anyone looking.
    if timeout_s is None:
        verdict = "NOT KILLED (no TIMEOUT line)"
    elif len(raw) <= BUFFER_BYTES and not rounds and not inner:
        verdict = ("EVIDENCE LOST — the log fits inside one stdio buffer and "
                   "carries no round, so the kill discarded it. Cannot be "
                   "triaged; re-run (both sweeps now pass -u)")
    elif inner:
        # The ENUMERATION esbmc run died, so there is no generalisation loop to
        # report on. Its progress is the claim count, and it is on the record
        # rather than inferred: the CE journal names both the numerator and the
        # denominator, and names how many payloads reached disk -- which is what
        # makes "a killed run produced nothing" false.
        if journal:
            k, n, w = journal
            verdict = (f"BUDGET, IN ENUMERATION — the inner esbmc run was "
                       f"killed, having DECIDED {k} of {n} claim(s) and left "
                       f"{w} witnessed path(s) with payloads in "
                       f"cov-ce-journal.json. Not nothing: that file is the "
                       f"result this run did produce")
        elif vccs:
            verdict = (f"BUDGET, IN ENUMERATION — the inner esbmc run was "
                       f"killed after generating {vccs} VCC(s); no journal line "
                       f"was reached, so no claim had been decided")
        else:
            verdict = ("STUCK BEFORE VCC GENERATION — the inner esbmc run was "
                       "killed before it printed a VCC count. If symex is the "
                       "cost, that is a frontend/model question, not a budget "
                       "one")
    elif certified:
        verdict = (f"BUDGET — it was WORKING when killed: {len(certified)} of "
                   f"{witnessed if witnessed is not None else '?'} path(s) "
                   f"reached certification, {shrinks} shrink step(s)")
    elif rounds and nothing >= len(rounds):
        verdict = ("STUCK-OR-TOO-SLOW — every round it finished reported "
                   "measuring NOTHING, so more time buys the same result "
                   "unless the round cost itself changes")
    elif rounds:
        verdict = (f"BUDGET, EARLIER — {len(rounds)} round(s) finished but the "
                   f"per-path certification never started")
    elif witnessed is not None:
        verdict = ("BUDGET, EARLIEST — enumeration finished, no round did. The "
                   "first round's cost is the thing to measure")
    else:
        verdict = "STUCK IN ENUMERATION — no witnessed-path line was printed"

    return {
        "path": str(path), "bytes": len(raw), "lines": len(lines),
        "witnessed": witnessed, "rounds": rounds, "measured_nothing": nothing,
        "certified": len(certified), "shrinks": shrinks,
        "vacuity_warnings": vacuity, "timeout_s": timeout_s,
        "last": lines[-1][:160] if lines else "", "verdict": verdict,
        "inner": inner, "symex_s": symex_s, "vccs": vccs, "journal": journal,
        "recursions": recursions,
    }


def main(argv):
    roots = argv[1:] or ["/tmp/certify_all", "/tmp/certify_poc"]
    logs = []
    for r in roots:
        logs.extend(sorted(Path(r).glob("*/*/driver.log")))
    if not logs:
        print("no driver.log under: " + ", ".join(roots))
        return 1
    shown = 0
    for p in logs:
        t = triage(p)
        if t["timeout_s"] is None:
            continue  # only KILLED runs are being triaged
        shown += 1
        who = "/".join(p.parts[-3:-1])
        print(f"\n===== {who}   ({t['lines']} lines, {t['bytes']} bytes, "
              f"killed at {t['timeout_s']}s)")
        if t["inner"]:
            # Printed FIRST, because it changes what every line below means: the
            # rounds are absent not because the loop failed but because the loop
            # never started.
            print(f"  WHAT DIED           : the ENUMERATION esbmc run (the "
                  f"driver quoted its output verbatim); the generalisation "
                  f"loop never started")
            if t["symex_s"] is not None:
                print(f"  symex               : {t['symex_s']:.1f}s of the "
                      f"{t['timeout_s']}s budget")
            if t["vccs"] is not None:
                print(f"  VCCs generated      : {t['vccs']}")
            if t["journal"]:
                k, n, w = t["journal"]
                print(f"  claims decided      : {k} of {n}   "
                      f"({100.0 * k / n:.0f}%)")
                print(f"  ON DISK ALREADY     : {w} witnessed path(s) with "
                      f"payloads in cov-ce-journal.json")
            if t["recursions"]:
                top = t["recursions"].most_common(3)
                print("  re-entry unwinding  : " + ", ".join(
                    f"{k} x{v}" for k, v in top))
        print(f"  witnessed paths      : {t['witnessed']}")
        print(f"  rounds finished      : " +
              (", ".join(f"{n} {w:.1f}s" for n, w in t["rounds"]) or "(none)"))
        print(f"  rounds measuring none: {t['measured_nothing']}")
        print(f"  paths certified      : {t['certified']}   "
              f"shrink steps: {t['shrinks']}")
        if t["vacuity_warnings"]:
            print(f"  ⚠ one-value-candidate vacuity warnings: "
                  f"{t['vacuity_warnings']}")
        print(f"  last line            : {t['last']}")
        print(f"  VERDICT              : {t['verdict']}")
    if not shown:
        print("no KILLED run found (every driver.log ended without a TIMEOUT)")
    print(f"\n{shown} killed run(s) triaged. A BUDGET verdict is NOT a result "
          f"about the contract; it is a statement about the --timeout it was "
          f"given, and quoting it as coverage is the failure this file exists "
          f"to prevent.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
