#!/usr/bin/env python3
"""Is st1inch's `solver-unknown` the ENCODER or the CONTRACT? Three arms, one factor.

THE CONFOUND THIS EXISTS TO BREAK. st1inch is the only benchmark in the corpus
run with `--z3 --tuple-node-flattener`, and the only one with any
`solver-unknown`. Corpus-wide, 4372 solves: 3711 UNSAT / 464 SAT / 0 no-verdict
on the four default-backend benchmarks, against 106 UNSAT / **0 SAT** / 90
no-verdict on st1inch. Coverage requires SAT, so st1inch's 0% is forced -- but
"the encoder does this" and "st1inch's source does this" are indistinguishable
from one benchmark run under one encoder.

THE VEHICLE IS CHOSEN BECAUSE ITS ANSWER IS ALREADY ON DISK. `aqua
--focus-function safeBalances` is recorded at 2 SAT (0.130-0.145 s), 9 UNSAT,
0 no-verdict under the default backend. It has struct state (`BalanceLib`), so
the node flattener has something to act on. Everything but the encoder flags is
held byte-identical across the three arms.

    arm A   (default backend)              POSITIVE CONTROL
    arm B   --z3 --tuple-node-flattener    the configuration st1inch runs under
    arm C   --z3 --tuple-sym-flattener     separates z3 from the NODE flattener

PREDICTION, WRITTEN BEFORE THE RUN -- a prediction recorded afterwards is not a
prediction:

    A  reproduces F = 2, both SAT solves under 1 s
    B  returns F = 0, both claims solver-unknown, each solve in the 8-14 s band
       with no verdict -- the same signature as st1inch
    C  decides (F = 2), which would locate the defect in the node flattener
       rather than in z3

FALSIFIER: if B returns F = 2 with sub-second SAT, the encoder is EXONERATED, the
47 st1inch unknowns are a property of that contract's source, and the next step
becomes a source-level PoC. Until then a source-level hunt is not licensed.

WHY THE POSITIVE CONTROL IS NOT DEAD -- three PoCs this session produced
unreadable negatives because their discriminator had never been seen to fire.
Here the discriminator is "a solve returns SAT", and it has fired 464 times
across four benchmarks on the current disk, 15 of them on this very contract. Arm
A runs in the SAME session on the SAME build: if A does not reproduce F = 2, the
comparison is void and this script says so rather than reporting B and C.

Serial by construction: one ESBMC at a time, `--memlimit`, `timeout`, and a
`start_new_session` so a kill takes the whole group. Running two ESBMCs at once
has taken this machine down before.
"""
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

ESBMC = Path("/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
INPUTS = Path("/home/samson/workspace/esbmc/notes/coverage/inputs")

ARMS = [
    ("A_default", [], "POSITIVE CONTROL -- must reproduce F=2 or the run is void"),
    ("B_node_flattener", ["--z3", "--tuple-node-flattener"],
     "the configuration st1inch runs under"),
    ("C_sym_flattener", ["--z3", "--tuple-sym-flattener"],
     "separates z3 from the NODE flattener"),
]

BASE = ["--solidity-path-coverage", "--cov-report-json", "--solidity-max-tx", "1",
        "--path-cov-max-goals", "10000", "--memlimit", "8g"]

RE_SOLVE = re.compile(r"^Solving claim '([^']+)'")
RE_TIME = re.compile(r"Runtime decision procedure:\s*([0-9.]+)s")
RE_PASS = re.compile(r"PASSED|SUCCESSFUL")


def run_arm(outdir, sol, ast, contract, focus, flags, timeout_s):
    outdir.mkdir(parents=True, exist_ok=True)
    cmd = [str(ESBMC), str(ast), "--sol", str(sol), "--contract", contract,
           "--focus-function", focus] + BASE + flags
    (outdir / "cmd.txt").write_text(" ".join(cmd) + "\n")
    try:
        p = subprocess.run(cmd, cwd=outdir, capture_output=True, text=True,
                           timeout=timeout_s, start_new_session=True)
        out = p.stdout + p.stderr
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        rc = "TIMEOUT"
    (outdir / "run.log").write_text(out)
    return rc, out


def summarise(outdir, out):
    rep = outdir / "cov-report.json"
    paths = f = u = None
    reasons = {}
    if rep.exists():
        s = json.loads(rep.read_text()).get("summary", {})
        paths = s.get("paths_total")
        f = s.get("F_feasible_with_ce")
        u = s.get("U_undecided")
        reasons = s.get("U_reasons") or {}
    times = [float(m.group(1)) for m in RE_TIME.finditer(out)]
    return paths, f, u, reasons, times


def main(argv):
    contract = "Aqua"
    focus = "safeBalances"
    sol = INPUTS / "aqua__Aqua.flat.sol"
    ast = INPUTS / "aqua__Aqua.flat.sol.solast"
    root = Path(argv[1]) if len(argv) > 1 else Path("encoder_arms_out")
    timeout_s = 400

    if not sol.exists():
        sys.exit(f"missing input: {sol} -- an absent input is not a measured zero")
    if not ast.exists():
        sys.exit(f"missing AST: {ast}")

    rows = []
    for name, flags, role in ARMS:
        print(f"\n=== arm {name}  ({role})")
        print(f"    flags: {' '.join(flags) if flags else '(default backend)'}")
        rc, out = run_arm(root / name, sol, ast, contract, focus, flags, timeout_s)
        paths, f, u, reasons, times = summarise(root / name, out)
        band = ""
        if times:
            band = f"{min(times):.3f}-{max(times):.3f}s over {len(times)} solve(s)"
        print(f"    exit={rc}  paths={paths}  F={f}  U={u}")
        print(f"    solve times: {band or 'none recorded'}")
        nz = {k: v for k, v in reasons.items() if v}
        print(f"    U reasons (non-zero only): {nz or 'none'}")
        rows.append((name, rc, paths, f, u, nz, times))

    print("\n" + "=" * 74)
    ctrl = rows[0]
    if ctrl[3] != 2:
        print("** THE POSITIVE CONTROL DID NOT REPRODUCE **")
        print(f"   arm A gave F={ctrl[3]}, expected 2 (recorded: 2 SAT at "
              f"0.130-0.145s, 9 UNSAT, 0 no-verdict).")
        print("   The comparison is VOID: a difference between arms cannot be "
              "attributed to the flags")
        print("   when the control itself moved. Do not read arms B or C.")
        return 2

    print("positive control reproduced (F=2); arms B and C are readable\n")
    print(f"{'arm':<20}{'paths':>7}{'F':>5}{'U':>5}   solve-time band")
    for name, rc, paths, f, u, nz, times in rows:
        band = f"{min(times):.3f}-{max(times):.3f}s" if times else "-"
        print(f"{name:<20}{str(paths):>7}{str(f):>5}{str(u):>5}   {band}   {nz or ''}")

    b = rows[1]
    print()
    if b[3] == 0:
        print("ARM B REPRODUCES the st1inch signature: the encoder is implicated, "
              "and st1inch's 0 on")
        print("the branch-coverage gate is an artefact of the unblock "
              "configuration rather than of the")
        print("contract. Arm C then says whether it is z3 or the node flattener.")
    elif b[3] == 2:
        print("ARM B DOES NOT REPRODUCE: the encoder is EXONERATED by this "
              "vehicle. st1inch's 47")
        print("unknowns are a property of that contract's source, and a "
              "source-level PoC is now licensed")
        print("(it was not before). Note this exonerates the flags ON THIS "
              "CONTRACT only.")
    else:
        print(f"ARM B gave F={b[3]}, neither 0 nor 2 -- the vehicle does not "
              "answer the question cleanly;")
        print("report the number, do not pick a side.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
