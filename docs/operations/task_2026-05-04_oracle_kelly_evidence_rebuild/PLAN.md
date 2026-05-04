# Oracle + StrategyProfile + Phase Authority + Phase-Aware Kelly — Live Cutover

**Task ID**: `task_2026-05-04_oracle_kelly_evidence_rebuild` (stable anchor; PR number assigned at open-time)
**Branch**: `oracle-kelly-evidence-rebuild-2026-05-04` (off `main` at `dbe32273`)
**Created**: 2026-05-04
**Last reused/audited**: 2026-05-04
**Authority basis**: operator directive 2026-05-04 ("要做就一步做到位"; "忽略review里任何不上live等言论") + Zeus_May4_review_bugs.md (external review, ignore its shadow-first cadence) + PLAN_v3 §6.P5 (phase-aware Kelly) + AGENTS.md (money path / planning lock / canonical truth) + memory L41 ("Live alpha overrides legacy design loyalty")
**Status**: PLAN-v1 — pre-implementation. Branched off main after PR #53 (`dbe32273`) merged 2026-05-04T07:40:14Z.
**Predecessors**: PR #51 (P0 UTC scheduler + P1 docs purge, merged at e62710e6); PR #53 (P2 MarketPhase plumbing + P3 D-B mode→phase + P4 D-A two-clock unification, flag-gated default-OFF in transit, merged at dbe32273).
**Note on PR numbering**: Earlier drafts of this plan called this work "PR #54", but #54 was taken by an unrelated `live-block antibodies + EntriesBlockRegistry` PR merged 2026-05-04T07:32:00Z (8 min before PR #53). All cross-references in code/docs/registries are anchored to the task path above, not to a PR number, because PR numbers are not assigned until `gh pr create`.

---

## §0 Operator framing

This PR closes Findings A/B/C/D/E/F from Zeus_May4_review_bugs.md as one atomic live cutover. No "shadow first" deferrals; no "spec only" placeholders; no flag default left OFF. The flag `ZEUS_MARKET_PHASE_DISPATCH` is preserved purely as an emergency kill-switch (operator can flip OFF if a catastrophic regression appears) but its **default flips to ON** when this PR ships, making MarketPhase the live dispatch axis from day-1.

The bug review's "shadow first / blocked OOS / evidence cohort" cadence is **explicitly overridden** by operator directive — its conservatism trades live alpha capture for evidence-doc purity, and per memory L41 we fix toward live alpha. The bug review's facts (A: missing≠OK; B: posterior bounds; C: LOW unsupported; D/E: scattered authorities; F: phase observability boundary) are kept; its execution cadence is replaced.

Scope deliberately excluded (own follow-up PRs, not pre-conditions for this one):
- PR #57 — activation artifact run_id/commit_sha/test_hash (Stage 5 of bug review). Independent surface; activation gating is orthogonal to oracle/strategy/phase authority.
- PR #58 — executable EV / orderbook depth / fee replay (Stage 6). Touches executor, not the authority axes this PR rebuilds.

Everything else from the bug review's Stages 0-4 lands here.

---

## §1 Decisions made (no operator gates)

Each was previously a pending OD; now resolved with the evidence on file.

| ID | Question | Decision | Why (citation) |
|---|---|---|---|
| D-1 | P5 phase-aware Kelly: live or shadow? | **LIVE.** Multiplier resolved at open-time on `decision_chain.kelly_multiplier_used`. | Operator directive 2026-05-04 + memory L41. Bug review's shadow-first is overridden. |
| D-2 | MISSING-evidence Kelly multiplier value | **0.5** (raw cap). Beta(1,1) uniform-prior posterior_mean at N=0 IS 0.5; this is the math, not a guess. | Bayes derivation; mirrors bug review §5.4 default. |
| D-3 | LOW-track policy without snapshot infra | **METRIC_UNSUPPORTED → multiplier 0; no live entries on LOW until LOW oracle bridge ships in a follow-up PR.** | Bug review §C: bridge is HIGH-only; LOW bridge is its own scope. Cannot fabricate evidence. |
| D-4 | StrategyProfile registry: spec or impl? | **Full impl + cutover.** New module `src/strategy/strategy_profile.py` + YAML `architecture/strategy_profile_registry.yaml` + 4-site cutover (kelly.py, control_plane.py LIVE_SAFE, control_plane.py _LIVE_ALLOWED, cycle_runner._classify_strategy). | Operator "做就做到位"; partial spec is doc-rot risk per bug review §6.3. |
| D-5 | Rescue ledger location | `docs/operations/live_rescue_ledger_2026-05-04.md` + cohort-boundary helper `cohort_pre_utc_fix(recorded_at)` keyed on PR #51 merge instant `2026-05-04T03:57:08Z` (UTC of `2026-05-03T22:57:08-05:00`). | File path is trivial; merge-instant precision sufficient (cron interval can be modeled as fuzz band in the ledger doc, not a separate column). |
| D-6 | Cohort split storage | **Report-time computation**, not DB column. Helper imported by attribution writers. | Avoids schema migration; instant is git-log facts. |
| D-7 | Phase observability boundary | **Lifted to live phase authority** in this PR via `MarketPhaseEvidence` object with `phase_source ∈ {verified_gamma, fallback_f1, unknown, onchain_resolved}`. `phase=None + flag ON` rejects live entries (Finding F closure). `phase_source=fallback_f1` permitted but `kelly_multiplier *= 0.7` (degraded). `phase_source=onchain_resolved` distinguishes RESOLVED from POST_TRADING. | Operator "做就做到位" + Finding F. Observability-only would be a half-measure. |
| D-8 | Flag `ZEUS_MARKET_PHASE_DISPATCH` lifecycle | **Default flips ON in this PR** (alongside cutover). Flag stays as emergency kill-switch but the day-1 baseline is phase-axis live. After ≥2 stable weeks the flag + legacy branches get excised in a separate cleanup PR. | Operator "做到位" + AGENTS.md "RED-level kill-switch" doctrine (preserve rollback path while shipping live). |
| D-9 | Beta-binomial posterior in this PR or PR #55? | **In this PR.** Without the posterior, the new MISSING/CAUTION/BLACKLIST distinctions are still raw-rate thresholds with no uncertainty. | Bug review §5.3. Posterior is the substance of "evidence-grade", not a deferred refinement. |
| D-10 | Storage path centralization (ZEUS_STORAGE_ROOT) | **In this PR.** New `src/state/paths.py` resolves `ZEUS_STORAGE_ROOT`, default `<repo>/`. Bridge/listener/oracle adopt it. Atomic writers + heartbeat per bug review §5.2. | "做到位"; centralization is a precondition for the path-mismatch class of bugs from PR #40 plan. |

These ten decisions replace `§0.1 OD-1 through OD-7` from the prior plan draft. None require operator confirmation — each is grounded in either operator directive 2026-05-04, the bug review's own §-citations, or AGENTS.md/memory facts.

---

## §2 Anchored facts (load-bearing)

| ID | Fact | Source |
|---|---|---|
| H1 | `_DEFAULT_OK = OracleInfo(0.0, OracleStatus.OK, 1.0)` returns OK for missing (city, metric); log claims "all cities OK" | `src/strategy/oracle_penalty.py:51,77,128-137` |
| H2 | `OracleStatus` enum currently has 4 values: OK/INCIDENTAL/CAUTION/BLACKLIST | `src/strategy/oracle_penalty.py:32-36` |
| H3 | Bridge `_load_settlements()` reads `temperature_metric='high'` only | bug review §C |
| H4 | `STRATEGY_KELLY_MULTIPLIERS` is 6 hardcoded entries | `src/strategy/kelly.py:67` |
| H5 | `_LIVE_ALLOWED_STRATEGIES` is 3 entries; `LIVE_SAFE_STRATEGIES` is 4 (includes shoulder_sell) | `src/control/control_plane.py:316,42-46` |
| H6 | Strategy-from-mode classifier still in `cycle_runner._classify_strategy` | `src/engine/cycle_runner.py:336` |
| H7 | `EdgeDecision.kelly_multiplier_used: float = 0.0` exists; populated at open-time | `src/engine/evaluator.py:215,3230` |
| H8 | `probability_trace_fact.market_phase` column landed in PR #53 stage 3 | git log fac4a9a6 |
| H9 | F1 invariant: Polymarket weather endDate uniformly 12:00 UTC of target_date (verified across 13 cities) | INVESTIGATION_EXTERNAL Q1 |
| H10 | UMA OO Polymarket-default liveness = 7200s (2h); proposePrice settles ≥14h after endDate observed | INVESTIGATION_EXTERNAL Q2 |
| H11 | PR #51 merge instant: `2026-05-04T03:57:08Z` (commit time of e62710e6) | git log -1 e62710e6 --format=%cI |
| H12 | Polymarket CLOB exposes resolution rules / endDate / UMA Settle event per market | bug review §4.2 + Polymarket docs |
| H13 | observed_target_day_fraction across 51 cities at 12:00 UTC ranges from 0.0 (Wellington pre-target) to ~1.0 (LA post-trading-close, but trading already gone) — east-west asymmetry per F4 | PLAN_v3 §3 + bug review §6.7 |

---

## §3 Implementation packets

Each = one commit on the rebuild branch (`oracle-kelly-evidence-rebuild-2026-05-04`). Sequencing in §4.

```
A1  Live rescue ledger + cohort boundary (DOC + helper)
    Files:
      + docs/operations/live_rescue_ledger_2026-05-04.md
      + src/state/cohort_boundary.py    (constants + helper)
      M attribution writer paths        (add cohort label)
    Content:
      - Ledger catalogs #40/#44/#47/#49/#51/#52/#53 with: status,
        live-behavior-changed?, emergency reason, not-final-design debt,
        validation evidence, expiry/review date, rollback condition.
      - cohort_boundary.ZEUS_PR51_MERGE_INSTANT_UTC = "2026-05-04T03:57:08Z"
        cohort_pre_utc_fix(recorded_at_utc) -> bool
        cohort_label(recorded_at_utc) -> {"pre_utc_fix" | "post_utc_fix"}
      - Attribution writer (probability_trace_fact and edge_observation
        downstream) emits cohort_label as report-time tag.
    Tests:
      tests/test_attribution_cohort_boundary.py
        - microsecond-inclusivity at boundary
        - synthetic decisions before/after instant resolved correctly

A2  Storage path centralization
    Files:
      + src/state/paths.py
      M src/strategy/oracle_penalty.py     (use paths.ORACLE_ERROR_RATES)
      M scripts/bridge_oracle_to_calibration.py  (use paths.SNAPSHOT_DIR + ORACLE_FILE)
      M scripts/oracle_snapshot_listener.py      (use paths.SNAPSHOT_DIR)
    Content:
      - paths.ZEUS_STORAGE_ROOT = Path(os.environ.get("ZEUS_STORAGE_ROOT", REPO_ROOT))
      - Atomic writer helper: tmp + os.replace; checksum + writer identity
      - Heartbeat file: paths.ORACLE_ARTIFACT_HEARTBEAT
    Tests:
      tests/test_state_paths.py
        - default == REPO_ROOT
        - ZEUS_STORAGE_ROOT env override redirects all artifacts
        - atomic writer leaves no partial file on crash

A3  Oracle evidence-grade rebuild (Findings A + B + C)
    Files:
      M src/strategy/oracle_penalty.py
      + src/strategy/oracle_estimator.py   (Beta-binomial posterior)
      M scripts/bridge_oracle_to_calibration.py  (output n + mismatches alongside rate)
    Content:
      - OracleStatus extended to 9 members:
        OK, INCIDENTAL, CAUTION, BLACKLIST,
        MISSING, STALE, MALFORMED, METRIC_UNSUPPORTED, INSUFFICIENT_SAMPLE
      - OracleInfo extended:
        city, metric, source_role, status,
        n, mismatches, posterior_mean, posterior_upper_95,
        last_observed_date, artifact_age_hours,
        evidence_quality, penalty_multiplier, block_reason
      - Beta-binomial posterior (Beta(1,1) prior) — see §5 for derivation
      - get_oracle_info(city, metric) returns evidence-graded result;
        missing pair → status=MISSING, multiplier=0.5
      - LOW track:
        if metric == "low" → status=METRIC_UNSUPPORTED, multiplier=0
        until LOW snapshot bridge ships
      - Live-policy multiplier table (constants, not function — auditable):
        OK                   → 1.0
        INCIDENTAL           → 1.0 (matches today's behavior)
        CAUTION              → 1.0 - posterior_upper_95 (linear, ≤0.97)
        BLACKLIST            → 0.0
        MISSING              → 0.5
        STALE                → 0.7  (degraded; threshold = 7d)
        MALFORMED            → previous_cache.multiplier × 0.7
        METRIC_UNSUPPORTED   → 0.0
        INSUFFICIENT_SAMPLE  → max(0.5, 1.0 - posterior_upper_95)
        (where posterior_upper_95 may itself shrink toward 1.0 at small N)
      - reload() catches malformed → status=MALFORMED on cache; previous good
        cache preserved per H1's existing pattern but now with explicit status.
    Tests:
      tests/test_oracle_evidence_status.py
        - OK1: missing file → all (city, metric) → status=MISSING, mult=0.5
        - OK2: unknown city → status=MISSING
        - OK3: known city + metric=low → status=METRIC_UNSUPPORTED, mult=0
        - OK4: malformed JSON → previous cache preserved + status=MALFORMED
        - OK5: Shenzhen high (n=25, m=10) → BLACKLIST (regression antibody)
        - OK6: zero-error N=12 city → INSUFFICIENT_SAMPLE (NOT OK with mult=1.0)
        - OK7: posterior_upper_95 monotone in m, n → no regression to lower bound

A4  StrategyProfile registry + 4-site cutover (Findings D + E)
    Files:
      + src/strategy/strategy_profile.py
      + architecture/strategy_profile_registry.yaml
      M src/strategy/kelly.py            (read multipliers from registry)
      M src/control/control_plane.py     (LIVE_SAFE + _LIVE_ALLOWED from registry)
      M src/engine/cycle_runner.py       (_classify_strategy outputs candidate;
                                          registry validates allowed mode + phase)
      M src/engine/evaluator.py          (resolve multiplier via registry)
    Registry schema:
      <strategy_key>:
        thesis: <text>
        live_status: shadow | blocked | canary | live | deprecated
        allowed_market_phases: [pre_settlement_day, settlement_day, ...]
        allowed_discovery_modes: [opening_hunt, ...]
        allowed_directions: [buy_yes | buy_no | both]
        allowed_bin_topology: [point | finite_range | open_shoulder]
        metric_support:
          high: live | shadow | blocked
          low:  live | shadow | blocked
        kelly_default_multiplier: 1.0
        kelly_phase_overrides:
          settlement_day: 1.0
          pre_settlement_day: 0.5
          post_trading: 0.0
          resolved: 0.0
        min_shadow_decisions: 100
        min_settled_decisions: 30
        promotion_evidence_ref: <doc path or null>
    Initial registry mirrors current 6 strategies' behavior verbatim
    (settlement_capture, center_buy, opening_inertia at live;
    shoulder_sell, shoulder_buy, center_sell at blocked).
    Cutover invariants: every authority that previously read a hardcoded
    set now reads strategy_profile.get(key) → fail-closed on unknown.
    Tests:
      tests/test_strategy_profile_registry.py
        - registry-driven dispatch matches today's hardcoded behavior
          on every (key, mode, phase, direction, metric) tuple from a
          parametrized matrix
        - unknown strategy → reject (no Kelly, no live entry, blacklist log)
        - shoulder_sell stays blocked from live entry post-cutover
        - LIVE_SAFE / _LIVE_ALLOWED divergence is now a single source

A5  MarketPhaseEvidence object + on-chain UMA resolved truth (Finding F)
    Files:
      M src/strategy/market_phase.py
      + src/strategy/market_phase_evidence.py  (dataclass + builder)
      M src/engine/cycle_runtime.py            (build evidence at open-time)
      M src/state/db.py                        (probability_trace_fact: 5 new cols)
      + src/state/uma_resolution_listener.py   (subscribe to UMA Settle)
      M src/engine/dispatch.py                 (phase=None + flag ON → reject)
    Schema additions to probability_trace_fact:
      market_phase_source TEXT     -- verified_gamma | fallback_f1 | unknown | onchain_resolved
      market_start_at TEXT
      market_end_at TEXT
      settlement_day_entry_utc TEXT
      uma_resolved_source TEXT     -- on-chain tx hash if resolved, else NULL
    UMA listener:
      subscribes to UMA OO `SettlementResolved` (or equivalent) event for
      each tracked condition_id; on resolution writes to local
      uma_resolution table; cycle_runtime reads it to mark RESOLVED.
      Replaces the heuristic "endDate < now → POST_TRADING+RESOLVED" collapse.
    Phase=None handling under flag ON:
      filter_market_to_settlement_day, should_enter_day0_window,
      is_settlement_day_dispatch — all reject phase=None for live-authority
      callers (raise PhaseAuthorityViolation). Flag OFF preserves legacy.
      `phase_source=fallback_f1` permitted with kelly_multiplier *= 0.7
      (degraded; pinned in A6 Kelly resolver).
    Tests:
      tests/test_market_phase_evidence.py
        - MarketPhaseEvidence built from market_dict carries phase_source=verified_gamma
          when market_end_at parsed cleanly
        - Missing market_end_at → phase_source=fallback_f1
        - phase=None + flag ON + live caller → raises
        - On-chain Settle event seen → phase=resolved, uma_resolved_source=tx
        - Persistence: 5 new columns present, populated, indexed for phase cohort queries

A6  Phase-aware Kelly LIVE (PLAN_v3 §6.P5 — full live cutover)
    Files:
      M src/strategy/kelly.py        (replace STRATEGY_KELLY_MULTIPLIERS table)
      M src/engine/evaluator.py      (resolver path uses registry + phase + oracle + fraction)
    Resolver formula (open-time, written to decision_chain.kelly_multiplier_used):
      m_strategy_phase   = registry.get(strategy_key).kelly_phase_overrides.get(market_phase, 0)
      m_oracle           = oracle_penalty.get_oracle_info(city, metric).penalty_multiplier
      m_observed_fraction = max(0.3, observed_target_day_fraction(decision_time, target_local_date, city.tz))
      m_phase_source     = 0.7 if phase_source == "fallback_f1" else 1.0
      kelly_multiplier   = m_strategy_phase × m_oracle × m_observed_fraction × m_phase_source
    observed_target_day_fraction:
      fraction = clamp((min(now, target_local_end) - target_local_start) / 24h, 0.0, 1.0)
      where target_local_start = city-local 00:00 of target_local_date
            target_local_end   = city-local 24:00 of target_local_date (= next-day 00:00)
    Migration policy (PLAN_v3 §6.P5 OD7):
      Existing positions retain whatever multiplier was on
      decision_chain.kelly_multiplier_used at open-time (already persisted).
      Only new-open positions use the new resolver.
      No retroactive recompute.
    Flag default:
      ZEUS_MARKET_PHASE_DISPATCH defaults to "1" (ON). Flag stays as
      emergency kill-switch via env override; legacy branches kept until
      a follow-up cleanup PR.
    Tests:
      tests/test_phase_aware_kelly_live.py
        - settlement_capture × SETTLEMENT_DAY × LA × decision_time=2026-05-08 12:30 UTC
          → strategy_phase=1.0, oracle=<known>, fraction (LA at 12:30 UTC = ?), phase_source=verified_gamma
          assert resolved multiplier matches hand-calc
        - same case with phase_source=fallback_f1 → 0.7 ×
        - same case with oracle=MISSING → 0.5 ×
        - existing position retains old multiplier (no recompute)
        - Wellington at 12:30 UTC has fraction near 1.0; LA same UTC has lower fraction
          (east-west asymmetry sanity)
        - integration: cycle replay with mixed (strategy, phase, oracle, city) yields
          deterministic Kelly per (key, phase, city) pair

A7  Critic R6 anti-rubber-stamp regression antibodies
    Files:
      + tests/test_authority_rebuild_invariants.py
    Content (pre-empts critic R6 ATTACKs):
      - I1: missing oracle file → no city returns OK status (Finding A floor)
      - I2: bridge schema regression → output dict carries n, mismatches, posterior
      - I3: registry parse error → fail-closed (no live entries from any strategy)
      - I4: phase=None + flag ON + live caller → raises (Finding F floor)
      - I5: cohort boundary microsecond inclusivity (PR #51 merge instant)
      - I6: Kelly resolver formula deterministic on parametrized fixture matrix
      - I7: post-trading market never enters DAY0_WINDOW under flag ON (D-A regression)
      - I8: storage path centralization — every artifact path resolved through paths.py

A8  Map maintenance + registry housekeeping
    Files:
      M architecture/source_rationale.yaml   (add: strategy_profile.py,
                                                    market_phase_evidence.py,
                                                    oracle_estimator.py,
                                                    cohort_boundary.py,
                                                    paths.py,
                                                    uma_resolution_listener.py)
      M architecture/test_topology.yaml      (add: 7 new test files)
      M docs/operations/current_state.md     (note rebuild cutover)
      M architecture/strategy_profile_registry.yaml metadata header
```

### §3.A6.tbl Phase-aware Kelly multiplier values (live)

```yaml
# Loaded into strategy_profile_registry.yaml::kelly_phase_overrides
# Operator can hot-edit to throttle a (key, phase) pair if realized
# P&L diverges. Initial values from PLAN_v3 §6.P5; registry takes
# authority going forward.

settlement_capture:
  pre_trading: 0.0
  pre_settlement_day: 0.5  # half Kelly when forecast-only entry into Day0 strategy
  settlement_day: 1.0      # full Kelly — peak alpha window
  post_trading: 0.0
  resolved: 0.0

center_buy:
  pre_trading: 0.0
  pre_settlement_day: 1.0  # full Kelly forecast play
  settlement_day: 0.5      # observation contamination penalty
  post_trading: 0.0
  resolved: 0.0

opening_inertia:
  pre_trading: 0.0
  pre_settlement_day: 1.0
  settlement_day: 0.0      # alpha decayed
  post_trading: 0.0
  resolved: 0.0

shoulder_sell:    {pre_trading: 0, pre_settlement_day: 0, settlement_day: 0, post_trading: 0, resolved: 0}  # blocked
shoulder_buy:     {pre_trading: 0, pre_settlement_day: 0, settlement_day: 0, post_trading: 0, resolved: 0}  # dormant
center_sell:      {pre_trading: 0, pre_settlement_day: 0, settlement_day: 0, post_trading: 0, resolved: 0}  # dormant
```

---

## §4 Sequencing

A1 → A2 → A3 → A4 → A5 → A6 → A7 → A8.

Hard dependencies:
- A2 must precede A3 (oracle path resolution moves to centralized helper).
- A3 must precede A4 (registry's `kelly_default_multiplier` consumption depends on oracle status table).
- A4 must precede A6 (Kelly live resolver reads from registry).
- A5 must precede A6 (Kelly live resolver reads `phase_source` for the 0.7 degrade factor).
- A1, A7, A8 can land anywhere but conventional last-touch is A1 first (rescue context for reviewers), then A8 (registries close the loop).

---

## §5 Beta-binomial posterior derivation (anchored math)

Beta(α, β) prior with conjugate Bernoulli observations:

  θ ~ Beta(α₀, β₀)
  θ | m, n ~ Beta(α₀ + m, β₀ + n − m)

Choice of prior: **Beta(1, 1)** (uniform). Justification:
- Non-informative: doesn't pre-commit oracle to any error rate
- Sample size 1 effectively: posterior shrinks to data fast
- Posterior mean at N=0 is (1)/(1+1) = 0.5 → matches the "MISSING → 0.5 multiplier" rule from D-2 by direct math, not by coincidence
- Bug review §5.3 chose this prior implicitly when computing the "0 errors with N=12, 95% upper bound ≈ 3/n" estimate

`posterior_upper_95(m, n)` = upper bound of 95% credible interval on `Beta(1+m, 1+n-m)`. Computed via `scipy.stats.beta.ppf(0.95, α, β)`. Approximation valid for n ≥ 1; at n=0 returns 0.95 (uniform prior 95% upper).

`evidence_quality(n)`:
  n < 10  → "weak"
  n < 50  → "moderate"
  n ≥ 50  → "strong"

`status` derived from posterior_upper_95 + n:
  n == 0                          → MISSING (no posterior)
  n < 10                          → INSUFFICIENT_SAMPLE
  posterior_upper_95 > 0.10       → BLACKLIST
  posterior_upper_95 > 0.05       → CAUTION
  posterior_upper_95 ≤ 0.05, m=0  → OK (zero observed errors at sufficient N)
  posterior_upper_95 ≤ 0.05, m>0  → INCIDENTAL
  artifact_age > 7 days           → STALE (regardless of above)

This collapses bug review's 9 statuses to a deterministic function of (m, n, age). The thresholds (10, 0.05, 0.10, 7d) are policy constants — operator can tune in commit body.

---

## §6 Relationship-test floor

Eight invariants (I1-I8 in §3.A7) are non-negotiable. Beyond those, three integration tests for the full cutover:

**INT1.** End-to-end discovery cycle replay with synthetic 51-city fixture: every candidate either enters with a valid (registry, phase, oracle, fraction) Kelly multiplier, or rejects with a documented reason code. Zero candidates entered with mult=1.0 + status=OK on a missing oracle file.

**INT2.** Pre-PR-54 fixture replay produces identical Kelly values for already-existing positions (migration invariant — no retroactive recompute).

**INT3.** Flag flip OFF→ON→OFF on a live-cycle simulation produces no exception and no orphaned partial transitions in probability_trace_fact (idempotent flag flip).

---

## §7 What this PR is NOT

- Not an executor / orderbook / fee rebuild — PR #58 owns Stage 6.
- Not an activation artifact rebuild — PR #57 owns Stage 5.
- Not a LOW oracle bridge implementation — LOW track is METRIC_UNSUPPORTED until its own PR.
- Not a calibration_pairs_v2 schema change — Kelly is post-calibration; no Platt rebuild.
- Not a CHECK-constraint migration (PLAN_v3 §6.P8) — 6 strategy keys preserved.
- Not a new strategy promotion — shoulder_sell / shoulder_buy / center_sell stay blocked.

---

## §8 Success criteria

1. critic-opus R6 returns APPROVED-WITH-CAVEATS or APPROVED. HIGH-severity caveats addressed before merge.
2. The rebuild PR ships A1-A8 in their own commits per §4.
3. §6 INT1+INT2+INT3 + §3.A7 I1-I8 all green pre-merge.
4. The 4 hardcoded strategy-authority sites resolved through `strategy_profile.get(key)`. `STRATEGY_KELLY_MULTIPLIERS` constant deleted; `_LIVE_ALLOWED_STRATEGIES` and `LIVE_SAFE_STRATEGIES` resolved through registry getters.
5. `oracle_penalty.get_oracle_info()` returns 9-status evidence-graded result. No path returns OK for unknown (city, metric).
6. `MarketPhaseEvidence.phase_source` populated on every probability_trace_fact row written by post-PR-54 cycles.
7. `decision_chain.kelly_multiplier_used` resolves through the live phase-aware resolver for new positions; existing positions unchanged.
8. `ZEUS_MARKET_PHASE_DISPATCH` default flips to "1"; legacy branches retained behind explicit env override.

---

## Cross-references

- Bug review: `/Users/leofitz/Downloads/Zeus_May4_review_bugs.md`
- Predecessor plan: `docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md` (§6.P5 supersedes-but-extends)
- Sibling tasks:
  - `docs/operations/task_2026-05-02_oracle_lifecycle/PLAN_v3.md` (path centralization context)
  - `docs/operations/activation/UNLOCK_CRITERIA.md` (PR #57 reference; not consumed here)
- Authority docs:
  - `AGENTS.md` — money path / planning lock / canonical truth
  - `architecture/source_rationale.yaml` — authority zones for new files
  - `docs/reference/zeus_risk_strategy_reference.md` — Kelly + risk policy reference
- Memory:
  - L41 — "Live alpha overrides legacy design loyalty" (overrides bug review's shadow-first)
  - L22+L28 — full regression suite pre-merge gate
  - feedback_critic_prompt_adversarial_template — R6 attack template
