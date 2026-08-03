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
/// ✅ MEASURED AT LAST -- `probe` had never been run. This file recorded results
/// for `ctrl` and `ctrlU256` while the unit it was WRITTEN for sat at
/// "EXPECTED", and _runs/ holds no B1/B2/B3 directory at all. It is the FIRST
/// of the two branches above, and the two units were run back to back in one
/// script (run_b2_probe.sh), bracket ON, so they are comparable:
///
///   probe   3 witnessed (enc=2 ABI gate, enc=6/7 the pair)
///           [coords] FREE: amount, msg.sender, state.tag      <- NO `token`
///           [refine 1] ... UNSEPARATED=[6, 7]
///           enc=6 and enc=7 regions BYTE-IDENTICAL, |R| identical
///           both -> REFERRED TO THE COORDINATE GATE
///           0 certified / 3 not          exit=1
///
///   ctrl    3 witnessed, same shape
///           [coords] FREE: amount, msg.sender, state.tag, token
///           [refine 2] ... UNSEPARATED=[7]
///           enc=6: token in [101, 2^160-1]
///           enc=7: token in [0, 100]      <- the true boundary is 100
///           2 certified / 1 not          exit=0
///
/// ONE VARIABLE: where the branched-on bool comes from. Same contract, same
/// command line, same `if (ok) / else` body. When `ok` is a function of a
/// PAYLOAD coordinate the two paths certify into disjoint regions; when `ok` is
/// the success bit of a low-level `call` the payload cannot see it and the pair
/// is unseparated. That is farming/deposit's three pairs (enc 26/27, 246/247,
/// 3622/3623, all splitting at safeTransferFrom:532 on `!success`) in twelve
/// lines.
///
/// ⚠ `token` DISAPPEARS ENTIRELY in `probe`, and that is worth more than the
/// verdict: it is the SAME `address token` parameter in both units, and it is a
/// free coordinate in `ctrl` and absent from `probe`'s payload. Used only inside
/// the assembly block, it is not harvested at all -- so the call's TARGET is
/// missing as well as its result.
///
/// ⚠ THREE LIMITS ON WHAT THIS PROVES, stated because none of them is visible
/// in the numbers above:
///   (1) The `ctrl` line recorded further down this file is `1 certified / 2
///       not`, and this run gives `2 / 1`. Three commits landed in between (the
///       retreat guard, --state-struct-fields, and threading it to the witness
///       side). Both are exit=0 and the control clears in both, but the two
///       numbers are NOT one measurement and must never be quoted as a series.
///   (2) `probe`'s gate verdict is the WEAK form -- the witness differs on
///       block.number and block.timestamp, both marked `[NOT a bounded
///       coordinate]` -- while farming/deposit's is the STRONG form ("agrees on
///       EVERY scalar quantity in the payload"). Same mechanism, more noise.
///   (3) Nothing here shows that HARVESTING the success bit would certify these
///       paths. It shows the pair is inseparable while it is missing.
///
/// NEGATIVE CONTROL, `ctrl`: identical body with the external call removed and
/// the branch put on the parameter. It must certify; if it does not, the run
/// measured the harness. It certified (above).
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

// ⛔ EVERYTHING BELOW IS `//` AND NOT `///` ON PURPOSE. It quotes ESBMC's
// mangled symbol ids verbatim, and those contain `@`, which solc's NatSpec
// parser reads as a documentation tag inside a `///` comment. Written as `///`
// it fails the whole compile with "Documentation tag @C@<C>... not valid for
// contracts" and leaves a ZERO-BYTE .solast -- which is what happened here once
// and had to be restored from git.
//
// ✅ THE DROP SITE IS NOW MEASURED, NOT INFERRED, AND ESBMC'S OWN COUNTER NAMES
// IT. `--verbosity coverage:9` was run on `probe` and `ctrlBool` back to back
// on ONE .solast (run_harvest_verbose.sh; full stdout in
// notes/coverage/certify/b2_harvest_verbose_driver.log, 2996 lines, AST stamp
// at the end equals the one at the top). The classifier's feed prints one
// `ce step:` line per assignment it considers:
//
//     ctrlBool  sym='sol:@C@B2_ExtcallSuccess@F@ctrlBool@amount#99' fn=''      stack[0]: <empty>
//               sym='sol:@C@B2_ExtcallSuccess@F@ctrlBool@ok#101'    fn=''      stack[0]: <empty>
//     probe     sym='sol:@C@B2_ExtcallSuccess@F@probe@amount#8'     fn=''      stack[0]: <empty>
//               sym='sol:@C@B2_ExtcallSuccess@F@probe@ok#12'        fn='probe' stack[3]: probe#32 < Nondet_Extcall < Main
//                                                                   nondet='nondet$symex::nondet40'
//
// So `ok` IS presented to the harvest in `probe`, with a mangled id of exactly
// the `sol:@C@<C>@F@<f>@<name>` shape goto_coverage.h calls the input test, and
// with a nondet source that the counterexample then resolves (`ok = 0` on
// path:7, `ok = 1` on path:6, at line 107). Branch (b) of the pre-written
// question -- "the value never reaches the classifier" -- is REFUTED.
//
// AND THE BUCKET IS A NUMBER, from the reports themselves
// (b2_probe_harvest_cov-report.json, b2_ctrlBool_harvest_cov-report.json):
//
//     unit      path  inputs       harness_nondets_dropped
//     ctrlBool  2/6/7 amount, ok   23 / 23 / 23
//     probe     2     amount       23      <- revert, `ok` never assigned
//     probe     6     amount       24      <- executes ok := call(...)
//     probe     7     amount       24      <- executes ok := call(...)
//
// The counter moves by EXACTLY ONE, on EXACTLY the two paths that assign `ok`,
// and `ok` is absent from `inputs` on exactly those two. That is the drop,
// counted by the tool, not deduced by me. Reason (a) of
// `extcall_returns_unavailable_reason` is confirmed FOR THIS SHAPE.
//
// probe's path:6 and path:7 payloads are byte-identical -- `inputs` both
// {"amount": "0"}, same `entry_storage` {"tag":"0"}, same `env`,
// `extcall_returns: []` -- differing ONLY in the `!ok` / `ok` decision step at
// line 110. That is the corpus's UNSEPARATED shape reproduced in twelve lines.
//
// ⛔ WHAT IS STILL NOT MEASURED: the PREDICATE. The one field that differs
// between the kept `amount` and the dropped `ok` is the frame (`fn=''` +
// empty stack vs `fn='probe'` + 3-deep stack), but no line prints the predicate
// itself, so "it keys on the frame" remains a hypothesis. What IS established
// is the site: whatever increments `harness_nondets_dropped`.
//
// ⚠ AN OPEN QUESTION FOUND HERE AND DELIBERATELY NOT CALLED A DEFECT. The
// second decision step's polarity does not line up across the pair, in BOTH
// units, so it is systematic:
//     path:6  final_state.tag=1 (the `then` arm), inputs ok=1
//             -> decisions[1] = {arm: "fall-through", branch_claim: "!ok"}
//     path:7  final_state.tag=2 (the `else` arm), inputs ok=0
//             -> decisions[1] = {arm: "taken",       branch_claim: "ok"}
// Under `IF !ok GOTO else` the first line is consistent and the second is not.
// This does NOT disturb the identification of farming/deposit's three pairs
// (enc 26/27, 246/247, 3622/3623): that rests on the two sides DIFFERING at one
// index of this field, which holds whichever polarity convention is intended.
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

    /// THIRD CONTROL, and it asks the question that decides whether the ESBMC
    /// side is worth changing at all.
    ///
    /// `probe` vs `ctrl` established that the pair is inseparable while the
    /// branched-on bool is missing from the payload, and separable when it is a
    /// FUNCTION of a payload coordinate. Neither says what happens when the bool
    /// ITSELF is the coordinate -- which is exactly what harvesting the success
    /// bit would produce. Answering it here costs one twelve-line unit and no
    /// C++ at all; getting it wrong the other way costs a full read of
    /// src/goto-programs/goto_coverage.cpp (473816 bytes) to buy a coordinate
    /// the loop then cannot use.
    ///
    /// ⚠ THERE IS A CONCRETE REASON TO EXPECT TROUBLE, and it is in the method
    /// rather than in the tool. §Certification's retreat has TWO triggers, and
    /// the second is "where cutting would leave that coordinate ONE value". A
    /// bool has exactly two values, so EVERY cut on it leaves one -- the cut
    /// branch of `refutation_response` is unreachable for this coordinate by
    /// construction and the retreat fires instead. So the expected outcome is
    /// not a cut but a PIN at x_pi, i.e. partial generalisation.
    ///
    /// THE THREE OUTCOMES, written before the run, because two of them are
    /// "certified" and they do NOT mean the same thing:
    ///   (a) both paths CERTIFIED with `ok` left ranging -- harvesting the
    ///       success bit would fully generalise these paths.
    ///   (b) both paths CERTIFIED with `ok` PINNED at x_pi ([retreat ...]
    ///       PINNED ok==0/1, and `retreated` non-empty in
    ///       generalise-result.json) -- harvesting still moves them OFF the
    ///       coordinate gate and off the 0-certified count, but the emitted test
    ///       is concrete on that coordinate. That is a real gain and a smaller
    ///       one than (a), and reporting it as (a) would overclaim.
    ///   (c) NOT certified even here -- then harvesting the bit cannot raise the
    ///       rate on deposit's three pairs, the ESBMC-side repair is the wrong
    ///       target, and the C++ read is cancelled a second time.
    ///
    /// ⛔ `state.tag` IS WRITTEN ON BOTH ARMS and is a free coordinate, so it is
    /// NOT the discriminator here any more than it is in `probe` -- the only
    /// difference from `probe` is where the bool comes from.
    ///
    /// ✅ MEASURED: 2 certified / 1 not, exit=0. Same script shape as probe/ctrl
    /// (run_b2_bool.sh), bracket ON, driver at 79171e702f.
    ///
    ///     enc=6: amount in [0, 2^256-1], msg.sender in [0, 1.8043e46],
    ///            ok in [1, 1], state.tag in [0, 2^256-1], msg.value == 0
    ///     enc=7: amount in [0, 2^256-1], msg.sender in [0, 1.8043e46],
    ///            ok in [0, 0], state.tag in [0, 2^256-1], msg.value == 0
    ///
    /// The 1 not-certified is enc=2, the ABI-gate path the msg.value auto-pin
    /// excludes by design; its own verdict says "⛔ This is NOT a failure to
    /// certify". So the SAME PAIR that gives 0 certified in `probe` gives 2 here.
    /// Harvesting the success bit would move these paths off the coordinate gate.
    ///
    /// ⛔ MY PREDICTION ABOVE WAS WRONG ABOUT THE MECHANISM, and the log says so
    /// plainly: there is NO `[retreat ...]` line and NO `[cut ...]` line. The
    /// retreat's one-value trigger never fired because the refutation loop was
    /// never entered. LEVEL 0 caught it first --
    ///     [level0] enc=6 single-point on: ok==1
    ///     [level0] enc=7 single-point on: ok==0
    /// because level 0's candidate list is exactly "the values the SIBLINGS' own
    /// counterexamples take here" (proposition 9), and for a bool those two
    /// values ARE the whole type. Zero extra queries. The reasoning that
    /// predicted the retreat was sound about the cut branch being unreachable
    /// and wrong about which branch would run instead.
    ///
    /// OUTCOME IS BETWEEN (a) AND (b), and must be reported as such: `ok` comes
    /// out as a POINT in each region, so a test is concrete on that coordinate --
    /// but `amount` and `state.tag` stay at FULL RANGE and `msg.sender` at
    /// 1.8e46, so the test is still parameterized on those. It is not the full
    /// generalisation of (a) and it is better than a concrete replay.
    function ctrlBool(uint256 amount, bool ok) external {
        if (ok) {
            tag = amount + 1;
        } else {
            tag = amount + 2;
        }
    }
}
