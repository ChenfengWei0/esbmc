"""Phase 2 — verifier-driven semantic reduction.

Fixpoint loop over non-mandatory functions in the syntactic closure.
Each outer pass rebuilds the AST, recomputes weights against the
current retained set, and walks candidates in descending weight order:

  L1: try delete(f) → if compile & oracle hold, commit.
  L2: else try set_visibility(f, 'internal') → if compile & oracle hold,
      commit.
  L3: else preserve f.

Outer loop terminates when a full pass produces no commit (fixpoint)
or MAX_PASSES is hit (defensive cap — under-approximation guarantees
each committed step strictly shrinks, so the fixpoint is reachable
in at most |candidates| passes, but we cap anyway).

Iteration matters for output size, not just speed: within a single
pass the weighted list is frozen, so a callee tried before its caller
fails (caller still references it) and gets preserved; a later pass
sees the caller gone and removes the callee. Without iteration, that
callee would remain in the final output.

Weight (v1): 3·[f ∉ π] + 2·[no caller in retained] + 1·[vis ∈ {public, external}]
"""

from __future__ import annotations

import shutil
import sys as _sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oracle import Oracle, oracles_match
from esbmc_driver import ESBMCDriver
from solc_driver import SolcDriver
from source_surgery import (
    FunctionEntry,
    apply_edits,
    change_visibility_to_internal,
    collect_call_graph,
    collect_function_entries,
    delete_range,
    parse_src,
)


@dataclass
class Phase2Report:
    ordering_version: str = "v1"
    attempts: List[dict] = field(default_factory=list)
    verifier_calls: int = 0
    compilation_calls: int = 0
    passes: int = 0
    fixpoint_reached: bool = False


def _compute_weight(
    f: FunctionEntry,
    in_trace: bool,
    any_caller_retained: bool,
) -> int:
    w = 0
    if not in_trace:
        w += 3
    if not any_caller_retained:
        w += 2
    if f.visibility in ("public", "external"):
        w += 1
    return w


def _write_files(sources: Sequence[Path], snapshot: Dict[str, str]) -> None:
    for s in sources:
        key = next(
            (k for k in snapshot.keys() if k == s.name or Path(k).name == s.name),
            None,
        )
        if key is not None:
            s.write_text(snapshot[key])


def _take_snapshot(sources: Sequence[Path]) -> Dict[str, str]:
    return {s.name: s.read_text() for s in sources}


def run(
    source_root: Path,
    sources: Sequence[Path],
    mandatory_qualified: Set[str],
    trace_functions: Set[str],   # fully-qualified like "C.f"
    retained_before_phase2: Set[str],  # the Phase 1 closure
    esbmc: ESBMCDriver,
    solc: SolcDriver,
    target_oracle: Oracle,
) -> Tuple[List[Path], Phase2Report]:

    report = Phase2Report()

    # Defensive cap; fixpoint is reachable in at most |candidates| passes
    # (each commit strictly shrinks the retained set), so 8 is slack.
    MAX_PASSES = 8

    def has_any_retained_caller(
        qual: str,
        retained: Set[str],
        call_graph: Dict[str, List[str]],
    ) -> bool:
        for caller, callees in call_graph.items():
            if qual in callees and caller in retained and caller != qual:
                return True
        return False

    for pass_idx in range(MAX_PASSES):
        # Fresh AST at the start of each pass — the retained set has
        # shrunk, and `has_any_retained_caller` must see that.
        res = solc.compile(list(sources))
        report.compilation_calls += 1
        if not res.ok or res.ast is None:
            break

        ast = res.ast
        entries = collect_function_entries(ast)
        call_graph = collect_call_graph(ast)
        current_retained = {e.qualified for e in entries}

        candidates: List[FunctionEntry] = []
        for e in entries:
            if e.qualified in mandatory_qualified:
                continue
            if e.kind == "constructor":
                continue
            # Modifiers are neither deletable (retained functions
            # reference them — compile would fail) nor visibility-
            # reducible (they have no public/external concept). Skip
            # up front to save one compile per modifier per pass.
            if e.kind == "modifier":
                continue
            if e.qualified not in retained_before_phase2:
                continue
            candidates.append(e)

        if not candidates:
            report.fixpoint_reached = True
            report.passes = pass_idx + 1
            break

        weighted = []
        for f in candidates:
            in_trace = f.qualified in trace_functions
            any_caller_retained = has_any_retained_caller(
                f.qualified, current_retained, call_graph,
            )
            w = _compute_weight(f, in_trace, any_caller_retained)
            weighted.append((w, f.qualified, f))

        weighted.sort(key=lambda t: (-t[0], t[1]))

        snapshot_of_committed = _take_snapshot(sources)
        made_progress = False

        for w, qual, f in weighted:

            # R2 — try delete
            _write_files(sources, snapshot_of_committed)
            # Fresh AST to locate the current src range (shifts after
            # each committed delete within this pass).
            cur = solc.compile(list(sources))
            report.compilation_calls += 1
            if not cur.ok or cur.ast is None:
                report.attempts.append({
                    "pass": pass_idx, "fn": qual,
                    "op": "skip_precompile_failed",
                    "stderr_head": cur.stderr[:200],
                })
                continue
            cur_entries = collect_function_entries(cur.ast)
            cur_entry = next(
                (e for e in cur_entries if e.qualified == qual),
                None,
            )
            if cur_entry is None:
                report.attempts.append({
                    "pass": pass_idx, "fn": qual,
                    "op": "skip_already_absent",
                })
                continue

            # Apply delete in-place
            filename = cur_entry.source_file
            matching_path = next(
                (s for s in sources if s.name == filename or str(s) == filename),
                None,
            )
            if matching_path is None:
                report.attempts.append({
                    "pass": pass_idx, "fn": qual,
                    "op": "skip_missing_path",
                })
                continue
            src_text = matching_path.read_text()
            edit = delete_range(src_text, cur_entry.src)
            matching_path.write_text(apply_edits(src_text, [edit]))

            # Compile the candidate-less program
            c2 = solc.compile(list(sources))
            report.compilation_calls += 1
            wall = 0.0
            if c2.ok:
                vres = esbmc.run(list(sources))
                report.verifier_calls += 1
                wall = vres.wall_sec
                if vres.oracle and oracles_match(vres.oracle, target_oracle):
                    snapshot_of_committed = _take_snapshot(sources)
                    made_progress = True
                    report.attempts.append({
                        "pass": pass_idx, "fn": qual, "op": "delete",
                        "compile": True, "oracle": True,
                        "verdict": "deleted", "wall_sec": round(wall, 2),
                    })
                    continue

            # R3 — try visibility → internal
            _write_files(sources, snapshot_of_committed)
            if cur_entry.visibility in ("public", "external"):
                src_text = matching_path.read_text()
                edit = change_visibility_to_internal(src_text, cur_entry.src)
                if edit is not None:
                    matching_path.write_text(apply_edits(src_text, [edit]))
                    c3 = solc.compile(list(sources))
                    report.compilation_calls += 1
                    if c3.ok:
                        vres = esbmc.run(list(sources))
                        report.verifier_calls += 1
                        wall = vres.wall_sec
                        if vres.oracle and oracles_match(vres.oracle, target_oracle):
                            snapshot_of_committed = _take_snapshot(sources)
                            made_progress = True
                            report.attempts.append({
                                "pass": pass_idx, "fn": qual,
                                "op": "internal",
                                "compile": True, "oracle": True,
                                "verdict": "internalized",
                                "wall_sec": round(wall, 2),
                            })
                            continue

            # Preserved — restore
            _write_files(sources, snapshot_of_committed)
            report.attempts.append({
                "pass": pass_idx, "fn": qual, "op": "preserve",
                "verdict": "preserved",
                "wall_sec": round(wall, 2),
            })

        report.passes = pass_idx + 1
        if not made_progress:
            report.fixpoint_reached = True
            break

    return list(sources), report
