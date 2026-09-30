# PROJECT SPECIFICATION — v2
## Domain-Aware Dockerfile Refactoring for ML Workloads (Package-Level Dependency Resolution)

**Version history**
- **v1** — original specification. Archived unchanged as `PROJECT_SPEC_v1_original.md`.
- **v2** — this document. Patches eleven identified gaps. All changes are marked inline
  with a `> **[v2 PATCH]**` callout stating what changed and why, so the delta against
  v1 stays auditable. Section numbering is preserved from v1 wherever possible; new
  material is added as sub-sections or as new Section 17.

This document is a complete, self-contained specification. Read it fully before writing any code. It contains the problem context, the research gap being addressed, the full system architecture, algorithms for every component, data schemas, and implementation guidance. Nothing outside this document should be assumed — if something is genuinely ambiguous, flag it rather than guessing.

---

## 0. SUMMARY OF v2 CHANGES

Eleven gaps were identified against v1. Four were raised in review; five more were found
while patching those four; a further two (G1b, G1c) were found by auditing the v2 patches
themselves and are defects the fixes introduced rather than inherited from v1. Each is
listed with the section that fixes it, so a reader can jump straight to the delta.

That the G1 fix introduced G1b is worth noting in the write-up as a methodological point:
libc consistency is a *whole-pipeline* invariant, not a property of the wheel check alone,
and patching it in one place while leaving the builder unconstrained recreated the same
crash by a different route. The §16 assertions exist to make the invariant checkable rather
than remembered.

| # | Gap | Severity | Fixed in |
|---|-----|----------|----------|
| G1 | **libc mismatch (musl vs glibc).** v1 §7.2 checked only the platform architecture tag. A `manylinux` wheel is built against `glibc`; installing it on an Alpine (`musl`) runtime produces a container that builds successfully and then crashes on first import. | **Critical** — silently inverts the tool's core correctness claim | §7.2, §7.2.1, §9.1 |
| G1b | **libc mismatch across the venv copy.** Found during v2 self-audit: "builder inherits the original base" (§9.1) and "runtime is never Alpine" (§9.1) are each correct but jointly produce a musl builder feeding a glibc runtime for Alpine inputs, and §9.3 copies `/opt/venv` across that boundary. Same crash as G1, reached by a different route — introduced by the G1 fix itself. | **Critical** | §9.1 builder rule |
| G1c | **Input vs output distro family conflated in KB lookups.** Found during v2 self-audit: §7.3 selected `runtime_system_deps[distro_family]` using the *input's* family, so an Alpine input would emit Alpine package names (`libxslt`) into the Debian runtime's `apt-get install` line, where the correct name is `libxslt1.1`. | Medium — loud build failure, but confusing to diagnose | §7.3 |
| G2 | **Build success ≠ working image.** v1 §10 declared SUCCESS on `docker build` exit code 0. Every failure mode introduced by G1, and any missing runtime shared library, occurs at *container start*, not at build. v1's validator was structurally incapable of detecting the tool's most likely error class. | **Critical** — would report false successes across the whole evaluation | §10.2, §10.3 |
| G3 | **Manifest myopia (PEP 517/621).** v1 §2.1 accepted only `requirements.txt`. Modern Python packaging has largely moved to `pyproject.toml`, `poetry.lock`, `Pipfile.lock`, and `uv.lock`. | High — reviewers will question real-world applicability | §2.1, §5.4, §15 |
| G4 | **Prefix clobbering.** v1 §9.3 did `COPY --from=builder /install /usr/local`, dumping a custom prefix tree over the runtime image's `/usr/local` and potentially overwriting system binaries and symlinks. Separately, `--prefix` bakes non-existent shebang paths into console scripts. | High — breaks containers, two distinct bugs | §9.2, §9.3 |
| G5 | **Corpus bias.** v1 §14 said "gather 50-80 real-world repositories." A random sample is dominated by pure-Python projects, on which this tool and StageCraft produce byte-identical output — i.e. a random corpus structurally cannot show the contribution. | High — a null result caused by sampling, not by the method | §14.1 |
| G6 | **Baseline availability was assumed.** v1 §14 said to run StageCraft and PARFUM on the corpus, parenthetically noting "check the paper's data availability link." If either tool is unavailable, unbuildable, or not reproducible, the entire comparative evaluation has no fallback. | High — single point of failure for the results chapter | §14.2 |
| G7 | **Non-deterministic wheel checks.** PyPI is a live, mutating service. An unpinned `requirements.txt` line (`lxml`, no specifier) resolves to whatever is newest *at run time*, so classifications are not reproducible and the evaluation cannot be re-run. | Medium — reproducibility, directly attacked in review | §7.2.2, §14.4 |
| G8 | **Python ABI tag matching underspecified.** v1 §7.2 said match "the Python tag (e.g. `cp311`)" but real wheels use `cp311-cp311`, `cp39-abi3`, `py3-none-any`, and free-threaded `cp313t`. Naive string matching mis-classifies `abi3` and universal wheels. | Medium — silent per-package misclassification | §7.2.3 |
| G9 | **"Classification accuracy" had no ground truth.** v1 §14 listed accuracy as a metric "spot-checked manually," which is not a measurement procedure and is not defensible as one. | Medium — an unfalsifiable headline metric | §14.3 |

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

> **[v2 NOTE — cite these properly.]** The "multi-gigabyte average bloat" figure and both
> CVE identifiers are inherited from v1 without a traced source. Before either appears in
> the report, locate the primary source and cite it, or restate the claim as a measurement
> this project makes itself on its own corpus (§14 collects exactly this data). Do not carry
> an uncited quantitative claim into a peer-reviewed submission — this is the single easiest
> thing for a reviewer to reject, and the corpus measurement is a stronger claim anyway
> because it is yours.

### 1.2 The established solution: multi-stage Docker builds
Docker supports **multi-stage builds**: a Dockerfile can contain multiple `FROM` instructions, each starting a new "stage." A common pattern:
- **Builder stage**: uses a full-featured base image, installs compilers and build tools, compiles/installs all dependencies, builds the application
- **Runtime stage**: uses a minimal base image (e.g., a `-slim` variant), and uses `COPY --from=builder <src> <dst>` to copy over *only* the compiled artifacts and runtime-necessary files — none of the build tooling is carried over, because the builder stage is discarded

This is a well-established best practice, but manually refactoring an existing single-stage Dockerfile into a correct multi-stage one is non-trivial and error-prone (you must correctly identify what's build-only vs. runtime-necessary, or you risk breaking the application).

### 1.3 The existing automated solution and its specific, documented gap
A tool called **StageCraft** was published in 2026 (Chen, Yang, Pan, Zhou, "Automating Dockerfile Refactoring to Multi-stage Builds," *Proc. ACM Softw. Eng.* 3, FSE, Article FSE070, July 2026) that automates this single-stage-to-multi-stage refactoring. It works in three phases: (1) static analysis of the Dockerfile to detect language/stack and classify dependencies, (2) a necessity-scoring gate to decide if refactoring is worthwhile, (3) synthesis of the new multi-stage Dockerfile.

**The critical, documented gap this project addresses**: StageCraft's own paper states, in its discussion section: *"we deliberately restrict the input scope to the Dockerfile rather than the full project context to avoid the complexity of parsing heterogeneous build systems... we acknowledge that taking the whole project context, including source code, build scripts, and manifests, as input could further improve the quality of multi-stage builds, and we consider the exploration of such hybrid analysis strategies a promising direction for future research."*

**In plain terms: StageCraft never parses the *contents* of `requirements.txt` (or any other Python dependency manifest).** Instead, for Python dependencies, it uses what it calls an "inheritance rule" (Section 3.2.2 of their paper): if the `pip` package manager is used at runtime (referenced in the final runtime-relevant Dockerfile instructions), **all** packages pip ever installed are labeled "runtime." If pip is only used during build, **all** its packages are labeled "build-time." This is a single, all-or-nothing decision made at the package-manager level — it never looks inside the manifest to see which *individual* packages actually need what. (Precisely: StageCraft's language-identification step, §3.2.1, does treat the *string* `COPY requirements.txt` as one low-weight language cue while scanning the Dockerfile line-by-line, but it never opens the file or enumerates its packages — so it still cannot tell `flask` from `lxml`.)

**Consequence**: a `requirements.txt` containing both `flask` (pure Python, no native dependencies at all) and `lxml` (needs a compiler and dev headers to build, and separate runtime shared libraries afterward) would have both packages receive the identical classification under StageCraft's rule — even though their actual native dependency needs are completely different.

> **[v2 PATCH — VERIFIED against the paper on 2026-09-04. See `docs/phase0_verification.md`.]**
> The block quote above and the "Section 3.2.2 / inheritance rule" attribution are the
> load-bearing justification for this project, so they were checked against the published
> article (`3797098.pdf`). Result: (a) the quote is **verbatim** and in the discussion
> section (§4.4 Discussions, p. FSE070:18); (b) the inheritance rule is in **§3.2.2**,
> named exactly that, and works as characterised; (c) StageCraft **does not parse manifest
> contents** — §4.4 lists "manifests" among the project context it deliberately excludes,
> and §3.2.2 labels pip packages by package-manager usage, not by inspection.
> **Framing correction from that review:** StageCraft *does* classify system-level (apt)
> packages per-item, so the genuine contribution is at the application/pip layer only —
> see the corrected note in §7.4. The core framing and the flask-vs-lxml test stand.

### 1.4 This project's contribution
This project builds a tool that **directly parses the Python dependency manifest** and resolves **each individually declared package's** actual build-time and runtime native dependency requirements — using (a) a curated knowledge base of known package-to-native-dependency mappings, and (b) a live check against the PyPI JSON API to determine whether a pre-built wheel exists for the target platform *and target libc* (which, if true, means **no compilation happens at all** for that package, regardless of what the knowledge base would otherwise suggest).

This enables **package-level precision**: within a single manifest, some packages can be correctly classified as needing zero extra native dependencies, while others are correctly identified as needing specific build-time tools that get isolated to the discarded builder stage — something StageCraft's manager-level rule cannot do, by its own authors' explicit design and admission.

**The contribution is falsifiable in one test.** Given a manifest containing `flask` and
`lxml`, this tool must emit different native-dependency sets for the two packages, and
StageCraft must emit the same label for both. That single divergence is the contribution;
everything else in this specification exists to make that divergence measurable, correct,
and reproducible. See §16 Module 4 and §14.

---

## 2. SCOPE

### 2.1 In scope
- Input: a single-stage Dockerfile + a Python dependency manifest
- Output: an optimized multi-stage Dockerfile + a `.dockerignore` file + a JSON classification report
- Target ecosystem: Python/ML web applications and services (Streamlit, FastAPI, Flask, generic Python scripts) — this is a **Python-and-ML-specific tool**, not a general multi-language tool
- Validation: the tool must attempt an actual `docker build` of its own output **and then start the resulting container to confirm the application's dependencies actually import** (§10)

> **[v2 PATCH — G3: manifest scope.]** v1 named `requirements.txt` as the only accepted
> manifest. That is too narrow to defend, but full support for every modern format is out
> of scope for the time available. The v2 position is a deliberate two-tier split:
>
> **Tier 1 — fully supported (parse and classify):** `requirements.txt`, including
> `-r` includes and the constraint/marker syntax described in §5.
>
> **Tier 2 — detect and report, do not classify:** `pyproject.toml` (PEP 621
> `[project.dependencies]` and Poetry's `[tool.poetry.dependencies]`), `Pipfile`,
> `poetry.lock`, `Pipfile.lock`, `uv.lock`, `setup.py`, `setup.cfg`, `environment.yml`.
> When one of these is present, the tool must say so explicitly in its report and in its
> CLI output — naming the file and stating that its dependencies were **not** analysed.
>
> Rationale for the split: a tool that silently ignores `pyproject.toml` and reports a
> confident classification is *wrong*, because it has classified an incomplete dependency
> set. A tool that detects it and says "this project also declares dependencies in
> pyproject.toml, which this version does not parse" is *honestly scoped*. The second is
> defensible in review; the first is not. Detection is roughly an hour of work — a
> filename glob plus a top-level key check — and it converts an attackable omission into
> a stated limitation. See §5.4 for the detection requirement and §15 for the wording.

### 2.2 Out of scope (explicitly — do not build these)
- Multi-language support (Go, Java, Node.js as primary languages) — Python is the sole target language for classification depth; if a secondary language stack is detected (e.g., a Node.js frontend build step), treat it as a black box to isolate/discard, not to classify in depth
- GPU/CUDA-specific dependency resolution beyond a basic "does this project use torch/tensorflow with CUDA" signal check — full CUDA version matrix resolution is future work
- A web UI — this is a CLI tool
- Handling private/internal PyPI indexes — public PyPI only
- Full dependency-graph (transitive dependency) resolution — the tool reasons about the packages explicitly listed in the manifest, not their sub-dependencies' sub-dependencies
- **Tier 2 manifest parsing** (§2.1) — detected and reported, never parsed for classification
- **Selecting Alpine/musl as a base for either the builder or the runtime stage** — see
  §9.1 for the policy and its rationale. Alpine inputs are *detected* and rebased, never
  propagated.

---

## 3. SYSTEM ARCHITECTURE — PIPELINE OVERVIEW

```
[Dockerfile] + [Python manifest]
        |
        v
  [1] PARSING LAYER
        |  -> structured Dockerfile object (base image, libc family, RUN commands, COPY,
        |     CMD/ENTRYPOINT, EXPOSE, ENV, ARG, WORKDIR, USER, HEALTHCHECK)
        |  -> structured manifest object (list of package name + version specifier + extras)
        |  -> Tier 2 manifest presence flags (detected, not parsed)
        v
  [2] STACK/LANGUAGE DETECTION
        |  -> primary language (Python), secondary languages if any (e.g., Node.js build step)
        |  -> architecture type (WEB_API, STATIC_SITE, BATCH_JOB, etc.)
        v
  [3] DEPENDENCY CLASSIFICATION ENGINE  <-- core contribution
        |  -> resolve target platform triple: (arch, libc, python ABI)
        |  -> for each Python package: build-time system deps, runtime system deps
        |     (via knowledge base + libc-aware live wheel check)
        |  -> for each apt/system package already in the Dockerfile:
        |     BUILD_ONLY / RUNTIME / UNKNOWN_CONSERVATIVE label
        v
  [4] NECESSITY SCORING
        |  -> composite score (bloat + efficiency + security); gate decision: proceed or stop
        v
  [5] MULTI-STAGE SYNTHESIS
        |  -> generates builder stage (venv-based) + runtime stage Dockerfile text
        |  -> generates .dockerignore
        v
  [6] BUILD VALIDATION
        |  -> attempts real `docker build`
        |  -> THEN starts the container and import-smoke-tests every declared package
        |  -> classifies failures, logs knowledge-base gaps
        v
  [7] REPORTING
        |  -> JSON report: what was classified how, confidence levels, size/metric estimates,
        |     resolved platform triple, pinned versions used for wheel checks
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
   > **[v2 PATCH — G1 support.]** Also detect `apk add` (Alpine), and record which
   > package manager was used. An Alpine input Dockerfile installs `libxml2-dev`
   > under a different name than Debian does, and the classifier must not silently
   > compare apk package names against an apt-derived knowledge base. If `apk` is
   > detected, set `libc_family = "musl"` (§4a) and mark extracted system packages
   > with `pkg_manager = "apk"` so §7.4 can skip reconciliation rather than produce
   > confidently wrong labels.
5. **Pip install line detection**: detect `RUN pip install` / `RUN pip3 install` commands and note their position/context in the Dockerfile (this is used later for cross-referencing, and for comparison against how StageCraft's inheritance rule would classify things).
6. **COPY/ADD extraction**: capture source and destination paths for all `COPY`/`ADD` instructions; distinguish a plain host-context `COPY` from a cross-stage `COPY --from=<stage>`.
7. **ENV and ARG extraction**: capture all declared environment variables and build arguments.
8. **WORKDIR extraction**.
9. **EXPOSE extraction** (list of ports).
10. **CMD and ENTRYPOINT extraction** (capture both if present; either can define runtime behavior).
11. **HEALTHCHECK extraction**.
12. **USER extraction** (is a non-root user already set?).

### 4a. Base image interpretation — NEW in v2

> **[v2 PATCH — G1.]** The parser must decompose the base image reference into fields the
> classifier can reason about, rather than passing a raw string downstream. This is the
> structural fix that makes the libc-aware wheel check in §7.2 possible at all.

From the resolved base image string, extract:

| Field | Meaning | Examples |
|---|---|---|
| `registry` | optional registry host | `docker.io`, `nvcr.io` |
| `image_name` | repository path | `python`, `nvidia/cuda` |
| `tag` | raw tag string | `3.11-slim-bookworm`, `12.4.1-runtime-ubuntu22.04` |
| `python_version` | `(major, minor)` if determinable, else `None` | `(3, 11)` |
| `libc_family` | `"glibc"` \| `"musl"` \| `"unknown"` | see rules below |
| `distro_family` | `"debian"` \| `"alpine"` \| `"ubuntu"` \| `"unknown"` | drives apt vs apk |
| `variant` | `"slim"` \| `"alpine"` \| `"full"` \| `"runtime"` \| `"devel"` \| `None` | |

**libc determination rules**, applied in order:
1. Tag or image name contains `alpine` → `musl` / `alpine`.
2. Tag contains `slim`, `bookworm`, `bullseye`, `trixie`, `buster`, or image is bare
   `python:<ver>` → `glibc` / `debian`.
3. Image or tag contains `ubuntu` → `glibc` / `ubuntu`.
4. A `RUN` command uses `apk add` → `musl` / `alpine` (overrides an unknown tag).
5. A `RUN` command uses `apt-get`/`apt` → `glibc` (family stays `unknown` if the
   tag gave no distro signal).
6. Otherwise → `unknown` / `unknown`.

**When `libc_family == "unknown"`, the classifier must not assume glibc.** It must treat
the wheel check as inconclusive and fall back to the conservative source-build path
(§7.3 step 4). Assuming glibc on an unknown base is precisely the failure mode G1
describes; guessing in the safe direction costs image size, guessing in the unsafe
direction produces a broken container.

**Python version fallback.** If `python_version` cannot be determined from the tag (e.g.
`FROM ubuntu:22.04` followed by `apt-get install python3`), it must be supplied by the
`--python-version` CLI flag, defaulting to the interpreter running the tool, and the
report must record that the value was assumed rather than detected. Wheel ABI matching
(§7.2.3) is meaningless without a concrete version.

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
- **[v2]** An Alpine-based Python Dockerfile using `apk add`, with `lxml` in its manifest —
  the G1 regression fixture. The tool must detect `input_libc = musl` and
  `distro_family = alpine`, must rebase **both** stages to Debian (§9.1), and must then
  resolve wheels against `target_libc = glibc` — so a `manylinux` wheel **is** correctly
  usable here, because the builder it emits is Debian. Reporting no match would be
  systematically over-conservative (§7.2). The fixture's assertions are therefore:
  musl *detected*, both stages rebased and reported, `manylinux` wheel matched.
- **[v2]** A synthetic musl-*target* unit test (not a fixture Dockerfile) that forces
  `target_libc = musl` directly, to exercise the §7.2.1 exclusion rules. Since §9.1 never
  emits a musl target, this path is unreachable end-to-end and must be tested at the
  wheel-resolver level or it will silently rot.
- **[v2]** A Dockerfile with an indeterminable libc (`FROM ubuntu:22.04` + `apt-get install python3`)
  to exercise the `unknown` → conservative path.

---

## 5. MODULE 2 — MANIFEST PARSER

### Purpose
Parse the Python dependency manifest into a clean list of declared packages with their version constraints and extras.

### Library
`pip-requirements-parser`

### 5.1 Required capabilities (Tier 1 — `requirements.txt`)
- Extract package name, version specifier (e.g., `>=4.9.0`, `==2.3.0`), and extras (e.g., `torch[cuda]` → extras = `{"cuda"}`)
- Handle comments (`# this is a comment`)
- Handle `-r other-requirements.txt` includes (recursively parse, or at minimum flag them as unresolved rather than silently ignoring)
- Handle git/URL-based requirements (e.g., `git+https://github.com/...`) — these should be flagged as "non-PyPI, cannot resolve via wheel check or knowledge base" rather than crashing or being silently dropped
- Handle environment markers (e.g., `package; python_version >= "3.8"`) — at minimum, extract the package correctly and note the marker exists (full marker evaluation is optional/stretch)

### 5.2 Name normalisation — NEW in v2

> **[v2 PATCH.]** Normalise every extracted name per PEP 503 before any knowledge-base
> or PyPI lookup: lowercase, and collapse runs of `-`, `_`, and `.` to a single `-`.
> `scikit_learn`, `Scikit-Learn`, and `scikit.learn` are the same distribution, and a
> knowledge base keyed on unnormalised strings will miss on real manifests while
> appearing to work on hand-written fixtures. Keep the raw string for reporting; use the
> normalised form as the lookup key.
>
> Note also that the **import name** frequently differs from the **distribution name**
> (`opencv-python` → `cv2`, `scikit-learn` → `sklearn`, `pillow` → `PIL`,
> `psycopg2-binary` → `psycopg2`, `attrs` → `attr`, `beautifulsoup4` → `bs4`,
> `pyyaml` → `yaml`, `python-dateutil` → `dateutil`). The smoke test in §10.2 imports
> modules, so the knowledge base needs an `import_names` field (§7.1) and the mapping
> cannot be derived by string transformation. Populate it during curation.

### 5.3 Output structure
A list of structured records: `{name, normalized_name, version_specifier, extras, markers, raw_line, is_resolvable}` where `is_resolvable = False` for git/URL-based or otherwise non-standard entries.

### 5.4 Tier 2 manifest detection — NEW in v2

> **[v2 PATCH — G3.]** Scan the project directory for the Tier 2 files listed in §2.1.
> For each found, record `{filename, format, declares_dependencies: bool}`. Determine
> `declares_dependencies` cheaply — for `pyproject.toml`, load it and check for
> `[project].dependencies`, `[project].optional-dependencies`, or
> `[tool.poetry.dependencies]`; a `pyproject.toml` containing only `[build-system]` or
> tool config (black, ruff, mypy) declares no runtime dependencies and should not raise a
> warning. Do **not** parse dependency contents.
>
> If any Tier 2 file declares dependencies, the report must carry
> `"unanalyzed_manifests"` and the CLI must print a clear warning: the classification
> covers only the Tier 1 manifest and may be incomplete. If a Tier 2 manifest declares
> dependencies and **no** Tier 1 manifest exists, the tool must refuse to synthesise and
> exit with a clear explanation — emitting a Dockerfile based on zero known dependencies
> is worse than emitting nothing.

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

> **[v2 NOTE — record the weights, don't just pick them.]** The specific weights and the
> secondary threshold are free parameters. Whatever values are chosen must be stored in a
> config file rather than as literals in code, and the report must record the values used.
> Expect to be asked in review how sensitive the results are to these numbers; a config
> file makes a sensitivity table a loop instead of a refactor. If Python is the primary
> language in 100% of the curated corpus (likely, given §14.1), say so plainly and note
> that this module is consequently doing little discriminative work on this corpus — it
> exists for correct handling of hybrid projects, and that is where it should be evaluated.

### Architecture type detection (coarse, rule-based)
Classify into categories like `WEB_API`, `STATIC_SITE`, `BATCH_JOB`, `UNKNOWN` based on: EXPOSE presence (web-facing signal), CMD/ENTRYPOINT keywords (`uvicorn`/`gunicorn` → WEB_API), presence of a static-build-then-serve pattern (`npm run build` followed by copying to an nginx/static directory → STATIC_SITE).

---

## 7. MODULE 4 — DEPENDENCY CLASSIFICATION ENGINE (CORE CONTRIBUTION)

This is the most important module. It must be genuinely per-package, not a blanket rule.

### 7.1 Knowledge base schema

Store as structured data (JSON or YAML file, not hardcoded in Python code). Each entry:

```jsonc
{
  "name": "lxml",                                  // PEP 503 normalised
  "version_range": "*",                            // or specific range if native deps differ by version
  "import_names": ["lxml.etree"],                  // [v2] for the §10.2 smoke test
  "build_time_system_deps": {                      // [v2] keyed by distro family
    "debian": ["libxml2-dev", "libxslt-dev", "gcc"],
    "alpine": ["libxml2-dev", "libxslt-dev", "musl-dev", "gcc"]
  },
  "runtime_system_deps": {
    "debian": ["libxml2", "libxslt1.1"],
    "alpine": ["libxml2", "libxslt"]
  },
  "extras": {},                                    // variant-dependent deps, e.g. torch
  "wheel_typically_available": true,               // [v2] see note below
  "source": "curated",                             // "curated" | "conda-forge-derived" | "inferred"
  "confidence": 0.95,
  "verified_by_build": false,                      // [v2] set true once §10 confirms it
  "notes": ""
}
```

> **[v2 PATCH — three schema changes.]**
>
> **(a) System deps are keyed by distro family.** v1 had flat lists that were implicitly
> Debian, which would be silently misapplied the moment a second distro entered the
> picture. Keying them makes the assumption explicit.
>
> **The `debian` key is mandatory; `alpine` is optional and currently unused.** Under
> §9.1's Debian-only output policy, §7.3 looks up the **output** family for every input,
> so Alpine inputs read the `debian` key too — an omitted `alpine` key changes nothing
> today. Populate `alpine` opportunistically where the names are known (it is the
> groundwork for future musl support and costs little during curation), but do not treat
> its absence as a coverage gap, and do not spend curation time on it at the expense of
> `debian` entries. A missing **`debian`** key is a real defect and should fail
> schema validation.
>
> **(b) `import_names` added** to support the smoke test (§10.2). Cannot be derived from
> the distribution name (§5.2). May be empty for packages with no importable top-level
> module (e.g. `gunicorn`, which is a console script) — the smoke test then checks the
> console script resolves on `PATH` instead.
>
> **(c) `wheel_typically_available` for `lxml` is `true`, not `false` as in v1.** v1's
> example entry claimed lxml has no wheels, which is wrong — lxml has shipped manylinux
> *and* musllinux wheels for years. This matters beyond a typo: **verify every KB entry's
> wheel claim against PyPI during curation.** This field is a documentation aid and a
> fallback for when PyPI is unreachable; the live check in §7.2 is authoritative and must
> win any disagreement. If the two disagree persistently for a package, the KB entry is
> stale — fix it and note the correction.
>
> **A caution about `psycopg2` for the curated corpus:** `psycopg2` builds from source and
> needs `libpq-dev` (build) / `libpq5` (runtime); `psycopg2-binary` ships wheels and needs
> neither. They are different distributions with near-identical names and opposite
> classifications. This is an *excellent* demonstration of per-package precision and a
> trap if conflated. Include both in the KB, and consider a fixture containing both.

**Critical design point (unchanged from v1, restated):** `build_time_system_deps` and `runtime_system_deps` are DIFFERENT lists with typically DIFFERENT package names (e.g., `libpq-dev` to build psycopg2, but only `libpq5` needed at runtime — the `-dev` package includes headers not needed post-compilation). Do not conflate these into one list. This asymmetry is the mechanism by which the tool shrinks images.

**Initial target size**: 60-150 curated entries, prioritizing common ML/data/web packages: numpy, scipy, pandas, lxml, psycopg2 *and* psycopg2-binary, pillow, opencv-python, matplotlib, scikit-learn, torch, tensorflow, cryptography, pyyaml, flask/fastapi/uvicorn/gunicorn (usually pure Python — should have empty dependency lists, serving as important "negative" examples that prove the tool doesn't over-flag everything).

**Data sourcing methodology** (for documentation/report purposes): entries seeded from (a) official package documentation and `setup.py`/`pyproject.toml` build requirements, (b) conda-forge feedstock `meta.yaml` files (these separate build vs. run requirements natively — `github.com/conda-forge/<package>-feedstock`), (c) Debian/Ubuntu package descriptions (`apt-cache show <package>-dev`), (d) manual test-builds to confirm/validate entries.

> **[v2 NOTE — conda-forge is a proxy, not ground truth.]** conda-forge `meta.yaml` files
> describe dependencies for building a *conda* package against conda's own toolchain and
> library ecosystem. Those requirements overlap with, but are not identical to, what pip
> needs on Debian — conda ships its own `libxml2`, and its compiler packages are named
> differently. Treat conda-forge as a strong hint that tells you *which* native libraries
> a package touches, then map to Debian names yourself and confirm by test-build. Record
> `source` honestly per entry: `"conda-forge-derived"` entries carry more uncertainty than
> `"curated"` ones, and §14.3 will ask you to report accuracy broken down by source.

### 7.2 Wheel-availability check — REVISED in v2 (G1, G7, G8)

> **[v2 PATCH — G1, the central correctness fix.]** v1 checked only the architecture in
> the platform tag. That is insufficient and actively dangerous: `manylinux` wheels are
> built against **glibc**, `musllinux` wheels against **musl**. An x86-64 `manylinux`
> wheel matches "x86_64" by substring on an Alpine target, so v1's check would report
> "wheel available → zero build deps," pip would install it, `docker build` would succeed,
> and the container would fail at first import. Because v1's validator only checked the
> build exit code (G2), that failure would have been recorded as a **success**.

**Resolve the target triple first.** Before any lookup, compute:

```
target = (arch, libc, python_abi)
  arch       from --target-arch, default x86_64          -> "x86_64" | "aarch64"
  libc       the OUTPUT libc — see the callout below     -> "glibc"  | "musl" | "unknown"
  python_abi from base image tag or --python-version      -> e.g. (3, 11)
```

> **[v2 PATCH — the wheel check targets the OUTPUT libc, not the input's.]** This is the
> same input/output distinction as §7.3's distro family, applied to libc, and it is easy
> to get backwards. The wheel check answers "will pip install a binary wheel *in the
> builder stage we are about to emit*." Since §9.1 forces that builder to glibc/Debian
> unconditionally, **the target libc is `glibc` for every input, including Alpine ones.**
>
> The input's libc (from §4a) is still needed, for three different purposes: deciding
> whether the original Dockerfile's system packages are comparable (§7.4 step 0), deciding
> whether a rebase is required and must be reported (§9.1), and reporting what the user
> started from. It is **not** the wheel-check target.
>
> Using the input libc here would be conservative rather than dangerous — Alpine inputs
> would find no `manylinux` match, fall to source builds, and inflate
> `conservative_fallback_rate` — but it would be *systematically wrong on every Alpine
> input*, understating the tool's own results for no benefit. Record both values in the
> report (`input_libc`, `target_libc`) so the distinction is visible and auditable.
>
> The `unknown`-libc conservative path (§7.2.1) therefore only triggers when the **output**
> libc is unknown. Under the current Debian-only output policy that cannot happen, so the
> path is dormant — retain it anyway, since it becomes live the moment musl output is
> supported, and a dormant-but-correct branch is cheaper than rediscovering the need later.

The resolved triple must appear in the JSON report (§11). A classification is only
meaningful relative to a stated target, and reviewers will want to see it.

**Query PyPI** at `https://pypi.org/pypi/{package}/{version}/json`, take entries where
`packagetype == "bdist_wheel"`, and parse each wheel filename per PEP 427:

```
{distribution}-{version}(-{build})?-{python_tag}-{abi_tag}-{platform_tag}.whl
```

**A wheel matches the target only if all three of the following hold.**

#### 7.2.1 Platform tag must match arch *and* libc

| Platform tag pattern | Matches `glibc` target | Matches `musl` target | Matches `unknown` |
|---|---|---|---|
| `any` (as in `py3-none-any`) | yes | yes | yes |
| `manylinux1_x86_64`, `manylinux2010_*`, `manylinux2014_*` | yes, if arch matches | **no** | no |
| `manylinux_{major}_{minor}_{arch}` (PEP 600) | yes, if arch matches | **no** | no |
| `musllinux_{major}_{minor}_{arch}` (PEP 656) | **no** | yes, if arch matches | no |
| `linux_x86_64` (unqualified) | **no** — unobtainable from PyPI, see below | **no** | no |
| `macosx_*`, `win_*` | no | no | no |

Two rules that are easy to get wrong and worth stating explicitly:
- **`manylinux` and `musllinux` are mutually exclusive.** Never treat one as a fallback
  for the other. This is the whole of G1.
- **A bare `linux_x86_64` tag cannot be obtained from PyPI** and must not count as a
  match. Be precise about the reason, because the obvious one is wrong: pip does *not*
  reject these tags — `packaging.tags.sys_tags()` emits `linux_x86_64` as valid on Linux,
  and pip will happily install such a wheel from a local file or a private index. What is
  true is that **PyPI refuses uploads** of unqualified `linux_*` wheels (post-PEP 513),
  because the glibc version they were built against is unconstrained and therefore
  unsafe to redistribute. Since this tool queries public PyPI only (§2.2), such a wheel
  can never appear in a response, so "never matches" is correct here. Anyone extending
  the tool to private indexes must revisit this rule rather than inheriting it.
- **`unknown` libc matches only `any`.** Pure-Python wheels are libc-independent and safe;
  anything compiled is unresolvable without knowing the libc, so it takes the conservative
  path.

*Note on glibc minor versions:* strictly, a `manylinux_2_28` wheel will not run on a
base image with glibc 2.17. Verifying that requires knowing the base image's glibc
version, which needs either a hardcoded distro→glibc table or pulling the image. **Do
not implement this.** Treat any `manylinux*` tag as matching a glibc target, and record
this as a documented limitation in §15.

Bound the claim honestly rather than overstating it. Debian **bookworm** is glibc 2.36 and
covers the manylinux levels in common use. Debian **bullseye** is glibc 2.31, and wheels
tagged `manylinux_2_34` or higher — built on AlmaLinux 9 images, and increasingly common —
will **not** install there. So the correct statement is "bookworm satisfies the manylinux
levels observed in our corpus," not "modern Debian satisfies every level on PyPI." The
§10.2 import smoke test catches the failure empirically even though the classifier cannot
predict it, which is what keeps the risk bounded.

> **[v2 PATCH — "prefer bookworm" is a corpus-selection rule, not a synthesis rule.]**
> An earlier draft said "prefer bookworm-based runtime images," which contradicts §9.1:
> the Debian codename is not independently selectable, it is a consequence of the Python
> version tag (`python:3.9-slim` is bullseye, `python:3.11-slim` and later are bookworm),
> and §9.1 forbids changing the Python version because doing so can break the project
> outright. The tool cannot have both. §9.1 wins — a silent Python upgrade is a worse
> failure than a possible manylinux mismatch, and the mismatch is caught by Test A while
> a version change may not be caught at all.
>
> So: **the synthesiser never chooses a codename.** It takes whatever the input's Python
> version implies and records the resulting `glibc_risk: "bounded" | "live"` flag in the
> §11 report (`live` when the resolved base is bullseye or older). The bookworm preference
> applies instead at §14.1 corpus curation, where projects are being chosen anyway, and
> at the §15 limitation. If a corpus project pins an old Python, the flag makes that
> visible in the results table rather than hiding it.

#### 7.2.2 Version resolution must be pinned and recorded — G7

> **[v2 PATCH — G7.]** PyPI is live and mutating. Given `lxml` with no version specifier,
> "the latest version" differs between runs, so classifications drift and the evaluation
> cannot be reproduced — a direct, easy reviewer attack on any live-API-dependent method.

Resolution rules:
1. Manifest pins exactly (`==2.3.0`) → use that version.
2. Manifest gives a range or nothing → query PyPI once, select the highest version
   satisfying the specifier that is not a pre-release, and **record the concrete resolved
   version** in the report.
3. Persist a **resolution lockfile** (`wheel_resolution_lock.json`) mapping
   `(normalized_name, specifier, target_triple)` → `(resolved_version, wheel_matched,
   matched_filename, queried_at_utc)`.
4. Add `--use-lock` to replay from the lockfile without network access, and
   `--refresh-lock` to re-query. Benchmark runs must use `--use-lock` after an initial
   population pass, so the reported numbers are reproducible from the artifact.

This also makes the tool usable offline and in CI, and makes the benchmark re-runnable by
a reviewer who has the lockfile but no network. The lockfile is a deliverable artifact,
not an implementation detail — ship it alongside the results.

#### 7.2.3 Python tag / ABI matching — G8

> **[v2 PATCH — G8.]** v1 said match "the Python tag (e.g. `cp311`)". Real wheels use
> several tag forms and naive matching mis-handles the two most useful ones.

Match rules against target Python `(3, N)`:

| Python tag | ABI tag | Matches? |
|---|---|---|
| `py3`, `py2.py3` | `none` | yes — universal pure-Python wheel |
| `cp3N` | `cp3N` | yes — exact CPython match |
| `cp3M` where `M <= N` | `abi3` | yes — stable ABI, forward-compatible |
| `cp3M` where `M != N` | `cp3M` | no |
| `cp3Nt` | `cp3Nt` | free-threaded build; treat as **no match** unless the target is explicitly free-threaded |
| `pp3*` | any | no — PyPy, out of scope |

Tags may be dot-separated compound values (`py2.py3`, `cp39.cp310`); split on `.` and
match if **any** component matches. Getting `abi3` wrong is the costly case: a
`cp37-abi3` wheel is installable on 3.11, and treating it as a miss sends
`cryptography` — a very common dependency — down the source-build path, which inflates
the build stage and understates the tool's own benefit.

#### 7.2.4 Caching and failure handling

Cache in memory keyed by `(normalized_name, resolved_version, target_triple)`, and
persist via the lockfile (§7.2.2). Additional requirements:
- Rate-limit politely (a small delay between requests) and set a descriptive
  `User-Agent`. A benchmark loop over 80 repositories will make thousands of requests;
  do not hammer PyPI.
- Retry transient failures (timeout, 5xx) with backoff, a few attempts.
- **404 means the distribution does not exist on public PyPI** — do not treat this as
  "no wheel." Mark `is_resolvable = False` (private index, typo, or renamed package)
  and take the conservative path with a distinct report reason.
- **On network failure, do not silently assume "no wheel available."** Doing so quietly
  converts every package to the conservative source-build path and would make a network
  outage look like a change in the tool's behaviour. Mark the result
  `wheel_check = "unavailable"`, take the conservative path, and surface the degradation
  prominently in the report and CLI output.

### 7.3 Per-package classification algorithm — REVISED in v2

For each package parsed from the manifest:

1. If `is_resolvable == False` (git/URL/local path) → no wheel check possible; conservative
   path, reason `"non-pypi-source"`.
2. Resolve the concrete version (§7.2.2) and check wheel availability against the full
   target triple (§7.2.1, §7.2.3).
> **[v2 PATCH — which `distro_family` keys the KB lookup?]** Two distinct families are in
> play and conflating them emits wrong package names. The **input** family (from §4a)
> determines *how to read the original Dockerfile* — whether its `apk`/`apt` packages are
> comparable, per §7.4 step 0. The **output** family (from §9.1, always Debian) determines
> *what names to emit into the synthesized Dockerfile*.
>
> **All KB dependency lookups in steps 3 and 4 below use the OUTPUT family.** For an
> Alpine input this means Debian names — correct, because §9.1 rebases the runtime to
> Debian `-slim` and §9.3 feeds these names to `apt-get install`. Using the input family
> would look up `libxslt` (Alpine) and then pass it to `apt-get`, which fails: the Debian
> package is `libxslt1.1`. Since §9.1 currently forces Debian output unconditionally, the
> output family is always `"debian"`; keep the lookup parameterised anyway so future musl
> support does not require rewriting the classifier.

3. **If a matching wheel exists** → `build_deps = []`; `runtime_deps` = KB's
   `runtime_system_deps[output_distro_family]` if present, else `[]`.
   Confidence source: `"kb+wheel"` if in KB, `"inferred-wheel"` if not.
   > Runtime deps still apply even when a wheel exists. A `manylinux` wheel bundles most
   > of its shared libraries, but not all — `opencv-python` needs `libGL` and
   > `libglib2.0` present at runtime on slim images, and `mysqlclient` needs
   > `libmariadb3`. Wheel availability eliminates *build* deps only. Conflating the two
   > would reintroduce G2-class runtime failures.
   >
   > *Do not use psycopg2 as the example here.* Plain `psycopg2` builds from source and so
   > never reaches this branch; `psycopg2-binary` ships wheels that **bundle** libpq and
   > therefore need no runtime dep. The pair is a good demonstration of per-package
   > precision (§7.1) but a misleading example of wheels-still-needing-runtime-deps.
4. **If no matching wheel exists** (source build required):
   - In KB, and KB has an entry for the **output** `distro_family` → use its
     `build_time_system_deps[output_family]` and `runtime_system_deps[output_family]`.
     Confidence `"kb"`.
   - In KB but no entry for the output distro family → conservative path, reason
     `"kb-distro-gap"`. (With §9.1's Debian-only output policy this should not occur for
     curated entries, since `debian` is the mandatory key; it exists for future musl
     support and to fail safe if an entry is malformed.)
   - Not in KB → conservative fallback: assume common build tooling
     (`build-essential`, `gcc`, `g++`, `make`, plus `python3-dev`) is needed; mark
     `confidence = "unresolved-conservative"`; log to the unresolved list for KB
     expansion.
5. **If `libc == "unknown"` and the only candidate wheels are `manylinux`/`musllinux`** →
   treat as no match (§7.2.1) and fall to step 4.

**The conservative fallback must be reported, not hidden.** Its whole purpose is to avoid
breaking builds at the cost of a larger image, and the fraction of packages that land
there is a headline honesty metric for the evaluation: it is the direct measure of
knowledge-base coverage. Report it as a percentage per repository, and in aggregate.

### 7.4 Reconciling against apt/system packages already declared in the input Dockerfile

For each system package extracted by Module 1:

0. **[v2]** If the package was installed via a package manager that does not match the
   **input** `distro_family` (e.g. `apk` packages against a Debian-keyed KB), skip
   reconciliation, label `UNKNOWN_CONSERVATIVE`, and record reason
   `"pkg-manager-mismatch"`. Guessing across package-manager namespaces produces
   confidently wrong labels, which are worse than admitted ignorance.

   > **[v2 PATCH — this is the one site that uses the INPUT family.]** §7.3 routes every
   > KB *dependency lookup* through the **output** family (G1c). This step is different:
   > it is comparing the input Dockerfile's own `apk`/`apt-get` lines against the KB's
   > naming namespace, so the question is which package manager *the input used*. Using
   > the output family here would make every Alpine input's `apk` packages look like
   > legitimate Debian names and silently reconcile them against the wrong namespace.
   >
   > Note the consequence for Alpine inputs: their extracted system packages all land in
   > `UNKNOWN_CONSERVATIVE`, so none are pruned. That is intended — the venv carries the
   > Python packages across the rebase, but system-package names do not translate, and
   > guessing is how G1c happened.
1. If it appears in the union of all `runtime_system_deps` computed above → label `RUNTIME`.
2. Else if it appears in the union of all `build_time_system_deps` computed above → label `BUILD_ONLY`.
3. Else if it matches a small curated fallback list of generically-known build tools (e.g., `gcc`, `g++`, `make`, `git`, `cmake`, `build-essential`, `pkg-config`, `autoconf`, `libtool`, `*-dev`, `*-devel`) → label `BUILD_ONLY`.
4. Else if it is referenced by name inside the Dockerfile's `CMD`, `ENTRYPOINT`, or `HEALTHCHECK` instructions (e.g., `curl` used in a HEALTHCHECK) → label `RUNTIME` (safety override — this catches cases where a tool is genuinely needed at runtime for a reason unrelated to Python packages).
   > **[v2] Widen this override.** Also scan `ENV` values, any shell scripts copied in and
   > referenced by `CMD`/`ENTRYPOINT` (if readable in the project directory), and
   > `supervisord`/entrypoint wrapper scripts. A `git` binary invoked by an entrypoint
   > script is a genuine runtime dependency that v1's Dockerfile-only scan would drop,
   > producing a container that builds and then fails — a G2-class error. If the
   > entrypoint is a script the tool cannot read, say so and treat all otherwise-unclaimed
   > tools as `UNKNOWN_CONSERVATIVE` rather than pruning them.
5. Else → label `UNKNOWN_CONSERVATIVE` (default to keeping it in the runtime stage to avoid breaking the build; flag for manual review in the output report).

> **[v2 PATCH — corrected after Phase 0 verified the paper. This step is PARITY with
> StageCraft, not the contribution.]** An earlier draft called this per-item apt
> reconciliation "the direct, demonstrable difference from StageCraft." That is false and
> a reviewer reading StageCraft §3.2.2 would catch it. StageCraft applies its blanket
> *inheritance rule* only to **application-level (pip/npm)** packages. For **system-level
> (apt)** packages it already does per-package classification: a curated build-tool list
> (gcc/make/git), a language-core check (python3 → runtime), a conservative build-time
> default, and promotion to runtime if the package appears in `CMD`/`ENTRYPOINT`/
> `HEALTHCHECK`. Steps 1–5 above are the *same algorithm* — step 3's curated list and
> step 4's runtime-directive promotion are point-for-point what §3.2.2 describes. So this
> section should be presented as a **faithful reproduction** of StageCraft's system-level
> classifier, not as a novelty over it.
>
> **The demonstrable difference lives entirely at the application/pip layer** (§7.3 +
> §7.2, and §7.6's shadow comparison): StageCraft labels *every* pip package identically
> via the inheritance rule, whereas this tool parses the manifest and classifies *each*
> pip package individually against the KB and a live wheel check. The flask-vs-lxml test
> (§1.4, §16 Module 4) is decisive precisely because flask and lxml are both **pip**
> packages — the exact case the inheritance rule cannot separate. See
> `docs/phase0_verification.md` and `docs/stagecraft_rule_verbatim.md`.

### 7.5 Output of this module

A structured classification result containing: the resolved target triple; per-Python-package classification (with confidence/source, resolved version, and matched wheel filename if any); per-system-package label with reason code; and lists of "unresolved" items for both categories.

### 7.6 StageCraft-equivalent shadow classification — NEW in v2

> **[v2 PATCH — supports G5/G9.]** In the same run, also compute what StageCraft's
> inheritance rule *would* produce for this input: one blanket label for all pip packages,
> derived from whether pip appears in runtime-relevant instructions (§1.3). Emit it
> alongside the real classification.
>
> Two reasons this is worth the ~30 lines. First, it yields the divergence metric of
> §14 directly, per repository, with no dependence on StageCraft being runnable —
> which is the G6 contingency. Second, it makes the contribution visible in every single
> report rather than only in aggregate benchmark tables.
>
> **State the caveat plainly wherever this metric appears:** this is *our reimplementation
> of the rule as described in their paper*, not their tool's actual output. It is
> evidence about the published algorithm, not a measured comparison against the artifact.
> If StageCraft turns out to be runnable (§14.2), report both and reconcile any
> disagreement — a divergence between the reimplementation and the real tool is itself a
> finding worth reporting.

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
               Score from 0-100 "efficiency", converted to a penalty score if below a
               threshold.

S_security = weighted sum of detected risks:
             - running as root (no USER instruction, or explicit USER root)
             - hardcoded secrets in ENV/ARG (heuristic: variable names containing
               "KEY", "SECRET", "PASSWORD", "TOKEN")
             - presence of known-risky leftover build tools (e.g., curl/git with
               no clear runtime justification)

S_total = w_b * S_bloat + w_e * S_efficiency + w_s * S_security
          (default all weights = 1, configurable)

Proceed with refactoring if S_total >= THRESHOLD.
```

> **[v2 NOTE — the threshold needs a stated derivation.]** v1 said "start around 20-30 and
> adjust based on observed behavior," which is fine as a development practice and
> indefensible as a published method. Two problems: the sub-scores are on wildly different
> and unnormalised scales (`S_bloat` counts packages, so it grows unboundedly;
> `S_efficiency` is 0-100), so with all weights at 1 the efficiency term dominates
> arbitrarily. And "adjust until it looks right" is unfalsifiable tuning on the test set.
>
> Required for v2:
> 1. **Normalise each sub-score to 0-100** before weighting, so the weights mean something.
> 2. **Calibrate on a held-out split.** Choose the threshold using a small development
>    subset of the corpus, then freeze it before running the full evaluation. Report the
>    chosen value, the subset used, and the sensitivity of the final results to it
>    (a table of outcomes at three or four thresholds is enough).
> 3. Store weights and threshold in config, not in code.
>
> Also note the honest framing: this gate is **inherited from StageCraft's design**, not a
> contribution of this project. Say so. Reimplementing a baseline's component faithfully
> is good methodology; presenting it as novel is not, and a reviewer who knows the paper
> will notice immediately.

If the score is below threshold, the tool should still output a message explaining why it decided not to refactor (e.g., "no significant build-time bloat detected"), not just silently do nothing.

---

## 9. MODULE 6 — MULTI-STAGE SYNTHESIS

### Purpose
Generate the actual optimized Dockerfile text.

### 9.1 Base image selection — REVISED in v2

- **Builder stage**: use a full-featured base image matching the detected primary language and the original Dockerfile's Python **version** (e.g., if original was `python:3.8`, builder stays on 3.8 — do not silently change major or minor versions; a Python version change alters ABI compatibility and can change which wheels apply, invalidating the classification the synthesis is based on). The base image **family** is *not* inherited unconditionally — it is forced to glibc/Debian per the rule immediately below.

  > **[v2 PATCH — G1, second-order. The builder's libc must match the runtime's.]**
  > "Builder inherits the original base" and "runtime is never Alpine" are individually
  > correct and jointly broken. For an Alpine input they produce a **musl builder feeding a
  > glibc runtime**, and §9.3 then copies `/opt/venv` across that boundary. Every compiled
  > extension module in that venv is musl-linked and will not load — the exact
  > `LIBC_MISMATCH` that §10.3 asserts "should be impossible given §9.1." §9.2's builder
  > template also hardcodes `apt-get`, which does not exist on an Alpine builder.
  >
  > **Rule: builder and runtime must share the same libc, always.** Since §9.1 forces a
  > glibc runtime, the builder is also forced to glibc. For an Alpine input, the builder
  > becomes `python:<same version>` (Debian-based, full) rather than `python:<ver>-alpine`.
  > Both stages change family together, and the report's `base_image_changed` field must
  > name both.
  >
  > This generalises: a venv is only portable between two images with the **same libc and
  > the same Python major.minor**. Assert both at synthesis time and fail loudly. The
  > Python-version assertion is already required below; this adds the libc half, and the
  > two together are what make the `COPY --from=builder /opt/venv /opt/venv` in §9.3 sound.
- **Runtime stage**:
  - If a CUDA/GPU signal was detected (e.g., `torch` with a cuda extra, or explicit CUDA references) → use a matching `nvidia/cuda:*-runtime-*` base image.
  - Else for Python → use the official `-slim` variant of the same Python version.
  - Do not implement distroless or Go-specific logic (out of scope, §2.2).

> **[v2 PATCH — G1: the Alpine policy. DECIDED: detect, never select.]**
>
> **The tool must never emit an Alpine base image for *either* stage, even when the input
> Dockerfile used one.** If the input is Alpine-based, the builder becomes the full Debian
> `python:<same version>` image and the runtime becomes `python:<same version>-slim`, and
> the report must state prominently that the base image family was changed **for both
> stages**, why, and that the user should review it. Rebasing only the runtime would leave
> a musl builder feeding a glibc runtime across the §9.3 venv copy — see the builder-libc
> rule above (G1b).
>
> Rationale, in the form it should take in the report:
> 1. **Correctness.** Alpine/musl requires `musllinux` wheels, which are published for
>    fewer distributions than `manylinux`. Selecting Alpine converts packages that would
>    install as binaries into source builds, requiring a larger build stage and a
>    correspondingly larger KB — Alpine-specific system package names for every entry.
> 2. **It often loses on its own terms.** For ML images, forcing source compilation of the
>    numpy/scipy stack frequently yields an image *larger* than `python:3.x-slim`, plus
>    much longer builds. Alpine's small base is a poor predictor of final ML image size.
> 3. **Scope honesty.** Supporting musl properly means a parallel apk knowledge base.
>    That roughly doubles curation for no gain against the research question, which is
>    about per-package precision, not about libc portability.
>
> This is a **deliberate, documented design decision, not an unhandled case.** The
> distinction matters in review: the tool detects musl correctly (§4a), refuses to
> propagate it, and says so. Record it in §15 and note musl support as future work.
>
> Changing a user's base image family is a real behavioural change and must be loud, not
> silent. Provide `--keep-base-family` which, for an Alpine input, aborts with an
> explanation rather than emitting a possibly-broken musl Dockerfile — never silently
> attempting musl synthesis.

### 9.2 Builder stage generation — REVISED in v2 (G4)

> **[v2 PATCH — G4.]** v1 used `pip install --prefix=/install`. Replaced with a virtual
> environment. Two independent bugs are fixed:
>
> 1. **Prefix clobbering.** v1 §9.3 copied `/install` over `/usr/local` in the runtime
>    stage, overwriting whatever the runtime base already had there — including Python's
>    own `bin/`, `lib/`, symlinks, and (on `python:*-slim`) the interpreter's standard
>    library layout. A silent overwrite of system files that may or may not break
>    depending on the base image is the worst kind of bug: intermittent and confusing.
> 2. **Shebang breakage.** `--prefix=/install` writes console scripts whose shebang is
>    `#!/install/bin/python`. That path does not exist in the runtime stage, so
>    `gunicorn`, `uvicorn`, `streamlit`, `celery` — every entrypoint these projects
>    actually use — fail with `no such file or directory`. This is not hypothetical: it
>    is the single most common failure mode of the copy-a-prefix pattern, and since these
>    are exactly the CMD targets of the target corpus (§14.1), v1 would have failed on a
>    large fraction of it. Note that `--prefix` also silently breaks under Debian's
>    `dist-packages` layout even for the pure-library case.
>
> A venv fixes both: it is relocatable-by-copy to the *same* path, keeps everything under
> one directory that does not collide with system files, and writes shebangs pointing at
> `/opt/venv/bin/python`, which exists in the runtime stage because the venv is copied
> there wholesale.

Builder stage, in this order (dependencies before source code, for layer caching):

```dockerfile
FROM <builder_base> AS builder

# 1. Build-only system dependencies (emit only if the list is non-empty)
RUN apt-get update && apt-get install -y --no-install-recommends \
        <build-only system deps> \
    && rm -rf /var/lib/apt/lists/*

# 2. Isolated virtual environment
ENV VIRTUAL_ENV=/opt/venv
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 3. Dependencies before source, so code edits don't invalidate the install layer
WORKDIR <original workdir or /app>
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 4. Application source
COPY . .
```

Implementation notes:
- Setting `ENV PATH` before `pip install` means no `--prefix`, no `--target`, no
  `--user`: plain `pip install` lands in the venv because the venv's `python` and `pip`
  are first on `PATH`.
- The venv path **must be identical in both stages**. venvs are not path-relocatable;
  copying `/opt/venv` to a different destination breaks every shebang. Use one constant.
- Use `python -m venv` (stdlib), not `virtualenv`.
- On a `python:*` builder the venv inherits that interpreter, and the runtime stage must
  match it on **both Python major.minor and libc** — otherwise the copied venv's compiled
  extension modules will not load. Both halves are enforced by §9.1 (the version rule and
  the builder-libc rule). Add a single synthesis assertion covering both, and fail loudly
  rather than emitting a Dockerfile that is guaranteed to break. These are the two
  preconditions that make `COPY --from=builder /opt/venv /opt/venv` sound; assert them
  where the copy is generated, not only where the bases are chosen.
- `--no-cache-dir` throughout; pip's cache is pure waste in a discarded stage.

### 9.3 Runtime stage generation — REVISED in v2 (G4)

```dockerfile
FROM <runtime_base>

# 1. Runtime-only system dependencies (emit only if non-empty)
RUN apt-get update && apt-get install -y --no-install-recommends \
        <runtime system deps> \
    && rm -rf /var/lib/apt/lists/*

# 2. Non-root user, created before COPY so ownership can be set in one step
RUN groupadd --system --gid 1001 appgroup \
    && useradd --system --uid 1001 --gid appgroup --create-home appuser

# 3. The venv — one self-contained directory, no system paths touched
COPY --from=builder --chown=appuser:appgroup /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 4. Application code
WORKDIR <original workdir or /app>
COPY --from=builder --chown=appuser:appgroup <app dir> <app dir>

# 5. Preserved from the original: ENV, EXPOSE, HEALTHCHECK, CMD/ENTRYPOINT — verbatim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
EXPOSE <ports>
HEALTHCHECK <original healthcheck>

USER appuser
CMD <original cmd, unchanged>
```

Requirements:
- **`COPY --from=builder /opt/venv /opt/venv`** — same path both sides, replacing v1's
  `COPY --from=builder /install /usr/local`. Nothing in a system directory is overwritten.
- Preserve `WORKDIR`, all `ENV` declarations, `EXPOSE`, `HEALTHCHECK`, and the original
  `CMD`/`ENTRYPOINT` **exactly** as they were. Only build structure changes; runtime
  behaviour must not. If the original had both `ENTRYPOINT` and `CMD`, preserve both and
  their forms — converting shell form to exec form changes signal handling and PID 1
  semantics, which is a behavioural change outside this tool's remit.
- **`ENV PATH` must be set in the runtime stage too.** This is the most commonly missed
  step in the venv pattern: without it the container silently runs the *system* Python,
  which has none of the installed packages. If `CMD` is shell-form and relies on `PATH`
  resolution, this is the difference between working and not.
- If the original set a non-root `USER`, preserve that user rather than inventing
  `appuser`. Only add a user when none existed.
- Add `--chown` on both `COPY --from` operations; a separate `RUN chown -R` duplicates
  the entire venv into a new layer, which for an ML image can mean gigabytes.
- If the original already ran as non-root and `pip install --user` semantics were in
  play, flag for manual review rather than guessing.

### 9.4 Fallback behavior for artifact copying

If the tool cannot confidently determine a narrower set of files to copy (which will usually be the case for typical Python web apps, unlike compiled-binary languages), fall back to copying the entire application directory rather than attempting incorrect selective copying. This is an intentional, documented trade-off, not a bug — note it as such in code comments and in the eventual project report.

> **[v2 NOTE — be precise about where the savings come from.]** Since the app directory is
> copied wholesale, essentially **all** of this tool's size reduction comes from two
> sources: (a) build-only system packages and `-dev` headers excluded from the runtime
> stage, and (b) pip/apt caches and build intermediates left behind in the discarded
> builder. Application source is rarely more than a few MB. State this in the report so
> the reduction figures are correctly attributed — and note that it means the tool's
> benefit is *concentrated* in exactly the compiled-dependency-heavy repositories that
> §14.1 requires the corpus to over-represent. The corpus curation and the mechanism of
> benefit are the same argument seen from two angles, which is a coherent story to tell
> rather than two separate concessions.

### 9.5 .dockerignore generation

Generate a `.dockerignore` alongside the new Dockerfile, composed from:
- General exclusions: `.git`, `.gitignore`, `README*`, `.vscode`, `.idea`, `*.md`
- Python-specific: `__pycache__`, `*.pyc`, `*.pyo`, `*.pyd`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.venv`, `venv/`, `*.egg-info`, `.tox`
- **[v2]** Container/CI files that should never enter the context: `Dockerfile*`,
  `.dockerignore`, `docker-compose*.yml`, `.github/`
- **[v2]** Data and model artifacts, which for ML repositories are frequently the single
  largest contributor to context size: `*.ipynb_checkpoints`, `data/`, `*.csv`, `*.h5`,
  `*.pkl`, `*.pt`, `*.onnx` — **but only when not referenced by the application code**,
  and always listed in the report for user confirmation. Excluding a model file the app
  loads at startup produces a container that builds and then crashes, which is exactly
  the G2 failure class this specification is trying to eliminate. When in doubt, do not
  exclude; the size win is not worth a broken image.
- Any test directories detected (`tests/`, `test/`) if present and not needed at runtime

> **[v2 NOTE]** `.dockerignore` affects build context size, and therefore build time, but
> has **no direct effect on final image size** when `COPY . .` is used — the ignored files
> were never going to be copied anyway. Do not credit `.dockerignore` with image size
> reduction in the evaluation. Report its effect on context size and build duration
> separately, and only claim image-size impact where a previously-copied directory
> (`.git`, `data/`) is now excluded.

---

## 10. MODULE 7 — BUILD VALIDATION — SUBSTANTIALLY REVISED in v2 (G2)

### Purpose
Verify the synthesized Dockerfile produces a **working** image — not merely one that
builds — and turn failures into actionable knowledge-base feedback.

> **[v2 PATCH — G2, the second critical fix.]** v1 declared SUCCESS on `docker build`
> exit code 0. This is structurally unsound for this specific tool, because the tool's
> characteristic error is **pruning a runtime dependency that the build never needed**.
> Consider the causal chain: the classifier decides `libpq5` is build-only and omits it
> from the runtime stage. `pip install psycopg2` already happened in the builder, so the
> build succeeds. `docker build` exits 0. v1 records SUCCESS. The container then dies on
> `import psycopg2` with `libpq.so.5: cannot open shared object file`.
>
> Note what this does to the evaluation: v1's §10 explicitly listed the pattern
> `<library>.so: cannot open shared object file` as a *build-log* failure signature — but
> that error is emitted at **container start**, so it can essentially never appear in a
> build log for this failure mode. v1 was watching the wrong stream for its most likely
> error. Worse, the same blindness applies to the G1 musl mismatch and to over-pruned
> `PATH`/venv problems. Every one of those would have been scored as a success, and the
> resulting size-reduction numbers would have been computed over broken images —
> making the tool look *better* the more aggressively it broke things.
>
> Build validation is therefore split into two mandatory phases.
>
> **Definition of SUCCESS, stated precisely.** A run is:
>
> | Status | Condition |
> |---|---|
> | `FAILURE` | Phase 1 exits non-zero, **or** Test A fails, **or** Test C fails |
> | `RUNTIME_WARNING` | Phase 1 and Tests A and C all pass, but Test B fails |
> | `SUCCESS` | Phase 1 exits 0 **and** Test A passes **and** Test C passes |
>
> Test A and Test C are both load-bearing because both detect defects *this tool
> introduced*: A catches a pruned runtime dependency, C catches a broken shebang or a
> missing runtime `ENV PATH`. Test B is downgraded because its failures usually reflect the
> application's environment — a missing database, absent credentials, an unmounted volume —
> not a refactoring error (§10.2).
>
> Do not state this as "both phases must pass": that is ambiguous given Test B's downgrade.
>
> **§14.3 reports two distinct rates, and the difference between them is informative:**
> - `working_image_rate` = Phase 1 **and Test A** — the narrow claim, "declared
>   dependencies are importable." Test A alone, so it isolates classification correctness.
> - `full_success_rate` = the `SUCCESS` row above (Phase 1 + A + C) — the operational claim,
>   "the image runs its entrypoint tooling."
>
> Report both. They should be nearly equal; a gap between them means the venv/`PATH`
> synthesis is misbehaving independently of classification, which is exactly the kind of
> distinction that would otherwise hide inside a single aggregate number.

### 10.1 Phase 1 — build

1. Run `docker build -t <test-tag> -f <synthesized dockerfile> .` in the project
   directory (via `subprocess` or `docker-py`).
2. Record exit code, full build log, wall-clock duration, and build context size.
3. On exit code 0, capture final image size (`docker image inspect --format '{{.Size}}'`)
   and layer count (`docker history`).
4. **Also build the original single-stage Dockerfile** under the same conditions, to get
   the before/after size baseline. Without this the reduction percentage has no
   denominator. Cache these baseline builds across benchmark re-runs — they are expensive
   and do not change.

### 10.2 Phase 2 — runtime smoke test — NEW in v2, MANDATORY

A build that produces an unusable image is a failure, and must be recorded as one.

**Test A — import check (primary).** For every package classified in Module 4, resolve its
import name(s) via the KB `import_names` field (§7.1), falling back to the normalised
distribution name with `-` → `_`. Then run a single container:

```bash
docker run --rm --entrypoint /opt/venv/bin/python <test-tag> -c \
  "import importlib,sys
failed=[]
for m in ['flask','lxml.etree','psycopg2','numpy']:
    try: importlib.import_module(m)
    except Exception as e: failed.append((m, type(e).__name__, str(e)))
if failed:
    [print('IMPORT_FAIL', *f, sep='\t') for f in failed]; sys.exit(1)
print('IMPORT_OK')"
```

Notes on doing this correctly:
- Override `--entrypoint` explicitly. Many corpus images have an `ENTRYPOINT` that would
  otherwise swallow the command, and the point here is to test the interpreter directly.
- Use the venv's absolute interpreter path rather than relying on `PATH`, so the test is
  diagnostic: if `PATH` is misconfigured (a real §9.3 risk) Test C catches it separately,
  and the import result stays unambiguous.
- Import **all** packages in one container and report every failure, rather than
  short-circuiting on the first. One run yields the full set of KB gaps.
- Distinguish `ImportError`/`ModuleNotFoundError` (package missing or shared library
  absent) from other exceptions at import time (a package that imports but errors for an
  unrelated reason — e.g. requires a config file or a GPU). Only the former is a
  classification bug; conflating them will manufacture phantom KB gaps.
- Packages with no importable module (`gunicorn`) are skipped here and covered by Test C.

**Test B — entrypoint smoke test (secondary).** Start the container with its real
`CMD`/`ENTRYPOINT`, wait a bounded interval (5-10s), then check:
- container still running, or exited 0 for a legitimately short-lived `BATCH_JOB`;
- if architecture type is `WEB_API` and a port was `EXPOSE`d, the port is listening
  (`docker exec` a socket check, or publish the port and connect from the host);
- capture container logs regardless of outcome.

Then stop and remove the container. Treat Test B failures as **`RUNTIME_WARNING`, not
`FAILURE`**, unless the logs show a signature from §10.3. Many real applications
legitimately exit or crash without a database, an API key, or a mounted volume, and that
is not this tool's doing. Test A is the load-bearing test because it isolates precisely
what this tool controls: whether declared dependencies are importable. Say this
explicitly in the report — it is the difference between measuring your tool and measuring
the corpus's environmental requirements.

**Test C — console script resolution.** For each entrypoint-relevant console script
(`gunicorn`, `uvicorn`, `streamlit`, `celery`, whatever appears in `CMD`), run
`docker run --rm --entrypoint sh <tag> -c 'command -v <script> && <script> --version'`.
This is the direct regression test for the two G4 bugs: a broken shebang or a missing
`ENV PATH` in the runtime stage both surface here and nowhere else.

### 10.3 Failure classification — REVISED in v2

Classify from build logs **and** container logs, tagging each with its phase so the
report distinguishes build-time from runtime failures:

| Signature | Category | Phase | Action |
|---|---|---|---|
| `<lib>.so[.N]*: cannot open shared object file` | `MISSING_RUNTIME_SYSTEM_DEP` | runtime (usually) | Map `.so` → Debian package via `apt-file`/`dpkg -S` in a scratch container; log KB gap against the importing package's `runtime_system_deps` |
| `ModuleNotFoundError: No module named X` | `MISSING_PACKAGE` | runtime | venv not copied, `PATH` wrong, or install silently failed — inspect, likely a synthesis bug not a KB gap |
| `Error loading shared library ... musl` / `Symbol not found: __libc_start_main` | `LIBC_MISMATCH` | runtime | **G1 regression.** Should be impossible given §9.1; if seen, the Alpine policy leaked |
| `gcc: command not found`, `error: command 'gcc' failed`, `fatal error: Python.h: No such file` | `MISSING_BUILD_SYSTEM_DEP` | build | Log KB gap against `build_time_system_deps` |
| `<header>.h: No such file or directory` | `MISSING_BUILD_HEADER` | build | Map header → `-dev` package; log KB gap |
| `no such file or directory` on a console script, or bad interpreter | `BROKEN_SHEBANG` | runtime | **G4 regression.** Should be impossible under §9.2 |
| `Could not find a version that satisfies` | `RESOLUTION_FAILURE` | build | Usually a pinned version unavailable for the target Python — not a KB gap |
| `exec format error` | `ARCH_MISMATCH` | runtime | Target arch vs build host; check §7.2 triple |
| anything else | `UNCLASSIFIED` | either | Log verbatim for manual review |

**Every category must be counted in the evaluation, and the report must never collapse
`FAILURE` into a single number.** The distribution across these categories *is* the error
analysis, and it is the most informative table in the results chapter.

### 10.4 Knowledge-base feedback loop

Write all gaps to a persistent, append-only, machine-readable log
(`kb_gaps.jsonl`), one record per gap:

```jsonc
{"timestamp": "...", "repo": "...", "phase": "runtime", "category": "MISSING_RUNTIME_SYSTEM_DEP",
 "missing_artifact": "libpq.so.5", "suspected_package": "psycopg2",
 "suspected_field": "runtime_system_deps", "resolved_apt_package": "libpq5",
 "raw_error": "...", "target_triple": {...}}
```

> **[v2 — a methodological warning that matters more than it looks.]** This loop creates a
> real risk of **overfitting the knowledge base to the benchmark corpus**. If you run the
> corpus, harvest gaps, patch the KB, and re-run, the reported success rate measures
> "performance after tuning on the test set" — which a reviewer will identify immediately
> and which invalidates the headline number.
>
> Mandatory protocol:
> 1. **Split the corpus** into a development set (~20%) and a held-out evaluation set
>    (~80%) *before* any KB tuning.
> 2. Use the feedback loop **only** on the development set.
> 3. **Freeze the KB.** Record its git commit hash in the report.
> 4. Run the evaluation set **once** against the frozen KB. That is the headline number.
> 5. Gaps found on the evaluation set are **reported as findings, not patched**. They are
>    your real coverage measurement and your limitations section.
>
> If you patch and re-run the evaluation set, you must report both numbers and label them
> honestly as pre- and post-tuning. Doing this correctly is a strength: it converts the KB
> coverage limitation from a weakness into a quantified, honestly-measured result, and
> lets you state a real generalisation estimate rather than a tuned one.

---

## 11. MODULE 8 — REPORTING

### Purpose
Produce a structured, human-readable and machine-readable (JSON) summary of what the tool
did and why, for practical trust/auditability and as raw evaluation data.

### Required report fields — EXTENDED in v2

```jsonc
{
  "tool_version": "2.0.0",
  "kb_version": "<git commit hash of knowledge_base.json>",     // [v2] §10.4 protocol
  "run_timestamp_utc": "...",
  "input_dockerfile": "<path>",
  "input_manifest": "<path>",

  // [v2] G1/G8 — a classification is meaningless without its target.
  // [v2 PATCH — G1b/G1c] Input and output are recorded separately. They differ whenever
  // §9.1 rebases (Alpine inputs), and conflating them is exactly what G1b and G1c were.
  // Consumers of this report must read `target` for anything dependency-related; `input`
  // exists for §7.4 step 0 comparability, the rebase decision, and human diagnosis.
  "input": {
    "base_image": "python:3.11-alpine",
    "libc": "musl",
    "libc_detection_source": "base_image_tag" | "apk_command" | "apt_command" | "unknown",
    "distro_family": "alpine",
    "python_version": [3, 11],
    "python_version_source": "base_image_tag" | "cli_flag" | "assumed_from_host"
  },
  "target": {                       // what the emitted stages actually are
    "arch": "x86_64",
    "libc": "glibc",                // always glibc under the current §9.1 output policy
    "distro_family": "debian",      // keys every KB dependency lookup (§7.3)
    "python_version": [3, 11],      // must equal input.python_version (§9.1, §9.2)
    "builder_base": "python:3.11",
    "runtime_base": "python:3.11-slim",
    // [v2] §7.2.1 — "live" when the codename implied by python_version is bullseye or
    // older (glibc 2.31), where a manylinux_2_34+ wheel can pass classification and then
    // fail to install. Not actionable by the synthesiser (§9.1 forbids version changes);
    // surfaced so the results table shows which rows carry the risk.
    "glibc_risk": "bounded" | "live"
  },

  // [v2] G3 — declared incompleteness
  "unanalyzed_manifests": [
    {"file": "pyproject.toml", "format": "pep621", "declares_dependencies": true}
  ],

  "base_image_changed": {                                       // [v2] §9.1 Alpine policy
    "changed": false, "from": null, "to": null, "reason": null
  },

  "necessity_score": {
    "total": 0, "bloat": 0, "efficiency": 0, "security": 0,
    "normalized": true, "weights": {"w_b": 1, "w_e": 1, "w_s": 1},
    "threshold": 0, "proceeded": true
  },

  "packages_classified": [
    {
      "name": "lxml", "normalized_name": "lxml",
      "version_specifier": ">=4.9.0",
      "resolved_version": "5.2.1",                              // [v2] G7 reproducibility
      "wheel_check": "matched" | "no_match" | "unavailable" | "not_on_pypi",
      "matched_wheel_filename": "lxml-5.2.1-cp311-cp311-manylinux_2_28_x86_64.whl",
      "build_deps": [], "runtime_deps": ["libxml2", "libxslt1.1"],
      "import_names": ["lxml.etree"],
      "confidence_source": "kb+wheel",
      "reason_code": null
    }
  ],

  "apt_packages_labeled": [
    {"name": "libpq-dev", "label": "BUILD_ONLY", "reason": "matched_build_deps_of:psycopg2"}
  ],

  "unresolved_packages_for_kb_expansion": [],
  "conservative_fallback_rate": 0.0,                            // [v2] coverage honesty metric

  // [v2] §7.6 — the contribution, per run
  "stagecraft_shadow_classification": {
    "blanket_label": "runtime",
    "rule_basis": "pip_referenced_in_runtime_instructions",
    "divergence_count": 0,
    "divergent_packages": [],
    "caveat": "Reimplementation of the rule as described in the paper; not the authors' tool output."
  },

  // [v2] G2 — build and runtime reported separately
  "build_validation_result": {
    "status": "SUCCESS" | "FAILURE" | "RUNTIME_WARNING",
    "build_phase": {
      "exit_code": 0, "duration_seconds": 0.0,
      "image_size_bytes": 0, "layer_count": 0, "context_size_bytes": 0
    },
    "baseline_original": {"image_size_bytes": 0, "layer_count": 0, "duration_seconds": 0.0},
    "smoke_test": {
      "import_check": {"status": "PASS", "failed_imports": []},
      "entrypoint_check": {"status": "PASS", "container_logs_excerpt": ""},
      "console_scripts": [{"script": "gunicorn", "resolved": true, "version_output": ""}]
    },
    "failure_category": null, "failure_phase": null
  },

  "size_reduction_percent": 0.0,
  "size_reduction_attribution": {                               // [v2] §9.4 honesty
    "build_only_system_packages_bytes_est": 0,
    "discarded_build_cache_bytes_est": 0
  }
}
```

> **[v2 NOTE — one naming change worth making.]** v1 called this field
> `estimated_size_reduction_percent`. Once §10.1 builds both the original and the
> refactored image, this is **measured**, not estimated — hence `size_reduction_percent`.
> Keep estimates and measurements in separately-named fields throughout; a reviewer who
> finds an "estimated" figure presented as a result will discount the entire results
> chapter, and here you have the real measurement, so take credit for it.

Also emit a short human-readable summary to stdout: the divergence count, the
conservative fallback rate, the measured size reduction, and any warnings (unanalysed
manifests, base image change, degraded wheel checks).

---

## 12. TECH STACK

- **Language**: Python 3.x throughout (pin a specific minor version for reproducibility)
- **Dockerfile parsing**: `dockerfile-parse`
- **Manifest parsing**: `pip-requirements-parser`
- **Wheel filename/tag parsing**: `packaging` — use `packaging.utils.parse_wheel_filename`
  and `packaging.tags` rather than hand-rolled regex. **[v2]** This is not a preference:
  PEP 427 filenames have compound and dot-separated tags (§7.2.3) and hand-written
  splitting is where G8-class bugs come from. `packaging` is maintained by PyPA and is
  already a transitive dependency of pip.
- **Version specifier handling**: `packaging.specifiers` / `packaging.version` for §7.2.2
  resolution — do not compare version strings lexically
- **Live dependency resolution**: `requests` against the PyPI JSON API
- **Docker orchestration/validation**: `docker` (docker-py) and/or `subprocess`.
  **[v2]** The smoke tests (§10.2) need `run`, `exec`, `logs`, and `stop`; docker-py
  handles these more cleanly than shelling out, but `subprocess` is acceptable if the
  log capture is careful. Pick one and be consistent.
- **Knowledge base storage**: JSON or YAML file (not a database — human-editable, and
  diffable so §10.4's freeze protocol works via git)
- **Testing**: `pytest`, with `responses` or `requests-mock` to stub the PyPI API so unit
  tests are hermetic and do not depend on the network or on live PyPI contents

---

## 13. SUGGESTED PROJECT FILE STRUCTURE

```
project_root/
├── parser/
│   ├── dockerfile_parser.py       # Module 1 + §4a base image analysis
│   └── manifest_parser.py         # Module 2 + §5.4 tier-2 detection
├── detection/
│   └── stack_detector.py          # Module 3
├── classification/
│   ├── knowledge_base.json        # curated package data (§7.1)
│   ├── target_resolver.py         # [v2] §7.2 target triple resolution
│   ├── wheel_resolver.py          # §7.2 libc/ABI-aware wheel check + lockfile
│   ├── classifier.py              # §7.3-7.5
│   └── stagecraft_shadow.py       # [v2] §7.6 baseline reimplementation
├── scoring/
│   ├── necessity_scorer.py        # Module 5
│   └── scoring_config.yaml        # [v2] weights + threshold, not in code
├── synthesis/
│   ├── dockerfile_synthesizer.py  # Module 6 (venv pattern, §9.2-9.3)
│   └── templates/                 # [v2] Dockerfile templates, kept out of Python strings
├── validation/
│   ├── build_validator.py         # §10.1
│   ├── smoke_tester.py            # [v2] §10.2 — the G2 fix
│   └── failure_classifier.py      # §10.3
├── reporting/
│   └── report_generator.py        # Module 8
├── test_fixtures/                 # sample Dockerfiles + manifests (§4)
├── tests/                         # pytest unit tests per module
├── benchmark/
│   ├── corpus.yaml                # [v2] §14.1 curated corpus w/ native-dep tags + split
│   ├── run_evaluation.py          # runs tool + baselines across corpus
│   └── ground_truth/              # [v2] §14.3 manual labels for accuracy measurement
├── artifacts/
│   ├── wheel_resolution_lock.json # [v2] §7.2.2 reproducibility
│   └── kb_gaps.jsonl              # §10.4
├── main.py                        # CLI entry point
└── requirements.txt               # this project's OWN dependencies
```

---

## 14. EVALUATION METHODOLOGY — SUBSTANTIALLY REVISED in v2

### 14.1 Corpus curation — G5

> **[v2 PATCH — G5.]** v1 said "assemble 50-80 real-world Python/ML repositories." A random
> sample of Python repositories is dominated by pure-Python dependency sets, and on a
> pure-Python manifest this tool and StageCraft produce **identical** output — there are no
> build-only native dependencies to separate. A random corpus would therefore show a
> divergence near zero and a size reduction near zero, and the write-up would report a
> null result *caused by sampling*, not by the method. This is the difference between
> "the contribution does not work" and "the corpus could not detect it," and a reviewer
> cannot distinguish them from the numbers alone.

**Stratified corpus, 50-80 repositories, with mandatory composition:**

| Stratum | Share | Definition | Purpose |
|---|---|---|---|
| **A — heavy native** | **≥ 60%** | ≥ 2 packages needing native build deps when built from source (`scipy`, `lxml`, `psycopg2`, `opencv-python`, `pillow`, `cryptography`, `numpy`, `pyarrow`, `h5py`, `mysqlclient`, `pycurl`, `uwsgi`, `grpcio`) | Where the contribution is measurable at all |
| **B — mixed** | ~25% | ≥ 1 native + several pure-Python | The realistic middle; where per-package precision matters most |
| **C — pure Python** | ~15% | no native deps | **Negative control.** Tool must produce ~zero divergence and must not over-flag. Proves precision, not just recall |

Stratum C is not filler — it is what makes a positive result in A credible. A tool that
flags build dependencies everywhere would score well on A and badly on C; reporting both
demonstrates the classifier discriminates rather than merely being aggressive.

**Selection procedure, documented for reproducibility:**
1. Define the search: GitHub repositories, Python-majority, containing a `Dockerfile`
   with exactly one `FROM`, plus a `requirements.txt`; record the search query verbatim.
2. Apply objective inclusion filters (≥ N stars, pushed within M months, builds
   successfully in its original form) and record counts at each filter step.
3. Classify each candidate into A/B/C by *mechanically* scanning its manifest against the
   native-dependency list — script this so the assignment is reproducible, not judged.
4. Sample within each stratum to hit the quotas. If stratum A is short, widen the search;
   **do not** relax the ≥60% requirement.
5. **[v2]** Record each candidate's declared Python version and prefer projects on Python
   3.11+ where the stratum quota allows a choice. This is the *only* lever the project has
   over the glibc-minor-version limitation (§7.2.1, §15 item 8): Python 3.11+ `-slim`
   images are bookworm (glibc 2.36), while 3.9 and older are bullseye (glibc 2.31), and
   §9.1 forbids the synthesiser from changing a project's Python version to fix this.
   Do **not** exclude older-Python projects outright — that would bias the corpus toward
   modern, well-maintained repositories and weaken the external-validity claim. Include
   them, and report their `target.glibc_risk = "live"` flag in the results table so any
   failure among them is attributable rather than mysterious.
6. **Publish `corpus.yaml`** with repository URLs, pinned commit SHAs, stratum
   assignments, and the dev/eval split (§10.4). Pinned SHAs are essential: repositories
   change, and without them nobody, including you, can reproduce the numbers later.

**Declare the bias plainly in the write-up.** The corpus deliberately over-represents
native-dependency-heavy projects relative to the Python ecosystem at large. Framed
correctly this is a strength — it is a targeted evaluation of the mechanism under study,
and stratum C bounds the over-flagging risk. Framed as if it were a representative
random sample, it is a fatal misrepresentation. The honest sentence is: *"we deliberately
over-sample native-dependency-heavy projects because the mechanism under test is inert on
pure-Python manifests; stratum C quantifies the false-positive cost of that mechanism, and
we make no claim about ecosystem-wide average reduction."* Do not claim the measured
average reduction generalises to all Python projects — it does not, by construction.

### 14.2 Baseline availability contingency — G6

> **[v2 PATCH — G6.]** v1 said to run StageCraft and PARFUM on the corpus, with a
> parenthetical "check the paper's data availability link." That is the entire comparative
> evaluation resting on an unverified assumption. StageCraft was published in July 2026;
> its artifact may be unreleased, unbuildable, dependent on a hosted service, or
> restricted. PARFUM targets Dockerfile *smell repair* rather than multi-stage
> refactoring, so it may not even be commensurable on these metrics. **Verify both before
> committing to a plan that depends on them** — this is a scheduling risk, not just a
> methods risk, and it is cheap to check early and expensive to discover late.

**Do this first, before writing code**, and record the outcome:
1. Locate StageCraft's artifact (paper's data-availability statement, ACM DL supplement,
   authors' GitHub). Attempt to install and run on one fixture.
2. Same for PARFUM.
3. Record precisely: reachable? installable? runnable? what input does it require? what
   output does it produce? is it commensurable with our metrics?

**Tiered fallback, in preference order:**

- **Tier 1 — both tools run.** Full three-way comparison as v1 intended. Also report the
  §7.6 shadow reimplementation against real StageCraft output; agreement validates the
  shadow, disagreement is a finding worth reporting in its own right.
- **Tier 2 — StageCraft unavailable, described precisely enough to reimplement.** Use the
  §7.6 shadow classifier as the baseline. Label it unambiguously throughout as *"StageCraft
  inheritance rule, reimplemented from the paper's description (§3.2.2)"* — never as
  "StageCraft." Include the rule's description verbatim in an appendix so a reader can
  check the fidelity of the reimplementation themselves. This is a legitimate and common
  methodology when artifacts are unavailable; it is only illegitimate if the
  reimplementation is presented as the original.
- **Tier 3 — neither available and the rule cannot be faithfully reproduced.** Fall back
  to comparison against (a) the unmodified original single-stage Dockerfile, and (b) a
  naive multi-stage baseline that moves *all* pip packages to the builder and copies the
  venv without per-package analysis. Baseline (b) is the important one: it isolates the
  contribution of per-package classification from the contribution of merely using
  multi-stage builds. **Implement baseline (b) regardless of tier** — it is ~50 lines,
  and without it a reviewer can fairly ask whether the gains come from your classifier or
  simply from adopting multi-stage builds at all. That question should not be left open.

Whichever tier applies, state it explicitly in the evaluation section along with the
evidence for it (dates checked, URLs, error messages). "The artifact was unavailable" is
an acceptable finding; an unexplained absent baseline is not.

### 14.3 Metrics and ground truth — G9

> **[v2 PATCH — G9.]** v1 listed "classification accuracy (spot-checked manually against a
> sample)" as a metric. Spot-checking is not a measurement procedure: no defined ground
> truth, no sample size, no inter-rater agreement, no protocol. As written it is
> unfalsifiable, and it is the metric most directly tied to the project's central claim —
> so it is the one most worth making rigorous.

**Automatically measured metrics** (no human judgement required):

| Metric | Definition | Notes |
|---|---|---|
| Build success rate | fraction with `docker build` exit 0 | Phase 1 only |
| **`working_image_rate`** | **fraction passing build AND Test A (import)** | **[v2] the headline correctness metric — G2.** Test A only, so it isolates classification correctness |
| **`full_success_rate`** | fraction passing build AND Test A AND Test C | **[v2]** the operational claim; per §10's status table. A gap vs `working_image_rate` indicates a venv/`PATH` synthesis problem independent of classification |
| Size reduction % | `(orig - refactored) / orig` | measured, both built (§10.1) |
| Layer count delta | | |
| Build duration delta | report cold and warm cache separately | |
| **Divergence count** | packages in one manifest receiving different build/runtime classifications | **the core-contribution metric**; structurally 0 for StageCraft |
| Conservative fallback rate | fraction taking the §7.3 step-4 path | KB coverage measure |
| Failure category distribution | counts per §10.3 category | the error analysis |

**Classification accuracy against a defined ground truth:**

Ground truth is established **empirically, not by opinion.** For a stratified random
sample of 15-20 repositories (drawn from the *evaluation* split, sampled across A/B/C):

1. For each package, determine the true build/runtime dependency split by direct
   experiment: build in a container, then use `ldd` on the installed `.so` files to
   enumerate actual linked shared libraries, and `dpkg -S` to map each to its Debian
   package. That set **is** the runtime dependency ground truth — it is derived from the
   binary, not from anyone's belief about the binary.
2. For build dependencies, install from source with `--no-binary :all:` in a minimal
   container and record which system packages are required to make it succeed.
3. Record the resulting labels in `benchmark/ground_truth/` as data files, with the
   commands used, so the derivation is auditable and re-runnable.
4. Report **per-package precision and recall** for build deps and runtime deps
   separately. Precision matters for size (over-inclusion = bloat); recall matters for
   correctness (under-inclusion = broken container). They are not interchangeable, and a
   single "accuracy" number would hide exactly the trade-off under study.
5. Break results down by `confidence_source` (`kb`, `kb+wheel`, `inferred-wheel`,
   `unresolved-conservative`). This shows whether the tool's own confidence signal is
   calibrated — a genuinely interesting secondary result, and cheap given the data is
   already collected.

Where a human judgement is unavoidable, have **two** annotators label independently and
report Cohen's κ. If only one annotator is available, say so and label the metric as
single-annotator — a stated limitation is fine; an unstated one is not.

### 14.4 Reproducibility requirements — G7

Ship, alongside the results:
- `corpus.yaml` with pinned commit SHAs and stratum/split assignments
- `wheel_resolution_lock.json` (§7.2.2), so classifications replay identically offline
- the frozen `knowledge_base.json` at its recorded commit hash
- all per-repository JSON reports (§11)
- `kb_gaps.jsonl`
- the exact tool versions: Python, Docker, pip, and this tool's own commit

State the date range of the PyPI queries. Wheel availability changes over time — a
package with no `musllinux` wheel today may have one next year — so results are
timestamped observations, not permanent facts about those packages. Saying so preempts
the obvious objection and costs one sentence.

---

## 15. KNOWN LIMITATIONS TO DOCUMENT HONESTLY

These are stated limitations, not defects. Each is a deliberate scope decision with a
rationale; the ones marked **[v2]** were added or sharpened during the v2 review.

**Dependency analysis**
1. Knowledge base coverage is incomplete (60-150 packages). Unresolved packages take a
   conservative path; the `conservative_fallback_rate` metric quantifies this honestly
   rather than hiding it.
2. No transitive dependency resolution — only directly-declared manifest packages are
   analysed. A pure-Python direct dependency may pull a compiled transitive one, whose
   runtime libraries this tool will not know about. **[v2]** The §10.2 import smoke test
   catches the resulting breakage empirically even though the classifier cannot predict
   it, which bounds the practical impact of this limitation.
3. **[v2]** Only `requirements.txt` is parsed. `pyproject.toml` (PEP 621/Poetry),
   `Pipfile`, `poetry.lock`, `Pipfile.lock`, `uv.lock`, `setup.py`, `setup.cfg`, and
   `environment.yml` are **detected and reported as unanalysed** (§5.4) but not parsed.
   Given the ecosystem's ongoing migration to PEP 517/621, this is the most consequential
   scope limitation in the tool and the highest-value single item of future work.
4. No extras-aware variant resolution beyond a basic CUDA/CPU signal check for well-known
   packages like `torch`.
5. No support for private/internal package indexes; public PyPI only.
6. **[v2]** Wheel availability is a timestamped observation, not a stable property.
   Results are valid as of the recorded query dates (§14.4).

**Platform and libc**
7. **[v2]** Alpine/musl is **detected but never selected** for either stage (§9.1). Alpine
   inputs are rebased to Debian for both builder and runtime, loudly and reportedly —
   rebasing only the runtime would leave a musl builder feeding a glibc runtime (G1b).
   musl support requires a parallel apk knowledge base and is future work.
8. **[v2]** glibc *minor* version compatibility is not verified — any `manylinux*` tag is
   treated as matching a glibc target (§7.2.1). This is safe on **bookworm** (glibc 2.36)
   for the manylinux levels in common use, but **not** on **bullseye** (glibc 2.31), where
   wheels tagged `manylinux_2_34`+ will fail to load. The tool cannot fix this by choosing
   a newer base: the codename follows from the project's Python version, and §9.1 forbids
   changing that. It instead flags affected runs via `target.glibc_risk = "live"` (§11),
   biases corpus selection toward bookworm-era Python (§14.1), and relies on the §10.2
   import test to catch the failure empirically. Strict prediction would need a
   distro→glibc table plus per-wheel minor-version comparison.
9. **[v2]** Only `x86_64` and `aarch64` are considered. Cross-architecture builds are not
   validated by execution, since the smoke test needs to run the image.
10. **[v2]** Free-threaded CPython (`cp3Nt`) wheels are treated as non-matching unless the
    target is explicitly free-threaded.

10b. **[v2]** A bare `linux_x86_64` platform tag is treated as never matching. This is
    correct for a public-PyPI-only tool, because PyPI refuses uploads of such wheels — but
    the reason is PyPI's upload policy, not a pip restriction (pip installs them happily
    from local files or private indexes). Anyone extending this tool to private indexes
    must revisit the rule.

**Synthesis**
11. Artifact copying falls back to whole-directory copying for most Python web app cases
    (§9.4) — intentional, and it means size reduction comes almost entirely from excluded
    build-only system packages and discarded build caches, not from source pruning.
12. **[v2]** Builder and runtime must share the same Python major.minor, because a copied
    venv's compiled extension modules are ABI-bound to their interpreter. This constrains
    the tool from "upgrading" old base images, which is a reasonable constraint for a
    refactoring tool but worth stating.
13. **[v2]** `CMD`/`ENTRYPOINT` are preserved verbatim, including shell form. Shell-form
    entrypoints inherit the PID-1 signal-handling caveats of the original; the tool does
    not convert forms, because that would be a behavioural change beyond its remit.
14. **[v2]** Data/model exclusions in `.dockerignore` are heuristic and reported for user
    confirmation. Excluding a model file loaded at startup would break the container, so
    the tool errs toward inclusion.

**Evaluation**
15. **[v2]** The corpus deliberately over-samples native-dependency-heavy projects
    (§14.1). Measured average size reduction does **not** generalise to a random sample of
    Python projects, by construction. Stratum C bounds the over-flagging cost.
16. **[v2]** If StageCraft's artifact is unavailable, the baseline is a reimplementation of
    its published rule (§14.2 Tier 2), which is evidence about the *algorithm as
    described*, not a measurement of the authors' tool.
17. **[v2]** Ground-truth labelling covers a 15-20 repository sample, not the full corpus
    (§14.3), and where human judgement is involved the annotator count is reported.
18. **[v2]** Test B (entrypoint smoke test) yields `RUNTIME_WARNING` rather than `FAILURE`
    on apps needing external services, credentials, or volumes. The working-image rate
    therefore rests on Test A (imports), which is the portion attributable to this tool.

---

## 16. DEFINITION OF DONE (per module)

- **Module 1 (Dockerfile parser)**: all §4 extraction methods implemented and verified
  against every fixture; all §4 error cases handled without crashing. **[v2]** §4a base
  image decomposition returns correct `libc_family` for: `python:3.11-slim` (glibc),
  `python:3.11-alpine` (musl), `ubuntu:22.04` + apt (glibc), and an unrecognised tag
  (unknown). `apk add` detection works.
- **Module 2 (Manifest parser)**: correctly extracts name/version/extras from a realistic
  `requirements.txt`; flags git URLs as unresolvable rather than crashing. **[v2]** PEP 503
  normalisation verified on `scikit_learn` / `Scikit-Learn` / `scikit.learn`; §5.4 Tier 2
  detection finds a dependency-declaring `pyproject.toml` and correctly ignores a
  config-only one.
- **Module 3 (Stack detection)**: identifies Python as primary on all Python fixtures;
  identifies a secondary Node.js stack on at least one hybrid fixture. **[v2]** Weights
  live in config, not code, and the values used appear in the report. **[v2]** Architecture
  type correct (`WEB_API` vs `BATCH_JOB`) on two fixtures differing only in
  `EXPOSE`/`CMD` — needed by §10.2 Test B to judge whether a short-lived exit is legitimate.
- **Module 4 (Classification engine)**: **THE CRITICAL TEST.** For one `requirements.txt`
  containing `flask` and `lxml`, produces *different* native-dependency results for the
  two, and the §7.6 shadow classifier produces the *same* label for both. This single
  divergence is the project's contribution made concrete; if it does not hold, nothing
  downstream matters.
  **[v2] Additionally required:**
  - **G1, at the resolver level:** with `target_libc` forced to `musl`, `manylinux`-only
    packages must **not** match and must fall to the conservative path. Test this as a unit
    test on the wheel resolver, since §9.1 means no end-to-end run ever produces a musl
    target.
  - **G1, end-to-end on the Alpine fixture:** `input_libc = musl` detected, both stages
    rebased to Debian, and wheels then resolved against `target_libc = glibc` — so a
    `manylinux` wheel **matches**. Asserting no-match here would encode the
    over-conservatism §7.2 warns against.
  - `cp37-abi3` wheel matches a 3.11 target; `cp39-cp39` does not (G8).
  - `py3-none-any` matches every target including `unknown` libc.
  - `linux_x86_64` (unqualified) never matches.
  - Unpinned specifier produces a recorded `resolved_version`, and `--use-lock` replays
    the identical classification with the network **actually disabled** (G7) — test by
    disabling it, not by trusting the cache.
  - `psycopg2` and `psycopg2-binary` in one manifest receive different classifications.
  - `libc == "unknown"` takes the conservative path rather than assuming glibc (§7.2.1).
  - A simulated network failure surfaces as `wheel_check = "unavailable"`, and a 404 as
    `"not_on_pypi"` — neither may masquerade as a genuine "no wheel" (§7.2.4).
  - `conservative_fallback_rate` is computed and present in the report.
  - KB lookups use the **output** distro family, not the input's (§7.3): an Alpine input
    yields Debian package names such as `libxslt1.1`, never `libxslt`.
- **Module 5 (Necessity scoring)**: produces a numeric score and correctly gates on one
  clearly-bloated and one clearly-minimal fixture. **[v2]** Sub-scores normalised to 0-100;
  threshold calibrated on the dev split and frozen; sensitivity table produced.
- **Module 6 (Synthesis)**: produces a syntactically valid Dockerfile for every fixture
  (verify with `docker build --check` or `hadolint`). **[v2]** Additionally:
  - Output uses the `/opt/venv` pattern with **identical paths** in both stages
  - Sets `ENV PATH` in **both** stages (grep for it; it fails silently by running the
    system Python)
  - Never emits `COPY --from=builder /install /usr/local`
  - Never emits an Alpine base for **either** stage (§9.1) — builder and runtime libc must
    match, so an Alpine input rebases both
  - Fails loudly if builder and runtime Python major.minor **or** libc would differ
  - `--keep-base-family` on an Alpine input aborts with an explanation rather than
    attempting musl synthesis
  - `CMD`/`ENTRYPOINT` byte-identical to the original, shell/exec form preserved
  - The naive multi-stage baseline synthesiser (§14.2 baseline (b)) is implemented — it is
    required regardless of evaluation tier
- **Module 7 (Validation)**: runs `docker build` against synthesized output for **every**
  fixture and correctly classifies the induced failures below. (v1 said "the majority of
  fixtures"; v2 requires every one. A fixture that cannot be validated is either a fixture
  worth fixing or a limitation worth recording — both are better than an unexamined gap,
  and "every" is a checkable condition where "majority" is not.)
  **[v2] Extended, and this is the G2 fix:** the import smoke test must run on **every**
  successful build (not "the majority" — every), and these four induced failures must be
  correctly categorised:
  1. Remove a needed runtime lib from the runtime stage → `MISSING_RUNTIME_SYSTEM_DEP`,
     phase `runtime`, detected by Test A (**not** by the build, which will pass — this is
     the precise case v1 could not see).
  2. **Delete the runtime `ENV PATH` line** → the container runs the system Python, which
     lacks the venv's packages. Test C reports the console script unresolvable
     (`command -v` fails); category `MISSING_PACKAGE`, phase `runtime`.
     > Note the category deliberately: this produces `command not found`, **not** a bad
     > interpreter, so it is *not* `BROKEN_SHEBANG`. §10.3's `MISSING_PACKAGE` row names
     > "`PATH` wrong" as one of its causes, which is exactly this. Keep the two faults and
     > their categories distinct — see item 3.
  3. **Rewrite a console script's shebang to a non-existent interpreter** (simulating the
     `--prefix=/install` bug this spec removed) → `BROKEN_SHEBANG`, phase `runtime`, caught
     by Test C. This is the direct G4 regression test and needs its own fixture, because
     item 2 does not exercise this code path.
  4. Omit a build dep for a source-built package → `MISSING_BUILD_SYSTEM_DEP`, phase
     `build`.
- **Module 8 (Reporting)**: valid, complete JSON matching §11 for every run, including the
  target triple, resolved versions, shadow classification, and the separated
  build/smoke-test results.

---

## 17. IMPLEMENTATION ORDER AND OPEN QUESTIONS — NEW in v2

### 17.1 Order
See `ROADMAP.md` for the phased build plan, dependencies between phases, and the
verification gate at the end of each. Phase 0 (verify the StageCraft quote and artifact
availability, §1.3 and §14.2) comes **before** any implementation, because both the
project's framing and its evaluation design depend on the answers.

### 17.2 Open questions to resolve, not to guess

Flagged per the specification's own instruction to surface ambiguity rather than silently
resolve it:

1. **Is the StageCraft quote accurate and is §3.2.2 the right reference?** The entire
   motivation rests on it (§1.3). Verify before coding.
2. **Is StageCraft's artifact obtainable and runnable?** Determines evaluation Tier
   (§14.2), and therefore the shape of the results chapter.
3. **Is PARFUM commensurable at all?** It repairs Dockerfile smells rather than
   introducing multi-stage builds; if the metrics do not apply, drop it with a stated
   reason instead of forcing a comparison that does not mean anything.
4. **Necessity-score threshold value**, post-calibration (§8). Must be frozen before the
   evaluation run and reported with a sensitivity table.
5. **Stack-detection weights** (§6). Config values needing a stated derivation.
6. **How large can the corpus realistically be?** Every repository requires two Docker
   builds (original + refactored) plus smoke tests. For ML images at multiple GB each
   this is substantial disk, time, and bandwidth. If 50-80 proves infeasible, reduce the
   count while **preserving the ≥60% stratum-A proportion** (§14.1) — a smaller stratified
   corpus is far better than a larger unstratified one, and much better than a corpus that
   cannot be run to completion. Decide this early, on measured timings from a pilot of
   five repositories, not on optimism.
7. **Ground-truth annotator count** (§14.3). If only one, state it.

---

END OF SPECIFICATION v2. If any instruction above is ambiguous or conflicts with a
practical implementation constraint discovered during coding, flag it explicitly rather
than silently resolving it in a way that might diverge from the intended design. Record
any such deviation, with its rationale, in this document as a v3 patch note rather than
only in code comments.
