#!/usr/bin/env python3
"""What does the tool say when it DEGRADES a unit -- in full, not truncated?

st1inch is the gate's 0/86 and the only benchmark whose FAIL has no call-depth
truncation behind it: `residual_call_census.py` found twelve DEGRADED units on
every one of its twenty-two runs. `branch_gate.py`'s docstring names degradation
as the first of three mechanisms that DEFLATE our numerator -- "internal calls
withdrawn by degradation or by the call-depth bound remove those decisions from
every path of the unit while branch coverage still counts them" -- and says it is
not visible in the gate's output.

Before any of that can be quantified, one thing has to be established rather than
assumed: DOES THE TOOL NAME THE WITHDRAWN CALL SITES, or only the degraded UNIT?
Those support completely different next steps. If the sites are named, the
decisions they carry can be counted against the denominator from the logs alone.
If only the unit is named, that costs a run with the policy instrumented.

So this prints the DEGRADATION lines WHOLE. `residual_call_census.py` buckets its
lines to 90 characters, which is right for a tally and wrong here -- the answer to
"is the call site named" lives past the truncation point, and reading a
90-character prefix and concluding from it is the shape this workspace bans.

Every line of every log is read. Lines are selected by mentioning any of the
policy's own vocabulary -- and the vocabulary list is printed with its hit counts,
so a term that matches NOTHING is visible as a term that matched nothing rather
than silently contributing zero.

Usage: python3 degradation_census.py <runs-dir-or-logs...>
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

# The policy's own words, taken from goto_coverage.cpp's messages and from
# branch_gate.py's description of the mechanisms. Printed with hit counts so a
# zero is a reported zero.
TERMS = ["DEGRADED unit", "degrad", "withdraw", "withdrawn", "call site",
         "not expanded", "MERGED", "short-circuit", "unit_budget",
         "path/length cap"]


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    logs = []
    for a in argv[1:]:
        p = Path(a)
        if p.is_dir():
            logs += sorted(p.glob("*/run.log")) + sorted(p.glob("*.log"))
        else:
            logs.append(p)
    if not logs:
        sys.exit("no logs found")

    print("## What the tool says when it DEGRADES a unit (lines printed WHOLE)\n")
    hits = Counter()
    # One representative FULL line per distinct message shape, plus the count.
    shapes = defaultdict(lambda: [0, None])
    per_log_degraded = {}
    for lg in logs:
        text = Path(lg).read_text(errors="replace")
        n_deg = 0
        for ln in text.splitlines():
            s = ln.strip()
            matched = [t for t in TERMS if t in s]
            if not matched:
                continue
            for t in matched:
                hits[t] += 1
            if "DEGRADED unit" in s:
                n_deg += 1
            # Shape key: the message with the quoted symbol and every number
            # blanked, so two instances of one message collapse and two
            # different messages do not.
            key = []
            in_q = False
            for ch in s:
                if ch == "'":
                    in_q = not in_q
                    key.append("'")
                    continue
                key.append("*" if (in_q or ch.isdigit()) else ch)
            k = "".join(key)
            shapes[k][0] += 1
            if shapes[k][1] is None:
                shapes[k][1] = s
        per_log_degraded[lg.parent.name if lg.name == "run.log" else lg.stem] = n_deg

    print("### term hit counts (a 0 here is a reported 0, not an absence)\n")
    for t in TERMS:
        print(f"    {hits.get(t, 0):>6}  {t!r}")

    print(f"\n### {len(shapes)} distinct message shape(s), each printed in full\n")
    for k, (n, example) in sorted(shapes.items(), key=lambda kv: -kv[1][0]):
        print(f"--- x{n}")
        print(f"    {example}")
    print()

    print("### DEGRADED unit lines per log\n")
    for name, n in sorted(per_log_degraded.items()):
        print(f"    {n:>4}  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
