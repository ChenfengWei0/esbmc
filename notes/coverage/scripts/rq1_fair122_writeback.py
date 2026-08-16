#!/usr/bin/env python3
"""Materialize and validate Fair600 PUT membership for 122 retained replays."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from put_all import _solidity_function_spans, forge_json_status_map
from rq1_case_batch import _detailed_test_rows, _is_valid_reference_test


LANES = ("no-coordinate", "uncertified")
CATEGORIES = {"no-generalizable-coordinate", "not-certified-fallback"}
CANONICAL = Path("/home/samson/workspace/VeriPUT/Results/RQ1/VeriPUT")


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def _sha(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent) as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def _span(source: str, name: str) -> tuple[int, int, int, int, int]:
    spans = _solidity_function_spans(source, name)
    if len(spans) != 1 or spans[0][0] is None:
        raise RuntimeError(f"expected one Solidity function: {name}")
    return spans[0][0]


def _body(source: str, name: str) -> str:
    span = _span(source, name)
    opening = source.find("{", span[4], span[1])
    return source[opening + 1:span[1] - 1]


def _optional_body(source: str, name: str) -> str:
    spans = _solidity_function_spans(source, name)
    if not spans:
        return ""
    return _body(source, name)


def _scoped_optional_body(source: str, name: str, owner_test: str) -> str:
    owner_start = _span(source, owner_test)[0]
    close = _contract_close(source, owner_test)
    openings = [source.find("{", match.start(), match.end()) for match in re.finditer(
        r"\bcontract\s+[A-Za-z_$][A-Za-z0-9_$]*[^{};]*\{", source)
                if match.start() < owner_start]
    opening = max((value for value in openings if value >= 0 and value < owner_start), default=-1)
    matches = [span[0] for span in _solidity_function_spans(source, name)
               if span[0] is not None and opening < span[0][0] < close]
    if not matches:
        return ""
    if len(matches) != 1:
        raise RuntimeError(f"expected at most one scoped {name}")
    span = matches[0]
    brace = source.find("{", span[4], span[1])
    return source[brace + 1:span[1] - 1]


def _replace_body(source: str, name: str, body: str) -> str:
    span = _span(source, name)
    opening = source.find("{", span[4], span[1])
    return source[:opening + 1] + body + source[span[1] - 1:]


def _without_anchors(source: str) -> str:
    while True:
        match = re.search(r"\bfunction\s+test_ce_anchor_[A-Za-z0-9_$]+\s*\(", source)
        if match is None:
            return source
        name = re.search(r"test_ce_anchor_[A-Za-z0-9_$]+", match.group(0)).group(0)
        span = _span(source, name)
        start = span[0]
        if start > 0 and source[start - 1] == "\n":
            start -= 1
        source = source[:start] + source[span[1]:]


def _contract_close(source: str, function: str) -> int:
    start = _span(source, function)[0]
    owners = []
    for match in re.finditer(r"\bcontract\s+[A-Za-z_$][A-Za-z0-9_$]*[^{};]*\{", source):
        opening = source.find("{", match.start(), match.end())
        depth = 0
        for index in range(opening, len(source)):
            depth += source[index] == "{"
            depth -= source[index] == "}"
            if depth == 0:
                if opening < start < index:
                    owners.append(index)
                break
    if len(owners) != 1:
        raise RuntimeError(f"expected one owning contract for {function}")
    return owners[0]


def _merge_flat_imports(source: str, replay_source: str) -> str:
    pattern = re.compile(r'import\s*\{(?P<symbols>[^}]+)\}\s*from\s*"\.\./src/flat\.sol"\s*;')
    current = pattern.search(source)
    replay = pattern.search(replay_source)
    if current is None or replay is None:
        return source
    symbols = {item.strip() for item in current.group("symbols").split(",") if item.strip()}
    symbols.update(item.strip() for item in replay.group("symbols").split(",") if item.strip())
    replacement = 'import {' + ", ".join(sorted(symbols)) + '} from "../src/flat.sol";'
    return source[:current.start()] + replacement + source[current.end():]


def _merge_mock_contracts(source: str, replay_source: str) -> str:
    additions = []
    for match in re.finditer(r"\bcontract\s+(ESBMCMock_[A-Za-z0-9_$]+)[^{]*\{", replay_source):
        if re.search(r"\bcontract\s+" + re.escape(match.group(1)) + r"\b", source):
            continue
        opening = replay_source.find("{", match.start(), match.end())
        depth = 0
        for index in range(opening, len(replay_source)):
            depth += replay_source[index] == "{"
            depth -= replay_source[index] == "}"
            if depth == 0:
                additions.append(replay_source[match.start():index + 1])
                break
    if not additions:
        return source
    owner = re.search(r"\bcontract\s+[A-Za-z_$][A-Za-z0-9_$]*CovTest", source)
    if owner is None:
        raise RuntimeError("destination test contract is absent")
    return source[:owner.start()] + "\n\n".join(additions) + "\n\n" + source[owner.start():]


def _fixture_declarations(replay_source: str) -> list[str]:
    return sorted(set(match.group(0).strip() for match in re.finditer(
        r"(?m)^\s*[A-Za-z_$][A-Za-z0-9_$.]*\s+mk_[A-Za-z0-9_$]+\s*;\s*$",
        replay_source)))


def _identity(entry: dict) -> dict:
    return (entry.get("covered_original_identity")
            or (entry.get("origin") or {}).get("covered_original_identity")
            or entry.get("origin") or {})


def _manifest_entry(item: dict) -> tuple[dict, Path]:
    identity = item["identity"]
    manifest_path = Path(item["manifest"])
    matches = []
    for entry in _load(manifest_path).get("entries", []):
        old = _identity(entry)
        if (str(old.get("path_function") or "") == identity[1]
                and str(old.get("unit") or "") == identity[2]
                and str(old.get("enc")) == identity[3]
                and str(old.get("piece") or "") == identity[4]):
            matches.append(entry)
    if len(matches) != 1:
        raise RuntimeError(f"expected one replay for {identity}, got {len(matches)}")
    entry = matches[0]
    covered = entry.get("covered_original_test_file") or entry.get("test_file")
    covered_sha = entry.get("covered_original_test_sha256") or entry.get("test_sha256")
    test_file = manifest_path.parent.parent / entry["project"] / str(covered)
    if not test_file.is_file():
        raise RuntimeError(f"retained replay hash mismatch: {identity}")
    replay_source = test_file.read_text(encoding="utf-8")
    if (_sha(replay_source) != covered_sha and _sha(_without_anchors(replay_source)) != covered_sha
            and entry.get("forge_status") != "Success"):
        raise RuntimeError(f"retained replay hash mismatch: {identity}")
    _span(replay_source, str(entry.get("test") or ""))
    return entry, test_file


def _project(path: Path) -> Path:
    for parent in (path.parent, *path.parents):
        if (parent / "foundry.toml").is_file():
            return parent
    raise RuntimeError(f"Foundry root missing for {path}")


def _collect(inventory_path: Path, rerun_root: Path) -> list[dict]:
    targets = defaultdict(list)
    for item in _load(inventory_path).get("rows", []):
        if item.get("category") in CATEGORIES:
            identity = item["identity"]
            targets[(identity[0], identity[1], identity[2])].append(item)
    candidates = defaultdict(list)
    for lane in LANES:
        for result_path in (rerun_root / lane).glob("*/*/subjects/*/result.json"):
            result = _load(result_path)
            target = f"{result_path.parts[-5]}/{result_path.parts[-2]}"
            for row in _detailed_test_rows(result):
                if (_is_valid_reference_test(row) and row.get("kind") == "put"
                        and row.get("b") is True):
                    key = (target, str(row.get("path_function") or ""),
                           str(row.get("unit") or ""))
                    if key in targets:
                        candidates[key].append((row, result_path))
    output = []
    for key, items in sorted(targets.items()):
        if key not in candidates:
            continue
        row, result_path = sorted(
            candidates[key], key=lambda pair: (int(pair[0].get("enc") or 0),
                                                str(pair[0].get("piece") or "")))[0]
        output.append({"key": key, "items": items, "row": row, "result": result_path})
    obligations = {tuple(item["identity"]) for group in output for item in group["items"]}
    if len(output) != 76 or len(obligations) != 122:
        raise RuntimeError(f"expected 76 groups/122 obligations, got {len(output)}/{len(obligations)}")
    return output


def _forge(root: Path, relative: str, test: str) -> dict:
    command = ["forge", "test", "--root", str(root), "--json", "--match-path", relative,
               "--match-test", rf"^{test}(\(|$)", "--fuzz-runs", "256"]
    run = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    statuses, _, failures = forge_json_status_map(run.stdout)
    selected = [status for (_suite, name), status in statuses.items()
                if name == test or name.startswith(test + "(")]
    success = run.returncode == 0 and not failures and selected == ["Success"]
    return {"command": command, "returncode": run.returncode, "statuses": selected,
            "success": success, "stdout": run.stdout, "stderr": run.stderr}


def _row_key(row: dict) -> tuple[str, str, str, str, str]:
    return (str(row.get("path_function") or ""), str(row.get("unit") or ""),
            str(row.get("enc")), str(row.get("piece") or ""), str(row.get("test") or ""))


def _apply(report: dict) -> dict:
    applied = []
    by_subject = defaultdict(list)
    for group in report["rows"]:
        if group.get("valid") is not True:
            raise RuntimeError("refusing incomplete fair122 report")
        dataset, subject = group["key"][0].split("/", 1)
        by_subject[(dataset, subject)].append(group)
    for (dataset, subject), groups in sorted(by_subject.items()):
        subject_dir = CANONICAL / dataset / "subjects" / subject
        result_path = subject_dir / "result.json"
        result = _load(result_path)
        put = result.setdefault("put", {})
        for key in ("raw_tests", "valid_tests", "raw_artifacts", "valid_artifacts"):
            put[key] = [row for row in put.get(key, [])
                        if (row.get("ce_anchor") or {}).get("binding") !=
                        "fair-rerun-rq3-closure/v1"]
        membership_root = subject_dir / "put" / "fair122-membership"
        if membership_root.exists():
            shutil.rmtree(membership_root)
        for group in groups:
            for anchor in group["anchors"]:
                digest = _sha("|".join(anchor["identity"]))[:16]
                destination = membership_root / digest
                shutil.copytree(group["project"], destination,
                                ignore=shutil.ignore_patterns("cache", "out"), symlinks=True)
                file_path = destination / group["relative_file"]
                put_json_path = destination / "put.json"
                put_json = _load(Path(group["source_row"]["put_json"]))
                put_json["file"] = str(file_path)
                put_json["path_function"] = anchor["identity"][1]
                put_json["unit"] = anchor["identity"][2]
                put_json["enc"] = int(anchor["identity"][3])
                put_json["piece"] = anchor["identity"][4] or None
                anchor_run = group["anchor_runs"][anchor["test"]]
                record = {
                    "schema": "rq1-fair-rerun-rq3-membership/v1",
                    "identity": anchor["identity"],
                    "source_put_identity": list(group["key"]),
                    "source_sha256": group["source_sha256"],
                    "put_test": group["source_row"]["test"],
                    "anchor_test": anchor["test"],
                    "retained_replay_file": anchor["replay_file"],
                    "retained_replay_sha256": anchor["replay_sha256"],
                    "retained_manifest": anchor["manifest"],
                    "put_run": group["put_run"],
                    "anchor_run": anchor_run,
                }
                record_path = destination / f"membership-{anchor['test']}.json"
                _atomic(record_path, record)
                metadata = {
                    "status": "embedded",
                    "binding": "fair-rerun-rq3-closure/v1",
                    "identity": anchor["identity"],
                    "test": anchor["test"],
                    "destination_put_test": group["source_row"]["test"],
                    "destination_source_sha256": group["source_sha256"],
                    "membership_record": str(record_path),
                    "membership_record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
                }
                row = dict(group["source_row"])
                row.update({"path_function": anchor["identity"][1],
                            "unit": anchor["identity"][2], "enc": int(anchor["identity"][3]),
                            "piece": anchor["identity"][4] or None, "file": str(file_path),
                            "put_json": str(put_json_path), "ce_anchor": metadata,
                            "forge_status": "Success", "ce_anchor_forge_status": "Success",
                            "valid_reference_test": True, "b": True})
                for key in ("raw_tests", "valid_tests", "raw_artifacts", "valid_artifacts"):
                    values = put.setdefault(key, [])
                    if _row_key(row) not in {_row_key(value) for value in values}:
                        values.append(row)
                put_json["fair122_memberships"] = [metadata]
                _atomic(put_json_path, put_json)
                applied.append(anchor["identity"])
        _atomic(result_path, result)
    if len({tuple(identity) for identity in applied}) != 122:
        raise RuntimeError(f"expected 122 applied identities, got {len(applied)}")
    return {"schema": "rq1-fair122-canonical-apply/v1", "applied": applied,
            "count": len(applied)}


def _stage(groups: list[dict], output: Path) -> dict:
    rows = []
    existing_path = output / "report.json"
    existing = {tuple(row.get("key") or []): row for row in
                (_load(existing_path).get("rows", []) if existing_path.is_file() else [])
                if row.get("valid") is True and Path(str(row.get("file") or "")).is_file()}
    for ordinal, group in enumerate(groups):
        if group["key"] in existing:
            rows.append(existing[group["key"]])
            continue
        row = group["row"]
        source_file = Path(row["file"])
        source_root = _project(source_file)
        destination = output / "projects" / f"{ordinal:03d}-{_sha('|'.join(group['key']))[:12]}"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source_root, destination, ignore=shutil.ignore_patterns("cache", "out"),
                        symlinks=True)
        relative = source_file.relative_to(source_root)
        staged_file = destination / relative
        source = staged_file.read_text(encoding="utf-8")
        setup_body = _body(source, "setUp")
        put_test = str(row["test"])
        put_body = _body(source, put_test)
        source = _replace_body(source, "setUp", "\n")
        source = _replace_body(source, put_test, setup_body + "\n" + put_body)
        anchors = []
        for item in group["items"]:
            entry, replay_file = _manifest_entry(item)
            replay_source = replay_file.read_text(encoding="utf-8")
            source = _merge_flat_imports(source, replay_source)
            source = _merge_mock_contracts(source, replay_source)
            replay_setup = _scoped_optional_body(replay_source, "setUp", str(entry["test"]))
            replay_body = _body(replay_source, str(entry["test"]))
            anchor = "test_ce_anchor_" + _sha("|".join(item["identity"]))[:16]
            declarations = [declaration for declaration in _fixture_declarations(replay_source)
                            if declaration not in source]
            prefix = "".join(f"\n  {declaration}" for declaration in declarations)
            anchor_source = (prefix + f"\n  function {anchor}() public {{\n{replay_setup}\n"
                             f"{replay_body}\n  }}\n")
            close = _contract_close(source, put_test)
            source = source[:close] + anchor_source + source[close:]
            anchors.append({"identity": item["identity"], "test": anchor,
                            "replay_file": str(replay_file),
                            "replay_sha256": (entry.get("covered_original_test_sha256")
                                              or entry["test_sha256"]),
                            "manifest": item["manifest"]})
        staged_file.write_text(source, encoding="utf-8")
        put_run = _forge(destination, str(relative), put_test)
        anchor_runs = {anchor["test"]: _forge(destination, str(relative), anchor["test"])
                       for anchor in anchors}
        rows.append({"key": list(group["key"]), "source_result": str(group["result"]),
                     "source_row": row, "project": str(destination), "file": str(staged_file),
                     "relative_file": str(relative), "source_sha256": _sha(source),
                     "put_run": put_run, "anchors": anchors, "anchor_runs": anchor_runs,
                     "valid": put_run["success"] and all(run["success"]
                                                         for run in anchor_runs.values())})
        _atomic(output / "progress.json", {"schema": "rq1-fair122-stage/v1", "rows": rows})
    report = {"schema": "rq1-fair122-stage/v1", "rows": rows,
              "groups": len(rows), "obligations": sum(len(row["anchors"]) for row in rows),
              "valid_groups": sum(row["valid"] for row in rows),
              "valid_obligations": sum(len(row["anchors"]) for row in rows if row["valid"])}
    _atomic(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--rerun-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "report.json"
    report = _load(report_path) if args.apply and report_path.is_file() else _stage(
        _collect(args.inventory, args.rerun_root), args.output)
    if args.apply:
        applied = _apply(report)
        _atomic(args.output / "apply.json", applied)
        print(json.dumps({"applied": applied["count"]}, sort_keys=True))
        return 0
    print(json.dumps({key: report[key] for key in ("groups", "obligations", "valid_groups",
                                                   "valid_obligations")}, sort_keys=True))
    return 0 if report["valid_obligations"] == 122 else 1


if __name__ == "__main__":
    raise SystemExit(main())
