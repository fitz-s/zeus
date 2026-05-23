# C-2 Fix — Opus Critic Verdict (2026-05-22)

Branch `claude/agent-ac369d05295761891` @ 7e4ca31386. VERDICT: **FIX_REQUIRED (SEV-2)**.

## Headline
The 2-line strategy_key fix (`_strategy_key_for`/`_strategy_key_for_hypothesis` IMMINENT_OPEN_CAPTURE → "imminent_open_capture") is correct in INTENT (cohort separation, family_id namespace) but is **NOT provably attribution-only**. The commit message's "profile-level gates/sizing/Kelly are byte-equal" claim is FALSE.

## The real finding (MAJOR / SEV-2)
The two registry profiles differ on sizing-relevant fields:
- `opening_inertia` (strategy_profile_registry.yaml:233/:221): `kelly_phase_overrides[settlement_day]=0.0`, `allowed_market_phases=[pre_settlement_day]`
- `imminent_open_capture` (:304/:292): `kelly_phase_overrides[settlement_day]=0.5`, `allowed_market_phases=[pre_settlement_day, settlement_day]`

Live path: evaluator.py:4850 `_strategy_key_for` → :5293 `phase_aware_kelly_multiplier(strategy_key,...)` → kelly.py:250 `profile.kelly_for_phase` → strategy_profile.py:178 `kelly_phase_overrides.get`. So for an IOC candidate at `market_phase==settlement_day`, the persisted key now yields Kelly 0.5 instead of 0.0 — i.e. a candidate previously phase-blocked→no-trade now sizes at live half-Kelly. That is firing+sizing, not attribution.

## Why it is narrowly gated (the mitigation)
Under the **live-default flag `ZEUS_MARKET_PHASE_DISPATCH=1` (ON)**: an IOC candidate at settlement_day is intercepted by `is_settlement_day_dispatch` (evaluator.py:2236 / dispatch.py:216) and routed to `settlement_capture` (a third profile), so (key=imminent_open_capture, phase=settlement_day) is **mutually exclusive** → the Kelly delta does NOT fire. Zero behavior change under live default. The delta fires ONLY under the emergency kill-switch `ZEUS_MARKET_PHASE_DISPATCH=0`.

## Path-independent fixes (do regardless of operator decision)
1. **Correct the commit message** — drop "byte-equal"; state the delta (kelly_phase_overrides[settlement_day] 0.0→0.5; allowed_market_phases adds settlement_day).
2. **Add Kelly-path non-vacuous test**: `phase_aware_kelly_multiplier(strategy_key="imminent_open_capture", market_phase="settlement_day") == 0.5` vs opening_inertia 0.0; plus a flag-OFF assertion documenting the divergence.
3. **MINOR**: `cycle_runner._classify_strategy:476` still collapses imminent→"opening_inertia" (docstring claims DB-CHECK/risk/projection authority). No prod caller today (test-only refs), dormant — but latent re-contamination if rewired. Update its mapping+docstring or delete it.

## Operator-gated decision (the only blocker)
Under flag-OFF (emergency revert), IOC@settlement_day sizing changes 0.0→0.5. Registry INTENT shows imminent_open_capture is deliberately designed for 0-24h-to-settlement with 0.5 — so the fix arguably REVEALS intended sizing the old wrong mapping suppressed. Decision:
- **A. Accept** (revealed-intended IOC behavior). Proceed with fixes 1-3.
- **B. Strictly attribution-only**: add a flag-OFF guard preserving the old phase-block (0.0) for IOC@settlement_day, guaranteeing zero sizing change.

## Confirmed-clean probes
- Key string matches registry:280 exactly. ✓
- DB CHECK fail-closed: schema v27 migrations actively strip hardcoded strategy_key CHECKs (db.py:3357/3395); no insert-failure for "imminent_open_capture". ✓
- New tests non-vacuous (revert→fail confirmed). 8 passed with fix.
- gate/haircut/entry-floor path (via _strategy_live_quality_policy) IS byte-equal between profiles — only the Kelly-phase + allowed-phases fields diverge.
- Pre-existing unrelated failure: test_phase6_day0_split.py riskguard_trailing_loss_stale (fails identically on HEAD~1).
