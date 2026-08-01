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


def ensure_project(bench, flat):
    proj = os.path.join(OUT, bench)
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
    if not os.path.exists(CERT):
        sys.exit(f"no certify sweep at {CERT}")
    rows = []
    for line in open(CERT):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("bucket") != "CERTIFIED":
            continue
        for enc, text in (r.get("certified") or {}).items():
            rows.append((r["benchmark"], r["unit"], int(enc), text))

    print(f"=== {len(rows)} CERTIFIED region(s) recorded by stage 2 ===")
    results = []
    for bench, unit, enc, text in rows:
        if bench not in BENCHES:
            print(f"  SKIP {bench}.{unit} enc={enc}: unknown benchmark key")
            continue
        flat_name, contract = BENCHES[bench]
        flat = os.path.join(INPUTS, flat_name)
        ast = flat + ".solast"
        region, holes, pins = parse_certified(text)
        if not region and not pins:
            print(f"  SKIP {bench}.{unit} enc={enc}: the recorded region "
                  f"parsed EMPTY, which is a PARSER failure, not an empty "
                  f"region -- refusing to emit a PUT over nothing")
            continue
        proj = ensure_project(bench, flat)
        wd = os.path.join(OUT, "_wd", f"{bench}__{unit}__{enc}")
        os.makedirs(wd, exist_ok=True)
        cmd = [sys.executable, PUT, "--esbmc", ESBMC, "--sol", flat,
               "--ast", ast, "--contract", contract, "--unit", unit,
               "--enc", str(enc), "--region", json.dumps(region),
               "--holes", json.dumps(holes),
               "--forge-project", proj, "--workdir", wd, "--timeout", "600"]
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
