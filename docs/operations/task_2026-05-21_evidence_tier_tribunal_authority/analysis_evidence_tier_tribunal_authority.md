# Evidence Tier / Tribunal Authority Audit

Source: operator-provided report, 2026-05-21. This file preserves the report's
full finding set in compact form so implementation progress can survive
compaction and branch handoff.

## Executive Verdict

Verdict: SAFE ONLY AFTER P0/P1 FIXES.

The money-path execution and redeem side-effect boundaries are materially
stronger than the prior audit. Remaining risk is governance authority mismatch:
EvidenceTier, ShadowExperiment, Tribunal, no-trade reporting, and correlation
risk objects must feed the same live eligibility authority.

## Findings

1. EvidenceTier runtime gate ignores `evidence_tier_required_for_live`.
   `StrategyProfile.is_runtime_live()` must use per-strategy required tier,
   not a hard-coded `LIVE_PILOT_TINY` threshold. Promotion blockers must be
   either hard-blocked or surfaced as structured runtime blockers.

2. `EvidenceReport` cannot trust `no_trade_events.strategy_key` unless the
   schema actually has that column. No-trade strategy provenance must be
   structured rather than buried in `reason_detail`.

3. Tribunal PROMOTE/DEMOTE writes must be durable or explicitly marked
   uncommitted. Returning a domain verdict while the authority row remains in
   an uncommitted caller transaction is a governance side-effect split.

4. `evidence_tier_assignments` needs schema constraints and reducer semantics:
   valid tier range, schema version, assignment source, verdict kind, and a
   deterministic current-state reducer.

5. Runtime live strategy tier must define the authority order between static
   YAML profile baseline and DB evidence-tier assignments. DB demotions must
   be able to block a static-live strategy.

6. Cluster exposure gross heat and variance heat must not collapse into a
   scalar whose semantics change when correlation context appears.

7. Regime correlation matrix JSON must be validated on write and read before it
   can influence risk sizing.

8. Evidence report win-rate semantics must not invert regret sign. If
   positive `total_regret_usd` means realized alpha over counterfactual, that
   convention must be documented and tested.

## Current-Code Triage

- Findings 6 and 7 are already fixed on current `main`: `ClusterExposureResult`
  separates gross and variance heat and policy uses `max(gross, variance)`;
  `RegimeCorrelationStore` validates city uniqueness, square shape, dimension,
  finite values, diagonal, symmetry, bounds, and PSD on fit/get.
- Finding 8 is already fixed by #277: positive `total_regret_usd` is explicitly
  defined as realized over counterfactual and tribunal tests cover winning and
  losing cohorts.
- Findings 1-5 remain the active repair scope for this branch.
