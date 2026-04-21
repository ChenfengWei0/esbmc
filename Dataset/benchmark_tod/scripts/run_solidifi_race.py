#!/usr/bin/env python3
"""Batch-run ESBMC --tod-race-check=auto on the SolidiFi TOD benchmark
(50 contracts, upgraded to pragma >=0.8.0).

Two-phase execution:

1. DISCOVERY (fast, ~5s budget): `--tod-race-check=auto --dump-harness`
   runs only the discovery + harness-emit pipeline, skipping full
   verification.  Records the auto-discovered pair list and compares
   against SolidiFi's labels.json to compute recall.

2. VERIFICATION (slow, ~180s budget per contract, optional): full
   --tod-race-check=auto run using --tod-jobs=2 with a 4 GB memory
   cap and a 25s ulimit per child.  Because many SolidiFi contracts
   have 20+ candidate pairs that each take 10-20s of SMT time, a
   contract that runs hot won't finish every pair — we record a
   partial verdict set rather than skipping it entirely.

Usage:
  ./run_solidifi_race.py                    # discovery only, all cases
  ./run_solidifi_race.py --verify            # + verification phase
  ./run_solidifi_race.py --only buggy_1 buggy_2
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/Dataset/benchmark_tod")
BENCH = ROOT / "solidifi_tod_08"
LABELS = ROOT / "solidifi_tod_active" / "labels.json"
ESBMC = Path("/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
OUT = ROOT / "results" / "esbmc_race"
OUT.mkdir(parents=True, exist_ok=True)

DISCOVERY_TIMEOUT = 30
VERIFY_TIMEOUT = 180

RE_TARGET_ANY = re.compile(
    r"^(abstract\s+)?contract\s+([A-Za-z_][A-Za-z0-9_]*)\s*(is\s+[^{]+)?\s*\{",
    re.MULTILINE,
)
RE_DISCOVERED = re.compile(
    r"^--tod-race-check: discovered (\d+) candidate pair\(s\) in '(\w+)'",
    re.MULTILINE,
)
RE_PAIR = re.compile(r"^\s+-\s+(\S+)\s+vs\s+(\S+)\s*$", re.MULTILINE)
RE_VERDICT_LINE = re.compile(
    r"^\s+->\s+(\S+)\s+(\S+)(?:\s+(.*))?$", re.MULTILINE
)
RE_SUMMARY = re.compile(
    r"^--tod-race-check summary: (\d+) pair\(s\) — (\d+) clean, "
    r"(\d+) TOD found, (\d+) error",
    re.MULTILINE,
)


def pick_target(src: str) -> str | None:
    """Name of the last non-abstract contract in `src`."""
    target = None
    for m in RE_TARGET_ANY.finditer(src):
        if m.group(1) and m.group(1).strip() == "abstract":
            continue
        target = m.group(2)
    return target


def load_labels() -> dict[str, dict]:
    data = json.loads(LABELS.read_text())
    out: dict[str, dict] = {}
    for entry in data.get("contracts", []):
        out[entry["file"]] = {
            "target": entry.get("contract_name"),
            "pairs": entry.get("tod_pairs", []),
        }
    return out


def run_discovery(case: str, target: str) -> dict:
    cdir = BENCH / case
    cmd = [
        "timeout",
        str(DISCOVERY_TIMEOUT),
        "bash",
        "-c",
        (
            "ulimit -v 4000000; ulimit -t {t}; "
            "exec {esbmc} contract.sol --contract {c} "
            "--tod-race-check=auto --dump-harness --bound "
            "--no-standard-checks".format(
                t=DISCOVERY_TIMEOUT - 5,
                esbmc=ESBMC,
                c=target,
            )
        ),
    ]
    t0 = time.monotonic()
    proc = subprocess.run(
        cmd, cwd=cdir, capture_output=True, text=True, timeout=DISCOVERY_TIMEOUT + 10
    )
    elapsed = time.monotonic() - t0
    combined = proc.stdout + "\n===STDERR===\n" + proc.stderr
    (OUT / f"{case}.discover.log").write_text(combined)
    m = RE_DISCOVERED.search(combined)
    pairs = []
    if m:
        post = combined[m.end() :]
        for p in RE_PAIR.finditer(post):
            pairs.append([p.group(1), p.group(2)])
            if len(pairs) >= int(m.group(1)):
                break
    return {
        "discovered": int(m.group(1)) if m else 0,
        "pairs": pairs,
        "discover_sec": round(elapsed, 1),
    }


def run_verify(case: str, target: str) -> dict:
    cdir = BENCH / case
    cmd = [
        "timeout",
        str(VERIFY_TIMEOUT + 10),
        "bash",
        "-c",
        (
            "ulimit -v 4000000; ulimit -t {t}; "
            "exec {esbmc} contract.sol --contract {c} "
            "--tod-race-check=auto --tod-jobs=2 --bound --unwind 1 "
            "--no-unwinding-assertions --no-standard-checks --cvc5".format(
                t=VERIFY_TIMEOUT,
                esbmc=ESBMC,
                c=target,
            )
        ),
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cdir, capture_output=True, text=True, timeout=VERIFY_TIMEOUT + 30
        )
        rc = proc.returncode
        out = proc.stdout
        err = proc.stderr
    except subprocess.TimeoutExpired as ex:
        rc = -1
        out = ex.stdout.decode() if ex.stdout else ""
        err = ex.stderr.decode() if ex.stderr else ""
    elapsed = time.monotonic() - t0
    combined = out + "\n===STDERR===\n" + err
    (OUT / f"{case}.verify.log").write_text(combined)

    verdicts = []
    for m in RE_VERDICT_LINE.finditer(combined):
        verdicts.append([m.group(1), m.group(2), (m.group(3) or "").strip()])
    s = RE_SUMMARY.search(combined)
    return {
        "verdicts": verdicts,
        "summary": {
            "total": int(s.group(1)) if s else None,
            "clean": int(s.group(2)) if s else None,
            "tod_found": int(s.group(3)) if s else None,
            "error": int(s.group(4)) if s else None,
        }
        if s
        else None,
        "verify_sec": round(elapsed, 1),
        "returncode": rc,
    }


def compute_recall(
    labels: list[list[str]], discovered: list[list[str]]
) -> dict:
    """Match labels (unordered pair) to discovered pairs (auto-mode
    normalises with a < b so re-order for match)."""
    as_set = set(tuple(sorted(p)) for p in labels)
    disc_set = set(tuple(sorted(p)) for p in discovered)
    hit = as_set & disc_set
    missing = sorted(as_set - disc_set)
    return {
        "label_count": len(as_set),
        "discovered_count": len(disc_set),
        "hits": len(hit),
        "recall": (len(hit) / len(as_set)) if as_set else None,
        "missing_labels": [list(p) for p in missing],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="also run the verification phase (slow).")
    ap.add_argument("--only", nargs="+", help="only these buggy_N cases.")
    args = ap.parse_args()

    labels_map = load_labels()
    cases = sorted(
        (d.name for d in BENCH.iterdir() if d.is_dir() and d.name.startswith("buggy_")),
        key=lambda s: int(s.split("_")[1]),
    )
    if args.only:
        cases = [c for c in cases if c in args.only]

    report = []
    for i, c in enumerate(cases):
        src = (BENCH / c / "contract.sol").read_text()
        # Prefer the labels.json contract_name (SolidiFi's deployable target);
        # fall back to a regex pick of the last non-abstract contract.
        target = (labels_map.get(c) or {}).get("target") or pick_target(src)
        print(f"[{i + 1}/{len(cases)}] {c} (target={target})", flush=True)
        if not target:
            report.append({"case": c, "error": "no_target"})
            continue
        disc = run_discovery(c, target)
        recall = compute_recall(
            (labels_map.get(c) or {}).get("pairs", []), disc["pairs"]
        )
        entry = {
            "case": c,
            "target": target,
            **disc,
            "recall": recall,
        }
        if args.verify:
            ver = run_verify(c, target)
            entry["verify"] = ver
            print(
                "    disc={d} labels={l} hits={h} recall={r} "
                "| tod={t} clean={cl} err={e} sec={s}".format(
                    d=disc["discovered"],
                    l=recall["label_count"],
                    h=recall["hits"],
                    r=recall["recall"],
                    t=(ver.get("summary") or {}).get("tod_found"),
                    cl=(ver.get("summary") or {}).get("clean"),
                    e=(ver.get("summary") or {}).get("error"),
                    s=ver.get("verify_sec"),
                ),
                flush=True,
            )
        else:
            print(
                "    disc={d} labels={l} hits={h} recall={r} sec={s}".format(
                    d=disc["discovered"],
                    l=recall["label_count"],
                    h=recall["hits"],
                    r=recall["recall"],
                    s=disc.get("discover_sec"),
                ),
                flush=True,
            )
        report.append(entry)

    # Aggregate
    total_labels = sum((e.get("recall") or {}).get("label_count", 0) for e in report)
    total_hits = sum((e.get("recall") or {}).get("hits", 0) for e in report)
    agg = {
        "total_cases": len(cases),
        "total_labels": total_labels,
        "total_hits": total_hits,
        "overall_recall": (total_hits / total_labels) if total_labels else None,
    }
    if args.verify:
        agg["total_tod_found"] = sum(
            ((e.get("verify") or {}).get("summary") or {}).get("tod_found", 0) or 0
            for e in report
        )
        agg["total_clean"] = sum(
            ((e.get("verify") or {}).get("summary") or {}).get("clean", 0) or 0
            for e in report
        )
        agg["total_error"] = sum(
            ((e.get("verify") or {}).get("summary") or {}).get("error", 0) or 0
            for e in report
        )

    out = {"aggregate": agg, "per_case": report}
    (OUT / "summary.json").write_text(json.dumps(out, indent=2))
    print("\n=== AGGREGATE ===")
    print(json.dumps(agg, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
