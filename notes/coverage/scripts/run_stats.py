#!/usr/bin/env python3
"""One run log in, its statistics and its DUPLICATE-SOLVE table out.

WHY IT EXISTS. `solver_arms/st1inch/z3+node-flat/run.log` showed five paths
producing TEN solves, with `path:13` deciding in 0.011 s on its third solve and
returning `out of memory` on its eighth. The tool itself calls that a defect:

    Verdicts Preserved: 2 — a claim already DECIDED whose later solve returned no
    verdict kept its decision. Non-zero also means the same claim key was solved
    more than once, which is a SEPARATE DEFECT

That was found by reading a 33 KB log by hand. The next logs are 300-700 KB,
which is why this exists: it reads the WHOLE file and prints the classification,
rather than anyone reaching for a pattern and reporting what it caught.

WHAT IT PRINTS, and every line of it is a quote or a count of quotes:
  * the solver actually used, including an auto-selection line if there is one --
    the FIRST thing to check when comparing two cells, because a run that
    auto-selects has not held the backend fixed and a comparison across it is
    confounded. (Measured: an `exp_chain_sweep` PoC auto-selected bitwuzla while
    the st1inch run it was compared against used `--z3 --tuple-node-flattener`.)
  * symex / slicing / VCC counts
  * the SOLVE SEQUENCE in order: claim, seconds, verdict
  * per claim key: how many times it was solved and whether the outcomes AGREE
  * the truncated-loop list, the degradation summary, and the coverage block

Usage: python3 run_stats.py <run.log> [more.log ...]
"""
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

RE_AUTOSEL = re.compile(r"auto-selecting '([^']+)' as SMT backend")
RE_SOLVING_WITH = re.compile(r"Solving with solver (.+)$")
RE_CLAIM = re.compile(r"Solving claim '([^']+)' with solver")
RE_TIME = re.compile(r"Runtime decision procedure:\s*([0-9.]+)s")
RE_UNKNOWN = re.compile(r"z3 returned `unknown` \(reason: ([^)]*)\)")
RE_PASS = re.compile(r"✓ PASSED: '([^']+)'")
RE_FAIL = re.compile(r"✗ FAILED: '([^']+)'")
RE_SYMEX = re.compile(r"Symex completed in:\s*([0-9.]+)s \((\d+) assignments\)")
RE_VCC = re.compile(r"Generated (\d+) VCC\(s\), (\d+) remaining after "
                    r"simplification \((\d+) assignments\)")
RE_INSTR = re.compile(
    r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")
KEEP_PREFIX = ("Path Status:", "U Reasons:", "Path Coverage:", "Complete Paths",
               "Reached :", "Path Exits:", "Verdicts Preserved:",
               "Report Completeness:", "Claim Budget:")


def stats(path):
    text = Path(path).read_text(errors="replace")
    lines = text.splitlines()

    auto = None
    solvers = Counter()
    symex = vcc = instr = None
    truncated = []
    degradation = None
    summary = []

    # The solve sequence is assembled by walking IN ORDER: a `Solving claim`
    # line opens a solve, and the next time/verdict lines close it. Anything
    # else would lose the pairing, and the pairing IS the finding.
    seq = []
    cur = None
    for ln in lines:
        s = ln.strip()
        m = RE_AUTOSEL.search(s)
        if m:
            auto = m.group(1)
        m = RE_SOLVING_WITH.search(s)
        if m:
            solvers[m.group(1)] += 1
        m = RE_SYMEX.search(s)
        if m:
            symex = (float(m.group(1)), int(m.group(2)))
        m = RE_VCC.search(s)
        if m:
            vcc = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = RE_INSTR.search(s)
        if m and instr is None:
            instr = (int(m.group(1)), int(m.group(2)))
        m = RE_CLAIM.search(s)
        if m:
            if cur is not None:
                seq.append(cur)
            cur = {"claim": m.group(1), "time": None, "verdict": None}
            continue
        if cur is not None:
            m = RE_UNKNOWN.search(s)
            if m:
                cur["verdict"] = f"unknown ({m.group(1)})"
            m = RE_TIME.search(s)
            if m:
                cur["time"] = float(m.group(1))
            m = RE_PASS.search(s)
            if m and cur["verdict"] is None:
                cur["verdict"] = "PASSED"
            m = RE_FAIL.search(s)
            if m and cur["verdict"] is None:
                cur["verdict"] = "FAILED"
        if "loop" in s and "function" in s and s.startswith("WARNING:"):
            truncated.append(s)
        if "degradation summary" in s:
            degradation = s
        if any(s.startswith(p) for p in KEEP_PREFIX):
            summary.append(s)
    if cur is not None:
        seq.append(cur)
    return {"auto": auto, "solvers": solvers, "symex": symex, "vcc": vcc,
            "instr": instr, "seq": seq, "truncated": truncated,
            "degradation": degradation, "summary": summary,
            "lines": len(lines)}


def report(path, st):
    print(f"## {path}   ({st['lines']} log line(s))\n")
    print("  backend: "
          + (f"AUTO-SELECTED '{st['auto']}'  <-- the backend was NOT held fixed; "
             f"any comparison across this cell is confounded"
             if st["auto"] else "no auto-selection line (an explicit flag, or "
                                "the default with nothing to say)"))
    for s, n in st["solvers"].most_common():
        print(f"           {n:>4} solve(s) with {s}")
    if st["instr"]:
        print(f"  instrumented: {st['instr'][0]} path(s) across "
              f"{st['instr'][1]} unit(s)")
    if st["symex"]:
        print(f"  symex       : {st['symex'][0]}s, {st['symex'][1]} assignments")
    if st["vcc"]:
        print(f"  VCCs        : {st['vcc'][0]} generated, {st['vcc'][1]} after "
              f"simplification, {st['vcc'][2]} assignments")
        if st["instr"] and st["instr"][0]:
            r = st["vcc"][0] / st["instr"][0]
            print(f"                => {r:.2f} VCC per instrumented path"
                  + ("   <-- NOT 1:1" if abs(r - 1.0) > 1e-9 else ""))

    print(f"\n  solve sequence ({len(st['seq'])} solve(s)):")
    for i, e in enumerate(st["seq"], 1):
        t = f"{e['time']:.3f}s" if e["time"] is not None else "no time"
        print(f"    {i:>3}  {e['claim']:<44} {t:>10}  "
              f"{e['verdict'] or 'no verdict line'}")

    by_key = OrderedDict()
    for e in st["seq"]:
        by_key.setdefault(e["claim"], []).append(e)
    dup = {k: v for k, v in by_key.items() if len(v) > 1}
    print(f"\n  distinct claim keys: {len(by_key)};  solved more than once: "
          f"{len(dup)}")
    for k, v in dup.items():
        verdicts = {e["verdict"] for e in v}
        times = [e["time"] for e in v if e["time"] is not None]
        agree = len(verdicts) == 1
        print(f"    {k}: {len(v)} solve(s), "
              + ("outcomes AGREE" if agree else "⛔ OUTCOMES DISAGREE")
              + (f", times {min(times):.3f}-{max(times):.3f}s" if times else ""))
        if not agree:
            for e in v:
                print(f"         {e['time']}s  {e['verdict']}")

    if st["truncated"]:
        print(f"\n  loops truncated at the unwind bound "
              f"({len(st['truncated'])} line(s)):")
        for s in st["truncated"]:
            print(f"    {s}")
    if st["degradation"]:
        print(f"\n  {st['degradation']}")
    if st["summary"]:
        print("\n  coverage block:")
        for s in st["summary"]:
            print(f"    {s}")
    print()


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    for p in argv[1:]:
        f = Path(p)
        if not f.exists():
            print(f"MISSING {p}\n")
            continue
        report(p, stats(f))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
