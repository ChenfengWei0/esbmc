// Complete-path coverage enumerates every entry->exit decision sequence.
// The nested guards make `a > 10 && a < 5` a syntactically enumerable but
// INFEASIBLE path: it must be counted in the denominator (3 paths) and must
// stay uncovered (2 reached). Locks two properties at once: an infeasible
// path is never silently dropped from the denominator (which would inflate
// coverage to 100%), and it is never reported as covered.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function f(uint256 a) public {
        if (a > 10) {
            if (a < 5) {
                x = 1;
            } else {
                x = 2;
            }
        } else {
            x = 3;
        }
    }
}
