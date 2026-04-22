"""Phase 0 — dead-code sweep.

Compiler-driven, no verifier calls. Removes declarations that are
syntactically unreferenced by any retained declaration in the unit:

  - `import` directives whose imported names are not used;
  - `struct` and `enum` definitions with no use site;
  - `event` and custom `error` definitions with no `emit` / `revert` use;
  - contract-level functions that have no callers within the program
    AND are not part of the mandatory set (locked_symbols). Orphan
    detection is deliberately conservative: only delete functions with
    visibility `internal` or `private` (public/external functions can
    be attacker entry points; removing them is Phase 2's job, which
    uses the verifier oracle for safety).

This phase mutates source files by byte-range splicing using the AST's
`src` field. After the sweep the program still compiles (verified by
a final `solc` pass).
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solc_driver import SolcDriver
from source_surgery import (
    SourceEdit,
    SrcRange,
    apply_edits,
    delete_range,
    parse_src,
)


@dataclass
class Phase0Report:
    removed: Dict[str, List[dict]] = field(
        default_factory=lambda: {
            "imports": [],
            "structs": [],
            "enums": [],
            "events": [],
            "errors": [],
            "functions": [],
        }
    )
    compilation_calls: int = 0


def _walk_identifiers(node, out: Set[str]) -> None:
    """Collect every `Identifier` name and `MemberAccess.memberName`
    appearing anywhere in the AST subtree."""
    if isinstance(node, dict):
        ntype = node.get("nodeType")
        if ntype == "Identifier":
            n = node.get("name")
            if isinstance(n, str):
                out.add(n)
        elif ntype == "MemberAccess":
            n = node.get("memberName")
            if isinstance(n, str):
                out.add(n)
        elif ntype == "UserDefinedTypeName":
            n = node.get("name")
            if isinstance(n, str):
                out.add(n)
            pt = node.get("pathNode")
            if isinstance(pt, dict):
                pn = pt.get("name")
                if isinstance(pn, str):
                    out.add(pn)
        elif ntype == "IdentifierPath":
            n = node.get("name")
            if isinstance(n, str):
                out.add(n)
        elif ntype == "ElementaryTypeNameExpression":
            # typeName may carry identifiers
            pass
        for v in node.values():
            _walk_identifiers(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_identifiers(v, out)


def _source_for(file: str, source_root: Path) -> str:
    return (source_root / file).read_text() if not Path(file).is_absolute() else Path(file).read_text()


def _write_source(file: str, source_root: Path, content: str) -> Path:
    path = Path(file) if Path(file).is_absolute() else source_root / file
    path.write_text(content)
    return path


def run(
    source_root: Path,
    sources: Sequence[Path],
    mandatory_bare_names: Set[str],
    solc: SolcDriver,
) -> Tuple[List[Path], Phase0Report]:
    """Mutate `sources` in-place under `source_root`, returning the
    (possibly new) list of sources and a Phase0Report.

    The `mandatory_bare_names` set lists every function/modifier/event
    that the mandatory set requires; we will never delete a declaration
    matching one of these names.
    """

    report = Phase0Report()

    # Compile once to get AST
    res = solc.compile(list(sources))
    report.compilation_calls += 1
    if not res.ok or res.ast is None:
        return list(sources), report

    ast = res.ast  # {filename: unit}

    # 1. Collect all `Identifier`/`MemberAccess`/`UserDefinedTypeName`
    #    names used anywhere in the program.
    used: Set[str] = set()
    for unit in ast.values():
        _walk_identifiers(unit, used)

    # 2. Per-file, decide which top-level declarations are safe to drop.
    per_file_edits: Dict[str, List[SourceEdit]] = {}
    per_file_sources: Dict[str, str] = {}

    def record(bucket: str, item: dict) -> None:
        report.removed[bucket].append(item)

    def edit_for_file(file: str) -> List[SourceEdit]:
        return per_file_edits.setdefault(file, [])

    def src_text(file: str) -> str:
        if file not in per_file_sources:
            per_file_sources[file] = _source_for(file, source_root)
        return per_file_sources[file]

    for filename, unit in ast.items():
        nodes = unit.get("nodes", [])
        for node in nodes:
            ntype = node.get("nodeType")
            rng = parse_src(node)
            if rng is None:
                continue

            if ntype == "ImportDirective":
                # Names it brings in: `symbolAliases` or implicit
                brought: List[str] = []
                aliases = node.get("symbolAliases") or []
                for a in aliases:
                    local = a.get("local") or (a.get("foreign") or {}).get("name")
                    if isinstance(local, str):
                        brought.append(local)
                # Wildcard imports or unit imports — skip deletion (too risky)
                if not brought:
                    continue
                if not any(name in used for name in brought):
                    edit_for_file(filename).append(
                        delete_range(src_text(filename), rng)
                    )
                    record(
                        "imports",
                        {"file": filename, "aliases": brought},
                    )
                continue

            if ntype == "StructDefinition":
                name = node.get("name", "")
                if name and name not in used and name not in mandatory_bare_names:
                    edit_for_file(filename).append(
                        delete_range(src_text(filename), rng)
                    )
                    record("structs", {"file": filename, "name": name})
                continue

            if ntype == "EnumDefinition":
                name = node.get("name", "")
                if name and name not in used and name not in mandatory_bare_names:
                    edit_for_file(filename).append(
                        delete_range(src_text(filename), rng)
                    )
                    record("enums", {"file": filename, "name": name})
                continue

            if ntype == "EventDefinition":  # file-level only; very rare
                name = node.get("name", "")
                if name and name not in used and name not in mandatory_bare_names:
                    edit_for_file(filename).append(
                        delete_range(src_text(filename), rng)
                    )
                    record("events", {"file": filename, "name": name})
                continue

            if ntype == "ErrorDefinition":
                name = node.get("name", "")
                if name and name not in used and name not in mandatory_bare_names:
                    edit_for_file(filename).append(
                        delete_range(src_text(filename), rng)
                    )
                    record("errors", {"file": filename, "name": name})
                continue

            if ntype == "ContractDefinition":
                # Walk contract members — check structs/events/errors/enums
                # and internal/private helper functions for orphanness
                contract_name = node.get("name", "")
                for member in node.get("nodes", []):
                    mtype = member.get("nodeType")
                    mrng = parse_src(member)
                    if mrng is None:
                        continue

                    if mtype == "EventDefinition":
                        mname = member.get("name", "")
                        if mname and mname not in used and mname not in mandatory_bare_names:
                            edit_for_file(filename).append(
                                delete_range(src_text(filename), mrng)
                            )
                            record(
                                "events",
                                {"file": filename, "contract": contract_name, "name": mname},
                            )
                        continue

                    if mtype == "ErrorDefinition":
                        mname = member.get("name", "")
                        if mname and mname not in used and mname not in mandatory_bare_names:
                            edit_for_file(filename).append(
                                delete_range(src_text(filename), mrng)
                            )
                            record(
                                "errors",
                                {"file": filename, "contract": contract_name, "name": mname},
                            )
                        continue

                    if mtype == "StructDefinition":
                        mname = member.get("name", "")
                        if mname and mname not in used and mname not in mandatory_bare_names:
                            edit_for_file(filename).append(
                                delete_range(src_text(filename), mrng)
                            )
                            record(
                                "structs",
                                {"file": filename, "contract": contract_name, "name": mname},
                            )
                        continue

                    if mtype == "EnumDefinition":
                        mname = member.get("name", "")
                        if mname and mname not in used and mname not in mandatory_bare_names:
                            edit_for_file(filename).append(
                                delete_range(src_text(filename), mrng)
                            )
                            record(
                                "enums",
                                {"file": filename, "contract": contract_name, "name": mname},
                            )
                        continue

                    # Orphan internal/private functions: no caller in `used`
                    if mtype == "FunctionDefinition":
                        vis = member.get("visibility", "")
                        if vis not in ("internal", "private"):
                            continue
                        fname = member.get("name") or member.get("kind", "function")
                        if fname in mandatory_bare_names:
                            continue
                        if fname in used:
                            continue
                        edit_for_file(filename).append(
                            delete_range(src_text(filename), mrng)
                        )
                        record(
                            "functions",
                            {
                                "file": filename,
                                "contract": contract_name,
                                "name": fname,
                                "visibility": vis,
                            },
                        )
                continue

    # 3. Apply edits + sanity-compile
    out_sources = []
    for src in sources:
        name = str(src.name)
        # solc uses just the filename when the file is passed by path;
        # match against the ast dict keys.
        matching_key = next(
            (k for k in ast.keys() if k == name or Path(k).name == src.name),
            None,
        )
        if matching_key is None or matching_key not in per_file_edits:
            out_sources.append(src)
            continue
        original = per_file_sources[matching_key]
        mutated = apply_edits(original, per_file_edits[matching_key])
        src.write_text(mutated)
        out_sources.append(src)

    # Final compile sanity check; if it fails, roll back.
    verify = solc.compile(list(out_sources), check_ast=False)
    report.compilation_calls += 1
    if not verify.ok:
        # Rollback: restore original sources we touched.
        for filename, content in per_file_sources.items():
            matching = next((s for s in sources if s.name == filename or str(s) == filename), None)
            if matching is not None:
                matching.write_text(content)
        # Nothing was removed.
        report.removed = {k: [] for k in report.removed}

    return out_sources, report
