#!/usr/bin/env python3
"""Double-Forge validate-only runner for the RQ3 mechanical anchor staging.

The RQ3 mechanical materializer stages only the changed test source.  The
corresponding PUT certification project is therefore copied into an external
scratch directory and the staged test plus its exact ``src/flat.sol`` are
overlaid before running both target functions.  No canonical result file is
ever opened for writing.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts"))

import rq1_put_ce_anchor_backfill as backfill  # pylint: disable=wrong-import-position


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def certify_project(source: Path) -> Path | None:
    """Find the subject's immutable PUT certification Foundry project."""
    # A mechanical source lives at put/<unit>/rq3-mechanical/<digest>/test/.
    unit = source.parents[3]
    projects = sorted(unit.glob("*certify-results"))
    candidates = [path for path in projects if (path / "foundry.toml").is_file()
                  and (path / "src" / "flat.sol").is_file()]
    return candidates[0] if len(candidates) == 1 else None


def validate_row(row: dict[str, Any], scratch_root: Path, fuzz_runs: int) -> dict[str, Any]:
    """Run exact PUT and anchor gates for one staged source."""
    identity = row.get("identity")
    result: dict[str, Any] = {"identity": identity, "status": "refused"}
    if row.get("status") != "staged":
        result["reason"] = "row is not staged"
        return result
    source = Path(str(row.get("source") or ""))
    staged_source = Path(str(row.get("staged_source") or ""))
    if not source.is_file() or not staged_source.is_file():
        result["reason"] = "canonical or staged source is absent"
        return result
    if row.get("source_sha256") != sha256_file(source):
        result["reason"] = "canonical source hash changed"
        return result
    if row.get("staged_sha256") != sha256_file(staged_source):
        result["reason"] = "staged source hash does not match manifest"
        return result
    project = certify_project(source)
    if project is None:
        result["reason"] = "unique PUT certification project is absent"
        return result
    flat_source = source.parents[1] / "src" / "flat.sol"
    if not flat_source.is_file():
        result["reason"] = "mechanical source flat.sol is absent"
        return result

    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode("utf-8")).hexdigest()
    destination = scratch_root / "validation" / digest
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(project, destination, symlinks=True,
                        ignore=shutil.ignore_patterns("cache", "out", "broadcast"))
        test_destination = destination / "test" / staged_source.name
        test_destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_source, test_destination)
        shutil.copy2(flat_source, destination / "src" / "flat.sol")
        put_test = str(row.get("test") or "")
        anchor_test = str(row.get("anchor_test") or "")
        if not put_test or not anchor_test:
            result["reason"] = "PUT or anchor test name is absent"
            return result
        artifact_root = destination.parent / (destination.name + "-forge-artifacts")
        put_ok, put_tail, put_record = backfill._forge(  # pylint: disable=protected-access
            destination, test_destination, put_test, fuzz_runs, artifact_root)
        anchor_ok, anchor_tail, anchor_record = backfill._forge(  # pylint: disable=protected-access
            destination, test_destination, anchor_test, fuzz_runs, artifact_root)
        result.update({
            "status": "validated" if put_ok and anchor_ok else "forge-failed",
            "reason": None if put_ok and anchor_ok else "PUT or anchor Forge gate failed",
            "source": str(source),
            "staged_source": str(staged_source),
            "staged_source_sha256": sha256_file(staged_source),
            "staged_project": str(destination),
            "test": put_test,
            "anchor_test": anchor_test,
            "put_forge_ok": put_ok,
            "anchor_forge_ok": anchor_ok,
            "put_forge_tail": put_tail,
            "anchor_forge_tail": anchor_tail,
            "put_run": put_record,
            "anchor_run": anchor_record,
        })
        atomic_json(destination / "ce-anchor-validation.json", result)
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        result["reason"] = f"validation setup failed: {exc}"
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--fuzz-runs", type=int, default=256)
    args = parser.parse_args()
    if args.jobs < 1 or args.offset < 0 or args.limit < 0:
        parser.error("jobs must be positive; offset and limit must be non-negative")
    if backfill._paths_overlap(args.scratch_root, backfill.DEFAULT_RESULT_ROOT):  # pylint: disable=protected-access
        parser.error("scratch root overlaps canonical RQ1 results")
    document = json.loads(args.manifest.read_text(encoding="utf-8"))
    binding = Path(str(document.get("binding") or ""))
    if not binding.is_file() or sha256_file(binding) != document.get("binding_sha256"):
        parser.error("staging binding is absent or stale")
    rows = [row for row in document.get("rows") or [] if row.get("status") == "staged"]
    rows = rows[args.offset:args.offset + args.limit if args.limit else None]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        results = list(executor.map(lambda row: validate_row(row, args.scratch_root,
                                                              args.fuzz_runs), rows))
    results.sort(key=lambda row: tuple(str(value) for value in row.get("identity") or []))
    counts = {status: sum(row.get("status") == status for row in results)
              for status in sorted({row.get("status") for row in results})}
    report = {
        "schema": "veriput-rq3-mechanical-anchor-double-forge-validation/v1",
        "manifest": str(args.manifest),
        "manifest_sha256": sha256_file(args.manifest),
        "binding": str(binding),
        "binding_sha256": document["binding_sha256"],
        "canonical_writes": 0,
        "scope": {"offset": args.offset, "limit": args.limit, "jobs": args.jobs},
        "counts": counts,
        "rows": results,
    }
    atomic_json(args.progress, report)
    print(json.dumps(counts, sort_keys=True))
    return 1 if any(row.get("status") != "validated" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
