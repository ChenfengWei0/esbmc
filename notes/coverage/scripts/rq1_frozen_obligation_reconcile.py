#!/usr/bin/env python3
"""Reconcile frozen RQ1 CE obligations with current physical Solidity tests.

This is a read-only audit.  It deliberately does not classify a result from a
row's ``kind`` field or from a ``test_put_`` name prefix.  For every frozen
identity it follows the canonical result.json detailed rows to the referenced
Solidity file and classifies the actual test function by its parameter list:

* PUT_BACKED: at least one current strict-valid physical test has parameters;
* CONCRETE_ONLY: no PUT exists, but a strict-valid zero-parameter test exists;
* UNRESOLVED_ROWS_NO_PHYSICAL: strict rows exist but their test file/function
  is absent or unreadable;
* UNRESOLVED_NO_STRICT_ROW: no current strict-valid row maps to the identity.

The four buckets partition the frozen ledger.  Only the first two are backed
by a physical test source.  This script does not re-run Forge; it relies on the
strict validity predicate that already requires the retained Forge evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rq1_concrete_replay_migrate import _case_dirs, _strict_valid_tests  # noqa: E402
from rq1_concrete_replay_store import _artifact_key, _physical_test_kind  # noqa: E402

DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_LEDGER = HERE.parent / "rq1_ce_obligations.frozen.json"


def _identity(case: str, row: dict) -> tuple[str, str, str, str, str]:
    """Return the frozen-ledger identity for one detailed test row."""
    return (case, *map(str, _artifact_key(row)))


def _row_view(row: dict) -> dict:
    """Keep the fields needed to audit one source-level test classification."""
    path = Path(str(row.get("file") or ""))
    test = str(row.get("test") or "")
    return {
        "file": str(path),
        "file_exists": path.is_file(),
        "test": test,
        "recorded_kind": row.get("kind"),
        "stage2_source": row.get("stage2_source"),
        "stage4_kind": row.get("stage4_kind"),
        "concrete_reason": row.get("concrete_reason") or row.get("reason"),
        "test_put_prefix": test.startswith("test_put_"),
        "physical_kind": _physical_test_kind(row),
    }


def _classify(rows: list[dict]) -> str:
    physical_kinds = {_physical_test_kind(row) for row in rows}
    if "put" in physical_kinds:
        return "PUT_BACKED"
    if "concrete" in physical_kinds:
        return "CONCRETE_ONLY"
    if rows:
        return "UNRESOLVED_ROWS_NO_PHYSICAL"
    return "UNRESOLVED_NO_STRICT_ROW"


def _concrete_only_cause(rows: list[dict]) -> str:
    """Return the recorded pipeline cause for one concrete-only obligation.

    This is intentionally an artifact/provenance classification, not a claim
    about whether the Solidity function could ever have a PUT.  It describes
    why the retained run wrote only a zero-parameter replay.
    """
    sources = {str(row.get("stage2_source") or "") for row in rows}
    sources.discard("")
    if "no-coordinate-concrete-fallback" in sources:
        return "NO_GENERALIZABLE_COORDINATE"
    if "cleared_not_certified_fallback" in sources:
        return "WITNESS_NOT_CERTIFIED"
    if "timeout_concrete_fallback" in sources:
        return "STAGE2_TIMEOUT_WITNESS"
    if "certified-region-concrete-fallback" in sources:
        return "CERTIFIED_REGION_CONCRETE_FALLBACK"
    if sources == {"certified-region"}:
        return "CERTIFIED_REGION_NOT_PARAMETERIZED"
    if sources == {"certified_region"}:
        return "LEGACY_CERTIFIED_REGION_NOT_PARAMETERIZED"
    if "source-grounded-manual-concrete-replay" in sources:
        return "SOURCE_GROUNDED_CONCRETE_REPLAY"
    if "source_grounded_callable_recovery" in sources:
        return "SOURCE_GROUNDED_CALLABLE_RECOVERY"
    if "source_constructor_revert_fallback" in sources:
        return "CONSTRUCTOR_REVERT_ONLY"

    reasons = "\n".join(
        str(row.get("concrete_reason") or row.get("reason") or "") for row in rows)
    if "NOT PARAMETERIZED" in reasons:
        return "NO_RENDERABLE_FREE_COORDINATE_LEGACY"
    return "MISSING_STAGE_PROVENANCE"


def reconcile(results_root: Path, ledger_path: Path) -> dict:
    """Join every frozen identity to strict rows and actual Solidity bodies."""
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    frozen = {tuple(map(str, row)) for row in ledger.get("obligations") or []}
    if ledger.get("schema") != "veriput-rq1-ce-obligation-ledger/v1" or not frozen:
        raise ValueError(f"not a frozen CE-obligation ledger: {ledger_path}")

    rows_by_identity: dict[tuple[str, str, str, str, str], list[dict]] = defaultdict(list)
    all_strict_rows = 0
    nonfrozen_rows = 0
    for case, subject_dir in _case_dirs(results_root):
        for row in _strict_valid_tests(subject_dir):
            all_strict_rows += 1
            identity = _identity(case, row)
            if identity in frozen:
                rows_by_identity[identity].append(row)
            else:
                nonfrozen_rows += 1

    categories: dict[str, list[dict]] = defaultdict(list)
    concrete_only_causes: dict[str, list[dict]] = defaultdict(list)
    row_counts = Counter()
    prefix_identity_count = 0
    for identity in sorted(frozen):
        rows = rows_by_identity.get(identity, [])
        category = _classify(rows)
        views = [_row_view(row) for row in rows]
        prefix_identity_count += int(any(view["test_put_prefix"] for view in views))
        entry = {"identity": list(identity), "rows": views}
        categories[category].append(entry)
        if category == "CONCRETE_ONLY":
            concrete_only_causes[_concrete_only_cause(rows)].append(entry)
        for view in views:
            row_counts["frozen_strict_rows"] += 1
            row_counts["frozen_test_put_prefix_rows"] += int(view["test_put_prefix"])
            if view["physical_kind"] == "put":
                row_counts["frozen_physical_put_rows"] += 1
            elif view["physical_kind"] == "concrete":
                row_counts["frozen_physical_concrete_rows"] += 1
            else:
                row_counts["frozen_unparsed_or_missing_rows"] += 1

    category_counts = {name: len(categories[name]) for name in (
        "PUT_BACKED",
        "CONCRETE_ONLY",
        "UNRESOLVED_ROWS_NO_PHYSICAL",
        "UNRESOLVED_NO_STRICT_ROW",
    )}
    if sum(category_counts.values()) != len(frozen):
        raise RuntimeError("frozen obligation partition is incomplete")
    return {
        "schema": "veriput-rq1-frozen-obligation-reconcile/v1",
        "method": {
            "identity": ["case", "path_function", "unit", "enc", "piece"],
            "row_filter": "rq1_concrete_replay_migrate._strict_valid_tests",
            "physical_classification": "actual Solidity test function parameter list",
            "not_used": ["row.kind alone", "test_put_ prefix alone", "aggregate counters"],
        },
        "inputs": {
            "results_root": str(results_root),
            "ledger": str(ledger_path),
            "frozen_obligation_count": len(frozen),
        },
        "counts": {
            **category_counts,
            "PHYSICAL_VALID_TOTAL": category_counts["PUT_BACKED"] +
            category_counts["CONCRETE_ONLY"],
            "UNRESOLVED_TOTAL": category_counts["UNRESOLVED_ROWS_NO_PHYSICAL"] +
            category_counts["UNRESOLVED_NO_STRICT_ROW"],
            "PARTITION_TOTAL": sum(category_counts.values()),
            "all_current_strict_rows": all_strict_rows,
            "nonfrozen_strict_rows": nonfrozen_rows,
            "frozen_test_put_prefix_identities": prefix_identity_count,
            **dict(row_counts),
        },
        "concrete_only_cause_counts": {
            cause: len(entries) for cause, entries in sorted(concrete_only_causes.items())
        },
        "identities": {name: categories[name] for name in category_counts},
        "concrete_only_causes": {
            cause: entries for cause, entries in sorted(concrete_only_causes.items())
        },
    }


def _atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                     encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--json-out", type=Path,
                        help="Write the complete per-obligation reconciliation JSON.")
    parser.add_argument("--json", action="store_true",
                        help="Print the complete reconciliation JSON to stdout.")
    args = parser.parse_args(argv)
    document = reconcile(args.results_root, args.ledger)
    if args.json_out:
        _atomic_json(args.json_out, document)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))
    else:
        print(json.dumps(document["counts"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
