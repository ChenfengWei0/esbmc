// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

contract Roles {
    struct RoleData {
        mapping(address => bool) members;
        bytes32 adminRole;
    }

    mapping(bytes32 => RoleData) private roles;

    function inspectRole(bytes32 role) external view {
        roles[role].adminRole;
    }
}
