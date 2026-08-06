pragma solidity ^0.8.0;

contract StringPath {
    uint256 public seen;

    function take(string memory s) public {
        if (bytes(s).length == 0) {
            seen = 1;
        } else {
            seen = 2;
        }
    }
}
