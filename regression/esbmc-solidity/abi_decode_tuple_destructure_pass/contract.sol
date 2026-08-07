// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

contract AbiDecodeTupleDestructure {
    function decode(bytes memory data) public pure {
        (uint256 amount, address account, string memory label) =
            abi.decode(data, (uint256, address, string));

        require(amount == 7);
        require(account != address(0));
        require(bytes(label).length >= 0);

        assert(amount == 7);
        assert(account != address(0));
        assert(bytes(label).length >= 0);
    }
}
