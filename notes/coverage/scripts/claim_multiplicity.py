#!/usr/bin/env python3
"""Is one claim key instrumented TWICE everywhere, or only on st1inch?

st1inch generates 10 VCCs for 5 paths under one claim key each, so every path
claim is solved twice and the second solve's non-verdict used to erase the
first's proof. Before reading 9273 lines of the instrumentation pass, ask the
cheap question: does a small contract do it too?

The discriminator is VCC count against path count, both of which the run prints
itself, plus the `Verdicts Preserved` counter -- non-zero means a claim key
reached the solve loop more than once AND had already been decided.

Serial, one esbmc at a time, seconds per contract.
"""
import json
import re
import subprocess
import time
from pathlib import Path

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
POC = Path("/home/samson/workspace/esbmc/notes/coverage/poc")
OUT = Path("/tmp/claude-1000/-home-samson-workspace-paper-review/"
           "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/dup")

# (stem, contract, focus-or-None). A mix of shapes: trivial setter, arithmetic,
# multiple units, a modifier, an external call.
CASES = [
    ("D16_OnlyByOverflow", "D16_OnlyByOverflow", None),
    ("D17_ExpChain", "D17_ExpChainLong", "setFeeReceiver"),
    ("D17_ExpChain", "D17_ExpChainShort", "setFeeReceiver"),
    ("D09_ValueGate", "D09_ValueGate", None),
    ("D10_WrapNotPanic", "D10_WrapNotPanic", None),
    ("Tiny2", "Tiny2", None),
    ("P17_Modifier", "P17_Modifier", None),
    ("P21_ExternalCall", "P21_ExternalCall", None),
    ("D01_StringState", "D01_StringState", "setFeeReceiver"),
]

RE_VCC = re.compile(r"^Generated (\d+) VCC\(s\), (\d+) remaining")
RE_VP = re.compile(r"^Verdicts Preserved: (\d+)")
RE_SOLVE = re.compile(r"^Solving claim '([^']+)'")

print(f"{'contract':<24}{'focus':<16}{'paths':>6}{'VCC':>6}{'solves':>8}"
      f"{'distinct':>10}{'VP':>5}  verdict")
for stem, contract, focus in CASES:
    sol = POC / f"{stem}.sol"
    if not sol.exists():
        print(f"{contract:<24}{'-':<16}   SOURCE MISSING")
        continue
    ast = POC / f"{stem}.solast"
    wd = OUT / f"{contract}"
    wd.mkdir(parents=True, exist_ok=True)
    cmd = [ESBMC, str(ast) if ast.exists() else str(sol), "--sol", str(sol),
           "--contract", contract, "--solidity-path-coverage",
           "--cov-report-json", "--solidity-max-tx", "1", "--memlimit", "4g"]
    if focus:
        cmd += ["--focus-function", focus]
    try:
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                           timeout=180, start_new_session=True)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
    (wd / "full.log").write_text(out)

    vcc = vp = None
    solves = []
    for ln in out.splitlines():
        m = RE_VCC.match(ln.strip())
        if m:
            vcc = int(m.group(1))
        m = RE_VP.match(ln.strip())
        if m:
            vp = int(m.group(1))
        m = RE_SOLVE.match(ln.strip())
        if m:
            solves.append(m.group(1))
    rep = wd / "cov-report.json"
    paths = None
    if rep.exists():
        paths = json.loads(rep.read_text()).get("summary", {}).get("paths_total")
    n_sol, n_dis = len(solves), len(set(solves))
    verdict = ("DUPLICATED" if (n_sol > n_dis and n_dis > 0)
               else ("ok" if n_dis else "?"))
    print(f"{contract:<24}{str(focus or '-'):<16}{str(paths):>6}{str(vcc):>6}"
          f"{n_sol:>8}{n_dis:>10}{str(vp):>5}  {verdict}")
