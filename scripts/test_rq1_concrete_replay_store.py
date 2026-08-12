#!/usr/bin/env python3
"""Self-contained tests for canonical RQ1 concrete replay persistence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "notes" / "coverage" / "scripts"))

from rq1_concrete_replay_store import (  # noqa: E402
    audit_manifest, invalidation_applies, load_manifest, persist_concrete_replay,
    persistence_coverage, repair_manifest_independence,
)


def check(condition: bool, message: str) -> int:
    if condition:
        return 0
    print("FAIL:", message)
    return 1


def fixture(root: Path) -> tuple[Path, dict]:
    project = root / "producer"
    (project / "src").mkdir(parents=True)
    (project / "test").mkdir()
    (project / "lib" / "forge-std" / "src").mkdir(parents=True)
    (project / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\ntest = "test"\nlibs = ["lib"]\n')
    (project / "src" / "flat.sol").write_text(
        "pragma solidity >=0.8.0; contract C { function f() public {} }\n")
    (project / "lib" / "forge-std" / "src" / "Test.sol").write_text(
        "pragma solidity >=0.8.0; contract Test {}\n")
    test = project / "test" / "CReplay.t.sol"
    test.write_text(
        'pragma solidity >=0.8.0; import {Test} from "forge-std/Test.sol"; '
        'import {C} from "../src/flat.sol"; contract CReplay is Test { '
        'function test_cov_0() public { C c = new C(); c.f(); } }\n')
    put_json = project / "put.json"
    put_json.write_text(json.dumps({
        "kind": "concrete", "unit": "f", "enc": 2,
        "path_function": "sol:@C@C@F@f#1",
        "stage2_source": "certified-region-concrete-fallback",
    }))
    return root / "rq1" / "peer182" / "subjects" / "case", {
        "kind": "concrete", "valid_reference_test": True,
        "forge_status": "Success", "unit": "f", "enc": 2,
        "test": "test_cov_0", "file": str(test), "put_json": str(put_json),
    }


def main() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        subject, concrete = fixture(root)
        dry = persist_concrete_replay(subject, concrete, dry_run=True)
        bad += check(dry["action"] == "persist", "dry-run reports the pending copy")
        bad += check(not (subject / "concrete-replays").exists(),
                     "dry-run does not create canonical storage")

        entry = persist_concrete_replay(subject, concrete)
        manifest = load_manifest(subject)
        project = subject / entry["project"]
        bad += check(project.joinpath(entry["test_file"]).is_file(),
                     "the canonical project contains the replay")
        bad += check(entry["forge_command"][-2:] == ["--match-path", entry["test_file"]]
                     and entry["forge_command"][3].endswith("\\("),
                     "the replay command selects an exact executable signature and file")
        bad += check(project.joinpath(entry["test_file"]).stat().st_ino !=
                     Path(concrete["file"]).stat().st_ino,
                     "the canonical replay is a private copy, not a hard link")
        bad += check((project / "src" / "flat.sol").is_file(),
                     "the exact flat source is retained")
        bad += check((project / "lib" / "forge-std" / "src" / "Test.sol").is_file(),
                     "forge-std is vendored, not a temporary symlink")
        bad += check(not audit_manifest(subject, manifest), "manifest hashes and paths audit")
        linked_alias = root / "linked-alias.t.sol"
        os.link(project / entry["test_file"], linked_alias)
        bad += check(any("hard-linked" in error for error in audit_manifest(subject)),
                     "legacy inode sharing is detected")
        bad += check(not repair_manifest_independence(subject),
                     "legacy inode sharing is repaired before new adoption")
        bad += check((project / entry["test_file"]).stat().st_ino != linked_alias.stat().st_ino,
                     "the repair leaves the canonical replay with a private inode")
        stale_manifest = load_manifest(subject)
        stale_manifest["entries"][0]["forge_command"] = [
            "forge", "test", "--match-test", "^test_cov_0$"]
        (subject / "concrete-replays" / "manifest.json").write_text(
            json.dumps(stale_manifest))
        bad += check(not repair_manifest_independence(subject),
                     "legacy replay command repair preserves an auditable manifest")
        repaired_command = load_manifest(subject)["entries"][0]["forge_command"]
        bad += check(repaired_command[-2:] == ["--match-path", entry["test_file"]]
                     and repaired_command[3] == "^test_cov_0\\(",
                     "legacy no-test match expressions are migrated")
        bad += check(str(root) not in json.dumps(manifest),
                     "manifest retains no external or temporary absolute path")

        second = persist_concrete_replay(subject, concrete)
        bad += check(second["replay_id"] == entry["replay_id"],
                     "adoption is content-addressed and idempotent")
        bad += check(len(load_manifest(subject)["entries"]) == 1,
                     "idempotent adoption does not duplicate the manifest")

        valid_put = {
            "kind": "put", "valid_reference_test": True, "unit": "f", "enc": 2,
            "test": "test_put_f", "put_json": concrete["put_json"],
        }
        coverage = persistence_coverage([valid_put, concrete], manifest["entries"])
        bad += check(coverage["complete"],
                     "same-path canonical concrete replay covers the PUT basis")
        other_path = {**valid_put, "enc": 3}
        bad += check(persistence_coverage(
            [other_path, concrete], manifest["entries"])["put_basis_missing_count"] == 1,
            "same-unit replay from a different path cannot stand in for the PUT basis")
        missing = persistence_coverage([valid_put], [])
        bad += check(missing["put_basis_missing_count"] == 1 and not missing["complete"],
                     "a PUT without retained concrete provenance is an explicit gap")

        copied_test = project / entry["test_file"]
        copied_test.write_text(copied_test.read_text() + "// changed\n")
        bad += check(any("hash mismatch" in error for error in audit_manifest(subject)),
                     "post-adoption mutation is detected")

        ledger = root / "pollution.json"
        ledger.write_text(json.dumps({"error_then_success_evidence_audit": {
            "affected_cases": ["peer182/case"]}}))
        old = root / "old.t.sol"
        old.write_text("old")
        ledger.touch()
        bad += check(invalidation_applies(
            "peer182/case", [{"file": str(old)}], ledger),
            "pre-audit evidence remains invalidated")
        fresh = root / "fresh.t.sol"
        fresh.write_text("fresh")
        future = ledger.stat().st_mtime + 1
        os.utime(fresh, (future, future))
        bad += check(not invalidation_applies(
            "peer182/case", [{"file": str(fresh)}], ledger),
            "a fresh repaired test can leave quarantine")
    if bad == 0:
        print("all rq1 concrete replay store tests passed")
    return bad


if __name__ == "__main__":
    raise SystemExit(main())
