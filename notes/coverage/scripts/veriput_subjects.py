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
import subprocess
from dataclasses import dataclass
from pathlib import Path


VERIPUT_ROOT = Path(os.environ.get(
    "VERIPUT_ROOT", "/home/samson/workspace/VeriPUT"))

KNOWN_SUBJECT_ROOTS = {
    "stress243": VERIPUT_ROOT / "Results" / "Stress243" / "subjects",
    "peer182": VERIPUT_ROOT / "Results" / "Peer182" / "subjects",
    "bugfix124": VERIPUT_ROOT / "Results" / "BugFix124" / "subjects",
}


class SubjectError(ValueError):
    """A prepared subject cannot be resolved safely."""


def _clean_key(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")


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

    def to_record(self) -> dict:
        return {
            "schema": "veriput-subject/v1",
            "benchmark": self.benchmark,
            "subject_id": self.subject_id,
            "benchmark_key": self.benchmark_key,
            "root": self.root,
            "flat_sol": self.flat_sol,
            "solast": self.solast,
            "contract": self.contract,
            "unit": self.unit,
            "solc_bin": self.solc_bin,
            "solc_extra": list(self.solc_extra),
            "meta_status": self.metadata.get("status"),
        }


@dataclass(frozen=True)
class UnitEnumeration:
    contract: str
    units: tuple[str, ...]
    skipped: tuple[dict, ...]

    def to_record(self) -> dict:
        return {
            "schema": "veriput-subject-units/v1",
            "contract": self.contract,
            "units": list(self.units),
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
        metadata={"status": data.get("meta_status")},
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
                }
            continue
        if visibility not in ("public", "external"):
            continue
        if not name:
            yield None, {
                "contract": owner_name,
                "kind": kind,
                "reason": "public/external function has no name",
            }
            continue
        yield name, None


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
    seen = set()
    skipped = []
    for cid in ordered_ids:
        node = contracts.get(cid)
        if not node:
            continue
        owner = node.get("name") or f"<contract {cid}>"
        for name, skip in _function_units(node, owner):
            if skip:
                skipped.append(skip)
                continue
            if name not in seen:
                seen.add(name)
                units.append(name)
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
        skipped=tuple(skipped),
    )


def ensure_solast(subject: PreparedSubject) -> bool:
    """Create `<flat.sol>.solast` for a prepared subject if it is absent.

    Returns True when it wrote the file and False when it already existed.
    """
    ast = Path(subject.solast)
    if ast.exists():
        return False
    if not subject.solc_bin:
        raise SubjectError(
            f"{subject.root} has no solc_bin, and {ast} does not exist")
    ast.parent.mkdir(parents=True, exist_ok=True)
    with ast.open("w") as stream:
        subprocess.run(
            [subject.solc_bin, "--ast-compact-json", subject.flat_sol],
            stdout=stream,
            stderr=subprocess.PIPE,
            text=True,
            check=True)
    return True
