#!/usr/bin/env python3
"""Focused fail-closed tests for the C27 canonical handoff."""

# pylint: disable=import-error,protected-access,wrong-import-position

from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rq1_anchor_c_provenance as provenance


class ProvenanceApplyTest(unittest.TestCase):
    """Exercise pre-write seal and path-overlap gates."""

    def test_apply_runner_rejects_unsealed_inventory(self) -> None:
        """A handoff without the validated schema cannot reach the writer."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sealed = root / "validated.json"
            sealed.write_text(json.dumps({"records": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "absent, stale, or malformed"):
                provenance.apply_ready_partition(sealed, root / "progress.json",
                                                 root / "scratch", 256, 4)

    def test_output_paths_must_be_disjoint(self) -> None:
        """Mutable outputs cannot overlap canonical or sealed paths."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "canonical"
            sealed = root / "sealed.json"
            self.assertIsNotNone(
                provenance._output_path_error([canonical / "progress.json"], [sealed],
                                              canonical))
            self.assertIsNotNone(
                provenance._output_path_error([sealed], [sealed], canonical))

    def test_apply_runner_rejects_self_consistent_swapped_bundle(self) -> None:
        """Internal hashes cannot authorize a replacement C27 handoff."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            consumer_path = root / "consumer.json"
            validation_path = root / "validation.json"
            sealed_path = root / "validated.json"
            records = []
            rows = []
            for index in range(12):
                identity = {
                    "case": f"case/{index}",
                    "path_function": f"path{index}",
                    "unit": "unit",
                    "enc": "0",
                    "piece": "",
                }
                digest = provenance.backfill._identity_digest(  # pylint: disable=protected-access
                    list(identity.values()))
                records.append({"identity": identity, "identity_sha256": digest})
                rows.append({
                    "identity": list(identity.values()),
                    "record_identity_sha256": digest,
                    "status": "validated",
                    "put_forge_ok": True,
                    "anchor_forge_ok": True,
                })
            consumer_path.write_text(json.dumps({
                "schema": "veriput-rq1-anchor-c-provenance-consumer/v1",
                "records": records,
            }), encoding="utf-8")
            validation_path.write_text(json.dumps({
                "schema": "veriput-rq1-anchor-c-provenance-progress/v1",
                "consumer_inventory": str(consumer_path),
                "consumer_inventory_sha256": hashlib.sha256(
                    consumer_path.read_bytes()).hexdigest(),
                "rows": rows,
            }), encoding="utf-8")
            stale = [dict(record) for record in records]
            stale[0] = {**stale[0], "unvalidated_redirect": "different evidence"}
            sealed_path.write_text(json.dumps({
                "schema": "veriput-rq1-anchor-c-provenance-validated-consumer/v1",
                "records": stale,
                "summary": {
                    "canonical_writes": False,
                    "records": 12,
                    "required_status": "validated",
                    "required_put_forge_ok": True,
                    "required_anchor_forge_ok": True,
                    "source_consumer_inventory": str(consumer_path),
                    "source_consumer_inventory_sha256": hashlib.sha256(
                        consumer_path.read_bytes()).hexdigest(),
                    "validation_progress": str(validation_path),
                    "validation_progress_sha256": hashlib.sha256(
                        validation_path.read_bytes()).hexdigest(),
                },
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "absent, stale, or malformed"):
                provenance.apply_ready_partition(sealed_path, root / "progress.json",
                                                 root / "scratch", 256, 4)


if __name__ == "__main__":
    unittest.main()
