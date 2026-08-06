#!/usr/bin/env python3
"""Build a VeriPUT unit manifest for prepared benchmark subjects.

This script never starts ESBMC.  By default it also does not invoke solc: rows
whose compact AST is absent are recorded as `missing-ast`.  Pass
`--generate-ast` when intentionally precomputing ASTs for a bounded set.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from veriput_subjects import (  # noqa: E402
    KNOWN_SUBJECT_ROOTS,
    SubjectError,
    resolve_subject,
    subject_dirs,
    unit_manifest,
)


def _subjects(args):
    if args.subject_id:
        if not args.subject_root and not args.benchmark:
            raise SubjectError(
                "--subject-id without --subject-root needs --benchmark")
        return [
            resolve_subject(
                sid,
                root=args.subject_root or None,
                benchmark=args.benchmark or None,
                require_unit=False,
            )
            for sid in args.subject_id
        ]
    dirs = subject_dirs(args.benchmark, args.subject_root or None)
    if args.limit:
        dirs = dirs[:args.limit]
    return [
        resolve_subject(
            str(path),
            benchmark=args.benchmark or None,
            require_unit=False,
        )
        for path in dirs
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--benchmark", choices=sorted(KNOWN_SUBJECT_ROOTS),
                    required=True,
                    help="prepared-subject population label")
    ap.add_argument("--subject-root", default="",
                    help="override the population's subjects directory")
    ap.add_argument("--subject-id", action="append", default=[],
                    help="one subject id to include. Repeatable. Without it, "
                         "all subjects under the root are considered")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N subjects from the sorted root. "
                         "Ignored with --subject-id")
    ap.add_argument("--generate-ast", action="store_true",
                    help="invoke each subject's solc_bin to create a missing "
                         "compact AST before enumeration. Still never starts "
                         "ESBMC")
    ap.add_argument("--out", default="",
                    help="write JSON manifest here. Without it, print to stdout")
    args = ap.parse_args()
    try:
        subjects = _subjects(args)
        doc = unit_manifest(
            args.benchmark, subjects, generate_ast=args.generate_ast)
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
            f"units={s['units']} skipped={s['skipped']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
