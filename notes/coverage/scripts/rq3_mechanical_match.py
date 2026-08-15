#!/usr/bin/env python3
"""Mechanically match RQ3 No_Cer_Reg artifacts to frozen RQ1 identities.

This is an evidence enumerator, not an adopter.  It never writes a canonical
subject and it never treats a match as a valid PUT.  Matches are emitted with
the exact identity key, source paths and Forge metadata so a later, stricter
backfill can authenticate each candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_VERIPUT_ROOT = Path("/home/samson/workspace/VeriPUT")
DEFAULT_RQ1_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ1" / "VeriPUT"
DEFAULT_RQ3_ROOT = DEFAULT_VERIPUT_ROOT / "Results" / "RQ3" / "VeriExploit" / "No_Cer_Reg"
DEFAULT_LEDGER = Path(__file__).resolve().parent.parent / "rq1_ce_obligations.frozen.json"


def read_json(path: Path) -> Any:
    """Read JSON, returning ``None`` for missing or malformed files."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def sha256(path: Path | None) -> str | None:
    """Return a file digest without following missing evidence references."""
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def scalar(value: Any) -> str:
    """Normalize identity scalar values without inventing missing values."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def identity(case: Any, path_function: Any, unit: Any, enc: Any,
             piece: Any = None) -> tuple[str, str, str, str, str]:
    """Build the five-field frozen identity used by RQ1."""
    return (scalar(case), scalar(path_function), scalar(unit), scalar(enc), scalar(piece))


def ledger_rows(path: Path) -> list[tuple[str, str, str, str, str]]:
    """Load and validate the immutable frozen identity population."""
    document = read_json(path)
    rows = document.get("obligations") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"ledger has no obligations list: {path}")
    result = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 5:
            raise ValueError(f"malformed ledger identity: {row!r}")
        result.append(identity(*row))
    if len(set(result)) != len(result):
        raise ValueError("frozen ledger contains duplicate identities")
    return result


def result_case(result_path: Path) -> tuple[str, str, str]:
    """Return benchmark, subject and RQ1-compatible case key."""
    subject_dir = result_path.parent
    subjects_dir = subject_dir.parent
    benchmark_dir = subjects_dir.parent
    benchmark = benchmark_dir.name
    subject = subject_dir.name
    return benchmark, subject, f"{benchmark}/{subject}"


def candidate_values(result: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield every RQ3 artifact row, de-duplicated by serialized content."""
    seen: set[str] = set()
    row = result.get("row") if isinstance(result.get("row"), dict) else {}
    put = result.get("put") if isinstance(result.get("put"), dict) else {}
    values: list[Any] = []
    for container in (row, put):
        for key in ("raw_artifacts", "valid_artifacts", "artifacts"):
            entries = container.get(key)
            if isinstance(entries, list):
                values.extend(entries)
    for item in values:
        if not isinstance(item, dict):
            continue
        marker = json.dumps(item, sort_keys=True, separators=(",", ":"), default=str)
        if marker in seen:
            continue
        seen.add(marker)
        yield item


def artifact_put_path(item: dict[str, Any], subject_dir: Path) -> Path | None:
    """Resolve a recorded put.json path only when it stays under RQ3 subject."""
    raw = item.get("put_json")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = subject_dir / path
    try:
        path = path.resolve()
        path.relative_to(subject_dir.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def merge_artifact(item: dict[str, Any], put_doc: dict[str, Any]) -> dict[str, Any]:
    """Prefer the result row while retaining put.json identity fields."""
    merged = dict(put_doc)
    merged.update({key: value for key, value in item.items() if value is not None})
    return merged


def match_tier(candidate: tuple[str, str, str, str, str],
               by_exact: dict[tuple[str, str, str, str, str], list[dict[str, Any]]],
               by_path_function: dict[tuple[str, str], list[dict[str, Any]]],
               by_unit: dict[tuple[str, str], list[dict[str, Any]]]) -> tuple[str, list[dict[str, Any]]]:
    """Match using the RQ3 closure relation, while preserving ambiguity.

    The ablation claim permits an RQ3 concrete replay to close over an RQ1
    concrete identity when its case and path function agree, even if the
    generated ``enc`` or ``piece`` label differs.  That relation is emitted
    explicitly; it is never silently rewritten as an exact identity.  The
    unit fallback is intentionally last and only unique rows are promoted to
    ``matched``.
    """
    exact = by_exact.get(candidate, [])
    if len(exact) == 1:
        return "exact", exact
    if exact:
        return "ambiguous-exact", exact

    function = by_path_function.get((candidate[0], candidate[1]), [])
    if len(function) == 1:
        return "same-path-function-cross-enc-piece", function
    if len(function) > 1:
        return "ambiguous-same-path-function", function

    unit = by_unit.get((candidate[0], candidate[2]), [])
    if len(unit) == 1:
        return "same-unit", unit
    if unit:
        return "ambiguous-same-unit", unit
    return "missing", []


def load_rq3(rq3_root: Path) -> list[dict[str, Any]]:
    """Collect RQ3 artifacts and preserve their result/put provenance."""
    records: list[dict[str, Any]] = []
    known_put_paths: set[Path] = set()
    # Accept both the published No_Cer_Reg layout and nested isolated rerun
    # shards (``<root>/shard-N/<benchmark>/subjects/<subject>/...``).
    for result_path in sorted(rq3_root.rglob("subjects/*/result.json")):
        result = read_json(result_path)
        if not isinstance(result, dict):
            continue
        benchmark, subject, case = result_case(result_path)
        subject_dir = result_path.parent
        for ordinal, item in enumerate(candidate_values(result), 1):
            put_path = artifact_put_path(item, subject_dir)
            if put_path is not None:
                put_path = put_path.resolve()
                if put_path in known_put_paths:
                    continue
                known_put_paths.add(put_path)
            put_doc = read_json(put_path) if put_path else {}
            put_doc = put_doc if isinstance(put_doc, dict) else {}
            merged = merge_artifact(item, put_doc)
            path_function = merged.get("path_function") or put_doc.get("path_function")
            unit = merged.get("unit") or put_doc.get("unit")
            enc = merged.get("enc") if merged.get("enc") is not None else put_doc.get("enc")
            piece = merged.get("piece") if merged.get("piece") is not None else put_doc.get("piece")
            if path_function is None or unit is None or enc is None:
                continue
            records.append({
                "case": case,
                "benchmark": benchmark,
                "subject": subject,
                "result_json": str(result_path),
                "artifact_ordinal": ordinal,
                "identity": identity(case, path_function, unit, enc, piece),
                "path_function": scalar(path_function),
                "unit": scalar(unit),
                "enc": scalar(enc),
                "piece": scalar(piece),
                "kind": merged.get("kind"),
                "is_concrete": bool(merged.get("is_concrete") or merged.get("kind") == "concrete"),
                "is_put": bool(merged.get("is_put") or merged.get("kind") == "put"),
                "forge_status": merged.get("forge_status"),
                "valid_reference_test": merged.get("valid_reference_test"),
                "stage2_source": merged.get("stage2_source"),
                "test": merged.get("test"),
                "file": merged.get("file"),
                "file_exists": bool(isinstance(merged.get("file"), str)
                                    and Path(str(merged["file"])).is_file()),
                "put_json": str(put_path) if put_path else None,
                "put_json_sha256": sha256(put_path),
                "flat_source_sha256": merged.get("flat_source_sha256"),
                "concrete_oracles": merged.get("concrete_oracles") or [],
                "materialization": merged.get("materialization")
                if isinstance(merged.get("materialization"), dict) else {},
                "source_record": merged,
            })

    # No_Cer_Reg keeps additional concrete-only attempts in put.json files
    # that are intentionally absent from the compact result.json artifact
    # summary.  They are still valid mechanical closure inputs, so enumerate
    # them directly rather than silently losing half of the RQ3 population.
    for put_path in sorted(rq3_root.rglob("subjects/*/**/put.json")):
        put_path = put_path.resolve()
        if put_path in known_put_paths:
            continue
        document = read_json(put_path)
        if not isinstance(document, dict):
            continue
        try:
            parts = put_path.parts
            subject_index = parts.index("subjects")
            benchmark = parts[subject_index - 1]
            subject = parts[subject_index + 1]
        except (ValueError, IndexError):
            continue
        path_function = document.get("path_function")
        unit = document.get("unit")
        enc = document.get("enc")
        if path_function is None or unit is None or enc is None:
            continue
        known_put_paths.add(put_path)
        piece = document.get("piece")
        # Recover the emitted concrete test when the compact put.json omitted
        # it.  Ambiguous files remain unbound and therefore non-strict.
        # put.json layout is subject/put/<path-function>/_wd/<run>/put.json.
        # Therefore parents[2] is the path-function directory; parents[3] is
        # the shared ``put`` directory and cannot contain the emitted test.
        pf_dir = put_path.parents[2] if len(put_path.parents) > 2 else put_path.parent
        pattern = f"*{unit}*concrete{enc}*.t.sol"
        test_files = sorted(pf_dir.glob(f"*/test/{pattern}"))
        file_path = test_files[0] if len(test_files) == 1 else None
        test_name = None
        if file_path is not None:
            try:
                import re
                source = file_path.read_text(encoding="utf-8")
                names = re.findall(r"\bfunction\s+(test[A-Za-z0-9_$]*)\s*\(", source)
                if len(names) == 1:
                    test_name = names[0]
            except (OSError, UnicodeDecodeError):
                file_path = None
        merged = dict(document)
        merged.setdefault("file", str(file_path) if file_path else None)
        merged.setdefault("test", test_name)
        merged.setdefault("put_json", str(put_path))
        records.append({
            "case": f"{benchmark}/{subject}",
            "benchmark": benchmark,
            "subject": subject,
            # subject/put/<path-function>/_wd/<run>/put.json -> parents[4]
            # is the subject directory.
            "result_json": str(put_path.parents[4] / "result.json"),
            "artifact_ordinal": None,
            "identity": identity(f"{benchmark}/{subject}", path_function, unit, enc, piece),
            "path_function": scalar(path_function),
            "unit": scalar(unit),
            "enc": scalar(enc),
            "piece": scalar(piece),
            "kind": merged.get("kind"),
            "is_concrete": bool(merged.get("is_concrete") or merged.get("kind") == "concrete"),
            "is_put": bool(merged.get("is_put") or merged.get("kind") == "put"),
            "forge_status": merged.get("forge_status"),
            "valid_reference_test": merged.get("valid_reference_test"),
            "stage2_source": merged.get("stage2_source"),
            "test": merged.get("test"),
            "file": merged.get("file"),
            "file_exists": bool(merged.get("file") and Path(str(merged["file"])).is_file()),
            "put_json": str(put_path),
            "put_json_sha256": sha256(put_path),
            "flat_source_sha256": merged.get("flat_source_sha256"),
            "concrete_oracles": merged.get("concrete_oracles") or [],
            "materialization": merged.get("materialization")
            if isinstance(merged.get("materialization"), dict) else {},
            "source_record": merged,
        })

    # The RQ3 concrete-replay store is authoritative for emitted replay
    # tests.  These entries can exist even when the compact result row has no
    # ``put.json`` reference, so enumerate the manifest explicitly rather than
    # losing the replay at the result/PUT boundary.
    existing_manifest_keys = {
        (row.get("identity"), row.get("file"), row.get("test"))
        for row in records
    }
    for manifest_path in sorted(rq3_root.rglob("subjects/*/concrete-replays/manifest.json")):
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        subject_dir = manifest_path.parent.parent
        parts = manifest_path.parts
        try:
            subject_index = parts.index("subjects")
            benchmark = parts[subject_index - 1]
            subject = parts[subject_index + 1]
        except (ValueError, IndexError):
            continue
        case = f"{benchmark}/{subject}"
        for ordinal, entry in enumerate(manifest.get("entries", []), 1):
            if not isinstance(entry, dict):
                continue
            origin = entry.get("origin")
            if not isinstance(origin, dict):
                continue
            path_function = origin.get("path_function")
            unit = origin.get("unit")
            enc = origin.get("enc")
            if path_function is None or unit is None or enc is None:
                continue
            piece = origin.get("piece")
            test_file = entry.get("test_file")
            project = entry.get("project")
            file_path = None
            if isinstance(test_file, str) and isinstance(project, str):
                candidate = (subject_dir / project / test_file).resolve()
                try:
                    candidate.relative_to(subject_dir.resolve())
                except (OSError, ValueError):
                    candidate = None
                if candidate is not None and candidate.is_file():
                    file_path = candidate
            put_path = None
            put_ref = origin.get("put_json")
            if isinstance(put_ref, dict):
                put_ref = put_ref.get("path")
            if isinstance(put_ref, str) and put_ref:
                candidate = (subject_dir / put_ref).resolve()
                try:
                    candidate.relative_to(subject_dir.resolve())
                except (OSError, ValueError):
                    candidate = None
                if candidate is not None and candidate.is_file():
                    put_path = candidate
            identity_key = identity(case, path_function, unit, enc, piece)
            # A manifest row is retained even if its put.json was already
            # enumerated: the manifest is the source of the concrete test and
            # its Forge oracle.  Deduplicate only the exact same test path.
            manifest_key = (identity_key, str(file_path) if file_path else None,
                            entry.get("test"))
            if manifest_key in existing_manifest_keys:
                continue
            existing_manifest_keys.add(manifest_key)
            records.append({
                "case": case,
                "benchmark": benchmark,
                "subject": subject,
                "result_json": str(manifest_path),
                "artifact_ordinal": ordinal,
                "identity": identity_key,
                "path_function": scalar(path_function),
                "unit": scalar(unit),
                "enc": scalar(enc),
                "piece": scalar(piece),
                "kind": "concrete",
                "is_concrete": True,
                "is_put": False,
                "forge_status": entry.get("forge_status"),
                "valid_reference_test": entry.get("valid_reference_test"),
                "stage2_source": origin.get("stage2_source"),
                "test": entry.get("test"),
                "file": str(file_path) if file_path else None,
                "file_exists": file_path is not None,
                "put_json": str(put_path) if put_path else None,
                "put_json_sha256": sha256(put_path),
                "flat_source_sha256": entry.get("flat_sha256"),
                "concrete_oracles": entry.get("concrete_oracles") or [],
                "materialization": {
                    "is_concrete": True,
                    "is_put": False,
                },
                "source_record": entry,
            })
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq1-root", type=Path, default=DEFAULT_RQ1_ROOT)
    parser.add_argument("--rq3-root", type=Path, default=DEFAULT_RQ3_ROOT)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--state", choices=("all", "generalized", "unresolved", "no-put"),
                        default="all",
                        help="restrict matches using the current RQ1 obligation state")
    parser.add_argument("--only-no-put", action="store_true",
                        help="compatibility alias for --state no-put")
    args = parser.parse_args()

    frozen = ledger_rows(args.ledger)
    frozen_set = set(frozen)
    target = frozen_set
    state = "no-put" if args.only_no_put else args.state
    if state != "all":
        # Import lazily: this keeps the matcher usable against a standalone RQ3
        # tree, while the normal mode remains independent of the large RQ1 scan.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from rq1_final_test_inventory import obligations  # pylint: disable=import-outside-toplevel
        generalized, unresolved, not_generalized = obligations(args.rq1_root)
        selected = {
            "generalized": generalized,
            "unresolved": unresolved,
            "no-put": not_generalized,
        }[state]
        target = frozen_set & selected

    candidates = load_rq3(args.rq3_root)
    by_exact: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_path_function: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_unit: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        key = tuple(item["identity"])
        by_exact[key].append(item)
        by_path_function[(key[0], key[1])].append(item)
        by_unit[(key[0], key[2])].append(item)

    matched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    missing: list[list[str]] = []
    for target_identity in sorted(target):
        tier, rows = match_tier(target_identity, by_exact, by_path_function,
                                by_unit)
        if tier == "missing":
            missing.append({"frozen_identity": list(target_identity),
                            "match_tier": "missing-rq3"})
            continue
        payload = {"frozen_identity": list(target_identity), "match_tier": tier,
                   "candidates": rows}
        if tier.startswith("ambiguous"):
            ambiguous.append(payload)
        else:
            matched.append(payload)

    strict_candidates = []
    closure_candidates = []
    for row in matched:
        for item in row["candidates"]:
            # This is deliberately mechanical and conservative.  RQ3 is a
            # concrete-only run, so a candidate still needs a real test file,
            # an execution oracle and an exact frozen identity.  No canonical
            # or Stage-2 certification claim is inferred here.
            materialization = item.get("materialization") or {}
            if (item.get("put_json") and item.get("kind") == "concrete"
                    and item.get("is_concrete") and not item.get("is_put")
                    and item.get("file_exists") and item.get("test")):
                closure_candidates.append({
                    "frozen_identity": row["frozen_identity"],
                    "match_tier": row["match_tier"],
                    "candidate": item,
                })
            if (row["match_tier"] == "exact" and item.get("put_json")
                    and item.get("kind") == "concrete"
                    and item.get("is_concrete") and not item.get("is_put")
                    and item.get("file_exists") and item.get("test")
                    and item.get("forge_status") == "Success"
                    and item.get("concrete_oracles")
                    and materialization.get("is_concrete", True)
                    and not materialization.get("is_put", False)):
                strict_candidates.append({
                    "frozen_identity": row["frozen_identity"],
                    "candidate": item,
                })
    report = {
        "schema": "veriput-rq3-mechanical-match/v1",
        "rq1_root": str(args.rq1_root.resolve()),
        "rq3_root": str(args.rq3_root.resolve()),
        "ledger": str(args.ledger.resolve()),
        "state": state,
        "frozen_count": len(frozen_set),
        "target_count": len(target),
        "rq3_artifact_count": len(candidates),
        "counts": {
            "matched": len(matched),
            "ambiguous": len(ambiguous),
            "missing": len(missing),
            "mechanically_strict_candidates": len(strict_candidates),
            "mechanical_closure_candidates": len(closure_candidates),
        },
        # Keep the RQ1 target population total even when RQ3 has not yet
        # emitted an artifact for an identity.  Consumers must account for
        # all three partitions before treating the closure as complete.
        "closure": {
            "target_count": len(target),
            "partition_count": len(matched) + len(ambiguous) + len(missing),
            "partition_complete": (len(matched) + len(ambiguous)
                                    + len(missing) == len(target)),
            "rq3_covered": len(matched) + len(ambiguous),
            "requires_rq3_generation": len(missing),
            "ambiguity_is_explicit": True,
            "missing_is_explicit": True,
        },
        "matched": matched,
        "ambiguous": ambiguous,
        "missing": missing,
        # Stable queue name for the next RQ3 generation pass.  Keep the
        # original ``missing`` field for compatibility with v1 consumers.
        "rq3_generation_queue": missing,
        "mechanical_candidates": strict_candidates,
        "closure_candidates": closure_candidates,
        "strict_candidate_note": (
            "Mechanical candidates are not canonical-ready. They still require "
            "source/cert/report/hash membership and the authoritative strength audit."),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"schema": report["schema"], **report["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
