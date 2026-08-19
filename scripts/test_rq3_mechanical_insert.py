#!/usr/bin/env python3
"""Smoke test transactional RQ3 mechanical insertion."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "notes/coverage/scripts/rq3_mechanical_insert.py"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source_project = root / "rq3" / "project"
        source = source_project / "test" / "Probe.t.sol"
        source.parent.mkdir(parents=True)
        (source_project / "foundry.toml").write_text("[profile.default]\n", encoding="utf-8")
        source.write_text("contract ProbeTest { function test_cov() public {} }\n", encoding="utf-8")
        source_put = root / "rq3" / "put.json"
        source_put.write_text(json.dumps({"path_function": "sol:@C@Probe@F@f#1",
                                          "unit": "f", "enc": 1, "piece": None}),
                              encoding="utf-8")
        report = root / "match.json"
        report.write_text(json.dumps({"matched": [{
            "frozen_identity": ["suite/sub", "sol:@C@Probe@F@f#1", "f", "1", ""],
            "match_tier": "exact",
            "candidates": [{"file": str(source), "test": "test_cov",
                            "path_function": "sol:@C@Probe@F@f#1", "unit": "f", "enc": 1,
                            "piece": None, "kind": "concrete", "is_concrete": True,
                            "is_put": False, "forge_status": "Success",
                            "put_json": str(source_put), "concrete_oracles": [{}]}]
        }], "ambiguous": [{"frozen_identity": ["suite/sub", "x", "f", "1", ""]}],
        "missing": [{"frozen_identity": ["suite/sub", "y", "f", "1", ""]}]}),
                              encoding="utf-8")
        manifest = root / "manifest.json"
        command = [sys.executable, str(SCRIPT), str(report), "--rq1-root", str(root / "rq1"),
                   "--manifest", str(manifest)]
        subprocess.run(command, check=True)
        plan = json.loads(manifest.read_text())
        assert plan["summary"] == {"ambiguous_excluded": 1, "candidates": 1,
                                   "inserted": 0, "missing_excluded": 1,
                                   "planned": 1, "refused": 0}
        subprocess.run(command + ["--apply"], check=True)
        result = root / "rq1" / "suite" / "subjects" / "sub" / "result.json"
        document = json.loads(result.read_text())
        row = document["put"]["valid_artifacts"][0]
        assert row["kind"] == "concrete" and row["is_put"] is False
        assert Path(row["file"]).is_file()
        assert json.loads(manifest.read_text())["summary"]["inserted"] == 1
    print("ok - RQ3 mechanical insertion is dry-run first and provenance preserving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
