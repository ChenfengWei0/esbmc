#!/usr/bin/env python3
"""Stage-2 across the corpus: run the generalisation driver on every unit.

EXECUTION_PLAN step 2.2 ("全基准跑认证, 报 adm(c) 分布 + 被切成单点的坐标占比")
has been open since the plan was written, and the reason is prosaic: there was
no sweep. `solidity_path_generalise.py` is invoked one unit at a time, so every
stage-2 number this project has ever quoted came from a hand-run unit -- which
is exactly how a single-contract result gets generalised by accident.

WHAT IT MEASURES, and why each column is here rather than derived later:

  * the OUTCOME BUCKET per unit, kept apart on purpose. "certified 0 of 4" and
    "the run was killed" are different findings and collapsing them is the
    failure-as-result pattern this repository keeps running into. The buckets
    are CERTIFIED / NOT-CERTIFIED-with-reason / NO-COORDINATE / KILLED / CRASHED
    / NO-PATH / NO-WITNESS-UNDECIDED / NO-WITNESS-UNKNOWN, and a unit lands in
    exactly one. The last three were ONE bucket until St1inch.balanceOf came
    back with two of three claims abandoned at the per-claim budget and was
    filed as NO-PATH -- i.e. as a property of the contract. See the comment on
    RE_NO_WITNESS_REFUSED for what that also implies about the default st1inch
    exclusion argued below.
  * the coordinate funnel per unit -- free, pinned, refused, dropped -- read off
    the driver's own lines rather than recomputed here, so the sweep cannot
    disagree with the tool about what was measured.
  * whether the S10 msg.value pin FIRED, DECLINED (payable) or COULD NOT BE READ.
    That is the difference between 0-of-5 and 4-of-5 on the one contract it has
    been measured on, and a sweep that did not record it would produce a corpus
    number whose largest single input is invisible.

INCREMENTAL BY CONSTRUCTION. Each unit's record is appended to the JSONL before
the next unit starts, so a kill -- which on this corpus is the expected way a
long sweep ends, not the exceptional one -- loses one unit rather than the run.
Re-invoking skips units already in the file unless --redo is given.

NOT A GATE. This prints a table and writes a file; it makes no pass/fail
judgement, because the thresholds Phase 2 talks about have to be picked from a
distribution that does not exist yet, and picking them from the first one
measured is choosing the bar after seeing the scores.
"""

import argparse
import concurrent.futures
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ESBMC_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
INPUTS = os.path.join(ESBMC_ROOT, "notes", "coverage", "inputs")
DRIVER = os.path.join(ESBMC_ROOT, "scripts", "solidity_path_generalise.py")
ESBMC = os.path.join(ESBMC_ROOT, "build", "src", "esbmc", "esbmc")

# benchmark -> (flat source basename, contract). The contract is the part after
# `__` in the input's name, but it is written out rather than parsed: a mapping
# that is derived by string surgery is a mapping that breaks silently when
# someone adds an input whose name has two underscores.
BENCHMARKS = {
    "aqua_Aqua": ("aqua__Aqua.flat.sol", "Aqua"),
    "cross_chain_swap_EscrowSrc": ("cross-chain-swap__EscrowSrc.flat.sol",
                                   "EscrowSrc"),
    "cross_chain_swap_EscrowDst": ("cross-chain-swap__EscrowDst.flat.sol",
                                   "EscrowDst"),
    "farming": ("farming__FarmingPool.flat.sol", "FarmingPool"),
    "limit_order_protocol": ("limit-order-protocol__MakerTraitsLib.flat.sol",
                             "MakerTraitsLib"),
    "st1inch_St1inch": ("st1inch__St1inch.flat.sol", "St1inch"),
}

# Read off the driver's own stdout. Anchored to the line prefixes it prints, so
# a change in its wording shows up as a missing field rather than as a wrong
# number -- the sweep must never infer a value it did not see.
RE_WITNESSED = re.compile(r"^\[enumerate\] (\d+) witnessed path\(s\)")
RE_COORDS = re.compile(r"^\[coords\] ([^\[]+?)(?:\s+\[pinned: (.*)\])?$")
RE_NO_COORD = re.compile(r"^\[coords\] NO GENERALISABLE COORDINATE — (.*)$")
RE_CERT = re.compile(r"^  enc=(\d+)(?: piece \d+/\d+)?: (.*)$")
RE_NOTCERT = re.compile(r"^  enc=(\d+): NOT CERTIFIED — (.*?); this path falls")
RE_PIN_FIRED = re.compile(r"^\[env\] msg\.value PINNED to 0")
RE_PIN_PAYABLE = re.compile(r"^\[env\] msg\.value NOT pinned: this unit is PAYABLE")
RE_PIN_UNKNOWN = re.compile(r"^\[env\] msg\.value NOT auto-pinned")
# ---- "NO WITNESSED PATH" IS TWO DIFFERENT FINDINGS AND THIS FILE COLLAPSED
# ---- THEM, IN THE ONE FUNCTION WHOSE DOCSTRING PROMISES IT DOES NOT ----
#
# `bucket()` returns NO-PATH whenever `witnessed is None`, and that is the state
# the driver reaches for BOTH of:
#
#   * every claim of the unit was DECIDED and none was witnessed -- a result
#     about this unit under this bound;
#   * some claim was ABANDONED at the per-claim budget, or never reached the
#     solver, or the dispatcher never entered the unit -- outcomes of the RUN
#     and the COMMAND LINE.
#
# MEASURED, St1inch.balanceOf: 3 claims, 2 `claim-budget-exceeded`, 1
# `bounded-holds`. Under the old reading that unit is a NO-PATH row, i.e. a
# property of the contract.
#
# It is not hypothetical for the corpus either. THE DEFAULT EXCLUSION OF
# st1inch, argued in the `benchmarks` help below, rests on the same collapse:
# "all 128 of its claims are U (59 solver-unknown, 69 bounded-holds ...) so
# there is no witnessed path for stage 2 to generalise." 59 solver-unknown is
# the solver giving up, not a measurement of the contract -- so 59 of the 128
# support the exclusion only under the reading this fix rejects. The help text
# already says the claim is "worth re-checking whenever the solver side moves";
# what it did not say is that the two halves of that 128 mean opposite things.
#
# The driver now prints which of the two it is, so this reads its verdict rather
# than re-deriving one. Anchored to the driver's own prefix, same as every
# pattern above it.
RE_NO_WITNESS_REFUSED = re.compile(
    r"^\[enumerate\] no witnessed path for this unit, ⛔ and it is NOT a "
    r"result: (.*)$")
RE_NO_WITNESS_DECIDED = re.compile(
    r"^\[enumerate\] no witnessed path for this unit, and every one of this "
    r"unit's claims was DECIDED")


def binary_identity():
    """Who produced a record: HEAD, whether src/ was dirty, and the binary's
    own mtime.

    HEAD alone lies in exactly the situation this project is always in -- an
    uncommitted fix means two different binaries share one commit -- so the
    mtime and the dirty flag are both load-bearing. Same three fields
    pathcov_collect.py records, on purpose: a record here and a record there
    have to be comparable when someone asks which build a number came from.
    """
    def _sh(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  cwd=ESBMC_ROOT, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    try:
        mtime = int(os.stat(ESBMC).st_mtime)
    except OSError:
        mtime = 0
    return {
        "head": _sh(["git", "rev-parse", "--short", "HEAD"]),
        "srcDirty": bool(_sh(["git", "status", "--porcelain", "--", "src/"])),
        "binaryMtime": mtime,
    }


def _killpg(proc):
    """Kill a subprocess's whole process GROUP, tolerating a dead one.

    Paired with `start_new_session=True`. Killing only the direct child leaves
    esbmc grandchildren alive holding their full `--memlimit`, which is what
    makes `jobs * memlimit` stop being a bound. Idempotent, because it is called
    from both the timeout path and a `finally`.
    """
    if proc is None:
        return
    # Called even after a NORMAL exit, on purpose: the group is then empty and
    # killpg raises ESRCH, which is caught. That costs nothing and means there
    # is exactly one reaping path instead of two that can drift apart.
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError, AttributeError):
        pass


def available_gib():
    """MemAvailable, in GiB. None when it cannot be read.

    Read rather than assumed, because the whole point of the budget below is to
    replace a guess with an arithmetic bound. `MemAvailable` is the kernel's own
    estimate of what can be handed out without swapping, which is the quantity
    that matters -- `MemFree` would under-count by the whole page cache.
    """
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except (OSError, ValueError, IndexError):
        return None
    return None


def job_memlimit_gib(jobs, reserve_frac=0.60, floor_gib=4):
    """Per-job `--memlimit`, or a refusal string. NEVER a silent degradation.

    THE RULE THIS REPLACES, AND WHY IT IS SAFE TO REPLACE IT. The project rule
    has been "never run esbmc concurrently -- it exhausted the machine once and
    forced a reboot". That rule is right, and its stated REASON is memory. The
    crash predates the discipline of passing `--memlimit` on every run; with a
    limit enforced per process, "how many fit" stops being a guess and becomes
    arithmetic over a number the kernel publishes.

    So this does not relax the rule, it discharges it: `jobs * memlimit` must fit
    inside a fraction of measured MemAvailable, and if it does not, this returns
    a REFUSAL rather than a smaller limit. Quietly shrinking the limit would be
    the failure this repository keeps hitting from the other side -- a bound that
    silently rewrites the thing it was supposed to bound.

    `reserve_frac` 0.60 leaves the rest for the page cache, the driver processes
    themselves and whatever else the machine is doing. `floor_gib` 4 is the point
    below which a real benchmark unit starts dying of the limit rather than of
    the problem, which would turn a parallelism decision into a measurement
    change.
    """
    if jobs <= 1:
        return 8, None
    avail = available_gib()
    if avail is None:
        return None, ("cannot read MemAvailable from /proc/meminfo, so the "
                      "memory budget for parallel jobs cannot be computed. "
                      "Refusing to guess -- run with --jobs 1")
    budget = avail * reserve_frac
    per = int(budget // jobs)
    if per < floor_gib:
        return None, (
            f"--jobs {jobs} does not fit: MemAvailable is {avail:.1f} GiB, the "
            f"budget is {reserve_frac:.0%} of that = {budget:.1f} GiB, which is "
            f"{per} GiB per job and below the {floor_gib} GiB floor. Below the "
            f"floor a unit starts dying of the memory limit rather than of the "
            f"problem, which would make this a measurement change and not a "
            f"scheduling one. Use --jobs {max(1, int(budget // floor_gib))} or "
            f"fewer")
    return per, None


def units_of(bench):
    """The unit list, read from the round-trip's own `emit.jsonl`.

    NOT from a whole-contract enumeration run. That was the first attempt and it
    is measured not to work: on aqua -- the SMALLEST benchmark -- the
    whole-contract run is killed at 180s and produces no report at all, which is
    exactly why `--focus` exists and why the driver's own docstring says
    whole-contract EscrowSrc exceeds a 900s budget with nothing to show. A unit
    list that costs more than the measurement is not a unit list.

    NOT from the AST either, though that was the obvious fallback. `emit.jsonl`
    has a property the AST cannot give: it is the unit set the COVERAGE sweep
    actually ran, committed, per benchmark. Reading it makes the stage-2 numbers
    and the coverage numbers statements about the same units -- and this project
    has already spent a session discovering that two measurements sharing a
    benchmark name and nothing else cannot be divided by one another.

    Each tag is `<Contract>__<method>`. The method is what `--unit` takes; the
    contract part is recorded but not used to select, because a unit inherited
    from a base contract is legitimately a unit of the contract under test
    (BaseEscrow.rescueFunds is measured under EscrowSrc, and correctly so).

    Library internals appear here too and are NOT filtered out. They are not
    units -- a unit is public or external -- so the driver reports NO-PATH for
    them, and that record is worth having: it is the difference between "the
    library was not measured" and "the library has nothing a unit-level method
    can measure", which is a distinction this corpus has already needed once
    (ImmutablesLib 0/8).
    """
    path = os.path.join(ESBMC_ROOT, "notes", "coverage", "forge_roundtrip",
                        bench, "emit.jsonl")
    if not os.path.exists(path):
        return None, (f"no {path}; the round-trip has not been run for this "
                      f"benchmark, so there is no unit set to be commensurable "
                      f"with. Run forge_roundtrip.py first")
    units, killed = [], []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            tag = r.get("tag") or ""
            if "__" not in tag:
                continue
            c, m = tag.split("__", 1)
            if m not in [u[1] for u in units]:
                units.append((c, m))
            if r.get("killed"):
                killed.append(m)
    if not units:
        return None, f"{path} names no <Contract>__<method> tag"
    return (units, killed), None


def parse_driver(out):
    """The driver's own report, as a record. Nothing is inferred."""
    rec = {"witnessed": None, "coords": [], "pins": None,
           "no_coordinate_reason": None, "certified": {}, "not_certified": {},
           "msg_value_pin": "not seen",
           # None means the driver printed NEITHER verdict -- an older driver,
           # or a run that died before reaching the branch. Deliberately not
           # defaulted to either side: a missing field is an unknown, and this
           # sweep already makes that distinction for `env_coord`.
           "empty_witness_verdict": None, "empty_witness_reason": None}
    for line in out.splitlines():
        m = RE_WITNESSED.match(line)
        if m:
            rec["witnessed"] = int(m.group(1))
        m = RE_NO_WITNESS_REFUSED.match(line)
        if m:
            rec["empty_witness_verdict"] = "REFUSED"
            rec["empty_witness_reason"] = m.group(1)
        elif RE_NO_WITNESS_DECIDED.match(line):
            rec["empty_witness_verdict"] = "DECIDED"
        if RE_PIN_FIRED.match(line):
            rec["msg_value_pin"] = "fired"
        elif RE_PIN_PAYABLE.match(line):
            rec["msg_value_pin"] = "declined (payable)"
        elif RE_PIN_UNKNOWN.match(line):
            rec["msg_value_pin"] = "could not be read"
        m = RE_NO_COORD.match(line)
        if m:
            rec["no_coordinate_reason"] = m.group(1)
            continue
        m = RE_COORDS.match(line)
        if m and not line.startswith(("[coords] DROPPED", "[coords] NOT ",
                                      "[coords] no --ast", "[coords] every",
                                      "[coords] UNSUPPORTED",
                                      "[coords] ACCOUNTING")):
            rec["coords"] = [c.strip() for c in m.group(1).split(",")
                             if c.strip()]
            rec["pins"] = m.group(2)
        m = RE_NOTCERT.match(line)
        if m:
            rec["not_certified"][m.group(1)] = m.group(2)
            continue
        m = RE_CERT.match(line)
        if m and "NOT CERTIFIED" not in line and "the region of this path" \
                not in line:
            rec["certified"][m.group(1)] = m.group(2)
    return rec


def bucket(rec, rc, out):
    """Exactly one outcome per unit, and the failure kinds stay apart.

    Order matters: a run that was KILLED may still have printed a coordinate
    list, and reporting that as "0 certified" would file a budget outcome as a
    search result. Same rule the driver's own round_failure_reason follows.
    """
    if "[run] TIMEOUT after" in out or rc == 124:
        return "KILLED"
    if rc not in (0, 1):
        return "CRASHED"
    if rec["no_coordinate_reason"]:
        return "NO-COORDINATE"
    if rec["certified"]:
        return "CERTIFIED"
    if rec["witnessed"] is None:
        # NO-PATH is reserved for the case the driver says was DECIDED. An
        # abandoned or undecided claim gets its own bucket, because a reader
        # summing NO-PATH rows is counting units the method cannot address, and
        # a unit whose claims were dropped at the budget is not one of those.
        v = rec.get("empty_witness_verdict")
        if v == "REFUSED":
            return "NO-WITNESS-UNDECIDED"
        if v is None:
            # The driver printed neither verdict. Older records and dead runs
            # land here, and they must not be silently promoted into NO-PATH:
            # every NO-PATH row is supposed to mean the run answered.
            return "NO-WITNESS-UNKNOWN"
        return "NO-PATH"
    return "NOT-CERTIFIED"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("benchmarks", nargs="*", default=[],
                    help="which to sweep; default is every one that has ever "
                         "produced a witnessed path. st1inch is EXCLUDED by "
                         "default, and the REASON has changed: it used to be "
                         "that all 22 of its runs were killed. They are not any "
                         "more -- the current collection is 21/22 with reports. "
                         "It is excluded now because all 128 of its claims are "
                         "U (59 solver-unknown, 69 bounded-holds, 0 "
                         "unit-not-entered), so there is no witnessed path for "
                         "stage 2 to generalise. Measured, not assumed, and "
                         "worth re-checking whenever the solver side moves")
    ap.add_argument("--out", default=os.path.join(ESBMC_ROOT, "notes",
                                                  "coverage", "certify",
                                                  "results.jsonl"))
    ap.add_argument("--timeout", type=int, default=600,
                    help="per DRIVER invocation, i.e. one unit's WHOLE loop -- "
                         "enumeration, level 0, the geometric bracket, every "
                         "refine round and every certification query. MEASURED: "
                         "at 120s SIX of aqua's eight units were killed, "
                         "because the geometric bracket alone takes 50-70s on a "
                         "TOY contract. A budget below the bracket's own cost "
                         "measures the budget, not the method.")
    # ---- THESE THREE ARE BELOW THE DRIVER'S OWN DEFAULTS, AND UNARGUED ----
    #
    # `solidity_path_generalise.py` defaults to probes=16, refine-rounds=3,
    # shrink-rounds=4. This sweep runs 8 / 2 / 3. `--timeout 600` above carries a
    # measured reason for its value; these three carry none, and never have.
    #
    # That matters because the LARGEST bucket in `certify_summary.py`'s "WHY
    # units did NOT certify" table is `shrink round budget exhausted` -- a count
    # whose meaning is entirely a function of a number nobody argued for. A
    # reader of that table is being told how often the method ran out of a budget
    # this sweep chose, not how often the method could not cut.
    #
    # MEASURED on the current sweep (binary 01f7dc37e1), EscrowSrc.cancel enc=15,
    # whose witness diverges on ONE bounded coordinate and no environment
    # quantity -- the cleanest case in that unit: the path's own counterexample
    # has `immutables.amount = 0` and after three shrink rounds the refuting
    # witness sits at 2^253-1. Three halvings of a 256-bit range, which is the
    # degenerate bisection the level-0 mechanism exists to avoid and which level 0
    # declined to apply here (the coordinate is not a point for every path).
    # Reaching a bound that way needs about 256 rounds, so at 3 the budget cannot
    # be crossed by any unit whose boundary is not already near the type limit.
    # ONE unit and one path, so this is a worked example, not a corpus claim.
    #
    # WHAT WOULD SETTLE IT, and it is cheap: re-run one such unit at
    # --shrink-rounds 3 / 8 / 16 and read where the reason lands. Three outcomes,
    # each a different fact: it certifies (the budget was the whole story), or the
    # reason moves to `refuted with no single-coordinate cut available` (a METHOD
    # property, which is what EXECUTION_PLAN's T1 criterion 3 asks for), or it
    # stays in the budget cell at 16 (bisection is the story and the repair is a
    # better cut, not a bigger budget). Until that is run, no number derived from
    # the budget cell may be read as a property of the method.
    ap.add_argument("--shrink-rounds", type=int, default=3,
                    help="how many refutations one region may absorb. BELOW the "
                         "driver's own default of 4, and the value is UNARGUED "
                         "-- see the comment above this flag for what it costs "
                         "and the three-arm run that would settle it.")
    ap.add_argument("--refine-rounds", type=int, default=2,
                    help="BELOW the driver's own default of 3, and unargued. "
                         "Fewer refine rounds means the span handed to "
                         "certification is coarser, so the shrink loop starts "
                         "further from the boundary and the budget above binds "
                         "sooner -- the two flags are not independent.")
    ap.add_argument("--probes", type=int, default=8,
                    help="HALF the driver's own default of 16, and unargued. "
                         "Resolution per refine round divides by (probes+1), so "
                         "this compounds with --refine-rounds above.")
    ap.add_argument("--skip-bracket", action="store_true",
                    help="the geometric bracket is the binding cost on real "
                         "input -- 258 probes per coordinate per direction. "
                         "Measured on the S4 fixture: a run whose bracket hit "
                         "its budget and measured NOTHING still produced every "
                         "exact region from level 0 plus refinement. ONE shape, "
                         "so this is offered rather than made default.")
    ap.add_argument("--level0", action="store_true", default=True)
    ap.add_argument("--jobs", type=int, default=1,
                    help="how many units to certify CONCURRENTLY. Default 1, "
                         "which is the historical behaviour.\n"
                         "The project rule has been 'never run esbmc "
                         "concurrently' -- it exhausted this machine once and "
                         "forced a reboot. That rule is right and its stated "
                         "REASON is memory; the crash predates the discipline "
                         "of passing --memlimit on every run. With a limit "
                         "enforced per process, how many fit is arithmetic over "
                         "a number the kernel publishes, not a guess.\n"
                         "So this does not relax the rule, it discharges it: "
                         "jobs x memlimit must fit inside 60%% of measured "
                         "MemAvailable, and if it does not the sweep REFUSES "
                         "rather than shrinking the limit. A silently smaller "
                         "limit would turn a scheduling decision into a "
                         "measurement change -- units would start dying of the "
                         "limit instead of the problem.")
    # ---- THE TWO FLAGS THAT MAKE AN ARM RECORDABLE INSTEAD OF HAND-RUN ----
    #
    # MEASURED, and it is why these exist: the four
    # `FarmingPoolCovTest_FarmingPool_transfer_put*.t.sol` on disk are green under
    # forge at `runs: 256`, and NO cert file contains a row for unit `transfer` --
    # all three sweeps were walked row by row and each reports "no row for unit
    # 'transfer'". Those regions came from `solidity_path_generalise.py` run by
    # hand with `--env-coord msg.sender`, because this sweep had no way to pass
    # that flag. The artefact survived; the input that produced it did not, so
    # "B = 4" could not be re-derived by re-running anything.
    #
    # That is the failure `put_all.py --cert` was added to prevent, one stage
    # earlier: an ARM whose results cannot be written into a file of their own is
    # an arm that has to be hand-run, and a hand-run measurement is one nobody
    # can repeat. Same house rule as `--cert` there -- point the sweep at its own
    # input and its own output, and record the flag ON EVERY ROW so two arms can
    # never be summed by accident.
    ap.add_argument("--env-coord", default=None,
                    help="passed to the driver: promote ONE environment quantity "
                         "(e.g. msg.sender, block.timestamp) to a FREE coordinate "
                         "instead of a pin. Recorded on every row, because a "
                         "region certified with an environment coordinate free is "
                         "a different statement from one certified with it "
                         "pinned, and the two must never share a table. ⚠ Use "
                         "--out to give this arm its OWN file: writing it into "
                         "results.jsonl would put two arms under one "
                         "(benchmark, unit) key.")
    # ---- THE PUNCH ARM, AND WHY ITS ABSENCE PRODUCED A FALSE ZERO ----
    #
    # MEASURED, and it is the reason these exist: not one line of this file
    # mentioned holes, and the driver's `--max-holes` defaults to 0. So the
    # corpus sweep could not ADMIT a hole into any region, whatever the
    # contracts do -- and the recorded corpus result, 0 of 7 certified regions
    # carrying a hole, is a fact about this command line and NOT about the
    # corpus. That is the always-empty-channel shape: a detector that cannot
    # fire reports the same 0 as one that fired and found nothing.
    #
    # The two flags are coupled and are exposed together on purpose. A punch is
    # only reachable when the loop is allowed to keep both sides of a cut
    # (`copy_holes`, driver: "needs both --max-region-pieces > 1 and
    # --max-holes > 0"), so offering the second without the first would be a
    # switch that still cannot fire -- the same failure one flag further in.
    #
    # WHY IT MATTERS RATHER THAN BEING TIDINESS: a side cut discards the whole
    # side that does not hold this path's counterexample, so WHICH side survives
    # is decided by a value the solver picked. Measured on one address
    # coordinate, the same region came out [256, 2^160-1] or [0, 254] depending
    # only on the sibling's witness -- a factor of 5.7e45 -- while a punch gives
    # [0, 2^160-1] \ {v} either way.
    #
    # Both DEFAULT to the driver's own defaults, so an unflagged sweep is
    # byte-identical to every sweep already recorded. Both are written onto
    # every row, same house rule as --env-coord: an arm whose configuration is
    # not in its records is an arm whose numbers cannot be re-derived.
    #
    # ⚠ Use --out to give the punch arm its OWN file. Writing it into
    # results.jsonl would put two arms under one (benchmark, unit) key, which is
    # the one-fact-two-ledgers failure this file already refuses for --redo.
    ap.add_argument("--max-holes", type=int, default=0,
                    help="passed to the driver: per coordinate, how many values "
                         "the region may PUNCH OUT before falling back to a side "
                         "cut. 0 (the driver's default, and every recorded "
                         "sweep's value) means NO region can carry a hole, so a "
                         "hole count taken from such a sweep measures this flag "
                         "and not the corpus. Needs --max-region-pieces > 1 to "
                         "have any effect.")
    ap.add_argument("--max-region-pieces", type=int, default=1,
                    help="passed to the driver: how many boxes one path's region "
                         "may be split into. 1 (the driver's default) throws the "
                         "non-counterexample side of every cut away, and is also "
                         "the setting under which --max-holes cannot fire.")
    ap.add_argument("--unit", action="append", default=[],
                    help="sweep only these unit names (repeatable). Without it "
                         "the whole benchmark is swept, which for a re-measure of "
                         "ONE unit means paying for every other unit first and "
                         "risking the budget before reaching it.")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--workdir", default="/tmp/certify_all")
    args = ap.parse_args()

    # A --unit that matches nothing must FAIL, not sweep everything. R8: iterate
    # the EXPECTED names, not the found ones; a typo that silently widens the
    # sweep back to the whole benchmark is the "missing input silently rewrites
    # the scope" shape -- an empty filter reads as "no restriction" when it means
    # "the restriction was lost".
    want_units = set(args.unit)

    names = args.benchmarks or [b for b in BENCHMARKS if b != "st1inch_St1inch"]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    os.makedirs(args.workdir, exist_ok=True)

    # THE MEMORY BOUND IS COMPUTED AND PRINTED BEFORE ANY RUN, and a failure to
    # fit is a refusal. Printed even at --jobs 1, so the number a sweep ran
    # under is in its own log rather than in whoever's memory launched it.
    memlimit, refusal = job_memlimit_gib(args.jobs)
    if refusal:
        print(f"[sweep] REFUSING --jobs {args.jobs}: {refusal}")
        return 1
    avail = available_gib()
    print(f"[sweep] --jobs {args.jobs}, --memlimit {memlimit}g each"
          + (f" (MemAvailable {avail:.1f} GiB; "
             f"{args.jobs} x {memlimit} = {args.jobs * memlimit} GiB committed)"
             if avail is not None else "")
          + ". Every esbmc run carries the limit, which is what makes running "
            "more than one of them an arithmetic question rather than a guess.",
          flush=True)
    write_lock = threading.Lock()

    ident = binary_identity()

    # --redo MUST TRUNCATE, NOT APPEND. It only ever cleared the skip set, and
    # records are appended -- so a --redo left the OLD record for every unit in
    # the file next to the new one, two rows with one (benchmark, unit) key and
    # different numbers. Which one a reader gets is then a property of its
    # parsing order, and this project has already been bitten by one fact kept
    # in two places. The previous file is moved aside rather than deleted: it
    # is a measurement, just one of a build that no longer exists.
    if args.redo and os.path.exists(args.out):
        keep = args.out + ".superseded"
        os.replace(args.out, keep)
        print(f"[sweep] --redo: moved the previous results to {keep} rather "
              f"than appending beside them; two records with one "
              f"(benchmark, unit) key would be read by parsing order")

    # RESUMING ACROSS A DIFFERENT BINARY IS THE TRAP THIS FEATURE SETS, and it
    # is the same one pathcov_collect.py already refuses. Resuming means "skip
    # this unit and keep its record", which is right for an interrupted sweep
    # and wrong after a fix: every unit prints `already recorded`, the file is
    # rewritten to look current, and the analysis quotes the OLD build's
    # numbers under the NEW build's name. Nothing says so.
    #
    # MEASURED, not hypothetical: run immediately after a frontend fix, this
    # sweep reported all 65 units already recorded and exited clean.
    #
    # Refused rather than auto-cleared -- deleting someone's sweep because a
    # timestamp moved is its own way to lose data. Records written before this
    # check existed carry no `binary` field at all and are treated as foreign,
    # which is the safe reading: they came from a build nobody recorded.
    done = set()
    if os.path.exists(args.out) and not args.redo:
        stale = []
        with open(args.out) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                done.add((r.get("benchmark"), r.get("unit")))
                if r.get("binary") != ident:
                    stale.append((r.get("benchmark"), r.get("unit"),
                                  r.get("binary")))
        if stale:
            shown = stale[:5]
            # SAY WHICH FIELD MOVED -- see the same fix in pathcov_collect.py.
            # "a DIFFERENT binary" is FALSE when only `head`/`srcDirty` moved,
            # which is what a mid-collection commit produces: measured on the
            # stage-1 corpus, EscrowDst and st1inch each carry three identities
            # with an IDENTICAL binaryMtime. Refused either way; named correctly
            # so the operator can tell "the build changed" from "I committed".
            mt_now = (ident or {}).get("binaryMtime")
            mt_moved = sum(1 for _b, _u, was in stale
                           if (was or {}).get("binaryMtime") != mt_now)
            what = (f"{mt_moved} of them by a genuinely DIFFERENT BINARY "
                    f"(binaryMtime differs)" if mt_moved else
                    "the BINARY IS THE SAME FILE in all of them (binaryMtime "
                    "identical) -- only head/srcDirty moved, i.e. the repo was "
                    "committed to mid-sweep")
            print(f"[sweep] REFUSING to resume: {len(stale)} of {len(done)} "
                  f"record(s) in {args.out} do not match the identity on disk "
                  f"now, and {what}.")
            print(f"  now:  {ident}")
            for b, u, was in shown:
                print(f"  was:  {b}/{u} -> {was}")
            if len(stale) > len(shown):
                print(f"  ... and {len(stale) - len(shown)} more")
            print("Resuming would skip those units and keep their records, so "
                  "the table would quote the old build's numbers under the new "
                  "build's name. Re-run with --redo to re-measure, or move the "
                  "file aside to keep it.")
            return 1
        if done:
            print(f"[sweep] {len(done)} unit(s) already recorded in "
                  f"{args.out}; they are SKIPPED. Pass --redo to re-run them")

    for bench in names:
        if bench not in BENCHMARKS:
            print(f"[sweep] unknown benchmark '{bench}'; known: "
                  + ", ".join(sorted(BENCHMARKS)))
            return 1
        sol, contract = BENCHMARKS[bench]
        wd = os.path.join(args.workdir, bench)
        os.makedirs(wd, exist_ok=True)
        print(f"\n########## {bench} ({contract}) ##########", flush=True)
        got, why = units_of(bench)
        if got is None:
            # A benchmark whose unit list could not be built is recorded as
            # exactly that, with the reason, rather than as zero units. "0 units"
            # and "we could not find out" are the two readings this project has
            # already confused once, on this very corpus.
            print(f"[sweep] {bench}: NO UNIT LIST — {why}")
            with open(args.out, "a") as f:
                f.write(json.dumps({"benchmark": bench, "unit": None,
                                    "bucket": "NO-UNIT-LIST",
                                    "reason": why}) + "\n")
            continue
        pairs, killed_in_roundtrip = got
        units = [m for _, m in pairs]
        if want_units:
            missing = sorted(want_units - set(units))
            if missing:
                # HARD FAIL on a name that is not there. Dropping it would sweep
                # the units that DID match and print a table that looks complete,
                # which is how a scope silently becomes a different scope.
                print(f"[sweep] {bench}: --unit named {missing}, which the "
                      f"round-trip's emit.jsonl does not list. Its units are: "
                      f"{', '.join(units)}. Refusing rather than sweeping the "
                      f"subset that happened to match")
                return 1
            units = [u for u in units if u in want_units]
            print(f"[sweep] {bench}: --unit restricts this sweep to "
                  f"{len(units)} of the benchmark's units")
        print(f"[sweep] {bench}: {len(units)} unit(s) from the round-trip's "
              f"emit.jsonl: {', '.join(units)}")
        if killed_in_roundtrip:
            # Named up front, because a unit the ENUMERATION could not finish is
            # very likely to be one this sweep cannot finish either -- and a
            # KILLED record here would otherwise read as a stage-2 result when
            # it is inherited from stage 1.
            print(f"[sweep] {bench}: {len(killed_in_roundtrip)} of these were "
                  f"KILLED in the round-trip's own enumeration and are "
                  f"expected to be killed here too: "
                  f"{', '.join(killed_in_roundtrip)}")

        todo = [(i, u) for i, u in enumerate(units, 1)
                if (bench, u) not in done]
        for i, unit in enumerate(units, 1):
            if (bench, unit) in done:
                print(f"  [{i}/{len(units)}] {unit} — already recorded",
                      flush=True)

        def run_unit(item):
            i, unit = item
            uwd = os.path.join(wd, unit)
            os.makedirs(uwd, exist_ok=True)
            # `-u`: UNBUFFERED. The driver's stdout is a PIPE, so Python block-
            # buffers it and a KILLED run loses whatever is still in the buffer.
            # This sweep's expected ending IS a kill, so that is the common case
            # rather than the exotic one, and the loss lands precisely on the
            # runs that died EARLY -- the ones whose first rounds are the only
            # thing that would have said why. MEASURED on this corpus: five of
            # the six KILLED units kept their logs only because their output had
            # already overflowed the buffer (aqua.ship 15418 lines, farming.
            # rescueFunds 1192, ...), while EscrowDst/cancel came back with two
            # lines and no evidence. Same fix in certify_poc.py, applied in the
            # same change: one of these two learning it and the other not is how
            # a fixed defect comes back under a different sweep's name.
            cmd = [sys.executable, "-u", DRIVER,
                   "--esbmc", ESBMC,
                   "--sol", os.path.join(INPUTS, sol),
                   "--ast", os.path.join(INPUTS, sol + ".solast"),
                   "--contract", contract, "--unit", unit, "--focus",
                   "--probes", str(args.probes),
                   "--refine-rounds", str(args.refine_rounds),
                   "--shrink-rounds", str(args.shrink_rounds),
                   # ---- THIS 180 IS THE PER-ESBMC-RUN BUDGET, AND IT IS THE
                   # ---- ONE THAT GOVERNS THE LARGEST FAILURE BUCKET ----
                   #
                   # `--timeout` on THIS sweep is the whole-driver budget (600s,
                   # and its help text argues for that value). The driver's own
                   # `--timeout` is per ESBMC INVOCATION, and it is what makes
                   # `round_failure_reason` emit "no outer-box round finished, so
                   # nothing was measured for this path (a BUDGET outcome)" --
                   # 43 of the 91 non-certification reasons on the partial sweep,
                   # the single largest bucket in `certify_summary.py`'s table.
                   #
                   # The `min(..., 180)` cap is UNARGUED. It is also invisible:
                   # the record below stores `unit_timeout_s` = the 600, so an
                   # artefact reader can see the budget that did NOT produce that
                   # bucket and cannot see the one that did. Recorded as its own
                   # field now -- deliberately NOT folded into `unit_timeout_s`,
                   # which is a different quantity and is quoted as such.
                   "--timeout", str(min(args.timeout, 180)),
                   "--memlimit", f"{memlimit}g", "--workdir", uwd,
                   # ALWAYS PASSED, not passed-only-when-non-default. A flag
                   # that appears on the command line only sometimes is a
                   # command line that cannot be read back off the record: the
                   # row would say max_holes=0 for both "we asked for 0" and
                   # "we never asked", which is the distinction this arm exists
                   # to make. The defaults equal the driver's, so an unflagged
                   # sweep is byte-identical in behaviour to every recorded one.
                   "--max-holes", str(args.max_holes),
                   "--max-region-pieces", str(args.max_region_pieces)]
            if args.level0:
                cmd.append("--level0")
            if args.skip_bracket:
                cmd.append("--skip-bracket")
            if args.env_coord:
                cmd += ["--env-coord", args.env_coord]
            t1 = time.time()
            # ---- KILL THE PROCESS GROUP, NOT THE CHILD ----
            #
            # `subprocess.run(timeout=)` SIGKILLs the DIRECT child only -- here
            # the driver `python3` -- and then blocks in communicate(). The
            # driver's own esbmc grandchild is ORPHANED, and it inherits the
            # stdout/stderr pipes, so communicate() waits for IT to exit: the
            # timeout does not fire on time and the worker slot is held.
            #
            # That breaks the arithmetic this whole flag rests on. `--jobs N`
            # commits `N * memlimit`, which assumes live-esbmc-count == N. After
            # one timeout it is N + orphans, and orphans are entitled to their
            # full memlimit with no parent to reap them. Four units timing out
            # together while four more start is 8 x 6 = 48 GiB on a 42 GiB
            # machine -- i.e. exactly the exhaustion the "never run esbmc
            # concurrently" rule was written after, reachable through the code
            # path added to discharge it.
            #
            # `start_new_session=True` puts the driver and every descendant in
            # their own process group; killpg then takes the whole tree. The
            # `finally` reaps it on ANY exit path, including the
            # KeyboardInterrupt that leaves the pool -- without it, Ctrl-C
            # leaves N drivers and N esbmc processes running unattended.
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    start_new_session=True)
            try:
                out, _ = proc.communicate(timeout=args.timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                _killpg(proc)
                try:
                    out, _ = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    out = ""
                out = (out or "") + f"\n[run] TIMEOUT after {args.timeout}s\n"
                rc = 124
            except BaseException:
                # Includes KeyboardInterrupt. Reap before propagating, or the
                # tree survives the sweep that started it.
                _killpg(proc)
                raise
            finally:
                _killpg(proc)
            wall = time.time() - t1
            rec = parse_driver(out)
            rec.update({"benchmark": bench, "unit": unit,
                        "bucket": bucket(rec, rc, out),
                        "wall_s": round(wall, 1), "exit": rc,
                        "memlimit_gib": memlimit, "jobs": args.jobs,
                        # THE CONFIGURATION TRAVELS WITH THE RECORD. Two units
                        # measured under different ladders are not comparable,
                        # and this project has already paid once for a ratio
                        # whose numerator and denominator came from runs that
                        # shared only a benchmark name.
                        "skip_bracket": bool(args.skip_bracket),
                        "level0": bool(args.level0),
                        # NOT omitted when unset. `None` here means "this arm
                        # ran with every environment quantity pinned or dropped",
                        # which is a DIFFERENT measurement from msg.sender being
                        # free -- and an absent key would read as "no arm
                        # information", i.e. as the thing certify_arms.py prints
                        # as MIXED. A recorded null is a fact; a missing field is
                        # an unknown.
                        "env_coord": args.env_coord,
                        # THE PUNCH ARM'S CONFIGURATION, on every row. A hole
                        # count read off rows that do not carry these two is a
                        # count whose denominator is unknown: `max_holes: 0`
                        # means no region COULD carry a hole, and a reader who
                        # cannot see that reads the 0 as a property of the
                        # contracts. Recorded as values rather than omitted when
                        # default, for the same reason `env_coord: null` is.
                        "max_holes": args.max_holes,
                        "max_region_pieces": args.max_region_pieces,
                        "probes": args.probes,
                        "refine_rounds": args.refine_rounds,
                        "shrink_rounds": args.shrink_rounds,
                        "unit_timeout_s": args.timeout,
                        # The per-ESBMC-RUN budget, which is NOT `unit_timeout_s`
                        # and is what "no outer-box round finished" counts. See
                        # the comment at the `--timeout` argument above: without
                        # this field the largest failure bucket in the summary
                        # has no budget recorded anywhere in the artefact.
                        # Records written before this field existed carry no
                        # value for it, and the summary says so rather than
                        # substituting `unit_timeout_s`.
                        "run_timeout_s": min(args.timeout, 180),
                        # Which binary produced this record. Read on resume; a
                        # file whose records came from another build is refused
                        # rather than continued.
                        "binary": ident})
            with open(os.path.join(uwd, "driver.log"), "w") as f:
                f.write(out)
            # ONE WRITER AT A TIME. Two processes appending to the same JSONL
            # can interleave a partial line, and a half-written record is worse
            # than a missing one -- it survives the resume check and is parsed
            # as data. FLUSHED for the same reason as before: this sweep's
            # expected ending is a kill, so an unflushed progress line is a
            # progress line that does not exist.
            with write_lock:
                with open(args.out, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                print(f"  [{i}/{len(units)}] {unit}: {rec['bucket']}, "
                      f"{len(rec['certified'])} certified / "
                      f"{len(rec['not_certified'])} not, "
                      f"{len(rec['coords'])} free coordinate(s), "
                      f"msg.value pin {rec['msg_value_pin']}, {wall:.0f}s",
                      flush=True)
            return rec

        def guarded(item):
            # ONE UNIT'S FAILURE MAY NOT END THE CORPUS. `pool.map` re-raises
            # the first worker exception out of main(), which would abort every
            # remaining benchmark with a traceback and no record -- the exact
            # opposite of the incremental design this file's docstring claims.
            # With N workers the exposure is N times larger, so it is caught
            # here and written as a record.
            try:
                return run_unit(item)
            except Exception as e:                       # noqa: BLE001
                i, unit = item
                rec = {"benchmark": bench, "unit": unit,
                       "bucket": "SWEEP-ERROR", "reason": f"{type(e).__name__}: {e}",
                       "jobs": args.jobs}
                with write_lock:
                    with open(args.out, "a") as f:
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                    print(f"  [{i}/{len(units)}] {unit}: SWEEP-ERROR — "
                          f"{type(e).__name__}: {e}", flush=True)
                return rec

        if args.jobs <= 1:
            for item in todo:
                guarded(item)
        else:
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=args.jobs) as pool:
                # Threads, not processes: every worker's real work is a
                # subprocess, so the GIL is released for all of it.
                #
                # THE JOIN IS LOAD-BEARING, not incidental. `run_unit` closes
                # over `bench`, `sol`, `contract`, `wd` and `memlimit`, which
                # are single cells in main()'s frame and are REBOUND by the next
                # iteration of the benchmark loop. Hoisting this pool out of the
                # loop -- the obvious next optimisation, since each benchmark
                # boundary is a barrier where N-1 workers idle -- would silently
                # produce records labelled with one benchmark and built from
                # another's source. If you hoist it, pass those values through
                # the item tuple first.
                list(pool.map(guarded, todo))

    print(f"\n[sweep] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
