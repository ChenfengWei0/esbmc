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
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ESBMC_ROOT, "scripts"))
from solidity_ast_dependencies import path_function_artifact_suffix  # noqa: E402
from veriput_path_guard import ensure_path_not_protected  # noqa: E402
from veriput_recipe import STRONG_CERTIFY_ARGS, STRONG_RECIPE_VERSION  # noqa: E402
from veriput_subjects import (SubjectError, ensure_solast,
                              enumerate_subject_units, resolve_subject)
# ---- THE INPUTS DIRECTORY IS THE POC'S, NOT A SHARED CORPUS ----
#
# `notes/coverage/inputs/` has been DELETED. It was the benchmark: this file
# resolves `INPUTS / BENCHMARKS[key][0]`, so while that directory existed a
# whole-corpus sweep was one command away regardless of the refusals above.
# Each PoC owns hardlinks to the files its unit needs, and `poc_one.py` points
# this at them. Overridden at the DEFINITION so every consumer gets the PoC's
# copy -- the same inode the corpus row was measured on -- by construction.
# Unset, this still names the deleted directory, so a benchmark-shaped
# invocation fails on a missing file instead of sweeping. Restore with
# `git checkout -- notes/coverage/inputs/` if a baseline re-measurement is ever
# needed; nothing was lost, it is all tracked.
INPUTS = os.environ.get(
    "VERIPUT_INPUTS_DIR",
    os.path.join(ESBMC_ROOT, "notes", "coverage", "inputs"))
DRIVER = os.path.join(ESBMC_ROOT, "scripts", "solidity_path_generalise.py")
ESBMC = os.path.join(ESBMC_ROOT, "build", "src", "esbmc", "esbmc")

STRONG_CERTIFY_VALUE_OPTIONS = {
    "--recipe-version": ("recipe_version", str),
    "--jobs": ("jobs", int),
    "--probes": ("probes", int),
    "--refine-rounds": ("refine_rounds", int),
    "--shrink-rounds": ("shrink_rounds", int),
    "--safety-retreat-after-tiny-cuts":
        ("safety_retreat_after_tiny_cuts", int),
    "--claim-budget": ("claim_budget", int),
    "--probe-witnesses": ("probe_witnesses", int),
    "--probe-ladder-budget": ("probe_ladder_budget", int),
    "--max-holes": ("max_holes", int),
    "--max-region-pieces": ("max_region_pieces", int),
    "--cut-policy": ("cut_policy", str),
    "--slot-coords": ("slot_coords", int),
}

STRONG_CERTIFY_BOOL_OPTIONS = {
    "--level0": "level0",
    "--level0-perturb": "level0_perturb",
    "--probe-ladder": "probe_ladder",
    "--skip-bracket": "skip_bracket",
    "--env-coord-disagreed": "env_coord_disagreed",
    "--pin-agreed-establishable-env": "pin_agreed_establishable_env",
    "--pin-agreed-state": "pin_agreed_state",
    "--state-struct-fields": "state_struct_fields",
    "--static-uncontrolled-inseparable": "static_uncontrolled_inseparable",
}


def apply_strong_certify_recipe(args):
    """Apply the shared benchmark certification recipe to parsed arguments."""
    if not getattr(args, "strong_recipe", False):
        return getattr(args, "recipe_version", "unversioned")
    idx = 0
    while idx < len(STRONG_CERTIFY_ARGS):
        opt = STRONG_CERTIFY_ARGS[idx]
        if opt.startswith("--esbmc-arg="):
            value = opt.split("=", 1)[1]
            if value not in args.esbmc_arg:
                args.esbmc_arg.append(value)
            idx += 1
            continue
        if opt in STRONG_CERTIFY_BOOL_OPTIONS:
            setattr(args, STRONG_CERTIFY_BOOL_OPTIONS[opt], True)
            idx += 1
            continue
        if opt in STRONG_CERTIFY_VALUE_OPTIONS:
            if idx + 1 >= len(STRONG_CERTIFY_ARGS):
                raise ValueError(f"strong recipe option {opt} has no value")
            attr, coerce = STRONG_CERTIFY_VALUE_OPTIONS[opt]
            setattr(args, attr, coerce(STRONG_CERTIFY_ARGS[idx + 1]))
            idx += 2
            continue
        raise ValueError(f"unsupported strong certify recipe option: {opt}")
    args.recipe_version = STRONG_RECIPE_VERSION
    return args.recipe_version

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
# ---- TWO SPELLINGS, AND THE OLD ONE WAS BROKEN BY A COORDINATE'S OWN NAME ----
#
# `[^\[]+?` cannot cross a `[`, and a mapping-slot coordinate IS
# `state._balances[msg.sender]`. So the moment --slot-coord was used the real
# coordinate line stopped matching at all, `pins` came back null, and the LAST
# `[coords]` line that happened to match -- a prose sentence about a mapping
# shape -- was recorded as the coordinate list. MEASURED,
# results_slotcoord_deposit.jsonl row 1:
#
#   "coords": ["mapping(s) whose SHAPE has no slot coordinate: _allowances
#              (value is mapping(address => uint256); ...)"], "pins": null
#
# The driver now marks the real line `FREE:`, so it is identified by what it IS
# rather than by a whitelist of prose prefixes to exclude -- a whitelist that is
# open at the bottom and that this is the second entry to fall through. The old
# spelling is still read, because a reader that stops recognising a message it
# used to handle is the same defect pointing the other way; it is tried only
# when no FREE line was seen.
RE_COORDS_FREE = re.compile(
    r"^\[coords\] FREE: (.+?)(?:   \[pinned: (.*)\])?$")
# The legacy form is anchored on the driver's own three-space `   [pinned: `
# separator instead of on "no `[` anywhere", so a bracketed coordinate name
# parses on OLD logs too. ⚠ That makes the exclusion whitelist below fully
# load-bearing for the legacy path -- it is the only thing separating the
# coordinate line from prose -- which is why the marked form exists and why
# `coords_line` records which of the two produced a row.
RE_COORDS = re.compile(r"^\[coords\] (.+?)(?:   \[pinned: (.*)\])?$")
RE_NO_COORD = re.compile(r"^\[coords\] NO GENERALISABLE COORDINATE — (.*)$")
RE_CERT = re.compile(r"^  enc=(\d+)(?: piece \d+/\d+)?: (.*)$")
# ---- A PATH SPLIT INTO PIECES WAS RECORDED AS ZERO CERTIFIED ----
#
# `RE_CERT` above expects `enc=12 piece 3/4:`. The driver has never printed
# that. It prints
#
#     enc=12 piece 3 (1 of 2 certified): distributor_ in [0, 0], ...
#
# and then a UNION line, `enc=12: the region of this path is the UNION of the 2
# boxes above`, which the caller EXCLUDES by name because it is prose and not a
# region. So when --max-region-pieces > 1 actually splits a path, BOTH lines
# miss and the unit is recorded with `certified: {}`.
#
# MEASURED, farming/setDistributor, and it is the whole reason this was found:
# the driver's own last line says `5 certified region(s), 3 not certified, over
# 5 witnessed path(s)` and lists five certified pieces under === CERTIFIED
# REGIONS ===. certify_all's row for the same run says 0 certified. Verified by
# running THIS module's parse_driver over that exact driver.log, not by reading
# the regex: certified=[] against the driver's 5.
#
# That is the write-side/read-side split this project has paid for before -- the
# piece arm has been able to produce pieces since 3f0395e60c and nothing
# downstream could ever see one. A hole arm that reports 0 holes and a hole arm
# whose reader cannot parse a hole print the same 0.
#
# KEYED `<enc>#<piece>`, deliberately NOT collapsed into `<enc>`: the pieces of
# one path are DIFFERENT boxes, each certified by its own query, and a dict
# keyed on enc alone would keep whichever came last and silently drop the rest.
# Downstream readers that assume an integer key must FAIL on this, and put_all.py
# is changed in the same commit to refuse it by name rather than crash in
# int(enc) -- emitting one PUT per piece needs a naming dimension the emitter
# does not have yet, and inventing one here would put two tests with the same
# function name in one forge project.
RE_CERT_PIECE = re.compile(
    r"^  enc=(\d+) piece (\d+) \(\d+ of \d+ certified\): (.*)$")
RE_NOTCERT = re.compile(
    r"^  enc=(\d+): NOT CERTIFIED — (.*?)(?:; (?:this path falls|no concrete)|$)")
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

# ---- LEVEL 0 IS DECIDED IN SECONDS AND WAS BEING THROWN AWAY WHOLE ----
#
# MEASURED, farming/setDistributor, from the KILLED arm's own driver.log:
#
#     [round] level-0: 7.5s wall, 4 coordinate(s), ~5 candidate value(s) ...
#     [level0] enc=2  single-point on: state._distributor==0, state._owner==1, ...
#     [level0] enc=12 single-point on: distributor_==0, state._distributor==0, ...
#     [level0] enc=13 single-point on: state._distributor==0, state._totalSupply==0
#     [level0] enc=14 single-point on: distributor_==0, state._distributor==0, ...
#     [level0] enc=15 single-point on: state._distributor==0, state._totalSupply==0
#     [run] TIMEOUT after 240s
#
# Five paths had a decided level-0 projection SEVEN AND A HALF SECONDS IN. The
# run was then killed 232 seconds later in the geometric ladder, the driver
# never reached the write of `generalise-result.json`, and this file recorded
# `certified {} / not_certified {}` -- a bare ZERO for a unit whose level 0 had
# answered for every path it had.
#
# That is the single largest failure shape on the corpus: 53 of 137
# non-certified paths say `no outer-box round finished, so nothing was measured
# for this path (a BUDGET outcome)`.
#
# THIS IS NOT A SECOND LEDGER. This file's contract, stated in its own header,
# is that the funnel is "read off the driver's own lines rather than recomputed
# here, so the sweep cannot disagree with the tool about what was measured".
# The driver already prints these lines; nothing here derives, infers or
# re-solves anything. What changes is only that a line the driver emits at
# second 7 is no longer discarded because the process died at second 240.
#
# ⛔ IT DOES NOT PROMOTE THE OUTCOME. `bucket()` is untouched: a KILLED run
# stays KILLED, because level 0 is a PROJECTION and not a certified region --
# it has not been through the certification query. The row simply stops
# claiming that a killed run measured nothing when its own log says otherwise.
RE_LEVEL0_POINT = re.compile(
    r"^\[level0\] enc=(\d+) single-point on: (.+)$")
# The tool's OWN warning that a one-value candidate list cannot tell a genuine
# point domain from a path with NO inputs at all. It is the discriminator for
# the `region is EMPTY (lo > hi)` bucket -- 20 of the 137 -- and it names the
# repair (try a second value on those coordinates). Recorded per path, because
# a level-0 point carrying this flag is NOT usable as a region without that
# second probe, and a consumer that could not tell the two apart would read a
# vacuous antecedent as an established single point.
RE_LEVEL0_VACUITY = re.compile(
    r"^\[level0\] ⚠ enc=(\d+): the point\(s\) on (.+?) came from a ONE-VALUE "
    r"candidate list")
RE_LEVEL0_ROUND = re.compile(
    r"^\[round\] level-0: ([\d.]+)s wall, (\d+) coordinate\(s\)")


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


def job_memlimit_gib(jobs, reserve_frac=0.60, floor_gib=4, want_gib=8):
    """Per-job `--memlimit`, or a refusal string. NEVER a silent degradation.

    `want_gib` is the caller's request. It used to be the literal 8 below, with
    no way to ask for anything else at --jobs 1 -- so the one number every
    single-job run on this corpus was made under lived nowhere but in this
    function, which is the same "a limit is a line nobody read" shape the
    driver's own --memlimit help complains about.

    STILL CHECKED AGAINST THE MACHINE, at every jobs count. A request that does
    not fit is REFUSED rather than quietly reduced, for the reason below: a
    silently smaller limit turns a scheduling decision into a measurement
    change. That check used to be skipped entirely at --jobs 1, which was safe
    only because the 8 was hardcoded and small.

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
    avail = available_gib()
    if avail is None:
        return None, ("cannot read MemAvailable from /proc/meminfo, so the "
                      "memory budget cannot be computed. Refusing to guess")
    budget = avail * reserve_frac
    # THE REQUEST IS CHECKED, AND IT IS THE REQUEST THAT IS RETURNED. Dividing
    # the budget by `jobs` would silently hand a single job the whole 60% of
    # MemAvailable, i.e. a limit nobody asked for -- the mirror of the silent
    # shrink this function refuses in the other direction.
    if want_gib * jobs > budget:
        return None, (
            f"--memlimit {want_gib}g x --jobs {jobs} = {want_gib * jobs} GiB "
            f"does not fit: MemAvailable is {avail:.1f} GiB and the budget is "
            f"{reserve_frac:.0%} of that = {budget:.1f} GiB. Refusing rather "
            f"than shrinking the limit, because a silently smaller limit makes "
            f"units die of the limit instead of the problem -- a measurement "
            f"change dressed as a scheduling one. Use --memlimit "
            f"{max(floor_gib, int(budget // max(1, jobs)))}g or fewer --jobs")
    if want_gib < floor_gib:
        return None, (
            f"--memlimit {want_gib}g is below the {floor_gib} GiB floor, under "
            f"which a real benchmark unit starts dying of the limit rather "
            f"than of the problem")
    # THE REQUEST IS WHAT IS RETURNED, at every jobs count. The old code handed
    # back `budget // jobs`, which is the largest limit that FITS and not the
    # one anyone asked for -- so raising --jobs silently raised or lowered the
    # per-run memory bound as a side effect of a scheduling decision, and two
    # sweeps run at different --jobs were two measurements wearing one name.
    # The fit check above has already refused anything that does not fit, so
    # there is nothing left to negotiate here.
    return want_gib, None


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


def units_from_enumeration_index(index_path, bench, want_units):
    """Single-POC unit authority from the Stage-1 collection manifest.

    Corpus sweeps deliberately use forge_roundtrip/emit.jsonl as their unit
    list. A POC-unit run is already narrower: poc_one.py passes one --unit and
    one private Stage-1 collection via --enumeration-index. Requiring the
    corpus round-trip file there turns a valid POC into NO-UNIT-LIST and then
    Stage 3 reads an empty cert file as a measurement. This fallback is enabled
    only for that one-unit path and is fail-closed against a mismatched
    manifest.
    """
    if not index_path or not want_units:
        return None, "no POC enumeration index and unit restriction"
    if len(want_units) != 1:
        return None, "POC enumeration index fallback requires exactly one --unit"
    try:
        d = json.load(open(index_path))
    except (OSError, ValueError) as e:
        return None, f"cannot read {index_path}: {e}"
    if d.get("benchmark") != bench:
        return None, (
            f"{index_path} is for benchmark {d.get('benchmark')!r}, not {bench!r}")
    cfg = d.get("config") or {}
    only = cfg.get("onlyUnits") or []
    unit = next(iter(want_units))
    if only and unit not in only:
        return None, (
            f"{index_path} restricts Stage 1 to {only}, which does not include "
            f"requested unit {unit!r}")
    primary = (d.get("primary") or {}).get("name") or BENCHMARKS[bench][1]
    return ([(primary, unit)], []), (
        f"using POC Stage-1 enumeration index {index_path}; this run is already "
        f"restricted to --unit {unit}, so forge_roundtrip/emit.jsonl is not "
        f"required")


# A REFUSAL, IN THE DRIVER'S OWN WORDS. The shape is `[<tag>] REFUSING ...`,
# which is how the driver declines to start: a workdir holding another
# configuration's artefacts, an artefact cell whose writer set is empty. The tag
# is captured separately so the row says WHICH gate fired without this sweep
# holding a list of them.
RE_DRIVER_REFUSED = re.compile(r"^\[([A-Za-z0-9_-]+)\] REFUS(?:ING|ED)\b")


def parse_driver(out):
    """The driver's own report, as a record. Nothing is inferred."""
    rec = {"witnessed": None, "coords": [], "pins": None,
           # WHICH spelling the coordinate list was read from. `None` means no
           # coordinate line was recognised at all, which must not read as
           # "this unit has no coordinates" -- the state that produced a prose
           # sentence in the coords field once already.
           "coords_line": None,
           "no_coordinate_reason": None, "certified": {}, "not_certified": {},
           "msg_value_pin": "not seen",
           # None means the driver printed NEITHER verdict -- an older driver,
           # or a run that died before reaching the branch. Deliberately not
           # defaulted to either side: a missing field is an unknown, and this
           # sweep already makes that distinction for `env_coord`.
           "empty_witness_verdict": None, "empty_witness_reason": None,
           # LEVEL 0, kept even when the run later dies. Empty dict means the
           # driver printed no level-0 point line at all; that is different from
           # "level 0 decided nothing", and the round line below is what tells
           # the two apart -- if level 0 never RAN there is no round line either.
           "level0_points": {}, "level0_vacuity_risk": {},
           "level0_round_s": None, "level0_coords": None,
           # THE DRIVER DECLINED TO START, and this is the sentence it said it
           # with. None means no refusal line was printed; it is not defaulted
           # to a string, because "" would read as a refusal with no reason.
           "driver_refusal": None, "driver_refusal_tag": None}
    for line in out.splitlines():
        m = RE_LEVEL0_POINT.match(line)
        if m:
            rec["level0_points"][m.group(1)] = m.group(2)
            continue
        m = RE_LEVEL0_VACUITY.match(line)
        if m:
            rec["level0_vacuity_risk"][m.group(1)] = [
                c.strip() for c in m.group(2).split(",") if c.strip()]
            continue
        m = RE_LEVEL0_ROUND.match(line)
        if m:
            rec["level0_round_s"] = float(m.group(1))
            rec["level0_coords"] = int(m.group(2))
            continue
        m = RE_DRIVER_REFUSED.match(line)
        if m and rec["driver_refusal"] is None:
            # FIRST ONE WINS. A refusal is a stop, so a later line cannot be a
            # better explanation of it -- and letting one overwrite the other
            # would report the last gate rather than the one that fired.
            rec["driver_refusal"] = line.strip()
            rec["driver_refusal_tag"] = m.group(1)
            continue
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
        # THE MARKED LINE WINS AND IS UNAMBIGUOUS. Once one has been seen the
        # fallback is never consulted again on this log, so a later prose line
        # cannot overwrite the real list -- which is exactly how the slot-coord
        # row came to hold a sentence.
        m = RE_COORDS_FREE.match(line)
        if m:
            rec["coords"] = [c.strip() for c in m.group(1).split(",")
                             if c.strip()]
            rec["pins"] = m.group(2)
            rec["coords_line"] = "FREE"
            continue
        m = RE_COORDS.match(line)
        if m and rec.get("coords_line") != "FREE" and not line.startswith(
                ("[coords] DROPPED", "[coords] NOT ",
                 "[coords] no --ast", "[coords] every",
                 "[coords] UNSUPPORTED", "[coords] ACCOUNTING",
                 "[coords] --pin-agreed-state",
                 "[coords] ESBMC query pins OMIT",
                 # Added when the whitelist failed. Kept because logs written by
                 # a driver without the FREE marker still have to parse.
                 "[coords] MAPPING SLOT", "[coords] mapping(s)",
                 "[coords] mapping READ slot access priority",
                 "[coords] mapping dependency policy",
                 "[coords] STATE PINNED",
                 "[coords] STATE NOT PINNED",
                 "[coords] bytesN mapping key",
                 "[coords] slot candidate", "[coords] NO mapping slot",
                 "[coords] the outer-box rounds refused")):
            rec["coords"] = [c.strip() for c in m.group(1).split(",")
                             if c.strip()]
            rec["pins"] = m.group(2)
            rec["coords_line"] = "legacy"
        m = RE_NOTCERT.match(line)
        if m:
            rec["not_certified"][m.group(1)] = m.group(2)
            continue
        # BEFORE RE_CERT, because a piece line also matches the plain form's
        # `^  enc=(\d+)...: (.*)$` shape once the optional group fails -- it
        # would be stored under the bare enc and the next piece would overwrite
        # it, which is worse than missing it: one of N boxes reported as if it
        # were the path's whole region.
        m = RE_CERT_PIECE.match(line)
        if m:
            rec["certified"][f"{m.group(1)}#{m.group(2)}"] = m.group(3)
            continue
        m = RE_CERT.match(line)
        if m and "NOT CERTIFIED" not in line and "the region of this path" \
                not in line:
            rec["certified"][m.group(1)] = m.group(2)
    return rec


def result_path_function(workdir):
    """Read the exact unit identity written by the stage-2 driver."""
    try:
        with open(os.path.join(workdir, "generalise-result.json")) as stream:
            value = json.load(stream).get("path_function")
        return value if isinstance(value, str) and value else None
    except (OSError, ValueError):
        return None


def result_not_certified_details(workdir, since_mtime=None):
    """Machine-readable NOT_CERTIFIED rows written by the stage-2 driver.

    `parse_driver` intentionally records the driver's prose because old logs
    are still useful, but the prose loses important accounting fields such as
    `concrete_fallback`. Keep those fields when `generalise-result.json`
    exists so later stages can separate a concrete fallback from a method-level
    unsupported path without re-running ESBMC.
    """
    path = os.path.join(workdir, "generalise-result.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return {}
        with open(path) as stream:
            rows = json.load(stream).get("not_certified") or []
    except (OSError, ValueError):
        return {}
    details = {}
    for row in rows:
        if not isinstance(row, dict) or "enc" not in row:
            continue
        details[str(row["enc"])] = row
    return details


def merge_not_certified_details(rec):
    """Keep machine-readable NOT_CERTIFIED rows visible to Stage 4.

    The prose parser is intentionally conservative and can miss paths when the
    driver exits through a structured side channel such as NO-COORDINATE.  The
    JSON result is authoritative for per-enc fallback metadata, so the sweep row
    must expose those encs in `not_certified` as well as in
    `not_certified_details`; downstream Stage 4 accounting iterates the former
    and reads proof/fallback tags from the latter.
    """
    not_certified = rec.setdefault("not_certified", {})
    for enc, detail in (rec.get("not_certified_details") or {}).items():
        if str(enc) in not_certified:
            continue
        if not isinstance(detail, dict):
            continue
        reason = detail.get("reason") or detail.get("verdict")
        if not reason:
            reason = "machine-readable NOT_CERTIFIED detail"
        not_certified[str(enc)] = str(reason)
    return rec


def result_pins(workdir, since_mtime=None):
    """Machine-readable pins written by the stage-2 driver."""
    path = os.path.join(workdir, "generalise-result.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return None
        with open(path) as stream:
            pins = json.load(stream).get("pins")
    except (OSError, ValueError):
        return None
    return pins if isinstance(pins, dict) else None


def result_certified_details(workdir, since_mtime=None):
    """Machine-readable CERTIFIED rows written by the stage-2 driver."""
    path = os.path.join(workdir, "generalise-result.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return {}
        with open(path) as stream:
            rows = json.load(stream).get("certified") or []
    except (OSError, ValueError):
        return {}
    details = {}
    for row in rows:
        if not isinstance(row, dict) or "enc" not in row:
            continue
        key = str(row["enc"])
        piece = row.get("piece")
        if piece not in (None, "", 1, "1"):
            key += "#" + str(piece)
        details[key] = row
    return details


def result_enumeration_salvage(workdir, since_mtime=None):
    sidecar = os.path.join(workdir, "enumeration-salvage.json")
    path = os.path.join(workdir, "generalise-result.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return None
        with open(path) as stream:
            source = (json.load(stream).get("enumeration_source") or {})
    except (OSError, ValueError):
        try:
            if since_mtime is not None and os.stat(sidecar).st_mtime < since_mtime:
                return None
            with open(sidecar) as stream:
                salvage = json.load(stream)
        except (OSError, ValueError):
            return None
    else:
        salvage = source.get("salvage")
    return salvage if isinstance(salvage, dict) and salvage else None


def result_generalise_progress(workdir, since_mtime=None):
    path = os.path.join(workdir, "generalise-progress.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return None
        with open(path) as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def result_enumeration_report(workdir, imported_report=None, since_mtime=None):
    """The stable Stage-1 enumeration report path for Stage 4.

    `cov-report.json` is reused by later certification queries, so direct
    enumeration preserves the original report as `enumeration-report.json`.
    Imported reports are already stable and remain authoritative.
    """
    if imported_report:
        return imported_report
    path = os.path.join(workdir, "enumeration-report.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return None
    except OSError:
        return None
    return path


def result_empty_witness_obstacles(workdir, unit=None, since_mtime=None):
    path = os.path.join(workdir, "cov-report.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return None
        with open(path) as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    details = {}
    total = 0
    for claim in data.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        if unit and not str(claim.get("condition") or "").startswith(unit + ":"):
            continue
        if claim.get("u_reason") != "named-obstacle":
            continue
        total += 1
        detail = str(claim.get("u_reason_detail") or "").strip()
        if detail:
            details[detail] = details.get(detail, 0) + 1
    if not total:
        return None
    return {
        "named_obstacle": {
            "total": total,
            "details": details,
        }
    }


RE_PATH_COV_PROBE_COUNTS = re.compile(
    r"--path-cov-probe: unit '([^']+)' added ([0-9]+) "
    r"exit-latched claim\(s\) for ([0-9]+) branch arm\(s\) at ([0-9]+) "
    r"physical exit\(s\); complete-path denominator remains ([0-9]+)")
RE_PATH_COV_PROBE_GOAL_CAP = re.compile(
    r"--path-cov-probe: unit '([^']+)' needs ([0-9]+) probe claims "
    r"\(([0-9]+) branch arms x ([0-9]+) physical exits\), exceeding "
    r"--path-cov-max-goals ([0-9]+)")
RE_ESBMC_ERROR_LINE = re.compile(r"^ERROR: (.*)$", re.MULTILINE)
RE_RUN_EXIT = re.compile(r"^\[run\] EXIT (-?[0-9]+)$", re.MULTILINE)
RE_RECURSIVE_HELPER_PREFLIGHT = re.compile(
    r"target call closure reaches direct self-recursive "
    r"function/helper wrapper\(s\): (.*?)\. "
    r"This preflight starts no ESBMC process")


def result_driver_diagnostic(out):
    out = out or ""
    if ("INTERNAL DEFECT" in out
            and "instrumented path claim(s) reached the solver" in out
            and "The harness never entered any unit" in out):
        return {
            "tag": "path-coverage-no-claims-reached-solver",
            "reason": "path coverage instrumentation emitted claims, but none reached the solver",
        }
    m = RE_RECURSIVE_HELPER_PREFLIGHT.search(out)
    if m:
        helpers = [item.strip() for item in m.group(1).split(",")
                   if item.strip()]
        return {
            "tag": "recursive-helper-preflight-refused",
            "reason": (
                "path enumeration refused before ESBMC because the target call "
                "closure reaches direct self-recursive helper wrappers"),
            "helpers": helpers,
        }
    m = RE_PATH_COV_PROBE_GOAL_CAP.search(out)
    if m:
        return {
            "tag": "path-coverage-probe-goal-cap",
            "reason": (
                "path coverage probe universe exceeded --path-cov-max-goals "
                "before any cov-report.json could be emitted"),
            "unit_id": m.group(1),
            "probe_claims": int(m.group(2)),
            "branch_arms": int(m.group(3)),
            "physical_exits": int(m.group(4)),
            "path_cov_max_goals": int(m.group(5)),
        }
    if "ESBMC produced no cov-report.json" in out:
        err = RE_ESBMC_ERROR_LINE.search(out)
        exit_code = RE_RUN_EXIT.search(out)
        diagnostic = {
            "tag": "esbmc-no-cov-report",
            "reason": "ESBMC exited before producing cov-report.json",
        }
        if err:
            diagnostic["error"] = err.group(1).strip()
        if exit_code:
            diagnostic["exit"] = int(exit_code.group(1))
        return diagnostic
    m = RE_PATH_COV_PROBE_COUNTS.search(out)
    if m and "[run] TIMEOUT after" in out:
        return {
            "tag": "path-coverage-probe-claim-explosion",
            "reason": (
                "path coverage probe enumeration timed out after emitting a "
                "large exit-latched claim product"),
            "unit_id": m.group(1),
            "probe_claims": int(m.group(2)),
            "branch_arms": int(m.group(3)),
            "physical_exits": int(m.group(4)),
            "complete_path_denominator": int(m.group(5)),
        }
    return None


def result_partial_witness_journal(workdir, since_mtime=None, progress=None):
    """Summarise the refutation-only witness journal left by a partial run.

    `cov-ce-journal.json` is not a certificate and must not promote a killed
    unit into CERTIFIED.  It is still useful scheduler evidence: ESBMC may have
    already found concrete path witnesses before the complete `cov-report.json`
    was unavailable.  Keep only a compact summary in the sweep row; the full
    payload stays in the unit workdir.
    """
    path = os.path.join(workdir, "cov-ce-journal.json")
    try:
        if since_mtime is not None and os.stat(path).st_mtime < since_mtime:
            return None
        with open(path) as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    witnesses = data.get("witnesses")
    if not isinstance(witnesses, dict) or not witnesses:
        return None
    paths = []
    witness_count_total = 0
    for claim, row in sorted(witnesses.items()):
        if not isinstance(row, dict):
            continue
        path_id = row.get("path_id")
        if path_id is None:
            condition = str(row.get("condition", ""))
            m = re.search(r":path:([^:\\s]+)$", condition)
            path_id = m.group(1) if m else claim
        many = row.get("witnesses")
        if isinstance(many, list):
            witness_count = len(many)
        else:
            try:
                witness_count = int(row.get("witness_count", 1))
            except (TypeError, ValueError):
                witness_count = 1
        witness_count_total += witness_count
        paths.append({
            "claim": claim.strip(),
            "path_id": str(path_id),
            "path_depth": row.get("path_depth"),
            "path_function": row.get("path_function"),
            "witness_count": witness_count,
        })
    if not paths:
        return None
    stage = (progress or {}).get("stage") if isinstance(progress, dict) else None
    context = ("certification-query"
               if isinstance(stage, str) and stage.startswith("certify-query")
               else "path-enumeration-or-probe")
    return {
        "kind": data.get("kind"),
        "version": data.get("version"),
        "source_stage": stage,
        "source_context": context,
        "partial": bool(data.get("partial")),
        "complete": bool(data.get("complete")),
        "claims_decided": data.get("claims_decided"),
        "claims_total": data.get("claims_total"),
        "path_count": len(paths),
        "witness_count": witness_count_total,
        "paths": paths,
    }


def certification_key(owner, unit, row_path_function, requested_path_function):
    """Resume identity; explicit overloads are independent measurements."""
    return (owner, unit,
            row_path_function if requested_path_function else None)


def apply_subject_ast_cache(subject, cache_root):
    if subject is None or not cache_root:
        return subject
    base = os.path.abspath(os.path.expanduser(cache_root))
    ast_name = os.path.basename(subject.solast)
    cached = os.path.join(
        base, subject.benchmark, subject.benchmark_key, ast_name)
    return subject.with_solast_path(cached, source="cache")


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
    diag = rec.get("driver_diagnostic") or {}
    if (
        isinstance(diag, dict) and
        diag.get("tag") == "path-coverage-probe-goal-cap" and
        rec["witnessed"] is None and not rec["certified"] and
        not rec["no_coordinate_reason"]
    ):
        return "DRIVER-REFUSED"
    # A DECLINED RUN IS NOT AN EMPTY ONE. Guarded on having produced nothing
    # else, so a refusal printed alongside real work cannot hide it -- the same
    # ordering rule the KILLED branch above follows.
    if (rec.get("driver_refusal") and rec["witnessed"] is None
            and not rec["certified"] and not rec["no_coordinate_reason"]):
        return "DRIVER-REFUSED"
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
    ap.add_argument("--subject-dir", default="",
                    help="prepared benchmark subject directory containing "
                         "flat.sol and meta.json. Requires exactly one --unit "
                         "and bypasses the historical six-entry BENCHMARKS "
                         "table.")
    ap.add_argument("--subject-id", default="",
                    help="prepared subject id under --subject-root, or under "
                         "one of the known VeriPUT Results/*/subjects roots "
                         "when --subject-root is omitted.")
    ap.add_argument("--subject-root", default="",
                    help="directory containing prepared subject directories. "
                         "Used with --subject-id.")
    ap.add_argument("--subject-benchmark",
                    choices=("stress243", "peer182", "bugfix124"),
                    default="",
                    help="known prepared-subject population used to resolve "
                         "--subject-id and to label the output row.")
    ap.add_argument("--ast-cache-root", default="",
                    help="for --subject-*, read/write compact ASTs under this "
                         "cache root instead of the prepared subject directory")
    ap.add_argument("--list-subject-units", action="store_true",
                    help="for --subject-*, print named public/external units "
                         "from the target contract's compact AST and exit. "
                         "This starts no ESBMC process.")
    ap.add_argument("--out", default=os.path.join(ESBMC_ROOT, "notes",
                                                  "coverage", "certify",
                                                  "results.jsonl"))
    ap.add_argument("--recipe-version", default="unversioned",
                    help="identity of the caller's complete method recipe. "
                         "Recorded on every row; this label does not set any "
                         "option by itself, so the concrete fields below remain "
                         "the authority for reproduction.")
    ap.add_argument("--strong-recipe", action="store_true",
                    help="apply the shared VeriPUT strong certification recipe "
                         f"({STRONG_RECIPE_VERSION}). Unlike --recipe-version, "
                         "this sets the actual region, slot, environment, and "
                         "ESBMC arguments used by the benchmark runner.")
    ap.add_argument("--scope", default="focus",
                    help="driver dispatcher alphabet: focus, whole, or a "
                         "comma-separated function set. Default focus preserves "
                         "the historical gate-cell invocation.")
    ap.add_argument("--max-tx", type=int, default=1,
                    help="driver transaction-sequence length. This travels "
                         "with --scope; changing either selects a different "
                         "cell and is recorded on every row.")
    ap.add_argument("--ce-collection-only", action="store_true",
                    help="stop each driver after 60-second-style path "
                         "enumeration evidence collection. The resulting "
                         "witness artifact is not a certified region or test.")
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
    ap.add_argument("--safety-retreat-after-tiny-cuts", type=int, default=2,
                    help="passed to the driver: for RESULT: UNSAFE "
                         "certification refutations, pin a coordinate at x_pi "
                         "after this many consecutive one-value cuts on that "
                         "same coordinate, while preserving another wide "
                         "non-environment coordinate. 0 disables the fallback. "
                         "Recorded on every row because it changes the "
                         "certified region shape.")
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
    ap.add_argument("--claim-budget", type=int, default=0, metavar="N",
                    help="passed to the driver: cap only the candidate VALUES "
                         "that Python emits for a geometric-bracket round. "
                         "DEFAULT 0 = uncapped. This does NOT cap a refine "
                         "round: ESBMC lays those probes from lo/hi and "
                         "--probes is the available control. Recorded on every "
                         "row so a thinned bracket cannot be compared with an "
                         "uncapped one as if they were the same measurement.")
    ap.add_argument("--skip-bracket", action="store_true",
                    help="the geometric bracket is the binding cost on real "
                         "input -- 258 probes per coordinate per direction. "
                         "Measured on the S4 fixture: a run whose bracket hit "
                         "its budget and measured NOTHING still produced every "
                         "exact region from level 0 plus refinement. ONE shape, "
                         "so this is offered rather than made default.")
    ap.add_argument("--env-coord-disagreed", action="store_true",
                    help="let the DRIVER name the environment coordinate "
                         "instead of the caller. It promotes every environment "
                         "quantity the witnessed paths DISAGREE on -- the "
                         "partition it already computes and already prints as "
                         "'Left unconstrained, so a path guarded by one of "
                         "these cannot certify' -- and skips anything already "
                         "pinned, which is what keeps the non-payable "
                         "msg.value pin intact. Default OFF, same house rule "
                         "as every other coordinate-set flag here.")
    ap.add_argument("--pin-agreed-state", action="store_true",
                    help="let the DRIVER name the entry-state pin instead of "
                         "the caller. It pins every state coordinate all "
                         "witnessed paths' counterexamples agree on, which is "
                         "the mirror of --pin-env. MEASURED on "
                         "farming/setDistributor: with the sender promoted but "
                         "the owner left free, 0 of 5 certify in 347.5s and "
                         "every path blames the owner; pinning it certifies 4 "
                         "of 5 in 87s. Default OFF: every region measured "
                         "under it is a statement about that entry-state "
                         "slice.")
    ap.add_argument("--level0", action="store_true", default=True)
    # ---- THE SECOND VALUE, REACHABLE FROM A SWEEP INSTEAD OF BY HAND ----
    #
    # The driver has printed, per path, that its level-0 point came from a
    # ONE-VALUE candidate list and therefore cannot be told apart from a path
    # with no inputs at all -- and it has named the repair in the same sentence.
    # MEASURED, farming/setDistributor: FIVE of five witnessed paths carried
    # that warning. Corpus-wide the shape it predicts is already in the records:
    # 20 of 137 non-certified paths report `region is EMPTY (lo > hi) under the
    # current pins`, i.e. the inversion, found one stage later at full ladder
    # cost.
    #
    # Recorded on every row and DEFAULT OFF, same house rule as --env-coord and
    # --max-holes: it changes the candidate list of every unit, so it changes
    # what every region measured under it is a statement about, and an arm whose
    # configuration is not in its records is an arm nobody can re-derive.
    ap.add_argument("--level0-perturb", action="store_true",
                    help="passed to the driver: after the level-0 round, "
                         "re-probe each at-risk coordinate at its value's "
                         "NEIGHBOURS, clamped to the type range the round "
                         "itself published. Both directions holding on a "
                         "neighbour means the antecedent is unsatisfiable and "
                         "the path is excluded from the slice -- not that the "
                         "domain is that point. ⚠ Use --out to give this arm "
                         "its OWN file.")
    # ---- THE PER-PATH OUTWARD LADDER, REACHABLE FROM A SWEEP ----
    #
    # These two exist in the driver, with their own unit tests and a tool-side
    # regression, and the sweep had no way to pass them -- so every ladder this
    # file has ever recorded was anchored at zero. That is the same failure
    # --env-coord above was added to prevent, one flag later: an arm that can
    # only be hand-run is an arm nobody can re-derive.
    #
    # WHAT NAMED THEM AS THE REPAIR, measured on farming/deposit and both arms
    # on disk: four paths report `shrink round budget exhausted; the witness
    # differs on: amount`, and the reason text is BYTE-IDENTICAL between an arm
    # at shrink 3 / refine 2 and one at 6 / 4 -- only the refuting witness value
    # moved. So the round budget is refuted as the cause; what is missing is a
    # BOUNDARY, and locating one is the geometric bracket's job.
    #
    # Recorded on every row and DEFAULT OFF, same house rule as --level0-perturb
    # directly above: they change the ladder, hence what every region measured
    # under them is a statement about.
    ap.add_argument("--probe-witnesses", type=int, default=0, metavar="N",
                    help="passed to the driver: ask the ENUMERATION run for up "
                         "to N distinct inputs per path (--all-witnesses "
                         "--max-witnesses N) and use the extra ones as KNOWN "
                         "MEMBERS of that path's domain. Costs no extra run -- "
                         "the enumeration happens anyway. A coordinate that "
                         "takes more than one value across a path's witnesses "
                         "is PROVED not to be a point before any query, which "
                         "is the blindness --level0-perturb attacks from the "
                         "other side. DEFAULT 0, i.e. OFF. ⚠ Use --out to give "
                         "this arm its OWN file.")
    ap.add_argument("--probe-ladder", action="store_true",
                    help="passed to the driver: lay the geometric bracket's "
                         "ladder PER PATH, anchored at that path's own known "
                         "members and doubling OUTWARD, instead of one shared "
                         "ladder anchored at zero. REQUIRES --probe-witnesses "
                         "(and --level0, which publishes the type ranges the "
                         "outward rungs are clamped to). The driver's own "
                         "measured example: a domain `amt in [10, 20]` "
                         "separated at 21 brackets to (16, 32] from zero and "
                         "to (20, 21] from the known member 20 -- same queries, "
                         "better places. ⚠ Use --out to give this arm its OWN "
                         "file.")
    ap.add_argument("--probe-ladder-budget", type=int, default=0, metavar="N",
                    help="passed to the driver: keep only the N rungs NEAREST "
                         "the member bracket on each side of a per-path ladder. "
                         "DEFAULT 0 = uncapped. MEASURED on farming/deposit: "
                         "uncapped the per-path ladder laid 5264 rungs and the "
                         "solver batch did not return in 780s; at budget 4 the "
                         "same 24 pairs lay about 156. ⚠ It is a LOSS and the "
                         "driver prints it -- a boundary beyond the last kept "
                         "rung comes back as a span reaching the type limit.")
    # ---- THE ABI VALUE-GATE PATH, REACHABLE FROM A SWEEP ----
    #
    # The driver pins msg.value to 0 on a non-payable unit BY DEFAULT, and that
    # default is right: a non-payable function's ABI gate reverts every call
    # carrying value, so no input with msg.value != 0 reaches the body. What it
    # excludes is the GATE PATH ITSELF, whose whole domain is msg.value != 0 --
    # the driver reports that path's region as EMPTY and says so.
    #
    # MEASURED, and it is why this is worth a flag rather than a hand run: on
    # farming/setDistributor that excluded path is enc=2, and with the pin lifted
    # and msg.value promoted to a coordinate it certifies and emits
    # `FarmingPoolCovTest_FarmingPool_setDistributor_put2.t.sol` -- 2 fuzz
    # parameters, 12 post-state assertions plus the gate's own assertFalse, green
    # under forge at `runs: 256`. That is a DELIVERABLE the pinned arm cannot
    # produce, and until now it could only be made by running the driver by hand.
    #
    # ⚠ IT COSTS THE OTHER PATHS. Same contract, same command apart from the
    # environment: 0 of 5 paths certified with msg.value unconstrained, 4 of 5
    # with it pinned. So this is an ARM -- its own --out, recorded on every row --
    # and never a new default.
    ap.add_argument("--no-auto-pin-value", action="store_true",
                    help="passed to the driver: do NOT pin msg.value to 0 on a "
                         "unit the source declares non-payable. This is the arm "
                         "that recovers the ABI value-gate path, whose entire "
                         "domain is msg.value != 0 and which the pinned arm "
                         "reports as an EMPTY region. Pair it with `--env-coord "
                         "msg.value` to make the value a FREE coordinate rather "
                         "than merely unpinned. ⚠ It costs the other paths -- "
                         "measured 0 of 5 certified unpinned vs 4 of 5 pinned -- "
                         "so give this arm its OWN --out.")
    ap.add_argument("--memlimit-gib", type=int, default=8, metavar="N",
                    help="per-ESBMC-process memory limit, in GiB. DEFAULT 8, "
                         "the value that used to be hardcoded for --jobs 1 and "
                         "could not be asked for otherwise -- so every "
                         "single-job number on this corpus was made under a "
                         "limit that lived in one function and in no record's "
                         "flags.\n"
                         "CHECKED, NOT TRUSTED: N x --jobs must fit inside 60%% "
                         "of measured MemAvailable or the sweep REFUSES. It "
                         "never silently shrinks, because a unit that dies of "
                         "the limit instead of the problem is a measurement "
                         "change wearing a scheduling decision's clothes.\n"
                         "⚠ RAISING IT IS NOT A FIX FOR THE CURRENT FAILURES. "
                         "MEASURED on results_pieces_corpus.jsonl, every row "
                         "read: not one non-completion has a memory "
                         "signature -- every `exit` is 1 (FAILED) or 124 "
                         "(timeout), never -9/-6/134 -- and the round that was "
                         "traced in detail died on `[run] TIMEOUT after 180s` "
                         "with 77.1s of solving done and nothing hung. The "
                         "budget that binds is --run-timeout. This buys "
                         "headroom so memory stops being a candidate "
                         "explanation, not throughput.")
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
    # REPEATABLE, because the driver's own flag is and this one was not.
    #
    # MEASURED, today, and it is the same failure the paragraph above describes
    # happening a second time: the green PUT
    # `FarmingPoolCovTest_FarmingPool_setDistributor_put2.t.sol` fuzzes msg.sender
    # AND is entered through a low-level call carrying msg.value, so its region
    # needs BOTH promoted. Every one of the seven recorded arms for that unit was
    # walked; each lists enc=2 as NOT certified, so no arm on disk produced it --
    # it was hand-run. The sweep could not express it: the driver takes
    # `--env-coord` with action="append" and this file forwarded exactly one.
    ap.add_argument("--env-coord", action="append", default=[],
                    help="passed to the driver: promote an environment quantity "
                         "(e.g. msg.sender, msg.value, block.timestamp) to a FREE "
                         "coordinate instead of a pin. REPEATABLE -- the driver "
                         "accepts several and a region may need several, e.g. a "
                         "sender-guarded path entered through a value-carrying "
                         "call. ⚠ Ladder cost is multiplicative in the coordinate "
                         "count, which is why the driver's help says to name them "
                         "one at a time; naming two is a deliberate purchase. "
                         "Recorded on every row, because a region certified with "
                         "an environment coordinate free is a different statement "
                         "from one certified with it pinned, and the two must "
                         "never share a table. ⚠ Use --out to give this arm its "
                         "OWN file: writing it into results.jsonl would put two "
                         "arms under one (benchmark, unit) key.")
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
    # ---- THE DRIVER HAS THE REPAIR THE TOOL NAMES; THIS SWEEP COULD NOT REACH
    # ---- IT ----
    #
    # `solidity_path_generalise.py --esbmc-arg` exists precisely for the
    # UNDECIDED-TRUNCATED refusal, and its own help says so: "the tool's own
    # refusal names repairs this driver could not apply ... Stage 4
    # (solidity_path_put.py) closed this gap; stage 2 had not." It is closed at
    # the driver. It was NOT closed here, so no CORPUS unit could ever be given
    # the repair -- only a hand-run one, which is the measurement nobody can
    # repeat.
    #
    # MEASURED, and it is not a corner: in results_envsender_shrink8.jsonl,
    # farming/setDistributor certifies 2 paths and loses 3, and every one of the
    # 3 carries UNDECIDED-TRUNCATED naming loop 55 and loop 56 in `_str_assign`
    # plus the exact retry to run. ⛔ That is NOT "the region is vacuous" -- the
    # tool says so in the same sentence -- so those 3 are currently filed as
    # not-certified on a bound, not on the method.
    #
    # EMITTED IN THE `=` FORM, always. `["--esbmc-arg", "--unwindset"]` makes
    # argparse read the value as the next OPTION and fail; the driver's help
    # calls this out for exactly the value we need. Building the `=` form here
    # means a caller never has to know that.
    #
    # ⚠ Use --out to give this arm its OWN file, same house rule as --env-coord
    # and --max-holes: a region certified under a widened loop bound is a
    # different statement from one certified under the default, and the two must
    # never share a (benchmark, unit) key.
    # ---- THE PER-ESBMC-RUN BUDGET STOPS BEING A CONSTANT NOBODY CAN REACH ----
    #
    # It was `min(args.timeout, 180)`, and the comment at the driver's --timeout
    # below has said for a while that the 180 is UNARGUED and INVISIBLE: the row
    # records `unit_timeout_s` (the 600/1500), so a reader sees the budget that
    # did NOT produce the largest failure bucket and cannot reach the one that
    # did.
    #
    # MEASURED, and it is why this is now a flag: farming/setDistributor run with
    # the tool's own named repair (--unwindset 55:512,56:512) DECIDED the three
    # UNDECIDED-TRUNCATED pieces -- they came back VACUOUS, a real verdict -- and
    # in the same run the two paths that used to CERTIFY (enc 12, 13) produced
    # NO RECORD AT ALL: absent from `certified` and from `not_certified` alike,
    # with wall time DOWN from 773s to 684s against a 1500s unit budget. Widening
    # a loop bound makes every query dearer, so that is the shape of a per-run
    # budget being hit, not of a method failing -- and with the 180 hardcoded the
    # two could not be told apart by any run.
    #
    # DEFAULT 180, so an unflagged sweep is byte-identical to every recorded one.
    ap.add_argument("--run-timeout", type=int, default=180,
                    help="per ESBMC INVOCATION, in seconds. NOT --timeout, "
                         "which is the whole driver loop for one unit. This is "
                         "the budget behind `no outer-box round finished, so "
                         "nothing was measured for this path (a BUDGET "
                         "outcome)`, the largest bucket in the summary. Default "
                         "180, the value that used to be hardcoded. The "
                         "effective value is min(--timeout, this) and is "
                         "recorded on every row as run_timeout_s.")
    ap.add_argument("--esbmc-arg", action="append", default=[],
                    dest="esbmc_arg", metavar="ARG",
                    help="pass one extra argument straight to EVERY ESBMC "
                         "invocation the driver makes -- enumeration, every "
                         "outer-box round AND the certification query. "
                         "Repeatable, one token each, and ⚠ USE THE `=` FORM "
                         "for any token starting with a dash: "
                         "--esbmc-arg=--unwindset --esbmc-arg=55:512,56:512. "
                         "Without the `=`, argparse reads the value as the "
                         "next OPTION -- the same trap the driver's own help "
                         "warns about, one level up, and the token this flag "
                         "exists to pass is exactly one that starts with a "
                         "dash. Recorded on every row, because a bound that differs "
                         "between two rows makes them two measurements wearing "
                         "one name.")
    # ---- THE SLOT THE GUARD READS IS NOT A PAYLOAD NAME, EVER ----
    #
    # This sweep derives nothing; the driver derives `coords` from the
    # counterexample payload, and a payload is a list of VALUES -- so it can
    # only ever offer a mapping slot at a key some counterexample happened to
    # pick. The slot a real guard reads follows a PARAMETER
    # (`_balances[msg.sender]`, `allowance[owner][spender]`) and is a function
    # of an input, so it cannot appear there at all. The driver says exactly
    # this in `mapping_state_vars`, and offers --slot-coords to propose them
    # from solc's own declarations instead.
    #
    # NOT PASSING IT WAS COSTING THE LARGEST REAL-REFUTATION BUCKET. MEASURED,
    # farming/deposit: its guard is `_mint` then
    # `balanceOf(msg.sender) > _MAX_BALANCE`, i.e. a relation between `amount`
    # and the balance slot -- and the recorded coords for that unit are
    # `amount, msg.sender, state._distributor, state._owner,
    # state._totalSupply`, with NO `_balances`. The box therefore leaves the
    # balance unconstrained, so for any interval on `amount` a witness exists
    # at some other balance, and the shrink loop cuts on `amount` forever while
    # the free variable is somewhere else. Its four shrink witnesses on
    # `amount` are 1.12e77, 3, 5.79e76, 8.68e76 -- full-range scatter, not the
    # geometric convergence a bisection on a real interval boundary produces.
    #
    # ⚠ COSTS QUERIES: ladder size is MULTIPLICATIVE in the coordinate count,
    # and the corpus is already losing 48 paths to the per-invocation timeout.
    # A budget rather than a switch for that reason, DEFAULT 0 so an unflagged
    # sweep is byte-identical to every recorded one, and its own --out.
    ap.add_argument("--slot-coords", type=int, default=0, metavar="N",
                    help="propose up to N mapping slots as free coordinates "
                         "(driver --slot-coords). Read from solc's "
                         "declarations: one `state.<m>[<k>]` per parameter of "
                         "the unit whose type matches the mapping's key type, "
                         "plus msg.sender on an address key. DEFAULT 0 = OFF, "
                         "which is what every recorded sweep ran with.")
    ap.add_argument("--slot-coord", action="append", default=[], metavar="EXPR",
                    help="name ONE slot coordinate explicitly, e.g. "
                         "state._balances[msg.sender] (driver --slot-coord). "
                         "Repeatable, and honoured independently of the "
                         "--slot-coords budget.")
    ap.add_argument("--state-struct-fields", action="store_true",
                    help="passed to the driver: decompose struct-valued entry "
                         "state into scalar leaves present in the report. "
                         "Recorded on every row because it changes the "
                         "coordinate set and therefore the certified slice.")
    ap.add_argument("--enumeration-index", default=None,
                    help="versioned stage-1 index.json for this one-unit run. "
                         "Paired with --enumeration-report and forwarded to "
                         "the driver for fail-closed compatibility checks.")
    ap.add_argument("--enumeration-report", default=None,
                    help="stage-1 unit report to reuse instead of paying for a "
                         "second path-enumeration ESBMC process.")
    ap.add_argument("--pin-extcall", action="store_true",
                    help="passed to the driver: fix every quantity the HARNESS "
                         "chose inside the execution -- an external call's "
                         "success bit is the common one -- at each path's OWN "
                         "counterexample value. ⚠ IT IS AN ARM AND NEEDS ITS "
                         "OWN --out. Such a quantity is not a call argument, so "
                         "a region certified under it holds only of the "
                         "executions in which the callee behaved that way, and "
                         "a test rendering it must realise the value some other "
                         "way.\n"
                         "MEASURED on farming/deposit, THREE ARMS, and the "
                         "result is an applicability limit rather than a knob:\n"
                         "  (1) bit FREE, bracket on -- KILLED at 420s, 0 of 7 "
                         "paths reached a verdict (a budget outcome).\n"
                         "  (2) bit PINNED, --skip-bracket -- all 7 reached a "
                         "verdict, 0 certified. `success` is gone from every "
                         "divergence line (it is pinned, so both sides agree by "
                         "construction) and the separator has MOVED: all six "
                         "name msg.sender, plus extcall.account which is "
                         "SafeERC20's own parameter bound to msg.sender and so "
                         "a mirror of it, not a second quantity.\n"
                         "  (3) bit PINNED + msg.sender promoted to a free "
                         "coordinate -- the refine rounds report "
                         "UNSEPARATED=[26,27,246,247,3622,3623] and the run "
                         "ends on `INVARIANT VIOLATED: certified regions "
                         "intersect`, each sibling PAIR sharing every point.\n"
                         "WHY (3) IS THE END OF THIS LINE. deposit's six paths "
                         "are three sibling PAIRS that differ in the success "
                         "bit and in nothing else. A region is a PRODUCT OF "
                         "PER-COORDINATE SETS over quantities a generated test "
                         "can SET; the bit is not one, so it is not a "
                         "coordinate, so no region can separate a pair. Pinning "
                         "it at certification time fixes the QUERY and leaves "
                         "the two REGIONS identical -- which the partition "
                         "invariant then correctly refuses. Making the bit a "
                         "region coordinate would change Definition 6, and is "
                         "not attempted here.\n"
                         "WHERE IT DOES WORK, measured: "
                         "notes/coverage/poc/B5_extcall_coord_fixture.py, both "
                         "units, four cases each -- the bit pinned to the "
                         "path's own value CERTIFIES, pinned to the sibling's "
                         "value comes back VACUOUS, left free comes back "
                         "REFUTED, and a name that does not exist is REFUSED.\n"
                         "⚠ COST. The driver offers every parseable nondet "
                         "local, and the instrumenter accepts only those it can "
                         "resolve: on deposit it took `extcall.success` and "
                         "refused account/value/fpt/supply/return_value$*, each "
                         "refusal costing one extra ESBMC invocation before the "
                         "re-query.")
    ap.add_argument("--static-extcall-inseparable", action="store_true",
                    help="passed to the driver: before region search, mark "
                         "witnessed sibling paths that agree on every "
                         "generated-test-settable payload and differ only on "
                         "concrete harvested extcall.* values as "
                         "NOT_CERTIFIED. OFF by default because an "
                         "artefact/stub cell may intentionally realise the "
                         "external-call behavior; the official gate-cell POC "
                         "recipe enables it because that cell has no such "
                         "fixture. Use a separate --out for this arm.")
    ap.add_argument("--static-uncontrolled-inseparable", action="store_true",
                    help="passed to the driver: before region search, mark "
                         "witnessed sibling paths split by known uncontrolled "
                         "ESBMC hash/nondet/extcall decisions, and not by a "
                         "free generated-test coordinate, as NOT_CERTIFIED. "
                         "Refutation-only; it saves refine/certify budget but "
                         "does not prove any PUT region.")
    ap.add_argument("--pin-agreed-establishable-env", action="store_true",
                    help="passed to the driver: pin each PUT-renderable "
                         "environment quantity on which every witnessed path "
                         "agrees. This is narrower than --pin-env, because "
                         "unrenderable values such as tx.origin and msg.data "
                         "are left unconstrained instead of producing certified "
                         "regions that a generated PUT cannot establish.")
    ap.add_argument("--pin", action="append", default=[], metavar="COORD=VALUE",
                    help="pass one `coord=value` PIN straight to the driver "
                         "(driver --pin). Repeatable. A pinned coordinate is "
                         "NOT generalised, and every region reported is a "
                         "statement about that slice. ⚠ WHY THIS SWEEP NEEDS "
                         "IT AND DID NOT HAVE IT: the driver's --level0 help "
                         "states its own scope as `coordinate == constant` "
                         "only, and says `coordinate A == coordinate B` is a "
                         "cross-coordinate relation that changes definition 6 "
                         "and is NOT attempted. A region is a PRODUCT of "
                         "per-coordinate intervals, so a box can never exclude "
                         "the set {x : c1 == c2} -- the certification query "
                         "keeps returning a fresh witness inside the box and "
                         "the shrink loop cuts a handful of values per round "
                         "against a refuting set of type-range size. MEASURED "
                         "on farming/setDistributor, gate cell, --env-coord "
                         "msg.sender: every refutation of enc 12/13/14/15 in "
                         "BOTH the 3-round and the 6-round arm named the same "
                         "PAIR, msg.sender and state._owner, and the 6-round "
                         "arm cut 2, 6, 128, 1280 and 43008 values per round "
                         "off a box of size ~2.6e94. Doubling the round budget "
                         "changed 0 certified to 0 certified and 187s to 348s. "
                         "Pinning ONE member of the pair is the smallest thing "
                         "that turns the relation back into `coordinate == "
                         "constant`, which is the case the method DOES cover. "
                         "⛔ IT IS NOT FREE: the region stops being a statement "
                         "about the pinned coordinate, so a rate measured with "
                         "a pin and one measured without are two measurements "
                         "wearing one name -- which is why the value is "
                         "recorded on every row and why this needs its own "
                         "--out.")
    # ---- WHICH CUT RULE PRODUCED A ROW IS PART OF WHAT THE ROW MEASURED ----
    #
    # The driver's default moved from "apply the tool's first `retry with ...`
    # suggestion, unread" to §Certification's rule -- keep the side of y_c that
    # x_pi lies on, take the cut removing the FEWEST values, and pin what cannot
    # be cut instead of losing the path. That changes the region a refutation
    # leaves behind and therefore changes what "certified" counts.
    #
    # ⚠ EVERY ROW RECORDED BEFORE THIS FLAG EXISTED WAS MEASURED UNDER `tool`
    # AND SAYS SO NOWHERE. That is precisely the shape this file already refuses
    # for --env-coord, --max-holes and --esbmc-arg: an arm whose configuration
    # is not in its records is an arm whose numbers cannot be re-derived. An
    # ABSENT `cut_policy` key therefore means "this row predates the flag", i.e.
    # `tool`, and must not be read as the current default.
    ap.add_argument("--cut-policy", choices=("spec", "tool"), default="spec",
                    help="passed to the driver. `spec` (default, and the "
                         "driver's) follows §Certification; `tool` is the "
                         "previous first-suggestion behaviour, kept so a "
                         "recorded arm can be reproduced verbatim. Recorded on "
                         "every row -- two rows measured under different cut "
                         "rules are two measurements wearing one name. ⚠ Use "
                         "--out to give each policy its OWN file.")
    ap.add_argument("--unit", action="append", default=[],
                    help="sweep only these unit names (repeatable). Without it "
                         "the whole benchmark is swept, which for a re-measure of "
                         "ONE unit means paying for every other unit first and "
                         "risking the budget before reaching it.")
    ap.add_argument("--path-function", default=None,
                    help="exact mangled identity for an overloaded --unit. "
                         "Passed through to the generalisation driver; the "
                         "result row records the resolved identity.")
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--workdir", default="/tmp/certify_all",
                    help="scratch root. The ARM's own subdirectory is added "
                         "under it automatically -- see below; pass this only "
                         "to move the whole tree off /tmp.")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve inputs and print child commands, but do not "
                         "run ESBMC, write driver logs, append JSONL rows, or "
                         "generate missing AST files.")
    args = ap.parse_args()
    try:
        apply_strong_certify_recipe(args)
    except ValueError as exc:
        print(f"[sweep] REFUSED: {exc}", file=sys.stderr)
        return 1
    if args.ce_collection_only:
        args.timeout = min(args.timeout, 60)
        args.run_timeout = min(args.run_timeout, 60)
    try:
        ensure_path_not_protected("--out", args.out)
        ensure_path_not_protected("--workdir", args.workdir)
        ensure_path_not_protected("--ast-cache-root", args.ast_cache_root)
    except ValueError as exc:
        print(f"[sweep] REFUSED: {exc}", file=sys.stderr)
        return 1

    # A --unit that matches nothing must FAIL, not sweep everything. R8: iterate
    # the EXPECTED names, not the found ones; a typo that silently widens the
    # sweep back to the whole benchmark is the "missing input silently rewrites
    # the scope" shape -- an empty filter reads as "no restriction" when it means
    # "the restriction was lost".
    want_units = set(args.unit)

    subject = None
    if args.subject_dir or args.subject_id or args.subject_root:
        if args.benchmarks:
            print("[sweep] REFUSED: --subject-* supplies the benchmark input; "
                  "do not also pass positional BENCHMARKS")
            return 1
        if not args.list_subject_units and len(want_units) != 1:
            print("[sweep] REFUSED: --subject-* is a contract-level prepared "
                  "subject and requires exactly one --unit for this run")
            return 1
        try:
            subject = resolve_subject(
                args.subject_id or args.subject_dir,
                root=args.subject_root if args.subject_root else None,
                benchmark=(args.subject_benchmark
                           if args.subject_benchmark else None),
                unit=next(iter(want_units)) if want_units else None,
                require_unit=not args.list_subject_units)
            subject = apply_subject_ast_cache(subject, args.ast_cache_root)
            wrote_ast = False if args.dry_run else ensure_solast(subject)
        except (SubjectError, subprocess.CalledProcessError) as exc:
            print(f"[sweep] REFUSED: could not resolve prepared subject: {exc}")
            return 1
        unit_label = f" unit={subject.unit}" if subject.unit else ""
        print(f"[sweep] prepared subject: {subject.benchmark_key} "
              f"contract={subject.contract}{unit_label} "
              f"flat={subject.flat_sol} ast={subject.solast}")
        if wrote_ast:
            print(f"[sweep] generated AST: {subject.solast}")
        elif args.dry_run and not os.path.exists(subject.solast):
            print(f"[sweep] dry-run: AST would be generated at "
                  f"{subject.solast}")
        if args.list_subject_units:
            if args.dry_run and not os.path.exists(subject.solast):
                print("[sweep] dry-run: cannot list units until the AST exists")
                return 0
            try:
                enum = enumerate_subject_units(subject)
            except SubjectError as exc:
                print(f"[sweep] REFUSED: could not enumerate units: {exc}")
                return 1
            print("[sweep] subject units: " + (
                ", ".join(enum.units) if enum.units else "<none>"))
            if enum.skipped:
                print("[sweep] skipped entry point(s):")
                for row in enum.skipped:
                    label = row.get("name") or row.get("kind") or "entry"
                    print(f"  - {row.get('contract')}.{label}: "
                          f"{row.get('reason')}")
            return 0

    names = [subject.benchmark_key] if subject else (
        args.benchmarks or [b for b in BENCHMARKS if b != "st1inch_St1inch"])

    # ---- THERE IS NO CORPUS SWEEP ANY MORE, ONLY ONE POC AT A TIME ----
    #
    # This file's own docstring calls itself a sweep, and that is exactly what
    # is banned: a benchmark key names 8 to 28 units, the default names five
    # benchmarks, and one invocation therefore commits to 65 driver runs at
    # 600s each. The corpus is split into PoCs, one per TARGET
    # public/external function (`poc_split.py`), and a run must name one.
    #
    # Refused rather than truncated to the first unit: silently running one and
    # writing it under the sweep's name is the one-fact-two-ledgers failure
    # this file already refuses for --redo and for the arm files.
    if len(names) != 1 or len(want_units) != 1:
        print(
            f"[sweep] REFUSED: this driver now certifies exactly ONE unit of "
            f"ONE benchmark per invocation, and it was given "
            f"{len(names)} benchmark(s) and {len(want_units)} --unit name(s).\n"
            f"  The corpus is split into PoCs, one per target public/external "
            f"function. A benchmark key by itself commits to every unit it "
            f"has, which is the run the work order bans.\n"
            f"    python3 notes/coverage/scripts/poc_split.py --list\n"
            f"    python3 notes/coverage/scripts/poc_one.py <poc-id>\n"
            f"  To drive it directly: {os.path.basename(__file__)} <one-bench> "
            f"--unit <one-unit> --out <that PoC's own file>")
        return 1
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    # ---- REFUSED HERE TOO, NOT ONLY IN THE DRIVER ----
    #
    # The driver raises on --probe-ladder without --probe-witnesses, so this
    # cannot silently mis-measure. It is repeated here because the driver raises
    # PER UNIT, after the enumeration run: a sweep would pay a full ESBMC pass
    # and write a row whose bucket is a driver crash, and a crash row is read as
    # a fact about the unit. Refusing before any run costs nothing and keeps the
    # failure named after the flag that caused it.
    if args.probe_ladder and not args.probe_witnesses:
        print("[sweep] REFUSED: --probe-ladder needs --probe-witnesses. The "
              "ladder is anchored at each path's own KNOWN MEMBERS, and "
              "without extra witnesses every path has exactly one, so the "
              "'ladder' would be a single point wearing the name of a "
              "bracket.")
        return 1
    if bool(args.enumeration_index) != bool(args.enumeration_report):
        print("[sweep] --enumeration-index and --enumeration-report must be "
              "passed together")
        return 1

    # ---- THE ARM OWNS ITS SCRATCH DIRECTORY, DERIVED FROM ITS --out ----
    #
    # The workdir was `<root>/<bench>/<unit>` with no arm component, so two
    # runs of the SAME unit under different flags shared one directory and
    # overwrote each other in place: cov-report.json, outer.json, cert.json and
    # driver.log all have fixed names.
    #
    # MEASURED, today, twice on one unit: a --skip-bracket run and a
    # --run-timeout 600 run of farming/startFarming both landed in
    # /tmp/certify_all/farming/startFarming, and the second destroyed the
    # first's driver.log -- the ONLY record of the per-round accounting that
    # the first run's whole conclusion rested on. It survived because it
    # happened to be copied out minutes earlier.
    #
    # It also blocks the obvious use of --jobs: two arms running concurrently
    # would interleave writes to one directory, and `stamp_workdir`'s
    # refusal cannot catch that -- it compares CONFIG_FIELDS, and neither
    # --run-timeout nor --skip-bracket is one of them, so both runs read as the
    # same configuration.
    #
    # Keyed on the --out STEM rather than on a new flag, because the house rule
    # that every arm gets its own results file is already enforced everywhere
    # (--env-coord, --max-holes, --esbmc-arg each say so in their help). Making
    # that one decision govern the scratch tree too means an arm cannot be
    # given its own file and still share a directory.
    arm_dir = os.path.splitext(os.path.basename(args.out))[0]
    arm_root = os.path.join(args.workdir, arm_dir)
    if args.redo and os.path.isdir(arm_root):
        keep = f"{arm_root}.superseded.{time.time_ns()}"
        os.replace(arm_root, keep)
        print(f"[sweep] --redo: moved the previous scratch tree to {keep}; "
              "a new stage-1 report changes the workdir stamp and may not "
              "overwrite old query artefacts")
    os.makedirs(arm_root, exist_ok=True)

    # THE MEMORY BOUND IS COMPUTED AND PRINTED BEFORE ANY RUN, and a failure to
    # fit is a refusal. Printed even at --jobs 1, so the number a sweep ran
    # under is in its own log rather than in whoever's memory launched it.
    memlimit, refusal = job_memlimit_gib(args.jobs, want_gib=args.memlimit_gib)
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
    print(f"[sweep] recipe {args.recipe_version}", flush=True)
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
        keep = f"{args.out}.superseded.{time.time_ns()}"
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
                done.add(certification_key(
                    r.get("benchmark"), r.get("unit"),
                    r.get("path_function"), args.path_function))
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
        if subject and bench == subject.benchmark_key:
            sol = subject.flat_sol
            ast = subject.solast
            contract = subject.contract
            subject_record = subject.to_record()
        elif bench not in BENCHMARKS:
            print(f"[sweep] unknown benchmark '{bench}'; known: "
                  + ", ".join(sorted(BENCHMARKS)))
            return 1
        else:
            sol, contract = BENCHMARKS[bench]
            ast = os.path.join(INPUTS, sol + ".solast")
            sol = os.path.join(INPUTS, sol)
            subject_record = None
        wd = os.path.join(args.workdir, arm_dir, bench)
        os.makedirs(wd, exist_ok=True)
        print(f"\n########## {bench} ({contract}) ##########", flush=True)
        if subject and bench == subject.benchmark_key:
            got, why = ([(subject.contract, subject.unit)], []), None
            print(f"[sweep] {bench}: using explicit prepared-subject unit "
                  f"{subject.contract}.{subject.unit}")
        else:
            got, why = units_of(bench)
        if got is None:
            got, poc_why = units_from_enumeration_index(
                args.enumeration_index, bench, want_units)
            if got is not None:
                print(f"[sweep] {bench}: {poc_why}")
                why = None
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
        if subject and bench == subject.benchmark_key:
            print(f"[sweep] {bench}: {len(units)} unit(s) from the prepared "
                  f"subject resolver: {', '.join(units)}")
        else:
            print(f"[sweep] {bench}: {len(units)} unit(s) from the "
                  f"round-trip's emit.jsonl: {', '.join(units)}")
        if killed_in_roundtrip:
            # Named up front, because a unit the ENUMERATION could not finish is
            # very likely to be one this sweep cannot finish either -- and a
            # KILLED record here would otherwise read as a stage-2 result when
            # it is inherited from stage 1.
            print(f"[sweep] {bench}: {len(killed_in_roundtrip)} of these were "
                  f"KILLED in the round-trip's own enumeration and are "
                  f"expected to be killed here too: "
                  f"{', '.join(killed_in_roundtrip)}")

        requested_pf = args.path_function if args.path_function else None
        todo = [(i, u) for i, u in enumerate(units, 1)
                if certification_key(bench, u, requested_pf,
                                     args.path_function) not in done]
        for i, unit in enumerate(units, 1):
            if certification_key(bench, unit, requested_pf,
                                 args.path_function) in done:
                print(f"  [{i}/{len(units)}] {unit} — already recorded",
                      flush=True)

        def run_unit(item):
            i, unit = item
            uwd = os.path.join(
                wd, unit + path_function_artifact_suffix(args.path_function))
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
                   "--sol", sol,
                   "--ast", ast,
                   "--contract", contract, "--unit", unit,
                   "--scope", args.scope, "--max-tx", str(args.max_tx),
                   "--probes", str(args.probes),
                   "--claim-budget", str(args.claim_budget),
                   "--refine-rounds", str(args.refine_rounds),
                   "--shrink-rounds", str(args.shrink_rounds),
                   "--safety-retreat-after-tiny-cuts",
                   str(args.safety_retreat_after_tiny_cuts),
                   # ---- THE PER-ESBMC-RUN BUDGET, NOW `--run-timeout` ----
                   #
                   # It was the literal 180 the comment below argues about. The
                   # comment is kept verbatim because every word of it is still
                   # true -- the quantity is still not the same as
                   # `unit_timeout_s`, still governs the largest failure bucket,
                   # and its DEFAULT is still unargued. What changed is that a
                   # run can now name it, which is what makes "budget" and
                   # "method" separable by a measurement instead of by opinion.
                   #
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
                   "--timeout", str(min(args.timeout, args.run_timeout)),
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
            if args.ce_collection_only:
                cmd.append("--ce-collection-only")
            if args.level0:
                cmd.append("--level0")
            if args.path_function:
                cmd += ["--path-function", args.path_function]
            if args.level0_perturb:
                cmd.append("--level0-perturb")
            # PASSED ONLY WHEN ASKED FOR, unlike --max-holes below. The driver's
            # default is 0/off and 0 is a MEANINGFUL value there ("one witness
            # per path"), so an always-passed `--probe-witnesses 0` would be
            # byte-identical in behaviour -- but the row already carries the
            # value, and the command line is what a reader replays by hand. Kept
            # off the line when off so a replayed command is the one that ran.
            if args.probe_witnesses:
                cmd += ["--probe-witnesses", str(args.probe_witnesses)]
            if args.probe_ladder:
                cmd.append("--probe-ladder")
            if args.probe_ladder_budget:
                cmd += ["--probe-ladder-budget", str(args.probe_ladder_budget)]
            if args.no_auto_pin_value:
                cmd.append("--no-auto-pin-value")
            if args.pin_extcall:
                cmd.append("--pin-extcall")
            if args.static_extcall_inseparable:
                cmd.append("--static-extcall-inseparable")
            if args.static_uncontrolled_inseparable:
                cmd.append("--static-uncontrolled-inseparable")
            if args.skip_bracket:
                cmd.append("--skip-bracket")
            if args.env_coord_disagreed:
                cmd.append("--env-coord-disagreed")
            if args.pin_agreed_establishable_env:
                cmd.append("--pin-agreed-establishable-env")
            if args.pin_agreed_state:
                cmd.append("--pin-agreed-state")
            for ec in args.env_coord:
                cmd += ["--env-coord", ec]
            # ALWAYS PASSED, like --max-holes above and for the same reason: a
            # flag that only sometimes appears cannot be read back off the
            # record. The defaults equal the driver's, so an unflagged sweep is
            # byte-identical to every recorded one.
            cmd += ["--slot-coords", str(args.slot_coords)]
            if args.state_struct_fields:
                cmd.append("--state-struct-fields")
            if args.enumeration_index:
                cmd += ["--enumeration-index", args.enumeration_index,
                        "--enumeration-report", args.enumeration_report]
            for sc in args.slot_coord:
                cmd += ["--slot-coord", sc]
            # Passed only when asked for, unlike --max-holes above, and the
            # difference is deliberate: an empty list IS the default value and
            # the row carries it verbatim, so "we passed none" is readable off
            # the record without a placeholder token on the command line.
            for p in args.pin:
                cmd += ["--pin", p]
            # ALWAYS PASSED, same rule again: the row must be able to say which
            # cut rule made it, and "the driver's default at the time" is not a
            # value anyone can read back off an artefact.
            cmd += ["--cut-policy", args.cut_policy]
            # `=` form, always -- see the comment at the flag. A value that
            # starts with a dash is the whole point of this flag and is the one
            # case the two-token form cannot pass.
            for a in args.esbmc_arg:
                cmd.append(f"--esbmc-arg={a}")
            if args.dry_run:
                print("[dry-run] " + " ".join(cmd), flush=True)
                return {"benchmark": bench, "unit": unit,
                        "bucket": "DRY-RUN", "subject": subject_record}
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
            generalise_progress = result_generalise_progress(uwd, t1)
            machine_pins = result_pins(uwd, t1)
            rec.update({"benchmark": bench, "unit": unit,
                        "path_function": result_path_function(uwd),
                        "certified_details":
                            result_certified_details(uwd, t1),
                        "not_certified_details":
                            result_not_certified_details(uwd, t1),
                        "enumeration_salvage":
                            result_enumeration_salvage(uwd, t1),
                        "driver_diagnostic": result_driver_diagnostic(out),
                        "generalise_progress": generalise_progress,
                        "empty_witness_obstacles":
                            result_empty_witness_obstacles(uwd, unit, t1),
                        "partial_witness_journal":
                            result_partial_witness_journal(
                                uwd, t1, progress=generalise_progress),
                        "wall_s": round(wall, 1), "exit": rc,
                        "memlimit_gib": memlimit, "jobs": args.jobs,
                        "recipe_version": args.recipe_version,
                        "scope": args.scope, "max_tx": args.max_tx,
                        # THE CONFIGURATION TRAVELS WITH THE RECORD. Two units
                        # measured under different ladders are not comparable,
                        # and this project has already paid once for a ratio
                        # whose numerator and denominator came from runs that
                        # shared only a benchmark name.
                        "skip_bracket": bool(args.skip_bracket),
                        # Explicit width mechanisms. `probes` also controls a
                        # refine round and therefore cannot prove that the
                        # geometric bracket ran. Likewise `cut_policy` governs
                        # certification retreat; it is not sibling subtraction.
                        "geometric_bracket": not bool(args.skip_bracket),
                        "sibling_subtraction": bool(
                            (rec.get("witnessed") or 0) > 1),
                        # WHO CHOSE THE COORDINATE SET. A region measured with
                        # the sender promoted and the owner pinned BY THE
                        # DRIVER is the same measurement as one where a human
                        # typed both names -- but only these two fields say
                        # which run can be reproduced without knowing what the
                        # human knew. `false` means the arm declined; an absent
                        # key means the row predates the flags.
                        "env_coord_disagreed": bool(args.env_coord_disagreed),
                        "pin_agreed_establishable_env": bool(
                            args.pin_agreed_establishable_env),
                        "pin_agreed_state": bool(args.pin_agreed_state),
                        "level0": bool(args.level0),
                        # THE ARM'S OWN FIELD. `false` means "we asked for the
                        # one-value list", not "the question did not arise" --
                        # every row recorded before this flag existed carries no
                        # key at all, and that absence is what marks it as
                        # predating the arm rather than as having declined it.
                        "level0_perturb": bool(args.level0_perturb),
                        # THE LADDER'S ANCHOR AND ITS WIDTH. A bracket measured
                        # from zero and one measured from a path's own members
                        # are different measurements of the same domain, and a
                        # reader who cannot see which reads the rungs as a
                        # property of the contract. Recorded as values rather
                        # than omitted when default, for the same reason
                        # `env_coord: null` is.
                        "probe_witnesses": args.probe_witnesses,
                        "probe_ladder": bool(args.probe_ladder),
                        "probe_ladder_budget": args.probe_ladder_budget,
                        # WHICH ENVIRONMENT THE REGIONS WERE MEASURED IN. A row
                        # with msg.value pinned and one with it free are two
                        # different statements about the same unit, and this is
                        # the field that stops them sharing a table.
                        "no_auto_pin_value": bool(args.no_auto_pin_value),
                        # NOT omitted when unset. `None` here means "this arm
                        # ran with every environment quantity pinned or dropped",
                        # which is a DIFFERENT measurement from msg.sender being
                        # free -- and an absent key would read as "no arm
                        # information", i.e. as the thing certify_arms.py prints
                        # as MIXED. A recorded null is a fact; a missing field is
                        # an unknown.
                        # THE FULL LIST, always. This is the authoritative field.
                        "env_coords": list(args.env_coord),
                        # KEPT FOR READERS WRITTEN BEFORE THE FLAG BECAME
                        # REPEATABLE, and deliberately NULL as soon as there is
                        # more than one. A reader that knows only this key would
                        # otherwise read a two-coordinate arm as a one-coordinate
                        # arm -- a wrong value, where null is the honest
                        # "no arm information" those readers already handle. It is
                        # a projection of `env_coords` computed at write time, not
                        # a second place the fact is kept.
                        "env_coord": (args.env_coord[0]
                                      if len(args.env_coord) == 1 else None),
                        # THE PUNCH ARM'S CONFIGURATION, on every row. A hole
                        # count read off rows that do not carry these two is a
                        # count whose denominator is unknown: `max_holes: 0`
                        # means no region COULD carry a hole, and a reader who
                        # cannot see that reads the 0 as a property of the
                        # contracts. Recorded as values rather than omitted when
                        # default, for the same reason `env_coord: null` is.
                        "max_holes": args.max_holes,
                        "max_region_pieces": args.max_region_pieces,
                        # THE COORDINATE SET IS PART OF WHAT WAS MEASURED. A
                        # region certified with the balance slot bounded is a
                        # different statement from one certified with it
                        # unconstrained -- and a reader who cannot see which
                        # would read the second as a property of the contract.
                        # Recorded as values, never omitted when default, for
                        # the same reason env_coord: null is.
                        "slot_coords": args.slot_coords,
                        "slot_coord": list(args.slot_coord),
                        "state_struct_fields": bool(args.state_struct_fields),
                        "enumeration_index": args.enumeration_index,
                        "enumeration_report": result_enumeration_report(
                            uwd, args.enumeration_report, t1),
                        # WHAT WE ASKED TO PIN, which is NOT the same field as
                        # `pins` -- that one is the driver's own report of what
                        # it ENDED UP pinning (auto msg.value, constants it
                        # refused to generalise), and it would read as though
                        # the sweep had requested them. `[]` means we requested
                        # none; an absent key means the row predates the flag.
                        "pin_requested": list(args.pin),
                        # WHETHER THE HARNESS-CHOSEN QUANTITIES WERE FIXED. A
                        # region certified with them pinned is a statement about
                        # a slice no generated test can enter by choosing
                        # arguments, so a row measured with this and one without
                        # are two measurements wearing one name. An absent key
                        # means the row predates the flag, i.e. `false`.
                        "pin_extcall": bool(args.pin_extcall),
                        # Static, refutation-only attribution for gate cells
                        # whose witnessed siblings differ only in extcall.*
                        # values no generated test can set. Default false; a
                        # stub/artefact arm must not inherit this silently.
                        "static_extcall_inseparable":
                            bool(args.static_extcall_inseparable),
                        "static_uncontrolled_inseparable":
                            bool(args.static_uncontrolled_inseparable),
                        # WHICH CUT RULE. An absent key means the row predates
                        # the flag and was therefore measured under `tool`; it
                        # must never be read as the current default. See the
                        # comment at the flag.
                        "cut_policy": args.cut_policy,
                        # THE EXTRA ESBMC ARGUMENTS TRAVEL WITH THE ROW, as a
                        # list and never omitted. `[]` means "we passed none",
                        # and an absent key means "this row predates the flag" --
                        # two different things, and the second one is what makes
                        # an old row's bound unknown rather than default.
                        "esbmc_args": list(args.esbmc_arg),
                        "probes": args.probes,
                        "claim_budget": args.claim_budget,
                        "refine_rounds": args.refine_rounds,
                        "shrink_rounds": args.shrink_rounds,
                        "safety_retreat_after_tiny_cuts":
                            args.safety_retreat_after_tiny_cuts,
                        "unit_timeout_s": args.timeout,
                        "subject": subject_record,
                        # The per-ESBMC-RUN budget, which is NOT `unit_timeout_s`
                        # and is what "no outer-box round finished" counts. See
                        # the comment at the `--timeout` argument above: without
                        # this field the largest failure bucket in the summary
                        # has no budget recorded anywhere in the artefact.
                        # Records written before this field existed carry no
                        # value for it, and the summary says so rather than
                        # substituting `unit_timeout_s`.
                        "run_timeout_s": min(args.timeout, args.run_timeout),
                        # Which binary produced this record. Read on resume; a
                        # file whose records came from another build is refused
                        # rather than continued.
                        "binary": ident})
            if rec.get("pins") is None and machine_pins is not None:
                rec["pins"] = machine_pins
            merge_not_certified_details(rec)
            rec["bucket"] = bucket(rec, rc, out)
            # THE COMMAND, THEN ITS OUTPUT. The log is read when a unit's row
            # cannot say what happened, and the first question then is what was
            # actually run -- the row records the sweep's flags, not the child's
            # argv, and the two differ by everything this function assembles.
            # MEASURED: a 0.0-second exit-1 row was attributed only after the
            # child was replayed by hand, which needed exactly this line.
            #
            # ⛔ ONE WRITER. A second write of this file was added earlier today
            # on the false premise that this one did not exist; two writers can
            # drift in format, and then which content a reader gets depends on
            # which ran last.
            with open(os.path.join(uwd, "driver.log"), "w") as f:
                f.write(" ".join(cmd) + "\n\n" + (out or ""))
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
                # ---- THE DENOMINATOR IS `witnessed`, AND THIS LINE USED TO
                # ---- HIDE THAT THE OTHER TWO DO NOT ADD UP TO IT ----
                #
                # `certified` and `not_certified` are the paths that reached a
                # VERDICT. A witnessed path can be in NEITHER: the
                # `--run-timeout` comment above records exactly that -- enc 12
                # and 13 "produced NO RECORD AT ALL: absent from `certified` and
                # from `not_certified` alike" when the per-run budget bound.
                #
                # So `certified / (certified + not)` is not the certification
                # rate; it silently drops the paths the budget ate and reads
                # HIGH. MEASURED on results_pieces_corpus.jsonl: 10 certified,
                # 93 not, 113 witnessed -- 10/103 = 9.7% against the true
                # 10/113 = 8.8%, and the 10 missing paths are the finding, not
                # a rounding difference.
                #
                # The remainder is printed as its own term rather than left to
                # subtraction, because a reader who has to do the arithmetic to
                # notice a gap is a reader who will not notice it.
                nw = rec.get("witnessed")
                nc, nn = len(rec["certified"]), len(rec["not_certified"])
                if nw is None:
                    tally = f"{nc} certified / {nn} not / witnessed UNKNOWN"
                    journal = rec.get("partial_witness_journal") or {}
                    if journal:
                        tally += (
                            f" [partial journal: {journal.get('path_count')} "
                            f"path(s), {journal.get('witness_count')} witness(es), "
                            f"{journal.get('claims_decided')}/"
                            f"{journal.get('claims_total')} claims decided]")
                else:
                    gap = nw - nc - nn
                    tally = (f"{nc} certified / {nn} not / {nw} witnessed"
                             + (f" ⚠ {gap} path(s) reached NO verdict"
                                if gap else ""))
                    # ---- WHAT LEVEL 0 HAD ALREADY DECIDED, ON THE SAME LINE ----
                    #
                    # A path with no verdict used to print as nothing but a
                    # number, and on this corpus that number is the largest
                    # bucket there is: 53 of 137. But level 0 answers in
                    # SECONDS -- measured 7.5s on farming/setDistributor, for
                    # all five of its paths -- and the run that reported "0
                    # certified / 0 not" was killed 232 seconds LATER, in the
                    # geometric ladder, with those five projections in its own
                    # log.
                    #
                    # Printed rather than only stored, because the operator
                    # reads this line and not the JSONL, and a recovery nobody
                    # sees is a recovery that will be re-derived by hand.
                    #
                    # ⛔ NOT a certified region and never counted as one: level
                    # 0 is a projection that has not been through the
                    # certification query, and `bucket()` is untouched. The
                    # vacuity count is carried WITH it because a point from a
                    # ONE-VALUE candidate list cannot be told apart from a path
                    # with no inputs at all -- the tool says so itself, and a
                    # reader shown only the point would take a vacuous
                    # antecedent for an established equality.
                    if gap and rec["level0_points"]:
                        nv = len(rec["level0_vacuity_risk"])
                        tally += (
                            f" [level 0 HAD decided {len(rec['level0_points'])}"
                            f" of them at {rec['level0_round_s']}s"
                            + (f", {nv} needing a second probe before use"
                               if nv else "")
                            + "]")
                print(f"  [{i}/{len(units)}] {unit}: {rec['bucket']}, "
                      f"{tally}, "
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

    if args.dry_run:
        print("\n[sweep] dry run completed; wrote no result rows or driver logs")
    else:
        print(f"\n[sweep] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
