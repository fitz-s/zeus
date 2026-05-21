# Task: Evidence Tier / Tribunal Authority Repair

Reference analysis: `analysis_evidence_tier_tribunal_authority.md`.

Rule for future continuation: before editing any item, re-read the matching
finding text in the analysis file and verify whether current `main` has already
fixed it. Do not reimplement a finding that is already closed by code and tests.

| Finding | Status | Implementation target |
| --- | --- | --- |
| 1. EvidenceTier runtime gate ignores required tier | COMPLETED | `StrategyProfile.is_runtime_live()` uses `evidence_tier_required_for_live` and blockers; `live_allowed_keys(conn=...)` supports DB effective tier |
| 2. EvidenceReport/no_trade strategy provenance mismatch | COMPLETED | `no_trade_events` gains `strategy_key`, `event_source`, `shadow_runtime`; writer/report use structured columns |
| 3. Tribunal writes not durable | COMPLETED | PROMOTE/DEMOTE uses state-layer writer with commit before return |
| 4. Evidence assignment schema/reducer missing | COMPLETED | constrained schema + `current_evidence_tier_assignment()` reducer |
| 5. Runtime does not read DB assignments | COMPLETED | control-plane strategy gate reads DB assignment overlay and fails closed if unavailable |
| 6. Gross vs variance scalar collapse | CLOSED_ALREADY_FIXED | Current `main` has `ClusterExposureResult` and `policy_heat=max(gross, variance)` |
| 7. Correlation matrix validation | CLOSED_ALREADY_FIXED | Current `main` validates matrices on fit/get |
| 8. Regret sign semantics | CLOSED_ALREADY_FIXED | #277 defines positive regret as realized-over-counterfactual win and tests it |

## Verification Targets

- `python3 scripts/check_schema_version.py`
- `python3 -m pytest tests/test_strategy_profile_evidence_tier.py tests/analysis/test_live_readiness_tribunal.py tests/test_decision_seq_cross_table_no_collision.py tests/test_phase4_t2_candidates.py tests/test_phase4_t3_candidates.py tests/test_phase4_t4_candidates.py tests/test_cluster_exposure_shrunk.py tests/test_regime_correlation_store.py -q`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <changed files>`
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files <changed files>`
