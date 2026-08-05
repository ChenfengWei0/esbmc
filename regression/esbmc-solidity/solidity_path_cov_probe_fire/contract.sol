pragma solidity ^0.8.0;

contract ProbeFire {
    uint256 public seed;
    uint256 public result;

    constructor(uint256 initialSeed) {
        seed = initialSeed;
    }

    function classify() public payable {
        if (seed == 0) {
            result = 1;
        } else {
            result = 2;
        }
    }
}
