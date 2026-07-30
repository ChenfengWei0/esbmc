#!/usr/bin/env python3
"""Exhaustive audit of the certification gate's INPUT SPACE.

WHY, and why as a script rather than four more patches. Four
false-certification routes have been found one at a time -- a refused
coordinate, an inverted interval, a signed coordinate, and a one-value ladder
that cannot tell a point domain from an empty one -- and all four had the
IDENTICAL symptom: a query that should refuse returned VERIFICATION SUCCESSFUL.
Four instances of one shape means the gate was missing a systematic check, not
four patches. A fifth is cheaper to find by enumeration than by collision.

The gate's contract, stated so the enumeration can be judged against it:

    SUCCESSFUL  may be returned ONLY for a box that is non-empty, expressible,
                inside the coordinate's type, and genuinely contained in the
                path's domain.
    FAILED      for a well-formed box that contains an input leaving the path.
    NEITHER     (a non-zero exit with a named reason, no verdict line) for a box
                the query refuses to answer about at all.

The third state is the one that matters. An unsatisfiable assumption makes every
exit assert hold for want of an execution, so "SUCCESSFUL" and "we declined" are
indistinguishable to a caller reading only the verdict -- which is exactly how
the first four got through.

Run: python3 notes/certify_gate_audit.py [path/to/esbmc]
Needs only the two-path fixture in regression/esbmc-solidity/
solidity_path_cov_punched_ce_independent (enc=2's true domain is the single
point 255, which is what makes one SUCCESSFUL row expected).
"""
import json
import os
import subprocess
import sys
import tempfile

A = (1 << 160) - 1

# (name, box, expected) -- expected is "REFUSE" (no verdict), "FAILED", or
# "SUCCESSFUL". Only ONE row may legitimately be SUCCESSFUL.
CASES = [
    ("inverted interval", [{"name": "to", "lo": "100", "hi": "11"}], "REFUSE"),
    ("genuine point domain", [{"name": "to", "lo": "255", "hi": "255"}],
     "SUCCESSFUL"),
    ("punched to empty",
     [{"name": "to", "lo": "255", "hi": "255", "holes": ["255"]}], "REFUSE"),
    ("hi at the type maximum", [{"name": "to", "lo": "0", "hi": str(A)}],
     "FAILED"),
    ("hi one past the type", [{"name": "to", "lo": "0", "hi": str(A + 1)}],
     "REFUSE"),
    ("lo past the type", [{"name": "to", "lo": str(A + 5), "hi": str(A)}],
     "REFUSE"),
    ("hole outside the type",
     [{"name": "to", "lo": "0", "hi": str(A), "holes": [str(A + 9)]}],
     "REFUSE"),
    ("hole on the lo endpoint",
     [{"name": "to", "lo": "255", "hi": "300", "holes": ["255"]}], "FAILED"),
    ("coordinate bounded twice",
     [{"name": "to", "lo": "0", "hi": "5"}, {"name": "to", "lo": "7",
                                             "hi": "9"}], "REFUSE"),
    ("coordinate does not exist",
     [{"name": "nosuch", "lo": "0", "hi": "5"}], "REFUSE"),
    ("no bounds at all", [], "FAILED"),
]


def main():
    esbmc = sys.argv[1] if len(sys.argv) > 1 else (
        "/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(
        here, "..", "regression", "esbmc-solidity",
        "solidity_path_cov_punched_ce_independent", "contract.sol")
    if not os.path.exists(src):
        print("fixture missing:", src)
        return 2
    cwd = tempfile.mkdtemp(prefix="certaudit-")
    subprocess.run(["cp", src, cwd], check=True)

    bad = []
    print("| case | expected | exit | verdict |")
    print("|---|---|---|---|")
    for name, box, want in CASES:
        spec = {"unit": "send", "enc": 2, "depth": 1,
                "ce": {"to": "255"}, "box": box}
        with open(os.path.join(cwd, "c.json"), "w") as f:
            json.dump(spec, f)
        p = subprocess.run(
            [esbmc, "contract.sol", "--contract", "Gate2",
             "--solidity-path-coverage", "--solidity-max-tx", "1",
             "--result-only", "--memlimit", "8g",
             "--path-cov-certify", "c.json"],
            cwd=cwd, capture_output=True, text=True, timeout=180)
        got = "REFUSE"
        for ln in (p.stdout + p.stderr).splitlines():
            t = ln.strip()
            if t == "VERIFICATION SUCCESSFUL":
                got = "SUCCESSFUL"
            elif t == "VERIFICATION FAILED":
                got = "FAILED"
        ok = got == want
        if not ok:
            bad.append((name, want, got))
        print(f"| {name} | {want} | {p.returncode} | {got}"
              f"{'' if ok else '  <-- MISMATCH'} |")

    if bad:
        print("\nGATE AUDIT FAILED:")
        for n, w, g in bad:
            print(f"  {n}: expected {w}, got {g}")
        # A SUCCESSFUL where REFUSE was expected is a false certificate; the
        # others are weaker but still a gate that does not do what is written
        # above.
        return 1
    print("\nGATE AUDIT PASSED: every shape lands where the contract says, and "
          "exactly one row is SUCCESSFUL (the genuine point domain).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
