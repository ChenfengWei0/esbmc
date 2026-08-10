#!/usr/bin/env python3
"""Stop local/remote RQ1 workers and report exact remaining processes."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


PATTERN = (
    r"[r]q1_veriput_run.py|[r]q1_local_pump.py|[r]q1_remote_pump.py|"
    r"[r]q1_local_supervisor.py|[c]ertify_all.py|[p]ut_all.py|"
    r"[s]olidity_path_put.py|[s]olidity_path_generalise.py|"
    r"[b]uild/src/esbmc/esbmc|[/ ]esbmc( |$)|[f]orge( |$)|[a]nvil( |$)"
)


def run(cmd: list[str], timeout: int = 12) -> dict:
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "timeout",
        }


def meminfo() -> dict:
    values = {}
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
    except OSError:
        return {}
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            try:
                values[parts[0][:-1]] = int(int(parts[1]) / 1024)
            except ValueError:
                pass
    total = values.get("MemTotal", 0)
    free = values.get("MemFree", 0)
    cache = values.get("Cached", 0) + values.get("SReclaimable", 0)
    buffers = values.get("Buffers", 0)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "总内存GiB": round(total / 1024, 3),
        "可用内存GiB": round(values.get("MemAvailable", 0) / 1024, 3),
        "buff_cacheGiB": round((cache + buffers) / 1024, 3),
        "进程占用估算GiB": round(max(0, total - free - cache - buffers) / 1024, 3),
        "swap占用GiB": round(max(0, swap_total - swap_free) / 1024, 3),
        "解释": "buff/cache 是可回收缓存，不等于 RQ1/ESBMC worker 仍在运行",
    }


def process_list() -> list[str]:
    proc = run(["pgrep", "-af", PATTERN])
    return [line for line in proc["stdout"].splitlines() if line.strip()]


def stop_local() -> dict:
    before = process_list()
    run(["pkill", "-TERM", "-f", PATTERN])
    time.sleep(2)
    run(["pkill", "-KILL", "-f", PATTERN])
    after = process_list()
    return {
        "停止前进程数": len(before),
        "停止后进程数": len(after),
        "停止前进程": before[:50],
        "停止后残留": after[:50],
        "内存": meminfo(),
    }


def remote_script() -> str:
    return f"""
set -u
pattern='{PATTERN}'
before="$(pgrep -af "$pattern" 2>/dev/null || true)"
pkill -TERM -f "$pattern" 2>/dev/null || true
sleep 2
pkill -KILL -f "$pattern" 2>/dev/null || true
after="$(pgrep -af "$pattern" 2>/dev/null || true)"
python3 - <<'PY'
import json
from pathlib import Path
values = {{}}
try:
    lines = Path('/proc/meminfo').read_text().splitlines()
except OSError:
    lines = []
for line in lines:
    parts = line.split()
    if len(parts) >= 2 and parts[0].endswith(':'):
        try:
            values[parts[0][:-1]] = int(int(parts[1]) / 1024)
        except ValueError:
            pass
total = values.get('MemTotal', 0)
free = values.get('MemFree', 0)
cache = values.get('Cached', 0) + values.get('SReclaimable', 0)
buffers = values.get('Buffers', 0)
swap_total = values.get('SwapTotal', 0)
swap_free = values.get('SwapFree', 0)
print(json.dumps({{
    '总内存GiB': round(total / 1024, 3),
    '可用内存GiB': round(values.get('MemAvailable', 0) / 1024, 3),
    'buff_cacheGiB': round((cache + buffers) / 1024, 3),
    '进程占用估算GiB': round(max(0, total - free - cache - buffers) / 1024, 3),
    'swap占用GiB': round(max(0, swap_total - swap_free) / 1024, 3),
    '解释': 'buff/cache 是可回收缓存，不等于 RQ1/ESBMC worker 仍在运行',
}}, ensure_ascii=False, sort_keys=True))
PY
printf '%s\\n' "$before" > /tmp/veriput_stop_before.txt
printf '%s\\n' "$after" > /tmp/veriput_stop_after.txt
python3 - <<'PY'
import json
from pathlib import Path
before = [line for line in Path('/tmp/veriput_stop_before.txt').read_text(errors='replace').splitlines() if line.strip()]
after = [line for line in Path('/tmp/veriput_stop_after.txt').read_text(errors='replace').splitlines() if line.strip()]
print(json.dumps({{
    '停止前进程数': len(before),
    '停止后进程数': len(after),
    '停止前进程': before[:50],
    '停止后残留': after[:50],
}}, ensure_ascii=False, sort_keys=True))
PY
"""


def stop_remote(host: str) -> dict:
    proc = run([
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        host,
        remote_script(),
    ], timeout=20)
    decoded = []
    for line in proc["stdout"].splitlines():
        try:
            decoded.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return {
        "host": host,
        "returncode": proc["returncode"],
        "stderr_tail": proc["stderr"][-1000:],
        "records": decoded,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="invmut-w2")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--remote-only", action="store_true")
    args = parser.parse_args()
    report = {
        "schema": "veriput-rq1-worker-stop/v1",
        "说明": "强制停止 RQ1/ESBMC/Forge worker；不会运行任何 benchmark",
    }
    if not args.remote_only:
        report["本机"] = stop_local()
    if not args.local_only:
        report["远程"] = stop_remote(args.host)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    local_left = (report.get("本机") or {}).get("停止后进程数", 0)
    remote_left = 0
    for record in (report.get("远程") or {}).get("records", []):
        if "停止后进程数" in record:
            remote_left = int(record.get("停止后进程数") or 0)
    return 1 if local_left or remote_left else 0


if __name__ == "__main__":
    raise SystemExit(main())
