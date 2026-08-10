#!/usr/bin/env python3
"""Ingest a completed RQ1 review notification.

The chat host delivers subagent review completions outside the repository.  This
script is the deterministic boundary: paste the notification JSON into stdin and
it validates the required review fields, updates the subagent ledger, optionally
commits accepted patches, and prints the fixed Chinese report format.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ORCHESTRATOR = HERE / "rq1_subagent_orchestrator.py"
REQUIRED_FIELDS = (
    "changed_code",
    "prior_failure",
    "correctness_argument",
    "verdict",
    "theory_delta",
    "commit decision",
    "next_action",
)


def _read_json_stdin() -> dict:
    raw = sys.stdin.read()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"输入不是合法 JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SystemExit("输入 JSON 必须是对象")
    return doc


def _status_text(doc: dict) -> str:
    status = doc.get("status")
    if isinstance(status, dict):
        text = status.get("completed") or status.get("error") or ""
    else:
        text = str(status or "")
    if not text:
        raise SystemExit("通知里没有 status.completed 文本")
    return str(text)


def _field(text: str, name: str) -> str:
    labels = "|".join(re.escape(label) for label in REQUIRED_FIELDS)
    pattern = re.compile(
        rf"(?im)^\s*{re.escape(name)}\s*[：:]\s*(.*?)(?=^\s*(?:{labels})\s*[：:]|\Z)",
        re.S,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _verdict(text: str) -> str:
    value = _field(text, "verdict").lower()
    if "needs-work" in value or "needs_work" in value or "返工" in value:
        return "needs-work"
    if "reject" in value or "拒绝" in value:
        return "rejected"
    if "accept" in value or "通过" in value:
        return "accepted"
    raise SystemExit("review 缺少可解析 verdict: accepted/needs-work/rejected")


def _changed_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"`([^`]+\.(?:py|cpp|h|c))`", text):
        path = match.group(1)
        if path not in paths:
            paths.append(path)
    return paths


def _agent_id(doc: dict, args: argparse.Namespace) -> str:
    if args.reviewed_agent_id:
        return args.reviewed_agent_id
    value = str(doc.get("reviewed_agent_id") or doc.get("target_agent_id") or "")
    if value:
        return value
    raise SystemExit("--reviewed-agent-id 必填；通知只给了 reviewer agent_path")


def _reviewer_id(doc: dict, args: argparse.Namespace) -> str:
    return args.reviewer_id or str(doc.get("agent_path") or "reviewer-unknown")


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _auto_commit(paths: list[str], message: str) -> str:
    existing = [path for path in paths if Path(path).exists()]
    if not existing:
        raise SystemExit("accepted review 要自动 commit，但没有可提交路径")
    add = _git(["add", "--", *existing])
    if add.returncode != 0:
        raise SystemExit(add.stderr.strip() or add.stdout.strip())
    commit = _git(["commit", "-m", message])
    if commit.returncode != 0:
        raise SystemExit(commit.stderr.strip() or commit.stdout.strip())
    sha = _git(["rev-parse", "HEAD"])
    if sha.returncode != 0:
        raise SystemExit(sha.stderr.strip() or sha.stdout.strip())
    return sha.stdout.strip()


def _record_review(agent_id: str, reviewer_id: str, verdict: str, note: str,
                   commit_sha: str) -> None:
    cmd = [
        sys.executable,
        str(ORCHESTRATOR),
        "review",
        "--agent-id",
        agent_id,
        "--reviewer-id",
        reviewer_id,
        "--verdict",
        verdict,
        "--note",
        note,
    ]
    if commit_sha:
        cmd += ["--commit-sha", commit_sha]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reviewed-agent-id")
    parser.add_argument("--reviewer-id")
    parser.add_argument("--patch-id", default="")
    parser.add_argument("--commit-message", default="")
    parser.add_argument("--auto-commit", action="store_true")
    args = parser.parse_args()

    doc = _read_json_stdin()
    text = _status_text(doc)
    values = {name: _field(text, name) for name in REQUIRED_FIELDS}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SystemExit("review 通知缺字段，禁止入账: " + ",".join(missing))

    verdict = _verdict(text)
    if verdict == "accepted" and "0" not in values["theory_delta"]:
        pass

    changed_paths = _changed_paths(values["changed_code"] + "\n" + text)
    commit_sha = ""
    if verdict == "accepted" and args.auto_commit:
        subject = args.commit_message or f"[scripts] Integrate {args.patch_id or 'RQ1 patch'}"
        commit_sha = _auto_commit(changed_paths, subject)
    elif verdict == "accepted":
        commit_sha = str(doc.get("commit_sha") or "").strip()
        if not commit_sha:
            raise SystemExit("accepted review 必须带 commit_sha 或使用 --auto-commit")

    agent_id = _agent_id(doc, args)
    reviewer_id = _reviewer_id(doc, args)
    compact_note = (
        f"changed_code={values['changed_code'][:500]} | "
        f"prior_failure={values['prior_failure'][:500]} | "
        f"correctness_argument={values['correctness_argument'][:700]} | "
        f"theory_delta={values['theory_delta'][:120]} | "
        f"next_action={values['next_action'][:240]}"
    )
    _record_review(agent_id, reviewer_id, verdict, compact_note, commit_sha)

    print("RQ1 review入账报告:")
    print(f"  被review_agent={agent_id}")
    print(f"  reviewer={reviewer_id}")
    print(f"  patch_id={args.patch_id}")
    print(f"  verdict={verdict}")
    print(f"  改了什么={values['changed_code']}")
    print(f"  为什么改={values['prior_failure']}")
    print(f"  是否正确={values['correctness_argument']}")
    print(f"  理论delta={values['theory_delta']}")
    print(f"  提交决定={values['commit decision']}")
    print(f"  下一步={values['next_action']}")
    print(f"  自动commit={'yes' if commit_sha else 'no'}")
    if commit_sha:
        print(f"  commit={commit_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
