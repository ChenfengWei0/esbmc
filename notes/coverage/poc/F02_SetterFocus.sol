// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// IS `focus = {unit, its setter}` + tx>=2 AS GOOD AS THE WHOLE CONTRACT + tx>=2?
///
/// This is the only question that decides whether multi-name `--focus-function`
/// is worth wiring into the corpus collector. Established already:
///
///   * multi-name focus works (F01_MultiFocus: one,two = 3+4 = 7 < 13, and the
///     run prints "2 unit(s) kept, 1 other(s) dropped");
///   * breadth of focus ALONE buys nothing (Tiny3 whole-contract at tx=1 has the
///     same two `bounded-holds` as the single-focus run, even though the
///     available predecessor `seed()` has no user-level decision at all);
///   * tx>=2 with focus OFF unlocks them completely (Tiny3: 5 F -> 7 F, 100%);
///   * tx>=2 with focus ON buys nothing, because every transaction is another
///     call to the same function (option-matrix-round1.md, six cells x two
///     units on aqua).
///
/// So the winning cell is "focus off + tx>=2", and whole-contract is expensive:
/// aqua needed 20 GiB and 778 s at tx=1 alone. If naming just the unit and its
/// setter reaches the same paths, that is the cheap version of the winning cell
/// and multi-name focus has a purpose. If it does not, multi-name focus should
/// be cut and the cut should be recorded with this measurement beside it.
///
/// Tiny3 cannot answer it: it has exactly two units, so {seed, withdraw} IS the
/// whole contract. This one has THREE, and `noise` exists only to make the two
/// configurations differ — it is unrelated to `bal`, so including it must not
/// change withdraw's verdicts, only the cost and the totals.
///
/// CELLS AND WHAT EACH OUTCOME MEANS (fixed here, before the run):
///
///   focus=withdraw, tx=2         withdraw's guarded paths stay U
///                                (the setter is not callable) — this is the
///                                POSITIVE CONTROL for "the setter matters".
///                                If they are F here, the whole framing is
///                                wrong and nothing below is interpretable.
///   focus=seed,withdraw, tx=2    if withdraw's paths are F, the cheap cell
///                                works => wire multi-name focus.
///   whole contract, tx=2         the reference. withdraw's paths F.
///
/// The comparison must be made on WITHDRAW'S OWN paths, not on the totals: the
/// whole-contract run enumerates `noise` as well, so its F and its denominator
/// are both larger for a reason that has nothing to do with the question.
contract F02_SetterFocus {
    uint256 public bal;

    /// The setter. No user-level decision in the body, so it contributes
    /// nothing to the path identity beyond the ABI value gate every unit has.
    function seed() external {
        bal = 500;
    }

    /// Unrelated to `bal`. Present only so that {seed, withdraw} is a PROPER
    /// subset of the contract's units.
    function noise(uint256 a) external {
        if (a > 10) {
            if (a > 20) {
                bal = bal;
            } else {
                bal = bal;
            }
        }
    }

    /// The unit under test. Both branches need `bal >= amt`, which only `seed`
    /// can establish.
    function withdraw(uint256 amt) external {
        require(amt > 0);
        require(bal >= amt);
        if (amt > 100) {
            bal -= amt;
        } else {
            bal -= 1;
        }
    }
}
