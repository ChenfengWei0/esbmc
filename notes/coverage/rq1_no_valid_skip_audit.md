# RQ1 no-valid skip audit

This file records target subjects that should not be force-converted into a PUT
under the current exact-target RQ1 definition.  Each entry must cite source-level
evidence and explain why a green generated test would not be evidence for the
requested target.

## peer182 / peer_ccsolbmc__COINNetwork

- Status: skip exact-target PUT materialization.
- Evidence: `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/Peer182/subjects/peer_ccsolbmc__COINNetwork/flat.sol:1323` declares `COINNetwork` as an abstract contract.
- Current artifacts: Stage 2 has certified inherited AccessControl paths such as
  `grantRole` and `revokeRole`, but Stage 4 attempts to instantiate
  `new COINNetwork()`.
- Reason: an abstract Solidity contract is not deployable as the exact target.
  A harness such as `contract COINNetworkHarness is COINNetwork { ... }` would
  test a derived harness contract, not the formal RQ1 target.  Therefore a
  forced green Foundry test would change the target definition and must not be
  counted as a valid/PUT artifact for this subject.

## bugfix124 / pop_001_Multicall

- Status: structurally non-generatable as a PUT under the current RQ1 target
  definition; keep as `VALID_NO_PUT` rather than manufacturing a wrapper PUT.
- Evidence: `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/BugFix124/subjects/pop_001_Multicall/flat.sol:3818` declares `Multicall` as a Solidity
  `library`; its only target function is
  `multicall(State storage state, bytes[] calldata data) internal`.
- Current artifacts: canonical RQ1 result has `completion_status=no-units`.
  The RQ3 closure scan also has no corresponding concrete replay that can
  supply the required fixed witness anchor.
- Reason: the exact target exposes no deployable public/external callable unit.
  Solidity compiles an internal library call into the caller, so the available
  `MulticallHarness` fallback necessarily executes against harness-owned
  `State` storage rather than an instance of `Multicall`.  The prepared source
  also omits the protocol's externally callable `Size.multicall` entry point.
  Initializing the token interfaces needed by `totalSupply()` would require
  test doubles and would still test the wrapper's state.  Such a green harness
  is useful as a compile/link check, but it changes the exact target and must
  not be counted as a valid replay.

## peer182 / peer_soltg__constructor_state_variable_init

- Status: resolved by an exact-target constructor-behavior replay.
- Evidence: `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/Peer182/subjects/peer_soltg__constructor_state_variable_init/flat.sol:22` declares target
  `Cv1`; the file contains constructor assertions but no public/external
  functions on `Cv1`.
- Current artifacts: `put/final_deploy_concrete_fallback` executes
  `new Cv1(int256(1))` after `vm.expectRevert()` and is retained in
  `concrete-replays/manifest.json` as one strict-valid concrete replay.
- Reason: unlike a derived wrapper, contract creation executes `Cv1`'s own
  creation code and its inherited constructors.  The source-level `assert`
  statements ground the expected revert, so this is behavioral evidence for
  the exact target rather than a creation-code-existence check.

## peer182 / peer_soltg__constructor_state_variable_init_chain_alternate

- Status: skip under the current named public/external unit definition.
- Evidence: `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/Peer182/subjects/peer_soltg__constructor_state_variable_init_chain_alternate/flat.sol:20` declares
  target `Dv2`; the executable checks are constructor assertions, with no
  public/external callable functions on the target.
- Current artifacts: canonical RQ1 result has `completion_status=no-units`.
- Reason: this is a constructor-state benchmark.  Counting a generated
  constructor-only test as a PUT would require extending the RQ1 unit definition
  beyond the frozen named-function target surface.

## peer182 / peer_soltg__constructor_state_variable_init_diamond

- Status: structurally non-generatable as a PUT under the current RQ1 target
  definition; keep as `VALID_NO_PUT` until a real generalized constructor
  coordinate is certified.
- Evidence: `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/Peer182/subjects/peer_soltg__constructor_state_variable_init_diamond/flat.sol:55` declares target
  `D4v3`; the source has constructor assertions but no public/external
  callable functions on `D4v3`.
- Current artifacts: the retained concrete deployment replay exercises
  `new D4v3()` and observes the constructor-order assertion panic. The only
  proposed PUT coordinate was block number, but it is not read on the
  deployment path and therefore does not generalize executed behavior.
- Reason: renaming the concrete deployment replay as a parameterized test
  would create a fake PUT: fuzzing a coordinate that cannot affect the target
  execution is not a generalization. A zero-argument constructor replay is
  valid concrete evidence, but it is not a PUT under the frozen RQ1 unit
  definition.
- Required change to revisit: extend the RQ1 constructor-unit model with an
  actually executed, certified deployment coordinate and a matching RQ3
  concrete anchor. Until then, neither extra Forge runs nor a synthetic wrapper
  may move this case out of `VALID_NO_PUT`.

## peer182 / peer_soltg__constructors

- Status: resolved by an exact-target constructor-behavior replay.
- Evidence: `/home/samson/workspace/VeriPUT/scripts/Results/workdirs/Peer182/subjects/peer_soltg__constructors/flat.sol:18` declares target `Ccs`; all benchmark
  checks are in the constructor, and the target has no public/external
  functions.
- Current artifacts: `put/final_deploy_concrete_fallback` executes
  `new Ccs(int256(1))` after `vm.expectRevert()` and is retained in
  `concrete-replays/manifest.json` as one strict-valid concrete replay.
- Reason: the call executes `Ccs` and `Bcs` creation behavior directly.  For
  the concrete input, the two branch assertions hold and the subsequent
  source assertion `x == 3` grounds the expected assertion-panic revert.
