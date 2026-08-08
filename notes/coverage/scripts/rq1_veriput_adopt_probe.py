#!/usr/bin/env python3
"""Adopt manually verified VeriPUT probe artifacts into an RQ1 journal row.

This script does not run ESBMC or Forge.  It only reuses the RQ1 runner's
artifact summarizers to build the same row shape that `results_all.py` reads
from `Results/RQ1/VeriPUT/<dataset>/results.jsonl`.
"""

import argparse
import json
import socket
import time
from collections import Counter
from pathlib import Path

from rq1_veriput_run import (_append_jsonl, _strength_quality, _utc_now,
                             summarize_certification, summarize_put_artifacts,
                             write_dataset_manifest)
from veriput_recipe import STRONG_RECIPE_VERSION


def _merge_counts(key, docs):
    out = Counter()
    for doc in docs:
        out.update(doc.get(key) or {})
    return dict(sorted(out.items()))


def _combine_put_summaries(put_roots):
    docs = [summarize_put_artifacts(path) for path in put_roots]
    out = {
        "raw": sum(doc["raw"] for doc in docs),
        "valid": sum(doc["valid"] for doc in docs),
        "put_raw": sum(doc["put_raw"] for doc in docs),
        "put_valid": sum(doc["put_valid"] for doc in docs),
        "concrete_raw": sum(doc["concrete_raw"] for doc in docs),
        "concrete_valid": sum(doc["concrete_valid"] for doc in docs),
        "summary_paths": sum((doc["summary_paths"] for doc in docs), []),
        "raw_tests": sum((doc["raw_tests"] for doc in docs), []),
        "valid_tests": sum((doc["valid_tests"] for doc in docs), []),
        "put_json_count": sum(doc["put_json_count"] for doc in docs),
        "stage4_generation_wall_s": round(
            sum(doc["stage4_generation_wall_s"] for doc in docs), 3),
        "stage4_emission_wall_s": round(
            sum(doc["stage4_emission_wall_s"] for doc in docs), 3),
        "foundry_replay_wall_s": round(
            sum(doc["foundry_replay_wall_s"] for doc in docs), 3),
        "put_all_wall_s": round(sum(doc["put_all_wall_s"] for doc in docs), 3),
        "oracle_class_counts": _merge_counts("oracle_class_counts", docs),
        "oracle_class_combo_counts": _merge_counts(
            "oracle_class_combo_counts", docs),
        "assertion_oracles": sum((doc["assertion_oracles"] for doc in docs), []),
    }
    out.update(_strength_quality(out))
    return out


def _combine_cert_summaries(cert_paths):
    docs = [summarize_certification(path) for path in cert_paths]
    return {
        "rows": sum(doc["rows"] for doc in docs),
        "bucket_counts": _merge_counts("bucket_counts", docs),
        "exit_counts": _merge_counts("exit_counts", docs),
        "witness_counts": _merge_counts("witness_counts", docs),
        "certified_regions": sum(doc["certified_regions"] for doc in docs),
        "not_certified_regions": sum(
            doc["not_certified_regions"] for doc in docs),
        "timed_out_units": sorted({
            unit for doc in docs for unit in doc.get("timed_out_units", [])
        }),
        "oom_units": sorted({
            unit for doc in docs for unit in doc.get("oom_units", [])
        }),
        "driver_refusal_tags": _merge_counts("driver_refusal_tags", docs),
    }


def _cert_units(cert_paths):
    units = []
    for path in cert_paths:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            unit = row.get("unit")
            if unit and unit not in units:
                units.append(unit)
    return units


def build_row(args):
    put_roots = [Path(path).expanduser().resolve() for path in args.put_root]
    cert_paths = [Path(path).expanduser().resolve() for path in args.cert]
    put_summary = _combine_put_summaries(put_roots)
    cert_summary = _combine_cert_summaries(cert_paths)
    units_attempted = _cert_units(cert_paths)
    stage2_wall_s = round(sum(
        _cert_wall(path) for path in cert_paths), 3)
    stage4_wall_s = put_summary["put_all_wall_s"]
    generation_wall_s = round(
        stage2_wall_s + put_summary["stage4_generation_wall_s"], 3)
    now = round(time.time(), 3)
    status = "ok" if put_summary["raw"] > 0 else "no-output"
    return {
        "key": f"gen:veriput:{args.subject_id}",
        "stage": "gen_veriput",
        "schema": "veriput-rq1-result-row/v1",
        "ts": now,
        "generated_at": _utc_now(),
        "host": socket.gethostname(),
        "n_concurrent": args.jobs,
        "mem_budget_mb": args.memlimit_gib * 1024,
        "tool_timeout_s": args.timeout,
        "esbmc_run_timeout_s": args.esbmc_run_timeout,
        "stage2_unit_timeout_cap_s": 0,
        "cleared_concrete_fallbacks_enabled": True,
        "timeout_concrete_fallbacks_enabled": True,
        "no_output_stage2_stop_s": 0,
        "no_candidate_stage2_unit_stop_n": 0,
        "max_consecutive_no_candidate_units": 0,
        "zero_output_stage4_stop_s": 0,
        "min_concrete_only_stage4_s": 0,
        "skip_concrete_only_after_put_valid": 0,
        "low_budget_concrete_only_stage4_skips": [],
        "low_budget_concrete_only_stage4_skip_count": 0,
        "put_saturated_concrete_only_stage4_skips": [],
        "put_saturated_concrete_only_stage4_skip_count": 0,
        "early_stop_reason": None,
        "wall_cap_s": args.timeout + args.wrapper_grace,
        "status": status,
        "completion_status": "adopted-probe",
        "budget_exhausted": False,
        "reason": args.reason,
        "subject_id": args.subject_id,
        "benchmark": args.benchmark,
        "dataset": args.dataset,
        "contract": args.contract,
        "raw": put_summary["raw"],
        "valid": put_summary["valid"],
        "put_raw": put_summary["put_raw"],
        "put_valid": put_summary["put_valid"],
        "concrete_raw": put_summary["concrete_raw"],
        "concrete_valid": put_summary["concrete_valid"],
        "quality_bucket": put_summary["quality_bucket"],
        "valid_put_with_R1": put_summary["valid_put_with_R1"],
        "valid_put_with_R2": put_summary["valid_put_with_R2"],
        "valid_put_with_R1_or_R2": put_summary["valid_put_with_R1_or_R2"],
        "valid_put_without_R1R2": put_summary["valid_put_without_R1R2"],
        "raw_tests": put_summary["raw_tests"],
        "valid_tests": put_summary["valid_tests"],
        "oracle_class_counts": put_summary["oracle_class_counts"],
        "oracle_class_combo_counts": put_summary["oracle_class_combo_counts"],
        "assertion_oracles": put_summary["assertion_oracles"],
        "put_json_count": put_summary["put_json_count"],
        "cert_bucket_counts": cert_summary["bucket_counts"],
        "cert_exit_counts": cert_summary["exit_counts"],
        "cert_witness_counts": cert_summary["witness_counts"],
        "cert_timed_out_units": cert_summary["timed_out_units"],
        "cert_oom_units": cert_summary["oom_units"],
        "units_attempted": units_attempted,
        "units_scheduled": len(units_attempted),
        "generation_wall_s": generation_wall_s,
        "stage2_wall_s": stage2_wall_s,
        "stage4_wall_s": stage4_wall_s,
        "stage4_generation_wall_s": put_summary["stage4_generation_wall_s"],
        "stage4_emission_wall_s": put_summary["stage4_emission_wall_s"],
        "foundry_replay_wall_s": put_summary["foundry_replay_wall_s"],
        "put_all_wall_s": put_summary["put_all_wall_s"],
        "foundry_replay_outside_generation_timeout": True,
        "wall": round(stage2_wall_s + stage4_wall_s, 3),
        "wall_total_s": round(stage2_wall_s + stage4_wall_s, 3),
        "maxrss_mb": 0.0,
        "artifact_root": args.artifact_root or str(put_roots[0].parent),
        "result_json": args.result_json,
        "cert_jsonl": ",".join(str(path) for path in cert_paths),
        "put_summary_paths": put_summary["summary_paths"],
        "raw_artifacts_retained": True,
        "valid_artifacts_retained": True,
        "recipe_version": STRONG_RECIPE_VERSION,
        "adopted_probe": True,
    }


def _write_result_json(path, row):
    if not path:
        return
    result_path = Path(path).expanduser().resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(row, sort_keys=True, indent=2) + "\n")


def _cert_wall(path):
    total = 0.0
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return total
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            total += float(row.get("wall_s") or 0.0)
        except (TypeError, ValueError):
            pass
    return total


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--put-root", action="append", required=True)
    parser.add_argument("--cert", action="append", required=True)
    parser.add_argument("--journal")
    parser.add_argument("--result-root",
                        default="/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
    parser.add_argument("--artifact-root")
    parser.add_argument("--result-json")
    parser.add_argument("--reason", default="adopted manually verified probe artifacts")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--esbmc-run-timeout", type=int, default=600)
    parser.add_argument("--wrapper-grace", type=int, default=60)
    parser.add_argument("--memlimit-gib", type=int, default=12)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--append", action="store_true",
                        help="append the generated row to the dataset journal")
    args = parser.parse_args(argv)

    row = build_row(args)
    print(json.dumps(row, sort_keys=True))
    if args.append:
        journal = Path(args.journal or Path(args.result_root) / args.dataset
                       / "results.jsonl").expanduser().resolve()
        if args.artifact_root:
            Path(args.artifact_root).expanduser().resolve().mkdir(
                parents=True, exist_ok=True)
        _write_result_json(args.result_json, row)
        _append_jsonl(journal, row)
        write_dataset_manifest(Path(args.result_root).expanduser().resolve(),
                               args.dataset, journal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
