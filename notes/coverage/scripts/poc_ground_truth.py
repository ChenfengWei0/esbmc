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
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESBMC_ROOT = HERE.parents[2]
DEFAULT_POC_DIR = ESBMC_ROOT / "notes" / "coverage" / "poc"
DEFAULT_PUT_ROOT = ESBMC_ROOT / "notes" / "coverage" / "put_roundtrip" / "_wd"
DEFAULT_POC_UNITS_DIR = ESBMC_ROOT / "notes" / "coverage" / "poc_units"
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
        paths.extend(sorted(root.glob("*/certify_gate.jsonl")))
        paths.extend(sorted(root.glob("*/*/certify_gate.jsonl")))
    return paths


def default_put_roots() -> list[Path]:
    roots = []
    if DEFAULT_PUT_ROOT.exists():
        roots.append(DEFAULT_PUT_ROOT)
    if DEFAULT_POC_UNITS_DIR.exists():
        roots.extend(sorted(
            path for path in DEFAULT_POC_UNITS_DIR.glob("*/put_*")
            if path.is_dir()))
    return roots


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
    return bool(wide_region_coords(region))


def wide_region_coords(region: dict) -> list[str]:
    coords = []
    for name, lo_hi in (region or {}).items():
        if not isinstance(lo_hi, list) or len(lo_hi) != 2:
            continue
        lo = _as_int(lo_hi[0])
        hi = _as_int(lo_hi[1])
        if lo is not None and hi is not None and hi > lo:
            coords.append(name)
    return sorted(coords)


def weak_detail_tag(detail: str) -> str:
    if detail == "no-fuzz-params":
        return "no-fuzz-params"
    if detail == "no-wide-region":
        return "no-wide-region"
    if detail.startswith("no-fuzz:wide-state-coordinate:"):
        return "no-fuzz:wide-state-coordinate"
    if detail.startswith("no-fuzz:wide-derived-coordinate:"):
        return "no-fuzz:wide-derived-coordinate"
    if detail.startswith("no-fuzz:wide-unlifted-coordinate:"):
        return "no-fuzz:wide-unlifted-coordinate"
    if detail.startswith("no-fuzz:state-skipped:"):
        return "no-fuzz:state-coordinate-dropped"
    if detail.startswith("no-fuzz:") and "cannot bound" in detail:
        if "type `bool`" in detail:
            return "no-fuzz:stale-bool-unliftable-note"
        return "no-fuzz:unliftable-type-note"
    if detail.startswith("no-fuzz:"):
        return "no-fuzz:other"
    if detail.startswith("no-oracle:ladder-refusal:"):
        return "no-oracle:ladder-refusal"
    if detail.startswith("no-oracle:") and "constant/immutable" in detail:
        return "no-oracle:constant-immutable"
    if detail.startswith("no-oracle:") and "no storage slot" in detail:
        return "no-oracle:no-storage-slot"
    if detail == "no-oracle:undifferentiated":
        return "no-oracle:undifferentiated"
    if detail.startswith("no-oracle:"):
        return "no-oracle:other-skipped"
    return "other"


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
            "path_function": doc.get("path_function"),
            "enc": doc.get("enc"),
            "depth": doc.get("depth"),
            "test": doc.get("test"),
            "file": doc.get("file"),
            "fuzz_params": stats.get("fuzz_params", 0),
            "lifted": stats.get("lifted") or [],
            "asserts": stats.get("asserts", 0),
            "holes": doc.get("holes") or {},
            "pins": doc.get("pins") or {},
            "region_coords": sorted(region),
            "wide_region_coords": wide_region_coords(region),
            "wide_region": region_has_width(region),
        }
        weak_reasons = []
        weak_details = []
        if row["fuzz_params"] <= 0:
            weak_reasons.append("no-fuzz-params")
            weak_details.append("no-fuzz-params")
            for note in doc.get("notes") or []:
                if "cannot bound" in note or "NOT PARAMETERIZED" in note:
                    weak_details.append(f"no-fuzz:{note}")
            for skipped in stats.get("state_skipped") or []:
                weak_details.append(f"no-fuzz:state-skipped:{skipped}")
            for coord in row["wide_region_coords"]:
                if coord.startswith("state."):
                    weak_details.append(f"no-fuzz:wide-state-coordinate:{coord}")
                elif "." in coord:
                    weak_details.append(f"no-fuzz:wide-derived-coordinate:{coord}")
                elif coord not in row["lifted"]:
                    weak_details.append(f"no-fuzz:wide-unlifted-coordinate:{coord}")
        if row["asserts"] <= 0:
            weak_reasons.append("no-oracle")
            for reason in doc.get("oracle_skipped") or (stats.get("oracle_skipped") or []):
                weak_details.append(f"no-oracle:{reason}")
            if doc.get("ladder_refusal"):
                weak_details.append(f"no-oracle:ladder-refusal:{doc.get('ladder_refusal')}")
            if not weak_details or all(not d.startswith("no-oracle:") for d in weak_details):
                weak_details.append("no-oracle:undifferentiated")
        if not row["wide_region"]:
            weak_reasons.append("no-wide-region")
            weak_details.append("no-wide-region")
        row["weak_reasons"] = weak_reasons
        row["weak_details"] = weak_details
        row["weak_detail_tags"] = sorted({weak_detail_tag(d) for d in weak_details})
        row["strong_shape"] = not weak_reasons
        rows.append(row)
    return rows, bad


def collect_put_rows_from_roots(put_roots: list[Path]) -> tuple[list[dict], int]:
    rows = []
    bad = 0
    seen = set()
    for root in put_roots:
        got, bad_docs = collect_put_rows(root)
        bad += bad_docs
        for row in got:
            path = row.get("path")
            if path in seen:
                continue
            seen.add(path)
            rows.append(row)
    return rows, bad


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
        "path_function": row.get("path_function"),
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


def empty_unit(contract: str | None,
               unit: str | None,
               sources_by_contract: dict[str, dict]) -> dict:
    return {
        "contract": contract,
        "unit": unit,
        "source": sources_by_contract.get(contract),
        "certifications": [],
        "puts": [],
    }


def update_unit_identity(entry: dict,
                         contract: str | None,
                         unit: str | None,
                         sources_by_contract: dict[str, dict]) -> None:
    if entry.get("contract") is None and contract is not None:
        entry["contract"] = contract
        entry["source"] = sources_by_contract.get(contract)
    if entry.get("unit") is None and unit is not None:
        entry["unit"] = unit
    if entry.get("source") is None and entry.get("contract") is not None:
        entry["source"] = sources_by_contract.get(entry.get("contract"))


def _filters(args, name: str) -> set[str]:
    return {str(v) for v in (getattr(args, name, None) or []) if str(v)}


def _only_filters(args) -> set[tuple[str, str]]:
    out = set()
    for value in getattr(args, "only", None) or []:
        if "." not in value:
            raise GroundTruthError(f"--only expects Contract.unit, got {value!r}")
        contract, unit = value.split(".", 1)
        if not contract or not unit:
            raise GroundTruthError(f"--only expects Contract.unit, got {value!r}")
        out.add((contract, unit))
    return out


def unit_matches_filters(row: dict, args) -> bool:
    contract = row.get("contract")
    unit = row.get("unit")
    source = row.get("source") or {}
    contract_filter = _filters(args, "contract")
    unit_filter = _filters(args, "unit")
    poc_filter = _filters(args, "poc")
    only_filter = _only_filters(args)
    if contract_filter and contract not in contract_filter:
        return False
    if unit_filter and unit not in unit_filter:
        return False
    if poc_filter and source.get("stem") not in poc_filter:
        return False
    if only_filter and (contract, unit) not in only_filter:
        return False
    return True


def add_unit_summaries(row: dict) -> None:
    weak_reasons = Counter()
    weak_details = Counter()
    weak_detail_tags = Counter()
    for put in row["puts"]:
        weak_reasons.update(put.get("weak_reasons") or [])
        weak_details.update(put.get("weak_details") or [])
        weak_detail_tags.update(put.get("weak_detail_tags") or [])
    certs = row.get("certifications") or []
    certified_paths = set()
    not_certified_paths = set()
    buckets = Counter()
    for cert in certs:
        buckets.update([cert.get("bucket") or "unknown"])
        certified_paths.update(cert.get("certified_paths") or [])
        not_certified_paths.update(cert.get("not_certified_paths") or [])
    row["put_summary"] = {
        "puts": len(row["puts"]),
        "strong_shape": sum(1 for p in row["puts"] if p.get("strong_shape")),
        "with_oracle": sum(1 for p in row["puts"] if p.get("asserts", 0) > 0),
        "with_fuzz_params": sum(1 for p in row["puts"] if p.get("fuzz_params", 0) > 0),
        "weak_reasons": dict(sorted(weak_reasons.items())),
        "weak_detail_tags": dict(sorted(weak_detail_tags.items())),
        "weak_details": dict(sorted(weak_details.items())),
    }
    row["cert_summary"] = {
        "rows": len(certs),
        "buckets": dict(sorted(buckets.items())),
        "certified_paths": sorted(certified_paths),
        "not_certified_paths": sorted(not_certified_paths),
    }
    certified_count = len(certified_paths)
    strong_count = row["put_summary"]["strong_shape"]
    if not certs:
        status = "no-certification-row"
    elif certified_count == 0:
        status = "no-certified-paths"
    elif not row["puts"]:
        status = "certified-no-put"
    elif strong_count == 0:
        status = "no-strong-put"
    elif strong_count < certified_count:
        status = "partial-strong-put"
    else:
        status = "ready-strong"
    row["ground_truth_status"] = status


def build_inventory(args) -> dict:
    poc_dir = Path(args.poc_dir)
    if getattr(args, "put_root", ""):
        put_roots = [Path(args.put_root)]
    else:
        put_roots = default_put_roots()
    cert_paths = [Path(p) for p in args.cert_jsonl]
    if not cert_paths:
        cert_paths = default_cert_paths(DEFAULT_CERT_ROOTS)

    sources = read_poc_sources(poc_dir, args.max_expected_lines)
    cert_rows, bad_cert_lines = collect_cert_rows(cert_paths)
    put_rows, bad_put_docs = collect_put_rows_from_roots(put_roots)

    sources_by_contract = source_by_contract(sources)

    units = {}
    for row in cert_rows:
        contract = contract_from_cert_row(row, sources)
        unit = row.get("unit")
        key = (contract, unit)
        entry = units.setdefault(key, empty_unit(contract, unit, sources_by_contract))
        update_unit_identity(entry, contract, unit, sources_by_contract)
        entry["certifications"].append(summarize_cert(row))

    for row in put_rows:
        contract = row.get("contract")
        unit = row.get("unit")
        key = (contract, unit)
        entry = units.setdefault(key, empty_unit(contract, unit, sources_by_contract))
        update_unit_identity(entry, contract, unit, sources_by_contract)
        entry["puts"].append(row)

    unit_rows = sorted(units.values(),
                       key=lambda r: (r.get("contract") is None,
                                      (r.get("contract") or ""),
                                      (r.get("unit") or "")))
    for row in unit_rows:
        add_unit_summaries(row)

    filtered_units = [row for row in unit_rows if unit_matches_filters(row, args)]
    filtered_puts = [put for row in filtered_units for put in row["puts"]]
    status_counts = Counter(row["ground_truth_status"] for row in filtered_units)

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
            "put_root": str(put_roots[0].resolve()) if len(put_roots) == 1
                        else None,
            "put_roots": [str(root.resolve()) for root in put_roots],
            "cert_jsonl": [str(p.resolve()) for p in cert_paths],
            "filters": {
                "contract": sorted(_filters(args, "contract")),
                "unit": sorted(_filters(args, "unit")),
                "poc": sorted(_filters(args, "poc")),
                "only": [".".join(v) for v in sorted(_only_filters(args))],
            },
        },
        "summary": {
            "poc_sources": len(sources),
            "sources_with_expected": sum(1 for s in sources if s["expected_blocks"]),
            "cert_rows": len(cert_rows),
            "put_rows": len(put_rows),
            "unit_rows": len(filtered_units),
            "all_unit_rows": len(unit_rows),
            "filtered_out_unit_rows": len(unit_rows) - len(filtered_units),
            "filtered_put_rows": len(filtered_puts),
            "strong_shape_puts": sum(1 for p in filtered_puts if p.get("strong_shape")),
            "unit_status": dict(sorted(status_counts.items())),
            "bad_cert_jsonl_lines": bad_cert_lines,
            "bad_put_docs": bad_put_docs,
        },
        "sources": sources,
        "units": filtered_units,
    }


def print_text(doc: dict, limit: int) -> None:
    summary = doc["summary"]
    print("POC ground truth inventory")
    for key in ("poc_sources", "sources_with_expected", "cert_rows", "put_rows",
                "filtered_put_rows", "unit_rows", "all_unit_rows",
                "filtered_out_unit_rows", "strong_shape_puts",
                "bad_cert_jsonl_lines", "bad_put_docs"):
        print(f"  {key:<24} {summary.get(key)}")
    if summary.get("unit_status"):
        print(f"  {'unit_status':<24} {summary.get('unit_status')}")
    print()
    for row in doc["units"][:limit or None]:
        certs = row.get("certifications") or []
        puts = row.get("puts") or []
        source = row.get("source") or {}
        expected = source.get("expected_blocks") or []
        print(f"{row.get('contract')}.{row.get('unit')}")
        print(f"  status                 {row.get('ground_truth_status')}")
        print(f"  cert rows              {len(certs)}")
        print(f"  PUTs                   {len(puts)}")
        print(f"  strong-shape PUTs      {row['put_summary']['strong_shape']}")
        if row["put_summary"]["weak_reasons"]:
            print(f"  weak reasons           {row['put_summary']['weak_reasons']}")
        if row["put_summary"]["weak_detail_tags"]:
            print(f"  weak detail tags       {row['put_summary']['weak_detail_tags']}")
        if row["put_summary"]["weak_details"]:
            shown = dict(list(row["put_summary"]["weak_details"].items())[:3])
            print(f"  weak details           {shown}")
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
    ap.add_argument("--put-root", default="",
                    help="read PUTs from this root only. Default: old "
                         "put_roundtrip/_wd plus every poc_units/*/put_* root")
    ap.add_argument("--cert-jsonl", action="append", default=[])
    ap.add_argument("--contract", action="append", default=[])
    ap.add_argument("--unit", action="append", default=[])
    ap.add_argument("--poc", action="append", default=[])
    ap.add_argument("--only",
                    action="append",
                    default=[],
                    help="restrict unit rows to Contract.unit; repeatable")
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
