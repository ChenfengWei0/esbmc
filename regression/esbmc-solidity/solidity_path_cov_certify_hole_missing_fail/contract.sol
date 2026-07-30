// THE SECOND HALF OF THE MUST-FLIP PAIR with
// solidity_path_cov_certify_hole_pass. Identical contract, identical enc/depth,
// identical lo and hi -- the ONLY difference is that this spec has no `holes`
// key at all.
//
//     with    `"holes": ["255"]`  ->  VERIFICATION SUCCESSFUL  (sibling)
//     without                     ->  VERIFICATION FAILED      (here)
//
// This half is what makes the other half mean something. `to == 255` walks the
// require-failure path, so the unpunched interval genuinely contains an input
// that leaves the certified path, and the query must say so. A change that made
// certification pass more easily -- a dropped bound, a vacuous assumption, an
// assert placed on one exit instead of all of them -- turns THIS directory
// green, which is the direction a single passing fixture cannot detect.
//
// It also pins the absent-key default: with no `holes` the assumption is the
// closed interval it always was, byte for byte, so every spec written before
// punched intervals existed still asks the same question. The reported count is
// pinned at 0 for the same reason -- "punched interval" must not be announced
// for a spec that punched nothing.
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
