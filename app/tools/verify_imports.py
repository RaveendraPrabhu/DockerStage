#!/usr/bin/env python3
"""Verify each entry's ``import_names`` by importing them in a real container.

Exit-gate item (spec §7.1 / ROADMAP Phase 2): "Every import_names verified by
importing in a container." For each entry we spin up python:3.11-slim (Debian
bookworm, the runtime base the tool targets), install the entry's Debian
*runtime* deps, ``pip install`` the package, and run ``python -c "import M"``
for every module name.

Needs Docker. Run it where Docker and the network are available:

    python tools/verify_imports.py --limit 5        # smoke a handful
    python tools/verify_imports.py --only psycopg2,numpy,lxml
    python tools/verify_imports.py                  # all (slow: one container each)
    python tools/verify_imports.py --selftest       # offline: exercise build_cmd

ceiling (v1): this proves the import *works*; it does not prove the dep list is
*minimal*. It installs runtime deps only, so a source-only package with no wheel
may fail at ``pip install`` for lack of build deps -- that's the full §10 build
path (Phase 6), not this smoke test. Entries with empty import_names (console
-script-only packages like uwsgi) are skipped here; they're spec Test C.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent.parent / "dockerstage" / "data" / "knowledge_base.json"
DEFAULT_IMAGE = "python:3.11-slim"


def build_cmd(entry: dict, image: str = DEFAULT_IMAGE) -> list[str]:
    """Return the ``docker run`` argv that installs deps + package and imports it.

    Pure function (no I/O) so it's unit-testable offline.
    """
    build_deps = entry.get("build_time_system_deps", {}).get("debian", [])
    runtime_deps = entry.get("runtime_system_deps", {}).get("debian", [])
    deps = list(dict.fromkeys(build_deps + runtime_deps))
    imports = entry["import_names"]
    name = entry["name"]

    steps = ["set -e"]
    if deps:
        steps.append("apt-get update -qq")
        steps.append("apt-get install -y --no-install-recommends " + " ".join(deps))
    steps.append(f"pip install --quiet --no-input '{name}'")
    steps.append("python -c '" + "; ".join(f"import {m}" for m in imports) + "'")
    script = " && ".join(steps)
    return ["docker", "run", "--rm", image, "bash", "-lc", script]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="comma-separated subset of entry names")
    ap.add_argument("--limit", type=int, help="check only the first N (after --only filter)")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help=f"base image (default {DEFAULT_IMAGE})")
    ap.add_argument("--selftest", action="store_true", help="run the offline build_cmd self-check and exit")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if shutil.which("docker") is None:
        print("docker not found on PATH -- this tool needs Docker. Skipping.", file=sys.stderr)
        return 2

    entries = [e for e in json.loads(KB_PATH.read_text())["entries"] if e["import_names"]]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        entries = [e for e in entries if e["name"] in wanted]
    if args.limit is not None:
        entries = entries[: args.limit]

    failures = []
    for e in entries:
        name = e["name"]
        print(f"-- {name}: import {e['import_names']} ...", flush=True)
        proc = subprocess.run(build_cmd(e, args.image), capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"ok   {name}")
        else:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
            failures.append((name, "\n".join(tail)))
            print(f"FAIL {name}\n    " + "\n    ".join(tail))

    print("\n" + "=" * 60)
    print(f"checked: {len(entries)}   failed: {len(failures)}")
    for name, tail in failures:
        print(f"  FAIL {name}: {tail.splitlines()[-1] if tail else '(no output)'}")
    return 1 if failures else 0


def _selftest() -> int:
    psycopg2 = {"name": "psycopg2", "import_names": ["psycopg2"],
                "build_time_system_deps": {"debian": ["libpq-dev"]},
                "runtime_system_deps": {"debian": ["libpq5"]}}
    cmd = build_cmd(psycopg2)
    assert cmd[:5] == ["docker", "run", "--rm", "python:3.11-slim", "bash"]
    script = cmd[-1]
    assert "apt-get install -y --no-install-recommends libpq-dev libpq5" in script
    assert "pip install --quiet --no-input 'psycopg2'" in script
    assert "python -c 'import psycopg2'" in script

    # pure-python entry: no apt-get step
    pure = {"name": "click", "import_names": ["click"], "runtime_system_deps": {"debian": []}}
    assert "apt-get" not in build_cmd(pure)[-1]

    # multiple import names -> one python -c importing all
    multi = {"name": "pillow", "import_names": ["PIL", "PIL.Image"], "runtime_system_deps": {"debian": ["libjpeg62-turbo"]}}
    assert "python -c 'import PIL; import PIL.Image'" in build_cmd(multi)[-1]
    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
