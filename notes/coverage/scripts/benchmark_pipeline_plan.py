#!/usr/bin/env python3
"""Plan the read-only VeriPUT benchmark pipeline.

This script stitches together the existing benchmark target, unit manifest,
AST-preheat, unit schedule, campaign, and certification-summary controllers.
It never invokes solc, Forge, fuzzing, ESBMC, or certification jobs.  It also
does not write Dataset/Results inputs; optional outputs go only under
``--out-dir``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ast_preheat_schedule  # noqa: E402
import ast_preheat_campaign_plan  # noqa: E402
import certify_result_summary  # noqa: E402
import subject_unit_manifest  # noqa: E402
import target_manifest  # noqa: E402
import unit_campaign_plan  # noqa: E402
import unit_manifest_gate  # noqa: E402
import unit_schedule  # noqa: E402


class PipelineError(ValueError):
    """The requested read-only pipeline cannot be planned."""


def _write_json(out_dir: str, name: str, doc: dict) -> str | None:
    if not out_dir:
        return None
    path = Path(out_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return str(path)


def _unit_manifest_args(args, target_doc: dict):
    return argparse.Namespace(benchmark="",
                              target_manifest=args.target_manifest_label,
                              target_manifest_doc=target_doc,
                              subject_root="",
                              subject_id=[],
                              limit=args.subject_limit,
                              shard=args.subject_shard,
                              generate_ast=False,
                              ast_cache_root=args.ast_cache_root,
                              use_inferred_solc_bin=False,
                              ast_timeout=args.ast_timeout,
                              journal="",
                              resume_journal="")


def _cert_summary(args, unit_schedule_doc: dict, schedule_path: str | None) -> dict | None:
    if not args.cert_jsonl:
        return None
    # summarize() currently takes one JSONL.  Use the first path as the quality
    # gate view; campaign planning still accepts all JSONLs for completion state.
    return certify_result_summary.summarize(args.cert_jsonl[0],
                                            schedule_path=schedule_path or "",
                                            min_certified_path_rate=args.min_certified_path_rate,
                                            sample_limit=args.sample_limit)


def choose_next_action(gate_doc: dict, preheat_doc: dict, unit_sched_doc: dict,
                       campaign_doc: dict, cert_doc: dict | None) -> dict:
    gate_status = gate_doc.get("gate_status")
    preheat_jobs = (preheat_doc.get("summary") or {}).get("jobs", 0)
    unit_jobs = (unit_sched_doc.get("summary") or {}).get("jobs", 0)
    selected_jobs = (campaign_doc.get("summary") or {}).get("selected_jobs", 0)
    cert_gate = (cert_doc or {}).get("gate") or {}

    if cert_gate.get("status") == "ready":
        action = "certification-ready-for-put"
        reason = "certification summary meets the configured strength gate"
    elif gate_status == "blocked" and preheat_jobs:
        action = "preheat-ast"
        reason = "compact AST rows block unit enumeration"
    elif gate_status == "blocked":
        action = "inspect-unit-manifest-blockers"
        reason = "; ".join(gate_doc.get("blockers") or []) or "unit manifest is blocked"
    elif unit_jobs and selected_jobs:
        action = "run-unit-campaign"
        reason = "unit jobs are schedulable under the next campaign attempt"
    elif unit_jobs:
        action = "inspect-campaign-state"
        reason = "unit schedule exists but no next campaign attempt is selected"
    else:
        action = "inspect-prepared-errors"
        reason = "no unit jobs are currently schedulable"

    return {
        "action": action,
        "reason": reason,
        "gate_status": gate_status,
        "preheat_jobs": preheat_jobs,
        "unit_jobs": unit_jobs,
        "selected_campaign_jobs": selected_jobs,
        "certification_gate": cert_gate.get("status"),
    }


def attach_next_action_command(next_action: dict, preheat_campaign_doc: dict,
                               campaign_doc: dict) -> dict:
    """Copy the currently actionable runner command into summary.next_action."""

    if next_action.get("action") == "preheat-ast":
        command_kind = "ast_preheat"
        next_run = preheat_campaign_doc.get("next_run")
    elif next_action.get("action") == "run-unit-campaign":
        command_kind = "unit_campaign"
        next_run = campaign_doc.get("next_run")
    else:
        return next_action

    if not next_run:
        return next_action

    enriched = dict(next_action)
    enriched["command_kind"] = command_kind
    for key in (
        "attempt",
        "batch_size",
        "dry_run_argv",
        "dry_run_cmd",
        "jobs",
        "memlimit_gb",
        "runner_argv",
        "runner_cmd",
        "runner_workers",
        "selected_jobs",
        "timeout_s",
    ):
        if key in next_run:
            enriched[key] = next_run[key]
    return enriched


def build_pipeline(args) -> dict:
    if not args.ast_cache_root:
        raise PipelineError("pass --ast-cache-root; refusing to plan prepared-subject AST writes")

    benchmarks = args.benchmark or list(target_manifest.BENCHMARKS)
    target_doc = target_manifest.build_manifest(Path(args.veriput_root),
                                                benchmarks,
                                                args.stress_scope)
    unit_manifest_doc = subject_unit_manifest.build_manifest(_unit_manifest_args(args, target_doc))
    gate_doc = unit_manifest_gate.build_gate(unit_manifest_doc, sample_limit=args.sample_limit)
    preheat_doc = ast_preheat_schedule.build_schedule(unit_manifest_doc,
                                                      ast_cache_root=args.ast_cache_root,
                                                      ast_timeout=args.ast_timeout,
                                                      shard=args.preheat_shard,
                                                      limit=args.preheat_limit)

    cert_out = args.cert_out
    paths = {}
    paths["target_manifest"] = _write_json(args.out_dir, "target-manifest.json", target_doc)
    paths["unit_manifest"] = _write_json(args.out_dir, "unit-manifest.json", unit_manifest_doc)
    paths["unit_manifest_gate"] = _write_json(args.out_dir, "unit-manifest-gate.json", gate_doc)
    paths["ast_preheat_schedule"] = _write_json(args.out_dir, "ast-preheat-schedule.json",
                                                preheat_doc)

    if args.out_dir and not cert_out:
        cert_out = str(Path(args.out_dir) / "certify-results.jsonl")
    next_ast_schedule_out = ""
    if args.out_dir:
        next_ast_schedule_out = str(Path(args.out_dir) / "next-ast-preheat-schedule.json")
    next_ast_journal = args.next_ast_preheat_journal
    if args.out_dir and not next_ast_journal:
        next_ast_journal = str(Path(args.out_dir) / "ast-preheat-run.jsonl")
    preheat_campaign_doc = ast_preheat_campaign_plan.plan_preheat_for_schedule(
        preheat_doc,
        paths["ast_preheat_schedule"] or "<in-memory ast preheat schedule>",
        journal_paths=args.ast_preheat_journal,
        batch_size=args.ast_preheat_batch_size,
        selection_strategy=args.ast_preheat_selection_strategy,
        max_attempts=args.ast_preheat_max_attempts,
        timeout_s=args.ast_preheat_outer_timeout,
        memlimit_gb=args.ast_preheat_memlimit_gb,
        next_schedule_out=next_ast_schedule_out,
        next_journal=next_ast_journal,
        jobs=args.ast_preheat_jobs,
        stop_on_failure=args.ast_preheat_stop_on_failure)
    if next_ast_schedule_out and Path(next_ast_schedule_out).exists():
        paths["next_ast_preheat_schedule"] = next_ast_schedule_out
    paths["ast_preheat_campaign_plan"] = _write_json(args.out_dir,
                                                     "ast-preheat-campaign-plan.json",
                                                     preheat_campaign_doc)

    unit_sched_doc = unit_schedule.build_schedule(unit_manifest_doc,
                                                  shard=args.unit_shard,
                                                  limit=args.unit_limit,
                                                  cert_out=cert_out)
    paths["unit_schedule"] = _write_json(args.out_dir, "unit-schedule.json", unit_sched_doc)

    next_schedule_out = ""
    if args.out_dir:
        next_schedule_out = str(Path(args.out_dir) / "next-unit-schedule.json")
    next_unit_journal = args.next_journal
    if args.out_dir and not next_unit_journal:
        next_unit_journal = str(Path(args.out_dir) / "unit-run.jsonl")
    campaign_doc = unit_campaign_plan.plan_campaign_for_schedule(
        unit_sched_doc,
        paths["unit_schedule"] or "<in-memory unit schedule>",
        journal_paths=args.journal,
        cert_jsonl_paths=args.cert_jsonl,
        min_certified_path_rate=args.min_certified_path_rate,
        attempt=args.attempt,
        next_schedule_out=next_schedule_out,
        next_journal=next_unit_journal,
        jobs=args.jobs,
        stop_on_failure=args.stop_on_failure)
    if next_schedule_out and Path(next_schedule_out).exists():
        paths["next_unit_schedule"] = next_schedule_out
    paths["unit_campaign_plan"] = _write_json(args.out_dir, "unit-campaign-plan.json",
                                              campaign_doc)

    cert_doc = _cert_summary(args, unit_sched_doc, paths["unit_schedule"])
    if cert_doc:
        paths["certify_result_summary"] = _write_json(args.out_dir,
                                                      "certify-result-summary.json",
                                                      cert_doc)

    next_action = choose_next_action(gate_doc, preheat_doc, unit_sched_doc, campaign_doc, cert_doc)
    next_action = attach_next_action_command(next_action, preheat_campaign_doc, campaign_doc)
    return {
        "schema": "veriput-benchmark-pipeline-plan/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "execution": {
            "runs_solc": False,
            "runs_forge": False,
            "runs_fuzz": False,
            "runs_esbmc": False,
            "writes_dataset_or_results": False,
        },
        "inputs": {
            "veriput_root": str(Path(args.veriput_root).resolve()),
            "benchmarks": benchmarks,
            "stress_scope": args.stress_scope,
            "ast_cache_root": str(Path(args.ast_cache_root).expanduser().resolve()),
            "ast_preheat_journals": args.ast_preheat_journal,
            "journals": args.journal,
            "cert_jsonls": args.cert_jsonl,
            "min_certified_path_rate": args.min_certified_path_rate,
        },
        "outputs": {
            key: value
            for key, value in paths.items()
            if value
        },
        "summary": {
            "targets": (target_doc.get("summary") or {}).get("targets", 0),
            "unit_manifest": unit_manifest_doc.get("summary"),
            "unit_gate": {
                "status": gate_doc.get("gate_status"),
                "blockers": gate_doc.get("blockers"),
                "warnings": gate_doc.get("warnings"),
            },
            "ast_preheat_jobs": (preheat_doc.get("summary") or {}).get("jobs", 0),
            "ast_preheat_campaign": preheat_campaign_doc.get("summary"),
            "unit_jobs": (unit_sched_doc.get("summary") or {}).get("jobs", 0),
            "campaign": campaign_doc.get("summary"),
            "certification_gate": (cert_doc or {}).get("gate"),
            "next_action": next_action,
        },
        "next_run": campaign_doc.get("next_run"),
        "next_runs": {
            "ast_preheat": preheat_campaign_doc.get("next_run"),
            "unit_campaign": campaign_doc.get("next_run"),
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--veriput-root",
                    default=str(target_manifest.DEFAULT_VERIPUT_ROOT),
                    help="root containing Datasets/ and Results/")
    ap.add_argument("--benchmark",
                    action="append",
                    choices=target_manifest.BENCHMARKS + ("stress203",),
                    default=[],
                    help="benchmark to include. Repeatable. Default: all")
    ap.add_argument("--stress-scope",
                    choices=("include", "stateful"),
                    default="include")
    ap.add_argument("--ast-cache-root",
                    required=True,
                    help="external compact-AST cache root to read from and schedule into")
    ap.add_argument("--target-manifest-label",
                    default="<in-memory target manifest>",
                    help="label stored in child docs for the in-memory target manifest")
    ap.add_argument("--subject-limit", type=int, default=0)
    ap.add_argument("--subject-shard", default="")
    ap.add_argument("--ast-timeout", type=float, default=subject_unit_manifest.DEFAULT_AST_TIMEOUT_S)
    ap.add_argument("--preheat-shard", default="")
    ap.add_argument("--preheat-limit", type=int, default=0)
    ap.add_argument("--ast-preheat-journal",
                    action="append",
                    default=[],
                    help="ast_preheat_run.py JSONL journal; repeatable")
    ap.add_argument("--ast-preheat-batch-size",
                    type=int,
                    default=ast_preheat_campaign_plan.DEFAULT_BATCH_SIZE)
    ap.add_argument("--ast-preheat-selection-strategy",
                    choices=ast_preheat_campaign_plan.SELECTION_STRATEGIES,
                    default="priority")
    ap.add_argument("--ast-preheat-max-attempts",
                    type=int,
                    default=ast_preheat_campaign_plan.DEFAULT_MAX_ATTEMPTS)
    ap.add_argument("--ast-preheat-outer-timeout", type=float, default=90.0)
    ap.add_argument("--ast-preheat-memlimit-gb",
                    type=float,
                    default=ast_preheat_campaign_plan.DEFAULT_MEMLIMIT_GB)
    ap.add_argument("--next-ast-preheat-journal", default="")
    ap.add_argument("--ast-preheat-jobs", type=int, default=1)
    ap.add_argument("--ast-preheat-stop-on-failure", action="store_true")
    ap.add_argument("--unit-shard", default="")
    ap.add_argument("--unit-limit", type=int, default=0)
    ap.add_argument("--cert-out", default="", help="certify_all.py JSONL path for unit jobs")
    ap.add_argument("--journal",
                    action="append",
                    default=[],
                    help="unit_schedule_run.py JSONL journal; repeat in attempt order")
    ap.add_argument("--cert-jsonl",
                    action="append",
                    default=[],
                    help="certify_all.py --out JSONL; repeatable for campaign quality")
    ap.add_argument("--min-certified-path-rate", type=float, default=0.70)
    ap.add_argument("--attempt", type=int, default=0)
    ap.add_argument("--next-journal", default="")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--stop-on-failure", action="store_true")
    ap.add_argument("--sample-limit", type=int, default=10)
    ap.add_argument("--out-dir",
                    default="",
                    help="optional directory for child JSON docs. Without it, write only stdout")
    ap.add_argument("--out", default="", help="write pipeline JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        doc = build_pipeline(args)
    except (OSError, PipelineError, target_manifest.TargetManifestError,
            subject_unit_manifest.SubjectError, unit_manifest_gate.GateError,
            ast_preheat_schedule.PreheatScheduleError, unit_schedule.ScheduleError,
            ast_preheat_campaign_plan.PreheatCampaignError, unit_campaign_plan.CampaignError,
            certify_result_summary.SummaryError) as exc:
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
