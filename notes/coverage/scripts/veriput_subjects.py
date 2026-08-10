#!/usr/bin/env python3
"""Prepared-subject resolver for VeriPUT benchmark adapters.

The POC runner owns `notes/coverage/poc_units/<id>/poc.json`.  The real
benchmarks under `/home/samson/workspace/VeriPUT/Results` instead use
`subjects/<id>/{flat.sol,meta.json}`.  This module is the narrow bridge between
those layouts and the lower-level drivers that already accept explicit
`--sol --ast --contract --unit` arguments.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path


VERIPUT_ROOT = Path(os.environ.get(
    "VERIPUT_ROOT", "/home/samson/workspace/VeriPUT"))
DEFAULT_AST_TIMEOUT_S = 60.0

KNOWN_SUBJECT_ROOTS = {
    "stress243": VERIPUT_ROOT / "Results" / "Stress243" / "subjects",
    "peer182": VERIPUT_ROOT / "Results" / "Peer182" / "subjects",
    "bugfix124": VERIPUT_ROOT / "Results" / "BugFix124" / "subjects",
}
FALLBACK_SUBJECT_ROOTS = {
    "peer182": (
        VERIPUT_ROOT / "scripts" / "Results" / "workdirs" / "Peer182"
        / "subjects",
    ),
    "bugfix124": (
        VERIPUT_ROOT / "scripts" / "Results" / "workdirs" / "BugFix124"
        / "subjects",
    ),
}
BUGFIX_DATASET_ROOT = VERIPUT_ROOT / "Datasets" / "Patch-Bug-Bench"
PEER_REQUIRED_SOURCE_SEGMENT = "contracts_080/"


class SubjectError(ValueError):
    """A prepared subject cannot be resolved safely."""


def _clean_key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def _subject_name_candidates(subject: str) -> tuple[str, ...]:
    out = []

    def add(name: str):
        if name and name not in out:
            out.append(name)

    add(subject)
    add(_clean_key(subject))
    match = re.match(r"^(.*)\s+\((\d+)\)$", subject)
    if match:
        base, index = match.groups()
        add(f"{base}__{index}")
        add(f"{base}_{index}")
    return tuple(out)


def _subject_dir_matches(path: Path, subject: str) -> bool:
    target = _clean_key(subject)
    if _clean_key(path.name) == target:
        return True
    meta = path / "meta.json"
    if not meta.exists():
        return False
    try:
        data = json.loads(meta.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    recorded = data.get("subject_id")
    return isinstance(recorded, str) and _clean_key(recorded) == target


def _find_subject_in_roots(subject: str, roots: list[Path]) -> list[Path]:
    candidates = _subject_name_candidates(subject)
    found = []
    for root in roots:
        for name in candidates:
            direct = root / name
            if (direct / "meta.json").exists():
                found.append(direct)
    if found:
        return found
    for root in roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if _subject_dir_matches(child, subject):
                found.append(child)
    return found


def _bugfix_dataset_subject_roots() -> tuple[Path, ...]:
    if not BUGFIX_DATASET_ROOT.is_dir():
        return ()
    return tuple(
        p for p in sorted(BUGFIX_DATASET_ROOT.iterdir())
        if p.is_dir() and p.name.startswith("class"))


def _normalize_dataset_bugfix_meta(meta: dict, d: Path) -> dict:
    out = dict(meta)
    out.setdefault("subject_id", out.get("id") or d.name)
    out.setdefault("benchmark", "bugfix124")
    out.setdefault("contract", out.get("target_contract"))
    out.setdefault("status", "ok")
    version_block = out.get("solc_version") or {}
    if isinstance(version_block, dict):
        out.setdefault("solc", version_block.get("fix") or version_block.get("bug"))
    out.setdefault("source_variant", "fix")
    out.setdefault("source_layout", "patch-bug-bench-dataset")
    return out


def _is_peer_contract080_meta(meta: dict) -> bool:
    source_file = meta.get("source_file") or ""
    return bool(meta.get("source_080")) and \
        PEER_REQUIRED_SOURCE_SEGMENT in source_file


def _peer_contract080_refusal(meta: dict, d: Path) -> str:
    source_file = meta.get("source_file") or ""
    return (
        f"{d} is not a peer contract080 target: expected source_080=true "
        f"and source_file containing {PEER_REQUIRED_SOURCE_SEGMENT!r}; "
        f"got source_080={bool(meta.get('source_080'))} "
        f"source_file={source_file!r}")


def _rehome_veriput_path(path: str | None) -> str | None:
    if not path:
        return path
    p = Path(path).expanduser()
    if p.exists() or not p.is_absolute():
        return path
    for marker in (
            Path("/home/samson/workspace/VeriPUT"),
            Path("/home/administrator/VeriPUT"),
    ):
        try:
            rel = p.relative_to(marker)
        except ValueError:
            continue
        moved = VERIPUT_ROOT / rel
        if moved.exists():
            return str(moved)
    return path


def _infer_solc_bin(metadata: dict) -> str | None:
    recorded = metadata.get("inferred_solc_bin")
    if isinstance(recorded, str) and recorded:
        return _portable_solc_bin(recorded)
    version = metadata.get("solc")
    if not isinstance(version, str) or not version:
        version_block = metadata.get("solc_version") or {}
        if isinstance(version_block, dict):
            version = version_block.get("fix") or version_block.get("bug")
    if isinstance(version, str) and version:
        local = (Path.home() / ".solc-select" / "artifacts"
                 / f"solc-{version}" / f"solc-{version}")
        if local.exists():
            return str(local)
    compile_block = metadata.get("compile") or {}
    cmd = compile_block.get("cmd") if isinstance(compile_block, dict) else None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return None
    if not argv:
        return None
    first = argv[0]
    name = Path(first).name
    if name == "solc" or name.startswith("solc-"):
        return _portable_solc_bin(first)
    return None


def _portable_solc_bin(path: str | None) -> str | None:
    if not path:
        return path
    recorded = Path(path).expanduser()
    if recorded.exists():
        return str(recorded)
    parts = recorded.parts
    try:
        artifacts_idx = parts.index("artifacts")
    except ValueError:
        return path
    tail = parts[artifacts_idx + 1:]
    if len(tail) < 2:
        return path
    local = Path.home() / ".solc-select" / "artifacts" / Path(*tail)
    if local.exists():
        return str(local)
    return path


@dataclass(frozen=True)
class PreparedSubject:
    benchmark: str
    subject_id: str
    root: str
    flat_sol: str
    solast: str
    contract: str
    unit: str
    solc_bin: str | None
    solc_extra: tuple[str, ...]
    metadata: dict

    @property
    def benchmark_key(self) -> str:
        return f"{_clean_key(self.benchmark)}__{_clean_key(self.subject_id)}"

    @property
    def inferred_solc_bin(self) -> str | None:
        inferred = _infer_solc_bin(self.metadata)
        if self.solc_bin and self.solc_bin_source == "inferred":
            return inferred or self.solc_bin
        if self.solc_bin:
            return None
        return inferred

    @property
    def solc_bin_source(self) -> str:
        source = self.metadata.get("solc_bin_source")
        if source in ("explicit", "inferred", "missing"):
            return source
        if self.solc_bin:
            return "explicit"
        if _infer_solc_bin(self.metadata):
            return "inferred"
        return "missing"

    def with_inferred_solc_bin(self) -> "PreparedSubject":
        inferred = self.inferred_solc_bin
        if not inferred:
            return self
        metadata = dict(self.metadata)
        metadata["inferred_solc_bin"] = inferred
        metadata["solc_bin_source"] = "inferred"
        return replace(self, solc_bin=inferred, metadata=metadata)

    def with_solast_path(self, solast: str, *, source: str) -> "PreparedSubject":
        metadata = dict(self.metadata)
        metadata["solast_source"] = source
        return replace(self, solast=solast, metadata=metadata)

    def to_record(self) -> dict:
        return {
            "schema": "veriput-subject/v1",
            "benchmark": self.benchmark,
            "subject_id": self.subject_id,
            "benchmark_key": self.benchmark_key,
            "root": self.root,
            "flat_sol": self.flat_sol,
            "solast": self.solast,
            "solast_source": self.metadata.get("solast_source") or "prepared",
            "contract": self.contract,
            "unit": self.unit,
            "solc_bin": self.solc_bin,
            "solc_bin_source": self.solc_bin_source,
            "solc": self.metadata.get("solc"),
            "inferred_solc_bin": self.inferred_solc_bin,
            "solc_extra": list(self.solc_extra),
            "meta_status": self.metadata.get("status"),
        }


@dataclass(frozen=True)
class UnitEnumeration:
    contract: str
    units: tuple[str, ...]
    skipped: tuple[dict, ...]
    unit_info: tuple[dict, ...] = ()
    no_unit_reason: str = ""

    def to_record(self) -> dict:
        record = {
            "schema": "veriput-subject-units/v1",
            "contract": self.contract,
            "units": list(self.units),
            "unit_info": list(self.unit_info),
            "skipped": list(self.skipped),
        }
        if not self.units:
            record["schedulable"] = False
            record["no_unit_reason"] = self.no_unit_reason or (
                "target contract has no public/external FunctionDefinition units")
        else:
            record["schedulable"] = True
        return record


def _subject_dir(subject: str | None, root: str | None,
                 benchmark: str | None) -> Path:
    if root:
        base = Path(root).expanduser().resolve()
        if subject:
            found = _find_subject_in_roots(subject, [base])
            if found:
                return found[0].resolve()
            return base / subject
        return base
    if subject:
        p = Path(subject).expanduser()
        if p.is_dir():
            return p.resolve()
        roots = subject_roots(benchmark) if benchmark else [
            root for bench in KNOWN_SUBJECT_ROOTS
            for root in subject_roots(bench)
        ]
        found = _find_subject_in_roots(subject, list(roots))
        if not found:
            raise SubjectError(f"prepared subject {subject!r} was not found")
        if len(found) > 1 and not benchmark:
            names = ", ".join(str(p) for p in found)
            raise SubjectError(
                f"prepared subject {subject!r} is ambiguous: {names}")
        return found[0].resolve()
    raise SubjectError("pass --subject-dir or --subject-id")


def subject_roots(benchmark: str) -> tuple[Path, ...]:
    if benchmark not in KNOWN_SUBJECT_ROOTS:
        raise SubjectError(
            f"unknown subject benchmark {benchmark!r}; known: "
            + ", ".join(sorted(KNOWN_SUBJECT_ROOTS)))
    roots = [KNOWN_SUBJECT_ROOTS[benchmark],
             *FALLBACK_SUBJECT_ROOTS.get(benchmark, ())]
    if benchmark == "bugfix124":
        roots.extend(_bugfix_dataset_subject_roots())
    return tuple(roots)


def _solast_for(flat: Path) -> Path:
    appended = flat.with_name(flat.name + ".solast")
    replaced = flat.with_suffix(".solast")
    if appended.exists():
        return appended
    if replaced.exists():
        return replaced
    return appended


def resolve_subject(subject: str | None = None, *, root: str | None = None,
                    benchmark: str | None = None,
                    unit: str | None = None,
                    require_unit: bool = True) -> PreparedSubject:
    if benchmark and benchmark not in KNOWN_SUBJECT_ROOTS:
        raise SubjectError(
            f"unknown subject benchmark {benchmark!r}; known: "
            + ", ".join(sorted(KNOWN_SUBJECT_ROOTS)))
    d = _subject_dir(subject, root, benchmark)
    meta_path = d / "meta.json"
    if not meta_path.exists():
        raise SubjectError(f"{d} has no meta.json")
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError as exc:
        raise SubjectError(f"{meta_path} is not valid JSON: {exc}") from exc
    flat = d / "flat.sol"
    if not flat.exists() and benchmark == "bugfix124" and (d / "fix.flat.sol").exists():
        flat = d / "fix.flat.sol"
        meta = _normalize_dataset_bugfix_meta(meta, d)
    status = meta.get("status")
    if status is not None and status != "ok":
        raise SubjectError(
            f"{d} is not a usable subject: status={meta.get('status')!r}")
    bench = benchmark or meta.get("benchmark") or d.parent.parent.name.lower()
    if bench == "peer182" and not _is_peer_contract080_meta(meta):
        raise SubjectError(_peer_contract080_refusal(meta, d))
    if not flat.exists():
        raise SubjectError(f"{d} has no flat.sol or dataset fix.flat.sol")
    contract = meta.get("contract") or meta.get("target_contract")
    if not contract:
        raise SubjectError(f"{meta_path} has no target contract")
    if require_unit and not unit:
        raise SubjectError(
            f"{d} is a contract-level subject; pass an explicit --unit")
    return PreparedSubject(
        benchmark=bench,
        subject_id=meta.get("subject_id") or d.name,
        root=str(d),
        flat_sol=str(flat.resolve()),
        solast=str(_solast_for(flat).resolve()),
        contract=contract,
        unit=unit or "",
        solc_bin=_portable_solc_bin(meta.get("solc_bin")),
        solc_extra=tuple(meta.get("solc_extra") or ()),
        metadata=meta,
    )


def subject_from_record(record: dict) -> PreparedSubject | None:
    data = record.get("subject")
    if not isinstance(data, dict):
        return None
    if data.get("schema") != "veriput-subject/v1":
        raise SubjectError(
            f"unknown subject schema {data.get('schema')!r} in cert row")
    required = ("benchmark", "subject_id", "root", "flat_sol", "solast",
                "contract", "unit")
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise SubjectError(
            "cert row subject block is missing: " + ", ".join(missing))
    flat_s = _rehome_veriput_path(data["flat_sol"])
    root_s = _rehome_veriput_path(data["root"])
    solast_s = _rehome_veriput_path(data["solast"])
    flat = Path(flat_s)
    root = Path(root_s)
    if not flat.exists() or not root.exists():
        resolved = resolve_subject(
            data["subject_id"],
            benchmark=data["benchmark"],
            unit=data["unit"],
            require_unit=False)
        if solast_s and Path(solast_s).exists():
            resolved = resolved.with_solast_path(solast_s, source="record")
        return resolved
    return PreparedSubject(
        benchmark=data["benchmark"],
        subject_id=data["subject_id"],
        root=root_s,
        flat_sol=flat_s,
        solast=solast_s,
        contract=data["contract"],
        unit=data["unit"],
        solc_bin=data.get("solc_bin"),
        solc_extra=tuple(data.get("solc_extra") or ()),
        metadata={
            "status": data.get("meta_status"),
            "solc": data.get("solc"),
            "inferred_solc_bin": data.get("inferred_solc_bin"),
            "solc_bin_source": data.get("solc_bin_source"),
            "solast_source": data.get("solast_source"),
        },
    )


def _read_compact_ast(path: Path) -> dict:
    text = path.read_text(errors="replace")
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise SubjectError(f"{path} does not contain a compact JSON AST")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise SubjectError(f"{path} is not valid compact JSON AST: {exc}") \
            from exc


def _walk_contracts(node, out):
    if isinstance(node, list):
        for item in node:
            _walk_contracts(item, out)
        return
    if not isinstance(node, dict):
        return
    if node.get("nodeType") == "ContractDefinition":
        out[node.get("id")] = node
    for value in node.values():
        if isinstance(value, (list, dict)):
            _walk_contracts(value, out)


def _param_count(node: dict, field: str) -> int:
    params = (node.get(field) or {}).get("parameters") or []
    return len(params) if isinstance(params, list) else 0


def _param_type_strings(node: dict, field: str) -> tuple[str, ...]:
    params = (node.get(field) or {}).get("parameters") or []
    if not isinstance(params, list):
        return ()
    out = []
    for param in params:
        if not isinstance(param, dict):
            out.append("")
            continue
        desc = param.get("typeDescriptions") or {}
        out.append(desc.get("typeString") or desc.get("typeIdentifier") or "")
    return tuple(out)


def _type_string(node: dict | None) -> str:
    if not isinstance(node, dict):
        return ""
    desc = node.get("typeDescriptions") or {}
    return desc.get("typeString") or desc.get("typeIdentifier") or ""


def _state_getter_signature(var: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """ABI shape for a public state variable getter, conservatively.

    Solidity synthesizes getter functions for public state variables.  Mapping
    keys and array indices become getter parameters; the remaining leaf becomes
    the return value.  We only need enough structure for scheduling and target
    filtering, so unsupported leaf details stay as a single return slot instead
    of causing the whole subject to disappear.
    """
    params: list[str] = []
    node = var.get("typeName")
    leaf = node
    while isinstance(node, dict):
        kind = node.get("nodeType")
        if kind == "Mapping":
            key = node.get("keyType") or node.get("keyName")
            params.append(_type_string(key) or "mapping-key")
            node = node.get("valueType")
            leaf = node
            continue
        if kind == "ArrayTypeName":
            params.append("uint256")
            node = node.get("baseType")
            leaf = node
            continue
        break
    ret = _type_string(leaf) or _type_string(var.get("typeName")) or \
        _type_string(var) or "unknown"
    return tuple(params), (ret,)


def _state_getter_unit_info(var: dict, owner_name: str) -> dict | None:
    name = var.get("name") or ""
    if not name:
        return None
    params, returns = _state_getter_signature(var)
    info = {
        "name": name,
        "contract": owner_name,
        "visibility": "public",
        "state_mutability": "view",
        "parameter_count": len(params),
        "parameter_types": list(params),
        "return_count": len(returns),
        "return_types": list(returns),
        "implemented": True,
        "synthetic_kind": "public-state-getter",
    }
    node_id = var.get("id")
    if node_id is not None:
        info["ast_id"] = node_id
    return info


def _function_units(contract_node: dict, owner_name: str):
    for node in contract_node.get("nodes") or []:
        if node.get("nodeType") != "FunctionDefinition":
            continue
        kind = node.get("kind", "function")
        visibility = node.get("visibility")
        name = node.get("name") or ""
        if kind != "function":
            if kind in ("fallback", "receive"):
                yield None, {
                    "contract": owner_name,
                    "kind": kind,
                    "reason": "fallback/receive has no named focus-function",
                }, None
            continue
        if visibility not in ("public", "external"):
            continue
        if not bool(node.get("implemented", True)):
            yield None, {
                "contract": owner_name,
                "kind": "unimplemented-function",
                "name": name,
                "reason": (
                    "public/external declaration has no FunctionDefinition body"),
            }, None
            continue
        if not name:
            yield None, {
                "contract": owner_name,
                "kind": kind,
                "reason": "public/external function has no name",
            }, None
            continue
        node_id = node.get("id")
        info = {
            "name": name,
            "contract": owner_name,
            "visibility": visibility,
            "state_mutability": node.get("stateMutability") or "",
            "parameter_count": _param_count(node, "parameters"),
            "parameter_types": list(_param_type_strings(node, "parameters")),
            "return_count": _param_count(node, "returnParameters"),
            "return_types": list(_param_type_strings(node, "returnParameters")),
            "implemented": bool(node.get("implemented", True)),
        }
        if node_id is not None:
            info["ast_id"] = node_id
            info["path_function"] = f"sol:@C@{owner_name}@F@{name}#{node_id}"
        yield name, None, info


def _no_unit_diagnostics(contract_node: dict, owner_name: str) -> list[dict]:
    out = []
    contract_kind = contract_node.get("contractKind")
    if contract_kind == "library":
        out.append({
            "contract": owner_name,
            "kind": "library-contract",
            "reason": "library target has no externally callable unit",
        })
    elif contract_kind == "interface":
        out.append({
            "contract": owner_name,
            "kind": "interface-contract",
            "reason": "interface target has declarations but no executable body",
        })
    if bool(contract_node.get("abstract")):
        out.append({
            "contract": owner_name,
            "kind": "abstract-contract",
            "reason": "abstract target may have no implemented public/external unit",
        })
    saw_node = False
    for node in contract_node.get("nodes") or []:
        if node.get("nodeType") != "FunctionDefinition":
            continue
        saw_node = True
        kind = node.get("kind", "function")
        visibility = node.get("visibility")
        name = node.get("name") or ""
        if kind == "constructor":
            out.append({
                "contract": owner_name,
                "kind": "constructor",
                "reason": "constructor is not a focus-function",
            })
        elif kind == "function" and visibility not in ("public", "external"):
            out.append({
                "contract": owner_name,
                "kind": "non-public-function",
                "name": name,
                "visibility": visibility,
                "reason": "function is not public/external",
            })
        elif kind == "function" and not bool(node.get("implemented", True)):
            out.append({
                "contract": owner_name,
                "kind": "unimplemented-function",
                "name": name,
                "visibility": visibility,
                "reason": "public/external declaration has no executable body",
            })
    if not saw_node:
        out.append({
            "contract": owner_name,
            "kind": "no-function-definitions",
            "reason": "target AST contains no FunctionDefinition nodes",
        })
    return out


def _classify_no_unit_reason(skipped: list[dict]) -> str:
    kinds = {row.get("kind") for row in skipped}
    if "library-contract" in kinds:
        return "target contract is a library, so no external transaction unit is schedulable"
    if "interface-contract" in kinds:
        return "target contract is an interface with no executable public/external body"
    if "public-state-getter" in kinds and all(
            row.get("kind") == "public-state-getter" for row in skipped):
        return (
            "target only exposes zero-argument public state getters; "
            "use structural getter-only concrete fallback")
    if "fallback" in kinds or "receive" in kinds:
        callable_kinds = {"fallback", "receive", "constructor"}
        if all(row.get("kind") in callable_kinds for row in skipped):
            return (
                "target only exposes constructor/fallback/receive entries, "
                "which have no named focus-function")
    if "non-public-function" in kinds:
        return "target functions are not public/external, so no unit is schedulable"
    if "unimplemented-function" in kinds or "abstract-contract" in kinds:
        return "target has no implemented public/external function body"
    if "constructor" in kinds:
        return "target only has constructor-level behavior and no named callable unit"
    if "no-function-definitions" in kinds:
        return "target AST has no FunctionDefinition nodes"
    return "target contract has no schedulable public/external unit"


def enumerate_subject_units(subject: PreparedSubject) -> UnitEnumeration:
    """Named public/external function units for the target contract.

    The result is strictly scoped to declarations owned by the target
    contract.  Inherited declarations belong to the base contract and are not
    separate target units for this evaluation.  Public state variable getters
    are reported as skipped rather than invented from source text; they are
    ABI entry points but not `--focus-function` names backed by a
    FunctionDefinition in the AST.
    """
    ast_path = Path(subject.solast)
    if not ast_path.exists():
        raise SubjectError(
            f"{ast_path} does not exist; run ensure_solast() before "
            "enumerating units")
    ast = _read_compact_ast(ast_path)
    contracts = {}
    _walk_contracts(ast, contracts)
    target_matches = [
        c for c in contracts.values() if c.get("name") == subject.contract
    ]
    if not target_matches:
        names = sorted({
            c.get("name") for c in contracts.values() if c.get("name")
        })
        raise SubjectError(
            f"target contract {subject.contract!r} is absent from {ast_path}; "
            f"available contracts: {', '.join(names[:25])}")
    if len(target_matches) > 1:
        ids = ", ".join(str(c.get("id")) for c in target_matches)
        raise SubjectError(
            f"target contract {subject.contract!r} is ambiguous in "
            f"{ast_path}: matching AST ids {ids}")
    target = target_matches[0]

    # The flattened AST contains the complete inheritance graph.  The target
    # contract is the evaluation unit, so do not silently turn inherited
    # declarations into target units or instrument their modifiers.
    ordered_ids = [target.get("id")]
    units = []
    unit_info = []
    seen_signatures = set()
    skipped = []
    for cid in ordered_ids:
        node = contracts.get(cid)
        if not node:
            continue
        owner = node.get("name") or f"<contract {cid}>"
        for name, skip, info in _function_units(node, owner):
            if skip:
                skipped.append(skip)
                continue
            sig = (
                name,
                tuple((info or {}).get("parameter_types") or ()),
            )
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                units.append(name)
                unit_info.append(info)
        for child in node.get("nodes") or []:
            if child.get("nodeType") == "VariableDeclaration" and \
                    child.get("visibility") == "public":
                getter_info = _state_getter_unit_info(child, owner)
                # Zero-argument public getters are already handled by the
                # structural getter-only concrete fallback.  Parameterized
                # getters, especially mappings and arrays, expose calldata
                # coordinates and must remain schedulable instead of being
                # dropped before Stage 2.
                if getter_info and getter_info.get("parameter_count", 0) > 0:
                    sig = (
                        getter_info["name"],
                        tuple(getter_info.get("parameter_types") or ()),
                    )
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        units.append(getter_info["name"])
                        unit_info.append(getter_info)
                    continue
                skipped.append({
                    "contract": owner,
                    "kind": "public-state-getter",
                    "name": child.get("name"),
                    "parameter_count": (
                        getter_info or {}).get("parameter_count", 0),
                    "parameter_types": (
                        getter_info or {}).get("parameter_types", []),
                    "return_count": (
                        getter_info or {}).get("return_count", 0),
                    "return_types": (
                        getter_info or {}).get("return_types", []),
                    "reason": "public state getter is not a FunctionDefinition",
                })
    if not units:
        seen_diag = {
            (row.get("contract"), row.get("kind"), row.get("name"),
             row.get("visibility"), row.get("reason"))
            for row in skipped
        }
        for cid in ordered_ids:
            node = contracts.get(cid)
            if not node:
                continue
            owner = node.get("name") or f"<contract {cid}>"
            for row in _no_unit_diagnostics(node, owner):
                key = (row.get("contract"), row.get("kind"), row.get("name"),
                       row.get("visibility"), row.get("reason"))
                if key not in seen_diag:
                    skipped.append(row)
                    seen_diag.add(key)
    return UnitEnumeration(
        contract=subject.contract,
        units=tuple(units),
        unit_info=tuple(unit_info),
        skipped=tuple(skipped),
        no_unit_reason=_classify_no_unit_reason(skipped) if not units else "",
    )


def subject_dirs(benchmark: str, root: str | None = None):
    if root:
        base = Path(root).expanduser().resolve()
    else:
        if benchmark not in KNOWN_SUBJECT_ROOTS:
            raise SubjectError(
                f"unknown subject benchmark {benchmark!r}; known: "
                + ", ".join(sorted(KNOWN_SUBJECT_ROOTS)))
        base = next((r for r in subject_roots(benchmark) if r.is_dir()),
                    KNOWN_SUBJECT_ROOTS[benchmark])
    if not base.is_dir():
        raise SubjectError(f"subject root does not exist: {base}")
    dirs = sorted(p for p in base.iterdir() if (p / "meta.json").exists())
    if benchmark != "peer182":
        return dirs
    filtered = []
    for path in dirs:
        try:
            meta = json.loads((path / "meta.json").read_text())
        except (OSError, json.JSONDecodeError):
            filtered.append(path)
            continue
        if _is_peer_contract080_meta(meta):
            filtered.append(path)
    return filtered


def _solc_cmd(subject: PreparedSubject) -> list[str]:
    return [
        subject.solc_bin,
        *subject.solc_extra,
        "--ast-compact-json",
        subject.flat_sol,
    ]


def _unlink_quietly(path: Path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def generate_solast(subject: PreparedSubject,
                    timeout_s: float = DEFAULT_AST_TIMEOUT_S) -> dict:
    """Create a compact AST without leaving corrupt partial output behind."""
    ast = Path(subject.solast)
    if ast.exists():
        return {
            "generated": False,
            "status": "exists",
            "path": str(ast),
        }
    if not subject.solc_bin and subject.inferred_solc_bin:
        subject = subject.with_inferred_solc_bin()
    if not subject.solc_bin:
        raise SubjectError(
            f"{subject.root} has no solc_bin, and {ast} does not exist")

    ast.parent.mkdir(parents=True, exist_ok=True)
    tmp = ast.with_name(f"{ast.name}.tmp.{os.getpid()}.{time.time_ns()}")
    cmd = _solc_cmd(subject)
    start = time.monotonic()
    try:
        with tmp.open("w") as stream:
            cp = subprocess.run(
                cmd,
                stdout=stream,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_s,
                check=False)
    except subprocess.TimeoutExpired as exc:
        _unlink_quietly(tmp)
        raise SubjectError(
            f"solc --ast-compact-json timed out after {timeout_s:g}s: "
            f"{subject.flat_sol}") from exc
    except OSError as exc:
        _unlink_quietly(tmp)
        raise SubjectError(
            f"solc --ast-compact-json could not start: {exc}") from exc

    wall_s = round(time.monotonic() - start, 3)
    stderr_tail = (cp.stderr or "")[-2000:]
    if cp.returncode:
        _unlink_quietly(tmp)
        raise SubjectError(
            f"solc --ast-compact-json failed rc={cp.returncode} "
            f"after {wall_s}s: {stderr_tail}")

    if ast.exists():
        _unlink_quietly(tmp)
        return {
            "generated": False,
            "status": "exists-after-race",
            "path": str(ast),
            "wall_s": wall_s,
            "stderr_tail": stderr_tail,
        }
    try:
        os.replace(tmp, ast)
    except OSError:
        _unlink_quietly(tmp)
        raise
    return {
        "generated": True,
        "status": "generated",
        "path": str(ast),
        "wall_s": wall_s,
        "stderr_tail": stderr_tail,
    }


def manifest_for_subject(subject: PreparedSubject, *, generate_ast=False,
                         ast_timeout_s: float = DEFAULT_AST_TIMEOUT_S) -> dict:
    try:
        run_subject = subject
        if generate_ast and not run_subject.solc_bin and run_subject.inferred_solc_bin:
            run_subject = run_subject.with_inferred_solc_bin()
        ast_info = generate_solast(run_subject, ast_timeout_s) \
            if generate_ast else {
                "generated": False,
                "status": "not-requested",
                "path": run_subject.solast,
            }
        if not Path(run_subject.solast).exists():
            return {
                "subject": run_subject.to_record(),
                "status": "missing-ast",
                "reason": f"{run_subject.solast} does not exist",
                "ast": ast_info,
            }
        enum = enumerate_subject_units(run_subject)
        return {
            "subject": run_subject.to_record(),
            "status": "ok",
            "ast_generated": bool(ast_info["generated"]),
            "ast": ast_info,
            "units": enum.to_record(),
        }
    except (OSError, SubjectError) as exc:
        return {
            "subject": subject.to_record(),
            "status": "error",
            "reason": str(exc),
        }


def unit_manifest(benchmark: str, subjects: list[PreparedSubject], *,
                  generate_ast=False,
                  ast_timeout_s: float = DEFAULT_AST_TIMEOUT_S) -> dict:
    rows = [manifest_for_subject(
        subject,
        generate_ast=generate_ast,
        ast_timeout_s=ast_timeout_s)
            for subject in subjects]
    summary = {
        "subjects": len(rows),
        "ok": sum(1 for row in rows if row["status"] == "ok"),
        "missing_ast": sum(1 for row in rows
                           if row["status"] == "missing-ast"),
        "error": sum(1 for row in rows if row["status"] == "error"),
        "units": sum(len((row.get("units") or {}).get("units") or [])
                     for row in rows),
        "skipped": sum(len((row.get("units") or {}).get("skipped") or [])
                       for row in rows),
    }
    return {
        "schema": "veriput-unit-manifest/v1",
        "benchmark": benchmark,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generate_ast": bool(generate_ast),
        "ast_timeout_s": ast_timeout_s,
        "summary": summary,
        "subjects": rows,
    }


def ensure_solast(subject: PreparedSubject,
                  timeout_s: float = DEFAULT_AST_TIMEOUT_S) -> bool:
    """Create `<flat.sol>.solast` for a prepared subject if it is absent.

    Returns True when it wrote the file and False when it already existed.
    """
    return bool(generate_solast(subject, timeout_s)["generated"])
