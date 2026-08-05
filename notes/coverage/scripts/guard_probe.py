#!/usr/bin/env python3
"""Measure whether a PUT's `if (_put_ok)` TRUE BRANCH is ever taken.

---- THE QUESTION, AND WHY IT IS NOT RHETORICAL ----------------------------

A revert-tolerant PUT wraps its call in `try/catch` and emits the rungs that
say the state CHANGED under `if (_put_ok)`. The emitter prints its own warning
on every such test:

    ⚠ WHETHER THE GUARD'S TRUE BRANCH IS EVER TAKEN IS NOT MEASURED HERE. If
      it never is, these assertions are green and say nothing.

That warning is the difference between "setDistributor 13 carries 12
assertions" and "it carries 10 assertions and 2 pieces of decoration". Nothing
in the pipeline answers it, and `forge test` passing does not: a guard that is
never true passes exactly as loudly as one that is always true.

---- THE DISCRIMINATOR, WRITTEN BEFORE THE RUN ------------------------------

Two mutated copies of the PUT are emitted into the same forge project, each
with the contract renamed so it coexists with the original:

    PROBE   -- the guarded assertions are replaced by `assertTrue(false, ...)`.
               RED  => the true branch IS reached; the conditional rungs are
                       real assertions.
               GREEN => the true branch is NEVER reached; those rungs are
                       vacuous and must not be counted as oracle.

    CONTROL -- the same `assertTrue(false, ...)`, placed OUTSIDE the guard.
               MUST be RED. If it is GREEN, forge is not running these copies
               at all and the PROBE result means nothing -- it would be a
               measurement of the harness, not of the guard.

⛔ THE CONTROL IS NOT OPTIONAL. A probe that cannot fail and a guard that is
never taken produce the same green, which is the failure shape this project
keeps hitting.

This costs no esbmc run. It edits nothing in place: both copies are written
under a `_guardprobe` name and removed afterwards unless --keep.

usage:
    guard_probe.py <put.t.sol> [--keep]
"""
import os
import re
import subprocess
import sys


GUARD_OPEN = "    if (_put_ok) {"


def find_project(path):
    d = os.path.dirname(os.path.abspath(path))
    while d != "/":
        if os.path.exists(os.path.join(d, "foundry.toml")):
            return d
        d = os.path.dirname(d)
    return None


def mutate(text, base, suffix, outside):
    """Rename every contract, then plant the always-false assertion."""
    out = text.replace(base, base + suffix)
    lines = out.splitlines()
    res, i, planted = [], 0, False
    while i < len(lines):
        ln = lines[i]
        if ln.rstrip() == GUARD_OPEN.rstrip() and not planted:
            if outside:
                # CONTROL: the false assertion sits BEFORE the guard, so it
                # runs on every fuzz draw regardless of the call's outcome.
                res.append('    assertTrue(false, "CONTROL: this must be RED");')
                res.append(ln)
                i += 1
                while i < len(lines) and lines[i].strip() != "}":
                    res.append(lines[i])
                    i += 1
            else:
                # PROBE: replace the guard's whole body.
                res.append(ln)
                res.append('    assertTrue(false, '
                           '"PROBE: the guard TRUE branch was taken");')
                i += 1
                while i < len(lines) and lines[i].strip() != "}":
                    i += 1
            planted = True
            continue
        res.append(ln)
        i += 1
    return "\n".join(res) + "\n", planted


def run(proj, name):
    p = subprocess.run(["forge", "test", "--match-contract", name, "-q"],
                       cwd=proj, capture_output=True, text=True, timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main(argv):
    args = [a for a in argv[1:] if a != "--keep"]
    keep = "--keep" in argv[1:]
    if not args or "--help" in argv[1:] or "-h" in argv[1:]:
        print(__doc__)
        return 0
    src = args[0]
    text = open(src).read()
    if GUARD_OPEN.rstrip() not in text:
        print("⛔ %s has no `if (_put_ok)` guard. There is nothing to measure "
              "here; this is NOT evidence that a guard elsewhere is fine."
              % src)
        return 2
    proj = find_project(src)
    if proj is None:
        print("⛔ no foundry.toml above %s" % src)
        return 2
    base = os.path.basename(src)[:-len(".t.sol")]

    written, verdicts = [], {}
    for suffix, outside in (("_gprobe", False), ("_gctl", True)):
        mut, planted = mutate(text, base, suffix, outside)
        if not planted:
            print("⛔ could not plant the assertion for %s" % suffix)
            return 2
        dst = os.path.join(os.path.dirname(os.path.abspath(src)),
                           base + suffix + ".t.sol")
        open(dst, "w").write(mut)
        written.append(dst)
        rc, out = run(proj, base + suffix)
        verdicts[suffix] = rc
        print("--- %s : forge rc=%d (%s)"
              % (base + suffix, rc, "RED" if rc else "GREEN"))
        print(out)

    if not keep:
        for d in written:
            os.unlink(d)

    ctl_red = verdicts["_gctl"] != 0
    probe_red = verdicts["_gprobe"] != 0
    print("=" * 76)
    if not ctl_red:
        print("⛔ THE CONTROL IS GREEN. An always-false assertion outside the "
              "guard did not fail, so forge did not run these copies and the "
              "PROBE result says nothing. DISCARD it.")
        return 1
    print("CONTROL is RED, as required -- the harness does run these copies.")
    if probe_red:
        print("PROBE is RED  => the `if (_put_ok)` TRUE branch IS taken. The "
              "conditional rungs are real assertions and count as oracle.")
    else:
        print("PROBE is GREEN => the `if (_put_ok)` TRUE branch is NEVER "
              "taken on 256 draws. Those rungs are VACUOUS: they must not be "
              "counted toward this PUT's assertion total.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
