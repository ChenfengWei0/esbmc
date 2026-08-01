#!/usr/bin/env python3
"""A coordinate cut to a SINGLE POINT: is that the ladder's limit, or the harness's?

EXECUTION_PLAN section 4 names "the share of coordinates cut to a single point"
as one of two headline numbers for subgoal 3. As it stands that share mixes two
different facts, and only one of them is about the method:

  (a) THE LADDER'S LIMIT. An input coordinate the generalisation could not widen
      beyond its counterexample value. This is the number section 4 wants.
  (b) THE HARNESS'S ENTRY STATE. At --solidity-max-tx 1 contract state is NOT
      havoc'd, so a transaction starts from whatever the constructor left. A
      state coordinate is then a single point BY CONSTRUCTION -- `[0,0]` is a
      true statement and a trivial one, and it says nothing about how well the
      ladder generalises.

MEASURED, and this is why it is not a hypothetical: farming/startFarming's driver
log prints, for EVERY one of its 26 paths,

    ⚠ the point(s) on state._distributor, state._totalSupply came from a
      ONE-VALUE candidate list, which CANNOT distinguish a genuine point domain
      from this path having NO inputs at all under the current pins

and every region it certified then carries `state._distributor in [0,0]` and
`state._totalSupply in [0,0]`. Those are the constructor's values. The tool said
so 26 times and nothing downstream has ever read it.

WHAT THIS SEPARATES, and how it decides. The region text is the driver's own
printed form, parsed with the SAME two regexes put_all.py uses -- one grammar,
two readers -- and each coordinate is attributed by its NAME:

    state.<x>   a contract state variable   -> class (b), entry state
    msg.*/tx.*/block.*  environment          -> class (b), pinned by the harness
    anything else                            -> class (a), a call argument

The name is the classification the DRIVER already made when it printed
`state.`-prefixed coordinates, so this reads a decision rather than re-deriving
one. A coordinate whose class cannot be decided is counted as UNCLASSIFIED and
printed by name; it is never folded into either bucket, because a residue folded
into a total is how a number stops meaning what its label says.

THE BINARY IS PRINTED FIRST. These records carry the build that produced them;
quoting a distribution without saying which build measured it is the failure this
repository has already paid for three times.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/home/samson/workspace/esbmc")
DEFAULT = [REPO / "notes/coverage/certify/results.jsonl",
           REPO / "notes/coverage/certify/poc_results.jsonl"]

# Byte for byte put_all.py's parsers, which are byte for byte the driver's own
# printers. Three readers of one grammar is already one too many; a fourth
# spelling of it would be the drift this comment exists to prevent.
INTERVAL_RE = re.compile(r"(\S+) in \[(\d+), (\d+)\](?: \\ \{([0-9, ]+)\})?")
PIN_RE = re.compile(r"(\S+) == (\d+)")

ENV_PREFIXES = ("msg.", "tx.", "block.")


def classify(name):
    if name.startswith("state."):
        return "state"
    if name.startswith(ENV_PREFIXES):
        return "env"
    if re.match(r"^[A-Za-z_]\w*$", name):
        return "input"
    return "unclassified"


def main(argv):
    paths = [Path(p) for p in argv[1:]] or DEFAULT
    files = [p for p in paths if p.exists()]
    if not files:
        print("no results file; run certify_all.py / certify_poc.py first")
        return 1

    binaries = Counter()
    # (class) -> [points, intervals]
    tally = defaultdict(lambda: [0, 0])
    unclassified = Counter()
    # per-benchmark, so a single dominant contract cannot masquerade as a corpus
    per_bench = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    n_regions = 0
    pinned_names = Counter()

    for f in files:
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            binaries[json.dumps(r.get("binary"), sort_keys=True)] += 1
            if r.get("bucket") != "CERTIFIED":
                continue
            bench = r.get("benchmark") or r.get("poc") or "?"
            for _enc, text in (r.get("certified") or {}).items():
                n_regions += 1
                seen = set()
                for m in INTERVAL_RE.finditer(text):
                    name, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
                    seen.add(name)
                    k = classify(name)
                    idx = 0 if lo == hi else 1
                    tally[k][idx] += 1
                    per_bench[bench][k][idx] += 1
                    if k == "unclassified":
                        unclassified[name] += 1
                for m in PIN_RE.finditer(text):
                    name = m.group(1)
                    if name in seen:
                        continue
                    # A PIN is not a measurement at all -- it is a value the
                    # driver fixed before measuring. Counted separately so it can
                    # never be read as "the ladder cut this to a point".
                    pinned_names[name] += 1

    print("## which build produced these records\n")
    for b, n in binaries.most_common():
        print(f"  {n:>4}  {b}")
    if len(binaries) > 1:
        print("\n  ** MORE THAN ONE BINARY IS REPRESENTED. The numbers below "
              "are a mixture and describe no single build. **")

    print(f"\n## {n_regions} certified region(s)\n")
    print(f"{'class':<14}{'single points':>15}{'intervals':>12}"
          f"{'point share':>14}")
    order = ["input", "state", "env", "unclassified"]
    for k in order:
        pts, iv = tally[k]
        tot = pts + iv
        share = f"{100.0 * pts / tot:.0f}%" if tot else "-"
        print(f"{k:<14}{pts:>15}{iv:>12}{share:>14}")
    ip, ii = tally["input"]
    sp, si = tally["state"]
    print()
    print(f"  THE NUMBER SECTION 4 ASKS FOR is the `input` row: "
          f"{ip} of {ip + ii} call-argument coordinate(s) were cut to a single "
          f"point"
          + (f" = {100.0 * ip / (ip + ii):.0f}%" if ip + ii else ""))
    print(f"  THE `state` ROW IS NOT THAT NUMBER. At --solidity-max-tx 1 the "
          f"entry state is the post-constructor state and is never havoc'd, so "
          f"a state coordinate at a point may be reporting the constructor "
          f"rather than the ladder. {sp} of {sp + si} state coordinate(s) are "
          f"points here.")
    if pinned_names:
        print(f"\n  PINNED (fixed BEFORE measuring, never a ladder result): " +
              ", ".join(f"{n} x{c}" for n, c in pinned_names.most_common(6)))
    if unclassified:
        print(f"\n  ** UNCLASSIFIED, named rather than folded in: " +
              ", ".join(f"{n} x{c}" for n, c in unclassified.most_common(10))
              + " **")

    print("\n## per benchmark, so one contract cannot stand in for the corpus\n")
    print(f"{'benchmark':<30}{'input pt/total':>16}{'state pt/total':>16}")
    for bench in sorted(per_bench):
        b = per_bench[bench]
        ip2, ii2 = b["input"]
        sp2, si2 = b["state"]
        print(f"{bench:<30}{f'{ip2}/{ip2 + ii2}':>16}"
              f"{f'{sp2}/{sp2 + si2}':>16}")

    print("\nThis prints a DISTRIBUTION and makes no pass/fail judgement: "
          "section 4's threshold is the user's to set and has never been set, "
          "and picking it now -- with the scores visible -- would be choosing "
          "the bar after seeing them.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
