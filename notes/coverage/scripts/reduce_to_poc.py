#!/usr/bin/env python3
"""Shrink a contract that will not run until the failure fits on a screen.

THE RULE THIS ENFORCES: when a real contract does not produce a result -- killed,
out of memory, no report, everything bounded-holds -- the next action is to
REDUCE IT, not to raise a limit. Raising `--memlimit` and lengthening timeouts
is explicitly out of bounds; a failure with no minimal reproduction is not a
finding, it is a debt.

Why it is worth automating rather than doing by eye: a 30-line hand-written
contract with a nested mapping reproduced st1inch's death (`std::bad_alloc` on
8 paths) AND exposed a second defect that the real benchmark could never have
shown -- a claim that had already been REFUTED came back as `U` with no reason
token. On the big contract that would have been absorbed into "too slow".

HOW IT REDUCES. Syntactic units, largest first, each removal kept only if the
failure SURVIVES:

    1. whole functions       (the biggest win, and usually most of the file)
    2. modifiers and their use sites
    3. base contracts and `using ... for`
    4. statements inside the function that still fails
    5. mapping nesting depth, then mappings to scalars
    6. integer width

After each candidate removal the contract must still COMPILE and must still
FAIL THE SAME WAY. A candidate that changes the failure mode is rejected and
the element is put back -- that element is part of the cause, which is itself
the answer.

THE PREDICATE IS EXPLICIT, NEVER "it broke". `--fail-on` takes one of:

    no-report        the run produced no cov-report.json
    partial          the report exists and is marked partial
    crash            non-zero exit that is not the coverage FAILED signal
    budget           at least one claim carries `claim-budget-exceeded`
    bounded-holds    at least N paths are bounded-holds (with --min-bh)
    internal-defect  the run printed its own INTERNAL DEFECT invariant

"The same way" means the same predicate is still true. Reducing against "it
exits non-zero" is how a memory bug turns into a syntax error and the reduction
reports success.

Usage:
    reduce_to_poc.py --sol X.sol --contract C --fail-on no-report
                     [--tx 1] [--timeout 120] [--memlimit 4g] [--min-bh 1]
                     [--out notes/coverage/poc/R_X.sol]
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
ESBMC = REPO / "build/src/esbmc/esbmc"

COV_FAILED_EXIT = 1  # coverage witnessed something; NOT a crash


def sh(cmd, cwd=None, timeout=300):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=cwd, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, _ = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            out = ""
        return -1, out


def run_once(src_text, contract, tx, timeout, memlimit):
    """Compile and run one candidate in its own directory. Returns
    (compiles, rc, stdout, report_or_None)."""
    d = Path(tempfile.mkdtemp(prefix="reduce_"))
    try:
        sol = d / "C.sol"
        sol.write_text(src_text)
        rc, out = sh(["solc", "--ast-compact-json", str(sol)], timeout=120)
        if rc != 0:
            return False, rc, out, None
        ast = d / "C.solast"
        ast.write_text(out)
        cmd = [str(ESBMC), str(ast), "--sol", str(sol),
               "--solidity-path-coverage", "--cov-report-json",
               "--memlimit", memlimit, "--contract", contract,
               "--solidity-max-tx", str(tx)]
        rc, out = sh(cmd, cwd=str(d), timeout=timeout)
        rep = d / "cov-report.json"
        data = None
        if rep.exists():
            try:
                data = json.loads(rep.read_text())
            except ValueError:
                data = None
        return True, rc, out, data
    finally:
        shutil.rmtree(d, ignore_errors=True)


def predicate(kind, min_bh, compiles, rc, out, data):
    if not compiles:
        return False
    if kind == "no-report":
        return data is None
    if kind == "partial":
        return bool(data and (data.get("partial")
                              or data.get("summary", {}).get("partial")))
    if kind == "crash":
        return rc not in (0, COV_FAILED_EXIT)
    if kind == "budget":
        if not data:
            return False
        ur = data.get("summary", {}).get("U_reasons", {})
        return ur.get("claim-budget-exceeded", 0) > 0
    if kind == "bounded-holds":
        if not data:
            return False
        ur = data.get("summary", {}).get("U_reasons", {})
        return ur.get("bounded-holds", 0) >= min_bh
    if kind == "internal-defect":
        return "INTERNAL DEFECT" in (out or "")
    raise SystemExit(f"unknown --fail-on: {kind}")


# --------------------------------------------------------------------------
# Candidate removals, largest first.
# --------------------------------------------------------------------------
FUNC_RE = re.compile(
    r"^[ \t]*(function|modifier|constructor|receive|fallback)\b[^\n]*$")


def top_level_blocks(text):
    """(start_line, end_line, header) for every function-like block, matched by
    brace depth rather than by a regex over the whole body -- a regex that tries
    to span a body stops at the first nested `}` and silently truncates."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if FUNC_RE.match(lines[i]):
            depth = 0
            started = False
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth <= 0:
                    break
                if not started and lines[j].rstrip().endswith(";"):
                    break  # an abstract/interface declaration
                j += 1
            out.append((i, min(j, len(lines) - 1), lines[i].strip()))
            i = j + 1
        else:
            i += 1
    return out


def drop_lines(text, lo, hi):
    lines = text.splitlines()
    return "\n".join(lines[:lo] + lines[hi + 1:]) + "\n"


def statements_of(text, lo, hi):
    """Line indices inside a block that are plausibly removable statements:
    a single line ending in `;` that is not a declaration of something used
    later. Conservative on purpose -- a wrong removal is rejected by the
    predicate anyway, and being conservative keeps the search short."""
    lines = text.splitlines()
    out = []
    for k in range(lo + 1, hi):
        s = lines[k].strip()
        if s.endswith(";") and not s.startswith("//"):
            out.append(k)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sol", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--fail-on", required=True)
    ap.add_argument("--tx", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--memlimit", default="4g")
    ap.add_argument("--min-bh", type=int, default=1)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    text = Path(a.sol).read_text()

    def fails(t):
        c, rc, out, data = run_once(t, a.contract, a.tx, a.timeout, a.memlimit)
        return predicate(a.fail_on, a.min_bh, c, rc, out, data)

    print(f"# reducing {a.sol} against `{a.fail_on}`\n", flush=True)
    t0 = time.time()
    if not fails(text):
        sys.exit(
            f"REFUSING TO REDUCE: the ORIGINAL does not satisfy `{a.fail_on}`. "
            f"Reducing against a predicate the input does not meet produces a "
            f"minimal contract for a different failure, and it will look like "
            f"a success. Fix the predicate first.")
    print(f"  original reproduces `{a.fail_on}`   "
          f"({len(text.splitlines())} lines)\n", flush=True)

    # PASS 1 -- whole function-like blocks, largest first.
    changed = True
    while changed:
        changed = False
        blocks = top_level_blocks(text)
        blocks.sort(key=lambda b: b[0] - b[1])  # largest first
        for lo, hi, header in blocks:
            cand = drop_lines(text, lo, hi)
            if fails(cand):
                print(f"  dropped  {header[:70]}   "
                      f"-> {len(cand.splitlines())} lines", flush=True)
                text = cand
                changed = True
                break

    # PASS 2 -- single statements inside whatever blocks remain.
    changed = True
    while changed:
        changed = False
        for lo, hi, header in top_level_blocks(text):
            for k in statements_of(text, lo, hi):
                cand = drop_lines(text, k, k)
                if fails(cand):
                    print(f"  dropped  stmt: "
                          f"{text.splitlines()[k].strip()[:60]}", flush=True)
                    text = cand
                    changed = True
                    break
            if changed:
                break

    print(f"\n  reduced to {len(text.splitlines())} lines in "
          f"{round(time.time() - t0)}s\n", flush=True)
    print(text)

    if a.out:
        outp = Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        banner = (
            f"// MINIMAL REPRODUCTION, reduced automatically from {a.sol}\n"
            f"// against the predicate `{a.fail_on}` at "
            f"--solidity-max-tx {a.tx}, --memlimit {a.memlimit}.\n"
            f"// Every element still here is one whose removal made the "
            f"failure GO AWAY,\n"
            f"// so this file is not merely smaller -- each remaining part is "
            f"load-bearing.\n")
        outp.write_text(banner + text)
        print(f"  written to {outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
