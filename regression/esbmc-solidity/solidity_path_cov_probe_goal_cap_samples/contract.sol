pragma solidity ^0.8.0;

contract ProbeGoalCap {
    uint256 public out;

    function classify(uint256 x) public payable {
        if (x == 1) {
            out = 1;
            return;
        }
        if (x == 2) {
            out = 2;
            return;
        }
        if (x == 3) {
            out = 3;
            return;
        }
        if (x == 4) {
            out = 4;
            return;
        }
        out = 5;
    }
}
