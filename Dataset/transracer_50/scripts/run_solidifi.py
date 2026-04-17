#!/usr/bin/env python3
"""Run ESBMC `--tod-balance-check` on the SolidiFI 50-buggy TOD benchmark.

SolidiFI TOD injections are ether-flow balance races (SWC-114 style),
so we use `--tod-balance-check`, not `--tod-race-check`.

Per-contract strategy: use the FIRST tod_pair from labels.json (specific
pair to avoid OOM from auto-discovery enumeration).  If the contract
has no labelled pair, skip.

Hardened wrapper (timeout + ulimit -v 4GB + ulimit -t 270).  Sequential
outer loop.  Results under Dataset/benchmark_tod/results/esbmc_balance/.
"""
from __future__ import annotations
import json, re, subprocess, time
from pathlib import Path

ROOT = Path("/home/samson/workspace/esbmc/Dataset/benchmark_tod")
SOURCES = ROOT / "solidifi_tod_08"
LABELS = json.loads((ROOT / "solidifi_tod_active" / "labels.json").read_text())
RESULTS = ROOT / "results" / "esbmc_balance"
RESULTS.mkdir(parents=True, exist_ok=True)
ESBMC = "/home/samson/workspace/esbmc/build/src/esbmc/esbmc"
WALL = 300
CPU = 270
VMEM_KB = 4000000


def run_one(case: dict) -> dict:
    fname = case["file"]         # e.g. "buggy_1"
    cname = case["contract_name"]
    pairs = case.get("tod_pairs", [])
    if not pairs:
        return {"case": fname, "contract": cname, "verdict": "no_pair"}
    fa, fb = pairs[0]

    sol_dir = SOURCES / fname
    sol = sol_dir / "contract.sol"
    if not sol.exists():
        return {"case": fname, "contract": cname, "verdict": "no_source"}

    out_dir = RESULTS / fname
    out_dir.mkdir(exist_ok=True)
    cmd = (
        f"timeout {WALL} bash -c '"
        f"ulimit -v {VMEM_KB}; ulimit -t {CPU}; "
        f"exec \"{ESBMC}\" contract.sol "
        f"--contract \"{cname}\" "
        f"--tod-balance-check={fa},{fb} --tod-jobs=1 "
        f"--bound --unwind 3 --no-unwinding-assertions "
        f"--cvc5"
        f"'"
    )
    (out_dir / "cmd").write_text(cmd + "\n")
    (out_dir / "pair").write_text(f"{fa},{fb}\n")

    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=sol_dir,
            capture_output=True, text=True,
            timeout=WALL + 10,
        )
        rc = r.returncode
        (out_dir / "run.stdout").write_text(r.stdout)
        (out_dir / "run.stderr").write_text(r.stderr)
    except subprocess.TimeoutExpired:
        rc = 124
        (out_dir / "run.stdout").write_text("")
        (out_dir / "run.stderr").write_text("timeout\n")
    (out_dir / "run.exitcode").write_text(str(rc))
    elapsed = time.time() - t0

    full = (out_dir / "run.stderr").read_text() + (out_dir / "run.stdout").read_text()
    m = re.search(
        r"--tod-balance-check summary: (\d+) pair\(s\) — (\d+) clean, (\d+) TOD found, (\d+) error",
        full,
    )
    if m:
        verdict = "summarised"
        pairs_n, clean, found, error = map(int, m.groups())
    elif "discovered 0 candidate pair(s)" in full:
        verdict = "no_pairs"; pairs_n = clean = found = error = 0
    elif rc == 124:
        verdict = "timeout"; pairs_n = clean = found = error = 0
    elif "boost::bad_any_cast" in full or "Aborted" in full or "Segmentation" in full or rc < 0:
        verdict = "crash"; pairs_n = clean = found = error = 0
    else:
        verdict = "unknown"; pairs_n = clean = found = error = 0

    return {
        "case": fname, "contract": cname,
        "pair": f"{fa},{fb}",
        "elapsed_s": round(elapsed, 1),
        "exit_code": rc,
        "verdict": verdict,
        "pairs": pairs_n, "clean": clean, "tod_found": found, "error": error,
    }


def main():
    cases = LABELS["contracts"]
    results = []
    for case in cases:
        fname = case["file"]
        print(f"[{fname}] contract={case['contract_name']}  pair={case.get('tod_pairs',[[]])[:1]}  running...")
        r = run_one(case)
        results.append(r)
        print(f"[{fname}] -> {r['verdict']} elapsed={r.get('elapsed_s','?')}s "
              f"pairs={r.get('pairs','-')}/clean={r.get('clean','-')}/found={r.get('tod_found','-')}/err={r.get('error','-')}")
    out = ROOT / "solidifi_balance_summary.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\n=== DONE ===")
    found = sum(1 for r in results if r.get("tod_found", 0) > 0)
    print(f"TOD-Balance found: {found}/{len(results)}")


if __name__ == "__main__":
    main()
