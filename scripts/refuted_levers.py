#!/usr/bin/env python3
"""PRINT EVERY LEVER THIS PROJECT HAS ALREADY REFUTED, from the commit log.

---- WHY THIS EXISTS, AND IT IS NOT A NICETY -------------------------------

On 2026-08-03 the same two experiments were run twice, hours apart, by the same
person, and the second run of each was presented as a new finding:

    14:53  e1791b4893  "REFUTED AGAIN ... --run-timeout 600 ALONE kills
                        farming/startFarming at the unit budget and produces
                        nothing at all"
    20:23  arm 2       --run-timeout 600 on EscrowDst::cancel.  21 minutes.
                        Same negative.

    17:16  16d0dc884d  "--claim-budget ... moves the span 38 orders of
                        magnitude, and does NOT move the certification rate"
    21:01  arm 4       --skip-bracket --probes 2, the same ladder-thinning
                        lever.  Same negative.

e1791b4893's own body says "recording the two failures here so the conjunction
is not later reported as if it were the obvious first thing to try". It was
written for a reader who never came.

THE ROOT CAUSE IS ADDRESSING, NOT CARE. CLAUDE.md's resume ritual is `git log
-5`. Those two refutations were at positions 20 and 26 of one day's log. The
state "which levers are dead" lives ONLY in commit subjects, and a five-line
window cannot see it, so it gets re-derived instead of read.

⛔ RUN THIS BEFORE LAUNCHING ANY EXPERIMENT. Not after, and not instead of
thinking -- the point is that an experiment whose lever appears below has
already been answered and its wall clock is pure loss.

---- WHAT IT MATCHES AND WHY THAT IS DELIBERATELY WIDE ---------------------

Commit subjects (and, with -v, bodies) carrying any of the words this project
uses when it kills an idea: REFUTED, WRONG, struck, does NOT move, cannot,
never, no-op, made it WORSE. The list is intentionally over-inclusive: a false
positive costs one line of reading, a false negative costs the hours above.

usage:  python3 scripts/refuted_levers.py [--since <git date>] [-v]
"""
import sys
import subprocess

MARKERS = [
    "REFUTED",
    "refuted",
    "is WRONG",
    "was WRONG",
    "IS WRONG",
    "struck",
    "STRUCK",
    "does NOT move",
    "did not move",
    "did NOT move",
    "made it WORSE",
    "cannot reach",
    "cannot fire",
    "silent no-op",
    "never has",
    "never runs",
    "produces nothing",
    "MEASURED NOTHING",
    "not the fix",
    "NOT THE FIX",
]


def main(argv):
    since = None
    verbose = False
    i = 1
    while i < len(argv):
        if argv[i] == "--since" and i + 1 < len(argv):
            since = argv[i + 1]
            i += 2
        elif argv[i] in ("-v", "--verbose"):
            verbose = True
            i += 1
        else:
            print(__doc__)
            return 2

    sep = "\x1e"
    cmd = [
        "git",
        "log",
        f"--pretty=format:%h{sep}%ad{sep}%s{sep}%b\x1d",
        "--date=format:%Y-%m-%d %H:%M",
    ]
    if since:
        cmd.append(f"--since={since}")
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        print("FATAL: git log failed:\n" + out.stderr)
        return 3
    records = [r for r in out.stdout.split("\x1d") if r.strip()]
    if not records:
        # ⛔ An empty result is a hard failure, not "nothing has been refuted".
        # A quiet empty list is exactly the reassurance this script exists to
        # refuse to give.
        print("FATAL: git log returned no commits for that range.")
        return 3

    hits = []
    for r in records:
        parts = r.strip().split(sep)
        if len(parts) < 3:
            continue
        h, d, subj = parts[0], parts[1], parts[2]
        body = parts[3] if len(parts) > 3 else ""
        where = []
        if any(m in subj for m in MARKERS):
            where.append("SUBJECT")
        if any(m in body for m in MARKERS):
            where.append("body")
        if where:
            hits.append((h, d, subj, body, where))

    print("=" * 78)
    print(f"LEVERS ALREADY ANSWERED -- {len(hits)} of {len(records)} commit(s)")
    if since:
        print(f"range: --since {since}")
    print("=" * 78)
    for h, d, subj, body, where in hits:
        tag = "⛔" if "SUBJECT" in where else "  "
        print(f"{tag} {h}  {d}  [{'+'.join(where)}]")
        for line in _wrap(subj, 72):
            print(f"      {line}")
        if verbose and body.strip():
            for line in body.strip().splitlines():
                print(f"        | {line}")
        print()
    print("=" * 78)
    print(
        "⛔ If the lever you are about to test appears above, it has already"
        " been answered. Read that commit before spending wall clock on it."
    )
    print("=" * 78)
    return 0


def _wrap(s, n):
    out, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > n:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv))
