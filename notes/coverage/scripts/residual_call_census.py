#!/usr/bin/env python3
"""What did the tool ALREADY say about the call sites it did not expand?

`branch_gate.py` puts both Escrows at `0/8` on `contracts/libraries/
ImmutablesLib.sol`, and the recorded explanation is a MEASUREMENT-SCOPE
difference: a library has no dispatcher, `--function` is banned here, so those
units are refused -- and their decisions are supposed to be covered "through the
units that call them".

The tool contradicts that in its own run log, and it names a remedy:

    WARNING: --solidity-path-coverage: N call site(s) are deeper than the call
    depth bound (4) and were NOT expanded (... sol:@C@ImmutablesLib@F@hash#932
    ...); paths through them are MERGED rather than enumerated.

Those are two different diagnoses with different consequences for the paper --
"this method cannot serve a library-only compilation unit" (a stated
applicability limit) versus "we ran at a depth bound that excluded a callee we do
reach" (a configuration we chose). The evidence to tell them apart has been on
disk since the collection ran and nothing has read it.

HOW THIS READS THE LOGS, and why it is not a pattern hunt. Every line of every
log is read and BUCKETED, with the bucket key derived from the line itself
(`WARNING: --solidity-path-coverage: <first clause>`), so a category nobody
thought to look for still shows up with a count. Only after the full tally are
the depth-bound and degradation lines pulled out for their callee names. Picking
a regex first and reporting what it caught is how a census misses the thing it
was run to find.

Usage: python3 residual_call_census.py <runs-dir>          # e.g. .../work
       python3 residual_call_census.py <a.log> <b.log> ...
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

INSTR = re.compile(
    r"instrumented (\d+) complete path\(s\) across (\d+) unit\(s\)")
# Callee ids look like `sol:@C@ImmutablesLib@F@hash#932`.
CALLEE = re.compile(r"sol:@C@(\w+)@F@(\w+)#(\d+)")
DEPTH = re.compile(r"(\d+) call site\(s\) are deeper than the call depth bound "
                   r"\((\d+)\)")


def bucket_key(line):
    """A category derived FROM the line, not chosen in advance.

    Everything after the tool prefix up to the first '(' or ';' -- enough to
    separate the kinds of warning, short enough that two instances of the same
    kind land together.
    """
    s = line.strip()
    for pre in ("WARNING: ", "ERROR: "):
        if s.startswith(pre):
            s = s[len(pre):]
            break
    s = s.replace("--solidity-path-coverage: ", "")
    s = re.split(r"[(;]", s, 1)[0]
    s = re.sub(r"\b\d+\b", "N", s)
    return s.strip()[:90]


def census(path):
    text = Path(path).read_text(errors="replace")
    lines = text.splitlines()
    kinds = Counter()
    depth_callees, degraded_callees = set(), set()
    depth_counts = []
    instr = None
    for ln in lines:
        s = ln.strip()
        if instr is None:
            m = INSTR.search(s)
            if m:
                instr = (int(m.group(1)), int(m.group(2)))
        if not (s.startswith("WARNING:") or s.startswith("ERROR:")):
            continue
        kinds[bucket_key(s)] += 1
        m = DEPTH.search(s)
        if m:
            depth_counts.append((int(m.group(1)), int(m.group(2))))
            depth_callees.update(f"{c}.{f}#{i}" for c, f, i in CALLEE.findall(s))
        elif "withdraw" in s and "call site" in s:
            degraded_callees.update(
                f"{c}.{f}#{i}" for c, f, i in CALLEE.findall(s))
    return {"lines": len(lines), "instr": instr, "kinds": kinds,
            "depth_callees": sorted(depth_callees), "depth_counts": depth_counts,
            "degraded_callees": sorted(degraded_callees)}


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    targets = []
    for a in argv[1:]:
        p = Path(a)
        if p.is_dir():
            targets += sorted(p.glob("*/run.log")) + sorted(p.glob("*.log"))
        else:
            targets.append(p)
    if not targets:
        sys.exit("no logs found")

    print("## What the tool already said about unexpanded call sites\n")
    all_depth = defaultdict(set)
    for t in targets:
        c = census(t)
        unit = t.parent.name if t.name == "run.log" else t.stem
        print(f"--- {unit}   ({c['lines']} log line(s))")
        print("    " + (f"instrumented {c['instr'][0]} path(s) across "
                        f"{c['instr'][1]} unit(s)" if c["instr"]
                        else "NO instrumentation line -- this run died before "
                             "enumerating"))
        if not c["kinds"]:
            print("    no WARNING/ERROR lines at all")
        for k, n in c["kinds"].most_common():
            print(f"      {n:>5} x  {k}")
        if c["depth_counts"]:
            n, bound = c["depth_counts"][0]
            print(f"    DEPTH BOUND {bound}: {n} unexpanded call site(s) named"
                  + (f" -- {', '.join(c['depth_callees'])}"
                     if c["depth_callees"] else " (none named)"))
            for cal in c["depth_callees"]:
                all_depth[cal.split(".", 1)[0]].add(cal)
        if c["degraded_callees"]:
            print(f"    WITHDRAWN call site(s): "
                  f"{', '.join(c['degraded_callees'])}")
        print()

    print("=" * 74)
    if not all_depth:
        print("  No run named a call site past the depth bound. Then the 0/8 is "
              "NOT the depth\n  bound, and the recorded scope explanation "
              "survives -- but say which warning the\n  earlier reading came "
              "from, because it came from somewhere.")
        return 1
    print("  Contracts owning a call site left UNEXPANDED at the depth bound:")
    for c in sorted(all_depth):
        print(f"    {c}: {', '.join(sorted(all_depth[c]))}")
    print("\n  A contract listed here IS called from the units -- its call site "
          "is simply past\n  the bound, so paths through it are MERGED rather "
          "than enumerated. That is a\n  DIFFERENT finding from 'a library-only "
          "compilation unit cannot be served', and\n  wherever the gate table "
          "says the latter about one of these, the attribution is\n  wrong. It "
          "does NOT by itself say the decisions would be reached at a higher "
          "bound;\n  that costs a run.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
