#!/usr/bin/env python3

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "notes" / "coverage" / "scripts"))

from rq1_veriput_triage import latest_result_paths


def test_canonical_result_wins_over_historical_directories():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subjects = root / "peer182" / "subjects"
        for name, marker in (
                ("case", "canonical"),
                ("case.redo.123.456", "redo"),
                ("case.superseded.789", "superseded")):
            path = subjects / name
            path.mkdir(parents=True)
            (path / "result.json").write_text(json.dumps({"marker": marker}))

        rows = latest_result_paths(root, ["peer182"])
        assert len(rows) == 1
        dataset, subject_id, result_path = rows[0]
        assert dataset == "peer182"
        assert subject_id == "case"
        assert json.loads(result_path.read_text())["marker"] == "canonical"


if __name__ == "__main__":
    test_canonical_result_wins_over_historical_directories()
    print("ok")
