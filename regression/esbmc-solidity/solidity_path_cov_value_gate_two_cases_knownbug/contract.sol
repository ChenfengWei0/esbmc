// TWO PATHS, TWO TESTS. Today the emitter produces ONE, labelled with both.
//
// KNOWNBUG: the expectations below describe what SHOULD happen. Measured today,
// `--generate-foundry-testcase` emits exactly this and nothing else:
//
//     // claim: sol:@C@C@F@set#13:path:3, sol:@C@C@F@set#13:path:2
//     function test_cov_0() public {
//       // [asserted] path exits normally; a revert fails the test
//       c0.set(0);
//     }
//
// One concrete call cannot walk two different decision sequences. `path:3` is
// the gate-passed path (`msg.value == 0`) and `path:2` is the gate-REJECTED one
// (value sent to a nonpayable entry, so `msg.value != 0`), and `c0.set(0)` sends
// nothing. So `path:2` is named as covered by a test that provably never
// reaches it.
//
// ---- WHY, AND IT IS NOT THE PAYLOAD ----
//
// The payload was wrong too and is now fixed (commit "the CE payload's
// environment now takes the LAST write before the unit"): cov-report.json now
// reports `env["msg.value"] = 0xFFFF...FFFF` for `path:2` and `0` for `path:3`,
// and `ce_consistency.py` passes on this contract.
//
// The remaining half is in the EMITTER, and it is one flag. `foundry.h`:
//
//     // Emitted as `{value: N}` ONLY when `payable` -- sending value to a
//     // non-payable method reverts. Nil / non-payable -> no value pin.
//     expr2tc msg_value;
//     bool payable = false;
//
// The generator already RECOVERS the right msg.value from `_sol_per_tx_reseed`
// -- that is why its `vm.prank` values are correct on the msg.sender cases --
// and then suppresses the pin for a non-payable method on the grounds that
// sending value there would revert. That is exactly backwards HERE: the path
// being rendered IS the revert. With the pin suppressed the two cases become
// byte-identical calls, the dedup fingerprint collapses them, and
// `claims_by_fingerprint` faithfully records both claims on the survivor.
//
// ---- WHAT THE FIX HAS TO EMIT, and why it is not `set{value: N}(...)` ----
//
// Solidity REFUSES `c0.set{value: 1}(0)` at compile time when `set` is
// non-payable, so the pin cannot simply be un-suppressed. The reverting arm has
// to go through a low-level call:
//
//     (bool ok, ) = address(c0).call{value: N}(
//         abi.encodeWithSignature("set(uint256)", 0));
//     assertFalse(ok);
//
// which is a different rendering, not a different flag -- hence KNOWNBUG rather
// than a one-line change.
//
// The contract is deliberately minimal: one unit, no source decision, so the
// ONLY decision in the run is the synthetic ABI value gate and nothing else can
// explain a second case appearing or failing to appear.
pragma solidity ^0.8.0;

contract C {
    uint256 public x;

    function set(uint256 v) external {
        x = v;
    }
}
