// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// DISCRIMINATING EXPERIMENT. Identical to `Tiny` except that the constructor
/// puts the balance in place, so `withdraw`'s guarded paths need NO preceding
/// call in the transaction.
///
///   Tiny  : `bal` starts 0; only `deposit` can raise it.
///   Tiny2 : `bal` starts 500; `withdraw`'s guard is satisfiable at entry.
///
/// Tiny measured: `--focus-function withdraw` gives 5 paths, 3 F, 2
/// bounded-holds; and dropping the focus entirely gives 8 paths, 6 F, THE SAME
/// 2 bounded-holds. So a whole-contract dispatcher, which may call `deposit()`
/// and then `withdraw()` inside one transaction, did not witness them either.
///
/// If Tiny2's two guarded paths come back F, the blocker is not the STATE but
/// the PRECEDING CALL -- i.e. the path identity `tr != enc || cnt != depth`
/// excludes exactly the executions that would establish the state, because a
/// preceding call contributes its own decisions to the same accumulator.
///
/// If they come back bounded-holds here too, the accumulator is innocent and
/// something else refuses the guard.
contract Tiny2 {
    uint256 public bal;

    constructor() {
        bal = 500;
    }

    function deposit(uint256 amt) external {
        require(amt > 0);
        bal += amt;
    }

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
