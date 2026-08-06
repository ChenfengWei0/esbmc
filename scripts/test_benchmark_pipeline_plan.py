#!/usr/bin/env python3
import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "notes" / "coverage" / "scripts"))

import benchmark_pipeline_plan  # noqa: E402


def check(cond, msg):
    if cond:
        print("ok:", msg)
        return 0
    print("FAIL:", msg)
    return 1


def argv_value(argv, flag):
    try:
        idx = argv.index(flag)
    except ValueError:
        return None
    if idx + 1 >= len(argv):
        return None
    return argv[idx + 1]


def args(tmp, out_dir=""):
    return argparse.Namespace(veriput_root=str(tmp / "VeriPUT"),
                              benchmark=["peer182"],
                              stress_scope="include",
                              ast_cache_root=str(tmp / "cache"),
                              target_manifest_label="<target>",
                              subject_limit=0,
                              subject_shard="",
                              ast_timeout=60.0,
                              preheat_shard="",
                              preheat_limit=0,
                              ast_preheat_journal=[],
                              ast_preheat_batch_size=32,
                              ast_preheat_selection_strategy="priority",
                              ast_preheat_max_attempts=3,
                              ast_preheat_outer_timeout=90.0,
                              ast_preheat_memlimit_gb=8.0,
                              next_ast_preheat_journal="",
                              ast_preheat_jobs=1,
                              ast_preheat_stop_on_failure=False,
                              unit_shard="",
                              unit_limit=0,
                              cert_out="",
                              journal=[],
                              cert_jsonl=[],
                              min_certified_path_rate=0.70,
                              attempt=0,
                              next_journal="",
                              jobs=2,
                              stop_on_failure=False,
                              sample_limit=5,
                              put_timeout=600.0,
                              put_memlimit_gib=8,
                              put_forge_timeout=300,
                              put_out_root="",
                              out_dir=out_dir)


def target_doc():
    return {
        "schema": "veriput-eval/target/v1",
        "summary": {
            "targets": 1,
        },
        "targets": [],
    }


def missing_ast_manifest(cache_root):
    return {
        "schema": "veriput-unit-manifest/v1",
        "ast_cache_root": str(cache_root),
        "summary": {
            "subjects": 1,
            "missing_ast": 1,
            "ok": 0,
            "error": 0,
        },
        "subjects": [
            {
                "status": "missing-ast",
                "subject": {
                    "benchmark": "peer182",
                    "subject_id": "S",
                    "contract": "C",
                },
            },
        ],
    }


def ok_manifest(cache_root, tmp):
    return {
        "schema": "veriput-unit-manifest/v1",
        "ast_cache_root": str(cache_root),
        "summary": {
            "subjects": 1,
            "missing_ast": 0,
            "ok": 1,
            "error": 0,
            "units": 1,
        },
        "subjects": [
            {
                "status": "ok",
                "subject": {
                    "root": str(tmp / "prepared" / "S"),
                    "benchmark": "peer182",
                    "benchmark_key": "peer182__S",
                    "subject_id": "S",
                    "contract": "C",
                },
                "unit_hints": {
                    "hinted_units": ["f"],
                    "missing_unit_hints": [],
                    "pending_unit_hints": [],
                },
                "units": {
                    "units": ["f"],
                    "skipped": [],
                },
            },
        ],
    }


def with_patches(patches, fn):
    old = []
    for obj, name, value in patches:
        old.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)
    try:
        return fn()
    finally:
        for obj, name, value in old:
            setattr(obj, name, value)


def test_missing_ast_pipeline_recommends_preheat_without_writes():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        cache_root = tmp / "cache"

        def run():
            a = args(tmp)
            doc = benchmark_pipeline_plan.build_pipeline(a)
            bad = 0
            bad += check(doc["summary"]["next_action"]["action"] == "preheat-ast",
                         f"missing ASTs select preheat: {doc['summary']['next_action']}")
            bad += check(doc["summary"]["ast_preheat_jobs"] == 1
                         and doc["summary"]["ast_preheat_campaign"]["selected_jobs"] == 1
                         and doc["summary"]["unit_jobs"] == 0,
                         f"preheat is before unit campaign: {doc['summary']}")
            bad += check(doc["outputs"] == {}, f"no out-dir means no child JSONs: {doc['outputs']}")
            bad += check("--dry-run" in doc["next_runs"]["ast_preheat"]["dry_run_argv"],
                         f"missing-AST path exposes AST dry-run argv: {doc['next_runs']}")
            bad += check("--dry-run" in doc["next_runs"]["ast_preheat"]["dry_run_cmd"],
                         f"missing-AST path exposes AST dry-run command: {doc['next_runs']}")
            bad += check(doc["next_runs"]["ast_preheat"]["memlimit_gb"] == 8.0
                         and "--memlimit-gb" in doc["next_runs"]["ast_preheat"]["runner_argv"],
                         f"missing-AST path carries AST memlimit: {doc['next_runs']}")
            bad += check(doc["summary"]["next_action"]["command_kind"] == "ast_preheat"
                         and "--dry-run" in doc["summary"]["next_action"]["dry_run_cmd"]
                         and "--dry-run" not in doc["summary"]["next_action"]["runner_cmd"]
                         and doc["summary"]["next_action"]["memlimit_gb"] == 8.0,
                         f"next_action carries actionable AST commands: "
                         f"{doc['summary']['next_action']}")
            bad += check(not cache_root.exists(),
                         f"planning does not create the AST cache: {cache_root}")
            return bad

        patches = [
            (benchmark_pipeline_plan.target_manifest, "build_manifest",
             lambda *_args, **_kwargs: target_doc()),
            (benchmark_pipeline_plan.subject_unit_manifest, "build_manifest",
             lambda call_args: missing_ast_manifest(call_args.ast_cache_root)),
            (benchmark_pipeline_plan.ast_preheat_schedule, "build_schedule",
             lambda *_args, **_kwargs: {
                 "schema": "veriput-ast-preheat-schedule/v1",
                 "ast_cache_root": str(cache_root),
                 "ast_timeout_s": 60.0,
                 "summary": {
                     "jobs": 1,
                 },
                 "jobs": [
                     {
                         "schema": "veriput-ast-preheat-job/v1",
                         "job_id": "peer182__S",
                         "priority": 0,
                         "ordinal": 0,
                         "benchmark": "peer182",
                         "subject_id": "S",
                         "solc_source": "explicit",
                         "preheat_argv": [
                             "/bin/false",
                             "--generate-ast",
                             "--ast-cache-root",
                             str(cache_root),
                         ],
                     },
                 ],
             }),
        ]
        return with_patches(patches, run)


def test_ready_pipeline_writes_requested_docs_and_selects_campaign():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out_dir = tmp / "out"

        def run():
            a = args(tmp, out_dir=str(out_dir))
            doc = benchmark_pipeline_plan.build_pipeline(a)
            unit_schedule = json.loads(Path(doc["outputs"]["unit_schedule"]).read_text())
            bad = 0
            bad += check(doc["summary"]["next_action"]["action"] == "run-unit-campaign",
                         f"ready units select campaign: {doc['summary']['next_action']}")
            bad += check(doc["next_run"]["timeout_s"] == 60.0
                         and doc["next_run"]["memlimit_gb"] == 8.0,
                         f"first attempt budget is preserved: {doc['next_run']}")
            bad += check(sorted(doc["outputs"]) == [
                "ast_preheat_campaign_plan",
                "ast_preheat_schedule",
                "next_ast_preheat_schedule",
                "next_unit_schedule",
                "target_manifest",
                "unit_campaign_plan",
                "unit_manifest",
                "unit_manifest_gate",
                "unit_schedule",
            ], f"out-dir receives the expected child docs: {doc['outputs']}")
            bad += check(doc["next_runs"]["ast_preheat"] is None
                         and doc["next_runs"]["unit_campaign"]["timeout_s"] == 60.0,
                         f"ready path has no AST preheat runner: {doc['next_runs']}")
            bad += check("--dry-run" in doc["next_runs"]["unit_campaign"]["dry_run_argv"],
                         f"pipeline exposes unit dry-run argv: {doc['next_runs']}")
            bad += check("--dry-run" in doc["next_runs"]["unit_campaign"]["dry_run_cmd"],
                         f"pipeline exposes unit dry-run command: {doc['next_runs']}")
            bad += check(doc["summary"]["next_action"]["command_kind"] == "unit_campaign"
                         and "--dry-run" in doc["summary"]["next_action"]["dry_run_cmd"]
                         and "--dry-run" not in doc["summary"]["next_action"]["runner_cmd"]
                         and doc["summary"]["next_action"]["attempt"] == 1
                         and doc["summary"]["next_action"]["timeout_s"] == 60.0
                         and doc["summary"]["next_action"]["memlimit_gb"] == 8.0,
                         f"next_action carries actionable unit commands: "
                         f"{doc['summary']['next_action']}")
            bad += check(unit_schedule["summary"]["jobs"] == 1
                         and unit_schedule["cert_out"] == str(out_dir / "certify-results.jsonl"),
                         f"unit schedule is usable by certify_all: {unit_schedule['summary']}")
            certify_argv = unit_schedule["jobs"][0]["certify_argv"]
            bad += check("--recipe-version" in certify_argv
                         and benchmark_pipeline_plan.STRONG_RECIPE_VERSION in certify_argv
                         and "--skip-bracket" in certify_argv
                         and "--pin-agreed-state" in certify_argv,
                         f"benchmark pipeline schedules the strong recipe: {certify_argv}")
            bad += check(argv_value(certify_argv, "--timeout") == "60"
                         and argv_value(certify_argv, "--run-timeout") == "60"
                         and argv_value(certify_argv, "--memlimit-gib") == "8",
                         f"base unit schedule carries first-attempt budget: {certify_argv}")
            next_unit_schedule = json.loads(Path(doc["outputs"]["next_unit_schedule"]).read_text())
            next_argv = next_unit_schedule["jobs"][0]["certify_argv"]
            bad += check(argv_value(next_argv, "--timeout") == "60"
                         and argv_value(next_argv, "--run-timeout") == "60"
                         and argv_value(next_argv, "--memlimit-gib") == "8",
                         f"campaign next schedule carries inner attempt budget: {next_argv}")
            return bad

        patches = [
            (benchmark_pipeline_plan.target_manifest, "build_manifest",
             lambda *_args, **_kwargs: target_doc()),
            (benchmark_pipeline_plan.subject_unit_manifest, "build_manifest",
             lambda call_args: ok_manifest(call_args.ast_cache_root, tmp)),
            (benchmark_pipeline_plan.ast_preheat_schedule, "build_schedule",
             lambda *_args, **_kwargs: {
                 "schema": "veriput-ast-preheat-schedule/v1",
                 "summary": {
                     "jobs": 0,
                 },
                 "jobs": [],
             }),
        ]
        return with_patches(patches, run)


def test_cert_ready_pipeline_selects_strong_stage4_command():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        out_dir = tmp / "out"
        cert = tmp / "certify.jsonl"
        cert.write_text("")

        def run():
            a = args(tmp, out_dir=str(out_dir))
            a.cert_jsonl = [str(cert)]
            doc = benchmark_pipeline_plan.build_pipeline(a)
            next_action = doc["summary"]["next_action"]
            stage4 = doc["next_runs"]["stage4_put"]
            bad = 0
            bad += check(next_action["action"] == "certification-ready-for-put"
                         and next_action["command_kind"] == "stage4_put",
                         f"cert-ready path selects Stage 4: {next_action}")
            bad += check("--strong-recipe" in stage4["runner_argv"]
                         and "--cert" in stage4["runner_argv"]
                         and str(cert) in stage4["runner_argv"],
                         f"Stage 4 command uses the strong PUT recipe: {stage4}")
            bad += check(stage4["out_root"] == str(out_dir / "put-roundtrip")
                         and stage4["timeout_s"] == 600.0
                         and stage4["memlimit_gb"] == 8
                         and stage4["forge_timeout_s"] == 300,
                         f"Stage 4 command carries explicit budget/output: {stage4}")
            bad += check(next_action["runner_cmd"] == stage4["runner_cmd"]
                         and " --strong-recipe " in f" {next_action['runner_cmd']} ",
                         f"next_action exposes the copyable Stage 4 command: "
                         f"{next_action}")
            return bad

        patches = [
            (benchmark_pipeline_plan.target_manifest, "build_manifest",
             lambda *_args, **_kwargs: target_doc()),
            (benchmark_pipeline_plan.subject_unit_manifest, "build_manifest",
             lambda call_args: ok_manifest(call_args.ast_cache_root, tmp)),
            (benchmark_pipeline_plan.ast_preheat_schedule, "build_schedule",
             lambda *_args, **_kwargs: {
                 "schema": "veriput-ast-preheat-schedule/v1",
                 "summary": {
                     "jobs": 0,
                 },
                 "jobs": [],
             }),
            (benchmark_pipeline_plan.certify_result_summary, "summarize",
             lambda *_args, **_kwargs: {
                 "schema": "veriput-certify-result-summary/v1",
                 "gate": {
                     "status": "ready",
                 },
                 "summary": {
                     "certified_path_rate": 1.0,
                 },
             }),
        ]
        return with_patches(patches, run)


def test_pipeline_refuses_dataset_or_results_write_paths():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = tmp / "VeriPUT"
        protected_cases = [
            ("ast cache", lambda a: setattr(
                a, "ast_cache_root", str(root / "Results" / "ast-cache"))),
            ("planner out dir", lambda a: setattr(
                a, "out_dir", str(root / "Datasets" / "planner"))),
            ("cert out", lambda a: setattr(
                a, "cert_out", str(root / "Results" / "certify.jsonl"))),
            ("AST journal", lambda a: setattr(
                a, "next_ast_preheat_journal", str(root / "Results" / "ast.jsonl"))),
            ("unit journal", lambda a: setattr(
                a, "next_journal", str(root / "Results" / "unit.jsonl"))),
            ("PUT out root", lambda a: setattr(
                a, "put_out_root", str(root / "Results" / "put"))),
        ]
        bad = 0
        for label, mutate in protected_cases:
            a = args(tmp)
            mutate(a)
            try:
                benchmark_pipeline_plan.build_pipeline(a)
            except benchmark_pipeline_plan.PipelineError as exc:
                bad += check("must not be under" in str(exc),
                             f"{label} under Dataset/Results is refused: {exc}")
            else:
                bad += check(False, f"{label} under Dataset/Results was accepted")
        return bad


TESTS = [
    test_missing_ast_pipeline_recommends_preheat_without_writes,
    test_ready_pipeline_writes_requested_docs_and_selects_campaign,
    test_cert_ready_pipeline_selects_strong_stage4_command,
    test_pipeline_refuses_dataset_or_results_write_paths,
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
