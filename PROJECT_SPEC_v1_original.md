# PROJECT SPECIFICATION
## Domain-Aware Dockerfile Refactoring for ML Workloads (Package-Level Dependency Resolution)

> **ARCHIVED — v1.** This is the original specification, preserved unchanged for
> diff/provenance purposes. The active specification is `PROJECT_SPEC.md` (v2),
> which patches nine identified gaps. Do not implement from this file.

This document is a complete, self-contained specification. Read it fully before writing any code. It contains the problem context, the research gap being addressed, the full system architecture, algorithms for every component, data schemas, and implementation guidance. Nothing outside this document should be assumed — if something is genuinely ambiguous, flag it rather than guessing.

---

## 1. PROJECT CONTEXT AND MOTIVATION

### 1.1 The problem
Docker images for Python/ML applications are typically built using **single-stage Dockerfiles** — every instruction (installing compilers, installing dependencies, copying code, configuring runtime) happens in one single build stage. This means tools that are only needed to *build* the application (e.g., compilers) remain permanently in the *final production image*, even though they serve no purpose at runtime.

This is a particular problem for Python ML/data libraries because many of them are **not pure Python** — they contain C/C++/Fortran extensions that must be compiled at install time:
- `numpy`, `scipy` — often need a Fortran/C compiler and BLAS/LAPACK libraries to build from source (though pre-built wheels frequently avoid this — see Section 1.3)
- `lxml` — needs `libxml2-dev` and `libxslt-dev` (development headers) to compile; once compiled, only needs the much smaller runtime shared libraries `libxml2` and `libxslt1.1`
- `psycopg2` — needs `libpq-dev` to compile; only needs `libpq5` (runtime shared library) afterward
- `torch` — may need CUDA toolkit/drivers depending on GPU vs CPU variant

Leaving the build-only tools (compilers, `-dev`/header packages, build utilities like `make`, `git`) in the final image causes:
1. **Bloated image size** — reported industry/academic findings put average unnecessary bloat in the multi-gigabyte range for compiled-dependency-heavy images
2. **Expanded security attack surface** — every extra package is a potential CVE source; leftover build tools like `curl` or `git` have had real associated CVEs (e.g., CVE-2018-1000007, CVE-2022-29187)

### 1.2 The established solution: multi-stage Docker builds
Docker supports **multi-stage builds**: a Dockerfile can contain multiple `FROM` instructions, each starting a new "stage." A common pattern:
- **Builder stage**: uses a full-featured base image, installs compilers and build tools, compiles/installs all dependencies, builds the application
- **Runtime stage**: uses a minimal base image (e.g., a `-slim` variant), and uses `COPY --from=builder <src> <dst>` to copy over *only* the compiled artifacts and runtime-necessary files — none of the build tooling is carried over, because the builder stage is discarded

This is a well-established best practice, but manually refactoring an existing single-stage Dockerfile into a correct multi-stage one is non-trivial and error-prone (you must correctly identify what's build-only vs. runtime-necessary, or you risk breaking the application).

### 1.3 The existing automated solution and its specific, documented gap
A tool called **StageCraft** was published in 2026 (Chen, Yang, Pan, Zhou, "Automating Dockerfile Refactoring to Multi-stage Builds," *Proc. ACM Softw. Eng.* 3, FSE, Article FSE070, July 2026) that automates this single-stage-to-multi-stage refactoring. It works in three phases: (1) static analysis of the Dockerfile to detect language/stack and classify dependencies, (2) a necessity-scoring gate to decide if refactoring is worthwhile, (3) synthesis of the new multi-stage Dockerfile.

**The critical, documented gap this project addresses**: StageCraft's own paper states, in its discussion section: *"we deliberately restrict the input scope to the Dockerfile rather than the full project context to avoid the complexity of parsing heterogeneous build systems... we acknowledge that taking the whole project context, including source code, build scripts, and manifests, as input could further improve the quality of multi-stage builds, and we consider the exploration of such hybrid analysis strategies a promising direction for future research."*

**In plain terms: StageCraft never opens or reads `requirements.txt` (or any other Python dependency manifest).** Instead, for Python dependencies, it uses what it calls an "inheritance rule" (Section 3.2.2 of their paper): if the `pip` package manager is used at runtime (referenced in the final runtime-relevant Dockerfile instructions), **all** packages pip ever installed are labeled "runtime." If pip is only used during build, **all** its packages are labeled "build-time." This is a single, all-or-nothing decision made at the package-manager level — it never looks inside the manifest to see which *individual* packages actually need what.

**Consequence**: a `requirements.txt` containing both `flask` (pure Python, no native dependencies at all) and `lxml` (needs a compiler and dev headers to build, and separate runtime shared libraries afterward) would have both packages receive the identical classification under StageCraft's rule — even though their actual native dependency needs are completely different.

### 1.4 This project's contribution
This project builds a tool that **directly parses the Python dependency manifest** (`requirements.txt`) and resolves **each individually declared package's** actual build-time and runtime native dependency requirements — using (a) a curated knowledge base of known package-to-native-dependency mappings, and (b) a live check against the PyPI JSON API to determine whether a pre-built wheel exists for the target platform (which, if true, means **no compilation happens at all** for that package, regardless of what the knowledge base would otherwise suggest).

This enables **package-level precision**: within a single manifest, some packages can be correctly classified as needing zero extra native dependencies, while others are correctly identified as needing specific build-time tools that get isolated to the discarded builder stage — something StageCraft's manager-level rule cannot do, by its own authors' explicit design and admission.

---

## 2. SCOPE

### 2.1 In scope
- Input: a single-stage Dockerfile + a `requirements.txt` file, for a **Python** application
- Output: an optimized multi-stage Dockerfile + a `.dockerignore` file + a JSON classification report
- Target ecosystem: Python/ML web applications and services (Streamlit, FastAPI, Flask, generic Python scripts) — this is a **Python-and-ML-specific tool**, not a general multi-language tool
- Validation: the tool must attempt an actual `docker build` of its own output and report success/failure

### 2.2 Out of scope (explicitly — do not build these)
- Multi-language support (Go, Java, Node.js as primary languages) — Python is the sole target language for classification depth; if a secondary language stack is detected (e.g., a Node.js frontend build step), treat it as a black box to isolate/discard, not to classify in depth
- GPU/CUDA-specific dependency resolution beyond a basic "does this project use torch/tensorflow with CUDA" signal check — full CUDA version matrix resolution is future work
- A web UI — this is a CLI tool
- Handling private/internal PyPI indexes — public PyPI only
- Full dependency-graph (transitive dependency) resolution — the tool reasons about the packages explicitly listed in `requirements.txt`, not their sub-dependencies' sub-dependencies

---

## 3. SYSTEM ARCHITECTURE — PIPELINE OVERVIEW

```
[Dockerfile] + [requirements.txt]
        |
        v
  [1] PARSING LAYER
        |  -> structured Dockerfile object (base image, RUN commands, COPY, CMD/ENTRYPOINT, EXPOSE, ENV, ARG, WORKDIR, USER, HEALTHCHECK)
        |  -> structured manifest object (list of package name + version specifier + extras)
        v
  [2] STACK/LANGUAGE DETECTION
        |  -> primary language (Python), secondary languages if any (e.g., Node.js build step)
        |  -> architecture type (WEB_API, STATIC_SITE, BATCH_JOB, etc.)
        v
  [3] DEPENDENCY CLASSIFICATION ENGINE  <-- core contribution
        |  -> for each Python package: build-time system deps, runtime system deps (via knowledge base + live wheel check)
        |  -> for each apt/system package already in the Dockerfile: BUILD_ONLY / RUNTIME / UNKNOWN_CONSERVATIVE label
        v
  [4] NECESSITY SCORING
        |  -> composite score (bloat + efficiency + security); gate decision: proceed or stop
        v
  [5] MULTI-STAGE SYNTHESIS
        |  -> generates builder stage + runtime stage Dockerfile text
        |  -> generates .dockerignore
        v
  [6] BUILD VALIDATION
        |  -> attempts real `docker build`, classifies failures, logs knowledge-base gaps
        v
  [7] REPORTING
        |  -> JSON report: what was classified how, confidence levels, size/metric estimates
```

Each numbered stage below corresponds to a separate, independently testable module.

---

## 4. MODULE 1 — DOCKERFILE PARSER

### Purpose
Convert a raw Dockerfile into a clean, structured, reliable object that all downstream modules consume. This module does NOT do any classification or decision-making — only extraction.

### Library
`dockerfile-parse` (Python package)

### Required extraction capabilities (all of the following must be implemented)
1. **Base image extraction** from `FROM` — must resolve `ARG`-based base images (e.g., `FROM ${BASE_IMAGE}` where `BASE_IMAGE` is defined by an earlier `ARG BASE_IMAGE=python:3.11` instruction). If unresolvable, flag as `"unknown_base_image"` rather than crashing.
2. **Multi-stage detection**: detect if the input Dockerfile *already* has multiple `FROM` instructions. If so, split the instruction list into per-stage groups, capturing each stage's alias if present (`FROM x AS builder`).
3. **RUN command extraction**: extract all `RUN` instructions; split commands chained with `&&` or `||` into individual commands; strip line-continuation backslashes.
4. **Apt/system package extraction**: from RUN commands, specifically detect `apt-get install` / `apt install` invocations and extract the clean list of package names (strip flags like `-y`, `--no-install-recommends`, handle multi-line continuations).
5. **Pip install line detection**: detect `RUN pip install` / `RUN pip3 install` commands and note their position/context in the Dockerfile (this is used later for cross-referencing, and for comparison against how StageCraft's inheritance rule would classify things).
6. **COPY/ADD extraction**: capture source and destination paths for all `COPY`/`ADD` instructions; distinguish a plain host-context `COPY` from a cross-stage `COPY --from=<stage>`.
7. **ENV and ARG extraction**: capture all declared environment variables and build arguments.
8. **WORKDIR extraction**.
9. **EXPOSE extraction** (list of ports).
10. **CMD and ENTRYPOINT extraction** (capture both if present; either can define runtime behavior).
11. **HEALTHCHECK extraction**.
12. **USER extraction** (is a non-root user already set?).

### Output structure
A single Python object/dataclass bundling all of the above fields, with clean accessor methods. Downstream modules must never need to touch the raw `dockerfile-parse` output directly.

### Error handling requirements
- Missing/nonexistent file path → raise a clear, specific exception (not a raw stack trace)
- No `FROM` instruction at all (malformed file) → raise a clear exception identifying this specific problem
- Unresolvable `ARG`-based FROM → do not crash; set base image field to `"unknown_base_image"` and continue
- Empty or comment-only Dockerfile → handle gracefully, return an object with empty/default fields, do not crash
- When this parser is run across a batch of files (during evaluation), a single malformed file must not crash the whole batch — catch, log, and skip

### Test fixtures required
Build/gather at least the following Dockerfiles for testing:
- A standard single-stage Python app with chained RUN commands (`apt-get update && apt-get install -y ...`)
- An already multi-stage Dockerfile (2+ FROM instructions, with stage aliasing)
- A Dockerfile using `ARG`-based FROM (`ARG BASE_IMAGE=python:3.11` then `FROM ${BASE_IMAGE}`)
- A Dockerfile using `COPY --from=<stage>`
- A minimal/edge-case Dockerfile (very few instructions) to test graceful handling
- At least 2-3 realistic ML-flavored single-stage Dockerfiles (Streamlit, FastAPI+sklearn style) with dependencies like `lxml`, `psycopg2`, `numpy` in their paired `requirements.txt`

---

## 5. MODULE 2 — MANIFEST PARSER

### Purpose
Parse `requirements.txt` into a clean list of declared Python packages with their version constraints and extras.

### Library
`pip-requirements-parser`

### Required capabilities
- Extract package name, version specifier (e.g., `>=4.9.0`, `==2.3.0`), and extras (e.g., `torch[cuda]` → extras = `{"cuda"}`)
- Handle comments (`# this is a comment`)
- Handle `-r other-requirements.txt` includes (recursively parse, or at minimum flag them as unresolved rather than silently ignoring)
- Handle git/URL-based requirements (e.g., `git+https://github.com/...`) — these should be flagged as "non-PyPI, cannot resolve via wheel check or knowledge base" rather than crashing or being silently dropped
- Handle environment markers (e.g., `package; python_version >= "3.8"`) — at minimum, extract the package correctly and note the marker exists (full marker evaluation is optional/stretch)

### Output structure
A list of structured records: `{name, version_specifier, extras, raw_line, is_resolvable}` where `is_resolvable = False` for git/URL-based or otherwise non-standard entries.

---

## 6. MODULE 3 — STACK/LANGUAGE DETECTION

### Purpose
Determine the primary language/framework of the project and detect any secondary build-only stacks (e.g., a Node.js build step in an otherwise Python project), plus a coarse architecture classification.

### Algorithm: multi-signal weighted scoring
Do NOT use a single signal (e.g., "just check the FROM line"). Instead, implement a weighted scoring system across multiple signal types, since hybrid/polyglot Dockerfiles are common and the dominant runtime language isn't always the first `FROM`.

**Signals to check** (each contributes points to a candidate language's score if matched):
- Base image name pattern (e.g., `python*` → Python signal, high weight)
- Package manager commands present in RUN instructions (`pip install` → Python, medium weight; `npm install` → Node.js, medium weight)
- Framework/runtime keywords in CMD/ENTRYPOINT (`streamlit`, `gunicorn`, `uvicorn`, `flask` → Python, high weight; `node`, `npm start` → Node.js, high weight)
- Presence of manifest files referenced in COPY (`requirements.txt` → Python signal; `package.json` → Node.js signal, medium weight)

Sum weighted signals per candidate language; the highest-scoring language is `primary_language`. Any other language scoring above a secondary threshold is added to `secondary_languages` (used later to detect "this is a build-only auxiliary stack that can be entirely discarded from runtime").

### Architecture type detection (coarse, rule-based)
Classify into categories like `WEB_API`, `STATIC_SITE`, `BATCH_JOB`, `UNKNOWN` based on: EXPOSE presence (web-facing signal), CMD/ENTRYPOINT keywords (`uvicorn`/`gunicorn` → WEB_API), presence of a static-build-then-serve pattern (`npm run build` followed by copying to an nginx/static directory → STATIC_SITE).

---

## 7. MODULE 4 — DEPENDENCY CLASSIFICATION ENGINE (CORE CONTRIBUTION)

This is the most important module. It must be genuinely per-package, not a blanket rule.

### 7.1 Knowledge base schema
Store as structured data (JSON or YAML file, not hardcoded in Python code). Each entry:

```
{
  "name": "lxml",
  "version_range": "*",                          // or specific range if native deps differ by version
  "build_time_system_deps": ["libxml2-dev", "libxslt-dev", "gcc"],
  "runtime_system_deps": ["libxml2", "libxslt1.1"],
  "extras": {
      // only present for packages with variant-dependent deps, e.g. torch
  },
  "wheel_typically_available": false,
  "source": "curated",                            // "curated" | "conda-forge-derived" | "inferred"
  "confidence": 0.95
}
```

**Critical design point**: `build_time_system_deps` and `runtime_system_deps` are DIFFERENT lists with typically DIFFERENT package names (e.g., `libpq-dev` to build psycopg2, but only `libpq5` needed at runtime — the `-dev` package includes headers not needed post-compilation). Do not conflate these into one list.

**Initial target size**: 60-150 curated entries, prioritizing common ML/data/web packages: numpy, scipy, pandas, lxml, psycopg2, pillow, opencv-python, matplotlib, scikit-learn, torch, tensorflow, cryptography, pyyaml (usually pure), flask/fastapi/uvicorn/gunicorn (usually pure Python — should have empty dependency lists, serving as important "negative" examples that prove the tool doesn't over-flag everything).

**Data sourcing methodology** (for documentation/report purposes): entries seeded from (a) official package documentation and `setup.py`/`pyproject.toml` build requirements, (b) conda-forge feedstock `meta.yaml` files (these separate build vs. run requirements natively — `github.com/conda-forge/<package>-feedstock`), (c) Debian/Ubuntu package descriptions (`apt-cache show <package>-dev`), (d) manual test-builds to confirm/validate entries.

### 7.2 Wheel-availability check
Before trusting the knowledge base's `build_time_system_deps`, check whether a pre-built wheel exists for the target platform/Python version via the PyPI JSON API (`https://pypi.org/pypi/{package}/{version}/json`). Parse the `urls` list in the response for entries where `packagetype == "bdist_wheel"`, and check if the wheel filename's platform tag (e.g., `manylinux_2_17_x86_64`, `macosx_...`) and Python tag (e.g., `cp311`) match the target. If a matching wheel exists, that package requires **zero build-time system dependencies** for this specific installation, regardless of what the knowledge base states (the knowledge base's build deps only apply when compiling from source).

Cache results (e.g., in-memory dict keyed by `package+version+platform`) to avoid redundant API calls during a single run or across a benchmark loop.

### 7.3 Per-package classification algorithm
For each package parsed from `requirements.txt`:
1. Check wheel availability for the target platform/Python version.
2. If wheel available → `build_deps = []`; `runtime_deps` = knowledge base's runtime deps if the package is in the KB, else `[]`.
3. If wheel NOT available (source build required):
   - If package found in knowledge base → use its `build_time_system_deps` and `runtime_system_deps`.
   - If package NOT found in knowledge base → default to a conservative fallback (assume it might need common build tools; mark `confidence = "unresolved-conservative"`) AND log this package to an "unresolved packages" list for later knowledge-base expansion.

### 7.4 Reconciling against apt/system packages already declared in the input Dockerfile
For each apt package extracted by Module 1:
1. If it appears in the union of all `runtime_system_deps` computed above → label `RUNTIME`.
2. Else if it appears in the union of all `build_time_system_deps` computed above → label `BUILD_ONLY`.
3. Else if it matches a small curated fallback list of generically-known build tools (e.g., `gcc`, `g++`, `make`, `git`, `cmake`, `build-essential`) → label `BUILD_ONLY`.
4. Else if it is referenced by name inside the Dockerfile's `CMD`, `ENTRYPOINT`, or `HEALTHCHECK` instructions (e.g., `curl` used in a HEALTHCHECK) → label `RUNTIME` (safety override — this catches cases where a tool is genuinely needed at runtime for a reason unrelated to Python packages).
5. Else → label `UNKNOWN_CONSERVATIVE` (default to keeping it in the runtime stage to avoid breaking the build; flag for manual review in the output report).

**This step-by-step, per-item reconciliation — as opposed to one blanket label for the whole apt-get block — is the direct, demonstrable difference from StageCraft's manifest-wide inheritance rule.**

### 7.5 Output of this module
A structured classification result containing: per-Python-package classification (with confidence/source), per-apt-package label, and lists of "unresolved" items for both categories.

---

## 8. MODULE 5 — NECESSITY SCORING

### Purpose
Decide whether the input Dockerfile is worth refactoring at all, before doing the work of synthesis. Prevents wasted effort on already-minimal Dockerfiles.

### Formula
```
S_bloat = (c1 * count_of_detected_build_only_system_packages)
        + (c2 * 1_if_any_dev_header_packages_present_else_0)
        + (c3 * 1_if_secondary_build_only_language_stack_detected_else_0)

S_efficiency = derived from instruction-ordering anti-pattern detection
               (e.g., COPY of source code placed BEFORE dependency installation —
               a caching anti-pattern per the "stable dependencies first" principle)
               Score from 0-100 "efficiency", converted to a penalty score if below a threshold.

S_security = weighted sum of detected risks:
             - running as root (no USER instruction, or explicit USER root)
             - hardcoded secrets in ENV/ARG (heuristic: variable names containing
               "KEY", "SECRET", "PASSWORD", "TOKEN")
             - presence of known-risky leftover build tools (e.g., curl/git with
               no clear runtime justification)

S_total = w_b * S_bloat + w_e * S_efficiency + w_s * S_security
          (default all weights = 1, configurable)

Proceed with refactoring if S_total >= THRESHOLD (default threshold: calibrate
during development; start around 20-30 and adjust based on observed behavior
across test fixtures — document whatever value is chosen and why).
```

If the score is below threshold, the tool should still output a message explaining why it decided not to refactor (e.g., "no significant build-time bloat detected"), not just silently do nothing.

---

## 9. MODULE 6 — MULTI-STAGE SYNTHESIS

### Purpose
Generate the actual optimized Dockerfile text.

### 9.1 Base image selection
- Builder stage: use a full-featured base image matching the detected primary language and, where determinable, the original Dockerfile's base image family/version (e.g., if original was `python:3.8`, builder stays `python:3.8` unless there's a strong reason to change the version — do not silently change major versions).
- Runtime stage: 
  - If a CUDA/GPU signal was detected (e.g., `torch` with a cuda extra, or explicit CUDA references) → use a matching `nvidia/cuda:*-runtime-*` base image.
  - Else for Python → use the official `-slim` variant of the same Python version.
  - (Do not implement distroless/Go-specific logic — out of scope per Section 2.2.)

### 9.2 Builder stage generation
Must include, in this order (for correct Docker layer caching — dependencies before source code):
1. `FROM <builder_base> AS builder`
2. `RUN apt-get update && apt-get install -y <build-only system deps, space-separated>` (only if the list is non-empty)
3. `WORKDIR` (preserve original if present, else default to something reasonable like `/app`)
4. `COPY requirements.txt .`
5. `RUN pip install --no-cache-dir --prefix=/install -r requirements.txt` (installing to an isolated prefix directory makes the subsequent copy to the runtime stage clean and precise)
6. `COPY . .` (the application source code)

### 9.3 Runtime stage generation
1. `FROM <runtime_base>`
2. `RUN apt-get update && apt-get install -y --no-install-recommends <runtime-only system deps> && rm -rf /var/lib/apt/lists/*` (only if the list is non-empty)
3. `COPY --from=builder /install /usr/local`
4. `COPY --from=builder <app dir> <app dir>`
5. Preserve from the original Dockerfile: `WORKDIR`, all `ENV` declarations, `EXPOSE`, `HEALTHCHECK`, and the original `CMD`/`ENTRYPOINT` exactly as they were (do not alter runtime behavior — only the build/dependency structure changes).
6. Production hardening: add a non-root `USER` if the original didn't already have one (create a simple unprivileged user, e.g. `appuser`, and switch to it before the final CMD/ENTRYPOINT).

### 9.4 Fallback behavior for artifact copying
If the tool cannot confidently determine a narrower set of files to copy (which will usually be the case for typical Python web apps, unlike compiled-binary languages), fall back to copying the entire application directory (`COPY . .` from the builder stage, or `COPY --from=builder /app /app` into runtime) rather than attempting incorrect selective copying. This is an intentional, documented trade-off, not a bug — note it as such in code comments and in the eventual project report.

### 9.5 .dockerignore generation
Generate a `.dockerignore` file alongside the new Dockerfile, composed from:
- General exclusions: `.git`, `.gitignore`, `README*`, `.vscode`, `.idea`
- Python-specific exclusions: `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`, `.venv`, `venv/`
- Any test directories detected in the project (e.g., `tests/`, `test/`) if they exist and are not needed at runtime

---

## 10. MODULE 7 — BUILD VALIDATION

### Purpose
Verify the synthesized Dockerfile actually produces a working image, and turn failures into actionable feedback.

### Algorithm
1. Run `docker build -t <test-tag> .` (via Python `subprocess` or `docker-py`) against the synthesized Dockerfile in its target project directory.
2. If exit code is 0 → SUCCESS. Additionally capture: final image size (`docker images` / `docker inspect`), layer count, and build duration — these are your evaluation metrics.
3. If exit code is non-zero → FAILURE. Parse `stderr`/build log text to classify the failure:
   - Pattern like `<library>.so[.\d]*: cannot open shared object file` → classify as **missing runtime system dependency**; extract the library name; log this as a knowledge-base gap (which package's `runtime_system_deps` entry was likely incomplete).
   - Pattern like `gcc: command not found`, `error: command 'gcc' failed`, or similar compiler-not-found errors during a `pip install` step → classify as **missing build-time system dependency**; log as a knowledge-base gap.
   - Anything else → classify as **unclassified failure**; log the raw error for manual review.
4. All logged gaps should be written to a persistent log/file (not just printed) so they can be used to expand the knowledge base later — this closes the loop between validation and knowledge-base improvement, and doubles as real data for the eventual "threats to validity" / limitations discussion in the project report.

---

## 11. MODULE 8 — REPORTING

### Purpose
Produce a structured, human-readable and machine-readable (JSON) summary of what the tool did and why, for both practical trust/auditability and for use as raw evaluation data.

### Required report fields
```
{
  "input_dockerfile": "<path>",
  "necessity_score": {"total": ..., "bloat": ..., "efficiency": ..., "security": ..., "proceeded": true/false},
  "packages_classified": [
     {"name": ..., "version": ..., "build_deps": [...], "runtime_deps": [...], "confidence_source": "kb" | "kb+wheel" | "inferred-wheel" | "unresolved-conservative"}
  ],
  "apt_packages_labeled": [
     {"name": ..., "label": "BUILD_ONLY" | "RUNTIME" | "UNKNOWN_CONSERVATIVE"}
  ],
  "unresolved_packages_for_kb_expansion": [...],
  "build_validation_result": {"status": "SUCCESS" | "FAILURE", "failure_category": "...", "image_size_bytes": ..., "layer_count": ...},
  "estimated_size_reduction_percent": ...
}
```

---

## 12. TECH STACK

- **Language**: Python 3.x throughout
- **Dockerfile parsing**: `dockerfile-parse`
- **Manifest parsing**: `pip-requirements-parser`
- **Live dependency resolution**: `requests` (calling the PyPI JSON API: `https://pypi.org/pypi/{package}/{version}/json`)
- **Docker orchestration/validation**: `docker` (the `docker-py` SDK) and/or Python's `subprocess` module for CLI invocation
- **Knowledge base storage**: a JSON or YAML file (not a database — keep it simple and human-editable)
- **Testing**: `pytest` recommended for unit tests on each module

---

## 13. SUGGESTED PROJECT FILE STRUCTURE

```
project_root/
├── parser/
│   ├── dockerfile_parser.py       # Module 1
│   └── manifest_parser.py         # Module 2
├── detection/
│   └── stack_detector.py          # Module 3
├── classification/
│   ├── knowledge_base.json        # curated package data (Section 7.1)
│   ├── wheel_resolver.py          # Section 7.2
│   └── classifier.py              # Sections 7.3-7.5
├── scoring/
│   └── necessity_scorer.py        # Module 5
├── synthesis/
│   └── dockerfile_synthesizer.py  # Module 6
├── validation/
│   └── build_validator.py         # Module 7
├── reporting/
│   └── report_generator.py        # Module 8
├── test_fixtures/                 # sample Dockerfiles + requirements.txt (see Section 4)
├── tests/                         # pytest unit tests per module
├── benchmark/
│   └── run_evaluation.py          # runs the tool + baselines across the benchmark repo corpus
├── main.py                        # CLI entry point, wires all modules together
└── requirements.txt               # this project's OWN dependencies (not to be confused with test fixtures)
```

---

## 14. EVALUATION METHODOLOGY (for later, but good to know upfront)

- Assemble a benchmark corpus of 50-80 real-world Python/ML repositories with single-stage Dockerfiles (target frameworks: Streamlit, FastAPI+ML libraries, Flask+sklearn/torch apps).
- Run this tool, StageCraft (their released tool — check the paper's data availability link), and PARFUM against the same corpus.
- Metrics to collect per tool: build success rate, average image size reduction (%), average layer count reduction, classification accuracy (spot-checked manually against a sample), and — uniquely for this project — the number of packages within a single manifest that received *different* build/runtime classifications from each other (this metric doesn't even apply meaningfully to StageCraft, since it can't produce mixed classifications within one manifest — this is a good way to directly demonstrate the core contribution empirically).

---

## 15. KNOWN LIMITATIONS TO DOCUMENT HONESTLY (do not try to solve these — just handle gracefully and note them)

- Knowledge base coverage is necessarily incomplete (60-150 packages); unresolved packages default conservative.
- No transitive dependency resolution (only directly-declared packages in requirements.txt are analyzed).
- No support for extras-aware variant resolution beyond a basic CUDA/CPU signal check for well-known packages like torch.
- Artifact copying falls back to whole-directory copying for most Python web app cases (Section 9.4) — this is intentional, not a flaw.
- No support for private/internal package indexes.

---

## 16. DEFINITION OF DONE (per module, for incremental progress tracking)

- **Module 1 (Dockerfile parser)**: all extraction methods in Section 4 implemented and correctly verified by eye against every test fixture; error handling cases in Section 4 all handled without crashing.
- **Module 2 (Manifest parser)**: correctly extracts name/version/extras from a realistic `requirements.txt`; correctly flags non-standard entries (git URLs) as unresolvable rather than crashing.
- **Module 3 (Stack detection)**: correctly identifies Python as primary language on all Python test fixtures; correctly identifies a secondary Node.js stack on at least one hybrid test fixture.
- **Module 4 (Classification engine)**: correctly produces DIFFERENT classifications for a pure-Python package (e.g., flask) vs. a compiled package (e.g., lxml) within the same requirements.txt — this specific test case is the most important one in the whole project, as it's the direct, demonstrable proof of the core contribution.
- **Module 5 (Necessity scoring)**: produces a numeric score and correctly gates (proceeds/stops) on at least one clearly-bloated and one clearly-minimal test fixture.
- **Module 6 (Synthesis)**: produces a syntactically valid Dockerfile for every test fixture.
- **Module 7 (Validation)**: successfully runs `docker build` against synthesized output for at least the majority of test fixtures, and correctly classifies at least one induced failure case (e.g., deliberately remove a needed runtime lib and confirm the validator correctly flags it as a missing-runtime-dependency failure).
- **Module 8 (Reporting)**: produces valid, complete JSON matching the schema in Section 11 for every run.

---

END OF SPECIFICATION. If any instruction above is ambiguous or conflicts with a practical implementation constraint discovered during coding, flag it explicitly rather than silently resolving it in a way that might diverge from the intended design.
