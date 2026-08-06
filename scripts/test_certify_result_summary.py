#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import certify_result_summary  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def write_jsonl(path, rows, bad_line=True):
    p = Path(path)
    text = "\n".join(json.dumps(row) for row in rows) + "\n"
    if bad_line:
        text += "not-json\n"
    p.write_text(text)
    return p


def schedule_doc():
    return {
        "schema":
        "veriput-unit-schedule/v1",
        "summary": {
            "jobs": 3,
        },
        "jobs": [
            {
                "schema": "veriput-unit-job/v1",
                "job_id": "peer182__C__f",
                "priority": 0,
                "benchmark": "peer182",
                "subject_id": "C",
                "contract": "C",
                "unit": "f",
                "certify_argv": ["/bin/false"],
            },
            {
                "schema": "veriput-unit-job/v1",
                "job_id": "bugfix124__D__g",
                "priority": 1,
                "benchmark": "bugfix124",
                "subject_id": "D",
                "contract": "D",
                "unit": "g",
                "certify_argv": ["/bin/false"],
            },
            {
                "schema": "veriput-unit-job/v1",
                "job_id": "stress243__E__h",
                "priority": 0,
                "benchmark": "stress243",
                "subject_id": "E",
                "contract": "E",
                "unit": "h",
                "certify_argv": ["/bin/false"],
            },
        ],
    }


def rows():
    return [
        {
            "benchmark": "peer182",
            "unit": "f",
            "bucket": "CERTIFIED",
            "witnessed": 3,
            "certified": {
                "1": "x in [0, 9]",
                "2": "y == 1",
            },
            "not_certified": {
                "3": "refuted with concrete witness",
            },
        },
        {
            "benchmark": "bugfix124",
            "unit": "g",
            "bucket": "KILLED",
            "witnessed": 4,
            "certified": {},
            "not_certified": {
                "7": "no outer-box round finished, so nothing was measured",
            },
        },
        {
            "benchmark": "bugfix124",
            "unit": "g",
            "bucket": "CERTIFIED",
            "witnessed": 2,
            "certified": {
                "8": "z in [5, 5]",
            },
            "not_certified": {},
        },
    ]


def test_summary_counts_paths_shapes_and_schedule_gaps():
    with tempfile.TemporaryDirectory() as td:
        cert = write_jsonl(Path(td) / "cert.jsonl", rows())
        sched = Path(td) / "schedule.json"
        sched.write_text(json.dumps(schedule_doc()) + "\n")
        doc = certify_result_summary.summarize(str(cert), schedule_path=str(sched))
    s = doc["summary"]
    bad = 0
    bad += check(doc["schema"] == "veriput-certify-result-summary/v1",
                 f"summary schema is stable: {doc['schema']}")
    bad += check(s["attempt_rows"] == 3 and s["bad_lines"] == 1 and s["duplicate_rows"] == 1,
                 f"rows and duplicate latest records are counted: {s}")
    bad += check(
        s["witnessed_paths"] == 5 and s["certified_paths"] == 3 and s["not_certified_paths"] == 1
        and s["no_verdict_paths"] == 1, f"path accounting keeps the no-verdict gap: {s}")
    bad += check(s["certified_region_shapes"] == {
        "point": 2,
        "wide": 1,
    }, f"region shapes separate point from wide: {s}")
    bad += check(s["not_certified_reason_buckets"] == {"refuted": 1},
                 f"latest-row not-certified reasons are bucketed: {s}")
    bad += check(doc["by_priority"] == {
        "0": {
            "CERTIFIED": 1,
        },
        "1": {
            "CERTIFIED": 1,
        },
    }, f"schedule priority grouping is applied: {doc['by_priority']}")
    bad += check(doc["summary"]["missing_scheduled_units"] == 1,
                 f"schedule-only unit is reported missing: {doc['summary']}")
    bad += check(doc["gate"]["status"] == "degraded",
                 f"missing schedule row degrades the gate: {doc['gate']}")
    return bad


def test_summary_gate_ready_when_threshold_and_schedule_are_clean():
    with tempfile.TemporaryDirectory() as td:
        cert = write_jsonl(Path(td) / "cert.jsonl", [
            {
                "benchmark": "peer182",
                "unit": "f",
                "bucket": "CERTIFIED",
                "witnessed": 2,
                "certified": {
                    "1": "x in [0, 2]",
                    "2": "x in [3, 5]",
                },
                "not_certified": {},
            },
        ],
                           bad_line=False)
        sched = Path(td) / "schedule.json"
        doc = schedule_doc()
        doc["jobs"] = doc["jobs"][:1]
        sched.write_text(json.dumps(doc) + "\n")
        summary = certify_result_summary.summarize(str(cert), schedule_path=str(sched))
    bad = 0
    bad += check(summary["gate"]["status"] == "ready",
                 f"clean high-rate result is ready: {summary['gate']}")
    bad += check(summary["summary"]["certified_path_rate"] == 1.0,
                 f"certified path rate is computed: {summary['summary']}")
    return bad


def test_summary_cli_writes_json():
    with tempfile.TemporaryDirectory() as td:
        cert = write_jsonl(Path(td) / "cert.jsonl", rows()[:1])
        out = Path(td) / "summary.json"
        cp = subprocess.run([
            sys.executable,
            str(ROOT / "notes" / "coverage" / "scripts" / "certify_result_summary.py"),
            str(cert),
            "--out",
            str(out),
        ],
                            capture_output=True,
                            text=True)
        doc = json.loads(out.read_text()) if out.exists() else {}
    if cp.returncode:
        print(cp.stdout)
        print(cp.stderr)
        return 1
    bad = 0
    bad += check(doc["summary"]["certified_regions"] == 2,
                 f"CLI writes summary JSON: {doc.get('summary')}")
    return bad


TESTS = [
    test_summary_counts_paths_shapes_and_schedule_gaps,
    test_summary_gate_ready_when_threshold_and_schedule_are_clean,
    test_summary_cli_writes_json,
]

if __name__ == "__main__":
    failures = 0
    for test in TESTS:
        try:
            failures += test()
        except Exception as exc:  # pragma: no cover - tiny script harness
            print(f"FAIL: {test.__name__}: {exc}")
            failures += 1
    raise SystemExit(1 if failures else 0)
