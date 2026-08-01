#!/usr/bin/env python3
"""Does stage 1 read the PREVIOUS run's cov-report.json when its own run dies?

THE DEFECT, as it stood. `solidity_path_generalise.enumerate_paths` ran ESBMC and
then asked `os.path.exists("cov-report.json")`. Two facts make that a live hole
rather than a tidiness point:

  * `run()` does NOT raise on a timeout -- by design, it returns the partial
    output with a marker so callers read it as UNKNOWN;
  * `--workdir` is explicitly reusable, which is exactly why the CERTIFY branch
    of the same file already deletes the file first ("the previous shrink round
    left one right here").

So a run that timed out, aborted, or was refused at instrumentation time fell
through to whatever report was already in the directory.

A CROSS-UNIT stale report was already caught -- the `claim_unit(c) == unit`
filter empties and the wiring check exits loudly. The uncaught case is the SAME
unit re-run in the same workdir under different flags: another `--max-tx`,
`--focus` toggled, a rebuilt binary. The filter matches, and the old
`(enc, depth, ce)` triples flow into the bracket, the refine rounds and every
certification query. Nothing downstream can notice; an `enc` is just an integer.

WHY THIS TEST CAN EXIST AT ALL, AND WHY IT IS A REAL MUST-FLIP. The two cells run
the SAME production function. `run` is stubbed so no esbmc is needed, and the
only thing that differs between them is whether the removal happens:

    cell FIXED     os.remove works        -> the stale file is gone, the run
                                             writes nothing, enumerate_paths
                                             raises SystemExit naming the missing
                                             report
    cell DEFECTIVE os.remove neutered     -> the stale file survives, and the
                                             OLD unit's paths are returned as
                                             though this run had produced them

Neutering `os.remove` reproduces the pre-fix code path exactly, because the
removal IS the entire difference between the two versions. That keeps this from
being a test that asserts what the current code happens to do.

Usage: python3 stale_report_check.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))

import solidity_path_generalise as g  # noqa: E402

# A report from an EARLIER run of the SAME unit -- the case the unit filter and
# the wiring check do not catch. `enc` 999 is chosen so it cannot be confused
# with anything a real run of this fixture would produce.
STALE = {
    "claims": [
        {"condition": "withdraw:path:999", "status": "F",
         "path_id": 999, "path_depth": 3, "path_function": "withdraw",
         "inputs": {"amt": "7"}, "env": {}, "entry_storage": {}},
    ]
}


def cell(name, neuter_remove):
    """Run the real enumerate_paths with a stale report already in the workdir
    and an ESBMC that produces nothing."""
    real_remove = os.remove
    with tempfile.TemporaryDirectory() as wd:
        (Path(wd) / "cov-report.json").write_text(json.dumps(STALE))

        # The stub stands in for an ESBMC that died: it writes NO report and
        # returns the same marker `run` produces on a timeout.
        def dead_run(*a, **k):
            return "\n[run] TIMEOUT after 900s: <stubbed>\n"

        g.run = dead_run
        if neuter_remove:
            os.remove = lambda p: None
        try:
            out = g.enumerate_paths("esbmc", "x.sol", "C", "withdraw", 1, 900,
                                    wd)
            paths = out[0]
            verdict = (f"RETURNED {len(paths)} path(s) from the stale report: "
                       + ", ".join(f"enc={e}" for e, _, _ in paths))
            stale_used = True
        except SystemExit as e:
            first = str(e).strip().splitlines()[0]
            verdict = f"REFUSED: {first}"
            stale_used = False
        finally:
            os.remove = real_remove
    print(f"  [{name}] {verdict}")
    return stale_used


def main():
    print("## Does a dead enumeration run read the PREVIOUS run's report?\n")
    print(f"driver : {g.__file__}\n")
    defective = cell("DEFECTIVE (removal neutered = the pre-fix code path)",
                     True)
    fixed = cell("FIXED     (removal active)", False)
    print()
    if defective and not fixed:
        print("  ✅ MUST-FLIP HOLDS. The pre-fix path returns another run's "
              "paths; the fixed\n     path refuses and names the missing "
              "report, which is what the caller can act on.")
        return 0
    if not defective:
        print("  ⛔ THE TEST IS BROKEN, NOT THE FIX: the defective cell did not "
              "reproduce the\n     defect, so the fixed cell's refusal proves "
              "nothing. A discriminator whose\n     two outcomes are the same "
              "by construction is not a discriminator.")
        return 1
    print("  ⛔ THE FIX DID NOT TAKE: the stale report is still being read with "
          "the removal\n     active.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
