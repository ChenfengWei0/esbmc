#!/usr/bin/env python3
"""Run the payload-vs-path consistency check over EVERY benchmark's reports.

WHY THIS EXISTS. `ce_consistency.py` is this project's detector for a published
payload that contradicts the path it claims to witness. It was written, it works,
and until 2026-08-01 it had NEVER BEEN RUN ON THE CORPUS -- neither `poc_run.py`
nor any of the three collection shell drivers invokes it, so the only evidence it
ever produced came from someone typing its name by hand. A detector nothing calls
is the same shape as a detector that cannot fire.

WHAT THE OUTPUT MUST NOT BE READ AS. `ce_consistency.py` prints, in its own
words, "A skipped decision is NOT a passing one." This wrapper preserves every
line of its output verbatim and additionally prints the corpus-wide totals with
the SKIPPED column beside the DISAGREE one, because the first measurement gave

    929 decisions evaluated, 226 agree, 0 DISAGREE, 703 SKIPPED

and a summary that reported only "0 DISAGREE" would describe a quarter of the
decision set as if it were all of it. The dominant refusal is a decision inside
an INLINED CALLEE reading the callee's own local, which the report cannot bind
because it publishes no argument mapping.

A benchmark whose claims are all U contributes ZERO decisions, not zero
disagreements -- U claims carry no `decisions` array at all. st1inch is entirely
in this state. That is printed as its own line rather than folded into a total,
because "checked and clean" and "never checked" are different facts and this
corpus has been misread in exactly that direction before.

Usage: python3 ce_consistency_corpus.py [<pathcov-root>]
"""
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parent / "pathcov"

NUM = {
    "evaluated": re.compile(r"^decisions evaluated\s+(\d+)$"),
    "agree": re.compile(r"^agree\s+(\d+)$"),
    "disagree": re.compile(r"^DISAGREE\s+(\d+)$"),
    "skipped": re.compile(r"^skipped \(unparsed / name not in payload\)\s+(\d+)$"),
}


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        sys.exit(f"no such directory: {root}")

    benches = sorted(d for d in root.iterdir() if d.is_dir())
    totals = {k: 0 for k in NUM}
    rows = []

    for b in benches:
        reps = sorted((b / "reports").glob("*.json")) if (b / "reports").is_dir() else []
        if not reps:
            rows.append((b.name, None, 0))
            print(f"\n### {b.name}: NO REPORTS -- contributes nothing, not zero")
            continue
        p = subprocess.run(
            [sys.executable, str(HERE / "ce_consistency.py")] + [str(r) for r in reps],
            capture_output=True, text=True)
        print(f"\n### {b.name}  ({len(reps)} report(s))  exit={p.returncode}")
        print(p.stdout, end="")
        if p.stderr.strip():
            print("STDERR:")
            print(p.stderr, end="")

        got = {}
        for ln in p.stdout.splitlines():
            s = ln.strip()
            for k, rx in NUM.items():
                m = rx.match(s)
                if m:
                    got[k] = int(m.group(1))
        for k in NUM:
            totals[k] += got.get(k, 0)
        rows.append((b.name, got, len(reps)))

    print("\n" + "=" * 78)
    print("## corpus totals -- SKIPPED is printed beside DISAGREE on purpose\n")
    print(f"{'bench':<32}{'reports':>8}{'eval':>8}{'agree':>8}"
          f"{'DISAGREE':>10}{'SKIPPED':>9}")
    for name, got, nrep in rows:
        if got is None:
            print(f"{name:<32}{nrep:>8}{'-':>8}{'-':>8}{'-':>10}{'-':>9}"
                  "   no reports")
            continue
        note = ""
        if got.get("evaluated", 0) == 0:
            note = "   NEVER CHECKED (all claims U -> no `decisions` array)"
        print(f"{name:<32}{nrep:>8}{got.get('evaluated', 0):>8}"
              f"{got.get('agree', 0):>8}{got.get('disagree', 0):>10}"
              f"{got.get('skipped', 0):>9}{note}")
    print(f"{'TOTAL':<32}{'':>8}{totals['evaluated']:>8}{totals['agree']:>8}"
          f"{totals['disagree']:>10}{totals['skipped']:>9}")

    if totals["skipped"]:
        pct = 100.0 * totals["skipped"] / max(1, totals["evaluated"])
        print(f"\n{totals['skipped']} of {totals['evaluated']} decisions "
              f"({pct:.0f}%) were REFUSED, not passed. Any statement of the form "
              f"\"the payloads are consistent\" covers only the "
              f"{totals['agree']} that were judged.")

    # Non-zero exit on a real disagreement, so a driver can gate on it. A high
    # skip rate is NOT an error here -- it is a measurement, reported above.
    return 1 if totals["disagree"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
