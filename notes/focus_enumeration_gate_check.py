#!/usr/bin/env python3
"""T2.0 on a REAL contract: does --focus-function change the enumeration?

The toy fixture passed the gate (identical content-addressed key sets, only the
status differing). The T2 table then showed FarmingPool.exit reporting a
contract-wide instrumented count of 1004 while every other FarmingPool unit
reported 9536, under exclude lists that the collector's own JSON shows are
byte-identical. That is the gate's premise failing on real input, or it is
something about how the run was made -- and the difference has to be SEEN, not
inferred.

This runs the same contract under several --focus-function targets with one
fixed, identical flag set, kills each run shortly after instrumentation, and
prints only the two lines that answer the question.
"""
import json
import re
import subprocess
import sys

import os

UNWIND = os.environ.get("GATE_UNWIND", "")

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
FLAT = ("/home/samson/workspace/esbmc/notes/coverage/inputs/"
        "farming__FarmingPool.flat.sol")

with open("/home/samson/workspace/esbmc/notes/coverage/data/"
          "esbmc_farming.json") as f:
    rep = json.load(f)
ex = []
for fn in rep["per_function"]["functions"]:
    if fn["function"] == "claim" and fn["contract"] == "FarmingPool":
        toks = fn["commandUsed"].split()
        ex = [toks[i + 1] for i, t in enumerate(toks)
              if t == "--coverage-exclude-contract"]
# AN EMPTY EXCLUDE LIST IS THE WIDEST SCOPE, NOT THE RECORDED ONE.
# `ex` stays [] whenever esbmc_farming.json has no per_function entry for
# FarmingPool.claim -- a rename, an attribution change, or a re-collection whose
# Pair-2 enumeration differs. Every run below would then be launched with ZERO
# --coverage-exclude-contract flags, putting the whole flat including the entire
# OpenZeppelin tree in scope, and the instrumented counts this script exists to
# COMPARE would come from a different configuration than the one it claims. The
# `0` was printed, but framed as informational.
if not ex:
    sys.exit("no exclude list recovered for FarmingPool.claim from "
             "esbmc_farming.json -- an empty list is the WIDEST scope, not the "
             "recorded one, and the comparison below would be against a "
             "different configuration than the one it names")
print(f"[excludes] {len(ex)} (taken from FarmingPool.claim's recorded command)")
# The unwind configuration changes the numbers this script prints and was
# recorded nowhere, so two runs were distinguishable only by whoever remembered
# which environment variable was set.
print(f"[unwind]   {UNWIND or '(unset; no --unwind passed)'}")

INSTR = re.compile(r"instrumented (\d+) complete path\(s\) across (\d+) unit")
DIST = re.compile(r"path distribution — .*")
EXPAND = re.compile(r"expanded (\d+) internal call\(s\)")
NOTEXP = re.compile(r"(\d+) call site\(s\) are deeper than the call depth bound")

for target in sys.argv[1:]:
    cmd = [ESBMC, FLAT + ".solast", "--sol", FLAT,
           "--contract", "FarmingPool", "--focus-function", target]
    for e in ex:
        cmd += ["--coverage-exclude-contract", e]
    cmd += ["--solidity-path-coverage", "--solidity-max-tx", "1",
            "--memlimit", "20g", "--result-only"]
    # INTERVENTION KNOB. The instrumentation lines say the gap between focus
    # targets is 168-vs-176 expanded internal calls and 38-vs-42 left
    # unexpanded at the call-depth bound. If that is the mechanism, moving the
    # bound has to move the numbers; if the numbers do not move, the mechanism
    # is something else and the reading was an inference.
    if UNWIND:
        cmd += ["--unwind", UNWIND]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        log = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        def _t(b):
            if b is None:
                return ""
            return b.decode(errors="replace") if isinstance(b, bytes) else b
        log = _t(e.stdout) + _t(e.stderr)
    m, d = INSTR.search(log), DIST.search(log)
    ee, ne = EXPAND.search(log), NOTEXP.search(log)
    print(f"--- {target}")
    print(f"    instrumented = {m.group(1) if m else '(line absent)'} "
          f"across {m.group(2) if m else '?'} unit(s)")
    print(f"    expanded internal calls = {ee.group(1) if ee else '-'}; "
          f"NOT expanded (depth bound) = {ne.group(1) if ne else '-'}")
    print(f"    {d.group(0) if d else '(no distribution line)'}")
