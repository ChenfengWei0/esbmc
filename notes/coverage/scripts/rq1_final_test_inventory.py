#!/usr/bin/env python3
"""Report the disjoint RQ1 path/CE obligation inventory.

One obligation is one instrumented path and its counterexample. A valid PUT
changes that obligation from not-generalized to generalized; it does not create
another obligation. Retry rows, PUT basis replays, same-path candidates, and
manifest-entry counts are therefore deliberately absent from this report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rq1_concrete_replay_migrate import (  # noqa: E402
    DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests)
from rq1_concrete_replay_store import (  # noqa: E402
    _artifact_key, _concrete_test_key, _entry_is_currently_not_generalized, _entry_test_keys,
    _physical_test_kind, audit_manifest, load_manifest)

DEFAULT_LEDGER = HERE.parent / "rq1_ce_obligations.frozen.json"


def _obligation_id(case: str, key: tuple) -> tuple[str, str, str, str, str]:
    """Return the immutable target-local identity of one instrumented CE."""
    return (case, str(key[0]), str(key[1]), str(key[2]), str(key[3]))


def obligations(result_root: Path) -> tuple[set[tuple], set[tuple]]:
    """Return physical generalized and not-generalized CE identities."""
    generalized = set()
    not_generalized = set()
    for case, subject_dir in _case_dirs(result_root):
        rows = _strict_valid_tests(subject_dir)

        put_keys = {_artifact_key(row) for row in rows if _physical_test_kind(row) == "put"}
        generalized.update(_obligation_id(case, key) for key in put_keys)

        concrete_rows = {
            _concrete_test_key(row): row
            for row in rows if _physical_test_kind(row) == "concrete"
        }
        not_generalized_test_keys = set()
        for entry in load_manifest(subject_dir).get("entries") or []:
            if (not isinstance(entry, dict)
                    or not _entry_is_currently_not_generalized(entry, put_keys)
                    or audit_manifest(subject_dir, {"entries": [entry]})):
                continue
            not_generalized_test_keys.update(_entry_test_keys(entry))
        confirmed_keys = {
            _artifact_key(concrete_rows[key])
            for key in concrete_rows.keys() & not_generalized_test_keys
        }
        not_generalized.update(_obligation_id(case, key) for key in confirmed_keys)
    if generalized & not_generalized:
        raise RuntimeError("CE obligation classified as both generalized and not-generalized")
    return generalized, not_generalized


def freeze_ledger(path: Path, obligation_ids: set[tuple]) -> None:
    """Atomically freeze the path population; later runs may only reclassify it."""
    doc = {
        "schema": "veriput-rq1-ce-obligation-ledger/v1",
        "identity": ["target", "path_function", "unit", "enc", "piece"],
        "total_ce_obligations": len(obligation_ids),
        "obligations": [list(item) for item in sorted(obligation_ids)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as stream:
        json.dump(doc, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def validate_ledger(path: Path, obligation_ids: set[tuple]) -> None:
    """Fail loudly if the frozen CE population gains or loses an identity."""
    doc = json.loads(path.read_text())
    frozen = {tuple(item) for item in doc.get("obligations") or []}
    if frozen == obligation_ids:
        return
    added = sorted(obligation_ids - frozen)
    missing = sorted(frozen - obligation_ids)
    raise RuntimeError(f"frozen CE ledger drift: added={len(added)}, missing={len(missing)}; "
                       "use --freeze-ledger only after explicitly approving a new population")


def inventory(result_root: Path, ledger: Path | None = None) -> dict:
    """Partition unique CE identities into generalized and not-generalized."""
    generalized, not_generalized = obligations(result_root)
    if ledger is not None:
        validate_ledger(ledger, generalized | not_generalized)

    counts = {
        "generalized_ce_obligations": len(generalized),
        "not_generalized_ce_obligations": len(not_generalized),
        "total_ce_obligations": len(generalized | not_generalized),
    }
    return {
        "schema": "veriput-rq1-ce-obligation-inventory/v1",
        "scope": "canonical-current",
        "grain": "instrumented path / CE obligation",
        "artifact_counts": counts,
        "definitions": {
            "generalized_ce_obligations":
            ("Unique target/path_function/unit/enc/piece identities backed by an existing "
             "parameterized Solidity test and a strict-valid result row."),
            "not_generalized_ce_obligations":
            ("Unique CE identities backed by an existing zero-parameter Solidity test, "
             "an audited execution-result oracle, Forge execution evidence, and no current "
             "valid PUT."),
            "total_ce_obligations":
            ("generalized_ce_obligations + not_generalized_ce_obligations. Retry rows, "
             "PUT basis replays, same-path candidates, and manifest entries are excluded."),
        },
        "consistency_checks": {
            "ce_obligation_partition":
            counts["total_ce_obligations"] == (counts["generalized_ce_obligations"] +
                                               counts["not_generalized_ce_obligations"]),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--freeze-ledger", action="store_true")
    args = parser.parse_args()
    generalized, not_generalized = obligations(args.result_root)
    if args.freeze_ledger:
        freeze_ledger(args.ledger, generalized | not_generalized)
    elif not args.ledger.is_file():
        parser.error(f"missing frozen CE ledger: {args.ledger}")
    report = inventory(args.result_root, args.ledger)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(rendered)
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
