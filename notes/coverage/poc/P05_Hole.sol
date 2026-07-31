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
