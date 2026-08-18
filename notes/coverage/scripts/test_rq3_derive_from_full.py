#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rq3_derive_from_full  # noqa: E402


def _write_project(root: Path, test_name: str, extra_body: str = "") -> tuple[Path, Path]:
    project = root / "Project"
    source = project / "test" / "Case.t.sol"
    (project / "src").mkdir(parents=True)
    source.parent.mkdir(parents=True)
    (project / "foundry.toml").write_text('[profile.default]\nsrc = "src"\ntest = "test"\n')
    (project / "src/flat.sol").write_text("contract C { function f(uint x) public {} }\n")
    source.write_text(
        "contract CaseTest {\n"
        f"  function {test_name}(uint x) public {{\n"
        "    c0.f(x);\n"
        f"{extra_body}"
        "  }\n"
        "}\n")
    return project, source


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _basis_entry(subject: Path, project: Path, source: Path, test: str,
                 identity: dict, replay_id: str = "basis-1") -> dict:
    log = project / "forge-replay.log"
    log.write_text("1 passed; 0 failed\n")
    return {
        "valid_reference_test": True,
        "forge_status": "Success",
        "project": str(project.relative_to(subject)),
        "test_file": str(source.relative_to(project)),
        "test": test,
        "test_sha256": _sha256(source),
        "flat_source": "src/flat.sol",
        "flat_sha256": _sha256(project / "src/flat.sol"),
        "forge_log": "forge-replay.log",
        "forge_log_sha256": _sha256(log),
        "replay_id": replay_id,
        "origin": identity,
    }


class RQ3DerivationTest(unittest.TestCase):

    def test_no_region_refinement_uses_retained_concrete_basis_only_for_refined_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = root / "Full"
            subject = full / "bugfix124" / "subjects" / "caseA"
            put_project, put_file = _write_project(subject / "put-row", "test_put_C_f_path1")
            basis_project, basis_file = _write_project(subject / "basis-row",
                                                       "test_concrete_replay_path1")
            put_json = put_project.parent / "put.json"
            put_json.write_text(json.dumps({
                "derived_by": {
                    "region_refinement_used": True
                }
            }))
            plain_project, plain_file = _write_project(subject / "plain-row",
                                                       "test_put_C_f_path2")
            plain_json = plain_project.parent / "put.json"
            plain_json.write_text(json.dumps({
                "derived_by": {
                    "region_refinement_used": False
                }
            }))
            (subject / "concrete-replays").mkdir(parents=True)
            (subject / "concrete-replays/manifest.json").write_text(
                json.dumps({
                    "entries": [_basis_entry(
                        subject, basis_project, basis_file,
                        "test_concrete_replay_path1", {
                            "path_function": "sol:@C@C@F@f#1",
                            "unit": "f",
                            "enc": 1,
                            "piece": "",
                        })]
                }))
            (subject / "result.json").write_text(
                json.dumps({
                    "valid_tests": [{
                        "case": "bugfix124/caseA",
                        "kind": "put",
                        "valid_reference_test": True,
                        "path_function": "sol:@C@C@F@f#1",
                        "unit": "f",
                        "enc": 1,
                        "piece": None,
                        "file": str(put_file),
                        "test": "test_put_C_f_path1",
                        "put_json": str(put_json),
                    }, {
                        "case": "bugfix124/caseA",
                        "kind": "put",
                        "valid_reference_test": True,
                        "path_function": "sol:@C@C@F@f#2",
                        "unit": "f",
                        "enc": 2,
                        "piece": None,
                        "file": str(plain_file),
                        "test": "test_put_C_f_path2",
                        "put_json": str(plain_json),
                    }]
                }))
            out = root / "No_region_refinement"
            manifest = rq3_derive_from_full.derive(full, out, "no-region-refinement", 5, False)
            tests = {entry["test"]: entry for entry in manifest["entries"]}
            self.assertEqual(set(tests), {"test_concrete_replay_path1", "test_put_C_f_path2"})
            self.assertEqual(tests["test_concrete_replay_path1"]["origin"]["replacement"],
                             "retained-certified-ce")
            self.assertTrue((out / tests["test_concrete_replay_path1"]["test_file"]).is_file())

    def test_no_test_oracle_refinement_removes_only_marked_r1r2_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = root / "Full"
            subject = full / "real203" / "subjects" / "caseB"
            body = ("    assertTrue(true);\n"
                    f"    {rq3_derive_from_full.BEGIN}\n"
                    "    assertEq(uint256(1), uint256(1));\n"
                    f"    {rq3_derive_from_full.END}\n"
                    "    assertFalse(false);\n")
            project, source = _write_project(subject / "put-row", "test_put_C_f_path3", body)
            put_json = project.parent / "put.json"
            put_json.write_text(
                json.dumps({
                    "stats": {
                        "assertion_oracles": [{
                            "classes": ["R1"],
                            "refinement_source": "oracle-refinement",
                        }]
                    }
                }))
            subject.mkdir(parents=True, exist_ok=True)
            (subject / "result.json").write_text(
                json.dumps({
                    "valid_tests": [{
                        "case": "real203/caseB",
                        "kind": "put",
                        "valid_reference_test": True,
                        "path_function": "sol:@C@C@F@f#3",
                        "unit": "f",
                        "enc": 3,
                        "piece": None,
                        "file": str(source),
                        "test": "test_put_C_f_path3",
                        "put_json": str(put_json),
                    }]
                }))
            out = root / "No_test_assert_refinement"
            manifest = rq3_derive_from_full.derive(full, out, "no-test-oracle-refinement", 5,
                                                   False)
            copied = out / manifest["entries"][0]["test_file"]
            text = copied.read_text()
            self.assertIn("assertTrue(true);", text)
            self.assertIn("assertFalse(false);", text)
            self.assertNotIn("assertEq(uint256(1), uint256(1));", text)
            self.assertEqual(manifest["entries"][0]["oracle_refinement_blocks_removed"], 1)

    def test_no_cer_reg_replaces_put_and_keeps_existing_concrete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = root / "Full"
            subject = full / "peer182" / "subjects" / "caseC"
            put_project, put_file = _write_project(subject / "put-row", "test_put_C_f_path4")
            put_json = put_project.parent / "put.json"
            put_json.write_text("{}")
            basis_project, basis_file = _write_project(subject / "basis-row",
                                                       "test_concrete_replay_path4")
            concrete_project, concrete_file = _write_project(
                subject / "concrete-row", "test_concrete_replay_path5")
            concrete_json = concrete_project.parent / "put.json"
            concrete_json.write_text("{}")
            identity = {
                "path_function": "sol:@C@C@F@f#4",
                "unit": "f",
                "enc": 4,
                "piece": "",
            }
            (subject / "concrete-replays").mkdir(parents=True)
            (subject / "concrete-replays/manifest.json").write_text(json.dumps({
                "entries": [_basis_entry(subject, basis_project, basis_file,
                                          "test_concrete_replay_path4", identity)]
            }))
            (subject / "result.json").write_text(json.dumps({
                "valid_tests": [{
                    "case": "peer182/caseC",
                    "kind": "put",
                    "valid_reference_test": True,
                    **identity,
                    "piece": None,
                    "file": str(put_file),
                    "test": "test_put_C_f_path4",
                    "put_json": str(put_json),
                }, {
                    "case": "peer182/caseC",
                    "kind": "concrete",
                    "valid_reference_test": True,
                    "path_function": "sol:@C@C@F@f#5",
                    "unit": "f",
                    "enc": 5,
                    "piece": None,
                    "file": str(concrete_file),
                    "test": "test_concrete_replay_path5",
                    "put_json": str(concrete_json),
                }]
            }))
            out = root / "No_Cer_Reg"
            manifest = rq3_derive_from_full.derive(full, out, "no-cer-reg", 5, False)
            tests = {entry["test"]: entry for entry in manifest["entries"]}
            self.assertEqual(set(tests), {
                "test_concrete_replay_path4", "test_concrete_replay_path5"
            })
            replacement = tests["test_concrete_replay_path4"]
            self.assertEqual(replacement["origin"]["replacement"], "retained-certified-ce")
            self.assertEqual(replacement["origin"]["basis_replay_id"], "basis-1")

    def test_no_cer_reg_refuses_missing_or_invalid_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            full = root / "Full"
            subject = full / "real203" / "subjects" / "caseD"
            project, source = _write_project(subject / "put-row", "test_put_C_f_path6")
            put_json = project.parent / "put.json"
            put_json.write_text("{}")
            row = {
                "case": "real203/caseD",
                "kind": "put",
                "valid_reference_test": True,
                "path_function": "sol:@C@C@F@f#6",
                "unit": "f",
                "enc": 6,
                "piece": None,
                "file": str(source),
                "test": "test_put_C_f_path6",
                "put_json": str(put_json),
            }
            subject.mkdir(parents=True, exist_ok=True)
            (subject / "result.json").write_text(json.dumps({"valid_tests": [row]}))
            with self.assertRaisesRegex(rq3_derive_from_full.DerivationError,
                                        "no exact authenticated concrete basis"):
                rq3_derive_from_full.derive(full, root / "out-missing", "no-cer-reg", 5,
                                            False)

            basis_project, basis_file = _write_project(subject / "basis-row",
                                                       "test_concrete_replay_path6")
            entry = _basis_entry(subject, basis_project, basis_file,
                                 "test_concrete_replay_path6", {
                                     "path_function": row["path_function"],
                                     "unit": "f",
                                     "enc": 6,
                                     "piece": "",
                                 })
            entry["forge_status"] = "Failure"
            (subject / "concrete-replays").mkdir(parents=True)
            (subject / "concrete-replays/manifest.json").write_text(
                json.dumps({"entries": [entry]}))
            with self.assertRaisesRegex(rq3_derive_from_full.DerivationError,
                                        "not Forge-green"):
                rq3_derive_from_full.derive(full, root / "out-red", "no-cer-reg", 5,
                                            False)

            entry["forge_status"] = "Success"
            wrong = json.loads(json.dumps(entry))
            wrong["origin"]["enc"] = 7
            (subject / "concrete-replays/manifest.json").write_text(
                json.dumps({"entries": [wrong]}))
            with self.assertRaisesRegex(rq3_derive_from_full.DerivationError,
                                        "no exact authenticated concrete basis"):
                rq3_derive_from_full.derive(full, root / "out-wrong", "no-cer-reg", 5,
                                            False)

            duplicate = json.loads(json.dumps(entry))
            duplicate["replay_id"] = "basis-duplicate"
            (subject / "concrete-replays/manifest.json").write_text(
                json.dumps({"entries": [entry, duplicate]}))
            with self.assertRaisesRegex(rq3_derive_from_full.DerivationError,
                                        "duplicate concrete basis"):
                rq3_derive_from_full.derive(full, root / "out-duplicate", "no-cer-reg", 5,
                                            False)


if __name__ == "__main__":
    unittest.main()
