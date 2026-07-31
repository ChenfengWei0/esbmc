#!/usr/bin/env python3
"""Print MemAvailable in whole GiB, read by NAME out of the whole /proc/meminfo.

Split out of recollect_all.sh so the headroom arithmetic is testable on its own
and so the sweep does not depend on a line-extracting shell utility. Picking the
field by name rather than by position means a kernel that adds a line makes this
fail loudly instead of returning a neighbouring number.

Exits non-zero if the field is absent, because a sweep that treats "could not
read the headroom" as "the headroom is fine" is the failure this whole check
exists to prevent.
"""
import sys
from pathlib import Path

text = Path("/proc/meminfo").read_text()
for line in text.splitlines():
    name, _, rest = line.partition(":")
    if name.strip() != "MemAvailable":
        continue
    parts = rest.split()
    if len(parts) < 2 or parts[1].lower() != "kb":
        sys.exit(f"MemAvailable is not in kB: {line!r}")
    print(int(parts[0]) // 1024 // 1024)
    sys.exit(0)

sys.exit("no MemAvailable line in /proc/meminfo")
