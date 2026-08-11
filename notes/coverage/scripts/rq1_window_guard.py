#!/usr/bin/env python3
"""Guard RQ1 investigation and execution to the active rolling window.

This is intentionally a small shared gate.  It prevents two failure modes:

* running subjects outside the current rolling no-valid window;
* manually inspecting result/cert/source artifacts for a later window before
  the current one has been settled.

The guard accepts either a JSON active-window file:

  {"entries": [{"bench": "bugfix124", "subject": "pop_001_Multicall"}]}

or the existing tab-separated manifest with `bench` and `subject` columns.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ACTIVE_WINDOW = Path(
    os.environ.get(
        "VERIPUT_RQ1_ACTIVE_WINDOW",
        "notes/coverage/rq1_runs/manual-005-012-novalid-r3/active-window.json"))
DEFAULT_RESULTS_ROOT = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_WORKDIR_ROOT = Path("/home/samson/workspace/VeriPUT/scripts/Results/workdirs")
BENCH_WORKDIR_DIR = {
    "bugfix124": "BugFix124",
    "peer182": "Peer182",
    "real203": "Stress243",
    "stress203": "Stress243",
    "stress243": "Stress243",
}


class WindowGuardError(ValueError):
    pass


@dataclass(frozen=True)
class WindowEntry:
    bench: str
    subject: str

    @property
    def key(self) -> tuple[str, str]:
        return self.bench, self.subject


@dataclass(frozen=True)
class ActiveWindow:
    path: Path
    entries: tuple[WindowEntry, ...]

    @property
    def keys(self) -> set[tuple[str, str]]:
        return {entry.key for entry in self.entries}

    def require(self, bench: str, subject: str) -> None:
        key = (str(bench), str(subject))
        if key not in self.keys:
            allowed = ", ".join(
                f"{entry.bench}/{entry.subject}" for entry in self.entries)
            raise WindowGuardError(
                f"{bench}/{subject} is outside active RQ1 window {self.path}; "
                f"allowed: {allowed}")


def _entries_from_json(path: Path, data: object) -> list[WindowEntry]:
    if not isinstance(data, dict):
        raise WindowGuardError(f"{path} must contain a JSON object")
    raw_entries = data.get("entries") or data.get("subjects") or []
    if not isinstance(raw_entries, list):
        raise WindowGuardError(f"{path}: entries must be a list")
    entries = []
    for idx, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise WindowGuardError(f"{path}: entry {idx} is not an object")
        bench = item.get("bench") or item.get("benchmark")
        subject = item.get("subject") or item.get("subject_id")
        if not bench or not subject:
            raise WindowGuardError(
                f"{path}: entry {idx} lacks bench/subject")
        entries.append(WindowEntry(str(bench), str(subject)))
    return entries


def _entries_from_tsv(path: Path) -> list[WindowEntry]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        if "bench" not in (reader.fieldnames or []):
            raise WindowGuardError(f"{path}: missing `bench` column")
        if "subject" not in (reader.fieldnames or []):
            raise WindowGuardError(f"{path}: missing `subject` column")
        return [
            WindowEntry(str(row["bench"]), str(row["subject"]))
            for row in reader
            if row.get("bench") and row.get("subject")
        ]


def load_active_window(path: str | Path | None = None) -> ActiveWindow:
    p = Path(path or DEFAULT_ACTIVE_WINDOW)
    if not p.exists():
        raise WindowGuardError(f"active window file does not exist: {p}")
    if p.suffix == ".tsv":
        entries = _entries_from_tsv(p)
    else:
        try:
            entries = _entries_from_json(p, json.loads(p.read_text()))
        except json.JSONDecodeError as exc:
            raise WindowGuardError(f"{p}: invalid JSON: {exc}") from exc
    if not entries:
        raise WindowGuardError(f"{p}: active window is empty")
    return ActiveWindow(p, tuple(entries))


def enforce_rows_in_window(rows: list[dict], window_path: str | Path | None) -> None:
    if not window_path:
        return
    window = load_active_window(window_path)
    for row in rows:
        bench = row.get("benchmark") or row.get("bench")
        subject = row.get("subject_id") or row.get("subject")
        window.require(str(bench), str(subject))


def result_json_path(bench: str, subject: str, *,
                     results_root: Path = DEFAULT_RESULTS_ROOT) -> Path:
    return results_root / bench / "subjects" / subject / "result.json"


def cert_jsonl_path(bench: str, subject: str, *,
                    results_root: Path = DEFAULT_RESULTS_ROOT) -> Path:
    return results_root / bench / "subjects" / subject / "cert" / "certify-results.jsonl"


def source_path(bench: str, subject: str, *,
                workdir_root: Path = DEFAULT_WORKDIR_ROOT) -> Path:
    bench_dir = BENCH_WORKDIR_DIR.get(bench, bench)
    return workdir_root / bench_dir / "subjects" / subject / "flat.sol"


def _print_path(path: Path) -> int:
    if not path.exists():
        print(f"REFUSED: artifact does not exist: {path}", file=sys.stderr)
        return 1
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--window", default=str(DEFAULT_ACTIVE_WINDOW))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    for name in ("show-result", "show-cert", "show-source", "check"):
        p = sub.add_parser(name)
        p.add_argument("bench")
        p.add_argument("subject")
    args = ap.parse_args(argv)

    try:
        window = load_active_window(args.window)
        if args.cmd == "list":
            for entry in window.entries:
                print(f"{entry.bench}\t{entry.subject}")
            return 0
        window.require(args.bench, args.subject)
        if args.cmd == "check":
            print(f"OK {args.bench}/{args.subject}")
            return 0
        if args.cmd == "show-result":
            return _print_path(result_json_path(args.bench, args.subject))
        if args.cmd == "show-cert":
            return _print_path(cert_jsonl_path(args.bench, args.subject))
        if args.cmd == "show-source":
            return _print_path(source_path(args.bench, args.subject))
        raise WindowGuardError(f"unknown command {args.cmd}")
    except WindowGuardError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
