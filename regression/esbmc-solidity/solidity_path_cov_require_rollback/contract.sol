// A `require` that fails AFTER a state write. Both complete paths must be
// covered (Reached : 2), and the failing one exits through the rollback block
// that restores the entry snapshot (`*this = _sol_save_this`).
//
// This is the shape that makes the counterexample's post-state correct: the
// restore is a WHOLE-OBJECT assignment, not a per-field one, so a harvester
// that only follows `this-><field>` writes reports the pre-rollback value
// (x = 1) as the post-state of a transaction that on-chain reverts to x = 0.
pragma solidity ^0.8.0;

contract Rq {
    uint256 public x;

    function g(uint256 a) public {
        x = 1;
        require(a >= 5);
        x = 2;
    }
}
