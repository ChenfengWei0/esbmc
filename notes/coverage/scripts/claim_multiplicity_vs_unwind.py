#!/usr/bin/env python3
"""Is the multiplicity the UNWIND BOUND? P21_ExternalCall, unwind varied.

P21 gives 20 VCCs for 5 path claims -- x4 -- and path coverage installs
`--unwind 4` for itself when none is given. If the multiplier tracks the bound,
the occurrences are the external-call RE-ENTRY model unfolding the unit body,
i.e. they are DIFFERENT queries about the same path identity, not a duplicated
instrumentation. That distinction decides the fix: different queries must all be
solved (a later one may witness what an earlier one could not), while a
duplicated claim would simply be waste.

Everything but --unwind is held fixed.
"""
import json
import re
import subprocess
from pathlib import Path

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
POC = Path("/home/samson/workspace/esbmc/notes/coverage/poc")
OUT = Path("/tmp/claude-1000/-home-samson-workspace-paper-review/"
           "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/dupunw")

RE_VCC = re.compile(r"^Generated (\d+) VCC\(s\)")
RE_SOLVE = re.compile(r"^Solving claim '([^']+)'")
RE_VP = re.compile(r"^Verdicts Preserved: (\d+)")

print(f"{'unwind':>8}{'paths':>7}{'VCC':>6}{'solves':>8}{'distinct':>10}"
      f"{'VP':>5}   F/U")
for unw in (1, 2, 3, 4, 6):
    wd = OUT / f"u{unw}"
    wd.mkdir(parents=True, exist_ok=True)
    cmd = [ESBMC, str(POC / "P21_ExternalCall.solast"),
           "--sol", str(POC / "P21_ExternalCall.sol"),
           "--contract", "P21_ExternalCall", "--solidity-path-coverage",
           "--cov-report-json", "--solidity-max-tx", "1", "--memlimit", "4g",
           "--unwind", str(unw)]
    try:
        p = subprocess.run(cmd, cwd=wd, capture_output=True, text=True,
                           timeout=300, start_new_session=True)
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
    (wd / "full.log").write_text(out)

    vcc = vp = None
    solves = []
    for ln in out.splitlines():
        s = ln.strip()
        m = RE_VCC.match(s)
        if m:
            vcc = int(m.group(1))
        m = RE_VP.match(s)
        if m:
            vp = int(m.group(1))
        m = RE_SOLVE.match(s)
        if m:
            solves.append(m.group(1))
    rep = wd / "cov-report.json"
    paths = f = u = None
    if rep.exists():
        sm = json.loads(rep.read_text()).get("summary", {})
        paths, f, u = (sm.get("paths_total"), sm.get("F_feasible_with_ce"),
                       sm.get("U_undecided"))
    print(f"{unw:>8}{str(paths):>7}{str(vcc):>6}{len(solves):>8}"
          f"{len(set(solves)):>10}{str(vp):>5}   {f}/{u}")
