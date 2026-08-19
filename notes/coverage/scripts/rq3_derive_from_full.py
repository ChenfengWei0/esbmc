#!/usr/bin/env python3
"""Derive the two non-search RQ3 ablations from a completed Full run.

This script never edits Full.  It consumes only strict-valid physical tests,
copies one self-contained Foundry project per selected test, applies the
requested mechanical transformation, and optionally replays the exact test.
Missing refinement provenance or a missing authenticated concrete basis is a
hard error rather than permission to infer an ablation result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SEED = "0x56657269505554"  # ASCII bytes of "VeriPUT".
BEGIN = "// VERIPUT_ORACLE_REFINEMENT_BEGIN"
END = "// VERIPUT_ORACLE_REFINEMENT_END"


class DerivationError(ValueError):
    """Full lacks evidence required for a semantics-preserving derivation."""


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DerivationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise DerivationError(f"{path}: expected a JSON object")
    return doc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(row: dict) -> tuple[str, str, str, str, str]:
    """Identify one emitted test unit, including its oracle input part.

    A certified path that splits into oracle input parts emits one physical PUT
    and retains one concrete basis per final part, so the part has to be part of
    the key.  Rows that never split carry no part and keep the empty string,
    which matches every pre-split manifest.
    """
    return (str(row.get("path_function") or ""), str(row.get("unit") or ""),
            str(row.get("enc") if row.get("enc") is not None else ""),
            str(row.get("piece") if row.get("piece") is not None else ""),
            str(row.get("oracle_input_part") or ""))


def _project(test_file: Path) -> Path:
    for parent in (test_file.parent, *test_file.parents):
        if (parent / "foundry.toml").is_file() and (parent / "src/flat.sol").is_file():
            return parent
    raise DerivationError(f"no Foundry project owns {test_file}")


def strip_oracle_refinement(source: str) -> tuple[str, int]:
    """Remove complete tagged blocks; reject nested or unbalanced markers."""
    output = []
    inside = False
    removed = 0
    for line in source.splitlines(keepends=True):
        if BEGIN in line:
            if inside:
                raise DerivationError("nested oracle-refinement marker")
            inside = True
            removed += 1
            continue
        if END in line:
            if not inside:
                raise DerivationError("oracle-refinement end without begin")
            inside = False
            continue
        if not inside:
            output.append(line)
    if inside:
        raise DerivationError("oracle-refinement begin without end")
    return "".join(output), removed


def _strict_valid_rows(full_root: Path) -> list[tuple[Path, dict]]:
    rows = []
    for result_path in sorted(full_root.rglob("result.json")):
        envelope = _read_json(result_path)
        # The runner writes {"schema": ..., "row": {...}} and keeps the test
        # rows inside "row"; older flat result.json files carry them at the top
        # level.  Accept both, exactly as rq3_compare_smoke.py does, otherwise
        # every current Full root looks empty and the derivation refuses.
        result = envelope.get("row") if isinstance(envelope.get("row"), dict) else envelope
        for row in result.get("valid_tests") or []:
            if isinstance(row, dict) and row.get("valid_reference_test") is True:
                rows.append((result_path.parent, dict(row)))
    return rows


def _basis_entries(subject_dir: Path) -> dict[tuple[str, str, str, str], dict]:
    manifest_path = subject_dir / "concrete-replays/manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = _read_json(manifest_path)
    entries = {}
    for entry in manifest.get("entries") or []:
        if not isinstance(entry, dict) or entry.get("valid_reference_test") is not True:
            continue
        origin = entry.get("origin") or {}
        key = _identity(origin)
        if key in entries:
            raise DerivationError(f"{manifest_path}: duplicate concrete basis for {key}")
        entries[key] = entry
    return entries


def _basis_test(subject_dir: Path, entry: dict) -> tuple[Path, str]:
    """Return one persisted Forge-green concrete basis, or fail closed."""
    if entry.get("valid_reference_test") is not True or entry.get("forge_status") != "Success":
        raise DerivationError(
            f"{entry.get('replay_id')}: retained concrete basis is not Forge-green")
    project_rel = Path(str(entry.get("project") or ""))
    test_rel = Path(str(entry.get("test_file") or ""))
    if (not project_rel.parts or project_rel.is_absolute() or ".." in project_rel.parts
            or not test_rel.parts or test_rel.is_absolute() or ".." in test_rel.parts):
        raise DerivationError(f"{entry.get('replay_id')}: retained basis path escapes subject")
    project = (subject_dir / project_rel).resolve()
    test_file = (project / test_rel).resolve()
    try:
        test_file.relative_to(project)
        project.relative_to(subject_dir.resolve())
    except ValueError as exc:
        raise DerivationError(
            f"{entry.get('replay_id')}: retained basis path escapes subject") from exc
    if not test_file.is_file():
        raise DerivationError(f"{entry.get('replay_id')}: retained basis test is missing")
    expected_test_sha = str(entry.get("test_sha256") or "")
    if not expected_test_sha or _sha256(test_file) != expected_test_sha:
        raise DerivationError(f"{entry.get('replay_id')}: retained basis test hash mismatch")
    flat_file = project / str(entry.get("flat_source") or "src/flat.sol")
    expected_flat_sha = str(entry.get("flat_sha256") or "")
    if not flat_file.is_file() or not expected_flat_sha or _sha256(flat_file) != expected_flat_sha:
        raise DerivationError(f"{entry.get('replay_id')}: retained basis flat source mismatch")
    replay_log = project / str(entry.get("forge_log") or "")
    expected_log_sha = str(entry.get("forge_log_sha256") or "")
    if not replay_log.is_file() or not expected_log_sha or _sha256(replay_log) != expected_log_sha:
        raise DerivationError(f"{entry.get('replay_id')}: retained basis Forge log mismatch")
    test = str(entry.get("test") or "")
    if not test:
        raise DerivationError(f"{entry.get('replay_id')}: retained basis test name is missing")
    return test_file, test


def _row_record(row: dict) -> dict:
    path = Path(str(row.get("put_json") or ""))
    if not path.is_file():
        raise DerivationError(f"missing put.json for {row.get('test')}: {path}")
    return _read_json(path)


def _copy_project(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=False)


def _forge(project: Path, test_file: Path, test: str, timeout: int) -> dict:
    relative = test_file.relative_to(project).as_posix()
    command = ["forge", "test", "--json", "--match-path", relative,
               "--match-test", "^" + re.escape(test) + "$", "--fuzz-runs", "10000",
               "--fuzz-seed", SEED]
    try:
        proc = subprocess.run(command, cwd=project, text=True, capture_output=True,
                              timeout=timeout, check=False)
        status = "Success" if proc.returncode == 0 else "Failure"
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        status = "Timeout"
        output = (exc.stdout or "") + (exc.stderr or "")
    return {"status": status, "command": command, "output": output}


def _write_entry(out_root: Path, index: int, case: str, source_file: Path,
                 test: str, mode: str, origin: dict, timeout: int,
                 run_forge: bool, remove_oracle_refinement: bool = False) -> dict:
    entry_id = f"{index:05d}_{hashlib.sha256((case + test).encode()).hexdigest()[:12]}"
    destination = out_root / "entries" / entry_id / "Project"
    source_project = _project(source_file)
    relative = source_file.relative_to(source_project)
    _copy_project(source_project, destination)
    copied_test = destination / relative
    if remove_oracle_refinement:
        transformed, removed = strip_oracle_refinement(copied_test.read_text(errors="replace"))
        if removed == 0:
            raise DerivationError(f"{source_file}: no oracle-refinement block to remove")
        copied_test.write_text(transformed)
    else:
        removed = 0
    forge = (_forge(destination, copied_test, test, timeout) if run_forge
             else {"status": "NotRun", "command": [], "output": ""})
    logs = destination.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "forge.log").write_text(forge.pop("output"))
    row = {
        "schema": "veriput-rq3-derived-entry/v1",
        "id": entry_id,
        "case": case,
        "mode": mode,
        "test": test,
        "test_file": str((Path("entries") / entry_id / "Project" / relative).as_posix()),
        "test_sha256": _sha256(copied_test),
        "oracle_refinement_blocks_removed": removed,
        "forge": forge,
        "origin": origin,
    }
    (destination.parent / "entry.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    return row


STRUCTURAL_CERTIFICATION_SOURCES = (
    "structural-abi-gate-no-coordinate",
    "structural-abi-getter-no-coordinate",
)
STRUCTURAL_STAGE4_KINDS = ("abi-value-gate", "getter-value-gate")


def _is_structural_certificate(row: dict, record: dict) -> bool:
    """True when this PUT rests on a structural certificate, not a solver CE.

    The compiler's nonpayable ABI gate rejects every nonzero `msg.value` before
    the body runs, so that region is certified from the declared mutability
    rather than from a counterexample.  There is no witness to replay, which is
    why these rows never retain a concrete basis.
    """
    for source in (row.get("certification_source"), (record or {}).get("certification_source")):
        if source in STRUCTURAL_CERTIFICATION_SOURCES:
            return True
    for kind in (row.get("stage4_kind"), (record or {}).get("stage4_kind"),
                 row.get("certified_detail_stage4_kind")):
        if kind in STRUCTURAL_STAGE4_KINDS:
            return True
    return False


def derive(full_root: Path, out_root: Path, mode: str, timeout: int,
           run_forge: bool) -> dict:
    rows = _strict_valid_rows(full_root)
    if not rows:
        raise DerivationError(f"{full_root}: no strict-valid Full rows")
    staging = Path(tempfile.mkdtemp(prefix=out_root.name + ".", dir=out_root.parent))
    emitted = []
    dropped_structural: list[str] = []
    try:
        for subject_dir, row in rows:
            case = str(row.get("case") or subject_dir.name)
            record = _row_record(row)
            selected_file = Path(str(row.get("file") or record.get("file") or ""))
            selected_test = str(row.get("test") or record.get("test") or "")
            origin = {"kind": row.get("kind"), "identity": list(_identity(row)),
                      "source_file": str(selected_file), "source_sha256": _sha256(selected_file)}
            if mode == "no-cer-reg" and row.get("kind") == "put":
                if _is_structural_certificate(row, record):
                    # A structural ABI-gate PUT has no solver counterexample and
                    # therefore no retained concrete basis: its certificate IS
                    # the region.  Strip the certified region and nothing is
                    # left to replay, so this arm legitimately emits no test for
                    # that path.  Dropping it is the ablation's result, not a
                    # missing artifact, and it is counted so the loss is visible.
                    dropped_structural.append(str(row.get("test") or ""))
                    continue
                basis = _basis_entries(subject_dir).get(_identity(row))
                if basis is None:
                    raise DerivationError(
                        f"{row.get('test')}: PUT has no exact authenticated concrete basis")
                selected_file, selected_test = _basis_test(subject_dir, basis)
                origin["replacement"] = "retained-certified-ce"
                origin["basis_replay_id"] = basis.get("replay_id")
            elif mode == "no-region-refinement" and row.get("kind") == "put":
                derived = record.get("derived_by") or {}
                if derived.get("region_refinement_used") is True:
                    basis = _basis_entries(subject_dir).get(_identity(row))
                    if basis is None:
                        raise DerivationError(
                            f"{row.get('test')}: refined region has no authenticated concrete basis")
                    selected_file, selected_test = _basis_test(subject_dir, basis)
                    origin["replacement"] = "retained-certified-ce"
                    origin["basis_replay_id"] = basis.get("replay_id")
            if selected_file != Path(origin["source_file"]):
                origin["selected_source_file"] = str(selected_file)
                origin["selected_source_sha256"] = _sha256(selected_file)
            if mode == "no-test-oracle-refinement" and row.get("kind") != "put":
                # No oracle-refinement assertion exists in a concrete replay.
                continue
            remove_oracle_refinement = False
            if mode == "no-test-oracle-refinement":
                details = (record.get("stats") or {}).get("assertion_oracles") or []
                r1r2 = [detail for detail in details if isinstance(detail, dict)
                        and set(detail.get("classes") or []) & {"R1", "R2"}]
                if any(detail.get("refinement_source") != "oracle-refinement"
                       for detail in r1r2):
                    raise DerivationError(
                        f"{row.get('test')}: R1/R2 assertion lacks refinement provenance")
                remove_oracle_refinement = bool(r1r2)
            emitted.append(_write_entry(staging, len(emitted), case, selected_file,
                                        selected_test, mode, origin, timeout, run_forge,
                                        remove_oracle_refinement))
        manifest = {
            "schema": "veriput-rq3-derived-manifest/v1",
            "mode": mode,
            "full_root": str(full_root.resolve()),
            "entries": emitted,
            "test_units": len(emitted),
            "forge_success": sum(row["forge"]["status"] == "Success" for row in emitted),
            "fuzz_runs": 10000,
            "fuzz_seed": SEED,
            "dropped_structural_certificate_puts": sorted(dropped_structural),
            "dropped_structural_certificate_put_count": len(dropped_structural),
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if out_root.exists():
            shutil.rmtree(out_root)
        os.replace(staging, out_root)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--mode", required=True,
                        choices=("no-region-refinement", "no-test-oracle-refinement",
                                 "no-cer-reg"))
    parser.add_argument("--forge-timeout", type=int, default=600)
    parser.add_argument("--no-forge", action="store_true",
                        help="prepare only; publication still requires a Forge replay")
    args = parser.parse_args()
    args.out_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        manifest = derive(args.full_root, args.out_root, args.mode,
                          args.forge_timeout, not args.no_forge)
    except DerivationError as exc:
        print(f"REFUSED: {exc}")
        return 2
    print(json.dumps({key: manifest[key] for key in
                      ("mode", "test_units", "forge_success", "fuzz_runs", "fuzz_seed",
                       "dropped_structural_certificate_put_count")},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
