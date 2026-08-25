#!/usr/bin/env python3
"""Persist RQ1 concrete replays as self-contained canonical Forge projects.

The Stage-4 working tree is provenance, not durable replay storage: old rows
can name a temporary directory and a later adoption can move the producing
tree.  This module copies a green concrete artifact, its exact flat source,
Foundry configuration and forge-std into the canonical subject directory.
The manifest contains only subject-relative executable paths plus hashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

SCHEMA = "veriput-rq1-concrete-replay-manifest/v1"
STORE_DIR = "concrete-replays"
MANIFEST_NAME = "manifest.json"
DEFAULT_INVALIDATION_LEDGER = Path(__file__).resolve().parents[1] / \
    "rq1_tuple_frontend_pollution_audit.json"


class ReplayPersistenceError(ValueError):
    """A claimed replay cannot be persisted without weakening its evidence."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or ""))
    return text.strip("_") or "unnamed"


def _atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent,
                                     encoding="utf-8") as stream:
        json.dump(doc, stream, indent=2, sort_keys=True)
        stream.write("\n")
        tmp = Path(stream.name)
    os.replace(tmp, path)


def _foundry_project(test_file: Path) -> Path:
    for parent in (test_file.parent, *test_file.parents):
        if (parent / "foundry.toml").is_file() and (parent / "src").is_dir():
            return parent
    raise ReplayPersistenceError(f"no Foundry project owns {test_file}")


def _copy_file(source: Path, destination: Path) -> None:
    """Copy bytes and metadata without preserving a source inode link."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    shutil.copystat(source, destination, follow_symlinks=True)


def _privatize_tree(root: Path) -> None:
    """Break legacy hard links so the canonical project owns every byte."""
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink() or path.stat().st_nlink <= 1:
            continue
        with tempfile.NamedTemporaryFile(delete=False, dir=path.parent) as stream:
            replacement = Path(stream.name)
        try:
            _copy_file(path, replacement)
            os.replace(replacement, path)
        finally:
            replacement.unlink(missing_ok=True)


def _copy_tree(source: Path, destination: Path) -> None:
    source = source.resolve()
    if not source.is_dir():
        raise ReplayPersistenceError(f"missing replay dependency tree: {source}")
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_symlink():
            resolved = item.resolve()
            if resolved.is_dir():
                _copy_tree(resolved, target)
            elif resolved.is_file():
                _copy_file(resolved, target)
        elif item.is_file():
            _copy_file(item, target)


def _load_record(path: object) -> dict:
    if not path:
        return {}
    try:
        doc = json.loads(Path(str(path)).read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return doc if isinstance(doc, dict) else {}


def replay_identity(row: dict) -> dict:
    record = _load_record(row.get("put_json"))
    return {
        "unit": row.get("unit") or record.get("unit"),
        "enc": row.get("enc") if row.get("enc") is not None else record.get("enc"),
        "piece": row.get("piece") if row.get("piece") is not None else record.get("piece"),
        "path_function": row.get("path_function") or record.get("path_function"),
        "stage2_source": row.get("stage2_source") or record.get("stage2_source"),
        "stage2_witness_check": (row.get("stage2_witness_check")
                                 or record.get("stage2_witness_check")),
        # One certified path can split into several oracle input parts, and each
        # final part keeps its own basis replay.  Without the part in the
        # identity all of a split path's bases collide on
        # (path_function, unit, enc, piece) and derivation reports a duplicate.
        # Unsplit rows carry None here, which keeps legacy identities unchanged.
        "oracle_input_part": (row.get("basis_for_oracle_input_part")
                              if row.get("basis_for_oracle_input_part") is not None else
                              record.get("basis_for_oracle_input_part")),
    }


def _relative_provenance(subject_dir: Path, path: object) -> dict:
    """Record a durable relative path, or only a hash for an external source."""
    if not path:
        return {"path_class": "missing"}
    candidate = Path(str(path)).expanduser().resolve()
    record = {
        "path_class": "external",
        "sha256": _sha256(candidate) if candidate.is_file() else None,
    }
    try:
        record["path"] = candidate.relative_to(subject_dir.resolve()).as_posix()
    except ValueError:
        return record
    record["path_class"] = "canonical-subject"
    return record


def _artifact_key(row: dict) -> tuple:
    identity = replay_identity(row)
    return (str(identity.get("path_function") or ""), str(identity.get("unit") or ""),
            str(identity.get("enc") if identity.get("enc") is not None else ""),
            str(identity.get("piece") if identity.get("piece") is not None else ""))


def _concrete_test_key(row: dict) -> tuple:
    """Identify one concrete test, not merely its path or Solidity name."""
    test_file = Path(str(row.get("file") or ""))
    flat_file = test_file.parent.parent / "src" / "flat.sol"
    return (_artifact_key(row), str(row.get("test")
                                    or ""), _sha256(test_file) if test_file.is_file() else None,
            _sha256(flat_file) if flat_file.is_file() else None)


def _entry_test_key(entry: dict) -> tuple:
    origin = entry.get("origin") if isinstance(entry, dict) else {}
    origin = origin if isinstance(origin, dict) else {}
    key = (str(origin.get("path_function")
               or ""), str(origin.get("unit")
                           or ""), str(origin.get("enc") if origin.get("enc") is not None else ""),
           str(origin.get("piece") if origin.get("piece") is not None else ""))
    return (key, str(entry.get("test") or ""), entry.get("test_sha256"), entry.get("flat_sha256"))


def persistence_publication_key(row: dict) -> str | None:
    """Return the hash-bound identity used to authorize one published row."""
    kind = row.get("kind") if isinstance(row, dict) else None
    if kind == "concrete":
        key = (kind, *_concrete_test_key(row))
    elif kind == "put":
        put_json = Path(str(row.get("put_json") or ""))
        key = (kind, _artifact_key(row), str(row.get("test") or ""),
               _sha256(put_json) if put_json.is_file() else "")
    else:
        return None
    return json.dumps(key, separators=(",", ":"), sort_keys=True)


def _put_requires_concrete_basis(row: dict) -> bool:
    """Only region-refined PUTs need a retained point replay for RQ3."""
    if not isinstance(row, dict) or row.get("kind") != "put":
        return False
    record = _load_record(row.get("put_json"))
    derived_by = row.get("derived_by") if isinstance(row.get("derived_by"), dict) else None
    if derived_by is None:
        derived_by = record.get("derived_by") if isinstance(record.get("derived_by"), dict) else {}
    return bool(derived_by.get("region_refinement_used"))


def _fused_put_basis_error(row: dict) -> str | None:
    """Audit fixed witness assertions already fused into a green PUT."""
    put_json = Path(str(row.get("put_json") or ""))
    record = _load_record(put_json)
    if not put_json.is_file() or not record:
        return "fused PUT basis put.json is unavailable"
    if row.get("valid_reference_test") is not True or row.get("forge_status") != "Success":
        return "fused PUT basis is not a green Full row"
    if record.get("kind") != "put":
        return "fused PUT basis record is not a PUT"
    fusion = record.get("fixed_replay_fusion")
    if not isinstance(fusion, dict) or fusion.get("status") != "fused":
        return "fixed replay assertions are not fused into this PUT"
    if fusion.get("destination_put_test") != (row.get("test") or record.get("test")):
        return "fused PUT basis names a different test"
    test_file = Path(str(row.get("file") or record.get("file") or ""))
    if not test_file.is_file() or _sha256(test_file) != fusion.get("destination_source_sha256"):
        return "fused PUT basis source hash differs"
    if not re.fullmatch(r"[0-9a-f]{64}", str(fusion.get("certified_ce_sha256") or "")):
        return "fused PUT basis lacks a certified CE hash"
    if not isinstance(fusion.get("oracles"), list):
        return "fused PUT basis lacks fixed replay oracle metadata"
    return None


def _entry_test_keys(entry: dict) -> set[tuple]:
    """Include the immutable source identity of a strictly augmented replay."""
    keys = {_entry_test_key(entry)}
    origin = entry.get("origin") if isinstance(entry, dict) else None
    if (isinstance(origin, dict) and entry.get("recovered_from_test_sha256")
            and entry.get("recovered_from_flat_sha256")):
        key = _entry_test_key(entry)[0]
        keys.add((key, str(entry.get("test") or ""), entry.get("recovered_from_test_sha256"),
                  entry.get("recovered_from_flat_sha256")))
    covered_identity = entry.get("covered_original_identity")
    if covered_identity is None and isinstance(origin, dict):
        covered_identity = origin.get("covered_original_identity")
    covered_test = entry.get("covered_original_test")
    if covered_test is None and isinstance(origin, dict):
        covered_test = origin.get("covered_original_test")
    if (isinstance(covered_identity, dict) and entry.get("covered_original_test_sha256")
            and entry.get("covered_original_flat_sha256")):
        key = (
            str(covered_identity.get("path_function") or ""),
            str(covered_identity.get("unit") or ""),
            str(covered_identity.get("enc") if covered_identity.get("enc") is not None else ""),
            str(covered_identity.get("piece") if covered_identity.get("piece") is not None else ""),
        )
        keys.add((key, str(covered_test or entry.get("test")
                           or ""), entry.get("covered_original_test_sha256"),
                  entry.get("covered_original_flat_sha256")))
    return keys


def _entry_is_currently_not_generalized(entry: dict, put_keys: set[tuple]) -> bool:
    """Reject a stale not-generalized label when a current exact PUT exists."""
    if entry.get("generalization_status") != "not-generalized":
        return False
    origin = entry.get("origin") if isinstance(entry, dict) else None
    if not isinstance(origin, dict):
        return False
    # Legacy source-grounded rows may lack path_function. Their target-local
    # (unit, enc, piece) fallback is still sufficient to reject a collision.
    key = _artifact_key(origin)
    if key in put_keys:
        return False
    if not key[0]:
        return not any(candidate[1:] == key[1:] for candidate in put_keys)
    return True


def _authenticated_put_basis_hashes(entry: dict, subject_dir: Path | None = None) -> dict[str, str]:
    """Return retained PUT hashes authenticated by Stage-2 basis metadata."""
    origin = entry.get("origin") if isinstance(entry, dict) else None
    if not isinstance(origin, dict):
        return {}
    put_origin = origin.get("put_json")
    if not isinstance(put_origin, dict) or not put_origin.get("sha256"):
        return {}
    if origin.get("stage2_witness_check") != "CERTIFIED-BASIS-REPLAY":
        return {}
    if origin.get("stage2_source") not in ("certified-region-concrete-fallback",
                                           "certified-region-point", "certified_region",
                                           "certified-region"):
        return {}
    hashes = {
        str(put_origin["sha256"]): "authenticated-put-json-sha256",
    }
    # Certified basis concrete rows store their own __basis_concrete/put.json.
    # The parameterized PUT is the sibling workdir without that suffix.
    rel_path = str(put_origin.get("path") or "")
    # The basis child's workdir is the PUT's workdir plus `__basis_concrete`
    # -- and, since put_all splits a fused PUT into one basis per oracle
    # input part, plus `__basis_concrete__<part>` (put_all.py, basis_wd).
    # Only the unsuffixed form was recognised here, so no part-split PUT was
    # ever authenticated against its basis and every one of them was demoted
    # as "lacks an exact concrete basis replay" with Forge green. MEASURED:
    # IRMLinearKink.computeInterestRateView, 4 green PUTs, put_valid 0
    # (fix-20260825-ceargs2); 9 subjects in the region-found-no-put census.
    sibling = re.match(r"^(.*)__basis_concrete(?:__[A-Za-z0-9_]+)?/put\.json$", rel_path)
    if subject_dir is not None and sibling:
        put_path = subject_dir / (sibling.group(1) + "/put.json")
        if put_path.is_file():
            hashes[_sha256(put_path)] = "authenticated-basis-sibling-put-json-sha256"
    return hashes


def _execution_evidence_errors(subject_dir: Path, entry: dict, project: Path,
                               test_file: Path) -> list[str]:
    """Validate non-Forge-log execution evidence for metadata-only recovery."""
    evidence = entry.get("execution_evidence")
    if not evidence:
        return [f"{entry.get('replay_id')}: missing Forge replay log"]
    if not isinstance(evidence, dict) or evidence.get("kind") != "put-summary-row":
        return [f"{entry.get('replay_id')}: unsupported execution evidence"]
    summary_name = str(evidence.get("summary_file") or "")
    summary_path = project / summary_name
    if (not summary_name or not summary_path.is_file()
            or summary_path.resolve().parent != project.resolve()):
        return [f"{entry.get('replay_id')}: missing put-summary execution evidence"]
    expected_sha = evidence.get("summary_sha256")
    if expected_sha != _sha256(summary_path):
        return [f"{entry.get('replay_id')}: put-summary evidence hash mismatch"]
    try:
        summary = json.loads(summary_path.read_text(errors="replace"))
    except json.JSONDecodeError:
        return [f"{entry.get('replay_id')}: invalid put-summary execution evidence"]
    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
    expected_file_hash = evidence.get("test_sha256")
    if expected_file_hash != _sha256(test_file):
        return [f"{entry.get('replay_id')}: execution evidence test hash mismatch"]
    rows = (summary.get("deliverable_b") or {}).get("rows") or []
    matched = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_file = Path(str(row.get("file") or ""))
        same_file = row_file.name == test_file.name
        row_hash = _sha256(row_file) if row_file.is_file() else None
        if (row.get("kind") == "concrete"
                and str(row.get("test") or "") == str(entry.get("test") or "")
                and str(row.get("unit") or "") == str(origin.get("unit") or "")
                and str(row.get("enc")) == str(origin.get("enc")) and same_file
                and row_hash == expected_file_hash and row.get("forge_status") == "Success"
                and row.get("valid_reference_test") is True):
            matched = True
            break
    if not matched:
        return [f"{entry.get('replay_id')}: put-summary evidence row not exact"]
    return []


def _semantic_solidity(source: str,
                       *,
                       preserve_strings: bool = False,
                       preserve_length: bool = False) -> str:
    """Remove comments and string contents without letting either hide code."""
    output = []
    position = 0
    while position < len(source):
        start = position
        if source.startswith("//", position):
            end = source.find("\n", position + 2)
            position = len(source) if end < 0 else end
            output.append(" " * (position - start))
        elif source.startswith("/*", position):
            end = source.find("*/", position + 2)
            position = len(source) if end < 0 else end + 2
            output.append(" " * (position - start))
        elif source[position] in ('"', "'"):
            quote = source[position]
            position += 1
            while position < len(source):
                if source[position] == "\\":
                    position += 2
                elif source[position] == quote:
                    position += 1
                    break
                else:
                    position += 1
            if preserve_strings:
                output.append(source[start:position])
            elif preserve_length:
                output.append(" " * (position - start))
            else:
                output.extend((quote, quote))
        else:
            output.append(source[position])
            position += 1
    return "".join(output)


def _solidity_function(source: str, name: str) -> tuple[str, str] | None:
    """Return one generated test's parameter list and body."""
    semantic = _semantic_solidity(source, preserve_length=True)
    match = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", semantic)
    if match is None:
        return None
    start = match.end()
    depth = 1
    index = start
    while index < len(semantic) and depth:
        depth += (semantic[index] == "(") - (semantic[index] == ")")
        index += 1
    if depth:
        return None
    params = source[start:index - 1]
    body_start = semantic.find("{", index)
    if body_start < 0:
        return None
    depth = 1
    body_end = body_start + 1
    while body_end < len(semantic) and depth:
        depth += (semantic[body_end] == "{") - (semantic[body_end] == "}")
        body_end += 1
    if depth:
        return None
    return params, source[body_start + 1:body_end - 1]


def _solidity_function_sources(source: str, name: str) -> list[tuple[str, str, str]]:
    """Return every exact function source, parameter list, and body for name."""
    semantic = _semantic_solidity(source, preserve_length=True)
    results = []
    for match in re.finditer(r"\bfunction\s+" + re.escape(name) + r"\s*\(", semantic):
        params_start = match.end()
        params_end = _matching_delimiter(semantic, params_start - 1, "(", ")")
        if params_end is None:
            continue
        body_start = semantic.find("{", params_end + 1)
        if body_start < 0 or ";" in semantic[params_end + 1:body_start]:
            continue
        body_end = _matching_delimiter(semantic, body_start, "{", "}")
        if body_end is None:
            continue
        results.append((source[match.start():body_end + 1], source[params_start:params_end],
                        source[body_start + 1:body_end]))
    return results


def _structural_put_basis_error(row: dict) -> str | None:
    """Audit a structural ABI-gate anchor as a non-CE PUT basis.

    This evidence class is intentionally distinct from a certified solver CE.
    It is accepted only when the Stage-4 summary, put.json, generated source,
    and both Forge gates bind to the same parameterized PUT and fixed anchor.
    """
    put_json = Path(str(row.get("put_json") or ""))
    record = _load_record(put_json)
    if not put_json.is_file() or not record:
        return "structural basis put.json is unavailable"
    record_anchor = record.get("ce_anchor")
    evidence_row = row
    required_summary = ("ce_anchor_forge_status", "gates", "certification_source",
                        "certified_detail_source", "certified_detail_stage4_kind")
    if any(evidence_row.get(field) is None for field in required_summary):
        summary_path = put_json.parents[2] / "put-summary.json"
        try:
            summary = json.loads(summary_path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError, IndexError):
            return "structural basis put-summary evidence is unavailable"
        candidates = [
            item for item in (summary.get("deliverable_b") or {}).get("rows") or []
            if isinstance(item, dict) and item.get("kind") == "put"
            and item.get("test") == row.get("test") and item.get("file") == row.get("file")
            and item.get("unit") == row.get("unit") and item.get("enc") == row.get("enc")
            and item.get("piece") == row.get("piece") and item.get("ce_anchor") == record_anchor
        ]
        if len(candidates) != 1:
            return "structural basis put-summary row is absent or ambiguous"
        evidence_row = candidates[0]
    if (evidence_row.get("kind") != "put" or evidence_row.get("is_put") is not True
            or evidence_row.get("b") is not True
            or evidence_row.get("valid_reference_test") is not True
            or evidence_row.get("forge_status") != "Success"
            or evidence_row.get("ce_anchor_forge_status") != "Success"
            or evidence_row.get("refused") is True or evidence_row.get("stale")):
        return "structural basis summary is not a green deliverable PUT"
    gates = evidence_row.get("gates")
    if (not isinstance(gates, dict) or any(
            gates.get(name) is not True
            for name in ("fuzz", "width", "assert", "green", "corpus"))):
        return "structural basis summary does not retain all PUT gates"
    anchor = row.get("ce_anchor") or evidence_row.get("ce_anchor")
    if not isinstance(anchor, dict) or anchor != record_anchor:
        return "structural basis summary and put.json anchors differ"
    if (anchor.get("status") != "embedded" or anchor.get("binding") != "structural-abi-gate/v1"
            or anchor.get("basis_kind") != "structural-certificate-not-solver-ce"
            or anchor.get("certification_source") != "structural-abi-gate-no-coordinate"):
        return "structural basis provenance is incomplete"
    if (record.get("kind") != "put" or record.get("stage2_source") != "certified-region"
            or record.get("stage4_kind") != "abi-value-gate"
            or record.get("certification_source") != "structural-abi-gate-no-coordinate"
            or record.get("certified_detail_source") != "structural-abi-gate-no-coordinate"
            or record.get("certified_detail_stage4_kind") != "abi-value-gate"
            or evidence_row.get("stage2_source") != "certified-region"
            or evidence_row.get("stage4_kind") != "abi-value-gate"
            or evidence_row.get("certification_source") != "structural-abi-gate-no-coordinate"
            or evidence_row.get("certified_detail_source") != "structural-abi-gate-no-coordinate"
            or evidence_row.get("certified_detail_stage4_kind") != "abi-value-gate"):
        return "structural basis certificate source is not exact"

    for field in ("path_function", "unit", "enc", "piece", "test"):
        if (field == "path_function" and not record.get(field)):
            return "structural basis put.json lacks path_function"
        if row.get(field) is not None and row.get(field) != record.get(field):
            return f"structural basis summary differs from put.json on {field}"
    destination_test = str(anchor.get("destination_put_test") or "")
    anchor_test = str(anchor.get("test") or "")
    if (not destination_test or destination_test != str(row.get("test") or "")
            or not re.fullmatch(r"test_structural_anchor_[0-9a-f]{16}", anchor_test)):
        return "structural basis test identity is invalid"

    source_path = Path(str(row.get("file") or ""))
    record_path = Path(str(record.get("file") or ""))
    if (not source_path.is_file() or not record_path.is_file()
            or source_path.resolve() != record_path.resolve()):
        return "structural basis source file differs from put.json"
    source = source_path.read_text(errors="replace")
    if anchor.get("destination_source_sha256") != hashlib.sha256(
            source.encode("utf-8")).hexdigest():
        return "structural basis destination source hash differs"
    destination_functions = _solidity_function_sources(source, destination_test)
    anchor_functions = _solidity_function_sources(source, anchor_test)
    if len(destination_functions) != 1 or len(anchor_functions) != 1:
        return "structural basis functions are absent or ambiguous"
    destination_source, destination_params, destination_body = destination_functions[0]
    if anchor.get("destination_put_function_sha256") != hashlib.sha256(
            destination_source.encode("utf-8")).hexdigest():
        return "structural basis destination function hash differs"
    semantic_destination = re.sub(r"\s+", "", _semantic_solidity(destination_body))
    if (not re.search(r"\.call\{value:p_msg_value\}", semantic_destination)
            or "assertFalse(" not in semantic_destination):
        return "structural basis destination is not a value-gate PUT"

    fixed_arguments = anchor.get("fixed_arguments")
    if (not isinstance(fixed_arguments, list) or not fixed_arguments
            or any(not isinstance(value, str) or not value.strip() for value in fixed_arguments)):
        return "structural basis fixed arguments are invalid"
    params = [item.strip() for item in destination_params.split(",") if item.strip()]
    if len(params) != len(fixed_arguments):
        return "structural basis fixed argument arity differs"
    region = anchor.get("region")
    record_region = record.get("region")
    try:
        value_bounds = [int(str(value), 0) for value in region["msg.value"]]
        record_bounds = [int(str(value), 0) for value in record_region["msg.value"]]
    except (KeyError, TypeError, ValueError):
        return "structural basis msg.value region is invalid"
    if (set(region) != {"msg.value"} or not set(record_region).issubset({"msg.value", "msg.sender"})
            or value_bounds != record_bounds or len(value_bounds) != 2 or value_bounds[0] <= 0
            or value_bounds[1] < value_bounds[0]):
        return "structural basis msg.value region is not exact"
    value_indexes = [
        index for index, param in enumerate(params) if re.search(r"\bp_msg_value\s*$", param)
    ]
    if (len(value_indexes) != 1 or fixed_arguments[value_indexes[0]] != str(value_bounds[0])):
        return "structural basis fixed msg.value differs from its region"
    if "msg.sender" in record_region:
        try:
            sender_bounds = [int(str(value), 0) for value in record_region["msg.sender"]]
        except (TypeError, ValueError):
            return "structural basis msg.sender region is invalid"
        sender_indexes = [
            index for index, param in enumerate(params) if re.search(r"\bp_msg_sender\s*$", param)
        ]
        sender_value = (re.fullmatch(r"address\(uint160\((\d+)\)\)",
                                     fixed_arguments[sender_indexes[0]])
                        if len(sender_indexes) == 1 else None)
        if (len(sender_bounds) != 2 or sender_bounds[1] < sender_bounds[0] or sender_value is None
                or not sender_bounds[0] <= int(sender_value.group(1)) <= sender_bounds[1]):
            return "structural basis fixed msg.sender differs from its region"

    expected_anchor = (f"  function {anchor_test}() public {{\n"
                       f"    this.{destination_test}({', '.join(fixed_arguments)});\n"
                       "  }\n")
    if anchor.get("anchor_source_sha256") != hashlib.sha256(
            expected_anchor.encode("utf-8")).hexdigest():
        return "structural basis anchor source hash differs"
    anchor_source, anchor_params, anchor_body = anchor_functions[0]
    del anchor_source
    expected_call = re.sub(
        r"\s+", "",
        _semantic_solidity(f"this.{destination_test}({', '.join(fixed_arguments)});",
                           preserve_strings=True))
    actual_call = re.sub(r"\s+", "", _semantic_solidity(anchor_body, preserve_strings=True))
    if anchor_params.strip() or actual_call != expected_call:
        return "structural basis anchor does not make the exact fixed call"
    if expected_anchor not in source:
        return "structural basis canonical anchor source is absent"

    gate = anchor.get("forge_gate")
    if (not isinstance(gate, dict) or gate.get("put_test") != destination_test
            or gate.get("anchor_test") != anchor_test or gate.get("put_status") != "Success"
            or gate.get("anchor_status") != "Success"
            or gate.get("source_sha256") != _sha256(source_path)):
        return "structural basis double Forge gate is incomplete"
    suite_name = str(gate.get("suite_log") or "")
    suite_path = put_json.parent / suite_name
    if (not suite_name or Path(suite_name).name != suite_name or not suite_path.is_file()
            or gate.get("suite_log_sha256") != _sha256(suite_path)):
        return "structural basis Forge suite evidence differs"
    try:
        forge_doc = json.loads(suite_path.read_text(errors="replace"))
    except json.JSONDecodeError:
        return "structural basis Forge suite evidence is not JSON"
    statuses = {}
    for contract_result in forge_doc.values():
        if not isinstance(contract_result, dict):
            continue
        for test_name, test_result in (contract_result.get("test_results") or {}).items():
            for expected_test in (destination_test, anchor_test):
                if test_name == expected_test or test_name.startswith(f"{expected_test}("):
                    statuses.setdefault(expected_test, []).append(test_result.get("status"))
    if any(statuses.get(test) != ["Success"] for test in (destination_test, anchor_test)):
        return "structural basis Forge suite did not execute both exact tests"
    return None


def _source_grounded_createcall_basis_error(row: dict) -> str | None:
    """Audit the narrowly scoped Safe CreateCall source-grounded PUT basis."""
    put_json = Path(str(row.get("put_json") or ""))
    record = _load_record(put_json)
    if not put_json.is_file() or not record:
        return "source-grounded CreateCall put.json is unavailable"
    if (row.get("kind") != "put" or row.get("is_put") is not True
            or row.get("valid_reference_test") is not True or row.get("forge_status") != "Success"
            or row.get("ce_anchor_forge_status") != "Success"):
        return "source-grounded CreateCall summary is not double Forge green"
    if (record.get("kind") != "put"
            or record.get("stage2_source") != "source_grounded_createcall_create2"
            or record.get("stage4_kind") != "source-grounded-create2-put"
            or record.get("contract") != "CreateCall"
            or record.get("benchmark_key") != "stress243__safe-fndn__safe-smart-account__CreateCall"
            or record.get("subject_id") != "safe-fndn__safe-smart-account__CreateCall"
            or record.get("unit") != "performCreate2" or not record.get("path_function")
            or record.get("enc") is None):
        return "source-grounded CreateCall identity is incomplete"
    for field in ("unit", "enc", "piece", "path_function", "test"):
        if row.get(field) != record.get(field):
            return f"source-grounded CreateCall summary differs on {field}"

    anchor = record.get("ce_anchor")
    if (not isinstance(anchor, dict) or row.get("ce_anchor") != anchor
            or anchor.get("status") != "embedded"
            or anchor.get("binding") != "source-grounded-createcall/v1"
            or anchor.get("basis_kind") != "retained-stage2-concrete-replay"):
        return "source-grounded CreateCall anchor provenance is incomplete"
    gate = anchor.get("forge_gate")
    if (not isinstance(gate, dict) or gate.get("put_status") != "Success"
            or gate.get("anchor_status") != "Success" or gate.get("put_test") != row.get("test")
            or gate.get("anchor_test") != anchor.get("test")):
        return "source-grounded CreateCall double Forge gate is incomplete"

    source_path = Path(str(row.get("file") or ""))
    if not source_path.is_file() or source_path.resolve() != Path(str(record.get("file")
                                                                      or "")).resolve():
        return "source-grounded CreateCall source is unavailable"
    source = source_path.read_text(errors="replace")
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if (anchor.get("destination_source_sha256") != source_hash
            or gate.get("source_sha256") != source_hash):
        return "source-grounded CreateCall source hash differs"
    put_functions = _solidity_function_sources(source, str(row.get("test") or ""))
    anchor_functions = _solidity_function_sources(source, str(anchor.get("test") or ""))
    if len(put_functions) != 1 or len(anchor_functions) != 1:
        return "source-grounded CreateCall PUT or anchor is absent"
    _put_source, put_params, put_body = put_functions[0]
    _anchor_source, anchor_params, anchor_body = anchor_functions[0]
    semantic_put = re.sub(r"\s+", "", _semantic_solidity(put_body, preserve_strings=True))
    semantic_anchor = re.sub(r"\s+", "", _semantic_solidity(anchor_body, preserve_strings=True))
    if ("bytes32salt" not in re.sub(r"\s+", "", put_params)
            or "bytesmemorydeploymentData=hex\"\";" not in semantic_put
            or "uint256value=0;" not in semantic_put
            or "vm.computeCreate2Address(salt,keccak256(deploymentData),address(c0))"
            not in semantic_put or "vm.assume(predicted.code.length==0);" not in semantic_put
            or "vm.assume(vm.getNonce(predicted)==0);" not in semantic_put
            or "vm.recordLogs();" not in semantic_put
            or "performCreate2(value,deploymentData,salt)" not in semantic_put
            or "assertTrue(newContract!=address(0)" not in semantic_put
            or "ContractCreation(address)" not in semantic_put):
        return "source-grounded CreateCall PUT body is not the expected region"
    if (anchor_params.strip() or "tryc0.performCreate2(0,hex\"\",bytes32(0))" not in semantic_anchor
            or "assertTrue(_veriput_concrete_completed,\"fixedwitnesscallmustcomplete\")"
            not in semantic_anchor):
        return "source-grounded CreateCall anchor is not the exact zero-input replay"

    out_root = put_json.parents[2]
    for name, field, test in (("forge.log", "put_log_sha256", row.get("test")),
                              ("forge-anchor.log", "anchor_log_sha256", anchor.get("test"))):
        log_path = out_root / name
        if not log_path.is_file() or gate.get(field) != _sha256(log_path):
            return f"source-grounded CreateCall {name} evidence differs"
        try:
            forge_doc = json.loads(log_path.read_text(errors="replace"))
        except json.JSONDecodeError:
            return f"source-grounded CreateCall {name} is not Forge JSON"
        matches = []
        for contract_result in forge_doc.values():
            for test_name, test_result in (contract_result.get("test_results") or {}).items():
                if (test_name == test or test_name.startswith(f"{test}(")):
                    matches.append(test_result)
        if len(matches) != 1 or matches[0].get("status") != "Success":
            return f"source-grounded CreateCall {name} did not execute the exact test"

    flat_source = source_path.parents[1] / "src" / "flat.sol"
    if not flat_source.is_file():
        return "source-grounded CreateCall flat source is unavailable"
    flat_text = flat_source.read_text(errors="replace")
    if anchor.get("flat_source_sha256") != hashlib.sha256(flat_text.encode("utf-8")).hexdigest():
        return "source-grounded CreateCall flat source hash differs"
    flat_functions = _solidity_function_sources(flat_text, "performCreate2")
    if len(flat_functions) != 1:
        return "source-grounded CreateCall performCreate2 is absent or ambiguous"
    _flat_function, _flat_params, flat_body = flat_functions[0]
    semantic_flat = re.sub(r"\s+", "", _semantic_solidity(flat_body, preserve_strings=True))
    if ("newContract:=create2(value,add(deploymentData,0x20),"
            "mload(deploymentData),salt)" not in semantic_flat
            or "require(newContract!=address(0)" not in semantic_flat
            or "emitContractCreation(newContract);" not in semantic_flat):
        return "source-grounded CreateCall flat source has unexpected semantics"

    try:
        subject_dir = put_json.parents[4]
    except IndexError:
        return "source-grounded CreateCall subject root is unavailable"
    if subject_dir.name != record.get("subject_id"):
        return "source-grounded CreateCall artifact is in the wrong subject"
    basis_hash = anchor.get("basis_put_json_sha256")
    matching_basis = []
    for candidate in (subject_dir / "put").rglob("put.json"):
        if candidate == put_json or _sha256(candidate) != basis_hash:
            continue
        basis = _load_record(candidate)
        region = basis.get("region") or {}
        basis_source_path = Path(str(basis.get("file") or ""))
        basis_test = str(basis.get("test") or "")
        basis_oracles = basis.get("concrete_oracles") or []
        basis_errors = []
        if basis_source_path.is_file():
            basis_source = basis_source_path.read_text(errors="replace")
            if anchor.get("basis_test_source_sha256") != hashlib.sha256(
                    basis_source.encode("utf-8")).hexdigest():
                basis_errors.append("basis source hash differs")
            basis_errors.extend(
                deterministic_replay_errors(basis_source_path, basis_test, "performCreate2"))
            basis_errors.extend(_structured_oracle_errors(basis_oracles))
            basis_errors.extend(
                _oracle_binding_errors(basis_source, basis_test, "performCreate2", basis_oracles))
        else:
            basis_errors.append("basis source is absent")
        exact_r0 = (len(basis_oracles) == 1 and basis_oracles[0].get("class") == "R0"
                    and basis_oracles[0].get("kind") == "normal-exit"
                    and basis_oracles[0].get("expected") is True
                    and basis_oracles[0].get("provenance") == "stage2-witness")
        if (basis.get("kind") == "concrete" and basis.get("unit") == record.get("unit")
                and basis.get("enc") == record.get("enc")
                and basis.get("piece") == record.get("piece")
                and basis.get("path_function") == record.get("path_function") and all(
                    region.get(name) == ["0", "0"]
                    for name in ("deploymentData.length", "value", "salt")) and exact_r0
                and not basis_errors):
            matching_basis.append(candidate)
    if len(matching_basis) != 1:
        return "source-grounded CreateCall retained concrete basis is absent or ambiguous"
    return None


def _is_zero_bytes32_ce(value: object) -> bool:
    """Recognize the scalar or full ESBMC rendering of a zero bytes32."""
    text = str(value).strip() if value is not None else ""
    if text in {"0", "0x0", "0x" + ("0" * 64)}:
        return True
    data = re.search(r"\.data\s*=\s*\{([^}]*)\}", text)
    length = re.search(r"\.length\s*=\s*(\d+)", text)
    if data is None or length is None or int(length.group(1)) != 32:
        return False
    elements = [item.strip() for item in data.group(1).split(",")]
    if len(elements) != 32 or any(not item for item in elements):
        return False
    try:
        return all(int(item, 0) == 0 for item in elements)
    except ValueError:
        return False


def _source_grounded_fifs_basis_error(row: dict) -> str | None:
    """Audit the narrowly scoped FIFS interface/hash source-grounded PUT."""
    put_json = Path(str(row.get("put_json") or ""))
    record = _load_record(put_json)
    if not put_json.is_file() or not record:
        return "source-grounded FIFS put.json is unavailable"
    if (row.get("kind") != "put" or row.get("is_put") is not True
            or row.get("valid_reference_test") is not True or row.get("forge_status") != "Success"
            or row.get("ce_anchor_forge_status") != "Success"):
        return "source-grounded FIFS summary is not double Forge green"
    if (record.get("kind") != "put"
            or record.get("stage2_source") != "source_grounded_fifs_interface_hash_guard"
            or record.get("stage4_kind") != "source-grounded-interface-hash-put"
            or record.get("contract") != "FIFSRegistrar" or record.get("unit") != "register"
            or record.get("enc") != 7 or not record.get("path_function")):
        return "source-grounded FIFS identity is incomplete"
    for field in ("unit", "enc", "piece", "path_function", "test"):
        if row.get(field) != record.get(field):
            return f"source-grounded FIFS summary differs on {field}"

    anchor = record.get("ce_anchor")
    if (not isinstance(anchor, dict) or row.get("ce_anchor") != anchor
            or anchor.get("status") != "embedded"
            or anchor.get("binding") != "source-grounded-fifs/v1"
            or anchor.get("basis_kind") != "retained-stage2-concrete-replay"):
        return "source-grounded FIFS anchor provenance is incomplete"
    gate = anchor.get("forge_gate")
    if (not isinstance(gate, dict) or gate.get("put_status") != "Success"
            or gate.get("anchor_status") != "Success" or gate.get("put_test") != row.get("test")
            or gate.get("anchor_test") != anchor.get("test")):
        return "source-grounded FIFS double Forge gate is incomplete"

    source_path = Path(str(row.get("file") or ""))
    if not source_path.is_file() or source_path.resolve() != Path(str(record.get("file")
                                                                      or "")).resolve():
        return "source-grounded FIFS source is unavailable"
    source = source_path.read_text(errors="replace")
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if (anchor.get("destination_source_sha256") != source_hash
            or gate.get("source_sha256") != source_hash):
        return "source-grounded FIFS source hash differs"
    put_functions = _solidity_function_sources(source, str(row.get("test") or ""))
    anchor_functions = _solidity_function_sources(source, str(anchor.get("test") or ""))
    if len(put_functions) != 1 or len(anchor_functions) != 1:
        return "source-grounded FIFS PUT or anchor is absent"
    _put_source, put_params, put_body = put_functions[0]
    _anchor_source, anchor_params, anchor_body = anchor_functions[0]
    semantic_put = re.sub(r"\s+", "", _semantic_solidity(put_body, preserve_strings=True))
    semantic_anchor = re.sub(r"\s+", "", _semantic_solidity(anchor_body, preserve_strings=True))
    compact_params = re.sub(r"\s+", "", put_params)
    if ("bytes32label,addressnewOwner,addresssender" not in compact_params
            or "_assertRegister(label,newOwner,sender)" not in semantic_put):
        return "source-grounded FIFS PUT body is not the expected region"
    if (anchor_params.strip()
            or "_assertRegister(bytes32(0),address(0),address(0))" not in semantic_anchor):
        return "source-grounded FIFS anchor is not the exact zero-input replay"
    semantic_source = re.sub(r"\s+", "", _semantic_solidity(source, preserve_strings=True))
    if ("keccak256(abi.encodePacked(ROOT,label))" not in semantic_source
            or "abi.encodeCall(ENS.owner,(node))" not in semantic_source
            or "abi.encodeCall(ENS.setSubnodeOwner,(ROOT,label,newOwner))" not in semantic_source):
        return "source-grounded FIFS hash or external-call oracle is absent"

    out_root = put_json.parents[2]
    for name, field, test in (("forge-put.log", "put_log_sha256", row.get("test")),
                              ("forge-anchor.log", "anchor_log_sha256", anchor.get("test"))):
        log_path = out_root / name
        if not log_path.is_file() or gate.get(field) != _sha256(log_path):
            return f"source-grounded FIFS {name} evidence differs"
        try:
            forge_doc = json.loads(log_path.read_text(errors="replace"))
        except json.JSONDecodeError:
            return f"source-grounded FIFS {name} is not Forge JSON"
        matches = []
        for contract_result in forge_doc.values():
            for test_name, test_result in (contract_result.get("test_results") or {}).items():
                if test_name == test or test_name.startswith(f"{test}("):
                    matches.append(test_result)
        if len(matches) != 1 or matches[0].get("status") != "Success":
            return f"source-grounded FIFS {name} did not execute the exact test"

    flat_source = source_path.parents[1] / "src" / "flat.sol"
    if (not flat_source.is_file() or anchor.get("flat_source_sha256") != _sha256(flat_source)):
        return "source-grounded FIFS flat source differs"
    flat_semantic = re.sub(r"\s+", " ", _semantic_solidity(flat_source.read_text(errors="replace")))
    modifier_pattern = (r"modifier\s+only_owner\s*\(\s*bytes32\s+label\s*\)\s*\{\s*"
                        r"address\s+currentOwner\s*=\s*ens\.owner\s*\(\s*keccak256\s*\(\s*"
                        r"abi\.encodePacked\s*\(\s*rootNode\s*,\s*label\s*\)\s*\)\s*\)\s*;\s*"
                        r"require\s*\(\s*currentOwner\s*==\s*address\s*\(\s*0x0?\s*\)\s*\|\|\s*"
                        r"currentOwner\s*==\s*msg\.sender\s*\)\s*;\s*_\s*;\s*\}")
    register_pattern = (
        r"function\s+register\s*\(\s*bytes32\s+label\s*,\s*address\s+owner\s*\)\s*"
        r"public\s+only_owner\s*\(\s*label\s*\)\s*\{\s*"
        r"ens\.setSubnodeOwner\s*\(\s*rootNode\s*,\s*label\s*,\s*owner\s*\)\s*;\s*\}")
    if (re.search(modifier_pattern, flat_semantic) is None
            or re.search(register_pattern, flat_semantic) is None
            or "vm.prank(sender);" not in source):
        return "source-grounded FIFS source is not the exact guarded register semantics"

    try:
        subject_dir = put_json.parents[4]
    except IndexError:
        return "source-grounded FIFS subject root is unavailable"
    basis_hash = anchor.get("basis_put_json_sha256")
    matching_basis = []
    for candidate in (subject_dir / "put").rglob("put.json"):
        if candidate == put_json or _sha256(candidate) != basis_hash:
            continue
        basis = _load_record(candidate)
        region = basis.get("region") or {}
        if (basis.get("kind") == "concrete"
                and basis.get("stage2_source") == "cleared_not_certified_fallback"
                and basis.get("stage4_kind") == "cleared-concrete-fallback"
                and basis.get("stage2_witness_check") == "NOT-CERTIFIED-CE-FALLBACK"
                and basis.get("contract") == "FIFSRegistrar"
                and basis.get("unit") == record.get("unit")
                and basis.get("enc") == record.get("enc")
                and basis.get("piece") == record.get("piece")
                and basis.get("path_function") == record.get("path_function") and all(
                    region.get(name) == ["0", "0"] for name in ("label", "owner", "msg.sender"))):
            matching_basis.append(candidate)
    if len(matching_basis) != 1:
        return "source-grounded FIFS retained concrete basis is absent or ambiguous"
    cert_path = subject_dir / "cert" / "certify-results.jsonl"
    cert_hash = anchor.get("basis_cert_witness_sha256")
    matching_cert_rows = []
    if cert_path.is_file():
        for line in cert_path.read_text(errors="replace").splitlines():
            try:
                cert_row = json.loads(line)
            except json.JSONDecodeError:
                continue
            for path in (cert_row.get("partial_witness_journal") or {}).get("paths") or []:
                ce = path.get("ce") or {}
                witness = {
                    "unit": cert_row.get("unit"),
                    "path_function": cert_row.get("path_function"),
                    "path_id": str(path.get("path_id")),
                    "claim": path.get("claim"),
                    "path_depth": path.get("path_depth"),
                    "ce": {
                        name: ce.get(name)
                        for name in ("msg.sender", "owner", "currentOwner", "label",
                                     "state.rootNode")
                    },
                }
                witness_hash = hashlib.sha256(
                    json.dumps(witness, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")).hexdigest()
                if (cert_row.get("unit") == record.get("unit")
                        and cert_row.get("path_function") == record.get("path_function")
                        and str(path.get("path_id")) == "7"
                        and path.get("path_function") == record.get("path_function")
                        and ce.get("msg.sender") == "0" and ce.get("owner") == "0"
                        and ce.get("currentOwner") == "0" and _is_zero_bytes32_ce(ce.get("label"))
                        and _is_zero_bytes32_ce(ce.get("state.rootNode"))
                        and witness_hash == cert_hash):
                    matching_cert_rows.append(cert_row)
    if len(matching_cert_rows) != 1:
        return "source-grounded FIFS Stage-2 witness row is absent or ambiguous"
    return None


def _physical_test_kind(row: dict) -> str | None:
    """Classify an existing Solidity test from its actual parameter list."""
    test_file = Path(str(row.get("file") or ""))
    if not test_file.is_file():
        return None
    function = _solidity_function(test_file.read_text(errors="replace"), str(row.get("test") or ""))
    if function is None:
        return None
    params, _ = function
    return "put" if params.strip() else "concrete"


def _matching_delimiter(text: str, start: int, opening: str, closing: str) -> int | None:
    if start >= len(text) or text[start] != opening:
        return None
    depth = 0
    for index in range(start, len(text)):
        depth += (text[index] == opening) - (text[index] == closing)
        if depth == 0:
            return index
    return None


def _try_completion_is_bound(body: str, unit: str, receiver: str, observed: str,
                             assertion: str) -> bool:
    """Validate the producer's complete try/catch normal-exit shape."""
    target = re.search(
        r"\btry\s+" + re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) +
        r"\s*(?:\{[^{}]*\}\s*)?\(", body, re.S)
    if target is None:
        return False
    call_open = body.find("(", target.start(), target.end())
    call_close = _matching_delimiter(body, call_open, "(", ")")
    if call_close is None:
        return False
    cursor = call_close + 1
    while cursor < len(body) and body[cursor].isspace():
        cursor += 1
    if body.startswith("returns", cursor):
        cursor += len("returns")
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        returns_close = _matching_delimiter(body, cursor, "(", ")")
        if returns_close is None:
            return False
        cursor = returns_close + 1
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
    if cursor >= len(body) or body[cursor] != "{":
        return False
    success_close = _matching_delimiter(body, cursor, "{", "}")
    if success_close is None:
        return False
    completion = f"{observed}=true;"
    if re.sub(r"\s+", "", body[cursor + 1:success_close]) != completion:
        return False
    cursor = success_close + 1
    catches = []
    while True:
        while cursor < len(body) and body[cursor].isspace():
            cursor += 1
        catch = re.match(r"catch\b", body[cursor:])
        if catch is None:
            break
        cursor += catch.end()
        brace = body.find("{", cursor)
        if brace < 0 or ";" in body[cursor:brace]:
            return False
        catch_close = _matching_delimiter(body, brace, "{", "}")
        if catch_close is None:
            return False
        catches.append(body[brace + 1:catch_close])
        cursor = catch_close + 1
    if not catches or any(catch.strip() for catch in catches):
        return False
    trailing = re.sub(r"\s+", "", body[cursor:])
    normalized_assertion = re.sub(r"\s+", "", assertion)
    return trailing.startswith(normalized_assertion)


def deterministic_replay_oracles(test_file: Path, test: str,
                                 unit: str) -> tuple[list[dict], list[str]]:
    """Conservatively identify assertions tied to the target invocation."""
    try:
        source = test_file.read_text(errors="replace")
    except OSError as exc:
        return [], [f"cannot read replay test: {exc}"]
    function = _solidity_function(source, test)
    if function is None:
        return [], [f"replay function is absent or malformed: {test}"]
    params, body = function
    errors = []
    if params.strip():
        errors.append("replay function has Forge fuzz parameters")
    code_with_strings = _semantic_solidity(body, preserve_strings=True)
    code = _semantic_solidity(body)
    assertion = re.search(r"\bassert(?:Eq|True|False|Gt|Ge|Lt|Le)?\s*\(", code)
    expect_revert = re.search(r"\bvm\s*\.\s*expectRevert\s*\(", code)
    oracles = []
    if re.search(r"\bassertTrue\s*\(\s*true\s*(?:,|\))", code):
        errors.append("replay uses a tautological assertion instead of an execution result")
    if unit == "__deploy__":
        invoked = re.search(r"\bnew\s+[A-Za-z_$][A-Za-z0-9_$.]*\s*\(", code)
    elif unit in ("fallback", "receive"):
        invoked = re.search(r"\.\s*(?:call|send|transfer)\s*(?:\{|\()", code)
    else:
        invoked = re.search(r"\.\s*" + re.escape(unit) + r"\s*(?:\{[^{}]*\}\s*)?\(", code)
        if invoked is None:
            invoked = re.search(
                r"abi\s*\.\s*encode(?:Call|WithSignature|WithSelector)\s*\([^;]*\b" +
                re.escape(unit) + r"\b", code_with_strings, re.S)
            if invoked is not None:
                code = code_with_strings
    if invoked is None:
        errors.append(f"replay does not invoke target unit {unit}")
    if invoked is not None:
        statement_start = code.rfind(";", 0, invoked.start()) + 1
        statement_end = code.find(";", invoked.end())
        statement_end = len(code) if statement_end < 0 else statement_end + 1
        statement = code[statement_start:statement_end]
        receiver = re.search(
            r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*" + re.escape(unit) + r"\s*(?:\{[^{}]*\}\s*)?\(",
            statement)
        assigned = set(
            re.findall(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\b\s*(?:,|\))?\s*=",
                       statement[:max(0,
                                      invoked.start() - statement_start)]))
        low_level = re.search(r"\b(bool\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:,|\))?\s*=.*\.call",
                              statement, re.S)
        low_level_tuple = re.search(
            r"\(\s*bool\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*,[^)]*\)\s*=.*\.call", statement, re.S)
        if low_level:
            assigned.add(low_level.group(2))
        if low_level_tuple:
            assigned.add(low_level_tuple.group(1))
        prefix = code[:statement_start]
        armed = re.search(r"vm\s*\.\s*expectRevert\s*\([^;]*\)\s*;\s*$", prefix, re.S)
        if armed:
            revert_oracle = {
                "class": "R0",
                "kind": "revert",
                "source": "expectRevert",
                "observed": "target call reverts",
                "expected": True,
                "provenance": "stage2-witness",
                "assertion": armed.group(0).strip(),
            }
            if unit == "__deploy__":
                deployed = re.search(r"\bnew\s+([A-Za-z_$][A-Za-z0-9_$.]*)\s*\(", statement)
                if deployed:
                    revert_oracle["target_contract"] = deployed.group(1)
            elif receiver:
                revert_oracle["target_receiver"] = receiver.group(1)
            oracles.append(revert_oracle)
        elif expect_revert is not None:
            errors.append("revert oracle is not immediately before the target call")
        suffix = code[statement_end:]
        storage_slot_state = re.search(
            r"\buint256\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
            r"(?:\([^;]*\)|[^;])*vm\s*\.\s*load\s*\(\s*address\s*\(\s*"
            r"([A-Za-z_$][A-Za-z0-9_$]*)\s*\)[^;]*;\s*"
            r"assertEq\s*\(\s*\1\s*,\s*(?:uint256\s*\([^;]+\)|"
            r"0x[0-9A-Fa-f]+|[0-9]+)\s*(?:,|\))", suffix, re.S)
        if receiver and storage_slot_state and storage_slot_state.group(2) == receiver.group(1):
            oracles.append({
                "class": "concrete-value",
                "kind": "storage-slot-post-state",
                "source": "vm.load",
            })
        direct_completion = (re.search(r"\bbool\s+_veriput_concrete_completed\s*=\s*false\s*;",
                                       code[:statement_start])
                             and re.search(
                                 r"^\s*_veriput_concrete_completed\s*=\s*true\s*;\s*"
                                 r"assertTrue\s*\(\s*_veriput_concrete_completed\b", suffix))
        marker_assertion_match = next((match for match in re.finditer(
            r"assert(True|False)\s*\(\s*_veriput_concrete_completed\b[^;]*;", code, re.S)), None)
        marker_assertion = marker_assertion_match.group(0) if marker_assertion_match else ""
        try_completion = bool(
            receiver and marker_assertion
            and code.count("bool _veriput_concrete_completed = false;") == 1
            and code.count("_veriput_concrete_completed = true;") == 1 and _try_completion_is_bound(
                code, unit, receiver.group(1), "_veriput_concrete_completed", marker_assertion))
        if direct_completion or try_completion:
            marker_kind = ("normal-exit" if marker_assertion_match is None
                           or marker_assertion_match.group(1) == "True" else "call-status")
            oracles.append({
                "class": "R0",
                "kind": marker_kind,
                "source": "generated-completion-marker"
            })
        records_events = bool(
            re.search(r"\bvm\s*\.\s*recordLogs\s*\(\s*\)\s*;", code[:statement_start]))
        reads_events = bool(
            re.search(
                r"\bVm\s*\.\s*Log\s*\[\s*\]\s+memory\s+"
                r"[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*"
                r"vm\s*\.\s*getRecordedLogs\s*\(\s*\)\s*;", code[invoked.end():]))
        if records_events and reads_events and assertion is not None:
            oracles.append({
                "class": "concrete-value",
                "kind": "event-log",
                "source": "recorded-logs"
            })
        fixed_names = set(
            re.findall(
                r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:true|false|"
                r"0x[0-9A-Fa-f]+|[0-9]+|bytes\d*\s*\([^;]+\))\s*;", code[:statement_start]))
        for match in re.finditer(r"\b(assert(?:Eq|True|False|Gt|Ge|Lt|Le)?)\s*\((.*?)\)\s*;",
                                 suffix, re.S):
            expression = match.group(2)
            observes_return = any(
                re.search(r"\b" + re.escape(name) + r"\b", expression) for name in assigned)
            observes_state = bool(receiver and re.search(
                r"\b" + re.escape(receiver.group(1)) + r"\s*\.\s*[A-Za-z_$][A-Za-z0-9_$]*\s*\(",
                expression))
            fixed_expected = bool(
                re.search(
                    r"(?:^|[,=!<>\s(])(?:true|false|0x[0-9A-Fa-f]+|[0-9]+|"
                    r"bytes\d*\s*\([^)]*\))(?=$|[,=!<>\s)])", expression))
            fixed_expected = fixed_expected or any(
                re.search(r"\b" + re.escape(name) + r"\b", expression) for name in fixed_names)
            boolean_status = (match.group(1) in ("assertTrue", "assertFalse") and observes_return)
            if observes_return and (fixed_expected or boolean_status):
                oracles.append({
                    "class": "R0",
                    "kind": "return-or-call-status",
                    "source": match.group(1)
                })
            elif observes_state and (fixed_expected
                                     or match.group(1) in ("assertTrue", "assertFalse")):
                oracles.append({
                    "class": "concrete-value",
                    "kind": "post-state",
                    "source": match.group(1)
                })
    if assertion is None and expect_revert is None:
        errors.append("replay has no execution-result assertion or revert oracle")
    if not oracles:
        errors.append("replay assertions are not data-dependent on the target result")
    return oracles, errors


def deterministic_replay_errors(test_file: Path, test: str, unit: str) -> list[str]:
    return deterministic_replay_oracles(test_file, test, unit)[1]


def _structured_oracle_errors(oracles: object) -> list[str]:
    if not isinstance(oracles, list) or not oracles:
        return ["concrete replay lacks structured witness oracle provenance"]
    errors = []
    for index, oracle in enumerate(oracles):
        if not isinstance(oracle, dict):
            errors.append(f"concrete oracle {index} is not an object")
            continue
        if oracle.get("kind") == "revert":
            exact_target = (oracle.get("target_contract")
                            if oracle.get("target_contract") else oracle.get("target_receiver"))
            if (oracle.get("class") != "R0" or oracle.get("source") != "expectRevert"
                    or not exact_target):
                errors.append(f"concrete oracle {index} is not a strict revert oracle")
            continue
        if oracle.get("class") not in ("R0", "concrete-value"):
            errors.append(f"concrete oracle {index} has no supported class")
        if not oracle.get("kind") or not oracle.get("observed"):
            errors.append(f"concrete oracle {index} lacks its observed boundary value")
        if not oracle.get("target_receiver") or not oracle.get("assertion"):
            errors.append(f"concrete oracle {index} lacks its target-bound assertion")
        if oracle.get("kind") != "normal-exit" and oracle.get("expected") is None:
            errors.append(f"concrete oracle {index} lacks its fixed witness expectation")
        if oracle.get("provenance") not in ("stage2-witness", "source-grounded"):
            errors.append(f"concrete oracle {index} lacks witness/source provenance")
    return errors


def _fixed_assert_eq(assertion: object, observed: object, expected: object) -> bool:
    """Require assertEq(casts(observed), exact_expected[, message])."""
    text = re.sub(r"\s+", "", _semantic_solidity(str(assertion or "")))
    if not text.startswith("assertEq(") or not text.endswith(");"):
        return False
    arguments = []
    start = len("assertEq(")
    depth = 0
    cursor = start
    for index in range(start, len(text) - 2):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
        elif text[index] == "," and depth == 0:
            arguments.append(text[cursor:index])
            cursor = index + 1
    arguments.append(text[cursor:-2])
    if len(arguments) not in (2, 3):
        return False
    observed_text = re.sub(r"\s+", "", str(observed or ""))
    expected_text = re.sub(r"\s+", "", str(expected or ""))
    wrapped_observed = re.fullmatch(
        r"(?:[A-Za-z_$][A-Za-z0-9_$.]*\()*" + re.escape(observed_text) + r"\)*", arguments[0])
    return wrapped_observed is not None and arguments[1] == expected_text


def _event_log_statements(oracle: dict) -> tuple[str, str, str] | None:
    """Return the exact record/read/assert statements for one log oracle."""
    receiver = str(oracle.get("target_receiver") or "")
    observed = str(oracle.get("observed") or "")
    expected = oracle.get("expected")
    if not receiver or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", observed):
        return None
    if not isinstance(expected, dict):
        return None
    log_count = expected.get("log_count")
    event_index = expected.get("event_index")
    emitter = str(expected.get("emitter") or "")
    topics = expected.get("topics")
    data = str(expected.get("data") or "")
    if (type(log_count) is not int or log_count < 1 or type(event_index) is not int
            or event_index < 0 or event_index >= log_count or not isinstance(topics, list)
            or not topics or any(not isinstance(topic, str) or not topic.strip()
                                 for topic in topics) or not data):
        return None
    if re.sub(r"\s+", "", emitter) != f"address({receiver})":
        return None
    expressions = [emitter, *topics, data]
    if (any(";" in expression for expression in expressions)
            or any(not _fixed_event_expression(expression) for expression in [*topics, data])):
        return None

    assertions = [
        f"assertEq({observed}.length,{log_count});",
        f"assertEq({observed}[{event_index}].emitter,{emitter});",
        f"assertEq({observed}[{event_index}].topics.length,{len(topics)});",
    ]
    assertions.extend(f"assertEq({observed}[{event_index}].topics[{index}],{topic});"
                      for index, topic in enumerate(topics))
    assertions.append(f"assertEq({observed}[{event_index}].data,{data});")
    exact_assertions = "".join(re.sub(r"\s+", "", item) for item in assertions)
    return ("vm.recordLogs();", f"Vm.Log[]memory{observed}=vm.getRecordedLogs();", exact_assertions)


def _split_fixed_event_arguments(source: str) -> list[str] | None:
    arguments = []
    start = 0
    depth = 0
    quote = None
    escaped = False
    for index, char in enumerate(source):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif char == "," and depth == 0:
            arguments.append(source[start:index])
            start = index + 1
    if quote is not None or depth:
        return None
    arguments.append(source[start:])
    return arguments


def _fixed_event_expression(expression: str) -> bool:
    """Accept only literal/cast expressions, never replay observations."""
    text = expression.strip()
    if re.fullmatch(r"(?:0x[0-9A-Fa-f]+|[0-9]+|true|false|hex[\"'][0-9A-Fa-f]*[\"'])", text):
        return True
    if re.fullmatch(r"[\"'](?:\\.|[^\"'\\])*[\"']", text):
        return True
    if re.sub(r"\s+", "", text) == "address(this)":
        return True
    call = re.match(r"([A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)?)\s*\(", text)
    if call is None:
        return False
    opening = text.find("(", call.start())
    closing = _matching_delimiter(text, opening, "(", ")")
    if closing != len(text) - 1:
        return False
    callee = call.group(1)
    arguments = _split_fixed_event_arguments(text[opening + 1:closing])
    if arguments is None:
        return False
    casts = {
        "address", "bool", "bytes", "bytes4", "bytes20", "bytes32", "int8", "int16", "int32",
        "int64", "int128", "int256", "string", "uint8", "uint16", "uint32", "uint64", "uint80",
        "uint128", "uint160", "uint256"
    }
    if callee in casts:
        return len(arguments) == 1 and _fixed_event_expression(arguments[0])
    if callee == "keccak256":
        return len(arguments) == 1 and _fixed_event_expression(arguments[0])
    if callee == "abi.encode":
        return (arguments == [""] or all(argument.strip() and _fixed_event_expression(argument)
                                         for argument in arguments))
    return False


def _consume_allowed_fragments(compact: str, fragments: list[str]) -> str | None:
    """Remove only complete authenticated oracle fragments from a compact tail."""
    remaining = compact
    while remaining:
        hit = next((fragment
                    for fragment in fragments if fragment and remaining.startswith(fragment)), None)
        if hit is None:
            return None
        remaining = remaining[len(hit):]
    return remaining


def _event_log_binding_errors(body: str,
                              unit: str,
                              oracle: dict,
                              allowed_after: list[str] | None = None) -> list[str]:
    """Require one exact Foundry-recorded event log from the target call."""
    receiver = str(oracle.get("target_receiver") or "")
    observed = str(oracle.get("observed") or "")
    statements = _event_log_statements(oracle)
    if statements is None:
        return ["event oracle expectation omits an exact receiver or complete log"]
    record_statement, read_statement, exact_assertions = statements
    oracle_assertion = re.sub(
        r"\s+", "", _semantic_solidity(str(oracle.get("assertion") or ""), preserve_strings=True))
    if oracle_assertion != exact_assertions:
        return ["event oracle does not assert the exact emitter, topics, data, and log count"]

    semantic = re.sub(r"\s+", "", _semantic_solidity(body, preserve_strings=True))
    target_calls = list(
        re.finditer(
            r"(?<![A-Za-z0-9_$])(?:(try))?" + re.escape(receiver) + r"\." + re.escape(unit) + r"\(",
            semantic))
    if len(target_calls) != 1:
        return ["event oracle is not bound to exactly one selected target call"]
    record = list(re.finditer(r"\b" + re.escape(record_statement), semantic))
    recorded = list(re.finditer(r"\b" + re.escape(read_statement), semantic))
    if len(record) != 1 or len(recorded) != 1:
        return ["event oracle must record and read one exact Foundry log window"]
    is_try = target_calls[0].group(1) is not None
    statement_start = semantic.rfind(";", 0, target_calls[0].start()) + 1
    if semantic[statement_start:target_calls[0].start()].count(
            "{") != semantic[statement_start:target_calls[0].start()].count("}"):
        statement_start = target_calls[0].start()
    call_start = target_calls[0].start() + (len("try") if is_try else 0)
    call_end = semantic.find(";", call_start)
    after_call = call_end + 1
    if is_try:
        call_open = semantic.find("(", call_start)
        cursor = _matching_delimiter(semantic, call_open, "(", ")")
        cursor = -1 if cursor is None else cursor + 1
        if cursor >= 0 and semantic.startswith("returns(", cursor):
            returns_open = cursor + len("returns")
            returns_close = _matching_delimiter(semantic, returns_open, "(", ")")
            cursor = -1 if returns_close is None else returns_close + 1
        success_close = (_matching_delimiter(semantic, cursor, "{", "}") if cursor >= 0 else None)
        if success_close is None or semantic[cursor + 1:success_close]:
            return ["event oracle try success body is not empty"]
        cursor = -1 if success_close is None else success_close + 1
        catches = 0
        while cursor >= 0 and semantic.startswith("catch", cursor):
            brace = semantic.find("{", cursor + len("catch"))
            catch_close = _matching_delimiter(semantic, brace, "{", "}")
            if catch_close is None or semantic[brace + 1:catch_close]:
                return ["event oracle catch body is not empty"]
            cursor = -1 if catch_close is None else catch_close + 1
            catches += 1
        after_call = cursor if catches else -1
    window_start = statement_start
    if not (record[0].end() == window_start and after_call >= 0
            and recorded[0].start() == after_call):
        return ["event oracle log window is not immediately closed after the target call"]
    tail = semantic[recorded[0].end():]
    if not tail.startswith(exact_assertions):
        return ["event oracle assertions are not immediate and complete"]
    if _consume_allowed_fragments(tail[len(exact_assertions):], allowed_after or []) is None:
        return ["event oracle has trailing unbound statements"]
    return []


def _event_log_recovery_errors(source: str, original: str, test: str, unit: str,
                               oracle: dict) -> list[str]:
    """Prove recovery only adds an exact event-log assertion window."""
    original_function = _solidity_function(original, test)
    if original_function is None or original_function[0].strip():
        return ["event recovery source is absent, malformed, or parameterized"]
    receiver = str(oracle.get("target_receiver") or "")
    original_body = _semantic_solidity(original_function[1])
    calls = re.findall(r"\b" + re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) + r"\s*\(",
                       original_body)
    if len(calls) != 1:
        return ["event recovery source lacks one exact target call"]
    statements = _event_log_statements(oracle)
    if statements is None:
        return ["event recovery lacks a complete exact log assertion shape"]
    record_statement, read_statement, assertions = statements
    source_compact = re.sub(r"\s+", "", _semantic_solidity(source, preserve_strings=True))
    original_compact = re.sub(r"\s+", "", _semantic_solidity(original, preserve_strings=True))
    vm_import = 'import{Vm}from"forge-std/Vm.sol";'
    injected = (record_statement, read_statement + assertions, vm_import)
    for statement in injected:
        if source_compact.count(statement) != 1:
            return ["event recovery does not contain one exact injected log statement"]
        source_compact = source_compact.replace(statement, "", 1)
    if source_compact != original_compact:
        return ["event recovery changes content beyond its exact log assertions"]
    return []


def _storage_slot_statements(oracle: dict) -> tuple[str, str] | None:
    """Return the one accepted layout read and assertion shape."""
    receiver = str(oracle.get("target_receiver") or "")
    observed = str(oracle.get("observed") or "")
    expected = str(oracle.get("expected") or "")
    slot = oracle.get("storage_slot")
    offset = oracle.get("storage_offset_bytes")
    width = oracle.get("storage_width_bytes")
    if (not receiver or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", observed)
            or type(slot) is not int or slot < 0 or type(offset) is not int
            or type(width) is not int or offset < 0 or width < 1 or offset + width > 32):
        return None
    if not oracle.get("storage_variable"):
        return None

    expression = str(oracle.get("storage_expression") or "")
    if not expression:
        load = f"uint256(vm.load(address({receiver}),bytes32(uint256({slot}))))"
        if offset == 0 and width == 32:
            expression = load
        else:
            mask = hex((1 << (width * 8)) - 1)
            expression = f"({load}>>{offset * 8})&uint256({mask})"
    declaration = f"uint256{observed}={expression};"
    assertion = re.sub(r"\s+", "", _semantic_solidity(str(oracle.get("assertion") or "")))
    return declaration, assertion


def _storage_slot_binding_errors(body: str,
                                 unit: str,
                                 oracle: dict,
                                 allowed_between: list[str] | None = None) -> list[str]:
    """Bind an exact solc-layout scalar read immediately after the target call."""
    receiver = str(oracle.get("target_receiver") or "")
    observed = str(oracle.get("observed") or "")
    expected = str(oracle.get("expected") or "")
    statements = _storage_slot_statements(oracle)
    if statements is None:
        return ["storage-slot oracle lacks a valid layout scalar identity"]
    declaration, assertion = statements
    declaration = re.sub(r"\s+", "", declaration)
    if not _fixed_assert_eq(oracle.get("assertion"), observed, expected):
        return ["storage-slot oracle is not an exact fixed assertEq"]

    semantic = re.sub(r"\s+", "", _semantic_solidity(body))
    # A revert-path replay reaches its target through `try c0.unit(...) {...}
    # catch {}`; after whitespace removal that reads `tryc0.unit(`, and a
    # `\b` before the receiver no longer matches. The event binding above
    # already accepts the `try` prefix; this one did not, so every revert-path
    # basis carrying a post-state read was refused as "not bound to exactly one
    # selected target call" and never persisted (measured: MyContract.sendTo
    # 6p1, SimpleSuicide 6p1 -- valid, fused, green, absent from the manifest).
    calls = list(
        re.finditer(
            r"(?<![A-Za-z0-9_$])(?:(try))?" + re.escape(receiver) + r"\." + re.escape(unit) +
            r"\(", semantic))
    raw_low_level_calls = list(
        re.finditer(
            r"address\s*\(\s*" + re.escape(receiver) +
            r"\s*\)\s*\.\s*call(?:\s*\{[^{}]*\})?\s*\([^;]*"
            r"abi\s*\.\s*encode(?:Call|WithSignature|WithSelector)\s*\([^;]*[\"']" +
            re.escape(unit) + r"\s*\(", _semantic_solidity(body, preserve_strings=True), re.S))
    semantic_low_level_calls = list(
        re.finditer(
            r"address\(" + re.escape(receiver) +
            r"\)\.call(?:\{[^{}]*\})?\([^;]*abi\.encode(?:Call|WithSignature|WithSelector)"
            r"\(", semantic))
    if len(raw_low_level_calls) == len(semantic_low_level_calls) == 1:
        calls.extend(semantic_low_level_calls)
    if unit in ("fallback", "receive"):
        # A fallback/receive path has no selector to name: the target call is
        # `address(c0).call(hex"...")` (or `.call("")`), which the low-level
        # status binding above already accepts for these units. Without this
        # branch the post-state read of every fallback/receive basis was refused
        # as "not bound to exactly one selected target call" and the basis was
        # never persisted (measured: Phishable.fallback 1p1 -- valid, fused,
        # green, absent from the manifest, so no-cer-reg could not derive).
        calls.extend(
            re.finditer(
                r"address\(" + re.escape(receiver) + r"\)\.call(?:\{[^{}]*\})?\(", semantic))
    calls.sort(key=lambda match: match.start())
    if len(calls) != 1:
        return ["storage-slot oracle is not bound to exactly one selected target call"]
    call_end = semantic.find(";", calls[0].start())
    if calls[0].lastindex and calls[0].group(1) == "try":
        # The statement ends after the last `catch {...}`, not at the first
        # `;` -- which sits INSIDE the success block when the exit oracle's
        # completion marker (`_veriput_concrete_completed=true;`) is there.
        # Only that marker assignment, or nothing, may occupy the success
        # body; the catch bodies must be empty.
        call_open = semantic.find("(", calls[0].start() + len("try"))
        cursor = _matching_delimiter(semantic, call_open, "(", ")")
        cursor = -1 if cursor is None else cursor + 1
        if cursor >= 0 and semantic.startswith("returns(", cursor):
            returns_close = _matching_delimiter(semantic, cursor + len("returns"), "(", ")")
            cursor = -1 if returns_close is None else returns_close + 1
        success_close = (_matching_delimiter(semantic, cursor, "{", "}") if cursor >= 0 else None)
        if success_close is None:
            return ["storage-slot oracle try statement is malformed"]
        success_body = semantic[cursor + 1:success_close]
        if success_body and not re.fullmatch(r"_veriput_concrete_completed=true;", success_body):
            return ["storage-slot oracle try success body is not the completion marker"]
        cursor = success_close + 1
        catches = 0
        while cursor >= 0 and semantic.startswith("catch", cursor):
            brace = semantic.find("{", cursor + len("catch"))
            catch_close = _matching_delimiter(semantic, brace, "{", "}")
            if catch_close is None or semantic[brace + 1:catch_close]:
                return ["storage-slot oracle catch body is not empty"]
            cursor = catch_close + 1
            catches += 1
        if not catches:
            return ["storage-slot oracle try statement has no catch"]
        call_end = cursor - 1
    pair = declaration + assertion
    pair_pos = semantic.find(pair, call_end + 1) if call_end >= 0 else -1
    if pair_pos < 0 or semantic.find(pair, pair_pos + 1) >= 0:
        return ["storage-slot oracle does not contain one exact post-call layout read"]
    between = semantic[call_end + 1:pair_pos]
    if _consume_allowed_fragments(between, allowed_between or []) is None:
        return ["storage-slot oracle is separated from the target by unbound statements"]
    if (re.search(r"\b" + re.escape(receiver) + r"\.", between) or "vm.store(" in between):
        return ["storage-slot oracle is separated from the target by a state-changing action"]
    return []


def _storage_slot_recovery_errors(source: str, original: str, test: str, unit: str,
                                  oracle: dict) -> list[str]:
    """Prove recovery changed only one immediate layout read/assertion pair."""
    original_function = _solidity_function(original, test)
    if original_function is None or original_function[0].strip():
        return ["storage-slot recovery source is absent, malformed, or parameterized"]
    receiver = str(oracle.get("target_receiver") or "")
    original_body = _semantic_solidity(original_function[1])
    calls = re.findall(r"\b" + re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) + r"\s*\(",
                       original_body)
    if len(calls) != 1:
        return ["storage-slot recovery source lacks one exact target call"]
    statements = _storage_slot_statements(oracle)
    if statements is None:
        return ["storage-slot recovery lacks an exact layout assertion shape"]
    injected = "".join(statements)
    source_compact = re.sub(r"\s+", "", _semantic_solidity(source))
    original_compact = re.sub(r"\s+", "", _semantic_solidity(original))
    if source_compact.count(injected) != 1:
        return ["storage-slot recovery does not contain one exact injected assertion"]
    if source_compact.replace(injected, "", 1) != original_compact:
        return ["storage-slot recovery changes content beyond its exact assertion"]
    return []


def _allowed_concrete_oracle_fragments(oracles: list[dict], current_index: int, *,
                                       include_event_read: bool,
                                       preserve_strings: bool) -> list[str]:
    """Compact source fragments authenticated by sibling fixed observables."""
    fragments = []

    def compact(text: str) -> str:
        return re.sub(r"\s+", "", _semantic_solidity(text, preserve_strings=preserve_strings))

    for index, oracle in enumerate(oracles):
        if index == current_index or not isinstance(oracle, dict):
            continue
        kind = oracle.get("kind")
        if kind == "storage-slot-post-state":
            statements = _storage_slot_statements(oracle)
            if statements is not None:
                declaration, _assertion = statements
                fragments.append(compact(declaration) + compact(str(oracle.get("assertion") or "")))
        elif kind == "event-log":
            statements = _event_log_statements(oracle)
            if statements is not None:
                _record, read_statement, assertions = statements
                fragment = read_statement + assertions if include_event_read else assertions
                fragments.append(compact(fragment))
        else:
            assertion = compact(str(oracle.get("assertion") or ""))
            if assertion:
                fragments.append(assertion)
    return sorted(set(fragments), key=len, reverse=True)


def _oracle_binding_errors(source: str, test: str, unit: str, oracles: object) -> list[str]:
    function = _solidity_function(source, test)
    if function is None or not isinstance(oracles, list):
        return ["cannot bind concrete oracle provenance to the selected test"]
    _params, body = function
    compact_body = re.sub(r"\s+", "", body)
    errors = []

    def _low_level_status_calls(receiver: str) -> list[re.Match[str]]:
        if not receiver:
            return []
        if unit in ("fallback", "receive"):
            return list(
                re.finditer(
                    r"\(\s*bool\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*,[^)]*\)\s*="
                    r"\s*address\s*\(\s*" + re.escape(receiver) +
                    r"\s*\)\s*\.\s*call(?:\s*\{[^{}]*\})?\s*\([^;]*;", body, re.S))
        return list(
            re.finditer(
                r"\(\s*bool\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*,[^)]*\)\s*="
                r"\s*address\s*\(\s*" + re.escape(receiver) + r"\s*\)\s*\.\s*call[^;]*abi\s*\.\s*"
                r"encode(?:Call|WithSignature|WithSelector)\s*\([^;]*[\"']" + re.escape(unit) +
                r"\s*\([^;]*;", body, re.S))

    return_oracles = [
        oracle for oracle in oracles
        if isinstance(oracle, dict) and oracle.get("kind") == "return-value"
    ]
    tuple_oracles = [
        oracle for oracle in return_oracles
        if oracle.get("return_index") is not None or oracle.get("return_arity") is not None
    ]
    tuple_binding = False
    if tuple_oracles:
        arities = {oracle.get("return_arity") for oracle in tuple_oracles}
        arity = next(iter(arities)) if len(arities) == 1 else None
        indices = [oracle.get("return_index") for oracle in tuple_oracles]
        complete = (type(arity) is int and arity > 1
                    and len(tuple_oracles) == len(return_oracles) == arity
                    and all(type(index) is int
                            for index in indices) and sorted(indices) == list(range(arity)))
        if not complete:
            errors.append(
                "tuple return oracle does not cover every ABI return component exactly once")
        else:
            ordered = sorted(tuple_oracles, key=lambda oracle: oracle["return_index"])
            if any(not oracle.get("solidity_type") or not oracle.get("observed")
                   for oracle in ordered):
                errors.append("tuple return oracle lacks a typed observed component")
            elif any(not _fixed_assert_eq(oracle.get("assertion"), oracle.get("observed"),
                                          oracle.get("expected")) for oracle in ordered):
                errors.append("tuple return oracle is not an exact fixed per-component assertEq")
            else:
                semantic_compact = re.sub(r"\s+", "", _semantic_solidity(body))
                receiver = str(ordered[0].get("target_receiver") or "")
                same_receiver = all(oracle.get("target_receiver") == receiver for oracle in ordered)
                lhs = ",".join(
                    str(oracle["solidity_type"]) + str(oracle["observed"]) for oracle in ordered)
                target_calls = list(
                    re.finditer(r"\b" + re.escape(receiver) + r"\." + re.escape(unit) +
                                r"\(", semantic_compact)) if receiver else []
                assertion_sequence = "".join(
                    re.sub(r"\s+", "", _semantic_solidity(str(oracle.get("assertion") or "")))
                    for oracle in ordered)
                call_end = (semantic_compact.find(";", target_calls[0].start())
                            if len(target_calls) == 1 else -1)
                assertions_immediate = (call_end >= 0 and assertion_sequence
                                        and semantic_compact[call_end +
                                                             1:].startswith(assertion_sequence))
                tuple_binding = bool(same_receiver and assertions_immediate and re.search(
                    r"\(" + re.escape(lhs) + r"\)=" + re.escape(receiver) + r"\." +
                    re.escape(unit) + r"\(", semantic_compact))
                if not tuple_binding:
                    errors.append("tuple return is not the exact complete typed target call result")
    for index, oracle in enumerate(oracles):
        if not isinstance(oracle, dict):
            continue
        assertion = re.sub(r"\s+", "", str(oracle.get("assertion") or ""))
        receiver = str(oracle.get("target_receiver") or "")
        if assertion and assertion not in compact_body:
            errors.append(f"concrete oracle {index} assertion is absent from selected test")
            continue
        direct_call = bool(receiver and re.search(
            r"\b" + re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) +
            r"\s*(?:\{[^{}]*\}\s*)?\(", body))
        low_level_status_calls = _low_level_status_calls(receiver)
        low_level_call = bool(low_level_status_calls)
        if receiver and not (direct_call or low_level_call):
            errors.append(f"concrete oracle {index} is not bound to selected target call")
        if oracle.get("kind") == "event-log":
            errors.extend(f"concrete oracle {index} {error}" for error in _event_log_binding_errors(
                body, unit, oracle,
                _allowed_concrete_oracle_fragments(
                    oracles, index, include_event_read=False, preserve_strings=True)))
            continue
        if oracle.get("kind") == "storage-slot-post-state":
            errors.extend(f"concrete oracle {index} {error}"
                          for error in _storage_slot_binding_errors(
                              body, unit, oracle,
                              _allowed_concrete_oracle_fragments(
                                  oracles, index, include_event_read=True, preserve_strings=False)))
            continue
        if oracle.get("kind") == "revert":
            semantic = _semantic_solidity(body, preserve_strings=True)
            if unit == "__deploy__":
                target_contract = str(oracle.get("target_contract") or "")
                armed_target = re.findall(
                    r"vm\s*\.\s*expectRevert\s*\([^;]*\)\s*;\s*"
                    r"new\s+" + re.escape(target_contract) + r"\s*\(", semantic, re.S)
            else:
                selected_receiver = re.escape(receiver)
                armed_target = re.findall(
                    r"vm\s*\.\s*expectRevert\s*\([^;]*\)\s*;\s*" + selected_receiver + r"\s*\.\s*" +
                    re.escape(unit) + r"\s*(?:\{[^{}]*\}\s*)?\(", semantic, re.S)
            if len(armed_target) != 1:
                errors.append(f"concrete oracle {index} expectRevert is not immediately armed for "
                              "the selected target call")
            continue
        if oracle.get("kind") == "normal-exit":
            observed = re.sub(r"\s+", "", str(oracle.get("observed") or ""))
            call_pos = compact_body.find(re.sub(r"\s+", "", f"{receiver}.{unit}("))
            if call_pos < 0 and low_level_call:
                call_pos = compact_body.find(f"address({receiver}).call")
            call_end = compact_body.find(";", call_pos) if call_pos >= 0 else -1
            assertion_pos = compact_body.find(assertion)
            initialization = f"bool{observed}=false;"
            completion = f"{observed}=true;"
            direct_shape = (call_end >= 0 and assertion_pos >= 0
                            and compact_body[call_end + 1:assertion_pos] == completion)
            try_shape = _try_completion_is_bound(body, unit, receiver, observed,
                                                 str(oracle.get("assertion") or ""))
            if (compact_body.count(initialization) != 1 or compact_body.count(completion) != 1
                    or not (direct_shape or try_shape)
                    or not assertion.startswith(f"assertTrue({observed},")):
                errors.append(f"concrete oracle {index} is not the strict normal-exit marker shape")
        elif oracle.get("kind") != "revert":
            observed = re.sub(r"\s+", "", str(oracle.get("observed") or ""))
            expected = re.sub(r"\s+", "", str(oracle.get("expected") or ""))
            if oracle.get("kind") == "call-status":
                completion_status = (oracle.get("expected") is False
                                     and observed == "_veriput_concrete_completed"
                                     and assertion.startswith(f"assertFalse({observed},")
                                     and compact_body.count(f"bool{observed}=false;") == 1
                                     and compact_body.count(f"{observed}=true;") == 1
                                     and _try_completion_is_bound(
                                         body, unit, receiver, observed,
                                         str(oracle.get("assertion") or "")))
                low_level_status = (len(low_level_status_calls) == 1
                                    and low_level_status_calls[0].group(1) == observed)
                if not (low_level_status or completion_status):
                    errors.append(
                        f"concrete oracle {index} status is not the selected target call result")
            if oracle.get("kind") == "return-value":
                sol_type = str(oracle.get("solidity_type") or "")
                semantic_body = _semantic_solidity(body)
                target_calls = list(
                    re.finditer(
                        r"\b" + re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) + r"\s*\(",
                        semantic_body))
                exact_returns = list(
                    re.finditer(
                        r"\b" + re.escape(sol_type) + r"\s+" + re.escape(observed) + r"\s*=\s*" +
                        re.escape(receiver) + r"\s*\.\s*" + re.escape(unit) + r"\s*\(",
                        semantic_body))
                tuple_component = (oracle.get("return_index") is not None
                                   or oracle.get("return_arity") is not None)
                scalar_binding = (sol_type and len(target_calls) == 1 and len(exact_returns) == 1
                                  and target_calls[0].start() >= exact_returns[0].start())
                if not (tuple_binding if tuple_component else scalar_binding):
                    errors.append(
                        f"concrete oracle {index} return is not the exact typed target call result")
            call_status_expected = oracle.get("expected")
            call_status_bound = (oracle.get("kind") == "call-status" and (
                (call_status_expected is False and assertion.startswith(f"assertFalse({observed},"))
                or
                (call_status_expected is True and assertion.startswith(f"assertTrue({observed},"))))
            getter_prefix = re.match(
                re.escape(receiver) + r"\.[A-Za-z_$][A-Za-z0-9_$]*\(", observed)
            getter_end = None
            if getter_prefix is not None:
                depth = 1
                for position in range(getter_prefix.end(), len(observed)):
                    if observed[position] == "(":
                        depth += 1
                    elif observed[position] == ")":
                        depth -= 1
                        if depth == 0:
                            getter_end = position + 1
                            break
            exact_getter = getter_end == len(observed)
            semantic_body = _semantic_solidity(body)
            semantic_compact = re.sub(r"\s+", "", semantic_body)
            semantic_assertion = re.sub(r'"(?:\\.|[^"\\])*"', '""',
                                        re.sub(r"\s+", "", str(oracle.get("assertion") or "")))
            selected_calls = list(
                re.finditer(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\." + re.escape(unit) + r"\(",
                            semantic_compact))
            exact_selected_call = (len(selected_calls) == 1
                                   and selected_calls[0].group(1) == receiver)
            selected_call_end = (semantic_compact.find(";", selected_calls[0].start())
                                 if exact_selected_call else -1)
            semantic_assertion_pos = semantic_compact.find(semantic_assertion)
            immediately_after_call = (selected_call_end >= 0 and semantic_assertion_pos >= 0
                                      and semantic_assertion_pos == selected_call_end + 1)
            boolean_state_assertion = (
                oracle.get("kind") == "post-state" and exact_getter and exact_selected_call
                and immediately_after_call
                and ((call_status_expected is False
                      and re.fullmatch(r"assertFalse\(" + re.escape(observed) + r"(?:,[^;]*)?\);",
                                       assertion) is not None) or
                     (call_status_expected is True
                      and re.fullmatch(r"assertTrue\(" + re.escape(observed) + r"(?:,[^;]*)?\);",
                                       assertion) is not None)))
            if (not call_status_bound and not boolean_state_assertion
                    and (observed not in assertion or expected not in assertion)):
                errors.append(
                    f"concrete oracle {index} assertion does not encode observed/expected values")
            call_pos = compact_body.find(re.sub(r"\s+", "", f"{receiver}.{unit}("))
            if call_pos < 0 and low_level_call:
                call_pos = compact_body.find(f"address({receiver}).call")
            assertion_pos = compact_body.find(assertion)
            between = compact_body[call_pos:assertion_pos] if (call_pos >= 0
                                                               and assertion_pos > call_pos) else ""
            if (oracle.get("kind") == "post-state"
                    and (call_status_expected is True or call_status_expected is False)
                    and not boolean_state_assertion):
                errors.append(
                    f"concrete oracle {index} is not an immediate exact receiver state assertion")
            if re.search(r"(?:^|;)" + re.escape(observed) + r"=", between):
                errors.append(f"concrete oracle {index} observed value is overwritten after call")
    return errors


def _entry_project(subject_dir: Path, entry: dict) -> Path:
    relative = Path(str(entry.get("project") or ""))
    project = (subject_dir / relative).resolve()
    root = (subject_dir / STORE_DIR).resolve()
    try:
        project.relative_to(root)
    except ValueError as exc:
        raise ReplayPersistenceError(f"manifest project escapes replay store: {relative}") from exc
    return project


def load_manifest(subject_dir: Path) -> dict:
    path = subject_dir / STORE_DIR / MANIFEST_NAME
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        doc = {}
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        doc = {"schema": SCHEMA, "entries": []}
    if not isinstance(doc.get("entries"), list):
        doc["entries"] = []
    doc.pop("subject_dir", None)
    for entry in doc["entries"]:
        if not isinstance(entry, dict):
            continue
        origin = entry.get("origin")
        if not isinstance(origin, dict):
            continue
        for field in ("test_file", "put_json"):
            value = origin.get(field)
            if isinstance(value, str):
                origin[field] = _relative_provenance(subject_dir, value)
            elif isinstance(value, dict) and value.get("path_class") == "external":
                value.pop("path", None)
    return doc


def invalidated_cases(path: Path = DEFAULT_INVALIDATION_LEDGER) -> set[str]:
    """Canonical cases whose apparent validity is explicitly revoked."""
    try:
        doc = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return set()
    audit = doc.get("error_then_success_evidence_audit") or {}
    return {str(case) for case in audit.get("affected_cases") or []}


def invalidation_applies(case: str,
                         tests: list[dict],
                         path: Path = DEFAULT_INVALIDATION_LEDGER) -> bool:
    """Whether current evidence still predates the pollution quarantine."""
    if case not in invalidated_cases(path):
        return False
    try:
        ledger_mtime = path.stat().st_mtime
    except OSError:
        return True
    retained_mtimes = []
    for test in tests:
        try:
            retained_mtimes.append(Path(str(test.get("file") or "")).stat().st_mtime)
        except OSError:
            continue
    # A repaired canonical rerun writes a fresh test after the frozen audit.
    # Merely rewriting result.json is deliberately insufficient.
    return not retained_mtimes or max(retained_mtimes) <= ledger_mtime


def audit_manifest(subject_dir: Path, manifest: dict | None = None) -> list[str]:
    manifest = manifest or load_manifest(subject_dir)
    errors = []
    for entry in manifest.get("entries") or []:
        try:
            project = _entry_project(subject_dir, entry)
        except ReplayPersistenceError as exc:
            errors.append(str(exc))
            continue
        test_file = project / str(entry.get("test_file") or "")
        flat_file = project / str(entry.get("flat_source") or "")
        for label, path, expected in (("test", test_file, entry.get("test_sha256")),
                                      ("flat source", flat_file, entry.get("flat_sha256"))):
            if not path.is_file():
                errors.append(f"{entry.get('replay_id')}: missing {label} {path}")
            elif expected != _sha256(path):
                errors.append(f"{entry.get('replay_id')}: {label} hash mismatch")
            elif path.stat().st_nlink > 1:
                errors.append(f"{entry.get('replay_id')}: {label} is hard-linked")
        origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
        recovered_hash = origin.get("recovered_from_test_sha256")
        if recovered_hash:
            recovered_relative = Path(str(entry.get("recovered_from_test_file") or ""))
            recovered_file = (project / recovered_relative).resolve()
            try:
                recovered_file.relative_to(project.resolve())
            except ValueError:
                errors.append(f"{entry.get('replay_id')}: recovered source escapes replay project")
            else:
                if not recovered_file.is_file():
                    errors.append(f"{entry.get('replay_id')}: missing recovered original source")
                elif (_sha256(recovered_file) != recovered_hash
                      or entry.get("recovered_from_test_sha256") != recovered_hash):
                    errors.append(f"{entry.get('replay_id')}: recovered original hash mismatch")
                elif recovered_file.stat().st_nlink > 1:
                    errors.append(f"{entry.get('replay_id')}: recovered original is hard-linked")
            if (entry.get("recovered_from_flat_sha256")
                    != origin.get("recovered_from_flat_sha256")):
                errors.append(f"{entry.get('replay_id')}: recovered flat source hash mismatch")
        covered_hash = origin.get("covered_original_test_sha256")
        if covered_hash:
            covered_relative = Path(str(entry.get("covered_original_test_file") or ""))
            covered_file = (project / covered_relative).resolve()
            try:
                covered_file.relative_to(project.resolve())
            except ValueError:
                errors.append(
                    f"{entry.get('replay_id')}: covered original source escapes replay project")
            else:
                if not covered_file.is_file():
                    errors.append(f"{entry.get('replay_id')}: missing covered original source")
                elif (_sha256(covered_file) != covered_hash
                      or entry.get("covered_original_test_sha256") != covered_hash):
                    errors.append(f"{entry.get('replay_id')}: covered original hash mismatch")
                elif covered_file.stat().st_nlink > 1:
                    errors.append(f"{entry.get('replay_id')}: covered original is hard-linked")
            if (entry.get("covered_original_flat_sha256")
                    != origin.get("covered_original_flat_sha256")):
                errors.append(
                    f"{entry.get('replay_id')}: covered original flat source hash mismatch")
            covered_identity = origin.get("covered_original_identity")
            if not isinstance(covered_identity, dict):
                errors.append(f"{entry.get('replay_id')}: missing covered original identity")
        if not (project / "foundry.toml").is_file():
            errors.append(f"{entry.get('replay_id')}: missing foundry.toml")
        if not (project / "lib" / "forge-std" / "src" / "Test.sol").is_file():
            errors.append(f"{entry.get('replay_id')}: forge-std is not vendored")
        if test_file.is_file():
            replay_errors = deterministic_replay_errors(
                test_file, str(entry.get("test") or ""),
                str((entry.get("origin") or {}).get("unit") or ""))
            errors.extend(f"{entry.get('replay_id')}: {error}" for error in replay_errors)
        errors.extend(f"{entry.get('replay_id')}: {error}"
                      for error in _structured_oracle_errors(entry.get("concrete_oracles")))
        if test_file.is_file():
            errors.extend(f"{entry.get('replay_id')}: {error}" for error in _oracle_binding_errors(
                test_file.read_text(errors="replace"), str(entry.get("test") or ""),
                str((entry.get("origin") or {}).get("unit") or ""), entry.get("concrete_oracles")))
        replay_log = project / str(entry.get("forge_log") or "")
        if int(entry.get("forge_passed_tests") or 0) < 1:
            errors.append(f"{entry.get('replay_id')}: no executed Forge replay test")
        elif not entry.get("forge_log"):
            errors.extend(_execution_evidence_errors(subject_dir, entry, project, test_file))
        elif not replay_log.is_file():
            errors.append(f"{entry.get('replay_id')}: missing Forge replay log")
        elif entry.get("forge_log_sha256") != _sha256(replay_log):
            errors.append(f"{entry.get('replay_id')}: Forge replay log hash mismatch")
        if entry.get("generalization_status") not in ("confirmed-generalized-to-put",
                                                      "same-path-candidate", "not-generalized"):
            errors.append(f"{entry.get('replay_id')}: missing generalization classification")
        if entry.get("generalization_status") == "confirmed-generalized-to-put":
            authenticated = _authenticated_put_basis_hashes(entry, subject_dir)
            matching = entry.get("matching_put_artifacts") or []
            if (not authenticated or not isinstance(matching, list) or not matching or any(
                    not isinstance(item, dict) or item.get("put_json_sha256") not in authenticated
                    for item in matching)):
                errors.append(f"{entry.get('replay_id')}: unauthenticated PUT basis classification")
        if not entry.get("concrete_oracles"):
            errors.append(f"{entry.get('replay_id')}: missing concrete execution oracle metadata")
        linked = [
            path.relative_to(project).as_posix() for path in project.rglob("*")
            if path.is_file() and not path.is_symlink() and path.stat().st_nlink > 1
        ]
        if linked:
            errors.append(f"{entry.get('replay_id')}: hard-linked dependency {linked[0]}")
    return errors


def repair_manifest_independence(subject_dir: Path, manifest: dict | None = None) -> list[str]:
    """Repair legacy inode links and make every replay command exact."""
    manifest = manifest or load_manifest(subject_dir)
    changed = False
    for entry in manifest.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        try:
            project = _entry_project(subject_dir, entry)
        except ReplayPersistenceError:
            continue
        if project.is_dir():
            _privatize_tree(project)
        command = [
            "forge", "test", "--match-test", f"^{re.escape(str(entry.get('test') or ''))}\\(",
            "--match-path",
            str(entry.get("test_file") or "")
        ]
        if entry.get("forge_command") != command:
            entry["forge_command"] = command
            changed = True
    errors = audit_manifest(subject_dir, manifest)
    if changed and not errors:
        manifest["updated_at"] = time.time()
        _atomic_json(subject_dir / STORE_DIR / MANIFEST_NAME, manifest)
    return errors


def partition_legacy_entries(subject_dir: Path,
                             manifest: dict | None = None,
                             *,
                             apply: bool = False) -> dict:
    """Separate invalid legacy entries without deleting their projects or metadata."""
    manifest = manifest or load_manifest(subject_dir)
    active = []
    legacy = list(manifest.get("legacy_entries") or [])
    moved = []
    for entry in manifest.get("entries") or []:
        entry_errors = audit_manifest(subject_dir, {"entries": [entry]})
        if entry_errors:
            legacy.append({**entry, "legacy_audit_errors": entry_errors})
            moved.append(str(entry.get("replay_id") or ""))
        else:
            active.append(entry)
    updated = {**manifest, "entries": active, "legacy_entries": legacy, "updated_at": time.time()}
    if apply and moved:
        _atomic_json(subject_dir / STORE_DIR / MANIFEST_NAME, updated)
    return {
        "active_entry_count": len(active),
        "legacy_entry_count": len(legacy),
        "moved_entry_count": len(moved),
        "moved_replay_ids": moved,
    }


def persist_concrete_replay(subject_dir: Path,
                            row: dict,
                            *,
                            dry_run: bool = False,
                            forge_timeout: int = 20) -> dict:
    """Atomically adopt one already-green concrete artifact."""
    if row.get("kind") != "concrete" or row.get("valid_reference_test") is not True:
        raise ReplayPersistenceError("only a valid concrete reference test may be persisted")
    source_test = Path(str(row.get("file") or "")).expanduser().resolve()
    if not source_test.is_file():
        raise ReplayPersistenceError(f"concrete test is not retained: {source_test}")
    identity = replay_identity(row)
    if not identity.get("unit"):
        raise ReplayPersistenceError("concrete replay identity has no unit")
    source_grounded_stage2 = identity.get("stage2_source") in {
        "source-grounded-manual-concrete-replay",
        "source_grounded_callable_recovery",
        # A constructor-revert replay is grounded in an explicit
        # assert/require/revert of the exact target source, not in a certified
        # path of a callable unit, so it has no (path_function, enc) to carry.
        # Demanding one withheld already-green artifacts and reported the whole
        # case as a persistence error.
        "source_constructor_revert_fallback",
    }
    if not source_grounded_stage2 and (not identity.get("path_function")
                                       or identity.get("enc") is None):
        raise ReplayPersistenceError(
            "verifier-derived concrete replay lacks exact path_function/enc identity")
    _detected_oracles, replay_errors = deterministic_replay_oracles(source_test,
                                                                    str(row.get("test") or ""),
                                                                    str(identity.get("unit") or ""))
    record = _load_record(row.get("put_json"))
    replay_oracles = row.get("concrete_oracles") or record.get("concrete_oracles")
    replay_errors.extend(_structured_oracle_errors(replay_oracles))
    replay_errors.extend(
        _oracle_binding_errors(source_test.read_text(errors="replace"), str(row.get("test") or ""),
                               str(identity.get("unit") or ""), replay_oracles))
    if replay_errors:
        raise ReplayPersistenceError("; ".join(replay_errors))
    source_project = _foundry_project(source_test)
    try:
        test_relative = source_test.relative_to(source_project)
    except ValueError as exc:
        raise ReplayPersistenceError("test is outside its Foundry project") from exc
    flat_source = source_project / "src" / "flat.sol"
    if not flat_source.is_file():
        raise ReplayPersistenceError(f"Foundry project has no src/flat.sol: {source_project}")
    recovered_from = None
    recovered_flat = None
    covered_original = None
    covered_original_flat = None
    covered_original_identity = None
    if row.get("recovered_from_file"):
        recovered_from = Path(str(row["recovered_from_file"])).expanduser().resolve()
        if not recovered_from.is_file():
            raise ReplayPersistenceError(
                f"storage-slot recovery source is not retained: {recovered_from}")
        recovered_project = _foundry_project(recovered_from)
        recovered_flat = recovered_project / "src" / "flat.sol"
        if not recovered_flat.is_file() or _sha256(recovered_flat) != _sha256(flat_source):
            raise ReplayPersistenceError(
                "storage-slot recovery does not use the exact original flat source")
        storage_oracles = [
            oracle for oracle in replay_oracles
            if isinstance(oracle, dict) and oracle.get("kind") == "storage-slot-post-state"
        ]
        event_oracles = [
            oracle for oracle in replay_oracles
            if isinstance(oracle, dict) and oracle.get("kind") == "event-log"
        ]
        if not replay_oracles or (storage_oracles and event_oracles):
            raise ReplayPersistenceError(
                "recovery requires one observable family: storage-slot or event oracle")
        if event_oracles:
            if len(event_oracles) != 1 or len(replay_oracles) != 1:
                raise ReplayPersistenceError(
                    "event recovery currently accepts one exact event oracle")
            recovery_errors = _event_log_recovery_errors(source_test.read_text(errors="replace"),
                                                         recovered_from.read_text(errors="replace"),
                                                         str(row.get("test") or ""),
                                                         str(identity.get("unit") or ""),
                                                         event_oracles[0])
        else:
            original_source = source_test.read_text(errors="replace")
            recovered_source = recovered_from.read_text(errors="replace")
            recovery_errors = []
            # Every generated state assertion must be present exactly once.
            injected = "".join(
                _storage_slot_statements(oracle)[0] + _storage_slot_statements(oracle)[1]
                for oracle in storage_oracles)
            source_compact = re.sub(r"\s+", "", _semantic_solidity(original_source))
            recovered_compact = re.sub(r"\s+", "", _semantic_solidity(recovered_source))
            if recovered_compact.count(injected) != 1 or \
                    recovered_compact.replace(injected, "", 1) != source_compact:
                recovery_errors.append(
                    "storage-slot recovery changes content beyond exact assertions")
        if recovery_errors:
            raise ReplayPersistenceError("; ".join(recovery_errors))
    if isinstance(row.get("covers_original"), dict):
        cover = row["covers_original"]
        covered_original = Path(str(cover.get("file") or "")).expanduser().resolve()
        if not covered_original.is_file():
            raise ReplayPersistenceError(
                f"covered original replay source is not retained: {covered_original}")
        covered_project = _foundry_project(covered_original)
        covered_original_flat = covered_project / "src" / "flat.sol"
        if (not covered_original_flat.is_file()
                or _sha256(covered_original_flat) != _sha256(flat_source)):
            raise ReplayPersistenceError(
                "covered original replay does not use the exact same flat source")
        covered_original_identity = cover.get("identity")
        if not isinstance(covered_original_identity, dict):
            raise ReplayPersistenceError("covered original replay lacks identity")
        for field in ("unit", "enc", "piece"):
            expected = covered_original_identity.get(field)
            actual = identity.get(field)
            if str(expected if expected is not None else "") != str(
                    actual if actual is not None else ""):
                raise ReplayPersistenceError(f"covered original identity disagrees on {field}")
        if str(cover.get("test") or row.get("test") or "") != str(row.get("test") or ""):
            raise ReplayPersistenceError("covered original test name disagrees")
    digest_seed = json.dumps(
        {
            "identity": identity,
            "test": row.get("test"),
            "test_sha256": _sha256(source_test),
            "flat_sha256": _sha256(flat_source),
        },
        sort_keys=True).encode()
    replay_id = (_safe_name(identity.get("unit")) + "-" +
                 hashlib.sha256(digest_seed).hexdigest()[:16])
    relative_project = Path(STORE_DIR) / "projects" / replay_id
    entry = {
        "schema":
        "veriput-rq1-concrete-replay/v1",
        "replay_id":
        replay_id,
        "project":
        relative_project.as_posix(),
        "test_file":
        test_relative.as_posix(),
        "test":
        row.get("test"),
        "test_sha256":
        _sha256(source_test),
        "flat_source":
        "src/flat.sol",
        "flat_sha256":
        _sha256(flat_source),
        "forge_command": [
            "forge", "test", "--match-test", f"^{re.escape(str(row.get('test') or ''))}\\(",
            "--match-path",
            test_relative.as_posix()
        ],
        "forge_status":
        row.get("forge_status"),
        "valid_reference_test":
        True,
        "generalization_status":
        "not-generalized",
        "matching_put_tests": [],
        "concrete_oracles":
        replay_oracles,
        "origin": {
            **identity,
            "test_file": _relative_provenance(subject_dir, source_test),
            "put_json": _relative_provenance(subject_dir, row.get("put_json")),
        },
    }
    if recovered_from is not None and recovered_flat is not None:
        recovered_relative = Path("recovered-from") / recovered_from.name
        entry.update({
            "recovered_from_test_file": recovered_relative.as_posix(),
            "recovered_from_test_sha256": _sha256(recovered_from),
            "recovered_from_flat_sha256": _sha256(recovered_flat),
        })
        entry["origin"].update({
            "recovered_from_test_file":
            _relative_provenance(subject_dir, recovered_from),
            "recovered_from_test_sha256":
            _sha256(recovered_from),
            "recovered_from_flat_sha256":
            _sha256(recovered_flat),
        })
    if (covered_original is not None and covered_original_flat is not None
            and covered_original_identity is not None):
        covered_relative = (Path("covered-originals") / (f"{_sha256(covered_original)[:12]}-"
                                                         f"{covered_original.name}"))
        covered_test = str(
            cover.get("test") or row.get("covered_original_test") or row.get("test") or "")
        entry.update({
            "covered_original_test": covered_test,
            "covered_original_test_file": covered_relative.as_posix(),
            "covered_original_test_sha256": _sha256(covered_original),
            "covered_original_flat_sha256": _sha256(covered_original_flat),
            "covered_original_identity": covered_original_identity,
        })
        entry["origin"].update({
            "covered_original_test":
            covered_test,
            "covered_original_test_file":
            _relative_provenance(subject_dir, covered_original),
            "covered_original_test_sha256":
            _sha256(covered_original),
            "covered_original_flat_sha256":
            _sha256(covered_original_flat),
            "covered_original_identity":
            covered_original_identity,
        })
    if dry_run:
        entry["action"] = "already-present" if (subject_dir /
                                                relative_project).is_dir() else "persist"
        return entry

    store = subject_dir / STORE_DIR
    destination = subject_dir / relative_project
    if not destination.is_dir():
        store.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=".replay-stage-", dir=store))
        staging = staging_root / replay_id
        try:
            (staging / "src").mkdir(parents=True)
            (staging / "test").mkdir(parents=True)
            _copy_file(source_project / "foundry.toml", staging / "foundry.toml")
            _copy_file(flat_source, staging / "src" / "flat.sol")
            _copy_file(source_test, staging / test_relative)
            if recovered_from is not None:
                _copy_file(recovered_from, staging / recovered_relative)
            if covered_original is not None:
                (staging / covered_relative.parent).mkdir(parents=True, exist_ok=True)
                _copy_file(covered_original, staging / covered_relative)
            forge_std = (source_project / "lib" / "forge-std").resolve()
            _copy_tree(forge_std, staging / "lib" / "forge-std")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)
    _privatize_tree(destination)
    if recovered_from is not None:
        retained_original = destination / recovered_relative
        if (not retained_original.is_file()
                or _sha256(retained_original) != _sha256(recovered_from)):
            _copy_file(recovered_from, retained_original)
        _privatize_tree(retained_original)
    if covered_original is not None:
        retained_covered = destination / covered_relative
        if (not retained_covered.is_file()
                or _sha256(retained_covered) != _sha256(covered_original)):
            retained_covered.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(covered_original, retained_covered)
        _privatize_tree(retained_covered)

    try:
        completed = subprocess.run(entry["forge_command"],
                                   cwd=destination,
                                   text=True,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   timeout=forge_timeout,
                                   check=False)
    except subprocess.TimeoutExpired as exc:
        raise ReplayPersistenceError(
            f"canonical concrete replay timed out after {forge_timeout}s") from exc
    output = completed.stdout or ""
    passed = max((int(count) for count in re.findall(r"\b(\d+) passed\b", output)), default=0)
    if completed.returncode != 0 or passed < 1 or "No tests found" in output:
        raise ReplayPersistenceError("canonical concrete replay did not execute a passing test: " +
                                     output[-1000:])
    replay_log = destination / "forge-replay.log"
    replay_log.write_text(output)
    entry.update({
        "forge_status": "Success",
        "forge_passed_tests": passed,
        "forge_log": replay_log.relative_to(destination).as_posix(),
        "forge_log_sha256": _sha256(replay_log),
        "forge_verified_at": time.time(),
    })

    manifest = load_manifest(subject_dir)
    active_entries = []
    legacy_entries = list(manifest.get("legacy_entries") or [])
    for item in manifest.get("entries") or []:
        if (isinstance(item, dict) and item.get("replay_id")
                and not audit_manifest(subject_dir, {"entries": [item]})):
            active_entries.append(item)
        elif isinstance(item, dict):
            legacy_entries.append(item)
    entries = {item.get("replay_id"): item for item in active_entries if item.get("replay_id")}
    entry["persisted_at"] = time.time()
    entries[replay_id] = entry
    manifest.update({
        "schema": SCHEMA,
        "updated_at": time.time(),
        "entries": [entries[key] for key in sorted(entries)],
        "legacy_entries": legacy_entries,
    })
    errors = audit_manifest(subject_dir, manifest)
    if errors:
        raise ReplayPersistenceError("; ".join(errors))
    _atomic_json(store / MANIFEST_NAME, manifest)
    return entry


def persistence_coverage(valid_tests: list[dict],
                         entries: list[dict],
                         subject_dir: Path | None = None) -> dict:
    """Report case coverage and exact PUT-to-concrete provenance gaps."""
    valid = [
        row for row in valid_tests
        if isinstance(row, dict) and row.get("valid_reference_test") is True
    ]
    puts = [row for row in valid if row.get("kind") == "put"]
    concretes = [row for row in valid if row.get("kind") == "concrete"]
    unrecognized_valid_count = len(valid) - len(puts) - len(concretes)
    # One obligation is one instrumented path/CE. Rows are retries or test
    # materializations, so they must not increase the obligation count.
    all_put_keys = {_artifact_key(row) for row in valid if _physical_test_kind(row) == "put"}
    put_keys = {
        _artifact_key(row)
        for row in puts
        if replay_identity(row).get("path_function") and replay_identity(row).get("enc") is not None
    }
    identity_matching_concretes = [row for row in concretes if _artifact_key(row) in put_keys]
    identity_unmatched_concretes = [row for row in concretes if _artifact_key(row) not in put_keys]
    audited_entries = []
    if subject_dir is not None:
        audited_entries = [
            entry for entry in entries
            if isinstance(entry, dict) and not audit_manifest(subject_dir, {"entries": [entry]})
        ]
    persisted_concrete_tests = set()
    persisted_not_generalized_tests = set()
    confirmed_not_generalized_keys = set()
    persisted_basis_artifacts = set()
    persisted_structural_basis_artifacts = set()
    persisted_fused_basis_artifacts = set()
    persisted_generalized_entries = 0
    persisted_not_generalized_entries = 0
    for entry in audited_entries:
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if isinstance(origin, dict):
            persisted_concrete_tests.update(_entry_test_keys(entry))
            if entry.get("generalization_status") == "confirmed-generalized-to-put":
                authenticated = _authenticated_put_basis_hashes(entry, subject_dir)
                if not authenticated:
                    continue
                persisted_generalized_entries += 1
                origin_key = (str(origin.get("path_function") or ""), str(origin.get("unit") or ""),
                              str(origin.get("enc") if origin.get("enc") is not None else ""),
                              str(origin.get("piece") if origin.get("piece") is not None else ""))
                persisted_basis_artifacts.update(
                    (origin_key, str(item.get("test") or ""),
                     str(item.get("put_json_sha256") or ""))
                    for item in entry.get("matching_put_artifacts") or [] if isinstance(item, dict)
                    and item.get("test") and item.get("put_json_sha256") in authenticated)
            elif _entry_is_currently_not_generalized(entry, all_put_keys):
                persisted_not_generalized_entries += 1
                persisted_not_generalized_tests.update(_entry_test_keys(entry))
    for row in puts:
        if (_structural_put_basis_error(row) is None
                or _source_grounded_createcall_basis_error(row) is None
                or _source_grounded_fifs_basis_error(row) is None):
            put_json = Path(str(row.get("put_json") or ""))
            persisted_structural_basis_artifacts.add(
                (_artifact_key(row), str(row.get("test") or ""), _sha256(put_json)))
        if _fused_put_basis_error(row) is None:
            put_json = Path(str(row.get("put_json") or ""))
            persisted_fused_basis_artifacts.add(
                (_artifact_key(row), str(row.get("test") or ""), _sha256(put_json)))
    missing_puts = []
    for row in puts:
        identity = replay_identity(row)
        put_json = Path(str(row.get("put_json") or ""))
        put_sha = _sha256(put_json) if put_json.is_file() else ""
        basis_key = (_artifact_key(row), str(row.get("test") or ""), put_sha)
        if (_put_requires_concrete_basis(row)
                and (not identity.get("path_function") or identity.get("enc") is None
                     or (basis_key not in persisted_basis_artifacts
                         and basis_key not in persisted_structural_basis_artifacts
                         and basis_key not in persisted_fused_basis_artifacts))):
            missing_puts.append({
                **replay_identity(row),
                "test": row.get("test"),
                "put_json": row.get("put_json"),
            })
    missing_concretes = []
    for row in concretes:
        identity = _concrete_test_key(row)
        if identity not in persisted_concrete_tests:
            missing_concretes.append({
                **replay_identity(row),
                "test": row.get("test"),
                "file": row.get("file"),
            })
    persisted_put_basis_count = len(puts) - len(missing_puts)
    persisted_valid_concrete_count = len(concretes) - len(missing_concretes)
    confirmed_not_generalized_concrete_count = len({_concrete_test_key(row)
                                                    for row in concretes}
                                                   & persisted_not_generalized_tests)
    for row in valid:
        if _physical_test_kind(row) != "concrete":
            continue
        if _concrete_test_key(row) in persisted_not_generalized_tests:
            confirmed_not_generalized_keys.add(_artifact_key(row))
    publishable_validity_keys = []
    for row in concretes:
        if _concrete_test_key(row) in persisted_concrete_tests:
            publishable_validity_keys.append(persistence_publication_key(row))
    for row in puts:
        put_json = Path(str(row.get("put_json") or ""))
        basis_key = (_artifact_key(row), str(row.get("test") or ""),
                     _sha256(put_json) if put_json.is_file() else "")
        if (not _put_requires_concrete_basis(row) or basis_key in persisted_basis_artifacts
                or basis_key in persisted_structural_basis_artifacts
                or basis_key in persisted_fused_basis_artifacts):
            publishable_validity_keys.append(persistence_publication_key(row))
    return {
        "schema":
        "veriput-rq1-concrete-replay-coverage/v1",
        "strict_valid":
        bool(valid),
        "manifest_entry_count":
        len(entries),
        "canonical_replay_count":
        len(audited_entries),
        "invalid_manifest_entry_count":
        len(entries) - len(audited_entries),
        "case_replay_persisted":
        bool(audited_entries or persisted_structural_basis_artifacts),
        "valid_put_count":
        len(puts),
        "valid_concrete_count":
        len(concretes),
        "unrecognized_valid_count":
        unrecognized_valid_count,
        "identity_matching_concrete_count":
        len(identity_matching_concretes),
        "identity_unmatched_concrete_count":
        len(identity_unmatched_concretes),
        "persisted_generalized_replay_entry_count":
        persisted_generalized_entries,
        "persisted_structural_basis_count":
        len(persisted_structural_basis_artifacts),
        "persisted_fused_put_basis_count":
        len(persisted_fused_basis_artifacts),
        "persisted_not_generalized_replay_entry_count":
        persisted_not_generalized_entries,
        "confirmed_not_generalized_concrete_count": (confirmed_not_generalized_concrete_count),
        "generalized_ce_obligation_count":
        len(all_put_keys),
        "not_generalized_ce_obligation_count":
        len(confirmed_not_generalized_keys),
        "persisted_put_basis_count":
        persisted_put_basis_count,
        "persisted_valid_concrete_count":
        persisted_valid_concrete_count,
        "put_basis_missing_count":
        len(missing_puts),
        "put_basis_missing":
        missing_puts,
        "valid_concrete_missing_count":
        len(missing_concretes),
        "valid_concrete_missing":
        missing_concretes,
        "publishable_validity_keys":
        sorted(key for key in publishable_validity_keys if key is not None),
        "complete":
        (bool(valid) and not missing_puts and not missing_concretes
         and not unrecognized_valid_count),
    }


def annotate_generalization(subject_dir: Path, valid_tests: list[dict]) -> dict:
    """Classify confirmed PUT bases separately from same-path candidates."""
    put_tests = {}
    for row in valid_tests:
        if (not isinstance(row, dict) or row.get("kind") != "put"
                or row.get("valid_reference_test") is not True):
            continue
        identity = replay_identity(row)
        if not identity.get("path_function") or identity.get("enc") is None:
            continue
        put_json = Path(str(row.get("put_json") or ""))
        put_tests.setdefault(_artifact_key(row), []).append({
            "test":
            str(row.get("test") or ""),
            "put_json_sha256":
            _sha256(put_json) if put_json.is_file() else None,
        })
    manifest = load_manifest(subject_dir)
    counts = {"confirmed-generalized-to-put": 0, "same-path-candidate": 0, "not-generalized": 0}
    for entry in manifest.get("entries") or []:
        origin = entry.get("origin") if isinstance(entry, dict) else None
        if not isinstance(origin, dict):
            continue
        key = (str(origin.get("path_function") or ""), str(origin.get("unit") or ""),
               str(origin.get("enc") if origin.get("enc") is not None else ""),
               str(origin.get("piece") if origin.get("piece") is not None else ""))
        exact_identity = bool(origin.get("path_function") and origin.get("enc") is not None)
        candidate_rows = put_tests.get(key) or [] if exact_identity else []
        candidates = sorted(set(row["test"] for row in candidate_rows if row["test"]))
        authenticated_basis = _authenticated_put_basis_hashes(entry, subject_dir)
        confirmed_artifacts = []
        if authenticated_basis:
            confirmed_artifacts = [
                row for row in candidate_rows
                if row.get("put_json_sha256") in authenticated_basis and row.get("test")
            ]
        confirmed = sorted(set(row["test"] for row in confirmed_artifacts))
        status = ("confirmed-generalized-to-put" if confirmed_artifacts else
                  "same-path-candidate" if candidates else "not-generalized")
        entry["generalization_status"] = status
        entry["matching_put_tests"] = confirmed
        entry["matching_put_artifacts"] = confirmed_artifacts
        entry["same_path_put_candidates"] = candidates
        entry["generalization_provenance"] = (
            authenticated_basis[confirmed_artifacts[0]["put_json_sha256"]]
            if confirmed_artifacts else "same-path-only" if candidates else "no-put-candidate")
        counts[status] += 1
    manifest["generalization"] = {
        "schema": "veriput-rq1-concrete-generalization/v1",
        "confirmed_generalized_to_put": counts["confirmed-generalized-to-put"],
        "same_path_candidates": counts["same-path-candidate"],
        "not_generalized": counts["not-generalized"],
    }
    manifest["updated_at"] = time.time()
    errors = audit_manifest(subject_dir, manifest)
    if errors:
        raise ReplayPersistenceError("; ".join(errors))
    _atomic_json(subject_dir / STORE_DIR / MANIFEST_NAME, manifest)
    return manifest["generalization"]
