// THE SECOND HALF OF A PAIR. See solidity_path_cov_punched_ce_independent for
// the full rationale; this directory is that fixture with exactly ONE number
// changed -- enc=3's supplied counterexample is 2^160-1 instead of 0.
//
// Both are genuine members of enc=3's domain, and before punched intervals
// existed that single number decided the answer, because the subtraction could
// only keep the SIDE of the excluded point that held this path's own
// counterexample:
//
//     CE = 0          ->  `to in [0, 254]`          (255 values)
//     CE = 2^160-1    ->  `to in [256, 2^160-1]`    (~1.46e48)
//
// a factor of ~5.7e45 apart, both correct. THE POINT OF THE PAIR is that the
// two directories now pin the IDENTICAL region `[0, 2^160-1] \ {255}`. A
// regression that only ever ran one of them would be satisfied by a side cut
// that happened to favour that direction; running both is what makes
// "independent of the counterexample" an observation rather than a claim.
// Verified by fault injection: with the hole candidate disabled, the two
// directories print the two different regions above.
//
// OVERLAP, stated rather than left to be discovered: this outer.json is
// IDENTICAL to solidity_path_cov_level0_single_point's. That directory pins the
// PROBE MECHANICS (one probe per direction, both decidable); this one exists so
// the counterexample-independence pair is discoverable by name and so a change
// favouring the high side cannot pass by turning only its partner red. It is
// not an independent measurement, and reading it as one would double-count a
// single run.
pragma solidity ^0.8.0;

contract Gate2 {
    uint256 public sink;
    address constant BANNED = address(0x00000000000000000000000000000000000000ff);

    function send(address to) external payable returns (uint256) {
        require(to != BANNED);
        sink = 1;
        return 1;
    }
}
