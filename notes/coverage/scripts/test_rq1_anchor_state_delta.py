#!/usr/bin/env python3
"""Focused tests for exact state-delta anchor recovery."""

from rq1_anchor_state_delta import materialize_state_delta_oracles


def check(condition, message):
    """Print one TAP-like assertion and return its failure count."""
    print(("ok - " if condition else "not ok - ") + message)
    return 0 if condition else 1


def main():
    """Exercise exact reads and fail-closed recovery boundaries."""
    bad = 0
    source = ("contract T { function test_cov_0() public {\n"
              "    c0.f();\n"
              "  }\n}\n")
    layout = {"packed": (2, 20, 1), "wide": (3, 0, 32)}
    maps = {"balances": (4, "address", 32, 0, "balances", None)}
    rewritten, oracles, error = materialize_state_delta_oracles(source, "test_cov_0", "f", {
        "packed": {
            "before": "0",
            "after": "1"
        },
        "balances[7]": {
            "before": "0",
            "after": "9"
        },
    }, (layout, maps))
    bad += check(error is None and len(oracles) == 2,
                 "packed scalar and mapping delta materialize together")
    bad += check(
        "vm.load(address(c0),bytes32(uint256(2)))" in rewritten and ">>160" in rewritten
        and "&uint256(0xff)" in rewritten, "packed scalar uses exact slot, offset, and width")
    bad += check("keccak256(abi.encode(address(uint160(7)), uint256(4)))" in rewritten,
                 "mapping read preserves the solc key type")
    bad += check(
        all(item["kind"] == "storage-slot-post-state" for item in oracles)
        and {item["storage_variable"]
             for item in oracles} == {"packed", "balances[7]"},
        "oracle metadata covers every changed variable exactly")

    unchanged, refused, error = materialize_state_delta_oracles(source, "test_cov_0", "f",
                                                                {"_balances$45[1]": {
                                                                    "after": "2"
                                                                }}, (layout, maps))
    bad += check(unchanged == source and not refused and "no scalar solc layout" in str(error),
                 "ESBMC internal storage aliases are refused rather than guessed")
    trailing = source.replace("    c0.f();", "    c0.f();\n    helper.g();")
    unchanged, refused, error = materialize_state_delta_oracles(trailing, "test_cov_0", "f",
                                                                {"wide": {
                                                                    "after": "2"
                                                                }}, (layout, maps))
    bad += check(unchanged == trailing and not refused and "after the target" in str(error),
                 "post-call statements cannot be displaced by recovery")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
