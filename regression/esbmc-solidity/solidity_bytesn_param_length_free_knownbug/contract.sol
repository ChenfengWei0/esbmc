// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// REGRESSION: an ABI bytesN argument's `.length` is pinned while its payload
// remains recoverable for Foundry testcase generation.
//
// bytesN lowers to `BytesStatic { unsigned char data[32]; size_t length; }`
// (solidity_bytes.c:16-19). The `.length` field is legitimate -- N ranges over
// [1,32] -- but the harness builds a public function's arguments in
// `assign_param_nondet` (solidity_convert_call.cpp), where bytesN falls into
// the branch whose comment calls it a "Scalar harness parameter", and is handed
// a bare `sideeffect(nondet)` of the whole struct. That leaves `.length` as
// free as `.data`.
//
// `bytes_static_equal` returns FALSE OUTRIGHT when the two lengths differ
// (solidity_bytes.c:354), before comparing a single byte. On the chain there is
// no calldata encoding of a bytesN with any length but N, so the not-equal arm
// is reachable here for a reason no transaction can produce.
//
// `roundTrip` below is a Solidity tautology: converting a bytes32 to uint256
// and back is the identity. If the ABI length is left free, ESBMC can take a
// false branch here that no transaction can produce.
//
// The old attempted fix used bytes_static_from_uint(nondet_uint256(), N). It
// made this proof succeed but hid the nondet symbol inside a function-call
// argument, so counterexample harvesting could not recover the payload for
// generated tests. The current frontend keeps the whole BytesStatic value
// nondet at the call site and pins only `.length` with a function-entry assume,
// so the solver sees only ABI-legal bytesN values while testcase generation
// still sees the original nondet payload symbol.
//
// The companion PoC with all three probes (including the two that already
// behave correctly, so a future fix is not mistaken for a repair of everything)
// is notes/coverage/poc/D12_Bytes32LengthFree.sol.
contract C
{
  function roundTrip(bytes32 b) public pure
  {
    assert(b == bytes32(uint256(b)));
  }
}
