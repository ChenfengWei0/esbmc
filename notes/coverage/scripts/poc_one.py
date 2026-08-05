#!/usr/bin/env python3
"""RUN EXACTLY ONE POC. There is no form of this command that runs two.

A PoC is one TARGET public/external function of one corpus contract, written
by `poc_split.py`. This is the only entry point that starts a corpus run, and
it takes one PoC id and nothing else -- no benchmark key, no `--all`, no glob.
Both underlying drivers additionally refuse a multi-unit invocation on their
own, so the restriction survives someone calling them directly.

TWO STAGES, and they are separate invocations on purpose. Stage 1's output is
the counterexample set; stage 2 reads it back and measures regions. Running
them as one command would make a stage-2 budget outcome indistinguishable from
a stage-1 one, which is the confusion the funnel exists to prevent.

    --stage 1   instrument + witness   (pathcov_collect.py, one unit)
    --stage 2   region + certify       (certify_all.py -> the generalise driver)

TWO CELLS, never merged:

    gate      --solidity-max-tx 1, focus = {unit}. Comparable against the
              branch-coverage baseline, which is MEASURED to run at one
              transaction.
    artefact  --solidity-max-tx 2, focus = {unit} + the functions that WRITE
              what the unit reads. The only configuration measured to reach
              cross-function state at all. REFUSED while that writer set is
              empty, because guessing it would make every artefact number a
              measurement of the guess.

THE TIME BOX IS ENFORCED HERE. The work order bans any single run longer than
60 seconds unless the task explicitly allows it, so that is the default and
exceeding it needs `--long N` -- which prints the rule it is overriding rather
than letting a large number slip past in a command line.

Usage:
    python3 poc_one.py <poc-id> [--stage 1|2] [--cell gate|artefact] [--long N]
    python3 poc_one.py --list
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
POCS = REPO / "notes/coverage/poc_units"

DEFAULT_TIMEOUT = 60


def index_pocs():
    p = POCS / "index.json"
    if not p.exists():
        sys.exit(f"no {p}; run poc_split.py first")
    d = json.loads(p.read_text())
    if d.get("schema") != "poc-units/3":
        sys.exit(f"{p} has schema {d.get('schema')!r}; this runner needs "
                 f"poc-units/3, in which a UNIT is a public/external function "
                 f"of a CONTRACT and every PoC owns its input. Re-run "
                 f"poc_split.py")
    return d


def load(pid):
    # THE INDEX IS THE AUTHORITY, not the directory. `poc_split.py --reconcile`
    # leaves a stale directory in place when it holds run output, so a
    # `poc.json` on disk is NOT evidence that the current unit rule names it.
    # Loading one anyway is how a corrected rule stays true in the index and
    # false in every run.
    idx = index_pocs()
    if pid not in idx["pocs"]:
        stale = (POCS / pid / "poc.json").exists()
        sys.exit(
            f"{pid}: NOT IN THE INDEX"
            + (f", but {POCS / pid / 'poc.json'} exists on disk. That is a "
               f"LEFTOVER from an older unit rule -- it was kept only because "
               f"the directory holds run output. It is not a PoC and will not "
               f"be run.\n" if stale else ".\n")
            + f"  full list: python3 notes/coverage/scripts/poc_one.py --list")
    p = POCS / pid / "poc.json"
    if not p.exists():
        # NAME THE NEAREST MATCHES. A typo that produced "no such PoC" and
        # nothing else would send the reader to `ls`, and the id carries three
        # components so a near miss is the common failure rather than the
        # exotic one.
        have = sorted(d.name for d in POCS.iterdir() if d.is_dir())
        near = [h for h in have if pid.lower() in h.lower()
                or h.lower().endswith(pid.lower())]
        msg = f"no such PoC: {pid}"
        if near:
            msg += "\n  did you mean:\n    " + "\n    ".join(near[:10])
        msg += (f"\n  full list: python3 "
                f"notes/coverage/scripts/poc_split.py --list")
        sys.exit(msg)
    return json.loads(p.read_text())


def run(cmd, timeout, cwd, inputs_dir):
    """One child, its own process group, reaped on every exit path.

    `subprocess.run(timeout=)` kills only the DIRECT child; the esbmc
    grandchild is orphaned holding its full --memlimit, and this machine has
    been exhausted once by exactly that.
    """
    print("[poc] " + " ".join(cmd), flush=True)
    # THE ONLY CORPUS THIS CHILD CAN SEE IS THIS POC'S OWN. The shared
    # `notes/coverage/inputs/` is deleted; both drivers resolve their inputs
    # directory from this variable, so a child of this runner physically cannot
    # reach another benchmark's source even if something in it tried to sweep.
    env = dict(os.environ, VERIPUT_INPUTS_DIR=str(inputs_dir))
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=str(cwd), start_new_session=True, env=env)
    killed = False
    try:
        p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        killed = True
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        p.communicate()
        rc = 124
    finally:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    wall = time.time() - t0
    print(f"\n[poc] exit={rc}{' KILLED at the time box' if killed else ''} "
          f"wall={wall:.1f}s", flush=True)
    return rc


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("poc", nargs="?",
                    help="exactly one PoC id. There is no plural form.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--stage", choices=("1", "2"), default="1")
    ap.add_argument("--cell", choices=("gate", "artefact"), default="gate")
    ap.add_argument("--long", type=int, default=None, metavar="N",
                    help="raise the per-run time box above the work order's "
                         "60 seconds. Prints the rule it overrides.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the command and the cell, run nothing")
    ap.add_argument("--fresh", action="store_true",
                    help="DISCARD records this binary did not write and "
                         "measure again. Stage 1 spells it `--fresh` and "
                         "stage 2 spells it `--redo`; this passes whichever "
                         "the stage wants, so the caller does not have to "
                         "know. Reaching stage 2's via --certify-arg would "
                         "force an --arm rename and file an identical "
                         "configuration as a new contrast arm. Long form: "
                         "this unit and start over. `pathcov_collect` refuses "
                         "to resume a collection written by a different "
                         "binary -- resuming would hang the old build's "
                         "reports under the new build's name -- and its "
                         "refusal tells the caller to pass this. Until now "
                         "this entry point could not, so the only way past a "
                         "correct gate was to move the directory aside by "
                         "hand. ⚠ It THROWS AWAY measurements: everything "
                         "collected for this unit is re-run.")
    # ---- AN ARM IS A CONFIGURATION, AND IT GETS ITS OWN OUTPUT FILE ----
    #
    # The work order now requires every query that reached NO verdict to be
    # attributed to the TOOL or to the INVOCATION, and the evidence shape for
    # "the invocation" is two runs differing in exactly ONE switch. There was
    # no way to express the second run: this runner hardcoded certify_all's
    # flags, so the only contrast available was editing the runner.
    #
    # `--arm` is REQUIRED with any passthrough, and it renames the output file.
    # certify_all's own help says it in the same words -- two arms writing one
    # file put two measurements under one (benchmark, unit) key -- and a
    # contrast whose two sides overwrite each other proves nothing at all.
    ap.add_argument("--arm", default="",
                    help="name this configuration. REQUIRED with any "
                         "--certify-arg, and it renames the output file so the "
                         "two sides of a contrast cannot overwrite each other.")
    ap.add_argument("--certify-arg", action="append", default=[], metavar="ARG",
                    help="one extra argument passed straight to certify_all "
                         "(--stage 2 only). Repeatable. ⚠ USE THE `=` FORM for "
                         "anything starting with a dash: "
                         "--certify-arg=--skip-bracket . Without it argparse "
                         "reads the value as the next option.")
    a = ap.parse_args()

    if a.list:
        d = index_pocs()
        for pid in d["pocs"]:
            poc = json.loads((POCS / pid / "poc.json").read_text())
            print(f"{pid:64s} {poc['visibility']:9s} "
                  f"{poc['declaring_contract']}.{poc['unit']}")
        print(f"\n{len(d['pocs'])} PoC(s); every one of them is a "
              f"public/external function of a contract")
        return 0

    if not a.poc:
        sys.exit("this command takes exactly one PoC id. "
                 "`--list` shows them.")

    if a.certify_arg and not a.arm:
        sys.exit(
            "--certify-arg changes the configuration, so this run needs "
            "--arm <name>.\n"
            "  Without it this arm would write certify_<cell>.jsonl -- the "
            "same file the unmodified configuration writes -- and the two "
            "sides of a contrast that overwrite each other prove nothing. "
            "A contrast is only evidence while BOTH sides are still on disk.")
    if a.certify_arg and a.stage != "2":
        sys.exit(f"--certify-arg is passed to the stage-2 driver, and this is "
                 f"--stage {a.stage}. Stage 1's knobs are the cell's "
                 f"(--scope / --max-tx), which live in poc.json.")

    poc = load(a.poc)
    cell = poc["cells"][a.cell]
    if a.cell == "artefact" and not cell["focus_with"]:
        sys.exit(
            f"{a.poc}: the artefact cell needs the ALPHABET chosen first, and "
            f"`focus_with` in its poc.json is empty.\n"
            f"  The rule is: {{{poc['unit']}}} plus the functions that WRITE "
            f"what it reads. Nothing in this tree computes that set, so it is "
            f"left empty rather than guessed -- a guessed alphabet makes every "
            f"number measured under it a measurement of the guess.\n"
            f"  Fill it in at {POCS / a.poc / 'poc.json'} and re-run, or use "
            f"--cell gate.")

    timeout = DEFAULT_TIMEOUT
    if a.long is not None:
        if a.long <= DEFAULT_TIMEOUT:
            sys.exit(f"--long {a.long} is not longer than the {DEFAULT_TIMEOUT}s "
                     f"default; drop the flag")
        print(f"[poc] ⚠ TIME BOX RAISED to {a.long}s. The work order bans any "
              f"single run over {DEFAULT_TIMEOUT}s unless the task explicitly "
              f"allows it -- this run is claiming that allowance.", flush=True)
        timeout = a.long

    suffix = f"__poc_{poc['declaring_contract']}_{poc['unit']}_{a.cell}"
    outdir = POCS / a.poc
    print(f"[poc] {a.poc}")
    print(f"[poc] target unit  : {poc['declaring_contract']}.{poc['unit']} "
          f"(dispatched through --contract {poc['harness_contract']})")
    print(f"[poc] cell         : {a.cell}  "
          f"(--solidity-max-tx {cell['max_tx']}, scope {cell['scope']})")
    print(f"[poc] why this cell: {cell['why']}")
    print(f"[poc] time box     : {timeout}s")

    if a.stage == "1":
        cmd = [sys.executable, "-u",
               str(HERE / "pathcov_collect.py"), poc["benchmark"],
               "--only", poc["unit"],
               "--out-suffix", suffix,
               "--scope", cell["scope"],
               "--max-tx", str(cell["max_tx"]),
               "--timeout", str(timeout)]
        # ---- THE GATE'S OWN ADVICE HAS TO BE REACHABLE FROM HERE ----------
        #
        # `pathcov_collect` refuses to resume a collection written by a
        # DIFFERENT BINARY -- correctly, since resuming would silently reuse
        # the old build's reports under the new build's name. Its refusal says
        # "Re-run with --fresh", and until now this entry point had no way to
        # pass that, so the only route was to move the directory aside by
        # hand. A gate that names an action the caller cannot take is a gate
        # people learn to work around.
        if a.fresh:
            cmd.append("--fresh")
        if cell["scope"] == "set":
            cmd += ["--focus-with", ",".join(cell["focus_with"])]
    else:
        arm = "".join(c if c.isalnum() else "_" for c in a.arm)
        out = outdir / (f"certify_{a.cell}" + (f"__{arm}" if arm else "")
                        + ".jsonl")
        cmd = [sys.executable, "-u",
               str(HERE / "certify_all.py"), poc["benchmark"],
               "--unit", poc["unit"],
               "--out", str(out),
               "--timeout", str(timeout),
               "--run-timeout", str(timeout)] + list(a.certify_arg)
        # ---- ONE FLAG, THE RIGHT VERB PER STAGE ---------------------------
        #
        # Stage 1's collector calls it `--fresh`, stage 2's sweep calls it
        # `--redo`. Both mean the same thing to the CALLER: discard the
        # records this binary did not write and measure again.
        #
        # The alternative was `--certify-arg=--redo`, and that is worse than
        # verbose: `--certify-arg` REQUIRES `--arm`, and `--arm` renames the
        # output file so two CONFIGURATIONS cannot overwrite each other. A
        # plain re-measure is not a second configuration, so reaching --redo
        # that way would file an identical-configuration rerun under a new arm
        # name -- inventing a contrast that does not exist, in the one place
        # this project is most careful never to do that.
        if a.fresh:
            cmd.append("--redo")
        if a.certify_arg:
            print(f"[poc] arm          : {a.arm}  "
                  f"(+ {' '.join(a.certify_arg)})")
            print(f"[poc] out          : {out.name}")

    inputs_dir = poc.get("inputs_dir")
    if not inputs_dir or not Path(inputs_dir).is_dir():
        sys.exit(
            f"{a.poc}: this PoC has no private input directory "
            f"({inputs_dir!r}). The shared corpus is deleted, so a PoC without "
            f"its own inputs has nothing to run on.\n"
            f"  Rebuild: python3 notes/coverage/scripts/poc_split.py\n"
            f"  If that fails because the corpus is gone: git checkout -- "
            f"notes/coverage/inputs/  (it is tracked; nothing was lost)")
    print(f"[poc] inputs       : {inputs_dir}")

    if a.dry_run:
        print("[poc] --dry-run: VERIPUT_INPUTS_DIR=" + inputs_dir
              + " " + " ".join(cmd))
        return 0
    return run(cmd, timeout + 30, REPO, inputs_dir)


if __name__ == "__main__":
    sys.exit(main())
