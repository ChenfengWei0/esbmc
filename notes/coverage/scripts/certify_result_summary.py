#!/usr/bin/env python3
"""Summarize VeriPUT Stage-2 certification result JSONL files.

`unit_schedule_run.py` can tell whether `certify_all.py` exited, but the real
PUT strength is in `certify_all.py --out`: witnessed paths, certified regions,
not-certified paths, no-verdict gaps, and whether certified regions are wide or
single-point.  This script is read-only unless `--out` is passed; it never
invokes solc, Forge, fuzzing, ESBMC, PUT emission, or certification jobs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

INTERVAL_RE = re.compile(r"(\S+) in \[([0-9]+), ([0-9]+)\](?: \\ \{([0-9, ]+)\})?")
PIN_RE = re.compile(r"(\S+) == ([0-9]+)")


class SummaryError(ValueError):
    """The certification output or schedule cannot be summarized."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SummaryError(f"{path} is not valid JSON: {exc}") from exc


def _read_jsonl(path: str) -> tuple[list[dict], int]:
    p = Path(path)
    rows = []
    bad_lines = 0
    if not p.exists():
        return rows, bad_lines
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad_lines += 1
    return rows, bad_lines


def _subject(row: dict) -> str:
    return row.get("benchmark") or row.get("poc") or "<unknown>"


def _row_key(row: dict) -> tuple[str, str, str | None]:
    return (_subject(row), row.get("unit") or "<none>", row.get("path_function"))


def _load_schedule(path: str) -> dict | None:
    if not path:
        return None
    doc = _load_json(path)
    if doc.get("schema") != "veriput-unit-schedule/v1":
        raise SummaryError(f"unsupported schedule schema {doc.get('schema')!r}")
    return doc


def _schedule_jobs(schedule: dict | None) -> dict[tuple[str, str], dict]:
    if not schedule:
        return {}
    jobs = {}
    for job in schedule.get("jobs") or []:
        subject = job.get("benchmark") or job.get("poc") or "<unknown>"
        unit = job.get("unit") or "<none>"
        jobs[(subject, unit)] = job
    return jobs


def _reason_bucket(reason: str) -> str:
    text = str(reason or "")
    if not text:
        return "<empty>"
    if "STATICALLY INSEPARABLE" in text:
        return "method-unsupported:static-extcall"
    if "refuted" in text.lower() or "concrete witness" in text.lower():
        return "refuted"
    if "timeout" in text.lower() or "budget" in text.lower():
        return "budget"
    if "EMPTY" in text or "lo > hi" in text:
        return "empty-region"
    if "no outer-box round finished" in text:
        return "budget:no-outer-box"
    return text.split(":", 1)[0][:80]


def _region_shape(text: str) -> dict:
    intervals = {}
    holes = {}
    for match in INTERVAL_RE.finditer(text or ""):
        name = match.group(1)
        lo, hi = int(match.group(2)), int(match.group(3))
        intervals[name] = (lo, hi)
        if match.group(4):
            holes[name] = sorted({int(v) for v in match.group(4).split(",") if v.strip()})
    consumed = set(intervals)
    pins = {}
    for match in PIN_RE.finditer(text or ""):
        name = match.group(1)
        if name in consumed:
            continue
        pins[name] = int(match.group(2))
    wide = [
        name for name, (lo, hi) in intervals.items()
        if hi > lo and len([v for v in holes.get(name, []) if lo <= v <= hi]) < (hi - lo + 1)
    ]
    parsed = bool(intervals or pins)
    return {
        "parsed": parsed,
        "intervals": len(intervals),
        "pins": len(pins),
        "holes": sum(len(values) for values in holes.values()),
        "wide": len(wide),
        "shape": "wide" if wide else ("point" if parsed else "unparsed"),
    }


def summarize(cert_jsonl: str,
              *,
              schedule_path: str = "",
              min_certified_path_rate: float = 0.70,
              sample_limit: int = 10) -> dict:
    rows, bad_lines = _read_jsonl(cert_jsonl)
    schedule = _load_schedule(schedule_path)
    jobs = _schedule_jobs(schedule)
    latest = {}
    duplicate_rows = 0
    for row in rows:
        key = _row_key(row)
        if key in latest:
            duplicate_rows += 1
        latest[key] = row

    bucket_rows = Counter()
    by_subject = defaultdict(Counter)
    by_priority = defaultdict(Counter)
    reason_buckets = Counter()
    region_shapes = Counter()
    samples = defaultdict(list)

    witnessed_paths = 0
    certified_paths = 0
    not_certified_paths = 0
    no_verdict_paths = 0
    certified_regions = 0
    rows_with_certified = 0
    rows_with_witnessed = 0

    for key, row in latest.items():
        subject, unit, _path_function = key
        job = jobs.get((subject, unit)) or {}
        priority = str(job.get("priority", row.get("priority", "<unknown>")))
        bucket = row.get("bucket") or "<missing-bucket>"
        bucket_rows[bucket] += 1
        by_subject[subject][bucket] += 1
        by_priority[priority][bucket] += 1

        certified = row.get("certified") or {}
        not_certified = row.get("not_certified") or {}
        c_count = len(certified) if isinstance(certified, dict) else 0
        n_count = len(not_certified) if isinstance(not_certified, dict) else 0
        certified_regions += c_count
        if c_count:
            rows_with_certified += 1
        if isinstance(row.get("witnessed"), int):
            witnessed = max(0, row["witnessed"])
            rows_with_witnessed += 1
            witnessed_paths += witnessed
            certified_paths += c_count
            not_certified_paths += n_count
            no_verdict_paths += max(0, witnessed - c_count - n_count)

        if isinstance(not_certified, dict):
            for reason in not_certified.values():
                reason_buckets[_reason_bucket(str(reason))] += 1

        if isinstance(certified, dict):
            for enc, region_text in certified.items():
                shape = _region_shape(str(region_text))
                region_shapes[shape["shape"]] += 1
                if len(samples[shape["shape"]]) < sample_limit:
                    samples[shape["shape"]].append({
                        "subject": subject,
                        "unit": unit,
                        "enc": enc,
                        "region": region_text,
                        "intervals": shape["intervals"],
                        "pins": shape["pins"],
                        "holes": shape["holes"],
                    })

        if bucket != "CERTIFIED" and len(samples[bucket]) < sample_limit:
            samples[bucket].append({
                "subject": subject,
                "unit": unit,
                "bucket": bucket,
                "witnessed": row.get("witnessed"),
                "certified": c_count,
                "not_certified": n_count,
                "driver_refusal": row.get("driver_refusal"),
                "no_coordinate_reason": row.get("no_coordinate_reason"),
            })

    scheduled_units = set(jobs)
    seen_units = {(key[0], key[1]) for key in latest}
    missing_scheduled = sorted(scheduled_units - seen_units)
    certified_path_rate = (certified_paths / witnessed_paths) if witnessed_paths else None
    verdict_path_rate = ((certified_paths + not_certified_paths) /
                         witnessed_paths if witnessed_paths else None)
    gate = "blocked"
    blockers = []
    if bad_lines:
        blockers.append("certification JSONL contains unparsable lines")
    if schedule and missing_scheduled:
        blockers.append("scheduled units have no certification row")
    if not latest:
        blockers.append("no certification rows")
    if certified_regions == 0:
        blockers.append("no certified regions")
    if witnessed_paths and certified_path_rate is not None \
            and certified_path_rate < min_certified_path_rate:
        blockers.append("certified path rate is below threshold")
    if blockers:
        gate = "blocked" if certified_regions == 0 else "degraded"
    else:
        gate = "ready"

    return {
        "schema":
        "veriput-certify-result-summary/v1",
        "generated_at":
        datetime.now(timezone.utc).isoformat(),
        "cert_jsonl":
        cert_jsonl,
        "schedule":
        schedule_path or None,
        "gate": {
            "status": gate,
            "min_certified_path_rate": min_certified_path_rate,
            "blockers": blockers,
        },
        "summary": {
            "attempt_rows": len(rows),
            "bad_lines": bad_lines,
            "unique_rows": len(latest),
            "duplicate_rows": duplicate_rows,
            "scheduled_units": len(scheduled_units),
            "missing_scheduled_units": len(missing_scheduled),
            "rows_with_witnessed": rows_with_witnessed,
            "rows_with_certified": rows_with_certified,
            "witnessed_paths": witnessed_paths,
            "certified_paths": certified_paths,
            "not_certified_paths": not_certified_paths,
            "no_verdict_paths": no_verdict_paths,
            "certified_regions": certified_regions,
            "certified_path_rate": certified_path_rate,
            "verdict_path_rate": verdict_path_rate,
            "bucket_rows": dict(sorted(bucket_rows.items())),
            "not_certified_reason_buckets": dict(sorted(reason_buckets.items())),
            "certified_region_shapes": dict(sorted(region_shapes.items())),
        },
        "by_subject": {
            subject: dict(sorted(counter.items()))
            for subject, counter in sorted(by_subject.items())
        },
        "by_priority": {
            priority: dict(sorted(counter.items()))
            for priority, counter in sorted(by_priority.items())
        },
        "missing_scheduled_units": [{
            "subject": subject,
            "unit": unit,
            "job_id": jobs[(subject, unit)].get("job_id"),
            "priority": jobs[(subject, unit)].get("priority"),
        } for subject, unit in missing_scheduled[:sample_limit]],
        "samples":
        dict(samples),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cert_jsonl", help="certify_all.py --out JSONL")
    ap.add_argument("--schedule", default="", help="optional veriput-unit-schedule/v1 JSON")
    ap.add_argument("--min-certified-path-rate",
                    type=float,
                    default=0.70,
                    help="gate threshold over witnessed paths")
    ap.add_argument("--sample-limit", type=int, default=10, help="maximum sample rows per bucket")
    ap.add_argument("--out", default="", help="write JSON summary here instead of stdout")
    args = ap.parse_args()
    try:
        doc = summarize(args.cert_jsonl,
                        schedule_path=args.schedule,
                        min_certified_path_rate=args.min_certified_path_rate,
                        sample_limit=args.sample_limit)
    except (OSError, SummaryError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
