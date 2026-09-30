# DockerStage

Package-level dependency resolution for Python/ML Dockerfiles.

DockerStage automatically refactors single-stage Python Dockerfiles into optimized multi-stage builds — but unlike existing tools, it does this by actually reading your requirements.txt and reasoning about each declared package individually, rather than making one blanket decision for the whole file.

# The problem

Most Python/ML Docker images are built as single-stage Dockerfiles — everything needed to build the application (compilers, dev headers, build tools) ends up permanently baked into the production image, even though none of it is needed once the app is actually running.

This is especially wasteful for ML/data libraries, which are often not pure Python:

lxml needs libxml2-dev/libxslt-dev (headers) to compile, but only the much smaller libxml2/libxslt1.1 runtime libraries afterward
psycopg2 needs libpq-dev to build, but only libpq5 at runtime
numpy/scipy often need a C/Fortran compiler and BLAS/LAPACK to build from source — though a pre-built wheel frequently avoids this entirely

Leaving the build-only tooling in the final image means unnecessarily large images and a wider security attack surface (every extra package is a potential CVE source).

# The existing gap

Multi-stage Docker builds solve this in principle — build in a full-featured stage, copy only runtime artifacts into a minimal final stage — but manually refactoring an existing Dockerfile this way is error-prone.

A recent tool, StageCraft (Chen, Yang, Pan, Zhou, FSE 2026), automates this refactoring. However, per its own discussion section, StageCraft deliberately restricts its analysis to the Dockerfile text alone and does not parse the dependency manifest — the authors explicitly name this as unexplored future work. As a result, its Python dependency classification uses an all-or-nothing "inheritance rule": if pip is used at runtime, every package it ever installed is labeled runtime; if pip is build-only, every package is labeled build-only — one decision for the entire manifest, regardless of what each individual package actually needs.

Consequence: a requirements.txt containing both flask (pure Python, needs nothing extra) and lxml (needs a compiler and headers to build) would receive the identical classification under this rule.

# What this tool does differently

dockerstage parses requirements.txt directly and resolves each declared package's actual build-time and runtime native dependencies individually, using:

a curated knowledge base mapping packages to their distinct build-time (compiler/headers) and runtime (shared library) system dependencies
a live PyPI wheel-availability check — if a pre-built wheel exists for the target platform, no compilation happens at all, regardless of what the knowledge base would otherwise suggest

This enables package-level precision: within a single manifest, flask and lxml can correctly receive different classifications — something a manifest-blind, manager-level rule structurally cannot do.

# Pipeline overview

Dockerfile + requirements.txt
        │
        ▼
 [1] Parsing layer           — structured Dockerfile + manifest objects
        ▼
 [2] Stack/language detection — primary/secondary language, architecture type
        ▼
 [3] Dependency classification — per-package build/runtime resolution (core contribution)
        ▼
 [4] Necessity scoring        — is refactoring even worth it?
        ▼
 [5] Multi-stage synthesis    — generates the optimized Dockerfile + .dockerignore
        ▼
 [6] Build validation         — actually builds the result, classifies any failures
        ▼
 [7] Reporting                — JSON report of what was classified, how, and why

 # Acknowledgment

This project's motivation and comparative baseline are grounded in:

D. Chen, W. Yang, M. Pan, and Y. Zhou, "Automating Dockerfile Refactoring to Multi-stage Builds," Proc. ACM Softw. Eng., vol. 3, no. FSE, Article FSE070, Jul. 2026. 
(https://dl.acm.org/doi/10.1145/3797098)
