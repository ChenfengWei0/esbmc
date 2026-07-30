#!/usr/bin/env python3
"""T2: the per-unit RUNNABILITY distribution for complete-path enumeration.

What this measures, and why it is a separate quantity from anything already on
disk: notes/coverage/ holds the LOCKED *branch* coverage dataset. Branch
coverage finishes in about a second per unit. Complete-path enumeration is the
thing whose cost is unknown -- EscrowSrc.withdraw finishes in seconds while
EscrowDst.withdraw, same project, adjacent file, same shape, did not finish in
880 seconds. Table 1 and table 5 both need that distribution and it does not
exist today.

The unit list is NOT re-derived. Every unit, its contract, and the project's
--coverage-exclude-contract list are read out of the locked collector's own
JSON, which has carried them since May. Only the coverage flags are swapped:
--branch-coverage-claims --k-induction --unlimited-k-steps becomes
--solidity-path-coverage --solidity-max-tx 1 --cov-report-json. Same scope
control, different coverage mode, so the two datasets stay comparable unit for
unit.

THE COLUMN THAT MATTERS IS PER-UNIT, AND THE FIRST VERSION OF THIS SCRIPT GOT
IT WRONG. `instrumented N complete path(s)` is CONTRACT-WIDE, so it is
identical for every unit of a contract -- which is correct (T2.0 confirmed
--focus-function does not change enumeration) and useless as a distribution: it
produced a column of six identical 2846s that looked like data. The per-unit
count has to come from the report, by grouping claims on `path_function`. Both
are recorded here, with the contract-wide one clearly labelled as context.

Sampling rules, fixed here rather than decided while running (plan 1.2/T2.3):
  * per-unit hard cap 600 s -- a unit that hits it is recorded as NOT COMPLETED
    and stays in the denominator. "Did not finish" is one of the numbers being
    measured, not a gap in the data.
  * 2 timed-out units in one project -> move to the next project (breadth
    first), so one pathological project cannot eat the slice.
  * units are taken in LEXICOGRAPHIC order -- arbitrary, but reproducible and
    independent of the results, which "whatever we got to" is not.
  * results are appended to disk after EVERY unit. Last night produced four core
    dumps; batching the write would have thrown away the clean rows with them.

The script is resumable: it reads back what is already on disk and skips those
units, so it can be run repeatedly inside a bounded foreground window instead of
being detached. Nothing here runs esbmc in the background.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
DATA = "/home/samson/workspace/esbmc/notes/coverage/data"
OUT = "/home/samson/workspace/esbmc/notes/runnability-distribution.md"
# The evidence behind every recorded row used to be written into a
# session-scoped scratchpad belonging to a different project, which
# os.makedirs(exist_ok=True) happily recreated after that session was gone. The
# audit trail for a hundred published rows lived somewhere unrecoverable and
# nothing said so. It belongs beside the table it supports.
LOGDIR = os.path.join(os.path.dirname(OUT), "t2logs")

# DEVIATION FROM THE PLAN'S 600s, STATED RATHER THAN ABSORBED.
# The plan sets a 600s per-unit cap. The agent's foreground command window is
# 590s, and esbmc must never be detached, so a unit given 600s can only ever be
# CUT OFF by the window -- producing no row at all -- instead of being recorded
# as a timeout. That is strictly worse than a slightly smaller cap: it turns a
# measured "did not finish" into a missing row. 540s leaves room for the run to
# be recorded. The only units this misreports are those that would have
# finished between 540s and 600s; they are recorded as not finishing.
UNIT_CAP = 540
TIMEOUTS_PER_PROJECT = 2
MEMLIMIT = "20g"

BENCHES = [
    "aqua_Aqua",
    "cross_chain_swap_EscrowDst",
    "cross_chain_swap_EscrowSrc",
    "farming",
    "limit_order_protocol",
    "st1inch_St1inch",
]


def excludes_from(cmd):
    out, toks = [], cmd.split()
    for i, t in enumerate(toks):
        if t == "--coverage-exclude-contract" and i + 1 < len(toks):
            out.append(toks[i + 1])
    return out


def units_of(bench):
    with open(os.path.join(DATA, f"esbmc_{bench}.json")) as f:
        rep = json.load(f)
    flat = rep["flatInput"]
    out = []
    for fn in rep["per_function"]["functions"]:
        cmd = fn["commandUsed"]
        out.append({
            "bench": bench,
            "contract": fn["contract"],
            "function": fn["function"],
            "focus": "--focus-function" in cmd,
            "excludes": excludes_from(cmd),
            "flat": flat,
        })
    out.sort(key=lambda u: (u["contract"], u["function"]))
    return out


PATHS_RE = re.compile(
    r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")

# THE TOOL'S OWN GUARD, WHICH THIS SCRIPT ORIGINALLY IGNORED.
# On st1inch, --focus-function enters no unit at all: 0 of 243 instrumented
# claims reach the solver, and ESBMC says so in as many words --
#   "INTERNAL DEFECT -- NOT ONE of the N instrumented path claim(s) reached the
#    solver. The harness never entered any unit, so this run establishes nothing
#    whatsoever ... This is a tool failure, not a result."
# The first version of this script counted those runs as COMPLETED, because its
# completion test was "the instrumented line is present and we did not time
# out", and that line IS present. Twenty-two rows of dashes were therefore
# recorded as successful measurements of a unit with no paths. That is failure
# disguised as a result, in the collector rather than in the tool -- and the
# tool had already refused to make the same mistake.
INTERNAL_DEFECT = re.compile(r"INTERNAL DEFECT")
REACHED_RE = re.compile(r"(\d+) of (\d+) instrumented path claim\(s\) reached")


def unit_rows(report_path, function):
    """This unit's own path claims, grouped out of the report.

    Match on the mangled `path_function`, whose shape is
    sol:@C@<contract>@F@<name>#<id>. Matching the plain name would also match a
    same-named method on another contract in the same flat, and these flats
    carry whole dependency trees.
    """
    if not os.path.exists(report_path):
        return None
    try:
        with open(report_path) as f:
            rep = json.load(f)
    except (OSError, ValueError):
        return None
    marker = f"@F@{function}#"
    tot = f_ = i_ = u_ = 0
    for c in rep.get("claims", []):
        pf = c.get("path_function") or ""
        if marker not in pf:
            continue
        tot += 1
        s = c.get("status")
        if s == "F":
            f_ += 1
        elif s == "I":
            i_ += 1
        else:
            u_ += 1
    return {"total": tot, "F": f_, "I": i_, "U": u_}


def run_unit(u, cap):
    cwd = tempfile.mkdtemp(prefix="t2-")
    cmd = [ESBMC, u["flat"] + ".solast", "--sol", u["flat"]]
    if u["focus"]:
        cmd += ["--contract", u["contract"], "--focus-function", u["function"]]
    else:
        cmd += ["--function", u["function"]]
    for e in u["excludes"]:
        cmd += ["--coverage-exclude-contract", e]
    cmd += ["--solidity-path-coverage", "--solidity-max-tx", "1",
            "--cov-report-json", "--memlimit", MEMLIMIT, "--result-only"]

    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=cap)
        log, rc = p.stdout + p.stderr, p.returncode
        timed_out = False
    except subprocess.TimeoutExpired as e:
        def _t(b):
            if b is None:
                return ""
            return b.decode(errors="replace") if isinstance(b, bytes) else b
        log, rc, timed_out = _t(e.stdout) + _t(e.stderr), None, True
    wall = round(time.time() - t0, 1)

    r = {"wall": wall, "timeout": timed_out, "exit": rc, "cap": cap}
    m = PATHS_RE.search(log)
    r["contract_wide"] = int(m.group(1)) if m else None
    r["unit"] = unit_rows(os.path.join(cwd, "cov-report.json"), u["function"])
    m = REACHED_RE.search(log)
    r["tool_failure"] = bool(INTERNAL_DEFECT.search(log)) or (
        m is not None and m.group(1) == "0" and m.group(2) != "0")
    # COMPLETED means the enumeration itself finished AND the harness actually
    # entered a unit. Dropping the second half is what let a run the tool itself
    # calls an internal defect be recorded as a successful measurement.
    # `r["unit"] is not None` is the third half, and leaving it out reopened
    # the same hole the paragraph above closed: unit_rows() returns None when
    # cov-report.json is missing, and a run that finished, printed the
    # instrumented line and tripped no INTERNAL DEFECT would then be written as
    # a row of dashes marked `yes` -- exactly the shape that was already
    # diagnosed once and fixed only for the one cause then known.
    r["completed"] = ((not timed_out) and (r["contract_wide"] is not None)
                      and not r["tool_failure"] and r["unit"] is not None)
    return r, log


def already_done():
    """(done keys, timeouts per bench) read back OFF DISK.

    The timeout counter must live on disk, not in this process. The breadth-
    first rule ("2 timed-out units end a project") is stated per PROJECT, and
    this script runs as a sequence of bounded foreground slices -- an in-process
    counter restarts at zero every slice, so the rule silently never fires.
    That already happened once: EscrowDst had two recorded timeouts and the
    next slice carried straight on through its library units.
    """
    done, touts, short = set(), {}, []
    if not os.path.exists(OUT):
        return done, touts
    with open(OUT) as f:
        for line in f:
            if not line.startswith("| `"):
                continue
            parts = [p.strip() for p in line.split("|")]
            # | '' | bench | contract | function | paths | F | I | U | wall |
            #   cap | completed | ctr | ''
            if len(parts) < 12:
                continue
            bench = parts[1].strip("` ")
            done.add((bench, parts[2].strip("` "), parts[3].strip("` ")))
            # BY COLUMN, NOT BY SUBSTRING. `"TIMEOUT" in line` also matches a
            # function or contract named *timeout*, and it cannot see the cap
            # the row was actually given.
            if parts[10] != "TIMEOUT":
                continue
            try:
                cap = int(parts[9])
            except ValueError:
                cap = -1
            if cap == UNIT_CAP:
                touts[bench] = touts.get(bench, 0) + 1
            else:
                # A timeout against a partial cap is not a measurement, so it
                # must not count toward the rule that ends a project -- and it
                # must not be silently kept either, because the unit is in
                # `done` and would never be retried.
                short.append((bench, parts[2].strip("` "),
                              parts[3].strip("` "), cap))
    if short:
        rows = "; ".join(f"{b}/{c}.{fn} (cap {cp}s)" for b, c, fn, cp in short)
        sys.exit(
            f"{OUT} contains {len(short)} TIMEOUT row(s) recorded against a "
            f"cap smaller than {UNIT_CAP}s: {rows}. Those are slice artifacts, "
            f"not measurements (see the cap fix in main()). Delete those rows "
            f"and re-run so the units are measured at the full cap.")
    return done, touts


HEADER = (
    "# Complete-path enumeration: per-unit runnability\n\n"
    "Measured by `t2_runnability.py` on esbmc `bea5dfe87b`. Per-unit cap "
    f"{UNIT_CAP}s, memlimit {MEMLIMIT}, units in lexicographic order, "
    "2 timed-out units end a project.\n"
    "A unit that did not finish stays in the denominator: \"did not finish\" "
    "is one of the measured values.\n\n"
    "`unit paths` is THIS unit's own complete paths, grouped out of "
    "`cov-report.json` on `path_function`.\n"
    "`ctr` is the contract-wide instrumented count and is CONTEXT ONLY. It was "
    "ASSERTED here to be identical for every unit of a contract\n"
    "because `--focus-function` does not change enumeration (T2.0). THAT "
    "ASSERTION IS REFUTED BY THIS TABLE: FarmingPool.exit reports 1004 where "
    "every other\n"
    "FarmingPool row reports 9536. The column is kept as context and no "
    "distribution claim is made from it.\n\n"
    f"`cap(s)` is the timeout this run was given, and it is always {UNIT_CAP}s: "
    "a slice with less room than that stops rather than\n"
    "recording a short-cap timeout, because such a row is indistinguishable "
    "from a measured one and is not a measurement.\n\n"
    "| bench | contract | function | unit paths | F | I | U | wall(s) | "
    "cap(s) | completed | ctr |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|\n")


def main():
    deadline = time.time() + float(sys.argv[1])
    done, disk_touts = already_done()
    new = not os.path.exists(OUT)
    os.makedirs(LOGDIR, exist_ok=True)
    f = open(OUT, "a")
    if new:
        f.write(HEADER)
        f.flush()

    for bench in BENCHES:
        units = units_of(bench)
        touts = disk_touts.get(bench, 0)
        for u in units:
            key = (bench, u["contract"], u["function"])
            if key in done:
                continue
            if touts >= TIMEOUTS_PER_PROJECT:
                print(f"[{bench}] {touts} timeouts on disk -> next project")
                break
            # A UNIT IS EITHER GIVEN THE FULL CAP OR NOT STARTED.
            # This used to be `cap = int(min(UNIT_CAP, left - 15))`, i.e. the
            # SLICE REMAINDER, floored only by `left < 30`. A unit could
            # therefore be given 15s where its honest budget is 540s, and the
            # row it produced said `TIMEOUT` in the same cell a real 540s
            # timeout uses. It happened: farming/FarmingPool/rescueFunds is on
            # record at wall 85.3s against cap 84 -- a unit that plausibly
            # finishes at ~86s, filed permanently as "did not finish", and
            # never retried because already_done() keys on the unit alone.
            # Worse, two such rows trip TIMEOUTS_PER_PROJECT and silently
            # truncate the rest of the project.
            # A short slice now ends the slice instead of producing a datum
            # that cannot be told apart from a measured one.
            left = deadline - time.time()
            if left - 15 < UNIT_CAP:
                print(f"[slice] {int(left)}s left, less than a full {UNIT_CAP}s "
                      f"cap plus overhead; stopping cleanly rather than "
                      f"recording a short-cap timeout")
                f.close()
                return 0
            cap = UNIT_CAP
            print(f"[run] {bench} {u['contract']}.{u['function']} cap={cap}s",
                  flush=True)
            r, log = run_unit(u, cap)
            with open(os.path.join(
                    LOGDIR, f"{bench}__{u['contract']}__{u['function']}.log"),
                    "w") as lf:
                lf.write(log)
            ur = r["unit"]
            f.write(f"| `{bench}` | `{u['contract']}` | `{u['function']}` | "
                    f"{ur['total'] if ur else '-'} | "
                    f"{ur['F'] if ur else '-'} | "
                    f"{ur['I'] if ur else '-'} | "
                    f"{ur['U'] if ur else '-'} | "
                    f"{r['wall']} | {r['cap']} | "
                    f"{'yes' if r['completed'] else ('TIMEOUT' if r['timeout'] else ('TOOL-FAILURE' if r['tool_failure'] else 'no'))} | "
                    f"{r['contract_wide'] if r['contract_wide'] is not None else '-'} |\n")
            f.flush()
            os.fsync(f.fileno())
            print(f"   -> unit={ur} wall={r['wall']}s "
                  f"completed={r['completed']}", flush=True)
            if r["timeout"]:
                touts += 1
    f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
