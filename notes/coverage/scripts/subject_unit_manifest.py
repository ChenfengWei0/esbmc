#!/usr/bin/env python3
"""Build a VeriPUT unit manifest for prepared benchmark subjects.

This script never starts ESBMC.  By default it also does not invoke solc: rows
whose compact AST is absent are recorded as `missing-ast`.  Pass
`--generate-ast` when intentionally precomputing ASTs for a bounded set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from veriput_subjects import (  # noqa: E402
    DEFAULT_AST_TIMEOUT_S,
    KNOWN_SUBJECT_ROOTS,
    PreparedSubject,
    SubjectError,
    manifest_for_subject,
    resolve_subject,
    subject_dirs,
)


def _parse_shard(text: str):
    if not text:
        return None
    try:
        left, right = text.split("/", 1)
        idx, total = int(left), int(right)
    except (ValueError, AttributeError):
        raise SubjectError("--shard must be in i/n form")
    if total <= 0 or idx < 0 or idx >= total:
        raise SubjectError("--shard needs 0 <= i < n")
    return idx, total


def _apply_shard(items, shard):
    if shard is None:
        return items
    idx, total = shard
    return [item for pos, item in enumerate(items) if pos % total == idx]


def _target_manifest_subjects(args):
    if not args.target_manifest:
        return None
    p = Path(args.target_manifest)
    try:
        doc = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise SubjectError(f"{p} is not valid JSON: {exc}") from exc
    if doc.get("schema") != "veriput-eval/target/v1":
        raise SubjectError(
            f"{p} has unsupported schema {doc.get('schema')!r}")
    items = []
    for target in doc.get("targets") or []:
        if target.get("status") != "ok":
            continue
        benchmark = target.get("benchmark")
        subject_id = target.get("subject_id")
        if not benchmark or not subject_id:
            raise SubjectError(
                f"{p} has an ok target row without benchmark/subject_id")
        try:
            subject = resolve_subject(
                subject_id,
                root=args.subject_root or None,
                benchmark=benchmark,
                require_unit=False)
        except SubjectError as exc:
            items.append((None, {
                "schema": "veriput-unit-target/v1",
                "status": "error",
                "reason": str(exc),
                "target": target,
            }))
            continue
        if subject.contract != target.get("contract"):
            items.append((subject, {
                "schema": "veriput-unit-target/v1",
                "status": "error",
                "reason": "target manifest contract disagrees with subject",
                "target": target,
                "subject_contract": subject.contract,
            }))
            continue
        items.append((subject, {
            "schema": "veriput-unit-target/v1",
            "status": "ok",
            "target": target,
        }))
    items = _apply_shard(items, _parse_shard(args.shard))
    if args.limit:
        items = items[:args.limit]
    return items


def _apply_ast_cache(subject: PreparedSubject | None,
                     cache_root: str) -> PreparedSubject | None:
    if subject is None or not cache_root:
        return subject
    base = Path(cache_root).expanduser().resolve()
    ast_name = Path(subject.solast).name
    cached = base / subject.benchmark / subject.benchmark_key / ast_name
    return subject.with_solast_path(str(cached), source="cache")


def _subjects(args):
    manifest_subjects = _target_manifest_subjects(args)
    if manifest_subjects is not None:
        return manifest_subjects
    if args.subject_id:
        if not args.subject_root and not args.benchmark:
            raise SubjectError(
                "--subject-id without --subject-root needs --benchmark")
        subjects = [
            (resolve_subject(
                 sid,
                 root=args.subject_root or None,
                 benchmark=args.benchmark or None,
                 require_unit=False),
             None)
            for sid in args.subject_id
        ]
        return _apply_shard(subjects, _parse_shard(args.shard))
    dirs = subject_dirs(args.benchmark, args.subject_root or None)
    dirs = _apply_shard(dirs, _parse_shard(args.shard))
    if args.limit:
        dirs = dirs[:args.limit]
    return [
        (resolve_subject(
             str(path),
             benchmark=args.benchmark or None,
             require_unit=False),
         None)
        for path in dirs
    ]


def _target_error_row(subject: PreparedSubject, target_info: dict):
    target = target_info.get("target") or {}
    subject_record = subject.to_record() if subject else {
        "benchmark": target.get("benchmark"),
        "subject_id": target.get("subject_id"),
        "contract": target.get("contract"),
    }
    return {
        "subject": subject_record,
        "status": "error",
        "reason": target_info["reason"],
        "target": target,
        "subject_contract": target_info.get("subject_contract"),
    }


def _annotate_target(row: dict, target_info: dict | None):
    if not target_info:
        return row
    target = target_info.get("target") or {}
    row["target"] = target
    hints = target.get("units_hint") or []
    if row.get("status") != "ok" or not hints:
        if hints:
            row["unit_hints"] = {
                "hinted_units": [],
                "missing_unit_hints": [],
                "pending_unit_hints": list(hints),
            }
        return row
    units = (row.get("units") or {}).get("units") or []
    unit_set = set(units)
    hinted = [name for name in hints if name in unit_set]
    missing = [name for name in hints if name not in unit_set]
    row["unit_hints"] = {
        "hinted_units": hinted,
        "missing_unit_hints": missing,
        "pending_unit_hints": [],
    }
    return row


def _load_resume_keys(path):
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    done = set()
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("status") != "ok":
            continue
        subj = row.get("subject") or {}
        key = subj.get("subject_id")
        if key:
            done.add(key)
    return done


def _write_journal(path, row):
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def build_manifest(args):
    subjects = _subjects(args)
    skipped = _load_resume_keys(args.resume_journal)
    rows = []
    skipped_resume = 0
    for subject, target_info in subjects:
        subject = _apply_ast_cache(subject, args.ast_cache_root)
        target = (target_info or {}).get("target") or {}
        subject_id = subject.subject_id if subject else target.get("subject_id")
        if subject_id in skipped:
            skipped_resume += 1
            continue
        if subject is None or (target_info and target_info.get("status") ==
                               "error"):
            row = _target_error_row(subject, target_info)
        else:
            if args.generate_ast and args.use_inferred_solc_bin:
                subject = subject.with_inferred_solc_bin()
            row = manifest_for_subject(
                subject,
                generate_ast=args.generate_ast,
                ast_timeout_s=args.ast_timeout)
            row = _annotate_target(row, target_info)
        rows.append(row)
        _write_journal(args.journal, row)
    summary = {
        "subjects": len(rows),
        "ok": sum(1 for row in rows if row["status"] == "ok"),
        "missing_ast": sum(1 for row in rows
                           if row["status"] == "missing-ast"),
        "error": sum(1 for row in rows if row["status"] == "error"),
        "units": sum(len((row.get("units") or {}).get("units") or [])
                     for row in rows),
        "hinted_units": sum(
            len((row.get("unit_hints") or {}).get("hinted_units") or [])
            for row in rows),
        "missing_unit_hints": sum(
            len((row.get("unit_hints") or {}).get("missing_unit_hints") or [])
            for row in rows),
        "pending_unit_hints": sum(
            len((row.get("unit_hints") or {}).get("pending_unit_hints") or [])
            for row in rows),
        "skipped": sum(len((row.get("units") or {}).get("skipped") or [])
                       for row in rows),
        "skipped_resume": skipped_resume,
    }
    return {
        "schema": "veriput-unit-manifest/v1",
        "benchmark": args.benchmark or None,
        "target_manifest": args.target_manifest or None,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generate_ast": bool(args.generate_ast),
        "ast_timeout_s": args.ast_timeout,
        "ast_cache_root": args.ast_cache_root or None,
        "shard": args.shard or None,
        "journal": args.journal or None,
        "resume_journal": args.resume_journal or None,
        "summary": summary,
        "subjects": rows,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", choices=sorted(KNOWN_SUBJECT_ROOTS),
                    default="",
                    help="prepared-subject population label")
    ap.add_argument("--target-manifest", default="",
                    help="read a veriput-eval/target/v1 manifest and enumerate "
                         "its ok targets through prepared Results subjects")
    ap.add_argument("--subject-root", default="",
                    help="override the population's subjects directory. With "
                         "--target-manifest this is intended for a manifest "
                         "whose rows all live under one prepared-subject root")
    ap.add_argument("--subject-id", action="append", default=[],
                    help="one subject id to include. Repeatable. Without it, "
                         "all subjects under the root are considered")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N subjects from the sorted root. "
                         "Ignored with --subject-id")
    ap.add_argument("--shard", default="",
                    help="select subject positions i/n after sorting. Example: "
                         "--shard 0/4. Works with --limit and --subject-id")
    ap.add_argument("--generate-ast", action="store_true",
                    help="invoke each subject's solc_bin to create a missing "
                         "compact AST before enumeration. Still never starts "
                         "ESBMC")
    ap.add_argument("--ast-cache-root", default="",
                    help="read/write compact ASTs under this cache root "
                         "instead of each prepared subject directory")
    ap.add_argument("--use-inferred-solc-bin", action="store_true",
                    help="with --generate-ast, use a solc path inferred from "
                         "prepared meta compile.cmd when solc_bin is absent")
    ap.add_argument("--ast-timeout", type=float, default=DEFAULT_AST_TIMEOUT_S,
                    help="per-subject solc timeout in seconds when "
                         "--generate-ast is set")
    ap.add_argument("--journal", default="",
                    help="append one JSONL row per processed subject and fsync "
                         "it. Useful for long AST preheat runs")
    ap.add_argument("--resume-journal", default="",
                    help="skip subject ids that already have status=ok in this "
                         "journal. Non-ok rows are retried")
    ap.add_argument("--out", default="",
                    help="write JSON manifest here. Without it, print to stdout")
    args = ap.parse_args()
    if not args.benchmark and not args.target_manifest:
        print("REFUSED: pass --benchmark or --target-manifest", file=sys.stderr)
        return 1
    try:
        doc = build_manifest(args)
    except SubjectError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}")
        s = doc["summary"]
        print(
            f"subjects={s['subjects']} ok={s['ok']} "
            f"missing_ast={s['missing_ast']} error={s['error']} "
            f"units={s['units']} skipped={s['skipped']} "
            f"skipped_resume={s['skipped_resume']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
