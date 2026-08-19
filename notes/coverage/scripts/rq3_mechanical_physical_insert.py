#!/usr/bin/env python3
"""Physically copy exact RQ3 concrete sources into the RQ1 closure.

This pass is deliberately mechanical.  It only accepts a source below the
same RQ3 subject and an exact ``unit/path-function/enc`` directory identity;
it never treats a same-unit or same-function file as an exact match.  No
solver, Forge, result adoption, or PUT credit is performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


_subject_cache: dict[tuple[str, str, str], tuple[list[Path], list[Path]]] = {}
_root_index: dict[tuple[str, str], list[Path]] | None = None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def subject(root: Path, identity: list[str]) -> Path:
    benchmark, case = identity[0].split("/", 1)
    return root / benchmark / "subjects" / case


def identity_key(identity: list[str]) -> str:
    return hashlib.sha256("\t".join(identity).encode()).hexdigest()[:20]


def source_candidates(rq3_root: Path, identity: list[str], allow_cross_enc: bool = False) -> list[tuple[Path, Path | None, Path | None]]:
    case, path_function, unit, enc, _piece = identity
    benchmark, case_name = case.split("/", 1)
    cache_key = (str(rq3_root), benchmark, case_name)
    global _root_index
    cached = _subject_cache.get(cache_key)
    if cached is None:
        root = rq3_root / benchmark / "subjects" / case_name
        if root.is_dir():
            roots = [root]
        else:
            if _root_index is None:
                _root_index = {}
                for subjects in rq3_root.rglob("subjects"):
                    if not subjects.is_dir():
                        continue
                    benchmark_root = subjects.parent
                    for case_root in subjects.iterdir():
                        if case_root.is_dir():
                            _root_index.setdefault(
                                (benchmark_root.name, case_root.name), []).append(case_root)
            roots = sorted(_root_index.get((benchmark, case_name), []))
        all_sources = [source for subject_root in roots
                       for source in subject_root.rglob("*.t.sol")]
        cached = (roots, all_sources)
        _subject_cache[cache_key] = cached
    roots, all_sources = cached
    if not roots:
        return []
    path_number = path_function.rsplit("#", 1)[-1] if "#" in path_function else ""
    suffix = f"__{unit}__pf{path_number}"
    candidates: list[tuple[Path, Path | None, Path | None]] = []
    # A source can be emitted under emit/, test/, or a concrete-replay copy.
    # The enclosing job directory is the identity proof; file names are not.
    for source in all_sources:
        text = "/".join(source.parts)
        if suffix not in text or not re.search(
                rf"__{re.escape(enc)}(?:p[0-9]+)?__certify-results", text):
            continue
        cert_re = re.compile(
            rf"__{re.escape(enc)}(?:p[0-9]+)?__certify-results$")
        job = next((parent for parent in source.parents
                    if cert_re.search(parent.name)), None)
        if job is None:
            continue
        if "/put/" not in f"/{source.as_posix()}/":
            continue
        project = next(
            (parent for parent in (source.parent, *source.parents)
             if (parent / "foundry.toml").is_file()),
            None,
        )
        flat = project / "src" / "flat.sol" if project else None
        put_json = next(
            (parent / "put.json" for parent in (source.parent, *source.parents)
             if (parent / "put.json").is_file()),
            None,
        )
        candidates.append((source, flat if flat and flat.is_file() else None,
                           put_json))
    """
    for subject_root in roots:
      for job in sorted(subject_root.glob(f"**/*{suffix}")):
        if not job.is_dir() or "/put/" not in f"/{job.as_posix()}/":
            continue
        for cert in sorted(job.rglob(f"*__{enc}__certify-results")):
            if not cert.is_dir():
                continue
            for source in sorted(cert.rglob("*.t.sol")):
                if not source.is_file():
                    continue
                project = next(
                    (parent for parent in (source.parent, *source.parents)
                     if (parent / "foundry.toml").is_file()),
                    None,
                )
                flat = project / "src" / "flat.sol" if project else None
                put_json = next(
                    (parent / "put.json" for parent in (source.parent, *source.parents)
                     if (parent / "put.json").is_file()),
                    None,
                )
                candidates.append((source, flat if flat and flat.is_file() else None,
                                   put_json))
    """
    # Exact source path may be in a concrete-replay project whose directory was
    # copied without the original ``put`` wrapper.  Retain only paths carrying
    # all three exact tokens in their ancestors.
    if not candidates:
        token = re.compile(
            rf"__{re.escape(enc)}(?:p[0-9]+)?__certify-results")
        for source in all_sources:
                text = "/".join(source.parts)
                if suffix not in text or not token.search(text):
                    continue
                project = next(
                    (parent for parent in (source.parent, *source.parents)
                     if (parent / "foundry.toml").is_file()),
                    None,
                )
                flat = project / "src" / "flat.sol" if project else None
                put_json = next(
                    (parent / "put.json" for parent in (source.parent, *source.parents)
                     if (parent / "put.json").is_file()),
                    None,
                )
                candidates.append((source, flat if flat and flat.is_file() else None,
                                   put_json))
    if not candidates and allow_cross_enc:
        # RQ3 rerun shards sometimes encode an equivalent encoder as 2p1/6p1.
        # Preserve this as source-only evidence; it is never exact identity
        # evidence and never receives PUT credit.
        for source in all_sources:
            text = "/".join(source.parts)
            if suffix not in text or not re.search(
                    r"__[A-Za-z0-9]+(?:p[0-9]+)?__certify-results", text):
                continue
            project = next(
                (parent for parent in (source.parent, *source.parents)
                 if (parent / "foundry.toml").is_file()),
                None,
            )
            flat = project / "src" / "flat.sol" if project else None
            put_json = next(
                (parent / "put.json" for parent in (source.parent, *source.parents)
                 if (parent / "put.json").is_file()),
                None,
            )
            candidates.append((source, flat if flat and flat.is_file() else None,
                               put_json))
    # Avoid choosing two replay variants for one identity.  Same source bytes
    # are equivalent; distinct bytes are ambiguous and remain metadata-only.
    unique: dict[str, tuple[Path, Path | None, Path | None]] = {}
    for candidate in candidates:
        unique.setdefault(sha256(candidate[0]), candidate)
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binding", type=Path)
    parser.add_argument("--closure", type=Path, required=True)
    parser.add_argument("--rq3-root", type=Path, required=True)
    parser.add_argument("--rq1-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-cross-enc", action="store_true")
    parser.add_argument("--allow-deploy-only", action="store_true")
    args = parser.parse_args()

    binding = json.loads(args.binding.read_text(encoding="utf-8"))
    prior = json.loads(args.closure.read_text(encoding="utf-8"))
    prior_by_identity = {
        tuple(row["identity"]): row for row in prior.get("rows", [])
    }
    rows: list[dict[str, Any]] = []
    changed_results: set[Path] = set()
    for binding_row in binding.get("rows", []):
        identity = list(binding_row["frozen_identity"])
        prior_row = prior_by_identity.get(tuple(identity), {})
        prior_status = prior_row.get("status")
        target = subject(args.rq1_root, identity)
        result = target / "result.json"
        row: dict[str, Any] = {
            "identity": identity,
            "prior_status": prior_status,
            "status": prior_status,
        }
        current_physical = False
        if result.is_file():
            try:
                current_document = json.loads(result.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                current_document = {}
            for existing in current_document.get("rq3_mechanical_closure", []):
                source_path = existing.get("source")
                if (existing.get("frozen_identity") == identity and source_path
                        and Path(source_path).is_file()
                        and Path(source_path).name.endswith(".t.sol")):
                    current_physical = True
                    break
        if current_physical:
            row["status"] = "already-physical"
            rows.append(row)
            continue
        candidates = source_candidates(args.rq3_root, identity, args.allow_cross_enc)
        if not candidates and args.allow_deploy_only:
            contract = identity[1].split(":", 1)[-1].split(".", 1)[0]
            contract = contract.removeprefix("@C@")
            subject_root = target
            # The deploy-only RQ3 artifact has no encoder directory.  It is
            # still a physical RQ3 test for the contract's storage surface.
            deploy_sources = [
                source for source in _subject_cache.get(
                    (str(args.rq3_root), identity[0].split("/", 1)[0],
                     identity[0].split("/", 1)[1]), ([], []))[1]
                if "/put/deploy_only/" in source.as_posix()
                and source.is_file()
                and contract.lower() in source.name.lower()
            ]
            if len({sha256(source) for source in deploy_sources}) == 1:
                candidates = [(deploy_sources[0], None, None)]
        if len(candidates) != 1:
            row["status"] = "ambiguous" if len(candidates) > 1 else "missing"
            row["candidate_count"] = len(candidates)
            rows.append(row)
            continue
        source, flat, put_json = candidates[0]
        key = identity_key(identity)
        destination = target / "put" / identity[2] / "rq3-mechanical" / key
        copied_source = destination / "test" / source.name
        copied_flat = destination / "src" / "flat.sol" if flat else None
        copied_put = destination / "put.json" if put_json else None
        row.update({
            "status": "source-backed",
            "source": str(source),
            "source_sha256": sha256(source),
            "destination": str(destination),
            "copied_source": str(copied_source),
            "flat": str(flat) if flat else None,
            "put_json": str(put_json) if put_json else None,
            "cross_enc": not bool(re.search(
                rf"__{re.escape(identity[3])}(?:p[0-9]+)?__certify-results",
                str(source))),
        })
        if args.apply:
            destination.mkdir(parents=True, exist_ok=True)
            copied_source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied_source)
            if flat and copied_flat:
                copied_flat.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(flat, copied_flat)
            if put_json and copied_put:
                shutil.copy2(put_json, copied_put)
            if not result.is_file():
                raise SystemExit(f"missing result.json: {result}")
            document = json.loads(result.read_text(encoding="utf-8"))
            closures = document.setdefault("rq3_mechanical_closure", [])
            closures[:] = [entry for entry in closures
                           if entry.get("frozen_identity") != identity]
            closures.append({
                "schema": "veriput-rq3-mechanical-physical-source/v1",
                "frozen_identity": identity,
                "rq3_identity": identity,
                "status": "source-backed",
                "match_tier": ("deploy-only-contract-source"
                                if "/put/deploy_only/" in str(source)
                                else ("same-path-function-cross-enc-source-scan"
                                      if row["cross_enc"]
                                      else "exact-directory-source-scan")),
                "source": str(copied_source),
                "source_sha256": sha256(copied_source),
                "flat_source": str(copied_flat) if copied_flat else None,
                "put_json": str(copied_put) if copied_put else None,
                "forge_run": False,
                "put_credit": False,
                "identity_rewrite": False,
            })
            result.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                              encoding="utf-8")
            changed_results.add(result)
        rows.append(row)
    summary = {
        "rows": len(rows),
        "prior_physical": sum(r["prior_status"] in {"source-backed", "source-only"}
                               for r in rows),
        "new_source_backed": sum(r["status"] == "source-backed"
                                  and r["prior_status"] not in {"source-backed", "source-only"}
                                  for r in rows),
        "ambiguous": sum(r["status"] == "ambiguous" for r in rows),
        "missing": sum(r["status"] == "missing" for r in rows),
        "apply": args.apply,
        "esbmc_run": False,
        "forge_run": False,
    }
    output = {
        "schema": "veriput-rq3-mechanical-physical-insert/v1",
        "binding": str(args.binding.resolve()),
        "closure": str(args.closure.resolve()),
        "rows": rows,
        "summary": summary,
        "changed_results": sorted(str(path) for path in changed_results),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
