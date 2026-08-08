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


class SubjectError(ValueError):
    """A prepared subject cannot be resolved safely."""


def _clean_key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


def _infer_solc_bin(metadata: dict) -> str | None:
    recorded = metadata.get("inferred_solc_bin")
    if isinstance(recorded, str) and recorded:
        return recorded
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
        return first
    return None


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

    def to_record(self) -> dict:
        return {
            "schema": "veriput-subject-units/v1",
            "contract": self.contract,
            "units": list(self.units),
            "unit_info": list(self.unit_info),
            "skipped": list(self.skipped),
        }


def _subject_dir(subject: str | None, root: str | None,
                 benchmark: str | None) -> Path:
    if root:
        base = Path(root).expanduser().resolve()
        if subject:
            return base / subject
        return base
    if subject:
        p = Path(subject).expanduser()
        if p.is_dir():
            return p.resolve()
        roots = ([KNOWN_SUBJECT_ROOTS[benchmark]]
                 if benchmark else list(KNOWN_SUBJECT_ROOTS.values()))
        found = [r / subject for r in roots if (r / subject / "meta.json").exists()]
        if not found:
            raise SubjectError(f"prepared subject {subject!r} was not found")
        if len(found) > 1:
            names = ", ".join(str(p) for p in found)
            raise SubjectError(
                f"prepared subject {subject!r} is ambiguous: {names}")
        return found[0].resolve()
    raise SubjectError("pass --subject-dir or --subject-id")


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
    if meta.get("status") != "ok":
        raise SubjectError(
            f"{d} is not a usable subject: status={meta.get('status')!r}")
    flat = d / "flat.sol"
    if not flat.exists():
        raise SubjectError(f"{d} has no flat.sol")
    contract = meta.get("contract")
    if not contract:
        raise SubjectError(f"{meta_path} has no target contract")
    if require_unit and not unit:
        raise SubjectError(
            f"{d} is a contract-level subject; pass an explicit --unit")
    bench = benchmark or meta.get("benchmark") or d.parent.parent.name.lower()
    return PreparedSubject(
        benchmark=bench,
        subject_id=meta.get("subject_id") or d.name,
        root=str(d),
        flat_sol=str(flat.resolve()),
        solast=str(_solast_for(flat).resolve()),
        contract=contract,
        unit=unit or "",
        solc_bin=meta.get("solc_bin"),
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
    return PreparedSubject(
        benchmark=data["benchmark"],
        subject_id=data["subject_id"],
        root=data["root"],
        flat_sol=data["flat_sol"],
        solast=data["solast"],
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
        yield name, None, {
            "name": name,
            "contract": owner_name,
            "visibility": visibility,
            "state_mutability": node.get("stateMutability") or "",
            "parameter_count": _param_count(node, "parameters"),
            "return_count": _param_count(node, "returnParameters"),
            "implemented": bool(node.get("implemented", True)),
        }


def enumerate_subject_units(subject: PreparedSubject) -> UnitEnumeration:
    """Named public/external function units for the target contract.

    The result is target-contract scoped and includes inherited callable
    functions through Solidity's `linearizedBaseContracts`.  Public state
    variable getters are reported as skipped rather than invented from source
    text; they are ABI entry points but not `--focus-function` names backed by
    a FunctionDefinition in the AST.
    """
    ast_path = Path(subject.solast)
    if not ast_path.exists():
        raise SubjectError(
            f"{ast_path} does not exist; run ensure_solast() before "
            "enumerating units")
    ast = _read_compact_ast(ast_path)
    contracts = {}
    _walk_contracts(ast, contracts)
    target = next((c for c in contracts.values()
                   if c.get("name") == subject.contract), None)
    if target is None:
        raise SubjectError(
            f"contract {subject.contract!r} is absent from {ast_path}")

    ordered_ids = target.get("linearizedBaseContracts") or [target.get("id")]
    units = []
    unit_info = []
    seen = set()
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
            if name not in seen:
                seen.add(name)
                units.append(name)
                unit_info.append(info)
        for child in node.get("nodes") or []:
            if child.get("nodeType") == "VariableDeclaration" and \
                    child.get("visibility") == "public":
                skipped.append({
                    "contract": owner,
                    "kind": "public-state-getter",
                    "name": child.get("name"),
                    "reason": "public state getter is not a FunctionDefinition",
                })
    return UnitEnumeration(
        contract=subject.contract,
        units=tuple(units),
        unit_info=tuple(unit_info),
        skipped=tuple(skipped),
    )


def subject_dirs(benchmark: str, root: str | None = None):
    if root:
        base = Path(root).expanduser().resolve()
    else:
        if benchmark not in KNOWN_SUBJECT_ROOTS:
            raise SubjectError(
                f"unknown subject benchmark {benchmark!r}; known: "
                + ", ".join(sorted(KNOWN_SUBJECT_ROOTS)))
        base = KNOWN_SUBJECT_ROOTS[benchmark]
    if not base.is_dir():
        raise SubjectError(f"subject root does not exist: {base}")
    return sorted(p for p in base.iterdir() if (p / "meta.json").exists())


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
        ast_info = generate_solast(subject, ast_timeout_s) \
            if generate_ast else {
                "generated": False,
                "status": "not-requested",
                "path": subject.solast,
            }
        if not Path(subject.solast).exists():
            return {
                "subject": subject.to_record(),
                "status": "missing-ast",
                "reason": f"{subject.solast} does not exist",
                "ast": ast_info,
            }
        enum = enumerate_subject_units(subject)
        return {
            "subject": subject.to_record(),
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
