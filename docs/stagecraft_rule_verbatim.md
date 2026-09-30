# StageCraft dependency-classification rule — verbatim transcription

Source: Dongjin Chen, Wenhua Yang, Minxue Pan, Yu Zhou, "Automating Dockerfile
Refactoring to Multi-stage Builds," *Proc. ACM Softw. Eng.* 3, FSE, Article FSE070,
July 2026. Local copy: `3797098.pdf` (22 pp.). Transcribed 2026-09-04 from
`pdftotext -layout`.

This file exists so the Phase 4 shadow classifier (spec §7.6) can be checked against
StageCraft's *actual* wording, not a paraphrase. Do not paraphrase it away.

---

## §3.2.2 Dependency Classification (page FSE070:5–6) — verbatim

> This component classifies each declared Dockerfile dependency as either a build or
> runtime dependency. Misclassification leads to two opposite risks. A conservative
> policy may retain unnecessary tools. For example, labeling gcc (used only to compile
> lxml) as runtime leaves a redundant compiler in the final image, inflating size and
> increasing the attack surface [25]. Conversely, an aggressive policy may remove
> required tools. If a service relies on curl in a HEALTHCHECK instruction but curl is
> treated as build-time and removed, the container can repeatedly fail health checks,
> disrupting orchestrated deployments such as Kubernetes.
>
> Our empirical analysis shows that dependencies differ in origin, purpose, and runtime
> relevance. We group them into two broad classes:
>
> - **System-level dependencies:** Installed via system package managers (e.g.,
>   apt-get); often a mixture of utilities such as python3 and git. Even when listed
>   together, their roles can diverge. For instance, python3 is commonly needed at
>   runtime, whereas git is typically build-only.
> - **Application-level dependencies:** Installed via language-specific package managers
>   (e.g., pip and npm); these libraries usually function as a coherent set serving
>   either runtime behavior or the build process. For example, libraries installed
>   through pip usually support either the application's runtime behavior or its build
>   process as a group.
>
> StageCraft applies tailored rules to each class. For system-level dependencies, we
> first match packages against a curated list of known build-time tools (e.g., gcc,
> make, and git), compiled from official documentation, industry practices, academic
> studies, and security advisories. The list is provided in our supplementary material.
> Matches are labeled build-time. Otherwise, we assess the package's relation to the
> detected target language (Section 3.2.1). If it is a core runtime component of that
> language (e.g., python3 for Python projects), we classify it as runtime. For remaining
> cases, StageCraft defaults to a conservative build-time label to preserve build
> correctness. As an additional safeguard, StageCraft checks whether the dependency
> appears in runtime-relevant Dockerfile instructions (CMD, ENTRYPOINT, HEALTHCHECK). If
> so, it is promoted to runtime.
>
> For application-level dependencies, StageCraft uses an **inheritance rule**. Because
> these packages are introduced by a language-specific package manager, their label
> follows how the package manager itself is used. If the package manager is present at
> runtime, all packages it installs are treated as runtime. If the package manager is
> only needed during the build, its packages are labeled build-time. This strategy
> reflects common practice, where such managers install a set of libraries that support
> a specific stage of the software lifecycle.

---

## §4.4 Discussions (page FSE070:18) — the input-scope statement, verbatim

> Consistent with this lightweight philosophy, we deliberately restrict the input scope
> to the Dockerfile rather than the full project context to avoid the complexity of
> parsing heterogeneous build systems. Nonetheless, we acknowledge that taking the whole
> project context, including source code, build scripts, and manifests, as input could
> further improve the quality of multi-stage builds, and we consider the exploration of
> such hybrid analysis strategies a promising direction for future research.

---

## §3.2.1 Multi-Signal Language Identification — the one place a manifest name appears

StageCraft's language identifier scans the Dockerfile **line by line** and scores
candidate languages on signals including "the base image, package managers, build tools,
framework keywords, configuration files, source file extensions, and runtime
directives." It names `COPY requirements.txt` and `.py`/`.go` file extensions as
*low-weight* signals, explicitly ranked below base images and `CMD`/`ENTRYPOINT`.

The distinction that matters: StageCraft reacts to the **string `requirements.txt`
appearing in a `COPY` instruction inside the Dockerfile**. It does not open the file or
read its contents. So it never enumerates the packages inside the manifest — which is
why it cannot tell `flask` from `lxml`.

---

## The shadow classifier (spec §7.6) must reproduce exactly this

For a faithful baseline, `stagecraft_shadow.py` implements the **application-level
inheritance rule only**, applied to pip:

- If pip is used at runtime (the manifest install / a pip-installed entry appears in a
  runtime-relevant instruction, or pip is otherwise present in the runtime image) →
  label **all** pip packages `RUNTIME`.
- If pip is used only during build → label **all** pip packages `BUILD_ONLY`.

One blanket label for every pip package. No per-package inspection. That single
all-or-nothing decision is what this project's per-package manifest analysis is measured
against (the flask-vs-lxml divergence, spec §16 Module 4 / §14).
