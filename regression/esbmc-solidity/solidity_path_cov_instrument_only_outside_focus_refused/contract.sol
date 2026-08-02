// `--path-cov-instrument-only`: DISPATCH WIDE, INSTRUMENT NARROW.
//
// `withdraw`'s interesting paths sit behind `require(bal >= amt)`, and `deposit`
// is the only writer that can make `bal` non-zero. One transaction is EXACTLY
// one entry call -- every dispatcher arm ends in a `return`
// (solidity_convert_constructor.cpp:445) -- so reaching those paths needs BOTH
// a second letter in the dispatcher alphabet AND a second transaction.
//
// Adding that letter with `--focus-function` alone also adds ITS paths to the
// denominator, because one option was answering two questions:
//
//     --focus-function withdraw,deposit          8 paths across 2 units
//     ... --path-cov-instrument-only withdraw    5 paths across 1 unit
//
// Both runs dispatch the same two entries and both witness the same executions.
// What differs is what the published `Complete Paths` is a count OF, and that
// is the whole point: a tx ladder compares cells of the same unit, and cells
// with different denominators are not comparable at all.
//
// MEASURED ON A REAL BENCHMARK, which is why this option exists rather than
// being a convenience. aqua `dock` needs `ship` in the alphabet (it is the only
// unit that writes the `tokensCount` its guard tests). With
// `--focus-function dock,ship` the instrumented set went from dock's 63 paths
// to 2796 -- `ship` contributes 2733 -- and the run was killed at a 300 s outer
// timeout with no usable answer, at tx=1 and again at tx=2. Pinning the
// denominator brought the same cell back to ~19 s.
//
// THE PAIR:
//   * this directory                                  option ON  -> 5 / 1 unit
//   * solidity_path_cov_instrument_only_control_wide  option OFF -> 8 / 2 units
//   * solidity_path_cov_instrument_only_outside_focus_refused
//         names a unit the focus does not select -> the run is REFUSED, because
//         an instrumented unit the dispatcher cannot enter reports every path
//         `unit-not-entered`, a zero that reads as "nothing reaches this code"
//         and means "nothing was asked to".
pragma solidity ^0.8.0;

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
