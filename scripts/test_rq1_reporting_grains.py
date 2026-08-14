#!/usr/bin/env python3
"""Regression checks for RQ1 case/artifact/replay reporting grains."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


artifact_audit = load_module(
    "rq1_artifact_audit_reporting_test",
    REPO / "notes" / "coverage" / "scripts" / "rq1_artifact_audit.py")
migrate = load_module(
    "rq1_concrete_replay_migrate_reporting_test",
    REPO / "notes" / "coverage" / "scripts" / "rq1_concrete_replay_migrate.py")
final_inventory = load_module(
    "rq1_final_test_inventory_reporting_test",
    REPO / "notes" / "coverage" / "scripts" / "rq1_final_test_inventory.py")


def write_result(root: Path, dataset: str, subject: str, rows: list[dict]) -> None:
    path = root / dataset / "subjects" / subject / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"valid_tests": rows}))


def valid_row(kind: str, *, classes: list[str] | None = None) -> dict:
    return {
        "file": f"/{kind}.t.sol",
        "test": f"test_{kind}",
        "kind": kind,
        "unit": "f",
        "enc": 1,
        "valid_reference_test": True,
        "oracle_classes": classes or [],
    }


def test_artifact_audit_separates_case_and_artifact_grains() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_result(root, "bench", "concrete_only", [valid_row("concrete")])
        write_result(root, "bench", "weak_put", [valid_row("put")])
        write_result(root, "bench", "strong_put", [valid_row("put", classes=["R2"])])
        report = artifact_audit.audit(
            argparse.Namespace(results_root=root, rewrite=False,
                               evidence_scope="canonical-current"))
        assert report["case_counts"] == {
            "inventory_cases": 3,
            "valid_cases": 3,
            "no_valid_cases": 0,
            "valid_no_put_cases": 1,
            "put_no_r1r2_cases": 1,
            "put_with_r1r2_cases": 1,
        }
        assert report["artifact_counts"]["valid_put_artifacts"] == 2
        assert report["artifact_counts"]["valid_concrete_artifacts"] == 1
        assert all(report["consistency_checks"].values())
        assert "case count" in report["definitions"]["case_counts"][
            "valid_no_put_cases"]


def test_canonical_scope_does_not_select_historical_stronger_rows() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_result(root, "bench", "subject", [valid_row("concrete")])
        write_result(root, "bench", "subject.redo.old", [valid_row("put", classes=["R2"])])
        canonical = artifact_audit.audit(argparse.Namespace(
            results_root=root, rewrite=False, evidence_scope="canonical-current"))
        historical = artifact_audit.audit(argparse.Namespace(
            results_root=root, rewrite=False, evidence_scope="historical-best"))
        assert canonical["case_counts"]["valid_no_put_cases"] == 1
        assert canonical["case_counts"]["put_with_r1r2_cases"] == 0
        assert historical["case_counts"]["put_with_r1r2_cases"] == 1
        assert canonical["evidence_scope"] == "canonical-current"
        assert historical["evidence_scope"] == "historical-best"


def migration_report(*, puts: int, generalized: int, not_generalized: int,
                     missing_put: int, missing_concrete: int) -> dict:
    persisted_put = puts - missing_put
    persisted_not_generalized = not_generalized - missing_concrete
    coverage = {
        "valid_put_count": puts,
        "valid_concrete_count": generalized + not_generalized,
        "identity_matching_concrete_count": generalized,
        "identity_unmatched_concrete_count": not_generalized,
        "persisted_generalized_replay_entry_count": generalized,
        "persisted_not_generalized_replay_entry_count": persisted_not_generalized,
        "confirmed_not_generalized_concrete_count": persisted_not_generalized,
        "generalized_ce_obligation_count": puts,
        "not_generalized_ce_obligation_count": persisted_not_generalized,
        "persisted_put_basis_count": persisted_put,
        "put_basis_missing_count": missing_put,
        "persisted_valid_concrete_count": generalized + persisted_not_generalized,
        "valid_concrete_missing_count": missing_concrete,
    }
    return {
        "case": "bench/subject",
        "strict_valid_count": puts + generalized + not_generalized,
        "status": "incomplete" if missing_put or missing_concrete else "complete",
        "coverage": coverage,
        "manifest_errors_after": [],
    }


def test_report_counts_only_final_put_and_not_generalized_concrete() -> None:
    report = migrate._summary(
        [migration_report(puts=3, generalized=2, not_generalized=4,
                          missing_put=1, missing_concrete=1)],
        total_cases=1, mode="dry-run", in_progress=False)
    counts = report["artifact_counts"]
    assert counts == {
        "generalized_ce_obligations": 3,
        "not_generalized_ce_obligations": 3,
        "total_ce_obligations": 6,
    }
    assert all(report["consistency_checks"].values())
    assert "excluded" in report["definitions"]["artifact_counts"][
        "total_ce_obligations"]


def test_final_inventory_reports_only_disjoint_outputs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = valid_row("put")
        first_file = root / "first.t.sol"
        retry_file = root / "retry.t.sol"
        first_file.write_text("contract T { function test_put(uint256 x) public {} }")
        retry_file.write_text(
            "contract T { function test_put_retry(uint256 x) public {} }")
        first["file"] = str(first_file)
        retry = {**first, "file": str(retry_file), "test": "test_put_retry"}
        write_result(root, "bench", "subject", [first, retry])
        report = final_inventory.inventory(root)
        assert report["artifact_counts"] == {
            "generalized_ce_obligations": 1,
            "not_generalized_ce_obligations": 0,
            "total_ce_obligations": 1,
        }
        assert report["consistency_checks"]["ce_obligation_partition"]


def test_stale_not_generalized_label_is_rejected() -> None:
    entry = {
        "generalization_status": "not-generalized",
        "origin": {
            "path_function": "sol:@C@C@F@f#1",
            "unit": "f",
            "enc": 1,
            "piece": None,
        },
    }
    key = (("sol:@C@C@F@f#1", "f", "1", ""))
    store = final_inventory._entry_is_currently_not_generalized
    assert store(entry, set())
    assert not store(entry, {key})

    pathless = {
        "generalization_status": "not-generalized",
        "origin": {"unit": "f", "enc": 1, "piece": None},
    }
    fallback = (("", "f", "1", ""))
    assert store(pathless, set())
    assert not store(pathless, {fallback})
    full_path_fallback = (("sol:@C@C@F@f#1", "f", "1", ""))
    assert not store(pathless, {full_path_fallback})


def test_put_basis_specialization_requires_the_original_witness() -> None:
    generated, status = migrate._specialize_put_basis(
        Path("/unused"), {"unit": "f", "enc": 1}, None)
    assert generated is None
    assert status["status"] == "witness-required"
    assert status["strategy"] == "certified-stage2-witness-only"


def test_reconcile_restores_classification_only_legacy_entries() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subject = Path(tmp) / "bench" / "subjects" / "subject"
        store = subject / "concrete-replays"
        store.mkdir(parents=True)
        manifest = {
            "schema": "veriput-rq1-concrete-replay-manifest/v1",
            "entries": [],
            "legacy_entries": [
                {
                    "replay_id": "classification-only",
                    "legacy_audit_errors": [
                        "classification-only: missing generalization classification",
                    ],
                },
                {
                    "replay_id": "real-error",
                    "legacy_audit_errors": [
                        "real-error: missing concrete execution oracle metadata",
                    ],
                },
            ],
        }
        (store / "manifest.json").write_text(json.dumps(manifest))
        restored = migrate._restore_classification_only_legacy(subject, apply=True)
        updated = json.loads((store / "manifest.json").read_text())
        assert restored["restored_entry_count"] == 1
        assert [entry["replay_id"] for entry in updated["entries"]] == [
            "classification-only"
        ]
        assert [entry["replay_id"] for entry in updated["legacy_entries"]] == [
            "real-error"
        ]
        assert "legacy_audit_errors" not in updated["entries"][0]


if __name__ == "__main__":
    test_artifact_audit_separates_case_and_artifact_grains()
    test_canonical_scope_does_not_select_historical_stronger_rows()
    test_report_counts_only_final_put_and_not_generalized_concrete()
    test_final_inventory_reports_only_disjoint_outputs()
    test_stale_not_generalized_label_is_rejected()
    test_put_basis_specialization_requires_the_original_witness()
    test_reconcile_restores_classification_only_legacy_entries()
    print("all RQ1 reporting grain tests passed")
