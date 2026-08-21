#!/usr/bin/env python3
"""Plan VeriPUT unit certification attempts under the campaign budget.

This script is a read-only controller for the agreed three-attempt policy:
60s/8GiB, 120s/8GiB, then 600s/10GiB.  It consumes a base
`veriput-unit-schedule/v1` plus zero or more `unit_schedule_run.py` JSONL
journals, classifies every job by latest status and attempt count, and can emit
the next retry schedule without invoking solc, Forge, fuzzing, ESBMC, or
certification jobs.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
UNIT_SCHEDULE_RUN = SCRIPT_DIR / "unit_schedule_run.py"
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import unit_schedule  # noqa: E402
from solidity_ast_dependencies import (  # noqa: E402
    path_function_declaration_id,
    unit_env_dependencies,
    unit_mapping_slot_accesses,
)

DEFAULT_POLICY = (
    {
        "attempt": 1,
        "timeout_s": 60.0,
        "memlimit_gb": 8.0,
    },
    {
        "attempt": 2,
        "timeout_s": 120.0,
        "memlimit_gb": 8.0,
    },
    {
        "attempt": 3,
        "timeout_s": 600.0,
        "memlimit_gb": 10.0,
    },
)
CERTIFY_TIMEOUT_GRACE_S = 10.0
RUNNER_TIMEOUT_GRACE_S = 5.0
DEFAULT_RETRY_REFINE_ROUNDS = "2"
BOUNDED_HOLDS_NO_WITNESS_REASON = "bounded-holds no witness"
GATED_UNIT_DEPTH_NO_WITNESS_REASON = "gated unit depth no witness"
PATH_COVERAGE_NO_CLAIMS_REASON = "path coverage no claims reached solver"
FOCUS_FUNCTION_MATCHED_NONE_REASON = "focus function matched no path-coverage unit"
PROBE_CLAIM_EXPLOSION_REASON = "probe enumeration claim explosion"
PROBE_CLAIM_EXPLOSION_TAG = "path-coverage-probe-claim-explosion"
PROBE_GOAL_CAP_REASON = "path coverage probe goal cap"
PROBE_GOAL_CAP_TAG = "path-coverage-probe-goal-cap"
AST_FOCUS_RETRY_REASONS = {
    "certified path rate below threshold",
    "no certified regions",
    "no generalisable coordinate",
    "focus function matched no path-coverage unit",
    "bounded-holds no witness",
}
AST_RETRY_MAX_ENV_COORDS = 2
AST_RETRY_MAX_SLOT_COORDS = 4

# CE scheduling is deliberately allow-listed.  These are the three cases for
# which a 60-second CE collection already exists.  A missing case policy must
# never be interpreted as evidence for an arbitrary contract or unit.
CE_CASE_POLICIES = {
    ("stress243", "ERC-3643__ERC-3643__TREXImplementationAuthority"):
    {
        "witness_units": ("getContracts",),
        "coordinate_required": True,
        "stop_units": (),
        "stop_case_without_candidate": False,
    },
    ("stress243", "balancer__balancer-v3-monorepo__DynamicWeightedLPOracle"):
    {
        # computeTVLGivenPrices consumed the full CE budget without a witness.
        # getRoundData has a single ABI coordinate and five scalar returns,
        # which is the narrowest fresh candidate left in this target.
        "witness_units": (),
        "initial_coordinate_units": ("getRoundData",),
        "coordinate_required": False,
        "stop_units": ("computeTVLGivenPrices",),
        "stop_case_without_candidate": False,
    },
    ("bugfix124", "pop_058_PuttyV2"):
    {
        # The observed CE and all completed certification rows are no-witness.
        # Do not infer a witness for the eight units that were never reached.
        "witness_units": (),
        "coordinate_required": True,
        "stop_units": (),
        "stop_case_without_candidate": True,
    },
}
CE_RUNTIME_COORDINATE_PREFIXES = (
    "block.",
    "tx.",
    "msg.data",
    "msg.sig",
)
CE_ENV_COORDINATES = {"msg.sender", "msg.value", "tx.origin"}


class CampaignError(ValueError):
    """The schedule, journals, or requested attempt cannot be planned."""


def _load_json(path: str) -> dict:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CampaignError(f"{path} is not valid JSON: {exc}") from exc


def _read_journal(path: str) -> tuple[list[dict], int]:
    p = Path(path)
    rows = []
    bad_lines = 0
    if not p.exists():
        return rows, bad_lines
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        rows.append(row)
    return rows, bad_lines


def _read_jsonl(path: str) -> tuple[list[dict], int]:
    return _read_journal(path)


def _job_case_key(job: dict) -> tuple[str, str]:
    return (str(job.get("benchmark") or ""),
            str(job.get("subject_id") or ""))


def _ce_row_matches_job(row: dict, job: dict) -> bool:
    if str(row.get("unit") or "") != str(job.get("unit") or ""):
        return False
    benchmark, subject_id = _job_case_key(job)
    subject = row.get("subject") or {}
    if isinstance(subject, dict):
        if (str(subject.get("benchmark") or "") == benchmark
                and str(subject.get("subject_id") or "") == subject_id):
            return True
    raw_benchmark = str(row.get("benchmark") or "")
    return raw_benchmark in (benchmark, f"{benchmark}__{subject_id}")


def _ce_coordinate_name(name: object) -> str:
    return str(name).strip()


def _ce_coordinate_values(rows: list[dict]) -> tuple[set[str], dict[str, set[str]]]:
    explicit = set()
    values = defaultdict(set)

    def add_value(name: object, value: object = "<explicit>") -> None:
        coord = _ce_coordinate_name(name)
        if not coord or coord.startswith(CE_RUNTIME_COORDINATE_PREFIXES):
            return
        explicit.add(coord)
        try:
            encoded = json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            encoded = repr(value)
        values[coord].add(encoded)

    for row in rows:
        for field in ("coords", "env_coords", "slot_coords"):
            raw = row.get(field)
            if isinstance(raw, str):
                add_value(raw)
            elif isinstance(raw, (list, tuple, set)):
                for coord in raw:
                    add_value(coord)
        journal = row.get("partial_witness_journal") or {}
        if not isinstance(journal, dict):
            continue
        for path in journal.get("paths") or []:
            if not isinstance(path, dict):
                continue
            ce = path.get("ce") or {}
            if not isinstance(ce, dict):
                continue
            for name, value in ce.items():
                add_value(name, value)
    return explicit, values


def _ce_observation(rows: list[dict]) -> dict:
    """Summarise CE evidence without treating CE as a proof."""

    explicit, coordinate_values = _ce_coordinate_values(rows)
    varying = sorted(name for name, values in coordinate_values.items()
                     if len(values) > 1)
    witness_paths = 0
    witness_count = 0
    certified_regions = 0
    for row in rows:
        progress = row.get("generalise_progress") or {}
        journal = row.get("partial_witness_journal") or {}
        if isinstance(row.get("witnessed"), int):
            witness_paths = max(witness_paths, int(row["witnessed"]))
        if isinstance(progress, dict):
            witness_paths = max(witness_paths,
                                int(progress.get("witnessed") or 0))
        if isinstance(journal, dict):
            witness_paths = max(witness_paths,
                                int(journal.get("path_count") or 0))
            witness_count = max(witness_count,
                                int(journal.get("witness_count") or 0))
        certified_regions = max(certified_regions,
                                len(row.get("certified") or {}))
    has_witness = witness_paths > 0 or witness_count > 0
    has_coordinate = bool(explicit or varying)
    return {
        "rows": len(rows),
        "latest_bucket": rows[-1].get("bucket") if rows else None,
        "latest_driver_tag": ((rows[-1].get("driver_diagnostic") or {}).get("tag")
                               if rows else None),
        "witnessed_paths": witness_paths,
        "witness_count": witness_count,
        "has_witness": has_witness,
        "explicit_coordinates": sorted(explicit),
        "varying_coordinates": varying,
        "has_coordinate_evidence": has_coordinate,
        "certified_regions": certified_regions,
        # A CE can nominate a candidate, but only a certified region can add
        # theory coverage.  Keep these counters separate and auditable.
        "provisional_candidate": bool(has_witness and has_coordinate),
        "theory_increment": (1 if certified_regions and has_coordinate else 0),
    }


def _ce_rows_for_job(rows: list[dict], job: dict) -> list[dict]:
    return [row for row in rows if _ce_row_matches_job(row, job)]


def _append_ce_coordinates(job: dict, observation: dict) -> None:
    """Carry only observed environment coordinates into the next retry."""

    coordinates = [name for name in observation.get("varying_coordinates") or []
                   if name in CE_ENV_COORDINATES]
    if not coordinates:
        return

    def rewrite(argv: list[str]) -> list[str]:
        for coordinate in coordinates:
            argv = _with_repeated_argv_value(argv, "--env-coord", coordinate)
        return argv

    _apply_argv_rewrite(job, rewrite)


def _apply_ce_scheduler_gate(pending_by_attempt: dict[int, list[dict]],
                             ce_rows: list[dict],
                             all_jobs: list[dict] | None = None) -> dict:
    """Filter the allow-listed cases using their actual 60-second CE evidence."""

    if not ce_rows:
        return {
            "enabled": False,
            "skipped_jobs": [],
            "selected_jobs": [],
            "theory_increment": 0,
            "provisional_candidates": 0,
        }

    skipped = []
    selected = []
    observations = {}
    for job in all_jobs or []:
        case_key = _job_case_key(job)
        if case_key not in CE_CASE_POLICIES:
            continue
        matching_rows = _ce_rows_for_job(ce_rows, job)
        if not matching_rows:
            continue
        case_label = f"{case_key[0]}/{case_key[1]}"
        unit = str(job.get("unit") or "")
        observations.setdefault(case_label, {})[unit] = _ce_observation(matching_rows)
    for attempt, jobs in list(pending_by_attempt.items()):
        kept = []
        for job in jobs:
            case_key = _job_case_key(job)
            policy = CE_CASE_POLICIES.get(case_key)
            if policy is None:
                kept.append(job)
                continue

            matching_rows = _ce_rows_for_job(ce_rows, job)
            observation = _ce_observation(matching_rows)
            case_label = f"{case_key[0]}/{case_key[1]}"
            unit = str(job.get("unit") or "")
            observations.setdefault(case_label, {})[unit] = observation
            unit_info = job.get("unit_info") or {}
            decision = "defer-no-case-evidence"
            reason = "unit is outside the case-specific CE allow-list"
            keep = False

            if unit in set(policy.get("stop_units") or ()):
                decision = "stop-no-witness"
                reason = "60-second CE ended without a path witness"
            elif unit in set(policy.get("witness_units") or ()):
                if (observation["has_witness"]
                        and (not policy.get("coordinate_required")
                             or observation["has_coordinate_evidence"])):
                    keep = True
                    decision = "select-witness-coordinate-candidate"
                    reason = "CE witness and coordinate variation are recorded"
                    _append_ce_coordinates(job, observation)
                elif observation["has_witness"]:
                    decision = "stop-witness-without-coordinate"
                    reason = "witness exists, but no varying coordinate was observed"
                else:
                    decision = "stop-no-witness"
                    reason = "allow-listed unit has no CE witness"
            elif unit in set(policy.get("initial_coordinate_units") or ()):
                parameters = int(unit_info.get("parameter_count") or 0)
                returns = int(unit_info.get("return_count") or 0)
                if parameters > 0 and returns > 0:
                    keep = True
                    decision = "select-abi-coordinate-candidate"
                    reason = "target AST records an ABI input and observable return"
                else:
                    decision = "stop-no-usable-coordinate"
                    reason = "initial candidate has no ABI input/observable return pair"
            elif policy.get("stop_case_without_candidate"):
                decision = "stop-case-no-witness"
                reason = "case has no observed witness; do not infer unvisited units"

            job = copy.deepcopy(job)
            job["ce_scheduler"] = {
                "schema": "veriput-ce-scheduler-evidence/v1",
                "decision": decision,
                "reason": reason,
                "observation": observation,
                "theory_increment": int(observation["theory_increment"]
                                         if keep else 0),
                "theory_credit_status": (
                    "certified-region-required" if keep else "not-submitted"),
            }
            if keep:
                kept.append(job)
                selected.append({
                    "attempt": attempt,
                    "case": case_label,
                    "unit": unit,
                    "decision": decision,
                    "reason": reason,
                    "theory_increment": job["ce_scheduler"]["theory_increment"],
                    "provisional_candidate": observation["provisional_candidate"],
                })
            else:
                skipped.append({
                    "attempt": attempt,
                    "case": case_label,
                    "unit": unit,
                    "decision": decision,
                    "reason": reason,
                    "theory_increment": 0,
                })
        pending_by_attempt[attempt] = kept

    return {
        "enabled": True,
        "observations": observations,
        "skipped_jobs": skipped,
        "selected_jobs": selected,
        "theory_increment": sum(item["theory_increment"] for item in selected),
        "provisional_candidates": sum(
            1 for item in selected
            if item.get("provisional_candidate")),
    }


def _row_attempt(row: dict, fallback_attempt: int, policy: dict[int, dict]) -> int:
    raw = row.get("campaign_attempt")
    try:
        attempt = int(raw)
    except (TypeError, ValueError):
        attempt = fallback_attempt
    if attempt not in policy:
        return fallback_attempt
    return attempt


def _load_schedule(path: str) -> dict:
    doc = _load_json(path)
    if doc.get("schema") != "veriput-unit-schedule/v1":
        raise CampaignError(f"unsupported schedule schema {doc.get('schema')!r}")
    return doc


def _cert_subject(row: dict) -> str:
    return row.get("benchmark") or row.get("poc") or "<unknown>"


def _cert_quality_keys(row: dict) -> set[tuple]:
    unit = row.get("unit") or "<none>"
    keys = {(_cert_subject(row), unit)}
    subject = row.get("subject") or {}
    if isinstance(subject, dict):
        pop = subject.get("benchmark")
        subject_id = subject.get("subject_id")
        benchmark_key = subject.get("benchmark_key")
        if pop and subject_id:
            keys.add((pop, subject_id, unit))
        if benchmark_key:
            keys.add((benchmark_key, unit))
    return keys


def _progress_bucket(row: dict) -> str:
    progress = row.get("generalise_progress") or {}
    if not isinstance(progress, dict) or not progress:
        return "<missing-progress>"
    stage = progress.get("stage") or "<missing-stage>"
    if isinstance(stage, str) and stage.startswith("outer-round"):
        return f"{stage}:{progress.get('round_kind') or '<unknown-round>'}"
    if isinstance(stage, str) and stage.startswith("certify-query"):
        return f"certification:{stage}"
    return str(stage)


def _is_slice_excluded_reason(reason: str) -> bool:
    return "EXCLUDED FROM THE SLICE by the pins" in str(reason or "")


def _is_method_unsupported_reason(reason: str) -> bool:
    text = str(reason or "")
    if "STATICALLY INSEPARABLE" not in text:
        return False
    lowered = text.lower()
    return ("hash" in lowered or "nondet" in lowered or "uncontrolled decision" in lowered
            or "__esbmc_hash_result" in text or "extcall" in lowered
            or "external-call" in lowered)


def _is_bounded_holds_no_witness(row: dict) -> bool:
    progress = row.get("generalise_progress") or {}
    if not isinstance(progress, dict):
        return False
    if progress.get("stage") != "no-witness":
        return False
    text = str(progress.get("reason") or "")
    return "bounded-holds" in text


def _is_named_obstacle_no_witness(row: dict) -> bool:
    if row.get("empty_witness_verdict") != "REFUSED":
        return False
    text = str(row.get("empty_witness_reason") or "")
    return "named-obstacle" in text


def _is_gated_unit_depth_no_witness(row: dict) -> bool:
    if not _is_named_obstacle_no_witness(row):
        return False
    obstacles = row.get("empty_witness_obstacles") or {}
    if isinstance(obstacles, dict):
        named = obstacles.get("named_obstacle") or {}
        if isinstance(named, dict):
            details = named.get("details") or {}
            if isinstance(details, dict):
                for detail in details:
                    if "unit still calls another UNIT's own body unexpanded" in detail:
                        return True
    text = str(row.get("empty_witness_reason") or "")
    return "unit still calls another UNIT's own body unexpanded" in text


def _driver_diagnostic_tag(row: dict) -> str:
    diagnostic = row.get("driver_diagnostic") or {}
    if not isinstance(diagnostic, dict):
        return ""
    return str(diagnostic.get("tag") or "")


def _driver_stopped_before_enumeration(row: dict) -> bool:
    progress = row.get("generalise_progress") or {}
    if not isinstance(progress, dict):
        return False
    if progress.get("stage") != "started":
        return False
    if row.get("bucket") != "NO-WITNESS-UNKNOWN":
        return False
    if row.get("partial_witness_journal"):
        return False
    return row.get("exit") not in (None, 0, 124)


def _cert_quality_by_unit(paths: list[str], min_certified_path_rate: float) -> tuple[dict, int]:
    latest = {}
    bad_lines = 0
    for path in paths:
        rows, bad = _read_jsonl(path)
        bad_lines += bad
        for row in rows:
            for key in _cert_quality_keys(row):
                latest[(key, row.get("path_function"))] = row

    by_unit = defaultdict(list)
    for (key, _path_function), row in latest.items():
        by_unit[key].append(row)

    quality = {}
    for key, rows in by_unit.items():
        witnessed = 0
        eligible_witnessed = 0
        retry_eligible_witnessed = 0
        certified = 0
        not_certified = 0
        slice_excluded = 0
        method_unsupported = 0
        regions = 0
        partial_journal_paths = 0
        partial_journal_witnesses = 0
        partial_claims_decided = 0
        partial_claims_total = 0
        no_verdict_paths = 0
        no_verdict_progress = Counter()
        progress_buckets = Counter()
        buckets = Counter()
        no_coordinate = False
        preflight_refused = False
        named_obstacle_no_witness = False
        path_cov_no_claims_reached = False
        path_cov_no_claims_diagnostic = None
        focus_function_matched_none = False
        focus_function_matched_none_diagnostic = None
        probe_claim_explosion = False
        probe_claim_explosion_diagnostic = None
        probe_goal_cap = False
        probe_goal_cap_diagnostic = None
        pre_enumeration_stop = False
        bounded_holds_no_witness = False
        gated_unit_depth_no_witness = False
        for row in rows:
            buckets[row.get("bucket") or "<missing-bucket>"] += 1
            progress_bucket = _progress_bucket(row)
            progress_buckets[progress_bucket] += 1
            if _driver_diagnostic_tag(row) == "path-coverage-no-claims-reached-solver":
                path_cov_no_claims_reached = True
                path_cov_no_claims_diagnostic = row.get("driver_diagnostic")
            if _driver_diagnostic_tag(row) == "focus-function-matched-none":
                focus_function_matched_none = True
                focus_function_matched_none_diagnostic = row.get(
                    "driver_diagnostic")
            if _driver_diagnostic_tag(row) == PROBE_CLAIM_EXPLOSION_TAG:
                probe_claim_explosion = True
                probe_claim_explosion_diagnostic = row.get("driver_diagnostic")
            if _driver_diagnostic_tag(row) == PROBE_GOAL_CAP_TAG:
                probe_goal_cap = True
                probe_goal_cap_diagnostic = row.get("driver_diagnostic")
            if _driver_stopped_before_enumeration(row):
                pre_enumeration_stop = True
            if _is_bounded_holds_no_witness(row):
                bounded_holds_no_witness = True
            if row.get("bucket") == "NO-COORDINATE" or row.get("no_coordinate_reason"):
                no_coordinate = True
            empty_reason = str(row.get("empty_witness_reason") or "")
            if _is_named_obstacle_no_witness(row):
                named_obstacle_no_witness = True
            if _is_gated_unit_depth_no_witness(row):
                gated_unit_depth_no_witness = True
            if "direct self-recursive function/helper" in empty_reason:
                preflight_refused = True
            c = row.get("certified") or {}
            n = row.get("not_certified") or {}
            c_count = len(c) if isinstance(c, dict) else 0
            n_count = len(n) if isinstance(n, dict) else 0
            slice_excluded_count = 0
            method_unsupported_count = 0
            if isinstance(n, dict):
                slice_excluded_count = sum(
                    1 for reason in n.values() if _is_slice_excluded_reason(reason))
                method_unsupported_count = sum(
                    1 for reason in n.values() if _is_method_unsupported_reason(reason))
            regions += c_count
            if isinstance(row.get("witnessed"), int):
                witnessed_row = max(0, row["witnessed"])
                witnessed += witnessed_row
                eligible_witnessed += max(0, witnessed_row - slice_excluded_count)
                retry_eligible_witnessed += max(
                    0, witnessed_row - slice_excluded_count - method_unsupported_count)
                certified += c_count
                not_certified += n_count
                slice_excluded += slice_excluded_count
                method_unsupported += method_unsupported_count
                gap = max(0, witnessed_row - c_count - n_count)
                no_verdict_paths += gap
                if gap:
                    no_verdict_progress[progress_bucket] += gap
            journal = row.get("partial_witness_journal") or {}
            if isinstance(journal, dict):
                partial_journal_paths += int(journal.get("path_count") or 0)
                partial_journal_witnesses += int(journal.get("witness_count") or 0)
                partial_claims_decided += int(journal.get("claims_decided") or 0)
                partial_claims_total += int(journal.get("claims_total") or 0)
        raw_rate = (certified / witnessed) if witnessed else (1.0 if regions else 0.0)
        slice_adjusted_rate = (
            certified / eligible_witnessed if eligible_witnessed else
            (1.0 if regions else 0.0))
        rate = (
            certified / retry_eligible_witnessed if retry_eligible_witnessed else
            (1.0 if regions else 0.0))
        strong = regions > 0 and rate >= min_certified_path_rate
        reason = ""
        certification_gap = any(
            key.startswith("certification:") for key in no_verdict_progress)
        refinement_gap = any(
            key.startswith("outer-round") for key in no_verdict_progress)
        if no_verdict_paths and certification_gap:
            reason = "certification-stage no verdict"
        elif no_verdict_paths and refinement_gap:
            reason = "refinement-stage no verdict"
        elif not regions and partial_journal_paths:
            reason = "partial witness journal only"
        elif not regions and bounded_holds_no_witness:
            reason = BOUNDED_HOLDS_NO_WITNESS_REASON
        elif not regions and gated_unit_depth_no_witness:
            reason = GATED_UNIT_DEPTH_NO_WITNESS_REASON
        elif not regions and path_cov_no_claims_reached:
            reason = PATH_COVERAGE_NO_CLAIMS_REASON
        elif not regions and focus_function_matched_none:
            reason = FOCUS_FUNCTION_MATCHED_NONE_REASON
        elif not regions and probe_goal_cap:
            reason = PROBE_GOAL_CAP_REASON
        elif not regions and probe_claim_explosion:
            reason = PROBE_CLAIM_EXPLOSION_REASON
        elif not regions:
            reason = "no certified regions"
        elif rate < min_certified_path_rate:
            reason = "certified path rate below threshold"
        non_retryable_reason = ""
        if not regions and no_coordinate:
            non_retryable_reason = "no generalisable coordinate"
        elif (not regions and named_obstacle_no_witness
              and not gated_unit_depth_no_witness):
            non_retryable_reason = "named obstacle no witness"
        elif not regions and preflight_refused:
            non_retryable_reason = "witness preflight refused"
        elif (not regions and pre_enumeration_stop
              and not path_cov_no_claims_reached
              and not focus_function_matched_none and not probe_goal_cap
              and not probe_claim_explosion):
            non_retryable_reason = "driver stopped before enumeration"
        quality[key] = {
            "strong": strong,
            "retryable": not non_retryable_reason,
            "non_retryable_reason": non_retryable_reason,
            "reason": reason,
            "rows": len(rows),
            "witnessed_paths": witnessed,
            "eligible_witnessed_paths": eligible_witnessed,
            "retry_eligible_witnessed_paths": retry_eligible_witnessed,
            "certified_paths": certified,
            "not_certified_paths": not_certified,
            "slice_excluded_paths": slice_excluded,
            "method_unsupported_paths": method_unsupported,
            "no_verdict_paths": no_verdict_paths,
            "certified_regions": regions,
            "raw_certified_path_rate": raw_rate,
            "slice_adjusted_certified_path_rate": slice_adjusted_rate,
            "certified_path_rate": rate,
            "progress_buckets": dict(sorted(progress_buckets.items())),
            "no_verdict_progress_paths": dict(sorted(no_verdict_progress.items())),
            "partial_journal_paths": partial_journal_paths,
            "partial_journal_witnesses": partial_journal_witnesses,
            "partial_claims_decided": partial_claims_decided,
            "partial_claims_total": partial_claims_total,
            "bucket_rows": dict(sorted(buckets.items())),
        }
        retry_diagnostic = (
            path_cov_no_claims_diagnostic
            or focus_function_matched_none_diagnostic
            or probe_goal_cap_diagnostic
            or probe_claim_explosion_diagnostic)
        if isinstance(retry_diagnostic, dict):
            quality[key]["driver_diagnostic"] = retry_diagnostic
    return quality, bad_lines


def _job_cert_quality(job: dict, cert_quality: dict) -> dict | None:
    cert_key = (job.get("benchmark") or job.get("poc") or "<unknown>",
                job.get("subject_id") or "<unknown>",
                job.get("unit") or "<none>")
    legacy_cert_key = (job.get("benchmark") or job.get("poc") or "<unknown>",
                       job.get("unit") or "<none>")
    return cert_quality.get(cert_key) or cert_quality.get(legacy_cert_key)


def _policy_by_attempt() -> dict[int, dict]:
    return {item["attempt"]: dict(item) for item in DEFAULT_POLICY}


def _selected_attempt(pending_by_attempt: dict[int, list[dict]], requested: int) -> int | None:
    if requested:
        if requested not in _policy_by_attempt():
            raise CampaignError("--attempt must be 1, 2, or 3")
        return requested
    for attempt in sorted(_policy_by_attempt()):
        if pending_by_attempt.get(attempt):
            return attempt
    return None


def _runner_argv(schedule_path: str,
                 journal_path: str,
                 attempt_cfg: dict,
                 *,
                 jobs: int = 1,
                 stop_on_failure: bool = False,
                 dry_run: bool = False) -> list[str]:
    runner_timeout_s = (
        float(attempt_cfg["timeout_s"]) + CERTIFY_TIMEOUT_GRACE_S +
        RUNNER_TIMEOUT_GRACE_S)
    argv = [
        sys.executable,
        str(UNIT_SCHEDULE_RUN),
        schedule_path,
        "--journal",
        journal_path,
        "--timeout",
        str(runner_timeout_s),
        "--memlimit-gb",
        str(attempt_cfg["memlimit_gb"]),
        "--jobs",
        str(jobs),
    ]
    if stop_on_failure:
        argv.append("--stop-on-failure")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _cmd(argv: list[str]) -> str:
    return shlex.join(str(arg) for arg in argv)


def _argv_value(argv: list[str], flag: str) -> str:
    try:
        idx = argv.index(flag)
    except ValueError:
        return ""
    if idx + 1 >= len(argv):
        return ""
    return argv[idx + 1]


def _with_argv_value(argv: list[str], flag: str, value: str) -> list[str]:
    rewritten = list(argv)
    try:
        idx = rewritten.index(flag)
    except ValueError:
        rewritten.extend([flag, value])
        return rewritten
    if idx + 1 >= len(rewritten):
        rewritten.append(value)
    else:
        rewritten[idx + 1] = value
    return rewritten


def _without_argv_flag(argv: list[str], flag: str, *, has_value: bool) -> list[str]:
    rewritten = []
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg == flag:
            skip_next = has_value
            continue
        rewritten.append(arg)
    return rewritten


def _with_esbmc_arg(argv: list[str], value: str) -> list[str]:
    prefix = "--esbmc-arg="
    if any(arg == "--esbmc-arg" for arg in argv):
        for idx, arg in enumerate(argv[:-1]):
            if arg == "--esbmc-arg" and argv[idx + 1] == value:
                return list(argv)
    if any(arg == prefix + value for arg in argv):
        return list(argv)
    return list(argv) + [prefix + value]


def _with_repeated_argv_value(argv: list[str], flag: str, value: str) -> list[str]:
    rewritten = [str(arg) for arg in argv]
    for idx, arg in enumerate(rewritten[:-1]):
        if arg == flag and rewritten[idx + 1] == value:
            return rewritten
    return rewritten + [flag, value]


def _extract_job_ast_path(job: dict) -> str:
    subject = job.get("subject") or {}
    if not isinstance(subject, dict):
        subject = {}
    target = job.get("target") or {}
    if not isinstance(target, dict):
        target = {}
    for candidate in (
            ((job.get("ast") or {}).get("path")
             if isinstance(job.get("ast"), dict) else None),
            ((target.get("ast") or {}).get("path")
             if isinstance(target.get("ast"), dict) else None),
            subject.get("solast"),
            target.get("solast")):
        if candidate:
            return str(candidate)
    argv = [str(arg) for arg in job.get("certify_argv") or []]
    ast_cache_root = _argv_value(argv, "--ast-cache-root")
    if ast_cache_root and job.get("benchmark") and job.get("subject_id"):
        bench = str(job.get("benchmark"))
        subject_id = str(job.get("subject_id"))
        return str(Path(ast_cache_root) / bench /
                   f"{bench}__{subject_id}" / "flat.sol.solast")
    return ""


def _append_ast_focus_retry_coords(item: dict, quality: dict) -> None:
    ast_path = _extract_job_ast_path(item)
    contract = str(item.get("contract") or "")
    unit = str(item.get("unit") or "")
    path_function = item.get("path_function")
    declaration_id = path_function_declaration_id(path_function)
    if not ast_path or not contract or not unit:
        return

    evidence = []
    env_coords = []
    slots = []
    env, env_evidence = unit_env_dependencies(
        ast_path, contract, unit, declaration_id=declaration_id)
    if env:
        env_coords = [
            coord for coord in env
            if coord in ("msg.sender", "msg.value", "tx.origin")
        ][:AST_RETRY_MAX_ENV_COORDS]
        evidence.extend(env_evidence or [])
    slot_accesses, slot_evidence = unit_mapping_slot_accesses(
        ast_path, contract, unit, declaration_id=declaration_id,
        access_mode="read")
    slot_access_mode = "read"
    if not slot_accesses:
        slot_accesses, slot_evidence = unit_mapping_slot_accesses(
            ast_path, contract, unit, declaration_id=declaration_id,
            access_mode="all")
        slot_access_mode = "all"
    if slot_accesses:
        for name, keys in slot_accesses:
            if not keys:
                continue
            slots.append("state." + str(name) +
                         "".join(f"[{key}]" for key in keys))
            if len(slots) >= AST_RETRY_MAX_SLOT_COORDS:
                break
        evidence.extend(slot_evidence or [])
    if not env_coords and not slots:
        return

    def rewrite(argv: list[str]) -> list[str]:
        for coord in env_coords:
            argv = _with_repeated_argv_value(argv, "--env-coord", coord)
        for slot in slots:
            argv = _with_repeated_argv_value(argv, "--slot-coord", slot)
        return argv

    _apply_argv_rewrite(item, rewrite)
    quality["retry_ast_focus"] = {
        "policy": "a23-ast-dependency-path-focus",
        "ast_path": ast_path,
        "contract": contract,
        "unit": unit,
        "path_function": path_function,
        "declaration_id": declaration_id,
        "env_coords": env_coords,
        "slot_coord": slots,
        "slot_access_mode": slot_access_mode if slots else None,
        "evidence": evidence[:12],
    }
    _mark_retry_quality(item, quality, "ast-coordinate-put-retry", 0)


def _attempt_out_path(out_path: str, attempt: int) -> str:
    if not out_path or attempt <= 1:
        return out_path
    p = Path(out_path)
    stem = p.stem
    suffix = p.suffix
    replacement = f"-a{attempt}"
    if re.search(r"-a[0-9]+$", stem):
        stem = re.sub(r"-a[0-9]+$", replacement, stem)
    else:
        stem = stem + replacement
    return str(p.with_name(stem + suffix))


def _apply_argv_rewrite(item: dict, rewrite) -> None:
    item["certify_argv"] = rewrite(
        [str(arg) for arg in item.get("certify_argv") or []])
    if "dry_run_argv" in item:
        item["dry_run_argv"] = rewrite(
            [str(arg) for arg in item.get("dry_run_argv") or []])


def _mark_retry_quality(item: dict, quality: dict, policy: str,
                        rank: int) -> None:
    quality["retry_quality_policy"] = policy
    quality["retry_quality_rank"] = rank
    item["retry_quality_policy"] = policy
    item["retry_quality_rank"] = rank


def _set_retry_refine_rounds(item: dict, refine_rounds: str) -> None:
    _apply_argv_rewrite(
        item, lambda argv: _with_argv_value(argv, "--refine-rounds",
                                           refine_rounds))


def _disable_probe_rewrite(argv: list[str]) -> list[str]:
    argv = _with_argv_value(argv, "--probe-witnesses", "0")
    argv = _without_argv_flag(argv, "--probe-ladder", has_value=False)
    return _without_argv_flag(argv, "--probe-ladder-budget", has_value=True)


def _copy_retry_diagnostic_fields(quality: dict, keys: tuple[str, ...]) -> None:
    diagnostic = quality.get("driver_diagnostic") or {}
    if not isinstance(diagnostic, dict):
        return
    for key in keys:
        if key in diagnostic:
            quality[f"retry_observed_{key}"] = diagnostic[key]


def _apply_path_coverage_no_claims_retry(item: dict, quality: dict) -> None:
    def rewrite(argv: list[str]) -> list[str]:
        argv = _disable_probe_rewrite(argv)
        return _with_argv_value(argv, "--refine-rounds",
                                DEFAULT_RETRY_REFINE_ROUNDS)

    _apply_argv_rewrite(item, rewrite)
    quality["retry_strategy"] = "direct-enumeration-after-no-claims"
    _mark_retry_quality(item, quality, "claim-enumeration-retry", 1)
    quality["retry_probe_witnesses"] = 0
    quality["retry_probe_ladder"] = False
    quality["retry_refine_rounds"] = int(DEFAULT_RETRY_REFINE_ROUNDS)
    quality["retry_reason"] = (
        "the previous coverage run emitted path claims but none reached the "
        "solver, which is recoverable by avoiding the probe pre-pass and "
        "letting the retry enumerate complete-path claims directly")


def _apply_focus_function_matched_none_retry(item: dict, quality: dict) -> None:
    def rewrite(argv: list[str]) -> list[str]:
        argv = _disable_probe_rewrite(argv)
        argv = _with_argv_value(argv, "--scope", "whole")
        return _with_argv_value(argv, "--refine-rounds",
                                DEFAULT_RETRY_REFINE_ROUNDS)

    _apply_argv_rewrite(item, rewrite)
    diagnostic = quality.get("driver_diagnostic") or {}
    if isinstance(diagnostic, dict):
        for key in ("focus_function", "available_units", "available_unit_count"):
            if key in diagnostic:
                quality[f"retry_observed_{key}"] = diagnostic[key]
    quality["retry_strategy"] = "whole-scope-after-focus-miss"
    _mark_retry_quality(item, quality, "whole-scope-coordinate-discovery", 1)
    quality["retry_scope"] = "whole"
    quality["retry_probe_witnesses"] = 0
    quality["retry_probe_ladder"] = False
    quality["retry_refine_rounds"] = int(DEFAULT_RETRY_REFINE_ROUNDS)
    quality["retry_reason"] = (
        "the previous path-coverage run accepted the requested focus function "
        "but enumerated no matching path-coverage unit; retry with whole-scope "
        "enumeration so the driver can discover the available path functions "
        "instead of repeating the same empty focus")


def _apply_probe_claim_explosion_retry(item: dict, quality: dict) -> None:
    def rewrite(argv: list[str]) -> list[str]:
        argv = _disable_probe_rewrite(argv)
        return _with_argv_value(argv, "--refine-rounds",
                                DEFAULT_RETRY_REFINE_ROUNDS)

    _apply_argv_rewrite(item, rewrite)
    quality["retry_strategy"] = "direct-enumeration-no-probe"
    _mark_retry_quality(item, quality, "direct-enumeration-no-probe", 2)
    quality["retry_probe_witnesses"] = 0
    quality["retry_probe_ladder"] = False
    quality["retry_refine_rounds"] = int(DEFAULT_RETRY_REFINE_ROUNDS)
    _copy_retry_diagnostic_fields(
        quality, ("probe_claims", "branch_arms", "physical_exits",
                  "complete_path_denominator", "path_cov_max_goals"))
    quality["retry_reason"] = (
        "the previous enumeration timed out after ESBMC expanded path probes "
        "into an exit-by-branch claim product; disable path probes on the "
        "retry so ESBMC enumerates complete-path claims directly instead of "
        "rebuilding the same product")


def _apply_partial_journal_retry(item: dict, quality: dict) -> None:
    _set_retry_refine_rounds(item, "1")
    quality["retry_strategy"] = "finish-partial-certification"
    _mark_retry_quality(item, quality, "finish-partial-certification", 3)
    quality["retry_refine_rounds"] = 1
    quality["retry_reason"] = (
        "the previous run left only a partial witness journal and no certified "
        "regions; spend the retry on finishing certification from the same "
        "bounded path space before opening additional refinement rounds")


def _apply_gated_unit_depth_retry(item: dict, quality: dict) -> None:
    def rewrite(argv: list[str]) -> list[str]:
        argv = _with_argv_value(argv, "--probe-witnesses", "0")
        argv = _without_argv_flag(argv, "--probe-ladder", has_value=False)
        argv = _without_argv_flag(argv, "--probe-ladder-budget", has_value=True)
        argv = _with_argv_value(argv, "--refine-rounds",
                                DEFAULT_RETRY_REFINE_ROUNDS)
        argv = _with_argv_value(argv, "--max-tx", "2")
        return _with_esbmc_arg(argv, "--unwind=8")

    _apply_argv_rewrite(item, rewrite)
    quality["retry_strategy"] = "deepen-internal-call-expansion"
    _mark_retry_quality(item, quality, "deepen-internal-call-expansion", 2)
    quality["retry_unwind"] = 8
    quality["retry_max_tx"] = 2
    quality["retry_probe_witnesses"] = 0
    quality["retry_probe_ladder"] = False
    quality["retry_refine_rounds"] = int(DEFAULT_RETRY_REFINE_ROUNDS)
    quality["retry_reason"] = (
        "the previous enumeration refused every claim because the focused unit "
        "still called another unit's external-entry body through a depth-bound "
        "residual call; raise ESBMC's unwind bound so the internal call can be "
        "expanded into a gate-free copy, disable path probes to avoid the "
        "exit-by-branch product, and allow one preparatory transaction before "
        "certification")


def _apply_retry_strategy(item: dict) -> None:
    quality = item.get("certification_quality")
    if not isinstance(quality, dict):
        return
    reason = quality.get("reason")
    if reason == "certification-stage no verdict":
        refine_rounds = "1"
        strategy = "certification-first"
        retry_reason = (
            "prior witnessed paths reached certification without a final verdict; "
            "spend the retry budget on certification before another refinement round")
    elif reason == "refinement-stage no verdict":
        refine_rounds = "1"
        strategy = "single-refine-certification-first"
        retry_reason = (
            "prior witnessed paths timed out during refinement; run only one "
            "linear refinement round before certification so the retry can "
            "produce fully bounded regions without spending the whole budget "
            "on refinement")
    elif (reason == "partial witness journal only"
          and not quality.get("witnessed_paths")
          and not quality.get("not_certified_paths")):
        _apply_partial_journal_retry(item, quality)
        return
    elif reason == BOUNDED_HOLDS_NO_WITNESS_REASON:
        _apply_argv_rewrite(
            item, lambda argv: _with_argv_value(
                _with_argv_value(argv, "--max-tx", "2"),
                "--refine-rounds",
                DEFAULT_RETRY_REFINE_ROUNDS))
        _append_ast_focus_retry_coords(item, quality)
        quality["retry_strategy"] = "deepen-witness-search"
        _mark_retry_quality(item, quality, "deepen-witness-search", 2)
        quality["retry_max_tx"] = 2
        quality["retry_refine_rounds"] = int(DEFAULT_RETRY_REFINE_ROUNDS)
        quality["retry_reason"] = (
            "the previous focused single-transaction enumeration decided every "
            "claim as bounded-holds and produced no path witness; keep the "
            "same dispatcher alphabet but allow one additional transaction "
            "before spending more certification budget")
        return
    elif reason == GATED_UNIT_DEPTH_NO_WITNESS_REASON:
        _apply_gated_unit_depth_retry(item, quality)
        return
    elif reason == PATH_COVERAGE_NO_CLAIMS_REASON:
        _apply_path_coverage_no_claims_retry(item, quality)
        return
    elif reason == FOCUS_FUNCTION_MATCHED_NONE_REASON:
        _apply_focus_function_matched_none_retry(item, quality)
        _append_ast_focus_retry_coords(item, quality)
        return
    elif reason == PROBE_GOAL_CAP_REASON:
        _apply_probe_claim_explosion_retry(item, quality)
        return
    elif reason == PROBE_CLAIM_EXPLOSION_REASON:
        _apply_probe_claim_explosion_retry(item, quality)
        return
    else:
        _set_retry_refine_rounds(item, DEFAULT_RETRY_REFINE_ROUNDS)
        if reason in AST_FOCUS_RETRY_REASONS:
            _append_ast_focus_retry_coords(item, quality)
        return

    _set_retry_refine_rounds(item, refine_rounds)

    quality["retry_strategy"] = strategy
    _mark_retry_quality(item, quality, strategy, 3)
    quality["retry_refine_rounds"] = int(refine_rounds)
    quality["retry_reason"] = retry_reason
    if reason in AST_FOCUS_RETRY_REASONS:
        _append_ast_focus_retry_coords(item, quality)


def _attempt_budgeted_jobs(jobs: list[dict], attempt_cfg: dict | None) -> list[dict]:
    if not attempt_cfg:
        return [copy.deepcopy(job) for job in jobs]
    attempt = int(attempt_cfg["attempt"])
    run_timeout_s = int(attempt_cfg["timeout_s"])
    certify_timeout_s = int(run_timeout_s + CERTIFY_TIMEOUT_GRACE_S)
    memlimit_gib = int(attempt_cfg["memlimit_gb"])
    budgeted = []
    for job in jobs:
        item = copy.deepcopy(job)
        out_path = _argv_value([str(arg) for arg in item.get("certify_argv") or []], "--out")
        retry_out_path = _attempt_out_path(out_path, attempt)
        workdir = unit_schedule.default_workdir_root(retry_out_path,
                                                     timeout_s=certify_timeout_s,
                                                     run_timeout_s=run_timeout_s,
                                                     memlimit_gib=memlimit_gib,
                                                     attempt=attempt)
        item["certify_argv"] = unit_schedule.budgeted_certify_argv(
            [str(arg) for arg in item.get("certify_argv") or []],
            timeout_s=certify_timeout_s,
            run_timeout_s=run_timeout_s,
            memlimit_gib=memlimit_gib,
            workdir=workdir)
        if retry_out_path:
            item["certify_argv"] = _with_argv_value(
                item["certify_argv"], "--out", retry_out_path)
        if "dry_run_argv" in item:
            dry = unit_schedule.budgeted_certify_argv(
                [str(arg) for arg in item.get("dry_run_argv") or []],
                timeout_s=certify_timeout_s,
                run_timeout_s=run_timeout_s,
                memlimit_gib=memlimit_gib,
                workdir=workdir)
            if retry_out_path:
                dry = _with_argv_value(dry, "--out", retry_out_path)
            if "--dry-run" not in dry:
                dry.append("--dry-run")
            item["dry_run_argv"] = dry
        item["certification_budget"] = {
            "timeout_s": certify_timeout_s,
            "run_timeout_s": run_timeout_s,
            "memlimit_gib": memlimit_gib,
            "workdir": workdir,
            "out": retry_out_path or None,
        }
        _apply_retry_strategy(item)
        budgeted.append(item)
    return budgeted


def _schedule_for_attempt(base_schedule: dict, selected_jobs: list[dict], attempt_cfg: dict | None,
                          source_journals: list[str],
                          source_ce_jsonls: list[str] | None = None) -> dict:
    attempt = (attempt_cfg or {}).get("attempt")
    run_timeout_s = (attempt_cfg or {}).get("timeout_s")
    certify_timeout_s = (
        int(run_timeout_s + CERTIFY_TIMEOUT_GRACE_S) if run_timeout_s else None)
    memlimit_gb = (attempt_cfg or {}).get("memlimit_gb")
    budgeted_jobs = _attempt_budgeted_jobs(selected_jobs, attempt_cfg)
    by_benchmark = Counter(job.get("benchmark") for job in budgeted_jobs)
    by_priority = Counter(str(job.get("priority", "<missing>")) for job in budgeted_jobs)
    certify_out = (budgeted_jobs[0].get("certification_budget") or {}).get("out") \
        if budgeted_jobs else None
    return {
        "schema": "veriput-unit-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schema": base_schedule.get("schema"),
            "schedule_generated_at": base_schedule.get("generated_at"),
            "schedule_source": base_schedule.get("source"),
            "schedule_summary": base_schedule.get("summary"),
            "campaign_policy": "veriput-unit-campaign-policy/v1",
            "campaign_attempt": attempt,
            "campaign_journals": source_journals,
            "campaign_ce_jsonls": source_ce_jsonls or [],
        },
        "shard": base_schedule.get("shard"),
        "limit": base_schedule.get("limit"),
        "cert_out": certify_out or base_schedule.get("cert_out"),
        "workdir": (budgeted_jobs[0].get("certification_budget") or {}).get("workdir")
        if budgeted_jobs else None,
        "summary": {
            "jobs": len(budgeted_jobs),
            "jobs_before_campaign_filter": len(base_schedule.get("jobs") or []),
            "campaign_attempt": attempt,
            "timeout_s": certify_timeout_s,
            "run_timeout_s": run_timeout_s,
            "memlimit_gb": memlimit_gb,
            "certify_timeout_s": certify_timeout_s,
            "certify_run_timeout_s": int(run_timeout_s) if run_timeout_s else None,
            "certify_memlimit_gib": int(memlimit_gb) if memlimit_gb else None,
            "certify_out": certify_out,
            "certify_workdir": (budgeted_jobs[0].get("certification_budget")
                                or {}).get("workdir") if budgeted_jobs else None,
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_priority": dict(sorted(by_priority.items())),
        },
        "skipped_rows": base_schedule.get("skipped_rows") or [],
        "skipped_units": base_schedule.get("skipped_units") or [],
        "no_unit_rows": base_schedule.get("no_unit_rows") or [],
        "duplicate_jobs": base_schedule.get("duplicate_jobs") or [],
        "jobs": budgeted_jobs,
    }


def _ordered_selected_jobs(jobs: list[dict], selection_strategy: str,
                           limit: int) -> tuple[list[dict], int]:
    if selection_strategy not in unit_schedule.SELECTION_STRATEGIES:
        raise CampaignError("--selection-strategy must be one of: " +
                            ", ".join(unit_schedule.SELECTION_STRATEGIES))
    if limit < 0:
        raise CampaignError("--limit must be non-negative")

    def quality_key(job: dict) -> tuple:
        quality = job.get("certification_quality") or {}
        if not isinstance(quality, dict):
            quality = {}
        raw_retry_rank = (
            job.get("retry_quality_rank")
            if job.get("retry_quality_rank") is not None
            else quality.get("retry_quality_rank"))
        try:
            retry_rank = int(raw_retry_rank)
        except (TypeError, ValueError):
            retry_rank = 9
        schedule_rank = job.get("schedule_rank") or {}
        coord = schedule_rank.get("coordinate_first") or [3]
        cheap = schedule_rank.get("cheap_first") or [50, 0, 0]
        put = schedule_rank.get("put_potential_first") or [5]
        try:
            coord_rank = int(coord[0])
        except (TypeError, ValueError, IndexError):
            coord_rank = 3
        try:
            cheap_rank = int(cheap[0])
        except (TypeError, ValueError, IndexError):
            cheap_rank = 50
        try:
            put_rank = int(put[0])
        except (TypeError, ValueError, IndexError):
            put_rank = 5
        reason = str(quality.get("reason") or "")
        no_coordinate_ast_retry = (
            reason == "no generalisable coordinate"
            and bool(quality.get("retry_ast_focus")))
        concrete_only_risk = (
            put_rank >= 5 and coord_rank >= 3
            and not no_coordinate_ast_retry)
        return (
            0 if no_coordinate_ast_retry else 1,
            retry_rank,
            1 if concrete_only_risk else 0,
            put_rank,
            0 if coord_rank <= 1 else 1,
            cheap_rank,
            int(job.get("priority") or 0),
            int(job.get("ordinal") or 0),
        )

    # THE SELECTION STRATEGY HAS TO SURVIVE THE QUALITY SORT. `_select_jobs`
    # interleaves the benchmarks for `round-robin-*`, and this sort then used
    # the schedule ORDINAL as its last tie-break, which put the original order
    # straight back: with five equal-quality jobs, `round-robin-benchmark`
    # with --limit 3 returned two peer182 jobs and one bugfix124 instead of one
    # per benchmark. Rank by the position the strategy chose, and keep the
    # ordinal after it so `priority` (where the strategy returns the jobs
    # unchanged) behaves exactly as before.
    selected_jobs = unit_schedule._select_jobs(list(jobs), selection_strategy)
    selection_index = {id(job): pos for pos, job in enumerate(selected_jobs)}
    # The ordinal is quality_key's LAST element, so it has to be replaced --
    # appending the selection position after it changes nothing.
    ordered = sorted(selected_jobs,
                     key=lambda job:
                     (quality_key(job)[:-1] + (selection_index[id(job)], quality_key(job)[-1])))
    before_limit = len(ordered)
    if limit:
        ordered = ordered[:limit]
    out = []
    for ordinal, job in enumerate(ordered):
        item = copy.deepcopy(job)
        item["ordinal"] = ordinal
        out.append(item)
    return out, before_limit


def plan_campaign_for_schedule(schedule: dict,
                               schedule_label: str,
                               *,
                               journal_paths: list[str] | None = None,
                               cert_jsonl_paths: list[str] | None = None,
                               ce_jsonl_paths: list[str] | None = None,
                               min_certified_path_rate: float = 0.70,
                               attempt: int = 0,
                               selection_strategy: str = "priority",
                               limit: int = 0,
                               next_schedule_out: str = "",
                               next_journal: str = "",
                               jobs: int = 1,
                               stop_on_failure: bool = False) -> dict:
    if schedule.get("schema") != "veriput-unit-schedule/v1":
        raise CampaignError(f"unsupported schedule schema {schedule.get('schema')!r}")
    journals = journal_paths or []
    cert_jsonls = cert_jsonl_paths or []
    ce_jsonls = ce_jsonl_paths or []
    cert_quality, bad_cert_lines = _cert_quality_by_unit(cert_jsonls, min_certified_path_rate)
    ce_rows = []
    bad_ce_lines = 0
    for path in ce_jsonls:
        rows, bad = _read_jsonl(path)
        ce_rows.extend(rows)
        bad_ce_lines += bad
    policy = _policy_by_attempt()
    jobs_by_id = {job.get("job_id"): job for job in schedule.get("jobs") or [] if job.get("job_id")}
    latest = {}
    attempts_by_job = defaultdict(set)
    status_attempts = Counter()
    bad_lines = 0
    orphan_rows = 0
    cert_weak = Counter()
    cert_non_retryable = Counter()

    for fallback_attempt, journal in enumerate(journals, start=1):
        rows, bad = _read_journal(journal)
        bad_lines += bad
        for row in rows:
            job_id = row.get("job_id")
            if not job_id:
                continue
            if job_id not in jobs_by_id:
                orphan_rows += 1
                continue
            attempts_by_job[job_id].add(_row_attempt(row, fallback_attempt, policy))
            status_attempts[row.get("status") or "<missing-status>"] += 1
            latest[job_id] = row

    pending_by_attempt = defaultdict(list)
    completed = []
    non_retryable = []
    exhausted = []
    latest_status = Counter()
    by_benchmark_state = defaultdict(Counter)
    by_priority_state = defaultdict(Counter)
    max_attempt = max(policy)

    for job_id, job in jobs_by_id.items():
        latest_row = latest.get(job_id)
        attempts = max(attempts_by_job[job_id], default=0)
        quality = _job_cert_quality(job, cert_quality)
        cert_strong = (not cert_jsonls) or (quality and quality.get("strong"))
        has_completion_source = ((latest_row and latest_row.get("status") == "ok")
                                 or (cert_jsonls and not latest_row and cert_strong))
        if has_completion_source and cert_strong:
            state = "completed-ok" if latest_row else "completed-certified"
            completed.append(job)
            latest_status["ok" if latest_row else "certified-without-runner-journal"] += 1
        elif (cert_jsonls and quality and quality.get("retryable") is False
              and quality.get("non_retryable_reason") == "no generalisable coordinate"
              and attempts < max_attempt):
            state = f"pending-attempt-{attempts + 1}"
            pending_job = copy.deepcopy(job)
            pending_job["certification_quality"] = dict(quality)
            pending_job["certification_quality"]["reason"] = (
                pending_job["certification_quality"].get("reason")
                or "no certified regions")
            pending_job["certification_quality"]["retryable"] = True
            pending_job["certification_quality"]["non_retryable_reason"] = ""
            pending_job["certification_quality"]["retry_reason"] = (
                "the previous row reported no generalisable coordinate, but "
                "target-contract AST dependency analysis may promote env, "
                "mapping-read, or assignment-key slot coordinates for a "
                "stronger PUT/R1/R2 retry before concrete-only fallback")
            pending_job["certification_quality"]["concrete_only_blocked_until"] = (
                "ast-coordinate-retry-exhausted")
            pending_by_attempt[attempts + 1].append(pending_job)
            latest_status[(latest_row or {}).get("status")
                          or "certified-without-runner-journal"] += 1
            cert_weak["no generalisable coordinate: ast dependency retry"] += 1
        elif cert_jsonls and quality and quality.get("retryable") is False:
            state = "non-retryable"
            non_retryable.append(job)
            reason = (
                quality.get("non_retryable_reason") or quality.get("reason")
                or "non-retryable")
            cert_non_retryable[reason] += 1
            latest_status[(latest_row or {}).get("status")
                          or "certified-without-runner-journal"] += 1
        elif attempts >= max_attempt:
            state = "exhausted"
            exhausted.append(job)
            latest_status[(latest_row or {}).get("status") or "never"] += 1
        else:
            next_attempt = attempts + 1
            state = f"pending-attempt-{next_attempt}"
            pending_job = job
            if latest_row and latest_row.get("status") == "ok" and cert_jsonls:
                reason = (quality or {}).get("reason") or "no certification row"
                cert_weak[reason] += 1
                pending_job = copy.deepcopy(job)
                pending_job["certification_quality"] = (
                    dict(quality) if isinstance(quality, dict) else {
                        "strong": False,
                        "reason": reason,
                    })
                pending_job["certification_quality"]["reason"] = reason
            pending_by_attempt[next_attempt].append(pending_job)
            latest_status[(latest_row or {}).get("status") or "never"] += 1
        by_benchmark_state[job.get("benchmark") or "<unknown>"][state] += 1
        by_priority_state[str(job.get("priority", "<missing>"))][state] += 1

    ce_scheduler = _apply_ce_scheduler_gate(pending_by_attempt, ce_rows,
                                             list(jobs_by_id.values()))
    selected = _selected_attempt(pending_by_attempt, attempt)
    selected_jobs_all = list(pending_by_attempt.get(selected, [])) if selected else []
    selected_jobs, selected_jobs_before_limit = _ordered_selected_jobs(
        selected_jobs_all, selection_strategy, limit)
    attempt_cfg = policy.get(selected) if selected else None
    next_schedule = _schedule_for_attempt(schedule, selected_jobs, attempt_cfg, journals,
                                          ce_jsonls)
    if next_schedule_out and next_schedule:
        out = Path(next_schedule_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(next_schedule, indent=2, sort_keys=True) + "\n")

    next_run = None
    if attempt_cfg:
        schedule_arg = next_schedule_out or "<next-schedule.json>"
        journal_arg = next_journal or f"<attempt-{attempt_cfg['attempt']}-journal.jsonl>"
        dry_run_argv = _runner_argv(schedule_arg,
                                    journal_arg,
                                    attempt_cfg,
                                    jobs=jobs,
                                    stop_on_failure=stop_on_failure,
                                    dry_run=True)
        runner_argv = _runner_argv(schedule_arg,
                                   journal_arg,
                                   attempt_cfg,
                                   jobs=jobs,
                                   stop_on_failure=stop_on_failure,
                                   dry_run=False)
        next_run = {
            "attempt":
            attempt_cfg["attempt"],
            "timeout_s":
            attempt_cfg["timeout_s"],
            "certify_timeout_s":
            float(attempt_cfg["timeout_s"]) + CERTIFY_TIMEOUT_GRACE_S,
            "runner_timeout_s":
            (float(attempt_cfg["timeout_s"]) + CERTIFY_TIMEOUT_GRACE_S +
             RUNNER_TIMEOUT_GRACE_S),
            "memlimit_gb":
            attempt_cfg["memlimit_gb"],
            "jobs":
            len(selected_jobs),
            "dry_run_argv":
            dry_run_argv,
            "dry_run_cmd":
            _cmd(dry_run_argv),
            "runner_argv":
            runner_argv,
            "runner_cmd":
            _cmd(runner_argv),
        }

    return {
        "schema": "veriput-unit-campaign-plan/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schedule": schedule_label,
        "journals": journals,
            "cert_jsonls": cert_jsonls,
            "ce_jsonls": ce_jsonls,
        "policy": {
            "schema": "veriput-unit-campaign-policy/v1",
            "attempts": list(DEFAULT_POLICY),
        },
        "summary": {
            "jobs": len(jobs_by_id),
            "completed_ok": len(completed),
            "non_retryable": len(non_retryable),
            "exhausted": len(exhausted),
            "bad_journal_lines": bad_lines,
            "bad_cert_jsonl_lines": bad_cert_lines,
            "bad_ce_jsonl_lines": bad_ce_lines,
            "orphan_journal_rows": orphan_rows,
            "cert_quality_enabled": bool(cert_jsonls),
            "cert_weak": dict(sorted(cert_weak.items())),
            "cert_non_retryable": dict(sorted(cert_non_retryable.items())),
            "status_attempts": dict(sorted(status_attempts.items())),
            "distinct_attempts_max": max((len(value) for value in attempts_by_job.values()),
                                         default=0),
            "latest_status": dict(sorted(latest_status.items())),
            "pending_by_attempt": {
                str(key): len(value)
                for key, value in sorted(pending_by_attempt.items())
            },
            "selected_attempt": selected,
            "selected_jobs": len(selected_jobs),
            "selected_jobs_before_limit": selected_jobs_before_limit,
            "selection_strategy": selection_strategy,
            "selection_limit": limit or None,
            "ce_scheduler": ce_scheduler,
        },
        "by_benchmark_state": {
            bench: dict(sorted(counter.items()))
            for bench, counter in sorted(by_benchmark_state.items())
        },
        "by_priority_state": {
            priority: dict(sorted(counter.items()))
            for priority, counter in sorted(by_priority_state.items())
        },
        "next_run": next_run,
        "next_schedule": next_schedule,
    }


def plan_campaign(schedule_path: str,
                  *,
                  journal_paths: list[str] | None = None,
                  cert_jsonl_paths: list[str] | None = None,
                  ce_jsonl_paths: list[str] | None = None,
                  min_certified_path_rate: float = 0.70,
                  attempt: int = 0,
                  selection_strategy: str = "priority",
                  limit: int = 0,
                  next_schedule_out: str = "",
                  next_journal: str = "",
                  jobs: int = 1,
                  stop_on_failure: bool = False) -> dict:
    schedule = _load_schedule(schedule_path)
    return plan_campaign_for_schedule(schedule,
                                      schedule_path,
                                      journal_paths=journal_paths,
                                      cert_jsonl_paths=cert_jsonl_paths,
                                      ce_jsonl_paths=ce_jsonl_paths,
                                      min_certified_path_rate=min_certified_path_rate,
                                      attempt=attempt,
                                      selection_strategy=selection_strategy,
                                      limit=limit,
                                      next_schedule_out=next_schedule_out,
                                      next_journal=next_journal,
                                      jobs=jobs,
                                      stop_on_failure=stop_on_failure)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schedule", help="base veriput-unit-schedule/v1 JSON")
    ap.add_argument("--journal",
                    action="append",
                    default=[],
                    help="unit_schedule_run.py JSONL journal; repeat in attempt order")
    ap.add_argument("--cert-jsonl",
                    action="append",
                    default=[],
                    help="certify_all.py --out JSONL; when present, runner-ok jobs "
                    "must also meet the certification quality threshold")
    ap.add_argument("--ce-jsonl",
                    action="append",
                    default=[],
                    help="60-second CE collection JSONL; restrict the allow-listed "
                    "cases to witnessed/coordinate-backed units")
    ap.add_argument("--min-certified-path-rate",
                    type=float,
                    default=0.70,
                    help="quality threshold used with --cert-jsonl")
    ap.add_argument("--attempt",
                    type=int,
                    default=0,
                    help="plan this exact attempt, or auto-select the earliest pending attempt")
    ap.add_argument("--selection-strategy",
                    choices=unit_schedule.SELECTION_STRATEGIES,
                    default="priority",
                    help="ordering policy for the selected pending attempt")
    ap.add_argument("--limit",
                    type=int,
                    default=0,
                    help="keep only the first N selected jobs after ordering")
    ap.add_argument("--next-schedule-out",
                    default="",
                    help="write the selected next-attempt schedule here")
    ap.add_argument("--next-journal",
                    default="",
                    help="journal path to include in the suggested runner argv")
    ap.add_argument("--jobs",
                    type=int,
                    default=1,
                    help="worker count to include in the suggested runner argv")
    ap.add_argument("--stop-on-failure",
                    action="store_true",
                    help="include --stop-on-failure in the suggested runner argv")
    ap.add_argument("--out", default="", help="write JSON plan here instead of stdout")
    args = ap.parse_args()
    try:
        doc = plan_campaign(args.schedule,
                            journal_paths=args.journal,
                            cert_jsonl_paths=args.cert_jsonl,
                            ce_jsonl_paths=args.ce_jsonl,
                            min_certified_path_rate=args.min_certified_path_rate,
                            attempt=args.attempt,
                            selection_strategy=args.selection_strategy,
                            limit=args.limit,
                            next_schedule_out=args.next_schedule_out,
                            next_journal=args.next_journal,
                            jobs=args.jobs,
                            stop_on_failure=args.stop_on_failure)
    except (OSError, CampaignError) as exc:
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
