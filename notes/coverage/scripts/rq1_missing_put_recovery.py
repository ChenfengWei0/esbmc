#!/usr/bin/env python3
"""Classify missing frozen RQ1 PUT identities and emit fair local rerun commands."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_RQ1 = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")
DEFAULT_LEDGER = Path(__file__).with_name("..").resolve() / "rq1_ce_obligations.frozen.json"
DEFAULT_ESBMC = Path("/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
RUNNER = Path(__file__).with_name("rq1_veriput_run.py")


def _inventory_module():
    path = Path(__file__).with_name("rq1_final_test_inventory.py")
    spec = importlib.util.spec_from_file_location("rq1_final_test_inventory_local", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _identity(case: str, entry: dict) -> tuple[str, str, str, str, str]:
    origin = entry.get("origin") or {}
    enc = origin.get("enc")
    return (case, str(origin.get("path_function") or ""), str(origin.get("unit") or ""),
            str(enc if enc is not None else ""), str(origin.get("piece") or ""))


def _category(source: str) -> str:
    if source in {"certified-region-concrete-fallback", "certified-region", "certified_region"}:
        return "certified-region-renderer-gap"
    if source == "structural_getter_only":
        return "structural-getter-only"
    if source == "no-coordinate-concrete-fallback":
        return "no-generalizable-coordinate"
    if source == "cleared_not_certified_fallback":
        return "not-certified-fallback"
    if source == "timeout_concrete_fallback":
        return "certification-timeout"
    if source in {"source-grounded-manual-concrete-replay", "source_grounded_callable_recovery"}:
        return "manual-source-grounded"
    if source == "source_constructor_revert_fallback":
        return "constructor-fallback"
    return "metadata-unknown"


def build(root: Path, ledger: Path) -> dict:
    inventory = _inventory_module()
    generalized, unresolved, not_generalized = inventory.obligations(root)
    frozen = {tuple(map(str, row)) for row in json.loads(ledger.read_text())["obligations"]}
    missing = frozen - (generalized | unresolved | not_generalized)
    selected: dict[tuple[str, ...], dict] = {}
    for case, subject_dir in inventory._case_dirs(root):  # pylint: disable=protected-access
        manifest = subject_dir / "concrete-replays" / "manifest.json"
        if not manifest.is_file():
            continue
        for entry in json.loads(manifest.read_text()).get("entries") or []:
            identity = _identity(case, entry)
            if identity not in missing:
                continue
            source = str((entry.get("origin") or {}).get("stage2_source") or "unknown")
            row = {"identity": list(identity), "category": _category(source),
                   "stage2_source": source, "manifest": str(manifest)}
            old = selected.get(identity)
            if old is None or old["category"] == "metadata-unknown":
                selected[identity] = row
    for identity in sorted(missing - set(selected)):
        selected[identity] = {"identity": list(identity),
                              "category": "unmatched-no-current-manifest",
                              "stage2_source": "", "manifest": ""}
    rows = [selected[key] for key in sorted(selected)]
    return {"schema": "veriput-rq1-missing-put-recovery/v1", "frozen_total": len(frozen),
            "observed_generalized": len(generalized), "observed_unresolved": len(unresolved),
            "observed_not_generalized": len(not_generalized), "missing_count": len(rows),
            "category_counts": dict(sorted(Counter(r["category"] for r in rows).items())),
            "rows": rows}


def commands(document: dict, output: Path, esbmc: Path) -> list[dict]:
    groups = {"fast": {"certified-region-renderer-gap", "structural-getter-only"},
              "no-coordinate": {"no-generalizable-coordinate"},
              "uncertified": {"not-certified-fallback"}}
    result = []
    for group, categories in groups.items():
        cases = defaultdict(set)
        for row in document["rows"]:
            if row["category"] in categories:
                case, _, unit, _, _ = row["identity"]
                dataset, subject = case.split("/", 1)
                cases[dataset].add((subject, unit))
        for dataset, members in sorted(cases.items()):
            argv = ["python3", str(RUNNER), "--benchmark", dataset, "--result-root",
                    str(output / group / dataset), "--ast-cache-root",
                    f"/tmp/rq1-recovery-{group}-{dataset}", "--esbmc", str(esbmc),
                    "--strict-case-wall-budget", "--timeout", "600", "--esbmc-run-timeout",
                    "120", "--stage2-stage4-reserve-s", "120", "--jobs", "1",
                    "--memlimit-gib", "4", "--forge-timeout", "180", "--redo"]
            for subject in sorted({item[0] for item in members}):
                argv += ["--subject-id", subject]
            for unit in sorted({item[1] for item in members if item[1]}):
                argv += ["--unit", unit]
            result.append({"group": group, "dataset": dataset,
                           "identity_count": sum(1 for row in document["rows"]
                                                 if row["category"] in categories and
                                                 row["identity"][0].startswith(dataset + "/")),
                           "case_count": len({item[0] for item in members}), "argv": argv,
                           "shell": shlex.join(argv)})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rq1-root", type=Path, default=DEFAULT_RQ1)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--esbmc", type=Path, default=DEFAULT_ESBMC)
    args = parser.parse_args()
    document = build(args.rq1_root, args.ledger)
    document["commands"] = commands(document, args.run_root, args.esbmc)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"missing_count": document["missing_count"],
                      "category_counts": document["category_counts"],
                      "commands": [{k: row[k] for k in ("group", "dataset", "identity_count",
                                                         "case_count")}
                                   for row in document["commands"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
