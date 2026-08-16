#!/usr/bin/env python3
"""Stage RQ1 renderer-gap R0 PUTs from exact valid RQ3 concrete tests.

This is intentionally narrower than the normal Stage-4 driver.  It reuses an
already-valid RQ3 fixed concrete witness plus RQ1's existing certified region;
it does not invoke ESBMC.  Only a verified normal-exit R0 completion wrapper
is promoted, and the original RQ3 test is retained verbatim as the anchor.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_renderer() -> Any:
    spec = importlib.util.spec_from_file_location(
        "renderer_gap_solidity_path_put", REPO / "scripts" / "solidity_path_put.py")
    if spec is None or spec.loader is None:
        raise ValueError("cannot import solidity_path_put.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_root(source: Path) -> Path | None:
    for parent in (source.parent, *source.parents):
        if (parent / "foundry.toml").is_file() and (parent / "src" / "flat.sol").is_file():
            return parent
    return None


def certified_detail(recovery: dict[str, Any], identity: list[str]) -> dict[str, Any] | None:
    case, path_function, unit, enc, _piece = identity
    manifest = None
    for row in recovery.get("rows", []):
        if row.get("identity") == identity:
            manifest = Path(str(row.get("manifest") or ""))
            break
    if manifest is None:
        return None
    subject = manifest.parent.parent
    for cert in sorted((subject / "cert").glob("*.jsonl")):
        for line in cert.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("unit") != unit or record.get("path_function") != path_function:
                continue
            detail = (record.get("certified_details") or {}).get(str(enc))
            if isinstance(detail, dict) and detail.get("verdict") == "CERTIFIED":
                return detail
    return None


def box(detail: dict[str, Any]) -> tuple[dict[str, tuple[int, int]], dict[str, list[int]]]:
    region: dict[str, tuple[int, int]] = {}
    holes: dict[str, list[int]] = {}
    for item in detail.get("box") or []:
        if not isinstance(item, dict) or not item.get("name"):
            raise ValueError("malformed certified box")
        name = str(item["name"])
        region[name] = (int(str(item["lo"])), int(str(item["hi"])))
        holes[name] = [int(str(value)) for value in item.get("holes") or []]
    return region, holes


def normalise_r0(source: str, unit: str) -> str | None:
    """Replace only the standard, asserted RQ3 completion wrapper with R0."""
    pattern = re.compile(
        r"\s*// \[revert-tolerant\] outcome not asserted\n"
        r"\s*bool (_veriput_concrete_completed\w*) = false;\n"
        r"\s*try ([^\n;]*\." + re.escape(unit) + r"\([^;\n]*\)) \{\n"
        r"\s*\1 = true;\n\s*\} catch \{\}\n"
        r"\s*assertTrue\(\1, \"fixed witness call must complete\"\);"
    )
    result, count = pattern.subn(
        "\n    // [asserted] path exits normally; a revert fails the test\n    \\2;", source)
    if count == 1:
        return result
    # Older emitters sometimes made the normal call directly but then wrapped
    # its already-asserted completion in a redundant boolean marker.  Remove
    # only that exact three-statement shape; the bare call remains Foundry's
    # normal-exit R0 oracle.
    direct = re.compile(
        r"\s*bool (_veriput_concrete_completed\w*) = false;\n"
        r"\s*([^\n;]*\." + re.escape(unit) + r"\([^;\n]*\));\n"
        r"\s*\1 = true;\n"
        r"\s*assertTrue\(\1, \"fixed witness call must complete\"\);"
    )
    result, count = direct.subn("\n    \\2;", source)
    return result if count == 1 else None


def anchor_lines(renderer: Any, source: Path, path_function: str, enc: int,
                 identity: list[str]) -> list[str] | None:
    emitted = renderer.EmittedFile(str(source))
    case = emitted.case_for(path_function, enc)
    if case is None:
        return None
    start, end = case[3]
    lines = list(emitted.lines[start:end + 1])
    anchor = "test_ce_anchor_renderer_" + hashlib.sha256(
        "\0".join(identity).encode("utf-8")).hexdigest()[:16]
    changed, count = re.subn(r"\bfunction\s+test_cov_\d+\s*\(",
                             f"function {anchor}(", lines[0])
    if count != 1:
        return None
    lines[0] = changed
    return lines


def forge_status(project: Path, relative: str, put_test: str, anchor_test: str) -> tuple[bool, str]:
    command = ["forge", "test", "--json", "--match-path", relative, "--match-test",
               "^test_(?:put_|ce_anchor_renderer_)", "--fuzz-runs", "256"]
    process = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=180)
    text = process.stdout + process.stderr
    return (process.returncode == 0 and put_test in text and anchor_test in text
            and '"status":"Success"' in text), text


def stage_one(renderer: Any, recovery: dict[str, Any], row: dict[str, Any], out: Path) -> dict[str, Any]:
    identity = [str(value) for value in row["identity"]]
    case, path_function, unit, enc_text, _piece = identity
    candidates = row.get("candidates") or []
    if len(candidates) != 1:
        return {"identity": identity, "status": "refused", "reason": "not exactly one source"}
    source = Path(str(candidates[0].get("source") or ""))
    project = project_root(source) if source.is_file() else None
    detail = certified_detail(recovery, identity)
    if project is None or detail is None:
        return {"identity": identity, "status": "refused", "reason": "source project or certificate missing"}
    try:
        enc = int(enc_text)
        region, holes = box(detail)
    except (KeyError, TypeError, ValueError) as exc:
        return {"identity": identity, "status": "refused", "reason": f"bad certified box: {exc}"}
    raw = source.read_text(encoding="utf-8")
    normalized = normalise_r0(raw, unit)
    if normalized is None:
        return {"identity": identity, "status": "refused", "reason": "not a unique normal-exit R0 wrapper"}
    target = out / hashlib.sha256("\0".join(identity).encode("utf-8")).hexdigest()[:20]
    copied = target / "project"
    shutil.copytree(project, copied)
    normalized_file = target / "normalized.t.sol"
    normalized_file.write_text(normalized, encoding="utf-8")
    emitted = renderer.EmittedFile(str(normalized_file))
    test_case = emitted.case_for(path_function, enc)
    anchor = anchor_lines(renderer, source, path_function, enc, identity)
    if test_case is None or anchor is None:
        return {"identity": identity, "status": "refused", "reason": "RQ3 claim/test binding absent"}
    match = re.search(r"@C@([^@]+)@F@", path_function)
    if match is None:
        return {"identity": identity, "status": "refused", "reason": "contract absent from path function"}
    contract = match.group(1)
    flat = copied / "src" / "flat.sol"
    params = renderer.source_inherited_function_params(flat.read_text(encoding="utf-8"), contract, unit)
    if params is None:
        return {"identity": identity, "status": "refused", "reason": "function parameters unavailable"}
    notes: list[str] = []
    try:
        put, stats = renderer.build_put(
            contract, unit, enc, int(detail.get("depth") or 0), path_function, region, holes, {},
            params, emitted, test_case, None, [], notes, exit_kind="normal",
            flat_source=flat.read_text(encoding="utf-8"))
    except renderer.ConcreteFallback as exc:
        return {"identity": identity, "status": "refused", "reason": exc.reason}
    if (put is None or not stats or stats.get("oracle_classes") != ["R0"]
            or not stats.get("wide_fuzz_coords")):
        return {"identity": identity, "status": "refused", "reason": "not an R0 parameterized PUT"}
    new_contract = f"{contract}RendererR0_{enc}"
    assembled = renderer.assemble_put_source(
        emitted, test_case, [put, anchor], new_contract, contract=contract, unit=unit,
        constructor_params=renderer.source_constructor_param_types(copied, contract),
        flat_source=flat.read_text(encoding="utf-8"))
    output = copied / "test" / f"{new_contract}.t.sol"
    output.write_text(assembled, encoding="utf-8")
    put_test = f"test_put_{contract}_{unit}_path{enc}"
    anchor_test = re.search(r"function\s+(test_ce_anchor_renderer_\w+)\(", "\n".join(anchor)).group(1)
    green, forge = forge_status(copied, f"test/{output.name}", put_test, anchor_test)
    (target / "forge.json").write_text(forge, encoding="utf-8")
    return {"identity": identity, "status": "validated" if green else "refused",
            "reason": None if green else "Forge did not report both Success tests",
            "source": str(source), "source_sha256": sha(source), "staged_source": str(output),
            "staged_source_sha256": sha(output), "put_test": put_test, "anchor_test": anchor_test,
            "stats": stats, "certified_detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    plan, recovery = load(args.plan), load(args.recovery)
    if args.out.exists():
        raise ValueError(f"output already exists: {args.out}")
    args.out.mkdir(parents=True)
    renderer = load_renderer()
    rows = [stage_one(renderer, recovery, row, args.out) for row in plan.get("rows", [])
            if row.get("status") == "ready"]
    report = {"schema": "rq1-renderer-gap-r0-stage/v1", "plan": str(args.plan.resolve()),
              "plan_sha256": sha(args.plan), "recovery": str(args.recovery.resolve()),
              "recovery_sha256": sha(args.recovery), "rows": rows,
              "counts": {"selected": len(rows),
                         "validated": sum(row["status"] == "validated" for row in rows),
                         "refused": sum(row["status"] == "refused" for row in rows)},
              "policy": "staging only; RQ3 valid concrete plus RQ1 certified R0 region; no ESBMC"}
    (args.out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if not report["counts"]["refused"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
