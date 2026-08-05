#!/usr/bin/env python3
"""TWO-WAY FIXTURE for the `extcall.<name>` certification coordinate.

WHAT IT GUARDS. Stage 2 can bound a quantity the HARNESS chose inside the
execution -- an external call's success bit is the usual one -- by naming it
`extcall.<name>` in the certification box. Two things about that mechanism are
invisible from outside unless they are measured:

  1. THE BOUND MUST REACH THE FORMULA. It is emitted as an ASSUME placed
     immediately AFTER the nondeterministic assignment, not at unit entry,
     because an entry-placed assume constrains an incarnation the assignment
     then overwrites -- a bound that binds nothing while looking like a bound.
     Cases A and B differ in that one bit and MUST produce different verdicts.
     If they ever agree, the placement has regressed.
  2. THE REFUSING BRANCH MUST STILL REFUSE. Case D names a local that does not
     exist and must come back as a refusal, not as a verdict.

WHY THIS SOURCE. notes/coverage/poc/B5_ExtcallInCallee.sol holds two units that
differ in exactly one thing: `probeInline` writes the assembly block in the
unit's own body, `probeLib` puts it one frame down in a library. farming/deposit
has probeLib's shape -- its low-level call is inside SafeERC20 -- so a mechanism
that only worked on probeInline would look identical from outside on any fixture
that did not carry both.

MEASURED, all eight cases matching the expectation written above them, on the
binary built from goto_coverage.cpp carrying the resolver and the reachability
restriction. Path enc=7 depth=2 is the arm on which the call SUCCEEDS; its
sibling enc=6 carries success=0, which is what makes case B vacuous rather than
merely refuted.

Usage:  python3 notes/coverage/poc/B5_extcall_coord_fixture.py
Exit 0 iff every case matches. One esbmc at a time, each with --memlimit.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
ESBMC = os.path.join(REPO, "build/src/esbmc/esbmc")
SRC = os.path.join(HERE, "B5_ExtcallInCallee.sol")
AST = os.path.join(HERE, "B5_ExtcallInCallee.solast")
WORK = "/tmp/b5_extcall_coord_fixture"
U256 = str((1 << 256) - 1)

# ⛔ THE VERDICT IS THIS EXACT PREFIX AND NOTHING ELSE. Matching the bare
# substring "RESULT:" also matches the tool's own banner ("THE RESULT OF THIS
# RUN IS THE `RESULT:` LINE"), which reported a correct REFUTED as an
# unrecognised verdict the first time this was written.
RES = "--path-cov-certify: RESULT:"
REF = "REFUSING THE QUERY"


def cases():
    for unit in ("probeLib", "probeInline"):
        base = [{"name": "amount", "lo": "0", "hi": U256},
                {"name": "msg.value", "lo": "0", "hi": "0"}]
        yield ("%s_A_success1" % unit, unit,
               base + [{"name": "extcall.success", "lo": "1", "hi": "1"}],
               "CERTIFIED")
        yield ("%s_B_success0" % unit, unit,
               base + [{"name": "extcall.success", "lo": "0", "hi": "0"}],
               "VACUOUS")
        yield ("%s_C_free" % unit, unit, list(base), "REFUTED")
        yield ("%s_D_nosuch" % unit, unit,
               base + [{"name": "extcall.zzz_no_such_local",
                        "lo": "0", "hi": "0"}],
               "REFUSED")


def outcome(log):
    """(token, the line it came from). Three states, never two."""
    for ln in log.splitlines():
        if REF in ln:
            return "REFUSED", ln.strip()
    for ln in log.splitlines():
        i = ln.find(RES)
        if i >= 0:
            return ln[i + len(RES):].strip().split()[0].strip("—-").strip(), \
                ln.strip()
    return "NO-RESULT-LINE", ""


def main():
    if not os.path.exists(ESBMC):
        sys.exit("no esbmc at %s -- build it first" % ESBMC)
    os.makedirs(WORK, exist_ok=True)
    bad = []
    n = 0
    for name, unit, box, expect in cases():
        n += 1
        w = os.path.join(WORK, name)
        os.makedirs(w, exist_ok=True)
        sp = os.path.join(w, "cert.json")
        with open(sp, "w") as f:
            json.dump({"unit": unit, "enc": 7, "depth": 2, "ce": {},
                       "box": box}, f)
        cmd = [ESBMC, AST, "--sol", SRC, "--contract", "B5_ExtcallInCallee",
               "--solidity-path-coverage", "--solidity-max-tx", "1",
               "--focus-function", unit, "--memlimit", "8g",
               "--path-cov-certify", sp, "--cov-report-json"]
        p = subprocess.run(cmd, cwd=w, capture_output=True, text=True,
                           timeout=300)
        log = p.stdout + p.stderr
        with open(os.path.join(w, "run.log"), "w") as f:
            f.write(log)
        got, line = outcome(log)
        ok = got == expect
        if not ok:
            bad.append(name)
        print("%s %-26s unit=%-12s expect=%-10s got=%-14s rc=%d"
              % ("OK  " if ok else "*** ", name, unit, expect, got,
                 p.returncode))
        if line:
            print("      " + line[:400])
    print("cases: %d ; mismatching the pre-written expectation: %d %s"
          % (n, len(bad), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
