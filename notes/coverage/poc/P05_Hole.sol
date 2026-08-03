// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: the punched region — Definition 5, an interval minus a finite set.
///
/// `require(x != 42)` makes the feasible domain of the guarded path
/// `[0, 2^256-1] \ {42}`, which is NOT an interval. It is the only shape in the
/// method that needs holes at all, and the only reason `--max-holes` exists.
///
/// EXPECTED: stage 2 certifies a region of the form `x in [lo, hi] \ {42}`, and
/// the emitted test carries `vm.assume(x != 42)` beside its `bound(x, lo, hi)`.
///
/// WHAT WOULD BE A FAILURE, and it is a quiet one: a region that simply
/// EXCLUDES the hole by shrinking to `[43, hi]`. That is sound but throws away
/// everything below 42, and the report would look like a successful
/// certification. The check is therefore on the region's SIZE, not on whether
/// certification succeeded.
///
/// ---- MEASURED 2026-08-03: THE HOLE RENDERS, AND IT IS LOAD-BEARING ----
///
/// Until now this contract had never been through stage 4 at all -- it was
/// absent from the emitter round-trip project's `src/`, so the one fixture
/// written for holes had never exercised the rendering it exists to check.
///
/// Two cells, identical but for whether the hole is handed to stage 4.
/// `pick`'s four paths are all witnessed; `enc=15 depth=3` is the guarded
/// `x <= 100` path, whose domain is `[0,100] \ {42}`.
///
///   WITH the hole   -> `x = bound(x, 0, 100); vm.assume(x != 42);`
///   WITHOUT it      -> `x = bound(x, 0, 100);`   and nothing else
///
/// The two emitted files differ in exactly that one line. forge:
///
///   hole   : [PASS] test_put_P05_Hole_pick_path15(uint256) (runs: 256)
///   nohole : [FAIL: EvmError: Revert] ... (runs: 37)   Logs: Bound result 42
///
/// The control did not merely go red -- it went red AT 42, the value the hole
/// removes, and forge printed the bound result that proves it. So `vm.assume`
/// here is not decoration: without it the test is red on a value strictly
/// inside its own certified interval, which is precisely why a side cut cannot
/// substitute for a punch.
///
/// ⚠ DECODING NOTE, recorded because it cost a run. Reading `arm` directly gets
/// the branch backwards. `enc=14`'s third decision is `!(x > 100)` on the
/// fall-through arm, which by the polarity rule (`branch_claim` is FALSE on the
/// edge actually walked) means `x > 100` is TRUE -- so 14 is the `x > 100` path
/// and 15 is the `x <= 100` one. Handing `[0,100]` to enc=14 was correctly
/// refused as VACUOUS by the non-vacuity witness, which is that check earning
/// its keep on an operator error rather than on a tool defect.
contract P05_Hole {
    uint256 public seen;

    function pick(uint256 x) external {
        require(x != 42);
        if (x > 100) {
            seen = 1;
        } else {
            seen = 2;
        }
    }
}
