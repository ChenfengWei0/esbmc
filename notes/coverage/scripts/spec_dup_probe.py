#!/usr/bin/env python3
"""Prove the ladder REFUSES a `vars` list that names one variable twice.

---- WHY THIS NEEDS ITS OWN RUN -------------------------------------------

`goto_coverage.cpp` used to resolve a spec variable with

    for (const auto &v : assert_vars) if (v.name == vname) spec = &v;

-- no `break`, so two entries with the same name left the LAST one winning and
the earlier one gone without a word. That matters the moment specs are
PROPOSED rather than hand-written: `delta_dir` is mandatory, so a proposer
wanting both directions would naturally write two same-named entries, get ONE
measured, and read the single result as though both had been asked.

The refusal added for it is a branch that has never executed. A refusal nobody
has seen fire is indistinguishable from one that cannot -- both produce clean
runs -- so this probe exists to make it fire on purpose.

---- THE DISCRIMINATOR, WRITTEN BEFORE THE RUN -----------------------------

    DUPLICATE : the spec's first `vars` entry is repeated verbatim.
                MUST exit non-zero AND print "more than once".
                A clean run here means the refusal is dead code.

    CONTROL   : the same spec, untouched.
                MUST run to completion. If the CONTROL also fails, this probe
                measured the spec, the binary or the command line -- not the
                duplicate -- and its result must be discarded.

usage:
    spec_dup_probe.py <workdir-containing-spec.json>
"""
import json
import os
import subprocess
import sys

ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"


def run(cmd, cwd):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main(argv):
    args = argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        return 0
    # ⛔ ABSOLUTE. The recorded command is run with cwd=wd, so a relative
    # workdir makes every path this probe builds resolve against wd a second
    # time. Measured: esbmc reported `cannot open .../spec.dup.json` and
    # abort()ed, and the probe read the SIGABRT as "the refusal did not fire"
    # -- a verdict about the code under test produced entirely by the probe's
    # own path handling.
    wd = os.path.abspath(args[0])
    log_path = os.path.join(wd, "run.log")
    if not os.path.exists(log_path):
        print(f"⛔ no run.log in {wd}")
        return 2
    # THE COMMAND IS ALREADY RECORDED -- `run_esbmc` writes it as run.log's
    # first line. Reading it, rather than adding a cmd.txt beside it, keeps
    # ONE record of one fact; two would drift the first time the driver's
    # argument list changes.
    recorded = open(log_path, errors="replace").read().splitlines()[0].split()
    if "--path-cov-assert" not in recorded:
        print(f"⛔ the command in {log_path} carries no --path-cov-assert, so "
              f"this workdir's last run was not a ladder query. Nothing to "
              f"duplicate.")
        return 2
    # THE SPEC IS THE ARG AFTER THE FLAG, not "the one whose name ends in
    # spec.json" -- the R2 pass writes `spec.r2_<param>.json` and a
    # suffix match would silently leave the command pointing at the FIRST
    # pass's spec while claiming to test the duplicate.
    spec_path = recorded[recorded.index("--path-cov-assert") + 1]
    if not os.path.exists(spec_path):
        print(f"⛔ the recorded spec {spec_path} is gone")
        return 2

    base = json.load(open(spec_path))
    if not base.get("vars"):
        print(f"⛔ {spec_path} has no `vars` entry to duplicate")
        return 2
    print(f"spec under test: {spec_path}  ({len(base['vars'])} var(s))")

    dup = dict(base)
    dup["vars"] = [base["vars"][0]] + list(base["vars"])
    dup_path = os.path.join(wd, "spec.dup.json")
    # ⛔ CLOSED BEFORE IT IS READ. `json.dump(d, open(p, "w"))` leaves the
    # flush to garbage collection, and the first version of this probe handed
    # esbmc a half-written file: the spec parser `abort()`s on malformed JSON,
    # so the run came back SIGABRT with no message and the probe reported
    # "REFUSAL DID NOT FIRE" about a refusal that works perfectly. A probe
    # that fails this way accuses the code under test of its own defect.
    with open(dup_path, "w") as f:
        json.dump(dup, f)
        f.flush()
        os.fsync(f.fileno())

    out = {}
    for label, path in (("DUPLICATE", dup_path), ("CONTROL", spec_path)):
        cmd = list(recorded)
        cmd[cmd.index("--path-cov-assert") + 1] = path
        rc, txt = run(cmd, wd)
        said = "more than once" in txt
        out[label] = (rc, said)
        print(f"--- {label}: rc={rc}  refusal-message={said}")
        print(f"      cmd: {' '.join(cmd)}")
        shown = False
        for ln in txt.splitlines():
            if "more than once" in ln or "REFUSING THE LADDER" in ln:
                print(f"      {ln.strip()}")
                shown = True
        # ⛔ ON AN UNEXPECTED OUTCOME, PRINT EVERYTHING. The first version
        # showed only the lines matching its own keywords, so a run that died
        # for a completely different reason printed NOTHING and the probe
        # reported "refusal did not fire" -- a conclusion about the code under
        # test drawn from a probe that had not looked at the failure.
        if not shown and rc not in (0, 1):
            print(f"      --- unexpected rc={rc}, FULL OUTPUT ---")
            for ln in txt.splitlines():
                print(f"      {ln}")

    print("=" * 76)
    ctl_rc, ctl_said = out["CONTROL"]
    dup_rc, dup_said = out["DUPLICATE"]
    # THE VERDICT IS THE MESSAGE, NOT THE EXIT CODE. Both runs exit 1 here --
    # esbmc exits 1 for "a candidate was refuted" too, which is the ladder
    # working. An earlier version keyed on `dup_rc != ctl_rc` and would have
    # called a live refusal dead.
    if ctl_rc not in (0, 1):
        print(f"⛔ THE CONTROL DID NOT RUN (rc={ctl_rc}). This probe measured "
              f"the command line, the spec file or the binary -- not the "
              f"duplicate. DISCARD the result.")
        return 1
    if ctl_said:
        print("⛔ THE CONTROL ALSO PRINTED THE REFUSAL. Then the message is "
              "not caused by the duplicate and this probe distinguishes "
              "nothing. DISCARD.")
        return 1
    if dup_said:
        print("REFUSAL FIRES: the duplicated spec is rejected by name while "
              "the identical spec without the duplicate runs the ladder. The "
              "branch is live, and it is reached before anything else can "
              "silently keep the last entry.")
        return 0
    print(f"⛔ REFUSAL DID NOT FIRE: duplicate rc={dup_rc}, no message. "
          f"Either the binary predates the change or the loop is not reached "
          f"for this spec.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
