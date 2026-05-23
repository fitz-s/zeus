# WAVE 3 Critic — Authority Doc + Semantic Drift Refresh (3 commits)

**Scope**: commits `4e121fb47a` (REVIEW.md + INDEX.md), `7f3a99c9c4` (current_*.md), `7e7c58281a` (lore INDEX + 6 semantic drifts + new fatal_misread + wave-2 carryover)
**Branch**: `feat/doc-alignment-2026-05-16`
**Authority**: `PLAN.md §WAVE 3` + `SEMANTIC_AUDIT.md`
**Critic**: opus, fresh context, 2026-05-16

---

## VERDICT: REVISE

**Overall Assessment**: 9 of 10 probes PASS. The semantic drift fixes (topology authority_note, core_claims status spell-out, ensemble_snapshots_v2 rename, module maturity bumps, new fatal_misread) are factually accurate against the codebase. The lore INDEX.json is reproducibly correct. AGENTS.md scout-claim verified. Paris parse error pre-existing as disclosed. **One MAJOR finding blocks accept**: 52 new INDEX.md rows ship with widespread PR-mismatches (~70% defect rate on the 15-row sample) — the PR numbers were attributed without verification and many point to unrelated merged work. This is fixable with one bounded sweep but should not ship as-is. WAVE 3 also has one MINOR consistency gap: PLAN.md Step 3.7 §5 specified "fix count metadata 8 → 9" but the yaml has no count field, so the spec was non-actionable; only the new entry was added (semantically equivalent fulfillment).

---

## Pre-commitment Predictions vs Actuals

| # | Prediction | Outcome |
|---|---|---|
| 1 | AGENTS.md scout-claim verification | HIT — zero `.py:NNN`/`.yaml:NNN` refs confirmed; worker disclosure accurate |
| 2 | INDEX.md PR numbers may have date/scope mismatches | HIT (severe) — ~70% defect rate on PR-tag accuracy in 15-row sample |
| 3 | Module manifest "stable" promotions may overstate coverage | MISS — broader test pattern reveals 25-46 test files per module; bumps justified |
| 4 | Settlement note may include factual claims needing verification | PARTIAL HIT — `0x69c47...` address lives in YAML registries only, not code; harvester docstring confirms Gamma API mechanism |
| 5 | `ensemble_snapshots_v2` rename may break code references | MISS — `ingest_grib_to_snapshots.py` (named producer) writes v2; legacy `ensemble_snapshots` retained as `legacy_archived` per ownership.yaml |
| 6 | Lore INDEX.json missing card file existence | MISS — all 3 cards exist; regenerated INDEX byte-identical to committed |
| 7 | `expected_signature: e3b0c44...` looks like stub | HIT — it IS empty-string SHA256, but the stub-on-first-run pattern is documented and enforced by `lore_reverify.py:213,250` (intentional) |
| 8 | fatal_misreads count "8 → 9" non-actionable | HIT — yaml has no count metadata field; spec was based on inaccurate audit observation |
| 9 | Paris yaml parse error pre-existing | HIT — verified via `git show 7e7c58281a~1`: BEFORE COMMIT parse error matches AFTER |
| 10 | Settlement description AGENTS.md alignment | MISS — harvester.py line 1 docstring confirms Gamma API polling + settlement detection; AGENTS.md addition accurate |

---

## Probe Disposition Table

| Probe | Result | Evidence |
|---|---|---|
| P1: REVIEW.md Tier 0 paths exist | PASS | `maintenance_worker/core/{validator,apply_publisher}.py`, `scripts/topology_v_next/{admission_engine,hard_safety_kernel}.py`, `bindings/zeus/safety_overrides.yaml` — all 5 exist; semantically Tier 0 (forbidden-path enforcement + admission gate + safety kernel) |
| P2: INDEX.md new rows have packet dirs present | PASS (dir-existence) / **MAJOR FAIL (PR-tag accuracy)** | 10/10 sampled dirs present. 15/15 PR-tag spot check: 11 mismatches against actual PR titles (see Major #1) |
| P3: PR #119/120/121 + commit `a924766c8a` verifiable | PASS | `gh pr view` confirms all 3 merged 2026-05-16; commit ref is merge of PR #121 (now origin/main HEAD) |
| P4: topology.yaml authority_note semantically accurate | PASS | `scripts/topology_doctor.py:34` loads `TOPOLOGY_PATH = ROOT / "architecture" / "topology.yaml"`; consumed throughout topology_doctor; "active nav authority" claim verified |
| P5: core_claims.yaml `replaced` status clarification | PASS | Inline comment correctly distinguishes "v2 enforced, v1 fallback may coexist"; matches the `ensemble_snapshots` (legacy on world.db) / `_v2` (canonical on forecasts.db) coexistence pattern |
| P6: `ensemble_snapshots_v2` rename in data_rebuild_topology.yaml | PASS | `producer_script: scripts/ingest_grib_to_snapshots.py` writes to `ensemble_snapshots_v2` (verified via `grep "INSERT INTO" scripts/ingest_grib_to_snapshots.py`); rebuild operators correctly directed |
| P7: New fatal_misread schema validity | PASS | 8 fields match existing entries exactly (`id`, `severity`, `false_equivalence`, `correction`, `proof_files`, `invalidation_condition`, `tests`, `task_classes`); describes a real failure mode tied to `maintenance_worker/core/archival_check_0.py` |
| P8: K1 DB split addendum in current_data_state.md accurate | PASS | Cross-checked against `architecture/db_table_ownership.yaml:24-25`: world↔forecasts split + FORECAST_CLASS table list (observations, settlements, calibration_pairs_v2, ensemble_snapshots_v2, source_run, market_events_v2) all confirmed; cross-DB write path `get_forecasts_connection_with_world()` referenced correctly |
| P9: AGENTS.md harvester + internal_resolver_v1 settlement note | PASS | `src/execution/harvester.py:1-8` docstring confirms Gamma API polling for recently settled markets; `0x69c47...` address documented in `architecture/settlement_dual_source_truth_2026_05_07.yaml:26` and `architecture/fatal_misreads.yaml:202` (post-2026-02-21 UMA OO V2 → internal resolver transition) |
| P10: lore INDEX.json (3 cards, 1 topic) | PASS | Schema-valid; all 3 referenced lore files exist at `docs/lore/topology/`; **byte-identical to regenerated `python3 scripts/lore_indexer.py --output ...`** (the committed INDEX was authentically generated, not hand-written) |

---

## Critical Findings

None.

---

## Major Findings

### MAJOR-1: INDEX.md systematic PR-tag inaccuracy across ~52 new rows (commit `4e121fb47a`)

Confidence: HIGH.

**Evidence (15-row sample, GitHub PR titles via `gh pr view`):**

| INDEX entry | INDEX claim | Actual PR title | Verdict |
|---|---|---|---|
| `task_2026-05-08_262_london_f_to_c` | PR #89 — "London °F→°C settlement semantics fix" | PR #89 = "fix(state): selection_coverage CHECK constraint + migration" | **MISMATCH** |
| `task_2026-05-08_f1_subprocess_hardening` | PR #90 — "F1 subprocess hardening" | PR #90 = "[codex] Repair object authority invariance waves 24-26" | **MISMATCH** |
| `task_2026-05-08_low_recalibration_residue_pr` | PR #91 — "LOW recalibration residue" | PR #91 = "fix: bundled follow-up — PR #84-89 comment fixes + tiny-PR block hook" | **MISMATCH** |
| `task_2026-05-08_topology_redesign_completion` | PR #92 — "topology redesign completion" | PR #92 = "feat: Track A.3 fail-CI + PR #91 follow-up + #242/#243 tech debt" | **MISMATCH** |
| `task_2026-05-09_workflow_redesign_plan` | PR #94 — "PR discipline and workflow redesign" | PR #94 = "fix(data): extend ECMWF Opendata horizon to D+10 — closes #134" | **MISMATCH** |
| `task_2026-05-11_tigge_vm_to_zeus_db` | PR #106 — "TIGGE VM-to-Zeus DB wiring" | PR #106 = "fix(hooks): redesign pr_create_loc_accumulation — 300 LOC..." | **MISMATCH** |
| `task_2026-05-07_recalibration_after_low_high_alignment` | PR #82 — "LOW/HIGH recalibration recovery" | PR #82 = "feat(daemon): settlements revival + UMA + Gamma backfill + A1+3h ECDS migration" | **MISMATCH** |
| `task_2026-05-07_navigation_topology_v2` | PR #79 — "topology v2 navigation upgrade" | PR #79 = "topology: reject .omc/plans + .omc/research as plan/audit targets" | **MISMATCH** |
| `task_2026-05-08_alignment_safe_implementation` | PR #88 — "alignment repair implementation" | PR #88 = "feat(state): subprocess wrap with write_class env propagation (Track A.2)" | **MISMATCH** |
| `task_2026-05-06_calibration_quality_blockers` | PR #80 — "calibration quality launch-blocker" | PR #80 = "fix(calibration): make LOW runtime recovery repeatable" | RELATED but title-mismatch |
| `task_2026-05-05_topology_noise_repair` | PR #67 — "topology boot-profile and script-route noise repair" | PR #67 = "Object-meaning invariance audit and repair" | **MISMATCH** (topology vs object-invariance) |
| `task_2026-05-14_k1_followups` | PR #116 — "K1 followup seam repairs" | PR #116 = "fix(state): K1 followup — canonical schema-ownership registry + typed connections..." | PASS (related) |
| `task_2026-05-14_data_daemon_live_efficiency` | PR #115 — "data-daemon live-efficiency refactor" | PR #115 = "[codex] Enforce data-daemon forecast authority readiness" | PASS (semantically aligned) |
| `task_2026-05-15_p_drift_remediation` | PR #119 — "probability drift remediation" | PR #119 = "feat: runtime improvement engineering package (P1-P10 + Pdrift)" | PASS (P-series umbrella) |
| `task_2026-05-15_p1_topology_v_next_additive` | PR #119 — "topology v-next additive phase 1" | PR #119 = "feat: runtime improvement engineering package (P1-P10 + Pdrift)" | PASS (umbrella) |

**Pattern**: PR numbers appear to have been assigned mechanically by date-proximity heuristic rather than verified against actual PR content. 11 of 15 (~70%) mismatches. The wave-series umbrella references (`PR #67` for object-invariance waves, `PR #119` for P-series) appear correct as informal anchoring, but cross-date single-task attributions are systematically wrong.

**Why this matters**: INDEX.md is a navigation surface for future agents. When an agent traces a packet to its anchor PR for context (e.g., "what did the london_f_to_c packet actually merge as?"), they will find unrelated work in the cited PR and lose trust in the index. This is the "data provenance" failure mode codified in Fitz Methodology #4: correct dir names + wrong audit metadata = silent disaster for downstream interpretation.

**Why MAJOR, not CRITICAL** (realist check):
- No runtime code consumes INDEX.md PR numbers (`grep -rn "INDEX.md" scripts/` returns only `authority_inventory_v2.py` and `archive_migration` references that read the file but do not parse PR tags)
- Dir names are self-documenting; agents notice mismatch on first lookup and self-correct via `gh pr view`
- Mitigated by: documentation-only surface, no runtime contract dependency, fast operator-detectable
- BUT: 52 entries × ~70% wrong = ~36 false claims shipping in the same PR that asserts "Last reviewed: 2026-05-16". This is exactly the audit-rot pattern the doc-alignment work is meant to prevent.

**Fix (≤30 LOC equivalent script run, 1 hr work)**:
```bash
# For each INDEX entry, query packet dir's git log for actual merge PR
for dir in docs/operations/task_2026-05-0[5-9]*/ docs/operations/task_2026-05-1[0-6]*/; do
  base=$(basename "$dir")
  # Find first commit touching this dir
  first_commit=$(git log --diff-filter=A --format=%H -- "$dir" | tail -1)
  # Find merge commit containing that commit
  merge=$(git log --ancestry-path --merges --format=%H "${first_commit}..origin/main" | tail -1)
  # Extract PR number from merge commit message
  pr=$(git log -1 --format=%s "$merge" | grep -oE "#[0-9]+" | head -1)
  echo "$base | actual_pr=$pr"
done
```
Then hand-correct the ~36 mismatched rows. Acceptable alternative: replace PR column with `Anchor commit/branch` showing commit SHA range (no claim of PR identity).

---

## Minor Findings

### MINOR-1: PLAN.md Step 3.7 specifies non-existent metadata field

PLAN.md §WAVE 3 step 3.7 line 181 states: `fatal_misreads.yaml: fix count metadata 8 → 9`. The yaml has no count/entries field — only the array of misreads. Worker correctly added the 9th entry (`artifact_authority_status_missing_gate`), achieving the spirit of the requirement. The spec line was based on an inaccurate SEMANTIC_AUDIT row (item #10 says "Counts 8 entries... count is stale" but there is no count to update). Non-actionable as written; semantically fulfilled by adding the entry.

Fix: Annotate PLAN.md §7 or in WAVE 7 deferral: "fatal_misreads.yaml had no count metadata field; spec item #10 in SEMANTIC_AUDIT.md is a description error."

### MINOR-2: `expected_signature` stub-population pattern not visible from card content alone

3 lore card files received `expected_signature: e3b0c442...` (SHA-256 of empty string). This is the documented intentional behavior per `lore_reverify.py:243-251` ("no prior expected_signature; signature recorded" on first run). However, an agent reading the lore card without consulting `lore_reverify.py` source might interpret the empty-hash as a known-bad signature or stale stub. The change is silently correct but not self-documenting.

Fix (3 lines): Add a comment to one of the lore cards stating "expected_signature is auto-populated by lore_reverify; e3b0c44... = empty-string SHA256 = 'recorded on first run'", or include this in `docs/operations/task_2026-05-15_runtime_improvement_engineering_package/04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md` if not already there.

### MINOR-3: pre-existing paris yaml parse error should be tracked

Pre-existing parse error at `architecture/paris_station_resolution_2026-05-01.yaml:143-144` is real and pre-WAVE 3. Worker disclosure accurate. Not introduced by this work, but: the file is loaded by no current `scripts/topology_doctor.py --parse` run (else would have been caught earlier). Implies the file is currently orphaned from CI parse validation. Either it should be added to `topology_doctor.py` parse list (broken-then) OR the file content moved to a non-yaml format if it's reference-only.

**Carry vs block**: This should **NOT block WAVE 3** (pre-existing, not in scope). It SHOULD be carried to **WAVE 7 deferrals** as a separate cleanup item.

### MINOR-4: AGENTS.md harvester note uses authority anchor `architecture/settlement_dual_source_truth_2026_05_07.yaml` without verifying the doc was named the source of truth

Worker correctly cites the yaml as authority. The yaml IS present and contains the address. But the harvester.py docstring + the registry are TWO sources; if they ever drift, AGENTS.md will cite the yaml while operators may trust the code docstring. Cosmetic.

Fix: Add `# Source-of-truth precedence: src/execution/harvester.py code > registry yaml > AGENTS.md prose` to settlement_dual_source_truth yaml header. ~3 LOC.

---

## What's Missing

- **PR-tag verification step in WAVE 3 spec**: PLAN.md §3.3 specified `regenerate or hand-add 15+ missing task packets` but did not require PR-tag verification. WAVE 3 critic recommends adding to PLAN.md as a gate: "every PR tag in INDEX.md MUST be verified against actual PR title via `gh pr view N` before commit".

- **fatal_misreads loadability test**: PLAN.md §3.9 says "fatal_misread loads" but the executed verification was only `python3 -c "import yaml; yaml.safe_load(...)"`. The new entry has `tests: [python3 scripts/topology_doctor.py --fatal-misreads --json]` but no commit log shows that test was actually run post-add.

- **lore_reverify run on the 3 NEW expected_signature fields**: Worker disclosure says "lore_reverify 3/3 cards OK" but if the signature was just populated as empty-string-hash, a re-run will overwrite with real content hashes. Either the disclosure means "lore_reverify ran AND populated signatures" (in which case the e3b0c44... bytes are wrong) OR "lore_reverify ran but card-with-empty-sig is treated as first-run-OK" (the documented behavior). Ambiguity worth noting.

- **REVIEW.md Tier 0 entries lack the `Truth-owning` distinction**: The 3 new entries are added as bullets, but REVIEW.md Tier 0 in the upstream section distinguishes "truth-owning" surfaces from "convenience" surfaces. The new bullets don't carry that distinction. Minor — REVIEW.md is consumed by humans, and the descriptive text "fail-closed safety contracts" / "admission gate" / "forbidden-rule set at runtime" carries the semantic weight.

---

## Ambiguity Risks

- **"K1 split addendum supersedes points 1-3"** in current_data_state.md: Points 1-3 still exist below the addendum with their own SUPERSEDED tag. A future reader could interpret either (a) the addendum replaces them (read addendum, ignore points 1-3) or (b) the addendum extends them (read both). Currently both interpretations yield same K1 outcome, but if K2 split arrives the layering becomes confusing.
  - Risk if wrong interpretation chosen: agent doing schema-routing lookup may use stale point-1-3 guidance, hitting wrong DB.
  - Mitigation: structurally redact points 1-3 to a single `Pre-K1 historical` block in next pass; out of scope for WAVE 3.

- **`replaced` claim_status spell-out**: The new comment says `v2 enforced, v1 fallback may coexist but is not the primary path`. "Not the primary path" is interpretable as either "must not be used" or "may be used as fallback". Code-side ambiguity: in `src/engine/evaluator.py:4185` evaluator REFUSES to fall back to legacy `ensemble_snapshots` (`"refusing to fall back to legacy ensemble_snapshots authority"`). The spell-out comment is softer than code reality.
  - Risk: agent reading core_claims.yaml may write code that lawfully falls back to v1, then runtime refuses.
  - Fix (≤5 LOC): tighten comment to `v2 enforced, v1 fallback exists structurally but runtime refuses fallback (see src/engine/evaluator.py:4185)`.

---

## Multi-Perspective Notes

**Executor**: A future agent given a packet dir from INDEX.md will read the PR tag, run `gh pr view N`, get unrelated work, then have to grep git log themselves to find the actual PR. ~10-30 min of wasted lookup per packet. The dir names are self-documenting enough that this is recoverable but annoying. Stakeholder cost is per-lookup, not per-row, so the blast radius scales with how often INDEX is consulted (estimate: daily by active maintainer).

**Stakeholder**: The 大扫除 goal of WAVE 3 was "authority doc + semantic drift refresh". The semantic drifts (6 of them) were fixed accurately and verifiably. The INDEX expansion was a secondary goal that was executed with wrong audit metadata. Stakeholder gets ~80% of WAVE 3's value (the semantic drift fixes are genuinely correct), but ships shipping known-wrong metadata in the same PR that claims `Last reviewed: 2026-05-16`. Reputational cost: if any future critic sees `PR #82` cited for "LOW/HIGH recalibration" and then opens PR #82 (a settlements + UMA + Gamma backfill PR), trust in the index erodes.

**Skeptic**: The argument FOR shipping as-is: PR-tag is informational metadata, no runtime consumes it, future agents will self-correct on first lookup. The argument AGAINST: the entire purpose of doc alignment is to make future audits cheap. Shipping ~36 false claims in the same artifact makes the next audit cycle more expensive, not less. The skeptic's preferred remediation: either fix the PR tags now (1 hr scripted sweep), OR replace the PR column with a commit SHA range (mechanical, accurate, less semantically rich but un-falsifiable).

---

## Verdict Justification

**REVISE** — not REJECT (the work is 80% solid), not ACCEPT-WITH-RESERVATIONS (the MAJOR finding affects 36+ rows in the same PR and is mechanically fixable in <1 hr).

What changes the verdict to ACCEPT:
1. **Fix INDEX.md PR-tag inaccuracies** for the ~36 mis-attributed rows, either by: (a) replacing PR# tags with verified commit SHAs / merge-commit SHAs via the script sketched in MAJOR-1 fix, OR (b) hand-correcting via `gh pr view` for each cross-date single-task entry.
2. **Optional**: address Minor #1-#4 in the same commit or defer explicitly to WAVE 7.

**Realist Check applied**: MAJOR-1 was initially considered for CRITICAL classification (52 rows × 70% defect = 36 false claims). Downgraded to MAJOR because:
- Mitigated by: no runtime contract consumes INDEX.md PR tags
- Mitigated by: dir names are semantically authoritative; PR tag is informational annotation
- Mitigated by: detection time = first lookup attempt (immediate)
- BUT severity remains MAJOR (not MINOR) because: 36 rows is a high-volume defect, ships in the same artifact that claims "Last reviewed", and undermines the doc-alignment work's core thesis of "authoritative metadata"

**Mode**: Review stayed in THOROUGH mode throughout. One MAJOR finding alone did not trigger ADVERSARIAL escalation; no patterns of systemic issues beyond the single defect class.

**Pre-existing paris yaml parse error**: NOT a WAVE 3 blocker. Worker disclosure accurate. Carry to **WAVE 7 deferrals** as a separate cleanup item — file should either be added to topology_doctor parse-validation surface or refactored to non-yaml if reference-only.

---

## Open Questions (unscored)

- Should INDEX.md replace the `Anchor PR` column entirely with a `Merged at` commit SHA + date? PR numbers age poorly when squash-merges or rewrites happen; commit SHAs are immutable.
- Are the `PR #67` umbrella attributions (8 entries for object-invariance waves) considered "correct" because PR #67 IS the umbrella work? My probe treated them as PASS, but this is a stylistic choice that should be documented.
- Should `expected_signature` populated to `e3b0c44...` be visually flagged in the lore card frontmatter (e.g., comment `# auto-populated on next lore_reverify run`)? Currently looks like a hash that someone computed and committed deliberately.

---

## Ralplan summary row

Not applicable — WAVE 3 is execution, not ralplan deliberation.
