pragma solidity ^0.8.0;

interface ITarget {}

contract OwnerBase {
    address private _owner;

    constructor() {
        _owner = msg.sender;
    }

    function transferOwnership(address next) public {
        require(msg.sender == _owner);
        require(next != address(0));
        _owner = next;
    }
}

contract Target is ITarget, OwnerBase {}
