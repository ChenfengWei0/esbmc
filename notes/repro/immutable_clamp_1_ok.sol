// Minimal reproducer for the St1inch constructor death.
//
// St1inch's constructor sets ORIGIN = block.timestamp (via the
// VotingPowerCalculator base) and then, still inside the constructor, calls
// _votingPowerAt(1e18, block.timestamp + MAX_LOCK_PERIOD), whose first two
// statements are
//
//     timestamp = timestamp < ORIGIN ? ORIGIN : timestamp;
//     uint256 t = timestamp - ORIGIN;
//
// MEASURED in the SSA of the real benchmark: `t` comes out as the CONSTANT 0
// for BOTH calls, even though their arguments differ by exactly one second
// (block_timestamp + 63072000 and + 63072001). All thirty `if (t & bit)` tests
// are then dead, the function returns its `balance` argument unchanged, and the
// constructor's own sanity check `1e18 * 20 > 1e18` reverts unconditionally.
//
// This file isolates that shape and nothing else: an immutable assigned from
// block.timestamp in the constructor, read back through the same clamp-then-
// subtract idiom, with the two offsets one apart.
//
// EXPECTED IF THE MODEL IS RIGHT: d1 == 63072000 and d2 == 63072001, so `Bad()`
// is unreachable and the difference is 1. If `t` really folds to 0, both come
// out 0, `equal` is true, and the revert fires.
pragma solidity ^0.8.0;

contract T {
    uint256 public immutable ORIGIN;
    uint256 public d1;
    uint256 public d2;
    bool public equal;

    error Bad();

    constructor() {
        ORIGIN = block.timestamp;
    }

    function probe() public {
        uint256 a = block.timestamp + 63072000;
        uint256 b = block.timestamp + 63072001;
        a = a < ORIGIN ? ORIGIN : a;
        b = b < ORIGIN ? ORIGIN : b;
        unchecked {
            d1 = a - ORIGIN;
            d2 = b - ORIGIN;
        }
        equal = (d1 == d2);
        if (d1 == 0 && d2 == 0) revert Bad();
    }
}
