#!/usr/bin/env python3
"""Real Forge integration check for VeriPUT's one-sided R2 prefilter."""

import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from solidity_path_put import (
    EmittedFile,
    filter_r2_specs,  # noqa: E402
    r2_candidates,
    r2_terms_from_specs,
    run_forge_r2_prefilter)

CONTRACT = """\
pragma solidity ^0.8.0;

contract TypedTerms {
    uint256 bal = 1000;

    function add(uint256 amount) external {
        if (amount > 10) {
            bal += 7;
        }
    }
}
"""

EMITTED = """\
pragma solidity >=0.8.0;

import {Test} from "forge-std/Test.sol";
import {TypedTerms} from "./contract.sol";

contract TypedTermsCovTest is Test {
  TypedTerms c0;
  function setUp() public {
    c0 = new TypedTerms();
  }
  // claim: sol:@C@TypedTerms@F@add#1:path:2
  function test_cov_0() public {
    // [asserted] path exits normally; a revert fails the test
    c0.add(11);
  }
}
"""


def op(rhs):
    return {"kind": "op", "op": "add", "lhs": {"kind": "pre"}, "rhs": rhs}


def main():
    if shutil.which("forge") is None:
        print("SKIP: forge is unavailable")
        return 77
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    forge_std = os.path.join(repo, "regression", "foundry-harness", "lib")
    if not os.path.isdir(os.path.join(forge_std, "forge-std")):
        print("SKIP: regression/foundry-harness/lib/forge-std is unavailable")
        return 77

    specs = [{
        "param":
        "batch",
        "stage":
        1,
        "kind":
        "typed",
        "depth":
        1,
        "candidate_count":
        3,
        "vars": [{
            "name":
            "bal",
            "equals": [
                {
                    "id": "holds",
                    "term": op({
                        "kind": "literal",
                        "value": "7"
                    })
                },
                {
                    "id": "refuted",
                    "term": {
                        "kind": "coord",
                        "name": "amount"
                    }
                },
                {
                    "id": "outsideBudget",
                    "term": op({
                        "kind": "literal",
                        "value": "8"
                    })
                },
            ],
            "abs": [],
            "deltas": [],
        }],
    }]

    with tempfile.TemporaryDirectory(prefix="veriput-forge-r2-") as root:
        project = os.path.join(root, "project")
        work = os.path.join(root, "work")
        os.makedirs(os.path.join(project, "src"))
        os.makedirs(os.path.join(project, "test"))
        os.makedirs(work)
        os.symlink(forge_std, os.path.join(project, "lib"), target_is_directory=True)
        with open(os.path.join(project, "foundry.toml"), "w") as stream:
            stream.write("""\
[profile.default]
src = "src"
test = "test"
libs = ["lib"]
solc_version = "0.8.34"
optimizer = false

[fuzz]
seed = "0x1"
""")
        with open(os.path.join(project, "src", "contract.sol"), "w") as stream:
            stream.write(CONTRACT)
        emitted_path = os.path.join(work, "TypedTerms.cov.t.sol")
        with open(emitted_path, "w") as stream:
            stream.write(EMITTED)
        emitted = EmittedFile(emitted_path)
        case = emitted.case_for("sol:@C@TypedTerms@F@add#1", 2)
        assert case is not None

        verdicts, evidence = run_forge_r2_prefilter(project,
                                                    work,
                                                    emitted,
                                                    case,
                                                    "TypedTerms",
                                                    "add",
                                                    2,
                                                    1,
                                                    "sol:@C@TypedTerms@F@add#1", {
                                                        "amount": (11, 100),
                                                        "state.bal": (1000, 1000)
                                                    }, {}, {}, [("amount", "uint256")],
                                                    {"bal": (0, 0, 32)}, {},
                                                    specs,
                                                    r2_terms_from_specs(specs),
                                                    ("GATE", "synthetic integration cell"), {},
                                                    60,
                                                    32,
                                                    2,
                                                    log=lambda line: print(line))

        keys = {candidate["text"]: candidate["key"] for candidate in r2_candidates(specs)}
        assert verdicts[keys["post == (pre + 7)"]] == "NOT-REFUTED"
        assert verdicts[keys["post == amount"]] == "REFUTED"
        assert verdicts[keys["post == (pre + 8)"]] == "NOT-RUN"
        survivors = {
            candidate["text"]
            for candidate in r2_candidates(filter_r2_specs(specs, verdicts))
        }
        assert survivors == {"post == (pre + 7)", "post == (pre + 8)"}
        assert evidence["requested"] == 3
        assert evidence["rendered"] == 2
        assert evidence["ran"] == 2
        assert evidence["refuted"] == 1
        assert evidence["not_refuted"] == 1
        assert evidence["not_run"] == 1
        assert not [
            name for name in os.listdir(os.path.join(project, "test")) if name.endswith(".t.sol")
        ]

        source = open(os.path.join(work, "fuzz-r2-prefilter.t.sol")).read()
        markers = set(re.findall(r"VERIPUT_CANDIDATE_[0-9a-f]{32}_\d+", source))
        assert len(markers) == 2
        payload = json.load(open(os.path.join(work, "fuzz-r2-prefilter.json")))
        assert isinstance(payload, dict) and payload

    print("solidity_path_put Forge integration: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
