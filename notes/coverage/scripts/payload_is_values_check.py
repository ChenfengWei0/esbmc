#!/usr/bin/env python3
"""P18: is an unevaluated expression still reaching final_state?

The check is on the PUBLISHED report rather than on a log line: the defect was
that a field contracted to hold values held an expression, so the test is
whether every published value PARSES AS AN INTEGER. Anything that does not is
printed in full -- a residue reported as a count would hide which field it was.

Also prints state_written_value_unavailable, because the fix does not delete the
fact, it MOVES it: a variable this path wrote whose value cannot be rendered must
still be visible, or a reader infers it was unchanged.
"""
import json
import subprocess
import sys
from pathlib import Path

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
POC = Path("/home/samson/workspace/esbmc/notes/coverage/poc")
OUT = Path("/tmp/claude-1000/-home-samson-workspace-paper-review/"
           "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/p18chk")


def run(cell, extra):
    wd = OUT / cell
    wd.mkdir(parents=True, exist_ok=True)
    sol = POC / "P18_Unchecked.sol"
    ast = POC / "P18_Unchecked.solast"
    cmd = [ESBMC, str(ast) if ast.exists() else str(sol), "--sol", str(sol),
           "--contract", "P18_Unchecked", "--solidity-path-coverage",
           "--cov-report-json", "--solidity-max-tx", "1",
           "--memlimit", "4g"] + extra
    p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                       timeout=180, start_new_session=True)
    (wd / "full.log").write_text(p.stdout + p.stderr)
    f = wd / "cov-report.json"
    return json.loads(f.read_text()) if f.exists() else None


def parses(v):
    try:
        int(v, 0)
        return True
    except (ValueError, TypeError):
        return False


for cell, extra in (("plain", []), ("divcheck", ["--div-by-zero-check"])):
    r = run(cell, extra)
    print(f"\n===== {cell}")
    if r is None:
        print("  NO REPORT")
        continue
    bad = 0
    for c in r.get("claims", []):
        if c.get("status") != "F":
            continue
        for field in ("final_state", "entry_storage", "inputs", "env"):
            for k, v in (c.get(field) or {}).items():
                if not parses(v):
                    bad += 1
                    print(f"  ** NOT A VALUE ** path {c.get('path_id')} "
                          f"{field}.{k} = {v!r}")
        un = c.get("state_written_value_unavailable")
        if un:
            print(f"     path {c.get('path_id')}: "
                  f"state_written_value_unavailable = {un}")
    print(f"  non-parsing published values: {bad}   "
          + ("PASS" if bad == 0 else "** FAIL **"))
