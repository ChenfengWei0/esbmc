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
///   * NO focus, `--solidity-max-tx 1` -> ~~the dispatch guards are independent,
///     so one transaction may call `deposit()` and then `withdraw()`~~
///     **REFUTED, twice over, and the retraction is kept rather than deleted
///     because this file is the thing that refuted it.**
///     MEASURED: 6 of 8, with `withdraw`'s two state-guarded paths still
///     `bounded-holds`; only `--solidity-max-tx 2` gives 8 of 8.
///     SOURCE: each dispatcher arm is `if (nondet) { f(...); return; }` --
///     `then.copy_to_operands(then_expr, return_expr)`,
///     src/solidity-frontend/solidity_convert_constructor.cpp:445, whose own
///     comment at :316 says "construct return; to avoid fall-through". So ONE
///     TRANSACTION IS EXACTLY ONE ENTRY CALL, at every scope.
///     ⇒ `--solidity-max-tx N` is the LENGTH of the call sequence and
///     `--focus-function` is its ALPHABET; the reachable sequences are the
///     words of length <= N over that alphabet, and no width redeems N = 1.
///     (The harness sketch at solidity_convert_contract.cpp:712-729 omits the
///     `return` and reads the other way; `build_bound_drive_helper` at
///     solidity_convert_constructor.cpp:645 deliberately omits it for real, so
///     THAT loop can call several per iteration. The two are worth comparing
///     before believing either comment.)
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
