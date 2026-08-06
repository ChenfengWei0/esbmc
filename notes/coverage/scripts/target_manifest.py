#!/usr/bin/env python3
"""Freeze VeriPUT Dataset targets into a single auditable manifest.

This script does not invoke solc, Forge, fuzzers, or ESBMC.  It only reads the
Dataset/Results target metadata that is already frozen on disk and emits a
`veriput-eval/target/v1` JSON document for later unit enumeration and proof
scheduling.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_VERIPUT_ROOT = Path("/home/samson/workspace/VeriPUT")
BENCHMARKS = ("peer182", "bugfix124", "stress243")


class TargetManifestError(ValueError):
    """The target manifest cannot be built from the current files."""


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _split_semis(text: str) -> list[str]:
    return [part for part in (text or "").split(";") if part]


def _repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise TargetManifestError(f"missing CSV: {path}")
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def _row_error(targets, benchmark, subject_id, reason, **extra):
    row = {
        "schema": "veriput-eval-target/v1",
        "benchmark": benchmark,
        "subject_id": subject_id,
        "status": "error",
        "reason": reason,
    }
    row.update(extra)
    targets.append(row)


def bugfix_targets(root: Path) -> list[dict]:
    base = root / "Datasets" / "Patch-Bug-Bench"
    rows = _read_csv(base / "summary.csv")
    targets = []
    for row in rows:
        subject_id = row["id"]
        contract = row.get("target_contract") or ""
        bug = root / row.get("bug", "")
        fix = root / row.get("fix", "")
        missing = [str(p) for p in (bug, fix) if not p.exists()]
        if not contract or missing:
            _row_error(
                targets,
                "bugfix124",
                subject_id,
                "missing target contract or source",
                contract=contract,
                missing=missing)
            continue
        targets.append({
            "schema": "veriput-eval-target/v1",
            "benchmark": "bugfix124",
            "subject_id": subject_id,
            "status": "ok",
            "contract": contract,
            "source_kind": "bugfix-pair",
            "sources": [
                {"variant": "bug", "path": _rel(bug, root)},
                {"variant": "fix", "path": _rel(fix, root)},
            ],
            "units_hint": _split_semis(row.get("changed_functions", "")),
            "metadata": {
                "case_class": row.get("class"),
                "tier": row.get("tier"),
                "source_dataset": row.get("source_dataset"),
                "modification_kind": row.get("modification_kind"),
                "bug_solc": row.get("bug_solc"),
                "fix_solc": row.get("fix_solc"),
            },
        })
    return targets


def stress_targets(root: Path, scope: str) -> list[dict]:
    base = root / "Datasets" / "Stress-Projects"
    rows = _read_csv(base / "TARGETS.csv")
    targets = []
    for row in rows:
        include = row.get("include") == "yes"
        stateful = row.get("state_class") == "STATEFUL"
        selected = include if scope == "include" else include and stateful
        if not selected:
            continue
        repo = row["repo"]
        contract = row["contract"]
        source = base / _repo_slug(repo) / row["path"]
        subject_id = f"{_repo_slug(repo)}__{contract}"
        if not source.exists():
            _row_error(
                targets,
                "stress243",
                subject_id,
                "target source does not exist",
                contract=contract,
                source=_rel(source, root))
            continue
        targets.append({
            "schema": "veriput-eval-target/v1",
            "benchmark": "stress243",
            "subject_id": subject_id,
            "status": "ok",
            "contract": contract,
            "source_kind": "stress-source",
            "sources": [{"variant": "source", "path": _rel(source, root)}],
            "units_hint": [],
            "metadata": {
                "repo": repo,
                "source_path": row["path"],
                "state_class": row.get("state_class"),
                "named_entry_points": row.get("named_entry_points"),
                "public_state_vars": row.get("public_state_vars"),
                "writing_entry_points": row.get("writing_entry_points"),
                "test_frameworks": row.get("test_frameworks"),
                "referenced_by_dev_tests": row.get("referenced_by_dev_tests"),
            },
        })
    return targets


def peer_targets(root: Path) -> list[dict]:
    subjects = root / "Results" / "Peer182" / "subjects"
    if not subjects.is_dir():
        raise TargetManifestError(f"missing prepared peer subjects: {subjects}")
    targets = []
    for meta_path in sorted(subjects.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError as exc:
            _row_error(
                targets,
                "peer182",
                meta_path.parent.name,
                f"invalid meta.json: {exc}")
            continue
        subject_id = meta.get("subject_id") or meta_path.parent.name
        if meta.get("status") != "ok":
            continue
        source_file = meta.get("source_file") or ""
        if not meta.get("source_080") or "contracts_080/" not in source_file:
            targets.append({
                "schema": "veriput-eval-target/v1",
                "benchmark": "peer182",
                "subject_id": subject_id,
                "status": "skipped",
                "reason": "peer subject is not from contracts_080",
                "contract": meta.get("contract") or "",
                "source_kind": "prepared-non-080-source",
                "sources": [],
                "units_hint": [],
                "metadata": {
                    "peer_tool": meta.get("peer_tool"),
                    "peer_arm": meta.get("peer_arm"),
                    "source_file": source_file,
                    "source_080": bool(meta.get("source_080")),
                },
            })
            continue
        flat = meta_path.parent / "flat.sol"
        contract = meta.get("contract") or ""
        if not contract or not flat.exists():
            _row_error(
                targets,
                "peer182",
                subject_id,
                "missing target contract or prepared source",
                contract=contract,
                source=_rel(flat, root))
            continue
        targets.append({
            "schema": "veriput-eval-target/v1",
            "benchmark": "peer182",
            "subject_id": subject_id,
            "status": "ok",
            "contract": contract,
            "source_kind": "prepared-080-source",
            "sources": [{"variant": "source", "path": _rel(flat, root)}],
            "units_hint": [],
            "metadata": {
                "peer_tool": meta.get("peer_tool"),
                "peer_arm": meta.get("peer_arm"),
                "source_file": source_file,
                "target_rule": meta.get("target_rule"),
                "target_alternatives": meta.get("target_alternatives") or [],
                "source_080": bool(meta.get("source_080")),
                "has_assert": meta.get("has_assert"),
            },
        })
    return targets


def build_manifest(root: Path, benchmarks: list[str], stress_scope: str) -> dict:
    normalized = []
    for name in benchmarks:
        if name == "stress203":
            normalized.append("stress243")
        else:
            normalized.append(name)
    unknown = sorted(set(normalized) - set(BENCHMARKS))
    if unknown:
        raise TargetManifestError(
            "unknown benchmark(s): " + ", ".join(unknown))

    targets = []
    if "peer182" in normalized:
        targets.extend(peer_targets(root))
    if "bugfix124" in normalized:
        targets.extend(bugfix_targets(root))
    if "stress243" in normalized:
        targets.extend(stress_targets(root, stress_scope))

    counts = Counter(
        (row["benchmark"], row["status"]) for row in targets)
    summary = {
        "targets": len(targets),
        "ok": sum(1 for row in targets if row["status"] == "ok"),
        "skipped": sum(1 for row in targets
                       if row["status"] == "skipped"),
        "error": sum(1 for row in targets if row["status"] == "error"),
        "by_benchmark": {
            bench: {
                "ok": counts[(bench, "ok")],
                "skipped": counts[(bench, "skipped")],
                "error": counts[(bench, "error")],
            }
            for bench in BENCHMARKS
            if any(row["benchmark"] == bench for row in targets)
        },
    }
    return {
        "schema": "veriput-eval/target/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "veriput_root": str(root.resolve()),
        "benchmarks": normalized,
        "stress_scope": stress_scope,
        "summary": summary,
        "targets": targets,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--veriput-root", default=str(DEFAULT_VERIPUT_ROOT),
                    help="root containing Datasets/ and Results/")
    ap.add_argument("--benchmark", action="append", choices=BENCHMARKS
                    + ("stress203",), default=[],
                    help="benchmark to include. Repeatable. Default: all")
    ap.add_argument("--stress-scope", choices=("include", "stateful"),
                    default="include",
                    help="Stress target policy: include=yes rows, or only "
                         "include=yes STATEFUL rows")
    ap.add_argument("--out", default="",
                    help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    benchmarks = args.benchmark or list(BENCHMARKS)
    try:
        doc = build_manifest(
            Path(args.veriput_root),
            benchmarks,
            args.stress_scope)
    except TargetManifestError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        s = doc["summary"]
        print(f"wrote {out}")
        print(f"targets={s['targets']} ok={s['ok']} error={s['error']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
