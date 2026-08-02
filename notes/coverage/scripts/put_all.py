#!/usr/bin/env python3
"""Stage 4 sweep: one PUT per CERTIFIED region, and a table of what came out.

The funnel's last column was 0 for a wiring reason, so the first thing stage 4
needs is a number that is MEASURED rather than hand-run: this script walks
every certified region stage 2 recorded and asks `solidity_path_put.py` for a
PUT, then prints what each one produced and why.

THE REGION IS PARSED BACK OUT OF THE SWEEP'S OWN PROSE, with the SAME regexes
the driver prints it with (`<name> in [lo, hi]` optionally `\\ {v, w}`, and
`<name> == <v>` for a pin). That is deliberate: re-deriving the region by
re-running stage 2 would make stage 4's input a DIFFERENT measurement from the
one the funnel counts, and the two would drift the moment either sweep is
re-run. Reading the recorded artefact keeps `A` and `B` the same 7 regions.

esbmc is run ONE AT A TIME, by construction: each PUT costs two sequential
esbmc invocations and this loop is serial.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
NOTES = os.path.abspath(os.path.join(HERE, "..", ".."))
INPUTS = os.path.join(NOTES, "coverage", "inputs")
CERT = os.path.join(NOTES, "coverage", "certify", "results.jsonl")
# The PoC set's own stage-2 sweep. Two files rather than one because they are
# two different questions: on a real contract "not certified" mixes the
# method's limits with the contract's difficulty, while a PoC is one shape and
# nothing else. Which file a row came from therefore travels with the row.
POC_CERT = os.path.join(NOTES, "coverage", "certify", "poc_results.jsonl")
POC_SRC = os.path.join(NOTES, "coverage", "poc")
OUT = os.path.join(NOTES, "coverage", "put_roundtrip")
ESBMC = os.path.join(REPO, "build", "src", "esbmc", "esbmc")
PUT = os.path.join(REPO, "scripts", "solidity_path_put.py")
FORGE_STD = os.path.join(
    NOTES, "coverage-comparison", "_foundry_roundtrip", "aqua_forge",
    "lib", "forge-std")

# benchmark key -> (flat basename, contract under test)
BENCHES = {
    "aqua_Aqua": ("aqua__Aqua.flat.sol", "Aqua"),
    "cross_chain_swap_EscrowSrc": ("cross-chain-swap__EscrowSrc.flat.sol",
                                   "EscrowSrc"),
    "cross_chain_swap_EscrowDst": ("cross-chain-swap__EscrowDst.flat.sol",
                                   "EscrowDst"),
    "farming": ("farming__FarmingPool.flat.sol", "FarmingPool"),
}

FOUNDRY_TOML = """[profile.default]
src = "src"
test = "test"
libs = ["lib"]
via_ir = true
optimizer = true
optimizer_runs = 200
"""

# Byte for byte the driver's own printers (solidity_path_generalise.py:800,
# and the `, {n} == {v}` pin suffix built at its report block). One grammar,
# two readers; if the driver ever changes how it prints a region this parser
# must fail loudly rather than silently read half of it, which is why the
# caller refuses a row whose region parses empty.
INTERVAL_RE = re.compile(r"(\S+) in \[(\d+), (\d+)\](?: \\ \{([0-9, ]+)\})?")
PIN_RE = re.compile(r"(\S+) == (\d+)")


def parse_certified(text):
    region, holes = {}, {}
    for m in INTERVAL_RE.finditer(text):
        region[m.group(1)] = [int(m.group(2)), int(m.group(3))]
        if m.group(4):
            holes[m.group(1)] = sorted(
                {int(v) for v in m.group(4).split(",") if v.strip()})
    consumed = set(region)
    pins = {}
    for m in PIN_RE.finditer(text):
        # `x in [0, 5]` also matches `PIN_RE` on nothing, but a coordinate that
        # is already an interval must never be re-read as a pin.
        if m.group(1) in consumed:
            continue
        pins[m.group(1)] = int(m.group(2))
    return region, holes, pins


def ensure_project(name, flat, shared=None):
    """The forge project a PUT is written into.

    `shared` names ONE project every source is copied into, instead of one
    project per source. The corpus keeps a project per benchmark, because a
    benchmark's flat is 70-180 KB and compiling four of them for every test run
    is the cost of a mistake. The PoC set is the opposite case: 35 contracts of
    ~1-8 KB whose whole point is to be measured TOGETHER, and one project means
    ONE `forge test` produces the whole table rather than 35 that have to be
    added up by hand -- and a total added up by hand is a total nobody can
    re-run.
    """
    proj = os.path.join(OUT, shared or name)
    for d in ("src", "test", "lib"):
        os.makedirs(os.path.join(proj, d), exist_ok=True)
    with open(os.path.join(proj, "foundry.toml"), "w") as f:
        f.write(FOUNDRY_TOML)
    dst = os.path.join(proj, "src", os.path.basename(flat))
    if not os.path.exists(dst):
        with open(flat, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
    lib = os.path.join(proj, "lib", "forge-std")
    if not os.path.exists(lib):
        os.symlink(FORGE_STD, lib)
    return proj


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", choices=("focus", "whole"), default="focus",
                    help="which of the two settled command lines to run in. "
                         "Default `focus`, which with --max-tx 1 is the GATE "
                         "cell -- the one every PUT in this directory was "
                         "produced in before it was an argument.")
    ap.add_argument("--poc", action="store_true",
                    help="read the PoC SET's stage-2 sweep "
                         "(certify/poc_results.jsonl) instead of the corpus's, "
                         "and write every PUT into ONE shared forge project so "
                         "a single `forge test` produces the whole table. The "
                         "two sweeps answer different questions and their rows "
                         "must never share a table: on a real contract 'not "
                         "certified' mixes the method's limits with the "
                         "contract's difficulty, while a PoC is one shape.")
    ap.add_argument("--max-tx", type=int, default=1)
    ap.add_argument("--auto-unwind", type=int, default=0,
                    help="passed to the driver: on an UNDECIDED-TRUNCATED "
                         "ladder, widen the loops the tool NAMED and retry, up "
                         "to N times. aqua `dock` is the recorded case.")
    args = ap.parse_args()
    cert_path = POC_CERT if args.poc else CERT
    if not os.path.exists(cert_path):
        sys.exit(f"no certify sweep at {cert_path}")
    rows = []
    for line in open(cert_path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("bucket") != "CERTIFIED":
            continue
        # WHICH SWEEP A ROW CAME FROM IS READ OFF THE ROW, not off the flag.
        # certify_poc.py keys its records on `poc`, certify_all.py on
        # `benchmark`. Detecting it here means pointing --cert at the wrong file
        # cannot silently produce rows resolved against the wrong sources; it
        # produces a row this loop refuses by name.
        key = r.get("benchmark") or r.get("poc")
        is_poc = "poc" in r
        for enc, text in (r.get("certified") or {}).items():
            rows.append((key, is_poc, r["unit"], int(enc), text))

    print(f"=== {len(rows)} CERTIFIED region(s) recorded by stage 2 "
          f"({os.path.basename(cert_path)}) ===")
    results = []
    for bench, is_poc, unit, enc, text in rows:
        if is_poc:
            # certify_poc.py runs the driver with `--contract <stem>`, so the
            # contract name IS the file stem; resolving it any other way would
            # be a second convention that can disagree with the sweep's.
            flat = os.path.join(POC_SRC, bench + ".sol")
            contract = bench
            if not os.path.exists(flat):
                print(f"  SKIP {bench}.{unit} enc={enc}: no PoC source at "
                      f"{flat}")
                continue
        elif bench not in BENCHES:
            print(f"  SKIP {bench}.{unit} enc={enc}: unknown benchmark key")
            continue
        else:
            flat_name, contract = BENCHES[bench]
            flat = os.path.join(INPUTS, flat_name)
        # ---- TWO AST NAMING CONVENTIONS, AND ONLY ONE IS RIGHT PER SOURCE ----
        #
        # The corpus flats are named `<x>.flat.sol` and their AST is generated
        # ALONGSIDE as `<x>.flat.sol.solast` -- suffix APPENDED. The PoC set uses
        # `Path.with_suffix('.solast')` (certify_poc.py), i.e. the extension is
        # REPLACED: `D09_ValueGate.solast`, not `D09_ValueGate.sol.solast`.
        #
        # Appending for both cost the entire first PoC stage-4 sweep: all seven
        # certified regions came back `exit=6 0.0s emitted=[]`, which reads like
        # an emitter that produced nothing and is actually
        #
        #     ERROR: failed to open input file .../D09_ValueGate.sol.solast
        #
        # -- esbmc dying on the command line before it verified anything. The
        # refusal message the driver prints for that outcome ("This is an
        # EMISSION outcome, not a property of the region") is correct and was
        # still misleading, because the emission never ran.
        #
        # The PoC branch follows certify_poc.py's convention rather than
        # inventing a third: whichever file stage 2 generated is the one stage 4
        # must read, or the two stages are looking at different ASTs.
        ast = (os.path.splitext(flat)[0] + ".solast") if is_poc \
            else (flat + ".solast")
        if not os.path.exists(ast):
            print(f"  SKIP {bench}.{unit} enc={enc}: no AST at {ast}")
            continue
        region, holes, pins = parse_certified(text)
        if not region and not pins:
            print(f"  SKIP {bench}.{unit} enc={enc}: the recorded region "
                  f"parsed EMPTY, which is a PARSER failure, not an empty "
                  f"region -- refusing to emit a PUT over nothing")
            continue
        proj = ensure_project(bench, flat, shared="poc" if is_poc else None)
        wd = os.path.join(OUT, "_wd", f"{bench}__{unit}__{enc}")
        os.makedirs(wd, exist_ok=True)
        cmd = [sys.executable, PUT, "--esbmc", ESBMC, "--sol", flat,
               "--ast", ast, "--contract", contract, "--unit", unit,
               "--enc", str(enc), "--region", json.dumps(region),
               "--holes", json.dumps(holes),
               "--forge-project", proj, "--workdir", wd, "--timeout", "600",
               # The CELL is a property of the measurement, not a default of
               # this sweep. INVOCATION_DECISIONS.md prints two command lines
               # and forbids quoting one into the other's table, so it is an
               # argument here and it is printed with the result.
               "--scope", args.scope, "--max-tx", str(args.max_tx),
               "--auto-unwind", str(args.auto_unwind)]
        for n, v in pins.items():
            cmd += ["--pin", f"{n}={v}"]
        print(f"\n--- {bench}.{unit} enc={enc} ---")
        p = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(p.stdout)
        sys.stdout.write(p.stderr)
        j = os.path.join(wd, "put.json")
        rec = json.load(open(j)) if os.path.exists(j) else {}
        results.append((bench, unit, enc, p.returncode, rec))

    print("\n" + "=" * 84)
    print("STAGE 4: certified region -> PUT with oracle")
    # THE CELL TRAVELS WITH THE TABLE. A run of the ARTEFACT cell may not be
    # quoted into the branch-coverage gate table and a run of the GATE cell may
    # not be quoted as the method's reach, so the table has to say which it is
    # rather than leaving the reader to remember the flags.
    cells = sorted({(r[4].get("cell") or {}).get("name", "UNRECORDED")
                    for r in results})
    print(f"CELL: scope={args.scope} --solidity-max-tx={args.max_tx} "
          f"-> {', '.join(cells) if cells else 'no run recorded one'}"
          + (f", --auto-unwind {args.auto_unwind}" if args.auto_unwind else ""))
    if len(cells) > 1:
        print("** MIXED CELLS IN ONE TABLE. These rows are not comparable and "
              "the table must not be quoted anywhere. **")
    print("=" * 84)
    print(f"{'benchmark':<28}{'unit':<16}{'enc':>5}{'rc':>4}"
          f"{'fuzz':>6}{'asserts':>9}  outcome")
    n_put = n_fuzz = n_oracle = n_both = 0
    for bench, unit, enc, rc, rec in results:
        st = rec.get("stats") or {}
        fz, ar = st.get("fuzz_params", 0), st.get("asserts", 0)
        if rc == 0:
            n_put += 1
            n_fuzz += 1 if fz else 0
            n_oracle += 1 if ar else 0
            n_both += 1 if (fz and ar) else 0
            outcome = os.path.basename(rec.get("file", ""))
        elif rc == 2:
            outcome = "REFUSED: " + str(rec.get("refused"))
        else:
            outcome = "REFUSED (see log above)"
        print(f"{bench:<28}{unit:<16}{enc:>5}{rc:>4}{fz:>6}{ar:>9}  {outcome}")
    print()
    print(f"  PUTs emitted                     : {n_put} of {len(results)} "
          f"certified region(s)")
    print(f"  ... of which carry FUZZ parameters: {n_fuzz}")
    print(f"  ... of which carry an ORACLE      : {n_oracle}")
    print(f"  ... of which carry BOTH           : {n_both}")
    print()
    print("  A PUT with no fuzz parameter is still a PUT -- it carries the")
    print("  certified region as an established entry state and the oracle --")
    print("  but it is ONE point of the region, not a fuzz test over it, and")
    print("  the two must not be added together.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
