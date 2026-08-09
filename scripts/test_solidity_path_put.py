#!/usr/bin/env python3
"""Regression for the stage-4 PUT driver's ENTRY-STATE establishment.

WHY THIS FILE EXISTS, and what it pins.

A certified region is a statement about a SLICE. The slice is named by two
things that arrive through different arguments and had, until this test existed,
two different fates:

  * a REGION bound `state.<v> in [lo, hi]`, which `build_put` established with a
    read-modify-write `vm.store`;
  * a PIN `state.<v> == k`, which `build_put` printed in the header and
    otherwise DROPPED.

A pin is `[k, k]` -- the same statement, arrived at because the operator named
the value rather than because the ladder measured it -- and `main()` already
concatenates the two when it writes the assert spec, so ESBMC has been answering
about the pinned value all along. Only the emitted test disagreed.

MEASURED, and this is the failure the omission produced. On
`FeeVault.setDiscount` the guard is `msg.sender == owner`. That is a
cross-coordinate relation, out of scope by Definition 6, so the success path
certifies only with `owner` PINNED -- which turns it into
coordinate-equals-constant. `owner` is therefore a pin on every run that
certifies that path, never a region bound. The emitted concrete case pranks
`msg.sender = 0` and leaves `owner` at whatever the constructor gave it (the
test contract's own address, since `owner = msg.sender` at deployment), and
forge reports:

    [FAIL: EvmError: Revert] test_cov_0()

i.e. the require the case was generated to walk PAST rejects it. With the pin
established the same path's PUT is green over 256 fuzz runs, and goes red on a
mutant that writes an extra state variable -- so the establishment is what makes
the test be about the certified execution at all.

TWO DIRECTIONS, because one of them is the whole point:

  * a pin whose variable HAS a storage slot must produce a `vm.store` and be
    marked ESTABLISHED in the header;
  * a pin whose variable has NO slot (a `constant`/`immutable`) must NOT be
    silently ignored -- it goes into `state_skipped` with a reason and the
    header says NOT ESTABLISHED, because "this test is not known to be inside
    the certified slice" has to be visible on the test rather than inferred
    from the absence of a line.

Pure-function: no ESBMC, no solver, no forge, no clock. The emitted-file fixture
below is a VERBATIM capture of the emitter's own output for this contract
(`--generate-foundry-testcase` on bench/FeeVault), warning banner included; a
paraphrased one could drift from the parser it is here to exercise.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from solidity_path_put import (ConcreteFallback, EmittedFile,  # noqa: E402
                               attempt_is_usable,
                               assemble_concrete_source, assemble_put_source,
                               assert_query_pins,
                               assert_query_region_entries, build_put,
                               check_esbmc_args, cell_of,
                               complete_missing_call_args,
                               constructor_param_hascode_specs,
                               constructor_param_interface_mock_specs,
                               constructor_external_interface_mock_lines,
                               constructor_staticcall_mock_lines,
                               add_esbmc_mapping_aliases,
                               canonical_state_coord_name,
                               effective_exit_kind,
                               exit_kind_asserted, find_unit_call,
                               fixture_from_esbmc_args, load_fixture_json,
                               assert_query_var_name,
                               expand_path_guard_coord_idents,
                               layout_scalar_key,
                               mapping_source_coord_alias,
                               prefer_esbmc_mapping_aliases,
                               no_oracle_reason, observed_env,
                               normal_exit_region_retreat,
                               oracle_class_summary,
                               oracle_classes_for_rung,
                               partial_ladder_already_has_strict_oracle,
                               path_condition_from_branch_claim,
                               path_conditions_from_branch_claim,
                               path_decision_assumes,
                               potential_rendered_widths_for_put,
                               rendered_env_coords_for_emitted_case,
                               parse_ladder, region_slot_vars, statement_start,
                               restore_ladder_row_names,
                               runtime_interface_mock_lines,
                               synthesize_unsupported_case_replay,
                               target_instance_for_call, rewrite_call_args,
                               truncated_loops, unwrap_normal_try_call,
                               unwindset_args)


# VERBATIM captures. Shape 1 is the assert gate's refusal, semicolon-joined on
# ONE line (aqua Aqua.dock, notes/coverage/put_roundtrip); shape 2 is the
# under-report warning, one line per loop (FarmingPool.exit, reproduced by hand
# from that unit's own cert.json). They are DIFFERENT sentences from different
# code paths, and a parser written for one and pointed at the other never fires.
TRUNC_SHAPE_1 = (
    "[put]   ladder REFUSED: ERROR: --path-cov-assert: RESULT: "
    "UNDECIDED-TRUNCATED -- the non-vacuity witness for path enc=12 did NOT "
    "come back refuted ... Loops truncated: loop 1 at file "
    "/home/samson/workspace/esbmc/src/c2goto/library/stdlib.c line 38 column 3 "
    "function __ESBMC_atexit_handler; loop 62 at file "
    "notes/coverage/inputs/aqua__Aqua.flat.sol line 2258 function dock; loop 64 "
    "at file /home/samson/workspace/esbmc/src/c2goto/library/string.c line 298 "
    "column 3 function __memset_impl")

TRUNC_SHAPE_2 = """WARNING: Coverage may be UNDER-REPORTED: 2 loop(s) hit the unwind bound while --no-unwinding-assertions was active, so the paths that needed more iterations were silently assumed away. Goals reachable only through those paths are counted as uncovered. Raise --unwind, use --unwindset/--unwindsetname for the specific loop, or switch to --k-induction / --incremental-bmc. Loops truncated:
WARNING:   loop 55 at file /home/samson/workspace/esbmc/src/c2goto/library/solidity/solidity_string.c line 206 column 5 function _str_assign
WARNING:   loop 56 at file /home/samson/workspace/esbmc/src/c2goto/library/solidity/solidity_string.c line 209 column 5 function _str_assign"""


# VERBATIM: bench/FeeVault, `--generate-foundry-testcase --focus-function
# setDiscount --solidity-max-tx 1`. Do not rewrap or tidy -- EmittedFile parses
# this text, so its exact shape is the fixture.
EMITTED = """\
// SPDX-License-Identifier: MIT
// Auto-generated by ESBMC 8.2.0
// Foundry coverage test reconstructed from ESBMC counterexamples.
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {FeeVault} from "./FeeVault.sol";

contract FeeVaultCovTest is Test {
  FeeVault c0;
  function setUp() public {
    c0 = new FeeVault();
  }
  // claim: sol:@C@FeeVault@F@setDiscount#61:path:7
  function test_cov_0() public {
    vm.prank(address(uint160(0)));
    // [asserted] path exits normally; a revert fails the test
    c0.setDiscount(address(uint160(0)), 250);
  }
}
"""

EMITTED_TARGET_AFTER_MOCK = """\
// SPDX-License-Identifier: MIT
// Auto-generated by ESBMC 8.2.0
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {Target} from "./Target.sol";

contract Mock {}

contract TargetCovTest is Test {
  Mock c0;
  Target c1;
  function setUp() public {
    c0 = new Mock();
    c1 = new Target();
  }
  // claim: sol:@C@Target@F@setFlag#9:path:7
  function test_cov_0() public {
    vm.prank(address(uint160(1)));
    // [asserted] path exits normally; a revert fails the test
    c1.setFlag(false);
  }
}
"""

EMITTED_PRECOMPILE_CTOR = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {HyperEVMRateProvider} from "./flat.sol";

contract HyperEVMRateProviderCovTest is Test {
  HyperEVMRateProvider c0;
  function setUp() public {
    c0 = new HyperEVMRateProvider(0, 0);
  }
  // claim: sol:@C@HyperEVMRateProvider@F@getSpotPriceMultiplier#75:path:3
  function test_cov_0() public {
    c0.getSpotPriceMultiplier();
  }
}
"""

PRECOMPILE_FLAT = """\
pragma solidity >=0.8.0;

contract HyperEVMRateProvider {
    uint256 private immutable _spotPriceMultiplier;
    constructor(uint32 tokenIndex, uint32 pairIndex) {
        uint8 szDecimals = HyperTokenInfoPrecompile.szDecimals(tokenIndex);
        _spotPriceMultiplier = 1e18 / (10 ** (8 - szDecimals));
        pairIndex;
    }
    function getSpotPriceMultiplier() external view returns (uint256) {
        return _spotPriceMultiplier;
    }
}

library HyperTokenInfoPrecompile {
    struct HyperTokenInfo {
        string name;
        uint64[] spots;
        uint64 deployerTradingFeeShare;
        address deployer;
        address evmContract;
        uint8 szDecimals;
        uint8 weiDecimals;
        int8 evmExtraWeiDecimals;
    }
    address public constant TOKEN_INFO_PRECOMPILE_ADDRESS =
        0x000000000000000000000000000000000000080C;
    function szDecimals(uint32 tokenIndex) internal view returns (uint8) {
        (bool success, bytes memory out) =
            TOKEN_INFO_PRECOMPILE_ADDRESS.staticcall(abi.encode(tokenIndex));
        require(success);
        HyperTokenInfo memory tokenInfo = abi.decode(out, (HyperTokenInfo));
        return tokenInfo.szDecimals;
    }
}
"""

HASCODE_CTOR_FLAT = """\
pragma solidity >=0.8.0;

contract C {
    constructor(address impl, address payable fallbackHandler, uint256 ignored) {
        require(hasCode(impl), "impl has no code");
        require(hasCode(fallbackHandler), "handler has no code");
        ignored;
    }
    function hasCode(address account) internal view returns (bool) {
        return account.code.length > 0;
    }
}
"""

# What solc reports for FeeVault: {var: (slot, offset, bytes)}. `feeBps` and
# `maxFee` are `constant` and are ABSENT, which is the fact the second direction
# below rests on.
LAYOUT = {"owner": (0, 0, 20), "feeReceiver": (1, 0, 20)}

PARAMS = [("u", "address"), ("bps", "uint16")]

LADDER = [("owner", "post == pre", "HOLDS"),
          ("feeReceiver", "post == pre", "HOLDS")]


def make_case():
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(EMITTED)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@FeeVault@F@setDiscount#61", 7)
    assert case is not None, "fixture: the emitted case for enc=7 was not found"
    return em, case


def make_case_target_after_mock():
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(EMITTED_TARGET_AFTER_MOCK)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Target@F@setFlag#9", 7)
    assert case is not None, "fixture: the emitted case for enc=7 was not found"
    return em, case


def make_case_precompile_ctor():
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(EMITTED_PRECOMPILE_CTOR)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for(
        "sol:@C@HyperEVMRateProvider@F@getSpotPriceMultiplier#75", 3)
    assert case is not None, "fixture: the precompile case was not found"
    return em, case


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        return 1
    print(f"ok: {msg}")
    return 0


def test_pin_with_a_slot_is_established():
    """`state.owner == 0` arrives as a PIN and must reach the test."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250),
                "u": (0, (1 << 160) - 1)},
        holes={}, pins={"state.owner": 0, "msg.value": 0},
        params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    bad = 0
    bad += check(put is not None, "a PUT is produced")
    text = "\n".join(put or [])
    bad += check(stats["state_stored"] == ["state.owner := 0"],
                 f"the pin is established: {stats['state_stored']}")
    bad += check("vm.store(address(c0), bytes32(uint256(0))" in text,
                 "a vm.store at owner's slot 0 is emitted")
    bad += check("PIN state.owner == 0   [ESTABLISHED by vm.store below]"
                 in text, "the header marks the pin ESTABLISHED")
    # msg.value is NOT a state pin and must not grow a store.
    bad += check("PIN msg.value == 0\n" in text + "\n",
                 "a non-state pin keeps its plain header line")
    # The two declared parameters are still lifted -- establishing a pin must
    # not cost the fuzz coordinates.
    bad += check(stats["lifted"] == ["u", "bps"],
                 f"both parameters are still lifted: {stats['lifted']}")
    return bad


def test_relation_establishes_state_from_fuzzed_sender():
    """`state.owner := msg.sender` must share the PUT's pranked sender."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250),
                "u": (0, (1 << 160) - 1),
                "msg.sender": (1, (1 << 160) - 1)},
        holes={}, pins={"msg.value": 0},
        params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes,
        establish=[{"target": "state.owner", "source": "msg.sender"}])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    text = "\n".join(put or [])
    bad += check("address p_msg_sender" in text,
                 "msg.sender is a fuzzed address parameter")
    bad += check("vm.prank(p_msg_sender);" in text,
                 "the unit call is pranked with the same parameter")
    bad += check("uint256(uint160(p_msg_sender))" in text,
                 "the storage write casts the address source to uint256")
    bad += check(stats["state_stored"] == ["state.owner := msg.sender"],
                 f"the relation is reported as stored: {stats['state_stored']}")
    bad += check(stats["established_relations"] == [
        {"target": "state.owner", "source": "msg.sender"}],
        f"the relation travels in stats: {stats['established_relations']}")
    return bad


def test_precheck_only_identifies_rendered_width_not_oracle_strength():
    """A point-shaped rendered region may still carry a verifier oracle."""
    em, case = make_case()
    widths = potential_rendered_widths_for_put(
        "setDiscount", PARAMS, em, case,
        region={"u": (0, 0), "bps": (250, 250),
                "state.owner": (0, 10)},
        holes={})
    bad = 0
    bad += check(widths == {"u": 1, "bps": 1},
                 f"only rendered calldata coordinates count: {widths}")
    bad += check(not any(w > 1 for w in widths.values()),
                 "the precheck can still tell that no rendered coordinate is wide")
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2,
        "sol:@C@FeeVault@F@setDiscount#61",
        region={"u": (0, 0), "bps": (250, 250),
                "state.owner": (0, 10)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=LADDER, notes=[])
    bad += check(put is not None and stats["state_asserts"] == 2,
                 "a no-wide rendered region still emits a one-point PUT when "
                 "the ladder supplies state oracles")
    return bad


def test_no_wide_rendered_coordinate_without_oracle_stays_concrete():
    """No fuzz dimension plus no rendered assertion is not a PUT."""
    em, case = make_case()
    notes = []
    try:
        build_put(
            "FeeVault", "setDiscount", 7, 2,
            "sol:@C@FeeVault@F@setDiscount#61",
            region={"u": (0, 0), "bps": (250, 250)},
            holes={}, pins={}, params=PARAMS, emitted=em, case=case,
            layout=LAYOUT, ladder_rows=[], notes=notes)
    except ConcreteFallback as exc:
        return check("no verifier-backed oracle" in exc.reason,
                     f"the fallback explains the missing oracle: {exc.reason}")
    return check(False, "expected ConcreteFallback")


def test_precheck_keeps_possible_parameterized_candidate_on_wide_env():
    """A wide environment coordinate may still become a PUT parameter."""
    em, case = make_case()
    widths = potential_rendered_widths_for_put(
        "setDiscount", PARAMS, em, case,
        region={"u": (0, 0), "bps": (250, 250),
                "msg.sender": (1, 99)},
        holes={})
    bad = 0
    bad += check(widths == {"u": 1, "bps": 1, "msg.sender": 99},
                 f"wide establishable environment coordinate is retained: {widths}")
    bad += check(any(w > 1 for w in widths.values()),
                 "the assertion ladder is not skipped when a PUT may be emitted")
    return bad


def test_storage_oracles_read_the_actual_target_instance_not_c0():
    """Mocks can occupy c0; storage oracles must follow the unit call receiver."""
    em, case = make_case_target_after_mock()
    notes = []
    put, stats = build_put(
        "Target", "setFlag", 7, 1, "sol:@C@Target@F@setFlag#9",
        region={"flag_": (0, 1), "msg.sender": (1, 1)},
        holes={}, pins={"state.owner": 1},
        params=[("flag_", "bool")], emitted=em, case=case,
        layout={"flag": (2, 0, 1), "owner": (3, 0, 20)},
        ladder_rows=[("flag", "post == flag_", "HOLDS"),
                     ("owner", "post == pre", "HOLDS")],
        notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("vm.store(address(c1), bytes32(uint256(3))" in text,
                 "state pin is established on the actual target c1")
    bad += check("vm.load(address(c1), bytes32(uint256(2))" in text,
                 "post-state oracle reads the actual target c1")
    bad += check("address(c0)" not in text,
                 "mock c0 is never used as the storage oracle target")
    bad += check(stats["lifted"] == ["flag_"],
                 f"the bool parameter is still lifted: {stats['lifted']}")
    return bad


def test_path_cov_fixture_replays_constructor_then_pins_state():
    """Foundry may need a legal constructor replay before fixture state pins."""
    em, case = make_case_target_after_mock()
    notes = []
    put, _stats = build_put(
        "Target", "setFlag", 7, 1, "sol:@C@Target@F@setFlag#9",
        region={"flag_": (0, 1), "msg.sender": (1, 1)},
        holes={}, pins={}, params=[("flag_", "bool")], emitted=em,
        case=case, layout={"flag": (2, 0, 1), "owner": (3, 0, 20)},
        ladder_rows=[("flag", "post == flag_", "HOLDS")], notes=notes)
    text = assemble_put_source(
        em, case, [put], "TargetCovTest_Target_setFlag_put7",
        {"contract": "Target", "skip_constructor": True,
         "foundry": {"constructor_args": ["address(uint160(7))"]},
         "state": {"owner": "1"}},
        {"flag": (2, 0, 1), "owner": (3, 0, 20)}, "Target", "setFlag")
    bad = 0
    bad += check("c1 = new Target(address(uint160(7)));" in text,
                 "the target constructor args are rewritten for replay")
    bad += check("vm.etch(_esbmc_fixture_c1" not in text,
                 "runtimeCode skip is not used when replay args are present")
    bad += check("vm.store(address(c1), bytes32(uint256(3))" in text,
                 "fixture scalar state is established on c1")
    bad += check("try new Mock() returns (Mock _esbmc_setup_c0)" in text,
                 "unused unrelated mock setup is revert-tolerant")
    return bad


def test_constructor_staticcall_mock_is_scoped_to_deployment():
    """A missing local precompile should not make a legal constructor red."""
    em, case = make_case_precompile_ctor()
    with tempfile.TemporaryDirectory() as project:
        os.makedirs(os.path.join(project, "src"))
        with open(os.path.join(project, "src", "flat.sol"), "w") as f:
            f.write(PRECOMPILE_FLAT)
        mocks = constructor_staticcall_mock_lines(
            project,
            {"entry_storage": {
                "_spotPriceMultiplier": "1000000000000000000"
            }}, "    ")
    text = assemble_concrete_source(
        em, case,
        "HyperEVMRateProviderCovTest_HyperEVMRateProvider_getSpot"
        "PriceMultiplier_concrete3_fb",
        None, None, "HyperEVMRateProvider", "getSpotPriceMultiplier", mocks)
    bad = 0
    mock_at = text.find("vm.mockCall(address(0x000000000000000000000000000000000000080C)")
    new_at = text.find("c0 = new HyperEVMRateProvider(0, 0);")
    clear_at = text.find("vm.clearMockedCalls();")
    bad += check(mock_at >= 0, "the constructor precompile is mocked")
    bad += check("uint8(8)" in text,
                 "szDecimals is reconstructed from the witness state")
    bad += check(mock_at < new_at < clear_at,
                 "the mock is active only for deployment")
    bad += check("function test_cov_0() public" in text,
                 "the concrete replay remains a concrete replay")
    return bad


def test_constructor_param_interface_calls_are_mocked_before_deploy():
    flat = """\
pragma solidity >=0.8.0;
interface IERC20 { function decimals() external view returns (uint8); }
interface IOracle { function getQuote(uint256 x) external view returns (uint256); }
interface IAuthority { function getImpl() external view returns (address); }
contract C {
  constructor(address token, address oracle, address authority) {
    IERC20(token).decimals();
    require(IOracle(oracle).getQuote(1) != 0);
    setAuthority(authority);
  }
  function setAuthority(address authority) public {
    require((IAuthority(authority)).getImpl() != address(0));
  }
  function f() external {}
}
"""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {C} from "./flat.sol";
contract CCovTest_0 is Test {
  C c0;
  function setUp() public {
    c0 = new C(address(uint160(1)), address(uint160(2)), address(uint160(3)));
  }
  // claim: sol:@C@C@F@f#9:path:1
  function test_cov_0() public {
    c0.f();
  }
}
contract CCovTest_1 is Test {
  C c0;
  function setUp() public {
    c0 = new C(address(uint160(1)), address(uint160(2)), address(uint160(3)));
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@f#9", 1)
    with tempfile.TemporaryDirectory() as project:
        os.makedirs(os.path.join(project, "src"))
        with open(os.path.join(project, "src", "flat.sol"), "w") as f:
            f.write(flat)
        specs = constructor_param_interface_mock_specs(project, "C")
    put = ["", "  function test_put_C_f_path1() public {", "    c0.f();", "  }"]
    text = assemble_put_source(
        em, case, [put], "CCovTest_0_put", contract="C", unit="f",
        constructor_param_mocks=specs, flat_source=flat)
    bad = 0
    new_at = text.find("c0 = new C(address(uint160(1)), "
                       "address(uint160(2)), address(uint160(3)));")
    decimals_at = text.find('abi.encodeWithSignature("decimals()")')
    quote_at = text.find('abi.encodeWithSignature("getQuote(uint256)")')
    impl_at = text.find('abi.encodeWithSignature("getImpl()")')
    bad += check(len(specs) == 3, f"three constructor arg mocks found: {specs}")
    bad += check(0 <= decimals_at < new_at,
                 "token decimals mock is inserted before deployment")
    bad += check(0 <= quote_at < new_at,
                 "oracle quote mock is inserted before deployment")
    bad += check(0 <= impl_at < new_at,
                 "indirect parenthesized interface mock is inserted before "
                 "deployment")
    bad += check("abi.encode(address(uint160(3000)))" in text,
                 "address return used by a constructor guard is nonzero")
    bad += check("address _esbmc_ctor_arg_mock" in text,
                 "constructor argument address is materialized for mockCall")
    bad += check("vm.etch(_esbmc_ctor_arg_mock" not in text,
                 "precompile constructor arguments are not etched")
    bad += check(text.count('abi.encodeWithSignature("decimals()")') == 2,
                 "each test contract setUp gets its own constructor mocks")
    return bad


def test_constructor_param_hascode_args_are_etched_before_deploy():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {C} from "./flat.sol";
contract CCovTest_0 is Test {
  C c0;
  function setUp() public {
    c0 = new C(address(uint160(0)), payable(address(uint160(11))), 3);
  }
  // claim: sol:@C@C@F@f#9:path:1
  function test_cov_0() public {
    c0.f();
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@f#9", 1)
    with tempfile.TemporaryDirectory() as project:
        os.makedirs(os.path.join(project, "src"))
        with open(os.path.join(project, "src", "flat.sol"), "w") as f:
            f.write(HASCODE_CTOR_FLAT)
        specs = constructor_param_hascode_specs(project, "C")
    put = ["", "  function test_put_C_f_path1() public {", "    c0.f();", "  }"]
    text = assemble_put_source(
        em, case, [put], "CCovTest_0_put", contract="C", unit="f",
        constructor_param_hascode_mocks=specs, flat_source=HASCODE_CTOR_FLAT)
    new_at = text.find(
        "c0 = new C(address(uint160(0)), payable(address(uint160(11))), 3);")
    impl_at = text.find("address _esbmc_ctor_code_0_0 = address(uint160(0));")
    handler_at = text.find(
        "address _esbmc_ctor_code_0_1 = payable(address(uint160(11)));")
    bad = 0
    bad += check([s["param_name"] for s in specs]
                 == ["impl", "fallbackHandler"],
                 f"hasCode address parameters are found: {specs}")
    bad += check(0 <= impl_at < new_at,
                 "the first hasCode constructor arg is etched before deploy")
    bad += check(0 <= handler_at < new_at,
                 "the payable hasCode constructor arg is etched before deploy")
    bad += check(text.count("vm.etch(_esbmc_ctor_code_") == 2,
                 "only hasCode constructor address args are etched")
    return bad


def test_constructor_nonzero_address_guards_repair_zero_defaults():
    flat = """\
pragma solidity >=0.8.0;
contract C {
  constructor(address implementationAuthority_, address idFactory_) {
    setImplementationAuthority(implementationAuthority_);
    require(idFactory_ != address(0), "invalid argument - zero address");
  }
  function setImplementationAuthority(address implementationAuthority_) public {
    require(implementationAuthority_ != address(0), "invalid argument - zero address");
  }
  function setIdFactory(address idFactory_) external {}
}
"""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {C} from "./flat.sol";
contract CCovTest is Test {
  C c0;
  function setUp() public {
    c0 = new C(address(uint160(0)), address(uint160(1)));
  }
  // claim: sol:@C@C@F@setIdFactory#9:path:15
  function test_cov_0() public {
    c0.setIdFactory(address(uint160(2)));
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@setIdFactory#9", 15)
    put = [
        "",
        "  function test_put_C_setIdFactory_path15(address idFactory_) public {",
        "    c0.setIdFactory(idFactory_);",
        "  }",
    ]
    text = assemble_put_source(
        em, case, [put], "CCovTest_put", contract="C",
        unit="setIdFactory", flat_source=flat)
    bad = 0
    bad += check("new C(address(uint160(1000)), address(uint160(1)))" in text,
                 "zero constructor arg rejected by source guard is repaired")
    bad += check("new C(address(uint160(0)), address(uint160(1)))" not in text,
                 "the guarded zero constructor arg is gone")
    return bad


def test_constructor_dynamic_array_defaults_cover_indexed_reads():
    flat = """\
pragma solidity >=0.8.0;
contract C {
  uint256 p4;
  constructor(uint256[] memory prices) {
    p4 = prices[4];
  }
  function f() external {}
}
"""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {C} from "./flat.sol";
contract CCovTest is Test {
  C c0;
  function setUp() public {
    c0 = new C(new uint256[](4));
  }
  // claim: sol:@C@C@F@f#9:path:1
  function test_cov_0() public {
    c0.f();
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@f#9", 1)
    put = ["", "  function test_put_C_f_path1() public {", "    c0.f();", "  }"]
    text = assemble_put_source(
        em, case, [put], "CCovTest_put", contract="C", unit="f",
        flat_source=flat)
    bad = 0
    bad += check("new C(new uint256[](5))" in text,
                 "constructor dynamic-array default covers indexed reads")
    bad += check("new C(new uint256[](4))" not in text,
                 "the too-short constructor array is gone")
    return bad


def test_unsupported_skeleton_is_synthesized_for_certified_put_lift():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {MarketUpdateProposer} from "./flat.sol";
contract MarketUpdateProposerCovTest is Test {
  function setUp() public {
    // UNSUPPORTED: constructor of MarketUpdateProposer has an argument type ESBMC cannot yet render as a literal
  }
  // claim: sol:@C@MarketUpdateProposer@F@setGovernor#196:path:15
  function test_cov_0() public {
    // UNSUPPORTED: MarketUpdateProposer.setGovernor has an argument type ESBMC cannot yet render as a literal
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
        case = em.case_for("sol:@C@MarketUpdateProposer@F@setGovernor#196",
                           15)
        notes = []
        repaired, repaired_case, changed = synthesize_unsupported_case_replay(
            em, case, "MarketUpdateProposer", "setGovernor",
            [("newGovernor", "address")],
            ["address", "address", "address", "ITimelock"], notes)
    finally:
        os.unlink(path)

    body = repaired.lines[repaired_case[3][0] + 1:repaired_case[3][1]]
    call_i = find_unit_call(body, "setGovernor")
    bad = 0
    bad += check(changed, "the unsupported skeleton is repaired")
    bad += check(call_i is not None,
                 "the synthesized body contains the unit call")
    text = "\n".join(body)
    full_text = "\n".join(repaired.lines)
    bad += check("MarketUpdateProposer c0 = new MarketUpdateProposer(" in text,
                 "a local target deployment is synthesized")
    bad += check("ITimelock(address(uint160(1003)))" in text,
                 "interface constructor parameters get nonzero placeholders")
    bad += check("import {MarketUpdateProposer, ITimelock} from" in full_text,
                 "custom constructor parameter types are imported")

    put, stats = build_put(
        "MarketUpdateProposer", "setGovernor", 15, 1,
        "sol:@C@MarketUpdateProposer@F@setGovernor#196",
        region={"newGovernor": (1, (1 << 160) - 1)},
        holes={}, pins={}, params=[("newGovernor", "address")],
        emitted=repaired, case=repaired_case, layout={}, ladder_rows=[],
        notes=notes, lift_unconstrained_calldata=True, exit_kind="normal")
    put_text = "\n".join(put or [])
    bad += check(put is not None, "build_put no longer refuses with no call")
    bad += check(stats["lifted"] == ["newGovernor"],
                 f"the source-synthesized argument is lifted: "
                 f"{stats['lifted']}")
    bad += check("c0.setGovernor(newGovernor);" in put_text,
                 "the PUT calls the target with the fuzz parameter")
    return bad


def test_esbmc_interface_mock_completion_adds_inherited_overloads():
    flat = """\
pragma solidity >=0.8.0;
interface IBase {
  function safeTransferFrom(address from, address to, uint256 tokenId) external;
}
interface IChild is IBase {
  function safeTransferFrom(address from, address to, uint256 tokenId, bytes calldata data) external;
  function ownerOf(uint256 tokenId) external view returns (address owner);
}
contract C { function f() external {} }
"""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {C, IChild} from "./flat.sol";
contract ESBMCMock_IChild is IChild {
  function safeTransferFrom(address, address, uint256, bytes memory) external pure override {}
  function ownerOf(uint256) external pure override returns (address) { return address(0); }
}
contract CCovTest is Test {
  C c0;
  function setUp() public { c0 = new C(); }
  // claim: sol:@C@C@F@f#9:path:1
  function test_cov_0() public {
    c0.f();
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@f#9", 1)
    put = ["", "  function test_put_C_f_path1() public {", "    c0.f();", "  }"]
    text = assemble_put_source(
        em, case, [put], "CCovTest_put", contract="C", unit="f",
        flat_source=flat)
    bad = 0
    bad += check("VeriPUT completed inherited/overloaded interface stubs" in text,
                 "the mock completion note is emitted")
    bad += check("function safeTransferFrom(address, address, uint256) "
                 "external pure override {}" in text,
                 "the inherited overload is added to the ESBMC mock")
    bad += check("function safeTransferFrom(address, address, uint256, "
                 "bytes memory) external pure override {}" in text,
                 "the existing overload is preserved")
    return bad


def test_runtime_interface_mock_lines_cover_literal_address_calls():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
        with open(os.path.join(tmp, "src", "flat.sol"), "w") as f:
            f.write("""\
pragma solidity >=0.8.0;
interface IRouter {
  function WETH() external pure returns (address);
  function swapETHForExactTokens(
    uint256 amountOut,
    address[] calldata path,
    address to,
    uint256 deadline
  ) external payable returns (uint256[] memory amounts);
}
contract C {
  IRouter usi =
    IRouter(0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D);
  function f(address payable t) external {
    address[] memory path = new address[](2);
    path[0] = usi.WETH();
    path[1] = t;
    usi.swapETHForExactTokens(1, path, address(this), block.timestamp);
  }
}
""")
        lines = runtime_interface_mock_lines(tmp, "    ")
    text = "\n".join(lines)
    bad = 0
    bad += check("vm.etch(_esbmc_ext_mock_0, hex\"00\");" in text,
                 "the literal interface address is given code")
    bad += check('abi.encodeWithSignature("WETH()")' in text,
                 "the zero-argument address-returning call is mocked")
    bad += check("abi.encode(address(0))" in text,
                 "the address return is encoded with the right ABI shape")
    bad += check('abi.encodeWithSignature("swapETHForExactTokens(uint256,'
                 'address[],address,uint256)")' in text,
                 "calldata arrays and uint aliases are canonicalized")
    bad += check("abi.encode(new uint256[](0))" in text,
                 "dynamic-array returns use an ABI-compatible empty array")
    return bad


def test_constructor_external_interface_mocks_cover_router_factory_chain():
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
        with open(os.path.join(tmp, "src", "flat.sol"), "w") as f:
            f.write("""\
pragma solidity >=0.8.0;
interface IRouter {
  function factory() external view returns (address);
}
interface IRouterBase {
  function factory() external view returns (address);
}
interface IFactory {
  function createPair(address a, address b) external returns (address);
}
contract DCFLike {
  address public router = 0x10ED43C718714eb63d5aA57B78B54704E256024E;
  IRouter public uniswapV2Router;
  address public pairAddress;
  constructor() {
    uniswapV2Router = IRouter(router);
    pairAddress = IFactory(uniswapV2Router.factory()).createPair(
      address(1),
      address(this)
    );
  }
}
""")
        lines = constructor_external_interface_mock_lines(tmp, "    ")
    text = "\n".join(lines)
    bad = 0
    bad += check("address _esbmc_ext_mock_0 = "
                 "address(0x10ED43C718714eb63d5aA57B78B54704E256024E);"
                 in text,
                 "the constructor router literal address is mocked")
    bad += check('abi.encodeWithSignature("factory()")' in text,
                 "the router factory() call is mocked")
    bad += check("abi.encode(address(0))" in text,
                 "factory() returns a mockable zero-address factory")
    bad += check('vm.mockCall(address(0), '
                 'abi.encodeWithSignature("createPair(address,address)")'
                 in text,
                 "the chained factory createPair call is mocked")
    return bad


def test_runtime_interface_mocks_survive_constructor_mock_clear():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";
contract VaultCovTest is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault();
  }
  // claim: sol:@C@Vault@F@pay#41:path:7
  function test_cov_0() public {
    c1.pay();
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Vault@F@pay#41", 7)
    put = [
        "",
        "  function test_put_Vault_pay_path7() public {",
        "    c1.pay();",
        "  }",
    ]
    constructor_mocks = [
        "    vm.mockCall(address(0x0000000000000000000000000000000000000004), "
        "bytes(\"\"), abi.encode(uint256(1)));",
    ]
    runtime_mocks = [
        "    address _esbmc_ext_mock_0 = "
        "address(0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D);",
        "    vm.etch(_esbmc_ext_mock_0, hex\"00\");",
        "    vm.mockCall(_esbmc_ext_mock_0, "
        "abi.encodeWithSignature(\"WETH()\"), abi.encode(address(0)));",
    ]
    text = assemble_put_source(
        em, case, [put], "VaultCovTest_put", contract="Vault", unit="pay",
        constructor_mocks=constructor_mocks, runtime_mocks=runtime_mocks)
    bad = 0
    bad += check(text.index("vm.mockCall(address(0x000000000000000000000000"
                            "0000000000000004)")
                 < text.index("c1 = new Vault();"),
                 "constructor mocks still precede deployment")
    bad += check(text.index("c1 = new Vault();")
                 < text.index("vm.clearMockedCalls();")
                 < text.index("vm.etch(_esbmc_ext_mock_0"),
                 "runtime mocks are installed after constructor mock clearing")
    bad += check(text.index("vm.etch(_esbmc_ext_mock_0")
                 < text.index("function test_put_Vault_pay_path7"),
                 "runtime mocks are in the setup preamble, not inside the PUT")
    return bad


def test_pranked_constructor_replay_sets_tx_origin_too():
    """A one-arg startPrank does not satisfy constructor onlyRealPeople gates."""
    src = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {EOAOnly} from "./flat.sol";
contract EOAOnlyCovTest is Test {
  EOAOnly c0;
  function setUp() public {
    vm.startPrank(address(uint160(7)));
    c0 = new EOAOnly(1);
    vm.stopPrank();
  }
  // claim: sol:@C@EOAOnly@F@setX#17:path:1
  function test_cov_0() public {
    c0.setX(1);
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(src)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@EOAOnly@F@setX#17", 1)
    put = [
        "  function test_put_EOAOnly_setX_path1(uint256 x) public {",
        "    c0.setX(x);",
        "    assertTrue(true);",
        "  }",
    ]
    text = assemble_put_source(
        em, case, [put], "EOAOnlyCovTest_EOAOnly_setX_put1",
        None, None, "EOAOnly", "setX")
    bad = 0
    bad += check("vm.startPrank(address(uint160(7)), address(uint160(7)));"
                 in text,
                 "constructor replay sets both msg.sender and tx.origin")
    bad += check("vm.startPrank(address(uint160(7)));" not in text,
                 "the red one-argument constructor prank is gone")
    bad += check("function test_put_EOAOnly_setX_path1" in text,
                 "the PUT still assembles")
    return bad


def test_pin_without_a_slot_is_reported_not_dropped():
    """`state.feeBps == 250` is a `constant`: no slot, and it must SAY so."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={"state.feeBps": 250},
        params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    bad = 0
    text = "\n".join(put or [])
    bad += check(stats["state_stored"] == [],
                 "nothing is stored for a variable with no slot")
    bad += check(any(s.startswith("state.feeBps ")
                     for s in stats["state_skipped"]),
                 f"the unreachable pin is REPORTED: {stats['state_skipped']}")
    bad += check("PIN state.feeBps == 250   [NOT ESTABLISHED" in text,
                 "the header marks the pin NOT ESTABLISHED")
    return bad


def test_region_bound_still_wins_over_a_duplicate_pin():
    """A name in BOTH region and pins must be established ONCE, by the region.

    Two ledgers of one coordinate is the shape this project has paid for
    before: the region bound is the measured statement and the pin is the
    operator's, so a duplicate must not emit two stores whose order decides the
    entry state.
    """
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "state.owner": (7, 7)},
        holes={}, pins={"state.owner": 0},
        params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(stats["state_stored"] == ["state.owner := 7"],
                 f"the REGION bound wins, once: {stats['state_stored']}")
    bad += check(text.count("vm.store(address(c0), bytes32(uint256(0))") == 1,
                 "exactly one store lands on owner's slot")
    return bad


def test_env_agreement_emits_when_the_preamble_matches():
    """`msg.sender in [0, 0]` against a preamble that pranks 0: EMIT.

    This is the FeeVault enc=7 case verbatim, and it is here as the control.
    A gate that only ever refuses is indistinguishable from one that is broken,
    and this fixture is what makes the refusal below a discriminator rather than
    a constant.
    """
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "msg.sender": (0, 0)},
        holes={}, pins={"msg.value": 0},
        params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    bad = 0
    bad += check(put is not None,
                 f"agreement emits a PUT (notes: {notes})")
    bad += check(stats and not stats.get("env_unchecked"),
                 "nothing is reported unchecked when both are comparable")
    return bad


def test_env_sender_disagreement_is_ESTABLISHED_not_refused():
    """`msg.sender in [1, 1]` against a preamble that pranks 0: REWRITE.

    THIS TEST USED TO ASSERT THE OPPOSITE, and the change is deliberate rather
    than a relaxation. Refusing was correct and it was also terminal: the
    preamble's sender comes from a DIFFERENT query's counterexample, so it
    essentially never lands inside a certified interval by chance, and checking
    alone converted every sender region into nothing. Measured on
    farming.setDistributor, both certified regions were refused on exactly
    this.

    `msg.sender` is the one environment quantity a Foundry test can CHOOSE, so
    it is now established. What must NOT change is that the test then really
    runs under the certified value, which is what the last two checks pin --
    the new prank present AND the old one gone. Rewriting that leaves the
    original line behind would be a test claiming a sender it does not use.
    """
    em, case = make_case()
    notes = []
    put, _stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "msg.sender": (1, 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    bad = 0
    bad += check(put is not None,
                 f"a disagreeing sender is now ESTABLISHED, not refused "
                 f"(notes: {notes})")
    if put is None:
        return bad + 3
    txt = "\n".join(put)
    bad += check("vm.prank(address(uint160(1)));" in txt,
                 "the governing prank is rewritten to the certified value")
    # THE OLD PRANK MUST BE GONE FROM THE CODE, AND IS EXPECTED TO SURVIVE IN
    # THE COMMENT. Two wrong versions of this check ran before this one, and
    # both failed for reasons unrelated to the sender:
    #   * `uint160(0)` -- also occurs inside the bound() of an address
    #     PARAMETER whose certified interval starts at 0;
    #   * the whole prank statement anywhere in the text -- the disclosure line
    #     quotes it, on purpose, as "(replacing `...`)", which is exactly the
    #     evidence a reader needs.
    # So the check is scoped to STATEMENT lines. Comments are excluded rather
    # than the quote being removed from the disclosure: a rewrite that does not
    # say what it replaced is a rewrite nobody can audit.
    code = [ln for ln in put if not ln.strip().startswith("//")]
    bad += check(not any("vm.prank(address(uint160(0)));" in ln
                         for ln in code),
                 "and the emitted case's own prank is GONE from the CODE "
                 "(it stays quoted in the disclosure, which is the point)")
    bad += check(any("environment ESTABLISHED" in ln for ln in put),
                 "the rewrite is disclosed on the test, so the prank is not "
                 "mistaken for part of the reconstructed counterexample")
    return bad


def test_target_sender_prank_is_inserted_after_replay_setup():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";
contract VaultCovTest is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault();
  }
  // claim: sol:@C@Vault@F@pay#41:path:7
  function test_cov_0() public {
    vm.prank(address(uint160(1)));
    // [asserted] path exits normally; a revert fails the test
    c1.pay(1);
    // [revert-tolerant] outcome not asserted
    try c1.pay(2) {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Vault@F@pay#41", 7)
    notes = []
    put, stats = build_put(
        "Vault", "pay", 7, 1, "sol:@C@Vault@F@pay#41",
        region={"msg.sender": (0, (1 << 160) - 1),
                "amount": (0, 10)},
        holes={}, pins={}, params=[("amount", "uint256")],
        emitted=em, case=case, layout={}, ladder_rows=[], notes=notes)
    text = "\n".join(put or [])
    old_prank = text.find("vm.prank(address(uint160(1)));")
    setup_call = text.find("try c1.pay(1) {} catch {}")
    target_prank = text.rfind("vm.prank(p_msg_sender);")
    target_call = text.rfind("try c1.pay(amount) {} catch {}")
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check(old_prank != -1 and setup_call != -1
                 and old_prank < setup_call,
                 "the original replay prank stays with the replay setup")
    bad += check(target_prank != -1 and target_call != -1
                 and setup_call < target_prank < target_call,
                 "a target-local prank is inserted after replay setup")
    bad += check("[revert-tolerant setup]" in text,
                 "the prefix normal-exit assertion is not a PUT oracle")
    bad += check(any("pre-target replay call" in n for n in notes),
                 f"the setup tolerance is reported: {notes}")
    bad += check(stats and "msg.sender" in stats.get("wide_fuzz_coords", []),
                 f"sender fuzz width is still counted: {stats}")
    return bad


def test_env_value_pin_disagreement_refuses():
    """`msg.value == 7` against a call carrying no `{value:}`: REFUSE.

    The ABSENCE of a value option is 0 -- a fact about the EVM, not a guess --
    so this is a genuine disagreement and not an unknown.
    """
    em, case = make_case()
    notes = []
    put, _stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={"msg.value": 7}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=LADDER, notes=notes)
    bad = 0
    bad += check(put is None, "a disagreeing msg.value REFUSES")
    bad += check(any("msg.value is certified at 7" in n for n in notes),
                 f"the refusal names msg.value: {notes}")
    return bad


def test_uncomparable_env_quantity_refuses_emission():
    """`tx.origin == 42` still cannot produce a test outside its proof slice."""
    em, case = make_case()
    notes = []
    put, _stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "tx.origin": (42, 42)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    bad = 0
    bad += check(put is None,
                 "an environment slice the emitter cannot establish REFUSES")
    bad += check(any("tx.origin is certified at 42" in note
                     and "cannot establish" in note for note in notes),
                 f"the refusal names the unsupported environment slice: {notes}")
    return bad


def test_block_timestamp_point_is_established_with_warp():
    """A singleton timestamp region becomes a `vm.warp` before the call."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.timestamp": (42, 42)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"timestamp point emits instead of refusing: {notes}")
    bad += check("    vm.warp(42);" in text,
                 "block.timestamp point is established by vm.warp")
    bad += check(stats["fuzz_params"] == 2,
                 f"point timestamp does not add a fuzz param: {stats}")
    return bad


def test_block_timestamp_range_is_fuzzed_with_warp():
    """A wide timestamp region is a bounded fuzz parameter passed to vm.warp."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.timestamp": (10, 20)},
        holes={"block.timestamp": [13]}, pins={}, params=PARAMS,
        emitted=em, case=case, layout=LAYOUT, ladder_rows=LADDER,
        notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"timestamp range emits instead of refusing: {notes}")
    bad += check("uint256 p_block_timestamp" in text,
                 "wide timestamp is in the PUT signature")
    bad += check("p_block_timestamp = bound(p_block_timestamp, 10, 20);" in text,
                 "wide timestamp is bounded")
    bad += check("vm.assume(p_block_timestamp != 13);" in text,
                 "timestamp holes are preserved")
    bad += check("    vm.warp(p_block_timestamp);" in text,
                 "wide timestamp drives vm.warp")
    bad += check(stats["fuzz_params"] == 3
                 and "block.timestamp" in stats["lifted"],
                 f"timestamp range is counted as fuzzed: {stats}")
    return bad


def test_block_number_point_is_established_with_roll():
    """A singleton block-number region becomes a `vm.roll` before the call."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.number": (42, 42)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"block-number point emits instead of refusing: {notes}")
    bad += check("    vm.roll(42);" in text,
                 "block.number point is established by vm.roll")
    bad += check(stats["fuzz_params"] == 2,
                 f"point block.number does not add a fuzz param: {stats}")
    return bad


def test_block_env_pins_are_established_with_cheatcodes():
    """Pinned block env slices are established, not left unchecked."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={"block.timestamp": 42, "block.number": 7,
                        "block.chainid": 31337},
        params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"block env pins emit instead of refusing: {notes}")
    bad += check("    vm.warp(42);" in text,
                 "pinned block.timestamp is established by vm.warp")
    bad += check("    vm.roll(7);" in text,
                 "pinned block.number is established by vm.roll")
    bad += check("    vm.chainId(31337);" in text,
                 "pinned block.chainid is established by vm.chainId")
    bad += check(stats.get("env_unchecked") == [],
                 f"established block pins do not remain unchecked: {stats}")
    bad += check(stats["fuzz_params"] == 2,
                 f"point block pins do not add fuzz params: {stats}")
    return bad


def test_block_number_range_is_fuzzed_with_roll():
    """A wide block-number region is a bounded fuzz parameter for vm.roll."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.number": (10, 20)},
        holes={"block.number": [13]}, pins={}, params=PARAMS,
        emitted=em, case=case, layout=LAYOUT, ladder_rows=LADDER,
        notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"block-number range emits instead of refusing: {notes}")
    bad += check("uint256 p_block_number" in text,
                 "wide block.number is in the PUT signature")
    bad += check("p_block_number = bound(p_block_number, 10, 20);" in text,
                 "wide block.number is bounded")
    bad += check("vm.assume(p_block_number != 13);" in text,
                 "block.number holes are preserved")
    bad += check("    vm.roll(p_block_number);" in text,
                 "wide block.number drives vm.roll")
    bad += check(stats["fuzz_params"] == 3
                 and "block.number" in stats["lifted"],
                 f"block.number range is counted as fuzzed: {stats}")
    return bad


def test_block_chainid_range_is_fuzzed_with_chainId():
    """A wide chain-id region is a bounded fuzz parameter for vm.chainId."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.chainid": (10, 20)},
        holes={"block.chainid": [13]}, pins={}, params=PARAMS,
        emitted=em, case=case, layout=LAYOUT, ladder_rows=LADDER,
        notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"chain-id range emits instead of refusing: {notes}")
    bad += check("uint256 p_block_chainid" in text,
                 "wide block.chainid is in the PUT signature")
    bad += check("p_block_chainid = bound(p_block_chainid, 10, 20);" in text,
                 "wide block.chainid is bounded")
    bad += check("vm.assume(p_block_chainid != 13);" in text,
                 "block.chainid holes are preserved")
    bad += check("    vm.chainId(p_block_chainid);" in text,
                 "wide block.chainid drives vm.chainId")
    bad += check(stats["fuzz_params"] == 3
                 and "block.chainid" in stats["lifted"],
                 f"block.chainid range is counted as fuzzed: {stats}")
    return bad


def test_extra_numeric_env_ranges_use_modeled_cheatcodes():
    """Modeled uint256 env setters become bounded PUT fuzz parameters."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.basefee": (1, 3),
                "block.prevrandao": (4, 6),
                "tx.gasprice": (7, 9)},
        holes={"block.prevrandao": [5]}, pins={}, params=PARAMS,
        emitted=em, case=case, layout=LAYOUT, ladder_rows=[
            ("byFee[block.basefee]", "post == pre", "HOLDS"),
            ("byRand[block.prevrandao]", "post == pre", "HOLDS"),
            ("byGas[tx.gasprice]", "post == pre", "HOLDS"),
        ], notes=notes,
        maps={"byFee": (7, "uint256", 32, 0, "byFee", None),
              "byRand": (8, "uint256", 32, 0, "byRand", None),
              "byGas": (9, "uint256", 32, 0, "byGas", None)})
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"modeled numeric env ranges emit: {notes}")
    bad += check("uint256 p_block_basefee" in text
                 and "p_block_basefee = bound(p_block_basefee, 1, 3);" in text
                 and "    vm.fee(p_block_basefee);" in text,
                 "block.basefee is established by vm.fee")
    bad += check("uint256 p_block_prevrandao" in text
                 and "p_block_prevrandao = bound(p_block_prevrandao, 4, 6);"
                 in text
                 and "vm.assume(p_block_prevrandao != 5);" in text
                 and "    vm.prevrandao(uint256(p_block_prevrandao));" in text,
                 "block.prevrandao is established with holes")
    bad += check("uint256 p_tx_gasprice" in text
                 and "p_tx_gasprice = bound(p_tx_gasprice, 7, 9);" in text
                 and "    vm.txGasPrice(p_tx_gasprice);" in text,
                 "tx.gasprice is established by vm.txGasPrice")
    bad += check(stats["fuzz_params"] == 5
                 and {"block.basefee", "block.prevrandao",
                      "tx.gasprice"} <= set(stats["lifted"]),
                 f"numeric env ranges are counted as fuzzed: {stats}")
    bad += check("keccak256(abi.encode(p_block_basefee, uint256(7)))" in text
                 and "keccak256(abi.encode(p_block_prevrandao, uint256(8)))"
                 in text
                 and "keccak256(abi.encode(p_tx_gasprice, uint256(9)))"
                 in text,
                 "modeled numeric env coordinates key storage oracles")
    return bad


def test_block_coinbase_range_is_fuzzed_with_coinbase():
    """The address-typed coinbase env setter uses an address fuzz parameter."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1),
                "block.coinbase": (10, 20)},
        holes={"block.coinbase": [13]}, pins={}, params=PARAMS,
        emitted=em, case=case, layout=LAYOUT, ladder_rows=[
            ("byMiner[block.coinbase]", "post == pre", "HOLDS"),
        ], notes=notes,
        maps={"byMiner": (9, "address", 32, 0, "byMiner", None)})
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"coinbase range emits instead of refusing: {notes}")
    bad += check("address p_block_coinbase" in text,
                 "wide block.coinbase is an address PUT parameter")
    bad += check(
        "p_block_coinbase = address(uint160(bound(uint256(uint160("
        "p_block_coinbase)), 10, 20)));" in text,
        "wide block.coinbase is bounded over the address domain")
    bad += check("vm.assume(uint256(uint160(p_block_coinbase)) != 13);" in text,
                 "block.coinbase holes are preserved")
    bad += check("    vm.coinbase(p_block_coinbase);" in text,
                 "wide block.coinbase drives vm.coinbase")
    bad += check(stats["fuzz_params"] == 3
                 and "block.coinbase" in stats["lifted"],
                 f"block.coinbase range is counted as fuzzed: {stats}")
    bad += check("keccak256(abi.encode(p_block_coinbase, uint256(9)))"
                 in text,
        "block.coinbase keys storage oracles with the established address")
    if bad:
        print(text)
    return bad


def test_esbmc_arg_passthrough_admits_unwindset_and_refuses_strategies():
    """`--unwindset` through, every strategy flag stopped.

    Both directions, because a passthrough that refuses everything is as useless
    as one that refuses nothing. `--unwindset` is the one the ladder's own
    UNDECIDED-TRUNCATED refusal names, and it moves only the symex side -- a
    SUPERSET of executions, so it cannot make a path look infeasible that is not.
    The strategy flags change which claims exist AND the bound they are answered
    under, and the only measurement of that ran on a snapshot binary this note
    itself marks unverified on newer builds.
    """
    bad = 0
    bad += check(check_esbmc_args(["--unwindset", "64:512"]) is None,
                 "--unwindset passes through")
    bad += check(check_esbmc_args(["--partial-loops"]) is None,
                 "--partial-loops passes through (the third repair the tool "
                 "names)")
    bad += check(check_esbmc_args([]) is None, "no extra args is fine")
    for flag in ("--k-induction", "--incremental-bmc", "--inductive-step",
                 "--loop-invariant", "--falsification", "--termination",
                 "--forward-condition", "--k-induction-parallel"):
        r = check_esbmc_args(["--unwindset", "64:512", flag])
        bad += check(r is not None and flag in r,
                     f"{flag} is refused even beside a legitimate flag")
    return bad


def test_foundry_fixture_loading_keeps_esbmc_fixture_as_fallback():
    bad = 0
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        fixture_path = fh.name
        json.dump({"contract": "DCF", "skip_constructor": True}, fh)
    try:
        want = {"contract": "DCF", "skip_constructor": True}
        bad += check(load_fixture_json(fixture_path) == want,
                     "direct foundry fixture JSON loads")
        bad += check(fixture_from_esbmc_args([
            "--unwindset",
            "64:512",
            "--path-cov-fixture",
            fixture_path,
        ]) == want,
                     "legacy ESBMC fixture still feeds Foundry assembly")
        bad += check(fixture_from_esbmc_args(["--path-cov-fixture"]) is None,
                     "missing fixture value is ignored")
        bad += check(load_fixture_json(
            "/tmp/does-not-exist-veriput-fixture") is None,
                     "missing foundry fixture is ignored")
    finally:
        os.unlink(fixture_path)
    return bad


def test_the_cell_is_named_and_an_unsettled_one_says_so():
    """Which of the two settled command lines a run is, or neither.

    INVOCATION_DECISIONS.md forbids quoting a run of one cell into the other's
    table. The enforcement this driver can offer is that the artefact NAMES its
    cell -- and that a configuration matching neither is called UNNAMED rather
    than being silently filed under whichever is closer, which is how a
    focused tx=2 run would end up quoted as the method's reach.
    """
    bad = 0
    n, r = cell_of("whole", 2)
    bad += check(n == "ARTEFACT" and "gate table" in r,
                 f"whole/tx=2 is the ARTEFACT cell, with its rule: {n}")
    n, r = cell_of("focus", 1)
    bad += check(n == "GATE" and "reach" in r,
                 f"focus/tx=1 is the GATE cell, with its rule: {n}")
    for scope, tx in (("focus", 2), ("whole", 1), ("whole", 3)):
        n, r = cell_of(scope, tx)
        bad += check(n == "UNNAMED" and "belongs to no table" in r,
                     f"{scope}/tx={tx} is UNNAMED, not filed under a neighbour")
    return bad


def test_a_widened_ladder_says_which_half_it_applies_to():
    """The oracle and the body come from two runs at different symex bounds.

    The emit run supplies the preamble and the concrete case and happens BEFORE
    any loop has been named, so it never carries the widening. Recording the
    widened flags as "the run's configuration" -- which the first version did --
    claims both runs used them, and that is a false provenance on the one
    artefact whose whole value is that its provenance is checkable.
    """
    em, case = make_case()
    notes = []
    put, _stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes, cell=cell_of("focus", 1),
        unwind=["--unwindset", "64:512"])
    text = "\n".join(put or [])
    bad = check("LADDER WIDENED: --unwindset 64:512" in text,
                "the widening is named on the test")
    bad += check("ASSERTION LADDER run only" in text,
                 "and it says which half of the test it applies to")
    put_partial, _ = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=[], cell=cell_of("focus", 1),
        unwind=["--partial-loops"])
    bad += check("LADDER WIDENED: --partial-loops"
                 in "\n".join(put_partial or []),
                 "a partial-loop ladder repair is named on the test too")
    # MUST NOT FIRE: an un-widened run must not grow the disclaimer, or every
    # PUT would carry a caveat about something that did not happen.
    put2, _ = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=[], cell=cell_of("focus", 1), unwind=[])
    bad += check("LADDER WIDENED" not in "\n".join(put2 or []),
                 "an un-widened run carries no such line")
    return bad


def test_the_emitted_test_carries_its_cell():
    """A PUT that does not say which cell produced it is quotable into both."""
    em, case = make_case()
    notes = []
    put, _stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case, layout=LAYOUT,
        ladder_rows=LADDER, notes=notes, cell=cell_of("focus", 1))
    text = "\n".join(put or [])
    return check("// CELL GATE --" in text,
                 "the cell is written onto the emitted test")


def test_both_truncation_shapes_are_read():
    """The tool NAMES the loop it cut, in two different sentences.

    This is the mechanism INVOCATION_DECISIONS.md records as missing ("we still
    have no mechanism for knowing how many unwinds are needed"). It was not
    missing from the tool, only from every consumer.
    """
    bad = 0
    l1, s1 = truncated_loops(TRUNC_SHAPE_1)
    bad += check([x[0] for x in sorted(l1)] == [1, 62, 64],
                 f"shape 1 (assert gate, one line): {[x[0] for x in sorted(l1)]}")
    bad += check(s1["assert-gate"] == 3 and s1["under-report-warning"] == 0,
                 f"shape 1 is attributed to its own branch: {s1}")
    bad += check(any(fn == "__memset_impl" for _i, _f, _l, fn in l1),
                 "the library loop the measured fix widened is named")

    l2, s2 = truncated_loops(TRUNC_SHAPE_2)
    bad += check([x[0] for x in sorted(l2)] == [55, 56],
                 f"shape 2 (under-report warning, one per line): "
                 f"{[x[0] for x in sorted(l2)]}")
    bad += check(s2["under-report-warning"] == 2 and s2["assert-gate"] == 0,
                 f"shape 2 is attributed to its own branch: {s2}")

    # MUST NOT FIRE. A log with no truncation report must yield nothing, or the
    # ladder would widen loops nobody cut and every run would look truncated.
    l3, s3 = truncated_loops(
        "--path-cov-assert: bal: post == pre  HOLDS\n"
        "some prose that mentions a loop 99 at file x line 1 function f\n")
    bad += check(l3 == [] and sum(s3.values()) == 0,
                 f"prose outside either report is NOT read as a truncation: "
                 f"{l3}")
    return bad


def test_the_ladder_widens_every_named_loop():
    """ONE --unwindset naming every loop, and only the symex side.

    ---- WHAT THIS TEST USED TO PIN, AND WHY THAT WAS BACKWARDS ----

    It required one `--unwindset` FLAG PER LOOP:

        ["--unwindset", "1:512", "--unwindset", "62:512", "--unwindset",
         "64:512"]

    That command line does not run. MEASURED, on the real binary:

        ERROR: option '--unwindset' cannot be specified more than once

    in 0.0s, before any analysis. So this test was green while standing behind
    a command line esbmc rejects outright, and the whole auto-unwind path had
    never once executed a widened query -- it printed its progress line, got
    exit 64, and moved on. A pure-function test that pins the SHAPE of an
    argument list can only be as good as one real invocation of it, and there
    had not been one.

    The accepted form is a single flag with a comma-separated list, verified on
    aqua `dock`: `--unwindset 1:8,62:8,64:8` makes all three loops unwind to
    iteration 7 and report "Not unwinding ... iteration 8".
    """
    loops, _ = truncated_loops(TRUNC_SHAPE_1)
    got = unwindset_args(loops, 512)
    bad = check(got == ["--unwindset", "1:512,62:512,64:512"],
                f"ONE --unwindset naming every loop: {got}")
    bad += check(got.count("--unwindset") == 1,
                 "the flag appears exactly once -- esbmc rejects a repeat "
                 "outright, which is what made every previous attempt a no-op")
    bad += check(all(g != "--unwind" for g in got),
                 "the ENUMERATION bound (--unwind) is never touched: widening "
                 "it would change the goal set, i.e. what is being measured")
    # The two reports spell the same loop's function differently (`dock` and
    # `dock;`), so the union carries duplicates. A duplicated id inside the
    # list is not rejected by esbmc, but it is still two answers to one
    # question written into one argument.
    dup = [(62, "notes/x.sol", 2258, "dock"),
           (62, "notes/x.sol", 2258, "dock;"),
           (64, "/lib/string.c", 298, "__memset_impl")]
    bad += check(unwindset_args(dup, 8) == ["--unwindset", "62:8,64:8"],
                 "a loop named twice under two function spellings is widened "
                 "ONCE")
    # MUST NOT FIRE: no named loop means no flag at all, not `--unwindset ""`.
    bad += check(unwindset_args([], 8) == [],
                 "no named loop produces no flag, not an empty argument")
    return bad


def test_a_retry_that_produced_no_ladder_is_not_adopted():
    """A crashed widening attempt must not overwrite the verdict it was
    trying to lift.

    THIS IS THE BRANCH THAT HAD NEVER FIRED, and its absence is what turned a
    correct refusal into a green run. MEASURED, on aqua `dock` with
    --auto-unwind 3 before the fix:

      * the attempt died on the command line (exit 64, 0.0s);
      * `parse_ladder` of an error message returns rows=[] blocker=None;
      * that None replaced blocker="truncated";
      * main()'s UNDECIDED-TRUNCATED gate therefore did not fire;
      * the driver emitted an oracle-free PUT and exited 0.

    Every one of those steps is individually reasonable, which is why the
    predicate is named and tested rather than left inline. BOTH directions are
    required: a predicate that always says "unusable" would freeze the ladder
    at its first verdict and look exactly like this one from outside.
    """
    bad = 0
    # UNUSABLE: the shape a rejected command line produces.
    bad += check(attempt_is_usable([], None) is False,
                 "no rows and no RESULT token is NOT a measurement")
    # USABLE: a run that reached a verdict.
    bad += check(attempt_is_usable([("bal", "post == pre", "HOLDS")], None),
                 "a run that produced candidate rows IS adopted")
    # USABLE: a run that still truncated -- that IS an answer, and adopting it
    # is what lets the next attempt double k.
    bad += check(attempt_is_usable([], "truncated"),
                 "a run that answered UNDECIDED-TRUNCATED again IS adopted, "
                 "so the ladder can widen further rather than stopping")
    bad += check(attempt_is_usable([], "vacuous"),
                 "a run that answered VACUOUS IS adopted")
    return bad


def test_region_coordinate_ladder_refusal_is_read():
    """The assert gate can refuse before printing any row, and that is fatal.

    VERBATIM SHAPE from aqua rawBalances: this used to miss
    LADDER_REFUSAL_RE because the producer wrote
    `REFUSING THE LADDER on region coordinate ...` rather than a colon/comma
    immediately after LADDER. The parser then returned rows=[] and
    refusal=None, which let main() emit an oracle-free PUT.
    """
    log = (
        "ERROR: --path-cov-assert: unit "
        "'sol:@C@Aqua@F@rawBalances#2819' -- REFUSING THE LADDER on region "
        "coordinate "
        "'state._balances[maker][app][0x2000000000000000000000000000000000000000000000000000000000000000][token].tokensCount': "
        "the hi value UINT256_MAX does not fit the coordinate's own type "
        "(admissible range [0, 255])."
    )
    rows, summary, refusal, blocker = parse_ladder(log)
    bad = 0
    bad += check(rows == [] and summary is None and blocker is None,
                 "a pre-ladder refusal has no rows, summary or blocker")
    bad += check(refusal is not None and "tokensCount" in refusal
                 and "admissible range [0, 255]" in refusal,
                 "the region-coordinate refusal is still read")
    bad += check(attempt_is_usable(rows, blocker) is False,
                 "without rows or a RESULT token it is not a widening answer")
    return bad


def test_partial_ladder_rows_are_used_only_when_final_table_is_missing():
    """A crash after a candidate verdict must not erase the verdict.

    ESBMC prints PARTIAL ROW lines as soon as individual assertion candidates
    are decided.  They are a salvage channel for bad_alloc / signal exits before
    report_path_cov_assertions() prints the final table, not a replacement for
    that table when the run completes.
    """
    partial_only = (
        "--path-cov-assert: PARTIAL ROW before final table: bal: post == pre  "
        "HOLDS\n"
        "terminate called after throwing an instance of 'std::bad_alloc'\n"
    )
    rows, summary, refusal, blocker = parse_ladder(partial_only)
    bad = 0
    bad += check(rows == [("bal", "post == pre", "HOLDS")],
                 f"partial row survives a missing final table: {rows}")
    bad += check(summary is None and refusal is None and blocker is None,
                 "a salvaged row is not a vacuity/refusal gate")

    final_table = (
        "--path-cov-assert: PARTIAL ROW before final table: bal: post == pre  "
        "HOLDS\n"
        "--path-cov-assert: bal: post == pre  REFUTED\n"
        "--path-cov-assert: ladder summary -- 1 candidate(s): 0 HOLDS, 1 "
        "REFUTED, 0 no verdict (solver unknown), 0 no verdict (never reached "
        "the solver)\n"
    )
    rows, summary, refusal, blocker = parse_ladder(final_table)
    bad += check(rows == [("bal", "post == pre", "REFUTED")],
                 f"final rows override partial salvage rows: {rows}")
    bad += check(summary == (1, 0, 1, 0, 0) and refusal is None
                 and blocker is None,
                 f"the final summary is still parsed: {summary}")
    return bad


# ---------------------------------------------------------------------------
# The unit's OWN RETURN VALUE as an oracle
# ---------------------------------------------------------------------------
#
# WHAT WOULD LOOK IDENTICAL IF THIS WERE BROKEN, which is what each case below
# is built to separate:
#
#   * the rungs never rendered at all  -> no assertion, and a PUT that looks
#     exactly like one whose unit returns nothing. Cases 1/3/6 fail on absence.
#   * the rungs rendered UNCONDITIONALLY -> an assertion certified about a
#     value the execution never produced, because every return rung carries
#     `|| !retset` and can hold for want of a returned value. Case 2 is the
#     must-flip: the SAME rows with `retlive` HOLDS instead of REFUTED must
#     produce nothing, and must say why.
#   * the binding spliced into a `try`/`catch` or an expectRevert call -> the
#     R0 exit-kind expectation, which this whole lifting route exists to
#     preserve, replaced by a different statement. Cases 5 and 7.

RETLIVE = "a value IS returned on this path (REFUTED == yes)"


def _ret_put(ladder_rows, rettypes, layout=None, maps=None, r2_terms=None,
             pins=None, region=None):
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region=(region or {"bps": (0, 250), "u": (0, (1 << 160) - 1)}),
        holes={}, pins=(pins or {}), params=PARAMS, emitted=em, case=case,
        layout=LAYOUT if layout is None else layout,
        ladder_rows=ladder_rows, notes=notes, rettypes=rettypes,
        maps=maps, r2_terms=r2_terms)
    return "\n".join(put or []), stats, notes


def test_return_rung_is_bound_and_asserted():
    """A HOLDS return rung, with `retlive` REFUTED: BIND and ASSERT."""
    text, stats, _n = _ret_put(
        LADDER + [("return", RETLIVE, "REFUTED"),
                  ("return", "return != 0", "HOLDS")],
        [("", "uint256")])
    bad = 0
    bad += check("uint256 _put_ret = c0.setDiscount(" in text,
                 "the call is bound to a typed local")
    bad += check("assertTrue(uint256(_put_ret) != 0" in text,
                 "the HOLDS rung becomes an assertion")
    bad += check(stats["return_asserts"] == 1 and stats["state_asserts"] == 2,
                 f"the two oracle sources are counted apart: {stats['asserts']} "
                 f"= {stats['state_asserts']} state + "
                 f"{stats['return_asserts']} return")
    bad += check("over the unit's OWN RETURN" in text,
                 "the header says the oracle has a return-value half")
    return bad


def test_return_rung_can_assert_a_scalar_entry_state_coord():
    """Getter ground truth: a certified `return == state.x` rung is renderable.

    The state coordinate is READ before the unit call; it is not lifted into the
    fuzz signature and not havoc'd. That keeps this as an oracle over the
    entry state ESBMC certified, rather than an unsupported state fuzz input.
    """
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return == state._distributor", "HOLDS")],
        [("", "address")],
        layout={"_distributor": (1, 0, 20)},
        r2_terms={
            "state._distributor": {
                "kind": "coord",
                "name": "state._distributor",
            },
        })
    bad = 0
    bad += check("uint256 _ret_pre_distributor = (uint256(vm.load("
                 in text,
                 "the entry-state storage coordinate is read before the call")
    bad += check("address _put_ret = c0.setDiscount(" in text,
                 "the return value is still bound from the call")
    bad += check("assertEq(uint256(uint160(_put_ret)), "
                 "_ret_pre_distributor" in text,
                 "the HOLDS rung compares the return with the entry-state read")
    bad += check(not any("state._distributor" in s
                         for s in stats["oracle_skipped"]),
                 f"the structured state coord was not dropped: "
                 f"{stats['oracle_skipped']}")
    bad += check(stats["return_asserts"] == 1,
                 f"one return assertion: {stats['return_asserts']}")
    return bad


def test_return_rung_can_assert_a_pinned_nonlayout_state_coord():
    """Immutable-like state pins can still be semantic return R2 endpoints."""
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return == state.baseRate", "HOLDS")],
        [("", "uint256")],
        layout={},
        pins={"state.baseRate": 7},
        r2_terms={
            "state.baseRate": {
                "kind": "coord",
                "name": "state.baseRate",
            },
        })
    bad = 0
    bad += check("_ret_pre_baseRate" not in text,
                 "a non-layout pin is not read from storage")
    bad += check(
        'assertEq(uint256(_put_ret), 7, "return: return == state.baseRate")'
        in text,
        "the pinned point value renders as the certified R2 endpoint")
    bad += check("return: return == state.baseRate (rung shape not renderable"
                 not in text,
                 "the return R2 rung is not reported as dropped")
    bad += check(stats["return_asserts"] == 1
                 and "R2" in stats["oracle_classes"],
                 f"the pinned endpoint is counted as an emitted R2: {stats}")
    return bad


def test_return_rung_can_assert_a_mapping_entry_state_coord():
    """Getter ground truth for `return == state.bal[u]` style slots."""
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return == state.bal[u]", "HOLDS")],
        [("", "uint256")],
        maps={"bal": (7, "address", 32, 0, "bal", None)},
        r2_terms={
            "state.bal[u]": {
                "kind": "coord",
                "name": "state.bal[u]",
            },
        })
    bad = 0
    bad += check("uint256 _ret_pre_bal_u = uint256(vm.load("
                 in text,
                 "the mapping slot is read before the call")
    bad += check("keccak256(abi.encode(u, uint256(7)))" in text,
                 "the pre-read uses the same mapping hash as state oracles")
    bad += check("assertEq(uint256(_put_ret), _ret_pre_bal_u" in text,
                 "the return is compared with the pre-read slot value")
    bad += check(not stats["oracle_skipped"],
                 f"the mapping return coordinate is not skipped: "
                 f"{stats['oracle_skipped']}")
    bad += check(stats["return_asserts"] == 1,
                 f"one return assertion: {stats['return_asserts']}")
    return bad


def test_a_retlive_that_HOLDS_kills_every_return_rung():
    """THE MUST-FLIP. Same rows, `retlive` HOLDS: nothing may be emitted.

    `retlive` asserts `!retset`, so HOLDS means NO execution of this path was
    shown to reach a return -- and every other return rung, which all carry
    `|| !retset`, is then holding for want of a returned value. Rendering one
    would assert something about a value that was never produced. The two
    inputs differ in ONE token, so a renderer that ignored the witness would
    pass the case above and fail here.
    """
    text, stats, _n = _ret_put(
        LADDER + [("return", RETLIVE, "HOLDS"),
                  ("return", "return != 0", "HOLDS")],
        [("", "uint256")])
    bad = 0
    bad += check("_put_ret" not in text,
                 "no binding is emitted")
    bad += check(stats["return_asserts"] == 0,
                 f"no return assertion: {stats['return_asserts']}")
    bad += check(any("retlive" in s and "VACUOUSLY" in s
                     for s in stats["oracle_skipped"]),
                 f"and the drop NAMES the witness: {stats['oracle_skipped']}")
    return bad


def test_a_bool_return_uses_assertTrue_not_a_cast():
    """`uint256(<bool>)` is not a Solidity conversion; the rung shape differs."""
    text, stats, _n = _ret_put(
        LADDER + [("return", RETLIVE, "REFUTED"),
                  ("return", "return == true", "HOLDS")],
        [("", "bool")])
    bad = 0
    bad += check("bool _put_ret = c0.setDiscount(" in text,
                 "the local is declared bool")
    bad += check("assertTrue(_put_ret," in text and "uint256(_put_ret)"
                 not in text,
                 "asserted directly, with no uint256 cast")
    bad += check(stats["return_asserts"] == 1,
                 f"one return assertion: {stats['return_asserts']}")
    return bad


def test_a_bool_return_can_assert_a_structured_bool_coord():
    from solidity_path_put import return_rung_assertions  # noqa: E402

    coord = return_rung_assertions(
        "return == ok", ("bool", None), "_put_ret",
        "return: return == ok", {"ok": "p_ok"},
        {"ok": {"kind": "coord", "name": "ok"}})
    literal = return_rung_assertions(
        "return == 1", ("bool", None), "_put_ret",
        "return: return == 1", {}, {})
    bad_literal = return_rung_assertions(
        "return == 2", ("bool", None), "_put_ret",
        "return: return == 2", {}, {})
    neq_zero = return_rung_assertions(
        "return != 0", ("bool", None), "_put_ret",
        "return: return != 0", {}, {})
    neq_one = return_rung_assertions(
        "return != 1", ("bool", None), "_put_ret",
        "return: return != 1", {}, {})
    bad_neq = return_rung_assertions(
        "return != 2", ("bool", None), "_put_ret",
        "return: return != 2", {}, {})
    bad = 0
    bad += check(coord == [
        '    assertEq(_put_ret, p_ok, "return: return == ok");'
    ], f"a bool return can compare against a certified bool coord: {coord}")
    bad += check(literal == [
        '    assertTrue(_put_ret, "return: return == 1");'
    ], f"a numeric bool literal is rendered as bool without R2 terms: {literal}")
    bad += check(bad_literal is None,
                 f"out-of-domain bool literal is still refused: {bad_literal}")
    bad += check(neq_zero == [
        '    assertTrue(_put_ret, "return: return != 0");'
    ], f"bool return != 0 is assertTrue: {neq_zero}")
    bad += check(neq_one == [
        '    assertFalse(_put_ret, "return: return != 1");'
    ], f"bool return != 1 is assertFalse: {neq_one}")
    bad += check(bad_neq is None,
                 f"out-of-domain bool inequality is still refused: {bad_neq}")
    return bad


def test_a_bool_literal_return_equality_reaches_the_put():
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return == 1", "HOLDS")],
        [("", "bool")])
    bad = 0
    bad += check('assertTrue(_put_ret, "return: return == 1");' in text,
                 "the bool literal equality is asserted")
    bad += check(stats["return_asserts"] == 1,
                 f"one return assertion: {stats['return_asserts']}")
    bad += check(not stats["oracle_skipped"],
                 f"the bool literal return row is not skipped: "
                 f"{stats['oracle_skipped']}")
    return bad


def test_fixed_bytes_return_casts_through_the_matching_uint_width():
    from solidity_path_put import return_kind, return_rung_assertions  # noqa: E402

    b4 = return_rung_assertions(
        "return == 7", return_kind("bytes4"), "_put_ret",
        "return: return == 7", {},
        {"7": {"kind": "literal", "value": "7"}})
    b32 = return_rung_assertions(
        "return == 7", return_kind("bytes32"), "_put_ret",
        "return: return == 7", {},
        {"7": {"kind": "literal", "value": "7"}})
    bad = 0
    bad += check(b4 == [
        '    assertEq(uint256(uint32(_put_ret)), 7, "return: return == 7");'
    ], f"bytes4 return casts through uint32 before uint256: {b4}")
    bad += check(b32 == [
        '    assertEq(uint256(_put_ret), 7, "return: return == 7");'
    ], f"bytes32 return keeps the existing same-width cast: {b32}")
    return bad


def test_a_whole_value_rung_on_a_tuple_unit_is_refused():
    """A WHOLE-value rung against a two-member declaration: refuse.

    That combination means the table and the AST disagree about the shape, and
    binding it either way would pick an arity nobody stated.
    """
    text, stats, _n = _ret_put(
        LADDER + [("return", RETLIVE, "REFUTED"),
                  ("return", "return != 0", "HOLDS")],
        [("", "uint248"), ("", "uint8")])
    bad = 0
    bad += check("_put_ret" not in text, "nothing is bound")
    bad += check(any("2 return value(s)" in s
                     for s in stats["oracle_skipped"]),
                 f"the drop names the count: {stats['oracle_skipped']}")
    return bad


def test_per_member_rungs_destructure_in_declaration_order():
    """`return.0` / `return.1` bind through a destructuring pattern.

    ORDER IS THE PROPERTY. Member 0 is declared `uint248` and member 1 `uint8`,
    and the two rungs assert DIFFERENT bounds, so a plan that swapped them
    would produce `uint8 _put_ret0` and an assertion of 2 against the member
    that holds 22 -- which is why the types and the bounds are both checked
    against their own index rather than merely counted.
    """
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return.0", "return in [22, 22]", "HOLDS"),
         ("return.1", "return in [2, 2]", "HOLDS")],
        [("", "uint248"), ("", "uint8")])
    bad = 0
    bad += check("(uint248 _put_ret0, uint8 _put_ret1) = c0.setDiscount("
                 in text,
                 "a destructuring pattern in declaration order is emitted")
    bad += check("assertGe(uint256(_put_ret0), 22" in text
                 and "assertLe(uint256(_put_ret0), 22" in text,
                 "member 0's bound is asserted on member 0")
    bad += check("assertGe(uint256(_put_ret1), 2" in text
                 and "assertLe(uint256(_put_ret1), 2" in text,
                 "member 1's bound is asserted on member 1")
    bad += check(stats["return_asserts"] == 4,
                 f"two interval rungs = four assertions: "
                 f"{stats['return_asserts']}")
    return bad


def test_a_member_with_no_rung_gets_an_EMPTY_slot():
    """Only the members that carry a HOLDS rung are named.

    An unused named local is a solc warning on every emitted test; an empty
    slot is a legal destructuring and says exactly what happened -- this
    member was not asserted on.
    """
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return.1", "return != 0", "HOLDS")],
        [("", "uint248"), ("", "uint8")])
    bad = 0
    bad += check("(, uint8 _put_ret1) = c0.setDiscount(" in text,
                 f"member 0 is an empty slot")
    bad += check("_put_ret0" not in text, "and gets no local at all")
    bad += check(stats["return_asserts"] == 1,
                 f"one assertion: {stats['return_asserts']}")
    return bad


def test_a_member_index_beyond_the_declaration_refuses():
    """`return.2` on a two-member unit: the table and the AST disagree."""
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return.2", "return != 0", "HOLDS")],
        [("", "uint248"), ("", "uint8")])
    bad = 0
    bad += check("_put_ret" not in text, "nothing is bound")
    bad += check(any("member 2" in s and "only 2 return value(s)" in s
                     for s in stats["oracle_skipped"]),
                 f"the drop names both numbers: {stats['oracle_skipped']}")
    return bad


def test_an_unbindable_member_does_not_cost_the_others():
    """One bad member is dropped BY NAME; the rest are still asserted."""
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return.0", "return != 0", "HOLDS"),
         ("return.1", "return != 0", "HOLDS")],
        [("", "uint256"), ("", "string memory")])
    bad = 0
    bad += check("(uint256 _put_ret0, ) = c0.setDiscount(" in text,
                 "the good member is bound and the bad one is an empty slot")
    bad += check(stats["return_asserts"] == 1,
                 f"only the good member is asserted: "
                 f"{stats['return_asserts']}")
    bad += check(any("return.1" in s and "string memory" in s
                     for s in stats["oracle_skipped"]),
                 f"the bad member is named: {stats['oracle_skipped']}")
    # MUST NOT FIRE. A member row is not a state variable, and filing it as one
    # ("no storage slot ... constant/immutable") is a wrong LABEL on a right
    # value -- the same class of defect as publishing a tuple member as
    # `state_written_value_unavailable`. Found by reading this very list.
    bad += check(not any("no storage slot" in s
                         for s in stats["oracle_skipped"]),
                 f"no member row is filed as a missing state variable: "
                 f"{stats['oracle_skipped']}")
    return bad


def test_a_retlive_that_HOLDS_kills_member_rungs_too():
    """THE MUST-FLIP, on the tuple side. One token, and nothing is emitted."""
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "HOLDS"),
         ("return.0", "return != 0", "HOLDS"),
         ("return.1", "return != 0", "HOLDS")],
        [("", "uint248"), ("", "uint8")])
    bad = 0
    bad += check("_put_ret" not in text, "no binding is emitted")
    bad += check(stats["return_asserts"] == 0,
                 f"no return assertion: {stats['return_asserts']}")
    bad += check(any("retlive" in s and "VACUOUSLY" in s
                     for s in stats["oracle_skipped"]),
                 f"and the drop NAMES the witness: {stats['oracle_skipped']}")
    return bad


def test_an_unbindable_return_type_is_reported():
    """A type this emitter cannot cast is DROPPED by name, never guessed."""
    _t, stats, _n = _ret_put(
        LADDER + [("return", RETLIVE, "REFUTED"),
                  ("return", "return != 0", "HOLDS")],
        [("", "string memory")])
    return check(any("string memory" in s for s in stats["oracle_skipped"]),
                 f"the drop names the type: {stats['oracle_skipped']}")


def test_bind_return_refuses_the_exit_kind_shapes():
    """`try`/`catch` and an already-bound call are the two shapes that must
    survive untouched.

    Tested on `bind_return` directly rather than through a captured emitted
    file: the property is the guard, and the fixture above has only the bare
    shape. Constructed input, and labelled as such.
    """
    from solidity_path_put import bind_return  # noqa: E402
    bad = 0
    ln, why = bind_return("    try c0.f(1) { } catch { }", "f", "uint256", "_r")
    bad += check(ln is None and "try" in (why or ""),
                 f"a try/catch statement is refused: {why}")
    ln, why = bind_return("    uint256 z = c0.f(1);", "f", "uint256", "_r")
    bad += check(ln is None and "already binds" in (why or ""),
                 f"an already-bound call is refused: {why}")
    ln, why = bind_return("    c0.f(1);", "f", "uint256", "_r")
    bad += check(ln == "    uint256 _r = c0.f(1);",
                 f"a bare asserted call IS bound: {ln}")
    return bad


def test_a_return_only_oracle_still_reaches_the_test():
    """No state rung at all: the return assertions must still be written.

    This is the guard that used to read `if post_reads:` -- equivalent only
    while every assertion came from a state rung. A unit whose only surviving
    rung is a return rung has no post-read, and the header would have
    announced assertions the body did not contain.
    """
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return in [1, 9]", "HOLDS")],
        [("", "uint256")])
    bad = 0
    bad += check(stats["state_asserts"] == 0,
                 "no state rung was supplied")
    bad += check(stats["return_asserts"] == 2,
                 f"the interval rung is two assertions: "
                 f"{stats['return_asserts']}")
    bad += check("assertGe(uint256(_put_ret), 1" in text
                 and "assertLe(uint256(_put_ret), 9" in text,
                 "and both are in the emitted body")
    return bad


def test_a_nonzero_literal_return_equality_is_asserted():
    """A plain ladder row `return == 7` needs no structured term table.

    State rungs already accepted decimal equality directly; return rungs only
    special-cased zero and otherwise required an R2 term entry. That dropped a
    common getter/pure-function oracle even though the solver had already
    proved the literal equality over the region.
    """
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return == 7", "HOLDS")],
        [("", "uint256")])
    bad = 0
    bad += check("assertEq(uint256(_put_ret), 7," in text,
                 "the nonzero literal equality is asserted")
    bad += check(stats["return_asserts"] == 1,
                 f"one return assertion: {stats['return_asserts']}")
    bad += check(not stats["oracle_skipped"],
                 f"the literal return row is not skipped: "
                 f"{stats['oracle_skipped']}")
    return bad


def test_a_nonzero_literal_return_inequality_is_asserted():
    """A proved `return != N` is weaker than equality but still an oracle."""
    text, stats, _n = _ret_put(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return != 7", "HOLDS")],
        [("", "uint256")])
    bad = 0
    bad += check("assertTrue(uint256(_put_ret) != 7," in text,
                 "the nonzero literal inequality is asserted")
    bad += check(stats["return_asserts"] == 1,
                 f"one return assertion: {stats['return_asserts']}")
    bad += check(not stats["oracle_skipped"],
                 f"the literal return inequality is not skipped: "
                 f"{stats['oracle_skipped']}")
    return bad


def test_a_refuted_return_rung_is_never_asserted():
    """REFUTED is the ladder working, not an oracle."""
    text, stats, _n = _ret_put(
        LADDER + [("return", RETLIVE, "REFUTED"),
                  ("return", "return == 0", "REFUTED"),
                  ("return", "return != 0", "HOLDS")],
        [("", "uint256")])
    bad = 0
    bad += check("assertEq(uint256(_put_ret), 0" not in text,
                 "the REFUTED rung produces no assertion")
    bad += check(stats["return_asserts"] == 1,
                 f"only the HOLDS one survives: {stats['return_asserts']}")
    return bad


# --- a mapping-slot KEY must be an expression the TEST can evaluate ---------
#
# `bal` is declared `mapping(address => uint256)` at slot 2. Only the key
# changes across the three cases below.
# THE SIX-TUPLE IS `storage_layout`'s OWN SHAPE, not a convenience for this
# test: (base slot, key type, value bytes, value BIT-OFFSET in the word, the
# mapping's label, the member's label or None).
#
# It was a 3-tuple here and a 6-tuple in `build_put`, so `_slot_pin_put` raised
# `ValueError: not enough values to unpack` -- and because this module runs its
# tests as a plain sequence, that traceback ABORTED the run. Every test after
# this one had silently not executed since the widening landed. A suite that
# stops early and a suite that passes are the same green from outside, which is
# why the count is checked at the bottom of this file rather than trusted.
SLOT_MAPS = {"bal": (2, "address", 32, 0, "bal", None),
             # A PACKED STRUCT FIELD, offset 31, which is aqua's own shape
             # (`Balance{uint248 amount; uint8 tokensCount}`). Its row exists
             # so the read-modify-write path is exercised by something other
             # than offset 0, where a dropped `voff` is invisible.
             "pack.tag": (3, "address", 1, 31, "pack", "tag"),
             # A TWO-LEVEL store. Its key element is a TUPLE, which is how the
             # layout reader records depth; one level stays a bare string so
             # every reader that only ever saw a string is unchanged.
             "two": (5, ("address", "address"), 32, 0, "two", None)}


def test_certified_region_mapping_slots_are_ASKED_before_guesses():
    """Aqua safeBalances' ground truth: the certified region already names the
    source slots, including the literal bytes32 key produced by Stage 2.

    The PUT side must reuse those coordinates instead of regenerating a
    same-typed cross product containing `strategyHash`, which is a different
    slot and not the certified region.
    """
    lit = "0x2000000000000000000000000000000000000000000000000000000000000000"
    region = {
        "maker": (0, (1 << 160) - 1),
        f"state._balances[maker][app][{lit}][token0].amount": (0, 0),
        f"state._balances[maker][app][{lit}][token0].tokensCount": (0, 0),
    }
    maps = {
        "_balances.amount": (4, ("address", "address", "bytes32", "address"),
                             31, 0, "_balances", "amount"),
        "_balances.tokensCount": (
            4, ("address", "address", "bytes32", "address"),
            1, 31, "_balances", "tokensCount"),
    }
    got = region_slot_vars(region, maps)
    bad = 0
    bad += check(got == [
        f"_balances[maker][app][{lit}][token0].amount",
        f"_balances[maker][app][{lit}][token0].tokensCount",
    ], f"the exact certified literal-key slots are reused: {got}")
    bad += check(not any("strategyHash" in g for g in got),
                 f"no guessed strategyHash slot is introduced: {got}")
    return bad


def test_mapping_aliases_keep_source_names_for_ladder_vars():
    """The ladder spec names mapping vars with solc/source labels.

    ERC-3643 measured the failure mode: solc reports `_allowances`, while
    ESBMC's merged contract field is `_allowances$496`. Queryability is checked
    through the alias row, but the `vars` and region entries sent to
    --path-cov-assert must keep source spelling; otherwise the ladder rejects
    `state._allowances$496[...]` as a coordinate the unit cannot express.
    """
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    source_maps = {
        "_allowances": (7, ("address", "address"), 32, 0,
                        "_allowances", None),
    }
    all_maps = add_esbmc_mapping_aliases(
        source_maps, {"_allowances": "_allowances$496"})
    query_maps = prefer_esbmc_mapping_aliases(all_maps)
    region = {
        "state._allowances[msg.sender][_spender]": (0, 0),
    }
    slots, used, skipped = source_access_slot_vars(
        [("_allowances", ("msg.sender", "_spender"))],
        query_maps, params=[("_spender", "address")])
    pins, pin_skips = assert_query_pins(
        {"state._allowances[msg.sender][_spender]": 1}, {}, query_maps)
    query_region, region_skips = assert_query_region_entries(
        region, {}, {}, query_maps)
    bad = 0
    bad += check("_allowances$496" in all_maps,
                 f"alias row is present beside source layout: {all_maps}")
    bad += check("_allowances" not in query_maps
                 and "_allowances$496" in query_maps,
                 f"query map drops the refused source name: {query_maps}")
    bad += check(region_slot_vars(region, query_maps) == [
        "_allowances[msg.sender][_spender]"],
                 f"certified source region is reused in source spelling: "
                 f"{region_slot_vars(region, query_maps)}")
    alias_region = {
        "state._allowances$496[msg.sender][_spender]": (0, 0),
    }
    bad += check(region_slot_vars(alias_region, query_maps) == [
        "_allowances[msg.sender][_spender]"],
                 f"certified alias region is restored to source spelling: "
                 f"{region_slot_vars(alias_region, query_maps)}")
    bad += check(slots == ["_allowances[msg.sender][_spender]"],
                 f"source-access priority asks the source slot: {slots}")
    bad += check(used == {"_allowances$496"} and skipped == [],
                 f"the alias suppresses fallback without skip noise: "
                 f"{used}, {skipped}")
    bad += check(pins == {
        "state._allowances[msg.sender][_spender]": 1
    } and pin_skips == [],
                 f"mapping pins keep source spelling: {pins}, {pin_skips}")
    bad += check(query_region == [{
        "name": "state._allowances[msg.sender][_spender]",
        "lo": "0", "hi": "0"
    }] and region_skips == [],
                 f"mapping region entries keep source spelling in the spec: "
                 f"{query_region}, {region_skips}")
    return bad


def test_mapping_aliases_keep_struct_member_tails_source_named():
    from solidity_path_put import propose_slot_vars  # noqa: E402

    source_maps = {
        "_balances.amount": (4, "address", 31, 0, "_balances", "amount"),
    }
    all_maps = add_esbmc_mapping_aliases(
        source_maps, {"_balances": "_balances$123"})
    query_maps = prefer_esbmc_mapping_aliases(all_maps)
    got = propose_slot_vars(
        query_maps, [("owner", "address")],
        dependencies=["_balances"])
    bad = 0
    bad += check("_balances$123.amount" in query_maps,
                 f"struct-valued mapping member keeps its member tail: "
                 f"{query_maps}")
    bad += check(got[0] == "_balances[msg.sender].amount",
                 f"fallback proposal uses the source base and member tail: "
                 f"{got}")
    return bad


def test_scalar_layout_aliases_use_source_slots_for_foundry_rendering():
    layout = {
        "_MAINTAINER_": (7, 0, 20),
        "_PRICE_LIMIT_": (8, 0, 32),
    }
    aliases = {
        "_MAINTAINER_": "_MAINTAINER_$3013",
        "_PRICE_LIMIT_": "_PRICE_LIMIT_$3071",
    }

    bad = 0
    bad += check(layout_scalar_key("_MAINTAINER_", layout, aliases)
                 == "_MAINTAINER_",
                 "source storage names remain their own layout key")
    bad += check(layout_scalar_key("_MAINTAINER_$3013", layout, aliases)
                 == "_MAINTAINER_",
                 "ESBMC scalar store aliases fall back to the solc source slot")
    bad += check(layout_scalar_key("_PRICE_LIMIT_$3071", layout, aliases)
                 == "_PRICE_LIMIT_",
                 "multiple scalar aliases are resolved from AST evidence")
    bad += check(layout_scalar_key("_UNKNOWN_$9", layout, aliases) is None,
                 "unknown alias is not guessed from spelling")
    bad += check(layout_scalar_key("_MAINTAINER_$3013", {}, aliases) is None,
                 "alias evidence alone is insufficient without solc layout")
    bad += check(layout_scalar_key("_balances$1[msg.sender]", layout, aliases)
                 is None,
                 "mapping-like names stay on the mapping renderer path")
    return bad


def test_scalar_assert_vars_use_source_names_and_restore_legacy_rows():
    layout = {
        "_owner": (0, 0, 20),
        "_pendingOwner": (1, 0, 20),
    }
    aliases = {
        "_owner": "_owner$32",
        "_pendingOwner": "_pendingOwner$166",
    }

    bad = 0
    bad += check(assert_query_var_name("_owner", layout, aliases) == "_owner",
                 "scalar assertion vars use the source storage name")
    bad += check(assert_query_var_name("_owner$32", layout, aliases) == "_owner",
                 "ESBMC scalar aliases are restored before the assertion ladder")
    bad += check(assert_query_var_name("_pendingOwner", layout, aliases)
                 == "_pendingOwner",
                 "scalar aliases are not sent to the assertion ladder")
    bad += check(assert_query_var_name("_balances$1[msg.sender]", layout, aliases)
                 == "_balances$1[msg.sender]",
                 "mapping slots stay in their mapping spelling")
    rows = [("_owner$32", "post == pre", "HOLDS"),
            ("_pendingOwner$166", "post == newOwner", "HOLDS"),
            ("return", "return == 0", "HOLDS")]
    bad += check(restore_ladder_row_names(rows, aliases)
                 == [("_owner", "post == pre", "HOLDS"),
                     ("_pendingOwner", "post == newOwner", "HOLDS"),
                     ("return", "return == 0", "HOLDS")],
                 "legacy ESBMC-named ladder rows still restore to source names")
    return bad


def test_state_store_aliases_have_one_canonical_entry_coordinate():
    aliases = {"_owner": "_owner$32"}
    bad = 0
    bad += check(canonical_state_coord_name("state._owner", aliases)
                 == "state._owner",
                 "source state coordinates are already canonical")
    bad += check(canonical_state_coord_name("state._owner$32", aliases)
                 == "state._owner",
                 "ESBMC store-name coordinates canonicalize to source names")
    bad += check(canonical_state_coord_name("state.bal[msg.sender]", aliases)
                 == "state.bal[msg.sender]",
                 "mapping slot coordinates are not scalar-alias collapsed")
    bad += check(canonical_state_coord_name("msg.sender", aliases)
                 == "msg.sender",
                 "non-state coordinates are unchanged")
    return bad


def test_mapping_store_aliases_have_source_path_guard_coordinate():
    maps = {
        "_isBlackListedBot$534": (
            12, "address", 1, 0, "_isBlackListedBot", None),
        "_balances$1.amount": (
            7, "address", 32, 0, "_balances", "amount"),
    }
    bad = 0
    bad += check(mapping_source_coord_alias(
        "state._isBlackListedBot$534[account]", maps)
        == "state._isBlackListedBot[account]",
        "mapping store alias exposes the source path-guard coordinate")
    bad += check(mapping_source_coord_alias(
        "state._balances$1[msg.sender].amount", maps)
        == "state._balances[msg.sender].amount",
        "mapping struct-member alias keeps its source member tail")
    bad += check(mapping_source_coord_alias(
        "state._unknown$1[account]", maps) is None,
        "unknown mapping aliases are not guessed from spelling")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(!_isBlackListedBot[account])"}],
        {"state._isBlackListedBot$534[account]": "_pre_blacklisted",
         "state._isBlackListedBot[account]": "_pre_blacklisted"})
    bad += check(lines == [
        ("!(!_isBlackListedBot[account])",
         "    vm.assume(_pre_blacklisted == 0);")
    ] and skipped == [],
        f"source alias lets unary mapping guard render: {lines}, {skipped}")
    return bad


def test_path_guard_coord_idents_expand_scalar_store_aliases():
    aliases = {"_owner": "_owner$413"}
    expanded = expand_path_guard_coord_idents(
        {"state._owner": "_pre_owner"}, state_store_names=aliases)
    bad = 0
    bad += check(expanded["state._owner$413"] == "_pre_owner",
                 "source scalar pre-read is visible under ESBMC store name")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(_owner$413 == return_value$__msgSender$1)"}],
        {**expanded, "msg.sender": "uint256(uint160(sender))"})
    bad += check(lines == [
        ("!(_owner$413 == return_value$__msgSender$1)",
         "    vm.assume(_pre_owner == uint256(uint160(sender)));")
    ] and skipped == [],
        f"store-named scalar guard renders with __msgSender alias: "
        f"{lines}, {skipped}")
    return bad


def test_path_guard_coord_idents_expand_mapping_source_aliases():
    maps = {
        "_isBlackListedBot": (3, "address", 1, 0, "_isBlackListedBot", None),
        "_isBlackListedBot$534": (
            3, "address", 1, 0, "_isBlackListedBot", None),
    }
    expanded = expand_path_guard_coord_idents(
        {"state._isBlackListedBot$534[account]": "_pre_blacklisted"},
        maps=maps)
    bad = 0
    bad += check(expanded["state._isBlackListedBot[account]"]
                 == "_pre_blacklisted",
                 "ESBMC mapping pre-read is visible under source name")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(!_isBlackListedBot[account])"}], expanded)
    bad += check(lines == [
        ("!(!_isBlackListedBot[account])",
         "    vm.assume(_pre_blacklisted == 0);")
    ] and skipped == [],
        f"source-named mapping guard renders: {lines}, {skipped}")
    return bad


def test_path_guard_coord_idents_expand_mapping_store_aliases():
    maps = {
        "_isBlackListedBot": (3, "address", 1, 0, "_isBlackListedBot", None),
        "_isBlackListedBot$534": (
            3, "address", 1, 0, "_isBlackListedBot", None),
    }
    expanded = expand_path_guard_coord_idents(
        {"state._isBlackListedBot[account]": "_pre_blacklisted"}, maps=maps)
    bad = 0
    bad += check(expanded["state._isBlackListedBot$534[account]"]
                 == "_pre_blacklisted",
                 "source mapping pre-read is visible under ESBMC store name")
    return bad


def test_contract_state_store_aliases_read_solc_declaration_ids():
    from solidity_ast_dependencies import contract_state_esbmc_store_names  # noqa: E402

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "Base", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "owner",
             "stateVariable": True},
        ]}, {
        "nodeType": "ContractDefinition", "name": "Token", "id": 2,
        "linearizedBaseContracts": [2, 1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 496,
             "name": "_allowances", "stateVariable": True},
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        aliases, evidence = contract_state_esbmc_store_names(path, "Token")
    finally:
        os.unlink(path)
    bad = 0
    bad += check(aliases == {"owner": "owner$10",
                             "_allowances": "_allowances$496"},
                 f"linearized state aliases use declaration ids: {aliases}")
    bad += check(evidence == [], f"no duplicate-name warning: {evidence}")
    return bad


def test_assert_query_keeps_state_pins_for_the_certified_slice():
    """State pins are single-point entry assumptions for --path-cov-assert.

    A wide state region is still not passed through this helper, but a pin is
    already a point value from certification.  Dropping it asks the assertion
    ladder over a wider state slice and loses return/state R2 facts tied to
    immutables such as `return == state.baseRate`.
    """
    keep, skipped = assert_query_pins(
        {"state._DOCKED": 255, "state.owner": 7, "msg.value": 0},
        layout={"owner": (0, 0, 20)}, maps={})
    bad = 0
    bad += check(keep == {"msg.value": 0, "state._DOCKED": 255,
                          "state.owner": 7},
                 f"state pins remain in the assert spec: {keep}")
    bad += check(skipped == [], f"no scalar state pin is skipped: {skipped}")
    return bad


def test_assert_query_region_keeps_slots_but_drops_state_scalars():
    lit = "0x2000000000000000000000000000000000000000000000000000000000000000"
    region = {
        "msg.value": (0, 0),
        "state._DOCKED": (255, 255),
        "state.owner": (7, 7),
        f"state._balances[maker][app][{lit}][token0].amount": (0, 0),
    }
    maps = {
        "_balances.amount": (4, ("address", "address", "bytes32", "address"),
                             31, 0, "_balances", "amount"),
    }
    entries, skipped = assert_query_region_entries(
        region, holes={"msg.value": [7]},
        layout={"owner": (0, 0, 20)}, maps=maps)
    names = [e["name"] for e in entries]
    bad = 0
    bad += check("msg.value" in names and entries[0].get("holes") == ["7"],
                 f"ordinary input coordinates and holes survive: {entries}")
    bad += check(
        f"state._balances[maker][app][{lit}][token0].amount" in names,
        f"certified mapping-member region survives: {entries}")
    bad += check("state._DOCKED" not in names,
                 f"semantic state is not sent to the assert query: {entries}")
    bad += check("state.owner" not in names,
                 f"storage scalar state is not sent to the assert query: "
                 f"{entries}")
    bad += check(any("state._DOCKED" in s for s in skipped),
                 f"and the skipped semantic region coordinate is reported: "
                 f"{skipped}")
    bad += check(any("state.owner" in s and "unconstrained-state superset" in s
                     for s in skipped),
                 f"and the skipped storage-scalar coordinate is reported: "
                 f"{skipped}")
    return bad


def _deriv_put(derived_by):
    """A PUT carrying a stage-2 derivation configuration, for the provenance
    tests. Region and holes are the ordinary wide ones so the PUT is actually
    emitted and the header can be read."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=LADDER, notes=notes, maps=SLOT_MAPS,
        derived_by=derived_by)
    return "\n".join(put or []), notes


def test_a_probe_only_width_is_FLAGGED_on_the_test():
    """MUST FLIP against the ladder control below.

    The work order does not accept a width that rests on a neighbourhood probe:
    a probe shows that some nearby value also walks the path, not where the
    path STOPS. When the arm ran neither the ladder nor the subtraction, the
    emitted test has to say so on its face -- the region is still certified, but
    its WIDTH is not evidence of a measured boundary, and those are different
    claims that a reader cannot separate from the region string alone.
    """
    text, _notes = _deriv_put({"level0": True, "level0_points": 8})
    bad = 0
    bad += check("WIDTH PROVENANCE" in text,
                 "the provenance block is on the test")
    bad += check("NO LADDER AND NO SUBTRACTION RAN" in text,
                 "and a probe-only row is FLAGGED")
    bad += check("level0_points = 8" in text,
                 "the switches themselves are printed, not just a verdict")
    return bad


def test_a_ladder_derived_width_is_NOT_flagged():
    """CONTROL. With the ladder's boundary probes recorded, the width has a
    measured source and the warning must be ABSENT. A flag that fires on every
    row says nothing -- the always-true reader this project has already been
    bitten by."""
    text, _notes = _deriv_put({"geometric_bracket": True,
                               "probe_ladder": True, "probes": 8})
    bad = 0
    bad += check("WIDTH PROVENANCE" in text,
                 "the provenance block is still printed")
    bad += check("NO LADDER AND NO SUBTRACTION RAN" not in text,
                 "and the probe-only warning is ABSENT")
    bad += check("Width sources that ran" in text
                 and "stage-2 geometric ladder" in text,
                 "the measured source is named")
    return bad


def test_probes_do_NOT_claim_a_ladder_when_the_bracket_was_skipped():
    """The historical false-positive shape: every row records probes=8, even
    when --skip-bracket prevented the geometric ladder from running."""
    text, _notes = _deriv_put({"skip_bracket": True, "probes": 8,
                               "level0": True})
    bad = 0
    bad += check("NO LADDER AND NO SUBTRACTION RAN" in text,
                 "a refine probe count is not labelled as a boundary ladder")
    bad += check("stage-2 geometric ladder" not in text,
                 "the width source line does not invent a skipped mechanism")
    return bad


def test_no_derivation_recorded_prints_no_provenance_block():
    """CONTROL 2. An older sweep row carries none of these switches. Printing
    an EMPTY provenance block would read as 'nothing ran', which is a claim;
    printing nothing reads as 'not recorded', which is the truth."""
    text, _notes = _deriv_put({})
    return check("WIDTH PROVENANCE" not in text,
                 "no block is printed when nothing was recorded")


def _region_put(region, holes):
    """A PUT over a caller-chosen region and hole set, for the interval tests.

    Deliberately a SECOND fixture rather than more optional arguments on
    `_slot_pin_put`: that one pins a region so the slot tests always describe
    the same slice, and widening it would make every slot test's region a
    function of whichever interval test ran last.
    """
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region=region, holes=holes, pins={}, params=PARAMS, emitted=em,
        case=case, layout=LAYOUT, ladder_rows=LADDER, notes=notes,
        maps=SLOT_MAPS)
    return put, stats, notes


def test_an_entirely_holed_coordinate_REFUSES_the_put():
    """MUST FLIP against the two controls below.

    `bps` in [7, 8] with both 7 and 8 holed has NO value left, while `u` is the
    full address space -- so the floor test is satisfied by `u` and the PUT
    would be emitted with a `bps` whose every `vm.assume` rejects. forge then
    fails the run for too many rejections: a RED test on the unmodified
    contract, for a reason that is not about the contract, appearing only on
    PUTs that had a wide coordinate to look healthy with.
    """
    put, _stats, notes = _region_put({"bps": (7, 8), "u": (0, (1 << 160) - 1)},
                                     {"bps": [7, 8]})
    bad = 0
    bad += check(put is None, "no PUT is emitted")
    bad += check(any("leaves NO value for" in n for n in notes),
                 f"and the refusal names the empty coordinate: {notes}")
    bad += check(any("bps" in n for n in notes),
                 "the note says WHICH coordinate is empty")
    return bad


# ---- THE R2 PROPOSER -------------------------------------------------------
#
# R2 (`post - pre in [lo, hi]`) has never been requested by the pipeline: the
# spec carried names only, and 0 of 128 driver files ever wrote `delta_dir`.
# These pin the rule that turns an already-measured ladder pass into the
# request -- above all the two cases where the honest answer is to propose
# NOTHING, because a proposer that always proposes is how a false certificate
# gets asked for.

INC_ROWS = [("bal", "post == pre", "REFUTED"), ("bal", "post != pre", "HOLDS"),
            ("bal", "post >= pre", "HOLDS"), ("bal", "post <= pre", "REFUTED")]
DEC_ROWS = [("bal", "post >= pre", "REFUTED"), ("bal", "post <= pre", "HOLDS")]
FROZEN_ROWS = [("bal", "post >= pre", "HOLDS"), ("bal", "post <= pre", "HOLDS")]
MIXED_ROWS = [("bal", "post >= pre", "REFUTED"),
              ("bal", "post <= pre", "REFUTED")]


def test_an_INCREASING_variable_proposes_an_inc_delta_named_by_the_parameter():
    """THE PROPERTY THE WHOLE R2 CHAIN EXISTS FOR: `post - pre == amount`."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    got = propose_r2_specs(INC_ROWS, [("to", "address"), ("amount", "uint256")])
    bad = 0
    s1 = [g for g in got if g["stage"] == 1 and g["kind"] == "num"]
    bad += check(len(s1) == 1, f"one stage-1 amount query: {got}")
    if not s1:
        return bad + 1
    bad += check(s1[0]["param"] == "amount",
                 "the address parameter is NOT used as a delta bound")
    bad += check(s1[0]["vars"] == [{"name": "bal",
                                    "abs_lo": "amount", "abs_hi": "amount",
                                    "delta_dir": "inc",
                                    "delta_lo": "amount",
                                    "delta_hi": "amount"}],
                 f"ONE entry carries BOTH questions -- `has_abs` and "
                 f"`has_delta` are independent flags on the same "
                 f"`assert_vart`, so the absolute bound costs no extra "
                 f"query: {s1[0]}")
    return bad


def test_a_DECREASING_variable_proposes_dec_not_inc():
    """⛔ THE DIRECTION IS NOT DECORATION. Candidates are unsigned, so
    `post - pre` WRAPS on a decrease; a `dec` region answered as `inc` is
    answered about 2^256 - d and the certificate would be false."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    got = propose_r2_specs(DEC_ROWS, [("amount", "uint256")])
    bad = 0
    s1 = [g for g in got if g["stage"] == 1]
    bad += check(len(s1) == 1, f"one stage-1 query: {got}")
    if not s1:
        return bad + 1
    bad += check(s1[0]["vars"][0]["delta_dir"] == "dec",
                 f"direction read from the ladder, not defaulted: {s1[0]}")
    bad += check(all(e["delta_dir"] == "dec"
                     for g in got for e in g["vars"] if "delta_dir" in e),
                 f"and the stage-2 cap inherits the SAME direction -- a cap "
                 f"answered as `inc` is answered about the wrapped "
                 f"difference, exactly like the exact bound: {got}")
    return bad


def test_an_UNCHANGED_variable_proposes_NO_DELTA_and_says_why():
    """Both ordering rungs holding means `post == pre` over the whole region:
    the delta is identically 0, so `[p, p]` is false for every nonzero p.
    Proposing a DELTA would spend a query to be told something already known.

    The ABSOLUTE bound is still proposed, and it is a different question: it
    asks whether the (unchanged) value equals the argument, which is how a
    no-op branch of a setter is told apart from a real write."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(FROZEN_ROWS, [("amount", "uint256")],
                           log=said.append)
    bad = 0
    deltas = [e for g in got for e in g["vars"] if "delta_dir" in e]
    bad += check(deltas == [], f"no delta entry anywhere: {deltas}")
    bad += check(any("every delta is 0" in s for s in said),
                 f"and the reason is printed: {said}")
    bad += check(any(e.get("abs_lo") == "amount"
                     for g in got for e in g["vars"]),
                 f"the absolute bound IS still asked: {got}")
    return bad


def test_a_MIXED_DIRECTION_region_proposes_NO_DELTA_but_DOES_propose_ABS():
    """⛔ THE CASE THAT MUST NOT BE GUESSED. Both ordering rungs REFUTED means
    the region holds an increasing execution AND a decreasing one. No single
    `delta_dir` is sound, and picking one would ask for a certificate about
    the wrapped difference on half the region.

    ⛔ AND THIS IS THE SETTER, which is why the arm now proposes something.
    `_distributor = d` moves the value up on some inputs and down on others,
    so BOTH ordering rungs are refuted and the delta arm correctly declines --
    and the old proposer stopped there, leaving the single most common shape
    in the corpus with no R2 at all. `post in [d, d]` needs no direction and
    is the entire property of a setter."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(MIXED_ROWS, [("amount", "uint256")],
                           log=said.append)
    bad = 0
    deltas = [e for g in got for e in g["vars"] if "delta_dir" in e]
    bad += check(deltas == [], f"no delta entry anywhere: {deltas}")
    bad += check(any("no single" in s and "delta_dir" in s for s in said),
                 f"and it says the region, not the proposer, is the reason: "
                 f"{said}")
    bad += check(any(e.get("abs_lo") == "amount" and e.get("abs_hi") == "amount"
                     for g in got for e in g["vars"]),
                 f"but the absolute bound IS proposed: {got}")
    return bad


def test_a_unit_whose_ONLY_PARAMETER_IS_AN_ADDRESS_proposes_an_ABS_bound():
    """farming `setDistributor`'s only parameter is an address.

    ⛔ NO DELTA, EVER. The difference of two balances is not an address, so
    `post - pre in [d, d]` is not a weaker question but a meaningless one.

    ⛔ BUT NOT `nothing`, WHICH IS WHAT IT USED TO BE. The numeric filter
    dropped the parameter and the proposer returned an empty list, so this
    unit -- the shape the corpus has most of -- asked for no R2 and the one
    property it obviously has, `post == the argument`, went unasked while
    being expressible the whole time."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(INC_ROWS, [("distributor_", "address")],
                           log=said.append)
    bad = 0
    bad += check(len(got) == 1, f"exactly one query: {got}")
    if not got:
        return bad + 1
    bad += check(got[0]["kind"] == "id" and got[0]["param"] == "distributor_",
                 f"and it is an identity query: {got[0]}")
    bad += check(got[0]["vars"] == [{"name": "bal", "abs_lo": "distributor_",
                                     "abs_hi": "distributor_"}],
                 f"absolute endpoints only, no delta key: {got[0]['vars']}")
    bad += check(any("meaningless" in s for s in said),
                 f"and the log says why no delta was asked: {said}")
    return bad


MIXED_WIDTH_ROWS = [("_distributor", "post >= pre", "HOLDS"),
                    ("_owner", "post >= pre", "HOLDS"),
                    ("_totalSupply", "post >= pre", "HOLDS"),
                    ("_balances[k]", "post >= pre", "HOLDS")]
MIXED_WIDTH_BYTES = {"_distributor": 20, "_owner": 20,
                     "_totalSupply": 32, "_balances[k]": 32}


def test_an_IDENTITY_endpoint_is_only_asked_about_candidates_of_ITS_WIDTH():
    """⛔ EIGHT QUESTIONS NOBODY HAS, AND FOUR OF THEM RUN THE SOLVER OUT.

    MEASURED on farming setDistributor enc=15. The identity query went out
    with all ten candidates and came back:

        _distributor   20 bytes   HOLDS      <- the answer that was wanted
        _owner         20 bytes   REFUTED    <- a real question: did the call
                                                also overwrite the owner?
        _totalSupply   32 bytes   REFUTED  \\
        _balances[..]  32 bytes   REFUTED   |  a balance is not an address
        _MAX_BALANCE   no slot    REFUTED   |
        _allowances[..][..] x4    NO VERDICT (solver unknown)

    A 32-byte balance cannot equal a 20-byte address, so those rows were
    REFUTED by construction; the four `_allowances` rows are the nested-mapping
    shape this corpus already knows answers solver-unknown, so they spent the
    solver to exhaustion for nothing. Cutting them loses NO verdict."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(MIXED_WIDTH_ROWS, [("d_", "address")],
                           log=said.append, var_bytes=MIXED_WIDTH_BYTES)
    bad = 0
    ids = [g for g in got if g["kind"] == "id"]
    bad += check(len(ids) == 1, f"one identity query: {got}")
    if not ids:
        return bad + 2
    names = sorted(e["name"] for e in ids[0]["vars"])
    bad += check(names == ["_distributor", "_owner"],
                 f"only the 20-byte candidates are asked: {names}")
    bad += check(any("NOT asked about it" in s for s in said),
                 f"and the exclusion is announced with its reason, never "
                 f"silent: {said}")
    return bad


def test_a_FIXED_BYTES_endpoint_is_width_filtered_like_an_identity():
    from solidity_path_put import propose_r2_specs  # noqa: E402
    rows = [("last4", "post >= pre", "HOLDS"),
            ("owner", "post >= pre", "HOLDS"),
            ("wide", "post >= pre", "HOLDS")]
    got = propose_r2_specs(rows, [("key_", "bytes4")],
                           var_bytes={"last4": 4, "owner": 20, "wide": 32})
    bad = 0
    ids = [g for g in got if g["kind"] == "id"]
    bad += check(len(ids) == 1, f"one fixed-bytes identity query: {got}")
    if not ids:
        return bad + 1
    bad += check(ids[0]["vars"] == [{
        "name": "last4",
        "abs_lo": "key_",
        "abs_hi": "key_",
    }], f"only the same-width bytes4 slot is asked: {ids[0]}")
    return bad


def test_the_WIDTH_FILTER_leaves_a_NUMERIC_endpoint_alone():
    """⛔ THE FILTER IS ABOUT IDENTITIES ONLY. An amount legitimately bounds a
    candidate of any width -- `uint8 fee` bounding a `uint256 total` is
    routine, and the C++ builds the comparison in the CANDIDATE's type. A
    width rule applied to the numeric arm would delete the delta bound that is
    the whole reason R2 exists."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    got = propose_r2_specs(MIXED_WIDTH_ROWS, [("amt", "uint8")],
                           var_bytes=MIXED_WIDTH_BYTES)
    bad = 0
    nums = [g for g in got if g["kind"] == "num"]
    bad += check(len(nums) == 1, f"the amount query is still there: {got}")
    if not nums:
        return bad + 1
    names = sorted(e["name"] for e in nums[0]["vars"])
    bad += check(names == sorted(MIXED_WIDTH_BYTES),
                 f"and it reaches EVERY candidate regardless of width: "
                 f"{names}")
    return bad


def test_WITHOUT_a_width_table_NOTHING_is_filtered():
    """⛔ A MISSING INPUT MAY NOT SILENTLY NARROW THE QUESTION. A caller that
    cannot supply the layout gets exactly the behaviour it had before the
    filter existed, rather than a quietly smaller query -- an absent table
    reads as `no information`, never as `no candidates`."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    got = propose_r2_specs(MIXED_WIDTH_ROWS, [("d_", "address")],
                           var_bytes=None)
    bad = 0
    ids = [g for g in got if g["kind"] == "id"]
    bad += check(len(ids) == 1 and len(ids[0]["vars"]) == 4,
                 f"all four candidates are asked: {ids}")
    return bad


def test_an_IDENTITY_with_NO_CANDIDATE_OF_ITS_WIDTH_sends_NO_QUERY():
    """A contract whose every candidate is a 32-byte number has nothing an
    address could equal. Sending the query anyway buys a column of REFUTED at
    the price of a whole esbmc run -- and it must be REPORTED, because a query
    that was never sent and a query that came back empty are different facts
    with different repairs."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(
        [("_totalSupply", "post >= pre", "HOLDS")], [("d_", "address")],
        log=said.append, var_bytes={"_totalSupply": 32})
    bad = 0
    bad += check([g for g in got if g["kind"] == "id"] == [],
                 f"no identity query is emitted: {got}")
    bad += check(any("NO candidate has its width" in s for s in said),
                 f"and it says so, naming the contract's storage as the "
                 f"cause rather than the proposer: {said}")
    return bad


def test_TWO_esbmc_invocations_do_not_share_ONE_log_file():
    """⛔ ONE FILE, TWO WRITERS, AND THE FIRST ONE'S EVIDENCE IS GONE.

    Every ladder call for a unit runs in the SAME directory, and the log was
    opened with mode "w", so each R2 pass silently replaced the first pass's
    output.

    MEASURED, and it blocked a live diagnosis rather than being a tidiness
    point: aqua `push` proposed 48 mapping-slot candidates and got back 6 rows
    over one unrelated variable. The tool prints a per-candidate REFUSAL
    saying why each name carries no candidate -- and that text was in the
    first pass's log, which the R2 pass had already overwritten by the time
    anyone looked. The question could not be answered from disk at all.

    Pinned in both directions: two calls leave two numbered logs with
    DIFFERENT contents, and `run.log` still holds the last one so no existing
    reader changes."""
    import tempfile
    from solidity_path_put import run_esbmc  # noqa: E402
    bad = 0
    with tempfile.TemporaryDirectory() as d:
        # `true` and `false` stand in for two esbmc calls: what matters is that
        # two invocations landed in one directory, not what they printed.
        run_esbmc("/bin/echo", "/dev/null", None, "C", "u", ["FIRST"], d,
                  1, 10, "1g", scope="whole")
        run_esbmc("/bin/echo", "/dev/null", None, "C", "u", ["SECOND"], d,
                  1, 10, "1g", scope="whole")
        names = sorted(os.listdir(d))
        bad += check("run.1.log" in names and "run.2.log" in names,
                     f"each invocation left its OWN log: {names}")
        if "run.1.log" in names and "run.2.log" in names:
            a = open(os.path.join(d, "run.1.log")).read()
            b = open(os.path.join(d, "run.2.log")).read()
            bad += check("FIRST" in a and "FIRST" not in b,
                         "the first call's output survives the second")
            bad += check("SECOND" in b,
                         "and the second call has its own")
        bad += check("run.log" in names
                     and "SECOND" in open(os.path.join(d, "run.log")).read(),
                     f"`run.log` still holds the LAST call, so every existing "
                     f"reader is unchanged: {names}")
    return bad


def test_a_candidate_with_NO_STORAGE_SLOT_gets_NO_R2_QUERY():
    """⛔ A QUERY SPENT ON A ROW THE EMITTER THEN DISCARDS.

    Whatever verdict comes back for a candidate solc's layout does not list,
    the emitter drops the rung -- "no storage slot ... a compile-time
    tautology, not an oracle" -- because no test can read the value at all.

    MEASURED on aqua `push`, where `_DOCKED` is the ONLY candidate the ladder
    ranges over: the R2 pass went out, came back `post in [amount, amount]
    REFUTED`, and the row was then dropped for having no slot. One whole esbmc
    query, zero usable output.

    ⛔ AND THE EMPTY CASE IS ANNOUNCED, not silent: a query that was never
    sent and a query that came back empty are different facts."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(
        [("_DOCKED", "post >= pre", "HOLDS")], [("amount", "uint256")],
        log=said.append, var_bytes={})          # nothing has a slot
    bad = 0
    bad += check(got == [], f"no query at all is proposed: {got}")
    bad += check(any("NO storage slot" in s for s in said),
                 f"and the exclusion names the candidate: {said}")
    bad += check(any("no query is sent" in s for s in said),
                 f"and the empty query is announced: {said}")
    return bad


def test_a_SLOTTED_candidate_beside_an_UNSLOTTED_one_still_gets_ITS_query():
    """⛔ THE DIRECTION THAT WOULD LOSE ORACLE. Excluding the unreadable
    candidate must not take the readable one with it -- one contract routinely
    has both, and dropping the query because part of it was useless would cost
    the part that was not."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    got = propose_r2_specs(
        [("_DOCKED", "post >= pre", "HOLDS"),
         ("_total", "post >= pre", "HOLDS")],
        [("amount", "uint256")], var_bytes={"_total": 32})
    bad = 0
    s1 = [g for g in got if g["stage"] == 1]
    bad += check(len(s1) == 1, f"the query is still sent: {got}")
    if not s1:
        return bad + 1
    names = [e["name"] for e in s1[0]["vars"]]
    bad += check(names == ["_total"],
                 f"carrying the readable candidate only: {names}")
    return bad


def test_asked_but_never_answered_is_counted_as_zero_not_as_seven():
    """⛔ THE aqua SHAPE: 48 questions, 7 answers, none of them to a question.

    Rows came back for `_DOCKED`, which the spec never named -- the component
    loop is deliberately not whitelisted by a slot-only spec. Counting rows
    would score that 7-of-48; the truth is 0-of-48 plus 7 nobody asked for, and
    a counter that cannot tell those apart is how this went unnoticed."""
    from solidity_path_put import ladder_answer_gap  # noqa: E402
    asked = [f"_balances[a{i}][b].amount" for i in range(48)]
    rows = [("_DOCKED", "post == pre", "HOLDS"),
            ("_DOCKED", "post >= pre", "HOLDS")]
    unanswered, unasked = ladder_answer_gap(asked, rows)
    bad = 0
    bad += check(len(unanswered) == 48,
                 f"all 48 are unanswered, not 41: {len(unanswered)}")
    bad += check(unasked == ["_DOCKED"],
                 f"and the row nobody asked for is reported separately, once: "
                 f"{unasked}")
    return bad


def test_a_ladder_that_answered_everything_reports_no_gap():
    """THE NEGATIVE CONTROL. Without it the gate above is indistinguishable
    from one that reports every run as broken -- which is the always-true
    reader this project has already shipped once."""
    from solidity_path_put import ladder_answer_gap  # noqa: E402
    asked = ["_bal[msg.sender]", "_bal[to]"]
    rows = [("_bal[msg.sender]", "post <= pre", "HOLDS"),
            ("_bal[to]", "post >= pre", "HOLDS"),
            ("_bal[to]", "post > pre", "REFUTED")]
    unanswered, unasked = ladder_answer_gap(asked, rows)
    bad = 0
    bad += check(unanswered == [],
                 f"nothing is unanswered when every name came back: "
                 f"{unanswered}")
    bad += check(unasked == [], f"and nothing is unasked: {unasked}")
    # a REFUTED row still counts as an ANSWER -- the question was answered,
    # the answer was no. Conflating them would report a working ladder as a
    # silent one.
    unanswered2, _ = ladder_answer_gap(
        ["x"], [("x", "post in [a, a]", "REFUTED")])
    bad += check(unanswered2 == [],
                 f"a REFUTED verdict is an answer, not a gap: {unanswered2}")
    return bad


def test_no_slot_asked_means_no_gap_and_no_claim():
    """A unit with no mapping asks nothing, so it must not be reported as a
    unit whose questions went unanswered. Zero asked and zero answered is not
    the aqua shape and must not print like it."""
    from solidity_path_put import ladder_answer_gap  # noqa: E402
    unanswered, unasked = ladder_answer_gap([], [("_owner", "post == pre",
                                                  "HOLDS")])
    bad = 0
    bad += check(unanswered == [], f"nothing was asked: {unanswered}")
    bad += check(unasked == ["_owner"],
                 f"the component row is still reported as unasked: {unasked}")
    return bad


def test_the_EXCLUSION_MESSAGE_names_no_cause_it_did_not_measure():
    """⛔ A DIAGNOSTIC THAT ASSERTS A MECHANISM IT NEVER CHECKED.

    The width-exclusion line used to end "...and four of these are the
    nested-mapping shape that answers solver-unknown" -- a fact about ONE run
    on farming, welded into a message every contract prints. On aqua the
    single excluded candidate is `_DOCKED`, a constant: not a mapping, and not
    four of anything. The next reader believes it, which makes it worse than
    saying less."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    propose_r2_specs([("_DOCKED", "post >= pre", "HOLDS")],
                     [("who", "address")], log=said.append,
                     var_bytes={"_DOCKED": 32})
    joined = "\n".join(said)
    bad = 0
    bad += check("nested-mapping" not in joined,
                 f"the message does not claim a mapping it never saw: "
                 f"{joined}")
    bad += check("four of these" not in joined,
                 f"nor a count it did not take: {joined}")
    bad += check("_DOCKED" in joined,
                 f"but it DOES name what it excluded: {joined}")
    return bad


def test_a_unit_with_NO_USABLE_PARAMETER_AT_ALL_proposes_nothing():
    """The honest empty case survives: `bool` and `bytes` name neither an
    amount nor an identity, so there is no endpoint and nothing to ask."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    said = []
    got = propose_r2_specs(INC_ROWS, [("flag", "bool"), ("data", "bytes")],
                           log=said.append)
    bad = 0
    bad += check(got == [], f"nothing proposed: {got}")
    bad += check(any("no parameter an endpoint could name" in s for s in said),
                 f"and the reason names the cause: {said}")
    return bad


def test_TWO_integer_parameters_produce_TWO_SEPARATE_queries():
    """⛔ ONE ENTRY PER VARIABLE PER SPEC. `goto_coverage.cpp` keeps one
    `assert_vart` per name and now REFUSES a duplicate outright, so a variable
    cannot carry two endpoint pairs in one query. Two parameters therefore
    cost two runs, and that cost must be visible rather than hidden behind a
    spec that would have been half-dropped."""
    from solidity_path_put import propose_r2_specs  # noqa: E402
    got = propose_r2_specs(INC_ROWS,
                           [("a", "uint256"), ("b", "uint128")])
    bad = 0
    s1 = [g for g in got if g["stage"] == 1]
    bad += check(len(s1) == 2, f"two stage-1 queries, not one merged: {got}")
    if len(s1) != 2:
        return bad + 1
    names = [g["param"] for g in s1]
    bad += check(names == ["a", "b"], f"one per parameter: {names}")
    for g in got:
        seen = [e["name"] for e in g["vars"]]
        bad += check(len(seen) == len(set(seen)),
                     f"no duplicate variable name inside a spec: {seen}")
    return bad


FORGE_OUT = """
Ran 3 tests for test/Probe.t.sol:Probe
[PASS] test_probe_0(uint256) (runs: 256, mu: 31451, ~: 31450)
[FAIL: assertion failed] test_probe_1(uint256) (runs: 3, mu: 100, ~: 100)
[FAIL: panic: arithmetic underflow or overflow (0x11)] test_probe_2(uint256) (runs: 1, mu: 9, ~: 9)
Suite result: FAILED. 1 passed; 2 failed; 0 skipped
"""


def test_a_fuzz_REFUTATION_is_read_and_a_pass_is_NOT_a_proof():
    """The prefilter's whole value is the FAIL rows; its whole danger is
    reading a PASS as a verdict. `forge found no failing draw in 256` is not a
    proof, so the strongest label must be NOT-REFUTED -- which still costs a
    solver query."""
    from solidity_path_put import fuzz_prefilter_verdicts  # noqa: E402
    got = fuzz_prefilter_verdicts(
        {"test_probe_0": "bal: post == pre",
         "test_probe_1": "bal: post != pre",
         "test_probe_2": "bal: post - pre in [amt, amt] with post >= pre"},
        FORGE_OUT)
    bad = 0
    bad += check(got["bal: post == pre"] == "NOT-REFUTED",
                 f"a PASS is NOT-REFUTED, never HOLDS: {got}")
    bad += check(got["bal: post != pre"] == "REFUTED",
                 f"an assertion failure is a refutation: {got}")
    bad += check(got["bal: post - pre in [amt, amt] with post >= pre"]
                 == "REFUTED",
                 f"a panic is also a refutation, not a NOT-RUN: {got}")
    bad += check("HOLDS" not in set(got.values()),
                 f"the word HOLDS never appears: {set(got.values())}")
    return bad


def test_a_probe_THAT_NEVER_RAN_is_NOT_RUN_not_a_pass():
    """⛔ THE ALWAYS-TRUE READER THIS FUNCTION EXISTS TO PREVENT. A probe whose
    test name is absent from the forge output did not execute -- the file did
    not compile, the name was wrong, the filter excluded it. Folding that into
    NOT-REFUTED would make every rung 'survive' and the prefilter would report
    a perfect pass rate while measuring nothing at all."""
    from solidity_path_put import fuzz_prefilter_verdicts  # noqa: E402
    got = fuzz_prefilter_verdicts({"test_probe_9": "bal: post > pre"},
                                  FORGE_OUT)
    bad = 0
    bad += check(got["bal: post > pre"] == "NOT-RUN",
                 f"absent means NOT-RUN: {got}")
    bad += check(fuzz_prefilter_verdicts({"test_probe_0": "x"}, "")
                 == {"x": "NOT-RUN"},
                 "and an EMPTY forge output is all NOT-RUN, not all passing")
    return bad


def test_JSON_fuzz_filter_refutes_only_its_labeled_assertion():
    from solidity_path_put import fuzz_prefilter_json_verdicts  # noqa: E402
    payload = {"test/Probe.t.sol:Probe": {"test_results": {
        "test_labeled(uint256)": {
            "status": "Failure",
            "reason": "VERIPUT_CANDIDATE_0 bal: post == amount: 9 != 0"},
        "test_panic(uint256)": {
            "status": "Failure",
            "reason": "panic: arithmetic underflow or overflow (0x11)"},
        "test_collision(uint256)": {
            "status": "Failure",
            "reason": "target reverted with VERIPUT_CANDIDATE_4 bal: "
                      "post == amount: fake"},
        "test_green(uint256)": {"status": "Success", "reason": None},
    }}}
    got = fuzz_prefilter_json_verdicts(
        {"test_labeled": "VERIPUT_CANDIDATE_0 bal: post == amount",
         "test_panic": "VERIPUT_CANDIDATE_1 bal: post == amount",
         "test_green": "VERIPUT_CANDIDATE_2 bal: post == amount",
         "test_absent": "VERIPUT_CANDIDATE_3 bal: post == amount",
         "test_collision": "VERIPUT_CANDIDATE_4 bal: post == amount"},
        json.dumps(payload))
    bad = 0
    bad += check(got["test_labeled"] == "REFUTED",
                 f"the matching labeled failure is a CE: {got}")
    bad += check(got["test_panic"] == "NOT-RUN",
                 f"an unrelated panic cannot remove a candidate: {got}")
    bad += check(got["test_green"] == "NOT-REFUTED",
                 f"a pass is explicitly not a proof: {got}")
    bad += check(got["test_absent"] == "NOT-RUN",
                 f"an absent probe ran no candidate: {got}")
    bad += check(got["test_collision"] == "NOT-RUN",
                 f"a target revert merely containing the marker is not a "
                 f"candidate assertion CE: {got}")
    bad += check("HOLDS" not in got.values(),
                 f"fuzz never produces a proof verdict: {got}")
    return bad


def test_R2_fuzz_filter_removes_only_concretely_refuted_candidates():
    from solidity_path_put import (filter_r2_specs,  # noqa: E402
                                   propose_r2_batch, r2_candidates)
    specs = propose_r2_batch(
        INC_ROWS, [("amount", "uint256")], source_literals=("0", "7"),
        depth=1, var_bytes={"bal": 32},
        rendered_coords=[("amount", "num", None)], term_budget=4,
        log=lambda _line: None)
    before = r2_candidates(specs)
    verdicts = {candidate["key"]: "NOT-REFUTED" for candidate in before}
    verdicts[before[0]["key"]] = "REFUTED"
    verdicts[before[1]["key"]] = "NOT-RUN"
    after = r2_candidates(filter_r2_specs(specs, verdicts))
    after_keys = {candidate["key"] for candidate in after}
    bad = 0
    bad += check(len(after) == len(before) - 1,
                 f"exactly one concrete CE is removed: {len(before)} -> "
                 f"{len(after)}")
    bad += check(before[0]["key"] not in after_keys,
                 f"the REFUTED key is absent: {after_keys}")
    bad += check(before[1]["key"] in after_keys,
                 f"NOT-RUN remains for ESBMC: {after_keys}")
    return bad


def test_R2_candidate_dedup_uses_safe_normalized_text_before_fuzz():
    from solidity_path_put import (dedup_r2_specs_by_normalized_text,  # noqa: E402
                                   r2_candidates)
    specs = [{"param": "batch", "stage": 1, "kind": "typed",
              "candidate_count": 8, "vars": [{
                  "name": "slot",
                  "equals": [
                      {"id": "e0", "term": {"kind": "coord", "name": "n"}},
                      {"id": "e1", "term": {
                          "kind": "op", "op": "add",
                          "lhs": {"kind": "coord", "name": "n"},
                          "rhs": {"kind": "coord", "name": "msg.value"}}},
                      {"id": "e2", "term": {
                          "kind": "coord", "name": "_claimTopic"}},
                  ],
                  "abs": [
                      {"id": "a0", "lo": {"kind": "coord", "name": "n"},
                       "hi": {"kind": "coord", "name": "n"}},
                      {"id": "a1", "lo": {
                          "kind": "op", "op": "sub",
                          "lhs": {"kind": "coord", "name": "n"},
                          "rhs": {"kind": "coord", "name": "msg.value"}},
                       "hi": {
                          "kind": "op", "op": "sub",
                          "lhs": {"kind": "coord", "name": "n"},
                          "rhs": {"kind": "coord", "name": "msg.value"}}},
                  ],
                  "deltas": [
                      {"id": "d0", "dir": "inc",
                       "lo": {"kind": "coord", "name": "n"},
                       "hi": {"kind": "coord", "name": "n"}},
                      {"id": "d1", "dir": "inc",
                       "lo": {
                          "kind": "op", "op": "add",
                          "lhs": {"kind": "coord", "name": "msg.value"},
                          "rhs": {"kind": "coord", "name": "n"}},
                       "hi": {
                          "kind": "op", "op": "add",
                          "lhs": {"kind": "coord", "name": "msg.value"},
                          "rhs": {"kind": "coord", "name": "n"}}},
                  ],
              }]}]
    said = []
    got, dropped = dedup_r2_specs_by_normalized_text(
        specs, {"msg.value": 0}, log=said.append)
    texts = [candidate["text"] for candidate in r2_candidates(got)]
    bad = 0
    bad += check(dropped == 3, f"three normalized duplicates are removed: "
                 f"{dropped}, {texts}")
    bad += check(texts == [
        "post == n",
        "post in [n, n]",
        "post - pre in [n, n] with post >= pre",
        "post == _claimTopic",
    ], f"one representative per normalized rung survives: {texts}")
    bad += check(any("before Forge/ESBMC" in line for line in said),
                 f"the log names when the drop happened: {said}")
    return bad


def test_typed_R2_is_ONE_BATCH_and_contains_pre_plus_coordinate():
    from solidity_path_put import (propose_r2_batch,  # noqa: E402
                                   r2_terms_from_specs,
                                   r2_term_text)
    got = propose_r2_batch(
        INC_ROWS, [("amount", "uint256")], source_literals=("0", "7"),
        depth=1, var_bytes={"bal": 32},
        rendered_coords=[("amount", "num", None)], term_budget=96,
        log=lambda _line: None)
    bad = 0
    bad += check(len(got) == 1, f"one path produces one R2 query: {got}")
    if not got:
        return bad + 1
    entry = got[0]["vars"][0]
    equality_terms = {r2_term_text(item["term"])
                      for item in entry["equals"]}
    delta_terms = {r2_term_text(item["lo"])
                   for item in entry["deltas"]
                   if r2_term_text(item["lo"]) == r2_term_text(item["hi"])}
    bad += check("(pre + amount)" in equality_terms,
                 f"post == pre + amount is asked: {sorted(equality_terms)}")
    bad += check("(amount - 7)" in delta_terms,
                 f"delta expressions use the same grammar: "
                 f"{sorted(delta_terms)}")
    lookup = r2_terms_from_specs(got)
    bad += check("(pre + amount)" in lookup,
                 f"the renderer receives the structured term: {lookup}")
    return bad


def test_typed_R2_proposes_return_equals_entry_state_coord_for_getters():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    got = propose_r2_batch(
        [("return", RETLIVE, "REFUTED")],
        [], rettypes=[("", "address")],
        rendered_coords=[("state._distributor", "id", 20)],
        term_budget=8, candidate_budget=8, log=lambda _line: None)
    candidates = r2_candidates(got)
    return_entry = next((v for v in got[0]["vars"]
                         if v["name"] == "return"), None) if got else None
    bad = 0
    bad += check(return_entry is not None,
                 f"the return value is an R2 target: {got}")
    bad += check(any(c["var"] == "return"
                     and c["text"] == "return == state._distributor"
                     for c in candidates),
                 f"the getter identity candidate is asked: {candidates}")
    bad += check(return_entry is not None and not return_entry["deltas"],
                 f"return R2 never asks post/pre deltas: {return_entry}")
    return bad


def test_typed_R2_return_candidates_never_name_pre_snapshot():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    got = propose_r2_batch(
        [("return", RETLIVE, "REFUTED"),
         ("return", "return != 0", "HOLDS")],
        [("amount", "uint256")], source_literals=("10", "20"),
        rettypes=[("", "uint256")],
        rendered_coords=[("amount", "num", None)],
        term_budget=32, candidate_budget=64, log=lambda _line: None)
    candidates = r2_candidates(got)
    texts = {c["text"] for c in candidates if c["var"] == "return"}
    bad = 0
    bad += check("return == 10" in texts and "return == 20" in texts,
                 f"literal return candidates are asked: {sorted(texts)}")
    bad += check(not any("pre" in text for text in texts),
                 f"return has no entry snapshot, so no candidate names pre: "
                 f"{sorted(texts)}")
    bad += check(not any(c["var"] == "return" and "delta" in c["key"]
                         for c in candidates),
                 f"return still has no delta candidates: {candidates}")
    return bad


def test_typed_R2_bool_return_asks_equality_only():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    got = propose_r2_batch(
        [("return", RETLIVE, "REFUTED")],
        [("flag_", "bool")], rettypes=[("", "bool")],
        rendered_coords=[("flag_", "bool", 1)],
        term_budget=8, candidate_budget=8, log=lambda _line: None)
    candidates = r2_candidates(got)
    return_entry = next((v for v in got[0]["vars"]
                         if v["name"] == "return"), None) if got else None
    bad = 0
    bad += check(return_entry is not None,
                 f"the bool return value is an R2 target: {got}")
    bad += check([c["text"] for c in candidates] == ["return == flag_"],
                 f"bool return R2 asks equality only: {candidates}")
    bad += check(return_entry is not None and not return_entry["abs"]
                 and not return_entry["deltas"],
                 f"bool return has no interval/delta candidates: "
                 f"{return_entry}")
    return bad


def _return_chain_ast_path():
    def ident(name, ref):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref,
                "typeDescriptions": {"typeString": "uint256"}}

    def lit(value):
        return {"nodeType": "Literal", "kind": "number",
                "value": str(value),
                "typeDescriptions": {"typeString": f"int_const {value}"}}

    def binop(lhs, op, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def cond(value):
        return binop(ident("y", 5), "==", lit(value))

    def ret(expr):
        return {"nodeType": "Return", "expression": expr}

    def ifret(value, expr):
        return {"nodeType": "IfStatement", "condition": cond(value),
                "trueBody": {"nodeType": "Block",
                             "statements": [ret(expr)]}}

    fn = {
        "nodeType": "FunctionDefinition",
        "id": 36,
        "name": "add",
        "parameters": {"parameters": [
            {"nodeType": "VariableDeclaration", "id": 3, "name": "x",
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 5, "name": "y",
             "typeDescriptions": {"typeString": "uint256"}},
        ]},
        "returnParameters": {"parameters": [
            {"nodeType": "VariableDeclaration", "id": 8, "name": "",
             "typeDescriptions": {"typeString": "uint256"}},
        ]},
        "body": {"nodeType": "Block", "statements": [
            ifret(0, ident("x", 3)),
            ifret(1, {"nodeType": "UnaryOperation", "operator": "++",
                      "prefix": True, "subExpression": ident("x", 3),
                      "typeDescriptions": {"typeString": "uint256"}}),
            ifret(2, binop(ident("x", 3), "+", lit(2))),
            ret(binop(ident("x", 3), "+", ident("y", 5))),
        ]},
    }
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition",
        "id": 84,
        "name": "Cr1",
        "linearizedBaseContracts": [84],
        "nodes": [fn],
    }]}
    f = tempfile.NamedTemporaryFile("w", suffix=".solast", delete=False)
    json.dump(ast, f)
    f.close()
    return f.name


def test_normal_exit_retreat_bounds_prefix_increment_return():
    from solidity_path_put import UINT256_MAX  # noqa: E402
    ast = _return_chain_ast_path()
    region = {"x": ["0", str(UINT256_MAX)], "y": ["1", "1"],
              "msg.value": ["0", "0"]}
    new_region, holes, notes = normal_exit_region_retreat(
        ast, "Cr1", "add",
        [{"branch_claim": "y == 0"},
         {"branch_claim": "!(y == 1)"}],
        region, {}, [("x", "uint256"), ("y", "uint256")],
        arity=2, declaration_id=36, rettypes=[("", "uint256")])
    bad = 0
    bad += check(new_region["x"] == [0, UINT256_MAX - 1],
                 f"++x normal exit narrows x below max: {new_region}")
    bad += check(new_region["y"] == ["1", "1"],
                 f"the branch coordinate stays pinned: {new_region}")
    bad += check(holes == {}, f"no holes introduced: {holes}")
    bad += check(notes and "normal-exit arithmetic retreat" in notes[0],
                 f"the retreat is disclosed: {notes}")
    os.unlink(ast)
    return bad


def test_normal_exit_retreat_keeps_product_region_for_variable_add():
    from solidity_path_put import UINT256_MAX  # noqa: E402
    ast = _return_chain_ast_path()
    region = {"x": ["0", str(UINT256_MAX)],
              "y": ["3", str(UINT256_MAX)], "msg.value": ["0", "0"]}
    new_region, _holes, notes = normal_exit_region_retreat(
        ast, "Cr1", "add",
        [{"branch_claim": "y == 0"},
         {"branch_claim": "y == 1"},
         {"branch_claim": "y == 2"}],
        region, {}, [("x", "uint256"), ("y", "uint256")],
        arity=2, declaration_id=36, rettypes=[("", "uint256")])
    bad = 0
    bad += check(new_region["x"] == [0, 0],
                 f"x is retreated to the product-safe slice: {new_region}")
    bad += check(new_region["y"] == ["3", str(UINT256_MAX)],
                 f"y remains the wide fuzz coordinate: {new_region}")
    bad += check(notes and "`x + y`" in notes[0],
                 f"the selected return expression is named: {notes}")
    os.unlink(ast)
    return bad


def test_source_R2_prefix_increment_return_candidate_is_asked():
    from solidity_path_put import (r2_candidates,  # noqa: E402
                                   source_assignment_r2_specs)
    ast = _return_chain_ast_path()
    specs, _evidence = source_assignment_r2_specs(
        ast, "Cr1", "add", [("x", "uint256"), ("y", "uint256")],
        {}, [("x", "num", None), ("y", "num", None)],
        arity=2, declaration_id=36, rettypes=[("", "uint256")],
        maps=None, log=lambda _line: None)
    texts = {c["text"] for c in r2_candidates(specs)}
    bad = 0
    bad += check("return == (x + 1)" in texts,
                 f"prefix ++ return becomes a direct R2 candidate: {texts}")
    os.unlink(ast)
    return bad


def test_typed_R2_candidate_budget_ignores_empty_bool_return_queue():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    rows = []
    for name in ("bal0", "bal1", "bal2"):
        rows += [(name, text, verdict) for _v, text, verdict in INC_ROWS]
    rows += [("return", RETLIVE, "REFUTED"),
             ("return", "return == false", "HOLDS"),
             ("return", "return == true", "REFUTED")]
    got = propose_r2_batch(
        rows, [("amount", "uint256")],
        source_literals=("0", "1", "5", "10", "9000"),
        rettypes=[("", "bool")],
        rendered_coords=[("amount", "num", None), ("msg.value", "num", None)],
        term_budget=96, candidate_budget=128, log=lambda _line: None)
    candidates = r2_candidates(got)
    bad = 0
    bad += check(len(candidates) == 128,
                 f"the candidate cap is still exact: {len(candidates)}")
    bad += check("return" not in {c["var"] for c in candidates},
                 f"empty bool return target is omitted before budget trim: "
                 f"{candidates[:6]}")
    return bad


def test_path_decision_guard_renders_mapping_slot_relation():
    cond = path_condition_from_branch_claim("!(balances[msg.sender] < amount)")
    bad = 0
    bad += check(cond == ("balances[msg.sender]", "<", "amount"),
                 f"path condition recovered from negated branch claim: {cond}")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(balances[msg.sender] < amount)"}],
        {"state.balances[msg.sender]": "_pre_balances_msg_sender",
         "amount": "amount"})
    bad += check(lines == [("!(balances[msg.sender] < amount)",
                            "    vm.assume(_pre_balances_msg_sender < amount);")],
                 f"mapping-slot path guard rendered: {lines}")
    bad += check(skipped == [], f"nothing skipped: {skipped}")
    return bad


def test_path_decision_guard_negates_plain_branch_claim():
    bad = 0
    bad += check(path_condition_from_branch_claim("msg.value == 0") ==
                 ("msg.value", "!=", "0"),
                 "plain branch claim is negated into the walked condition")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "msg.value == 0"}],
        {"msg.value": "p_msg_value"})
    bad += check(lines == [("msg.value == 0",
                            "    vm.assume(p_msg_value != 0);")],
                 f"plain branch guard rendered: {lines}")
    bad += check(skipped == [], f"nothing skipped: {skipped}")
    return bad


def test_path_decision_guard_handles_double_negated_branch_claim():
    bad = 0
    bad += check(path_condition_from_branch_claim("!(!(msg.sender == owner))") ==
                 ("msg.sender", "!=", "owner"),
                 "double-negated branch claim is unwrapped before parsing")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(!(msg.sender == owner))"}],
        {"msg.sender": "p_msg_sender", "state.owner": "_pre_owner"})
    bad += check(lines == [("!(!(msg.sender == owner))",
                            "    vm.assume(p_msg_sender != _pre_owner);")],
                 f"double-negated path guard rendered: {lines}")
    bad += check(skipped == [], f"nothing skipped: {skipped}")
    return bad


def test_path_decision_guard_splits_safe_boolean_shapes():
    bad = 0
    bad += check(path_conditions_from_branch_claim(
        "!(_chosenNumber > 0 && _chosenNumber <= 100)") == [
            ("_chosenNumber", ">", "0"),
            ("_chosenNumber", "<=", "100"),
        ], "negated conjunction means this path walked both conjuncts")
    bad += check(path_conditions_from_branch_claim(
        "!((_chosenNumber > 0 && _chosenNumber <= 100))") == [
            ("_chosenNumber", ">", "0"),
            ("_chosenNumber", "<=", "100"),
        ], "extra outer parentheses do not hide the top-level conjunction")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(_chosenNumber > 0 && _chosenNumber <= 100)"}],
        {"_chosenNumber": "chosen"})
    bad += check(lines == [
        ("!(_chosenNumber > 0 && _chosenNumber <= 100)",
         "    vm.assume(chosen > 0);"),
        ("!(_chosenNumber > 0 && _chosenNumber <= 100)",
         "    vm.assume(chosen <= 100);"),
    ] and skipped == [],
        f"safe conjunction path guard renders both assumes: {lines}, {skipped}")
    bad += check(path_conditions_from_branch_claim(
        "proposalId > proposalCount || proposalId == 0") == [
            ("proposalId", "<=", "proposalCount"),
            ("proposalId", "!=", "0"),
        ], "plain disjunction means this path walked both negated arms")
    bad += check(path_conditions_from_branch_claim(
        "msg.sender != InstanceBuyer && msg.sender != InstanceOwner") is None,
        "a disjunctive walked guard is not flattened into conjunctions")
    bad += check(path_conditions_from_branch_claim(
        "(msg.sender != InstanceBuyer && msg.sender != InstanceOwner)") is None,
        "outer parentheses keep a disjunctive walked guard grouped")
    lines, skipped = path_decision_assumes(
        [{"branch_claim":
          "msg.sender != InstanceBuyer && msg.sender != InstanceOwner"}],
        {"msg.sender": "sender", "state.InstanceBuyer": "_pre_buyer",
         "state.InstanceOwner": "_pre_owner"})
    bad += check(lines == [
        ("msg.sender != InstanceBuyer && msg.sender != InstanceOwner",
         "    vm.assume((sender == _pre_buyer || sender == _pre_owner));")
    ] and skipped == [],
        f"false conjunction renders as a disjunctive assume: "
        f"{lines}, {skipped}")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(msg.sender == owner || members[msg.sender] == 1)"}],
        {"msg.sender": "sender", "state.owner": "_pre_owner",
         "state.members[msg.sender]": "_pre_member"})
    bad += check(lines == [
        ("!(msg.sender == owner || members[msg.sender] == 1)",
         "    vm.assume((sender == _pre_owner || _pre_member == 1));")
    ] and skipped == [],
        f"true disjunction renders as one OR assume: {lines}, {skipped}")
    return bad


def test_path_decision_guard_renders_unary_bool_mapping_relation():
    bad = 0
    bad += check(path_condition_from_branch_claim("!(!_isBlackListedBot[account])") ==
                 ("_isBlackListedBot[account]", "==", "0"),
                 "double-negated unary bool claim becomes a false guard")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(!_isBlackListedBot[account])"}],
        {"state._isBlackListedBot[account]": "_pre_blacklisted_account"})
    bad += check(lines == [
        ("!(!_isBlackListedBot[account])",
         "    vm.assume(_pre_blacklisted_account == 0);")
    ], f"unary bool mapping guard rendered: {lines}")
    bad += check(skipped == [], f"nothing skipped: {skipped}")
    return bad


def test_path_decision_guard_negates_plain_unary_bool_claim():
    bad = 0
    bad += check(path_condition_from_branch_claim("paused") ==
                 ("paused", "==", "0"),
                 "plain bool branch claim is negated into the walked condition")
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "paused"}],
        {"state.paused": "_pre_paused"})
    bad += check(lines == [("paused", "    vm.assume(_pre_paused == 0);")],
                 f"plain unary bool guard rendered: {lines}")
    bad += check(skipped == [], f"nothing skipped: {skipped}")
    return bad


def test_path_guard_materializes_state_coord_without_oracle_rung():
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2,
        "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={"msg.value": 0}, params=PARAMS, emitted=em,
        case=case, layout=LAYOUT, ladder_rows=[], notes=notes,
        path_decisions=[{"branch_claim": "!(msg.sender == owner)"}])
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced: {notes}")
    bad += check("uint256 _pre_owner = (uint256(vm.load(address(c0)" in text,
                 "path guard materializes owner even without an oracle rung")
    bad += check("vm.assume(uint256(uint160(0)) == _pre_owner);" in text,
                 "the state-backed path guard is rendered")
    bad += check(stats["path_guard_assumes"] == 1
                 and stats["path_guard_skipped"] == [],
                 f"the guard is counted as established: {stats}")
    return bad


def test_path_decision_guard_skips_true_constant_relation():
    lines, skipped = path_decision_assumes(
        [{"branch_claim": "!(msg.value == 0)"}],
        {"msg.value": "0"})
    bad = 0
    bad += check(lines == [], f"true constant guard is not emitted: {lines}")
    bad += check(skipped == [], f"true constant guard is not reported lost: {skipped}")
    return bad


def test_typed_R2_term_budget_is_VISIBLE_not_a_second_query():
    from solidity_path_put import propose_r2_batch  # noqa: E402
    said = []
    got = propose_r2_batch(
        INC_ROWS, [("amount", "uint256")], source_literals=("0", "1", "2"),
        depth=1, var_bytes={"bal": 32},
        rendered_coords=[("amount", "num", None)], term_budget=3,
        log=said.append)
    bad = 0
    bad += check(len(got) == 1, f"truncation still sends one batch: {got}")
    bad += check(any("NOT ASKED" in line for line in said),
                 f"the omitted suffix is named: {said}")
    return bad


def test_typed_R2_candidate_budget_caps_claims_and_shares_them():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    rows = INC_ROWS + [("other", text, verdict)
                       for _var, text, verdict in INC_ROWS]
    said = []
    got = propose_r2_batch(
        rows, [("amount", "uint256")], source_literals=("0", "7"),
        depth=1, var_bytes={"bal": 32, "other": 32},
        rendered_coords=[("amount", "num", None)], term_budget=96,
        candidate_budget=7, log=said.append)
    candidates = r2_candidates(got)
    bad = 0
    bad += check(len(candidates) == 7,
                 f"the solver claim cap is exact: {len(candidates)}")
    bad += check({candidate["var"] for candidate in candidates}
                 == {"bal", "other"},
                 f"round-robin prevents first-variable starvation: "
                 f"{candidates}")
    bad += check(any("NOT ASKED" in line and "candidate budget" in line
                     for line in said),
                 f"the omitted claim suffix is explicit: {said}")
    return bad


def test_typed_R2_candidate_budget_reaches_every_variable_before_second_laps():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    rows = [(f"v{i:02d}", text, verdict) for i in range(50)
            for _var, text, verdict in INC_ROWS]
    got = propose_r2_batch(
        rows, [("amount", "uint256")], source_literals=("0", "7"),
        depth=1, var_bytes={f"v{i:02d}": 32 for i in range(50)},
        rendered_coords=[("amount", "num", None)], term_budget=96,
        candidate_budget=128, log=lambda _line: None)
    candidates = r2_candidates(got)
    represented = {candidate["var"] for candidate in candidates}
    bad = 0
    bad += check(len(candidates) == 128,
                 f"the global cap remains exact: {len(candidates)}")
    bad += check(represented == {f"v{i:02d}" for i in range(50)},
                 f"every variable receives a first candidate: {represented}")
    forge_prefix = {candidate["var"] for candidate in candidates[:50]}
    bad += check(forge_prefix == {f"v{i:02d}" for i in range(50)},
                 f"an independently smaller Forge cap is fair too: "
                 f"{forge_prefix}")
    return bad


def test_skipped_forge_R2_accounting_is_complete_and_conservative():
    from solidity_path_put import skipped_forge_r2_evidence  # noqa: E402
    specs = [{"vars": [{"name": "bal", "equals": [{
        "id": "e0", "term": {"kind": "literal", "value": "7"}}],
                       "abs": [], "deltas": []}]}]
    got = skipped_forge_r2_evidence(
        specs, 128, "rollback path has no observable R2 post-state", 256)
    bad = 0
    bad += check(got["requested"] == got["not_run"] == 1,
                 f"every skipped candidate is NOT-RUN: {got}")
    bad += check(got["requested"] == (got["refuted"]
                                      + got["not_refuted"]
                                      + got["not_run"]),
                 f"verdict counts conserve requested: {got}")
    bad += check(got["selected"] == 1 and got["rendered"] == got["ran"] == 0,
                 f"selection and execution counts stay distinct: {got}")
    bad += check(got["candidates"][0]["verdict"] == "NOT-RUN",
                 f"the per-candidate evidence agrees: {got}")
    return bad


def test_partial_ladder_R2_skip_requires_a_rendered_strict_oracle():
    rows = [("bal", "post >= pre", "HOLDS")]
    good = {"fuzz_params": 1, "asserts": 1, "state_asserts": 1,
            "return_asserts": 0, "oracle_classes": ["R1"]}
    no_fuzz = {"fuzz_params": 0, "asserts": 1, "state_asserts": 1,
               "return_asserts": 0, "oracle_classes": ["R1"]}
    no_assert = {"fuzz_params": 1, "asserts": 0, "state_asserts": 0,
                 "return_asserts": 0, "oracle_classes": []}
    only_r0 = {"fuzz_params": 1, "asserts": 1, "state_asserts": 0,
               "return_asserts": 0, "oracle_classes": ["R0"]}
    bad = 0
    bad += check(partial_ladder_already_has_strict_oracle(rows, None, good),
                 "a partial ladder with a fuzz oracle can skip the larger R2 "
                 "batch")
    bad += check(not partial_ladder_already_has_strict_oracle(rows,
                                                              (1, 1, 0, 0, 0),
                                                              good),
                 "a complete ladder still gets the normal R2 chance")
    bad += check(not partial_ladder_already_has_strict_oracle([], None, good),
                 "no salvaged row means no R1 basis for skipping R2")
    bad += check(not partial_ladder_already_has_strict_oracle(rows, None,
                                                              no_fuzz),
                 "a non-fuzz point oracle is not a strict PUT")
    bad += check(not partial_ladder_already_has_strict_oracle(rows, None,
                                                              no_assert),
                 "a reachability-only fuzz body cannot skip R2")
    bad += check(not partial_ladder_already_has_strict_oracle(rows, None,
                                                              only_r0),
                 "an R0-only PUT must not freeze a no-R1/R2 result")
    return bad


def test_oracle_class_metadata_keeps_R0_R1_R2_apart():
    bad = 0
    bad += check(oracle_classes_for_rung("post >= pre") == ["R1"],
                 "plain pre/post ordering is R1")
    bad += check(oracle_classes_for_rung("post == amount") == ["R2"],
                 "exact endpoint over an input is R2")
    bad += check(oracle_classes_for_rung(
        "post - pre in [amount, amount] with post >= pre") == ["R1", "R2"],
        "a delta bound with a direction records the R1/R2 combination")
    bad += check(oracle_classes_for_rung("return == 1") == ["R2"],
                 "return endpoint assertions are R2")
    details = [{"classes": ["R2", "R1"]}, {"classes": ["R0"]},
               {"classes": ["R1"]}]
    bad += check(oracle_class_summary(details) == ["R0", "R1", "R2"],
                 "summary is stable, unique, and ordered")
    return bad


def test_typed_R2_omits_bool_without_a_bool_endpoint():
    from solidity_path_put import propose_r2_batch  # noqa: E402
    said = []
    got = propose_r2_batch(
        [("flag", "post == pre", "REFUTED"),
         ("flag", "post != pre", "HOLDS")],
        [], source_literals=("0", "1"), var_bytes={"flag": 1},
        rendered_coords=[], log=said.append)
    bad = 0
    bad += check(got == [], f"no structured bool claim is asked: {got}")
    bad += check(any("ordering-capable unsigned scalar" in line
                     for line in said),
                 f"the omission is named rather than silent: {said}")
    return bad


def test_typed_R2_proposes_bool_equality_to_bool_coordinate():
    from solidity_path_put import propose_r2_batch, r2_candidates  # noqa: E402
    got = propose_r2_batch(
        [("flag", "post == pre", "REFUTED"),
         ("flag", "post != pre", "HOLDS")],
        [("flag_", "bool")], var_bytes={"flag": 1},
        rendered_coords=[("flag_", "bool", 1)], log=lambda _line: None)
    candidates = r2_candidates(got)
    bad = 0
    bad += check(len(got) == 1, f"one bool R2 batch: {got}")
    bad += check(candidates == [{
        "key": "s0:v0:equals:e0",
        "var": "flag",
        "text": "post == flag_",
    }], f"only equality to the bool coordinate is asked: {candidates}")
    entry = got[0]["vars"][0] if got else {}
    bad += check(not entry.get("abs") and not entry.get("deltas"),
                 f"bool R2 has no interval or delta candidates: {entry}")
    return bad


def test_a_bool_region_parameter_is_lifted_and_can_feed_R2():
    bool_emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Flagger} from "./Flagger.sol";
contract FlaggerCovTest is Test {
  Flagger c0;
  function setUp() public {
    c0 = new Flagger();
  }
  // claim: sol:@C@Flagger@F@set#10:path:2
  function test_cov_0() public {
    // [asserted] path exits normally; a revert fails the test
    c0.set(false);
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as out:
        out.write(bool_emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Flagger@F@set#10", 2)
    notes = []
    r2_terms = {"flag_": {"kind": "coord", "name": "flag_"}}
    put, stats = build_put(
        "Flagger", "set", 2, 1, "sol:@C@Flagger@F@set#10",
        region={"flag_": (0, 1)}, holes={}, pins={},
        params=[("flag_", "bool")], emitted=em, case=case,
        layout={"flag": (0, 0, 1)},
        ladder_rows=[("flag", "post == flag_", "HOLDS")],
        notes=notes, r2_terms=r2_terms)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a bool PUT is produced: {notes}")
    bad += check("function test_put_Flagger_set_path2(bool flag_) public"
                 in text,
                 "the bool coordinate is lifted into the fuzz signature")
    bad += check("bound(flag_" not in text,
                 "a full bool domain needs no numeric bound() cast")
    bad += check("assertEq(_post_flag, (flag_ ? uint256(1) : uint256(0))"
                 in text,
                 "the bool R2 endpoint is rendered as the storage bit")
    bad += check(stats["fuzz_params"] == 1 and stats["asserts"] == 1,
                 f"the bool PUT is parameterized and has one oracle: {stats}")
    bad += check(stats["rendered_width"] == {"flag_": 2}
                 and stats["wide_fuzz_coords"] == ["flag_"],
                 f"stats record rendered fuzz width, not just region width: "
                 f"{stats}")
    return bad


def test_a_bool_region_parameter_can_feed_bool_return_R2():
    bool_emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Echo} from "./Echo.sol";
contract EchoCovTest is Test {
  Echo c0;
  function setUp() public {
    c0 = new Echo();
  }
  // claim: sol:@C@Echo@F@g#10:path:3
  function test_cov_0() public {
    // [asserted] path exits normally; a revert fails the test
    c0.g(false);
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as out:
        out.write(bool_emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Echo@F@g#10", 3)
    notes = []
    r2_terms = {"a": {"kind": "coord", "name": "a"}}
    put, stats = build_put(
        "Echo", "g", 3, 12, "sol:@C@Echo@F@g#10",
        region={"a": (0, 1)}, holes={}, pins={},
        params=[("a", "bool")], emitted=em, case=case, layout={},
        ladder_rows=[("return", RETLIVE, "REFUTED"),
                     ("return", "return == a", "HOLDS")],
        notes=notes, rettypes=[("", "bool")], r2_terms=r2_terms)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a bool return PUT is produced: {notes}")
    bad += check("function test_put_Echo_g_path3(bool a) public" in text,
                 "the bool coordinate is lifted into the fuzz signature")
    bad += check('assertEq(_put_ret, a, "return: return == a");' in text,
                 "bool return R2 compares against the bool parameter spelling")
    bad += check("(a ? uint256(1) : uint256(0))" not in text,
                 "the storage-bit spelling is not used for bool return R2")
    bad += check(stats["return_asserts"] == 1 and stats["fuzz_params"] == 1,
                 f"the bool PUT has one return oracle and one fuzz param: "
                 f"{stats}")
    return bad


def test_a_fixed_bytes_region_parameter_is_lifted_via_uint_input():
    """Fixed bytes are fuzzed as same-width integers and cast at the call.

    Foundry's `bound()` is numeric, but the contract must still receive a
    `bytesN` ABI value. The raw integer remains the R2 absolute endpoint, so
    `post == key_` can render without putting bytesN into a delta grammar.
    """
    bytes_emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Keyed} from "./Keyed.sol";
contract KeyedCovTest is Test {
  Keyed c0;
  function setUp() public {
    c0 = new Keyed();
  }
  // claim: sol:@C@Keyed@F@take#11:path:4
  function test_cov_0() public {
    // [asserted] path exits normally; a revert fails the test
    c0.take(bytes4(0x00000000));
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as out:
        out.write(bytes_emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Keyed@F@take#11", 4)
    notes = []
    r2_terms = {"key_": {"kind": "coord", "name": "key_"}}
    put, stats = build_put(
        "Keyed", "take", 4, 1, "sol:@C@Keyed@F@take#11",
        region={"key_": (0x12, 0x34)}, holes={}, pins={},
        params=[("key_", "bytes4")], emitted=em, case=case,
        layout={"last": (0, 0, 4)},
        ladder_rows=[("last", "post == key_", "HOLDS")],
        notes=notes, r2_terms=r2_terms)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a bytes4 PUT is produced: {notes}")
    bad += check("function test_put_Keyed_take_path4(uint32 key_) public"
                 in text,
                 "the fuzz signature uses a boundable uint32")
    bad += check("key_ = uint32(bound(uint256(key_), 18, 52));" in text,
                 "the numeric fuzz input is bounded to the certified region")
    bad += check("c0.take(bytes4(key_));" in text,
                 "the unit call receives the ABI-level bytes4 value")
    bad += check("assertEq(_post_last, key_" in text,
                 "the absolute R2 endpoint uses the raw same-width integer")
    bad += check(stats["rendered_width"] == {"key_": 35}
                 and stats["wide_fuzz_coords"] == ["key_"],
                 f"rendered width is tracked for fixed bytes: {stats}")
    return bad


def test_source_R2_atoms_are_scoped_to_the_unit_and_contract_chain():
    from solidity_path_put import source_r2_literals  # noqa: E402
    ast = {"nodeType": "SourceUnit", "nodes": [
        {"nodeType": "ContractDefinition", "name": "Other", "id": 2,
         "linearizedBaseContracts": [2], "nodes": [
             {"nodeType": "VariableDeclaration", "id": 20, "name": "NO",
              "constant": True, "value": {"nodeType": "Literal",
                                             "kind": "number", "value": "99"}}]},
        {"nodeType": "ContractDefinition", "name": "C", "id": 1,
         "linearizedBaseContracts": [1], "nodes": [
             {"nodeType": "VariableDeclaration", "id": 10, "name": "K",
              "constant": True, "value": {"nodeType": "Literal",
                                             "kind": "number", "value": "3"}},
             {"nodeType": "FunctionDefinition", "id": 11, "name": "f",
              "parameters": {"parameters": []},
              "body": {"nodeType": "Block", "statements": [
                  {"nodeType": "Literal", "kind": "number", "value": "7"}]}}
         ]}
    ]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        values, evidence = source_r2_literals(path, "C", "f", arity=0)
    finally:
        os.unlink(path)
    bad = 0
    bad += check(values == ["3", "7"],
                 f"unit literal and own constant only: {values}")
    bad += check(any("constant K" in line for line in evidence),
                 f"constant provenance is retained: {evidence}")
    return bad


def test_source_R2_assignment_candidates_are_small_setter_queries():
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "x",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "ignored",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "ready",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "limit",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint16"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "timed",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "setX",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}},
                 {"id": 22, "name": "other",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "123:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 10,
                                      "name": "x"},
                     "rightHandSide": {"nodeType": "Identifier",
                                       "referencedDeclaration": 21,
                                       "name": "amount"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "234:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "ready"},
                     "rightHandSide": {"nodeType": "Literal",
                                       "kind": "bool",
                                       "value": "true"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "345:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 13,
                                      "name": "limit"},
                     "rightHandSide": {"nodeType": "Literal",
                                       "kind": "number",
                                       "value": "7"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "367:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 14,
                                      "name": "timed"},
                     "rightHandSide": {"nodeType": "Literal",
                                       "kind": "number",
                                       "value": "2",
                                       "subdenomination": "seconds"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "456:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 11,
                                      "name": "ignored"},
                     "rightHandSide": {"nodeType": "Identifier",
                                       "referencedDeclaration": 22,
                                       "name": "other"}}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "setX", [("amount", "uint256"), ("other", "uint256")],
            {"x": (0, 0, 32), "ignored": (1, 0, 32),
             "ready": (2, 0, 1), "limit": (3, 0, 2),
             "timed": (4, 0, 32)},
            [("amount", "num", None)], arity=2, log=lambda _msg: None)
        none, none_evidence = source_assignment_r2_specs(
            path, "C", "setX", [("amount", "uint256"), ("other", "uint256")],
            {"x": (0, 0, 32), "ignored": (1, 0, 32),
             "ready": (2, 0, 1), "limit": (3, 0, 2),
             "timed": (4, 0, 32)},
            [], arity=2, log=lambda _msg: None)
    finally:
        os.unlink(path)
    bad = 0
    bad += check(len(specs) == 1, f"one small source spec: {specs}")
    vars_ = specs[0]["vars"] if specs else []
    var = next((item for item in vars_ if item.get("name") == "x"), {})
    bad += check(var.get("name") == "x",
                 f"the assigned state variable is targeted: {var}")
    bad += check(var.get("equals", [{}])[0].get("term") ==
                 {"kind": "coord", "name": "amount"},
                 f"the endpoint is the rendered parameter: {var}")
    bvar = next((item for item in vars_ if item.get("name") == "ready"), {})
    bad += check(bvar.get("equals", [{}])[0].get("term") ==
                 {"kind": "literal", "value": "1"},
                 f"the bool literal endpoint is encoded as a verifier decimal: "
                 f"{bvar}")
    nvar = next((item for item in vars_ if item.get("name") == "limit"), {})
    bad += check(nvar.get("equals", [{}])[0].get("term") ==
                 {"kind": "literal", "value": "7"},
                 f"the numeric literal endpoint is encoded as a verifier "
                 f"decimal: {nvar}")
    bad += check(not any(item.get("name") == "timed" for item in vars_),
                 f"subdenominated source literals are not unitless R2 atoms: "
                 f"{vars_}")
    bad += check(specs[0].get("candidate_count") == 3 if specs else False,
                 f"only the source assignment candidate is asked: {specs}")
    bad += check(any("x: post == amount" in line for line in evidence),
                 f"the source provenance is recorded: {evidence}")
    bad += check(any("ready: post == true" in line for line in evidence),
                 f"the bool source provenance is recorded: {evidence}")
    bad += check(any("limit: post == 7" in line for line in evidence),
                 f"the numeric source provenance is recorded: {evidence}")
    bad += check(len(none) == 1 and none[0].get("candidate_count") == 2,
                 f"literal assignments remain even when the parameter is not "
                 f"rendered: {none}, {none_evidence}")
    return bad


def test_source_R2_self_updates_prioritize_delta_queries():
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "limit",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint16"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "timed",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "signedSink",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "bump",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}},
                 {"id": 22, "name": "signedAmount",
                  "typeDescriptions": {"typeString": "int256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "100:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 10,
                                      "name": "bal"},
                     "rightHandSide": {"nodeType": "Identifier",
                                       "referencedDeclaration": 21,
                                       "name": "amount"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "120:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 10,
                                      "name": "bal"},
                     "rightHandSide": {"nodeType": "BinaryOperation",
                                       "operator": "+",
                                       "leftExpression": {
                                           "nodeType": "Identifier",
                                           "referencedDeclaration": 21,
                                           "name": "amount"},
                                       "rightExpression": {
                                           "nodeType": "Identifier",
                                           "referencedDeclaration": 10,
                                           "name": "bal"}}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "140:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 11,
                                      "name": "limit"},
                     "rightHandSide": {"nodeType": "BinaryOperation",
                                       "operator": "-",
                                       "leftExpression": {
                                           "nodeType": "Identifier",
                                           "referencedDeclaration": 11,
                                           "name": "limit"},
                                       "rightExpression": {
                                           "nodeType": "Literal",
                                           "kind": "number",
                                           "value": "7"}}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "160:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "timed"},
                     "rightHandSide": {"nodeType": "Literal",
                                       "kind": "number",
                                       "value": "2",
                                       "subdenomination": "seconds"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "180:10:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 13,
                                      "name": "signedSink"},
                     "rightHandSide": {"nodeType": "Identifier",
                                       "referencedDeclaration": 22,
                                       "name": "signedAmount"}}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "bump", [("amount", "uint256"),
                                ("signedAmount", "int256")],
            {"bal": (0, 0, 32), "limit": (1, 0, 2),
             "timed": (2, 0, 32), "signedSink": (3, 0, 32)},
            [("amount", "num", None), ("signedAmount", "num", None)],
            arity=2, log=lambda _msg: None)
        none, none_evidence = source_assignment_r2_specs(
            path, "C", "bump", [("amount", "uint256"),
                                ("signedAmount", "int256")],
            {"bal": (0, 0, 32), "limit": (1, 0, 2),
             "timed": (2, 0, 32), "signedSink": (3, 0, 32)},
            [], arity=2, log=lambda _msg: None)
    finally:
        os.unlink(path)
    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    bal_deltas = entries.get("bal", {}).get("deltas", [])
    limit_deltas = entries.get("limit", {}).get("deltas", [])
    bad = 0
    bad += check(len(specs) == 1, f"one source spec: {specs}")
    bad += check(len(bal_deltas) == 1 and bal_deltas[0]["dir"] == "inc",
                 f"compound and binary self-add deduplicate: {bal_deltas}")
    bad += check(bal_deltas and bal_deltas[0]["lo"] ==
                 {"kind": "coord", "name": "amount"},
                 f"the inc delta is the rendered unsigned parameter: "
                 f"{bal_deltas}")
    bad += check(len(limit_deltas) == 1 and limit_deltas[0]["dir"] == "dec",
                 f"self-subtract literal becomes a dec delta: {limit_deltas}")
    bad += check(limit_deltas and limit_deltas[0]["lo"] ==
                 {"kind": "literal", "value": "7"},
                 f"the dec delta uses the unitless literal: {limit_deltas}")
    bad += check("timed" not in entries and "signedSink" not in entries,
                 f"subdenominated literals and signed params are skipped: "
                 f"{entries}")
    bad += check(specs[0].get("candidate_count") == 2 if specs else False,
                 f"candidate_count counts delta candidates: {specs}")
    bad += check(any("bal: post - pre == amount" in line
                     for line in evidence),
                 f"the inc provenance is recorded: {evidence}")
    bad += check(any("limit: pre - post == 7" in line
                     for line in evidence),
                 f"the dec provenance is recorded: {evidence}")
    bad += check(len(none) == 1 and none[0].get("candidate_count") == 1,
                 f"literal self-updates remain without rendered params: "
                 f"{none}, {none_evidence}")
    return bad


def test_source_R2_mapping_slot_updates_prioritize_exact_slot_queries():
    from solidity_path_put import (r2_candidates, r2_term_text,  # noqa: E402
                                   source_assignment_r2_specs)
    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}

    def ident(ref, name, ty=None):
        out = {"nodeType": "Identifier", "referencedDeclaration": ref,
               "name": name}
        if ty is not None:
            out["typeDescriptions"] = {"typeString": ty}
        return out

    def index(base, key, ty="uint256"):
        return {"nodeType": "IndexAccess", "baseExpression": base,
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def method(base, name, arg, extra_args=None):
        return {"nodeType": "FunctionCall", "kind": "functionCall",
                "expression": {"nodeType": "MemberAccess",
                               "memberName": name,
                               "expression": base},
                "arguments": [arg] + list(extra_args or []),
                "typeDescriptions": {"typeString": "uint256"}}

    balances_sender = index(ident(10, "balances"), msg_sender)
    allowance_sender = index(ident(11, "allowance"), msg_sender,
                             "mapping(address => uint256)")
    allowance_sender_spender = index(allowance_sender, ident(22, "spender"))
    balances_owner = index(ident(10, "balances"), ident(13, "owner"))
    bad_key = index(ident(12, "badKeyed"), ident(22, "spender"))
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "balances",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "allowance",
             "stateVariable": True,
             "typeDescriptions": {
                 "typeString": "mapping(address => mapping(address => uint256))"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "badKeyed",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(uint256 => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "move",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}},
                 {"id": 22, "name": "spender",
                  "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "100:10:0",
                     "leftHandSide": balances_sender,
                     "rightHandSide": ident(21, "amount")}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "120:10:0",
                     "leftHandSide": allowance_sender_spender,
                     "rightHandSide": method(
                         allowance_sender_spender, "sub",
                         ident(21, "amount"),
                         [{"nodeType": "Literal", "kind": "string",
                           "value": "ERC20: transfer amount exceeds allowance"}])}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "140:10:0",
                     "leftHandSide": allowance_sender_spender,
                     "rightHandSide": ident(21, "amount")}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "150:10:0",
                     "leftHandSide": balances_owner,
                     "rightHandSide": ident(21, "amount")}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "160:10:0",
                     "leftHandSide": bad_key,
                     "rightHandSide": ident(21, "amount")}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "move", [("amount", "uint256"),
                                ("spender", "address")],
            {"owner": (3, 0, 20)},
            [("amount", "num", None)], arity=2,
            maps={"balances": (0, "address", 32, 0, "balances", None),
                  "allowance": (1, ("address", "address"), 32, 0,
                                "allowance", None),
                  "badKeyed": (2, "uint256", 32, 0, "badKeyed", None)},
            log=lambda _msg: None)
        alias_maps = prefer_esbmc_mapping_aliases(add_esbmc_mapping_aliases(
            {"allowance": (1, ("address", "address"), 32, 0,
                           "allowance", None)},
            {"allowance": "allowance$11"}))
        alias_specs, _alias_evidence = source_assignment_r2_specs(
            path, "C", "move", [("amount", "uint256"),
                                ("spender", "address")],
            {}, [("amount", "num", None)], arity=2, maps=alias_maps,
            log=lambda _msg: None)
    finally:
        os.unlink(path)
    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    alias_entries = ({entry["name"]: entry
                      for entry in alias_specs[0]["vars"]}
                     if alias_specs else {})
    bal_deltas = entries.get("balances[msg.sender]", {}).get("deltas", [])
    bal_owner_deltas = entries.get(
        "balances[state.owner]", {}).get("deltas", [])
    allow = entries.get("allowance[msg.sender][spender]", {})
    allow_deltas = allow.get("deltas", [])
    allow_equals = allow.get("equals", [])
    candidates = r2_candidates(specs)
    bad = 0
    bad += check(len(specs) == 1, f"one source spec: {specs}")
    bad += check(len(bal_deltas) == 1 and bal_deltas[0]["dir"] == "inc",
                 f"msg.sender-keyed mapping increment is mined: {bal_deltas}")
    bad += check(bal_deltas and bal_deltas[0]["lo"] ==
                 {"kind": "coord", "name": "amount"},
                 f"mapping inc delta uses the rendered amount: {bal_deltas}")
    bad += check(len(bal_owner_deltas) == 1
                 and bal_owner_deltas[0]["dir"] == "inc",
                 f"state-keyed mapping increment is mined: "
                 f"{bal_owner_deltas}")
    bad += check(bal_owner_deltas and bal_owner_deltas[0]["lo"] ==
                 {"kind": "coord", "name": "amount"},
                 f"state-keyed mapping inc delta uses the rendered amount: "
                 f"{bal_owner_deltas}")
    bad += check(len(allow_deltas) == 1 and allow_deltas[0]["dir"] == "dec",
                 f"nested mapping self-subtract is mined: {allow_deltas}")
    bad += check(allow_deltas and allow_deltas[0]["lo"] ==
                 {"kind": "coord", "name": "amount"},
                 f"nested mapping dec delta uses the rendered amount: "
                 f"{allow_deltas}")
    allow_equal_terms = [r2_term_text(item["term"]) for item in allow_equals]
    bad += check(allow_equal_terms == [
        "(state.allowance[msg.sender][spender] - amount)", "amount"
    ], f"nested mapping direct and self-subtract equalities are mined: "
        f"{allow_equals}")
    bad += check("badKeyed[spender]" not in entries,
                 f"key type mismatches are skipped rather than guessed: "
                 f"{entries}")
    bad += check(specs[0].get("candidate_count") == 5 if specs else False,
                 f"candidate_count includes mapping slot candidates: {specs}")
    bad += check(any("balances[msg.sender]: post - pre == amount" in line
                     for line in evidence),
                 f"mapping increment provenance is recorded: {evidence}")
    bad += check(any("balances[state.owner]: post - pre == amount" in line
                     for line in evidence),
                 f"state-keyed mapping provenance is recorded: {evidence}")
    bad += check(any(item["var"] == "allowance[msg.sender][spender]"
                     and item["text"] == "post == amount"
                     for item in candidates),
                 f"mapping direct-set R2 candidate is renderable: {candidates}")
    bad += check(any(item["var"] == "allowance[msg.sender][spender]"
                     and item["text"] ==
                     "post == (state.allowance[msg.sender][spender] - amount)"
                     for item in candidates),
                 f"mapping self-subtract equality is renderable: {candidates}")
    bad += check("allowance$11[msg.sender][spender]" in alias_entries,
                 f"source R2 mapping candidates use ESBMC alias names when "
                 f"the query map has no source key: {alias_entries}")
    return bad


def test_source_R2_helper_mapping_increment_unwraps_tuple_argument():
    from solidity_path_put import (r2_candidates, source_assignment_r2_specs  # noqa: E402
                                  )

    def ident(ref, name, ty=None):
        out = {"nodeType": "Identifier", "referencedDeclaration": ref,
               "name": name}
        if ty is not None:
            out["typeDescriptions"] = {"typeString": ty}
        return out

    msg_sender = {
        "nodeType": "MemberAccess", "memberName": "sender",
        "expression": {"nodeType": "Identifier", "name": "msg"},
        "typeDescriptions": {"typeString": "address"}}

    def index(base, key, ty="uint256"):
        return {"nodeType": "IndexAccess", "baseExpression": base,
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def allowance(owner, spender):
        first = index(ident(10, "allowance"), owner,
                      "mapping(address => uint256)")
        return index(first, spender)

    amount_arg = {
        "nodeType": "BinaryOperation", "operator": "+",
        "leftExpression": allowance(msg_sender, ident(22, "spender",
                                                      "address")),
        "rightExpression": {
            "nodeType": "TupleExpression",
            "components": [ident(21, "amount", "uint256")],
            "typeDescriptions": {"typeString": "uint256"}},
        "typeDescriptions": {"typeString": "uint256"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "allowance", "stateVariable": True,
             "typeDescriptions": {
                 "typeString": "mapping(address => mapping(address => uint256))"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "inc",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}},
                 {"id": 22, "name": "spender",
                  "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "FunctionCall", "kind": "functionCall",
                     "expression": ident(30, "_set"),
                     "arguments": [msg_sender, ident(22, "spender",
                                                     "address"), amount_arg]}}]}},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "_set",
             "parameters": {"parameters": [
                 {"id": 31, "name": "owner",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 32, "name": "spender",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 33, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "200:10:0",
                     "leftHandSide": allowance(ident(31, "owner", "address"),
                                               ident(32, "spender", "address")),
                     "rightHandSide": ident(33, "amount", "uint256")}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        maps = prefer_esbmc_mapping_aliases(add_esbmc_mapping_aliases(
            {"allowance": (1, ("address", "address"), 32, 0,
                           "allowance", None)},
            {"allowance": "allowance$10"}))
        specs, evidence = source_assignment_r2_specs(
            path, "C", "inc", [("amount", "uint256"),
                               ("spender", "address")],
            {}, [("amount", "num", None), ("spender", "id", 20)],
            arity=2, declaration_id=20, maps=maps, log=lambda _msg: None)
    finally:
        os.unlink(path)
    candidates = r2_candidates(specs)
    bad = 0
    bad += check(any(item["var"] == "allowance$10[msg.sender][spender]"
                     and item["text"] ==
                     "post - pre in [amount, amount] with post >= pre"
                     for item in candidates),
                 f"helper-inlined mapping increment becomes an alias-named "
                 f"delta R2 candidate: {candidates}; evidence={evidence}")
    return bad


def test_source_R2_unary_updates_prioritize_one_step_deltas():
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402
    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}
    nonces_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": {"nodeType": "Identifier",
                           "referencedDeclaration": 11,
                           "name": "nonces"},
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "uint256"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "nonce",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "nonces",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "UnaryOperation", "operator": "++",
                     "prefix": False, "src": "100:7:0",
                     "subExpression": {"nodeType": "Identifier",
                                       "referencedDeclaration": 10,
                                       "name": "nonce"}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "UnaryOperation", "operator": "--",
                     "prefix": True, "src": "120:7:0",
                     "subExpression": nonces_sender}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch", [], {"nonce": (0, 0, 32)}, [],
            arity=0, maps={"nonces": (1, "address", 32, 0, "nonces", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)
    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    nonce_deltas = entries.get("nonce", {}).get("deltas", [])
    slot_deltas = entries.get("nonces[msg.sender]", {}).get("deltas", [])
    bad = 0
    bad += check(len(nonce_deltas) == 1 and nonce_deltas[0]["dir"] == "inc",
                 f"scalar ++ becomes an inc-by-one candidate: {nonce_deltas}")
    bad += check(nonce_deltas and nonce_deltas[0]["lo"] ==
                 {"kind": "literal", "value": "1"},
                 f"scalar ++ uses literal one: {nonce_deltas}")
    bad += check(len(slot_deltas) == 1 and slot_deltas[0]["dir"] == "dec",
                 f"mapping -- becomes a dec-by-one candidate: {slot_deltas}")
    bad += check(slot_deltas and slot_deltas[0]["lo"] ==
                 {"kind": "literal", "value": "1"},
                 f"mapping -- uses literal one: {slot_deltas}")
    bad += check(specs[0].get("candidate_count") == 2 if specs else False,
                 f"candidate_count includes unary deltas: {specs}")
    bad += check(any("nonce: post - pre == 1" in line for line in evidence),
                 f"scalar unary provenance is recorded: {evidence}")
    bad += check(any("nonces[msg.sender]: pre - post == 1" in line
                     for line in evidence),
                 f"mapping unary provenance is recorded: {evidence}")
    return bad


def test_source_R2_delete_updates_prioritize_zero_endpoints():
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402
    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}
    bal_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": {"nodeType": "Identifier",
                           "referencedDeclaration": 13,
                           "name": "bal"},
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "uint256"}}

    def delete_expr(sub, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "UnaryOperation", "operator": "delete",
            "prefix": True, "src": src, "subExpression": sub}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "count",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "ready",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "clear",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 delete_expr({"nodeType": "Identifier",
                              "referencedDeclaration": 10,
                              "name": "count"}, "100:7:0"),
                 delete_expr({"nodeType": "Identifier",
                              "referencedDeclaration": 11,
                              "name": "ready"}, "120:7:0"),
                 delete_expr({"nodeType": "Identifier",
                              "referencedDeclaration": 12,
                              "name": "owner"}, "140:7:0"),
                 delete_expr(bal_sender, "160:7:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "clear", [], {"count": (0, 0, 32),
                                     "ready": (1, 0, 1),
                                     "owner": (2, 0, 20)}, [],
            arity=0, maps={"bal": (3, "address", 32, 0, "bal", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)
    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    count_terms = [item["term"] for item in entries.get("count", {}).get(
        "equals", [])]
    ready_terms = [item["term"] for item in entries.get("ready", {}).get(
        "equals", [])]
    bal_terms = [item["term"] for item in entries.get("bal[msg.sender]", {}).get(
        "equals", [])]
    bad = 0
    bad += check(count_terms == [{"kind": "literal", "value": "0"}],
                 f"delete uint becomes post == 0: {entries}")
    bad += check(ready_terms == [{"kind": "literal", "value": "0"}],
                 f"delete bool becomes post == false/0: {entries}")
    bad += check(bal_terms == [{"kind": "literal", "value": "0"}],
                 f"delete mapping slot becomes post == 0: {entries}")
    owner_terms = [item["term"] for item in entries.get("owner", {}).get(
        "equals", [])]
    bad += check(owner_terms == [{"kind": "literal", "value": "0"}],
                 f"delete address becomes post == 0: {entries}")
    bad += check(specs[0].get("candidate_count") == 4 if specs else False,
                 f"candidate_count includes delete endpoints: {specs}")
    bad += check(any("ready: post == false" in line for line in evidence),
                 f"bool delete provenance names false: {evidence}")
    bad += check(any("bal[msg.sender]: post == 0" in line for line in evidence),
                 f"mapping delete provenance is recorded: {evidence}")
    return bad


def test_source_R2_address_zero_assignments_prioritize_zero_endpoints():
    from solidity_path_put import (r2_candidates,  # noqa: E402
                                   source_assignment_r2_specs)
    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}

    def ident(ref, name):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name}

    def address_zero():
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": "address"}},
                "arguments": [{"nodeType": "Literal", "kind": "number",
                               "value": "0"}]}

    owners_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": ident(11, "owners"),
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "address"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "owners",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => address)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "renounce",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "100:12:0",
                     "leftHandSide": ident(10, "owner"),
                     "rightHandSide": address_zero()}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "120:12:0",
                     "leftHandSide": owners_sender,
                     "rightHandSide": address_zero()}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "renounce", [], {"owner": (0, 0, 20)}, [],
            arity=0, maps={"owners": (1, "address", 20, 0, "owners", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)
    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    owner_terms = [item["term"] for item in entries.get("owner", {}).get(
        "equals", [])]
    slot_terms = [item["term"] for item in entries.get("owners[msg.sender]", {}).get(
        "equals", [])]
    candidates = r2_candidates(specs)
    bad = 0
    bad += check(owner_terms == [{"kind": "literal", "value": "0"}],
                 f"address(0) assignment to scalar is mined: {entries}")
    bad += check(slot_terms == [{"kind": "literal", "value": "0"}],
                 f"address(0) assignment to mapping slot is mined: {entries}")
    bad += check([item["text"] for item in candidates].count("post == 0") == 2,
                 f"both address-zero candidates render as post == 0: "
                 f"{candidates}")
    bad += check(any("owner: post == address(0)" in line for line in evidence),
                 f"scalar provenance names address(0): {evidence}")
    bad += check(any("owners[msg.sender]: post == address(0)" in line
                     for line in evidence),
                 f"mapping provenance names address(0): {evidence}")
    return bad


def test_source_R2_environment_value_assignments_use_rendered_env_coords():
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402
    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}
    msg_value = {"nodeType": "MemberAccess", "memberName": "value",
                 "expression": {"nodeType": "Identifier", "name": "msg"},
                 "typeDescriptions": {"typeString": "uint256"}}
    block_timestamp = {"nodeType": "MemberAccess", "memberName": "timestamp",
                       "expression": {"nodeType": "Identifier",
                                      "name": "block"},
                       "typeDescriptions": {"typeString": "uint256"}}
    block_number = {"nodeType": "MemberAccess", "memberName": "number",
                    "expression": {"nodeType": "Identifier",
                                   "name": "block"},
                    "typeDescriptions": {"typeString": "uint256"}}
    block_chainid = {"nodeType": "MemberAccess", "memberName": "chainid",
                     "expression": {"nodeType": "Identifier",
                                    "name": "block"},
                     "typeDescriptions": {"typeString": "uint256"}}
    block_basefee = {"nodeType": "MemberAccess", "memberName": "basefee",
                     "expression": {"nodeType": "Identifier",
                                    "name": "block"},
                     "typeDescriptions": {"typeString": "uint256"}}
    tx_gasprice = {"nodeType": "MemberAccess", "memberName": "gasprice",
                   "expression": {"nodeType": "Identifier", "name": "tx"},
                   "typeDescriptions": {"typeString": "uint256"}}
    block_coinbase = {"nodeType": "MemberAccess", "memberName": "coinbase",
                      "expression": {"nodeType": "Identifier",
                                     "name": "block"},
                      "typeDescriptions": {"typeString": "address"}}
    bal_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": {"nodeType": "Identifier",
                           "referencedDeclaration": 13,
                           "name": "bal"},
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "uint256"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "paid",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "total",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "stamp",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "height",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 16, "name": "chain",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 17, "name": "fee",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 18, "name": "miner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "pay",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "100:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 10,
                                      "name": "owner"},
                     "rightHandSide": msg_sender}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "120:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 11,
                                      "name": "paid"},
                     "rightHandSide": msg_value}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "140:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "total"},
                     "rightHandSide": msg_value}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "160:12:0",
                     "leftHandSide": bal_sender,
                     "rightHandSide": msg_value}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "180:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 14,
                                      "name": "stamp"},
                     "rightHandSide": block_timestamp}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "200:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "total"},
                     "rightHandSide": block_timestamp}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "220:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 15,
                                      "name": "height"},
                     "rightHandSide": block_number}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "240:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "total"},
                     "rightHandSide": block_number}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "260:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 16,
                                      "name": "chain"},
                     "rightHandSide": block_chainid}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "280:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "total"},
                     "rightHandSide": block_chainid}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "300:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 17,
                                      "name": "fee"},
                     "rightHandSide": block_basefee}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "+=",
                     "src": "320:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 12,
                                      "name": "total"},
                     "rightHandSide": tx_gasprice}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=",
                     "src": "340:12:0",
                     "leftHandSide": {"nodeType": "Identifier",
                                      "referencedDeclaration": 18,
                                      "name": "miner"},
                     "rightHandSide": block_coinbase}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "pay", [], {"owner": (0, 0, 20),
                                   "paid": (1, 0, 32),
                                   "total": (2, 0, 32),
                                   "stamp": (4, 0, 32),
                                   "height": (5, 0, 32),
                                   "chain": (6, 0, 32),
                                   "fee": (7, 0, 32),
                                   "miner": (8, 0, 20)},
            [("msg.sender", "id", 20), ("msg.value", "num", None),
             ("block.timestamp", "num", None),
             ("block.number", "num", None),
             ("block.chainid", "num", None),
             ("block.basefee", "num", None),
             ("tx.gasprice", "num", None),
             ("block.coinbase", "id", 20)],
            arity=0, maps={"bal": (3, "address", 32, 0, "bal", None)},
            log=lambda _msg: None)
        none, _none_evidence = source_assignment_r2_specs(
            path, "C", "pay", [], {"owner": (0, 0, 20),
                                   "paid": (1, 0, 32),
                                   "total": (2, 0, 32),
                                   "stamp": (4, 0, 32),
                                   "height": (5, 0, 32),
                                   "chain": (6, 0, 32),
                                   "fee": (7, 0, 32),
                                   "miner": (8, 0, 20)},
            [], arity=0, maps={"bal": (3, "address", 32, 0, "bal", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)
    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    owner_terms = [item["term"] for item in entries.get("owner", {}).get(
        "equals", [])]
    paid_terms = [item["term"] for item in entries.get("paid", {}).get(
        "equals", [])]
    stamp_terms = [item["term"] for item in entries.get("stamp", {}).get(
        "equals", [])]
    height_terms = [item["term"] for item in entries.get("height", {}).get(
        "equals", [])]
    chain_terms = [item["term"] for item in entries.get("chain", {}).get(
        "equals", [])]
    fee_terms = [item["term"] for item in entries.get("fee", {}).get(
        "equals", [])]
    miner_terms = [item["term"] for item in entries.get("miner", {}).get(
        "equals", [])]
    total_deltas = entries.get("total", {}).get("deltas", [])
    bal_deltas = entries.get("bal[msg.sender]", {}).get("deltas", [])
    bad = 0
    bad += check(owner_terms == [{"kind": "coord", "name": "msg.sender"}],
                 f"owner = msg.sender is mined as an id endpoint: {entries}")
    bad += check(paid_terms == [{"kind": "coord", "name": "msg.value"}],
                 f"paid = msg.value is mined as a numeric endpoint: {entries}")
    bad += check(stamp_terms == [{"kind": "coord",
                                  "name": "block.timestamp"}],
                 f"stamp = block.timestamp is mined as a numeric endpoint: "
                 f"{entries}")
    bad += check(height_terms == [{"kind": "coord",
                                   "name": "block.number"}],
                 f"height = block.number is mined as a numeric endpoint: "
                 f"{entries}")
    bad += check(chain_terms == [{"kind": "coord",
                                  "name": "block.chainid"}],
                 f"chain = block.chainid is mined as a numeric endpoint: "
                 f"{entries}")
    bad += check(fee_terms == [{"kind": "coord",
                                "name": "block.basefee"}],
                 f"fee = block.basefee is mined as a numeric endpoint: "
                 f"{entries}")
    bad += check(miner_terms == [{"kind": "coord",
                                  "name": "block.coinbase"}],
                 f"miner = block.coinbase is mined as an id endpoint: "
                 f"{entries}")
    total_delta_terms = [item["lo"] for item in total_deltas]
    bad += check({"kind": "coord", "name": "msg.value"} in total_delta_terms,
                 f"total += msg.value is mined as a delta: {total_deltas}")
    bad += check({"kind": "coord", "name": "block.timestamp"}
                 in total_delta_terms,
                 f"total += block.timestamp is mined as a delta: "
                 f"{total_deltas}")
    bad += check({"kind": "coord", "name": "block.number"}
                 in total_delta_terms,
                 f"total += block.number is mined as a delta: "
                 f"{total_deltas}")
    bad += check({"kind": "coord", "name": "block.chainid"}
                 in total_delta_terms,
                 f"total += block.chainid is mined as a delta: "
                 f"{total_deltas}")
    bad += check({"kind": "coord", "name": "tx.gasprice"}
                 in total_delta_terms,
                 f"total += tx.gasprice is mined as a delta: "
                 f"{total_deltas}")
    bad += check(len(bal_deltas) == 1 and bal_deltas[0]["lo"] ==
                 {"kind": "coord", "name": "msg.value"},
                 f"bal[msg.sender] += msg.value is mined as a mapping delta: "
                 f"{bal_deltas}")
    bad += check(any("owner: post == msg.sender" in line for line in evidence),
                 f"sender provenance is recorded: {evidence}")
    bad += check(any("bal[msg.sender]: post - pre == msg.value" in line
                     for line in evidence),
                 f"mapping value provenance is recorded: {evidence}")
    bad += check(any("stamp: post == block.timestamp" in line
                     for line in evidence),
                 f"timestamp provenance is recorded: {evidence}")
    bad += check(any("height: post == block.number" in line
                     for line in evidence),
                 f"block-number provenance is recorded: {evidence}")
    bad += check(any("chain: post == block.chainid" in line
                     for line in evidence),
                 f"chain-id provenance is recorded: {evidence}")
    bad += check(any("fee: post == block.basefee" in line
                     for line in evidence),
                 f"basefee provenance is recorded: {evidence}")
    bad += check(any("miner: post == block.coinbase" in line
                     for line in evidence),
                 f"coinbase provenance is recorded: {evidence}")
    bad += check(none == [], f"unrendered environment coords are not mined: {none}")
    return bad


def test_source_R2_msg_sender_helper_calls_use_rendered_env_coord():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}

    def ident(ref, name, ty="address"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def call(ref, name):
        return {"nodeType": "FunctionCall", "arguments": [],
                "expression": ident(ref, name, "function () view returns "
                                    "(address)"),
                "typeDescriptions": {"typeString": "address"}}

    def assign(lhs, rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    sender_call = call(30, "_msgSender")
    fake_call = call(31, "senderLike")
    seen_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": ident(11, "seen",
                                "mapping(address => uint256)"),
        "indexExpression": sender_call,
        "typeDescriptions": {"typeString": "uint256"}}
    ast = {"nodeType": "SourceUnit", "nodes": [
        {"nodeType": "ContractDefinition", "name": "Context", "id": 1,
         "linearizedBaseContracts": [1], "nodes": [
             {"nodeType": "FunctionDefinition", "id": 30,
              "name": "_msgSender",
              "parameters": {"parameters": []},
              "returnParameters": {"parameters": [
                  {"id": 32, "name": "",
                   "typeDescriptions": {"typeString": "address"}}]},
              "body": {"nodeType": "Block", "statements": [
                  {"nodeType": "Return", "expression": msg_sender}]}}]},
        {"nodeType": "ContractDefinition", "name": "C", "id": 2,
         "linearizedBaseContracts": [2, 1], "nodes": [
             {"nodeType": "VariableDeclaration", "id": 10, "name": "owner",
              "stateVariable": True,
              "typeDescriptions": {"typeString": "address"}},
             {"nodeType": "VariableDeclaration", "id": 11, "name": "seen",
              "stateVariable": True,
              "typeDescriptions": {
                  "typeString": "mapping(address => uint256)"}},
             {"nodeType": "FunctionDefinition", "id": 31,
              "name": "senderLike",
              "parameters": {"parameters": []},
              "returnParameters": {"parameters": [
                  {"id": 33, "name": "",
                   "typeDescriptions": {"typeString": "address"}}]},
              "body": {"nodeType": "Block", "statements": [
                  {"nodeType": "Return",
                   "expression": ident(10, "owner")}]}},
             {"nodeType": "FunctionDefinition", "id": 40, "name": "touch",
              "parameters": {"parameters": [
                  {"id": 41, "name": "amount",
                   "typeDescriptions": {"typeString": "uint256"}}]},
              "body": {"nodeType": "Block", "statements": [
                  assign(ident(10, "owner"), sender_call, "100:12:0"),
                  assign(seen_sender, ident(41, "amount", "uint256"),
                         "120:12:0")]}},
             {"nodeType": "FunctionDefinition", "id": 50, "name": "bad",
              "parameters": {"parameters": []},
              "body": {"nodeType": "Block", "statements": [
                  assign(ident(10, "owner"), fake_call, "160:12:0")]}}
         ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch", [("amount", "uint256")],
            {"owner": (0, 0, 20)},
            [("msg.sender", "id", 20), ("amount", "num", None)],
            arity=1, maps={"seen": (1, "address", 32, 0, "seen", None)},
            log=lambda _msg: None)
        unrendered, _unrendered_evidence = source_assignment_r2_specs(
            path, "C", "touch", [("amount", "uint256")],
            {"owner": (0, 0, 20)}, [("amount", "num", None)],
            arity=1, maps={"seen": (1, "address", 32, 0, "seen", None)},
            log=lambda _msg: None)
        bad_specs, _bad_evidence = source_assignment_r2_specs(
            path, "C", "bad", [], {"owner": (0, 0, 20)}, [],
            arity=0, maps={}, log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equal_text(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    bad = 0
    bad += check(equal_text("owner") == ["msg.sender"],
                 f"_msgSender() assignment is mined as msg.sender: {entries}")
    bad += check(equal_text("seen[msg.sender]") == ["amount"],
                 f"_msgSender() mapping key is mined as msg.sender: {entries}")
    bad += check(any("owner: post == msg.sender" in line
                     for line in evidence),
                 f"helper-call provenance uses msg.sender: {evidence}")
    unrendered_entries = {entry["name"]: entry
                          for entry in unrendered[0]["vars"]} if unrendered else {}
    bad += check("owner" not in unrendered_entries,
                 f"_msgSender() RHS is still gated by rendered msg.sender: "
                 f"{unrendered_entries}")
    unrendered_seen = [
        r2_term_text(item["term"])
        for item in unrendered_entries.get("seen[msg.sender]", {}).get(
            "equals", [])]
    bad += check(unrendered_seen == ["amount"],
                 f"_msgSender() slot keys keep existing msg.sender-key "
                 f"semantics: {unrendered_entries}")
    bad += check(bad_specs == [],
                 f"nontrivial address helpers are not treated as msg.sender: "
                 f"{bad_specs}")
    return bad


def test_source_R2_inlines_one_internal_helper_call():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def call(ref, name, args, ty="tuple()"):
        return {"nodeType": "FunctionCall", "kind": "functionCall",
                "arguments": args,
                "expression": ident(ref, name, "function"),
                "typeDescriptions": {"typeString": ty}}

    def index(base, key, ty):
        return {"nodeType": "IndexAccess", "baseExpression": base,
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    allowances_owner = index(
        ident(10, "allowances", "mapping(address => mapping(address => uint256))"),
        ident(21, "owner", "address"),
        "mapping(address => uint256)")
    allowances_owner_spender = index(
        allowances_owner, ident(22, "spender", "address"), "uint256")
    approve_call = call(20, "_approve", [
        call(40, "_msgSender", [], "address"),
        ident(31, "spender", "address"),
        ident(32, "amount", "uint256")])
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "allowances", "stateVariable": True,
             "typeDescriptions": {
                 "typeString": "mapping(address => mapping(address => uint256))"}},
            {"nodeType": "FunctionDefinition", "id": 40,
             "name": "_msgSender",
             "parameters": {"parameters": []},
             "returnParameters": {"parameters": [
                 {"id": 41, "name": "",
                  "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "Return", "expression": msg_sender}]}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "_approve",
             "parameters": {"parameters": [
                 {"id": 21, "name": "owner",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 22, "name": "spender",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 23, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=", "src": "100:12:0",
                     "leftHandSide": allowances_owner_spender,
                     "rightHandSide": ident(23, "amount", "uint256")}}]}},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "approve",
             "parameters": {"parameters": [
                 {"id": 31, "name": "spender",
                  "typeDescriptions": {"typeString": "address"}},
                 {"id": 32, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement", "expression": approve_call}]}}
        ]}]}
    maps = {"allowances": (7, ("address", "address"), 32, 0, "allowances",
                           None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "approve",
            [("spender", "address"), ("amount", "uint256")], {},
            [("msg.sender", "id", 20), ("spender", "id", 20),
             ("amount", "num", None)],
            arity=2, maps=maps, log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    equals = [r2_term_text(item["term"])
              for item in entries.get(
                  "allowances[msg.sender][spender]", {}).get("equals", [])]
    bad = 0
    bad += check(equals == ["amount"],
                 f"one internal helper call is source-inlined: {entries}")
    bad += check(any("allowances[msg.sender][spender]: post == amount"
                     in line for line in evidence),
                 f"inlined helper provenance is recorded: {evidence}")
    return bad


def test_source_R2_mines_modifier_suffix_effects_only():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value),
                "typeDescriptions": {"typeString": "uint256"}}

    def assign(rhs_ref, rhs_name, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": ident(10, "_status", "uint256"),
            "rightHandSide": ident(rhs_ref, rhs_name, "uint256")}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10,
             "name": "_status", "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11,
             "name": "_ENTERED", "stateVariable": True, "constant": True,
             "typeDescriptions": {"typeString": "uint256"},
             "value": num(2)},
            {"nodeType": "VariableDeclaration", "id": 12,
             "name": "_NOT_ENTERED", "stateVariable": True, "constant": True,
             "typeDescriptions": {"typeString": "uint256"},
             "value": num(1)},
            {"nodeType": "ModifierDefinition", "id": 30,
             "name": "nonReentrant",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 assign(11, "_ENTERED", "100:12:0"),
                 {"nodeType": "PlaceholderStatement"},
                 assign(12, "_NOT_ENTERED", "130:16:0")]}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "modifiers": [{
                 "nodeType": "ModifierInvocation",
                 "modifierName": {
                     "nodeType": "Identifier",
                     "referencedDeclaration": 30,
                     "name": "nonReentrant"},
                 "arguments": []}],
             "body": {"nodeType": "Block", "statements": []}},
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch", [], {"_status": (0, 0, 32)}, [],
            arity=0, maps={}, log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} \
        if specs else {}
    equals = [r2_term_text(item["term"])
              for item in entries.get("_status", {}).get("equals", [])]
    bad = 0
    bad += check(equals == ["1"],
                 f"modifier suffix, not prefix, is mined: {entries}")
    bad += check(any("_status: post == _NOT_ENTERED" in line
                     for line in evidence),
                 f"modifier suffix provenance is recorded: {evidence}")
    bad += check(not any("_status: post == _ENTERED" in line
                         for line in evidence),
                 f"modifier prefix is not treated as final state: {evidence}")
    return bad


def test_source_R2_arithmetic_assignments_prioritize_expression_endpoints():
    from solidity_path_put import (r2_term_text,  # noqa: E402
                                   source_assignment_r2_specs)

    def ident(ref, name):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}
    msg_value = {"nodeType": "MemberAccess", "memberName": "value",
                 "expression": {"nodeType": "Identifier", "name": "msg"},
                 "typeDescriptions": {"typeString": "uint256"}}
    quote_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": ident(15, "quote"),
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "uint256"}}

    def assign(ref_or_lhs, rhs, src):
        lhs = ref_or_lhs if isinstance(ref_or_lhs, dict) else ident(
            ref_or_lhs[0], ref_or_lhs[1])
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "fee",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "scaled",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "paidLess",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "half",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "broken",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "quote",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "calc",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 assign((10, "fee"), binop("+", ident(21, "amount"), num(7)),
                        "100:12:0"),
                 assign((11, "scaled"), binop("*", num(2), ident(21, "amount")),
                        "120:12:0"),
                 assign((12, "paidLess"), binop("-", msg_value, num(1)),
                        "140:12:0"),
                 assign((13, "half"), binop("/", ident(21, "amount"), num(2)),
                        "160:12:0"),
                 assign((14, "broken"), binop("/", ident(21, "amount"), num(0)),
                        "180:12:0"),
                 assign(quote_sender, binop("*", ident(21, "amount"), num(3)),
                        "200:12:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "calc", [("amount", "uint256")],
            {"fee": (0, 0, 32), "scaled": (1, 0, 32),
             "paidLess": (2, 0, 32), "half": (3, 0, 32),
             "broken": (4, 0, 32)},
            [("amount", "num", None), ("msg.value", "num", None)],
            arity=1, maps={"quote": (5, "address", 32, 0, "quote", None)},
            log=lambda _msg: None)
        unrendered, _ = source_assignment_r2_specs(
            path, "C", "calc", [("amount", "uint256")],
            {"fee": (0, 0, 32), "scaled": (1, 0, 32),
             "paidLess": (2, 0, 32), "half": (3, 0, 32),
             "broken": (4, 0, 32)},
            [], arity=1,
            maps={"quote": (5, "address", 32, 0, "quote", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equal_text(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    bad = 0
    bad += check(equal_text("fee") == ["(amount + 7)"],
                 f"amount + literal endpoint is mined: {entries}")
    bad += check(equal_text("scaled") == ["(2 * amount)"],
                 f"literal * amount endpoint is mined: {entries}")
    bad += check(equal_text("paidLess") == ["(msg.value - 1)"],
                 f"msg.value - literal endpoint is mined: {entries}")
    bad += check(equal_text("half") == ["(amount / 2)"],
                 f"division by nonzero literal endpoint is mined: {entries}")
    bad += check("broken" not in entries,
                 f"division by zero is not mined as an endpoint: {entries}")
    bad += check(equal_text("quote[msg.sender]") == ["(amount * 3)"],
                 f"mapping arithmetic endpoint is mined: {entries}")
    bad += check(any("fee: post == (amount + 7)" in line
                     for line in evidence),
                 f"scalar arithmetic provenance is recorded: {evidence}")
    bad += check(any("quote[msg.sender]: post == (amount * 3)" in line
                     for line in evidence),
                 f"mapping arithmetic provenance is recorded: {evidence}")
    bad += check(unrendered == [],
                 f"arithmetic endpoints do not mine unrendered coords: "
                 f"{unrendered}")
    return bad


def test_source_R2_state_entry_coords_are_used_only_when_rendered():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def assign(ref, name, rhs, src, op="="):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": ident(ref, name), "rightHandSide": rhs}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "seed",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "mirror",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "next",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "total",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "savedOwner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 16, "name": "ready",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "VariableDeclaration", "id": 17, "name": "copiedReady",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "copy",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 assign(11, "mirror", ident(10, "seed"), "100:12:0"),
                 assign(12, "next", binop("+", ident(10, "seed"),
                                          ident(21, "amount")), "120:12:0"),
                 assign(13, "total", ident(10, "seed"), "140:12:0", "+="),
                 assign(15, "savedOwner", ident(14, "owner", "address"),
                        "160:12:0"),
                 assign(17, "copiedReady", ident(16, "ready", "bool"),
                        "180:12:0"),
                 assign(12, "next", binop("/", ident(10, "seed"), num(0)),
                        "200:12:0")]}}
        ]}]}
    layout = {"seed": (0, 0, 32), "mirror": (1, 0, 32),
              "next": (2, 0, 32), "total": (3, 0, 32),
              "owner": (4, 0, 20), "savedOwner": (5, 0, 20),
              "ready": (6, 0, 1), "copiedReady": (7, 0, 1)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "copy", [("amount", "uint256")], layout,
            [("amount", "num", None), ("state.seed", "num", None),
             ("state.owner", "id", 20), ("state.ready", "bool", 1)],
            arity=1, log=lambda _msg: None)
        unrendered, _ = source_assignment_r2_specs(
            path, "C", "copy", [("amount", "uint256")], layout,
            [("amount", "num", None)], arity=1, log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equals(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    total_deltas = entries.get("total", {}).get("deltas", [])
    bad = 0
    bad += check(equals("mirror") == ["state.seed"],
                 f"state.seed direct endpoint is mined: {entries}")
    bad += check(equals("next") == ["(state.seed + amount)"],
                 f"state.seed arithmetic endpoint is mined and divide-by-zero "
                 f"is skipped: {entries}")
    bad += check(len(total_deltas) == 1 and
                 r2_term_text(total_deltas[0]["lo"]) == "state.seed",
                 f"state.seed delta endpoint is mined: {total_deltas}")
    bad += check(equals("savedOwner") == ["state.owner"],
                 f"state.owner id endpoint is mined: {entries}")
    bad += check(equals("copiedReady") == ["state.ready"],
                 f"state.ready bool endpoint is mined: {entries}")
    bad += check(any("mirror: post == state.seed" in line
                     for line in evidence),
                 f"state coord provenance is recorded: {evidence}")
    bad += check(any("total: post - pre == state.seed" in line
                     for line in evidence),
                 f"state delta provenance is recorded: {evidence}")
    bad += check(unrendered == [],
                 f"unrendered state coords are not mined: {unrendered}")
    return bad


def test_source_R2_type_conversion_wrappers_are_unwrapped_conservatively():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def cast(ty, arg):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "typeDescriptions": {"typeString": ty},
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": ty}},
                "arguments": [arg]}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def assign(ref_or_lhs, rhs, src, op="="):
        if isinstance(ref_or_lhs, dict):
            lhs = ref_or_lhs
        else:
            ref, name, ty = ref_or_lhs
            lhs = ident(ref, name, ty)
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}
    msg_value = {"nodeType": "MemberAccess", "memberName": "value",
                 "expression": {"nodeType": "Identifier", "name": "msg"},
                 "typeDescriptions": {"typeString": "uint256"}}
    quote_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": ident(17, "quote", "mapping(address => uint256)"),
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "uint256"}}
    amount = ident(21, "amount", "uint256")
    who = ident(22, "who", "address")

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "small",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint128"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "wide",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "total",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "calc",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "payableOwner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 16, "name": "paid",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 17, "name": "quote",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "wrap",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}},
                 {"id": 22, "name": "who",
                  "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [
                 assign((10, "small", "uint128"), cast("uint128", amount),
                        "100:12:0"),
                 assign((11, "wide", "uint256"), cast("uint128", amount),
                        "120:12:0"),
                 assign((12, "total", "uint256"), cast("uint256", amount),
                        "140:12:0", "+="),
                 assign((13, "calc", "uint256"),
                        binop("+", cast("uint256", amount),
                              cast("uint256", num(7))), "160:12:0"),
                 assign((14, "owner", "address"), cast("address", who),
                        "180:12:0"),
                 assign((15, "payableOwner", "address"),
                        cast("address payable", who), "200:12:0"),
                 assign((16, "paid", "uint256"), cast("uint256", msg_value),
                        "220:12:0"),
                 assign(quote_sender, cast("uint256", amount),
                        "240:12:0")]}}
        ]}]}
    layout = {"small": (0, 0, 16), "wide": (1, 0, 32),
              "total": (2, 0, 32), "calc": (3, 0, 32),
              "owner": (4, 0, 20), "payableOwner": (5, 0, 20),
              "paid": (6, 0, 32)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "wrap", [("amount", "uint256"), ("who", "address")],
            layout, [("amount", "num", None), ("who", "id", 20),
                     ("msg.value", "num", None)], arity=2,
            maps={"quote": (7, "address", 32, 0, "quote", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equals(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    total_deltas = entries.get("total", {}).get("deltas", [])
    bad = 0
    bad += check(equals("small") == ["amount"],
                 f"same-target uint128(amount) is mined: {entries}")
    bad += check("wide" not in entries,
                 f"uint128(amount) assigned to uint256 is not simplified: "
                 f"{entries}")
    bad += check(len(total_deltas) == 1 and
                 r2_term_text(total_deltas[0]["lo"]) == "amount",
                 f"cast-wrapped += amount is mined: {total_deltas}")
    bad += check(equals("calc") == ["(amount + 7)"],
                 f"cast-wrapped arithmetic operands are mined: {entries}")
    bad += check(equals("owner") == ["who"],
                 f"address(who) endpoint is mined: {entries}")
    bad += check(equals("payableOwner") == ["who"],
                 f"address payable cast endpoint is mined: {entries}")
    bad += check(equals("paid") == ["msg.value"],
                 f"uint256(msg.value) endpoint is mined: {entries}")
    bad += check(equals("quote[msg.sender]") == ["amount"],
                 f"cast-wrapped mapping endpoint is mined: {entries}")
    bad += check(any("calc: post == (amount + 7)" in line
                     for line in evidence),
                 f"cast arithmetic provenance is recorded: {evidence}")
    return bad


def test_source_R2_constant_identifiers_prioritize_literal_endpoints():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def boolean(value):
        return {"nodeType": "Literal", "kind": "bool", "value": value}

    def address_zero():
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "typeDescriptions": {"typeString": "address"},
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": "address"}},
                "arguments": [num(0)]}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def assign(ref_or_lhs, rhs, src, op="="):
        if isinstance(ref_or_lhs, dict):
            lhs = ref_or_lhs
        else:
            ref, name, ty = ref_or_lhs
            lhs = ident(ref, name, ty)
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}
    bal_sender = {
        "nodeType": "IndexAccess",
        "baseExpression": ident(15, "bal", "mapping(address => uint256)"),
        "indexExpression": msg_sender,
        "typeDescriptions": {"typeString": "uint256"}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 30, "name": "MAX",
             "stateVariable": True, "constant": True, "value": num(7),
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 31, "name": "STEP",
             "stateVariable": True, "constant": True, "value": num(3),
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 32, "name": "READY",
             "stateVariable": True, "constant": True, "value": boolean(True),
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "VariableDeclaration", "id": 33, "name": "ZERO",
             "stateVariable": True, "constant": True, "value": address_zero(),
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 34, "name": "COMPLEX",
             "stateVariable": True, "constant": True,
             "value": binop("+", num(1), num(2)),
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 10, "name": "fee",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "total",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "ready",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "owner",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "bad",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "useConstants",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 assign((10, "fee", "uint256"), ident(30, "MAX", "uint256"),
                        "100:12:0"),
                 assign((11, "total", "uint256"), ident(31, "STEP", "uint256"),
                        "120:12:0", "+="),
                 assign((12, "ready", "bool"), ident(32, "READY", "bool"),
                        "140:12:0"),
                 assign((13, "owner", "address"), ident(33, "ZERO", "address"),
                        "160:12:0"),
                 assign((14, "bad", "uint256"), ident(34, "COMPLEX", "uint256"),
                        "180:12:0"),
                 assign(bal_sender, ident(30, "MAX", "uint256"),
                        "200:12:0")]}}
        ]}]}
    layout = {"fee": (0, 0, 32), "total": (1, 0, 32),
              "ready": (2, 0, 1), "owner": (3, 0, 20),
              "bad": (4, 0, 32)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "useConstants", [], layout, [], arity=0,
            maps={"bal": (5, "address", 32, 0, "bal", None)},
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equals(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    total_deltas = entries.get("total", {}).get("deltas", [])
    bad = 0
    bad += check(equals("fee") == ["7"],
                 f"numeric constant endpoint is mined: {entries}")
    bad += check(len(total_deltas) == 1 and
                 r2_term_text(total_deltas[0]["lo"]) == "3",
                 f"numeric constant delta is mined: {total_deltas}")
    bad += check(equals("ready") == ["1"],
                 f"bool constant endpoint is mined: {entries}")
    bad += check(equals("owner") == ["0"],
                 f"address-zero constant endpoint is mined: {entries}")
    bad += check(equals("bal[msg.sender]") == ["7"],
                 f"mapping constant endpoint is mined: {entries}")
    bad += check("bad" not in entries,
                 f"complex constant expressions are not interpreted: {entries}")
    bad += check(any("fee: post == MAX" in line for line in evidence),
                 f"numeric constant provenance uses the constant name: "
                 f"{evidence}")
    bad += check(any("total: post - pre == STEP" in line
                     for line in evidence),
                 f"delta constant provenance uses the constant name: "
                 f"{evidence}")
    return bad


def test_source_R2_mapping_literal_keys_are_named_when_slot_safe():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def boolean(value):
        return {"nodeType": "Literal", "kind": "bool", "value": value}

    def hex_lit(value):
        return {"nodeType": "Literal", "kind": "hexString",
                "hexValue": value, "value": value}

    def address_num(value):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "typeDescriptions": {"typeString": "address"},
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": "address"}},
                "arguments": [num(value)]}

    def index(base_ref, base_name, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": ident(base_ref, base_name),
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def assign(lhs, rhs, src, op="="):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "count",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(uint256 => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "flagged",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(bool => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "owners",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "hexOwners",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "bytesMap",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(bytes4 => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 assign(index(10, "count", num(7)), ident(21, "amount"),
                        "100:12:0"),
                 assign(index(11, "flagged", boolean(True)), ident(21, "amount"),
                        "120:12:0", "+="),
                 assign(index(12, "owners", address_num(1)), ident(21, "amount"),
                        "140:12:0"),
                 assign(index(13, "hexOwners", hex_lit("12ab")),
                        ident(21, "amount"), "160:12:0"),
                 assign(index(14, "bytesMap", hex_lit("12ab")),
                        ident(21, "amount"), "180:12:0")]}}
        ]}]}
    maps = {"count": (0, "uint256", 32, 0, "count", None),
            "flagged": (1, "bool", 32, 0, "flagged", None),
            "owners": (2, "address", 32, 0, "owners", None),
            "hexOwners": (3, "address", 32, 0, "hexOwners", None),
            "bytesMap": (4, "bytes4", 32, 0, "bytesMap", None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1, maps=maps,
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equals(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    flagged_deltas = entries.get("flagged[1]", {}).get("deltas", [])
    bad = 0
    bad += check(equals("count[7]") == ["amount"],
                 f"uint literal key slot is mined: {entries}")
    bad += check(len(flagged_deltas) == 1 and
                 r2_term_text(flagged_deltas[0]["lo"]) == "amount",
                 f"bool literal key slot delta is mined: {flagged_deltas}")
    bad += check(equals("owners[1]") == ["amount"],
                 f"address(number) literal key slot is mined: {entries}")
    bad += check(equals("hexOwners[0x12ab]") == ["amount"],
                 f"address hex literal key slot is mined: {entries}")
    bad += check("bytesMap[0x12ab]" not in entries,
                 f"bytesN literal keys are not emitted as uint256 slots: "
                 f"{entries}")
    bad += check(any("count[7]: post == amount" in line for line in evidence),
                 f"literal-key provenance is recorded: {evidence}")
    return bad


def test_source_R2_mapping_constant_keys_fold_to_safe_slot_literals():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def boolean(value):
        return {"nodeType": "Literal", "kind": "bool", "value": value}

    def hex_lit(value):
        return {"nodeType": "Literal", "kind": "hexString",
                "hexValue": value, "value": value}

    def address_cast(arg):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "typeDescriptions": {"typeString": "address"},
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": "address"}},
                "arguments": [arg]}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def index(base_ref, base_name, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": ident(base_ref, base_name),
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def assign(lhs, rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 30, "name": "K",
             "stateVariable": True, "constant": True, "value": num(9),
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 31, "name": "ON",
             "stateVariable": True, "constant": True, "value": boolean(True),
             "typeDescriptions": {"typeString": "bool"}},
            {"nodeType": "VariableDeclaration", "id": 32, "name": "A",
             "stateVariable": True, "constant": True,
             "value": address_cast(num(2)),
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 33, "name": "H",
             "stateVariable": True, "constant": True,
             "value": address_cast(hex_lit("beef")),
             "typeDescriptions": {"typeString": "address"}},
            {"nodeType": "VariableDeclaration", "id": 34, "name": "B",
             "stateVariable": True, "constant": True, "value": hex_lit("beef"),
             "typeDescriptions": {"typeString": "bytes4"}},
            {"nodeType": "VariableDeclaration", "id": 35, "name": "BAD",
             "stateVariable": True, "constant": True,
             "value": binop("+", num(1), num(2)),
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 10, "name": "count",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(uint256 => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "flagged",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(bool => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "owners",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "hexOwners",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "bytesMap",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(bytes4 => uint256)"}},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "badMap",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(uint256 => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 assign(index(10, "count", ident(30, "K")),
                        ident(21, "amount"), "100:12:0"),
                 assign(index(11, "flagged", ident(31, "ON", "bool")),
                        ident(21, "amount"), "120:12:0"),
                 assign(index(12, "owners", ident(32, "A", "address")),
                        ident(21, "amount"), "140:12:0"),
                 assign(index(13, "hexOwners", ident(33, "H", "address")),
                        ident(21, "amount"), "160:12:0"),
                 assign(index(14, "bytesMap", ident(34, "B", "bytes4")),
                        ident(21, "amount"), "180:12:0"),
                 assign(index(15, "badMap", ident(35, "BAD")),
                        ident(21, "amount"), "200:12:0")]}}
        ]}]}
    maps = {"count": (0, "uint256", 32, 0, "count", None),
            "flagged": (1, "bool", 32, 0, "flagged", None),
            "owners": (2, "address", 32, 0, "owners", None),
            "hexOwners": (3, "address", 32, 0, "hexOwners", None),
            "bytesMap": (4, "bytes4", 32, 0, "bytesMap", None),
            "badMap": (5, "uint256", 32, 0, "badMap", None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1, maps=maps,
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equals(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    bad = 0
    bad += check(equals("count[9]") == ["amount"],
                 f"uint constant key is folded to a literal slot: {entries}")
    bad += check(equals("flagged[1]") == ["amount"],
                 f"bool constant key is folded to a literal slot: {entries}")
    bad += check(equals("owners[2]") == ["amount"],
                 f"address numeric constant key is folded: {entries}")
    bad += check(equals("hexOwners[0xbeef]") == ["amount"],
                 f"address hex constant key is folded: {entries}")
    bad += check("bytesMap[0xbeef]" not in entries,
                 f"bytesN constant key is still refused: {entries}")
    bad += check("badMap[3]" not in entries,
                 f"complex constant key is not interpreted: {entries}")
    bad += check(any("count[9]: post == amount" in line for line in evidence),
                 f"constant-key provenance uses folded slot text: {evidence}")
    return bad


def test_source_R2_enum_mapping_keys_use_same_typed_params():
    from solidity_path_put import (RETURN_VAR, map_key_type_ok,  # noqa: E402
                                   propose_slot_vars, r2_term_text,
                                   source_assignment_r2_specs)

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def index(base_ref, base_name, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": ident(base_ref, base_name),
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    by_status = index(10, "byStatus", ident(21, "s", "enum C.Status"))
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "EnumDefinition", "id": 2, "name": "Status",
             "members": [{"nodeType": "EnumValue", "id": 3, "name": "Open"},
                         {"nodeType": "EnumValue", "id": 4, "name": "Closed"}]},
            {"nodeType": "VariableDeclaration", "id": 10, "name": "byStatus",
             "stateVariable": True,
             "typeDescriptions": {
                 "typeString": "mapping(enum C.Status => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"id": 21, "name": "s",
                  "typeDescriptions": {"typeString": "enum C.Status"}},
                 {"id": 22, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "returnParameters": {"parameters": [
                 {"id": 23, "name": "",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "Assignment", "operator": "=", "src": "100:8:0",
                     "leftHandSide": by_status,
                     "rightHandSide": ident(22, "amount")}},
                 {"nodeType": "Return", "src": "120:8:0",
                  "expression": by_status}]}}
        ]}]}
    maps = {"byStatus": (5, "enum C.Status", 32, 0, "byStatus", None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch",
            [("s", "enum C.Status"), ("amount", "uint256")], {},
            [("s", "id", None), ("amount", "num", None)], arity=2,
            rettypes=[("", "uint256")], maps=maps, log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    def equals(name):
        return [r2_term_text(item["term"])
                for item in entries.get(name, {}).get("equals", [])]

    bad = 0
    bad += check(map_key_type_ok("enum C.Status"),
                 "enum mapping keys are storage-layout-safe value keys")
    bad += check(not map_key_type_ok("bytes"),
                 "dynamic bytes mapping keys remain refused")
    bad += check(propose_slot_vars(
        maps, [("s", "enum C.Status"), ("other", "enum C.Other")],
        log=lambda _msg: None) == ["byStatus[s]"],
        "slot proposer uses only same-typed enum parameters")
    bad += check(equals("byStatus[s]") == ["amount"],
                 f"enum-key mapping assignment is mined: {entries}")
    bad += check(equals(RETURN_VAR) == ["state.byStatus[s]"],
                 f"enum-key mapping return is mined: {entries}")
    bad += check(any("byStatus[s]: post == amount" in line
                     for line in evidence),
                 f"enum-key mapping provenance is recorded: {evidence}")
    return bad


def test_source_R2_enum_state_literals_fold_to_ordinals():
    from solidity_path_put import r2_term_text, source_assignment_r2_specs  # noqa: E402

    enum_ty = "enum C.Status"

    def ident(ref, name, ty=enum_ty):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def enum_member(ref, name):
        return {"nodeType": "MemberAccess", "referencedDeclaration": ref,
                "memberName": name,
                "expression": ident(2, "Status",
                                    "type(enum C.Status)"),
                "typeDescriptions": {"typeString": enum_ty}}

    def assign(rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": ident(10, "status"),
            "rightHandSide": rhs}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "EnumDefinition", "id": 2, "name": "Status",
             "canonicalName": "C.Status",
             "members": [{"nodeType": "EnumValue", "id": 3, "name": "Open"},
                         {"nodeType": "EnumValue", "id": 4, "name": "Paused"},
                         {"nodeType": "EnumValue", "id": 5, "name": "Closed"}]},
            {"nodeType": "VariableDeclaration", "id": 10, "name": "status",
             "stateVariable": True,
             "typeDescriptions": {"typeString": enum_ty}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "close",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 assign(enum_member(5, "Closed"), "100:8:0")]}},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "reset",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "UnaryOperation", "operator": "delete",
                 "src": "140:6:0",
                 "subExpression": ident(10, "status")}]}},
            {"nodeType": "FunctionDefinition", "id": 40, "name": "bad",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 assign({"nodeType": "MemberAccess",
                         "referencedDeclaration": 99,
                         "memberName": "Foreign",
                         "expression": ident(98, "Other",
                                             "type(enum C.Other)"),
                         "typeDescriptions": {"typeString": enum_ty}},
                        "180:8:0")]}}
        ]}]}
    layout = {"status": (0, 0, 32)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        close_specs, close_evidence = source_assignment_r2_specs(
            path, "C", "close", [], layout, [], arity=0,
            log=lambda _msg: None)
        reset_specs, _reset_evidence = source_assignment_r2_specs(
            path, "C", "reset", [], layout, [], arity=0,
            log=lambda _msg: None)
        bad_specs, _bad_evidence = source_assignment_r2_specs(
            path, "C", "bad", [], layout, [], arity=0,
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    def equals(specs):
        entries = {entry["name"]: entry
                   for entry in specs[0]["vars"]} if specs else {}
        return [r2_term_text(item["term"])
                for item in entries.get("status", {}).get("equals", [])]

    bad = 0
    bad += check(equals(close_specs) == ["2"],
                 f"enum assignment folds to its declaration ordinal: "
                 f"{close_specs}")
    bad += check(any("status: post == Status.Closed" in line
                     for line in close_evidence),
                 f"enum provenance preserves the source member: "
                 f"{close_evidence}")
    bad += check(equals(reset_specs) == ["0"],
                 f"delete on enum state resets to ordinal zero: "
                 f"{reset_specs}")
    bad += check(bad_specs == [],
                 f"unknown enum member id is refused closed: {bad_specs}")
    return bad


def test_source_R2_return_candidates_prioritize_return_expressions():
    from solidity_path_put import (RETURN_VAR, r2_candidates,  # noqa: E402
                                   r2_term_text,
                                   source_assignment_r2_specs)
    def method(base, name, arg):
        return {"nodeType": "FunctionCall", "kind": "functionCall",
                "expression": {"nodeType": "MemberAccess",
                               "memberName": name,
                               "expression": base},
                "arguments": [arg],
                "typeDescriptions": {"typeString": "uint256"}}

    def call(ref, name, args, ty="uint256"):
        return {"nodeType": "FunctionCall", "kind": "functionCall",
                "expression": {"nodeType": "Identifier",
                               "referencedDeclaration": ref,
                               "name": name,
                               "typeDescriptions": {"typeString": "function"}},
                "arguments": args,
                "typeDescriptions": {"typeString": ty}}

    quote = {
        "nodeType": "FunctionDefinition", "id": 20, "name": "quote",
        "parameters": {"parameters": [
            {"id": 21, "name": "amount",
             "typeDescriptions": {"typeString": "uint256"}}]},
        "returnParameters": {"parameters": [
            {"id": 22, "name": "", "typeDescriptions": {
                "typeString": "uint256"}}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "Return", "src": "100:10:0",
            "expression": {"nodeType": "BinaryOperation", "operator": "+",
                           "leftExpression": {"nodeType": "Identifier",
                                              "referencedDeclaration": 21,
                                              "name": "amount"},
                           "rightExpression": {"nodeType": "Literal",
                                               "kind": "number",
                                               "value": "7"}}}]}}
    safe_quote = {
        "nodeType": "FunctionDefinition", "id": 25, "name": "safeQuote",
        "parameters": {"parameters": [
            {"id": 26, "name": "amount",
             "typeDescriptions": {"typeString": "uint256"}}]},
        "returnParameters": {"parameters": [
            {"id": 27, "name": "", "typeDescriptions": {
                "typeString": "uint256"}}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "Return", "src": "150:10:0",
            "expression": method(
                method({"nodeType": "Identifier",
                        "referencedDeclaration": 26,
                        "name": "amount"},
                       "mul",
                       {"nodeType": "Literal", "kind": "number",
                        "value": "3"}),
                "div",
                {"nodeType": "Literal", "kind": "number",
                 "value": "2"})}]}}
    flag = {
        "nodeType": "FunctionDefinition", "id": 30, "name": "flag",
        "parameters": {"parameters": [
            {"id": 31, "name": "ok",
             "typeDescriptions": {"typeString": "bool"}}]},
        "returnParameters": {"parameters": [
            {"id": 32, "name": "", "typeDescriptions": {
                "typeString": "bool"}}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "Return", "src": "200:10:0",
            "expression": {"nodeType": "Identifier",
                           "referencedDeclaration": 31,
                           "name": "ok"}}]}}
    pair = {
        "nodeType": "FunctionDefinition", "id": 40, "name": "pair",
        "parameters": {"parameters": [
            {"id": 41, "name": "amount",
             "typeDescriptions": {"typeString": "uint256"}}]},
        "returnParameters": {"parameters": [
            {"id": 42, "name": "", "typeDescriptions": {
                "typeString": "uint256"}},
            {"id": 43, "name": "", "typeDescriptions": {
                "typeString": "uint256"}}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "Return", "src": "300:10:0",
            "expression": {"nodeType": "Identifier",
                           "referencedDeclaration": 41,
                           "name": "amount"}}]}}
    named = {
        "nodeType": "FunctionDefinition", "id": 50, "name": "named",
        "parameters": {"parameters": [
            {"id": 51, "name": "amount",
             "typeDescriptions": {"typeString": "uint256"}}]},
        "returnParameters": {"parameters": [
            {"id": 52, "name": "out", "typeDescriptions": {
                "typeString": "uint256"}}]},
        "body": {"nodeType": "Block", "statements": [
            {"nodeType": "ExpressionStatement", "expression": {
                "nodeType": "Assignment", "operator": "=",
                "src": "400:10:0",
                "leftHandSide": {"nodeType": "Identifier",
                                 "referencedDeclaration": 52,
                                 "name": "out"},
                "rightHandSide": {
                    "nodeType": "BinaryOperation", "operator": "*",
                    "leftExpression": {"nodeType": "Identifier",
                                       "referencedDeclaration": 51,
                                       "name": "amount"},
                    "rightExpression": {"nodeType": "Literal",
                                        "kind": "number",
                                        "value": "2"}}}},
            {"nodeType": "Return", "src": "420:7:0"}]}}
    helper = {
        "nodeType": "FunctionDefinition", "id": 60, "name": "_helper",
        "parameters": {"parameters": [
            {"id": 61, "name": "x",
             "typeDescriptions": {"typeString": "uint256"}}]},
        "returnParameters": {"parameters": [
            {"id": 62, "name": "", "typeDescriptions": {
                "typeString": "uint256"}}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "Return", "src": "500:10:0",
            "expression": {"nodeType": "BinaryOperation", "operator": "+",
                           "leftExpression": {"nodeType": "Identifier",
                                              "referencedDeclaration": 61,
                                              "name": "x"},
                           "rightExpression": {"nodeType": "Literal",
                                               "kind": "number",
                                               "value": "7"}}}]}}
    via_helper = {
        "nodeType": "FunctionDefinition", "id": 70, "name": "viaHelper",
        "parameters": {"parameters": [
            {"id": 71, "name": "amount",
             "typeDescriptions": {"typeString": "uint256"}}]},
        "returnParameters": {"parameters": [
            {"id": 72, "name": "", "typeDescriptions": {
                "typeString": "uint256"}}]},
        "body": {"nodeType": "Block", "statements": [{
            "nodeType": "Return", "src": "600:10:0",
            "expression": call(60, "_helper", [{
                "nodeType": "Identifier",
                "referencedDeclaration": 71,
                "name": "amount",
                "typeDescriptions": {"typeString": "uint256"}}])}]}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1],
        "nodes": [quote, safe_quote, flag, pair, named, helper,
                  via_helper]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        quote_specs, quote_evidence = source_assignment_r2_specs(
            path, "C", "quote", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        flag_specs, flag_evidence = source_assignment_r2_specs(
            path, "C", "flag", [("ok", "bool")], {},
            [("ok", "bool", 1)], arity=1,
            rettypes=[("", "bool")], log=lambda _msg: None)
        safe_specs, safe_evidence = source_assignment_r2_specs(
            path, "C", "safeQuote", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        pair_specs, _pair_evidence = source_assignment_r2_specs(
            path, "C", "pair", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1,
            rettypes=[("", "uint256"), ("", "uint256")],
            log=lambda _msg: None)
        named_specs, named_evidence = source_assignment_r2_specs(
            path, "C", "named", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1,
            rettypes=[("out", "uint256")], log=lambda _msg: None)
        helper_specs, helper_evidence = source_assignment_r2_specs(
            path, "C", "viaHelper", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1,
            rettypes=[("", "uint256")], log=lambda _msg: None)
    finally:
        os.unlink(path)
    quote_entry = next((entry for entry in quote_specs[0]["vars"]
                        if entry["name"] == RETURN_VAR), {}) if quote_specs else {}
    quote_terms = [r2_term_text(item["term"])
                   for item in quote_entry.get("equals", [])]
    flag_entry = next((entry for entry in flag_specs[0]["vars"]
                       if entry["name"] == RETURN_VAR), {}) if flag_specs else {}
    flag_terms = [r2_term_text(item["term"])
                  for item in flag_entry.get("equals", [])]
    safe_entry = next((entry for entry in safe_specs[0]["vars"]
                       if entry["name"] == RETURN_VAR), {}) if safe_specs else {}
    safe_terms = [r2_term_text(item["term"])
                  for item in safe_entry.get("equals", [])]
    named_entry = next((entry for entry in named_specs[0]["vars"]
                        if entry["name"] == RETURN_VAR), {}) if named_specs else {}
    named_terms = [r2_term_text(item["term"])
                   for item in named_entry.get("equals", [])]
    helper_entry = next((entry for entry in helper_specs[0]["vars"]
                         if entry["name"] == RETURN_VAR), {}) \
        if helper_specs else {}
    helper_terms = [r2_term_text(item["term"])
                    for item in helper_entry.get("equals", [])]
    bad = 0
    bad += check(quote_terms == ["(amount + 7)"],
                 f"the arithmetic return expression is prioritized: "
                 f"{quote_specs}")
    bad += check([c["text"] for c in r2_candidates(quote_specs)] ==
                 ["return == (amount + 7)"],
                 f"return candidates render as return rows: "
                 f"{r2_candidates(quote_specs)}")
    bad += check(any("return: return == (amount + 7)" in line
                     for line in quote_evidence),
                 f"the return provenance is recorded: {quote_evidence}")
    bad += check(safe_terms == ["((amount * 3) / 2)"],
                 f"SafeMath-style chained return expression is prioritized: "
                 f"{safe_specs}")
    bad += check(any("return: return == ((amount * 3) / 2)" in line
                     for line in safe_evidence),
                 f"SafeMath-style return provenance is recorded: "
                 f"{safe_evidence}")
    bad += check(flag_terms == ["ok"],
                 f"bool return coordinates are preserved as bool terms: "
                 f"{flag_specs}, {flag_evidence}")
    bad += check([c["text"] for c in r2_candidates(flag_specs)] ==
                 ["return == ok"],
                 f"bool return candidate is rendered: "
                 f"{r2_candidates(flag_specs)}")
    bad += check(pair_specs == [],
                 f"multi-return whole-value source candidates are skipped: "
                 f"{pair_specs}")
    bad += check(named_terms == ["(amount * 2)"],
                 f"assignment to a named return parameter is prioritized: "
                 f"{named_specs}")
    bad += check(any("return: return == (amount * 2)" in line
                     for line in named_evidence),
                 f"the named-return provenance is recorded: {named_evidence}")
    bad += check(helper_terms == ["(amount + 7)"],
                 f"single-return helper calls feed return R2 candidates: "
                 f"{helper_specs}")
    bad += check(any("return: return == (amount + 7)" in line
                     for line in helper_evidence),
                 f"helper-return provenance is recorded at the caller return: "
                 f"{helper_evidence}")
    return bad


def test_source_R2_return_conditionals_expose_leaf_candidates():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name,
                "typeDescriptions": {"typeString": "uint256"}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value),
                "typeDescriptions": {"typeString": "uint256"}}

    def bool_expr(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "bool"}}

    def cond(t_expr, f_expr):
        return {"nodeType": "Conditional",
                "condition": bool_expr(">", ident(21, "x"), ident(22, "y")),
                "trueExpression": t_expr, "falseExpression": f_expr,
                "typeDescriptions": {"typeString": "uint256"}}

    def ret(expr):
        return {"nodeType": "Return", "src": "100:10:0",
                "expression": expr}

    def function(fid, name, returns, body_expr):
        return {"nodeType": "FunctionDefinition", "id": fid, "name": name,
                "parameters": {"parameters": [
                    {"id": 21, "name": "x",
                     "typeDescriptions": {"typeString": "uint256"}},
                    {"id": 22, "name": "y",
                     "typeDescriptions": {"typeString": "uint256"}}]},
                "returnParameters": {"parameters": [{
                    "id": fid + 1, "name": "",
                    "typeDescriptions": {"typeString": returns}}]},
                "body": {"nodeType": "Block", "statements": [
                    ret(body_expr)]}}

    nested = cond({
        "nodeType": "TupleExpression",
        "components": [cond(num(1), num(2))],
        "typeDescriptions": {"typeString": "uint256"},
    }, num(3))
    logic = bool_expr(
        "&&",
        bool_expr(">", ident(21, "x"), num(10)),
        bool_expr("<", ident(22, "y"), num(5)))
    compare = bool_expr(">", ident(21, "x"), ident(22, "y"))
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            function(30, "tern", "uint256", cond(num(10), num(20))),
            function(40, "nested", "uint256", nested),
            function(50, "logic", "bool", logic),
            function(60, "compare", "bool", compare),
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        tern, tern_evidence = source_assignment_r2_specs(
            path, "C", "tern", [("x", "uint256"), ("y", "uint256")], {},
            [("x", "num", None), ("y", "num", None)], arity=2,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        nested_specs, nested_evidence = source_assignment_r2_specs(
            path, "C", "nested", [("x", "uint256"), ("y", "uint256")],
            {}, [("x", "num", None), ("y", "num", None)], arity=2,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        logic_specs, _logic_evidence = source_assignment_r2_specs(
            path, "C", "logic", [("x", "uint256"), ("y", "uint256")],
            {}, [("x", "num", None), ("y", "num", None)], arity=2,
            rettypes=[("", "bool")], log=lambda _msg: None)
        compare_specs, _compare_evidence = source_assignment_r2_specs(
            path, "C", "compare", [("x", "uint256"), ("y", "uint256")],
            {}, [("x", "num", None), ("y", "num", None)], arity=2,
            rettypes=[("", "bool")], log=lambda _msg: None)
    finally:
        os.unlink(path)

    def return_equals(specs):
        entry = next((entry for entry in specs[0]["vars"]
                      if entry["name"] == RETURN_VAR), {}) if specs else {}
        return [r2_term_text(item["term"])
                for item in entry.get("equals", [])]

    bad = 0
    bad += check(return_equals(tern) == ["10", "20"],
                 f"plain ternary return leaves are candidates: {tern}")
    bad += check(return_equals(nested_specs) == ["1", "2", "3"],
                 f"nested ternary return leaves are candidates: "
                 f"{nested_specs}")
    bad += check(return_equals(logic_specs) == ["0", "1"],
                 f"bool short-circuit return gets true/false candidates: "
                 f"{logic_specs}")
    bad += check(return_equals(compare_specs) == ["0", "1"],
                 f"bool comparison return gets true/false candidates: "
                 f"{compare_specs}")
    bad += check(any("return: return == 10" in line
                     for line in tern_evidence),
                 f"ternary provenance records a source return: "
                 f"{tern_evidence}")
    bad += check(any("return: return == 3" in line
                     for line in nested_evidence),
                 f"nested ternary provenance records the false leaf: "
                 f"{nested_evidence}")
    return bad


def test_source_R2_return_type_conversion_wrappers_are_unwrapped():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def cast(ty, arg):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "typeDescriptions": {"typeString": ty},
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": ty}},
                "arguments": [arg]}

    def msg_value():
        return {"nodeType": "MemberAccess", "memberName": "value",
                "expression": {"nodeType": "Identifier", "name": "msg"},
                "typeDescriptions": {"typeString": "uint256"}}

    def ret(expr, src="100:10:0"):
        return {"nodeType": "Return", "src": src, "expression": expr}

    def assign(lhs, rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def function(fid, name, params, returns, statements):
        return {"nodeType": "FunctionDefinition", "id": fid, "name": name,
                "parameters": {"parameters": params},
                "returnParameters": {"parameters": returns},
                "body": {"nodeType": "Block", "statements": statements}}

    amount = {"id": 21, "name": "amount",
              "typeDescriptions": {"typeString": "uint256"}}
    who = {"id": 31, "name": "who",
           "typeDescriptions": {"typeString": "address"}}
    out = {"id": 72, "name": "out",
           "typeDescriptions": {"typeString": "uint256"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            function(20, "wide", [amount], [{
                "id": 22, "name": "",
                "typeDescriptions": {"typeString": "uint256"}}],
                [ret(cast("uint256", ident(21, "amount")))]),
            function(30, "owner", [who], [{
                "id": 32, "name": "",
                "typeDescriptions": {"typeString": "address"}}],
                [ret(cast("address", ident(31, "who", "address")))]),
            function(40, "small", [amount], [{
                "id": 42, "name": "",
                "typeDescriptions": {"typeString": "uint128"}}],
                [ret(cast("uint128", ident(21, "amount")))]),
            function(50, "badWide", [amount], [{
                "id": 52, "name": "",
                "typeDescriptions": {"typeString": "uint256"}}],
                [ret(cast("uint128", ident(21, "amount")))]),
            function(60, "pay", [], [{
                "id": 62, "name": "",
                "typeDescriptions": {"typeString": "uint256"}}],
                [ret(cast("uint256", msg_value()))]),
            function(70, "named", [amount], [out], [
                assign(ident(72, "out"), cast("uint256", ident(21, "amount")),
                       "300:10:0"),
                {"nodeType": "Return", "src": "320:7:0"}]),
            function(80, "calc", [amount], [{
                "id": 82, "name": "",
                "typeDescriptions": {"typeString": "uint256"}}],
                [ret({"nodeType": "BinaryOperation", "operator": "+",
                      "leftExpression": cast("uint256", ident(21, "amount")),
                      "rightExpression": cast("uint256", num(7)),
                      "typeDescriptions": {"typeString": "uint256"}})])
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)

    def specs_for(unit, params, rendered, rettypes):
        return source_assignment_r2_specs(
            path, "C", unit, params, {}, rendered, arity=1,
            rettypes=rettypes, log=lambda _msg: None)

    try:
        wide_specs, wide_evidence = specs_for(
            "wide", [("amount", "uint256")], [("amount", "num", None)],
            [("", "uint256")])
        owner_specs, _owner_evidence = specs_for(
            "owner", [("who", "address")], [("who", "id", 20)],
            [("", "address")])
        small_specs, _small_evidence = specs_for(
            "small", [("amount", "uint256")], [("amount", "num", None)],
            [("", "uint128")])
        bad_specs, _bad_evidence = specs_for(
            "badWide", [("amount", "uint256")], [("amount", "num", None)],
            [("", "uint256")])
        pay_specs, _pay_evidence = specs_for(
            "pay", [], [("msg.value", "num", None)], [("", "uint256")])
        named_specs, _named_evidence = specs_for(
            "named", [("amount", "uint256")], [("amount", "num", None)],
            [("out", "uint256")])
        calc_specs, _calc_evidence = specs_for(
            "calc", [("amount", "uint256")], [("amount", "num", None)],
            [("", "uint256")])
    finally:
        os.unlink(path)

    def return_terms(specs):
        entry = next((item for item in specs[0]["vars"]
                      if item["name"] == RETURN_VAR), {}) if specs else {}
        return [r2_term_text(item["term"])
                for item in entry.get("equals", [])]

    bad = 0
    bad += check(return_terms(wide_specs) == ["amount"],
                 f"matching uint cast return is unwrapped: {wide_specs}")
    bad += check(any("return: return == amount" in line
                     for line in wide_evidence),
                 f"cast return provenance stays source based: {wide_evidence}")
    bad += check(return_terms(owner_specs) == ["who"],
                 f"matching address cast return is unwrapped: {owner_specs}")
    bad += check(return_terms(small_specs) == ["amount"],
                 f"matching narrower return type is still source-mined: "
                 f"{small_specs}")
    bad += check(bad_specs == [],
                 f"unsafe narrow cast feeding a wider return is refused: "
                 f"{bad_specs}")
    bad += check(return_terms(pay_specs) == ["msg.value"],
                 f"cast-wrapped msg.value return is mined when rendered: "
                 f"{pay_specs}")
    bad += check(return_terms(named_specs) == ["amount"],
                 f"named return assignments share the cast unwrap: "
                 f"{named_specs}")
    bad += check(return_terms(calc_specs) == ["(amount + 7)"],
                 f"cast-wrapped arithmetic return terms are mined: "
                 f"{calc_specs}")
    return bad


def test_source_R2_return_can_name_a_rendered_state_pin():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "baseRate",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "rate",
             "parameters": {"parameters": []},
             "returnParameters": {"parameters": [
                 {"id": 21, "name": "",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "Return",
                  "expression": {
                      "nodeType": "Identifier",
                      "name": "baseRate",
                      "referencedDeclaration": 10,
                      "typeDescriptions": {"typeString": "uint256"}}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "rate", [], {},
            [("state.baseRate", "num", None)], arity=0,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        unrendered, _ = source_assignment_r2_specs(
            path, "C", "rate", [], {}, [], arity=0,
            rettypes=[("", "uint256")], log=lambda _msg: None)
    finally:
        os.unlink(path)

    entry = next((item for item in specs[0]["vars"]
                  if item["name"] == RETURN_VAR), {}) if specs else {}
    terms = [r2_term_text(item["term"]) for item in entry.get("equals", [])]
    bad = 0
    bad += check(terms == ["state.baseRate"],
                 f"rendered state pin can feed return R2: {specs}")
    bad += check(any("return: return == state.baseRate" in line
                     for line in evidence),
                 f"state-pin return provenance is recorded: {evidence}")
    bad += check(unrendered == [],
                 f"unrendered state pin is not guessed: {unrendered}")
    return bad


def test_source_R2_local_aliases_feed_return_state_and_mapping_terms():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def msg_sender():
        return {"nodeType": "MemberAccess", "memberName": "sender",
                "expression": {"nodeType": "Identifier", "name": "msg"},
                "typeDescriptions": {"typeString": "address"}}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def local_decl(ref, name, ty, value):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{
                    "nodeType": "VariableDeclaration", "id": ref,
                    "name": name,
                    "typeDescriptions": {"typeString": ty}}],
                "initialValue": value}

    def assign(lhs, rhs, src, op="="):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def index(base_ref, base_name, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": ident(base_ref, base_name),
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "total",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "uint256"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"id": 21, "name": "amount",
                  "typeDescriptions": {"typeString": "uint256"}}]},
             "returnParameters": {"parameters": [{
                 "id": 22, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 local_decl(30, "fee", "uint256",
                            binop("*", ident(21, "amount"), num(3))),
                 local_decl(31, "caller", "address", msg_sender()),
                 assign(ident(10, "total"), ident(30, "fee"), "100:10:0",
                        "+="),
                 assign(index(11, "bal", ident(31, "caller", "address")),
                        ident(21, "amount"), "120:10:0", "+="),
                 {"nodeType": "Return", "src": "140:10:0",
                  "expression": ident(30, "fee")}
             ]}}
        ]}]}
    maps = {"bal": (1, "address", 32, 0, "bal", None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "touch", [("amount", "uint256")],
            {"total": (0, 0, 32)}, [("amount", "num", None),
                                    ("msg.sender", "id", 20)],
            arity=1, maps=maps, rettypes=[("", "uint256")],
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    total_deltas = entries.get("total", {}).get("deltas", [])
    bal_deltas = entries.get("bal[msg.sender]", {}).get("deltas", [])
    ret_entry = entries.get(RETURN_VAR, {})
    return_terms = [r2_term_text(item["term"])
                    for item in ret_entry.get("equals", [])]
    bad = 0
    bad += check(len(total_deltas) == 1 and
                 r2_term_text(total_deltas[0]["lo"]) == "(amount * 3)",
                 f"local fee alias feeds a state delta: {total_deltas}")
    bad += check(len(bal_deltas) == 1 and
                 r2_term_text(bal_deltas[0]["lo"]) == "amount",
                 f"local caller alias names the msg.sender slot: {entries}")
    bad += check(return_terms == ["(amount * 3)"],
                 f"local fee alias feeds the return R2 term: {ret_entry}")
    bad += check(any("total: post - pre == (amount * 3)" in line
                     for line in evidence),
                 f"local alias provenance records the expanded term: "
                 f"{evidence}")
    return bad


def test_source_R2_local_aliases_are_invalidated_after_mutation():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def local_decl(ref, name, value):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{
                    "nodeType": "VariableDeclaration", "id": ref,
                    "name": name,
                    "typeDescriptions": {"typeString": "uint256"}}],
                "initialValue": value}

    def assign(lhs, rhs, src, op="="):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def ret(expr, src):
        return {"nodeType": "Return", "src": src, "expression": expr}

    amount_param = {"id": 21, "name": "amount",
                    "typeDescriptions": {"typeString": "uint256"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "FunctionDefinition", "id": 20, "name": "stale",
             "parameters": {"parameters": [amount_param]},
             "returnParameters": {"parameters": [{
                 "id": 22, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 local_decl(30, "fee", ident(21, "amount")),
                 assign(ident(30, "fee"), num(1), "100:10:0", "+="),
                 ret(ident(30, "fee"), "120:10:0")]}},
            {"nodeType": "FunctionDefinition", "id": 40, "name": "fresh",
             "parameters": {"parameters": [amount_param]},
             "returnParameters": {"parameters": [{
                 "id": 42, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 local_decl(50, "fee", ident(21, "amount")),
                 assign(ident(50, "fee"), num(1), "200:10:0", "+="),
                 assign(ident(50, "fee"), num(7), "220:10:0"),
                 ret(ident(50, "fee"), "240:10:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        stale_specs, stale_evidence = source_assignment_r2_specs(
            path, "C", "stale", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1, rettypes=[("", "uint256")],
            log=lambda _msg: None)
        fresh_specs, _fresh_evidence = source_assignment_r2_specs(
            path, "C", "fresh", [("amount", "uint256")], {},
            [("amount", "num", None)], arity=1, rettypes=[("", "uint256")],
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    def return_terms(specs):
        entry = next((item for item in specs[0]["vars"]
                      if item["name"] == RETURN_VAR), {}) if specs else {}
        return [r2_term_text(item["term"])
                for item in entry.get("equals", [])]

    bad = 0
    bad += check(stale_specs == [],
                 f"a mutated local does not keep a stale alias: "
                 f"{stale_specs}, {stale_evidence}")
    bad += check(return_terms(fresh_specs) == ["7"],
                 f"a later simple assignment creates a fresh alias: "
                 f"{fresh_specs}")
    return bad


def test_source_R2_mapping_getter_returns_named_entry_slot_coord():
    from solidity_path_put import RETURN_VAR, r2_candidates, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def msg_sender():
        return {"nodeType": "MemberAccess", "memberName": "sender",
                "expression": {"nodeType": "Identifier", "name": "msg"},
                "typeDescriptions": {"typeString": "address"}}

    def index(base_ref, base_name, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": ident(base_ref, base_name),
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "mapping(address => uint256)"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "get",
             "parameters": {"parameters": [
                 {"id": 21, "name": "who",
                  "typeDescriptions": {"typeString": "address"}}]},
             "returnParameters": {"parameters": [{
                 "id": 22, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "Return", "src": "100:10:0",
                 "expression": index(10, "bal",
                                     ident(21, "who", "address"))}]}},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "mine",
             "parameters": {"parameters": []},
             "returnParameters": {"parameters": [{
                 "id": 32, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "Return", "src": "200:10:0",
                 "expression": index(10, "bal", msg_sender())}]}}
        ]}]}
    maps = {"bal": (7, "address", 32, 0, "bal", None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        get_specs, get_evidence = source_assignment_r2_specs(
            path, "C", "get", [("who", "address")], {},
            [("who", "id", 20)], arity=1, maps=maps,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        mine_specs, _mine_evidence = source_assignment_r2_specs(
            path, "C", "mine", [], {}, [("msg.sender", "id", 20)],
            arity=0, maps=maps, rettypes=[("", "uint256")],
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    def return_terms(specs):
        entry = next((item for item in specs[0]["vars"]
                      if item["name"] == RETURN_VAR), {}) if specs else {}
        return [r2_term_text(item["term"])
                for item in entry.get("equals", [])]

    bad = 0
    bad += check(return_terms(get_specs) == ["state.bal[who]"],
                 f"getter return names the parameterized entry slot: "
                 f"{get_specs}")
    bad += check([item["text"] for item in r2_candidates(get_specs)] ==
                 ["return == state.bal[who]"],
                 f"getter return candidate renders for ESBMC: "
                 f"{r2_candidates(get_specs)}")
    bad += check(any("return: return == state.bal[who]" in line
                     for line in get_evidence),
                 f"getter return provenance is recorded: {get_evidence}")
    bad += check(return_terms(mine_specs) == ["state.bal[msg.sender]"],
                 f"msg.sender-keyed getter return is mined: {mine_specs}")
    return bad


def test_storage_layout_expands_top_level_struct_scalar_members():
    from solidity_path_put import _storage_layout_struct_members  # noqa: E402

    types = {
        "t_uint256": {"encoding": "inplace", "numberOfBytes": "32"},
        "t_bool": {"encoding": "inplace", "numberOfBytes": "1"},
        "t_nested": {"encoding": "inplace", "members": []},
        "t_array": {"encoding": "dynamic_array"},
    }
    members = [
        {"label": "count", "slot": "0", "offset": "0",
         "type": "t_uint256"},
        {"label": "flag", "slot": "1", "offset": "31",
         "type": "t_bool"},
        {"label": "nested", "slot": "2", "offset": "0",
         "type": "t_nested"},
        {"label": "items", "slot": "3", "offset": "0",
         "type": "t_array"},
    ]
    got = _storage_layout_struct_members("box", "7", members, types)
    bad = 0
    bad += check(got == {"box.count": (7, 0, 32),
                         "box.flag": (8, 31, 1)},
                 f"top-level struct layout exposes only scalar fields: {got}")
    return bad


def test_storage_layout_expands_struct_mapping_members():
    from solidity_path_put import (  # noqa: E402
        _storage_layout_struct_mappings, assert_query_pins,
        assert_query_region_entries, esbmc_certifiable_maps, parse_slot_name,
        propose_slot_vars, region_slot_vars)

    types = {
        "t_address": {"encoding": "inplace", "label": "address",
                      "numberOfBytes": "20"},
        "t_uint256": {"encoding": "inplace", "label": "uint256",
                      "numberOfBytes": "32"},
        "t_uint8": {"encoding": "inplace", "label": "uint8",
                    "numberOfBytes": "1"},
        "t_bal": {"encoding": "inplace", "label": "struct C.Bal",
                  "numberOfBytes": "32",
                  "members": [
                      {"label": "amount", "slot": "0", "offset": "0",
                       "type": "t_uint256"},
                      {"label": "tag", "slot": "0", "offset": "31",
                       "type": "t_uint8"}]},
        "t_map": {"encoding": "mapping", "key": "t_address",
                  "value": "t_bal"},
    }
    members = [{"label": "userDeposits", "slot": "2", "offset": "0",
                "type": "t_map"}]
    maps = _storage_layout_struct_mappings("vault", "7", members, types)
    parsed = parse_slot_name("vault.userDeposits[who].amount")
    log = []
    proposed = propose_slot_vars(
        maps, [("who", "address"), ("amount", "uint256")],
        log=log.append)
    region = {"state.vault.userDeposits[who].amount": (1, 2)}
    pins = {"state.vault.userDeposits[who].amount": 1}
    query_region, skipped_region = assert_query_region_entries(
        region, {}, {}, maps)
    query_pins, skipped_pins = assert_query_pins(pins, {}, maps)

    bad = 0
    bad += check(maps == {
        "vault.userDeposits.amount": (9, "address", 32, 0,
                                      "vault.userDeposits", "amount"),
        "vault.userDeposits.tag": (9, "address", 1, 31,
                                   "vault.userDeposits", "tag"),
    }, f"struct-contained mapping fields are layout coords: {maps}")
    bad += check(parsed == ("vault.userDeposits", ["who"], ".amount"),
                 f"dotted mapping base is parsed without eating tail: {parsed}")
    bad += check(esbmc_certifiable_maps(maps) == {},
                 f"struct-contained mappings are not ESBMC-queryable yet: "
                 f"{esbmc_certifiable_maps(maps)}")
    bad += check(proposed == [],
                 f"struct-contained mapping fields are not proposed to "
                 f"--path-cov-assert yet: {proposed}")
    bad += check(region_slot_vars(region, maps) == [],
                 "certified-region dotted slots are not reused before ESBMC "
                 "can certify them")
    bad += check(query_region == [] and query_pins == {},
                 f"dotted mapping region/pins are not passed to "
                 f"--path-cov-assert: {query_region}, {query_pins}")
    bad += check(skipped_region and skipped_pins,
                 f"dotted mapping skips are reported: {skipped_region}, "
                 f"{skipped_pins}")
    bad += check(any("struct-contained mapping_t fields" in line
                     for line in log),
                 f"skipped struct-contained mapping says why: {log}")
    return bad


def test_source_R2_struct_mapping_members_wait_for_ESBMC_support():
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def member(base, name, ty="uint256"):
        return {"nodeType": "MemberAccess", "memberName": name,
                "expression": base,
                "typeDescriptions": {"typeString": ty}}

    def index(base, key, ty="struct C.Bal storage ref"):
        return {"nodeType": "IndexAccess", "baseExpression": base,
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def local_decl(ref, name, ty, value):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{
                    "nodeType": "VariableDeclaration", "id": ref,
                    "name": name, "storageLocation": "storage",
                    "typeDescriptions": {"typeString": ty}}],
                "initialValue": value}

    def assign(lhs, rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def ret(expr, src):
        return {"nodeType": "Return", "src": src, "expression": expr}

    def function(fid, name, statements):
        return {"nodeType": "FunctionDefinition", "id": fid, "name": name,
                "parameters": {"parameters": [who_param, amount_param]},
                "returnParameters": {"parameters": [{
                    "id": fid + 1, "name": "",
                    "typeDescriptions": {"typeString": "uint256"}}]},
                "body": {"nodeType": "Block", "statements": statements}}

    who_param = {"id": 21, "name": "who",
                 "typeDescriptions": {"typeString": "address"}}
    amount_param = {"id": 22, "name": "amount",
                    "typeDescriptions": {"typeString": "uint256"}}
    deposits = member(ident(10, "vault", "struct C.Vault"),
                      "userDeposits", "mapping(address => struct C.Bal)")
    direct_row = index(deposits, ident(21, "who", "address"))
    direct_amount = member(direct_row, "amount")
    alias_amount = member(ident(30, "row", "struct C.Bal storage pointer"),
                          "amount")
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "vault",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "struct C.Vault"}},
            function(20, "direct", [
                assign(direct_amount, ident(22, "amount"), "100:10:0"),
                ret(direct_amount, "120:10:0")]),
            function(40, "viaAlias", [
                local_decl(30, "row", "struct C.Bal storage pointer",
                           direct_row),
                assign(alias_amount, ident(22, "amount"), "200:10:0"),
                ret(alias_amount, "220:10:0")])
        ]}]}
    maps = {"vault.userDeposits.amount": (9, "address", 32, 0,
                                          "vault.userDeposits", "amount")}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        direct_specs, direct_evidence = source_assignment_r2_specs(
            path, "C", "direct", [("who", "address"), ("amount", "uint256")],
            {}, [("who", "id", 20), ("amount", "num", None)], arity=2,
            maps=maps, rettypes=[("", "uint256")], log=lambda _msg: None)
        alias_specs, alias_evidence = source_assignment_r2_specs(
            path, "C", "viaAlias",
            [("who", "address"), ("amount", "uint256")], {},
            [("who", "id", 20), ("amount", "num", None)], arity=2,
            maps=maps, rettypes=[("", "uint256")], log=lambda _msg: None)
    finally:
        os.unlink(path)

    bad = 0
    bad += check(direct_specs == [],
                 f"direct struct mapping field is not proposed before ESBMC "
                 f"can certify mapping_t fields: {direct_specs}")
    bad += check(alias_specs == [],
                 f"storage alias to struct mapping field is not proposed "
                 f"before ESBMC can certify mapping_t fields: {alias_specs}")
    bad += check(direct_evidence == [] and alias_evidence == [],
                 f"no source-R2 provenance is claimed for unqueryable "
                 f"struct mappings: {direct_evidence + alias_evidence}")
    return bad


def test_source_R2_top_level_struct_members_are_state_coords():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def member(base, name, ty="uint256"):
        return {"nodeType": "MemberAccess", "memberName": name,
                "expression": base,
                "typeDescriptions": {"typeString": ty}}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def assign(lhs, rhs, src, op="="):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": op, "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def ret(expr, src):
        return {"nodeType": "Return", "src": src, "expression": expr}

    def box_field(name, ty="uint256"):
        return member(ident(10, "box", "struct C.Box"), name, ty)

    amount_param = {"id": 21, "name": "amount",
                    "typeDescriptions": {"typeString": "uint256"}}
    ok_param = {"id": 22, "name": "ok",
                "typeDescriptions": {"typeString": "bool"}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "box",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "struct C.Box"}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "get",
             "parameters": {"parameters": []},
             "returnParameters": {"parameters": [{
                 "id": 24, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 ret(box_field("count"), "100:10:0")]}},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "set",
             "parameters": {"parameters": [amount_param, ok_param]},
             "returnParameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 assign(box_field("count"), ident(21, "amount"),
                        "200:10:0"),
                 assign(box_field("flag", "bool"), ident(22, "ok", "bool"),
                        "220:10:0")]}},
            {"nodeType": "FunctionDefinition", "id": 40, "name": "grow",
             "parameters": {"parameters": [amount_param]},
             "returnParameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 assign(box_field("count"),
                        binop("+", box_field("count"),
                              ident(21, "amount")), "300:10:0")]}},
            {"nodeType": "FunctionDefinition", "id": 50, "name": "bump",
             "parameters": {"parameters": []},
             "returnParameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "UnaryOperation", "operator": "++",
                     "src": "400:6:0", "subExpression": box_field("count")}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "UnaryOperation", "operator": "delete",
                     "src": "420:6:0",
                     "subExpression": box_field("flag", "bool")}}]}}
        ]}]}
    layout = {"box.count": (3, 0, 32), "box.flag": (4, 0, 1)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        get_specs, get_evidence = source_assignment_r2_specs(
            path, "C", "get", [], layout, [], arity=0,
            rettypes=[("", "uint256")], log=lambda _msg: None)
        set_specs, set_evidence = source_assignment_r2_specs(
            path, "C", "set", [("amount", "uint256"), ("ok", "bool")],
            layout, [("amount", "num", None), ("ok", "bool", 1)], arity=2,
            log=lambda _msg: None)
        grow_specs, _grow_evidence = source_assignment_r2_specs(
            path, "C", "grow", [("amount", "uint256")], layout,
            [("amount", "num", None)], arity=1, log=lambda _msg: None)
        bump_specs, _bump_evidence = source_assignment_r2_specs(
            path, "C", "bump", [], layout, [], arity=0,
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    def entries(specs):
        return {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}

    get_entry = entries(get_specs).get(RETURN_VAR, {})
    set_entries = entries(set_specs)
    grow_entry = entries(grow_specs).get("box.count", {})
    bump_entries = entries(bump_specs)
    get_terms = [r2_term_text(item["term"])
                 for item in get_entry.get("equals", [])]
    count_terms = [r2_term_text(item["term"])
                   for item in set_entries.get("box.count", {}).get("equals", [])]
    flag_terms = [r2_term_text(item["term"])
                  for item in set_entries.get("box.flag", {}).get("equals", [])]
    grow_equals = [r2_term_text(item["term"])
                   for item in grow_entry.get("equals", [])]
    grow_deltas = [r2_term_text(item["lo"])
                   for item in grow_entry.get("deltas", [])]
    bump_deltas = [r2_term_text(item["lo"])
                   for item in bump_entries.get("box.count", {}).get(
                       "deltas", [])]
    bump_deletes = [r2_term_text(item["term"])
                    for item in bump_entries.get("box.flag", {}).get(
                        "equals", [])]

    bad = 0
    bad += check(get_terms == ["state.box.count"],
                 f"struct getter return names the entry-state field: "
                 f"{get_specs}")
    bad += check(count_terms == ["amount"],
                 f"struct numeric setter mines the field assignment: "
                 f"{set_specs}")
    bad += check(flag_terms == ["ok"],
                 f"struct bool setter mines the field assignment: {set_specs}")
    bad += check(grow_equals == ["(state.box.count + amount)"],
                 f"struct self-update mines a strong post endpoint: "
                 f"{grow_specs}")
    bad += check(grow_deltas == ["amount"],
                 f"struct self-update mines a delta endpoint: {grow_specs}")
    bad += check(bump_deltas == ["1"],
                 f"struct unary increment mines a one-step delta: "
                 f"{bump_specs}")
    bad += check(bump_deletes == ["0"],
                 f"struct delete mines a zero endpoint: {bump_specs}")
    bad += check(any("return: return == state.box.count" in line
                     for line in get_evidence),
                 f"struct getter provenance is recorded: {get_evidence}")
    bad += check(any("box.flag: post == ok" in line
                     for line in set_evidence),
                 f"struct setter provenance is recorded: {set_evidence}")
    return bad


def test_source_R2_storage_local_aliases_resolve_to_state_coords():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def member(base, name, ty="uint256"):
        return {"nodeType": "MemberAccess", "memberName": name,
                "expression": base,
                "typeDescriptions": {"typeString": ty}}

    def index(base_ref, base_name, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": ident(base_ref, base_name),
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def binop(op, lhs, rhs):
        return {"nodeType": "BinaryOperation", "operator": op,
                "leftExpression": lhs, "rightExpression": rhs,
                "typeDescriptions": {"typeString": "uint256"}}

    def local_decl(ref, name, ty, value, storage_location):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{
                    "nodeType": "VariableDeclaration", "id": ref,
                    "name": name, "storageLocation": storage_location,
                    "typeDescriptions": {"typeString": ty}}],
                "initialValue": value}

    def assign(lhs, rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def ret(expr, src):
        return {"nodeType": "Return", "src": src, "expression": expr}

    def function(fid, name, params, statements):
        return {"nodeType": "FunctionDefinition", "id": fid, "name": name,
                "parameters": {"parameters": params},
                "returnParameters": {"parameters": [{
                    "id": fid + 1, "name": "",
                    "typeDescriptions": {"typeString": "uint256"}}]},
                "body": {"nodeType": "Block", "statements": statements}}

    amount_param = {"id": 21, "name": "amount",
                    "typeDescriptions": {"typeString": "uint256"}}
    who_param = {"id": 22, "name": "who",
                 "typeDescriptions": {"typeString": "address"}}
    box_ident = ident(10, "box", "struct C.Box storage ref")
    bal_who = index(11, "bal", ident(22, "who", "address"),
                    "struct C.Bal storage ref")
    b_count = member(ident(30, "b", "struct C.Box storage pointer"), "count")
    row_amount = member(ident(31, "row", "struct C.Bal storage pointer"),
                        "amount")
    mem_count = member(ident(40, "m", "struct C.Box memory"), "count")
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "box",
             "stateVariable": True,
             "typeDescriptions": {"typeString": "struct C.Box"}},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "bal",
             "stateVariable": True,
             "typeDescriptions": {
                 "typeString": "mapping(address => struct C.Bal)"}},
            function(20, "storageAlias", [amount_param, who_param], [
                local_decl(30, "b", "struct C.Box storage pointer",
                           box_ident, "storage"),
                local_decl(31, "row", "struct C.Bal storage pointer",
                           bal_who, "storage"),
                assign(b_count, ident(21, "amount"), "100:10:0"),
                assign(row_amount,
                       binop("+", row_amount, ident(21, "amount")),
                       "120:10:0"),
                ret(b_count, "140:10:0")]),
            function(50, "memoryAlias", [amount_param], [
                local_decl(40, "m", "struct C.Box memory", box_ident,
                           "memory"),
                assign(mem_count, ident(21, "amount"), "200:10:0"),
                ret(mem_count, "220:10:0")])
        ]}]}
    layout = {"box.count": (3, 0, 32)}
    maps = {"bal.amount": (7, "address", 32, 0, "bal", "amount")}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        storage_specs, storage_evidence = source_assignment_r2_specs(
            path, "C", "storageAlias",
            [("amount", "uint256"), ("who", "address")], layout,
            [("amount", "num", None), ("who", "id", 20)], arity=2,
            maps=maps, rettypes=[("", "uint256")], log=lambda _msg: None)
        memory_specs, memory_evidence = source_assignment_r2_specs(
            path, "C", "memoryAlias", [("amount", "uint256")], layout,
            [("amount", "num", None)], arity=1, maps=maps,
            rettypes=[("", "uint256")], log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry
               for entry in storage_specs[0]["vars"]} if storage_specs else {}
    box_terms = [r2_term_text(item["term"])
                 for item in entries.get("box.count", {}).get("equals", [])]
    row_equals = [r2_term_text(item["term"])
                  for item in entries.get("bal[who].amount", {}).get(
                      "equals", [])]
    row_deltas = [r2_term_text(item["lo"])
                  for item in entries.get("bal[who].amount", {}).get(
                      "deltas", [])]
    ret_terms = [r2_term_text(item["term"])
                 for item in entries.get(RETURN_VAR, {}).get("equals", [])]
    bad = 0
    bad += check(box_terms == ["amount"],
                 f"storage struct alias feeds the top-level field setter: "
                 f"{storage_specs}")
    bad += check(row_equals == ["(state.bal[who].amount + amount)"],
                 f"storage mapping-value alias feeds the exact endpoint: "
                 f"{storage_specs}")
    bad += check(row_deltas == ["amount"],
                 f"storage mapping-value alias feeds the delta: "
                 f"{storage_specs}")
    bad += check(ret_terms == ["state.box.count"],
                 f"storage struct alias feeds the return coord: "
                 f"{storage_specs}")
    bad += check(any("bal[who].amount: post - pre == amount" in line
                     for line in storage_evidence),
                 f"storage alias provenance is recorded: {storage_evidence}")
    bad += check(memory_specs == [],
                 f"memory aliases are not treated as state writes: "
                 f"{memory_specs}, {memory_evidence}")
    return bad


def test_source_R2_storage_mapping_aliases_preserve_later_indices():
    from solidity_path_put import RETURN_VAR, r2_term_text  # noqa: E402
    from solidity_path_put import source_assignment_r2_specs  # noqa: E402

    def ident(ref, name, ty="uint256"):
        return {"nodeType": "Identifier", "referencedDeclaration": ref,
                "name": name, "typeDescriptions": {"typeString": ty}}

    def index_expr(base, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": base,
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    def local_decl(ref, name, ty, value):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{
                    "nodeType": "VariableDeclaration", "id": ref,
                    "name": name, "storageLocation": "storage",
                    "typeDescriptions": {"typeString": ty}}],
                "initialValue": value}

    def assign(lhs, rhs, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=", "src": src,
            "leftHandSide": lhs, "rightHandSide": rhs}}

    token_param = {"id": 21, "name": "token",
                   "typeDescriptions": {"typeString": "address"}}
    who_param = {"id": 22, "name": "who",
                 "typeDescriptions": {"typeString": "address"}}
    amount_param = {"id": 23, "name": "amount",
                    "typeDescriptions": {"typeString": "uint256"}}
    inner_init = index_expr(
        ident(10, "two",
              "mapping(address => mapping(address => uint256))"),
        ident(21, "token", "address"),
        "mapping(address => uint256) storage ref")
    inner_who = index_expr(
        ident(30, "inner", "mapping(address => uint256) storage pointer"),
        ident(22, "who", "address"))
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "two",
             "stateVariable": True,
             "typeDescriptions": {
                 "typeString":
                 "mapping(address => mapping(address => uint256))"}},
            {"nodeType": "FunctionDefinition", "id": 20,
             "name": "nestedAlias",
             "parameters": {"parameters": [
                 token_param, who_param, amount_param]},
             "returnParameters": {"parameters": [{
                 "id": 24, "name": "",
                 "typeDescriptions": {"typeString": "uint256"}}]},
             "body": {"nodeType": "Block", "statements": [
                 local_decl(30, "inner",
                            "mapping(address => uint256) storage pointer",
                            inner_init),
                 assign(inner_who, ident(23, "amount"), "100:10:0"),
                 {"nodeType": "Return", "src": "120:10:0",
                  "expression": inner_who}]}}
        ]}]}
    maps = {"two": (9, ("address", "address"), 32, 0, "two", None)}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out_file:
        json.dump(ast, out_file)
    try:
        specs, evidence = source_assignment_r2_specs(
            path, "C", "nestedAlias",
            [("token", "address"), ("who", "address"),
             ("amount", "uint256")], {}, [("token", "id", 20),
                                           ("who", "id", 20),
                                           ("amount", "num", None)],
            arity=3, maps=maps, rettypes=[("", "uint256")],
            log=lambda _msg: None)
    finally:
        os.unlink(path)

    entries = {entry["name"]: entry for entry in specs[0]["vars"]} if specs else {}
    slot_terms = [r2_term_text(item["term"])
                  for item in entries.get("two[token][who]", {}).get(
                      "equals", [])]
    ret_terms = [r2_term_text(item["term"])
                 for item in entries.get(RETURN_VAR, {}).get("equals", [])]
    bad = 0
    bad += check(slot_terms == ["amount"],
                 f"mapping storage alias preserves the later key: {specs}")
    bad += check(ret_terms == ["state.two[token][who]"],
                 f"mapping storage alias return names both keys: {specs}")
    bad += check(any("two[token][who]: post == amount" in line
                     for line in evidence),
                 f"mapping alias provenance is recorded: {evidence}")
    return bad


def test_bool_literal_R2_rows_render_from_ESBMC_true_spelling():
    from solidity_path_put import r2_terms_from_specs, rung_assertions  # noqa: E402
    specs = [{"vars": [{"name": "ready", "equals": [{
        "id": "src0", "term": {"kind": "literal", "value": "1"}}],
                       "abs": [], "deltas": []}]}]
    terms = r2_terms_from_specs(specs)
    got = rung_assertions("post == true", "_pre_ready", "_post_ready",
                          "ready: post == true", r2_terms=terms)
    bad = 0
    bad += check(got == [
        '    assertEq(_post_ready, 1, "ready: post == true");'
    ], f"ESBMC's bool text maps back to the verifier decimal term: {got}")
    return bad


def test_source_R2_candidates_run_before_the_typed_batch():
    from solidity_path_put import r2_candidates, schedule_source_r2_specs  # noqa: E402
    source = [{"param": "source_assign", "kind": "source-assign",
               "vars": [{"name": "ready", "equals": [{
                   "id": "src0", "term": {"kind": "literal", "value": "1"}}],
                          "abs": [], "deltas": []},
                         {"name": "bal", "equals": [], "abs": [],
                          "deltas": [{
                              "id": "src1", "dir": "inc",
                              "lo": {"kind": "coord", "name": "amount"},
                              "hi": {"kind": "coord", "name": "amount"}}]}]}]
    typed = [{"param": "batch", "stage": 1, "kind": "typed",
              "candidate_count": 2, "vars": [
                  {"name": "bal", "equals": [{
                      "id": "e0", "term": {"kind": "coord",
                                           "name": "amount"}}],
                   "abs": [], "deltas": []},
                  {"name": "ready", "equals": [{
                      "id": "e1", "term": {"kind": "coord",
                                           "name": "flag_"}}],
                   "abs": [], "deltas": []}]}]
    said = []
    got = schedule_source_r2_specs(source, typed, log=said.append)
    candidates = r2_candidates(got)
    bad = 0
    bad += check(len(got) == 2,
                 f"source and typed candidates are separate R2 queries: {got}")
    bad += check(got[0].get("kind") == "source-assign",
                 f"the source provenance is visible first: {got[0]}")
    bad += check(got[0].get("candidate_count") == 2,
                 f"the source candidate count is refreshed: {got}")
    bad += check(got[1].get("candidate_count") == 2,
                 f"the typed candidate count is preserved: {got}")
    bad += check([c["text"] for c in candidates] ==
                 ["post == 1",
                  "post - pre in [amount, amount] with post >= pre",
                  "post == amount", "post == flag_"],
                 f"all source candidates are scheduled before typed "
                 f"mechanical candidates: {candidates}")
    bad += check(any("before the mechanical batch" in line for line in said),
                 f"the log says source candidates are prioritized: {said}")
    return bad


def test_source_R2_schedule_keeps_source_outside_the_mechanical_budget():
    from solidity_path_put import r2_candidates, schedule_source_r2_specs  # noqa: E402
    source = [{"vars": [{"name": "ready", "equals": [{
        "id": "src0", "term": {"kind": "literal", "value": "1"}}],
                         "abs": [], "deltas": []}]}]
    typed = [{"param": "batch", "stage": 1, "kind": "typed",
              "candidate_count": 2, "vars": [
                  {"name": "bal", "equals": [{
                      "id": "e0", "term": {"kind": "coord",
                                           "name": "amount"}}],
                   "abs": [], "deltas": []},
                  {"name": "ready", "equals": [{
                      "id": "e1", "term": {"kind": "coord",
                                           "name": "flag_"}}],
                   "abs": [], "deltas": []}]}]
    said = []
    got = schedule_source_r2_specs(source, typed, log=said.append)
    texts = [candidate["text"] for candidate in r2_candidates(got)]
    bad = 0
    bad += check(len(got) == 2 and got[0].get("candidate_count") == 1,
                 f"source gets its own prefix query: {got}")
    bad += check("post == 1" in texts and "post == flag_" in texts,
                 f"source does not spend the mechanical candidate budget: "
                 f"{texts}, {said}")
    return bad


def test_same_arity_overloads_use_the_exact_path_declaration():
    from solidity_path_put import (function_params, function_returns,  # noqa: E402
                                   overload_artifact_label,
                                   select_path_claim,
                                   source_r2_literals)
    def declaration(node_id, parameter, param_type, ret_type, literal):
        return {
            "nodeType": "FunctionDefinition", "id": node_id, "name": "f",
            "parameters": {"parameters": [{
                "name": parameter,
                "typeDescriptions": {"typeString": param_type}}]},
            "returnParameters": {"parameters": [{
                "name": "out",
                "typeDescriptions": {"typeString": ret_type}}]},
            "body": {"nodeType": "Block", "statements": [{
                "nodeType": "Literal", "kind": "number",
                "value": literal}]}}
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            declaration(11, "amount", "uint256", "uint256", "7"),
            declaration(12, "who", "address", "address", "9")]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        params = function_params(path, "C", "f", arity=1,
                                 declaration_id=11)
        returns = function_returns(path, "C", "f", arity=1,
                                   declaration_id=11)
        literals, _ = source_r2_literals(
            path, "C", "f", arity=1, declaration_id=11)
        artifact_label = overload_artifact_label(path, "C", "f", 11)
    finally:
        os.unlink(path)
    report = {"claims": [
        {"condition": "f:path:3", "path_id": 3,
         "path_function": "sol:@C@C@F@f#11"},
        {"condition": "f:path:3", "path_id": 3,
         "path_function": "sol:@C@C@F@f#12"}]}
    ambiguous, why = select_path_claim(report, "f", 3)
    exact, exact_why = select_path_claim(
        report, "f", 3, path_function="sol:@C@C@F@f#11")
    bad = 0
    bad += check(params == [("amount", "uint256")],
                 f"parameters come from declaration 11: {params}")
    bad += check(returns == [("out", "uint256")],
                 f"returns come from declaration 11: {returns}")
    bad += check(literals == ["7"],
                 f"source atoms come from declaration 11: {literals}")
    bad += check(artifact_label == "_pf11",
                 f"overloaded artifact names carry the node id: "
                 f"{artifact_label}")
    bad += check(ambiguous is None and "multiple path functions" in why,
                 f"legacy simple-name selection refuses ambiguity: {why}")
    bad += check(exact is report["claims"][0] and exact_why is None,
                 f"the exact mangled identity selects one claim: {exact}")
    return bad


def test_overload_persistence_keys_and_work_suffixes_are_distinct():
    from solidity_ast_dependencies import path_function_artifact_suffix  # noqa: E402
    notes_scripts = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "notes",
        "coverage", "scripts")
    sys.path.insert(0, notes_scripts)
    import certify_all  # noqa: E402
    first = "sol:@C@C@F@f#11"
    second = "sol:@C@C@F@f#12"
    bad = 0
    bad += check(path_function_artifact_suffix(first) == "__pf11",
                 "the workdir suffix carries declaration 11")
    bad += check(path_function_artifact_suffix(second) == "__pf12",
                 "the workdir suffix carries declaration 12")
    key1 = certify_all.certification_key("bench", "f", first, first)
    key2 = certify_all.certification_key("bench", "f", second, second)
    bad += check(key1 != key2,
                 f"overloads have independent resume keys: {key1}, {key2}")
    legacy1 = certify_all.certification_key("bench", "f", first, None)
    legacy2 = certify_all.certification_key("bench", "f", second, None)
    bad += check(legacy1 == legacy2 == ("bench", "f", None),
                 f"legacy simple-unit rows keep their old identity: "
                 f"{legacy1}, {legacy2}")
    return bad


def test_structured_R2_term_renders_with_the_lifted_coordinate():
    from solidity_path_put import rung_assertions  # noqa: E402
    term = {"kind": "op", "op": "add", "lhs": {"kind": "pre"},
            "rhs": {"kind": "coord", "name": "amount"}}
    got = rung_assertions(
        "post == (pre + amount)", "_pre_bal", "_post_bal", "bal: eq",
        {"amount": "p_amount"}, {"amount": "p_amount"},
        {"(pre + amount)": term})
    return check(got == [
        '    unchecked { assertEq(_post_bal, (_pre_bal + p_amount), '
        '"bal: eq"); }'],
        f"structured term uses unchecked arithmetic over emitted identifiers: "
        f"{got}")


def test_structured_R2_interval_accepts_literal_endpoint_without_lookup():
    """Mixed intervals should not require a redundant literal term entry."""
    from solidity_path_put import rung_assertions  # noqa: E402
    term = {"kind": "op", "op": "add", "lhs": {"kind": "pre"},
            "rhs": {"kind": "coord", "name": "msg.value"}}
    got = rung_assertions(
        "post in [0, (pre + msg.value)]", "_pre_bal", "_post_bal",
        "bal: abs", {"msg.value": "0"}, {"msg.value": "0"},
        {"(pre + msg.value)": term})
    bad = 0
    bad += check(got == [
        '    assertGe(_post_bal, 0, "bal: abs");',
        '    unchecked { assertLe(_post_bal, (_pre_bal + 0), "bal: abs"); }'],
        f"literal low endpoint and structured high endpoint both render: {got}")
    refused = rung_assertions(
        "post in [floor, (pre + msg.value)]", "_pre_bal", "_post_bal",
        "bal: abs", {"msg.value": "0"}, {"msg.value": "0"},
        {"(pre + msg.value)": term})
    bad += check(refused is None,
                 f"a non-literal endpoint still needs a certified term: "
                 f"{refused}")
    return bad


def test_structured_R2_term_renders_with_entry_mapping_coord():
    em, case = make_case()
    notes = []
    term = {
        "kind": "op", "op": "sub",
        "lhs": {"kind": "coord", "name": "state.allowance[u]"},
        "rhs": {"kind": "coord", "name": "bps"},
    }
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2,
        "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout={"total": (2, 0, 32)},
        maps={"allowance": (7, "address", 32, 0, "allowance", None)},
        ladder_rows=[
            ("total", "post == (state.allowance[u] - bps)", "HOLDS"),
        ],
        notes=notes, r2_terms={
            "(state.allowance[u] - bps)": term,
        })
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced: {notes}")
    bad += check("uint256 _pre_allowance_u = uint256(vm.load("
                 in text,
                 "the R2 endpoint mapping slot is read before the call")
    bad += check("keccak256(abi.encode(u, uint256(7)))" in text,
                 "the R2 endpoint uses the mapping slot hash")
    bad += check("assertEq(_post_total, (_pre_allowance_u - bps)"
                 in text,
                 "the certified equality renders with the entry mapping value")
    bad += check(not stats["oracle_skipped"],
                 f"no certified R2 row is dropped: {stats['oracle_skipped']}")
    return bad


def test_structured_R2_requires_a_successful_revert_tolerant_call():
    from solidity_path_put import rung_asserts_a_change  # noqa: E402
    bad = 0
    bad += check(rung_asserts_a_change("post == (pre + amount)"),
                 "post == expression is success-dependent")
    bad += check(rung_asserts_a_change("post in [amount, amount]"),
                 "absolute intervals are success-dependent")
    bad += check(rung_asserts_a_change(
        "post - pre in [amount, amount] with post >= pre"),
        "a symbolic delta lower bound may be nonzero")
    bad += check(not rung_asserts_a_change(
        "post - pre in [0, amount] with post >= pre"),
        "a literal-zero delta still holds on rollback")
    return bad


def test_oracle_mapping_candidates_share_the_dependency_filter():
    from solidity_path_put import propose_slot_vars  # noqa: E402
    maps = {
        "allow": (1, ("address", "address"), 32, 0, "allow", None),
        "bal": (2, "address", 32, 0, "bal", None),
    }
    said = []
    got = propose_slot_vars(
        maps, [("who", "address")], dependencies=["bal"], log=said.append)
    bad = 0
    bad += check(got == ["bal[msg.sender]", "bal[who]"],
                 f"only the dependency mapping reaches the oracle: {got}")
    bad += check(any("allow" in line and "excluded" in line for line in said),
                 f"the excluded mapping is named: {said}")
    return bad


def _r2_harness(reply):
    """(new_rows, log, specs_written) for one R2 pass against a fake ladder.

    The runner is INJECTED so the wiring is proven to fire without esbmc. A
    proposer with no call site and a call site that never runs look identical
    from the outside, and this file exists because that keeps happening.
    """
    from solidity_path_put import run_r2_passes  # noqa: E402
    said, written = [], []

    def write_spec(suffix, spec):
        written.append((suffix, spec))
        return "/tmp/spec" + suffix + ".json"

    def parse(text):
        return reply, None, ("a refusal" if text == "REFUSED" else None), None

    new = run_r2_passes(
        [{"param": "amount", "stage": 1, "kind": "num",
          "vars": [{"name": "bal",
                    "abs_lo": "amount", "abs_hi": "amount",
                    "delta_dir": "inc",
                    "delta_lo": "amount", "delta_hi": "amount"}]}],
        {"unit": "u", "enc": 7}, write_spec,
        lambda p: ("REFUSED" if reply == [] else "ok"), parse,
        log=said.append)
    return new, said, written


def test_an_R2_PASS_actually_runs_and_carries_the_proposed_vars():
    """⛔ THE WIRING, not the proposer. The spec handed to the extra run must
    keep the base fields AND replace `vars` with the proposal; a pass that
    posted the ORIGINAL vars would run, cost a query, and come back with the
    same R1 rungs looking like R2 produced nothing."""
    new, said, written = _r2_harness(
        [("bal", "post - pre in [amount, amount] with post >= pre", "HOLDS")])
    bad = 0
    bad += check(len(written) == 1, f"exactly one spec written: {written}")
    if not written:
        return bad + 1
    suffix, spec = written[0]
    bad += check(spec.get("unit") == "u" and spec.get("enc") == 7,
                 f"the base spec's fields survive: {spec}")
    bad += check(spec["vars"][0]["delta_dir"] == "inc"
                 and spec["vars"][0]["delta_lo"] == "amount",
                 f"and `vars` is the PROPOSAL: {spec['vars']}")
    bad += check(suffix == ".r2_amount_s1",
                 f"written beside the first spec, not over it, and the STAGE "
                 f"is in the name -- two stages on one parameter must not "
                 f"collide on one file: {suffix}")
    bad += check(len(new) == 1 and new[0][1].startswith("post - pre in ["),
                 f"the delta row is returned: {new}")
    return bad


def test_an_ABSOLUTE_row_is_MERGED_and_not_silently_dropped():
    """⛔ THE READER, NOT THE REQUEST. `run_r2_passes` accepted the two DELTA
    shapes and nothing else, so an absolute row -- the entire point of asking
    for one -- came back from the ladder, matched no prefix, and was discarded
    as though the pass had returned empty.

    A request whose answer no reader accepts is indistinguishable from a
    request that was never sent, and this file exists because that keeps
    happening. Pinned in BOTH directions: the abs row is merged, and the pass
    is NOT reported as empty."""
    new, said, _w = _r2_harness(
        [("bal", "post in [amount, amount]", "HOLDS")])
    bad = 0
    bad += check(len(new) == 1, f"exactly the abs row is merged: {new}")
    if not new:
        return bad + 1
    bad += check(new[0] == ("bal", "post in [amount, amount]", "HOLDS"),
                 f"verbatim, verdict included: {new[0]}")
    bad += check(not any("NO R2 ROW" in s for s in said),
                 f"and the pass is NOT announced as empty: {said}")
    return bad


def test_a_RETURN_R2_row_is_MERGED_and_not_reported_empty():
    """Pure/view units buy their oracle through `return == literal`.

    The R2 reader used to accept state-shaped rows only, so ESBMC could report
    `return == 20 HOLDS` and the driver would still print an oracle whose only
    return assertion was `return != 0`.
    """
    new, said, _w = _r2_harness(
        [("return", "return == 20", "HOLDS"),
         ("return", "return in [20, 20]", "HOLDS")])
    bad = 0
    bad += check(("return", "return == 20", "HOLDS") in new,
                 f"return equality row is merged: {new}")
    bad += check(("return", "return in [20, 20]", "HOLDS") in new,
                 f"return absolute row is merged: {new}")
    bad += check(not any("NO R2 ROW" in s for s in said),
                 f"and the pass is NOT announced as empty: {said}")
    return bad


def _stage2_harness(stage1_reply):
    """(specs actually written, log) for a two-stage proposal whose stage-1
    verdict is `stage1_reply`."""
    from solidity_path_put import run_r2_passes  # noqa: E402
    said, written = [], []

    def write_spec(suffix, spec):
        written.append((suffix, spec))
        return "/tmp/spec" + suffix + ".json"

    replies = [stage1_reply,
               [("bal", "post - pre in [0, amount] with post >= pre",
                 "HOLDS")]]

    def parse(_text):
        return (replies.pop(0) if replies else []), None, None, None

    new = run_r2_passes(
        [{"param": "amount", "stage": 1, "kind": "num",
          "vars": [{"name": "bal", "abs_lo": "amount", "abs_hi": "amount",
                    "delta_dir": "inc",
                    "delta_lo": "amount", "delta_hi": "amount"}]},
         {"param": "amount", "stage": 2, "kind": "cap",
          "vars": [{"name": "bal", "delta_dir": "inc",
                    "delta_lo": "0", "delta_hi": "amount"}]}],
        {"unit": "u", "enc": 7}, write_spec,
        lambda _p: "ok", parse, log=said.append)
    return new, said, written


def test_the_CAP_pass_RUNS_when_stage_1_REFUTED_the_exact_delta():
    """The property a withdraw-shaped unit is actually about. `delta == amt`
    is refuted the moment the unit takes a fee, and refuted there does NOT
    mean the delta is unbounded -- `delta <= amt` is still an oracle, and a
    mutant that moves MORE than the argument goes RED on it."""
    new, said, written = _stage2_harness(
        [("bal", "post - pre in [amount, amount] with post >= pre",
          "REFUTED")])
    bad = 0
    bad += check(len(written) == 2, f"both passes ran: "
                                    f"{[s for s, _ in written]}")
    bad += check(any(t.startswith("post - pre in [0, amount]")
                     for _v, t, _d in new),
                 f"and the cap row came back: {new}")
    return bad


def test_the_CAP_pass_IS_SKIPPED_when_stage_1_ALREADY_HOLDS():
    """⛔ THE NEGATIVE CONTROL, and the reason the filter exists. The cap is
    STRICTLY WEAKER than the exact bound: `delta in [0, amt]` is implied by
    `delta in [amt, amt]`. Running it anyway would spend a whole esbmc query
    to buy an assertion the test already carries a stronger version of."""
    new, said, written = _stage2_harness(
        [("bal", "post - pre in [amount, amount] with post >= pre", "HOLDS")])
    bad = 0
    bad += check(len(written) == 1,
                 f"only stage 1 was written: {[s for s, _ in written]}")
    bad += check(any("NOT RUN" in s for s in said),
                 f"and the skip is announced with its reason: {said}")
    bad += check(not any(t.startswith("post - pre in [0,")
                         for _v, t, _d in new),
                 f"no cap row merged: {new}")
    return bad


def test_the_CAP_pass_IS_SKIPPED_when_stage_1_gave_NO_VERDICT():
    """⛔ NO-VERDICT IS NOT REFUTED. A solver-unknown on the exact bound says
    nothing about the cap, and re-asking a question the solver already could
    not answer buys a second no-verdict for a second query."""
    new, said, written = _stage2_harness(
        [("bal", "post - pre in [amount, amount] with post >= pre",
          "no verdict (solver unknown)")])
    bad = 0
    bad += check(len(written) == 1,
                 f"only stage 1 was written: {[s for s, _ in written]}")
    bad += check(any("NOT RUN" in s for s in said),
                 f"and the skip is announced: {said}")
    return bad


def test_a_source_R2_HOLD_skips_later_mechanical_candidates_for_that_var():
    from solidity_path_put import run_r2_passes  # noqa: E402
    said, written = [], []

    def write_spec(suffix, spec):
        written.append((suffix, spec))
        return "/tmp/spec" + suffix + ".json"

    replies = [
        [("bal", "post == amount", "HOLDS")],
        [("other", "post == flag_", "HOLDS")],
    ]

    def parse(_text):
        return (replies.pop(0) if replies else []), None, None, None

    specs = [
        {"param": "source_assign", "stage": 1, "kind": "source-assign",
         "vars": [{"name": "bal", "equals": [{
             "id": "src0", "term": {"kind": "coord", "name": "amount"}}],
                   "abs": [], "deltas": []}]},
        {"param": "batch", "stage": 1, "kind": "typed",
         "vars": [
             {"name": "bal", "equals": [{
                 "id": "e0", "term": {"kind": "coord", "name": "amount"}}],
              "abs": [], "deltas": []},
             {"name": "other", "equals": [{
                 "id": "e1", "term": {"kind": "coord", "name": "flag_"}}],
              "abs": [], "deltas": []},
         ]},
    ]
    got = run_r2_passes(specs, {"unit": "u", "enc": 7}, write_spec,
                        lambda _p: "ok", parse, log=said.append)
    bad = 0
    bad += check(len(written) == 2,
                 f"source and remaining typed entries run separately: "
                 f"{[suffix for suffix, _ in written]}")
    if len(written) == 2:
        bad += check(all(spec.get("candidate_policy") == "exact"
                         for _suffix, spec in written),
                     f"R2 passes ask only their explicit candidates: "
                     f"{written}")
        bad += check([entry["name"] for entry in written[1][1]["vars"]] ==
                     ["other"],
                     f"the proven variable is pruned from the typed batch: "
                     f"{written[1][1]['vars']}")
    bad += check(("bal", "post == amount", "HOLDS") in got,
                 f"the source row is retained: {got}")
    bad += check(("other", "post == flag_", "HOLDS") in got,
                 f"unproven variables in the typed batch still run: {got}")
    bad += check(any("pruned 1 mechanical" in line for line in said),
                 f"the prune is visible in logs: {said}")
    return bad


def test_exact_mapping_R2_unknown_is_the_only_CVC5_retry_shape():
    from solidity_path_put import (  # noqa: E402
        parse_ladder, should_retry_exact_mapping_r2_with_cvc5)
    spec = {
        "candidate_policy": "exact",
        "vars": [{"name": "allowance[msg.sender][spender]",
                  "equals": [{"id": "src0",
                              "term": {"kind": "coord",
                                       "name": "amount"}}]}],
    }
    rows, _s, _r, _b = parse_ladder(
        "--path-cov-assert: allowance[msg.sender][spender]: "
        "post == amount  NO VERDICT (solver unknown)\n")
    bad = 0
    bad += check(should_retry_exact_mapping_r2_with_cvc5(spec, rows, []),
                 "exact mapping R2 solver-unknown is retried with CVC5")
    bad += check(not should_retry_exact_mapping_r2_with_cvc5(
        spec, rows, ["--cvc5"]),
                 "an explicit caller solver is respected")
    scalar = dict(spec)
    scalar["vars"] = [{"name": "total",
                       "equals": [{"id": "src0",
                                   "term": {"kind": "coord",
                                            "name": "amount"}}]}]
    bad += check(not should_retry_exact_mapping_r2_with_cvc5(
        scalar, [("total", "post == amount",
                  "NO VERDICT (solver unknown)")], []),
                 "scalar unknowns do not trigger the mapping fallback")
    mixed = dict(spec)
    mixed.pop("candidate_policy")
    bad += check(not should_retry_exact_mapping_r2_with_cvc5(mixed, rows, []),
                 "the broad first ladder is not retried")
    return bad


def test_an_R2_PASS_THAT_RETURNS_NOTHING_is_REPORTED_not_absorbed():
    """⛔ THE FAILING BRANCH. A pass that produced no R2 row means the
    request never reached the ladder. Absorbed silently, the PUT is
    indistinguishable from one where R2 was never asked for -- which is
    exactly how R2 went unrequested for this long without anyone noticing."""
    new, said, _w = _r2_harness([])
    bad = 0
    bad += check(new == [], f"nothing merged: {new}")
    bad += check(any("NO R2 ROW" in s for s in said),
                 f"and the empty pass is announced: {said}")
    return bad


def test_a_ROLLBACK_path_DOES_NOT_SPEND_an_R2_ESBMC_pass():
    from solidity_path_put import maybe_run_r2_passes  # noqa: E402
    said, written, notes = [], [], []

    def write_spec(suffix, spec):
        written.append((suffix, spec))
        return "/tmp/spec" + suffix

    def runner(_path):
        raise AssertionError("rollback R2 must not call ESBMC")

    specs = [{"param": "amount", "stage": 1, "kind": "num",
              "vars": [{"name": "bal",
                        "abs_lo": "amount", "abs_hi": "amount",
                        "delta_dir": "inc",
                        "delta_lo": "amount", "delta_hi": "amount"}]}]
    got = maybe_run_r2_passes(
        specs, {"unit": "u", "enc": 7}, write_spec, runner,
        lambda _text: ([], None, None, None), rollback_here=True,
        notes=notes, log=said.append)
    bad = 0
    bad += check(got == [], f"no R2 rows are merged: {got}")
    bad += check(written == [], f"no R2 spec is written: {written}")
    bad += check(any("R2 ESBMC pass NOT RUN" in s for s in said),
                 f"the skip is logged: {said}")
    bad += check(any("reverting path" in s for s in notes),
                 f"put.json notes will carry the reason: {notes}")
    return bad


def test_a_REVERT_path_DOES_NOT_SPEND_an_R2_ESBMC_pass():
    from solidity_path_put import maybe_run_r2_passes  # noqa: E402
    said, written, notes = [], [], []

    def write_spec(suffix, spec):
        written.append((suffix, spec))
        return "/tmp/spec" + suffix

    def runner(_path):
        raise AssertionError("revert R2 must not call ESBMC")

    specs = [{"param": "amount", "stage": 1, "kind": "num",
              "vars": [{"name": "bal",
                        "abs_lo": "amount", "abs_hi": "amount",
                        "delta_dir": "inc",
                        "delta_lo": "amount", "delta_hi": "amount"}]}]
    got = maybe_run_r2_passes(
        specs, {"unit": "u", "enc": 7}, write_spec, runner,
        lambda _text: ([], None, None, None), revert_here=True,
        notes=notes, log=said.append)
    bad = 0
    bad += check(got == [], f"no R2 rows are merged: {got}")
    bad += check(written == [], f"no R2 spec is written: {written}")
    bad += check(any("Stage-1 says" in s for s in said),
                 f"the skip is logged as a Stage-1 revert: {said}")
    bad += check(any("reverting path" in s for s in notes),
                 f"put.json notes will carry the reason: {notes}")
    return bad


def test_an_R2_PASS_never_overwrites_a_row_the_FIRST_pass_decided():
    """Only delta rows are taken. A second run disagreeing with the first
    about an R1 rung is a fact worth seeing, not a silent update -- and an R1
    row merged twice would double-count in the oracle total."""
    new, _s, _w = _r2_harness(
        [("bal", "post == pre", "HOLDS"),
         ("bal", "post - pre in [amount, amount] with post >= pre", "HOLDS")])
    bad = 0
    bad += check(len(new) == 1, f"only the delta row is merged: {new}")
    bad += check(all("post - pre" in t for _v, t, _d in new),
                 f"and nothing else: {new}")
    return bad


# ---- THE SLOT-NAME PROPOSER ------------------------------------------------
#
# Regression for a defect that reached the CORPUS: the key-type field of a
# `maps` row is a string for one level and a TUPLE for a nested store, and the
# proposer read it as a string unconditionally. aqua dock, farming
# setDistributor 12 and 13 -- three PUTs that had been emitting -- died with
# `'tuple' object has no attribute 'strip'`, while every test here stayed
# green because every fixture mapping was one level.


def test_a_ONE_LEVEL_mapping_proposes_one_key():
    """The one-level spelling of a PARAMETER key must stay byte-identical: if
    the nested branch had changed it, every corpus row would have moved at once
    and the change would have looked like a nesting fix.

    `bal[msg.sender]` now rides ALONGSIDE it, and first. That is the shape a
    real address-keyed mapping actually has, and a proposer drawing only from
    the parameter list could never name it -- the ladder was being asked about
    a slot the unit does not write while the one it does write went unmentioned.
    """
    from solidity_path_put import propose_slot_vars  # noqa: E402
    got = propose_slot_vars(
        {"bal": (2, "address", 32, 0, "bal", None)},
        [("u", "address"), ("amt", "uint256")])
    bad = 0
    bad += check(got == ["bal[msg.sender]", "bal[u]"],
                 f"the caller-keyed name comes first, the parameter-keyed one "
                 f"is unchanged: {got}")
    bad += check("bal[amt]" not in got,
                 "and a uint256 parameter is still not offered for an address "
                 "level")
    return bad


def test_a_NESTED_mapping_proposes_ONE_KEY_PER_LEVEL():
    """⛔ THE CRASH. A tuple key type must produce a two-key name, not an
    exception. `[u][u]` is included on purpose -- one parameter may serve both
    levels and `bal2[u][u]` is a real slot.

    Every level of an address-keyed store offers `msg.sender`, so the cross
    product is 3x3, and the ORDER is the one the budget's prefix depends on:
    msg.sender first at each level, then the parameters by name.
    """
    from solidity_path_put import propose_slot_vars  # noqa: E402
    got = propose_slot_vars(
        {"two": (5, ("address", "address"), 32, 0, "two", None)},
        [("o", "address"), ("s", "address")])
    return check(
        got == ["two[msg.sender][msg.sender]", "two[msg.sender][o]",
                "two[msg.sender][s]", "two[o][msg.sender]", "two[o][o]",
                "two[o][s]", "two[s][msg.sender]", "two[s][o]", "two[s][s]"],
        f"two levels, cross product, msg.sender-first then sorted: {got}")


def test_a_NESTED_STRUCT_mapping_keeps_its_FIELD_TAIL():
    """aqua's shape: nested AND struct-valued. The field tail must survive the
    key cross product, or the name addresses the whole word instead of the
    packed member -- including on the caller-keyed candidates, which is the
    combination aqua actually needs (`_balances[msg.sender][...]`.amount)."""
    from solidity_path_put import propose_slot_vars  # noqa: E402
    got = propose_slot_vars(
        {"pack.amount": (7, ("address", "address"), 31, 0, "pack", "amount")},
        [("o", "address")])
    return check(got == ["pack[msg.sender][msg.sender].amount",
                         "pack[msg.sender][o].amount",
                         "pack[o][msg.sender].amount",
                         "pack[o][o].amount"],
                 f"field tail survives on every candidate: {got}")


def test_a_LEVEL_WITH_NO_MATCHING_PARAMETER_proposes_NOTHING():
    """A partially-keyed name would address a word nothing wrote. Refusing the
    whole store is correct; emitting `two[o]` for a 2-level store is the defect
    `test_a_slot_named_with_the_WRONG_DEPTH_is_refused` catches downstream, and
    it must not be produced here in the first place.

    ALSO THE CONTROL ON `msg.sender`: the address level gains it, the bytes32
    level does not, and one empty level still kills the whole store. If the new
    candidate had been added unconditionally rather than per key type, this test
    would go green with `two[msg.sender][msg.sender]` -- a name whose second key
    is an address where the store wants a bytes32.
    """
    from solidity_path_put import propose_slot_vars  # noqa: E402
    got = propose_slot_vars(
        {"two": (5, ("address", "bytes32"), 32, 0, "two", None)},
        [("o", "address")])
    return check(got == [], f"no bytes32 parameter, so no name at all: {got}")


def test_a_FIXED_BYTES_mapping_level_uses_same_typed_parameter():
    from solidity_path_put import propose_slot_vars  # noqa: E402
    got = propose_slot_vars(
        {"seen": (5, "bytes32", 32, 0, "seen", None)},
        [("digest", "bytes32"), ("who", "address")])
    bad = 0
    bad += check(got == ["seen[digest]"],
                 f"bytes32 key uses the bytes32 parameter only: {got}")
    bad += check("seen[who]" not in got,
                 "an address parameter is not offered for a bytes32 level")
    return bad


def test_mapping_proposer_includes_safe_entry_state_keys_after_params():
    """Real ERC20-like code often writes `balances[owner]`.

    The ladder has to ask that slot directly; otherwise a source-R2 candidate
    may exist but the first assertion pass still measures only param/caller
    slots. Only layout-backed, safely encodable state keys are proposed.
    """
    from solidity_path_put import propose_slot_vars  # noqa: E402
    maps = {
        "bal": (2, "address", 32, 0, "bal", None),
        "quota": (3, "uint256", 32, 0, "quota", None),
        "flagged": (4, "bool", 32, 0, "flagged", None),
        "seen": (5, "bytes32", 32, 0, "seen", None),
    }
    state_types = {
        "owner": "address",
        "limit": "uint256",
        "flag": "bool",
        "digest": "bytes32",
        "unslotted": "address",
    }
    layout = {"owner": (0, 0, 20), "limit": (1, 0, 32),
              "flag": (2, 0, 1), "digest": (3, 0, 32)}
    got = propose_slot_vars(
        maps, [("u", "address"), ("amount", "uint256"), ("ok", "bool"),
               ("digestParam", "bytes32")],
        state_types=state_types, layout=layout, log=lambda _msg: None)
    bad = 0
    bad += check(got == [
        "bal[msg.sender]", "bal[u]", "bal[state.owner]",
        "flagged[ok]", "flagged[state.flag]",
        "quota[amount]", "quota[state.limit]", "seen[digestParam]",
    ], f"safe entry-state keys are proposed after caller/params: {got}")
    bad += check("bal[state.unslotted]" not in got,
                 "a state key absent from solc storage layout is not guessed")
    bad += check("seen[state.digest]" not in got,
                 "bytesN state keys remain refused until ABI spelling is "
                 "modelled exactly")
    return bad


def test_source_access_slots_preserve_state_keys_before_fallback():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "allow",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "owner",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"id": 21, "name": "spender",
                  "typeDescriptions": {"typeString": "address"}}]},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "IndexAccess", "src": "100:5:0",
                     "baseExpression": {"nodeType": "Identifier",
                                        "name": "bal",
                                        "referencedDeclaration": 10},
                     "indexExpression": {"nodeType": "Identifier",
                                         "name": "owner",
                                         "referencedDeclaration": 12}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "IndexAccess", "src": "120:5:0",
                     "baseExpression": {
                         "nodeType": "IndexAccess",
                         "baseExpression": {"nodeType": "Identifier",
                                            "name": "allow",
                                            "referencedDeclaration": 11},
                         "indexExpression": {"nodeType": "Identifier",
                                             "name": "owner",
                                             "referencedDeclaration": 12}},
                     "indexExpression": {"nodeType": "Identifier",
                                         "name": "spender",
                                         "referencedDeclaration": 21}}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    maps = {"bal": (2, "address", 32, 0, "bal", None),
            "allow": (3, ("address", "address"), 32, 0, "allow", None),
            "seen": (4, "bytes32", 32, 0, "seen", None)}
    slots, used, skipped = source_access_slot_vars(
        accesses + [("seen", ("state.owner",))], maps,
        params=[("spender", "address")],
        state_types={"owner": "address"}, layout={"owner": (0, 0, 20)})
    bad = 0
    bad += check(accesses == [
        ("allow", ("state.owner", "spender")),
        ("bal", ("state.owner",)),
    ], f"AST source access keeps state keys distinct from params: {accesses}")
    bad += check(any("state.bal[state.owner]" in line for line in evidence),
                 f"evidence prints the state-keyed source slot: {evidence}")
    bad += check(slots == ["allow[state.owner][spender]",
                           "bal[state.owner]"],
                 f"source-resolved slots are emitted before fallback guesses: "
                 f"{slots}")
    bad += check(used == {"bal", "allow"},
                 f"accepted source slots suppress their fallback maps: {used}")
    bad += check(any("state.seen[state.owner]" in s and "not a safe" in s
                     for s in skipped),
                 f"bytesN/incompatible source key is refused, not guessed: "
                 f"{skipped}")
    return bad


def test_source_access_slots_keep_numeric_environment_keys():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    def member(base, name):
        return {"nodeType": "MemberAccess", "memberName": name,
                "expression": {"nodeType": "Identifier", "name": base}}

    def access(base_ref, base_name, key, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(base_ref, base_name),
            "indexExpression": key}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "paid",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "byTime",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "byHeight",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "byChain",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 14, "name": "byFee",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 15, "name": "byGas",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 16, "name": "byMiner",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 17, "name": "byAddr",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 access(10, "paid", member("msg", "value"), "100:5:0"),
                 access(11, "byTime", member("block", "timestamp"),
                        "120:5:0"),
                 access(12, "byHeight", member("block", "number"),
                        "140:5:0"),
                 access(13, "byChain", member("block", "chainid"),
                        "160:5:0"),
                 access(14, "byFee", member("block", "basefee"),
                        "180:5:0"),
                 access(15, "byGas", member("tx", "gasprice"),
                        "200:5:0"),
                 access(16, "byMiner", member("block", "coinbase"),
                        "220:5:0"),
                 access(17, "byAddr", member("block", "chainid"),
                        "240:5:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    maps = {"paid": (2, "uint256", 32, 0, "paid", None),
            "byTime": (3, "uint256", 32, 0, "byTime", None),
            "byHeight": (4, "uint256", 32, 0, "byHeight", None),
            "byChain": (5, "uint256", 32, 0, "byChain", None),
            "byFee": (6, "uint256", 32, 0, "byFee", None),
            "byGas": (7, "uint256", 32, 0, "byGas", None),
            "byMiner": (8, "address", 32, 0, "byMiner", None),
            "byAddr": (9, "address", 32, 0, "byAddr", None)}
    slots, used, skipped = source_access_slot_vars(
        accesses, maps, params=[], state_types={}, layout={})
    bad = 0
    bad += check(accesses == [
        ("byAddr", ("block.chainid",)),
        ("byChain", ("block.chainid",)),
        ("byFee", ("block.basefee",)),
        ("byGas", ("tx.gasprice",)),
        ("byHeight", ("block.number",)),
        ("byMiner", ("block.coinbase",)),
        ("byTime", ("block.timestamp",)),
        ("paid", ("msg.value",)),
    ], f"environment keys are preserved from source: {accesses}")
    bad += check(any("state.paid[msg.value]" in line for line in evidence),
                 f"environment key evidence is recorded: {evidence}")
    bad += check(slots == ["byChain[block.chainid]",
                           "byFee[block.basefee]",
                           "byGas[tx.gasprice]",
                           "byHeight[block.number]",
                           "byMiner[block.coinbase]",
                           "byTime[block.timestamp]",
                           "paid[msg.value]"],
                 f"environment keys become source slots: {slots}")
    bad += check(used == {"byChain", "byFee", "byGas", "byHeight",
                          "byMiner", "byTime", "paid"},
                 f"accepted env slots suppress fallback maps: {used}")
    bad += check(any("byAddr[block.chainid]" in s
                     and "not safely renderable as `address`" in s
                     for s in skipped),
                 f"incompatible env key remains refused: {skipped}")
    return bad


def test_source_access_slots_unwrap_safe_type_conversion_keys():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    def member(base, name):
        return {"nodeType": "MemberAccess", "memberName": name,
                "expression": {"nodeType": "Identifier", "name": base}}

    def cast(name, arg):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "expression": {"nodeType": "ElementaryTypeNameExpression",
                               "typeName": {
                                   "nodeType": "ElementaryTypeName",
                                   "name": name}},
                "arguments": [arg]}

    def access(base_ref, base_name, key, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(base_ref, base_name),
            "indexExpression": key}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "paid",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "height",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "owner",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "small",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 access(10, "paid", cast("uint256", member("msg", "value")),
                        "100:5:0"),
                 access(11, "height",
                        cast("uint256", member("block", "number")),
                        "120:5:0"),
                 access(12, "owner",
                        cast("address payable", member("msg", "sender")),
                        "140:5:0"),
                 access(13, "small",
                        cast("uint32", member("block", "number")),
                        "160:5:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    maps = {"paid": (2, "uint256", 32, 0, "paid", None),
            "height": (3, "uint256", 32, 0, "height", None),
            "owner": (4, "address", 32, 0, "owner", None),
            "small": (5, "uint32", 32, 0, "small", None)}
    slots, used, skipped = source_access_slot_vars(
        accesses, maps, params=[], state_types={}, layout={})
    bad = 0
    bad += check(accesses == [
        ("height", ("block.number",)),
        ("owner", ("msg.sender",)),
        ("paid", ("msg.value",)),
    ], f"safe type-conversion keys are unwrapped: {accesses}")
    bad += check(not any("small" in name for name, _keys in accesses),
                 f"narrowing uint32(block.number) is not guessed: {accesses}")
    bad += check(any("state.height[block.number]" in line
                     for line in evidence),
                 f"conversion evidence names the unwrapped env key: "
                 f"{evidence}")
    bad += check(slots == ["height[block.number]", "owner[msg.sender]",
                           "paid[msg.value]"],
                 f"unwrapped conversion keys render as source slots: {slots}")
    bad += check(used == {"height", "owner", "paid"} and skipped == [],
                 f"accepted conversion slots suppress fallback: {used}, "
                 f"{skipped}")
    return bad


def test_source_access_slots_keep_safe_literal_keys():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    def literal(kind, value):
        return {"nodeType": "Literal", "kind": kind, "value": value}

    def hex_lit(value):
        return {"nodeType": "Literal", "kind": "hexString",
                "hexValue": value, "value": value}

    def address_cast(arg):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": "address"}},
                "arguments": [arg]}

    def access(base_ref, base_name, key, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(base_ref, base_name),
            "indexExpression": key}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "count",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "flagged",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "owners",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 13, "name": "bytesMap",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 access(10, "count", literal("number", "7"), "100:5:0"),
                 access(11, "flagged", literal("bool", True), "120:5:0"),
                 access(12, "owners",
                        address_cast(literal("number", "1")), "140:5:0"),
                 access(13, "bytesMap", hex_lit("12ab"), "160:5:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    maps = {"count": (0, "uint256", 32, 0, "count", None),
            "flagged": (1, "bool", 32, 0, "flagged", None),
            "owners": (2, "address", 32, 0, "owners", None),
            "bytesMap": (3, "bytes4", 32, 0, "bytesMap", None)}
    slots, used, skipped = source_access_slot_vars(accesses, maps)
    bad = 0
    bad += check(accesses == [
        ("bytesMap", ("0x12ab",)),
        ("count", ("7",)),
        ("flagged", ("1",)),
        ("owners", ("1",)),
    ], f"AST source access preserves safe literal key spellings: {accesses}")
    bad += check(any("state.flagged[1]" in line for line in evidence),
                 f"bool literal source key is printed as 1: {evidence}")
    bad += check(slots == ["count[7]", "flagged[1]", "owners[1]"],
                 f"safe literal-key source slots are accepted: {slots}")
    bad += check(used == {"count", "flagged", "owners"},
                 f"accepted literal slots suppress fallback: {used}")
    bad += check(any("state.bytesMap[0x12ab]" in s
                     and "not safely renderable" in s for s in skipped),
                 f"bytesN literal key is refused, not guessed: {skipped}")
    return bad


def test_source_access_slots_fold_safe_constant_keys():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    def num(value):
        return {"nodeType": "Literal", "kind": "number", "value": str(value)}

    def boolean(value):
        return {"nodeType": "Literal", "kind": "bool", "value": value}

    def hex_lit(value):
        return {"nodeType": "Literal", "kind": "hexString",
                "hexValue": value, "value": value}

    def address_cast(arg):
        return {"nodeType": "FunctionCall", "kind": "typeConversion",
                "expression": {
                    "nodeType": "ElementaryTypeNameExpression",
                    "typeName": {"nodeType": "ElementaryTypeName",
                                 "name": "address"}},
                "arguments": [arg]}

    def binop():
        return {"nodeType": "BinaryOperation", "operator": "+",
                "leftExpression": num(1), "rightExpression": num(2)}

    def const_decl(ref, name, ty, value):
        return {"nodeType": "VariableDeclaration", "id": ref, "name": name,
                "stateVariable": True, "constant": True, "value": value,
                "typeDescriptions": {"typeString": ty}}

    def state_decl(ref, name):
        return {"nodeType": "VariableDeclaration", "id": ref, "name": name,
                "stateVariable": True}

    def access(base_ref, base_name, key_ref, key_name, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(base_ref, base_name),
            "indexExpression": ident(key_ref, key_name)}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            const_decl(30, "K", "uint256", num(9)),
            const_decl(31, "ON", "bool", boolean(True)),
            const_decl(32, "A", "address", address_cast(num(2))),
            const_decl(33, "B", "bytes4", hex_lit("beef")),
            const_decl(34, "BAD", "uint256", binop()),
            state_decl(10, "count"),
            state_decl(11, "flagged"),
            state_decl(12, "owners"),
            state_decl(13, "bytesMap"),
            state_decl(14, "badMap"),
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 access(10, "count", 30, "K", "100:5:0"),
                 access(11, "flagged", 31, "ON", "120:5:0"),
                 access(12, "owners", 32, "A", "140:5:0"),
                 access(13, "bytesMap", 33, "B", "160:5:0"),
                 access(14, "badMap", 34, "BAD", "180:5:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    maps = {"count": (0, "uint256", 32, 0, "count", None),
            "flagged": (1, "bool", 32, 0, "flagged", None),
            "owners": (2, "address", 32, 0, "owners", None),
            "bytesMap": (3, "bytes4", 32, 0, "bytesMap", None),
            "badMap": (4, "uint256", 32, 0, "badMap", None)}
    slots, used, skipped = source_access_slot_vars(accesses, maps)
    bad = 0
    bad += check(accesses == [
        ("bytesMap", ("0xbeef",)),
        ("count", ("9",)),
        ("flagged", ("1",)),
        ("owners", ("2",)),
    ], f"safe constant keys fold to source slot literals: {accesses}")
    bad += check(not any("BAD" in line for line in evidence),
                 f"complex constants are not guessed as slot keys: {evidence}")
    bad += check(slots == ["count[9]", "flagged[1]", "owners[2]"],
                 f"safe constant-key source slots are accepted: {slots}")
    bad += check(used == {"count", "flagged", "owners"},
                 f"accepted constant slots suppress fallback: {used}")
    bad += check(any("state.bytesMap[0xbeef]" in s
                     and "not safely renderable" in s for s in skipped),
                 f"bytesN constant key is still refused: {skipped}")
    return bad


def test_source_access_slots_resolve_local_key_aliases_in_order():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"}}

    def local_decl(ref, name, init=None):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{"nodeType": "VariableDeclaration",
                                  "id": ref, "name": name}],
                "initialValue": init}

    def assign(lhs, rhs):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "Assignment", "operator": "=",
            "leftHandSide": lhs, "rightHandSide": rhs}}

    def access(key, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(10, "bal"),
            "indexExpression": key}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "owner",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "admin",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 local_decl(32, "late"),
                 assign(ident(32, "late"), msg_sender),
                 access(ident(32, "late"), "90:5:0"),
                 local_decl(30, "sender", msg_sender),
                 access(ident(30, "sender"), "100:5:0"),
                 local_decl(31, "who", ident(11, "owner")),
                 access(ident(31, "who"), "120:5:0"),
                 assign(ident(31, "who"), ident(12, "admin")),
                 access(ident(31, "who"), "140:5:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    slots, used, skipped = source_access_slot_vars(
        accesses, {"bal": (2, "address", 32, 0, "bal", None)},
        state_types={"admin": "address", "owner": "address"},
        layout={"admin": (0, 0, 20), "owner": (1, 0, 20)})
    bad = 0
    bad += check(accesses == [
        ("bal", ("msg.sender",)),
        ("bal", ("state.admin",)),
        ("bal", ("state.owner",)),
    ], f"local aliases resolve in statement order and assignments update: "
        f"{accesses}")
    bad += check(any("state.bal[msg.sender]" in line for line in evidence)
                 and any("state.bal[state.owner]" in line for line in evidence)
                 and any("state.bal[state.admin]" in line for line in evidence),
                 f"evidence records the resolved alias keys: {evidence}")
    bad += check(slots == ["bal[msg.sender]", "bal[state.admin]",
                           "bal[state.owner]"],
                 f"only live renderable aliases become source slots: {slots}")
    bad += check(used == {"bal"},
                 f"accepted alias slots suppress fallback: {used}")
    bad += check(skipped == [],
                 f"assigned aliases do not leak stale local names: {skipped}")
    return bad


def test_source_access_slots_substitute_helper_and_modifier_actuals():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"}}

    def local_decl(ref, name, init):
        return {"nodeType": "VariableDeclarationStatement",
                "declarations": [{"nodeType": "VariableDeclaration",
                                  "id": ref, "name": name}],
                "initialValue": init}

    def access(key, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(10, "bal"),
            "indexExpression": key}}

    def helper_call(arg, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "FunctionCall", "src": src,
            "expression": ident(30, "touchOne"), "arguments": [arg]}}

    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "owner",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "admin",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "touchOne",
             "parameters": {"parameters": [
                 {"nodeType": "VariableDeclaration", "id": 31,
                  "name": "who"}]},
             "body": {"nodeType": "Block", "statements": [
                 access(ident(31, "who"), "300:5:0")]}},
            {"nodeType": "ModifierDefinition", "id": 40, "name": "guard",
             "parameters": {"parameters": [
                 {"nodeType": "VariableDeclaration", "id": 41,
                  "name": "auth"}]},
             "body": {"nodeType": "Block", "statements": [
                 access(ident(41, "auth"), "400:5:0"),
                 {"nodeType": "PlaceholderStatement"}]}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"nodeType": "VariableDeclaration", "id": 21,
                  "name": "to"}]},
             "modifiers": [{
                 "nodeType": "ModifierInvocation",
                 "modifierName": ident(40, "guard"),
                 "arguments": [ident(12, "admin")]}],
             "body": {"nodeType": "Block", "statements": [
                 helper_call(ident(21, "to"), "100:5:0"),
                 helper_call(ident(11, "owner"), "120:5:0"),
                 local_decl(50, "sender", msg_sender),
                 helper_call(ident(50, "sender"), "140:5:0")]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    slots, used, skipped = source_access_slot_vars(
        accesses, {"bal": (2, "address", 32, 0, "bal", None)},
        params=[("to", "address")],
        state_types={"admin": "address", "owner": "address"},
        layout={"admin": (0, 0, 20), "owner": (1, 0, 20)})
    bad = 0
    bad += check(accesses == [
        ("bal", ("msg.sender",)),
        ("bal", ("state.admin",)),
        ("bal", ("state.owner",)),
        ("bal", ("to",)),
    ], f"helper/modifier formals are substituted with caller actuals: "
        f"{accesses}")
    bad += check(any("modifier guard#40" in line and "state.bal[state.admin]" in line
                     for line in evidence),
                 f"modifier actual substitution is visible in evidence: "
                 f"{evidence}")
    bad += check(slots == ["bal[msg.sender]", "bal[state.admin]",
                           "bal[state.owner]", "bal[to]"],
                 f"substituted helper/modifier slots are renderable: {slots}")
    bad += check(used == {"bal"} and skipped == [],
                 f"all substituted slots are accepted: {used}, {skipped}")
    return bad


def test_source_access_slots_treat_msg_sender_helper_actual_as_sender():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name, ty="address"):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref,
                "typeDescriptions": {"typeString": ty}}

    msg_sender = {"nodeType": "MemberAccess", "memberName": "sender",
                  "expression": {"nodeType": "Identifier", "name": "msg"},
                  "typeDescriptions": {"typeString": "address"}}

    def call(ref, name, args=None, ty="address"):
        return {"nodeType": "FunctionCall", "arguments": list(args or []),
                "expression": ident(ref, name, ty),
                "typeDescriptions": {"typeString": ty}}

    def index(base, key, ty="uint256"):
        return {"nodeType": "IndexAccess",
                "baseExpression": base,
                "indexExpression": key,
                "typeDescriptions": {"typeString": ty}}

    allow_owner = index(
        ident(10, "_allowances",
              "mapping(address => mapping(address => uint256))"),
        ident(31, "owner", "address"),
        "mapping(address => uint256)")
    allow_owner_spender = index(
        allow_owner, ident(32, "spender", "address"), "uint256")
    ast = {"nodeType": "SourceUnit", "nodes": [
        {"nodeType": "ContractDefinition", "name": "Context", "id": 1,
         "linearizedBaseContracts": [1], "nodes": [
             {"nodeType": "FunctionDefinition", "id": 30,
              "name": "_msgSender",
              "parameters": {"parameters": []},
              "returnParameters": {"parameters": [{
                  "id": 34, "name": "",
                  "typeDescriptions": {"typeString": "address"}}]},
              "body": {"nodeType": "Block", "statements": [
                  {"nodeType": "Return", "expression": msg_sender}]}}]},
        {"nodeType": "ContractDefinition", "name": "Token", "id": 2,
         "linearizedBaseContracts": [2, 1], "nodes": [
             {"nodeType": "VariableDeclaration", "id": 10,
              "name": "_allowances", "stateVariable": True},
             {"nodeType": "FunctionDefinition", "id": 20, "name": "approve",
              "parameters": {"parameters": [
                  {"nodeType": "VariableDeclaration", "id": 21,
                   "name": "spender",
                   "typeDescriptions": {"typeString": "address"}},
                  {"nodeType": "VariableDeclaration", "id": 22,
                   "name": "amount",
                   "typeDescriptions": {"typeString": "uint256"}}]},
              "body": {"nodeType": "Block", "statements": [{
                  "nodeType": "ExpressionStatement",
                  "expression": call(40, "_approve", [
                      call(30, "_msgSender", []),
                      ident(21, "spender", "address"),
                      ident(22, "amount", "uint256")], "tuple()")}]}},
             {"nodeType": "FunctionDefinition", "id": 40, "name": "_approve",
              "parameters": {"parameters": [
                  {"nodeType": "VariableDeclaration", "id": 31,
                   "name": "owner",
                   "typeDescriptions": {"typeString": "address"}},
                  {"nodeType": "VariableDeclaration", "id": 32,
                   "name": "spender",
                   "typeDescriptions": {"typeString": "address"}},
                  {"nodeType": "VariableDeclaration", "id": 33,
                   "name": "amount",
                   "typeDescriptions": {"typeString": "uint256"}}]},
              "body": {"nodeType": "Block", "statements": [{
                  "nodeType": "ExpressionStatement",
                  "expression": {
                      "nodeType": "Assignment", "operator": "=",
                      "leftHandSide": allow_owner_spender,
                      "rightHandSide": ident(33, "amount", "uint256")}}]}}
         ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "Token", "approve", declaration_id=20)
    finally:
        os.unlink(path)
    slots, used, skipped = source_access_slot_vars(
        accesses,
        {"_allowances": (3, ("address", "address"), 32, 0,
                         "_allowances", None)},
        params=[("spender", "address"), ("amount", "uint256")])
    bad = 0
    bad += check(accesses == [("_allowances",
                               ("msg.sender", "spender"))],
                 f"_msgSender() actual is substituted before source slot "
                 f"selection: {accesses}")
    bad += check(any("state._allowances[msg.sender][spender]" in line
                     for line in evidence),
                 f"evidence names the exact ERC20 approval slot: {evidence}")
    bad += check(slots == ["_allowances[msg.sender][spender]"],
                 f"source slot filter asks the exact approval slot only: "
                 f"{slots}")
    bad += check(used == {"_allowances"} and skipped == [],
                 f"accepted exact slot suppresses cross-product fallback: "
                 f"{used}, {skipped}")
    return bad


def test_source_access_slots_follow_call_options_wrapped_helpers():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import source_access_slot_vars  # noqa: E402

    def ident(ref, name):
        return {"nodeType": "Identifier", "name": name,
                "referencedDeclaration": ref}

    def access(key, src):
        return {"nodeType": "ExpressionStatement", "expression": {
            "nodeType": "IndexAccess", "src": src,
            "baseExpression": ident(10, "bal"),
            "indexExpression": key}}

    wrapped_callee = {
        "nodeType": "FunctionCallOptions",
        "expression": ident(30, "touchOne"),
        "options": [{
            "nodeType": "Identifier", "name": "gas",
            "referencedDeclaration": None,
        }],
        "names": ["gas"],
    }
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 30, "name": "touchOne",
             "parameters": {"parameters": [
                 {"nodeType": "VariableDeclaration", "id": 31,
                  "name": "who"}]},
             "body": {"nodeType": "Block", "statements": [
                 access(ident(31, "who"), "300:5:0")]}},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": [
                 {"nodeType": "VariableDeclaration", "id": 21,
                  "name": "to"}]},
             "body": {"nodeType": "Block", "statements": [{
                 "nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "FunctionCall", "src": "100:5:0",
                     "expression": wrapped_callee,
                     "arguments": [ident(21, "to")]}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    slots, used, skipped = source_access_slot_vars(
        accesses, {"bal": (2, "address", 32, 0, "bal", None)},
        params=[("to", "address")], state_types={}, layout={})
    bad = 0
    bad += check(accesses == [("bal", ("to",))],
                 f"call-options wrapped helper is followed: {accesses}")
    bad += check(any("function touch#20 -> function touchOne#30" in line
                     and "state.bal[to]" in line for line in evidence),
                 f"wrapped helper provenance is preserved: {evidence}")
    bad += check(slots == ["bal[to]"] and used == {"bal"} and skipped == [],
                 f"wrapped helper slot is renderable: {slots}, {used}, "
                 f"{skipped}")
    return bad


def test_source_access_slots_render_state_struct_member_keys():
    from solidity_ast_dependencies import unit_mapping_slot_accesses  # noqa: E402
    from solidity_path_put import (contract_state_types,  # noqa: E402
                                   source_access_slot_vars)
    ast = {"nodeType": "SourceUnit", "nodes": [{
        "nodeType": "ContractDefinition", "name": "C", "id": 1,
        "linearizedBaseContracts": [1], "nodes": [
            {"nodeType": "StructDefinition", "id": 30, "name": "Config",
             "members": [
                 {"nodeType": "VariableDeclaration", "id": 31,
                  "name": "owner",
                  "typeDescriptions": {"typeString": "address"}},
                 {"nodeType": "VariableDeclaration", "id": 32,
                  "name": "digest",
                  "typeDescriptions": {"typeString": "bytes32"}}]},
            {"nodeType": "VariableDeclaration", "id": 10, "name": "bal",
             "stateVariable": True},
            {"nodeType": "VariableDeclaration", "id": 11, "name": "cfg",
             "stateVariable": True,
             "typeName": {"nodeType": "UserDefinedTypeName",
                          "referencedDeclaration": 30},
             "typeDescriptions": {"typeString": "struct C.Config"}},
            {"nodeType": "VariableDeclaration", "id": 12, "name": "balAlias",
             "stateVariable": True},
            {"nodeType": "FunctionDefinition", "id": 20, "name": "touch",
             "parameters": {"parameters": []},
             "body": {"nodeType": "Block", "statements": [
                 {"nodeType": "VariableDeclarationStatement",
                  "declarations": [{
                      "nodeType": "VariableDeclaration", "id": 40,
                      "name": "c", "storageLocation": "storage",
                      "typeDescriptions": {
                          "typeString": "struct C.Config"}}],
                  "initialValue": {"nodeType": "Identifier",
                                   "name": "cfg",
                                   "referencedDeclaration": 11}},
                 {
                 "nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "IndexAccess", "src": "100:5:0",
                     "baseExpression": {"nodeType": "Identifier",
                                        "name": "bal",
                                        "referencedDeclaration": 10},
                     "indexExpression": {
                         "nodeType": "MemberAccess", "memberName": "owner",
                         "expression": {"nodeType": "Identifier",
                                        "name": "cfg",
                                        "referencedDeclaration": 11}}}},
                 {"nodeType": "ExpressionStatement", "expression": {
                     "nodeType": "IndexAccess", "src": "120:5:0",
                     "baseExpression": {"nodeType": "Identifier",
                                        "name": "balAlias",
                                        "referencedDeclaration": 12},
                     "indexExpression": {
                         "nodeType": "MemberAccess", "memberName": "owner",
                         "expression": {"nodeType": "Identifier",
                                        "name": "c",
                                        "referencedDeclaration": 40}}}}]}}
        ]}]}
    fd, path = tempfile.mkstemp(suffix=".solast")
    with os.fdopen(fd, "w") as out:
        json.dump(ast, out)
    try:
        state_types = contract_state_types(path, "C")
        accesses, evidence = unit_mapping_slot_accesses(
            path, "C", "touch", declaration_id=20)
    finally:
        os.unlink(path)
    slots, used, skipped = source_access_slot_vars(
        accesses, {"bal": (2, "address", 32, 0, "bal", None),
                   "balAlias": (3, "address", 32, 0, "balAlias", None)},
        state_types=state_types,
        layout={"cfg.owner": (1, 0, 20), "cfg.digest": (2, 0, 32)})
    bad = 0
    bad += check(state_types.get("cfg.owner") == "address",
                 f"struct member state type is exported: {state_types}")
    bad += check(accesses == [("bal", ("state.cfg.owner",)),
                              ("balAlias", ("state.cfg.owner",))],
                 f"AST source access names the struct member key: {accesses}")
    bad += check(any("state.bal[state.cfg.owner]" in line
                     for line in evidence),
                 f"evidence preserves the struct member key: {evidence}")
    bad += check(any("state.balAlias[state.cfg.owner]" in line
                     and "AST src 120:5:0" in line for line in evidence),
                 f"local struct alias member access reaches evidence: "
                 f"{evidence}")
    bad += check(slots == ["bal[state.cfg.owner]",
                           "balAlias[state.cfg.owner]"],
                 f"struct member key source slot is renderable: {slots}")
    bad += check(used == {"bal", "balAlias"} and skipped == [],
                 f"accepted without fallback or skip noise: {used}, {skipped}")
    return bad


def test_the_CANDIDATE_BUDGET_says_what_it_dropped():
    """⛔ NO SILENT CAP. Four levels against three address parameters PLUS the
    caller is 4^4 = 256 names; the cap keeps 24 and must SAY so, or a truncated
    candidate set reads as 'the tool found only these'.

    ⛔ AND THIS IS WHERE `msg.sender`-FIRST EARNS ITS PLACE. Sorted by name it
    would fall behind `a`, `b` and `c`, and on a four-level store the 24-name
    prefix would contain not one candidate mentioning it -- the cap would
    silently undo the change on exactly the deepest store, which is the one
    whose outer key is most certainly the caller.
    """
    from solidity_path_put import propose_slot_vars  # noqa: E402
    said = []
    got = propose_slot_vars(
        {"deep": (9, ("address",) * 4, 32, 0, "deep", None)},
        [("a", "address"), ("b", "address"), ("c", "address")],
        log=said.append)
    bad = 0
    bad += check(len(got) == 24, f"the cap holds: {len(got)}")
    bad += check(any("DROPPING 232" in s for s in said),
                 f"and it names what it dropped: {said}")
    bad += check(got[0] == "deep[msg.sender][msg.sender][msg.sender]"
                           "[msg.sender]",
                 f"the kept ones are the msg.sender-first prefix, not an "
                 f"arbitrary subset: {got[0]}")
    bad += check(any("[a]" in g for g in got),
                 "and the parameter-keyed candidates are NOT starved out -- "
                 "the prefix reaches past the all-caller corner")
    return bad


# ---- R2 WITH A NAMED ENDPOINT ---------------------------------------------
#
# A SEPARATE layout and fixture, deliberately. Widening the shared LAYOUT would
# change the input of every slot test at once, and those tests are the negative
# control for this work -- their output must stay bit-identical.
R2_LAYOUT = {"owner": (0, 0, 20), "feeReceiver": (1, 0, 20),
             "totalFees": (2, 0, 32)}


def _r2_put(ladder, region=None, r2_terms=None, emitted_text=None):
    """A PUT over a caller-chosen ladder, for the named-R2-bound tests."""
    if emitted_text is None:
        em, case = make_case()
    else:
        fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
        with os.fdopen(fd, "w") as out:
            out.write(emitted_text)
        try:
            em = EmittedFile(path)
        finally:
            os.unlink(path)
        case = em.case_for("sol:@C@FeeVault@F@setDiscount#61", 7)
        assert case is not None, "fixture: the emitted case was not found"
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region=region or {"bps": (0, 100), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em,
        case=case, layout=R2_LAYOUT, ladder_rows=ladder, notes=notes,
        maps=SLOT_MAPS, r2_terms=r2_terms)
    return put, stats, notes


def test_an_ADDRESS_endpoint_renders_for_an_ABSOLUTE_bound():
    """⛔ THE SETTER PROPERTY, PROVEN AND THEN THROWN AWAY AT THE LAST STEP.

    MEASURED on farming setDistributor enc=13, in the run that added the
    absolute-bound request. The ladder answered

        _distributor: post in [distributor_, distributor_]   HOLDS

    -- `the state ends equal to the argument`, which IS the whole property of
    a setter and the strongest oracle available on that unit -- and the
    emitter dropped it with `rung shape not rendered`, because the address
    coordinate was deliberately kept out of the endpoint table.

    The reason it was kept out is sound for a DELTA (`post - pre == an
    address` is meaningless) and does not transfer to an ABSOLUTE bound. Two
    questions, one flag: the same conflation the R2 proposer had on the
    request side, sitting on the render side.

    The slot read is a uint256, so the endpoint must carry the cast."""
    from solidity_path_put import rung_assertions  # noqa: E402
    got = rung_assertions("post in [d_, d_]", "_pre_x", "_post_x", "x: abs",
                          idents={},               # arithmetic table: empty
                          idents_abs={"d_": "uint256(uint160(d_))"})
    bad = 0
    bad += check(got is not None, "the absolute rung RENDERS at all")
    if got is None:
        return bad + 2
    body = "\n".join(got)
    bad += check("assertGe(_post_x, uint256(uint160(d_))," in body,
                 f"lower endpoint is the cast address parameter: {body}")
    bad += check("assertLe(_post_x, uint256(uint160(d_))," in body,
                 f"and so is the upper: {body}")
    return bad


def test_an_ADDRESS_endpoint_is_STILL_REFUSED_for_a_DELTA_bound():
    """⛔ THE NEGATIVE CONTROL, and the rule the change must not break. The
    difference of two balances is not an address, so `post - pre in [d_, d_]`
    is not a weaker question but a meaningless one. The arithmetic table is
    what the delta shapes read, and the address is NOT in it -- so the rung is
    dropped whole rather than rendered against a nonsense endpoint.

    MUST FLIP against the absolute arm above: same endpoint name, same tables,
    opposite outcome."""
    from solidity_path_put import rung_assertions  # noqa: E402
    got = rung_assertions("post - pre in [d_, d_] with post >= pre",
                          "_pre_x", "_post_x", "x: delta",
                          idents={},
                          idents_abs={"d_": "uint256(uint160(d_))"})
    bad = 0
    bad += check(got is None,
                 f"the delta rung is DROPPED, not rendered against an "
                 f"address: {got}")
    # And an arithmetic endpoint still renders, so the refusal above is about
    # the TYPE and not about the table being empty.
    ok = rung_assertions("post - pre in [amt, amt] with post >= pre",
                         "_pre_x", "_post_x", "x: delta",
                         idents={"amt": "amt"})
    bad += check(ok is not None and any("- _pre_x, amt," in l for l in ok),
                 f"while an arithmetic endpoint still renders: {ok}")
    return bad


def test_a_named_R2_bound_renders_as_the_test_parameter():
    """THE POINT OF THE WHOLE CHANGE. `post - pre in [bps, bps]` is the shape
    a deposit-like unit is actually about, and until now R2 could only carry a
    decimal -- which on a fuzzed parameter is false on all but one draw and had
    to be dropped. The endpoint must come out as the test's OWN identifier."""
    put, _stats, notes = _r2_put(
        [("totalFees", "post - pre in [bps, bps] with post >= pre", "HOLDS")])
    bad = 0
    bad += check(put is not None, f"the PUT is emitted (notes: {notes})")
    if put is None:
        return bad + 1
    # ⛔ `put` is a LIST OF LINES. `"..." in put` is an ELEMENT test, which is
    # false for every substring -- so a positive check written that way fails
    # loudly (harmless) and a `not in` check passes VACUOUSLY (an always-true
    # reader). Join once, here, and compare against text.
    body = "\n".join(put)
    bad += check("assertGe(_post_totalFees - _pre_totalFees, bps," in body,
                 "the lower endpoint is the parameter `bps`, not a literal")
    bad += check("assertLe(_post_totalFees - _pre_totalFees, bps," in body,
                 "and so is the upper endpoint")
    if bad:
        print(body)
    return bad


def test_an_R2_bound_naming_an_UNLIFTED_COORDINATE_is_DROPPED():
    """⛔ THE REFUSING BRANCH, which is the one that decides whether this
    change is safe. `u` is a real, lifted, spellable parameter -- and an
    ADDRESS, so it is not a number and must not bound anything. `qty` is not a
    coordinate at all. Rendering either would produce a file `forge build`
    rejects, i.e. it would break the whole suite rather than lose one
    assertion. A numeric rung rides along so the PUT is emitted either way and
    the test cannot pass by the PUT simply being absent."""
    put, _stats, notes = _r2_put(
        [("totalFees", "post in [0, 500]", "HOLDS"),
         ("owner", "post - pre in [u, u] with post >= pre", "HOLDS"),
         ("feeReceiver", "post in [qty, qty]", "HOLDS")])
    bad = 0
    bad += check(put is not None, f"the PUT is emitted (notes: {notes})")
    if put is None:
        return bad + 1
    body = "\n".join(put)
    bad += check("assertGe(_post_totalFees, 0," in body,
                 "the numeric rung that rides along IS rendered")
    bad += check("_post_owner - _pre_owner" not in body,
                 "the address-named endpoint produced NO assertion")
    bad += check("assertGe(_post_feeReceiver" not in body
                 and "assertLe(_post_feeReceiver" not in body,
                 "and neither did the endpoint naming a non-coordinate")
    # AND THE DROP IS VISIBLE. Silence here would be the same artefact as a
    # rendered rung from the reader's side: a test with two fewer assertions
    # and no statement anywhere that two were refused.
    bad += check("rung DROPPED: owner: post - pre in [u, u]" in body,
                 "the address-named drop is REPORTED on the test")
    bad += check("rung DROPPED: feeReceiver: post in [qty, qty]" in body,
                 "and so is the non-coordinate drop")
    if bad:
        print(body)
    return bad


def test_an_OBSERVED_sender_renders_for_an_ABSOLUTE_R2_endpoint():
    """`owner = msg.sender` is a high-value source R2 fact.

    The sender need not be a fuzzed coordinate for this scalar equality: the
    emitted call already has a governing `vm.prank`, and the oracle can compare
    against that exact caller. Mapping slots stay under the stricter
    `key_expr_of` gate; this only widens the absolute endpoint table.
    """
    put, stats, notes = _r2_put(
        [("owner", "post == msg.sender", "HOLDS")],
        r2_terms={"msg.sender": {"kind": "coord", "name": "msg.sender"}})
    bad = 0
    bad += check(put is not None, f"the PUT is emitted (notes: {notes})")
    if put is None:
        return bad + 3
    body = "\n".join(put)
    bad += check("assertEq(_post_owner, uint256(uint160(0))" in body,
                 "the observed prank sender becomes the absolute endpoint")
    bad += check("_post_owner - _pre_owner" not in body,
                 "the sender was not admitted as an arithmetic delta endpoint")
    bad += check(stats.get("asserts") == 1,
                 f"the sender equality is counted as a real oracle: {stats}")
    fixed, fixed_stats, fixed_notes = _r2_put(
        [("owner", "post == msg.sender", "HOLDS")],
        region={"bps": (0, 100), "u": (0, (1 << 160) - 1),
                "msg.sender": (5, 5)},
        r2_terms={"msg.sender": {"kind": "coord", "name": "msg.sender"}})
    fixed_body = "\n".join(fixed or [])
    bad += check(fixed is not None,
                 f"the width-one sender PUT is emitted (notes: {fixed_notes})")
    bad += check("vm.prank(address(uint160(5)));" in fixed_body,
                 "the governing prank is rewritten to the certified sender")
    bad += check("assertEq(_post_owner, uint256(uint160(address(uint160(5))))"
                 in fixed_body,
                 "and that established sender is also an absolute endpoint")
    bad += check("address p_msg_sender" not in fixed_body
                 and fixed_stats.get("fuzz_params") == 2,
                 f"the width-one sender does not become a fuzz parameter: "
                 f"{fixed_stats}")
    if bad:
        print(body)
        print(fixed_body)
    return bad


def test_an_OBSERVED_msg_value_renders_for_numeric_R2_endpoints():
    """A call without `{value:}` runs with `msg.value == 0`.

    That observed literal is safe for both absolute and delta numeric endpoints:
    it does not prove anything about other values, but it does let a certified
    row that names the emitted call's value become a real oracle instead of a
    dropped rung.
    """
    terms = {"msg.value": {"kind": "coord", "name": "msg.value"}}
    absolute, abs_stats, abs_notes = _r2_put(
        [("totalFees", "post == msg.value", "HOLDS")],
        r2_terms=terms)
    delta, delta_stats, delta_notes = _r2_put(
        [("totalFees", "post - pre in [msg.value, msg.value] with post >= pre",
          "HOLDS")],
        r2_terms=terms)
    abs_body = "\n".join(absolute or [])
    delta_body = "\n".join(delta or [])
    bad = 0
    bad += check(absolute is not None,
                 f"the absolute value PUT is emitted (notes: {abs_notes})")
    bad += check("assertEq(_post_totalFees, 0," in abs_body,
                 "the absent value option renders as the EVM default 0")
    bad += check(abs_stats.get("asserts") == 1,
                 f"the absolute value equality is counted: {abs_stats}")
    bad += check(delta is not None,
                 f"the delta value PUT is emitted (notes: {delta_notes})")
    bad += check("assertGe(_post_totalFees - _pre_totalFees, 0," in delta_body
                 and "assertLe(_post_totalFees - _pre_totalFees, 0,"
                 in delta_body,
                 "the observed value is also a numeric delta endpoint")
    bad += check(delta_stats.get("asserts") == 3,
                 f"the delta value oracle has all three assertions: "
                 f"{delta_stats}")
    expr_term = {
        "kind": "op",
        "op": "add",
        "lhs": {"kind": "pre"},
        "rhs": {"kind": "coord", "name": "msg.value"},
    }
    expr, expr_stats, expr_notes = _r2_put(
        [("totalFees", "post == (pre + msg.value)", "HOLDS")],
        r2_terms={"(pre + msg.value)": expr_term})
    expr_body = "\n".join(expr or [])
    bad += check(expr is not None,
                 f"the structured value PUT is emitted (notes: {expr_notes})")
    bad += check("assertEq(_post_totalFees, (_pre_totalFees + 0),"
                 in expr_body,
                 "the observed value feeds structured R2 expressions too")
    bad += check(expr_stats.get("asserts") == 1,
                 f"the structured value equality is counted: {expr_stats}")
    if bad:
        print(abs_body)
        print(delta_body)
        print(expr_body)
    return bad


def test_OBSERVED_block_cheatcodes_render_for_numeric_R2_endpoints():
    """Literal numeric environment cheatcodes are observable one-point envs."""
    with_block = EMITTED.replace(
        "    vm.prank(address(uint160(0)));\n",
        "    vm.warp(42);\n"
        "    vm.roll(7);\n"
        "    vm.chainId(31337);\n"
        "    vm.fee(101);\n"
        "    vm.prevrandao(uint256(202));\n"
        "    vm.txGasPrice(303);\n"
        "    vm.coinbase(address(uint160(404)));\n"
        "    vm.prank(address(uint160(0)));\n")
    terms = {
        "block.timestamp": {"kind": "coord", "name": "block.timestamp"},
        "block.number": {"kind": "coord", "name": "block.number"},
        "block.chainid": {"kind": "coord", "name": "block.chainid"},
    }
    stamp, stamp_stats, stamp_notes = _r2_put(
        [("totalFees", "post == block.timestamp", "HOLDS")],
        r2_terms=terms, emitted_text=with_block)
    height, height_stats, height_notes = _r2_put(
        [("totalFees", "post - pre in [block.number, block.number] "
          "with post >= pre", "HOLDS")],
        r2_terms=terms, emitted_text=with_block)
    chain, chain_stats, chain_notes = _r2_put(
        [("totalFees", "post == block.chainid", "HOLDS")],
        r2_terms=terms, emitted_text=with_block)
    stamp_body = "\n".join(stamp or [])
    height_body = "\n".join(height or [])
    chain_body = "\n".join(chain or [])
    bad = 0
    bad += check(stamp is not None,
                 f"the timestamp endpoint PUT is emitted: {stamp_notes}")
    bad += check("assertEq(_post_totalFees, 42," in stamp_body,
                 "the observed warp literal becomes the timestamp endpoint")
    bad += check("p_block_timestamp" not in stamp_body
                 and "block.timestamp" not in stamp_stats.get("lifted", []),
                 f"observing a literal warp does not add a fuzz parameter: "
                 f"{stamp_stats}")
    bad += check(height is not None,
                 f"the block-number endpoint PUT is emitted: {height_notes}")
    bad += check("assertGe(_post_totalFees - _pre_totalFees, 7,"
                 in height_body
                 and "assertLe(_post_totalFees - _pre_totalFees, 7,"
                 in height_body,
                 "the observed roll literal becomes the block-number delta")
    bad += check("p_block_number" not in height_body
                 and "block.number" not in height_stats.get("lifted", []),
                 f"observing a literal roll does not add a fuzz parameter: "
                 f"{height_stats}")
    bad += check(chain is not None,
                 f"the chain-id endpoint PUT is emitted: {chain_notes}")
    bad += check("assertEq(_post_totalFees, 31337," in chain_body,
                 "the observed chainId literal becomes the chain-id endpoint")
    bad += check("p_block_chainid" not in chain_body
                 and "block.chainid" not in chain_stats.get("lifted", []),
                 f"observing a literal chainId does not add a fuzz parameter: "
                 f"{chain_stats}")
    if bad:
        print(stamp_body)
        print(height_body)
        print(chain_body)
    return bad


def test_OBSERVED_block_env_slot_keys_are_nameable():
    """Observed numeric environment cheatcodes can key mapping pre/post reads.

    `resolve_coord` in ESBMC accepts modeled environment names; the PUT side
    must still wait until this emitter has an expression for the current test.
    Literal environment cheatcode preambles provide exactly that expression.
    """
    with_block = EMITTED.replace(
        "    vm.prank(address(uint160(0)));\n",
        "    vm.warp(42);\n"
        "    vm.roll(7);\n"
        "    vm.chainId(31337);\n"
        "    vm.fee(101);\n"
        "    vm.prevrandao(uint256(202));\n"
        "    vm.txGasPrice(303);\n"
        "    vm.coinbase(address(uint160(404)));\n"
        "    vm.prank(address(uint160(0)));\n")
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as out:
        out.write(with_block)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@FeeVault@F@setDiscount#61", 7)
    assert case is not None, "fixture: the emitted case was not found"
    notes = []
    maps = {"byTime": (7, "uint256", 32, 0, "byTime", None),
            "byHeight": (8, "uint256", 32, 0, "byHeight", None),
            "byChain": (9, "uint256", 32, 0, "byChain", None)}
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2,
        "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 100), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=R2_LAYOUT, ladder_rows=[
            ("byTime[block.timestamp]", "post == pre", "HOLDS"),
            ("byHeight[block.number]", "post == pre", "HOLDS"),
            ("byChain[block.chainid]", "post == pre", "HOLDS"),
        ], notes=notes, maps=maps)
    body = "\n".join(put or [])
    bad = 0
    bad += check(put is not None,
                 f"block-keyed slot PUT is emitted: {notes}")
    bad += check("keccak256(abi.encode(uint256(42), uint256(7)))" in body,
                 "timestamp-keyed slot uses the observed warp literal")
    bad += check("keccak256(abi.encode(uint256(7), uint256(8)))" in body,
                 "block-number-keyed slot uses the observed roll literal")
    bad += check("keccak256(abi.encode(uint256(31337), uint256(9)))" in body,
                 "chain-id-keyed slot uses the observed chainId literal")
    bad += check(stats.get("state_asserts") == 3
                 and not stats.get("oracle_skipped"),
                 f"all block-keyed frame rungs are emitted: {stats}")
    if bad:
        print(body)
    return bad


def test_R2_proposal_env_coords_include_observable_replay_values():
    from solidity_path_put import (find_unit_call,  # noqa: E402
                                   rendered_env_coords_for_r2)
    em, case = make_case()
    body = em.lines[case[3][0] + 1:case[3][1]]
    call_i = find_unit_call(body, "setDiscount")
    with_block = EMITTED.replace(
        "    vm.prank(address(uint160(0)));\n",
        "    vm.warp(42);\n"
        "    vm.roll(7);\n"
        "    vm.chainId(31337);\n"
        "    vm.fee(101);\n"
        "    vm.prevrandao(uint256(202));\n"
        "    vm.txGasPrice(303);\n"
        "    vm.coinbase(address(uint160(404)));\n"
        "    vm.prank(address(uint160(0)));\n")
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as out:
        out.write(with_block)
    try:
        block_em = EmittedFile(path)
    finally:
        os.unlink(path)
    block_case = block_em.case_for("sol:@C@FeeVault@F@setDiscount#61", 7)
    block_body = block_em.lines[block_case[3][0] + 1:block_case[3][1]]
    block_call_i = find_unit_call(block_body, "setDiscount")
    got = rendered_env_coords_for_r2(body, call_i, {})
    got_block = rendered_env_coords_for_r2(block_body, block_call_i, {})
    bad = 0
    bad += check(got == [("msg.sender", "id", 20),
                         ("msg.value", "num", None)],
                 f"ordinary replay exposes sender and value only: {got}")
    bad += check(got_block == [("msg.sender", "id", 20),
                               ("msg.value", "num", None),
                               ("block.timestamp", "num", None),
                               ("block.number", "num", None),
                               ("block.chainid", "num", None),
                               ("block.basefee", "num", None),
                               ("block.prevrandao", "num", None),
                               ("tx.gasprice", "num", None),
                               ("block.coinbase", "id", 20)],
                 f"literal environment cheatcodes expose env coords: {got_block}")
    return bad


def test_R2_env_coords_are_recovered_from_the_emitted_case():
    em, case = make_case()
    got = rendered_env_coords_for_emitted_case(em, case, "setDiscount", {})
    missing = rendered_env_coords_for_emitted_case(
        em, case, "doesNotExist", {"msg.value": (0, 0)})
    bad = 0
    bad += check(got == [("msg.sender", "id", 20),
                         ("msg.value", "num", None)],
                 f"main-path R2 setup recovers emitted env coords: {got}")
    bad += check(missing == [],
                 f"missing calls produce no guessed env coords: {missing}")
    return bad


def test_a_numeric_R2_bound_is_UNCHANGED():
    """NEGATIVE CONTROL for the widened regex. A decimal endpoint must render
    exactly as it did before names were allowed; if `([0-9]+|name)` had been
    written so that a digit string falls into the name branch, every existing
    R2 rung would silently vanish and only this test would notice."""
    put, _stats, notes = _r2_put(
        [("totalFees", "post in [10, 20]", "HOLDS")])
    bad = 0
    bad += check(put is not None, f"the PUT is emitted (notes: {notes})")
    if put is None:
        return bad + 1
    body = "\n".join(put)
    bad += check("assertGe(_post_totalFees, 10," in body, "the literal low bound")
    bad += check("assertLe(_post_totalFees, 20," in body, "the literal high bound")
    if bad:
        print(body)
    return bad


def test_a_RENAMED_coordinate_is_spelled_with_its_TEST_name():
    """The emitter renames a coordinate to `p_<name>` when it would collide in
    the test's own scope. A bound that emitted the SOURCE name would compile
    against whatever else happens to be in scope, or not at all -- so the
    lookup must go through the emitter's table and not through the ladder's
    spelling. Exercised directly on `rung_assertions`, because provoking the
    collision through a fixture would be testing the collision rule instead."""
    from solidity_path_put import bound_term, rung_assertions  # noqa: E402
    lines = rung_assertions(
        "post - pre in [bps, bps] with post >= pre",
        "_pre_x", "_post_x", "x: r", {"bps": "p_bps"})
    bad = 0
    bad += check(lines is not None, "the rung renders")
    if lines is None:
        return bad + 1
    body = "\n".join(lines)
    bad += check("_post_x - _pre_x, p_bps," in body,
                 "the endpoint uses the TEST name `p_bps`")
    bad += check(", bps," not in body,
                 "and the source name `bps` appears nowhere")
    bad += check(bound_term("bps", {"bps": "p_bps"}) == "p_bps",
                 "bound_term maps a known name")
    bad += check(bound_term("bps", {}) is None,
                 "bound_term REFUSES an unknown name")
    bad += check(bound_term("42", {}) == "42",
                 "bound_term passes a decimal through with no table at all")
    if bad:
        print(body)
    return bad


def test_a_hole_OUTSIDE_the_interval_costs_no_width():
    """CONTROL 1. Subtracting a hole the bound already excludes understates the
    width, and a width-2 coordinate understated to 1 fails the floor test --
    losing a PUT that was perfectly emittable."""
    put, _stats, notes = _region_put({"bps": (7, 9), "u": (0, (1 << 160) - 1)},
                                     {"bps": [7, 999]})
    bad = 0
    bad += check(put is not None, f"the PUT is still emitted (notes: {notes})")
    bad += check(not any("leaves NO value" in n for n in notes),
                 "and nothing is reported empty")
    return bad


def test_a_REPEATED_hole_is_counted_once():
    """CONTROL 2. `bps` in [7, 8] with the hole 7 listed twice has ONE value
    left, not zero. Counting the list rather than the set would make this
    indistinguishable from the empty case above -- two different regions, one
    outcome, and the discriminator would decide nothing."""
    put, _stats, notes = _region_put({"bps": (7, 8), "u": (0, (1 << 160) - 1)},
                                     {"bps": [7, 7]})
    bad = 0
    bad += check(put is not None, f"the PUT is still emitted (notes: {notes})")
    bad += check(not any("leaves NO value" in n for n in notes),
                 f"and it is NOT reported empty: {notes}")
    return bad


def _slot_pin_put(pins, ladder=None, state_types=None):
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins=pins, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=ladder if ladder is not None else LADDER,
        notes=notes, maps=SLOT_MAPS, state_types=state_types)
    return "\n".join(put or []), stats


def test_a_slot_pin_keyed_by_a_parameter_is_established():
    """The working direction, so the refusal below is not vacuously right."""
    text, stats = _slot_pin_put({"state.bal[u]": 7})
    bad = 0
    bad += check(stats["state_stored"] == ["state.bal[u] := 7"],
                 f"the slot pin is established: {stats['state_stored']}")
    bad += check("keccak256(abi.encode(u, uint256(2)))" in text,
                 "the slot address is hashed from the FUZZ parameter")
    return bad


def _entry_state_put(region, pins=None):
    em, case = make_case()
    notes = []
    base = {"bps": (0, 250), "u": (0, (1 << 160) - 1)}
    base.update(region)
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region=base, holes={}, pins=pins or {}, params=PARAMS, emitted=em,
        case=case, layout=LAYOUT, ladder_rows=LADDER, notes=notes,
        maps=SLOT_MAPS)
    return "\n".join(put or []), stats


def test_an_ESTABLISHED_SCALAR_PIN_is_READ_BACK_and_checked():
    """⛔ `vm.store` CANNOT FAIL. Hand it a slot the contract never reads -- a
    packed offset mis-taken, a slot number gone stale after a recompile -- and
    it writes the word, returns, and every rung below runs green about a state
    nobody set. The region is a statement about a SLICE, and a test that never
    entered the slice is evidence about a different execution.

    The write is therefore followed by a read of the same address, so the
    whole class of silent non-landings becomes a RED test with the
    coordinate's name in the message."""
    text, stats = _entry_state_put({}, pins={"state.owner": 5})
    bad = 0
    bad += check(stats["state_stored"] == ["state.owner := 5"],
                 f"the pin is established: {stats['state_stored']}")
    bad += check("vm.store(address(c0)" in text, "and it is a real store")
    lands = [ln for ln in text.splitlines()
             if "assertEq(" in ln and "did NOT land" in ln]
    bad += check(len(lands) == 1,
                 f"exactly one read-back check for the one pin: {lands}")
    bad += check(bool(lands) and "state.owner" in lands[0],
                 f"and it names the coordinate, not just 'a pin': {lands}")
    # POSITION. A check that lands after the call is checking the POST state,
    # which the unit may legitimately have changed -- it would be red for the
    # wrong reason, or green for one.
    body = text.splitlines()
    ci = [i for i, ln in enumerate(body) if "c0.setDiscount(" in ln]
    li = [i for i, ln in enumerate(body) if "did NOT land" in ln]
    bad += check(bool(ci) and bool(li) and li[0] < ci[0],
                 f"and it sits BEFORE the call (check at {li}, call at {ci})")
    return bad


def test_an_ESTABLISHED_MAPPING_PIN_is_READ_BACK_at_the_HASHED_slot():
    """The mapping case is the one that actually went wrong. A mapping address
    is keccak(key, slot), so a wrong key order, a wrong level count or a stale
    slot all write a well-formed word nothing reads. This emitter has already
    shipped a pin that was satisfied by coincidence."""
    text, stats = _slot_pin_put({"state.bal[255]": 7})
    bad = 0
    lands = [ln for ln in text.splitlines() if "did NOT land" in ln]
    bad += check(len(lands) == 1, f"one read-back check: {lands}")
    bad += check(bool(lands)
                 and "keccak256(abi.encode(uint256(255), uint256(2)))"
                 in lands[0],
                 f"and it reads the SAME hashed address the store wrote, not "
                 f"a literal slot: {lands}")
    return bad


def test_a_WIDE_entry_bound_is_CHECKED_READ_ONLY_instead_of_dropped():
    """⛔ THE HALF THE DROP USED TO THROW AWAY. The write stays forbidden --
    the entry state is never havoc'd, so storing a fuzz-chosen value explores
    entry states the proof never saw, and doing it is what turned three PoC
    PUTs RED. But the query ASSUMED the entry value is in `[lo, hi]`, and if
    the constructor's value is outside it the assumption was vacuous and the
    certificate is about no execution at all. That costs one read to check."""
    text, stats = _entry_state_put({"state.owner": (3, 100)})
    bad = 0
    ins = [ln for ln in text.splitlines() if "OUTSIDE the certified" in ln]
    bad += check(len(ins) == 2,
                 f"both endpoints checked, neither being a type limit: {ins}")
    bad += check(all("vm.load(address(c0)" in ln for ln in ins),
                 f"read-only -- it LOADS: {ins}")
    bad += check(not any("vm.store" in ln and "owner" in ln
                         for ln in text.splitlines()),
                 "and nothing is STORED for it: the entry state the proof was "
                 "about is kept exactly")
    bad += check(any("checked, not set" in s
                     for s in stats.get("state_stored", [])),
                 f"the accounting says checked-not-set, so it is never read "
                 f"as an established pin: {stats.get('state_stored')}")
    return bad


def test_a_WHOLE_TYPE_entry_bound_emits_NO_TAUTOLOGY_and_says_so():
    """⛔ THE NEGATIVE CONTROL, and the failure mode it rules out. `owner in
    [0, 2^160-1]` constrains nothing, so `assertLe(owner, 2^160-1)` cannot
    fail. Emitting it would raise the assertion count while the oracle stayed
    exactly where it was -- an always-true reader, which this project has
    already shipped once. Nothing is emitted and the coordinate is reported
    UNCHECKED rather than checked."""
    text, stats = _entry_state_put({"state.owner": (0, (1 << 160) - 1)})
    bad = 0
    ins = [ln for ln in text.splitlines() if "OUTSIDE the certified" in ln]
    bad += check(ins == [], f"no check at all is emitted: {ins}")
    bad += check(any("state.owner" in s and "not even an in-region check" in s
                     for s in stats.get("state_skipped", [])),
                 f"and the coordinate is reported as UNCHECKED, with the "
                 f"reason: {stats.get('state_skipped')}")
    return bad


def test_a_slot_pin_keyed_by_a_literal_is_established():
    """A literal key needs its uint256 cast: abi.encode of a rational_const
    does not compile."""
    text, stats = _slot_pin_put({"state.bal[255]": 7})
    bad = 0
    bad += check(stats["state_stored"] == ["state.bal[255] := 7"],
                 f"the literal-keyed pin is established: "
                 f"{stats['state_stored']}")
    bad += check("keccak256(abi.encode(uint256(255), uint256(2)))" in text,
                 "the literal key is cast before abi.encode")
    return bad


def test_a_slot_pin_keyed_by_entry_state_is_established():
    """`balances[owner]` is common in real ERC20-like contracts.

    The key is not a function parameter, but it is still a value the PUT can
    name: read the entry-state owner slot, cast it back to address, and use the
    same expression for the mapping hash that the contract will use.
    """
    text, stats = _slot_pin_put(
        {"state.bal[state.owner]": 7}, state_types={"owner": "address"})
    bad = 0
    bad += check(stats["state_stored"] == ["state.bal[state.owner] := 7"],
                 f"the state-keyed slot pin is established: "
                 f"{stats['state_stored']}")
    bad += check("keccak256(abi.encode(address(uint160((uint256(vm.load("
                 "address(c0), bytes32(uint256(0))))" in text,
                 "the key is the entry-state owner slot read, not a guessed "
                 "identifier")
    bad += check(not stats["state_skipped"],
                 f"nothing about the state-keyed slot is skipped: "
                 f"{stats['state_skipped']}")
    return bad


def test_a_slot_pin_keyed_by_msg_sender_is_REFUSED():
    """THE POINT. Inside a Foundry test `msg.sender` is whoever called the
    TEST, while the unit sees the test contract (or the pranked address). A
    store at the test's own sender writes a word the unit never reads -- and
    because a frame-condition slot's rungs are `post == pre`, the test would be
    GREEN while establishing nothing.

    Reachable only since the stage-2 driver learned to PROPOSE
    `state.<m>[msg.sender]` as a coordinate, which is why it is pinned now.
    """
    text, stats = _slot_pin_put({"state.bal[msg.sender]": 7})
    bad = 0
    bad += check(stats["state_stored"] == [],
                 f"nothing is stored: {stats['state_stored']}")
    bad += check(any("ENVIRONMENT quantity" in s
                     for s in stats["state_skipped"]),
                 f"and the reason names the hazard: {stats['state_skipped']}")
    bad += check("vm.store(address(c0), keccak256" not in text,
                 "no hashed vm.store reaches the emitted body")
    return bad


def test_a_nested_slot_is_read_at_the_ITERATED_hash():
    """MUST FLIP against the one-level tests above, which stay bit-identical.

    `m[a][b]` lives at `keccak(b . keccak(a . p))` -- the rule applied twice,
    with the inner hash taking the place of the mapping's declared slot. One
    hash would be a perfectly well-formed address of a word nothing ever wrote,
    and every rung over it would hold trivially: green, and about nothing.

    The two hashes are COUNTED rather than matched against a full expected
    string, so the test does not also pin how a key is cast -- that is
    `slot_key_expr`'s business and it has its own tests.
    """
    text, stats = _slot_pin_put(
        {}, ladder=[("two[u][u]", "post == pre", "HOLDS")])
    bad = 0
    reads = [ln for ln in text.splitlines() if "_pre_" in ln and "vm.load" in ln]
    bad += check(len(reads) == 1, f"one pre-read is emitted: {len(reads)}")
    if not reads:
        return bad + 3
    bad += check(reads[0].count("keccak256(abi.encode(") == 2,
                 f"the address is hashed TWICE, once per level: {reads[0]}")
    bad += check("uint256(5)" in reads[0],
                 "and the innermost operand is the mapping's declared slot")
    bad += check(not stats["oracle_skipped"],
                 f"nothing was dropped: {stats['oracle_skipped']}")
    return bad


def test_a_slot_named_with_the_WRONG_DEPTH_is_refused():
    """THE NEGATIVE CONTROL for the test above, and the reason it exists.

    A two-level store named with one key is the failure that cannot be seen in
    the output: `keccak(a . p)` is a valid address, `vm.load` returns the zero
    word, and `post == pre` passes forever. So the depth is compared against
    the layout rather than trusted, and the refusal names both numbers.
    """
    text, stats = _slot_pin_put(
        {}, ladder=[("two[u]", "post == pre", "HOLDS")])
    bad = 0
    bad += check(any("2-level store but the name gives 1 key(s)" in s
                     for s in stats["oracle_skipped"]),
                 f"the wrong depth is refused, with both numbers: "
                 f"{stats['oracle_skipped']}")
    bad += check("_pre_two" not in text,
                 "and no read of the wrong word reaches the emitted body")
    return bad


def test_the_oracle_side_refuses_the_same_key():
    """One fact, one place: the ORACLE side used to refuse a non-parameter key
    while the WRITING side accepted it. Both go through `slot_key_expr` now."""
    text, stats = _slot_pin_put(
        {}, ladder=[("bal[msg.sender]", "post == pre", "HOLDS")])
    bad = 0
    bad += check(any("ENVIRONMENT quantity" in s
                     for s in stats["oracle_skipped"]),
                 f"the rung is dropped with the same reason: "
                 f"{stats['oracle_skipped']}")
    bad += check("_pre_bal_msg_sender" not in text,
                 "and no read of the wrong slot is emitted")
    return bad


def _sender_keyed_put(sender_region, pins=None, ladder=None):
    """A PUT whose region ESTABLISHES `msg.sender` and whose slot is keyed by it.

    Deliberately `_slot_pin_put`'s fixture with exactly ONE thing added --
    `msg.sender` in the region -- so the only difference between this and the
    two refusal tests directly above is the antecedent under test. If the
    refusal were being removed rather than given a precondition, those two
    would go green here too and the pair would stop being a control.
    """
    em, case = make_case()
    notes = []
    region = {"bps": (0, 250), "u": (0, (1 << 160) - 1),
              "msg.sender": sender_region}
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region=region, holes={}, pins=pins or {}, params=PARAMS, emitted=em,
        case=case, layout=LAYOUT,
        ladder_rows=ladder if ladder is not None else LADDER,
        notes=notes, maps=SLOT_MAPS)
    return "\n".join(put or []), stats


def test_a_slot_keyed_by_an_ESTABLISHED_FUZZED_sender_is_READ():
    """THE FLIP. `slot_key_expr` refuses `msg.sender` because the address the
    unit sees as its caller is chosen by THIS emitter's preamble while the
    region names the verifier's quantity -- so the two could disagree and the
    resulting `post == pre` over an untouched word would be green and empty.

    Once `establish_env_sender` has rewritten the governing prank, they cannot
    disagree: the address IS the string this file emitted. So the key becomes
    nameable, and the read must be at the hash of THAT expression -- not at
    some other address that merely also exists in the test.
    """
    text, stats = _sender_keyed_put(
        (0, 100), ladder=[("bal[msg.sender]", "post == pre", "HOLDS")])
    bad = 0
    bad += check(not any("ENVIRONMENT quantity" in s
                         for s in stats["oracle_skipped"]),
                 f"the key is no longer refused: {stats['oracle_skipped']}")
    bad += check("keccak256(abi.encode(p_msg_sender, uint256(2)))" in text,
                 "and the slot is read at the hash of the PRANKED address, "
                 "which is the fuzz parameter the prank was given")
    bad += check("vm.prank(p_msg_sender);" in text,
                 "the prank really does use that same expression -- without "
                 "this the hash could be of an address nothing runs as")
    bad += check(text.index("p_msg_sender = ")
                 < text.index("keccak256(abi.encode(p_msg_sender"),
                 "and the bound() of that parameter PRECEDES the hash, or the "
                 "slot would be computed from the raw draw and the call would "
                 "run as the bounded one -- two different words")
    bad += check("_pre_bal_msg_sender" in text and
                 "_post_bal_msg_sender" in text,
                 "the rung's two reads are both emitted")
    return bad


def test_a_slot_keyed_by_an_ESTABLISHED_POINT_sender_is_WRITTEN():
    """The width-one branch chooses a LITERAL rather than a parameter, and the
    pin (WRITING) side must use that same literal. This side is the dangerous
    one: `slot_key_expr` exists because the writing side used to be the
    permissive one, so a fix that only reached the oracle would restore exactly
    the asymmetry that function was written to remove.
    """
    text, stats = _sender_keyed_put(
        (5, 5), pins={"state.bal[msg.sender]": 7})
    bad = 0
    bad += check(not any("ENVIRONMENT quantity" in s
                         for s in stats["state_skipped"]),
                 f"the pin is no longer refused: {stats['state_skipped']}")
    bad += check(
        "keccak256(abi.encode(address(uint160(5)), uint256(2)))" in text,
        "and the store lands at the hash of the CERTIFIED address")
    bad += check("vm.prank(address(uint160(5)));" in text,
                 "which is the address the prank was rewritten to")
    bad += check(any("bal[msg.sender]" in s for s in stats["state_stored"]),
                 f"and it is reported as stored: {stats['state_stored']}")
    return bad


def test_an_OBSERVED_msg_value_slot_key_is_nameable():
    """`msg.value` becomes a slot key only when the emitter has an expression.

    For an ordinary call without a `{value: ...}` option, that expression is the
    observed value `0`. This is not inherited from establishing `msg.sender`;
    it comes from the same call-line observation used by R2 endpoint rendering.
    """
    text, stats = _sender_keyed_put(
        (0, 100), ladder=[("bal[msg.value]", "post == pre", "HOLDS")])
    bad = 0
    bad += check(not any("ENVIRONMENT quantity" in s
                         for s in stats["oracle_skipped"]),
                 f"observed msg.value is no longer refused: "
                 f"{stats['oracle_skipped']}")
    bad += check("keccak256(abi.encode(uint256(0), uint256(2)))" in text,
                 "and the slot is read at the observed zero-value key")
    bad += check("_pre_bal_msg_value" in text
                 and "_post_bal_msg_value" in text,
                 "the msg.value-keyed rung's reads are emitted")
    return bad


# --- a WIDE environment coordinate is a range the test cannot establish -----
#
# Reachable only via `--env-coord`, which promotes an environment quantity from
# a pin to a free coordinate. Before that flag was ever passed, every
# environment coordinate in a region was width-one and this branch could not be
# entered; a wide one fell through the parameter loop (msg.sender is not a
# declared parameter), the entry-state loop (no `state.` prefix) AND the
# width-one check, and vanished with nothing on the emitted test.
#
# The fixture's preamble pranks `address(uint160(0))`, so the observed sender
# is 0 in every case below and only the RANGE moves.
TOLERANT_EMITTED = EMITTED.replace(
    "    // [asserted] path exits normally; a revert fails the test\n"
    "    c0.setDiscount(address(uint160(0)), 250);\n",
    "    // [revert-tolerant] outcome not asserted\n"
    "    try c0.setDiscount(address(uint160(0)), 250) {} catch {}\n")

# A ladder carrying BOTH kinds of rung, so one test can show that the drop is
# selective. If it dropped both, the branch would look like it fires while
# actually being "emit no oracle under try/catch", which is a different and
# much worse rule.
MIXED_LADDER = [("owner", "post == pre", "HOLDS"),
                ("feeReceiver", "post != pre", "HOLDS")]


def _make_case_from(text):
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@FeeVault@F@setDiscount#61", 7)
    assert case is not None, "fixture: the emitted case for enc=7 was not found"
    return em, case


def _mixed_put(emitted_text):
    em, case = _make_case_from(emitted_text)
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=MIXED_LADDER, notes=notes)
    return put, stats, notes


def test_a_change_rung_is_GUARDED_on_a_revert_tolerant_call():
    """MUST FLIP against the bare-call control below.

    ---- WHAT THIS TEST USED TO ASSERT, AND WHY IT CHANGED ------------------

    It asserted the change rung was DROPPED. The measurement behind that rule
    is real and still stands: farming.setDistributor enc=13 emitted a
    `post != pre` pair and came back

        [FAIL: _distributor: post != pre; counterexample: args=[0x...0064]]

    on the UNMODIFIED contract -- a revert leaves storage untouched, so the
    rung is FALSE on exactly the outcome a `try {} catch {}` exists to
    tolerate.

    Dropping was not the only sound answer, and it was the weakest one: it
    threw away the only rungs that say the call DID something, leaving a PUT
    whose whole oracle is the frame condition. The rung is a statement about
    executions THAT WALK THE PATH, and a reverting execution does not -- so it
    is now emitted UNDER the condition that the call did not revert. Every
    input of the certified region walks this path, so an in-region input that
    did not revert walked it and the rung holds of it.

    ⛔ THIS TEST WAS INVISIBLE FOR THE WHOLE OF THAT CHANGE. `SLOT_MAPS` was a
    3-tuple against a 6-tuple consumer, so an earlier test raised ValueError
    and ABORTED the module -- every test from that point on, including this
    one, silently did not run. A suite that stops early and a suite that
    passes look identical from outside. That is why `main` now also checks that
    every `test_` function in this module is registered.

    THE GUARD MUST BE SELECTIVE, exactly as the drop had to be. `post == pre`
    holds on a revert too, so it stays UNCONDITIONAL; putting every rung behind
    the flag would weaken assertions that never needed it.

    ⛔ WHAT IS STILL NOT ESTABLISHED HERE, and the header of every guarded PUT
    says so too: whether the guard's true branch is ever TAKEN. A guarded
    assertion skipped on all 256 fuzz runs is green and says nothing.
    """
    put, stats, notes = _mixed_put(TOLERANT_EMITTED)
    bad = 0
    bad += check(put is not None, f"a PUT is still produced (notes: {notes})")
    if put is None:
        return bad + 6
    # Asserted on the STATEMENT, not on the substring "post != pre": that text
    # legitimately appears in the header (the certified ladder is printed) and
    # in the CONDITIONAL note (which quotes the rung it guarded). A first
    # version of this check searched the whole text and matched the comment.
    code = [ln for ln in put if not ln.strip().startswith("//")]
    chg = [i for i, ln in enumerate(code)
           if "assertTrue(_post_feeReceiver != _pre_feeReceiver" in ln]
    bad += check(len(chg) == 1,
                 f"the change assertion IS rendered, not dropped: {len(chg)}")
    gdecl = [i for i, ln in enumerate(code) if "bool _put_ok = true;" in ln]
    gopen = [i for i, ln in enumerate(code) if "if (_put_ok) {" in ln]
    bad += check(len(gdecl) == 1 and len(gopen) == 1,
                 f"the flag is declared once and opens one block: "
                 f"{len(gdecl)}, {len(gopen)}")
    # POSITION, not mere presence. A change assertion that sits BEFORE the
    # guard, or after its closing brace, is unconditional while every printed
    # note claims it is conditional -- the exact red test the drop rule existed
    # to prevent, wearing the name of the fix.
    bad += check(bool(chg) and bool(gopen) and chg[0] > gopen[0],
                 f"and the change assertion is INSIDE the guarded block "
                 f"(guard at {gopen}, assertion at {chg})")
    # The catch must CLEAR the flag. Left permanently true, the guard is
    # decoration and the assertion is unconditional.
    bad += check(any("catch { _put_ok = false; }" in ln for ln in code),
                 "the catch clears the flag, so the guard can be false")
    bad += check(stats.get("guarded_asserts", 0) > 0,
                 f"the accounting counts them apart: "
                 f"{stats.get('guarded_asserts')}")
    eq = [i for i, ln in enumerate(code)
          if "assertEq(_post_owner, _pre_owner" in ln]
    bad += check(bool(eq) and bool(gopen) and eq[0] < gopen[0],
                 f"while the `post == pre` rung of the SAME ladder stays "
                 f"UNCONDITIONAL -- the guard is selective (eq at {eq}, "
                 f"guard at {gopen})")
    return bad


def _rollback_put(emitted_text, rollback, exit_kind=None):
    em, case = _make_case_from(emitted_text)
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=MIXED_LADDER, notes=notes,
        rollback_exit=rollback, exit_kind=exit_kind)
    return put, stats, notes


def test_a_ROLLBACK_path_drops_every_layer_2_3_rung_and_ASSERTS_THE_REVERT():
    """⛔ THE BRANCH THAT HAD NO RUN. The emitter grew a whole reverting-path
    arm and not one test entered it, which is this project's recurring failure
    -- a new defence that compiles, is called, and never fires looks exactly
    like one that works.

    WHAT IT IS FOR. A path that exits through a rollback is emitted as
    `try c0.f() {} catch {}`: an oracle that cannot fail whatever the contract
    does. Meanwhile the ladder's layer-2/3 verdicts on such a path compare the
    value BETWEEN the write and the rollback -- a moment no test and no chain
    can read -- so they are not merely weak, they are about nothing.

    The trade is deliberate and it is a NET GAIN: 25 assertions about an
    unobservable moment, none of which can ever be red, are replaced by ONE
    that says the call must revert. A mutant that stops reverting goes from
    invisible to RED.
    """
    put, stats, notes = _rollback_put(TOLERANT_EMITTED, True)
    bad = 0
    bad += check(put is not None, f"a PUT is still produced (notes: {notes})")
    if put is None:
        return bad + 6
    code = [ln for ln in put if not ln.strip().startswith("//")]
    body = "\n".join(code)
    bad += check("assertTrue(_post_feeReceiver != _pre_feeReceiver" not in body,
                 "the change rung is GONE -- it compared a pre-rollback value")
    bad += check("assertEq(_post_owner, _pre_owner" not in body,
                 "and so is the unchanged rung: layer 2 goes as a whole, not "
                 "selectively -- both were read at the same unreadable moment")
    bad += check(any("assertFalse(_put_ok," in ln for ln in code),
                 f"and the FIRST layer replaces them: the call must revert")
    bad += check(any("catch { _put_ok = false; }" in ln for ln in code),
                 "the catch clears the flag, or the assertFalse can never fail")
    bad += check(stats.get("rollback_exit") is True,
                 f"the accounting records WHY the oracle is one line: "
                 f"{stats.get('rollback_exit')}")
    bad += check(stats.get("guarded_asserts", 0) == 0
                 and not any("if (_put_ok) {" in ln for ln in code),
                 f"no guarded block survives -- a guard with nothing in it is "
                 f"an oracle that reads as present and asserts nothing")
    bad += check(any("DROPPED" in s and "rollback" in s.lower()
                     for s in stats.get("oracle_skipped", [])),
                 f"and the drop is NAMED, not silent: "
                 f"{stats.get('oracle_skipped')}")
    return bad


def test_a_STAGE1_normal_path_counts_the_bare_call_as_R0():
    """A pure/no-state path can still be a valid PUT if Stage 1 proved normal
    exit and the emitted call is bare.  The call itself is the R0 oracle: a
    mutant that makes it revert turns the test red.  This is the shape seen in
    peer SOLTG branch-merge functions, where no state/return rung exists."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2,
        "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=[], notes=notes, exit_kind="normal")
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 3
    bad += check(stats.get("exit_kind_asserts") == 1
                 and stats.get("asserts") == 1,
                 f"the normal bare call counts as the R0 oracle: {stats}")
    bad += check(stats.get("oracle_classes") == ["R0"],
                 f"the oracle class is R0: {stats.get('oracle_classes')}")
    bad += check([d.get("text") for d in stats.get("assertion_oracles", [])]
                 == ["path exits normally"],
                 f"the assertion metadata names normal exit: "
                 f"{stats.get('assertion_oracles')}")
    return bad


def test_effective_exit_kind_falls_back_to_the_fresh_claim():
    """Old Stage-2 rows may not carry an enumeration report for put_all to pass.

    The Stage-4 emitter has already selected the fresh cov-report claim by the
    time it builds the PUT, so that claim must still recover the R0 exit oracle.
    """
    bad = 0
    bad += check(effective_exit_kind(None, {"exit_kind": "normal"}) == "normal",
                 "missing CLI exit kind falls back to the selected claim")
    bad += check(effective_exit_kind("revert", {"exit_kind": "normal"})
                 == "revert",
                 "an explicit Stage-4 exit kind still wins over the claim")
    bad += check(effective_exit_kind(None, {"exit_kind": "undetermined"})
                 == "unknown",
                 "legacy report spelling is normalized")
    bad += check(effective_exit_kind(None, {}) is None,
                 "an absent exit kind stays absent")
    return bad


def test_a_STAGE1_normal_try_call_is_unwrapped_for_return_oracles():
    """peer_solar array-utils shape.

    The coverage emitter produced a revert-tolerant wrapper even though the
    selected path report says normal exit.  Keeping the wrapper drops every
    return rung; unwrapping it gives both the R0 exit oracle and the member
    return assertions.
    """
    src = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
contract C { function indexOf(address[] memory, address) public returns (uint256, bool) {} }
contract CCovTest is Test {
  C c0;
  function setUp() public { c0 = new C(); }
  // claim: sol:@C@C@F@indexOf#1292:path:7
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c0.indexOf(new address[](4), address(uint160(0))) {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(src)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@indexOf#1292", 7)
    notes = []
    put, stats = build_put(
        "C", "indexOf", 7, 2, "sol:@C@C@F@indexOf#1292",
        region={"a": (0, (1 << 160) - 1)}, holes={}, pins={},
        params=[("A", "address[]"), ("a", "address")], emitted=em,
        case=case, layout={}, ladder_rows=[
            ("return", "a value IS returned on this path (REFUTED == yes)",
             "REFUTED"),
            ("return.0", "return == 0", "HOLDS"),
            ("return.1", "return == false", "HOLDS"),
        ], notes=notes, exit_kind="normal",
        rettypes=[("", "uint256"), ("", "bool")])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 5
    txt = "\n".join(put)
    bad += check("try c0.indexOf" not in txt,
                 "the normal-exit PUT no longer tolerates a revert")
    bad += check("[asserted] path exits normally" in txt,
                 "the emitted comment names the R0 oracle")
    bad += check("(uint256 _put_ret0, bool _put_ret1) = "
                 "c0.indexOf(new address[](4), a);" in txt,
                 "the tuple return is bound after unwrapping")
    bad += check("assertEq(uint256(_put_ret0), 0" in txt
                 and "assertFalse(_put_ret1" in txt,
                 "both member return rungs are asserted")
    bad += check(stats["return_asserts"] == 2
                 and stats["exit_kind_asserts"] == 1
                 and stats["asserts"] == 3,
                 f"R0 plus two return assertions are counted: {stats}")
    return bad


def test_a_normal_try_call_with_trailing_semicolon_is_unwrapped():
    bad = 0
    line = '    try c0.changeHello(lang, "") {} catch {};'
    unwrapped, changed = unwrap_normal_try_call(line)
    bad += check(changed, "try/catch with a trailing semicolon is recognized")
    bad += check(unwrapped == '    c0.changeHello(lang, "");',
                 f"the unwrapped call is a bare asserting call: {unwrapped}")
    return bad


def test_a_ROLLBACK_bare_call_gets_expectRevert_layer_1_oracle():
    """St1inch.setMaxLossRatio enc=14 shape.

    The concrete coverage case can share one emitted replay between a normal
    sibling and an overflow rollback sibling. The PUT must follow THIS
    certified path's exit kind, not the shared replay comment that says the
    concrete call exits normally.
    """
    put, stats, notes = _rollback_put(EMITTED, True, exit_kind="revert")
    bad = 0
    bad += check(put is not None, f"a PUT is still produced (notes: {notes})")
    if put is None:
        return bad + 6
    body = "\n".join(put)
    bad += check("vm.expectRevert();" in body,
                 "a bare high-level rollback call is armed with expectRevert")
    bad += check("path exits normally" not in body,
                 "the inherited normal-exit comment is not left beside the "
                 "revert oracle")
    bad += check("path exits through revert" in body,
                 "the rewritten call comment names the asserted exit kind")
    bad += check("assertFalse(_put_ok" not in body,
                 "the try/catch success-flag oracle is not used for a bare "
                 "high-level call")
    bad += check(stats.get("exit_kind_asserts") == 1
                 and stats.get("asserts") == 1,
                 f"the expectRevert line counts as the path oracle: {stats}")
    bad += check(stats.get("oracle_classes") == ["R0"]
                 and [d.get("layer") for d in
                      stats.get("assertion_oracles", [])] == ["exit"],
                 "oracle metadata records only the emitted layer-1 assertion")
    bad += check(stats.get("rollback_exit") is True
                 and stats.get("exit_kind") == "revert",
                 f"the accounting keeps both rollback and exit kind: {stats}")
    return bad


def test_a_NON_rollback_path_is_BYTE_IDENTICAL_to_before():
    """⛔ THE NEGATIVE CONTROL, without which the test above proves nothing.
    A change that dropped layer 2/3 on EVERY path would pass every check above
    while destroying the oracle on the paths that still have one. The same
    fixture with the flag off must produce exactly what it produced before the
    arm existed -- guarded rung inside the block, unchanged rung outside it."""
    on, _s1, _n1 = _rollback_put(TOLERANT_EMITTED, True)
    off, stats, notes = _rollback_put(TOLERANT_EMITTED, False)
    bad = 0
    bad += check(off is not None, f"a PUT is produced (notes: {notes})")
    if off is None:
        return bad + 4
    code = [ln for ln in off if not ln.strip().startswith("//")]
    bad += check(any("assertTrue(_post_feeReceiver != _pre_feeReceiver" in ln
                     for ln in code),
                 "the change rung is BACK")
    bad += check(any("assertEq(_post_owner, _pre_owner" in ln for ln in code),
                 "the unchanged rung is BACK")
    bad += check(not any("assertFalse(_put_ok," in ln for ln in code),
                 "and no revert expectation is planted on a path that does "
                 "not revert -- that would be RED on the unmodified contract")
    bad += check(stats.get("rollback_exit") is False,
                 f"the flag is off in the accounting too: "
                 f"{stats.get('rollback_exit')}")
    bad += check(on != off,
                 "and the two arms actually differ -- if they were equal the "
                 "flag would be reaching nothing")
    return bad


def test_a_STAGE1_revert_path_ASSERTS_THE_REVERT_without_calling_it_rollback():
    """Aqua safeBalances enc=6 shape: Stage 1 says the path exits by revert,
    but the assertion ladder did not print the rollback-specific warning.

    That is still a layer-1 oracle. A `try c0.f() {} catch {}` with no
    assertFalse is green even if a mutant stops reverting, so it is weaker than
    the Stage-1 report already allows.
    """
    put, stats, notes = _rollback_put(TOLERANT_EMITTED, False,
                                      exit_kind="revert")
    bad = 0
    bad += check(put is not None, f"a PUT is still produced (notes: {notes})")
    if put is None:
        return bad + 6
    code = [ln for ln in put if not ln.strip().startswith("//")]
    body = "\n".join(code)
    bad += check("assertTrue(_post_feeReceiver != _pre_feeReceiver" not in body,
                 "post-state rungs are dropped on an ordinary revert path")
    bad += check(any("catch { _put_ok = false; }" in ln for ln in code),
                 "the catch clears the success flag")
    bad += check(any("assertFalse(_put_ok," in ln for ln in code),
                 "the PUT asserts the call must revert")
    bad += check(stats.get("rollback_exit") is False,
                 f"the report does not mislabel it as rollback: {stats}")
    bad += check(stats.get("exit_kind") == "revert"
                 and stats.get("exit_kind_asserts") == 1,
                 f"the ordinary revert is recorded as exit-kind oracle: "
                 f"{stats}")
    bad += check(any("Stage-1" in s or "revert" in s.lower()
                     for s in stats.get("oracle_skipped", [])),
                 f"the dropped rungs explain why: {stats.get('oracle_skipped')}")
    return bad


def test_the_ROLLBACK_LINE_of_the_ladder_log_is_PARSED_in_both_directions():
    """The fact lived in the tool's own log and the emitter had NO READER for
    it: `ROLLBACK` appeared zero times in solidity_path_put.py. Pinned by
    (unit, enc) PAIR, not by enc alone -- one log carries many units and an
    enc collision across them would plant a revert expectation on the wrong
    path."""
    from solidity_path_put import rollback_exit_paths  # noqa: E402
    log = (
        "WARNING: --path-cov-assert: unit 'sol:@C@F@F@setDistributor#5926' "
        "path enc=13 exits through a ROLLBACK revert. The rollback IS "
        "modelled, so the values below are the correctly RESTORED state\n"
        "--path-cov-assert: unit 'sol:@C@F@F@deposit#77' path enc=4 exits "
        "normally\n")
    got = rollback_exit_paths(log)
    bad = 0
    bad += check(got == {("sol:@C@F@F@setDistributor#5926", 13)},
                 f"exactly the reverting pair, and the unit id is carried: "
                 f"{got}")
    bad += check(rollback_exit_paths("") == set()
                 and rollback_exit_paths(None) == set(),
                 "an empty or missing log names no path, rather than raising")
    bad += check(rollback_exit_paths(
        "unit 'x' path enc=4 exits normally") == set(),
        "and a NON-reverting line is not matched -- a reader that matched "
        "every line would silently strip the oracle off every path")
    return bad


def test_the_same_change_rung_IS_asserted_on_a_bare_call():
    """THE CONTROL. Identical ladder, identical region, call NOT wrapped.

    Without this, the branch above would pass just as well if it dropped
    change rungs unconditionally -- and the pipeline would silently lose every
    "this path writes something" oracle it has.
    """
    put, stats, notes = _mixed_put(EMITTED)
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 2
    bad += check(any("assertTrue(_post_feeReceiver != _pre_feeReceiver" in ln
                     for ln in put),
                 "the change assertion IS rendered on a bare call")
    bad += check(not any("REVERT-TOLERANT" in s
                         for s in stats["oracle_skipped"]),
                 f"and nothing is dropped for tolerance: "
                 f"{stats['oracle_skipped']}")
    return bad


def _env_range_put(region_extra):
    em, case = make_case()
    notes = []
    region = {"bps": (0, 250), "u": (0, (1 << 160) - 1)}
    region.update(region_extra)
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region=region, holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=LADDER, notes=notes)
    return put, stats, notes


def test_a_wide_env_coordinate_is_FUZZED_not_disclosed_as_one_point():
    """A WIDE `msg.sender` becomes a fuzz parameter bound into the interval.

    THIS TEST USED TO ASSERT THAT THE RANGE WAS MERELY DISCLOSED -- that the
    PUT was one point of a wider certified claim and said so. The prose it
    pinned was true and the outcome was weak: a region certified over 2^15
    senders produced a test exercising one of them, and the reason given was
    that "an environment quantity is not a call argument, so it cannot be
    bound() into the signature". That is true of the CALL's argument list and
    false of the test FUNCTION's, which is the list a fuzz parameter goes in.

    Three separate things are pinned, because the failure modes are separate:
    the parameter must EXIST, it must be BOUND to this interval (an unbounded
    address parameter is a different and much weaker test), and the prank must
    actually USE it (a bound parameter nothing reads is dead code beside an
    unchanged prank).
    """
    put, stats, _n = _env_range_put({"msg.sender": (0, 100)})
    bad = 0
    bad += check(put is not None, "a PUT is still produced")
    if put is None:
        return bad + 3
    txt = "\n".join(put)
    bad += check("address p_msg_sender" in txt,
                 "msg.sender is a FUZZ PARAMETER of the test function")
    bad += check("bound(" in txt and ", 0, 100)" in txt,
                 "and it is bound() into the certified interval")
    bad += check("vm.prank(p_msg_sender);" in txt,
                 "and the governing prank actually uses it")
    return bad


def test_a_wide_env_coordinate_EXCLUDING_the_sender_is_also_fuzzed():
    """MUST FLIP, in the opposite direction from before.

    0 is outside [5, 100]. This used to be the refusal case, and it was the
    right answer while the preamble's sender was the only sender available.
    Now the prank is rewritten, so whether the emitted case's own sender
    happened to fall inside the interval is no longer a property of anything --
    and a driver that still refused here would be refusing on evidence about a
    line it had already replaced.

    The interval is pinned as [5, 100] rather than only checking that a PUT
    came out: a rewrite that bound the parameter to the WRONG interval would
    produce a green test over the wrong senders, which is the failure this
    whole file exists to stop.
    """
    put, stats, notes = _env_range_put({"msg.sender": (5, 100)})
    bad = 0
    bad += check(put is not None,
                 f"a sender interval that excludes the emitted case's own "
                 f"sender is now fuzzed, not refused (notes: {notes})")
    if put is None:
        return bad + 1
    bad += check(", 5, 100)" in "\n".join(put),
                 "and it is bound to THAT interval, not to the old one")
    return bad


def test_a_width_one_env_coordinate_emits_at_the_certified_value():
    """A width-one sender needs no fuzz parameter, and must still be the value
    the region names rather than whatever the preamble happened to prank."""
    put, stats, _n = _env_range_put({"msg.sender": (0, 0)})
    bad = 0
    bad += check(put is not None, "an agreeing width-one env coordinate emits")
    if put is None:
        return bad + 2
    txt = "\n".join(put)
    bad += check(stats["env_unchecked"] == [],
                 f"and reports nothing unchecked: {stats['env_unchecked']}")
    bad += check("address p_msg_sender" not in txt,
                 "a single point buys no fuzz parameter -- bound(x, v, v) is a "
                 "constant wearing a fuzz parameter's type")
    return bad


def test_msg_value_without_a_value_option_is_still_CHECKED_and_refuses():
    """The lifting mechanism must not invent a value-bearing call.

    This ordinary member call has no `{value: ...}` option. A pin at seven is
    therefore outside the emitted call's value-zero execution and still fails
    closed.
    """
    em, case = make_case()
    notes = []
    put, _stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={"msg.value": 7}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=LADDER, notes=notes)
    bad = 0
    bad += check(put is None, "a disagreeing msg.value still REFUSES")
    bad += check(any("msg.value is certified at 7" in n for n in notes),
                 f"and still names the quantity: {notes}")
    return bad


def test_a_revert_tolerant_body_is_NOT_called_an_assertion():
    """MUST FLIP. The no-oracle header used to promise "the exit-kind
    expectation below is still an assertion" unconditionally, and a
    revert-tolerant body asserts nothing at all. Measured on the aqua
    round-trip: dock_put12, push_put14 and safeBalances_put14 all carried the
    promise over a `try {} catch {}` with zero assert statements."""
    body = ["    // [revert-tolerant] outcome not asserted",
            "    try c0.safeBalances(maker, app) {} catch {}"]
    bad = 0
    bad += check(exit_kind_asserted(body) is False,
                 "a try/catch body does NOT assert the exit kind")
    return bad


def test_an_asserted_body_still_counts_as_an_assertion():
    """The NEGATIVE CONTROL, and the reason the predicate is a function. If only
    the false arm were ever exercised, a predicate hard-wired to False would
    pass the check above and be indistinguishable from a working one. All three
    shapes the emitter uses to carry an assertion must read as True."""
    bad = 0
    bare = ["    // [asserted] path exits normally; a revert fails the test",
            "    c0.setDiscount(u, bps);"]
    gate = ["    // [asserted] value sent to a NON-PAYABLE entry: the call "
            "must fail",
            "    assertFalse(ok3, \"value sent to a non-payable entry must "
            "revert\");"]
    rev = ["    vm.expectRevert();",
           "    c0.pull(x);"]
    bad += check(exit_kind_asserted(bare) is True,
                 "a BARE call asserts the exit kind")
    bad += check(exit_kind_asserted(gate) is True,
                 "the non-payable value gate asserts the exit kind")
    bad += check(exit_kind_asserted(rev) is True,
                 "an armed vm.expectRevert asserts the exit kind")
    return bad


def test_dropped_rungs_are_not_reported_as_no_rung_holding():
    """MUST FLIP. "Not one rung HOLDS" was printed whenever zero assertions were
    RENDERED, and rendering is downstream of holding.

    MEASURED on aqua `dock` enc=12, which is what exposed it: the ladder came
    back 3 HOLDS / 3 REFUTED on `_DOCKED`, all three HOLDS were then dropped
    because solc's layout does not list `_DOCKED` (a constant/immutable, where
    the assertion would be a compile-time tautology), and the emitted file said
    not one rung holds.

    The direction of the error is what makes it worth a test: it HIDES WORK
    THAT SUCCEEDED. "the prover found nothing" and "the prover found three
    things this emitter cannot render" call for opposite next actions.
    """
    em, case = make_case()
    notes = []
    dock_ladder = [("_DOCKED", "post == pre", "HOLDS"),
                   ("_DOCKED", "post != pre", "REFUTED"),
                   ("_DOCKED", "post >= pre", "HOLDS"),
                   ("_DOCKED", "post <= pre", "HOLDS"),
                   ("_DOCKED", "post > pre", "REFUTED"),
                   ("_DOCKED", "post < pre", "REFUTED")]
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT, ladder_rows=dock_ladder, notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(stats["asserts"] == 0,
                 f"the fixture reproduces the zero-oracle case: "
                 f"{stats['asserts']}")
    bad += check("EVERY RUNG THAT HOLDS WAS DROPPED" in text,
                 "the header says the rungs held and could not be rendered")
    bad += check("NOT ONE RUNG HOLDS" not in text,
                 "and does NOT claim nothing held")
    # ONE, not three. `post == pre` HOLDS entails `post >= pre` and
    # `post <= pre`, so the ladder's three HOLDS are one INDEPENDENT rung --
    # and one is the honest count of what was lost when it could not be
    # rendered. Reporting three would claim three times the oracle.
    bad += check("1 rung(s) HOLD" in text,
                 f"the count is the number of INDEPENDENT rungs that HELD, "
                 f"not the number of rows")
    bad += check(len(stats["oracle_implied"]) == 2,
                 f"and the other two are filed as IMPLIED, which is not the "
                 f"same fact as dropped: {stats['oracle_implied']}")
    bad += check(any("_DOCKED" in s and "compile-time tautology" in s
                     for s in stats["oracle_skipped"]),
                 f"the per-rung reason is still printed: "
                 f"{stats['oracle_skipped']}")
    return bad


def test_the_ANTICHAIN_keeps_the_STRICT_rung_and_drops_what_it_entails():
    """⛔ THE NUMBER IS READ AS STRENGTH. `assertGe(post, pre)` beside
    `assertGt(post, pre)` cannot fail on any execution the strict one passes,
    so the pair detects exactly what the strict one detects alone -- while a
    PUT reporting six assertions of which three are entailed claims an oracle
    twice as sharp as the one it has."""
    from solidity_path_put import antichain  # noqa: E402
    rows = [("bal", "post > pre", "HOLDS"),
            ("bal", "post >= pre", "HOLDS"),
            ("bal", "post != pre", "HOLDS"),
            ("bal", "post <= pre", "REFUTED")]
    kept, implied = antichain(rows)
    bad = 0
    bad += check([t for _v, t, _d in kept]
                 == ["post > pre", "post <= pre"],
                 f"the strict rung stays and so does the REFUTED row: {kept}")
    bad += check(sorted(t for _v, t, _d in implied)
                 == ["post != pre", "post >= pre"],
                 f"and both entailed rungs are named: {implied}")
    bad += check(len(kept) + len(implied) == len(rows),
                 f"nothing vanishes between the two lists: "
                 f"{len(kept)} + {len(implied)} vs {len(rows)}")
    return bad


def test_the_ANTICHAIN_may_not_let_a_GUARDED_rung_dominate_an_UNGUARDED_one():
    """⛔ THE BUG THIS FILTER WOULD OTHERWISE HAVE INTRODUCED. On a
    revert-tolerant call the CHANGE rungs are emitted inside `if (_put_ok)`
    and the rest unconditionally:

        assertGe(post, pre, ...)                  <- always runs
        if (_put_ok) { assertGt(post, pre, ...) }  <- skipped on a revert

    `post > pre` entails `post >= pre` as a PROPOSITION, but the assertion it
    renders is skipped exactly when the call reverts -- which is the execution
    where the unconditional one still has something to say. Dropping the outer
    rung leaves that execution asserting NOTHING: oracle destroyed by a filter
    whose entire premise is that it destroys none.

    MEASURED SHAPE: farming setDistributor enc=13 emits `_distributor:
    post >= pre` unconditionally and `post != pre` / `post > pre` guarded.

    MUST FLIP against the bare-call arm below."""
    from solidity_path_put import antichain  # noqa: E402
    rows = [("d", "post > pre", "HOLDS"),
            ("d", "post != pre", "HOLDS"),
            ("d", "post >= pre", "HOLDS")]
    kept, implied = antichain(rows, revert_tolerant=True)
    bad = 0
    bad += check([t for _v, t, _d in implied] == ["post != pre"],
                 f"only the rung on the SAME side of the guard is dropped -- "
                 f"`post != pre` is a change rung like `post > pre`: "
                 f"{implied}")
    bad += check(("d", "post >= pre", "HOLDS") in kept,
                 f"and the UNCONDITIONAL rung survives: {kept}")
    # THE CONTROL. Under a bare call there is no guard, every rung is
    # unconditional, and the full table applies -- otherwise this rule would
    # be leaving redundancy behind everywhere instead of only where it must.
    kept2, implied2 = antichain(rows, revert_tolerant=False)
    bad += check(sorted(t for _v, t, _d in implied2)
                 == ["post != pre", "post >= pre"],
                 f"on a BARE call both entailed rungs go: {implied2}")
    return bad


def test_the_ANTICHAIN_never_uses_a_REFUTED_rung_to_drop_a_HOLDING_one():
    """⛔ THE DIRECTION THAT WOULD DESTROY ORACLE. A REFUTED `post > pre`
    entails nothing whatsoever, so using it to drop `post >= pre` would delete
    a rung that HOLDS on the strength of one that does not -- the emitted test
    would lose a real assertion and the count would say it was redundant."""
    from solidity_path_put import antichain  # noqa: E402
    kept, implied = antichain([("bal", "post > pre", "REFUTED"),
                               ("bal", "post >= pre", "HOLDS")])
    bad = 0
    bad += check(implied == [], f"nothing is dropped: {implied}")
    bad += check(("bal", "post >= pre", "HOLDS") in kept,
                 f"the holding rung survives: {kept}")
    return bad


def test_the_ANTICHAIN_does_not_reach_ACROSS_VARIABLES():
    """`a: post > pre` says nothing about `b: post >= pre`. Domination that
    ignored the variable would silently strip the frame condition off every
    other slot the moment one slot moved."""
    from solidity_path_put import antichain  # noqa: E402
    kept, implied = antichain([("a", "post > pre", "HOLDS"),
                               ("b", "post >= pre", "HOLDS")])
    bad = 0
    bad += check(implied == [], f"nothing is dropped: {implied}")
    bad += check(len(kept) == 2, f"both survive: {kept}")
    return bad


def test_the_ANTICHAIN_lets_a_DELTA_rung_dominate_NOTHING():
    """⛔ IT RUNS BEFORE RENDERING, so a rung it removes is gone whether or not
    the dominating rung turns out to be renderable. The six ordering rungs
    render through one branch and one slot lookup, so within that family
    nothing can be lost. A DELTA rung's endpoints can be UNSPELLABLE, and
    letting it dominate `post >= pre` would trade a rung that renders for one
    that does not -- losing the oracle in exactly the case where the
    domination was supposed to be free."""
    from solidity_path_put import antichain  # noqa: E402
    kept, implied = antichain(
        [("bal", "post - pre in [amt, amt] with post >= pre", "HOLDS"),
         ("bal", "post >= pre", "HOLDS")])
    bad = 0
    bad += check(implied == [],
                 f"the ordering rung is NOT dropped by the delta: {implied}")
    bad += check(len(kept) == 2, f"both survive: {kept}")
    return bad


def test_the_ANTICHAIN_drops_return_rungs_implied_by_a_literal_value():
    """A certified return equality is the oracle; its singleton interval and
    non-equal-to-a-different-literal companions report no extra strength."""
    from solidity_path_put import antichain  # noqa: E402
    rows = [("return", "return == 20", "HOLDS"),
            ("return", "return in [20, 20]", "HOLDS"),
            ("return", "return != 0", "HOLDS"),
            ("return.0", "return == 0", "HOLDS"),
            ("return.0", "return != 1", "HOLDS"),
            ("return.1", "return != 0", "HOLDS")]
    kept, implied = antichain(rows)
    bad = 0
    bad += check([t for v, t, _d in kept if v == "return"]
                 == ["return == 20"],
                 f"bare return keeps only the exact literal: {kept}")
    bad += check(sorted(t for v, t, _d in implied if v == "return")
                 == ["return != 0", "return in [20, 20]"],
                 f"the redundant return rows are implied: {implied}")
    bad += check(("return.0", "return == 0", "HOLDS") in kept,
                 f"tuple member equality survives: {kept}")
    bad += check(("return.0", "return != 1", "HOLDS") in implied,
                 f"tuple member weak inequality is implied: {implied}")
    bad += check(("return.1", "return != 0", "HOLDS") in kept,
                 f"another tuple member is not touched: {kept}")
    return bad


def test_the_ANTICHAIN_does_not_use_REFUTED_return_rows_as_evidence():
    from solidity_path_put import antichain  # noqa: E402
    rows = [("return", "return == 20", "REFUTED"),
            ("return", "return != 0", "HOLDS")]
    kept, implied = antichain(rows)
    bad = 0
    bad += check(implied == [], f"nothing is implied by REFUTED rows: {implied}")
    bad += check(kept == rows, f"both original rows survive: {kept}")
    return bad


def test_the_ANTICHAIN_normalizes_R2_point_values_before_dominance():
    """R2 may prove the same fact through several endpoint spellings after the
    certified region pins an environment coordinate. Those spellings must not
    inflate the oracle count."""
    from solidity_path_put import antichain  # noqa: E402
    rows = [("_owner", "post == 0", "HOLDS"),
            ("_owner", "post == msg.value", "HOLDS"),
            ("_owner", "post in [msg.value, msg.value]", "HOLDS"),
            ("_owner", "post in [0, _claimTopic]", "HOLDS"),
            ("_owner", "post == pre", "HOLDS")]
    kept, implied = antichain(rows, point_values={"msg.value": 0})
    bad = 0
    bad += check([t for _v, t, _d in kept]
                 == ["post == 0", "post == pre"],
                 f"only the independent exact facts survive: {kept}")
    bad += check(sorted(t for _v, t, _d in implied)
                 == ["post == msg.value",
                     "post in [0, _claimTopic]",
                     "post in [msg.value, msg.value]"],
                 f"the point-equivalent and weaker interval rows are implied: "
                 f"{implied}")
    return bad


def test_the_ANTICHAIN_does_not_normalize_unpinned_R2_coordinates():
    from solidity_path_put import antichain  # noqa: E402
    rows = [("_owner", "post == 0", "HOLDS"),
            ("_owner", "post == _claimTopic", "HOLDS")]
    kept, implied = antichain(rows, point_values={"msg.value": 0})
    bad = 0
    bad += check(implied == [], f"a wide coordinate is not a point: {implied}")
    bad += check(kept == rows, f"both exact facts survive: {kept}")
    return bad


def test_the_ANTICHAIN_folds_R2_zero_and_one_identities():
    from solidity_path_put import antichain  # noqa: E402
    rows = [("slot", "post == n", "HOLDS"),
            ("slot", "post == (n + msg.value)", "HOLDS"),
            ("slot", "post == (msg.value + n)", "HOLDS"),
            ("slot", "post == (n - msg.value)", "HOLDS"),
            ("slot", "post == (n * 1)", "HOLDS"),
            ("slot", "post == (n + state.ZERO)", "HOLDS")]
    kept, implied = antichain(
        rows, point_values={"msg.value": 0, "state.ZERO": 0})
    bad = 0
    bad += check(kept == [("slot", "post == n", "HOLDS")],
                 f"the source-level exact endpoint is the one kept: {kept}")
    bad += check(sorted(t for _v, t, _d in implied) == sorted(
        ["post == (n + msg.value)", "post == (msg.value + n)",
         "post == (n - msg.value)", "post == (n * 1)",
         "post == (n + state.ZERO)"]),
                 f"all algebraic copies are implied: {implied}")
    return bad


def test_the_ANTICHAIN_folds_safe_commutative_and_self_delta_terms_only():
    from solidity_path_put import antichain  # noqa: E402
    rows = [("slot", "post == (pre + n)", "HOLDS"),
            ("slot", "post == (n + pre)", "HOLDS"),
            ("slot", "post == (n - n)", "HOLDS"),
            ("slot", "post == 0", "HOLDS"),
            ("slot", "post == (n - pre)", "HOLDS")]
    kept, implied = antichain(rows)
    bad = 0
    bad += check(("slot", "post == (n - pre)", "HOLDS") in kept,
                 f"non-identity subtraction is not normalized away: {kept}")
    bad += check(("slot", "post == 0", "HOLDS") in kept,
                 f"literal zero is preferred over `n - n`: {kept}")
    bad += check(("slot", "post == (n - n)", "HOLDS") in implied,
                 f"`n - n` is an implied spelling of zero: {implied}")
    bad += check(len([1 for _v, t, _d in kept
                      if t in ("post == (pre + n)", "post == (n + pre)")])
                 == 1,
                 f"commutative addition keeps one spelling: {kept}")
    return bad


def test_a_ladder_where_nothing_held_still_says_so():
    """THE NEGATIVE CONTROL. If the headline were hard-wired to the dropped
    wording it would pass the case above and be wrong on every genuinely empty
    ladder -- which is the more common one. Same unit, same layout, only the
    verdicts change."""
    em, case = make_case()
    notes = []
    put, stats = build_put(
        "FeeVault", "setDiscount", 7, 2, "sol:@C@FeeVault@F@setDiscount#61",
        region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
        holes={}, pins={}, params=PARAMS, emitted=em, case=case,
        layout=LAYOUT,
        ladder_rows=[("owner", "post != pre", "REFUTED"),
                     ("owner", "post > pre", "REFUTED")],
        notes=notes)
    text = "\n".join(put or [])
    bad = 0
    bad += check(stats["asserts"] == 0, "no assertion is rendered")
    bad += check("NOT ONE RUNG HOLDS" in text,
                 "an all-REFUTED ladder says nothing held")
    bad += check("EVERY RUNG THAT HOLDS WAS DROPPED" not in text,
                 "and does not claim rungs were dropped")
    return bad


def test_the_retlive_witness_is_not_counted_as_a_rung_that_held():
    """`retlive` HOLDS means NO execution reached a return, so counting it would
    report the ABSENCE of a return value as a rendering failure -- and would
    print "1 rung(s) HOLD" for a ladder in which nothing useful held."""
    bad = 0
    h, _w = no_oracle_reason([("return", RETLIVE, "HOLDS")])
    bad += check(h == "NOT ONE RUNG HOLDS",
                 f"a lone retlive HOLDS is not a rung that held: {h}")
    h, _w = no_oracle_reason([("return", RETLIVE, "HOLDS"),
                              ("bal", "post == pre", "HOLDS")])
    bad += check(h == "EVERY RUNG THAT HOLDS WAS DROPPED",
                 f"but a real rung beside it still counts: {h}")
    h, w = no_oracle_reason([("return", RETLIVE, "REFUTED"),
                             ("bal", "post == pre", "HOLDS")])
    bad += check("1 rung(s) HOLD" in w,
                 f"and the count excludes the witness: {w}")
    return bad


def test_a_piece_label_distinguishes_two_boxes_of_one_path():
    """TWO CERTIFIED BOXES, ONE enc: the names must differ.

    `--max-region-pieces > 1` lets stage 2 certify a path as a UNION of boxes,
    each by its own query, so one enc yields SEVERAL regions and therefore
    several PUTs. Measured on farming.setDistributor with the tool's own
    --unwindset repair: enc 12 came back as 2 pieces and enc 13 as 3, five
    certified regions for two paths.

    WHAT BREAKS WITHOUT THE LABEL, and it is two different failures:

      * put_all writes `test/<contract>.t.sol` from the same name, so piece 4
        OVERWRITES piece 3 -- two emissions, one file, and a sweep that reports
        both;
      * put_all's B gate keys its forge verdict table on the TEST NAME across
        every suite, so the two pieces share one cell and whichever forge
        reported last decides both.

    The second is why the FUNCTION name carries it and not only the file: two
    contracts may legally declare the same function, so this is an accounting
    collision rather than a compile error -- the kind that stays silent.

    BOTH DIRECTIONS. An unlabelled call must produce the byte-identical old
    name, or every PUT already on disk stops matching the gate's lookup.
    """
    em, case = make_case()
    args = dict(region={"bps": (0, 250), "u": (0, (1 << 160) - 1)},
                holes={}, pins={}, params=PARAMS, emitted=em, case=case,
                layout=LAYOUT, ladder_rows=LADDER)
    plain, _s1 = build_put("FeeVault", "setDiscount", 7, 2,
                           "sol:@C@FeeVault@F@setDiscount#61", notes=[],
                           **args)
    p3, _s2 = build_put("FeeVault", "setDiscount", 7, 2,
                        "sol:@C@FeeVault@F@setDiscount#61", notes=[],
                        piece_label="p3", **args)
    p4, _s3 = build_put("FeeVault", "setDiscount", 7, 2,
                        "sol:@C@FeeVault@F@setDiscount#61", notes=[],
                        piece_label="p4", **args)
    bad = 0
    tp, t3, t4 = ("\n".join(x or []) for x in (plain, p3, p4))
    bad += check(
        "function test_put_FeeVault_setDiscount_path7(" in tp,
        "no label reproduces the existing name byte for byte")
    bad += check(
        "function test_put_FeeVault_setDiscount_path7p3(" in t3,
        "piece 3 carries its label")
    bad += check(
        "function test_put_FeeVault_setDiscount_path7p4(" in t4,
        "piece 4 carries its label")
    # THE POINT: the two pieces must not collide. Asserted as a difference
    # rather than as two literals, because a future label scheme that changed
    # both spellings must still keep them apart.
    bad += check(
        "test_put_FeeVault_setDiscount_path7p3(" not in t4,
        "and piece 4 does NOT carry piece 3's name -- one enc, two boxes, two "
        "names")
    # MUST NOT FIRE: the label may not leak into an unlabelled run.
    bad += check(
        "path7p" not in tp,
        "an unlabelled run grows no suffix at all, not an empty one")
    return bad


# ---------------------------------------------------------------------------
# THE LOW-LEVEL VALUE-GATE SHAPE: two lines, one statement
# ---------------------------------------------------------------------------
#
# A path whose exit is the ABI VALUE GATE cannot be replayed as `c0.f(args)` --
# Solidity refuses `{value: v}` on a non-payable function at COMPILE time -- so
# the emitter writes the only form that compiles, and it BREAKS IT ACROSS TWO
# LINES with the unit's name on the second.
#
# Everything in this driver that says "the call" meant "the line that names the
# unit", and for this shape those are different indices. Three separate
# consumers were wrong in three different ways (see `statement_start`); the one
# that had already been MEASURED is the reader:
#
#   [put] REFUSED: the emitted case does not run in the certified environment
#   slice: msg.value is certified over [1, 115792089237316195423570985008687907
#   853269984665640564039457584007913129639935] but the emitted case sets it to
#   0 (`no {value:} option on the call, so msg.value is 0`), which is OUTSIDE
#   that range
#
# -- a refusal produced by looking at the wrong line. The case DOES send 1.
VALUE_GATE_EMITTED = """\
// SPDX-License-Identifier: MIT
// Auto-generated by ESBMC 8.2.0
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {FarmingPool} from "./farming__FarmingPool.flat.sol";

contract FarmingPoolCovTest is Test {
  FarmingPool c0;
  function setUp() public {
    c0 = new FarmingPool();
  }
  // claim: sol:@C@FarmingPool@F@setDistributor#5926:path:2
  function test_cov_4() public {
    vm.deal(address(this), 1);
    // [asserted] value sent to a NON-PAYABLE entry: the call must fail
    (bool ok5, ) = address(c0).call{value: 1}(
        abi.encodeWithSignature("setDistributor(address)", address(uint160(0))));
    assertFalse(ok5, "value sent to a non-payable entry must revert");
  }
}
"""
# The four `test_cov_*` member-call cases of the same emitted file, its two
# mock contracts and its real constructor arguments are omitted. What is pinned
# here is the STATEMENT SHAPE, and the five lines of `test_cov_4` are a VERBATIM
# capture of it -- line break, indentation and all -- from
# tmp/enc2/put/emit/FarmingPool.cov.t.sol.

FARM_LAYOUT = {"_distributor": (0, 0, 20), "_totalSupply": (1, 0, 32)}
FARM_PARAMS = [("distributor_", "address")]
FARM_LADDER = [("_distributor", "post == pre", "HOLDS"),
               ("_totalSupply", "post == pre", "HOLDS")]
UINT256_MAX = (1 << 256) - 1


def _value_gate_case():
    # NOT `_make_case_from`: that helper looks up the FeeVault claim by name,
    # and this fixture is a different unit. Matching on the FULL mangled
    # identity is the whole reason `case_for` takes one -- two units of one
    # contract have independent path-id spaces.
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(VALUE_GATE_EMITTED)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@FarmingPool@F@setDistributor#5926", 2)
    assert case is not None, "fixture: the value-gate case for enc=2 was missing"
    return em, case


def _value_gate_put(region_extra, pins=None):
    em, case = _value_gate_case()
    notes = []
    region = {"distributor_": (0, (1 << 160) - 1),
              "msg.sender": (0, (1 << 160) - 1)}
    region.update(region_extra)
    put, stats = build_put(
        "FarmingPool", "setDistributor", 2, 1,
        "sol:@C@FarmingPool@F@setDistributor#5926",
        region=region, holes={}, pins=pins or {}, params=FARM_PARAMS,
        emitted=em, case=case, layout=FARM_LAYOUT, ladder_rows=FARM_LADDER,
        notes=notes)
    return put, stats, notes


def test_the_value_gate_statement_is_read_as_ONE_statement():
    """`find_unit_call` lands on line 2 of 2; the statement starts on line 1."""
    em, case = _value_gate_case()
    body = em.lines[case[3][0] + 1:case[3][1]]
    call_i = find_unit_call(body, "setDistributor")
    bad = 0
    bad += check(call_i is not None, "the low-level call is found at all")
    if call_i is None:
        return bad + 4
    bad += check("abi.encodeWithSignature" in body[call_i],
                 f"and the hit is the SECOND line: {body[call_i].strip()!r}")
    st = statement_start(body, call_i)
    bad += check(st == call_i - 1,
                 f"the statement starts one line earlier: {st} vs {call_i}")
    bad += check("{value: 1}" in body[st],
                 f"which is the line carrying the value option: "
                 f"{body[st].strip()!r}")
    # THE MEASURED BUG. Reading the matched line alone yields 0.
    obs = observed_env(body, call_i, body[call_i])
    bad += check(obs["msg.value"][0] == 1,
                 f"msg.value is read as 1, not 0: {obs['msg.value']}")
    return bad


def test_high_level_value_call_is_parsed_and_rewritten():
    """High-level calls may carry Solidity call options before the args."""
    body = [
        "    vm.deal(address(this), 10);",
        "    vm.prank(address(uint160(0)));",
        "    // [revert-tolerant] outcome not asserted",
        "    try c0.buy{value: 10}(address(uint160(0)), 7) {} catch {}",
        "    try c0.play{value: 10}() {} catch {}",
    ]
    bad = 0
    call_i = find_unit_call(body, "buy")
    bad += check(call_i == 3,
                 f"high-level value call is found: {call_i}")
    bad += check(target_instance_for_call(body, call_i, "buy") == "c0",
                 "the target instance is read past `{value: ...}`")
    rewritten, args = rewrite_call_args(
        body[call_i], "buy", {0: "p_player", 1: "p_amount"})
    bad += check(args == ["address(uint160(0))", "7"],
                 f"the original arguments are parsed: {args}")
    bad += check(
        rewritten == "    try c0.buy{value: 10}(p_player, p_amount) {} catch {}",
        f"only the argument list is rewritten: {rewritten}")
    play_i = find_unit_call(body, "play")
    _rewritten_play, play_args = rewrite_call_args(body[play_i], "play", {})
    bad += check(play_i == 4 and play_args == [],
                 f"no-arg value call is parsed too: {play_i}, {play_args}")
    return bad


PAYABLE_HIGH_LEVEL_EMITTED = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {PrivateBank} from "./PrivateBank.sol";

contract PrivateBankCovTest is Test {
  PrivateBank c0;
  function setUp() public {
    c0 = new PrivateBank();
  }
  // claim: sol:@C@PrivateBank@F@Deposit#68:path:5
  function test_cov_0() public {
    c0.Deposit();
  }
}
"""


def _payable_high_level_case():
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(PAYABLE_HIGH_LEVEL_EMITTED)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@PrivateBank@F@Deposit#68", 5)
    assert case is not None, "fixture: payable Deposit enc=5 was missing"
    return em, case


def test_payable_high_level_call_can_establish_msg_value():
    """A certified payable msg.value range should become a real PUT input."""
    em, case = _payable_high_level_case()
    notes = []
    put, stats = build_put(
        "PrivateBank", "Deposit", 5, 1,
        "sol:@C@PrivateBank@F@Deposit#68",
        region={"msg.value": (1, UINT256_MAX)}, holes={}, pins={},
        params=[], emitted=em, case=case,
        layout={"total": (0, 0, 32)},
        ladder_rows=[("total", "post >= pre", "HOLDS")],
        notes=notes, unit_payable=True)
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 6
    txt = "\n".join(put)
    bad += check("uint256 p_msg_value" in txt,
                 "msg.value is lifted into the PUT signature")
    bad += check(f"p_msg_value = bound(p_msg_value, 1, {UINT256_MAX});" in txt,
                 "msg.value is bounded to the certified payable interval")
    bad += check("vm.deal(address(this), p_msg_value);" in txt,
                 "the payer is funded before the value-bearing call")
    bad += check("c0.Deposit{value: p_msg_value}();" in txt,
                 "the high-level payable target call carries the fuzzed value")
    bad += check(stats["fuzz_params"] == 1
                 and stats["wide_fuzz_coords"] == ["msg.value"],
                 f"the B ledger counts msg.value as the PUT dimension: {stats}")
    bad += check("payable high-level call" in txt,
                 "the transformation is reported in the PUT header")
    return bad


def test_nonpayable_high_level_call_still_refuses_wide_msg_value():
    """Without an AST-payable proof, high-level `{value:}` is not invented."""
    em, case = _payable_high_level_case()
    notes = []
    put, _stats = build_put(
        "PrivateBank", "Deposit", 5, 1,
        "sol:@C@PrivateBank@F@Deposit#68",
        region={"msg.value": (1, UINT256_MAX)}, holes={}, pins={},
        params=[], emitted=em, case=case, layout={}, ladder_rows=[],
        notes=notes, unit_payable=False)
    bad = 0
    bad += check(put is None, "unconfirmed high-level value establishment REFUSES")
    bad += check(any("msg.value is certified over [1" in n
                     and "OUTSIDE that range" in n for n in notes),
                 f"the old fail-closed refusal remains: {notes}")
    return bad


def test_only_the_low_level_value_gate_assertion_counts_as_exit_kind():
    from solidity_path_put import (find_unit_call,  # noqa: E402
                                   low_level_value_gate_asserts_exit)
    em, case = _value_gate_case()
    body = em.lines[case[3][0] + 1:case[3][1]]
    call_i = find_unit_call(body, "setDistributor")
    bad = 0
    bad += check(low_level_value_gate_asserts_exit(
        body, call_i, body[call_i]),
        "the emitter's assertFalse(ok) value gate is recognized")
    bad_body = list(body)
    bad_body[call_i + 1] = "    assertFalse(c0.paused());"
    bad += check(not low_level_value_gate_asserts_exit(
        bad_body, call_i, bad_body[call_i]),
        "an arbitrary assertFalse after the call is NOT an exit-kind oracle")
    return bad


def test_a_single_line_call_still_reports_its_own_statement():
    """THE NEGATIVE CONTROL for `statement_start`.

    If it were hard-wired to `i - 1` -- or to any walk that always steps back --
    it would pass the case above and silently move every ordinary call's
    insertion point one line up, which is where the emitter's `vm.prank` sits.
    The FeeVault fixture's call is a plain one-line statement and must report
    itself.
    """
    em, case = make_case()
    body = em.lines[case[3][0] + 1:case[3][1]]
    call_i = find_unit_call(body, "setDiscount")
    bad = 0
    bad += check(statement_start(body, call_i) == call_i,
                 "a one-line call statement starts at its own line")
    obs = observed_env(body, call_i, body[call_i])
    bad += check(obs["msg.value"][0] == 0,
                 f"and a call with no value option is still 0: "
                 f"{obs['msg.value']}")
    bad += check(obs["msg.sender"][0] == 0,
                 f"and the prank above it is still read: {obs['msg.sender']}")
    return bad


def test_the_low_level_value_gate_emits_a_PUT():
    """THE END-TO-END DIRECTION: enc=2's certified region reaches a test."""
    put, stats, notes = _value_gate_put({"msg.value": (1, UINT256_MAX)})
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 6
    txt = "\n".join(put)
    bad += check("abi.encodeWithSignature(\"setDistributor(address)\", "
                 "distributor_))" in txt,
                 "the unit's argument is rewritten INSIDE encodeWithSignature, "
                 "past the signature string")
    bad += check("\"setDistributor(address)\"" in txt,
                 "and the signature string itself is untouched")
    bad += check("uint256 p_msg_value" in txt,
                 "msg.value is a FUZZ PARAMETER of the PUT")
    bad += check(f"p_msg_value = bound(p_msg_value, 1, {UINT256_MAX});" in txt,
                 "and is bounded to the complete certified interval")
    # THE STATEMENT MUST NOT BE SPLIT. Everything the PUT adds goes in front of
    # its first line, never between the two.
    code = [ln for ln in put if not ln.strip().startswith("//")]
    i = [k for k, ln in enumerate(code)
         if "{value: p_msg_value}(" in ln]
    bad += check(len(i) == 1 and "abi.encodeWithSignature" in code[i[0] + 1],
                 "the two lines of the statement are still adjacent")
    bad += check(any("assertFalse(ok5," in ln for ln in put),
                 "the emitter's own exit-kind assertion survives")
    bad += check(stats["asserts"] == 3 and stats["state_asserts"] == 2
                 and stats["exit_kind_asserts"] == 1,
                 f"and the B gate ledger counts that explicit exit oracle: "
                 f"{stats}")
    bad += check(any(ln.strip() == "vm.prank(p_msg_sender);" for ln in put),
                 "the established prank sits above the statement, not inside "
                 "it")
    bad += check(any(ln.strip() ==
                     "vm.deal(p_msg_sender, p_msg_value);" for ln in put),
                 "and the chosen sender is FUNDED, or the call fails for lack "
                 "of funds and assertFalse passes for the wrong reason")
    return bad


ST1INCH_MISSING_ARGS_EMITTED = """\
// SPDX-License-Identifier: MIT
// Auto-generated by ESBMC 8.2.0
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {St1inch} from "./st1inch__St1inch.flat.sol";

contract St1inchCovTest is Test {
  St1inch c1;
  function setUp() public {
    c1 = new St1inch();
  }
  // claim: sol:@C@St1inch@F@approve#9171:path:3
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.approve() {} catch {}
  }
  // claim: sol:@C@St1inch@F@approve#9171:path:2
  function test_cov_1() public {
    vm.deal(address(this), 1);
    // [asserted] value sent to a NON-PAYABLE entry: the call must fail
    (bool ok2, ) = address(c1).call{value: 1}(
        abi.encodeWithSignature("approve()"));
    assertFalse(ok2, "value sent to a non-payable entry must revert");
  }
}
"""

ST1_PARAMS = [("", "address"), ("", "uint256")]


def _st1inch_missing_case(enc):
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(ST1INCH_MISSING_ARGS_EMITTED)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@St1inch@F@approve#9171", enc)
    assert case is not None, f"fixture: st1inch enc={enc} case missing"
    return em, case


def test_missing_replay_args_become_full_domain_fuzz_inputs():
    """st1inch approve reverts for all calldata, but replay omitted calldata.

    Stage 1 certified only `msg.value == 0`; the address/uint arguments are
    absent from the concrete replay because they do not affect the path. A PUT
    still has to fuzz them, otherwise an always-revert ERC20 entry produces no
    parameterized test even though the certified path is independent of them.
    """
    em, case = _st1inch_missing_case(3)
    notes = []
    put, stats = build_put(
        "St1inch", "approve", 3, 1, "sol:@C@St1inch@F@approve#9171",
        region={"msg.value": (0, 0)}, holes={}, pins={},
        params=ST1_PARAMS, emitted=em, case=case, layout={},
        ladder_rows=[], notes=notes, exit_kind="revert")
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 6
    txt = "\n".join(put)
    bad += check("function test_put_St1inch_approve_path3(address arg0, "
                 "uint256 arg1)" in txt,
                 "anonymous omitted parameters get stable fuzz names")
    bad += check("arg0 = address(uint160(bound(uint256(uint160(arg0)), 0, "
                 f"{(1 << 160) - 1})))" in txt,
                 "the omitted address argument is bounded over its full domain")
    bad += check("arg1 = bound(arg1, 0, "
                 f"{(1 << 256) - 1});" in txt,
                 "the omitted uint256 argument is bounded over its full domain")
    bad += check("try c1.approve(arg0, arg1) {} catch { _put_ok = false; }" in txt,
                 "the high-level revert-tolerant call is rewritten with calldata")
    bad += check("assertFalse(_put_ok" in txt,
                 "and the certified revert becomes an exit-kind oracle")
    bad += check(stats["fuzz_params"] == 2 and stats["exit_kind_asserts"] == 1,
                 f"the B ledger sees fuzz width and the revert oracle: {stats}")
    return bad


def test_missing_setup_replay_args_are_completed_not_fuzzed():
    """Pre-target same-unit replay calls must compile after target lifting.

    IRMSynth produced a valid raw PUT whose target call was repaired to
    `computeInterestRate(arg0,arg1,arg2)`, but earlier setup replay calls to
    the same unit stayed as `computeInterestRate()` and made Foundry reject the
    generated test before the double-oracle run could execute.
    """
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Rate} from "./Rate.sol";
contract RateCovTest is Test {
  Rate c1;
  function setUp() public {
    c1 = new Rate();
  }
  // claim: sol:@C@Rate@F@compute#12:path:9
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.compute() {} catch {}
    // [revert-tolerant] outcome not asserted
    try c1.compute() {} catch {}
    // [revert-tolerant] outcome not asserted
    try c1.compute() {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Rate@F@compute#12", 9)
    notes = []
    put, stats = build_put(
        "Rate", "compute", 9, 1, "sol:@C@Rate@F@compute#12",
        region={}, holes={}, pins={},
        params=[("a", "address"), ("x", "uint256"), ("y", "uint256")],
        emitted=em, case=case, layout={}, ladder_rows=[], notes=notes,
        exit_kind="revert")
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("try c1.compute() {} catch {}" not in text,
                 "no pre-target or target replay call is left with empty args")
    bad += check(text.count(
        "try c1.compute(address(uint160(0)), 0, 0) {} catch {}") == 2,
                 "pre-target setup calls are completed with default args")
    bad += check("try c1.compute(a, x, y) {} catch { _put_ok = false; }"
                 in text,
                 "the target call still receives the fuzz parameters")
    bad += check(any("completed 2 pre-target replay call" in n for n in notes),
                 f"the setup repair is reported: {notes}")
    bad += check(stats["fuzz_params"] == 3,
                 f"only the target call contributes fuzz width: {stats}")
    return bad


def test_unconstrained_replay_args_become_full_domain_fuzz_inputs():
    """A concrete replay argument can still be unconstrained by the proof.

    This is the same certification shape as a missing-argument replay, except
    Stage 1 happened to print a literal. Since Stage 2 proved the path under a
    region that does not mention the calldata parameters, the PUT can soundly
    replace those literals with full-domain fuzz inputs.
    """
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {St1inch} from "./st1inch__St1inch.flat.sol";

contract St1inchCovTest is Test {
  St1inch c1;
  function setUp() public {
    c1 = new St1inch();
  }
  // claim: sol:@C@St1inch@F@approve#9171:path:3
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.approve(address(uint160(1)), 2) {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@St1inch@F@approve#9171", 3)
    notes = []
    put, stats = build_put(
        "St1inch", "approve", 3, 1, "sol:@C@St1inch@F@approve#9171",
        region={"msg.value": (0, 0)}, holes={}, pins={},
        params=ST1_PARAMS, emitted=em, case=case, layout={},
        ladder_rows=[], notes=notes, exit_kind="revert",
        lift_unconstrained_calldata=True)
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 5
    txt = "\n".join(put)
    bad += check("function test_put_St1inch_approve_path3(address arg0, "
                 "uint256 arg1)" in txt,
                 "the present-but-unconstrained calldata becomes fuzz params")
    bad += check("try c1.approve(arg0, arg1) {} catch { _put_ok = false; }" in txt,
                 "the concrete replay literals are replaced at the unit call")
    bad += check("declared parameter `arg0` is absent from the certified region"
                 in "\n".join(notes),
                 "the note records why arg0 was lifted")
    bad += check("declared parameter `arg1` is absent from the certified region"
                 in "\n".join(notes),
                 "the note records why arg1 was lifted")
    bad += check(stats["rendered_width"] == {
                     "arg0": 1 << 160,
                     "arg1": 1 << 256,
                 },
                 f"the rendered widths are the full calldata domains: {stats}")
    return bad


def test_missing_address_payable_replay_arg_casts_at_the_unit_call():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";
contract VaultCovTest is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault();
  }
  // claim: sol:@C@Vault@F@pay#41:path:6
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.pay() {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Vault@F@pay#41", 6)
    notes = []
    put, stats = build_put(
        "Vault", "pay", 6, 1, "sol:@C@Vault@F@pay#41",
        region={}, holes={}, pins={},
        params=[("", "address payable")], emitted=em, case=case,
        layout={}, ladder_rows=[], notes=notes, exit_kind="revert")
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("function test_put_Vault_pay_path6(address arg0) public"
                 in text,
                 "address payable is fuzzed as an ordinary boundable address")
    bad += check("arg0 = address(uint160(bound(uint256(uint160(arg0)), 0, "
                 f"{(1 << 160) - 1})))" in text,
                 "the payable address argument is still bounded as address")
    bad += check("try c1.pay(payable(arg0)) {} catch { _put_ok = false; }"
                 in text,
                 "the high-level unit call casts the fuzz address to payable")
    bad += check(stats["fuzz_params"] == 1
                 and stats["wide_fuzz_coords"] == ["arg0"],
                 f"the B ledger sees the payable address fuzz input: {stats}")
    return bad


def test_address_payable_replay_prefix_calls_are_cast():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";
contract VaultCovTest is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault();
  }
  // claim: sol:@C@Vault@F@pay#41:path:7
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.pay(address(uint160(1))) {} catch {}
    // [revert-tolerant] outcome not asserted
    try c1.pay(address(uint160(2))) {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Vault@F@pay#41", 7)
    notes = []
    put, stats = build_put(
        "Vault", "pay", 7, 1, "sol:@C@Vault@F@pay#41",
        region={"to": (0, (1 << 160) - 1)}, holes={}, pins={},
        params=[("to", "address payable")], emitted=em, case=case,
        layout={}, ladder_rows=[], notes=notes,
        lift_unconstrained_calldata=True)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("try c1.pay(payable(address(uint160(1)))) {} catch {}"
                 in text,
                 "the replay prefix casts its payable literal")
    bad += check("try c1.pay(payable(to)) {} catch {}" in text,
                 "the lifted target call still casts the fuzz address")
    bad += check(any("repaired 2 replay call" in n for n in notes),
                 f"the repair is visible in notes: {notes}")
    bad += check(stats and stats["fuzz_params"] == 1,
                 f"the payable repair does not change PUT width accounting: "
                 f"{stats}")
    return bad


def test_address_payable_constructor_args_are_cast():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";
contract VaultCovTest is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault(address(uint160(1)), address(uint160(2)));
  }
  // claim: sol:@C@Vault@F@approve#41:path:7
  function test_cov_0() public {
    bool ok = c1.approve(address(uint160(3)), 4);
    assertTrue(ok);
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Vault@F@approve#41", 7)
    text = assemble_put_source(
        em, case, ["  function test_put_Vault_approve_path7() public {}"],
        "VaultCovTest_Vault_approve_put7", contract="Vault", unit="approve",
        constructor_params=["address payable", "address payable"])
    bad = 0
    bad += check("c1 = new Vault(payable(address(uint160(1))), "
                 "payable(address(uint160(2))));" in text,
                 "constructor address payable args are cast in setUp")
    bad += check("function test_cov_0()" not in text,
                 "stale concrete tests are still removed")
    return bad


def test_unused_setup_helper_deployment_is_revert_tolerant():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Helper} from "./Helper.sol";
import {Target} from "./Target.sol";
contract TargetCovTest is Test {
  Helper c0;
  Target c1;
  function setUp() public {
    c0 = new Helper(address(uint160(0)));
    c1 = new Target();
  }
  // claim: sol:@C@Target@F@run#41:path:7
  function test_cov_0() public {
    c1.run();
  }
}
contract TargetCovTest_1 is Test {
  Helper c0;
  Target c1;
  function setUp() public {
    c0 = new Helper(address(uint160(0)));
    c1 = new Target();
  }
  // claim: sol:@C@Target@F@run#41:path:8
  function test_cov_1() public {
    c1.run();
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Target@F@run#41", 7)
    text = assemble_put_source(
        em, case, ["  function test_put_Target_run_path7() public {",
                   "    c1.run();", "  }"],
        "TargetCovTest_Target_run_put7", contract="Target", unit="run")
    bad = 0
    bad += check(text.count("try new Helper(address(uint160(0))) returns "
                            "(Helper _esbmc_setup_c0)") == 2,
                 "unused helper deployments are revert-tolerant in every "
                 "test contract, without cross-contract name collisions")
    bad += check(text.count("c0 = _esbmc_setup_c0;") == 2,
                 "successful helper construction still assigns each instance")
    bad += check(text.count("c1 = new Target();") == 2,
                 "target deployment remains strict")
    return bad


def test_missing_fixed_bytes_replay_arg_becomes_full_domain_fuzz_input():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Keyed} from "./Keyed.sol";
contract KeyedCovTest is Test {
  Keyed c1;
  function setUp() public {
    c1 = new Keyed();
  }
  // claim: sol:@C@Keyed@F@poke#12:path:5
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.poke() {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Keyed@F@poke#12", 5)
    notes = []
    put, stats = build_put(
        "Keyed", "poke", 5, 1, "sol:@C@Keyed@F@poke#12",
        region={}, holes={}, pins={},
        params=[("", "bytes4")], emitted=em, case=case, layout={},
        ladder_rows=[], notes=notes, exit_kind="revert")
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("function test_put_Keyed_poke_path5(uint32 arg0) public"
                 in text,
                 "anonymous bytes4 calldata is fuzzed as uint32")
    bad += check(f"arg0 = uint32(bound(uint256(arg0), 0, {(1 << 32) - 1}));"
                 in text,
                 "the omitted bytes4 argument is bounded over its full domain")
    bad += check("try c1.poke(bytes4(arg0)) {} catch { _put_ok = false; }"
                 in text,
                 "the high-level replay is rewritten with a bytes4 cast")
    bad += check(stats["fuzz_params"] == 1
                 and stats["wide_fuzz_coords"] == ["arg0"],
                 f"the B ledger sees the fixed-bytes fuzz input: {stats}")
    return bad


def test_missing_string_replay_arg_becomes_dynamic_fuzz_input():
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {EmergencyOracleFactory} from "./EmergencyOracleFactory.sol";
contract EmergencyOracleFactoryCovTest is Test {
  EmergencyOracleFactory c1;
  function setUp() public {
    c1 = new EmergencyOracleFactory();
  }
  // claim: sol:@C@EmergencyOracleFactory@F@newEmergencyOracle#8:path:4
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.newEmergencyOracle() {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for(
        "sol:@C@EmergencyOracleFactory@F@newEmergencyOracle#8", 4)
    notes = []
    put, stats = build_put(
        "EmergencyOracleFactory", "newEmergencyOracle", 4, 1,
        "sol:@C@EmergencyOracleFactory@F@newEmergencyOracle#8",
        region={}, holes={}, pins={},
        params=[("description", "string calldata")], emitted=em, case=case,
        layout={}, ladder_rows=[], notes=notes, exit_kind="revert")
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("function test_put_EmergencyOracleFactory_newEmergencyOracle_"
                 "path4(string memory description) public" in text,
                 "a dynamic string calldata parameter becomes a Foundry fuzz arg")
    bad += check("bound(description" not in text,
                 "dynamic calldata is not sent through numeric bound()")
    bad += check("try c1.newEmergencyOracle(description) {} catch { "
                 "_put_ok = false; }" in text,
                 "the target call receives the dynamic fuzz parameter")
    bad += check(stats["fuzz_params"] == 1
                 and stats["wide_fuzz_coords"] == ["description"]
                 and stats["dynamic_fuzz_coords"] == ["description"],
                 f"the B ledger records the dynamic fuzz coordinate: {stats}")
    bad += check(any("not available as a numeric R1/R2 endpoint" in n
                     for n in notes),
                 f"the note records the R1/R2 limitation: {notes}")
    return bad


def test_unconstrained_string_replay_arg_becomes_dynamic_fuzz_input():
    """A printed dynamic literal can still be proof-unconstrained.

    EmergencyOracleFactory's raw replay prints `newEmergencyOracle("")`, but
    Stage 2 certifies only the admin-gate region.  Since the path proof leaves
    `description` unconstrained, the literal should be replaced by a Foundry
    fuzz argument rather than forcing a concrete replay.
    """
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {EmergencyOracleFactory} from "./EmergencyOracleFactory.sol";
contract EmergencyOracleFactoryCovTest is Test {
  EmergencyOracleFactory c1;
  function setUp() public {
    c1 = new EmergencyOracleFactory();
  }
  // claim: sol:@C@EmergencyOracleFactory@F@newEmergencyOracle#33:path:6
  function test_cov_0() public {
    // [revert-tolerant] outcome not asserted
    try c1.newEmergencyOracle("") {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for(
        "sol:@C@EmergencyOracleFactory@F@newEmergencyOracle#33", 6)
    notes = []
    put, stats = build_put(
        "EmergencyOracleFactory", "newEmergencyOracle", 6, 2,
        "sol:@C@EmergencyOracleFactory@F@newEmergencyOracle#33",
        region={"state.isAdmin[msg.sender]": (0, 0)}, holes={}, pins={},
        params=[("description", "string calldata")], emitted=em, case=case,
        layout={}, ladder_rows=[], notes=notes, exit_kind="revert",
        lift_unconstrained_calldata=True)
    text = "\n".join(put or [])
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    bad += check("function test_put_EmergencyOracleFactory_newEmergencyOracle_"
                 "path6(string memory description) public" in text,
                 "the concrete string literal becomes a fuzz parameter")
    bad += check('newEmergencyOracle("")' not in text,
                 "the target call no longer pins the literal string")
    bad += check("try c1.newEmergencyOracle(description) {} catch { "
                 "_put_ok = false; }" in text,
                 "the target call is rewritten with the fuzzed description")
    bad += check(stats["fuzz_params"] == 1
                 and stats["dynamic_fuzz_coords"] == ["description"],
                 f"the dynamic coordinate is counted as fuzzed: {stats}")
    bad += check(stats["oracle_classes"] == ["R0"],
                 f"the rollback path carries only the exit oracle: {stats}")
    return bad


def test_missing_low_level_dynamic_args_update_abi_signature():
    line = (
        '    (bool ok, ) = address(c1).call(abi.encodeWithSignature("pack()"));')
    completed, args, implicit, err = complete_missing_call_args(
        line, "pack", [("label", "string calldata"), ("blob", "bytes")], [])
    bad = 0
    bad += check(err is None, f"dynamic low-level completion succeeds: {err}")
    bad += check(implicit == [0, 1],
                 f"both dynamic parameters are implicit fuzz candidates: "
                 f"{implicit}")
    bad += check(args == ['""', 'hex""'],
                 f"dynamic defaults compile in raw replay/setup paths: {args}")
    bad += check('abi.encodeWithSignature("pack(string,bytes)", "", hex"")'
                 in (completed or ""),
                 f"the ABI signature and defaults are rendered: {completed}")
    return bad


def test_missing_low_level_value_gate_args_update_abi_signature():
    """The same omitted-argument repair must update low-level ABI calls."""
    em, case = _st1inch_missing_case(2)
    notes = []
    put, stats = build_put(
        "St1inch", "approve", 2, 1, "sol:@C@St1inch@F@approve#9171",
        region={"msg.value": (1, UINT256_MAX)}, holes={}, pins={},
        params=ST1_PARAMS, emitted=em, case=case, layout={},
        ladder_rows=[], notes=notes, exit_kind="revert")
    bad = 0
    bad += check(put is not None, f"a PUT is produced (notes: {notes})")
    if put is None:
        return bad + 5
    txt = "\n".join(put)
    bad += check('abi.encodeWithSignature("approve(address,uint256)", '
                 "arg0, arg1))" in txt,
                 "the low-level ABI signature and arguments are completed")
    bad += check("{value: p_msg_value}(" in txt,
                 "the value-gate msg.value coordinate is still fuzzed")
    bad += check("assertFalse(ok2," in txt,
                 "the concrete value-gate exit assertion survives")
    bad += check(stats["fuzz_params"] == 3 and stats["exit_kind_asserts"] == 1,
                 f"two calldata fuzz params plus msg.value are counted: {stats}")
    return bad


def test_assembled_put_source_drops_stale_concrete_replays():
    """PUT projects should not compile stale `test_cov_*` replay functions."""
    em, case = _st1inch_missing_case(3)
    notes = []
    put, _stats = build_put(
        "St1inch", "approve", 3, 1, "sol:@C@St1inch@F@approve#9171",
        region={"msg.value": (0, 0)}, holes={}, pins={},
        params=ST1_PARAMS, emitted=em, case=case, layout={},
        ladder_rows=[], notes=notes, exit_kind="revert")
    text = assemble_put_source(
        em, case, [put], "St1inchCovTest_St1inch_approve_put3")
    bad = 0
    bad += check("function setUp()" in text,
                 "the deployment preamble stays in the PUT project")
    bad += check("function test_cov_" not in text,
                 "stale concrete replay functions are removed")
    bad += check("try c1.approve() {} catch {}" not in text,
                 "the zero-argument stale replay call cannot kill compilation")
    bad += check("function test_put_St1inch_approve_path3" in text,
                 "the generated PUT remains")
    bad += check("try c1.approve(arg0, arg1) {} catch" in text,
                 "and the repaired PUT call is present")
    return bad


def test_assembled_put_source_drops_stale_replays_in_later_contracts():
    """Deleting stale replays must use pre-insertion line numbers."""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {Vault} from "./Vault.sol";
contract VaultCovTest_0 is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault();
  }
  // claim: sol:@C@Vault@F@pay#41:path:7
  function test_cov_0() public {
    try c1.pay(address(uint160(1))) {} catch {}
  }
}
contract VaultCovTest_1 is Test {
  Vault c1;
  function setUp() public {
    c1 = new Vault();
  }
  // claim: sol:@C@Vault@F@pay#41:path:6
  function test_cov_1() public {
    try c1.pay(address(uint160(2))) {} catch {}
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@Vault@F@pay#41", 7)
    put = [
        "",
        "  function test_put_Vault_pay_path7(address to) public {",
        "    try c1.pay(payable(to)) {} catch {}",
        "  }",
    ]
    text = assemble_put_source(em, case, [put], "VaultCovTest_0_put")
    bad = 0
    bad += check("function test_put_Vault_pay_path7" in text,
                 "the generated PUT is inserted")
    bad += check("function test_cov_" not in text,
                 "stale concrete replays are removed across all contracts")
    bad += check("address(uint160(2))" not in text,
                 "the later contract's stale body is not left behind")
    return bad


def test_assembled_concrete_source_keeps_only_the_selected_replay():
    """Point regions are concrete deliverables, not parameterized PUTs."""
    em, case = _st1inch_missing_case(3)
    text = assemble_concrete_source(
        em, case, "St1inchCovTest_St1inch_approve_concrete3")
    bad = 0
    bad += check("contract St1inchCovTest_St1inch_approve_concrete3 is Test"
                 in text,
                 "the concrete fallback gets a unique test contract")
    bad += check("function test_cov_1()" not in text,
                 "unrelated concrete replays are removed")
    bad += check("function test_cov_0()" in text,
                 "the selected concrete replay remains")
    bad += check("function test_put_" not in text,
                 "the concrete fallback does not pretend to be a PUT")
    bad += check("from \"../src/" in text,
                 "imports are still rewritten for the forge project layout")
    return bad


def test_assembled_concrete_source_refuses_unsupported_replay():
    """A green empty replay is not a reference test."""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {BoxUser} from "./BoxUser.sol";

contract BoxUserCovTest is Test {
  BoxUser c0;
  function setUp() public {
    c0 = new BoxUser();
  }
  // claim: sol:@C@BoxUser@F@put#9:path:4
  function test_cov_0() public {
    // UNSUPPORTED: BoxUser.put has an argument type ESBMC cannot yet render as a literal
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@BoxUser@F@put#9", 4)
    bad = 0
    try:
        assemble_concrete_source(
            em, case, "BoxUserCovTest_BoxUser_put_concrete4", unit="put")
        refused = None
    except ValueError as exc:
        refused = str(exc)
    bad += check(refused is not None and "UNSUPPORTED" in refused,
                 f"unsupported concrete replay is refused: {refused}")
    return bad


def test_assembled_concrete_source_completes_missing_call_args():
    """A bad concrete replay must not poison a neighbouring PUT project."""
    emitted = """\
// SPDX-License-Identifier: MIT
pragma solidity >=0.8.0;
import {Test} from "forge-std/Test.sol";
import {C} from "./C.sol";

contract CCovTest_0 is Test {
  C c0;
  function setUp() public {
    c0 = new C();
  }
  // claim: sol:@C@C@F@f#9:path:4
  function test_cov_0() public {
    try c0.f() {} catch {}
    c0.f();
  }
}
"""
    fd, path = tempfile.mkstemp(suffix=".cov.t.sol")
    with os.fdopen(fd, "w") as f:
        f.write(emitted)
    try:
        em = EmittedFile(path)
    finally:
        os.unlink(path)
    case = em.case_for("sol:@C@C@F@f#9", 4)
    text = assemble_concrete_source(
        em, case, "CCovTest_C_f_concrete4", unit="f",
        params=[("a", "address"), ("b", "uint256"), ("c", "uint256")])
    bad = 0
    completed = "c0.f(address(uint160(0)), 0, 0)"
    bad += check(text.count(completed) == 2,
                 "all omitted concrete replay arguments are completed")
    bad += check("c0.f()" not in text,
                 "the uncompilable zero-argument calls are gone")
    bad += check("function test_put_" not in text,
                 "the repaired concrete replay is still not a PUT")
    return bad


def test_the_funding_line_precedes_the_prank():
    """`vm.prank` binds to the NEXT call; a deal placed after it would consume
    nothing but must not be the last cheatcode standing between it and the
    call. foundry.cpp's own comment states the ordering rule."""
    put, _stats, _n = _value_gate_put({"msg.value": (1, UINT256_MAX)})
    code = [ln.strip() for ln in (put or []) if not ln.strip().startswith("//")]
    bad = 0
    if "vm.deal(p_msg_sender, p_msg_value);" not in code:
        return check(False, "the funding line is present")
    bad += check(code.index("vm.deal(p_msg_sender, p_msg_value);")
                 < code.index("vm.prank(p_msg_sender);"),
                 "the deal comes before the prank")
    return bad


def test_a_value_gate_certified_at_ZERO_still_REFUSES():
    """THE MUST-FLIP, and it flips the OTHER WAY from the case above.

    The reader used to answer 0 for this statement. That wrong answer REFUSED
    `msg.value in [1, ...]` (previous test) and would have ACCEPTED
    `msg.value == 0` (this one). A reader hard-wired to either constant passes
    exactly one of the two; only one that actually reads `{value: 1}` passes
    both.
    """
    put, _stats, notes = _value_gate_put({}, pins={"msg.value": 0})
    bad = 0
    bad += check(put is None, "a call that sends 1 is NOT in the slice value==0")
    bad += check(any("msg.value is certified at 0" in n and "sets it to 1" in n
                     for n in notes),
                 f"and the refusal quotes the value it actually read: {notes}")
    return bad


def main():
    bad = 0
    # ---- THE REGISTRY BELOW IS HAND-MAINTAINED, SO IT IS CHECKED ----------
    #
    # Two ways this module has silently measured nothing, both real:
    #   * a `test_` function written and never added here -- it simply never
    #     runs, and the suite is green because it asked no question;
    #   * an exception in an early test ABORTING the loop, so every later test
    #     is skipped. That is what a stale `SLOT_MAPS` did, hiding an
    #     already-broken assertion behind an unrelated ValueError.
    #
    # The first is caught here. The second is caught by counting how many
    # tests actually reported, below.
    registered = set()
    declared = {k for k, v in list(globals().items())
                if k.startswith("test_") and callable(v)}
    ran = 0
    for t in (test_dropped_rungs_are_not_reported_as_no_rung_holding,
              test_the_ANTICHAIN_keeps_the_STRICT_rung_and_drops_what_it_entails,
              test_the_ANTICHAIN_may_not_let_a_GUARDED_rung_dominate_an_UNGUARDED_one,
              test_the_ANTICHAIN_never_uses_a_REFUTED_rung_to_drop_a_HOLDING_one,
              test_the_ANTICHAIN_does_not_reach_ACROSS_VARIABLES,
              test_the_ANTICHAIN_lets_a_DELTA_rung_dominate_NOTHING,
              test_the_ANTICHAIN_drops_return_rungs_implied_by_a_literal_value,
              test_the_ANTICHAIN_does_not_use_REFUTED_return_rows_as_evidence,
              test_the_ANTICHAIN_normalizes_R2_point_values_before_dominance,
              test_the_ANTICHAIN_does_not_normalize_unpinned_R2_coordinates,
              test_the_ANTICHAIN_folds_R2_zero_and_one_identities,
              test_the_ANTICHAIN_folds_safe_commutative_and_self_delta_terms_only,
              test_a_ladder_where_nothing_held_still_says_so,
              test_the_retlive_witness_is_not_counted_as_a_rung_that_held,
              test_a_revert_tolerant_body_is_NOT_called_an_assertion,
              test_an_asserted_body_still_counts_as_an_assertion,
              test_relation_establishes_state_from_fuzzed_sender,
              test_both_truncation_shapes_are_read,
              test_the_ladder_widens_every_named_loop,
              test_a_retry_that_produced_no_ladder_is_not_adopted,
              test_region_coordinate_ladder_refusal_is_read,
              test_partial_ladder_rows_are_used_only_when_final_table_is_missing,
              test_a_widened_ladder_says_which_half_it_applies_to,
              test_the_cell_is_named_and_an_unsettled_one_says_so,
              test_the_emitted_test_carries_its_cell,
              test_esbmc_arg_passthrough_admits_unwindset_and_refuses_strategies,
              test_pin_with_a_slot_is_established,
              test_precheck_only_identifies_rendered_width_not_oracle_strength,
              test_no_wide_rendered_coordinate_without_oracle_stays_concrete,
              test_precheck_keeps_possible_parameterized_candidate_on_wide_env,
              test_storage_oracles_read_the_actual_target_instance_not_c0,
              test_path_cov_fixture_replays_constructor_then_pins_state,
              test_constructor_staticcall_mock_is_scoped_to_deployment,
              test_constructor_param_interface_calls_are_mocked_before_deploy,
              test_constructor_param_hascode_args_are_etched_before_deploy,
              test_constructor_nonzero_address_guards_repair_zero_defaults,
              test_constructor_dynamic_array_defaults_cover_indexed_reads,
              test_unsupported_skeleton_is_synthesized_for_certified_put_lift,
              test_esbmc_interface_mock_completion_adds_inherited_overloads,
              test_runtime_interface_mock_lines_cover_literal_address_calls,
              test_constructor_external_interface_mocks_cover_router_factory_chain,
              test_runtime_interface_mocks_survive_constructor_mock_clear,
              test_pranked_constructor_replay_sets_tx_origin_too,
              test_pin_without_a_slot_is_reported_not_dropped,
              test_region_bound_still_wins_over_a_duplicate_pin,
              test_env_agreement_emits_when_the_preamble_matches,
              test_env_sender_disagreement_is_ESTABLISHED_not_refused,
              test_target_sender_prank_is_inserted_after_replay_setup,
              test_env_value_pin_disagreement_refuses,
              test_msg_value_without_a_value_option_is_still_CHECKED_and_refuses,
              test_uncomparable_env_quantity_refuses_emission,
              test_block_timestamp_point_is_established_with_warp,
              test_block_timestamp_range_is_fuzzed_with_warp,
              test_block_number_point_is_established_with_roll,
              test_block_env_pins_are_established_with_cheatcodes,
              test_block_number_range_is_fuzzed_with_roll,
              test_block_chainid_range_is_fuzzed_with_chainId,
              test_extra_numeric_env_ranges_use_modeled_cheatcodes,
              test_block_coinbase_range_is_fuzzed_with_coinbase,
              test_return_rung_is_bound_and_asserted,
              test_return_rung_can_assert_a_scalar_entry_state_coord,
              test_return_rung_can_assert_a_pinned_nonlayout_state_coord,
              test_return_rung_can_assert_a_mapping_entry_state_coord,
              test_a_retlive_that_HOLDS_kills_every_return_rung,
              test_a_bool_return_uses_assertTrue_not_a_cast,
              test_a_bool_return_can_assert_a_structured_bool_coord,
              test_a_bool_literal_return_equality_reaches_the_put,
              test_fixed_bytes_return_casts_through_the_matching_uint_width,
              test_a_whole_value_rung_on_a_tuple_unit_is_refused,
              test_per_member_rungs_destructure_in_declaration_order,
              test_a_member_with_no_rung_gets_an_EMPTY_slot,
              test_a_member_index_beyond_the_declaration_refuses,
              test_an_unbindable_member_does_not_cost_the_others,
              test_a_retlive_that_HOLDS_kills_member_rungs_too,
              test_an_unbindable_return_type_is_reported,
              test_bind_return_refuses_the_exit_kind_shapes,
              test_a_return_only_oracle_still_reaches_the_test,
              test_a_nonzero_literal_return_equality_is_asserted,
              test_a_nonzero_literal_return_inequality_is_asserted,
              test_a_refuted_return_rung_is_never_asserted,
              test_an_ESTABLISHED_SCALAR_PIN_is_READ_BACK_and_checked,
              test_an_ESTABLISHED_MAPPING_PIN_is_READ_BACK_at_the_HASHED_slot,
              test_a_WIDE_entry_bound_is_CHECKED_READ_ONLY_instead_of_dropped,
              test_a_WHOLE_TYPE_entry_bound_emits_NO_TAUTOLOGY_and_says_so,
              test_a_slot_pin_keyed_by_a_parameter_is_established,
              test_a_slot_pin_keyed_by_a_literal_is_established,
              test_a_slot_pin_keyed_by_entry_state_is_established,
              test_a_slot_pin_keyed_by_msg_sender_is_REFUSED,
              test_a_probe_only_width_is_FLAGGED_on_the_test,
              test_a_ladder_derived_width_is_NOT_flagged,
              test_probes_do_NOT_claim_a_ladder_when_the_bracket_was_skipped,
              test_no_derivation_recorded_prints_no_provenance_block,
              test_an_entirely_holed_coordinate_REFUSES_the_put,
              test_an_INCREASING_variable_proposes_an_inc_delta_named_by_the_parameter,
              test_a_DECREASING_variable_proposes_dec_not_inc,
              test_an_UNCHANGED_variable_proposes_NO_DELTA_and_says_why,
              test_a_MIXED_DIRECTION_region_proposes_NO_DELTA_but_DOES_propose_ABS,
              test_a_unit_whose_ONLY_PARAMETER_IS_AN_ADDRESS_proposes_an_ABS_bound,
              test_a_unit_with_NO_USABLE_PARAMETER_AT_ALL_proposes_nothing,
              test_TWO_esbmc_invocations_do_not_share_ONE_log_file,
              test_a_candidate_with_NO_STORAGE_SLOT_gets_NO_R2_QUERY,
              test_a_SLOTTED_candidate_beside_an_UNSLOTTED_one_still_gets_ITS_query,
              test_asked_but_never_answered_is_counted_as_zero_not_as_seven,
              test_a_ladder_that_answered_everything_reports_no_gap,
              test_no_slot_asked_means_no_gap_and_no_claim,
              test_the_EXCLUSION_MESSAGE_names_no_cause_it_did_not_measure,
              test_an_IDENTITY_endpoint_is_only_asked_about_candidates_of_ITS_WIDTH,
              test_a_FIXED_BYTES_endpoint_is_width_filtered_like_an_identity,
              test_the_WIDTH_FILTER_leaves_a_NUMERIC_endpoint_alone,
              test_WITHOUT_a_width_table_NOTHING_is_filtered,
              test_an_IDENTITY_with_NO_CANDIDATE_OF_ITS_WIDTH_sends_NO_QUERY,
              test_TWO_integer_parameters_produce_TWO_SEPARATE_queries,
              test_a_fuzz_REFUTATION_is_read_and_a_pass_is_NOT_a_proof,
              test_a_probe_THAT_NEVER_RAN_is_NOT_RUN_not_a_pass,
              test_JSON_fuzz_filter_refutes_only_its_labeled_assertion,
              test_R2_fuzz_filter_removes_only_concretely_refuted_candidates,
              test_typed_R2_is_ONE_BATCH_and_contains_pre_plus_coordinate,
              test_typed_R2_proposes_return_equals_entry_state_coord_for_getters,
              test_typed_R2_return_candidates_never_name_pre_snapshot,
              test_typed_R2_bool_return_asks_equality_only,
              test_normal_exit_retreat_bounds_prefix_increment_return,
              test_normal_exit_retreat_keeps_product_region_for_variable_add,
              test_source_R2_prefix_increment_return_candidate_is_asked,
              test_typed_R2_candidate_budget_ignores_empty_bool_return_queue,
              test_path_decision_guard_renders_mapping_slot_relation,
              test_path_decision_guard_negates_plain_branch_claim,
              test_path_decision_guard_handles_double_negated_branch_claim,
              test_path_decision_guard_splits_safe_boolean_shapes,
              test_path_decision_guard_renders_unary_bool_mapping_relation,
              test_path_decision_guard_negates_plain_unary_bool_claim,
              test_path_decision_guard_skips_true_constant_relation,
              test_path_guard_materializes_state_coord_without_oracle_rung,
              test_path_guard_coord_idents_expand_scalar_store_aliases,
              test_path_guard_coord_idents_expand_mapping_source_aliases,
              test_path_guard_coord_idents_expand_mapping_store_aliases,
              test_typed_R2_term_budget_is_VISIBLE_not_a_second_query,
              test_typed_R2_candidate_budget_caps_claims_and_shares_them,
              test_typed_R2_candidate_budget_reaches_every_variable_before_second_laps,
              test_skipped_forge_R2_accounting_is_complete_and_conservative,
              test_partial_ladder_R2_skip_requires_a_rendered_strict_oracle,
              test_oracle_class_metadata_keeps_R0_R1_R2_apart,
              test_effective_exit_kind_falls_back_to_the_fresh_claim,
              test_a_STAGE1_normal_try_call_is_unwrapped_for_return_oracles,
              test_a_normal_try_call_with_trailing_semicolon_is_unwrapped,
              test_typed_R2_omits_bool_without_a_bool_endpoint,
              test_typed_R2_proposes_bool_equality_to_bool_coordinate,
              test_a_bool_region_parameter_is_lifted_and_can_feed_R2,
              test_a_bool_region_parameter_can_feed_bool_return_R2,
              test_a_fixed_bytes_region_parameter_is_lifted_via_uint_input,
              test_R2_candidate_dedup_uses_safe_normalized_text_before_fuzz,
              test_source_R2_atoms_are_scoped_to_the_unit_and_contract_chain,
              test_source_R2_assignment_candidates_are_small_setter_queries,
              test_source_R2_self_updates_prioritize_delta_queries,
              test_source_R2_mapping_slot_updates_prioritize_exact_slot_queries,
              test_source_R2_helper_mapping_increment_unwraps_tuple_argument,
              test_source_R2_unary_updates_prioritize_one_step_deltas,
              test_source_R2_delete_updates_prioritize_zero_endpoints,
              test_source_R2_address_zero_assignments_prioritize_zero_endpoints,
              test_source_R2_environment_value_assignments_use_rendered_env_coords,
              test_source_R2_msg_sender_helper_calls_use_rendered_env_coord,
              test_source_R2_inlines_one_internal_helper_call,
              test_source_R2_mines_modifier_suffix_effects_only,
              test_source_R2_arithmetic_assignments_prioritize_expression_endpoints,
              test_source_R2_state_entry_coords_are_used_only_when_rendered,
              test_source_R2_type_conversion_wrappers_are_unwrapped_conservatively,
              test_source_R2_constant_identifiers_prioritize_literal_endpoints,
              test_source_R2_mapping_literal_keys_are_named_when_slot_safe,
              test_source_R2_mapping_constant_keys_fold_to_safe_slot_literals,
              test_source_R2_enum_mapping_keys_use_same_typed_params,
              test_source_R2_enum_state_literals_fold_to_ordinals,
              test_source_R2_return_candidates_prioritize_return_expressions,
              test_source_R2_return_conditionals_expose_leaf_candidates,
              test_source_R2_return_type_conversion_wrappers_are_unwrapped,
              test_source_R2_return_can_name_a_rendered_state_pin,
              test_source_R2_local_aliases_feed_return_state_and_mapping_terms,
              test_source_R2_local_aliases_are_invalidated_after_mutation,
              test_source_R2_mapping_getter_returns_named_entry_slot_coord,
              test_storage_layout_expands_top_level_struct_scalar_members,
              test_storage_layout_expands_struct_mapping_members,
              test_source_R2_struct_mapping_members_wait_for_ESBMC_support,
              test_source_R2_top_level_struct_members_are_state_coords,
              test_source_R2_storage_local_aliases_resolve_to_state_coords,
              test_source_R2_storage_mapping_aliases_preserve_later_indices,
              test_bool_literal_R2_rows_render_from_ESBMC_true_spelling,
              test_source_R2_candidates_run_before_the_typed_batch,
              test_source_R2_schedule_keeps_source_outside_the_mechanical_budget,
              test_same_arity_overloads_use_the_exact_path_declaration,
              test_overload_persistence_keys_and_work_suffixes_are_distinct,
              test_structured_R2_term_renders_with_the_lifted_coordinate,
              test_structured_R2_interval_accepts_literal_endpoint_without_lookup,
              test_structured_R2_term_renders_with_entry_mapping_coord,
              test_structured_R2_requires_a_successful_revert_tolerant_call,
              test_oracle_mapping_candidates_share_the_dependency_filter,
              test_an_R2_PASS_actually_runs_and_carries_the_proposed_vars,
              test_an_ABSOLUTE_row_is_MERGED_and_not_silently_dropped,
              test_a_RETURN_R2_row_is_MERGED_and_not_reported_empty,
              test_the_CAP_pass_RUNS_when_stage_1_REFUTED_the_exact_delta,
              test_the_CAP_pass_IS_SKIPPED_when_stage_1_ALREADY_HOLDS,
              test_the_CAP_pass_IS_SKIPPED_when_stage_1_gave_NO_VERDICT,
              test_a_source_R2_HOLD_skips_later_mechanical_candidates_for_that_var,
              test_exact_mapping_R2_unknown_is_the_only_CVC5_retry_shape,
              test_an_R2_PASS_THAT_RETURNS_NOTHING_is_REPORTED_not_absorbed,
              test_a_ROLLBACK_path_DOES_NOT_SPEND_an_R2_ESBMC_pass,
              test_a_REVERT_path_DOES_NOT_SPEND_an_R2_ESBMC_pass,
              test_an_R2_PASS_never_overwrites_a_row_the_FIRST_pass_decided,
              test_a_ONE_LEVEL_mapping_proposes_one_key,
              test_a_NESTED_mapping_proposes_ONE_KEY_PER_LEVEL,
              test_a_NESTED_STRUCT_mapping_keeps_its_FIELD_TAIL,
              test_a_LEVEL_WITH_NO_MATCHING_PARAMETER_proposes_NOTHING,
              test_a_FIXED_BYTES_mapping_level_uses_same_typed_parameter,
              test_mapping_proposer_includes_safe_entry_state_keys_after_params,
              test_source_access_slots_preserve_state_keys_before_fallback,
              test_source_access_slots_keep_numeric_environment_keys,
              test_source_access_slots_unwrap_safe_type_conversion_keys,
              test_source_access_slots_keep_safe_literal_keys,
              test_source_access_slots_fold_safe_constant_keys,
              test_source_access_slots_resolve_local_key_aliases_in_order,
              test_source_access_slots_substitute_helper_and_modifier_actuals,
              test_source_access_slots_treat_msg_sender_helper_actual_as_sender,
              test_source_access_slots_follow_call_options_wrapped_helpers,
              test_source_access_slots_render_state_struct_member_keys,
              test_the_CANDIDATE_BUDGET_says_what_it_dropped,
              test_certified_region_mapping_slots_are_ASKED_before_guesses,
              test_mapping_aliases_keep_source_names_for_ladder_vars,
              test_mapping_aliases_keep_struct_member_tails_source_named,
              test_scalar_layout_aliases_use_source_slots_for_foundry_rendering,
        test_scalar_assert_vars_use_source_names_and_restore_legacy_rows,
              test_state_store_aliases_have_one_canonical_entry_coordinate,
              test_mapping_store_aliases_have_source_path_guard_coordinate,
              test_contract_state_store_aliases_read_solc_declaration_ids,
              test_assert_query_keeps_state_pins_for_the_certified_slice,
              test_assert_query_region_keeps_slots_but_drops_state_scalars,
              test_an_ADDRESS_endpoint_renders_for_an_ABSOLUTE_bound,
              test_an_ADDRESS_endpoint_is_STILL_REFUSED_for_a_DELTA_bound,
              test_a_named_R2_bound_renders_as_the_test_parameter,
              test_an_R2_bound_naming_an_UNLIFTED_COORDINATE_is_DROPPED,
              test_an_OBSERVED_sender_renders_for_an_ABSOLUTE_R2_endpoint,
              test_an_OBSERVED_msg_value_renders_for_numeric_R2_endpoints,
              test_OBSERVED_block_cheatcodes_render_for_numeric_R2_endpoints,
              test_OBSERVED_block_env_slot_keys_are_nameable,
              test_R2_proposal_env_coords_include_observable_replay_values,
              test_R2_env_coords_are_recovered_from_the_emitted_case,
              test_a_numeric_R2_bound_is_UNCHANGED,
              test_a_RENAMED_coordinate_is_spelled_with_its_TEST_name,
              test_a_hole_OUTSIDE_the_interval_costs_no_width,
              test_a_REPEATED_hole_is_counted_once,
              test_a_nested_slot_is_read_at_the_ITERATED_hash,
              test_a_slot_named_with_the_WRONG_DEPTH_is_refused,
              test_the_oracle_side_refuses_the_same_key,
              test_a_slot_keyed_by_an_ESTABLISHED_FUZZED_sender_is_READ,
              test_a_slot_keyed_by_an_ESTABLISHED_POINT_sender_is_WRITTEN,
              test_an_OBSERVED_msg_value_slot_key_is_nameable,
              test_a_change_rung_is_GUARDED_on_a_revert_tolerant_call,
              test_a_ROLLBACK_path_drops_every_layer_2_3_rung_and_ASSERTS_THE_REVERT,
              test_a_STAGE1_normal_path_counts_the_bare_call_as_R0,
              test_a_ROLLBACK_bare_call_gets_expectRevert_layer_1_oracle,
              test_a_NON_rollback_path_is_BYTE_IDENTICAL_to_before,
              test_a_STAGE1_revert_path_ASSERTS_THE_REVERT_without_calling_it_rollback,
              test_the_ROLLBACK_LINE_of_the_ladder_log_is_PARSED_in_both_directions,
              test_the_same_change_rung_IS_asserted_on_a_bare_call,
              test_a_wide_env_coordinate_is_FUZZED_not_disclosed_as_one_point,
              test_a_wide_env_coordinate_EXCLUDING_the_sender_is_also_fuzzed,
              test_a_width_one_env_coordinate_emits_at_the_certified_value,
              test_a_piece_label_distinguishes_two_boxes_of_one_path,
              test_the_value_gate_statement_is_read_as_ONE_statement,
              test_high_level_value_call_is_parsed_and_rewritten,
              test_payable_high_level_call_can_establish_msg_value,
              test_nonpayable_high_level_call_still_refuses_wide_msg_value,
              test_only_the_low_level_value_gate_assertion_counts_as_exit_kind,
              test_a_single_line_call_still_reports_its_own_statement,
              test_the_low_level_value_gate_emits_a_PUT,
              test_foundry_fixture_loading_keeps_esbmc_fixture_as_fallback,
              test_missing_replay_args_become_full_domain_fuzz_inputs,
              test_missing_setup_replay_args_are_completed_not_fuzzed,
              test_unconstrained_replay_args_become_full_domain_fuzz_inputs,
              test_missing_address_payable_replay_arg_casts_at_the_unit_call,
              test_address_payable_replay_prefix_calls_are_cast,
              test_address_payable_constructor_args_are_cast,
              test_unused_setup_helper_deployment_is_revert_tolerant,
              test_missing_fixed_bytes_replay_arg_becomes_full_domain_fuzz_input,
              test_missing_string_replay_arg_becomes_dynamic_fuzz_input,
              test_unconstrained_string_replay_arg_becomes_dynamic_fuzz_input,
              test_missing_low_level_dynamic_args_update_abi_signature,
              test_missing_low_level_value_gate_args_update_abi_signature,
              test_assembled_put_source_drops_stale_concrete_replays,
              test_assembled_put_source_drops_stale_replays_in_later_contracts,
              test_assembled_concrete_source_keeps_only_the_selected_replay,
              test_assembled_concrete_source_refuses_unsupported_replay,
              test_assembled_concrete_source_completes_missing_call_args,
              test_the_funding_line_precedes_the_prank,
              test_a_value_gate_certified_at_ZERO_still_REFUSES):
        print(f"--- {t.__name__}")
        registered.add(t.__name__)
        bad += t()
        ran += 1
    missing = sorted(declared - registered)
    if missing:
        print(f"\n⛔ {len(missing)} test function(s) exist in this module and "
              f"are NOT in the registry above, so they never ran: "
              f"{', '.join(missing)}")
        bad += len(missing)
    if ran != len(registered):
        print(f"\n⛔ {len(registered)} test(s) registered but {ran} reported -- "
              f"the loop did not reach the end")
        bad += 1
    print(f"\n{ran} test(s) ran, {len(declared)} declared in this module")
    if bad:
        print(f"\n{bad} check(s) FAILED")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
