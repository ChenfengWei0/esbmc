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
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_VERIPUT_ROOT = Path(os.environ.get(
    "VERIPUT_ROOT", "/home/samson/workspace/VeriPUT"))
BENCHMARKS = ("peer182", "bugfix124", "stress243")
PEER_REQUIRED_SOURCE_SEGMENT = "contracts_080/"


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


def _is_peer_contract080(meta: dict) -> bool:
    source_file = meta.get("source_file") or ""
    return bool(meta.get("source_080")) and \
        PEER_REQUIRED_SOURCE_SEGMENT in source_file


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise TargetManifestError(f"missing CSV: {path}")
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def _stress_prepared_subjects(root: Path) -> set[str]:
    return set(_stress_prepared_meta(root))


def _stress_prepared_meta(root: Path) -> dict[str, dict]:
    subjects = root / "Results" / "Stress243" / "subjects"
    if not subjects.is_dir():
        raise TargetManifestError(f"missing prepared stress subjects: {subjects}")
    usable = {}
    for meta_path in sorted(subjects.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("status") == "ok" and (meta_path.parent / "flat.sol").exists():
            subject_id = meta.get("subject_id") or meta_path.parent.name
            usable[subject_id] = meta
    return usable


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


def stress_targets(root: Path, scope: str, *, prepared_ok_only=False) -> list[dict]:
    base = root / "Datasets" / "Stress-Projects"
    rows = _read_csv(base / "TARGETS.csv")
    prepared_meta = _stress_prepared_meta(root) if prepared_ok_only else None
    prepared = set(prepared_meta) if prepared_meta is not None else None
    targets = []
    seen_subjects = set()

    def append_target(row: dict, subject_id: str, repo: str, contract: str,
                      source: Path, metadata: dict):
        if subject_id in seen_subjects:
            return
        seen_subjects.add(subject_id)
        targets.append({
            "schema": "veriput-eval-target/v1",
            "benchmark": "stress243",
            "subject_id": subject_id,
            "status": "ok",
            "contract": contract,
            "source_kind": "stress-source",
            "sources": [{"variant": "source", "path": _rel(source, root)}],
            "units_hint": [],
            "metadata": metadata,
        })

    for row in rows:
        include = row.get("include") == "yes"
        stateful = row.get("state_class") == "STATEFUL"
        selected = include if scope == "include" else include and stateful
        repo = row["repo"]
        contract = row["contract"]
        subject_id = f"{_repo_slug(repo)}__{contract}"
        if prepared is not None:
            selected = include and subject_id in prepared
        if not selected:
            continue
        source = base / _repo_slug(repo) / row["path"]
        if not source.exists():
            _row_error(
                targets,
                "stress243",
                subject_id,
                "target source does not exist",
                contract=contract,
                source=_rel(source, root))
            continue
        append_target(
            row,
            subject_id,
            repo,
            contract,
            source,
            {
                "repo": repo,
                "source_path": row["path"],
                "state_class": row.get("state_class"),
                "named_entry_points": row.get("named_entry_points"),
                "public_state_vars": row.get("public_state_vars"),
                "writing_entry_points": row.get("writing_entry_points"),
                "test_frameworks": row.get("test_frameworks"),
                "referenced_by_dev_tests": row.get("referenced_by_dev_tests"),
            })
    if prepared_ok_only:
        for subject_id in sorted(prepared - seen_subjects):
            meta = prepared_meta[subject_id]
            repo = meta.get("repo") or ""
            contract = meta.get("contract") or ""
            path = meta.get("path") or ""
            if not repo or not contract or not path:
                _row_error(
                    targets,
                    "stress243",
                    subject_id,
                    "prepared subject is missing repo/contract/path metadata",
                    contract=contract,
                    metadata=meta)
                continue
            source = base / _repo_slug(repo) / path
            if not source.exists():
                _row_error(
                    targets,
                    "stress243",
                    subject_id,
                    "prepared target source does not exist",
                    contract=contract,
                    source=_rel(source, root))
                continue
            append_target(
                {},
                subject_id,
                repo,
                contract,
                source,
                {
                    "repo": repo,
                    "source_path": path,
                    "state_class": meta.get("state_class"),
                    "named_entry_points": meta.get("named_entry_points"),
                    "public_state_vars": meta.get("public_state_vars"),
                    "writing_entry_points": meta.get("writing_entry_points"),
                    "test_frameworks": meta.get("test_frameworks"),
                    "referenced_by_dev_tests": meta.get(
                        "referenced_by_dev_tests"),
                    "source": "prepared-subject-meta",
                })
    return targets


def peer_targets(root: Path) -> list[dict]:
    subjects = root / "Results" / "Peer182" / "subjects"
    if not subjects.is_dir():
        fallback = root / "scripts" / "Results" / "workdirs" / "Peer182" / "subjects"
        if fallback.is_dir():
            subjects = fallback
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
        if not _is_peer_contract080(meta):
            targets.append({
                "schema": "veriput-eval-target/v1",
                "benchmark": "peer182",
                "subject_id": subject_id,
                "status": "skipped",
                "reason": (
                    "peer RQ1 only schedules contract080 sources; "
                    f"expected source_file to contain {PEER_REQUIRED_SOURCE_SEGMENT!r} "
                    "and source_080=true"),
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
    stress203_requested = False
    for name in benchmarks:
        if name == "stress203":
            stress203_requested = True
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
        scope = "include" if stress203_requested else stress_scope
        targets.extend(stress_targets(
            root,
            scope,
            prepared_ok_only=stress203_requested))

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
