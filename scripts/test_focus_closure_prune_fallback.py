#!/usr/bin/env python3
"""solidity_path_generalise.run must re-run a focused query unpruned when the
frontend's prune marker shows up in the output, and must NOT re-run otherwise."""
import os, stat, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import solidity_path_generalise as spg


def fake_esbmc(tmp, marker_unless_opt_out):
    p = os.path.join(tmp, "esbmc")
    with open(p, "w") as f:
        f.write("#!/bin/sh\n")
        f.write("case \" $* \" in *' --no-focus-closure-prune '*) echo 'VERIFICATION SUCCESSFUL'; exit 0;; esac\n")
        if marker_unless_opt_out:
            f.write("echo 'warning: no body for function __ESBMC_focus_closure_prune_violation'\n")
        f.write("echo 'VERIFICATION SUCCESSFUL'; exit 0\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
    return p


def calls_in(out):
    return out.count("[run] CMD ")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        sol = os.path.join(tmp, "c.sol"); open(sol, "w").write("contract C{}")
        e = fake_esbmc(tmp, True)
        out = spg.run(e, sol, "C", [], 1, 30, tmp, focus="f", esbmc_args=["--x"])
        assert calls_in(out) == 2, out
        assert "FOCUS-CLOSURE-PRUNE VIOLATION" in out
        assert "--no-focus-closure-prune" in out.split("[run] CMD ")[-1], out
        # opt-out already present: no second run even if the marker is printed
        out = spg.run(e, sol, "C", [], 1, 30, tmp, focus="f", esbmc_args=["--no-focus-closure-prune"])
        assert calls_in(out) == 1, out
        # no focus: never re-run
        out = spg.run(e, sol, "C", [], 1, 30, tmp, focus=None)
        assert calls_in(out) == 1, out
        # clean output: single run
        e2 = fake_esbmc(tmp, False)
        out = spg.run(e2, sol, "C", [], 1, 30, tmp, focus="f")
        assert calls_in(out) == 1, out
    print("all checks passed")


if __name__ == "__main__":
    main()
