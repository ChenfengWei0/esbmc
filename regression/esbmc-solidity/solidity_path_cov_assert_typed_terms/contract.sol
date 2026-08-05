pragma solidity ^0.8.0;

contract TypedTerms {
    uint256 bal;
    uint256 wrapped;

    constructor() {
        bal = 1000;
        wrapped = 115792089237316195423570985008687907853269984665640564039457584007913129639935;
    }

    function add(uint256 amount) external payable {
        if (amount > 10) {
            bal += 7;
            wrapped = 6;
        }
    }
}
