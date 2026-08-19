#!/usr/bin/env python3
"""Integration smoke test for the staging-only RQ1 anchor injector."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = (Path(__file__).resolve().parents[1] /
          "notes/coverage/scripts/rq1_anchor_mechanical_injector.py")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "rq1"
        operation = root / "peer182" / "subjects" / "demo" / "put" / "f" / "project"
        test_dir = operation / "test"
        test_dir.mkdir(parents=True)
        target = test_dir / "DemoCovTest_Demo_f_put1.t.sol"
        basis = test_dir / "DemoCovTest_Demo_f_concrete1.t.sol"
        common = """pragma solidity >=0.8.0;
import {Test} from \"forge-std/Test.sol\";
contract DemoCovTest_Demo_f_%s is Test {
  uint256 c0;
  function setUp() public { c0 = 1; }
  // claim: sol:@C@Demo@F@f#1:path:1
  %s
}
"""
        target.write_text(common % ("put1", "function test_put_Demo_f_path1(uint256 x) public { c0 = x; }"),
                           encoding="utf-8")
        basis.write_text(common % ("concrete1", "function test_cov_0() public { vm.prank(address(1)); c0 = 2; }"),
                         encoding="utf-8")
        record = operation / "put.json"
        record.write_text(json.dumps({
            "kind": "put", "path_function": "sol:@C@Demo@F@f#1", "unit": "f",
            "enc": 1, "piece": None, "file": str(target),
            "test": "test_put_Demo_f_path1",
        }), encoding="utf-8")
        staging = Path(tmp) / "staging"
        manifest = staging / "manifest.json"
        subprocess.run([
            sys.executable, str(SCRIPT), "--rq1-root", str(root),
            "--staging-root", str(staging), "--manifest", str(manifest),
        ], check=True)
        plan = json.loads(manifest.read_text(encoding="utf-8"))
        assert plan["summary"] == {"already-anchored": 0, "ready": 1, "refused": 0,
                                    "total": 1}
        entry = plan["entries"][0]
        staged = staging / entry["relative_target"]
        assert staged.is_file() and entry["anchor_test"].startswith("test_ce_anchor_mech_")
        assert target.read_text(encoding="utf-8") == common % (
            "put1", "function test_put_Demo_f_path1(uint256 x) public { c0 = x; }")
        assert entry["basis_function_sha256"] != entry["anchor_function_sha256"]
        assert "function " + entry["anchor_test"] in staged.read_text(encoding="utf-8")
    print("ok - mechanical anchor injector stages one exact claim-bound rename")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
