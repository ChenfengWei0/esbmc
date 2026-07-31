#!/usr/bin/env python3
"""Does a different SMT ENCODING make st1inch's one hanging query decidable?

WHAT IS BEING TESTED, and why it is not a shot in the dark. `--focus-function
setFeeReceiver` on st1inch spends 0.095 s in symex, produces 875 assignments and
10 VCCs, and then one solver call never returns. Three backends fail three
different ways -- bitwuzla never returns, cvc5 raises std::bad_alloc at 4 GB with
0.000 s of decision-procedure time, z3 refuses at ENCODING TIME with
`datatype is not well-founded` and core dumps.

z3's message is about an algebraic datatype whose constructor mentions itself
with no base case. The hand-written control settled where it does NOT come from:
`struct Node { Node[] kids; }` -- a genuinely self-referential Solidity struct --
is accepted by all three backends (notes/coverage/poc/D05_RecursiveStruct.sol).
So the recursion is introduced by ESBMC's own tuple/array encoding, not by the
source. That makes the encoder a variable worth changing, which is the one thing
none of the earlier attempts changed.

Every cell is capped at 100 s and 4 GB and runs in its own process group. The
cap is deliberately BELOW the per-claim budget: the question here is "does this
encoding decide the query quickly", and a cell that needs longer than the
baseline has not answered it.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
ST = Path("/home/samson/workspace/esbmc/notes/coverage/inputs/"
          "st1inch__St1inch.flat.sol")
OUT = Path("/tmp/claude-1000/-home-samson-workspace-paper-review/"
           "e0047351-2714-4000-919d-058ca8af97c5/scratchpad/st_enc")
CAP = 100

CONFIGS = [
    ("z3-baseline", ["--z3"]),
    ("z3-symflat", ["--z3", "--tuple-sym-flattener"]),
    ("z3-nodeflat", ["--z3", "--tuple-node-flattener"]),
    ("bw-symflat", ["--bitwuzla", "--tuple-sym-flattener"]),
    ("bw-arrflat", ["--bitwuzla", "--array-flattener"]),
    ("cvc5-native", ["--cvc5", "--cvc5-native-tuples"]),
]

MARKS = ("Complete Paths", "Path Status", "Path Coverage", "ERROR",
         "Runtime decision procedure", "Solving with solver",
         "Encoding to solver time", "Symex completed", "Generated ")


def sh(cmd, cwd, timeout):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, cwd=cwd, start_new_session=True)
    t0 = time.time()
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out, time.time() - t0
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, _ = p.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            out = ""
        return -1, out, time.time() - t0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for tag, extra in CONFIGS:
        d = OUT / tag
        d.mkdir(exist_ok=True)
        cmd = [ESBMC, str(ST) + ".solast", "--sol", str(ST),
               "--solidity-path-coverage", "--solidity-max-tx", "1",
               "--cov-report-json", "--path-cov-max-goals", "10000",
               "--memlimit", "4g", "--contract", "St1inch",
               "--focus-function", "setFeeReceiver"] + extra
        rc, out, wall = sh(cmd, str(d), CAP)
        (d / "run.log").write_text(out)
        print(f"\n===== {tag}  {' '.join(extra)}  "
              f"rc={rc}  wall={wall:.1f}s =====", flush=True)
        if rc == -1:
            print(f"  [KILLED at {CAP}s -- never returned]")
        seen = set()
        for ln in out.splitlines():
            if any(m in ln for m in MARKS):
                s = ln.strip()
                if s in seen:
                    continue
                seen.add(s)
                print("  " + s, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
