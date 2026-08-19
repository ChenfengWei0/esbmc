#!/usr/bin/env python3
"""Revalidate canonical RQ1 PUT proof obligations with clean k-induction.

This runner is deliberately read-only with respect to RQ1.  It reconstructs
queries from retained PUT workdirs and writes resumable evidence to a sibling
audit directory.  One inventory item is one physical PUT (exact put.json
SHA-256), not a result-row retry.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import signal
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from rq1_concrete_replay_migrate import DEFAULT_RESULT_ROOT, _case_dirs, _strict_valid_tests
from rq1_concrete_replay_store import _physical_test_kind

DEFAULT_OUTPUT = Path("/home/samson/workspace/VeriPUT/Results/RQ1_KInduction_Audit")
DEFAULT_ESBMC = Path("/home/samson/workspace/esbmc/build/src/esbmc/esbmc")
AST_LOCK = threading.Lock()
FIXTURE_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()
ACTIVE_PROCESSES: set[subprocess.Popen] = set()
SOURCE_ROOT = Path("/home/samson/workspace/VeriPUT/scripts/Results/workdirs")
GUARD_SEMANTICS = "materialized-path-guard-ast/v1"

VALUE_OPTIONS = {
    "--base-k-step", "--k-step", "--max-inductive-step", "--max-k-step",
    "--memlimit", "--unwind", "--unwindset", "--path-cov-assert",
    "--path-cov-certify",
}
FLAG_OPTIONS = {
    "--incremental-bmc", "--k-induction", "--unlimited-k-steps",
    "--disable-forward-condition", "--enable-forward-condition",
    "--partial-loops", "--overflow-check",
    "--unsigned-overflow-check", "--signed-overflow-check",
    "--div-by-zero-check", "--no-div-by-zero-check",
    "--result-only", "--cov-report-json", "--path-cov-arith-resolve",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as stream:
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def retained_command(workdir: Path) -> list[str] | None:
    for log in sorted((workdir / "assert").glob("run*.log")):
        first = log.read_text(errors="replace").splitlines()[:1]
        if not first:
            continue
        words = shlex.split(first[0])
        for i, word in enumerate(words):
            if word.endswith("/esbmc") or word == "esbmc":
                command = words[i:]
                try:
                    sol_index = command.index("--sol")
                except ValueError:
                    return command
                option_index = sol_index + 1
                while (option_index < len(command) and
                       not command[option_index].startswith("--")):
                    option_index += 1
                # Some retained logs did not shell-quote source paths with
                # spaces. Reassemble the single --sol value before recovery.
                command[sol_index + 1:option_index] = [
                    " ".join(command[sol_index + 1:option_index])
                ]
                return command
    return None


def clean_command(command: list[str], esbmc: Path, memlimit: str) -> list[str]:
    cleaned = [str(esbmc)]
    i = 1
    while i < len(command):
        word = command[i]
        if word in VALUE_OPTIONS or word.startswith("--unwind"):
            i += 2
            continue
        if word in FLAG_OPTIONS or word == "--solidity-max-tx":
            i += 2 if word == "--solidity-max-tx" else 1
            continue
        cleaned.append(word)
        i += 1
    cleaned.extend([
        "--k-induction", "--enable-forward-condition", "--max-k-step", "30",
        "--solidity-max-tx", "1",
        "--memlimit", memlimit, "--result-only", "--cov-report-json",
    ])
    return cleaned


def materialize_ast(command: list[str], output: Path, subject: str,
                    put_json: Path) -> list[str]:
    """Replace an expired AST-cache input with an isolated source-hash AST."""
    if len(command) < 2:
        return command
    current_ast_root = output / "asts"
    try:
        if Path(command[1]).is_file() and Path(command[1]).is_relative_to(
                current_ast_root):
            return command
    except ValueError:
        pass
    try:
        sol_index = command.index("--sol")
        source = Path(command[sol_index + 1])
    except (ValueError, IndexError):
        return command
    if not source.is_file():
        put_unit_dir = put_json.parents[2]
        matches = list(put_unit_dir.glob("*certify-results/src/flat.sol"))
        if len(matches) != 1:
            matches = list(SOURCE_ROOT.glob(f"*/subjects/{subject}/flat.sol"))
        if len(matches) != 1:
            return command
        source = matches[0]
        command[sol_index + 1] = str(source)
    ast = output / "asts" / f"{sha256(source)}.solast"
    with AST_LOCK:
        if not ast.is_file():
            ast.parent.mkdir(parents=True, exist_ok=True)
            compiler = Path("solc")
            pragma = re.search(
                r"\bpragma\s+solidity\s*=\s*(\d+\.\d+\.\d+)\s*;",
                source.read_text(errors="replace"))
            if pragma:
                cached = Path.home() / ".solcx" / f"solc-v{pragma.group(1)}"
                if cached.is_file():
                    compiler = cached
            completed = subprocess.run(
                [str(compiler), "--ast-compact-json", str(source)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            if completed.returncode != 0:
                return command
            with tempfile.NamedTemporaryFile("wb", dir=ast.parent, delete=False) as stream:
                stream.write(completed.stdout)
                temporary = Path(stream.name)
            os.replace(temporary, ast)
    return [command[0], str(ast), *command[2:]]


def materialize_fixtures(command: list[str], output: Path) -> list[str]:
    """Copy retained path fixtures into the isolated audit directory."""
    command = list(command)
    for index, word in enumerate(command[:-1]):
        if word != "--path-cov-fixture":
            continue
        source = Path(command[index + 1])
        if not source.is_file():
            continue
        destination = output / "fixtures" / f"{sha256(source)}.json"
        with FIXTURE_LOCK:
            if not destination.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                        "wb", dir=destination.parent, delete=False) as stream:
                    stream.write(source.read_bytes())
                    temporary = Path(stream.name)
                os.replace(temporary, destination)
        command[index + 1] = str(destination)
    return command


def inventory(root: Path) -> list[dict]:
    unique: dict[tuple, dict] = {}
    for case, subject_dir in _case_dirs(root):
        for row in _strict_valid_tests(subject_dir):
            if _physical_test_kind(row) != "put":
                continue
            put_json = Path(row.get("put_json") or "")
            if not put_json.is_file():
                continue
            data = json.loads(put_json.read_text())
            stats = data.get("stats") or {}
            final_oracles = [
                {
                    "var": oracle.get("var"),
                    "text": oracle.get("text"),
                    "layer": oracle.get("layer"),
                    "classes": oracle.get("classes") or [],
                }
                for oracle in stats.get("assertion_oracles") or []
                if oracle.get("verdict") == "HOLDS"
                and oracle.get("emitted_in_test") is True
            ]
            stage4_kind = data.get("stage4_kind")
            if stage4_kind == "abi-value-gate":
                for oracle in final_oracles:
                    if oracle.get("layer") == "exit":
                        oracle["text"] = "path exits through revert"
            digest = sha256(put_json)
            key = (case, str(data.get("path_function") or ""),
                   str(data.get("unit") or row.get("unit") or ""),
                   str(data.get("enc") if data.get("enc") is not None else ""),
                   str(data.get("piece") if data.get("piece") is not None else ""))
            unique[key] = {
                "case": case,
                "subject_dir": str(subject_dir),
                "put_json": str(put_json),
                "put_json_sha256": digest,
                "path_function": key[1], "unit": key[2], "enc": key[3], "piece": key[4],
                "test": data.get("test") or row.get("test"),
                # The valid-test row owns the final artifact selected into RQ1.
                # put.json.file can point at an older same-named rerun.
                "test_file": row.get("file"),
                "oracle_classes": sorted(set(stats.get("oracle_classes") or [])),
                "exit_kind": ("revert" if stage4_kind == "abi-value-gate"
                              else stats.get("exit_kind")),
                "stage4_kind": stage4_kind,
                # Only emitted final-test assertions are proof obligations.  The
                # raw ladder also contains superseded and deliberately dropped
                # candidates (notably vacuous return candidates on revert paths).
                "expected_ladder": final_oracles,
            }
    return [unique[key] for key in sorted(unique)]


def stratified_sample(items: list[dict], size: int) -> list[dict]:
    """Select a deterministic round-robin sample across dataset and oracle shape."""
    groups: dict[tuple, list[dict]] = {}
    for item in items:
        dataset = item["case"].split("/", 1)[0]
        classes = "+".join(item.get("oracle_classes") or ["none"])
        key = (dataset, classes, item.get("exit_kind") or "unknown")
        groups.setdefault(key, []).append(item)
    for values in groups.values():
        values.sort(key=lambda item: hashlib.sha256(
            (item["case"] + item["path_function"] + item["enc"] + item["piece"]).encode()
        ).hexdigest())
    selected = []
    keys = sorted(groups)
    while len(selected) < min(size, len(items)):
        progressed = False
        for key in keys:
            if groups[key] and len(selected) < size:
                selected.append(groups[key].pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def completed_artifact_hashes(outputs: list[Path]) -> set[str]:
    """Collect exact PUT hashes already attempted by earlier audit outputs."""
    hashes = set()
    for output in outputs:
        for result in output.glob("artifacts/*/result.json"):
            try:
                digest = json.loads(result.read_text()).get("put_json_sha256")
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(digest, str) and len(digest) == 64:
                hashes.add(digest)
    return hashes


def stress_score(item: dict) -> dict:
    """Return stable structural difficulty features for a PUT artifact."""
    put_json = Path(item["put_json"])
    data = json.loads(put_json.read_text())
    stats = data.get("stats") or {}
    region = data.get("region") or {}
    pins = data.get("pins") or {}
    holes = data.get("holes") or {}
    state_stored = stats.get("state_stored") or []
    expected = item.get("expected_ladder") or []
    workdir = put_json.parent
    source_bytes = 0
    for candidate in (workdir / "src" / "flat.sol", workdir / "flat.sol"):
        if candidate.is_file():
            source_bytes = candidate.stat().st_size
            break
    features = {
        "oracle_count": len(expected),
        "region_dimensions": len(region),
        "pin_dimensions": len(pins),
        "hole_count": sum(len(values) for values in holes.values()
                          if isinstance(values, list)),
        "state_establishments": len(state_stored),
        "path_depth": int(data.get("depth") or 0),
        "source_bytes": source_bytes,
    }
    # Solver pressure is dominated by proof obligations and symbolic/state
    # dimensions. Source size and path depth break ties among similar PUTs.
    score = (features["oracle_count"] * 1_000_000 +
             features["state_establishments"] * 250_000 +
             features["region_dimensions"] * 100_000 +
             features["pin_dimensions"] * 50_000 +
             features["hole_count"] * 10_000 +
             features["path_depth"] * 1_000 +
             min(features["source_bytes"], 999_999))
    return {"score": score, "features": features}


def stress_sample(items: list[dict], size: int,
                  excluded_hashes: set[str]) -> list[dict]:
    """Select the structurally hardest untested PUTs deterministically."""
    ranked = []
    for item in items:
        if item["put_json_sha256"] in excluded_hashes:
            continue
        difficulty = stress_score(item)
        ranked.append({**item, "stress": difficulty})
    ranked.sort(key=lambda item: (-item["stress"]["score"],
                                  item["put_json_sha256"]))
    return ranked[:size]


def certify_spec(assert_spec: dict, put_data: dict) -> dict:
    box = []
    holes = put_data.get("holes") or {}
    state_stored = (put_data.get("stats") or {}).get("state_stored")
    established_state = None
    if isinstance(state_stored, list):
        established_state = {
            entry.split(":=", 1)[0].strip()
            for entry in state_stored
            if isinstance(entry, str) and ":=" in entry
        }
    for name, bounds in (put_data.get("region") or {}).items():
        item = {"name": name, "lo": str(bounds[0]), "hi": str(bounds[1])}
        if holes.get(name):
            item["holes"] = [str(value) for value in holes[name]]
        box.append(item)
    for name, value in (put_data.get("pins") or {}).items():
        if name in (put_data.get("region") or {}):
            continue
        # A retained counterexample can contain state coordinates that the
        # final Foundry PUT explicitly could not establish.  Revalidation must
        # prove the materialized PUT's domain, not those dropped coordinates.
        if (name.startswith("state.") and established_state is not None and
                name not in established_state):
            continue
        box.append({"name": name, "lo": str(value), "hi": str(value)})
    if not any(item["name"] == "msg.value" for item in box):
        # A Foundry call without an explicit `{value: ...}` carries zero wei.
        # Retained structural regions can omit that implicit ABI coordinate,
        # but the proof query must still describe the materialized PUT domain.
        box.append({"name": "msg.value", "lo": "0", "hi": "0"})
    result = {
        "unit": assert_spec["unit"], "enc": assert_spec["enc"],
        "depth": assert_spec.get("depth", 1), "box": box,
    }
    # Stage 4 uses enc=1/depth=0 as a materialization marker for the ABI
    # nonpayable value gate.  In the instrumented CFG the actual rejecting
    # edge is the first false decision: seed 1 becomes enc=2 at depth 1.
    if (put_data.get("stage4_kind") == "abi-value-gate" and
            str(assert_spec.get("enc")) == "1" and
            int(assert_spec.get("depth", 0)) == 0):
        result["enc"] = 2
        result["depth"] = 1
    if put_data.get("establish"):
        result["establish"] = put_data["establish"]
    if put_data.get("_audit_path_guards"):
        result["guards"] = put_data["_audit_path_guards"]
    return result


def resumed_certify_spec(assert_spec: dict, put_data: dict,
                         retained_region: dict) -> dict:
    """Rebuild a retained certificate with its proved current path identity."""
    result = certify_spec(assert_spec, put_data)
    if retained_region.get("remapped"):
        result["enc"] = retained_region["enc"]
        result["depth"] = retained_region["depth"]
    return result


def _strip_balanced_wrapper(text: str) -> str:
    """Remove only parentheses enclosing the complete expression."""
    text = text.strip()
    changed = True
    while changed:
        changed = False
        if text.startswith("(") and text.endswith(")"):
            depth = 0
            closes_at_end = False
            for index, char in enumerate(text):
                depth += char == "("
                depth -= char == ")"
                if depth == 0:
                    closes_at_end = index == len(text) - 1
                    break
            if closes_at_end:
                text = text[1:-1].strip()
                changed = True
                continue
    return text


def _split_top_level(text: str, separator: str) -> list[str]:
    depth = 0
    parts = []
    start = 0
    index = 0
    while index <= len(text) - len(separator):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and text.startswith(separator, index):
            parts.append(text[start:index].strip())
            start = index + len(separator)
            index += len(separator)
            continue
        index += 1
    if parts:
        parts.append(text[start:].strip())
    return parts


def _guard_state_aliases(put_data: dict) -> dict[str, str]:
    names = set()
    for source in (put_data.get("region") or {}, put_data.get("pins") or {}):
        names.update(name for name in source if name.startswith("state."))
    for oracle in (put_data.get("stats") or {}).get("assertion_oracles") or []:
        name = oracle.get("var")
        if isinstance(name, str) and name not in {"return", "exit"} and not name.startswith(
                "return."):
            names.add("state." + name)
    for stored in (put_data.get("stats") or {}).get("state_stored") or []:
        if isinstance(stored, str) and ":=" in stored:
            target = stored.split(":=", 1)[0].strip()
            if target.startswith("state."):
                names.add(target)
    aliases: dict[str, str] = {}
    for name in sorted(names):
        identifier = "_pre_" + re.sub(
            r"[^0-9A-Za-z_]", "_", name[len("state."):]).strip("_")
        if identifier in aliases and aliases[identifier] != name:
            aliases[identifier] = ""
        else:
            aliases[identifier] = name
    return aliases


def _guard_operand(text: str, aliases: dict[str, str], address_names: set[str]) -> dict:
    text = _strip_balanced_wrapper(text)
    casts = []
    while True:
        match = re.fullmatch(r"(uint256|uint160|address)\((.*)\)", text)
        if not match:
            break
        casts.append(match.group(1))
        text = _strip_balanced_wrapper(match.group(2))
    if "uint160" in casts or "address" in casts:
        literal = int(text, 0) if re.fullmatch(
            r"(?:0x[0-9A-Fa-f]+|[0-9]+)", text) else None
        if text not in address_names and (literal is None or literal >= 2**160):
            raise ValueError(
                f"truncating address cast lacks address-typed provenance: {text!r}")
    if re.fullmatch(r"(?:0x[0-9A-Fa-f]+|[0-9]+)", text):
        return {"kind": "literal", "value": str(int(text, 0))}
    if text == "true":
        return {"kind": "literal", "value": "1"}
    if text == "false":
        return {"kind": "literal", "value": "0"}
    if text == "p_msg_sender":
        text = "msg.sender"
    elif text == "p_msg_value":
        text = "msg.value"
    elif text.startswith("_pre_"):
        text = aliases.get(text, "")
    if not text or not re.fullmatch(
            r"(?:[A-Za-z_]\w*|(?:msg|tx|block)\.\w+|state\.[A-Za-z_][\w$]*"
            r"(?:\[[^\[\]]+\])*(?:\.[A-Za-z_][\w$]*)?)", text):
        raise ValueError(f"unsupported materialized path-guard operand: {text!r}")
    return {"kind": "coord", "name": text}


def parse_materialized_guard(expression: str, put_data: dict) -> dict:
    """Parse the deliberately small guard language emitted by Stage 4."""
    aliases = _guard_state_aliases(put_data)
    address_names = set(put_data.get("_audit_guard_address_names") or [])
    expression = _strip_balanced_wrapper(expression)
    disjuncts = _split_top_level(expression, "||") or [expression]
    relations = []
    for disjunct in disjuncts:
        disjunct = _strip_balanced_wrapper(disjunct)
        # Solidity's bool-to-uint rendering is equivalent to the bool itself.
        ternary = re.fullmatch(
            r"\(?\s*([A-Za-z_]\w*)\s*\?\s*uint256\(1\)\s*:\s*uint256\(0\)\s*\)?"
            r"\s*(==|!=)\s*0", disjunct)
        if ternary:
            disjunct = f"{ternary.group(1)} {ternary.group(2)} 0"
        depth = 0
        relation = None
        for index, char in enumerate(disjunct):
            depth += char == "("
            depth -= char == ")"
            if depth != 0:
                continue
            match = re.match(r"(==|!=|<=|>=|<|>)", disjunct[index:])
            if match:
                relation = (disjunct[:index], match.group(1),
                            disjunct[index + len(match.group(1)):])
                break
        if relation is None:
            raise ValueError(
                f"unsupported materialized path-guard expression: {expression!r}")
        lhs, operator, rhs = relation
        relations.append({
            "lhs": _guard_operand(lhs, aliases, address_names),
            "op": operator,
            "rhs": _guard_operand(rhs, aliases, address_names),
        })
    return {"any": relations}


def _materialized_test(text: str, test: str) -> tuple[str, set[str]] | None:
    declarations = list(re.finditer(
        r"\bfunction\s+" + re.escape(test) + r"\s*\((.*?)\)\s+public\s*\{",
        text, re.S))
    if len(declarations) != 1:
        return None
    declaration = declarations[0]
    open_brace = declaration.end() - 1
    depth = 0
    for index in range(open_brace, len(text)):
        depth += text[index] == "{"
        depth -= text[index] == "}"
        if depth == 0:
            params = declaration.group(1)
            addresses = set(re.findall(
                r"\baddress(?:\s+payable)?\s+([A-Za-z_]\w*)", params))
            return text[open_brace + 1:index], addresses
    return None


def materialized_put_source(put_json: Path, put_data: dict,
                            test_file: Path | None = None) -> Path | None:
    """Locate the final PUT without trusting an expired absolute file path."""
    named = Path(put_data.get("file") or "")
    test = str(put_data.get("test") or "")
    if test_file is not None:
        if not test_file.is_file():
            return None
        return (test_file if _materialized_test(
            test_file.read_text(errors="replace"), test) else None)
    if named.is_file():
        return (named if _materialized_test(
            named.read_text(errors="replace"), test) else None)
    if not named.name:
        return None
    for ancestor in list(put_json.parents)[:5]:
        if len(ancestor.parts) < 5:
            continue
        matches = [path for path in ancestor.glob("**/" + named.name)
                   if path.is_file()]
        matching = [path for path in matches if _materialized_test(
            path.read_text(errors="replace"), test)]
        if len(matching) == 1:
            return matching[0]
    return None


def materialized_path_guards(put_json: Path, put_data: dict,
                             test_file: Path | None = None) -> list[dict]:
    """Recover only Stage 4's explicit complete-path guard block."""
    source = materialized_put_source(put_json, put_data, test_file)
    if source is None:
        if (put_data.get("stats") or {}).get("path_guard_assumes"):
            raise ValueError("final PUT source carrying path guards is unavailable")
        return []
    text = source.read_text(errors="replace")
    selected = _materialized_test(text, str(put_data.get("test") or ""))
    if selected is None:
        raise ValueError("final PUT source does not contain one exact test declaration")
    text, address_names = selected
    marker = "// complete-path guard recovered from the emit report"
    expressions = []
    for block in text.split(marker)[1:]:
        for line in block.splitlines()[1:]:
            match = re.fullmatch(r"\s*vm\.assume\((.*)\);\s*", line)
            if not match:
                break
            expressions.append(match.group(1))
    expected = int((put_data.get("stats") or {}).get("path_guard_assumes") or 0)
    if len(expressions) != expected:
        raise ValueError(
            f"materialized path-guard count mismatch: metadata={expected}, source={len(expressions)}")
    parse_data = {**put_data, "_audit_guard_address_names": sorted(address_names)}
    return [parse_materialized_guard(expression, parse_data)
            for expression in expressions]


def assert_term_text(term: dict) -> str:
    """Render one structured assertion term exactly as path coverage does."""
    kind = term.get("kind")
    if kind == "pre":
        return "pre"
    if kind == "coord":
        return str(term.get("name"))
    if kind == "literal":
        return str(term.get("value"))
    if kind == "op":
        operator = {"add": "+", "sub": "-", "mul": "*", "div": "/"}.get(
            term.get("op"))
        if operator is not None:
            return (f"({assert_term_text(term.get('lhs') or {})} {operator} "
                    f"{assert_term_text(term.get('rhs') or {})})")
    return ""


def final_assert_spec(assert_spec: dict, expected_oracles: list[dict]) -> dict | None:
    """Keep only variables used by assertions emitted in the final PUT.

    A variable spec contains structured candidates whose rendered text is owned
    by ESBMC. Retain only candidates rendered into the final PUT, and mark the
    resulting query exact. Variables absent from the final test and superseded
    candidates for retained variables must not consume revalidation time.
    """
    expected_vars = {
        str(oracle.get("var")) for oracle in expected_oracles
        if oracle.get("layer") != "exit" and oracle.get("var") is not None
    }
    variables = []
    for variable in assert_spec.get("vars") or []:
        name = str(variable.get("name"))
        if name not in expected_vars:
            continue
        texts = {
            str(oracle.get("text")) for oracle in expected_oracles
            if str(oracle.get("var")) == name
        }
        if set(variable) == {"name"}:
            if "post == pre" in texts:
                variable = {
                    "name": name,
                    "equals": [{"id": "rq1pre", "term": {"kind": "pre"}}],
                    "abs": [],
                    "deltas": [],
                }
            elif not texts.intersection({
                    "post != pre", "post >= pre", "post <= pre",
                    "post > pre", "post < pre",
            }):
                # The generic source ladder contains no final R1 obligation
                # for this variable. Its retained R2 spec, if any, is handled
                # by a later authoritative batch.
                continue
        else:
            subject = name if name.startswith("return") else "post"
            equals = []
            for candidate in variable.get("equals") or []:
                rendered_term = assert_term_text(candidate.get("term") or {})
                spellings = {f"{subject} == {rendered_term}"}
                if rendered_term in {"0", "1"}:
                    spellings.add(
                        f"{subject} == {'true' if rendered_term == '1' else 'false'}")
                if spellings.intersection(texts):
                    equals.append(candidate)
            existing_equalities = {
                assert_term_text(candidate.get("term") or {}) for candidate in equals
            }
            for text in sorted(texts):
                match = re.fullmatch(
                    re.escape(subject) + r" == (true|false|-?[0-9]+)", text)
                if match is None:
                    continue
                value = {"true": "1", "false": "0"}.get(
                    match.group(1), match.group(1))
                if value in existing_equalities:
                    continue
                equals.append({
                    "id": "rq1final" + str(len(equals)),
                    "term": {"kind": "literal", "value": value},
                })
                existing_equalities.add(value)
            absolute = [
                candidate for candidate in variable.get("abs") or []
                if (f"{subject} in [{assert_term_text(candidate.get('lo') or {})}, "
                    f"{assert_term_text(candidate.get('hi') or {})}]") in texts
            ]
            deltas = []
            for candidate in variable.get("deltas") or []:
                increasing = candidate.get("dir") == "inc"
                rendered = (
                    ("post - pre in [" if increasing else "pre - post in [") +
                    assert_term_text(candidate.get("lo") or {}) + ", " +
                    assert_term_text(candidate.get("hi") or {}) + "] with " +
                    ("post >= pre" if increasing else "pre >= post"))
                if rendered in texts:
                    deltas.append(candidate)
            variable = {
                **variable, "equals": equals, "abs": absolute, "deltas": deltas
            }
            if not equals and not absolute and not deltas:
                continue
        variables.append(variable)
    if not variables:
        return None
    return {
        **assert_spec,
        "candidate_policy": "exact",
        "vars": variables,
    }


def ordered_assert_specs(paths: list[Path]) -> list[Path]:
    """Try the authoritative base ladder before optional R2 search batches."""
    return sorted(paths, key=lambda path: (path.name != "spec.json", path.name))


def assertion_var_chunks(variables: list[dict]) -> list[list[dict]]:
    """Keep generic and synthesized frame ladders single-variable."""
    expensive_frame = any(
        set(variable) == {"name"} or any(
            equality.get("id") == "rq1pre"
            for equality in variable.get("equals") or [])
        for variable in variables)
    chunk_size = 1 if expensive_frame else 5
    return [variables[start:start + chunk_size]
            for start in range(0, len(variables), chunk_size)]


def verdict(output: str, returncode: int, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if re.search(
            r"REFUSING THE QUERY: path enc=\d+ is not among this unit's "
            r"\d+ enumerated path\(s\)", output):
        return "stale-path"
    certify = re.findall(r"--path-cov-certify: RESULT: ([A-Z][A-Z-]*)", output)
    if certify:
        return {
            "CERTIFIED": "proved", "REFUTED": "refuted",
            "VACUOUS": "inconclusive", "UNDECIDED": "inconclusive",
            "UNDECIDED-TRUNCATED": "inconclusive",
        }.get(certify[-1], "error")
    ladder = re.findall(
        r"ladder summary -- \d+ candidate\(s\): (\d+) HOLDS, (\d+) REFUTED, "
        r"(\d+) no verdict \(solver unknown\), (\d+) no verdict", output)
    if ladder:
        holds, refuted, unknown, unreached = map(int, ladder[-1])
        if refuted:
            return "refuted"
        if unknown or unreached:
            return "inconclusive"
        return "proved" if holds else "inconclusive"
    # Query refusals describe the possible RESULT values in their diagnostic.
    # They are not solver verdicts and must remain fail-closed.
    if "REFUSING THE QUERY" in output or "ERROR:" in output:
        return "error"
    if re.search(r"(?m)^VERIFICATION FAILED$", output):
        return "refuted"
    if re.search(r"(?m)^VERIFICATION SUCCESSFUL$", output):
        return "proved"
    if re.search(r"(?m)^VERIFICATION UNKNOWN$", output):
        return "inconclusive"
    if returncode < 0 or "Out of memory" in output or "bad_alloc" in output:
        return "oom-or-killed"
    return "error"


def certification_needs_remap(status: str, log: Path) -> bool:
    """Retry current path identities when the retained identity is unusable."""
    if status == "stale-path":
        return True
    if status != "inconclusive" or not log.is_file():
        return False
    outcomes = re.findall(
        r"--path-cov-certify: RESULT: ([A-Z][A-Z-]*)",
        log.read_text(errors="replace"))
    return bool(outcomes and outcomes[-1] == "VACUOUS")


def run_query(command: list[str], cwd: Path, log: Path, timeout: int) -> dict:
    started = time.time()
    timed_out = False
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, errors="replace",
                               start_new_session=True)
    with PROCESS_LOCK:
        ACTIVE_PROCESSES.add(process)
    try:
        output, _ = process.communicate(timeout=timeout)
        returncode = process.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            tail, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            tail, _ = process.communicate()
        def decoded(value: str | bytes | None) -> str:
            if isinstance(value, bytes):
                return value.decode(errors="replace")
            return value or ""

        output = decoded(exc.stdout) + decoded(tail) + "\nHARD TIMEOUT\n"
        returncode = 124
    finally:
        with PROCESS_LOCK:
            ACTIVE_PROCESSES.discard(process)
    log.write_text(output)
    report = cwd / "cov-report.json"
    retained_report = log.with_suffix(".report.json")
    if report.is_file():
        retained_report.write_bytes(report.read_bytes())
    return {
        "command": command, "returncode": returncode,
        "elapsed_seconds": round(time.time() - started, 3),
        "status": verdict(output, returncode, timed_out),
        "log": str(log),
        "report": str(retained_report) if retained_report.is_file() else None,
    }


def current_path_candidates(base: list[str], item: dict, destination: Path,
                            timeout: int) -> tuple[list[tuple[int, int]], dict]:
    """Extract current exact path identities from the instrumented GOTO."""
    query = run_query(base + ["--goto-functions-only"], destination,
                      destination / "current-paths.log", timeout)
    output = Path(query["log"]).read_text(errors="replace")
    unit = re.escape(item["path_function"])
    matches = re.findall(
        rf"ASSERT path_tr\$\d+ != (\d+) \|\| path_cnt\$\d+ != (\d+) "
        rf"// {unit}:path:\d+", output)
    candidates = sorted({(int(enc), int(depth)) for enc, depth in matches},
                        key=lambda pair: (pair[1], pair[0]))
    return candidates, query


def remap_stale_path(base: list[str], item: dict, put_data: dict,
                     primary: dict, destination: Path,
                     timeout: int) -> tuple[dict | None, list[dict]]:
    """Find the unique current path certified by the retained PUT region."""
    candidates, discovery = current_path_candidates(
        base, item, destination, timeout)
    attempts = [{"kind": "path-discovery", **discovery,
                 "candidates": [{"enc": enc, "depth": depth}
                                for enc, depth in candidates]}]
    certified = []
    expected_exit = item.get("exit_kind")
    for index, (enc, depth) in enumerate(candidates):
        candidate_spec = certify_spec(primary, put_data)
        candidate_spec["enc"] = enc
        candidate_spec["depth"] = depth
        path = destination / f"certify-remap-{index}.json"
        atomic_json(path, candidate_spec)
        query = run_query(base + ["--path-cov-certify", str(path)],
                          destination,
                          destination / f"certify-remap-{index}.log", timeout)
        query["path_exit_kind"] = report_path_exit_kind(
            Path(query.get("report") or ""), str(enc))
        attempts.append({"kind": "path-remap", "enc": enc,
                         "depth": depth, **query})
        exit_compatible = (
            expected_exit not in {"normal", "revert"} or
            query["path_exit_kind"] == expected_exit)
        if query["status"] == "proved" and exit_compatible:
            certified.append(candidate_spec)
    return (certified[0] if len(certified) == 1 else None), attempts


def report_path_exit_kind(report: Path, enc: str) -> str | None:
    """Read the certified path's own exit kind from its non-vacuity row."""
    if not report.is_file():
        return None
    try:
        claims = json.loads(report.read_text()).get("claims") or []
    except (OSError, json.JSONDecodeError):
        return None
    expected_id = f"{enc}#nonvacuous"
    kinds = {
        str(claim.get("exit_kind"))
        for claim in claims
        if str(claim.get("path_id")) == expected_id
        and claim.get("exit_kind") in {"normal", "revert", "undetermined"}
    }
    return next(iter(kinds)) if len(kinds) == 1 else None


def certify_path_enc(log: Path, fallback: str) -> str:
    """Return the current path id after any historical-path remapping."""
    spec = log.parent / "certify.json"
    try:
        enc = json.loads(spec.read_text()).get("enc")
    except (OSError, json.JSONDecodeError):
        return fallback
    return str(enc) if enc is not None else fallback


def pinned_entry_constant(item: dict, variable: str) -> int | None:
    """Return an exact materialized entry pin for a state oracle variable."""
    try:
        put_data = json.loads(Path(item["put_json"]).read_text())
    except (KeyError, OSError, json.JSONDecodeError):
        return None
    name = f"state.{variable}"
    value = (put_data.get("pins") or {}).get(name)
    if value is None:
        interval = (put_data.get("region") or {}).get(name)
        if isinstance(interval, list) and len(interval) == 2 and (
                str(interval[0]) == str(interval[1])):
            value = interval[0]
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def classify_obligation(item: dict, queries: list[dict]) -> tuple[str, list[dict]]:
    """Classify only the region and final PUT oracles, never rejected candidates."""
    if not queries:
        return "error", []
    region_status = queries[0].get("status")
    observed = []
    assertion_output = "\n".join(
        Path(query["log"]).read_text(errors="replace")
        for query in queries[1:] if Path(query.get("log") or "").is_file())
    for expected in item.get("expected_ladder") or []:
        if expected.get("layer") == "exit":
            expected_exit = {
                "path exits normally": "normal",
                "path exits through revert": "revert",
            }.get(expected.get("text"))
            observed_exit = queries[0].get("path_exit_kind")
            if region_status != "proved" or expected_exit is None:
                status = "error"
            elif observed_exit == expected_exit:
                status = "proved"
            elif observed_exit in {"normal", "revert"}:
                status = "refuted"
            else:
                status = "inconclusive"
            observed.append({**expected, "status": status})
            continue
        pattern = (r"(?m)^--path-cov-assert:\s+" +
                   re.escape(str(expected.get("var") or "")) + r":\s*" +
                   re.escape(str(expected.get("text") or "")) +
                   r"\s+(HOLDS|REFUTED|UNDECIDED)")
        matches = re.findall(pattern, assertion_output)
        status = ({"HOLDS": "proved", "REFUTED": "refuted",
                   "UNDECIDED": "inconclusive"}.get(matches[-1])
                  if matches else "inconclusive")
        proof_basis = None
        if status == "inconclusive" and expected.get("text") == (
                f"{expected.get('var')} != 0"):
            equality_pattern = (
                r"(?m)^--path-cov-assert:\s+" +
                re.escape(str(expected.get("var") or "")) + r":\s*" +
                re.escape(str(expected.get("var") or "")) +
                r" == (-?[0-9]+)\s+HOLDS")
            proved_constants = re.findall(equality_pattern, assertion_output)
            nonzero = next(
                (value for value in reversed(proved_constants) if int(value) != 0),
                None)
            if nonzero is not None:
                status = "proved"
                proof_basis = (
                    f"implied by {expected.get('var')} == {nonzero} HOLDS")
        relation = {
            "post > pre": lambda post, pre: post > pre,
            "post >= pre": lambda post, pre: post >= pre,
            "post < pre": lambda post, pre: post < pre,
            "post <= pre": lambda post, pre: post <= pre,
            "post != pre": lambda post, pre: post != pre,
        }.get(str(expected.get("text")))
        if status == "inconclusive" and relation is not None:
            variable = str(expected.get("var") or "")
            equality_pattern = (
                r"(?m)^--path-cov-assert:\s+" + re.escape(variable) +
                r":\s*post == (-?[0-9]+)\s+HOLDS")
            proved_constants = re.findall(equality_pattern, assertion_output)
            entry = pinned_entry_constant(item, variable)
            if proved_constants and entry is not None:
                post = int(proved_constants[-1])
                if relation(post, entry):
                    status = "proved"
                    proof_basis = (
                        f"implied by post == {post} HOLDS and entry pin "
                        f"state.{variable} == {entry}")
        result = {**expected, "status": status}
        if proof_basis is not None:
            result["proof_basis"] = proof_basis
        observed.append(result)
    statuses = [region_status, *(entry["status"] for entry in observed)]
    if "stale-path" in statuses:
        return "stale-path", observed
    if "refuted" in statuses:
        return "refuted", observed
    if "timeout" in statuses:
        return "timeout", observed
    if "error" in statuses or "oom-or-killed" in statuses:
        return "error", observed
    if all(status == "proved" for status in statuses):
        return "proved", observed
    return "inconclusive", observed


def refresh_result(item: dict, retained: dict) -> dict:
    queries = retained.get("queries") or []
    for query in queries:
        log = Path(query.get("log") or "")
        if not log.is_file():
            query["status"] = "error"
            continue
        query["status"] = verdict(log.read_text(errors="replace"),
                                  int(query.get("returncode") or 0),
                                  query.get("status") == "timeout")
        if query.get("path_exit_kind") not in {"normal", "revert", "undetermined"}:
            query["path_exit_kind"] = report_path_exit_kind(
                Path(query.get("report") or ""),
                certify_path_enc(log, item["enc"]))
    status, observed = classify_obligation(item, queries)
    return {**retained, **item, "status": status,
            "final_oracle_results": observed}


def path_guard_digest(guards: list[dict]) -> str:
    canonical = json.dumps(guards, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def guard_cache_valid(result: dict, digest: str) -> bool:
    """Old terminal results proved a wider, guard-free domain and are stale."""
    return (result.get("guard_semantics") == GUARD_SEMANTICS and
            result.get("path_guard_digest") == digest)


def run_item(item: dict, output: Path, esbmc: Path, memlimit: str,
             timeout: int, resume: bool) -> dict:
    artifact_id = item["put_json_sha256"][:16]
    destination = output / "artifacts" / artifact_id
    result_file = destination / "result.json"
    destination.mkdir(parents=True, exist_ok=True)
    put_json = Path(item["put_json"])
    put_data = json.loads(put_json.read_text())
    try:
        put_data["_audit_path_guards"] = materialized_path_guards(
            put_json, put_data, Path(item["test_file"]) if item.get("test_file") else None)
    except ValueError as exc:
        result = {
            **item,
            "status": "error",
            "queries": [],
            "guard_semantics": GUARD_SEMANTICS,
            "guard_recovery_error": str(exc),
        }
        atomic_json(result_file, result)
        return result
    guard_digest = path_guard_digest(put_data["_audit_path_guards"])
    retained_region = None
    if resume and result_file.is_file():
        retained = json.loads(result_file.read_text())
        retained = refresh_result(item, retained)
        atomic_json(result_file, retained)
        cache_valid = guard_cache_valid(retained, guard_digest)
        if cache_valid and retained.get("status") in {"proved", "refuted"}:
            return retained
        retained_queries = retained.get("queries") or []
        retained_certify = destination / "certify.json"
        if (
                cache_valid and retained.get("status") == "inconclusive" and
                retained_queries and
                retained_queries[0].get("status") == "proved" and
                retained_certify.is_file()):
            retained_region = retained_queries[0]
    workdir = put_json.parent
    old_command = retained_command(workdir)
    specs = ordered_assert_specs(list((workdir / "assert").glob("spec*.json")))
    if old_command is None or not specs:
        result = {
            **item, "status": "missing-evidence", "queries": [],
            "guard_semantics": GUARD_SEMANTICS,
            "path_guard_digest": guard_digest,
        }
        atomic_json(result_file, result)
        return result
    base = clean_command(old_command, esbmc, memlimit)
    base = materialize_fixtures(base, output)
    base = materialize_ast(base, output, Path(item["subject_dir"]).name, put_json)
    primary = json.loads(specs[0].read_text())
    local_certify = destination / "certify.json"
    if retained_region is not None:
        if retained_region.get("remapped"):
            atomic_json(local_certify, resumed_certify_spec(
                primary, put_data, retained_region))
        elif not local_certify.is_file():
            atomic_json(local_certify, certify_spec(primary, put_data))
        queries = [retained_region]
        region_status = "proved"
    else:
        atomic_json(local_certify, certify_spec(primary, put_data))
        queries = []
        command = base + ["--path-cov-certify", str(local_certify)]
        queries.append(
            run_query(command, destination, destination / "certify.log", timeout))
        queries[0]["path_exit_kind"] = report_path_exit_kind(
            Path(queries[0].get("report") or ""),
            str(json.loads(local_certify.read_text())["enc"]))
        region_status = queries[0]["status"]
    primary_log = Path(queries[0].get("log") or "")
    if certification_needs_remap(region_status, primary_log):
        remapped, attempts = remap_stale_path(
            base, item, put_data, primary, destination, timeout)
        queries.extend(attempts)
        if remapped is not None:
            atomic_json(local_certify, remapped)
            successful = next(
                attempt for attempt in attempts
                if attempt.get("status") == "proved" and
                attempt.get("enc") == remapped["enc"] and
                attempt.get("depth") == remapped["depth"])
            queries[0] = {
                **successful,
                "historical_enc": item["enc"],
                "historical_depth": primary.get("depth"),
                "remapped": True,
            }
            region_status = "proved"
    assertion_oracles = [
        oracle for oracle in item.get("expected_ladder") or []
        if oracle.get("layer") != "exit"
    ]
    if region_status == "proved" and assertion_oracles:
        assertion_index = 0
        for index, source_spec in enumerate(specs):
            filtered_spec = final_assert_spec(
                json.loads(source_spec.read_text()), assertion_oracles)
            if filtered_spec is None:
                continue
            certified_identity = json.loads(local_certify.read_text())
            filtered_spec["enc"] = certified_identity["enc"]
            filtered_spec["depth"] = certified_identity["depth"]
            # A monolithic automatic ladder grows by every generic candidate
            # for every state variable. Use small chunks: this bounds solver
            # work while amortizing Solidity frontend and GOTO construction.
            variables = filtered_spec["vars"]
            chunks = assertion_var_chunks(variables)
            for variable_chunk in chunks:
                single_spec = {**filtered_spec, "vars": variable_chunk}
                if all(set(variable) == {"name"} for variable in variable_chunk):
                    # Name-only specs request ESBMC's generic comparison ladder.
                    # candidate_policy=exact deliberately suppresses that ladder.
                    single_spec.pop("candidate_policy", None)
                if put_data["_audit_path_guards"]:
                    single_spec["guards"] = put_data["_audit_path_guards"]
                local_spec = destination / f"assert-{assertion_index}.json"
                atomic_json(local_spec, single_spec)
                command = base + ["--path-cov-assert", str(local_spec)]
                queries.append(
                    run_query(command, destination,
                              destination / f"assert-{assertion_index}.log",
                              timeout))
                assertion_index += 1
                if queries[-1]["status"] in {
                        "timeout", "error", "oom-or-killed", "stale-path"
                }:
                    break
                partial_status, _ = classify_obligation(item, queries)
                if partial_status in {"proved", "refuted"}:
                    break
            if queries[-1]["status"] in {
                    "timeout", "error", "oom-or-killed", "stale-path"
            }:
                break
            partial_status, _ = classify_obligation(item, queries)
            if partial_status in {"proved", "refuted"}:
                break
    overall, observed = classify_obligation(item, queries)
    result = {**item, "status": overall, "queries": queries,
              "guard_semantics": GUARD_SEMANTICS,
              "path_guard_digest": guard_digest,
              "final_oracle_results": observed}
    atomic_json(result_file, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--esbmc", type=Path, default=DEFAULT_ESBMC)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--stress-size", type=int)
    parser.add_argument("--exclude-results", type=Path, action="append", default=[])
    parser.add_argument("--artifact-sha", help="Run exactly one PUT JSON SHA-256 prefix")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--memlimit", default="4g")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    items = inventory(args.result_root)
    atomic_json(args.output / "inventory.json", {
        "schema": "veriput-rq1-put-kinduction-inventory/v1",
        "scope": "canonical-current physical valid PUT artifacts",
        "count": len(items), "items": items,
    })
    if args.artifact_sha:
        selected = [item for item in items
                    if item["put_json_sha256"].startswith(args.artifact_sha)]
        if len(selected) != 1:
            parser.error(f"--artifact-sha matched {len(selected)} inventory items")
    elif args.stress_size:
        excluded = completed_artifact_hashes(args.exclude_results)
        selected = stress_sample(items, args.stress_size, excluded)
        atomic_json(args.output / f"stress-{args.stress_size}.json", {
            "schema": "veriput-rq1-put-kinduction-stress-sample/v1",
            "population_count": len(items), "excluded_count": len(excluded),
            "sample_count": len(selected),
            "selection": ("descending structural difficulty: final oracle count, "
                          "state establishments, region/pin/hole dimensions, "
                          "path depth, source bytes; PUT SHA tie-break"),
            "items": selected,
        })
    elif args.sample_size:
        selected = stratified_sample(items, args.sample_size)
        atomic_json(args.output / f"sample-{args.sample_size}.json", {
            "schema": "veriput-rq1-put-kinduction-sample/v1",
            "population_count": len(items), "sample_count": len(selected),
            "selection": "dataset/oracle-classes/exit-kind round-robin; stable SHA ordering",
            "items": selected,
        })
    else:
        selected = items[:args.limit] if args.limit else items
    if args.inventory_only:
        print(json.dumps({"population_count": len(items), "selected_count": len(selected)},
                         sort_keys=True))
        return 0
    results = []
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs)
    futures = [executor.submit(run_item, item, args.output, args.esbmc,
                               args.memlimit, args.timeout, not args.no_resume)
               for item in selected]
    try:
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            results.append(result)
            print(f"[{completed}/{len(selected)}] {result['status']} {result['case']} "
                  f"{result['unit']} enc={result['enc']}", flush=True)
    except KeyboardInterrupt:
        for future in futures:
            future.cancel()
        with PROCESS_LOCK:
            processes = list(ACTIVE_PROCESSES)
        for process in processes:
            os.killpg(process.pid, signal.SIGTERM)
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    executor.shutdown()
    counts = {status: sum(result["status"] == status for result in results)
              for status in sorted({result["status"] for result in results})}
    atomic_json(args.output / "summary.json", {
        "schema": "veriput-rq1-put-kinduction-summary/v1",
        "inventory_count": len(items), "selected_count": len(selected),
        "completed_count": len(results), "status_counts": counts,
        "isolated": True, "rq1_modified": False,
    })
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
