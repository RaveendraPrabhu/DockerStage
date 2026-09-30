"""Knowledge base loader + schema validator (spec §7.1).

The authoritative schema lives in ``data/kb_schema.json`` (JSON Schema 2020-12).
This module enforces the *same* rules with the standard library only, so the
check runs in CI and in the offline test harness without pulling in
``jsonschema``. If the two ever disagree the JSON file is the spec; keep this in
sync with it.

``load()`` is what downstream phases (Phase 4 classification) call to read the
bundled KB. ``validate()`` returns a list of human-readable error strings; an
empty list means the KB conforms.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
KB_PATH = DATA_DIR / "knowledge_base.json"

# Mirrors kb_schema.json. Update both together.
SCHEMA_VERSION = "2.0"
MIN_ENTRIES = 60
ENTRY_KEYS = frozenset(
    {
        "name",
        "version_range",
        "import_names",
        "build_time_system_deps",
        "runtime_system_deps",
        "extras",
        "wheel_typically_available",
        "source",
        "confidence",
        "verified_by_build",
        "notes",
    }
)
SOURCE_ENUM = frozenset({"curated", "conda-forge-derived", "inferred"})
DISTRO_KEYS = frozenset({"debian", "alpine"})
EXTRA_KEYS = frozenset({"build_time_system_deps", "runtime_system_deps", "notes"})

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")  # PEP 503 normalised
_PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")  # distro package name


def load(path: Path | str = KB_PATH) -> dict[str, Any]:
    """Read and JSON-parse the knowledge base. Does not validate."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _check_dep_map(errs: list[str], where: str, value: Any) -> None:
    if not isinstance(value, dict):
        errs.append(f"{where}: must be an object, got {type(value).__name__}")
        return
    extra = set(value) - DISTRO_KEYS
    if extra:
        errs.append(f"{where}: unexpected distro key(s) {sorted(extra)}")
    if "debian" not in value:
        errs.append(f"{where}: missing required 'debian' key")
    for distro, pkgs in value.items():
        if distro not in DISTRO_KEYS:
            continue
        _check_pkg_list(errs, f"{where}.{distro}", pkgs)


def _check_pkg_list(errs: list[str], where: str, pkgs: Any) -> None:
    if not isinstance(pkgs, list):
        errs.append(f"{where}: must be an array, got {type(pkgs).__name__}")
        return
    if len(pkgs) != len(set(pkgs)):
        errs.append(f"{where}: duplicate package names")
    for pkg in pkgs:
        if not isinstance(pkg, str) or not _PKG_RE.match(pkg):
            errs.append(f"{where}: invalid package name {pkg!r}")


def _check_entry(errs: list[str], idx: int, entry: Any) -> None:
    tag = f"entries[{idx}]"
    if not isinstance(entry, dict):
        errs.append(f"{tag}: must be an object")
        return
    keys = set(entry)
    if keys != ENTRY_KEYS:
        for k in ENTRY_KEYS - keys:
            errs.append(f"{tag}: missing required field '{k}'")
        for k in keys - ENTRY_KEYS:
            errs.append(f"{tag}: unexpected field '{k}'")
    name = entry.get("name")
    tag = f"entries[{idx}]({name})" if isinstance(name, str) else tag
    if not isinstance(name, str) or not _NAME_RE.match(name):
        errs.append(f"{tag}: 'name' not PEP 503 normalised: {name!r}")

    vr = entry.get("version_range")
    if not isinstance(vr, str) or not vr:
        errs.append(f"{tag}: 'version_range' must be a non-empty string")

    imports = entry.get("import_names")
    if not isinstance(imports, list):
        errs.append(f"{tag}: 'import_names' must be an array")
    else:
        if len(imports) != len(set(imports)):
            errs.append(f"{tag}: 'import_names' has duplicates")
        for im in imports:
            if not isinstance(im, str) or not im:
                errs.append(f"{tag}: 'import_names' contains empty/non-string {im!r}")

    _check_dep_map(errs, f"{tag}.build_time_system_deps", entry.get("build_time_system_deps"))
    _check_dep_map(errs, f"{tag}.runtime_system_deps", entry.get("runtime_system_deps"))

    extras = entry.get("extras")
    if not isinstance(extras, dict):
        errs.append(f"{tag}: 'extras' must be an object")
    else:
        for ename, variant in extras.items():
            ewhere = f"{tag}.extras[{ename}]"
            if not isinstance(variant, dict):
                errs.append(f"{ewhere}: must be an object")
                continue
            bad = set(variant) - EXTRA_KEYS
            if bad:
                errs.append(f"{ewhere}: unexpected key(s) {sorted(bad)}")
            if "build_time_system_deps" in variant:
                _check_dep_map(errs, f"{ewhere}.build_time_system_deps", variant["build_time_system_deps"])
            if "runtime_system_deps" in variant:
                _check_dep_map(errs, f"{ewhere}.runtime_system_deps", variant["runtime_system_deps"])
            if "notes" in variant and not isinstance(variant["notes"], str):
                errs.append(f"{ewhere}.notes: must be a string")

    if not isinstance(entry.get("wheel_typically_available"), bool):
        errs.append(f"{tag}: 'wheel_typically_available' must be a boolean")
    if entry.get("source") not in SOURCE_ENUM:
        errs.append(f"{tag}: 'source' must be one of {sorted(SOURCE_ENUM)}")
    conf = entry.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= conf <= 1.0):
        errs.append(f"{tag}: 'confidence' must be a number in [0.0, 1.0]")
    if not isinstance(entry.get("verified_by_build"), bool):
        errs.append(f"{tag}: 'verified_by_build' must be a boolean")
    if not isinstance(entry.get("notes"), str):
        errs.append(f"{tag}: 'notes' must be a string")


def validate(data: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings ([] means valid)."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["top level: must be an object"]

    extra = set(data) - {"schema_version", "generated_notes", "entries"}
    if extra:
        errs.append(f"top level: unexpected key(s) {sorted(extra)}")
    if data.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version must be {SCHEMA_VERSION!r}, got {data.get('schema_version')!r}")

    entries = data.get("entries")
    if not isinstance(entries, list):
        errs.append("entries: must be an array")
        return errs
    if len(entries) < MIN_ENTRIES:
        errs.append(f"entries: need >= {MIN_ENTRIES}, got {len(entries)}")

    seen: set[str] = set()
    for i, entry in enumerate(entries):
        _check_entry(errs, i, entry)
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            if entry["name"] in seen:
                errs.append(f"entries[{i}]: duplicate name {entry['name']!r}")
            seen.add(entry["name"])
    return errs


if __name__ == "__main__":
    problems = validate(load())
    if problems:
        print(f"INVALID: {len(problems)} problem(s)")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    print(f"OK: {len(load()['entries'])} entries valid against schema {SCHEMA_VERSION}")
