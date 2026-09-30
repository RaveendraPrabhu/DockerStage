# Phase 0 — Foundation verification

**Date:** 2026-09-04
**Verifier:** automated read of the source PDF (`3797098.pdf`, 22 pp.), cross-checked
against `PROJECT_SPEC.md` §1.3.
**Paper:** Dongjin Chen, Wenhua Yang, Minxue Pan, Yu Zhou, "Automating Dockerfile
Refactoring to Multi-stage Builds," *Proc. ACM Softw. Eng.* 3, FSE, Article FSE070,
July 2026. Metadata confirms title, authors, venue, and article number as cited.

**Bottom line:** The project's framing survives. All three load-bearing claims (a), (b),
(c) are confirmed against the paper. One framing error was found and corrected — it
*overstated* the novelty of the system-package handling, not the pip handling, so the
core contribution is unaffected (arguably sharpened). Network-dependent items (artifact
reachability) remain open and are flagged for the user.

---

## Exit-gate checklist

- [x] **Quote verified verbatim** — §1.3's block quote is verbatim, in the discussion
      section (§4.4 Discussions, p. FSE070:18). No spec rewrite needed.
- [x] **Inheritance rule confirmed and transcribed** — it is in §3.2.2, named
      "inheritance rule," and works exactly as §1.3 characterises. Transcribed verbatim
      to `docs/stagecraft_rule_verbatim.md`.
- [~] **Artifact availability determined for both baselines** — *partially.* The paper
      gives a data-availability URL (below), but it cannot be reached from this
      environment (no network). Reachability/installability/runnability is **open** and
      must be checked by the user. Evidence and the exact URL are recorded below.
- [x] **Evaluation tier chosen and recorded** — provisional **Tier 2** (faithful shadow
      reimplementation), upgradeable to **Tier 1** if the artifact is confirmed runnable.
      Rationale below. The shadow classifier is fully unblocked either way, because the
      rule is now transcribed verbatim.

---

## Claim (a) — the quote is verbatim and in the discussion section ✅

**Spec §1.3 quotes:**

> "we deliberately restrict the input scope to the Dockerfile rather than the full
> project context to avoid the complexity of parsing heterogeneous build systems... we
> acknowledge that taking the whole project context, including source code, build
> scripts, and manifests, as input could further improve the quality of multi-stage
> builds, and we consider the exploration of such hybrid analysis strategies a promising
> direction for future research."

**Paper §4.4 Discussions (p. FSE070:18) reads:**

> "Consistent with this lightweight philosophy, we deliberately restrict the input scope
> to the Dockerfile rather than the full project context to avoid the complexity of
> parsing heterogeneous build systems. Nonetheless, we acknowledge that taking the whole
> project context, including source code, build scripts, and manifests, as input could
> further improve the quality of multi-stage builds, and we consider the exploration of
> such hybrid analysis strategies a promising direction for future research."

**Verdict.** Verbatim. The spec's `...` elides exactly one word of connective tissue
(". Nonetheless,") and changes nothing else. It is correctly attributed to the discussion
section. **Safe to submit as quoted.** For the citation, the section is titled "4.4
Discussions"; page is FSE070:18.

---

## Claim (b) — the inheritance rule is in §3.2.2 and works as characterised ✅

**Paper §3.2.2 (p. FSE070:6), verbatim:**

> "For application-level dependencies, StageCraft uses an inheritance rule. Because these
> packages are introduced by a language-specific package manager, their label follows how
> the package manager itself is used. If the package manager is present at runtime, all
> packages it installs are treated as runtime. If the package manager is only needed
> during the build, its packages are labeled build-time."

**Verdict.** Exact match to §1.3: the name ("inheritance rule"), the section (§3.2.2),
and the mechanism (all-or-nothing at the package-manager level, no per-package
inspection). Confirmed.

---

## Claim (c) — StageCraft never reads the manifest ✅ (with one precision to state)

Two independent parts of the paper confirm it:

1. **§4.4** explicitly lists "manifests" among the project context StageCraft
   *deliberately does not take as input* ("we deliberately restrict the input scope to
   the Dockerfile ... including source code, build scripts, and manifests ... a promising
   direction for future research").
2. **§3.2.2** classifies pip packages by *how the package manager is used*, never by
   reading requirements.txt.

**The one precision that must go in the write-up** (so a reviewer can't nitpick it):
§3.2.1 (language identification) lists "configuration files (`COPY requirements.txt`)"
as a *low-weight language signal*. StageCraft scans the **Dockerfile line by line** and
reacts to the **string `requirements.txt` appearing in a COPY instruction** — it does
**not** open the file or enumerate its packages. So "never reads the manifest" is true in
the sense that matters (contents are never parsed). The precise phrasing for §1.3/§8:
*StageCraft treats the filename `requirements.txt` as a lexical cue in the Dockerfile for
language detection, but never parses the manifest's contents, and therefore cannot
distinguish `flask` from `lxml`.* Claim (c) holds; the contribution is intact.

---

## Framing error found and corrected (this is what Phase 0 is for)

The error was in the project's **favor** and would have been caught by any reviewer who
read §3.2.2, so it had to be fixed now.

**What the spec claimed (old §7.4):** "This step-by-step, per-item reconciliation — as
opposed to one blanket label for the whole apt-get block — is the direct, demonstrable
difference from StageCraft's manifest-wide inheritance rule."

**What the paper actually says (§3.2.2):** StageCraft applies the blanket inheritance
rule *only to application-level (pip/npm) dependencies.* For **system-level (apt)**
dependencies it already does **per-package** classification:
1. match against a curated build-tool list (gcc, make, git) → build-time;
2. else if a core runtime component of the language (python3) → runtime;
3. else conservative build-time default;
4. **promote to runtime if the package appears in CMD / ENTRYPOINT / HEALTHCHECK.**

Our spec's §7.4 reconciliation is *the same algorithm* — including the identical
CMD/ENTRYPOINT/HEALTHCHECK promotion (spec §7.4 step 4) and the curated build-tool list
(step 3). So per-item apt reconciliation is **parity with StageCraft, not a
contribution.**

**Consequence — the contribution must be stated at the pip layer only:** the demonstrable
difference is that StageCraft's inheritance rule labels *all pip packages identically*,
whereas this tool parses the manifest and classifies *each pip package individually*
against a KB + live wheel check. The flask-vs-lxml divergence test is valid **because
both are pip (application-level) packages** — exactly where the inheritance rule is
blunt. This does not weaken the contribution; it locates it precisely and makes it
defensible.

**Fixes applied to `PROJECT_SPEC.md`:** §7.4's closing boast rewritten to describe apt
reconciliation as faithful parity with StageCraft's system-level classifier (with the
citation), and to relocate the "demonstrable difference" claim to the application/pip
layer. §1.3's "verify before submission" callout replaced with a "verified" note
pointing here. (§1.4 and §7.6 were already correct — they attribute the difference to the
"manager-level rule" over pip, which is accurate.)

---

## Corollary findings (useful, not blockers)

- **§9.1 Alpine policy has precedent in StageCraft.** §3.4.1: for interpreted languages
  like Python, StageCraft "chooses an official slim variant"; §4.4: it "avoids aggressive
  OS swaps, ensuring that standard system libraries required by the language runtime
  remain available." Our decision to rebase to Debian `-slim` and never Alpine can cite
  StageCraft as precedent rather than defending it from scratch.
- **CUDA signal is shared.** §3.4.1 selects a CUDA base image on an `AI_ML_CUDA`
  architectural signal — matches spec §9.1's CUDA-signal base selection.
- **Necessity scoring is confirmed inherited.** §3.1/§3.3 use the same three dimensions
  (image bloat, structural inefficiency, security risk) as spec §8. This confirms the
  §17/ROADMAP note that the scoring gate is inherited from StageCraft, not a contribution
  of this work — state it that way in Phase 8.
- **StageCraft's own reported gains** (§4.4): 52.2% image-size reduction, 49.0%
  cached-build-time reduction, 50.0% high-risk-vulnerability reduction. These are
  StageCraft's numbers, not general facts — usable as *related-work* context, but they do
  **not** substitute for the spec §1.1 uncited claims, which still need our own corpus
  measurements (ROADMAP Phase 8).

---

## Baseline artifact availability

**StageCraft.** §8 Data Availability: *"We release the tool, curated knowledge assets,
and the full evaluation dataset at https://anonymous.4open.science/r/Supplemental-materials-EE15
to support reproducibility and future research."*

- **Note the risk:** this is an **anonymous.4open.science** URL — a double-blind review
  mirror — even though the published paper names its authors. Such links can expire or be
  withdrawn after acceptance. **Untestable here (no network).**
- **Action for the user (needs network):** open the URL; record reachable? installable?
  runnable on one fixture? what input it wants, what output it emits. Also look for a
  de-anonymized permanent home (GitHub/Zenodo) linked from the camera-ready or the
  authors' pages. Update the "artifact availability" gate line when done.

**PARFUM.** Refs [5], [6]: Durieux, "Parfum: Detection and Automatic Repair of Dockerfile
Smells," arXiv:2302.01707, plus the ICSE'24 image-size study. A real, published, publicly
available tool. **But** StageCraft's own §4.1.2 states PARFUM "is not designed for
multi-stage refactoring" and used it only as a partial smell-repair baseline. Per ROADMAP
Phase 0 task 5, PARFUM is likely **not commensurable** with our multi-stage,
package-level metric — a defensible "dropped, here's why" outcome. Keep it only if a
smell-count or size comparison is genuinely informative.

**GPT-4.** StageCraft's third baseline (§4.1.2): `gpt-4.1`, `temperature=0.2`,
`top_p=1.0`, three repair rounds on build failure. Reproducible in principle if we want an
LLM baseline, but out of scope unless explicitly added.

---

## Evaluation-tier decision (spec §14.2)

**Provisional: Tier 2 — faithful shadow reimplementation of the inheritance rule.**

- Tier 1 (run the real StageCraft) is **preferred** and remains open: pursue it the moment
  the artifact URL is confirmed reachable and runnable. If so, upgrade and record the
  commit/version used.
- Tier 2 is safe to build against **now**, because §3.2.2's rule is transcribed verbatim
  (`docs/stagecraft_rule_verbatim.md`) and is ~30 lines. This de-risks the results chapter
  regardless of the artifact.
- Tier 3 (naive multi-stage baseline) is built anyway in Phase 5 — it isolates *our*
  contribution from the value of merely adopting multi-stage builds.

**Why this satisfies the gate.** The only remaining unknown (artifact reachability) does
not block any coding: Phase 1–6 depend on our own pipeline and the verbatim rule, not on
running StageCraft. So Phase 1 may proceed. Finalising Tier 1-vs-2 is a Phase 7 decision
and needs only a network check, recorded above as an open action.

---

## Open actions carried out of Phase 0

1. **[user, needs network]** Test `https://anonymous.4open.science/r/Supplemental-materials-EE15`;
   record reachability/installability/runnability; find any permanent de-anonymized
   mirror. Decide Tier 1 vs Tier 2 on the evidence.
2. **[Phase 8]** The §1.1 bloat figure and both CVE IDs are still uncited; StageCraft's
   52.2%/49.0%/50.0% are *its* results, not general facts. Replace with our own corpus
   measurements where possible.
