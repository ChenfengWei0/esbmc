pragma solidity ^0.8.0;

contract ProbeSilent {
    uint256 public result;

    function set() public payable {
        result = 1;
    }
}
