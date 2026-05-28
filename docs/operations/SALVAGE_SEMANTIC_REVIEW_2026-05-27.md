# Salvage Semantic Review — 2026-05-27

**Reviewer:** Executor agent af1e8a07625973d48 (sonnet)
**Main anchor:** `7395ba735d` (ghost-table cherry-pick on top of `b360211d99`)
**Salvage commit:** `cc520b926d` — 366 patches from 36 source branches
**Review date:** 2026-05-27

---

## Executive Summary

| Source Branch | Classification | Commits | Live-money Impact | Notes |
|---|---|---|---|---|
| `feat/ft-ship-64` (patches 46-49) | **LIVE_REGRESSION_BLOCKER** | 4 | Y | Chain-terminal P0 fixes — NOT on main |
| `feat/ft-64-live-wiring` (patches 54-55) | **APPLY** | 2 | Y | Calibration UNIQUE-collision fix for FT rebuild |
| `feat/onboard-jinan-zhengzhou` | **APPLY** | 1 | Y | Jinan+Zhengzhou NOT in cities.json on main |
| `feat/onboard-jinan-zhengzhou-v2` | **APPLY** | 2 | Y | Same cities, v2 includes INSERT field fix |
| `claude/agent-a3e2db11718201192` | DUPLICATE | 8 | N | LIVE-PROB-P0 gate fully on main in `src/signal/probability_sanity.py` |
| `fix/market-discovery-full-city-coverage` | DUPLICATE | 6 | N | clob_crosscheck=False + budget gate on main in `market_scanner.py` |
| `claude/exec-freshness` | DUPLICATE | 5 | N | `_reprice_recapture_fresh_snapshot` + `_propagate_recaptured_snapshot_fields` on main |
| `fix/platt-preflight-plural-data-version` | DUPLICATE | 1 | N | `allowed_data_versions` tuple field on main in `metric_specs.py` |
| `fix/prob-gate-apply-list-doc-20260524` | DUPLICATE | 3 | N | `apply_to_metrics` / `apply_to_strategies` enforcement on main |
| `claude/review5-24-fixes` | DUPLICATE | 4 | N | MC seed + cohort-scope fixes on main (`ensemble_signal.py`, `market_analysis.py`) |
| `fix/ddd-config-qingdao-nstar` | DUPLICATE | 1 | N | Qingdao in `v2_city_floors.json` + `v2_nstar.json` on main (N_star=110/113 vs 113/113 — close enough, intent fulfilled) |
| `claude/agent-a1820f998a1667eb4` | DUPLICATE | 3 | N | FT rebuild + Qingdao SEV-1 — same content on main via #342 |
| `claude/agent-a1fa36126aec54f32` | DUPLICATE | 1 | N | Sentinel gate `data_version=all` — on main |
| `claude/agent-a4b75ff7d05ff7bf4` | DUPLICATE | 9 | N | FT calibration scaffold (metric-aware cycle, ship-readiness antibody) — on main |
| `claude/agent-a597b866de666f259` | DUPLICATE | 2 | N | Matched-date eval tools — on main |
| `claude/agent-a5e789b38e7bac861` | DUPLICATE | 1 | N | Ship-readiness antibody — on main |
| `feat/ens-bias-canonical-schema-producer` | DUPLICATE | 10 | N | `ens_bias_repo.py`, `ens_error_model.py` on main |
| `claude/ens-bias-hierarchical` | DUPLICATE | 6 | N | ENS hierarchical bias estimator — on main via #334 |
| `claude/ens-predictive-error` | DUPLICATE | 5 | N | `fit_predictive_error_bucket`, `fit_city_predictive_error` — on main via #336 |
| `claude/data-temporal-pr1` | DUPLICATE | 23 | N | `source_time_frontier`, `TemporalPolicy`, frontier store — on main via #329 |
| `claude/chain-local-refactor` | DUPLICATE | 13 | N | `ChainOnlyFact`, `VenueVisibilityStatus`, `ChainSnapshotCompleteness` — on main via #347+#352 |
| `claude/chain-local-refactor-d0` | DUPLICATE | 5 | N | Part-2 audit cleanup — on main via #352 |
| `claude/market-cost-seam-2026-05-27` | SUPERSEDED | 12 | N | PR #348 merged with same intent (BinEdge.entry_price, σ_market, unified budget) |
| `feat/ci-topology-phase-e` | SUPERSEDED | 3 | N | PR #346 merged Phase E+G; patches are pre-merge incremental steps |
| `feat/ft-64-live-wiring` (patches 1-53, 56-58) | SUPERSEDED | 55 | N | Core content landed via PR #342+#349; patches 54-55 are the exception (APPLY above) |
| `feat/ft-ship-64` (patches 1-45) | SUPERSEDED | 45 | N | Landed via PR #349 squash; patches 46-49 are the exception (LIVE_REGRESSION_BLOCKER above) |
| `fix/onboarding-pipeline-completion` | SUPERSEDED | 10 | N | Early FT calibration steps landed via #342; branch was a stepping-stone |
| `backup/pr333-before-rebase` | INSIGHT_ONLY | 1 | N | `fix(live): close PR-map release blockers` — pre-rebase snapshot, content on main |
| `claude/agent-aaa3a03be64e2e0f3` | SUPERSEDED | 7 | N | Chain-local refactor Part 2 worker commits — final form on main via #352 |
| `draft/ens-refinement-research-2026-05-25` | INSIGHT_ONLY | 10+1 | N | All `docs(ens)` / `docs(research)` — ship-mechanics specs, ROI reports, HK provenance probe; no runtime changes |
| `docs/authority-system-pointers` | INSIGHT_ONLY | 2 | N | Docs-only: pipeline currency + PR #338 Copilot doc findings |
| `save/topology-h7-20260524` | INSIGHT_ONLY | 1 | N | H7 topology enforcement rule — policy already in `docs/operations/AGENTS.md` |
| `pr-332` | UNKNOWN | 28 | N | EDLI no-submit certificate system — entirely absent from main; `codex/edli-v1-no-submit-complete` worktree preserved; operator decision needed on whether to merge |
| `codex/edli-live-order-aggregate-substrate` | UNKNOWN | 40 | N | EDLI live-order aggregate substrate extension — extends pr-332; operator decision needed |
| `codex/edli-v1-implementation` | UNKNOWN | 6 | N | EDLI v1 scaffolding — precursor to pr-332; likely superseded by pr-332 |
| `redemption/edli-proof-kernel` | UNKNOWN | 2 | N | EDLI redemption proof contracts — isolated kernel test; operator decision |
| `redemption/edli-proof-kernel-on-pr328-scaffold` | UNKNOWN | 6 | N | EDLI kernel on PR #328 scaffold — precursor; likely superseded by pr-332 |

---

## LIVE_REGRESSION_BLOCKER Items (operator action required)

### BLOCKER-1: Chain-terminal position_drift suppression — MISSING from main

**Source:** `feat/ft-ship-64` patch `0046-fix-reconcile-suppress-position_drift-findings-for-chain-terminal.patch`
**File:** `src/execution/exchange_reconcile.py`
**Symptom without fix:** Every settled/voided token where the Polymarket data-api auto-redeem indexer lags (hours-to-days) generates recurring `position_drift` findings. The reconciler records drift → data-api shows 0 → resolver clears → data-api shows residual again → new drift recorded. Thrash loop consumes reconcile quota, pollutes logs, may stall live cycle.

**What the patch adds:**
- `_CHAIN_TERMINAL_POSITION_PHASES = frozenset({"settled", "voided", "admin_closed"})`
- `_chain_settled_tokens(conn)` — reads `position_current.phase` for terminal tokens
- In the reconcile loop: if token ∈ chain_terminal_tokens → call `_resolve_open_position_drift_findings` and skip drift recording

**Cherry-pick command (operator to run after review):**
```bash
git cherry-pick 9c18e39aec^..cd2c9bbb9b  # 4 commits: patches 46-49 from feat/ft-ship-64
```
(Note: these are the last 4 commits on the now-deleted `feat/ft-ship-64` branch; SHAs preserved in salvage patches.)

**Patches in salvage:**
- `salvage-patches/feat_ft-ship-64/0046-fix-reconcile-suppress-position_drift-findings-for-c.patch`
- `salvage-patches/feat_ft-ship-64/0047-fix-state-chain-terminal-markets-exclude-chain_only_.patch`
- `salvage-patches/feat_ft-ship-64/0048-fix-state-extend-chain-terminal-phase-filter-to-matc.patch`
- `salvage-patches/feat_ft-ship-64/0049-fix-state-chain-terminal-chain_only_quarantined-toke.patch`

---

### BLOCKER-2: `query_chain_only_quarantine_rows` excludes terminal markets — MISSING from main

**Source:** `feat/ft-ship-64` patch `0047` + `0048`
**File:** `src/state/db.py`, function `query_chain_only_quarantine_rows` (line 9185)

**Symptom without fix:** `query_chain_only_quarantine_rows` returns chain_only_quarantined rows even when the parent market is in a terminal phase (settled/voided/admin_closed/economically_closed/quarantined). These rows hydrate the runtime cache, causing load-portfolio to keep seeing "quarantined positions" for markets that no longer exist live. Portfolio `_has_quarantined_positions` stays armed → entry gate stays blocked → zero live entries.

**Main's current state:**
```python
# src/state/db.py line 9194-9201
rows = conn.execute(
    """SELECT token_id ... FROM token_suppression
       WHERE suppression_reason = 'chain_only_quarantined'
       ORDER BY created_at ASC, token_id ASC"""
).fetchall()
```
No terminal-phase filter. The patch adds a `NOT EXISTS (SELECT 1 FROM position_current pc WHERE pc.phase IN ('settled','voided','admin_closed','economically_closed','quarantined'))` guard.

**Same cherry-pick command as BLOCKER-1** — patches 47+48 are part of the same 4-commit set.

---

### BLOCKER-3: `query_token_suppression_tokens` never includes chain_only_quarantined in ignored_tokens — MISSING from main

**Source:** `feat/ft-ship-64` patch `0049`
**File:** `src/state/db.py`, function `query_token_suppression_tokens` (line 9163)

**Symptom without fix:** The function that builds `portfolio.ignored_tokens` only queries `RESOLVED_TOKEN_SUPPRESSION_REASONS = ("operator_quarantine_clear", "settled_position")`. `chain_only_quarantined` tokens whose parent market has reached terminal phase NEVER enter `ignored_tokens`. Result: `reconcile_with_chain` Rule 3 re-quarantines these tokens every cycle from the chain API response, regenerating quarantine positions and re-arming `_has_quarantined_positions`. Zero entries, permanent loop.

**Same cherry-pick command as BLOCKER-1** — patch 49 is part of the 4-commit set.

---

## APPLY Items (proposed cherry-picks, operator must confirm)

### APPLY-1: Calibration UNIQUE-collision fix for FT rebuild — MISSING from main

**Source:** `feat/ft-64-live-wiring` patches `0054` + `0055`
**Files:** `src/calibration/` (rebuild path)

**What it does:** During a non-`'none'` rebuild (e.g., `full_transport_v1`), a snapshot whose error-model bucket is fail-open has `applied_family` downgraded to `'none'`. Without this fix, writing that snapshot as `error_model_family='none'` collides with the pre-existing `'none'` baseline row on the 8-column UNIQUE key (which excludes `error_model_family`). Fix: skip fail-open snapshots in the non-`'none'` rebuild path; count them as `snapshots_fail_open_skipped`. Symmetric fix applied to both serial and parallel rebuild paths.

**Why APPLY not BLOCKER:** The current rebuild already ran successfully for the FT ship. This would only re-bite on the NEXT full rebuild attempt. Not an active live-trading blocker today.

**Patches in salvage:**
- `salvage-patches/feat_ft-64-live-wiring/0054-fix-calibration-skip-fail-open-snapshots-in-non-none.patch`
- `salvage-patches/feat_ft-64-live-wiring/0055-fix-calibration-symmetric-fail-open-guard-in-paralle.patch`

---

### APPLY-2: Jinan (ZSJN) + Zhengzhou (ZHCC) city onboarding — NOT on main

**Source:** `feat/onboard-jinan-zhengzhou` (1 commit) + `feat/onboard-jinan-zhengzhou-v2` (2 commits — includes INSERT field-name fix)
**Files:** `config/cities.json` and related K1 steps

**Status:** Both Jinan and Zhengzhou are absent from `config/cities.json` on main (verified: `grep -c "Jinan\|Zhengzhou" config/cities.json` → 0). The `feat/onboard-jinan-zhengzhou-v2` branch has 3 commits including a `fix(onboard): align market_events_v2 INSERT field names to canonical writer` patch that the v1 branch lacks. Use v2 variant.

**Note:** Per new-city protocol: onboarding requires full TIGGE historical redownload + full ensemble ingest + calibration before shadow. These patches are just the config registration step. Operator should follow the new-city protocol before enabling in live trading.

**Patches in salvage:**
- `salvage-patches/feat_onboard-jinan-zhengzhou-v2/0001-feat-onboarding-add-Jinan-ZSJN-Zhengzhou-ZHCC-repoin.patch`
- `salvage-patches/feat_onboard-jinan-zhengzhou-v2/0002-fix-onboard-align-market_events_v2-INSERT-field-name.patch`
- (skip `0003-wip-...` — WIP preservation commit, no code change)

---

## UNKNOWN Items (operator domain decision required)

### UNKNOWN-1: EDLI no-submit certificate system (pr-332, 28 commits)

**Question:** Should the EDLI no-submit certificate system be merged to main? It is entirely absent from main. The `codex/edli-v1-no-submit-complete` worktree is preserved separately (PR #332 was open). This is a substantial external-codebase system (event-driven ledger, certificate authority, reactor). No Zeus runtime code touches it. Operator must decide: merge, keep on separate branch, or archive.

**Related:** `codex/edli-live-order-aggregate-substrate` (40 commits) extends pr-332 with live-order aggregate substrate + pre-submit revalidation. Also `codex/edli-v1-implementation` (6 commits) and `redemption/edli-proof-kernel*` are likely precursors superseded by pr-332.

---

## INSIGHT_ONLY Notes

The following patch groups have no runtime impact and can remain on the salvage branch as research artifacts:

- **`draft/ens-refinement-research-2026-05-25`** (10 commits + 1 uncommitted): ENS ship-mechanics spec, HK HIGH root-cause analysis (12Z nighttime-window prior contamination), math/ROI reports, execution ledger. Valuable ops docs. No runtime code.
- **`docs/authority-system-pointers`** (2 commits): Pipeline currency docs + PR #338 Copilot doc fixes. All docs-only.
- **`save/topology-h7-20260524`** (1 commit): H7 working-artifact enforcement — policy already captured in `docs/operations/AGENTS.md`.
- **`backup/pr333-before-rebase`** (1 commit): Pre-rebase snapshot. Content on main.

---

## Classification Counts

| Classification | Branch Count | Commit Count |
|---|---|---|
| LIVE_REGRESSION_BLOCKER | 1 branch (4 commits) | 4 |
| APPLY | 3 branches | 5 |
| DUPLICATE | 18 branches | ~105 |
| SUPERSEDED | 7 branches | ~145 |
| INSIGHT_ONLY | 4 branches | ~14 |
| UNKNOWN | 5 branches (EDLI) | ~82 |

**Total unique not-on-main commits reviewed:** ~355 across 36 branches (11 patches are paired reverts that cancel out).

---

## Reviewer Notes

1. **BLOCKER-1/2/3 are the same 4-commit set** from `feat/ft-ship-64` patches 46-49. The 3 blockers describe different facets of the same chain-terminal quarantine bug. One cherry-pick resolves all three.

2. **ft-ship-64 patches 44+45 cancel** (governor reconcile_finding_limit change + immediate revert). Net effect zero.

3. **ft-64-live-wiring patches 50+53 cancel** (TTL-cache for CLOB facts + immediate revert). Net effect zero.

4. **Qingdao N_star discrepancy** is minor (patch uses 113/113; main has 110/113). Semantically equivalent — the intent (stop the unconditional small-sample amplifier) is met. No re-apply needed.

5. **pr-332 EDLI work** is tracked in the preserved `codex/edli-v1-no-submit-complete` worktree. The salvage patches here are a backup; operator should work from the worktree directly if deciding to merge.
