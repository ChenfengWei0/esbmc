// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES candidate 3 of the farming/deposit coords-gate: the SUCCESS BIT of
/// a low-level `call` made from an assembly block, branched on afterwards.
///
/// WHERE IT COMES FROM, verbatim, SafeERC20.safeTransferFrom, which is the
/// third statement of FarmingPool.deposit:
///     assembly ("memory-safe") {
///         ...
///         success := call(gas(), token, 0, data, 0x64, 0x0, 0x20)
///         if success {
///             switch returndatasize()
///             case 0 { success := gt(extcodesize(token), 0) }
///             default { success := and(gt(returndatasize(), 31), eq(mload(0), 1)) }
///         }
///     }
///     if (!success) revert SafeTransferFromFailed();
///
/// The driver's own report says `extcall_returns` is NOT harvested, and gives
/// three reasons; this file is reason (a) -- "assigned inside an approximated
/// assembly block: the value IS harvested and resolved, then dropped because
/// the classification has buckets only for parameters and environment values
/// and a call's return is a local."
///
/// WHY P21_ExternalCall DOES NOT COVER THIS. There the call is `s.ping()`,
/// which returns nothing, and the branch is on `armed` -- a plain bool state
/// variable the classifier CAN see. P21 isolates the re-entry model. This file
/// isolates the returned value being the branch condition.
///
/// EXPECTED, `probe`: two complete paths. The question this file answers is
/// whether their counterexamples agree on `token`, `amount` and `msg.sender`.
///   * they AGREE and the path is referred to the coordinate gate -> the
///     success bit is a quantity outside the classification and is a live
///     candidate for deposit's enc 26/27/246/247;
///   * they DIFFER on `token` -> the success bit is a FUNCTION of a payload
///     coordinate here, the shape is not reproduced, and this file is the
///     wrong isolation rather than a result.
///
/// NEGATIVE CONTROL, `ctrl`: identical body with the external call removed and
/// the branch put on the parameter. It must certify; if it does not, the run
/// measured the harness.
///
/// ⛔ THE FIRST VERSION OF `ctrl` COULD NOT PASS BY CONSTRUCTION, and its run is
/// struck. It branched on `(uint160(token) & 1) == 1`. A region is a PRODUCT OF
/// PER-COORDINATE SETS, each an interval minus a bounded number of holes
/// (Definition 6), and "every odd address" is neither -- it is 2^159 disjoint
/// points. So the control could only ever report `shrink round budget
/// exhausted`, which it did (0 certified / 3 not), and it would have done so
/// whatever the candidate did. A negative control whose expected outcome is
/// unreachable is the always-refusing mirror of an always-true reader. `ctrl`
/// now branches on an ORDER comparison, whose two sides are exactly two
/// intervals.
contract B2_ExtcallSuccess {
    uint256 public tag;

    function probe(address token, uint256 amount) external {
        bool ok;
        assembly ("memory-safe") {
            ok := call(gas(), token, 0, 0, 0, 0, 0)
        }
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    function ctrl(address token, uint256 amount) external {
        bool ok = uint160(token) > 100;
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }

    /// SECOND CONTROL, added because the first repair STILL failed and the
    /// reason looks like the coordinate's TYPE rather than the branch.
    ///
    /// MEASURED, `ctrl` above, `uint160(token) > 100`: 0 certified / 3 not, and
    /// `token`'s region came back IDENTICAL on both sides --
    ///     token: (0, 1461501637330902918203684832716283019655932542975)
    /// which is exactly 2^160 - 1, the full address range, on enc=6 AND enc=7.
    /// Refinement separated NOTHING on the branch variable. The driver's span
    /// for it is
    ///     'token': (0, 115792089237316195423570985008687907853269984665640564039457584007913129639935)
    /// i.e. the full uint256. So ~54 candidate values are spread over 2^256
    /// while every value the coordinate can actually take lies below 2^160: the
    /// fraction of the ladder that lands inside the type is 2^-96, and a
    /// boundary at 100 is invisible to it. B1's `ctrl` has the same boundary at
    /// 100 on a uint256 coordinate and DID separate (wrongly, at 6.8e75, but it
    /// separated), so the branch is not what differs.
    ///
    /// `ctrlU256` is `ctrl` with the SAME boundary and the SAME body on a
    /// uint256 parameter instead of an address. If it separates and `ctrl` does
    /// not, the ladder being laid over uint256 for a narrower-typed coordinate
    /// is the mechanism.
    ///
    /// ⛔ TWO THINGS I WROTE ABOUT THIS ARE STRUCK, both by reading the driver
    /// rather than by another run:
    ///
    /// (a) "a mechanism nobody was looking for" is FALSE. It is already written
    ///     down, beside TYPE_RANGE_RE in solidity_path_generalise.py: "laying
    ///     probes over the whole 256-bit range on a 160-bit `address` puts most
    ///     of them OUTSIDE the type, where they wrap and measure a different
    ///     number." What is new is not the mechanism but that the repair reaches
    ///     ONE reader and not the other: `type_ranges` clamps the ladder in the
    ///     `geometric` branch of outer_round only, while the refine branch takes
    ///     `spans[c]` and the skip-bracket fallback is `(0, UINT256_MAX)` with
    ///     no clamp at all. One fact, two ledgers, one of them not updated.
    ///
    /// (b) "this reaches the corpus" was an INFERENCE, not a measurement, and it
    ///     had a visible counter-indication I did not check: every PoC run here
    ///     carried --skip-bracket, so the geometric round never ran and the
    ///     clamp never applied; every farming arm was recorded with
    ///     `skip_bracket: false`, so on the corpus the clamped round DID run.
    ///     Whether an address coordinate still fails there is a separate
    ///     measurement -- the bracket-ON re-run of `ctrl` and `ctrlU256` -- and
    ///     until it lands nothing about msg.sender on the corpus follows from
    ///     this file.
    ///
    /// ⛔ THAT RE-RUN HAS NOW LANDED AND IT REFUTES THE TYPE MECHANISM
    /// OUTRIGHT. Same file, same command line, bracket ON (no --skip-bracket):
    ///     ctrl      `uint160(token) > 100`  1 certified / 2 not  exit=0
    ///     ctrlU256  `uint256 tokenNum > 100` 1 certified / 2 not  exit=0
    /// IDENTICAL. The 0/3-against-1/2 differential that mechanism (3) of commit
    /// 6250f8d90b rested on exists ONLY under --skip-bracket, and it is struck
    /// as a mechanism for anything the corpus does. On the corpus the type
    /// clamp DOES apply -- farming/deposit's own refine line reads
    /// `'msg.sender': (0, 1461501637330902918203684832716283019655932542975)`,
    /// i.e. 2^160-1, not 2^256-1 -- because --level0 publishes the type range
    /// before the ladder is laid.
    ///
    /// WHAT BOTH BRACKET-ON RUNS DO SHOW, and it is the same thing twice: the
    /// bracket LOCATED THE BOUNDARY and the span threw it away.
    ///     [bracket] enc=6 `tokenNum lower in [64, 128)`   (true boundary: 100)
    ///     [bracket] enc=7 `tokenNum upper in (64, 128]`
    ///     [refine 1] spans={'tokenNum': (0, 2^256-1)}      <- the whole type
    /// `brackets_for` unions the upper and lower brackets ACROSS ALL PATHS, so
    /// enc=6's type-topping upper bracket swallows enc=7's tight [64, 128).
    /// enc=7 then never certifies and its cut removes ONE value per round, four
    /// times, out of 6.8e75. Same on `ctrl` at 8.597e46. The union is argued in
    /// the code ("NOT per-path spans: those would multiply the claim count by
    /// the path count"); what did not exist until these two runs is its COST,
    /// measured, on a twelve-line contract whose boundary was already bracketed
    /// to within a factor of two.
    function ctrlU256(uint256 tokenNum, uint256 amount) external {
        bool ok = tokenNum > 100;
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }
}
