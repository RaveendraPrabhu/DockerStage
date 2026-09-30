"""Tests for the manifest parser (spec §5 Module 2): Tier 1 requirements parsing
with PEP 503 normalization, and Tier 2 detect-only classification."""

from __future__ import annotations

from dockerstage.parser.manifest_parser import (
    parse_requirements_text,
    parse_requirements_file,
    detect_tier2_manifest,
)

from _util import fixture


def test_tier1_basic_normalization_and_specifiers():
    reqs = parse_requirements_file(fixture("simple_flask", "requirements.txt"))
    by_norm = {r.normalized_name: r for r in reqs}
    assert set(by_norm) == {"flask", "gunicorn", "psycopg2-binary", "requests"}
    assert by_norm["flask"].version_specifier == "==2.0.1"
    assert by_norm["gunicorn"].version_specifier == ">=20.1"
    assert by_norm["requests"].version_specifier == ""
    assert all(r.is_resolvable and r.kind == "pypi" for r in reqs)


def test_pep503_normalization_collapses_variants():
    reqs = parse_requirements_file(fixture("reqs", "reqs_normalize.txt"))
    norm = [r.normalized_name for r in reqs]
    # scikit-learn written three ways all collapse to one canonical key
    assert norm.count("scikit-learn") == 3
    assert "flask" in norm and "pillow" in norm


def test_tier1_tricky_lines_classified():
    reqs = parse_requirements_text(open(fixture("reqs", "reqs_tricky.txt")).read())
    kinds = {}
    for r in reqs:
        kinds.setdefault(r.kind, []).append(r)

    # include / constraint directives are recorded but not resolvable
    assert any(r.name == "base.txt" for r in kinds.get("include", []))
    assert any(r.name == "constraints.txt" for r in kinds.get("constraint", []))
    assert kinds.get("option")  # --extra-index-url
    assert kinds.get("local")   # -e ./local_pkg
    assert kinds.get("vcs")     # git+https ... #egg=requests

    resolvable = [r for r in reqs if r.is_resolvable]
    names = {r.normalized_name for r in resolvable}
    assert names == {"django", "uvicorn", "numpy", "requests"}

    uvicorn = next(r for r in resolvable if r.normalized_name == "uvicorn")
    assert uvicorn.extras == ["standard"]
    numpy = next(r for r in resolvable if r.normalized_name == "numpy")
    assert numpy.markers is not None and "python_version" in numpy.markers
    django = next(r for r in resolvable if r.normalized_name == "django")
    # packaging serializes a SpecifierSet in canonical (sorted) order, so compare
    # the set of clauses rather than an exact string.
    assert set(django.version_specifier.split(",")) == {">=4.2", "<5.0"}


def test_git_url_egg_name_extracted_but_unresolvable():
    reqs = parse_requirements_text("git+https://github.com/psf/requests.git@main#egg=requests\n")
    assert len(reqs) == 1
    r = reqs[0]
    assert r.kind == "vcs" and r.is_resolvable is False
    assert r.name == "requests"


def test_tier2_pyproject_with_deps_detected():
    m = detect_tier2_manifest(fixture("pyproject_deps", "pyproject.toml"))
    assert m is not None
    assert m.format == "pep621"
    assert m.declares_dependencies is True
    assert m.detection_fidelity in ("parsed", "regex-fallback")


def test_tier2_config_only_pyproject_declares_nothing():
    # Exit-gate example: [build-system] + [tool.ruff], no application deps.
    m = detect_tier2_manifest(fixture("pyproject_config_only", "pyproject.toml"))
    assert m is not None
    assert m.format == "pep621"
    assert m.declares_dependencies is False   # build-system.requires must NOT count


def test_tier2_setup_py_pipfile_conda_detected():
    setup = detect_tier2_manifest(fixture("setup_py", "setup.py"))
    assert setup.format == "setup.py" and setup.declares_dependencies is True

    pip = detect_tier2_manifest(fixture("pipfile", "Pipfile"))
    assert pip.format == "pipfile" and pip.declares_dependencies is True

    conda = detect_tier2_manifest(fixture("conda", "environment.yml"))
    assert conda.format == "conda" and conda.declares_dependencies is True


def test_tier2_ignores_unrecognised_filename():
    assert detect_tier2_manifest(fixture("simple_flask", "requirements.txt")) is None
