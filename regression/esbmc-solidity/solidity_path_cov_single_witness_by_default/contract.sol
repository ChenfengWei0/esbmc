// THE DEFAULT MUST NOT MOVE: one witness per path unless asked.
//
// Its partner (solidity_path_cov_all_witnesses_reach_report) runs the SAME
// contract with `--all-witnesses` and gets more payloads than paths. This one
// runs without the flag and pins that every witnessed path carries exactly one
// -- `4 witness(es) across 4 witnessed path(s); 0 path(s) carry more than one
// (--all-witnesses is off)`.
//
// Two things would break silently without this direction, and neither would
// look like a defect:
//
//   * enumeration turning itself on. `--all-witnesses` also switches on
//     `collect_nondet_values`, which walks every SSA step and queries the model
//     per nondet symbol -- the code calls it "non-trivial on coverage runs with
//     many claims and large arrays". A run that started enumerating by accident
//     would get slower and produce a bigger report, with no error anywhere.
//   * the count field going stale. `witness_count` is emitted on every F claim,
//     so a build that hard-coded it, or stopped updating it, would still print
//     a plausible number. Pinning the exact 4-of-4 with the explicit `0 path(s)
//     carry more than one` makes a frozen field visible the moment its partner
//     test's number moves and this one's does not.
//
// The census is asserted as a whole line, zeros included, for the same reason
// the U-reason breakdown prints every slot: a number shown only when it is
// interesting cannot be distinguished from a number that stopped being
// computed.
pragma solidity ^0.8.0;

contract D {
    uint256 public x;

    function g(uint256 a) public {
        require(a != 0);
        if (a > 100) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
