"""Manifest parsing (spec §5 Module 2).

Tier 1 (fully parsed): requirements.txt-style files, via ``packaging`` — the
spec-named library. Each line becomes a Requirement with a PEP 503 normalized
name (the lookup key the classifier will use in later phases).

Tier 2 (detect-only, spec §5.4 / gap G3): modern manifests (pyproject.toml,
Pipfile, poetry.lock, Pipfile.lock, uv.lock, setup.py, setup.cfg,
environment.yml). We do NOT resolve their dependency graphs in this project; we
only detect their presence and whether they declare dependencies, so the report
can flag "manifest not fully analyzed". pyproject parsing prefers a real TOML
parser (tomllib on 3.11+, else tomli); if neither is importable it falls back to
a regex section-scan and records detection_fidelity="regex-fallback".
"""

from __future__ import annotations

import os
import re
from typing import Optional

from packaging.requirements import Requirement as _PkgRequirement
from packaging.utils import canonicalize_name

from ..models import Requirement, Tier2Manifest

# --------------------------------------------------------------------------- #
# TOML loader resolution (documented offline fallback)
# --------------------------------------------------------------------------- #
try:  # Python 3.11+
    import tomllib as _toml  # type: ignore
    _TOML_OK = True
except ImportError:
    try:
        import tomli as _toml  # type: ignore
        _TOML_OK = True
    except ImportError:
        _toml = None
        _TOML_OK = False


# --------------------------------------------------------------------------- #
# Tier 1 — requirements.txt
# --------------------------------------------------------------------------- #
_OPTION_LINE = re.compile(r"^\s*(--?[A-Za-z][\w-]*)(?:[=\s]+(.*))?$")
_VCS_PREFIXES = ("git+", "hg+", "bzr+", "svn+")


def _classify_and_build(line: str) -> Optional[Requirement]:
    """Turn one logical requirements.txt line into a Requirement, or None if the
    line carries no dependency (blank/comment)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    # Strip inline comments: ' #' (space-hash) per pip grammar, but not '#egg='.
    # Only split on ' #' to avoid clobbering URL fragments.
    if " #" in stripped:
        stripped = stripped.split(" #", 1)[0].strip()
    if not stripped:
        return None

    # Include / constraint directives.
    m = _OPTION_LINE.match(stripped)
    if m and m.group(1) in ("-r", "--requirement", "-c", "--constraint"):
        target = (m.group(2) or "").strip()
        kind = "include" if m.group(1) in ("-r", "--requirement") else "constraint"
        return Requirement(
            name=target, normalized_name="", raw_line=line.rstrip("\n"),
            is_resolvable=False, kind=kind,
        )

    # Editable install: -e <path-or-url>
    if m and m.group(1) in ("-e", "--editable"):
        return Requirement(
            name=(m.group(2) or "").strip(), normalized_name="",
            raw_line=line.rstrip("\n"), is_resolvable=False, kind="local",
        )

    # Any other global option line (e.g. --index-url ...) — record, not resolvable.
    if m and stripped.startswith("-"):
        return Requirement(
            name=stripped, normalized_name="", raw_line=line.rstrip("\n"),
            is_resolvable=False, kind="option",
        )

    # Direct URL / VCS / local path — not a resolvable PyPI name.
    low = stripped.lower()
    if low.startswith(_VCS_PREFIXES) or "://" in stripped:
        kind = "vcs" if low.startswith(_VCS_PREFIXES) else "url"
        # Best-effort name from #egg= fragment.
        name = stripped
        if "#egg=" in stripped:
            name = stripped.split("#egg=", 1)[1].split("&", 1)[0]
        return Requirement(
            name=name, normalized_name=canonicalize_name(name) if "#egg=" in stripped else "",
            raw_line=line.rstrip("\n"), is_resolvable=False, kind=kind,
        )

    # Normal PEP 508 requirement.
    try:
        pkg = _PkgRequirement(stripped)
    except Exception:
        return Requirement(
            name=stripped, normalized_name="", raw_line=line.rstrip("\n"),
            is_resolvable=False, kind="unparsed",
        )
    return Requirement(
        name=pkg.name,
        normalized_name=str(canonicalize_name(pkg.name)),
        version_specifier=str(pkg.specifier),
        extras=sorted(pkg.extras),
        markers=str(pkg.marker) if pkg.marker else None,
        raw_line=line.rstrip("\n"),
        is_resolvable=True,
        kind="pypi",
    )


def _logical_req_lines(text: str):
    """Join backslash-continued lines (pip allows them in requirements files)."""
    out, buf = [], ""
    for raw in text.splitlines():
        if buf:
            buf += " " + raw.strip()
        else:
            buf = raw
        if buf.rstrip().endswith("\\"):
            buf = buf.rstrip()[:-1].rstrip()
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def parse_requirements_text(text: str):
    """Parse requirements.txt content -> list[Requirement] (Tier 1, spec §5.1-5.3)."""
    reqs = []
    for line in _logical_req_lines(text):
        req = _classify_and_build(line)
        if req is not None:
            reqs.append(req)
    return reqs


def parse_requirements_file(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return parse_requirements_text(fh.read())


# --------------------------------------------------------------------------- #
# Tier 2 — detect-only (spec §5.4)
# --------------------------------------------------------------------------- #
_TIER2_BY_NAME = {
    "pyproject.toml": "pep621",
    "pipfile": "pipfile",
    "poetry.lock": "lock",
    "pipfile.lock": "lock",
    "uv.lock": "lock",
    "setup.py": "setup.py",
    "setup.cfg": "setup.cfg",
    "environment.yml": "conda",
    "environment.yaml": "conda",
}


def _pyproject_declares_deps_toml(data: dict) -> bool:
    project = data.get("project", {})
    if isinstance(project, dict):
        if project.get("dependencies"):
            return True
        if project.get("optional-dependencies"):
            return True
    tool = data.get("tool", {})
    if isinstance(tool, dict):
        poetry = tool.get("poetry", {})
        if isinstance(poetry, dict) and (poetry.get("dependencies") or poetry.get("group")):
            return True
        pdm = tool.get("pdm", {})
        if isinstance(pdm, dict) and pdm.get("dev-dependencies"):
            return True
    # NOTE: [build-system].requires is deliberately NOT counted. It lists the build
    # backend bootstrap (setuptools/wheel), not the project's application
    # dependencies. Per the Phase 1 exit gate, a pyproject with only
    # [build-system] + a [tool.*] config table is "config-only".
    return False


def _pyproject_declares_deps_regex(text: str) -> bool:
    """Fallback when no TOML parser is available. Looks for keys/sections that
    declare *application* dependencies — not [build-system].requires."""
    patterns = [
        r"(?m)^\s*dependencies\s*=",
        r"(?m)^\s*\[project\.optional-dependencies\]",
        r"(?m)^\s*\[tool\.poetry\.dependencies\]",
        r"(?m)^\s*\[tool\.poetry\.group\.[\w-]+\.dependencies\]",
        r"(?m)^\s*\[tool\.pdm\.dev-dependencies\]",
    ]
    return any(re.search(p, text) for p in patterns)


def detect_tier2_manifest(path: str) -> Optional[Tier2Manifest]:
    """Detect a modern manifest and whether it declares dependencies. Returns None
    if the filename is not a recognised Tier 2 manifest."""
    filename = os.path.basename(path)
    fmt = _TIER2_BY_NAME.get(filename.lower())
    if fmt is None:
        return None

    declares = False
    fidelity = "parsed"

    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = ""

    if fmt == "pep621":
        if _TOML_OK and _toml is not None:
            try:
                data = _toml.loads(text)
                declares = _pyproject_declares_deps_toml(data)
            except Exception:
                declares = _pyproject_declares_deps_regex(text)
                fidelity = "regex-fallback"
        else:
            declares = _pyproject_declares_deps_regex(text)
            fidelity = "regex-fallback"
    elif fmt == "conda":
        declares = bool(re.search(r"(?m)^\s*dependencies\s*:", text))
    elif fmt == "setup.py":
        declares = ("install_requires" in text) or ("setup_requires" in text)
    elif fmt == "setup.cfg":
        declares = ("install_requires" in text) or ("[options]" in text)
    elif fmt == "pipfile":
        declares = ("[packages]" in text) or ("[dev-packages]" in text)
    elif fmt == "lock":
        declares = True  # a lock file exists precisely to pin dependencies

    return Tier2Manifest(
        filename=filename, path=path, format=fmt,
        declares_dependencies=declares, detection_fidelity=fidelity,
    )
