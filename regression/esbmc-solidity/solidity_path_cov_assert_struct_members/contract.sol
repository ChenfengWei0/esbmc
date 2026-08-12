pragma solidity ^0.8.0;

contract StructMembers {
    struct PoolInfo {
        uint256 token;
        uint256 pId;
        uint256 rewards;
    }

    PoolInfo pool;

    constructor() {
        pool = PoolInfo(1, 1, 1);
    }

    function update() external payable {
        pool.token = 2;
        pool.pId = 3;
        pool.rewards = 4;
    }
}
