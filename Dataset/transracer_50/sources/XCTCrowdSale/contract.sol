// ===== XCTCrowdSale.sol =====
pragma solidity >=0.8.0;

/* taking ideas from OpenZeppelin, thanks */
contract SafeMath {
    function safeAdd(uint256 x, uint256 y) internal pure returns (uint256) {
        uint256 z = x + y;
        assert((z >= x) && (z >= y));
        return z;
    }

    function safeSub(uint256 x, uint256 y) internal pure returns (uint256) {
        assert(x >= y);
        return x - y;
    }

    function safeMin256(uint256 x, uint256 y) internal pure returns (uint256) {
        return x < y ? x : y;
    }
}

contract XCTCrowdSale is SafeMath {
    //crowdsale parameters
    address public beneficiary;
    uint256 public startBlock = 4969760;
    uint256 public constant hardCap = 4000 ether; 
    uint256 public amountRaised;

    constructor(address _beneficiary) {
        beneficiary = _beneficiary;
        amountRaised = 0;
    }

    modifier inProgress() {
      require(block.number >= startBlock);
      require(amountRaised < hardCap);
      _;
    }

    //fund raising
    receive() external payable {
        fundRaising();
    }

    function fundRaising() public payable inProgress {
        require(msg.value >= 15 ether && msg.value <= 50 ether);
        uint256 contribution = safeMin256(msg.value, safeSub(hardCap, amountRaised));
        amountRaised = safeAdd(amountRaised, contribution);

        //send to XChain Team
        payable(beneficiary).transfer(contribution);

        // Refund the msg.sender, in the case that not all of its ETH was used.
        if (contribution != msg.value) {
            uint256 overpay = safeSub(msg.value, contribution);
            payable(msg.sender).transfer(overpay);
        }
    }
}