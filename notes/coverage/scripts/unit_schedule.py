#!/usr/bin/env python3
"""Expand a VeriPUT unit manifest into concrete certification jobs.

This script is intentionally read-only with respect to benchmark inputs.  It
does not invoke solc, Forge, fuzzing, or ESBMC.  Its output is an auditable
per-unit schedule that can be inspected before scarce proof attempts are spent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[2]
CERTIFY_ALL = SCRIPT_DIR / "certify_all.py"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT / "scripts"))
from solidity_path_generalise import direct_recursive_helpers_in_unit_closure  # noqa: E402
from veriput_path_guard import ensure_path_not_protected  # noqa: E402
from veriput_recipe import STRONG_RECIPE_VERSION, strong_certify_args  # noqa: E402

DEFAULT_TIMEOUT_S = 60
DEFAULT_RUN_TIMEOUT_S = 60
DEFAULT_MEMLIMIT_GIB = 8
SELECTION_STRATEGIES = ("priority", "round-robin-benchmark", "round-robin-subject")
INITIALIZER_LIKE_UNITS = {
    "init",
    "initialize",
    "setup",
    "setUp",
}
CHEAP_STATE_UNIT_NAMES = {
}
ADMIN_ZERO_INTERFACE_UNIT_NAMES = {
    "acceptOwnership",
    "pause",
    "renounceOwnership",
    "unPause",
    "unpause",
}
ADMIN_SETTER_UNIT_NAMES = {
    "setAddressFrozen",
    "setCompliance",
    "setIdentityRegistry",
    "setName",
    "setOnchainID",
    "setSymbol",
}
ACCESS_CONTROL_MUTATOR_UNIT_NAMES = {
    "addAgent",
    "burn",
    "forcedTransfer",
    "freezePartialTokens",
    "mint",
    "recoveryAddress",
    "removeAgent",
    "unfreezePartialTokens",
}
MODERATE_STATE_UNIT_NAMES = {
    "approve",
    "setApprovalForAll",
    "transferOwnership",
}
EXPENSIVE_UNIT_NAME_FRAGMENTS = (
    "batch",
    "buy",
    "change",
    "claim",
    "create",
    "deposit",
    "execute",
    "flash",
    "safe",
    "sell",
    "upgrade",
    "withdraw",
)
INTERNAL_TARGET_WRAPPER_UNIT_NAMES = {
    "execute",
    "executeBatch",
    "executeTransaction",
}

# These routes are scheduling hints for the initial NO-PATH observations only.
# They never grant theory credit: each replacement still needs a CE and normal
# certification before it can become a valid test or PUT.
NO_PATH_STATIC_ROUTES = {
    ("bugfix124", "acfix_032_CVE_2021_39167"):
    {"failed_unit": "isOperation", "replacement": "execute", "target": "_afterCall"},
    ("bugfix124", "acfix_033_CVE_2021_39168"):
    {"failed_unit": "isOperation", "replacement": "execute", "target": "_afterCall"},
    ("bugfix124", "acfix_3_5_101_ANCHToken"):
    {"failed_unit": "balanceOf", "replacement": "transfer", "target": "_transfer"},
}

BUDGET_VALUE_FLAGS = {
    "--timeout",
    "--run-timeout",
    "--memlimit-gib",
}
REPLACEABLE_VALUE_FLAGS = BUDGET_VALUE_FLAGS | {"--workdir"}


class ScheduleError(ValueError):
    """The input manifest cannot be converted into unit jobs."""


def _route_initial_no_path_unit(subject: dict, units: list[str]) -> tuple[list[str], dict | None]:
    """Put an evidence-backed caller before a known NO-PATH getter."""
    key = (str(subject.get("benchmark") or ""), str(subject.get("subject_id") or ""))
    route = NO_PATH_STATIC_ROUTES.get(key)
    if route is None:
        return units, None
    failed, replacement = route["failed_unit"], route["replacement"]
    if failed not in units or replacement not in units:
        return units, None
    reordered = [replacement]
    reordered.extend(unit for unit in units if unit not in {replacement, failed})
    return reordered, {
        "schema": "veriput-no-path-route/v1",
        "failed_unit": failed,
        "replacement": replacement,
        "target": route["target"],
        "theory_credit": 0,
    }


def _read_json(path: str) -> dict:
    if path == "-":
        text = sys.stdin.read()
        name = "<stdin>"
    else:
        p = Path(path)
        text = p.read_text()
        name = str(p)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScheduleError(f"{name} is not valid JSON: {exc}") from exc


def _parse_shard(text: str):
    if not text:
        return None
    try:
        left, right = text.split("/", 1)
        idx, total = int(left), int(right)
    except (AttributeError, ValueError):
        raise ScheduleError("--shard must be in i/n form")
    if total <= 0 or idx < 0 or idx >= total:
        raise ScheduleError("--shard needs 0 <= i < n")
    return idx, total


def _apply_shard(items, shard):
    if shard is None:
        return items
    idx, total = shard
    return [item for pos, item in enumerate(items) if pos % total == idx]


def _select_jobs(jobs: list[dict], selection_strategy: str) -> list[dict]:
    if selection_strategy not in SELECTION_STRATEGIES:
        raise ScheduleError("--selection-strategy must be one of: " +
                            ", ".join(SELECTION_STRATEGIES))
    if selection_strategy == "priority":
        return list(jobs)

    groups = []
    grouped = {}
    for job in jobs:
        if selection_strategy == "round-robin-benchmark":
            key = (job.get("benchmark") or "<unknown>",)
        else:
            key = (
                job.get("benchmark") or "<unknown>",
                job.get("subject_id") or "<unknown>",
            )
        if key not in grouped:
            grouped[key] = []
            groups.append(key)
        grouped[key].append(job)

    selected = []
    while groups:
        next_groups = []
        for key in groups:
            queue = grouped[key]
            if queue:
                selected.append(queue.pop(0))
            if queue:
                next_groups.append(key)
        groups = next_groups
    return selected


def _validate_budget(name: str, value: int) -> int:
    if value < 0:
        raise ScheduleError(f"{name} must be non-negative")
    return value


def _append_budget(argv: list[str], flag: str, value: int) -> None:
    if value:
        argv.extend([flag, str(value)])


def default_workdir_root(cert_out: str,
                         *,
                         timeout_s: int = DEFAULT_TIMEOUT_S,
                         run_timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
                         memlimit_gib: int = DEFAULT_MEMLIMIT_GIB,
                         attempt: int = 0) -> str:
    suffix = f"t{timeout_s}_r{run_timeout_s}_m{memlimit_gib}"
    if attempt:
        suffix = f"a{attempt}_{suffix}"
    if cert_out:
        return str(Path(cert_out).expanduser().resolve().parent /
                   f"certify-work-{suffix}")
    recipe = STRONG_RECIPE_VERSION.replace("/", "_")
    return f"/tmp/certify_all/{recipe}/{suffix}"


def budgeted_certify_argv(argv: list[str],
                          *,
                          timeout_s: int = 0,
                          run_timeout_s: int = 0,
                          memlimit_gib: int = 0,
                          workdir: str | None = None) -> list[str]:
    """Return argv with a single authoritative certify_all budget."""

    filtered = []
    skip_next = False
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item in REPLACEABLE_VALUE_FLAGS and (item != "--workdir" or workdir is not None):
            skip_next = True
            continue
        filtered.append(item)
    _append_budget(filtered, "--timeout", timeout_s)
    _append_budget(filtered, "--run-timeout", run_timeout_s)
    _append_budget(filtered, "--memlimit-gib", memlimit_gib)
    if workdir:
        filtered.extend(["--workdir", workdir])
    return filtered


def _zero_interface_sender_arm(unit_info: dict | None) -> bool:
    """Promote msg.sender for state-changing units with no ABI coordinates."""

    if not unit_info:
        return False
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    return mutability not in ("view", "pure") and params == 0


def _region_strategy(unit_info: dict | None) -> dict:
    sender_arm = _zero_interface_sender_arm(unit_info)
    return {
        "zero_interface_sender_arm": sender_arm,
        "env_coords": ["msg.sender"] if sender_arm else [],
        "reason": ("state-changing unit has no ABI parameter coordinate"
                   if sender_arm else "shared strong recipe"),
    }


def _sequence_strategy(unit: str, target_hints: set[str]) -> dict:
    if _is_internal_target_wrapper(unit, target_hints):
        return {
            "scope": "whole",
            "max_tx": 2,
            "reason": (
                "internal/private target hint needs a public wrapper sequence "
                "to establish predecessor state before the target wrapper"),
        }
    return {
        "scope": "focus",
        "max_tx": 1,
        "reason": "single focused unit call",
    }


def _certify_argv(subject: dict, unit: str, ast_cache_root: str | None, out_path: str | None,
                  dry_run: bool, *, timeout_s: int, run_timeout_s: int,
                  memlimit_gib: int, workdir: str,
                  unit_info: dict | None = None,
                  target_hints: set[str] | None = None) -> list[str]:
    argv = [
        sys.executable,
        str(CERTIFY_ALL),
        "--subject-dir",
        subject["root"],
        "--subject-benchmark",
        subject["benchmark"],
        "--unit",
        unit,
    ]
    if ast_cache_root:
        argv.extend(["--ast-cache-root", ast_cache_root])
    if out_path:
        argv.extend(["--out", out_path])
    argv.extend(strong_certify_args())
    sequence = _sequence_strategy(unit, target_hints or set())
    if sequence["scope"] != "focus":
        argv.extend(["--scope", sequence["scope"]])
    if int(sequence["max_tx"]) != 1:
        argv.extend(["--max-tx", str(sequence["max_tx"])])
    for coord in _region_strategy(unit_info)["env_coords"]:
        argv.extend(["--env-coord", coord])
    argv = budgeted_certify_argv(argv,
                                 timeout_s=timeout_s,
                                 run_timeout_s=run_timeout_s,
                                 memlimit_gib=memlimit_gib,
                                 workdir=workdir)
    if dry_run:
        argv.append("--dry-run")
    return argv


def _has_internal_target_hint(target_hints: set[str]) -> bool:
    return any(str(name).startswith("_") for name in target_hints)


def _is_internal_target_wrapper(unit: str, target_hints: set[str]) -> bool:
    if not _has_internal_target_hint(target_hints):
        return False
    lower = unit.lower()
    return (unit in INTERNAL_TARGET_WRAPPER_UNIT_NAMES
            or lower.startswith("execute"))


def _unit_priority(unit: str, hinted: set[str], unit_info: dict | None,
                   static_obstacles: list[dict] | None = None) -> tuple[int, str]:
    if static_obstacles:
        return 4, "static-obstacle"
    if unit in hinted:
        if (unit_info and unit not in INITIALIZER_LIKE_UNITS
                and _unit_cost_rank(unit, unit_info)[0] >= 70):
            return 1, "expensive-target-hint"
        return 0, "target-hint"
    if _is_internal_target_wrapper(unit, hinted):
        return 0, "internal-target-wrapper"
    if not unit_info:
        return 2, "enumerated"
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    returns = int(unit_info.get("return_count") or 0)
    if mutability not in ("view", "pure"):
        if unit in INITIALIZER_LIKE_UNITS:
            return 2, "initializer-like"
        if params == 0 and returns == 0:
            return 1, "zero-interface-state-changing"
        return 1, "state-changing"
    if params or returns:
        return 2, "pure/view-with-interface"
    return 3, "zero-arg-view"


def _unit_cost_rank(unit: str, unit_info: dict | None) -> tuple[int, int, int]:
    """Cheap-first order inside the same semantic priority bucket.

    The priority bucket still decides what class of unit comes first.  This
    rank only breaks ties inside that class, where source order often puts
    heavy business methods before simple setters and view helpers.  Under a
    fixed subject budget that can prevent later cheap units from being tried at
    all.
    """
    if not unit_info:
        return (50, 0, 0)
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    returns = int(unit_info.get("return_count") or 0)
    lower = unit.lower()
    if unit in INITIALIZER_LIKE_UNITS:
        tier = 80
    elif mutability in ("view", "pure"):
        tier = 5
    elif unit in ADMIN_ZERO_INTERFACE_UNIT_NAMES and params == 0:
        tier = 65
    elif unit in ADMIN_SETTER_UNIT_NAMES:
        tier = 65
    elif unit in ACCESS_CONTROL_MUTATOR_UNIT_NAMES:
        tier = 68
    elif unit in MODERATE_STATE_UNIT_NAMES:
        tier = 45
    elif unit in CHEAP_STATE_UNIT_NAMES or lower.startswith("set"):
        tier = 10
    elif params == 0 and returns == 0:
        tier = 15
    elif any(fragment in lower for fragment in EXPENSIVE_UNIT_NAME_FRAGMENTS):
        tier = 70
    else:
        tier = 30
    if mutability == "payable":
        tier += 10
    return (tier, params, returns)


def _job_for_unit(row: dict, unit: str, ordinal: int, ast_cache_root: str | None,
                  out_path: str | None, unit_info: dict | None, *,
                  timeout_s: int, run_timeout_s: int, memlimit_gib: int,
                  workdir: str) -> dict:
    subject = dict(row["subject"])
    subject["unit"] = unit
    unit_hints = row.get("unit_hints") or {}
    hinted = set(unit_hints.get("hinted_units") or [])
    target_hints = hinted | set(unit_hints.get("missing_unit_hints") or [])
    static_obstacles = _static_obstacles_for_unit(row, subject, unit)
    priority, reason = _unit_priority(unit, target_hints, unit_info,
                                      static_obstacles)
    return {
        "schema": "veriput-unit-job/v1",
        "job_id": (f"{subject.get('benchmark_key') or subject['subject_id']}__"
                   f"{unit}"),
        "priority": priority,
        "priority_reason": reason,
        "schedule_rank": {
            "cheap_first": list(_unit_cost_rank(unit, unit_info)),
            "ordinal": ordinal,
        },
        "ordinal": ordinal,
        "benchmark": subject["benchmark"],
        "subject_id": subject["subject_id"],
        "contract": subject["contract"],
        "unit": unit,
        "subject": subject,
        "target": row.get("target"),
        "unit_hints": row.get("unit_hints"),
        "unit_info": unit_info,
        "region_strategy": _region_strategy(unit_info),
        "sequence_strategy": _sequence_strategy(unit, target_hints),
        "static_obstacles": static_obstacles,
        "certification_budget": {
            "timeout_s": timeout_s or None,
            "run_timeout_s": run_timeout_s or None,
            "memlimit_gib": memlimit_gib or None,
            "workdir": workdir or None,
        },
        "certify_argv": _certify_argv(subject,
                                      unit,
                                      ast_cache_root,
                                      out_path,
                                      dry_run=False,
                                      timeout_s=timeout_s,
                                      run_timeout_s=run_timeout_s,
                                      memlimit_gib=memlimit_gib,
                                      workdir=workdir,
                                      unit_info=unit_info,
                                      target_hints=target_hints),
        "dry_run_argv": _certify_argv(subject,
                                      unit,
                                      ast_cache_root,
                                      out_path,
                                      dry_run=True,
                                      timeout_s=timeout_s,
                                      run_timeout_s=run_timeout_s,
                                      memlimit_gib=memlimit_gib,
                                      workdir=workdir,
                                      unit_info=unit_info,
                                      target_hints=target_hints),
    }


def _static_obstacles_for_unit(row: dict, subject: dict, unit: str) -> list[dict]:
    ast_path = (
        ((row.get("ast") or {}).get("path"))
        or subject.get("solast")
    )
    if not ast_path:
        return []
    helpers = direct_recursive_helpers_in_unit_closure(
        ast_path, subject.get("contract"), unit)
    if not helpers:
        return []
    return [{
        "tag": "recursive-helper-preflight",
        "reason": (
            "target call closure reaches direct self-recursive helper wrappers"),
        "helpers": helpers,
    }]


def build_schedule(manifest: dict,
                   *,
                   shard: str = "",
                   limit: int = 0,
                   selection_strategy: str = "priority",
                   cert_out: str = "",
                   timeout_s: int = DEFAULT_TIMEOUT_S,
                   run_timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
                   memlimit_gib: int = DEFAULT_MEMLIMIT_GIB,
                   workdir: str = "") -> dict:
    if manifest.get("schema") != "veriput-unit-manifest/v1":
        raise ScheduleError(f"unsupported schema {manifest.get('schema')!r}; expected "
                            "veriput-unit-manifest/v1")
    timeout_s = _validate_budget("--timeout", timeout_s)
    run_timeout_s = _validate_budget("--run-timeout", run_timeout_s)
    memlimit_gib = _validate_budget("--memlimit-gib", memlimit_gib)
    workdir = workdir or default_workdir_root(cert_out,
                                              timeout_s=timeout_s,
                                              run_timeout_s=run_timeout_s,
                                              memlimit_gib=memlimit_gib)

    ast_cache_root = manifest.get("ast_cache_root") or None
    try:
        ensure_path_not_protected("--ast-cache-root", ast_cache_root)
        ensure_path_not_protected("--cert-out", cert_out)
        ensure_path_not_protected("--workdir", workdir)
    except ValueError as exc:
        raise ScheduleError(str(exc)) from exc
    jobs = []
    skipped_rows = []
    duplicate_jobs = []
    no_path_routes = []
    seen_jobs = set()
    for row_pos, row in enumerate(manifest.get("subjects") or []):
        status = row.get("status")
        if status != "ok":
            skipped_rows.append({
                "row": row_pos,
                "status": status,
                "reason": row.get("reason"),
                "subject": row.get("subject"),
                "target": row.get("target"),
            })
            continue
        subject = row.get("subject") or {}
        units = list((row.get("units") or {}).get("units") or [])
        infos = {
            item.get("name"): item
            for item in (row.get("units") or {}).get("unit_info") or []
            if isinstance(item, dict) and item.get("name")
        }
        missing = [
            name for name in ("root", "benchmark", "subject_id", "contract")
            if not subject.get(name)
        ]
        if missing:
            raise ScheduleError(f"ok row {row_pos} subject is missing: {', '.join(missing)}")
        units, route = _route_initial_no_path_unit(subject, units)
        if route is not None:
            no_path_routes.append({"subject": subject, **route})
        for unit in units:
            key = (subject.get("benchmark"), subject.get("subject_id"), unit)
            if key in seen_jobs:
                duplicate_jobs.append({
                    "row": row_pos,
                    "unit": unit,
                    "reason": "duplicate prepared subject unit",
                    "subject": subject,
                    "target": row.get("target"),
                })
                continue
            seen_jobs.add(key)
            jobs.append(_job_for_unit(row, unit, len(jobs), ast_cache_root,
                                      cert_out or None, infos.get(unit),
                                      timeout_s=timeout_s,
                                      run_timeout_s=run_timeout_s,
                                      memlimit_gib=memlimit_gib,
                                      workdir=workdir))

    shard_spec = _parse_shard(shard)
    total_jobs = len(jobs)
    def _job_sort_key(item: dict) -> tuple:
        rank = item.get("schedule_rank", {}).get("cheap_first") or [50, 0, 0]
        tier = rank[0] if rank else 50
        rest = tuple(rank[1:])
        target_wrapper = (
            0 if item.get("priority_reason") == "internal-target-wrapper"
            else 1)
        hinted_tie = (
            0 if item.get("priority_reason") in
            ("target-hint", "internal-target-wrapper",
             "expensive-target-hint") else 1)
        return (item["priority"], target_wrapper, tier, hinted_tie, rest,
                item["ordinal"])

    jobs.sort(key=_job_sort_key)
    jobs = _select_jobs(jobs, selection_strategy)
    jobs = _apply_shard(jobs, shard_spec)
    if limit:
        jobs = jobs[:limit]

    by_benchmark = Counter(job["benchmark"] for job in jobs)
    by_priority = Counter(str(job["priority"]) for job in jobs)
    static_obstacles = Counter(
        obstacle.get("tag") or "<unknown>"
        for job in jobs
        for obstacle in job.get("static_obstacles") or [])
    skipped_by_status = Counter(str(row.get("status") or "<missing>") for row in skipped_rows)
    return {
        "schema": "veriput-unit-schedule/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "schema": manifest.get("schema"),
            "benchmark": manifest.get("benchmark"),
            "target_manifest": manifest.get("target_manifest"),
            "generate_ast": manifest.get("generate_ast"),
            "ast_cache_root": ast_cache_root,
            "summary": manifest.get("summary"),
        },
        "shard": shard or None,
        "limit": limit or None,
        "selection_strategy": selection_strategy,
        "cert_out": cert_out or None,
        "workdir": workdir or None,
        "recipe_version": STRONG_RECIPE_VERSION,
        "certification_budget": {
            "timeout_s": timeout_s or None,
            "run_timeout_s": run_timeout_s or None,
            "memlimit_gib": memlimit_gib or None,
            "workdir": workdir or None,
        },
        "summary": {
            "jobs": len(jobs),
            "jobs_before_shard": total_jobs,
            "subjects": len({(job["benchmark"], job["subject_id"])
                             for job in jobs}),
            "by_benchmark": dict(sorted(by_benchmark.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "static_obstacle_jobs": sum(1 for job in jobs if job.get("static_obstacles")),
            "static_obstacles_by_tag": dict(sorted(static_obstacles.items())),
            "no_path_routes": no_path_routes,
            "skipped_rows": len(skipped_rows),
            "skipped_by_status": dict(sorted(skipped_by_status.items())),
            "duplicate_jobs": len(duplicate_jobs),
        },
        "skipped_rows": skipped_rows,
        "duplicate_jobs": duplicate_jobs,
        "jobs": jobs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", help="veriput-unit-manifest/v1 JSON path, or '-' for stdin")
    ap.add_argument("--shard", default="", help="select job positions i/n after priority sorting")
    ap.add_argument("--limit",
                    type=int,
                    default=0,
                    help="keep only the first N jobs after sharding")
    ap.add_argument("--selection-strategy",
                    choices=SELECTION_STRATEGIES,
                    default="priority",
                    help="unit-job ordering policy before sharding and limit")
    ap.add_argument("--cert-out",
                    default="",
                    help="append this --out path to generated certify_all argv")
    ap.add_argument("--timeout",
                    type=int,
                    default=DEFAULT_TIMEOUT_S,
                    help="certify_all.py unit budget to embed in every job")
    ap.add_argument("--run-timeout",
                    type=int,
                    default=DEFAULT_RUN_TIMEOUT_S,
                    help="certify_all.py per-ESBMC-run budget to embed in every job")
    ap.add_argument("--memlimit-gib",
                    type=int,
                    default=DEFAULT_MEMLIMIT_GIB,
                    help="certify_all.py per-ESBMC memory budget to embed in every job")
    ap.add_argument("--workdir",
                    default="",
                    help="certify_all.py scratch root to embed. Default derives from --cert-out "
                    "and the budget.")
    ap.add_argument("--out", default="", help="write JSON schedule here. Without it, print stdout")
    args = ap.parse_args()
    try:
        manifest = _read_json(args.manifest)
        doc = build_schedule(manifest,
                             shard=args.shard,
                             limit=args.limit,
                             selection_strategy=args.selection_strategy,
                             cert_out=args.cert_out,
                             timeout_s=args.timeout,
                             run_timeout_s=args.run_timeout,
                             memlimit_gib=args.memlimit_gib,
                             workdir=args.workdir)
    except ScheduleError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if args.out:
        try:
            ensure_path_not_protected("--out", args.out)
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        s = doc["summary"]
        print(f"wrote {out}; jobs={s['jobs']} "
              f"skipped_rows={s['skipped_rows']}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
