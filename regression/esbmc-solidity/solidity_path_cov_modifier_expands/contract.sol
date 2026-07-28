// A modifier's own decisions must be part of the UNIT's path identity.
//
// The Solidity frontend expands a modifier by renaming the function and adding
// an auxiliary wrapper; only one of the two carries the external-entry marker.
// Under the unit rule that is exactly right: the aux is not a unit, and the
// internal call between them is expanded, so `set` ends up with one path set
// covering the modifier AND the body. If instead the aux had been the unit, or
// the call had not been expanded, the `require` inside `onlyOwner` would not
// appear in any enumerated path at all.
//
// Decisions in order: the ABI non-payable gate, the modifier's require, the
// body's if. A failed require terminates before the body, so the shape is FOUR
// paths, not eight:
//
//   enc 2    gate rejects (value sent)             -> revert
//   enc 6    gate passes, require fails            -> revert
//   enc 14   gate passes, require holds, a <= 1    -> normal
//   enc 15   gate passes, require holds, a >  1    -> normal
//
// This also settles a standing question from the design notes — whether every
// exit produced by modifier expansion maps to a regular branch target.
// Measured here: undetermined 0, i.e. every one of the four exits is
// classified on positive evidence, none falls through unclassified.
pragma solidity ^0.8.0;

contract M {
    address public owner;
    uint256 public x;

    modifier onlyOwner() {
        require(msg.sender == owner);
        _;
    }

    function set(uint256 a) public onlyOwner {
        if (a > 1) {
            x = 1;
        } else {
            x = 2;
        }
    }
}
