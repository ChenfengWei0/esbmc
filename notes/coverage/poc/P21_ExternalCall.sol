// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// ISOLATES: an external call, which is modelled as NONDET RE-ENTRY into this
/// contract's own dispatcher.
///
/// This is the biggest gap the review found, and it is the reason the pass
/// installs a loop bound for itself at all. The source records the failure it
/// prevents: without a bound, `t.call("")` recurses into
/// `_ESBMC_Nondet_Extcall_C` — measured at 944 unwindings and `ERROR: Out of
/// memory`. Every other contract in this set avoids external calls, so nothing
/// here exercises the mechanism that motivates the tool's own default.
///
/// `pull` writes state, makes an external call, then reads the state back and
/// branches on it. Under the re-entry model the callee may call back into
/// `pull` (or `poke`) and change `armed` in between, so the "impossible" branch
/// is reachable in the model.
///
/// EXPECTED, and the three outcomes mean different things:
///   * the re-entrant branch is WITNESSED  -> the model does re-enter, the
///     bound governs how deep, and a test generated from it needs a mock that
///     re-enters or it will be red on the unmodified contract;
///   * it is reported bounded-holds        -> re-entry is bounded away rather
///     than explored, and the method under-reports a real Solidity hazard;
///   * the run does not terminate at the default bound -> the bound is doing
///     the work the comment claims, and that cost belongs in the cost model.
///
/// It also exercises the emitter's hardest case: an external call needs a mock,
/// and a nondet return value from that mock is exactly where a generated test
/// once went RED on the unmodified contract while the exit census said the path
/// exited normally.
interface ISink {
    function ping() external;
}

contract P21_ExternalCall {
    bool public armed;
    uint256 public tag;

    function poke() external {
        armed = !armed;
    }

    function pull(ISink s) external {
        armed = true;
        s.ping();
        if (armed) {
            tag = 1;
        } else {
            tag = 2;
        }
    }
}
