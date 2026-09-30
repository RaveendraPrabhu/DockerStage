#!/usr/bin/env python3
"""Verify each entry's ``wheel_typically_available`` flag against live PyPI.

Exit-gate item (spec §7.1 / ROADMAP Phase 2): "Every wheel_typically_available
checked against PyPI, not inherited." The live §7.2 check is authoritative and
must win any disagreement with the KB flag; this tool reports the disagreements
so they can be fixed by hand.

Needs network. Run it in an environment with internet access:

    python tools/verify_wheels.py                 # check all entries
    python tools/verify_wheels.py --only numpy,lxml,flask
    python tools/verify_wheels.py --selftest      # offline: exercise the decision fn

"Typically available" here means: the latest release ships a wheel usable by a
normal Linux install — a pure-Python ``*-any.whl`` or a ``manylinux``/
``musllinux`` wheel. macOS/Windows-only wheels and bare ``linux_x86_64`` do not
count (spec §7.2.1).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KB_PATH = Path(__file__).resolve().parent.parent / "dockerstage" / "data" / "knowledge_base.json"
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
UA = "dockerstage-verify-wheels/1.0 (+https://github.com/dockerstage; KB curation check)"


def wheel_available(files: list[dict]) -> bool:
    """True if any file is a Linux-usable wheel (pure ``any`` or many/musllinux).

    ``files`` is a PyPI release file list (each dict has ``packagetype`` and
    ``filename``). Platform tag is the last ``-``-separated field of the wheel
    name, dot-joined for compound tags.
    """
    for f in files:
        if f.get("packagetype") != "bdist_wheel":
            continue
        fn = f.get("filename", "")
        if not fn.endswith(".whl"):
            continue
        # ponytail: coarse substring on the platform tag; Phase 4's wheel_resolver
        # does the rigorous packaging.utils.parse_wheel_filename parse. Fine for a
        # curation-time yes/no on wheel presence.
        plat = fn[:-4].split("-")[-1]
        if any(t == "any" or t.startswith("manylinux") or t.startswith("musllinux") for t in plat.split(".")):
            return True
    return False


def fetch_latest_files(name: str, timeout: float = 15.0) -> list[dict]:
    """Return the latest release's file list from PyPI ([] if the name is 404)."""
    req = urllib.request.Request(PYPI_JSON.format(name=name), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    files = data.get("urls") or []
    if not files:  # latest version was sdist-only or yanked; scan newest release with files
        releases = data.get("releases", {})
        for _ver, fl in sorted(releases.items(), reverse=True):
            if fl:
                return fl
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="comma-separated subset of entry names")
    ap.add_argument("--delay", type=float, default=0.2, help="seconds between requests (politeness)")
    ap.add_argument("--selftest", action="store_true", help="run the offline decision-fn self-check and exit")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    entries = json.loads(KB_PATH.read_text())["entries"]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        entries = [e for e in entries if e["name"] in wanted]

    mismatches, errors, checked = [], [], 0
    for e in entries:
        name, claimed = e["name"], e["wheel_typically_available"]
        try:
            observed = wheel_available(fetch_latest_files(name))
        except urllib.error.URLError as exc:
            print(f"NETWORK ERROR on {name}: {exc}", file=sys.stderr)
            print("Aborting: this tool needs internet access.", file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - report and continue
            errors.append((name, str(exc)))
            print(f"?? {name}: {exc}")
            continue
        checked += 1
        mark = "OK " if observed == claimed else "!! "
        if observed != claimed:
            mismatches.append((name, claimed, observed))
        print(f"{mark}{name}: kb={claimed} pypi={observed}")
        time.sleep(args.delay)

    print("\n" + "=" * 60)
    print(f"checked: {checked}   mismatches: {len(mismatches)}   errors: {len(errors)}")
    for name, claimed, observed in mismatches:
        print(f"  MISMATCH {name}: KB says {claimed}, PyPI shows {observed} -> fix the KB flag")
    return 1 if mismatches else 0


def _selftest() -> int:
    assert wheel_available([{"packagetype": "bdist_wheel", "filename": "flask-3.0-py3-none-any.whl"}]) is True
    assert wheel_available([{"packagetype": "bdist_wheel", "filename": "numpy-2.0-cp311-cp311-manylinux_2_17_x86_64.whl"}]) is True
    assert wheel_available([{"packagetype": "bdist_wheel", "filename": "x-1.0-cp311-cp311-musllinux_1_2_x86_64.whl"}]) is True
    # macOS/Windows-only and bare linux_x86_64 do not count as typically available
    assert wheel_available([{"packagetype": "bdist_wheel", "filename": "x-1.0-cp311-cp311-macosx_11_0_arm64.whl"}]) is False
    assert wheel_available([{"packagetype": "bdist_wheel", "filename": "x-1.0-cp311-cp311-linux_x86_64.whl"}]) is False
    # sdist only
    assert wheel_available([{"packagetype": "sdist", "filename": "x-1.0.tar.gz"}]) is False
    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
