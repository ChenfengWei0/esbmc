"""ESBMC subprocess driver with the hard wrapper required by
feedback_esbmc_unwind.md (ulimit + timeout), plus oracle extraction
via `--dump-violation-info`.

Every run writes its violation info to a per-iteration JSON file; the
caller decides whether the reported oracle matches the target.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from oracle import Oracle, ViolationInfo


@dataclass
class RunResult:
    ok: bool               # process exited (not timed out) and produced output
    returncode: int
    stdout: str
    stderr: str
    wall_sec: float
    oracle: Optional[Oracle]        # extracted from dump JSON; None if no violation
    info: Optional[ViolationInfo]   # full parsed dump; None if no violation


class ESBMCDriver:
    def __init__(
        self,
        binary: str,
        base_flags: List[str],
        *,
        wall_timeout_sec: int = 600,
        cpu_seconds: int = 540,
        virtual_memory_kb: int = 4_000_000,
    ) -> None:
        self.binary = binary
        self.base_flags = list(base_flags)
        self.wall_timeout_sec = wall_timeout_sec
        self.cpu_seconds = cpu_seconds
        self.virtual_memory_kb = virtual_memory_kb

    def run(
        self,
        sources: List[Path],
        extra_flags: Optional[List[str]] = None,
        *,
        violation_info_path: Optional[Path] = None,
    ) -> RunResult:
        """Invoke ESBMC under a ulimit+timeout wrapper.

        Returns a RunResult whose `oracle` is extracted from the
        `--dump-violation-info` sidecar (not from stderr parsing).
        """

        if violation_info_path is None:
            # Dump to a private temp file that we clean up below.
            fd = tempfile.NamedTemporaryFile(
                delete=False, prefix="esbmc-violation-", suffix=".json"
            )
            fd.close()
            dump_path = Path(fd.name)
            owns_dump = True
        else:
            dump_path = Path(violation_info_path)
            owns_dump = False

        cmd_inner = [
            self.binary,
            *[str(s) for s in sources],
            *self.base_flags,
            *(extra_flags or []),
            "--dump-violation-info",
            str(dump_path),
        ]
        # Quote each argument for the inner shell so paths with spaces or
        # special characters are preserved verbatim.
        import shlex

        inner_cmd = " ".join(shlex.quote(a) for a in cmd_inner)
        wrapper = (
            f"ulimit -v {self.virtual_memory_kb}; "
            f"ulimit -t {self.cpu_seconds}; "
            f"exec {inner_cmd}"
        )

        import time

        start = time.monotonic()
        try:
            proc = subprocess.run(
                ["bash", "-c", wrapper],
                capture_output=True,
                text=True,
                timeout=self.wall_timeout_sec,
            )
            stdout, stderr = proc.stdout, proc.stderr
            returncode = proc.returncode
            ok_run = True
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout.decode() if exc.stdout else "") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or "")
            stderr = (exc.stderr.decode() if exc.stderr else "") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
            returncode = -1
            ok_run = False
        wall = time.monotonic() - start

        info: Optional[ViolationInfo] = None
        oracle: Optional[Oracle] = None
        if ok_run and dump_path.exists() and dump_path.stat().st_size > 0:
            try:
                info = ViolationInfo.load(dump_path)
                oracle = info.oracle
            except Exception:
                info = None
                oracle = None
        if owns_dump:
            try:
                dump_path.unlink(missing_ok=True)
            except Exception:
                pass

        return RunResult(
            ok=ok_run,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            wall_sec=wall,
            oracle=oracle,
            info=info,
        )
