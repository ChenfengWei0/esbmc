#!/usr/bin/env python3
"""Two certification queries that differ ONLY in the msg.sender bound.

The path here DOES depend on msg.sender (`require(msg.sender != BANNED)`), so:
  A: sender pinned to BANNED   -> every input in the box reverts  -> SUCCESSFUL
  B: sender pinned to 0        -> no input in the box reverts     -> FAILED
if the bound binds. Identical verdicts mean it does not.

The verdict is read as a WHOLE LINE, never as a substring: ESBMC opens every
bounded run with a warning that CONTAINS "VERIFICATION SUCCESSFUL", and taking
that as the answer is how this project's certification gate was once
unconditionally green.
"""
import os
import subprocess

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
HERE = os.path.dirname(os.path.abspath(__file__))


def verdict(log):
    seen = "UNKNOWN"
    for line in log.splitlines():
        s = line.strip()
        if s == "VERIFICATION SUCCESSFUL":
            seen = "SUCCESSFUL"
        elif s == "VERIFICATION FAILED":
            seen = "FAILED"
    return seen


for name, spec in [("A sender==BANNED(255)", "certA.json"),
                   ("B sender==0", "certB.json")]:
    p = subprocess.run(
        [ESBMC, "--sol", "contract.sol", "--contract", "Gate3",
         "--solidity-path-coverage", "--path-cov-certify", spec,
         "--solidity-max-tx", "1", "--result-only", "--memlimit", "20g"],
        cwd=HERE, capture_output=True, text=True, timeout=120)
    log = p.stdout + p.stderr
    with open(os.path.join(HERE, spec + ".log"), "w") as f:
        f.write(log)
    bounds = [l.strip() for l in log.splitlines()
              if "input bound(s) at entry" in l]
    print(f"{name}: verdict={verdict(log)} exit={p.returncode}")
    for b in bounds:
        print("    " + b[:150])
