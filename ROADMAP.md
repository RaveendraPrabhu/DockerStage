# IMPLEMENTATION ROADMAP

Companion to `PROJECT_SPEC.md` (v2). The spec says *what* to build; this says *in what
order*, *why that order*, and *how you know a phase is actually finished*.

---

## How to use this document

Nine phases. Each has an objective, a rationale for its position in the sequence, concrete
deliverables, and an **exit gate** — a checkable condition, not a feeling. Do not start a
phase before its gate predecessors pass.

Two rules that matter more than the schedule:

**The exit gates are the point.** A phase that "mostly works" but fails its gate will cost
more later than finishing it now, because every downstream phase inherits the defect.
Phase 4's gate in particular is the whole project in miniature.

**Phase 0 is not optional and not busywork.** It can invalidate the project's framing or
its evaluation design. Doing it in week one costs two days; discovering it in week ten
costs the results chapter.

### Critical path

```
Phase 0  ─→ 1 ─→ 2 ─→ 3 ─→ 4 ─→ 5 ─→ 6 ─→ 7 ─→ 8
(verify)   (parse)  (KB)  (classify) (score) (synth) (validate) (eval) (write)
              │             ▲                    │        │
              └── 4 needs 1,2,3 ────────────────┘        │
                            │                             │
              Phase 6 gate needs Phase 5 output ──────────┘
```

Phases 1, 2, and 3 are largely independent of each other and can be interleaved or
parallelised. Everything from Phase 4 onward is strictly sequential — Phase 4 consumes all
three, and each later phase consumes the one before it.

---

## Phase 0 — Verify the foundations (do this first)

> **STATUS 2026-09-04 — substantially DONE. See `docs/phase0_verification.md`.** All three
> claims (a/b/c) verified against `3797098.pdf`: quote verbatim (§4.4), inheritance rule
> confirmed (§3.2.2), manifest contents never parsed. One framing error found and fixed —
> StageCraft *does* classify apt packages per-item, so the contribution is the pip layer
> only (spec §7.4 corrected). Rule transcribed verbatim to `docs/stagecraft_rule_verbatim.md`.
> Tier decision: **provisional Tier 2** (shadow reimplementation), upgradeable to Tier 1.
> **One item still open (needs network):** confirm the StageCraft artifact URL is reachable
> and runnable — does not block Phases 1–6.

**Objective.** Confirm the two external facts the project depends on, before writing code
that assumes them.

**Why first.** Two independent single points of failure live here. If the StageCraft quote
is inaccurate or the inheritance rule works differently than §1.3 describes, the project's
entire motivation needs rewriting — and it is far cheaper to rewrite a framing than to
rewrite a framing plus 3,000 lines of code built on it. If the artifact is unobtainable,
the evaluation design changes (spec §14.2 Tier 2 or 3), which changes what Phase 4 must
also build. Both answers are inputs to later phases, not afterthoughts.

**Tasks.**
1. Obtain the StageCraft paper (*Proc. ACM Softw. Eng.* 3, FSE, Article FSE070, July 2026).
2. Verify the discussion-section quote in spec §1.3 **verbatim**. Record page and section.
3. Verify the inheritance rule is in §3.2.2 and works as characterised — in particular
   confirm claim (c): that it never reads the manifest at all, as opposed to reading it for
   some narrower purpose. Copy the rule's description verbatim into
   `docs/stagecraft_rule_verbatim.md` for the appendix and for fidelity-checking the
   Phase 4 shadow classifier.
4. Attempt to obtain and run StageCraft's artifact. Record: URL, reachable?, installable?,
   runnable on one fixture?, what input it needs, what output it gives.
5. Same for PARFUM. Additionally judge whether its metrics are commensurable with ours at
   all — it repairs Dockerfile smells rather than introducing multi-stage builds, so it may
   simply not be comparable. A stated "not commensurable, dropped, here's why" is a fine
   outcome.
6. Decide the evaluation tier per spec §14.2 and write the decision down with its evidence.

**Deliverable.** `docs/phase0_verification.md` — findings, dates, URLs, error messages,
and the resulting tier decision.

**Exit gate.**
- [x] Quote verified verbatim (§4.4 Discussions, p. FSE070:18) — no §1.3 rewrite needed
- [x] Inheritance rule confirmed (§3.2.2) and transcribed (`docs/stagecraft_rule_verbatim.md`)
- [~] Artifact availability: URL recorded; reachability open (needs network) — not blocking
- [x] Evaluation tier chosen and recorded (provisional Tier 2, upgradeable to Tier 1)

**If the gate fails.** If claim (c) is false — StageCraft does read manifests in some form
— **stop and re-scope before proceeding.** The contribution would need restating as a
difference in *depth* of manifest analysis rather than its *presence*. That is still very
likely a defensible contribution, but it is a different paper, and finding out now is a
good outcome, not a bad one.

---

## Phase 1 — Parsing layer (Modules 1 & 2)

**Objective.** Turn a Dockerfile and a manifest into trustworthy structured objects.

> **STATUS — DONE (2026-09-04).** Implemented under `app/` as the `dockerstage`
> package. 33 unit tests pass against 10 Dockerfile fixtures + 7 manifest fixtures,
> run under **both** tokenizer front-ends (`dockerfile-parse` and the stdlib
> fallback) against identical expectations. Run with `cd app && pytest`, or offline
> with `python tests/run_offline.py`. All exit-gate items below verified. One
> deviation, documented in `app/README.md`: `dockerfile-parse` is the primary
> tokenizer with a stdlib fallback so the parser runs (and is testable) where the
> library or a package index is unavailable; both emit one uniform instruction
> stream, so downstream extraction is identical.

**Why here.** Everything consumes these. A parser bug surfaces as a mysterious
classification bug three phases later, so the payoff on getting this exactly right is
higher than it feels while writing it.

**Tasks.**
1. `parser/dockerfile_parser.py` — all twelve extractions in spec §4, plus §4 error handling.
2. **§4a base image decomposition** — the `libc_family` / `distro_family` /
   `python_version` fields. This is the structural precondition for the G1 fix; without it
   Phase 4 cannot be libc-aware at all.
3. `apk add` detection alongside `apt-get install`, tagging extracted packages with their
   package manager (spec §4 item 4).
4. `parser/manifest_parser.py` — spec §5.1, plus **PEP 503 normalisation** (§5.2).
5. **§5.4 Tier 2 manifest detection** — glob for `pyproject.toml`/`Pipfile`/lockfiles;
   for `pyproject.toml` distinguish dependency-declaring from config-only. Do not parse
   dependency contents.
6. Build all fixtures in spec §4 "Test fixtures required", including the two new v2 ones
   (Alpine + `lxml`; indeterminable-libc Ubuntu).
7. pytest unit tests per extraction.

**Exit gate.**
- [x] Every §4 extraction correct on every fixture, verified against hand-written expectations
- [x] `libc_family` correct for: `python:3.11-slim`→glibc, `python:3.11-alpine`→musl,
      `ubuntu:22.04`+apt→glibc, unrecognised tag→unknown
- [x] `ARG`-based `FROM` resolves; unresolvable → `unknown_base_image` without crashing
- [x] Malformed/empty/no-FROM files raise clear exceptions or degrade gracefully per §4
- [x] `scikit_learn` / `Scikit-Learn` / `scikit.learn` all normalise to `scikit-learn`
- [x] Tier 2 detection finds a dependency-declaring `pyproject.toml`; a config-only one
      (only `[build-system]` + `[tool.ruff]`) does **not** raise a warning
- [x] Batch mode: one malformed file logs and skips without killing the run

---

## Phase 2 — Knowledge base (spec §7.1)

> **STATUS — DONE (2026-09-30).** Curated 119 entries with the v2 schema.
> Schema validator and PyPI wheel flags verification tools created.
> GitHub Actions CI workflow implemented to automate verification.

**Objective.** Curate 60-150 entries with the v2 schema.

**Why here.** Independent of Phase 1, so it can run in parallel — and it is the phase most
likely to be underestimated. Budget generously: each entry needs a build-dep list, a
runtime-dep list, import names, and ideally a verification build. This is slow, manual, and
unglamorous, and Phase 4's quality is capped by it.

**Tasks.**
1. Define the schema exactly as spec §7.1, including the v2 changes: distro-keyed dep maps,
   `import_names`, `verified_by_build`.
2. Curate, prioritising: the negative controls (`flask`, `fastapi`, `uvicorn`, `requests`,
   `pydantic` — empty dep lists, and these matter as much as the positive cases because
   they are what prevent over-flagging), then the heavy hitters (`numpy`, `scipy`, `pandas`,
   `lxml`, `pillow`, `opencv-python`, `matplotlib`, `scikit-learn`, `cryptography`,
   `psycopg2` **and** `psycopg2-binary`, `torch`, `tensorflow`, `pyarrow`, `h5py`,
   `mysqlclient`, `grpcio`, `uwsgi`).
3. Populate `import_names` — cannot be derived by string transformation
   (`opencv-python`→`cv2`, `pillow`→`PIL`, `beautifulsoup4`→`bs4`, `pyyaml`→`yaml`,
   `python-dateutil`→`dateutil`, `attrs`→`attr`). Get these right or Phase 6's smoke test
   will report false failures and you will chase phantom KB gaps.
4. **Verify every `wheel_typically_available` claim against live PyPI.** v1's example entry
   had `lxml: false`, which is wrong — lxml has shipped manylinux and musllinux wheels for
   years. Assume other inherited claims are similarly stale.
5. Record `source` honestly per entry. conda-forge is a *hint* about which native libraries
   a package touches, not ground truth for Debian package names — map and confirm yourself.
6. Track the KB in git from the first commit; §10.4's freeze protocol needs the history.

**Exit gate.**
- [x] ≥60 entries, schema-valid (write a JSON-schema check and run it in CI)
- [x] Build and runtime dep lists are genuinely different where they should be —
      spot-check `psycopg2`: `libpq-dev` build, `libpq5` runtime
- [x] `psycopg2` vs `psycopg2-binary` correctly differ
- [x] ≥10 pure-Python entries with explicitly empty dep lists (negative controls)
- [x] Every `import_names` verified by actually importing in a container
- [x] Every `wheel_typically_available` checked against PyPI, not inherited

---

## Phase 3 — Stack detection (Module 3)

**Objective.** Primary/secondary language and architecture type.

**Why here.** Small, independent, and it must precede Phase 6: §10.2 Test B needs
`architecture_type` to judge whether a container exiting quickly is a legitimate
`BATCH_JOB` or a crashed `WEB_API`. (Note it is *not* needed for Phase 5's base image
selection — §9.1 keys off the CUDA signal and Python version only.) Phase 5 does consume
`secondary_languages` to decide what can be discarded wholesale.

**Tasks.** Spec §6 weighted scoring; weights in `scoring_config.yaml`, not literals;
architecture type rules; a hybrid Python+Node fixture.

**Exit gate.**
- [x] Python primary on all Python fixtures
- [x] Node.js detected as *secondary* on the hybrid fixture, not primary
- [x] `WEB_API` vs `BATCH_JOB` correct on fixtures that differ only in EXPOSE/CMD
- [x] Weights read from config; the config values used appear in the report

---

## Phase 4 — Classification engine (Module 4) ← the core contribution

**Objective.** Per-package build/runtime resolution, libc- and ABI-aware, reproducible.

**Why here.** Needs Phases 1-3. This is where the contribution lives and where four of
the eleven v2 gaps (G1, G7, G8, plus G5's enabling metric) are fixed. Give it the most time.

**Tasks.**
1. `classification/target_resolver.py` — the `(arch, libc, python_abi)` triple, including
   the `unknown`-libc conservative path and the `--python-version` fallback.
2. `classification/wheel_resolver.py`:
   - PyPI JSON API client with retry/backoff, polite rate limiting, descriptive User-Agent
   - Wheel filename parsing via **`packaging.utils.parse_wheel_filename`** — not regex.
     Compound dot-separated tags (`py2.py3`, `cp39.cp310`) are exactly where hand-rolled
     parsing produces G8 bugs.
   - **Platform matching per spec §7.2.1** — manylinux↔glibc, musllinux↔musl, mutually
     exclusive; bare `linux_x86_64` never matches; `any` matches everything
   - **ABI matching per §7.2.3** — `abi3` forward compatibility is the case that matters;
     getting it wrong sends `cryptography` down the source path and understates your own
     results
   - **Version resolution + lockfile per §7.2.2**, with `--use-lock` / `--refresh-lock`
   - Distinguish 404 (`not_on_pypi`) from network failure (`unavailable`) from genuine
     no-match. Never let a network failure masquerade as "no wheel."
3. `classification/classifier.py` — spec §7.3 per-package algorithm and §7.4 system-package
   reconciliation, including the §7.4 step 0 package-manager mismatch check and the widened
   step 4 runtime override (ENV values, entrypoint scripts).
   **Watch the two distro families (§7.3).** KB dependency lookups use the **output**
   family (Debian, per §9.1); the **input** family is only for deciding whether the
   original Dockerfile's system packages are comparable at all (§7.4 step 0). Getting these
   backwards emits `libxslt` into an `apt-get install` line, where the correct name is
   `libxslt1.1` — it fails at build time, loudly, but it will cost an afternoon to find.
4. `classification/stagecraft_shadow.py` — spec §7.6. ~30 lines, and it is the G6
   contingency plus the per-run divergence metric. Check it against the verbatim rule
   transcribed in Phase 0.
5. Unit tests with **`responses`/`requests-mock`** — stub PyPI so tests are hermetic. Tests
   that hit live PyPI will break when a package publishes a new wheel, and will fail in CI.

**Exit gate — the most important in the project.**
- [ ] **THE CRITICAL TEST:** one `requirements.txt` with `flask` + `lxml` yields *different*
      native-dep results for the two, while the shadow classifier yields the *same* label
      for both. Without this the project has no demonstrated contribution.
- [ ] **G1 (resolver unit test):** `target_libc` forced to `musl` → `manylinux`-only
      packages do **not** match, fall to conservative. Must be a unit test — §9.1 means no
      end-to-end run produces a musl target, so this path rots if untested directly.
- [ ] **G1 (end-to-end, Alpine fixture):** `input_libc = musl` detected, both stages
      rebased to Debian, wheels resolved against `target_libc = glibc` → `manylinux`
      **matches**. The wheel check targets the *output* libc; asserting no-match here would
      be systematically over-conservative on every Alpine input.
- [ ] **G8:** `cp37-abi3` matches py3.11; `cp39-cp39` does not; `py3-none-any` matches all
      targets including `unknown` libc; `linux_x86_64` never matches
- [ ] **G7:** unpinned specifier records a concrete `resolved_version`; `--use-lock`
      reproduces byte-identical classifications **with the network disabled** (test this by
      actually disabling it, not by trusting the cache)
- [ ] `psycopg2` and `psycopg2-binary` in one manifest classify differently
- [ ] `unknown` libc takes the conservative path rather than assuming glibc
- [ ] Network failure surfaces as `unavailable` and a 404 as `not_on_pypi` — neither as a
      silent "no wheel"
- [ ] `conservative_fallback_rate` computed and reported
- [ ] KB lookups use the **output** distro family: an Alpine input produces Debian names
      (`libxslt1.1`), never Alpine ones (`libxslt`)

---

## Phase 5 — Scoring and synthesis (Modules 5 & 6)

**Objective.** The gate decision, and correct multi-stage Dockerfile output.

**Why here.** Needs Phase 4's classifications. Fixes G4.

**Tasks.**
1. `scoring/necessity_scorer.py` — spec §8, with sub-scores **normalised to 0-100** before
   weighting. Unnormalised, `S_bloat` (a package count) and `S_efficiency` (0-100) are on
   incomparable scales and equal weights are meaningless.
2. Calibrate the threshold on the **dev split only**, then freeze it. Produce a sensitivity
   table at three or four values.
3. `synthesis/dockerfile_synthesizer.py` with templates in `synthesis/templates/`, not
   embedded Python strings — templates are reviewable and diffable.
4. **The venv pattern, spec §9.2/§9.3.** Specifically: `python -m venv /opt/venv`;
   identical path both stages; `ENV PATH` in **both** stages; `COPY --chown` rather than a
   separate `RUN chown -R` (which duplicates a multi-GB venv into a new layer); assert
   builder and runtime agree on **both** Python major.minor **and libc**, failing loudly
   otherwise — a venv is portable only between images matching on both.
5. **The Alpine policy, §9.1** — never emit an Alpine base for **either** stage. Both
   stages rebase together: an Alpine input yields a Debian builder *and* a Debian `-slim`
   runtime. Rebasing only the runtime would leave a musl builder feeding a glibc runtime,
   which reintroduces G1 through the venv copy. Report the change loudly, naming both
   stages; `--keep-base-family` aborts rather than attempting musl synthesis.
6. Preserve `WORKDIR`/`ENV`/`EXPOSE`/`HEALTHCHECK`/`CMD`/`ENTRYPOINT` verbatim, including
   shell vs exec form.
7. `.dockerignore` per §9.5, with data/model exclusions conservative and reported.
8. Also implement the **naive multi-stage baseline** (spec §14.2 Tier 3 baseline (b)):
   move all pip installs to a builder, copy the venv, no per-package analysis. ~50 lines,
   and it isolates *your* contribution from the contribution of merely using multi-stage
   builds — a question a reviewer will certainly ask.

**Exit gate.**
- [ ] Syntactically valid Dockerfile for every fixture (`docker build --check` or `hadolint`)
- [ ] **G4:** output contains `COPY --from=builder /opt/venv /opt/venv` and **never**
      `COPY --from=builder /install /usr/local`
- [ ] `ENV PATH` present in **both** stages (grep the output; this is the most commonly
      missed step in the venv pattern and it fails silently by running the system Python)
- [ ] Alpine input → Debian builder **and** Debian `-slim` runtime, both reported in
      `base_image_changed`; no Alpine base in either stage; `--keep-base-family` aborts
      with an explanation
- [ ] Mismatched builder/runtime Python major.minor **or** libc fails loudly at synthesis
- [ ] `CMD`/`ENTRYPOINT` byte-identical to the original, form preserved
- [ ] Scorer gates correctly on one bloated and one minimal fixture; sub-scores normalised;
      threshold frozen and its value recorded
- [ ] Naive baseline synthesiser works

---

## Phase 6 — Validation and reporting (Modules 7 & 8)

**Objective.** Prove the images actually *work*, and emit the full report.

**Why here.** Needs Phase 5 output. **This phase fixes G2, and it is where a working image
is distinguished from a merely-building one.** Everything the evaluation reports depends on
this distinction being real: without the smoke test, the size-reduction numbers get
computed over broken images, and the tool scores *better* the more aggressively it breaks
them.

**Tasks.**
1. `validation/build_validator.py` — §10.1, including **building the original** for the
   baseline denominator. Cache baseline builds; they are expensive and static.
2. `validation/smoke_tester.py` — §10.2. All three tests:
   - **Test A, import check** — the load-bearing one. Explicit `--entrypoint`, absolute
     venv interpreter path, all packages in one container, report every failure. Separate
     `ImportError`/`ModuleNotFoundError` from unrelated import-time exceptions (a package
     needing a GPU or a config file is not a classification bug, and conflating them
     manufactures phantom KB gaps).
   - **Test B, entrypoint** — bounded wait, `RUNTIME_WARNING` not `FAILURE` unless a §10.3
     signature appears. Apps legitimately need databases and credentials; that is the
     corpus's environment, not your tool's error.
   - **Test C, console scripts** — the only test that catches broken shebangs and a missing
     runtime `ENV PATH`. Do not skip it; it is the direct G4 regression test.
3. `validation/failure_classifier.py` — the §10.3 table, tagging phase (build vs runtime).
4. `.so` → Debian package mapping (`dpkg -S` / `apt-file` in a scratch container) so gaps
   are actionable.
5. `artifacts/kb_gaps.jsonl` append-only log, §10.4 schema.
6. `reporting/report_generator.py` — the full §11 JSON plus the stdout summary.

**Exit gate.**
- [ ] Full pipeline runs end to end on **every** fixture — matching §16's v2 requirement,
      which replaced v1's unfalsifiable "majority of fixtures". A fixture that cannot be
      validated is either worth fixing or worth recording as a limitation
- [ ] Import smoke test runs on **every** successful build
- [ ] **Induced failure 1:** remove a needed runtime lib → build **passes**, Test A
      **fails**, categorised `MISSING_RUNTIME_SYSTEM_DEP` phase `runtime`. This is the exact
      case v1 was structurally blind to; if this test does not pass, G2 is not fixed.
- [ ] **Induced failure 2:** delete the runtime `ENV PATH` → Test C reports the console
      script unresolvable, categorised `MISSING_PACKAGE` phase `runtime`. (Deliberately
      **not** `BROKEN_SHEBANG` — a missing `PATH` gives `command not found`, not a bad
      interpreter. §10.3's `MISSING_PACKAGE` row names "`PATH` wrong" as a cause.)
- [ ] **Induced failure 3:** rewrite a console script's shebang to a non-existent
      interpreter → `BROKEN_SHEBANG` phase `runtime`, via Test C. Needs its own fixture;
      failure 2 does not reach this code path. This is the G4 regression test.
- [ ] **Induced failure 4:** omit a build dep for a source build → `MISSING_BUILD_SYSTEM_DEP`
      phase `build`
- [ ] Report validates against the §11 schema, including target triple, resolved versions,
      shadow classification, and separated build/smoke results
- [ ] Status follows §10's table exactly: `SUCCESS` = Phase 1 exit 0 + Test A + Test C;
      `RUNTIME_WARNING` = those pass but Test B fails; `FAILURE` = Phase 1, A, or C fails.
      Verify by construction, not by hoping.
- [ ] **Both** §14.3 rates emitted and distinguished: `working_image_rate` (build + Test A,
      the headline classification-correctness metric) and `full_success_rate` (build + A + C).
      A gap between them is a venv/`PATH` synthesis signal, so they must not be merged.

---

## Phase 7 — Corpus and evaluation

**Objective.** Run the stratified corpus and collect defensible numbers.

**Why here.** Needs the whole pipeline. Fixes G5, G6, G9.

**Tasks.**
1. **Pilot on five repositories first** — one per stratum plus two heavy-native. Measure
   wall-clock time, disk, and bandwidth per repository. Then decide the real corpus size
   from measured numbers. Two Docker builds per repo, ML images at multiple GB each; this
   is the phase most likely to overrun, and the pilot is what converts that risk into a
   known quantity.
2. Curate per spec §14.1: **≥60% stratum A**, ~25% B, ~15% C. Script the stratum
   assignment so it is mechanical and reproducible, not judged. Record filter counts at
   each step.
3. **Split dev (~20%) / eval (~80%) before any KB tuning**, and write the split into
   `corpus.yaml` with pinned commit SHAs.
4. Run the dev split. Harvest KB gaps. Patch the KB. Iterate **on the dev split only**.
5. **Freeze the KB.** Record its commit hash.
6. Run the eval split **once**. That is the headline number. Gaps found here are *reported
   as findings, not patched*.
7. Ground truth per §14.3: 15-20 repositories from the eval split, labels derived
   empirically via `ldd` + `dpkg -S` and `--no-binary :all:` source installs. Record the
   commands so the derivation is auditable. Report per-package **precision and recall**,
   separately for build and runtime deps, broken down by `confidence_source`.
8. Run the baselines per the Phase 0 tier decision — always including the naive
   multi-stage baseline from Phase 5.
9. Assemble the §14.4 reproducibility bundle.

**Exit gate.**
- [ ] Corpus meets the ≥60% stratum-A quota; `corpus.yaml` published with pinned SHAs
- [ ] Dev/eval split recorded **before** tuning, and demonstrably so (git history)
- [ ] KB frozen with a recorded commit hash before the eval run
- [ ] Eval split run **once** against the frozen KB
- [ ] Divergence count > 0 on stratum A and ≈ 0 on stratum C — the second half is what
      makes the first half credible
- [ ] Precision and recall reported separately for build and runtime deps
- [ ] Failure category distribution tabulated
- [ ] Reproducibility bundle complete

---

## Phase 8 — Write-up

**Objective.** Report it honestly.

**Tasks.**
1. Resolve every open question in spec §17.2 — none should still be open.
2. Trace or drop the §1.1 uncited claims (the multi-gigabyte bloat figure, both CVE IDs).
   Replace with your own corpus measurements where possible; that is a stronger claim
   because it is yours.
3. Write §15's limitations as stated scope decisions with rationales, not apologies.
4. Be explicit about the framing decisions that a reviewer would otherwise raise as
   objections:
   - The corpus is deliberately biased toward native-dependency-heavy projects, and the
     measured reduction does not generalise ecosystem-wide. Stratum C bounds the
     over-flagging cost.
   - The necessity-scoring gate is **inherited from StageCraft's design**, faithfully
     reimplemented, not a contribution of this work.
   - If the baseline is the shadow reimplementation, say so every time it appears.
   - Alpine/musl is detected and deliberately declined, with reasons.
5. Report the KB-freeze protocol as a methodological strength — it converts the coverage
   limitation into a quantified generalisation estimate.

**Exit gate.**
- [ ] No uncited quantitative claims
- [ ] Every §17.2 question answered
- [ ] All four framing decisions stated plainly in the text
- [ ] Reproducibility bundle referenced and complete

---

## Sequencing notes

**Parallelisable.** Phases 1, 2, 3 are mutually independent. Phase 2 (KB curation) is the
long pole and benefits most from an early start — begin it during Phase 0/1 rather than
waiting.

**Where the time actually goes.** Expect Phase 2 (manual curation), Phase 4 (the core
logic and its many edge cases), and Phase 7 (Docker builds are slow, ML images are large)
to dominate. Phases 0, 3, and 8 are comparatively quick.

**The three highest-risk items, and what to do about them.**
1. *StageCraft claim (c) is false* → Phase 0 catches it. Mitigation: verify before coding.
2. *Corpus infeasible at 50-80 repositories* → Phase 7's pilot catches it. Mitigation:
   reduce count, hold the stratum proportion; a smaller stratified corpus beats a larger
   unstratified one, and beats one that cannot finish.
3. *KB coverage too thin, high conservative-fallback rate* → visible from Phase 4 onward.
   Mitigation: this is a *reportable result*, not a failure — the metric exists to quantify
   it honestly. Do not respond by patching against the eval split.

**Demo-able milestones.** End of Phase 4 gives the flask-vs-lxml divergence — the
contribution, demonstrable in one command. End of Phase 6 gives a working refactored image
with measured size reduction. Those two are the natural checkpoints to show a supervisor.
