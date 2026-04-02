# Architecture Correction Pack Report

## A. Semantic Linter Expansion
**Old Rule:** `p_posterior` required `entry_method`.
**New Rule:** Added strict constraint that accessing `p_raw` enforces the presence of `bias_correction`, `calibration`, `platt`, or `sigma_instrument` verification in the semantic provenance envelope.
**Gating:** Integrated functionally into the `pytest` lifecycle (`test_entire_repo_passes_linter`) acting as an airtight syntactic block.

## B. Verification-Based Governance (`ExpiringAssumption`)
**Old Semantic:** Assumed safety degraded from the absolute `introduced_at` date.
**New Semantic:** Replaced `introduced_at` with `last_verified_at`, appending attribution layers: `verified_by` and `verification_source`. This refactors degradation out of chronological decay into empirical confirmation spans.

## C. Zero-Tolerance Diurnal Routing
We collapsed the scattered `15.0` defaulting (found across `src/config.py` and `Portfolio.Position`) into a single deterministic source of truth: `src/signal/peak_hour_provider.py`. All diurnal evaluations are strictly piped through `get_peak_hour_context` which asserts the precise local hour of truth along with analytical `confidence` and `fallback_reason`.

## D. Microstructure Data Schema Expansion
`token_price_log` schema has successfully upgraded to index 5 new microstructure attributes directly from the PolyMarket CLOB API without contaminating downstream logic.
**Schema Amendments:** `volume`, `bid`, `ask`, `spread`, and `source_timestamp`.
**Execution Guard:** Emphatic confirmation that this sprint focuses natively on *data collection and DB ingestion alone*. Evaluators strictly persist limits but no logic was reshaped to weaponize or execute on the spread vectors yet.

## Negative Evidence: 3 Gaps Still Unsolved
1. PolyMarket event creation timestamps (market liquidity latency mapping) are still missing upstream.
2. We have not fully migrated volume aggregations from discrete snapshots into localized rolling-velocity metrics across standard time horizons.
3. Market execution execution sizing (vwmp chunk execution scale assumptions) is not integrated contextually with the new `spread` parameters, still utilizing `size_usd` statics.
