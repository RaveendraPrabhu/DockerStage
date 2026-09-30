"""Tests for the Dockerfile parser (spec §4 Module 1).

Core extractions are asserted under every available tokenizer, so on a machine
with dockerfile-parse installed the library path and the stdlib fallback are
cross-checked against the same expectations."""

from __future__ import annotations

from dockerstage.parser.dockerfile_parser import (
    parse_dockerfile,
    parse_dockerfile_text,
    parse_many,
)
from dockerstage.errors import MissingFromError

from _util import fixture, raises, available_tokenizers

TOKS = available_tokenizers()


def _parse(name, tok):
    return parse_dockerfile(fixture(name, "Dockerfile"), tokenizer=tok)


def test_simple_flask_all_extractions():
    for tok in TOKS:
        p = _parse("simple_flask", tok)
        assert p.tokenizer_used == tok
        assert p.is_multistage is False
        assert len(p.stages) == 1
        bi = p.base_image
        assert bi.image_name == "python" and bi.tag == "3.11-slim"
        assert bi.libc_family == "glibc" and bi.distro_family == "debian"
        assert bi.python_version == (3, 11)
        assert [pk.name for pk in p.apt_packages()] == ["build-essential", "libpq-dev"]
        assert p.apk_packages() == []
        assert len(p.pip_installs) == 1
        assert p.pip_installs[0].requirement_files == ["requirements.txt"]
        assert p.pip_installs[0].pip_executable == "pip"
        assert p.workdir == "/app"
        assert p.env.get("PYTHONUNBUFFERED") == "1"
        assert p.exposed_ports == ["8000"]
        assert p.user == "appuser" and p.has_nonroot_user is True
        assert p.cmd.form == "exec" and p.cmd.argv[0] == "gunicorn"
        assert len(p.copies) == 2


def test_alpine_lxml_is_musl_and_apk_extracted():
    for tok in TOKS:
        p = _parse("alpine_lxml", tok)
        bi = p.base_image
        assert bi.libc_family == "musl" and bi.distro_family == "alpine"
        assert bi.python_version == (3, 11)
        names = [pk.name for pk in p.apk_packages()]
        assert names == ["gcc", "musl-dev", "libxml2-dev", "libxslt-dev"]
        assert ".build-deps" not in names            # --virtual value skipped
        assert p.apt_packages() == []
        assert p.pip_installs[0].inline_packages == ["lxml==4.9.3"]
        assert p.entrypoint.form == "exec"
        assert p.entrypoint.argv == ["python", "app.py"]


def test_ubuntu_glibc_python_version_indeterminate():
    for tok in TOKS:
        p = _parse("ubuntu_py_indeterminate", tok)
        bi = p.base_image
        assert bi.distro_family == "ubuntu" and bi.libc_family == "glibc"
        assert bi.python_version is None
        assert bi.python_version_source == "unknown"
        assert [pk.name for pk in p.apt_packages()] == ["python3", "python3-pip", "git"]
        assert p.pip_installs[0].pip_executable == "pip3"
        assert p.pip_installs[0].requirement_files == ["/tmp/requirements.txt"]


def test_unknown_base_libc_stays_unknown():
    for tok in TOKS:
        p = _parse("unknown_base", tok)
        bi = p.base_image
        assert bi.libc_family == "unknown"
        assert bi.distro_family == "unknown"
        assert bi.resolved is True


def test_arg_before_from_resolves():
    for tok in TOKS:
        p = _parse("arg_from", tok)
        bi = p.base_image
        assert bi.image_name == "python" and bi.tag == "3.11-slim"
        assert bi.python_version == (3, 11) and bi.libc_family == "glibc"
        assert p.args.get("APP_ENV") == "prod"
        inline = p.pip_installs[0].inline_packages
        assert "fastapi" in inline and "uvicorn[standard]" in inline


def test_unresolved_arg_from_becomes_unknown():
    for tok in TOKS:
        p = _parse("arg_from_unresolved", tok)
        assert p.base_image.is_unknown is True
        assert p.base_image.resolved is False


def test_multistage_split_and_copy_from():
    for tok in TOKS:
        p = _parse("multistage", tok)
        assert p.is_multistage is True and len(p.stages) == 2
        assert p.base_image.tag == "3.11-slim"          # runtime = last stage
        assert p.builder_base_image.tag == "3.11-slim"
        copy_from = [c for c in p.copies if c.from_stage]
        assert copy_from and copy_from[0].from_stage == "builder"
        assert copy_from[0].chown == "1000:1000"
        assert copy_from[0].sources == ["/install"]
        assert copy_from[0].dest == "/usr/local"
        assert p.exposed_ports == ["8501/tcp"]
        assert "curl" in (p.healthcheck or "")
        assert p.cmd.form == "exec" and p.cmd.argv[0] == "streamlit"
        # builder-stage apt only
        assert [pk.name for pk in p.apt_packages()] == ["gcc"]
        assert all(pk.stage_index == 0 for pk in p.apt_packages())


def test_ml_torch_python_dash_m_pip_and_multiple_installs():
    for tok in TOKS:
        p = _parse("ml_torch", tok)
        bi = p.base_image
        assert bi.distro_family == "debian" and bi.libc_family == "glibc"
        assert bi.python_version == (3, 10) and bi.variant == "slim"
        assert [pk.name for pk in p.apt_packages()] == ["build-essential", "git", "curl"]
        assert len(p.pip_installs) == 2
        assert all(pi.pip_executable == "python -m pip" for pi in p.pip_installs)
        assert any(pi.requirement_files == ["requirements.txt"] for pi in p.pip_installs)
        assert p.entrypoint.argv == ["python", "-m", "src.train"]


def test_no_from_raises():
    for tok in TOKS:
        with raises(MissingFromError):
            _parse("no_from", tok)


def test_empty_dockerfile_is_graceful():
    for tok in TOKS:
        p = _parse("empty", tok)
        assert p.stages == []
        assert p.base_image is None
        assert "empty-dockerfile" in p.warnings


def test_parse_many_skips_one_malformed_file():
    paths = [
        fixture("simple_flask", "Dockerfile"),
        fixture("no_from", "Dockerfile"),
        fixture("alpine_lxml", "Dockerfile"),
    ]
    results = parse_many(paths)
    assert len(results) == 3
    assert results[0]["parsed"] is not None and results[0]["error"] is None
    assert results[1]["parsed"] is None and "MissingFromError" in results[1]["error"]
    assert results[2]["parsed"] is not None and results[2]["error"] is None


def test_inline_hash_not_treated_as_comment():
    text = 'FROM python:3.11-slim\nRUN echo "not # a comment" && pip install flask\n'
    p = parse_dockerfile_text(text, tokenizer="stdlib")
    assert p.pip_installs and "flask" in p.pip_installs[0].inline_packages


def test_dockerfile_parse_adapter_contract():
    """Verify the dockerfile-parse adapter without needing the library installed:
    it upper-cases instructions, skips COMMENT entries, and converts the library's
    0-indexed startline to our 1-indexed line."""
    from dockerstage.parser.dockerfile_parser import _adapt_structure

    fake_structure = [
        {"instruction": "COMMENT", "value": "# syntax=...", "startline": 0},
        {"instruction": "from", "value": "python:3.11-slim", "startline": 1},
        {"instruction": "RUN", "value": "pip install flask", "startline": 2},
    ]
    raws = _adapt_structure(fake_structure)
    assert [r.instruction for r in raws] == ["FROM", "RUN"]   # COMMENT dropped
    assert raws[0].line == 2 and raws[1].line == 3           # 0-indexed -> 1-indexed
    assert raws[0].value == "python:3.11-slim"
