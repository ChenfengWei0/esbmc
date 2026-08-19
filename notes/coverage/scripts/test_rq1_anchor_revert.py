#!/usr/bin/env python3
"""Focused tests for strict revert-only anchor recovery."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rq1_anchor_revert import (  # pylint: disable=wrong-import-position
    _output_path_error, _validated_selector, materialize_revert_oracle)
import rq1_put_ce_anchor_backfill as backfill  # pylint: disable=wrong-import-position
from rq1_concrete_replay_store import (  # pylint: disable=wrong-import-position
    _oracle_binding_errors)


def _source(body: str) -> str:
    """Wrap a small replay body in one test contract."""
    return "contract T {\n  function test_cov_0() public {\n" + body + "\n  }\n}\n"


def main() -> int:
    """Exercise every accepted shape plus an inert-string rejection."""
    # pylint: disable=too-many-locals
    failures = 0

    low_level = _source(
        "    (bool ok, ) = address(c0).call{value: 1}(\n"
        '      abi.encodeWithSignature("owner()"));\n'
        '    assertFalse(ok, "must revert");')
    rendered, oracles, error = materialize_revert_oracle(low_level, "test_cov_0", "owner")
    failures += int(error is not None or rendered == low_level or
                    "bool _veriput_revert_status" not in rendered or
                    [oracle["kind"] for oracle in oracles] != ["call-status"] or
                    bool(_oracle_binding_errors(rendered, "test_cov_0", "owner", oracles)))

    tolerant = _source(
        "    try c0.f{value: 7}(uint256(1)) {} catch {}")
    rendered, oracles, error = materialize_revert_oracle(tolerant, "test_cov_0", "f")
    failures += int(error is not None or "vm.expectRevert();" not in rendered or
                    "c0.f{value: 7}(uint256(1));" not in rendered or
                    [oracle["kind"] for oracle in oracles] != ["revert"] or
                    bool(_oracle_binding_errors(rendered, "test_cov_0", "f", oracles)))

    direct = _source("    c0.f(uint256(1));")
    rendered, oracles, error = materialize_revert_oracle(direct, "test_cov_0", "f")
    failures += int(error is not None or
                    rendered.index("vm.expectRevert();") > rendered.index("c0.f(") or
                    len(oracles) != 1 or
                    bool(_oracle_binding_errors(rendered, "test_cov_0", "f", oracles)))

    armed = _source("    vm.expectRevert();\n    c0.f(uint256(1));")
    rendered, oracles, error = materialize_revert_oracle(armed, "test_cov_0", "f")
    failures += int(error is not None or rendered != armed or len(oracles) != 1 or
                    bool(_oracle_binding_errors(rendered, "test_cov_0", "f", oracles)))

    spoofed = _source('    string memory note = "c0.f()";')
    _rendered, oracles, error = materialize_revert_oracle(spoofed, "test_cov_0", "f")
    failures += int(error is None or bool(oracles))

    identity = ("case", "path", "f", "1", "")
    seal = "a" * 64
    progress = {"rows": [{
        "identity": list(identity), "status": "refused", "reason": "edge",
        "record_identity_sha256": seal,
    }]}
    inventory = {"records": [{
        "identity": {"case": "case", "path_function": "path", "unit": "f",
                     "enc": "1", "piece": ""},
        "identity_sha256": seal,
    }]}
    selected, records = _validated_selector(progress, inventory, "edge", {identity})
    failures += int(set(selected) != {identity} or set(records) != {identity})
    for broken_progress, broken_inventory in (
            ({"rows": progress["rows"] * 2}, inventory),
            (progress, {"records": []}),
            ({"rows": [{**progress["rows"][0], "record_identity_sha256": "b" * 64}]},
             inventory)):
        try:
            _validated_selector(broken_progress, broken_inventory, "edge", {identity})
            failures += 1
        except ValueError:
            pass

    failures += int(_output_path_error(
        Path("/canonical/report.json"), Path("/canonical"), []) is None)
    failures += int(_output_path_error(
        Path("/tmp/input.json"), Path("/canonical"), [Path("/tmp/input.json")]) is None)
    failures += int(_output_path_error(
        Path("/tmp/output.json"), Path("/canonical"), [Path("/tmp/input.json")]) is not None)

    selector = {
        "basis_source_sha256": "1" * 64,
        "certification_record_sha256": "2" * 64,
        "certified_ce_sha256": "3" * 64,
        "claim_sha256": "4" * 64,
        "cov_report_sha256": "5" * 64,
    }
    prepared = {"metadata": {
        "basis_source_sha256": "1" * 64,
        "certification_record_sha256": "2" * 64,
        "certified_ce_sha256": "3" * 64,
        "report_binding": {
            "claim_sha256": "4" * 64,
            "cov_report_sha256": "5" * 64,
        },
    }}
    failures += int(backfill._revert_edge_prepared_error(prepared, selector) is not None)
    rejected = {**selector, "claim_sha256": "6" * 64}
    failures += int("differs from selector seals" not in str(
        backfill._revert_edge_prepared_error(prepared, rejected)))
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
