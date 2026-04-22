"""Phase 1 — compile-driven syntactic closure with 4-level fallback.

The closure is constructed by starting from the mandatory set `M` and
iteratively asking solc: "does the program compile if we keep only the
members in S?" On failure, solc's error messages identify the missing
symbols; we add them to S and retry.

If the closure compiles but the oracle does not hold, we escalate:

  L0: mandatory only
  L1: L0 ∪ all public/external functions in the contract
  L2: full P₀ (Phase-0 sweep output)
  L3: full P (before Phase 0) — diagnostic for sweep false positives

On success, the reduced source is written back in place of the input
sources, and the fallback level is recorded.
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
    SourceEdit,
    SrcRange,
    apply_edits,
    collect_function_entries,
    delete_range,
    parse_src,
)


@dataclass
class Phase1Report:
    mandatory_seed: List[str] = field(default_factory=list)
    syntactic_closure: List[str] = field(default_factory=list)
    fallback_level_used: Optional[int] = None
    compilation_calls: int = 0
    verifier_calls: int = 0
    attempts: List[dict] = field(default_factory=list)


def _keep_only(
    source_root: Path,
    originals: Dict[str, str],
    ast: Dict[str, dict],
    keep_qualified: Set[str],
    keep_bare_always: Set[str],
    sources: Sequence[Path],
) -> List[Path]:
    """Write out each source file with only the retained declarations."""

    for src_path in sources:
        # Match AST key (full path + basename both present in `ast`).
        matching_key = None
        for k in ast.keys():
            if k == str(src_path) or Path(k).name == src_path.name or k == src_path.name:
                matching_key = k
                break
        if matching_key is None:
            continue
        # Match originals snapshot key (we keyed by short filename).
        orig_key = next(
            (k for k in originals.keys() if k == src_path.name or Path(k).name == src_path.name),
            None,
        )
        if orig_key is None:
            continue
        original_text = originals[orig_key]
        unit = ast[matching_key]
        edits: List[SourceEdit] = []
        for node in unit.get("nodes", []):
            ntype = node.get("nodeType")
            rng = parse_src(node)
            if rng is None or ntype is None:
                continue
            if ntype == "ContractDefinition":
                cname = node.get("name", "")
                for member in node.get("nodes", []):
                    mtype = member.get("nodeType")
                    if mtype not in ("FunctionDefinition", "ModifierDefinition"):
                        continue
                    mrng = parse_src(member)
                    if mrng is None:
                        continue
                    mname = member.get("name") or member.get("kind", "function")
                    if member.get("kind") == "constructor":
                        continue
                    if mname in keep_bare_always:
                        continue
                    qual = f"{cname}.{mname}"
                    if qual in keep_qualified:
                        continue
                    edits.append(delete_range(original_text, mrng))
                continue
        mutated = apply_edits(original_text, edits)
        src_path.write_text(mutated)
    return list(sources)


def _collect_used_names(ast: Dict[str, dict]) -> Set[str]:
    # Re-implementation of Phase 0's walker (duplicated here to avoid
    # circular import). Cheap.
    used: Set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            ntype = node.get("nodeType")
            if ntype == "Identifier":
                n = node.get("name")
                if isinstance(n, str):
                    used.add(n)
            elif ntype == "MemberAccess":
                n = node.get("memberName")
                if isinstance(n, str):
                    used.add(n)
            elif ntype in ("UserDefinedTypeName", "IdentifierPath"):
                n = node.get("name")
                if isinstance(n, str):
                    used.add(n)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(ast)
    return used


def _add_dependencies(
    keep_qualified: Set[str],
    entries: List[FunctionEntry],
    ast: Dict[str, dict],
) -> Set[str]:
    """Walk the bodies of retained functions, pull in anything they
    reference by name. A single pass; the outer loop retries compilation
    so multi-hop dependencies converge."""

    # Build index: bare name -> list of entries
    by_name: Dict[str, List[FunctionEntry]] = {}
    for e in entries:
        by_name.setdefault(e.name, []).append(e)

    # For every retained function, walk its body
    additions: Set[str] = set()
    kept_entries = [e for e in entries if e.qualified in keep_qualified]
    for filename, unit in ast.items():
        for top in unit.get("nodes", []):
            if top.get("nodeType") != "ContractDefinition":
                continue
            cname = top.get("name", "")
            for member in top.get("nodes", []):
                mtype = member.get("nodeType")
                if mtype not in ("FunctionDefinition", "ModifierDefinition"):
                    continue
                mname = member.get("name") or member.get("kind", "function")
                qual = f"{cname}.{mname}"
                if qual not in keep_qualified:
                    continue
                referenced: Set[str] = set()

                def walk(n):
                    if isinstance(n, dict):
                        ntype = n.get("nodeType")
                        if ntype == "Identifier":
                            nm = n.get("name")
                            if isinstance(nm, str):
                                referenced.add(nm)
                        elif ntype == "MemberAccess":
                            nm = n.get("memberName")
                            if isinstance(nm, str):
                                referenced.add(nm)
                        for v in n.values():
                            walk(v)
                    elif isinstance(n, list):
                        for v in n:
                            walk(v)

                walk(member.get("body") or {})
                # Modifier invocations
                for mi in member.get("modifiers", []) or []:
                    mn = (mi.get("modifierName") or {}).get("name")
                    if isinstance(mn, str):
                        referenced.add(mn)

                for name in referenced:
                    for cand in by_name.get(name, []):
                        additions.add(cand.qualified)
    return additions - keep_qualified


def run(
    source_root: Path,
    sources: Sequence[Path],
    mandatory_qualified: Set[str],
    phase0_snapshot: Dict[str, str],
    esbmc: ESBMCDriver,
    solc: SolcDriver,
    target_oracle: Oracle,
    pre_phase0_snapshot: Optional[Dict[str, str]] = None,
) -> Tuple[List[Path], Phase1Report]:
    """Run Phase 1.

    `phase0_snapshot`: map filename -> source content AS OF the end of
                       Phase 0. Used to restore before each retry.
    `pre_phase0_snapshot`: the source content BEFORE Phase 0 ran. Only
                           needed for L3 fallback; pass None if unavailable.
    """

    report = Phase1Report(mandatory_seed=sorted(mandatory_qualified))

    def restore(snapshot: Dict[str, str]) -> None:
        for src_path in sources:
            key = next(
                (k for k in snapshot.keys() if k == src_path.name or Path(k).name == src_path.name),
                None,
            )
            if key is not None:
                src_path.write_text(snapshot[key])

    # ------------------------------------------------------------------
    # Level 0 — mandatory-only closure
    # ------------------------------------------------------------------

    restore(phase0_snapshot)
    res = solc.compile(list(sources))
    report.compilation_calls += 1
    if not res.ok or res.ast is None:
        report.attempts.append({"level": 0, "status": "solc_failed_pre_reduction"})
        return _finish_level_failed(sources, phase0_snapshot, esbmc, solc, target_oracle, report, 0, pre_phase0_snapshot)

    ast = res.ast
    entries = collect_function_entries(ast)
    keep_bare = {q.split(".", 1)[1] for q in mandatory_qualified if "." in q}
    # Constructors are kept by name == contract name; collect them.
    for e in entries:
        if e.kind == "constructor":
            keep_bare.add(e.name)

    S: Set[str] = set(mandatory_qualified)
    # Iterate: prune → compile → if missing, pull in deps
    while True:
        _keep_only(source_root, phase0_snapshot, ast, S, keep_bare, sources)
        cres = solc.compile(list(sources))
        report.compilation_calls += 1
        if cres.ok:
            break
        # Add any functions referenced by retained code but not yet in S
        deps = _add_dependencies(S, entries, ast)
        if not deps:
            # Compile failure unrelated to missing declarations: e.g.
            # state-var visibility vs inheritance. Give up on this level.
            report.attempts.append({"level": 0, "status": "compile_stuck", "stderr_head": cres.stderr[:400]})
            break
        S = S | deps

    report.syntactic_closure = sorted(S)

    if cres.ok:
        vres = esbmc.run(list(sources))
        report.verifier_calls += 1
        report.attempts.append({
            "level": 0,
            "status": "compiled",
            "oracle_matched": bool(vres.oracle and oracles_match(vres.oracle, target_oracle)),
            "wall_sec": round(vres.wall_sec, 2),
        })
        if vres.oracle and oracles_match(vres.oracle, target_oracle):
            report.fallback_level_used = 0
            return list(sources), report

    # ------------------------------------------------------------------
    # Level 1 — add all public/external functions
    # ------------------------------------------------------------------

    restore(phase0_snapshot)
    pub_ext = {e.qualified for e in entries if e.visibility in ("public", "external")}
    S1 = set(mandatory_qualified) | pub_ext
    while True:
        _keep_only(source_root, phase0_snapshot, ast, S1, keep_bare, sources)
        cres = solc.compile(list(sources))
        report.compilation_calls += 1
        if cres.ok:
            break
        deps = _add_dependencies(S1, entries, ast)
        if not deps:
            break
        S1 = S1 | deps

    if cres.ok:
        vres = esbmc.run(list(sources))
        report.verifier_calls += 1
        report.attempts.append({
            "level": 1,
            "status": "compiled",
            "oracle_matched": bool(vres.oracle and oracles_match(vres.oracle, target_oracle)),
            "wall_sec": round(vres.wall_sec, 2),
        })
        if vres.oracle and oracles_match(vres.oracle, target_oracle):
            report.syntactic_closure = sorted(S1)
            report.fallback_level_used = 1
            return list(sources), report

    # ------------------------------------------------------------------
    # Level 2 — full P₀. The closure is "every function in the
    # Phase-0-swept program", because we've given up on narrowing by
    # mandatory closure at this level and want Phase 2 to iterate over
    # the entire remaining function set.
    # ------------------------------------------------------------------

    restore(phase0_snapshot)
    vres = esbmc.run(list(sources))
    report.verifier_calls += 1
    report.attempts.append({
        "level": 2,
        "status": "full_p0",
        "oracle_matched": bool(vres.oracle and oracles_match(vres.oracle, target_oracle)),
        "wall_sec": round(vres.wall_sec, 2),
    })
    if vres.oracle and oracles_match(vres.oracle, target_oracle):
        # Refresh the closure from the current (restored) AST.
        cres2 = solc.compile(list(sources))
        report.compilation_calls += 1
        if cres2.ok and cres2.ast is not None:
            report.syntactic_closure = sorted(
                e.qualified for e in collect_function_entries(cres2.ast)
            )
        else:
            report.syntactic_closure = []
        report.fallback_level_used = 2
        return list(sources), report

    # ------------------------------------------------------------------
    # Level 3 — full P (before Phase 0). Same closure semantics: every
    # function in the pre-sweep program.
    # ------------------------------------------------------------------

    if pre_phase0_snapshot is not None:
        restore(pre_phase0_snapshot)
        vres = esbmc.run(list(sources))
        report.verifier_calls += 1
        report.attempts.append({
            "level": 3,
            "status": "pre_phase0_diagnostic",
            "oracle_matched": bool(vres.oracle and oracles_match(vres.oracle, target_oracle)),
            "wall_sec": round(vres.wall_sec, 2),
        })
        if vres.oracle and oracles_match(vres.oracle, target_oracle):
            cres3 = solc.compile(list(sources))
            report.compilation_calls += 1
            if cres3.ok and cres3.ast is not None:
                report.syntactic_closure = sorted(
                    e.qualified for e in collect_function_entries(cres3.ast)
                )
            else:
                report.syntactic_closure = []
            report.fallback_level_used = 3
            return list(sources), report

    report.fallback_level_used = None  # total failure
    return list(sources), report


def _finish_level_failed(sources, snapshot, esbmc, solc, target_oracle, report, level, pre_snap):
    """Handle the case where we can't even get a compile at some level;
    just return what we have with a None fallback_level."""
    report.fallback_level_used = None
    return list(sources), report
