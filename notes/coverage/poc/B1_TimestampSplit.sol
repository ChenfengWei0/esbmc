// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES candidate 1 of the farming/deposit coords-gate: `block.timestamp`
/// compared against a STORED checkpoint, where the branch outcome leaves NO
/// trace in the counterexample payload.
///
/// WHERE IT COMES FROM, verbatim, UserAccounting.farmedPerToken:
///     (uint256 checkpoint, uint256 fpt) = (info.checkpoint, info.farmedPerTokenStored);
///     if (block.timestamp != checkpoint) { ... }
/// reached from FarmingPool.deposit through _mint -> _update -> updateBalances
/// -> _farmedPerToken. deposit's arm ran with `--env-coord msg.sender`, so
/// block.timestamp was NOT a free coordinate; it was pinned by the environment
/// rule, identically on every path.
///
/// WHY P09_TimeLock DOES NOT COVER THIS. There the comparison sits in a
/// `require`, so failing it REVERTS and the two sides are different exits; and
/// `unlockAt` is a plain uint256 state variable the classifier can harvest.
/// Here both sides RETURN NORMALLY and write the same variable, which is the
/// shape that makes two paths agree on every payload scalar.
///
/// EXPECTED, `probe`: two complete paths whose counterexamples agree on
/// `amount` and `msg.sender` and differ only on block.timestamp, i.e. the
/// method's §Coordinates gate -- `REFERRED TO THE COORDINATE GATE`. If instead
/// they certify, block.timestamp is NOT what separates deposit's enc 26/27/
/// 246/247 and this candidate is eliminated.
///
/// NEGATIVE CONTROL, `ctrl` in the same file and the same shape: the branch is
/// on the PARAMETER instead of on the clock. If `ctrl` also fails to certify,
/// nothing here is about the clock and the run measured the harness, not the
/// candidate.
contract B1_TimestampSplit {
    uint256 public checkpoint;
    uint256 public tag;

    function setCheckpoint(uint256 c) external {
        checkpoint = c;
    }

    function probe(uint256 amount) external {
        if (block.timestamp != checkpoint) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    function ctrl(uint256 amount) external {
        if (amount > 100) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }
}
