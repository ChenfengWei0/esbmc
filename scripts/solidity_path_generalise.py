#!/usr/bin/env python3
"""Drive ESBMC's Solidity complete-path generalisation loop.

The tool measures; this script decides. That split is deliberate and is kept
everywhere below: ESBMC never parses its own report, never chooses a ladder, and
never applies a shrink -- it answers exactly the query it is handed. Every policy
decision (which ladder, which span, when to stop) lives here, so changing policy
never touches the verifier.

The loop, and why each step is the shape it is:

  1. ENUMERATE.  `--solidity-path-coverage --cov-report-json` gives, per complete
     path, its identity (enc, depth) and a counterexample. `depth` matters as
     much as `enc`: a stage-2 query identifies a path by `tr == enc && cnt ==
     depth`, and the `cnt` conjunct is what stops a longer path whose 64-bit `tr`
     wrapped from answering for a shorter one.

  2. BRACKET (geometric).  A first linear ladder cannot work on a 256-bit input:
     any span wide enough to contain the boundary makes the resolution useless.
     So round 1 probes at 0, 1, 2, 4, ... 2^k. That brackets the bound within a
     factor of two whatever its magnitude, in ONE run.

     This replaces the rule the design originally recorded -- "take the span from
     the nearest sibling counterexample". That rule was measured NOT to work: a
     solver counterexample can sit arbitrarily far from the boundary, and on the
     first contract tried it sat at 2^256-1, which is the whole type. The bracket
     uses only the path's OWN verdicts, so it does not depend on where some other
     path's counterexample happened to land.

  3. REFINE (linear, inside the bracket).  Each further round divides the
     resolution by (probes+1) again, so precision is logarithmic in ROUNDS while
     every round stays a single batch. It never becomes an adaptive
     query-per-step search, which is what sank the withdrawn widening route.

  4. SUBTRACT.  Zero queries: path domains partition the input space, so an input
     in this path's outer box and in no sibling's must walk this path. ESBMC does
     this and prints a candidate region per path.

  5. CERTIFY.  `assume(box); assert(tr == pi)`. SUCCESSFUL means the region is
     certified. FAILED comes with a witness input inside the box that leaves the
     path, and ESBMC prints the exact cut that excludes it while keeping the
     path's own counterexample -- one refutation, one cut, no bisection. This
     script applies that cut and retries.

A candidate region is NEVER trusted because it was subtracted. Subtraction is
sound only if path enumeration is complete for the unit, so every region goes
through the independent certification query before it is reported as certified.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

UINT256_MAX = (1 << 256) - 1


def run(esbmc, sol, contract, extra, max_tx, timeout, cwd):
    cmd = [esbmc, "--sol", os.path.abspath(sol), "--contract", contract,
           "--solidity-path-coverage", "--solidity-max-tx", str(max_tx),
           "--result-only", "--memlimit", "8g"] + extra
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=timeout)
    return p.stdout + p.stderr


def parse_int(s):
    s = s.strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s)


def enumerate_paths(esbmc, sol, contract, unit, max_tx, timeout, cwd):
    """Step 1. Returns [(enc, depth, {coord: ce_value})] for `unit`."""
    run(esbmc, sol, contract, ["--cov-report-json"], max_tx, timeout, cwd)
    with open(os.path.join(cwd, "cov-report.json")) as f:
        rep = json.load(f)
    out = []
    for c in rep.get("claims", []):
        if c.get("function") != unit or c.get("status") != "F":
            continue
        if "path_id" not in c or "path_depth" not in c:
            continue
        ce = {}
        for n, v in (c.get("inputs") or {}).items():
            ce[n] = parse_int(v)
        for n, v in (c.get("entry_storage") or {}).items():
            ce["state." + n] = parse_int(v)
        out.append((int(c["path_id"]), int(c["path_depth"]), ce))
    # Same enc can appear once per transaction instance; keep one of each.
    seen, uniq = set(), []
    for enc, depth, ce in out:
        if enc in seen:
            continue
        seen.add(enc)
        uniq.append((enc, depth, ce))
    return uniq


def geometric_values(limit):
    """Round-1 ladder: magnitude-independent, one run."""
    vals, v = [0], 1
    while v <= limit:
        vals.append(v)
        v *= 2
    vals.append(limit)
    return sorted(set(vals))


BOX_RE = re.compile(
    r"path enc=(\d+) depth=\d+ OUTER box \(D_path is CONTAINED in it\): (.*)")
BRACKET_RE = re.compile(r"path enc=(\d+) BRACKET \(refine[^)]*\): (.*)")
REGION_RE = re.compile(
    r"path enc=(\d+) CERTIFIED region after subtracting sibling outer boxes "
    r"\(zero queries\): ([^—]*)(— WARNING.*)?")
SHRINK_RE = re.compile(r"retry with (\S+) in \[(\d+), (\d+)\]")


def parse_intervals(text):
    # Scanned, not split: an interval contains ", " itself, so splitting on it
    # cuts every interval in half and silently yields nothing.
    return {m.group(1): (int(m.group(2)), int(m.group(3)))
            for m in re.finditer(r"(\S+) in \[(\d+), (\d+)\]", text)}


def brackets_for(coord, brackets):
    """Where the SEPARATION boundary still is, per the bracket report.

    A bracket that runs into the type limit (upper ending at 2^256-1, or lower
    starting at 0) is not a separation point -- it says "no bound was found
    inside the type", i.e. the bound IS the type limit. Refining towards it
    keeps the span at the full type range and the loop never narrows, which is
    exactly what it did before this was excluded.
    """
    lo, hi = None, None
    for txt in brackets.values():
        for m in re.finditer(
                re.escape(coord) + r" (upper|lower) in [\[(](\d+), (\d+)[\])]",
                txt):
            a, b = int(m.group(2)), int(m.group(3))
            if m.group(1) == "upper" and b >= UINT256_MAX:
                continue
            if m.group(1) == "lower" and a <= 0:
                continue
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    return (lo, hi) if lo is not None else None


def outer_round(esbmc, sol, contract, unit, paths, coords, pins, probes,
                max_tx, timeout, cwd, spans=None, geometric=False):
    """Steps 2-4: one batch. Returns (boxes, brackets, regions, warned)."""
    spec_coords = []
    for c in coords:
        if geometric:
            spec_coords.append(
                {"name": c, "values": [str(v)
                                       for v in geometric_values(UINT256_MAX)]})
        else:
            lo, hi = spans[c]
            spec_coords.append({"name": c, "lo": str(lo), "hi": str(hi)})
    spec = {"unit": unit, "probes": probes, "coords": spec_coords,
            "pin": [{"name": n, "value": str(v)} for n, v in pins.items()],
            "paths": [{"enc": e, "depth": d,
                       "ce": {k: str(v) for k, v in ce.items() if k in coords}}
                      for e, d, ce in paths]}
    path = os.path.join(cwd, "outer.json")
    with open(path, "w") as f:
        json.dump(spec, f)
    log = run(esbmc, sol, contract, ["--path-cov-outer-box", path],
              max_tx, timeout, cwd)
    boxes, brackets, regions, warned = {}, {}, {}, set()
    for line in log.splitlines():
        m = BOX_RE.search(line)
        if m:
            boxes[int(m.group(1))] = parse_intervals(m.group(2))
        m = BRACKET_RE.search(line)
        if m:
            brackets[int(m.group(1))] = m.group(2)
        m = REGION_RE.search(line)
        if m:
            regions[int(m.group(1))] = parse_intervals(m.group(2))
            if m.group(3):
                warned.add(int(m.group(1)))
    return boxes, brackets, regions, warned


def certify(esbmc, sol, contract, unit, enc, depth, box, ce, pins,
            max_tx, timeout, cwd):
    """Step 5. Returns (ok, suggested_box_or_None)."""
    spec = {"unit": unit, "enc": enc, "depth": depth,
            "ce": {k: str(v) for k, v in ce.items()},
            "box": [{"name": n, "lo": str(lo), "hi": str(hi)}
                    for n, (lo, hi) in box.items()] +
                   [{"name": n, "lo": str(v), "hi": str(v)}
                    for n, v in pins.items()]}
    path = os.path.join(cwd, "cert.json")
    with open(path, "w") as f:
        json.dump(spec, f)
    log = run(esbmc, sol, contract,
              ["--path-cov-certify", path, "--cov-report-json"],
              max_tx, timeout, cwd)
    if "VERIFICATION SUCCESSFUL" in log:
        return True, None
    m = SHRINK_RE.search(log)
    if not m:
        return False, None
    nb = dict(box)
    nb[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return False, nb


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--esbmc", default="esbmc")
    ap.add_argument("--sol", required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--unit", required=True)
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--probes", type=int, default=16)
    ap.add_argument("--refine-rounds", type=int, default=3)
    ap.add_argument("--shrink-rounds", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--pin", action="append", default=[],
                    help="coord=value, e.g. state.bal=50. Pinned coordinates "
                         "are NOT generalised; every region reported is a "
                         "statement about that slice and carries the pin.")
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()

    pins = {}
    for p in args.pin:
        n, _, v = p.partition("=")
        pins[n] = parse_int(v)

    cwd = args.workdir or tempfile.mkdtemp(prefix="pathgen-")
    os.makedirs(cwd, exist_ok=True)
    print(f"[workdir] {cwd}")

    paths = enumerate_paths(args.esbmc, args.sol, args.contract, args.unit,
                            args.max_tx, args.timeout, cwd)
    if not paths:
        print("[enumerate] no witnessed path for this unit; nothing to "
              "generalise. That is a result, not an error: a path with no "
              "counterexample has no known member of its domain to keep, so "
              "there is nothing to grow a region around.")
        return 1
    print(f"[enumerate] {len(paths)} witnessed path(s): "
          + ", ".join(f"enc={e} depth={d}" for e, d, _ in paths))

    coords = sorted({k for _, _, ce in paths for k in ce} - set(pins))
    if not coords:
        print("[coords] every coordinate is pinned; nothing to generalise")
        return 1
    print(f"[coords] {', '.join(coords)}"
          + (f"   [pinned: {pins}]" if pins else ""))

    # Round 1: geometric bracket.
    _, brackets, regions, warned = outer_round(
        args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
        args.probes, args.max_tx, args.timeout, cwd, geometric=True)
    print(f"[bracket] {brackets}")

    # Rounds 2..N: linear inside the union of the brackets, per coordinate.
    spans = {c: (brackets_for(c, brackets) or (0, UINT256_MAX))
             for c in coords}
    for r in range(args.refine_rounds):
        _, brackets, regions, warned = outer_round(
            args.esbmc, args.sol, args.contract, args.unit, paths, coords, pins,
            args.probes, args.max_tx, args.timeout, cwd, spans=spans)
        print(f"[refine {r+1}] spans={spans} regions={regions}"
              + (f" UNSEPARATED={sorted(warned)}" if warned else ""))
        new = {c: (brackets_for(c, brackets) or spans[c]) for c in coords}
        if new == spans:
            break
        spans = new

    # Certify every candidate, shrinking on the witness when refuted.
    ok, failed = {}, {}
    for enc, depth, ce in paths:
        box = regions.get(enc)
        if box is None:
            failed[enc] = "no fully bounded region was measured"
            continue
        if enc in warned:
            # Not fatal: certification is the arbiter. But say it, because a
            # region that a cut could not separate is EXPECTED to be refuted.
            print(f"[certify enc={enc}] region overlaps an unseparated sibling; "
                  f"certifying anyway, the query is what decides")
        for _ in range(args.shrink_rounds):
            good, nb = certify(args.esbmc, args.sol, args.contract, args.unit,
                               enc, depth, box, ce, pins, args.max_tx,
                               args.timeout, cwd)
            if good:
                ok[enc] = box
                break
            if nb is None or nb == box:
                failed[enc] = "refuted with no single-coordinate cut available"
                break
            print(f"[shrink enc={enc}] {box} -> {nb}")
            box = nb
        else:
            failed[enc] = "shrink round budget exhausted"

    print("\n=== CERTIFIED REGIONS ===")
    for enc, box in sorted(ok.items()):
        pin_txt = "".join(f", {n} == {v}" for n, v in pins.items())
        print(f"  enc={enc}: "
              + ", ".join(f"{n} in [{lo}, {hi}]" for n, (lo, hi) in box.items())
              + pin_txt)
    for enc, why in sorted(failed.items()):
        print(f"  enc={enc}: NOT CERTIFIED — {why}; this path falls back to its "
              f"concrete counterexample test")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
