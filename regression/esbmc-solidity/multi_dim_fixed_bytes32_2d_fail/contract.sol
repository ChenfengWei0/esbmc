// SPDX-License-Identifier: UNLICENSED
pragma solidity >=0.8.0 <0.9.0;

// Violation test for 2D fully-fixed `bytes32[N][M]`.
//
// KNOWNBUG: symex silently drops bytes32-equality assertions when they
// live inside the native `array_typet(array_typet(BytesStatic, N), M)`
// body — "Generated 0 VCC(s)" even though the body contains a
// guaranteed-violation `assert(buf[1][2] == bytes32(0xbeef))`. Expected
// output is VERIFICATION FAILED; current output is VERIFICATION
// SUCCESSFUL due to the silent drop, so this test serves as a
// KNOWNBUG trip-wire.
//
// Scope note: no SUCCESSFUL-variant dual is shipped. A "pass" test
// would vacuously pass under the same silent drop and the regression
// framework's KNOWNBUG / CORE modes can't distinguish "passes because
// it should" from "passes because asserts were elided". The fail trip
// wire alone is the sound signal.
//
// Suspected area: interaction between the `bytes32` → BytesStatic
// struct lowering (keccak-pack / struct-eq path in
// solidity_convert_expr.cpp) and the native 2D array_typet write
// chain. Independent of the §B `array_convt` array-of-array issue.
contract MultiDimBytes32_2DFail {
    bytes32[3][2] internal buf;

    function run() external {
        buf[0][0] = bytes32(uint256(0x1111));
        // BUG: buf[1][2] never written.
        assert(buf[1][2] == bytes32(uint256(0xbeef)));
    }
}
