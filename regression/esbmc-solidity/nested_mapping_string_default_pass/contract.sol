// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.0;

contract NestedMappingStringDefault {
    mapping(uint256 => mapping(address => string)) private advancedTokenURI;

    function check(uint256 tokenId, address minter) public view {
        require(bytes(advancedTokenURI[tokenId][minter]).length == 0);
        assert(bytes(advancedTokenURI[tokenId][minter]).length == 0);
    }
}
