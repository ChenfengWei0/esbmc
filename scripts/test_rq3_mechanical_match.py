#!/usr/bin/env python3
"""Focused tests for the RQ3-to-frozen mechanical matcher."""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))

from rq3_mechanical_match import ledger_rows, load_rq3  # noqa: E402


def main() -> int:
    """Exercise exact identity and fail-closed missing-field handling."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        ledger = root / "ledger.json"
        ledger.write_text(json.dumps({
            "schema": "test",
            "obligations": [["peer182/sub", "pf", "unit", "3", ""]],
        }) + "\n",
                              encoding="utf-8")
        rq3 = root / "rq3" / "peer182" / "subjects" / "sub"
        rq3.mkdir(parents=True)
        test_file = rq3 / "test.t.sol"
        test_file.write_text("contract T { function test_cov_0() public {} }\n",
                             encoding="utf-8")
        put_json = rq3 / "put.json"
        put_json.write_text(json.dumps({
            "kind": "concrete",
            "path_function": "pf",
            "unit": "unit",
            "enc": 3,
            "piece": None,
        }) + "\n",
                               encoding="utf-8")
        result = rq3 / "result.json"
        result.write_text(json.dumps({
            "row": {
                "raw_artifacts": [{
                    "kind": "concrete",
                    "path_function": "pf",
                    "unit": "unit",
                    "enc": 3,
                    "piece": None,
                    "is_concrete": True,
                    "is_put": False,
                    "forge_status": "Success",
                    "file": str(test_file),
                    "test": "test_cov_0",
                    "put_json": str(put_json),
                    "concrete_oracles": [{"kind": "normal-exit"}],
                    "materialization": {"is_concrete": True, "is_put": False},
                }],
            },
        }) + "\n",
                              encoding="utf-8")
        assert ledger_rows(ledger) == [("peer182/sub", "pf", "unit", "3", "")]
        rows = load_rq3(root / "rq3")
        assert len(rows) == 1
        assert rows[0]["identity"] == ("peer182/sub", "pf", "unit", "3", "")
        assert rows[0]["file_exists"] is True

        # Also exercise the real No_Cer_Reg path: subject/put/<pf>/_wd/<run>.
        direct_pf = rq3 / "put" / "pf-direct"
        direct_run = direct_pf / "_wd" / "run"
        direct_test_dir = direct_pf / "subject__certify-results" / "test"
        direct_run.mkdir(parents=True)
        direct_test_dir.mkdir(parents=True)
        direct_test = direct_test_dir / "unit_concrete4_fb.t.sol"
        direct_test.write_text("contract T { function test_direct() public {} }\n",
                               encoding="utf-8")
        direct_put = direct_run / "put.json"
        direct_put.write_text(json.dumps({
            "kind": "concrete",
            "path_function": "pf-direct",
            "unit": "unit",
            "enc": 4,
            "piece": None,
        }) + "\n", encoding="utf-8")
        direct_rows = load_rq3(root / "rq3")
        direct = next(row for row in direct_rows
                      if row["path_function"] == "pf-direct")
        assert direct["result_json"].endswith("/sub/result.json")
        assert direct["file_exists"] is True
        assert direct["test"] == "test_direct"

    print("ok - RQ3 exact identity and evidence fields are retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
