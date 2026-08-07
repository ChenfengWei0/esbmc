// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

contract AbiDecodeMsgDataSlice {
    function decode() public {
        (uint256 amount, address account) =
            abi.decode(msg.data[4:], (uint256, address));

        require(amount == 9);
        require(account != address(0));

        assert(amount == 9);
        assert(account != address(0));
    }
}
