# SESSION-WIDE CLOSURE VERDICT — Phase 3-7 Mainline Completion

> **RESOLUTION (PR #284):** All 3 findings below were FIXED in PR #284. This is a FROZEN
> review record: it was written against `origin/main @62ed96e133` when `SCHEMA_VERSION` was
> **25**. Five sibling live PRs (#279–283) then advanced main to `22dba73349` / `SCHEMA_VERSION`
> **26**. The shipped SEV-3.1 CHECK-list fix targets the current **26** (not the 25 cited in the
> frozen body below). Version numbers in the body reflect review-time state, not current code.

**VERDICT: FIX_REQUIRED** *(historical — fixed in PR #284)*

Delta reviewed: `git diff phase2_landed(5c471cd51f)..origin/main(62ed96e133)` = 221 files, +37408/-974.
Worktree: phase7-settlement-typegate-20260521 @ 62ed96e133 (clean, == origin/main).
Reviewer mode: THOROUGH → escalated to ADVERSARIAL after 2 failing tests + a schema-coherence smell surfaced.
Reviewed: 2026-05-21.

## Headline

The **money gate holds end-to-end** — the single most important session invariant is intact: every NEW
strategy from Phase 3 and Phase 4 is gated OFF live capital. The cross-phase data seams (regime→correlation→
candidate, gross+variance cluster-cap composition, tier→settlement) are genuinely wired, not stubbed, and
compose correctly. **No new strategy can reach live capital. No schema-chain corruption at runtime.**

However, two NEW-PHASE tests fail on mainline HEAD (both within the 364-failure baseline, so not a fresh
regression — but they are logic/coherence failures, NOT env-gated, so they are in-scope and block CLEAR_PASS).
Both are TEST defects with NO production runtime risk, but they are CI hygiene failures that must be fixed
before the mainline-completion is declared done. Plus one schema-CHECK-list staleness smell (latent, no
current writer).

---

## SEV-2 (must fix before declaring done — failing tests in the curated/new-phase set)

### SEV-2.1 — Stale absolute SCHEMA_VERSION assertion (cross-phase coherence)
`tests/test_shoulder_strategy_vnext.py:156` — `test_p_3_5_schema_version_is_22`
```
assert SCHEMA_VERSION == 22  →  AssertionError: assert 25 == 22
```
Phase 3 hardcoded `SCHEMA_VERSION == 22`. Phases 5/6/7 legitimately bumped it 23→24→25
(`src/state/db.py:897 SCHEMA_VERSION = 25`). The Phase-3 test asserts a brittle absolute version
constant that later phases invalidated — a textbook cross-phase seam a per-phase critic cannot catch.
- **Production risk: NONE.** The bump is correct and intentional; only the frozen test constant is wrong.
- **Fix:** Update the assertion to `== 25`, or (better) delete the absolute-version test and rely on
  `tests/state/test_schema_current_invariant.py` (which passes) for version coherence. Absolute-constant
  version asserts should never have been written — they guarantee breakage on the next bump.

### SEV-2.2 — Test mock omits a real BinEdge derived property
`tests/test_fdr.py:701` (`test_full_family_selection_uses_one_candidate_family_across_strategies`),
crash at `src/contracts/shoulder_strategy_vnext.py:97`
```
AttributeError: 'types.SimpleNamespace' object has no attribute 'is_open_high'
```
The fixture builds an edge bin with `is_shoulder=True` (line 701) but omits `is_open_high`.
`classify_shoulder_candidate` reaches `b.is_open_high` only on the shoulder path; sibling fixtures use
`is_shoulder=False` and short-circuit, which is why only this one test trips.
- **Production risk: NONE — verified.** On the REAL `BinEdge` (`src/types/market.py:95-105`),
  `is_open_high` and `is_shoulder` are computed `@property` (derived from low/high), ALWAYS present. A real
  shoulder edge by construction has `is_open_high` computable. Only the hand-rolled `SimpleNamespace` mock
  is missing it. This test has been latently broken since Phase 3 introduced the `is_open_high` access
  (confirmed: fixture never had the field at phase3/phase6/main; 0 matches at all three refs).
- **Fix:** Add `is_open_high=False` (and `is_open_low=False`) to the line-701 fixture, OR use a real
  `BinEdge` instead of `SimpleNamespace`. Prefer the real type — `SimpleNamespace` mocks of `BinEdge`
  silently diverge from its property surface (this is the second time a derived property bit a mock).

---

## SEV-3 (latent / hygiene — no current runtime path)

### SEV-3.1 — Three table CHECK lists do not include the current SCHEMA_VERSION (25)
- `src/state/schema/tail_stress_scenarios_schema.py:34` — `CHECK (schema_version IN (17..23))` — missing 24,25
- `src/state/schema/shoulder_exposure_ledger_schema.py:37` — `CHECK (schema_version IN (22,23))` — missing 24,25
- `src/state/schema/regime_correlation_cache_schema.py:38` — `CHECK (schema_version IN (24))` — missing 25
- **Why no crash today:** each writer stamps the row with its *table-local* `SCHEMA_VERSION` constant
  (shoulder_exposure_ledger.py:93 → 23; regime_correlation_store.py:174 → 24), both in their own CHECK list.
  `tail_stress_scenarios` currently has NO writer at all — `run_stress_tests`
  (`src/strategy/stress_scenarios.py:98-132`) only computes a dict, never INSERTs (T2 thin mode, math
  deferred to T3+). So all three CHECK lists are latent.
- **Why it still matters (Fitz #4 provenance):** when T3+ wires a real tail_stress writer, or any future
  migration re-stamps these rows at the global version, these CHECK lists will reject the insert. This is a
  pattern that recurred once already this session (Phase 3 wave-critic caught a ghost table). The structural
  fix is to make the category impossible: drive the table-row schema_version from the writer's intent and
  keep CHECK lists as monotonic ranges that always include the current global version on every bump.
- **Fix:** extend each CHECK list through 25 (or convert to `CHECK (schema_version <= 25)` style range gated
  by a coherence test). Low effort, removes a latent landmine.

---

## PER-SEAM FINDINGS (the point of a session-wide pass)

### SEAM 1 — MONEY GATE end-to-end across ALL 13 strategies — **PASS**
Runtime enumeration via `src/strategy/strategy_profile.py::all_profiles()` (13 strategies):

| runtime-live=True (4, ALL pre-existing) | runtime-live=False (9) |
|---|---|
| center_buy (live/LIVE_NORMAL) | shoulder_buy (blocked/IDEA) — P3 |
| imminent_open_capture (live/LIVE_LIMITED_HAIRCUT) | shoulder_sell (shadow/SHADOW_PASS) — P3 |
| opening_inertia (live/LIVE_LIMITED_HAIRCUT) | cross_market_correlation_hedge (shadow/IDEA) — P4 |
| settlement_capture (live/LIVE_NORMAL) | neg_risk_basket, liquidity_provision_with_heartbeat, resolution_window_maker, stale_quote_detector, weather_event_arbitrage (shadow) — P4 |

- All 4 runtime-live keys were already `live_status: live` at `phase2_landed` (verified via
  `git show phase2_landed:architecture/strategy_profile_registry.yaml`). NONE are new.
- Gate logic (`strategy_profile.py:135-138`): `live_status=="live" AND evidence_tier>=LIVE_PILOT_TINY(5)`.
- Tier ordering verified: SHADOW_PASS=3, PAPER_COHORT=4 both < LIVE_PILOT_TINY=5. The PAPER_COHORT(4)
  blocked-from-live boundary is explicitly tested (`tests/test_strategy_profile_evidence_tier.py:100`).
- **THE invariant the whole session must preserve is intact.** Note: boundary coverage is via spot-checks
  rather than a single all-13 enumeration loop the brief requested — adequate (gate is structural), but
  adding the explicit all-registry loop would harden it.

### SEAM 2 — Regime→Correlation→Candidate chain — **PASS (genuinely wired, not stubbed)**
Traced `src/strategy/candidates/cross_market_correlation_hedge.py` end-to-end:
`regime_tag_for(city, target_date, decision_time, conn)` (P3, line 159) → SQL read of
`regime_correlation_cache` (P5 T2, line 178) → `RegimeCorrelationStore(conn).get(regime, stored_cities)`
(P5, line 213) → off-diagonal magnitude gate (line 241). Data flows; no hardcode/stub. `_SHADOW_EDGE=0.02`
is decision_events-row only; `target_size_usd=None` on enter confirms no live sizing. Fail-open at every
guard maps to `CORR_HEDGE_REGIME_UNAVAILABLE`. Correct shadow behavior.

### SEAM 3 — Cluster-cap interaction (P3 gross + P5 variance) — **PASS (compose conservatively)**
`src/state/portfolio.py:2105-2108` — `policy_heat = max(gross_heat, variance_heat)` when variance context
exists, else gross only. Neither cap overrides the other; the more-restrictive wins. Production caller
`src/engine/evaluator.py:4702-4709` DOES pass full regime context (`_phase5_store`, regime, cities), so the
variance cap is live in production (not the gross-only path at evaluator.py:517, which is the
projected/family-fallback path). Ordering correct: cluster `policy_heat` → `risk_throttle`
(evaluator.py:4716-4728) → `dynamic_kelly_mult` (line 4740). **Cap fires before the kelly multiplier.**
Graceful degrade to `gross_notional` on missing/UNKNOWN regime context.

### SEAM 4 — Tier→Settlement (P7 verifier consumes P6 EvidenceTier) — **PASS (coherent, looser than brief)**
`src/contracts/settlement_capture_verifier.py:28` imports `EvidenceTier`; normalizes enum-or-string to
`.name` for storage (lines 146-151); persists on the verification row. `check_pre_promotion_gate`
(line 246-292) is a COHERENT-count gate (≥ threshold, default 5), NOT a tier comparison — tier is stored,
gate is count-based. Coupling is looser than the brief implied ("consumes EvidenceTier") but the contract is
coherent: no type mismatch, no defect.

### SEAM 5 — SCHEMA CHAIN coherence — **PASS at runtime (with SEV-3.1 latent caveat)**
- `src/state/db.py:897` SCHEMA_VERSION = 25; PRAGMA stamped at db.py:2544.
- Required gate suite: `test_table_registry_coherence + test_schema_current_invariant +
  test_money_path_lifecycle_replay + test_live_release_gate` → **39 passed, 0 failed.**
- `no_trade_events_schema.py` CHECK lists correctly include 25 (the high-churn table).
- The 3 stale CHECK lists (SEV-3.1) do NOT break registration coherence (the coherence test passes) because
  no current writer stamps them at 25. No ghost/unregistered table recurred (Phase 3 wave-critic's catch
  did not regress).

### SEAM 6 — Antibodies present + passing — **PASS**
- P7 type-gate: `grep 'umaResolutionStatus =='` over `src/` → **0 matches (exit 1)**. The only `!=`
  comparison is the single canonical typed-gate constructor (`src/contracts/settlement_outcome.py:179`).
  Raw string equality is eradicated.
- P4 BH counts-based FDR partition + sb= spread_bucket: tests present
  (`tests/test_phase4_t1_spread_bucket.py`, `tests/test_fdr.py`) — pass except the SEV-2.2 mock defect.
- P3 Kelly clamp [0.05,0.20]: present.
- P6 regret-sign regression (wave-fix): `tests/analysis/test_regret_decomposer.py` +
  `tests/analysis/test_live_readiness_tribunal.py` → pass.

### TAGS / PROVENANCE — PASS with one note
phase3..phase7 tags are sequential, no duplicates/orphans, each points at a real commit. NOTE:
`phase3_landed` points at the merge of sibling-PR #265 (`fix/registry-shoulder-ledger-boot`, a concurrent
live-launch PR), not a phase-3-named commit — tag-placement artifact from interleaving, not a content defect
(phase 3 content is in ancestry; SEAM 1/5 verified phase-3 strategies present and gated).

---

## BASELINE / ENV-GATED CONFIRMATION
Full-suite baseline on clean main: **364 failed / 10262 passed / 173 skipped** (6805s). The ~356 env-gated
(venue/web3/RPC/creds) failures are pre-existing and out of scope per brief. Spot-check confirmed the
character: e.g. `tests/test_venue_command_repo.py` (sibling live-launch PR #264/#265 territory, explicitly
NOT this session's focus) has a non-env order-fact dedup assertion failure — flagged here for awareness only,
NOT attributed to Phase 3-7. The 2 SEV-2 failures above ARE within the 364 baseline (not fresh regressions
from this review) but are logic/coherence, not env-gated, hence in-scope and blocking.

---

## WHAT WOULD UPGRADE TO CLEAR_PASS
1. Fix SEV-2.1 (update/delete the `SCHEMA_VERSION==22` absolute assertion).
2. Fix SEV-2.2 (add `is_open_high`/`is_open_low` to the test_fdr.py:701 fixture, or use real BinEdge).
3. (Recommended, not blocking) Extend the 3 SEV-3.1 CHECK lists through 25 before any T3+ writer lands.

No SEV-1. No new strategy reaches live capital. No runtime schema corruption. The cross-phase architecture
is sound; the blockers are test-only coherence failures, not shipped-broken production logic.
