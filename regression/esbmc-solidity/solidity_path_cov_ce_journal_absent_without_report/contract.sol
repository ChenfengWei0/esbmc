// THE OTHER HALF OF THE MUST-FLIP PAIR: the journal must NOT fire here.
//
// Its partner (solidity_path_cov_ce_journal_survives_death) asserts that a
// counterexample payload reaches disk mid-solve. A guard that is always on is
// worth nothing, and this project has already shipped one -- a certification
// gate whose answer was true on every input because it substring-matched a
// phrase that appears in an ordinary warning. So the negative direction is
// pinned in its own test rather than assumed.
//
// SAME CONTRACT, SAME PATHS, SAME WITNESSES: `Path Status: F 4, I 0, U 0` is
// asserted here precisely so that "no journal line" cannot be explained away as
// "nothing was witnessed". Four paths are refuted in this run and NOT ONE of
// them is journalled, because the run did not ask for the counterexample
// payload at all (`--cov-report-json` is absent, so the payload is not even
// harvested -- the slicer removes the symbols it would be built from).
//
// The distinction the pair establishes is therefore exactly the right one: the
// journal follows the request for a payload, not the presence of a witness.
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
