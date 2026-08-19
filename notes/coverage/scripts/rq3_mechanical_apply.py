#!/usr/bin/env python3
"""Copy source-backed RQ3 concrete closures into isolated RQ1 subtrees.

This is intentionally not PUT adoption: it does not modify ``put`` counters,
frozen identities, or claim Forge status.  Each copied closure records both
the frozen target identity and the original RQ3 identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def subject(root: Path, identity: list[str]) -> Path:
    benchmark, case = identity[0].split("/", 1)
    return root / benchmark / "subjects" / case


def project_root(source: Path) -> Path | None:
    for parent in (source.parent, *source.parents):
        if (parent / "foundry.toml").is_file():
            return parent
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--canonical-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding="utf-8"))
    rows = document.get("rows") or []
    plan = []
    for row in rows:
        candidate = row.get("rq3_candidate") or {}
        source = Path(str(candidate.get("file") or ""))
        put_json = Path(str(candidate.get("put_json") or ""))
        target_subject = subject(args.canonical_root, list(row["frozen_identity"]))
        if row.get("insertion_status") != "mapped" or not source.is_file():
            plan.append({"status": "metadata-only" if row.get("insertion_status") == "mapped"
                         else "missing", "identity": row["frozen_identity"]})
            continue
        project = project_root(source)
        if project is None or not put_json.is_file():
            plan.append({"status": "refused", "identity": row["frozen_identity"],
                         "reason": "source project or put.json absent"})
            continue
        key = hashlib.sha256(json.dumps(row["frozen_identity"],
                                       separators=(",", ":")).encode()).hexdigest()[:16]
        destination = target_subject / "put" / str(row["frozen_identity"][2]) / "rq3-mechanical" / key
        plan.append({"status": "source-backed", "identity": row["frozen_identity"],
                     "rq3_identity": candidate.get("identity"), "match_tier": row.get("match_tier"),
                     "source": str(source), "put_json": str(put_json),
                     "destination": str(destination), "source_sha256": digest(source),
                     "put_json_sha256": digest(put_json)})
    summary = {"rows": len(rows),
               "source_backed": sum(item["status"] == "source-backed" for item in plan),
               "metadata_only": sum(item["status"] == "metadata-only" for item in plan),
               "missing": sum(item["status"] == "missing" for item in plan),
               "refused": sum(item["status"] == "refused" for item in plan)}
    report = {"schema": "veriput-rq3-mechanical-closure/v1", "manifest": str(args.manifest),
              "canonical_write": bool(args.apply), "summary": summary, "rows": plan}
    report_path = args.backup_root / "mechanical-closure-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.apply:
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        print(json.dumps(summary, sort_keys=True))
        return 0
    args.backup_root.mkdir(parents=True, exist_ok=True)
    backups = []
    for item in plan:
        if item["status"] != "source-backed":
            continue
        identity = item["identity"]
        target_subject = subject(args.canonical_root, identity)
        result = target_subject / "result.json"
        if not result.is_file():
            item["status"] = "refused"
            item["reason"] = "canonical result.json absent"
            continue
        backup = args.backup_root / "results" / identity[0].replace("/", "__") / (identity[2] + ".json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result, backup)
        backups.append(str(backup))
    for item in plan:
        if item["status"] != "source-backed":
            continue
        source = Path(item["source"])
        put_json = Path(item["put_json"])
        destination = Path(item["destination"])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "test").mkdir(exist_ok=True)
        shutil.copy2(source, destination / "test" / source.name)
        project = project_root(source)
        assert project is not None
        (destination / "src").mkdir(exist_ok=True)
        flat = project / "src" / "flat.sol"
        if flat.is_file():
            shutil.copy2(flat, destination / "src" / "flat.sol")
        shutil.copy2(put_json, destination / "put.json")
        result = subject(args.canonical_root, item["identity"]) / "result.json"
        document = json.loads(result.read_text(encoding="utf-8"))
        closures = document.setdefault("rq3_mechanical_closure", [])
        closures[:] = [row for row in closures
                       if row.get("frozen_identity") != item["identity"]]
        closures.append({"schema": "veriput-rq3-mechanical-closure-entry/v1",
                         "frozen_identity": item["identity"],
                         "rq3_identity": item.get("rq3_identity"),
                         "match_tier": item.get("match_tier"),
                         "source": str(destination / "test" / source.name),
                         "put_json": str(destination / "put.json"),
                         "source_sha256": item["source_sha256"],
                         "put_json_sha256": item["put_json_sha256"],
                         "forge_run": False, "put_credit": False})
        result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report["backups"] = backups
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
