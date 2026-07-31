// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// A hand-written proof-of-concept contract. Every query on it is milliseconds,
/// so an experiment here costs seconds instead of the 180 s a real benchmark
/// costs, and every path is one I chose deliberately.
///
/// It isolates ONE question, the one that dominates every corpus loss measured
/// so far: can complete-path coverage reach a path whose guard only ANOTHER
/// public function can satisfy?
///
///   `bal` is 0 after the constructor.
///   `deposit` is the only writer that can make it non-zero.
///   `withdraw`'s interesting paths all sit behind `require(bal >= amt)`.
///
/// So:
///   * `--focus-function deposit`  -> its paths should be witnessed (F).
///   * `--focus-function withdraw` -> everything past the balance guard is, by
///     the invocation contract's §2, unreachable at EVERY `--solidity-max-tx`,
///     because under focus every transaction is another call to `withdraw`.
///   * NO focus, `--solidity-max-tx 1` -> the dispatch guards are independent,
///     so one transaction may call `deposit()` and then `withdraw()`. If that
///     is true, `withdraw`'s guarded paths become reachable HERE, and the
///     corpus-scale question "is the entry state the blocker, or is it
///     something else" is answered on a contract that fits on one screen.
///
/// No mappings, no libraries, no external calls: the solver picks the cheap
/// backend and nothing about the result can be blamed on modelling a data
/// structure.
contract Tiny {
    uint256 public bal;

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
