#!/usr/bin/env python3
"""Read-only POC ground-truth inventory for VeriPUT.

The script does not run solc, Forge, fuzzing, ESBMC, or the PUT generator.  It
only joins source comments and already-existing artefacts so a POC can be
audited before spending one verifier run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESBMC_ROOT = HERE.parents[2]
DEFAULT_POC_DIR = ESBMC_ROOT / "notes" / "coverage" / "poc"
DEFAULT_PUT_ROOT = ESBMC_ROOT / "notes" / "coverage" / "put_roundtrip" / "_wd"
DEFAULT_CERT_ROOTS = (
    ESBMC_ROOT / "notes" / "coverage" / "certify",
    ESBMC_ROOT / "notes" / "coverage" / "poc_units",
)

CONTRACT_RE = re.compile(r"^\s*contract\s+([A-Za-z_][A-Za-z0-9_]*)\b")
FUNCTION_RE = re.compile(r"^\s*function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
COMMENT_RE = re.compile(r"^\s*//[/!]?\s?(.*)$")
EXPECTED_RE = re.compile(r"\bEXPECTED\b", re.IGNORECASE)
PATH_FUNCTION_CONTRACT_RE = re.compile(r"@C@([^@]+)@F@")


class GroundTruthError(ValueError):
    """The requested inventory cannot be read."""


def _clean_comment(line: str) -> str | None:
    match = COMMENT_RE.match(line)
    if not match:
        return None
    return match.group(1).rstrip()


def _line_starts_new_section(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    upper = stripped.upper()
    return (upper.startswith("WHAT ") or upper.startswith("THE FAILURE")
            or upper.startswith("----") or upper.startswith("MEASURED")
            or upper.startswith("RUN,") or upper.startswith("A FUNCTION "))


def extract_expected_blocks(lines: list[str], start_idx: int, max_lines: int) -> list[str]:
    block = []
    for raw in lines[start_idx:start_idx + max_lines]:
        text = _clean_comment(raw)
        if text is None:
            break
        if block and _line_starts_new_section(text):
            break
        block.append(text.strip())
    return [line for line in block if line]


def read_poc_sources(poc_dir: Path, max_expected_lines: int) -> list[dict]:
    if not poc_dir.exists():
        raise GroundTruthError(f"missing POC source directory: {poc_dir}")
    rows = []
    for path in sorted(poc_dir.glob("*.sol")):
        lines = path.read_text(errors="replace").splitlines()
        contracts = []
        functions = []
        expected = []
        for idx, line in enumerate(lines):
            match = CONTRACT_RE.match(line)
            if match:
                contracts.append(match.group(1))
            match = FUNCTION_RE.match(line)
            if match:
                functions.append(match.group(1))
            comment = _clean_comment(line)
            if comment is not None and EXPECTED_RE.search(comment):
                block = extract_expected_blocks(lines, idx, max_expected_lines)
                if block:
                    expected.append({"line": idx + 1, "text": block})
        rows.append({
            "stem": path.stem,
            "path": str(path),
            "contracts": contracts,
            "primary_contract": contracts[-1] if contracts else None,
            "functions": sorted(set(functions)),
            "expected_blocks": expected,
        })
    return rows


def default_cert_paths(cert_roots: tuple[Path, ...]) -> list[Path]:
    paths = []
    for root in cert_roots:
        if not root.exists():
            continue
        paths.extend(sorted(root.glob("*.jsonl")))
        paths.extend(sorted(root.glob("*/*/certify_gate.jsonl")))
    return paths


def read_jsonl(path: Path) -> tuple[list[dict], int]:
    rows = []
    bad = 0
    try:
        with path.open() as handle:
            for line_no, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                row["_source"] = str(path)
                row["_line"] = line_no
                rows.append(row)
    except OSError:
        bad += 1
    return rows, bad


def collect_cert_rows(paths: list[Path]) -> tuple[list[dict], int]:
    rows = []
    bad = 0
    for path in paths:
        got, bad_lines = read_jsonl(path)
        rows.extend(got)
        bad += bad_lines
    return rows, bad


def _as_int(value):
    try:
        return int(str(value), 0)
    except (TypeError, ValueError):
        return None


def region_has_width(region: dict) -> bool:
    for lo_hi in (region or {}).values():
        if not isinstance(lo_hi, list) or len(lo_hi) != 2:
            continue
        lo = _as_int(lo_hi[0])
        hi = _as_int(lo_hi[1])
        if lo is not None and hi is not None and hi > lo:
            return True
    return False


def collect_put_rows(put_root: Path) -> tuple[list[dict], int]:
    if not put_root.exists():
        return [], 0
    rows = []
    bad = 0
    for path in sorted(put_root.glob("**/put.json")):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            bad += 1
            continue
        stats = doc.get("stats") or {}
        region = doc.get("region") or {}
        row = {
            "path": str(path),
            "contract": doc.get("contract"),
            "unit": doc.get("unit"),
            "enc": doc.get("enc"),
            "depth": doc.get("depth"),
            "test": doc.get("test"),
            "file": doc.get("file"),
            "fuzz_params": stats.get("fuzz_params", 0),
            "asserts": stats.get("asserts", 0),
            "holes": doc.get("holes") or {},
            "pins": doc.get("pins") or {},
            "region_coords": sorted(region),
            "wide_region": region_has_width(region),
        }
        row["strong_shape"] = (row["fuzz_params"] > 0 and row["asserts"] > 0
                               and row["wide_region"])
        rows.append(row)
    return rows, bad


def index_by_contract_unit(rows: list[dict]) -> dict[tuple[str | None, str | None], list[dict]]:
    by_key = defaultdict(list)
    for row in rows:
        by_key[(row.get("contract"), row.get("unit"))].append(row)
    return by_key


def source_by_contract(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for row in rows:
        for contract in row.get("contracts") or []:
            out.setdefault(contract, row)
    return out


def summarize_cert(row: dict) -> dict:
    certified = row.get("certified") or {}
    not_certified = row.get("not_certified") or {}
    return {
        "source": row.get("_source"),
        "line": row.get("_line"),
        "bucket": row.get("bucket"),
        "witnessed": row.get("witnessed"),
        "coords": row.get("coords") or [],
        "pins": row.get("pins"),
        "certified_paths": sorted(certified),
        "not_certified_paths": sorted(not_certified),
        "unit_timeout_s": row.get("unit_timeout_s"),
        "run_timeout_s": row.get("run_timeout_s"),
        "memlimit_gib": row.get("memlimit_gib"),
        "recipe_version": row.get("recipe_version"),
    }


def contract_from_cert_row(row: dict, sources: list[dict]) -> str | None:
    if row.get("contract"):
        return row.get("contract")
    if row.get("poc"):
        source = next((s for s in sources if s["stem"] == row.get("poc")), None)
        if source:
            return source.get("primary_contract")
    path_function = row.get("path_function") or ""
    match = PATH_FUNCTION_CONTRACT_RE.search(path_function)
    if match:
        return match.group(1)
    benchmark = row.get("benchmark") or ""
    if "_" in benchmark:
        return benchmark.rsplit("_", 1)[1]
    return None


def build_inventory(args) -> dict:
    poc_dir = Path(args.poc_dir)
    put_root = Path(args.put_root)
    cert_paths = [Path(p) for p in args.cert_jsonl]
    if not cert_paths:
        cert_paths = default_cert_paths(DEFAULT_CERT_ROOTS)

    sources = read_poc_sources(poc_dir, args.max_expected_lines)
    cert_rows, bad_cert_lines = collect_cert_rows(cert_paths)
    put_rows, bad_put_docs = collect_put_rows(put_root)

    puts_by_key = index_by_contract_unit(put_rows)
    sources_by_contract = source_by_contract(sources)

    units = {}
    for row in cert_rows:
        contract = contract_from_cert_row(row, sources)
        unit = row.get("unit")
        key = (contract, unit)
        entry = units.setdefault(key, {
            "contract": contract,
            "unit": unit,
            "source": sources_by_contract.get(contract),
            "certifications": [],
            "puts": [],
        })
        entry["certifications"].append(summarize_cert(row))

    for key, rows in puts_by_key.items():
        entry = units.setdefault(key, {
            "contract": key[0],
            "unit": key[1],
            "source": sources_by_contract.get(key[0]),
            "certifications": [],
            "puts": [],
        })
        entry["puts"].extend(rows)

    unit_rows = sorted(units.values(),
                       key=lambda r: (r.get("contract") is None,
                                      (r.get("contract") or ""),
                                      (r.get("unit") or "")))
    for row in unit_rows:
        row["put_summary"] = {
            "puts": len(row["puts"]),
            "strong_shape": sum(1 for p in row["puts"] if p.get("strong_shape")),
            "with_oracle": sum(1 for p in row["puts"] if p.get("asserts", 0) > 0),
            "with_fuzz_params": sum(1 for p in row["puts"] if p.get("fuzz_params", 0) > 0),
        }

    return {
        "schema": "veriput-poc-ground-truth/v1",
        "read_only": True,
        "execution": {
            "runs_solc": False,
            "runs_forge": False,
            "runs_fuzz": False,
            "runs_esbmc": False,
            "writes_dataset_or_results": False,
        },
        "inputs": {
            "poc_dir": str(poc_dir.resolve()),
            "put_root": str(put_root.resolve()),
            "cert_jsonl": [str(p.resolve()) for p in cert_paths],
        },
        "summary": {
            "poc_sources": len(sources),
            "sources_with_expected": sum(1 for s in sources if s["expected_blocks"]),
            "cert_rows": len(cert_rows),
            "put_rows": len(put_rows),
            "unit_rows": len(unit_rows),
            "strong_shape_puts": sum(1 for p in put_rows if p.get("strong_shape")),
            "bad_cert_jsonl_lines": bad_cert_lines,
            "bad_put_docs": bad_put_docs,
        },
        "sources": sources,
        "units": unit_rows,
    }


def print_text(doc: dict, limit: int) -> None:
    summary = doc["summary"]
    print("POC ground truth inventory")
    for key in ("poc_sources", "sources_with_expected", "cert_rows", "put_rows",
                "unit_rows", "strong_shape_puts", "bad_cert_jsonl_lines",
                "bad_put_docs"):
        print(f"  {key:<24} {summary.get(key)}")
    print()
    for row in doc["units"][:limit or None]:
        certs = row.get("certifications") or []
        puts = row.get("puts") or []
        source = row.get("source") or {}
        expected = source.get("expected_blocks") or []
        print(f"{row.get('contract')}.{row.get('unit')}")
        print(f"  cert rows              {len(certs)}")
        print(f"  PUTs                   {len(puts)}")
        print(f"  strong-shape PUTs      {row['put_summary']['strong_shape']}")
        if certs:
            last = certs[-1]
            print(f"  last observed bucket   {last.get('bucket')}")
            print(f"  last observed cert     {last.get('certified_paths')}")
            print(f"  last observed notcert  {last.get('not_certified_paths')}")
        if expected:
            first = expected[0]
            print(f"  expected line          {first['line']}")
            print(f"  expected               {' '.join(first['text'])[:240]}")
        print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--poc-dir", default=str(DEFAULT_POC_DIR))
    ap.add_argument("--put-root", default=str(DEFAULT_PUT_ROOT))
    ap.add_argument("--cert-jsonl", action="append", default=[])
    ap.add_argument("--max-expected-lines", type=int, default=8)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--format", choices=("json", "text"), default="text")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    try:
        doc = build_inventory(args)
    except (OSError, GroundTruthError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    if args.format == "json":
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        print_text(doc, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
