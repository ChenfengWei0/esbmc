#!/usr/bin/env python3
"""R2's interval bounds: LITERAL CONSTANTS versus EXPRESSIONS the path holds.

An R2 rung is `a <= post <= b` or `a <= post - pre <= b`. Two readings of what
may appear at `a` and `b`:

  A  literal constants only -- WHAT THE PIPELINE EMITS TODAY. The tool prints
     `post - pre in [1, 10]` (verbatim, regression
     solidity_path_cov_assert_delta_fits) and the renderer matches `(\\d+)` and
     nothing else (solidity_path_put.rung_assertions), so a rung carrying an
     expression would be DROPPED with `rung shape not rendered`.
  B  quantities the path already holds: the values the test rendered for its own
     coordinates, the pre-values it recorded, and the contract's own constants.

The proposal on the table was to adopt B, and its supporting argument was that A
cannot catch M2. THIS SCRIPT WAS BUILT TO TEST THAT ARGUMENT BEFORE ADOPTING IT,
with the discriminator written down first, and the argument DID NOT SURVIVE.

TARGET. bench/FeeVault `withdraw`, the path where the amount is accepted, a
discount APPLIES, and the fee stays under the cap:
`amount in [1, 3] ether`, `disc in [1, 249]`, so `rate = 250 - disc in [1, 249]`.

  reference  fee = amount * rate / 10000
  M1         fee = amount * rate / 100          (the fee is 100x, then capped)
  M2         the discount is never applied      (rate stays feeBps = 250)

THE CONSTANT IS COMPUTED, AND THE FIRST HAND-FIGURE WAS WRONG. `net = amount -
floor(amount*rate/10000)` is minimised at the CORNER amount = 1e18, rate = 249,
not at "the smallest amount with the largest fee" -- the largest fee (7.47e16)
needs amount = 3e18 and the two cannot co-occur. So the tightest valid constant
lower bound is 1e18 - 1e18*249/10000 = 975_100_000_000_000_000, and NOT the
~0.925e18 that both readings of the region first produced. Both are run, because
that difference turns out to be the whole result.

MEASURED, 10 fuzz seeds x 256 runs, forge 1.7.1, on M2:

    test_A_loose      net >= 0.925  ether               FAIL  0/10   <- escapes
    test_A_tight      net >= 0.9751 ether               FAIL 10/10
    test_A_tight_corner  (directed, amount=1e18,disc=249)FAIL 10/10
    test_B_expr       net > amount - amount*feeBps/1e4  FAIL 10/10

and on the reference all four pass; on M1, A_loose / A_tight / B all fail while
the directed corner PASSES (rate = 1 there, so even a 100x fee stays small).

⇒ THE PRE-REGISTERED CONDITION FIRED IN THE "RE-OPEN" DIRECTION. The prediction
was "M2 should survive the tight constant, or be hit very rarely"; it is caught
in 10 of 10 seeds. The M2 argument for adopting B is WITHDRAWN.

WHY THE PREDICTION MISSED, and this is the transferable part. The region
arithmetic is right: M2 violates the tight constant only for
`amount in [1e18, ~1.0001e18)`, about 5e-5 of the region. What is wrong is the
PROBABILITY MODEL -- it assumes uniform sampling and forge's fuzzer is not a
uniform sampler. The counterexamples say so directly:

    seed 3: [FAIL: A_tight ...] (runs: 1)   args=[1, 8333]
    seed 1: [FAIL: A_tight ...] (runs: 5)   args=[1, 249]

The raw fuzz input is the integer 1. forge heavily samples small integers and
type boundaries, and `bound(x, lo, hi)` maps those onto the interval's LOWER
EDGE -- which is exactly where a region-extremum constant is attained. A
constant bound is tight only at a corner, and this fuzzer aims at corners.

WHAT SURVIVES AS AN ARGUMENT FOR B, none of which is "it detects more":

  1. A's strength is entirely how tight the constant is, and 0.925 vs 0.9751 is
     the difference between 0/10 and 10/10. Both first attempts at that number
     were wrong.
  2. Computing 0.9751 requires the region extremum of `amount -
     amount*rate/10000` -- and a synthesiser able to do that already holds the
     expression. So "constants are easier to emit" is not established.
  3. B's diagnosis is semantic. On M2 it reports
     `1962641930044030767 <= 1962641930044030767` -- exact equality, the
     signature of "strictly better" being violated. A_tight reports one number
     being 1e14 below another, which does not name the fault.
  4. B is region-independent. Widen the region and the constant must be
     recomputed and necessarily loosens; the expression does not move.

Usage:  python3 r2_const_vs_expr.py [--seeds N]
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

SRC = pathlib.Path(__file__).resolve().parents[4] / "bench" / "FeeVault"

REF_BODY = """    function withdraw(uint256 amount) external returns (uint256 net) {
        uint256 d = deposits[msg.sender];
        require(amount > 0 && amount <= d);
        uint256 rate = feeBps;
        if (discountBps[msg.sender] > 0) rate = feeBps - discountBps[msg.sender];
        uint256 fee = amount * rate / 10000;
        if (fee > maxFee) fee = maxFee;
        net = amount - fee;
        deposits[msg.sender] = d - amount;
    }"""

M1_BODY = REF_BODY.replace("amount * rate / 10000", "amount * rate / 100")
M2_BODY = REF_BODY.replace(
    "        if (discountBps[msg.sender] > 0) rate = feeBps - discountBps[msg.sender];\n",
    "        // M2: the discount is never applied\n")

A_TIGHT = 10**18 - (10**18 * 249) // 10000     # 975100000000000000
A_LOOSE = 925 * 10**15                          # the careless figure

TEST = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// manual: true -- a DISCRIMINATOR, never counted in a conversion rate.
// One region, one path, four oracles over it.

import "forge-std/Test.sol";
import "../src/FeeVault.sol";

contract ABTest is Test {
    FeeVault v;
    address alice = address(0xA11CE);

    uint256 constant A_TIGHT = %d;
    uint256 constant A_LOOSE = %d;

    function setUp() public { v = new FeeVault(); }

    function _walk(uint256 amount, uint16 disc) internal returns (uint256) {
        v.setDiscount(alice, disc);
        vm.deal(alice, amount);
        vm.startPrank(alice);
        v.deposit{value: amount}();
        uint256 net = v.withdraw(amount);
        vm.stopPrank();
        return net;
    }

    function test_A_loose(uint256 amount, uint16 disc) public {
        amount = bound(amount, 1 ether, 3 ether);
        disc = uint16(bound(uint256(disc), 1, 249));
        assertGe(_walk(amount, disc), A_LOOSE, "A_loose");
    }

    function test_A_tight(uint256 amount, uint16 disc) public {
        amount = bound(amount, 1 ether, 3 ether);
        disc = uint16(bound(uint256(disc), 1, 249));
        assertGe(_walk(amount, disc), A_TIGHT, "A_tight");
    }

    // The corner the tight constant is attained at, driven DIRECTLY. Without it,
    // "A_tight passed" cannot be told apart from "the fuzzer never reached the
    // only inputs on which it could fail".
    function test_A_tight_corner() public {
        assertGe(_walk(1 ether, 249), A_TIGHT, "A_tight at the corner");
    }

    function test_B_expr(uint256 amount, uint16 disc) public {
        amount = bound(amount, 1 ether, 3 ether);
        disc = uint16(bound(uint256(disc), 1, 249));
        uint256 undiscounted = amount - amount * uint256(v.feeBps()) / 10000;
        assertGt(_walk(amount, disc), undiscounted, "B");
    }
}
""" % (A_TIGHT, A_LOOSE)

# Anchored on the LAST `]` before the name. The first version used
# `\\[(PASS|FAIL)[^\\]]*\\]`, which stops at the `]` closing an inner
# `args=[1, 8333]`, so every failing FUZZ row was silently unreadable -- exactly
# the two rows this experiment exists to read -- while the non-fuzz row came
# through and the summary looked clean.
ROW = re.compile(r"^\[(PASS|FAIL).*\]\s+(\w+)\(")
EXPECTED = ["test_A_loose", "test_A_tight", "test_A_tight_corner", "test_B_expr"]


def build(root, name, body):
    proj = root / ("ab_" + name)
    proj.mkdir(parents=True)
    shutil.copy(SRC / "foundry.toml", proj / "foundry.toml")
    (proj / "lib").mkdir()
    (proj / "lib" / "forge-std").symlink_to(SRC / "lib" / "forge-std")
    (proj / "src").mkdir()
    (proj / "test").mkdir()
    sol = (SRC / "src" / "FeeVault.sol").read_text()
    if sol.count(REF_BODY) != 1:
        sys.exit("bench/FeeVault's withdraw body has moved; this PoC pins it "
                 "verbatim so a silent divergence cannot pass as a result")
    (proj / "src" / "FeeVault.sol").write_text(sol.replace(REF_BODY, body))
    (proj / "test" / "AB.t.sol").write_text(TEST)
    return proj


def verdicts(proj, seed):
    cp = subprocess.run(["forge", "test", "--fuzz-seed", str(seed)],
                        cwd=str(proj), capture_output=True, text=True,
                        timeout=900)
    seen = {}
    for line in (cp.stdout + cp.stderr).splitlines():
        m = ROW.match(line.strip())
        if m:
            seen.setdefault(m.group(2), m.group(1))
    missing = [n for n in EXPECTED if n not in seen]
    if missing:
        sys.exit(f"{proj.name} seed {seed}: nothing readable about {missing}. "
                 f"A row that is not read is not a PASS.\n--- raw ---\n"
                 f"{cp.stdout}{cp.stderr}")
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    a = ap.parse_args()
    if shutil.which("forge") is None:
        sys.exit("forge is not on PATH")
    print(f"A_TIGHT = {A_TIGHT}   (region extremum, computed)")
    print(f"A_LOOSE = {A_LOOSE}   (the careless figure)\n")
    with tempfile.TemporaryDirectory(prefix="r2ab_") as tmp:
        root = pathlib.Path(tmp)
        projs = {n: build(root, n, b) for n, b in
                 (("ref", REF_BODY), ("m1", M1_BODY), ("m2", M2_BODY))}
        tally = {n: {e: 0 for e in EXPECTED} for n in projs}
        for n, proj in projs.items():
            for seed in range(1, a.seeds + 1):
                v = verdicts(proj, seed)
                for e in EXPECTED:
                    tally[n][e] += 1 if v[e] == "FAIL" else 0
                print(f"{n:<4} seed {seed:>2}: "
                      + ", ".join(f"{e}={v[e]}" for e in EXPECTED))
        print()
        print(f"{'oracle':<22}" + "".join(f"{n:>10}" for n in projs)
              + "     (FAIL count out of "f"{a.seeds} seed(s))")
        for e in EXPECTED:
            print(f"{e:<22}" + "".join(f"{tally[n][e]:>10}" for n in projs))
        print()
        bad = 0
        if any(tally["ref"][e] for e in EXPECTED):
            print("** CONTROL BROKEN: an oracle fails on the REFERENCE, so no "
                  "failure elsewhere means anything **")
            bad = 1
        if tally["m2"]["test_B_expr"] != a.seeds:
            print("** CONTROL BROKEN: option B must fail on M2 in every seed **")
            bad = 1
        print("VERDICT on the M2 argument for option B: "
              + ("WITHDRAWN -- the tight constant catches M2 too "
                 f"({tally['m2']['test_A_tight']}/{a.seeds})"
                 if tally["m2"]["test_A_tight"] > a.seeds // 2 else
                 "UPHELD -- the tight constant lets M2 through "
                 f"({tally['m2']['test_A_tight']}/{a.seeds})"))
        return bad


if __name__ == "__main__":
    sys.exit(main())
