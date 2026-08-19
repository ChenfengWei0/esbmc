#!/usr/bin/env python3
"""Fail-closed tests for the shared revert-edge partition loader."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import rq1_put_ce_anchor_backfill as backfill  # pylint: disable=wrong-import-position


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    """Accept one closed fixture and reject broken ownership/input seals."""
    failures = 0
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        fields = ("audit", "identity_manifest", "inventory", "ledger", "selector_progress")
        paths = {}
        for field in fields:
            path = root / (field + ".json")
            path.write_text("{}\n", encoding="utf-8")
            paths[field] = path
        rows = []
        for index in range(29):
            ready = index < 17
            rows.append({
                "identity": ["case", "path", "f", str(index), ""],
                "status": "ready" if ready else "refused",
                "reason": None if ready else "blocked",
                "record_identity_sha256": "1" * 64,
                "basis_source_sha256": "2" * 64 if ready else None,
                "certification_record_sha256": "3" * 64 if ready else None,
                "certified_ce_sha256": "4" * 64 if ready else None,
                "claim_sha256": "5" * 64 if ready else None,
                "cov_report_sha256": "6" * 64 if ready else None,
            })
        paths["identity_manifest"].write_text(json.dumps({
            "schema": "rq1-frozen905-b85-identity-manifest/v1",
            "count": 29,
            "identities": [row["identity"] for row in rows],
        }), encoding="utf-8")
        document = {
            "schema": "veriput-rq1-anchor-revert-edge-partition/v1",
            "counts": {"selected": 29, "ready": 17, "refused": 12},
            "rows": rows,
        }
        for field, path in paths.items():
            document[field] = str(path)
            document[field + "_sha256"] = _sha(path)
        original_manifest = backfill.FROZEN_B85_IDENTITY_MANIFEST_SHA256
        backfill.FROZEN_B85_IDENTITY_MANIFEST_SHA256 = _sha(paths["identity_manifest"])
        artifact = root / "partition.json"
        try:
            artifact.write_text(json.dumps(document), encoding="utf-8")
            selected = backfill._load_partition_rows(artifact, "revert-edge")
            failures += int(len(selected) != 17)
            for mutation in (
                    {"counts": {"selected": 29, "ready": 16, "refused": 13}},
                    {"inventory_sha256": "0" * 64},
                    {"identity_manifest_sha256": "0" * 64},
                    {"rows": [{**rows[0], "identity": ["other", "path", "f", "0", ""]}]
                     + rows[1:]}):
                artifact.write_text(json.dumps({**document, **mutation}), encoding="utf-8")
                try:
                    backfill._load_partition_rows(artifact, "revert-edge")
                    failures += 1
                except RuntimeError:
                    pass
        finally:
            backfill.FROZEN_B85_IDENTITY_MANIFEST_SHA256 = original_manifest
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
