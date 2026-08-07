// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

contract NestedMappingBytes32Default {
    mapping(uint256 => mapping(address => bytes32)) public minterData;

    function check(uint256 tokenId, address minter) public view {
        require(minterData[tokenId][minter] == bytes32(0));
        assert(minterData[tokenId][minter] == bytes32(0));
    }
}
