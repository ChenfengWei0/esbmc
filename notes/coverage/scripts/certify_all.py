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
    are CERTIFIED / NOT-CERTIFIED-with-reason / NO-COORDINATE / KILLED / CRASHED,
    and a unit lands in exactly one.
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
import json
import os
import re
import subprocess
import sys
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
           "msg_value_pin": "not seen"}
    for line in out.splitlines():
        m = RE_WITNESSED.match(line)
        if m:
            rec["witnessed"] = int(m.group(1))
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
        return "NO-PATH"
    return "NOT-CERTIFIED"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("benchmarks", nargs="*", default=[],
                    help="which to sweep; default is every one that has ever "
                         "produced a witnessed path (st1inch is EXCLUDED by "
                         "default -- all 22 of its runs were killed by the 180s "
                         "bound and its enumeration side is empty, which is "
                         "measured, not assumed)")
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
    ap.add_argument("--shrink-rounds", type=int, default=3)
    ap.add_argument("--refine-rounds", type=int, default=2)
    ap.add_argument("--probes", type=int, default=8)
    ap.add_argument("--skip-bracket", action="store_true",
                    help="the geometric bracket is the binding cost on real "
                         "input -- 258 probes per coordinate per direction. "
                         "Measured on the S4 fixture: a run whose bracket hit "
                         "its budget and measured NOTHING still produced every "
                         "exact region from level 0 plus refinement. ONE shape, "
                         "so this is offered rather than made default.")
    ap.add_argument("--level0", action="store_true", default=True)
    ap.add_argument("--redo", action="store_true")
    ap.add_argument("--workdir", default="/tmp/certify_all")
    args = ap.parse_args()

    names = args.benchmarks or [b for b in BENCHMARKS if b != "st1inch_St1inch"]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    os.makedirs(args.workdir, exist_ok=True)

    done = set()
    if os.path.exists(args.out) and not args.redo:
        with open(args.out) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                done.add((r.get("benchmark"), r.get("unit")))
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

        for i, unit in enumerate(units, 1):
            if (bench, unit) in done:
                print(f"  [{i}/{len(units)}] {unit} — already recorded")
                continue
            uwd = os.path.join(wd, unit)
            os.makedirs(uwd, exist_ok=True)
            cmd = [sys.executable, DRIVER,
                   "--esbmc", ESBMC,
                   "--sol", os.path.join(INPUTS, sol),
                   "--ast", os.path.join(INPUTS, sol + ".solast"),
                   "--contract", contract, "--unit", unit, "--focus",
                   "--probes", str(args.probes),
                   "--refine-rounds", str(args.refine_rounds),
                   "--shrink-rounds", str(args.shrink_rounds),
                   "--timeout", str(min(args.timeout, 180)),
                   "--memlimit", "8g", "--workdir", uwd]
            if args.level0:
                cmd.append("--level0")
            if args.skip_bracket:
                cmd.append("--skip-bracket")
            t1 = time.time()
            try:
                p = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=args.timeout)
                out, rc = p.stdout + p.stderr, p.returncode
            except subprocess.TimeoutExpired as e:
                def _t(b):
                    if b is None:
                        return ""
                    return b.decode(errors="replace") if isinstance(b, bytes) \
                        else b
                out = _t(e.stdout) + _t(e.stderr) + \
                    f"\n[run] TIMEOUT after {args.timeout}s\n"
                rc = 124
            wall = time.time() - t1
            rec = parse_driver(out)
            rec.update({"benchmark": bench, "unit": unit,
                        "bucket": bucket(rec, rc, out),
                        "wall_s": round(wall, 1), "exit": rc})
            with open(args.out, "a") as f:
                f.write(json.dumps(rec) + "\n")
            logp = os.path.join(uwd, "driver.log")
            with open(logp, "w") as f:
                f.write(out)
            # FLUSHED. This sweep's expected runtime is hours and its expected
            # ending is a kill, so an unflushed progress line is a progress line
            # that does not exist: Python block-buffers stdout to a file, and
            # the first run of this script showed an EMPTY log after three
            # minutes of real work. A long job that cannot be watched is a long
            # job that gets restarted for no reason.
            print(f"  [{i}/{len(units)}] {unit}: {rec['bucket']}, "
                  f"{len(rec['certified'])} certified / "
                  f"{len(rec['not_certified'])} not, "
                  f"{len(rec['coords'])} free coordinate(s), "
                  f"msg.value pin {rec['msg_value_pin']}, {wall:.0f}s",
                  flush=True)

    print(f"\n[sweep] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
