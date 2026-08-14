// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Roles {
    struct RoleData {
        mapping(address => bool) members;
        bytes32 adminRole;
    }

    mapping(bytes32 => RoleData) private roles;

    function getRoleAdmin(bytes32 role) external view returns (bytes32) {
        return roles[role].adminRole;
    }
}
