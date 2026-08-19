#!/usr/bin/env python3
"""Insert mechanically matched RQ3 concrete artifacts into an RQ1 tree.

The default mode is a read-only plan.  ``--apply`` writes only the supplied
RQ1 root and records every preimage in a rollback manifest.  This tool adds a
concrete artifact (``is_put=false``); it never upgrades a concrete replay to a
generalized PUT and never edits the RQ3 source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def identity_digest(identity: list[str]) -> str:
    return hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]


def subject_dir(root: Path, case: str) -> Path:
    benchmark, subject = case.split("/", 1)
    return root / benchmark / "subjects" / subject


def project_root(source: Path) -> Path | None:
    for parent in (source.parent, *source.parents):
        if (parent / "foundry.toml").is_file():
            return parent
    return None


def candidate_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return unique, non-ambiguous closure rows; preserve remap tier."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in report.get("matched", []):
        if not isinstance(item, dict):
            continue
        frozen = item.get("frozen_identity")
        candidates = item.get("candidates")
        if not isinstance(frozen, list) or not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            source = candidate.get("file")
            key = ("\t".join(str(x) for x in frozen), str(source))
            if key in seen:
                continue
            seen.add(key)
            rows.append({"frozen_identity": frozen,
                         "match_tier": item.get("match_tier"),
                         "candidate": candidate})
    return rows


def artifact(identity: list[str], candidate: dict[str, Any], file: Path,
             test: str, put_json: Path, source_sha: str,
             match_tier: str | None) -> dict[str, Any]:
    return {
        "case": identity[0],
        "path_function": candidate.get("path_function", identity[1]),
        "unit": candidate.get("unit", identity[2]),
        "enc": candidate.get("enc", identity[3]),
        "piece": candidate.get("piece", identity[4]),
        "kind": "concrete",
        "is_concrete": True,
        "is_put": False,
        "forge_status": candidate.get("forge_status"),
        "file": str(file),
        "test": test,
        "put_json": str(put_json),
        "put_json_sha256": digest(put_json),
        "flat_source_sha256": candidate.get("flat_source_sha256"),
        "concrete_oracles": candidate.get("concrete_oracles") or [],
        "materialization": {"is_concrete": True, "is_put": False},
        "mechanical_origin": {
            "schema": "rq3-mechanical-closure/v1",
            "frozen_identity": identity,
            "match_tier": match_tier,
            "rq3_put_json": candidate.get("put_json"),
            "rq3_put_json_sha256": candidate.get("put_json_sha256"),
            "rq3_source_sha256": source_sha,
        },
    }


def add_once(document: dict[str, Any], row: dict[str, Any]) -> bool:
    put = document.setdefault("put", {})
    if not isinstance(put, dict):
        raise ValueError("result.json put field is not an object")
    raw = put.setdefault("raw_artifacts", [])
    valid = put.setdefault("valid_artifacts", [])
    if not isinstance(raw, list) or not isinstance(valid, list):
        raise ValueError("result.json artifact lists are not arrays")
    marker = (row["put_json"], row["test"], row["path_function"], row["enc"])
    for existing in raw:
        if isinstance(existing, dict) and (existing.get("put_json"), existing.get("test"),
                                           existing.get("path_function"), existing.get("enc")) == marker:
            return False
    raw.append(row)
    valid.append(row.copy())
    for key in ("raw", "valid", "concrete_raw", "concrete_valid"):
        put[key] = sum(1 for item in raw if item.get("kind") == "concrete") if key in ("raw", "concrete_raw") else sum(1 for item in valid if item.get("kind") == "concrete")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true",
                        help="restore preimages and remove directories created by --apply")
    args = parser.parse_args()
    if args.apply and args.rollback:
        parser.error("--apply and --rollback are mutually exclusive")
    if args.rollback:
        manifest = load(args.manifest)
        root = Path(str(manifest["rq1_root"])).resolve()
        restored = 0
        for item in manifest.get("preimages", {}).values():
            path = Path(str(item["path"])).resolve()
            if root not in path.parents:
                raise ValueError(f"rollback path escapes RQ1 root: {path}")
            backup = item.get("backup")
            if backup:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup, path)
                restored += 1
        removed = 0
        for raw in manifest.get("created_directories", []):
            path = Path(str(raw)).resolve()
            if root not in path.parents:
                raise ValueError(f"rollback directory escapes RQ1 root: {path}")
            if path.is_dir():
                shutil.rmtree(path)
                removed += 1
        print(json.dumps({"restored": restored, "removed": removed}, sort_keys=True))
        return 0
    report = load(args.report)
    rows = candidate_rows(report)
    plan: dict[str, Any] = {"schema": "rq3-mechanical-insert/v1",
                            "report": str(args.report.resolve()),
                            "rq1_root": str(args.rq1_root.resolve()),
                            "apply": args.apply, "rows": [], "preimages": {},
                            "created_directories": []}
    backup_dir = args.manifest.parent / "preimages"
    for ordinal, item in enumerate(rows, 1):
        identity = [str(value) for value in item["frozen_identity"]]
        candidate = item["candidate"]
        source = Path(str(candidate.get("file", "")))
        project = project_root(source) if source.is_file() else None
        row = {"ordinal": ordinal, "identity": identity,
               "match_tier": item.get("match_tier"), "status": "refused"}
        if project is None or not candidate.get("test"):
            row["reason"] = "source project or test is absent"
            plan["rows"].append(row)
            continue
        token = identity_digest(identity) + "-" + hashlib.sha256(str(source).encode()).hexdigest()[:12]
        destination = subject_dir(args.rq1_root, identity[0]) / "put" / "rq3-mechanical" / token
        copied_source = destination / "project" / source.relative_to(project)
        put_json = destination / "_wd" / "mechanical" / "put.json"
        result_json = subject_dir(args.rq1_root, identity[0]) / "result.json"
        row.update({"source": str(source), "destination": str(destination),
                    "result_json": str(result_json), "put_json": str(put_json)})
        if args.apply:
            if destination.exists():
                row["reason"] = "destination already exists"
                plan["rows"].append(row)
                continue
            for path in (result_json,):
                if path.is_file():
                    key = str(path.resolve())
                    if key in plan["preimages"]:
                        continue
                    backup = backup_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, backup)
                    plan["preimages"][key] = {"path": key, "backup": str(backup),
                                               "sha256": digest(path)}
            destination.mkdir(parents=True, exist_ok=True)
            plan["created_directories"].append(str(destination))
            shutil.copytree(project, destination / "project", dirs_exist_ok=True)
            put_json.parent.mkdir(parents=True, exist_ok=True)
            source_sha = digest(source)
            put_doc = dict(candidate)
            put_doc.update({"file": str(copied_source), "test": candidate["test"],
                            "path_function": candidate.get("path_function", identity[1]),
                            "unit": candidate.get("unit", identity[2]),
                            "enc": candidate.get("enc", identity[3]),
                            "piece": candidate.get("piece", identity[4]),
                            "kind": "concrete", "is_concrete": True, "is_put": False})
            put_json.write_text(json.dumps(put_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            document = load(result_json) if result_json.is_file() else {"put": {}}
            new_row = artifact(identity, candidate, copied_source, str(candidate["test"]),
                               put_json, source_sha, item.get("match_tier"))
            changed = add_once(document, new_row)
            if changed:
                result_json.parent.mkdir(parents=True, exist_ok=True)
                result_json.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            row["status"] = "inserted" if changed else "already-present"
        else:
            row["status"] = "planned"
        plan["rows"].append(row)
    plan["summary"] = {"candidates": len(rows),
                        "planned": sum(x["status"] == "planned" for x in plan["rows"]),
                        "inserted": sum(x["status"] == "inserted" for x in plan["rows"]),
                        "refused": sum(x["status"] == "refused" for x in plan["rows"]),
                        "ambiguous_excluded": len(report.get("ambiguous", [])),
                        "missing_excluded": len(report.get("missing", []))}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(plan["summary"], sort_keys=True))
    return 0 if not plan["summary"]["refused"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
