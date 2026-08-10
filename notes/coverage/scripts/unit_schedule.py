#!/usr/bin/env python3
"""Expand a VeriPUT unit manifest into concrete certification jobs.

This script is intentionally read-only with respect to benchmark inputs.  It
does not invoke solc, Forge, fuzzing, or ESBMC.  Its output is an auditable
per-unit schedule that can be inspected before scarce proof attempts are spent.
"""

from __future__ import annotations

import argparse
import json
import re
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
    "addPoints",
    "adjustAdminAccess",
    "disableController",
    "enableBalanceForwarder",
    "forceTransferOwnership",
    "includeAccount",
    "lowerMinFeeUSDForCampaign",
    "pause",
    "removeAccount",
    "setAllowedBurningAddresses",
    "setBurnPath",
    "setCapacity",
    "setDefaultMevTaxMultiplier",
    "setDeploymentFee",
    "setFee",
    "setFeeRecipient",
    "setFactory",
    "setGovernorAdmin",
    "setGoverned",
    "setImplementationAuthority",
    "setMaxMevSwapFeePercentage",
    "setMetadataService",
    "setMinFeeUSD",
    "setNativeToken",
    "setOwner",
    "setPoolMevTaxMultiplier",
    "setProtocolFeePercentage",
    "setProtocolSwapFeePercentage",
    "setProtocolYieldFeePercentage",
    "setPublicDeploymentStatus",
    "setPublicSuffixList",
    "setResolver",
    "setRewardConfig",
    "setRewardConfigWithMultiplier",
    "setSlippageTolerance",
    "setStaticSwapFeePercentage",
    "setTargetToken",
    "setTTL",
    "setUpgradeContract",
    "touch",
    "transferFactoryOwnership",
    "unpause",
    "updateDelay",
    "updateLRTConfig",
    "updateMaxNodeDelegatorCount",
    "updateWinnersPerPeriod",
}
CHEAP_GETTER_UNIT_NAMES = {
    "allowance",
    "balanceOf",
    "decimals",
    "domainSeparator",
    "name",
    "nonces",
    "owner",
    "paused",
    "symbol",
    "tokenURI",
    "totalSupply",
    "uri",
    "version",
}
CHEAP_GETTER_PREFIXES = (
    "can",
    "get",
    "has",
    "is",
)
ADMIN_ZERO_INTERFACE_UNIT_NAMES = {
    "acceptOwnership",
    "pause",
    "renounceOwnership",
    "unPause",
    "unpause",
}
UNFOCUSABLE_SPECIAL_UNIT_NAMES = {
    "fallback",
    "receive",
}
UNFOCUSABLE_SPECIAL_REASON = (
    "fallback/receive cannot be selected by --focus-function")
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
}
OWNERSHIP_TRANSFER_UNIT_NAMES = {
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
    "redeem",
    "safe",
    "sell",
    "supply",
    "upgrade",
    "withdraw",
)
CHEAP_STATE_UNIT_PREFIXES = (
    "add",
    "disable",
    "enable",
    "lower",
    "remove",
    "set",
    "touch",
    "unpause",
    "update",
)

# These are bounded, evidence-backed routes for the first NO-PATH bucket.  A
# route is a scheduling hint only: it never credits coverage.  The failed
# getter is suppressed only when a same-contract replacement is present.
NO_PATH_STATIC_ROUTES = {
    ("bugfix124", "acfix_032_CVE_2021_39167"):
    {
        "failed_units": ("isOperation",),
        "preferred_units": ("execute",),
        "fallback_units": ("getMinDelay",),
        "target_function": "_afterCall",
        "reason": "public execute caller reaches the changed _afterCall body",
    },
    ("bugfix124", "acfix_033_CVE_2021_39168"):
    {
        "failed_units": ("isOperation",),
        "preferred_units": ("execute",),
        "fallback_units": ("getMinDelay",),
        "target_function": "_afterCall",
        "reason": "public execute caller reaches the changed _afterCall body",
    },
    ("bugfix124", "acfix_3_5_101_ANCHToken"):
    {
        "failed_units": ("balanceOf",),
        "preferred_units": ("transfer",),
        "fallback_units": ("tokenFromReflection",),
        "target_function": "_transfer",
        "reason": "public transfer caller reaches the changed _transfer body",
    },
}

BUDGET_VALUE_FLAGS = {
    "--timeout",
    "--run-timeout",
    "--memlimit-gib",
}
REPLACEABLE_VALUE_FLAGS = BUDGET_VALUE_FLAGS | {"--workdir"}


class ScheduleError(ValueError):
    """The input manifest cannot be converted into unit jobs."""


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


def _compiler_abi_value_gate(unit_info: dict | None) -> bool:
    """Identify Solidity's compiler-inserted nonpayable value gate.

    Payable functions can contain real source-level ``msg.value`` guards. They
    are not ABI gates and must stay out of this automatic coordinate arm.
    """

    if not unit_info:
        return False
    mutability = unit_info.get("state_mutability") or ""
    visibility = unit_info.get("visibility")
    if visibility is not None and visibility not in ("public", "external"):
        return False
    return mutability == "nonpayable"


def _region_strategy(unit_info: dict | None) -> dict:
    sender_arm = _zero_interface_sender_arm(unit_info)
    value_arm = _compiler_abi_value_gate(unit_info)
    mutability = (unit_info or {}).get("state_mutability") or ""
    params = int((unit_info or {}).get("parameter_count") or 0)
    slot_coords = 0
    if mutability not in ("view", "pure") and 0 < params <= 3:
        slot_coords = 2
    env_coords = []
    if sender_arm:
        env_coords.append("msg.sender")
    if value_arm:
        env_coords.append("msg.value")
    reasons = []
    if sender_arm:
        reasons.append("state-changing unit has no ABI parameter coordinate")
    if value_arm:
        reasons.append("compiler-inserted nonpayable ABI value gate")
    if slot_coords:
        reasons.append("small state-changing ABI gets mapping-slot coordinates")
    return {
        "zero_interface_sender_arm": sender_arm,
        "zero_interface_value_arm": value_arm,
        "compiler_abi_value_gate": value_arm,
        "no_auto_pin_value": value_arm,
        "env_coords": env_coords,
        "slot_coords": slot_coords,
        "reason": "; ".join(reasons) if reasons else "shared strong recipe",
    }


def _region_coordinate_rank(unit_info: dict | None) -> tuple[int, str]:
    strategy = _region_strategy(unit_info)
    env_count = len(strategy.get("env_coords") or [])
    slot_count = int(strategy.get("slot_coords") or 0)
    if slot_count and env_count:
        return 0, "env-and-slot-coordinates"
    if slot_count:
        return 1, "slot-coordinates"
    if env_count:
        return 2, "env-coordinates"
    return 3, "abi-only-or-no-coordinate"


def _static_no_path_route(row: dict, units: list[str]) -> dict | None:
    """Return a finite, auditable replacement for a known NO-PATH first unit.

    This deliberately does not inspect or rewrite result files.  It is a
    static route for three identified subjects and only activates when the
    failed unit and a replacement are both present in the current manifest.
    The returned ``theory_credit`` is always zero; verification must establish
    any ``expected_theory_delta_after_verification`` later.
    """

    subject = row.get("subject") or {}
    key = (str(subject.get("benchmark") or ""),
           str(subject.get("subject_id") or ""))
    rule = NO_PATH_STATIC_ROUTES.get(key)
    if rule is None:
        return None
    available = {str(unit) for unit in units}
    failed = next((unit for unit in rule["failed_units"] if unit in available),
                  None)
    if failed is None:
        return None
    primary = next((unit for unit in rule["preferred_units"] if unit in available),
                   None)
    fallback = next((unit for unit in rule["fallback_units"] if unit in available),
                    None)
    selected = primary or fallback
    if selected is None or selected == failed:
        return None
    role = "primary" if primary is not None else "structural-getter-fallback"
    return {
        "schema": "veriput-no-path-static-route/v1",
        "source_bucket": "NO-PATH",
        "subject_id": key[1],
        "failed_unit": failed,
        "selected_unit": selected,
        "preferred_unit": primary,
        "fallback_unit": fallback,
        "selection_role": role,
        "target_function": rule["target_function"],
        "reason": rule["reason"],
        "expected_theory_delta_after_verification": 1 if primary else 0,
        "theory_credit": 0,
    }


def _put_potential_rank(unit_info: dict | None) -> tuple[int, str]:
    """Rank units by whether they can produce a parameterized oracle.

    Under the RQ1 budget, running a heavy target first often materializes only
    a deploy/concrete replay and exhausts the subject before cheap getter or
    setter coordinates are tried.  This rank is stricter than the generic cost
    rank: any ABI/env/slot coordinate that can feed R1/R2 is preferred over a
    zero-coordinate unit, even when the zero-coordinate unit was target-hinted.
    """
    if not unit_info:
        return 4, "unknown-interface"
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    returns = int(unit_info.get("return_count") or 0)
    coord_rank, coord_reason = _region_coordinate_rank(unit_info)
    if params and returns and mutability in ("view", "pure"):
        return 0, "parameterized-getter-return-coordinate"
    if params and mutability not in ("view", "pure") and coord_rank <= 1:
        return 0, f"state-abi-{coord_reason}"
    if params:
        return 1, "abi-coordinate"
    if coord_rank <= 2:
        return 2, coord_reason
    if returns and mutability in ("view", "pure"):
        return 3, "return-only-getter"
    return 5, "zero-coordinate-concrete-risk"


def _name_only_cost_rank(unit: str) -> tuple[int, str]:
    """Use the callable name when manifest metadata is unavailable."""

    lower = unit.lower()
    if (unit in CHEAP_GETTER_UNIT_NAMES
            or lower.startswith(CHEAP_GETTER_PREFIXES)
            or lower.startswith((
                "allowance",
                "balance",
                "controller",
                "current",
                "decimals",
                "domain",
                "factory",
                "implementation",
                "name",
                "owner",
                "paused",
                "round",
                "symbol",
                "total",
                "unclaimed",
                "uri",
                "user",
                "version",
            ))):
        return 1, "getter-name-fallback"
    if unit in INITIALIZER_LIKE_UNITS:
        return 80, "initializer-like"
    if (unit in OWNERSHIP_TRANSFER_UNIT_NAMES
            or unit in CHEAP_STATE_UNIT_NAMES
            or lower.startswith(CHEAP_STATE_UNIT_PREFIXES)
            or lower.startswith("approve")):
        return 10 if unit not in OWNERSHIP_TRANSFER_UNIT_NAMES else 8, "state-name-fallback"
    if any(fragment in lower for fragment in EXPENSIVE_UNIT_NAME_FRAGMENTS):
        return 70, "expensive-name-fallback"
    return 30, "generic-name-fallback"


def _strong_recipe_slot_coords() -> int:
    args = strong_certify_args()
    for idx, arg in enumerate(args[:-1]):
        if arg == "--slot-coords":
            try:
                return int(args[idx + 1])
            except (TypeError, ValueError):
                return 0
    return 0


def _load_solc_ast(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        text = Path(path).read_text(errors="replace")
        return json.loads(text[text.index("{"):])
    except (OSError, ValueError):
        return None


def _contract_chain(ast: dict, contract: str | None) -> list[dict]:
    by_id = {}
    target = None

    def walk(node):
        nonlocal target
        if isinstance(node, dict):
            if node.get("nodeType") == "ContractDefinition":
                if node.get("id") is not None:
                    by_id[node["id"]] = node
                if node.get("name") == contract:
                    target = node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ast)
    if target is None:
        return []
    chain = target.get("linearizedBaseContracts") or [target.get("id")]
    return [by_id[cid] for cid in reversed(chain) if cid in by_id]


def _matching_path_functions(subject: dict, unit: str,
                             unit_info: dict | None) -> list[str]:
    if unit_info and unit_info.get("path_function"):
        path_function = str(unit_info["path_function"])
        target_owner = subject.get("contract") or ""
        if target_owner:
            path_function = re.sub(
                r"^sol:@C@[^@]+@F@",
                f"sol:@C@{target_owner}@F@",
                path_function,
                count=1)
        return [path_function]
    ast = _load_solc_ast(subject.get("solast"))
    if ast is None:
        return []
    want_arity = None
    want_types = None
    if unit_info:
        try:
            want_arity = int(unit_info.get("parameter_count"))
        except (TypeError, ValueError):
            want_arity = None
        if unit_info.get("parameter_types"):
            want_types = tuple(str(x) for x in unit_info.get("parameter_types"))
    target_owner = subject.get("contract") or ""
    raw_candidates: list[tuple[str, int]] = []
    for contract in _contract_chain(ast, subject.get("contract")):
        owner = contract.get("name")
        for node in contract.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            if node.get("nodeType") != "FunctionDefinition":
                continue
            if node.get("name") != unit:
                continue
            if node.get("implemented") is False:
                continue
            if node.get("visibility") not in ("public", "external"):
                continue
            params = ((node.get("parameters") or {}).get("parameters") or [])
            if want_arity is not None and len(params) != want_arity:
                continue
            if want_types is not None:
                got_types = tuple(
                    str((p.get("typeDescriptions") or {}).get("typeString") or
                        (p.get("typeDescriptions") or {}).get("typeIdentifier") or "")
                    for p in params
                    if isinstance(p, dict))
                if got_types != want_types:
                    continue
            if owner and node.get("id") is not None:
                raw_candidates.append((str(owner), int(node["id"])))

    if not raw_candidates:
        return []

    if target_owner:
        target_candidates = [
            (owner, node_id)
            for owner, node_id in raw_candidates
            if owner == target_owner
        ]
        if target_candidates:
            raw_candidates = target_candidates
        else:
            # ESBMC merges inherited callable bodies into the selected target
            # contract before path coverage.  The stable node id remains the
            # base declaration id, but cov-report.json names the target
            # contract as the owner (e.g. Derived@f#base_id), not Base@f#base_id.
            raw_candidates = [(target_owner, node_id)
                              for _owner, node_id in raw_candidates]

    candidates = []
    seen = set()
    for owner, node_id in raw_candidates:
        item = f"sol:@C@{owner}@F@{unit}#{node_id}"
        if item not in seen:
            seen.add(item)
            candidates.append(item)
    return candidates


def _unique_path_function(subject: dict, unit: str,
                          unit_info: dict | None) -> str | None:
    candidates = _matching_path_functions(subject, unit, unit_info)
    return candidates[0] if len(candidates) == 1 else None


def _path_function_job_suffix(path_function: str | None) -> str:
    if not path_function:
        return ""
    marker = path_function.rsplit("#", 1)[-1]
    if not marker.isdigit():
        marker = re.sub(r"[^A-Za-z0-9]+", "_", path_function).strip("_")
    return "__pf" + marker


def _certify_argv(subject: dict, unit: str, ast_cache_root: str | None, out_path: str | None,
                  dry_run: bool, *, timeout_s: int, run_timeout_s: int,
                  memlimit_gib: int, workdir: str,
                  unit_info: dict | None = None,
                  path_function: str | None = None) -> list[str]:
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
    if path_function:
        argv.extend(["--path-function", path_function])
    argv.extend(strong_certify_args())
    region_strategy = _region_strategy(unit_info)
    if region_strategy.get("no_auto_pin_value"):
        argv.append("--no-auto-pin-value")
    for coord in region_strategy["env_coords"]:
        argv.extend(["--env-coord", coord])
    slot_coords = int(region_strategy.get("slot_coords") or 0)
    if slot_coords > _strong_recipe_slot_coords():
        argv.extend(["--slot-coords", str(region_strategy["slot_coords"])])
    argv = budgeted_certify_argv(argv,
                                 timeout_s=timeout_s,
                                 run_timeout_s=run_timeout_s,
                                 memlimit_gib=memlimit_gib,
                                 workdir=workdir)
    if dry_run:
        argv.append("--dry-run")
    return argv


def _unit_priority(unit: str, hinted: set[str], unit_info: dict | None,
                   static_obstacles: list[dict] | None = None,
                   route_role: str | None = None) -> tuple[int, str]:
    if static_obstacles:
        return 4, "static-obstacle"
    if route_role == "primary":
        return 0, "no-path-route-primary"
    if route_role == "structural-getter-fallback":
        return 1, "no-path-route-structural-getter-fallback"
    put_rank, put_reason = _put_potential_rank(unit_info)
    if put_rank == 0:
        return 0, f"put-first-{put_reason}"
    if unit in hinted:
        if not unit_info:
            cost_tier, _reason = _name_only_cost_rank(unit)
            if cost_tier >= 70:
                return 3, "expensive-target-hint-no-interface"
            return 0, "target-hint"
        cost_tier = _unit_cost_rank(unit, unit_info)[0]
        coord_rank, coord_reason = _region_coordinate_rank(unit_info)
        if cost_tier <= 12 or coord_rank <= 1:
            return 0, f"target-hint-{coord_reason}"
        if unit not in INITIALIZER_LIKE_UNITS and cost_tier >= 70:
            return 3, "expensive-target-hint-after-put-candidates"
        return 2, "target-hint-after-cheap-coordinate"
    if not unit_info:
        cost_tier, _reason = _name_only_cost_rank(unit)
        if cost_tier <= 10:
            return 1, "enumerated"
        if cost_tier >= 70:
            return 3, "enumerated"
        return 2, "enumerated"
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    returns = int(unit_info.get("return_count") or 0)
    if _is_cheap_getter_unit(unit, unit_info):
        return 1, "cheap-pure/view-getter"
    if mutability in ("view", "pure") and (
            params or returns):
        return 2, "pure/view-with-interface"
    if mutability not in ("view", "pure"):
        if unit in INITIALIZER_LIKE_UNITS:
            return 3, "initializer-like"
        coord_rank, coord_reason = _region_coordinate_rank(unit_info)
        if coord_rank <= 1 and _unit_cost_rank(unit, unit_info)[0] <= 30:
            return 1, f"cheap-state-{coord_reason}"
        if _unit_cost_rank(unit, unit_info)[0] <= 10:
            return 1, "state-changing"
        if params == 0 and returns == 0:
            return 2, "zero-interface-state-changing"
        return 2, "state-changing"
    return 3, "zero-arg-view"


def _is_cheap_getter_unit(unit: str, unit_info: dict | None) -> bool:
    if not unit_info:
        return False
    mutability = unit_info.get("state_mutability") or ""
    if mutability not in ("view", "pure"):
        return False
    params = int(unit_info.get("parameter_count") or 0)
    if params > 2:
        return False
    returns = int(unit_info.get("return_count") or 0)
    if returns > 0:
        return True
    if params == 0 and unit in CHEAP_GETTER_UNIT_NAMES:
        return True
    return any(unit.startswith(prefix) for prefix in CHEAP_GETTER_PREFIXES)


def _unit_cost_rank(unit: str, unit_info: dict | None) -> tuple[int, int, int]:
    """Cheap-first order inside the same semantic priority bucket.

    The priority bucket still decides what class of unit comes first.  This
    rank only breaks ties inside that class, where source order often puts
    heavy business methods before simple setters and view helpers.  Under a
    fixed subject budget that can prevent later cheap units from being tried at
    all.
    """
    if not unit_info:
        tier, _reason = _name_only_cost_rank(unit)
        return (tier, 0, 0)
    mutability = unit_info.get("state_mutability") or ""
    params = int(unit_info.get("parameter_count") or 0)
    returns = int(unit_info.get("return_count") or 0)
    lower = unit.lower()
    if _is_cheap_getter_unit(unit, unit_info):
        tier = 1
    elif unit in INITIALIZER_LIKE_UNITS:
        tier = 80
    elif mutability in ("view", "pure"):
        tier = 5
    elif unit in ADMIN_ZERO_INTERFACE_UNIT_NAMES and params == 0:
        tier = 65
    elif unit in ADMIN_SETTER_UNIT_NAMES:
        tier = 65
    elif unit in ACCESS_CONTROL_MUTATOR_UNIT_NAMES:
        tier = 68
    elif unit in OWNERSHIP_TRANSFER_UNIT_NAMES:
        tier = 8
    elif unit in MODERATE_STATE_UNIT_NAMES:
        tier = 45
    elif (unit in CHEAP_STATE_UNIT_NAMES
          or any(lower.startswith(prefix.lower())
                 for prefix in CHEAP_STATE_UNIT_PREFIXES)):
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


def _is_unfocusable_special_unit(unit: str, unit_info: dict | None) -> bool:
    if unit not in UNFOCUSABLE_SPECIAL_UNIT_NAMES:
        return False
    if unit_info is None:
        return True
    return unit_info.get("name") == unit


def _no_unit_row(row_pos: int, row: dict, reason: str,
                 skipped: list[dict]) -> dict:
    return {
        "row": row_pos,
        "reason": reason,
        "subject": row.get("subject") or {},
        "target": row.get("target"),
        "skipped": skipped,
    }


def _job_for_unit(row: dict, unit: str, ordinal: int, ast_cache_root: str | None,
                  out_path: str | None, unit_info: dict | None, *,
                  timeout_s: int, run_timeout_s: int, memlimit_gib: int,
                  workdir: str, path_function: str | None = None,
                  no_path_route: dict | None = None,
                  route_role: str | None = None) -> dict:
    subject = dict(row["subject"])
    subject["unit"] = unit
    hinted = set((row.get("unit_hints") or {}).get("hinted_units") or [])
    static_obstacles = _static_obstacles_for_unit(row, subject, unit)
    priority, reason = _unit_priority(unit, hinted, unit_info, static_obstacles,
                                      route_role)
    if path_function is None:
        path_function = _unique_path_function(subject, unit, unit_info)
    named_candidates = {}
    if path_function:
        named_candidates[unit] = path_function
    region_strategy = _region_strategy(unit_info)
    region_strategy["positive_named_candidate_mapping"] = named_candidates
    coord_rank, coord_reason = _region_coordinate_rank(unit_info)
    put_rank, put_reason = _put_potential_rank(unit_info)
    route_rank = 2
    if route_role == "primary":
        route_rank = 0
    elif route_role == "structural-getter-fallback":
        route_rank = 1
    return {
        "schema": "veriput-unit-job/v1",
        "job_id": (f"{subject.get('benchmark_key') or subject['subject_id']}__"
                   f"{unit}{_path_function_job_suffix(path_function)}"),
        "priority": priority,
        "priority_reason": reason,
        "schedule_rank": {
            "cheap_first": list(_unit_cost_rank(unit, unit_info)),
            "coordinate_first": [coord_rank, coord_reason],
            "put_potential_first": [put_rank, put_reason],
            "no_path_route_first": route_rank,
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
        "path_function": path_function,
        "region_strategy": region_strategy,
        "positive_named_candidate_mapping": named_candidates,
        "continuation_policy": {
            "coordinate_bearing": coord_rank <= 1,
            "put_candidate": put_rank <= 1,
            "weak_result_requeue": put_rank > 1 or coord_rank > 1,
            "reason": ("coordinate/PUT candidate"
                       if put_rank <= 1 or coord_rank <= 1
                       else "deprioritize after weak Stage-2 result"),
        },
        "static_obstacles": static_obstacles,
        "no_path_route": no_path_route,
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
                                      path_function=path_function),
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
                                      path_function=path_function),
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
    skipped_units = []
    no_unit_rows = []
    duplicate_jobs = []
    no_path_routes = []
    no_path_suppressed = []
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
        units = (row.get("units") or {}).get("units") or []
        unit_name_counts = Counter(str(unit) for unit in units)
        info_rows = [
            item for item in (row.get("units") or {}).get("unit_info") or []
            if isinstance(item, dict)
        ]
        infos_by_name = {
            item.get("name"): item
            for item in info_rows
            if item.get("name")
        }
        missing = [
            name for name in ("root", "benchmark", "subject_id", "contract")
            if not subject.get(name)
        ]
        if missing:
            raise ScheduleError(f"ok row {row_pos} subject is missing: {', '.join(missing)}")
        if not units:
            no_unit_rows.append(
                _no_unit_row(row_pos, row,
                             ((row.get("units") or {}).get("no_unit_reason")
                              or "target contract has no schedulable units"),
                             (row.get("units") or {}).get("skipped") or []))
            continue
        row_jobs_before = len(jobs)
        row_skipped_units = []
        no_path_route = _static_no_path_route(row, [str(unit) for unit in units])
        if no_path_route:
            no_path_routes.append({
                **no_path_route,
                "row": row_pos,
                "benchmark": subject.get("benchmark"),
                "contract": subject.get("contract"),
            })
        for unit_pos, unit in enumerate(units):
            unit_info = None
            if unit_pos < len(info_rows) and info_rows[unit_pos].get("name") == unit:
                unit_info = info_rows[unit_pos]
            if unit_info is None:
                unit_info = infos_by_name.get(unit)
            if _is_unfocusable_special_unit(unit, unit_info):
                skipped = {
                    "row": row_pos,
                    "unit": unit,
                    "reason": UNFOCUSABLE_SPECIAL_REASON,
                    "subject": subject,
                    "target": row.get("target"),
                    "unit_info": unit_info,
                }
                skipped_units.append(skipped)
                row_skipped_units.append(skipped)
                continue
            if no_path_route and unit == no_path_route["failed_unit"]:
                skipped = {
                    "row": row_pos,
                    "unit": unit,
                    "reason": (
                        "known NO-PATH first bucket; statically routed to "
                        f"{no_path_route['selected_unit']}"),
                    "subject": subject,
                    "target": row.get("target"),
                    "unit_info": unit_info,
                    "no_path_route": no_path_route,
                }
                skipped_units.append(skipped)
                row_skipped_units.append(skipped)
                no_path_suppressed.append(skipped)
                continue
            path_functions = _matching_path_functions(subject, unit, unit_info)
            if unit_name_counts[str(unit)] > 1 and not path_functions:
                skipped = {
                    "row": row_pos,
                    "unit": unit,
                    "reason": (
                        "overloaded unit needs unique --path-function"),
                    "subject": subject,
                    "target": row.get("target"),
                    "unit_info": unit_info,
                }
                skipped_units.append(skipped)
                row_skipped_units.append(skipped)
                continue
            if len(path_functions) <= 1:
                path_functions = [path_functions[0] if path_functions else None]
            for path_function in path_functions:
                key = (subject.get("benchmark"), subject.get("subject_id"),
                       unit, path_function)
                if key in seen_jobs:
                    duplicate_jobs.append({
                        "row": row_pos,
                        "unit": unit,
                        "path_function": path_function,
                        "reason": "duplicate prepared subject unit",
                        "subject": subject,
                        "target": row.get("target"),
                    })
                    continue
                seen_jobs.add(key)
                route_role = None
                if no_path_route and unit == no_path_route["selected_unit"]:
                    route_role = no_path_route["selection_role"]
                jobs.append(_job_for_unit(row, unit, len(jobs), ast_cache_root,
                                          cert_out or None, unit_info,
                                          timeout_s=timeout_s,
                                          run_timeout_s=run_timeout_s,
                                          memlimit_gib=memlimit_gib,
                                          workdir=workdir,
                                          path_function=path_function,
                                          no_path_route=(no_path_route
                                                         if route_role else None),
                                          route_role=route_role))
        if len(jobs) == row_jobs_before and row_skipped_units:
            if all(item.get("reason") == UNFOCUSABLE_SPECIAL_REASON
                   for item in row_skipped_units):
                no_unit_rows.append(
                    _no_unit_row(
                        row_pos,
                        row,
                        "target contract exposes only fallback/receive entries; "
                        "use deploy-only concrete fallback",
                        row_skipped_units))

    shard_spec = _parse_shard(shard)
    total_jobs = len(jobs)
    def _job_sort_key(item: dict) -> tuple:
        rank = item.get("schedule_rank", {}).get("cheap_first") or [50, 0, 0]
        tier = rank[0] if rank else 50
        rest = tuple(rank[1:])
        coord_rank = (
            item.get("schedule_rank", {}).get("coordinate_first") or [3])[0]
        put_rank = (
            item.get("schedule_rank", {}).get("put_potential_first") or [5])[0]
        hinted_tie = (
            0 if str(item.get("priority_reason") or "").startswith("target-hint")
            else 1)
        concrete_risk = 1 if put_rank >= 5 else 0
        coordinate_quality = 0 if put_rank <= 1 or coord_rank <= 1 else 1
        return (item["priority"], coordinate_quality, concrete_risk, put_rank,
                item.get("schedule_rank", {}).get("no_path_route_first", 2),
                tier, coord_rank, hinted_tie, rest,
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
    skipped_units_by_reason = Counter(
        str(item.get("reason") or "<missing>") for item in skipped_units)
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
            "skipped_rows": len(skipped_rows),
            "skipped_by_status": dict(sorted(skipped_by_status.items())),
            "skipped_units": len(skipped_units),
            "skipped_units_by_reason": dict(sorted(skipped_units_by_reason.items())),
            "no_unit_rows": len(no_unit_rows),
            "duplicate_jobs": len(duplicate_jobs),
            "no_path_routes": len(no_path_routes),
            "no_path_suppressed_units": len(no_path_suppressed),
            "theory_credit": 0,
        },
        "skipped_rows": skipped_rows,
        "skipped_units": skipped_units,
        "no_unit_rows": no_unit_rows,
        "duplicate_jobs": duplicate_jobs,
        "no_path_routes": no_path_routes,
        "no_path_suppressed_units": no_path_suppressed,
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
