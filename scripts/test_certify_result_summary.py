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
            {
                "schema": "veriput-unit-job/v1",
                "job_id": "stress243__I__i",
                "priority": 2,
                "benchmark": "stress243",
                "subject_id": "I",
                "contract": "I",
                "unit": "i",
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
            "generalise_progress": {
                "stage": "complete",
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
            "generalise_progress": {
                "stage": "outer-round-started",
                "round_kind": "linear-refine",
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
            "generalise_progress": {
                "stage": "complete",
            },
        },
        {
            "benchmark": "stress243",
            "unit": "h",
            "bucket": "KILLED",
            "witnessed": 2,
            "certified": {},
            "not_certified": {},
            "generalise_progress": {
                "stage": "certify-query-started",
                "enc": 31,
            },
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
    bad += check(s["attempt_rows"] == 4 and s["bad_lines"] == 1 and s["duplicate_rows"] == 1,
                 f"rows and duplicate latest records are counted: {s}")
    bad += check(
        s["witnessed_paths"] == 7 and s["certified_paths"] == 3 and s["not_certified_paths"] == 1
        and s["no_verdict_paths"] == 3, f"path accounting keeps the no-verdict gap: {s}")
    bad += check(s["certified_region_shapes"] == {
        "point": 2,
        "wide": 1,
    }, f"region shapes separate point from wide: {s}")
    bad += check(s["not_certified_reason_buckets"] == {"refuted": 1},
                 f"latest-row not-certified reasons are bucketed: {s}")
    bad += check(doc["by_priority"] == {
        "0": {
            "CERTIFIED": 1,
            "KILLED": 1,
        },
        "1": {
            "CERTIFIED": 1,
        },
    }, f"schedule priority grouping is applied: {doc['by_priority']}")
    bad += check(doc["summary"]["missing_scheduled_units"] == 1,
                 f"schedule-only unit is reported missing: {doc['summary']}")
    bad += check(s["progress_rows"] == {
        "certification:certify-query-started": 1,
        "complete": 2,
    }, f"progress stages are counted from latest rows: {s}")
    bad += check(s["noncert_progress_rows"] == {
        "certification:certify-query-started": 1,
    }, f"non-certified rows are grouped by last progress stage: {s}")
    bad += check(s["no_verdict_progress_paths"] == {
        "certification:certify-query-started": 2,
        "complete": 1,
    }, f"no-verdict gaps are weighted by progress stage: {s}")
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


def test_summary_gate_uses_slice_adjusted_path_rate():
    with tempfile.TemporaryDirectory() as td:
        cert = write_jsonl(Path(td) / "cert.jsonl", [
            {
                "benchmark": "peer182",
                "unit": "f",
                "bucket": "CERTIFIED",
                "witnessed": 2,
                "certified": {
                    "3": "x in [0, 9]",
                },
                "not_certified": {
                    "2": "EXCLUDED FROM THE SLICE by the pins "
                         "(msg.value: CE 1 outside [0, 0]), which is why its "
                         "region came back EMPTY on x. This is NOT a failure to certify",
                },
            },
        ],
                           bad_line=False)
        sched = Path(td) / "schedule.json"
        doc = schedule_doc()
        doc["jobs"] = doc["jobs"][:1]
        sched.write_text(json.dumps(doc) + "\n")
        summary = certify_result_summary.summarize(str(cert), schedule_path=str(sched))
    s = summary["summary"]
    bad = 0
    bad += check(summary["gate"]["status"] == "ready",
                 f"slice-excluded path does not degrade the body-slice gate: {summary['gate']}")
    bad += check(s["certified_path_rate"] == 0.5
                 and s["slice_adjusted_certified_path_rate"] == 1.0,
                 f"raw and slice-adjusted rates are both reported: {s}")
    bad += check(s["slice_excluded_paths"] == 1 and s["eligible_witnessed_paths"] == 1,
                 f"slice-excluded paths are separated from eligible witnesses: {s}")
    bad += check(s["not_certified_reason_buckets"] == {
        "slice-excluded-by-pins": 1,
    }, f"slice exclusions have a distinct reason bucket: {s}")
    return bad


def test_summary_gate_uses_retry_adjusted_path_rate_for_method_limits():
    with tempfile.TemporaryDirectory() as td:
        cert = write_jsonl(Path(td) / "cert.jsonl", [
            {
                "benchmark": "peer182",
                "unit": "f",
                "bucket": "CERTIFIED",
                "witnessed": 2,
                "certified": {
                    "3": "x in [0, 9]",
                },
                "not_certified": {
                    "12": "STATICALLY INSEPARABLE: this path has a witnessed sibling "
                          "whose source-level split is driven by an ESBMC hash/nondet/"
                          "external-call decision rather than by a generated-test-settable "
                          "coordinate (decision#3 random == 0).",
                },
            },
        ],
                           bad_line=False)
        sched = Path(td) / "schedule.json"
        doc = schedule_doc()
        doc["jobs"] = doc["jobs"][:1]
        sched.write_text(json.dumps(doc) + "\n")
        summary = certify_result_summary.summarize(str(cert), schedule_path=str(sched))
    s = summary["summary"]
    bad = 0
    bad += check(summary["gate"]["status"] == "ready",
                 f"method-limited path does not cause a retry-quality gate failure: "
                 f"{summary['gate']}")
    bad += check(s["certified_path_rate"] == 0.5
                 and s["slice_adjusted_certified_path_rate"] == 0.5
                 and s["retry_adjusted_certified_path_rate"] == 1.0,
                 f"raw, slice-adjusted, and retry-adjusted rates are reported: {s}")
    bad += check(s["method_unsupported_paths"] == 1
                 and s["retry_eligible_witnessed_paths"] == 1,
                 f"method-unsupported paths are separated from retry-eligible witnesses: {s}")
    return bad


def test_summary_matches_prepared_subject_benchmark_key_alias():
    with tempfile.TemporaryDirectory() as td:
        cert = write_jsonl(Path(td) / "cert.jsonl", [
            {
                "benchmark": "bugfix124__acfix_fixlink_DepositLog",
                "unit": "approvedToLog",
                "bucket": "CERTIFIED",
                "witnessed": 2,
                "certified": {
                    "2": "msg.value in [1, 9]",
                },
                "not_certified": {
                    "12": "STATICALLY INSEPARABLE: decision#3 random == 0 "
                          "uses __esbmc_hash_result / NONDET source",
                },
            },
        ],
                           bad_line=False)
        sched = Path(td) / "schedule.json"
        doc = schedule_doc()
        doc["jobs"] = [{
            "schema": "veriput-unit-job/v1",
            "job_id": "bugfix124__acfix_fixlink_DepositLog__approvedToLog",
            "priority": 0,
            "benchmark": "bugfix124",
            "subject_id": "acfix_fixlink_DepositLog",
            "contract": "DepositLog",
            "unit": "approvedToLog",
            "subject": {
                "benchmark": "bugfix124",
                "benchmark_key": "bugfix124__acfix_fixlink_DepositLog",
            },
            "certify_argv": ["/bin/false"],
        }]
        sched.write_text(json.dumps(doc) + "\n")
        summary = certify_result_summary.summarize(str(cert), schedule_path=str(sched))
    bad = 0
    bad += check(summary["summary"]["missing_scheduled_units"] == 0,
                 f"prepared benchmark_key rows satisfy schedule coverage: {summary['summary']}")
    bad += check(summary["by_priority"] == {
        "0": {
            "CERTIFIED": 1,
        },
    }, f"priority grouping uses benchmark_key aliases: {summary['by_priority']}")
    bad += check(summary["summary"]["not_certified_reason_buckets"] == {
        "method-unsupported:static-uncontrolled": 1,
    }, f"hash/nondet static split gets its own reason bucket: {summary['summary']}")
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
    test_summary_gate_uses_slice_adjusted_path_rate,
    test_summary_gate_uses_retry_adjusted_path_rate_for_method_limits,
    test_summary_matches_prepared_subject_benchmark_key_alias,
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
