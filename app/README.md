# dockerstage

Refactors single-stage Python/ML Dockerfiles into multi-stage builds with
**package-level** dependency resolution. This is the implementation for the
capstone; the controlling design document is `../PROJECT_SPEC.md` and the
build order is `../ROADMAP.md`.

This directory currently contains **Phase 1 — the parsing layer (Modules 1 & 2)**.
Later phases (knowledge base, libc-aware classifier, synthesiser, evaluation)
build on the structured objects produced here.

## What Phase 1 does

Two modules turn raw input into trustworthy structured objects that every later
phase consumes:

- **Module 1 — Dockerfile parser** (`dockerstage/parser/dockerfile_parser.py`)
  extracts stages, base image, RUN commands, `apt-get`/`apk` system packages,
  `pip install` invocations (including `-r` requirement files), COPY/ADD
  (with `--from=` / `--chown=`), ENV/ARG/WORKDIR/EXPOSE/USER, and CMD/ENTRYPOINT/
  HEALTHCHECK (preserving exec vs shell form). ARG-before-FROM is resolved
  (`${VAR}`, `$VAR`, `${VAR:-default}`); an unresolvable base degrades to
  `unknown_base_image` instead of crashing.
- **Module 1a — base-image decomposition** (`parser/base_image.py`) splits the
  image reference into registry/name/tag/digest and infers `python_version`,
  `libc_family` (glibc/musl/**unknown** — never guessed), `distro_family`, and
  `variant`. This is the precondition for the libc-aware classification that
  distinguishes this tool from the StageCraft baseline.
- **Module 2 — manifest parser** (`parser/manifest_parser.py`): Tier 1 parses
  `requirements.txt` into `Requirement` objects with PEP 503-normalized names;
  Tier 2 *detects* (does not resolve) modern manifests (`pyproject.toml`,
  `Pipfile`, lockfiles, `setup.py/.cfg`, `environment.yml`) and flags whether
  each declares application dependencies.

The public API is re-exported from the package root:

```python
import dockerstage as ds
parsed = ds.parse_dockerfile("path/to/Dockerfile")   # -> ParsedDockerfile
reqs   = ds.parse_requirements_file("requirements.txt")  # -> list[Requirement]
t2     = ds.detect_tier2_manifest("pyproject.toml")   # -> Tier2Manifest | None
```

## Dependencies and a deliberate deviation

The spec names `dockerfile-parse` and `packaging` as the parsing libraries.

- **`packaging`** is used directly for PEP 503 normalization and PEP 508
  requirement parsing (spec §5).
- **`dockerfile-parse`** is used as the **primary** Dockerfile tokenizer when it
  is importable. Because it is not available in every environment (e.g. an
  offline CI sandbox with no package index), the parser also ships a
  self-contained **stdlib tokenizer** and falls back to it automatically. Both
  front-ends emit the *same* uniform instruction stream, so every downstream
  extraction is identical regardless of which ran. `ParsedDockerfile.tokenizer_used`
  records the choice.

  This is the one intentional deviation from the spec's single-library
  assumption. It strengthens fidelity rather than weakening it: the primary path
  is exactly the spec-named library, and on a machine where it is installed the
  test suite cross-checks the library path and the fallback against identical
  expectations. The pure adapter (`_adapt_structure`) that maps
  `dockerfile-parse`'s output is unit-tested even where the library is absent.

- **TOML** for `pyproject.toml` detection uses stdlib `tomllib` on Python 3.11+,
  falls back to `tomli` if installed, and finally to a regex section-scan
  (recorded as `detection_fidelity="regex-fallback"`). Only presence/among-declares
  detection is needed here, so the fallback is sufficient.

Install for real use (on a machine with network):

```bash
cd app
python -m pip install -e ".[test,toml]"
```

## Running the tests

Primary (pytest):

```bash
cd app
pytest
```

Offline / no-pytest fallback — a stdlib harness that runs the same `test_*`
functions:

```bash
cd app
python tests/run_offline.py
```

Both runners exercise `test_fixtures/`, which includes the two v2-gap fixtures:
`alpine_lxml` (musl base + compiled dependency) and `ubuntu_py_indeterminate`
(glibc base whose Python version is not in the tag).

## Layout

```
app/
  dockerstage/
    __init__.py            # public API re-exports
    models.py              # dataclasses: the contract for every later phase
    errors.py              # parser/manifest exceptions
    parser/
      base_image.py        # §4a decomposition + libc inference (6 rules)
      dockerfile_parser.py # §4 tokenizer (dockerfile-parse | stdlib) + extractions
      manifest_parser.py   # §5 Tier 1 parse + PEP 503 + Tier 2 detect
  tests/
    _util.py               # pytest-free helpers (fixtures path, raises, tokenizers)
    test_base_image.py
    test_dockerfile_parser.py
    test_manifest_parser.py
    run_offline.py         # stdlib test harness
  test_fixtures/           # Dockerfiles + manifests with hand-checked expectations
  pyproject.toml
```
