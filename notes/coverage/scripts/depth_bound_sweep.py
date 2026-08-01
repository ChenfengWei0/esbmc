#!/usr/bin/env python3
"""Is the two Escrows' 8-decision gap the CALL-DEPTH BOUND rather than scope?

WHY. `branch_gate.py` after the claim-key repair puts both Escrows at 0/8 on
`contracts/libraries/ImmutablesLib.sol`, and that shortfall was recorded as a
MEASUREMENT-SCOPE difference: the collector refuses library units
(`--contract <Lib>` has no dispatcher, `--function` is forbidden here because it
verifies from an arbitrary state and can yield a red test).

But the tool says something else in its own run log, and it names the remedy:

    WARNING: 8 call site(s) are deeper than the call depth bound (4) and were
    NOT expanded (... sol:@C@ImmutablesLib@F@hash#932 ...); paths through them
    are MERGED rather than enumerated. Raise --unwind to enumerate them

So `ImmutablesLib` IS called from the units; its call sites are simply past the
depth bound. Those are different diagnoses with different consequences for the
paper — "our method cannot serve a library-only compilation unit" (a real, stated
applicability limit) versus "we ran it at a depth bound that excluded a callee we
do reach" (a configuration we chose).

THIS IS NOT A WORKAROUND, AND THE DISTINCTION IS THE POINT. The project rule is
that raising a limit is not an answer. Here the limit is not being raised to make
a number better; it is being varied to find out WHICH of two explanations the
0/8 has. Whatever comes out, the honest report is the attribution, not the
improved figure — and if the cost is prohibitive that is itself the finding.

WHAT IS VARIED: `--unwind` only. Under `--solidity-path-coverage` that one flag
sets BOTH the enumeration's call-depth bound and its loop bound, and the pass
adopts it for symex too, so they cannot be separated from outside. That coupling
is itself worth reporting.

THE COMPARISON IS ON WHICH FILES THE WITNESSED DECISIONS LAND IN, not on totals:
the gate's currency is canonical decisions per file, and a bigger F count that
lands in the same files buys nothing at the gate.

READINGS, FIXED BEFORE THE RUN:

  A  ImmutablesLib decisions appear at a higher bound
       -> the 0/8 is the DEPTH BOUND, not scope. The "library-only compilation
          unit" limitation still stands as written, but it is NOT what these 8
          decisions are, and the gate table's attribution has to be rewritten.
  B  they do not appear at any affordable bound
       -> the recorded scope explanation survives, and the residual-call warning
          is naming a call site whose decisions are not these 8. Report which.
  C  the run does not finish at the higher bound
       -> the answer is a cost, not a scope: say at which bound it stopped
          finishing and what that bound would have cost. NOT A WIN, and not a
          reason to raise anything further.
  D  the F count rises but the per-file distribution does not
       -> more witnesses inside files already saturated; the gate does not move
          and neither explanation is supported. This is the outcome that looks
          like progress and is not.

Usage: python3 depth_bound_sweep.py <flat.sol> <Contract> <unit> <n1,n2,...>
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESBMC = HERE.parent.parent.parent / "build" / "src" / "esbmc" / "esbmc"
INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")
RESID = re.compile(r"(\d+) call site\(s\) are deeper than the call depth bound")


def run(flat, contract, unit, unwind, wd, timeout):
    cmd = [
        str(ESBMC), str(flat) + ".solast", "--sol", str(flat),
        "--solidity-path-coverage", "--cov-report-json",
        "--solidity-max-tx", "1", "--path-cov-max-goals", "10000",
        "--memlimit", "8g", "--contract", contract,
        "--focus-function", unit, "--unwind", str(unwind),
    ]
    # start_new_session + killpg: a timeout here must not leave an esbmc holding
    # 8 GiB. This machine has been exhausted once by orphaned solver processes,
    # and `subprocess.run`'s own kill reaches only the direct child.
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, cwd=wd,
                         start_new_session=True)
    try:
        out, rc = p.communicate(timeout=timeout)[0], p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        p.communicate()
        return None, "OUTER TIMEOUT", []
    rep = Path(wd) / "cov-report.json"
    claims = json.loads(rep.read_text()).get("claims", []) if rep.exists() else []
    return rc, out, claims


def files_of(claims, flat, project):
    """Which ORIGINAL source files the witnessed decisions land in.

    ⛔ THE FIRST VERSION OF THIS FUNCTION COULD NOT ANSWER THE QUESTION, AND IT
    RAN ONCE BEFORE THAT WAS NOTICED. It bucketed on `decisions[].file`, which is
    the FLAT input's path -- one value, on every claim, at every bound. So
    "reading A: a new file appeared" was unreachable by construction, and the
    run duly reported B/D. A discriminator whose two outcomes are the same by
    construction is not a discriminator, and this project has three of them on
    record; this is the fourth.

    The report publishes flat LINE numbers, and the mapping from a flat line to
    its original file is the flat's own `// File` blocks -- which is exactly what
    `branch_gate.canonical_in_scope` uses to build the gate's per-file table. So
    the currency here is now the gate's currency, and the two can be compared.

    Intersected with the canonical decision lines of each in-scope file, for the
    same reason the gate does it: without the intersection this would count
    decisions the baseline never had in either column.
    """
    sys.path.insert(0, str(HERE.parent.parent))          # notes/
    import branch_gate as bg                             # noqa: E402
    canon, _blocks = bg.canonical_in_scope(str(flat), project)

    lines = set()
    f_claims = 0
    for c in claims:
        if c.get("status") != "F":
            continue
        f_claims += 1
        for d in c.get("decisions") or []:
            if d.get("synthetic_abi_gate"):
                continue
            ln = d.get("line")
            if isinstance(ln, int) and ln > 0:
                lines.add(ln)

    per_file = Counter()
    for fn, c_lines in canon.items():
        hit = len(lines & c_lines)
        if hit:
            per_file[fn] = hit
    # Files with a ZERO are kept out of `per_file` but printed by the caller from
    # `canon`, so a file that stays at 0 across bounds is visible rather than
    # absent -- absent and zero read the same on a table and mean different
    # things.
    return f_claims, per_file, {f: len(s) for f, s in canon.items()}


def main(argv):
    if len(argv) < 5:
        sys.exit(__doc__)
    flat = Path(argv[1]).resolve()
    contract, unit = argv[2], argv[3]
    bounds = [int(x) for x in argv[4].split(",")]
    timeout = int(argv[5]) if len(argv) > 5 else 900
    if not ESBMC.exists():
        sys.exit(f"no esbmc at {ESBMC}")
    # The scope helper takes a project key. `collect.is_project_own_marker`
    # currently IGNORES it -- the rule is entirely about the marker's path shape
    # -- but it is derived rather than faked, so this keeps working if that
    # helper ever starts reading it. Same convention as `collect.BENCHES`: the
    # flat is `<project>__<Entry>.flat.sol`.
    project = flat.name.split("__", 1)[0].replace("-", "_")

    print("## Is the gap the call-depth bound or the scope?\n")
    print(f"binary : {ESBMC}")
    print(f"input  : {flat}   unit: {unit}\n")

    seen = {}
    denom = {}
    with tempfile.TemporaryDirectory() as wd:
        for n in bounds:
            rc, out, claims = run(flat, contract, unit, n, wd, timeout)
            if rc is None:
                print(f"--unwind {n}:  {out} after {timeout}s "
                      f"-- contributes nothing, not zero\n")
                seen[n] = None
                continue
            i, r = INSTR.search(out), RESID.search(out)
            f_claims, per_file, denom = files_of(claims, flat, project)
            seen[n] = per_file
            print(f"--unwind {n}:  exit {rc}   "
                  + (f"{i.group(1)} path(s) across {i.group(2)} unit(s)"
                     if i else "(no instrumentation line)")
                  + f"   F claims {f_claims}"
                  + (f"   residual calls past the bound: {r.group(1)}"
                     if r else "   residual-call warning ABSENT"))
            # EVERY in-scope file, including the ones still at zero. A file that
            # is absent from a table and a file that is zero on it read the same
            # and mean different things -- and "still 0 at the higher bound" is
            # the whole answer in reading B.
            for fn in sorted(denom):
                print(f"        {per_file.get(fn, 0):>4} / {denom[fn]:<4} "
                      f"canonical decision(s)  {fn}")
            print()

    base = seen.get(bounds[0])
    print("=" * 74)
    # ONE BOUND IS NOT A SWEEP. With a single bound there is nothing to compare
    # against, and every branch below would report a comparison that was never
    # made -- the run above printed "READING C: no higher bound finished" when
    # no higher bound had been ASKED for. Refuse the verdict instead of
    # manufacturing one.
    if len(bounds) < 2:
        print("  NO VERDICT: one bound was given, so nothing was compared. The "
              "per-file table\n  above is a single measurement, not an "
              "attribution. Pass at least two bounds.")
        return 1
    if base is None:
        print("  VERDICT: the baseline bound did not finish; nothing to "
              "compare against.")
        return 1
    new_files = set()
    for n in bounds[1:]:
        p = seen.get(n)
        if p is None:
            continue
        new_files |= (set(p) - set(base))
    if new_files:
        print("  ✅ READING A: raising the bound reached files the baseline "
              "never touched:")
        for fn in sorted(new_files):
            print(f"        {fn}")
        print("     ⇒ that shortfall is the CALL-DEPTH BOUND, not scope. The "
              "gate table's\n        attribution has to be rewritten, and the "
              "cost of the higher bound reported\n        beside it.")
        return 0
    if all(seen.get(n) is None for n in bounds[1:]):
        print("  ⛔ READING C: no higher bound finished. The answer is a COST, "
              "not a scope.\n     Report the bound at which it stopped "
              "finishing. This is not a win and it is\n     not a reason to "
              "raise anything further.")
        return 1
    print("  ⛔ READING B/D: the higher bounds reached no new file. Either the "
          "residual-call\n     warning names call sites whose decisions are "
          "not the missing ones (B), or the\n     extra witnesses land in "
          "files already saturated (D). Say which, from the\n     per-file "
          "counts above -- do not round it to 'no change'.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
