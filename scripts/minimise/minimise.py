#!/usr/bin/env python3
"""esbmc-minimise — CLI driver for the three-phase counter-example
minimiser.

Usage
-----

  # 1. Run ESBMC normally on the input and dump the violation info.
  esbmc contract.sol --contract C --overflow-check --cvc5 \\
        --incremental-bmc \\
        --dump-violation-info /tmp/violation.json

  # 2. Feed the info to the minimiser.
  python scripts/minimise/minimise.py \\
      --input contract.sol \\
      --oracle /tmp/violation.json \\
      --out reduced/ \\
      --esbmc-flags "--contract C --overflow-check --cvc5 --incremental-bmc"

Outputs under `<out>/`:
  reduced/<same names>.sol   — reduced program
  manifest.json              — what was removed and why
  violation_final.json       — oracle emitted by the final verifier run
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional, Set

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from oracle import Oracle, ViolationInfo, oracles_match
from solc_driver import SolcDriver
from esbmc_driver import ESBMCDriver
from source_surgery import collect_function_entries
from manifest import ManifestBuilder
from phases import phase0_sweep, phase1_closure, phase2_reduce


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Minimise a Solidity counter-example while preserving a named oracle.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--input",
        required=True,
        help="Path to the primary .sol source (or directory of sources).",
    )
    ap.add_argument(
        "--oracle",
        required=True,
        help="Path to the --dump-violation-info JSON from the original ESBMC run.",
    )
    ap.add_argument(
        "--out",
        required=True,
        help="Output directory. Created if missing. Overwritten if present.",
    )
    ap.add_argument(
        "--esbmc",
        default="/home/samson/workspace/esbmc/build/src/esbmc/esbmc",
        help="Path to the ESBMC binary.",
    )
    ap.add_argument(
        "--solc",
        default="solc",
        help="Path to the solc binary (must be 0.8.x).",
    )
    ap.add_argument(
        "--esbmc-flags",
        default="",
        help="Flags passed to ESBMC on every run, quoted as a single string.",
    )
    ap.add_argument(
        "--wall-timeout-sec",
        type=int,
        default=600,
        help="Per-ESBMC-invocation wall timeout (default 600s).",
    )
    ap.add_argument(
        "--skip-phase-0",
        action="store_true",
        help="Diagnostic: start from Phase 1 (use the input as-is).",
    )
    ap.add_argument(
        "--skip-phase-1",
        action="store_true",
        help="Diagnostic: skip Phase 1 closure (jump to Phase 2 on the input).",
    )
    ap.add_argument(
        "--skip-phase-2",
        action="store_true",
        help="Diagnostic: stop after Phase 1.",
    )
    return ap.parse_args()


def _resolve_sources(input_arg: str) -> List[Path]:
    p = Path(input_arg).resolve()
    if p.is_dir():
        return sorted(p.glob("*.sol"))
    if p.is_file():
        return [p]
    raise FileNotFoundError(p)


def _copy_into_out(originals: List[Path], out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    copies: List[Path] = []
    for src in originals:
        dst = out_dir / src.name
        shutil.copy2(src, dst)
        copies.append(dst)
    return copies


def main() -> int:
    args = _parse_args()
    oracle_info = ViolationInfo.load(Path(args.oracle))
    target_oracle = oracle_info.oracle

    originals = _resolve_sources(args.input)
    out_dir = Path(args.out).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    reduced_dir = out_dir / "reduced"
    reduced_dir.mkdir()

    sources = _copy_into_out(originals, reduced_dir)

    solc = SolcDriver(args.solc)
    esbmc = ESBMCDriver(
        binary=args.esbmc,
        base_flags=shlex.split(args.esbmc_flags),
        wall_timeout_sec=args.wall_timeout_sec,
    )

    try:
        solc_version = solc.version()
    except Exception as exc:
        print(f"solc version check failed: {exc}", file=sys.stderr)
        return 1

    # Mandatory set — split `locked_symbols` (qualified) + trace (qualified)
    mandatory_qualified: Set[str] = set(oracle_info.locked_symbols)
    trace_qualified: Set[str] = {f"{m.contract}.{m.function}" for m in oracle_info.trace_methods}
    # Trace often includes synthetic functions like `_ESBMC_Main_<C>`; filter.
    trace_qualified = {q for q in trace_qualified if not q.split(".")[1].startswith("_ESBMC_")}
    mandatory_qualified |= {q for q in trace_qualified if "." in q}

    mandatory_bare = {q.split(".", 1)[1] for q in mandatory_qualified if "." in q}

    manifest = ManifestBuilder(
        out_path=out_dir / "manifest.json",
        oracle=target_oracle.as_dict(),
        input_meta={
            "sources": [str(s) for s in originals],
            "esbmc_binary": args.esbmc,
            "esbmc_flags": shlex.split(args.esbmc_flags),
            "solc": args.solc,
            "solc_version": solc_version,
            "wall_timeout_sec": args.wall_timeout_sec,
        },
    )
    manifest.flush()

    run_start = time.monotonic()

    # Take pre-phase-0 snapshot for L3 fallback
    pre_phase0_snapshot = {s.name: s.read_text() for s in sources}

    # ----- Phase 0 -----
    if args.skip_phase_0:
        phase0_report = phase0_sweep.Phase0Report()
    else:
        sources, phase0_report = phase0_sweep.run(
            source_root=reduced_dir,
            sources=sources,
            mandatory_bare_names=mandatory_bare,
            solc=solc,
        )
    manifest.record_phase(
        "phase_0",
        {
            "removed": phase0_report.removed,
            "compilation_calls": phase0_report.compilation_calls,
        },
    )

    # Snapshot after Phase 0 — Phase 1 needs this to restore between fallback levels
    phase0_snapshot = {s.name: s.read_text() for s in sources}

    # ----- Phase 1 -----
    if args.skip_phase_1:
        phase1_report = phase1_closure.Phase1Report(
            mandatory_seed=sorted(mandatory_qualified),
            fallback_level_used=-1,  # sentinel "skipped"
        )
    else:
        sources, phase1_report = phase1_closure.run(
            source_root=reduced_dir,
            sources=sources,
            mandatory_qualified=mandatory_qualified,
            phase0_snapshot=phase0_snapshot,
            esbmc=esbmc,
            solc=solc,
            target_oracle=target_oracle,
            pre_phase0_snapshot=pre_phase0_snapshot,
        )

    manifest.record_phase(
        "phase_1",
        {
            "mandatory_seed": phase1_report.mandatory_seed,
            "syntactic_closure": phase1_report.syntactic_closure,
            "fallback_level_used": phase1_report.fallback_level_used,
            "compilation_calls": phase1_report.compilation_calls,
            "verifier_calls": phase1_report.verifier_calls,
            "attempts": phase1_report.attempts,
        },
    )

    if phase1_report.fallback_level_used is None:
        # Abandoned — restore original and bail.
        for s in sources:
            s.write_text(pre_phase0_snapshot[s.name])
        manifest.finalise(
            {
                "status": "gave_up",
                "reason": "no fallback level reproduced the oracle",
                "wall_sec": round(time.monotonic() - run_start, 2),
            }
        )
        print("MINIMISE: gave up — oracle could not be reproduced at any level.")
        return 2

    # ----- Phase 2 -----
    if args.skip_phase_2:
        phase2_report = phase2_reduce.Phase2Report()
    else:
        retained = set(phase1_report.syntactic_closure or [])
        if not retained:
            # If Phase 1 used L2 or L3 we may not have a closure set recorded.
            # Fall back to every defined function in the current program.
            res = solc.compile(list(sources))
            if res.ok and res.ast:
                retained = {e.qualified for e in collect_function_entries(res.ast)}
        sources, phase2_report = phase2_reduce.run(
            source_root=reduced_dir,
            sources=sources,
            mandatory_qualified=mandatory_qualified,
            trace_functions=trace_qualified,
            retained_before_phase2=retained,
            esbmc=esbmc,
            solc=solc,
            target_oracle=target_oracle,
        )
    manifest.record_phase(
        "phase_2",
        {
            "ordering_version": phase2_report.ordering_version,
            "attempts": phase2_report.attempts,
            "verifier_calls": phase2_report.verifier_calls,
            "compilation_calls": phase2_report.compilation_calls,
            "passes": phase2_report.passes,
            "fixpoint_reached": phase2_report.fixpoint_reached,
        },
    )

    # Final verification + summary stats
    final_run = esbmc.run(
        list(sources),
        violation_info_path=out_dir / "violation_final.json",
    )
    final_ok = bool(final_run.oracle and oracles_match(final_run.oracle, target_oracle))

    # Reduction stats
    res_final = solc.compile(list(sources))
    fn_count_final = len(collect_function_entries(res_final.ast or {})) if res_final.ok else 0
    lines_final = sum(len(s.read_text().splitlines()) for s in sources)

    res_original = solc.compile(list(originals))
    fn_count_original = len(collect_function_entries(res_original.ast or {})) if res_original.ok else 0
    lines_original = sum(len(o.read_text().splitlines()) for o in originals)

    manifest.finalise(
        {
            "status": "ok" if final_ok else "oracle_mismatch",
            "reduction": {
                "functions_original": fn_count_original,
                "functions_retained": fn_count_final,
                "lines_original": lines_original,
                "lines_retained": lines_final,
            },
            "final_oracle_matched": final_ok,
            "wall_sec": round(time.monotonic() - run_start, 2),
        }
    )
    print(
        f"MINIMISE: functions {fn_count_original} → {fn_count_final}, "
        f"lines {lines_original} → {lines_final}, "
        f"oracle_match={final_ok}, "
        f"wall={time.monotonic() - run_start:.1f}s"
    )
    return 0 if final_ok else 3


if __name__ == "__main__":
    sys.exit(main())
