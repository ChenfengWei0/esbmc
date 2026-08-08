pragma solidity ^0.8.0;

contract OwnableBase {
    uint256 owner;

    constructor() {
        owner = 1;
    }
}

contract AliasChild is OwnableBase {
    function setOwner(uint256 newOwner) external payable {
        owner = newOwner;
    }
}
