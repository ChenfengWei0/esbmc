pragma solidity >=0.8.0;

contract Types {
    struct Pair {
        uint256 value;
    }
}

contract UseTypes {
    function check() public pure {
        Types.Pair memory pair;
        assert(pair.value != 0);
    }
}
