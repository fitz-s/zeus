# Remaining Repair Backlog

> Scope: items intentionally left open after the non-DB small-package repair loop on `data-improve`.
> Created: 2026-04-13.
> Rule: do not mix these with the ongoing DB rebuild tree unless the packet explicitly depends on rebuilt data.

## Current Baseline

The non-DB runtime/semantic repair pass has landed through:

- `38288e4` — tail alpha is now explicit calibration treatment
- `9ec7a65` — alpha consumers declare EV compatibility
- `4966f8e` — exit EV gates declare zero-cost `HoldValue`
- `6e2adb5` — complete market-family vig is removed before posterior fusion
- `2463d70` — harvester Stage-2 has DB-shape preflight
- `e29fa3f` — missing monitor-to-exit chains escalate to infrastructure RED
- `4804419` — Gamma discovery rejects explicit cross-city market bindings
- `bcbad58` — settlement-sensitive entries reject degenerate CI
- `18ab892` — FDR authority is candidate-family scoped
- `2815078` — buy-yes exits use degraded best-bid proxy when needed
- `433461c` — day0 stale probability no longer blocks exit authority
- `88278d2` — live entry sizing uses token-specific fee-adjusted execution price

The remaining items below are not closed by those commits.

## DB / Rebuild / Calibration Dependent

These should wait for the DB rebuild or explicitly run against the rebuilt data tree.

1. **Historical diurnal aggregates DST-safe rebuild**
   - Rebuild `hourly_observations` / `diurnal_curves` from zone-aware local timestamps.
   - Revalidate DST cities: NYC, Chicago, London, Paris.
   - Runtime `get_current_local_hour()` is already zone-aware; the remaining risk is stale materialized history.

2. **Calibration-pair and Platt readiness after raw data rebuild**
   - Backfill/materialize replay-compatible `ensemble_snapshots.p_raw_json`.
   - Rebuild calibration pairs from decision-time vectors.
   - Refit Platt models only after vector/materialization integrity is proven.

3. **Harvester bias-correction provenance**
   - `harvest_settlement()` still does not record whether live/evaluator bias correction was enabled for the `p_raw` it is learning from.
   - Needs DB/data provenance support, not just a code marker.

4. **Alpha override profitability validation**
   - Current note says only London alpha override has been validated as profitable.
   - Requires per-city validation on rebuilt settlement/decision data before enabling or deleting overrides.

5. **Strategy tracker PnL must be reconstructible from durable truth**
   - Current tracker surfaces can report PnL not derivable from durable settlement/exit events.
   - Fix should rebuild or mark tracker summaries from durable event truth, not JSON/Paper legacy state.

6. **Legacy paper positions and stale fallback**
   - Paper positions with missing token IDs and `chain_state=unknown` caused stale fallback/RiskGuard RED.
   - This is legacy-state cleanup and projection validation, not a live-only runtime patch.

7. **Malformed `solar_daily` rootpage verification**
   - Needs a deliberate day0 capture / schema-integrity run against the rebuilt DB.
   - Current evidence is stale-unverified, not a confirmed active runtime failure.

## Larger Strategy / Architecture Packages

These are not blocked by a specific DB file, but they are too broad for blind small-patch treatment.

1. **D3 execution economics full closure**
   - Done: evaluator sizing uses token-specific fee-adjusted `ExecutionPrice`.
   - Remaining: market-specific `tickSize`, `negRisk`, dynamic fee-rate provenance, realized fill/slippage reconciliation, and carrying typed execution cost beyond evaluator.

2. **D4 symmetric `DecisionEvidence` contract**
   - Current state: several exit asymmetry manifestations are fixed or mitigated.
   - Remaining: entry and exit still do not share one comparable statistical burden contract.
   - Needs a design packet spanning evaluator, monitor refresh, exit authority, and tests.

3. **D2 profit-validated tail policy**
   - Done: tail scaling is explicit `TailTreatment(serves='calibration_accuracy')`.
   - Remaining: define and validate a profit-serving tail policy, likely direction/objective-aware, using buy-no P&L evidence.

4. **D6 nonzero hold-cost policy**
   - Done: exit EV gates route through zero-cost `HoldValue` for auditability.
   - Remaining: define funding/time/correlation costs with provenance and pass portfolio context into exit authority.

5. **D1 true EV-target alpha policy**
   - Done: active entry/monitor consumers gate alpha target compatibility.
   - Remaining: derive and validate an EV-target alpha policy; diagnostic replay still unwraps alpha directly and should be handled in a separate parity package if replay promotion depends on it.

6. **Durable monitor evidence spine**
   - Done: missing near-settlement monitor chains now escalate to infrastructure RED.
   - Remaining: DB projection/schema support for lifetime monitor counts or durable monitor evidence before settlement.

7. **Harvester Stage-2 canonical migration**
   - Done: Stage-2 DB-shape preflight makes missing runtime tables visible and non-spammy.
   - Remaining: migrate Stage-2 learning to fully canonical helpers and rebuilt `p_raw_json` data.

8. **Gamma source attestation beyond discovery**
   - Done: `find_weather_markets()` rejects explicit cross-city discovery conflicts.
   - Remaining: monitor helpers (`get_current_yes_price`, `get_sibling_outcomes`) and harvester closed-event polling need their own source-attestation package if they must defend against malformed Gamma payloads.

## External / Workspace Coordination

1. **Open-Meteo quota coordination**
   - Needs workspace-wide scheduling/cooldown coordination across Zeus and sibling data agents.
   - Current evidence suggests recent 429s are stale, but the structural risk remains.

2. **ACP router pre-dispatch hard gates**
   - Router fallback currently recovers after auth/network/timeout failures instead of preflighting candidates before dispatch.
   - Needs workspace/router package, not a Zeus runtime-only patch.

## Suggested Order

1. Finish DB rebuild and run data integrity checks.
2. Rebuild or validate point-in-time `p_raw_json`, calibration pairs, diurnal aggregates, and Platt models.
3. Close strategy tracker / stale legacy surfaces from durable truth.
4. Pick one larger strategy design package at a time: D3 full execution economics, D4 `DecisionEvidence`, D2 profit-tail policy, D6 nonzero hold cost, D1 EV alpha.
5. Only after each design package has critic + review + verifier approval, promote it from backlog into an active operations packet.

## Guardrails

- Do not promote replay/backtest output into live authority without market-price linkage, active sizing parity, and selection-family parity with the live control unit.
- Do not interpret a rebuilt WU settlement sample as strategy replay coverage unless forecast references, compatible vectors, and market prices are also linked.
- Do not touch physical DB files from this backlog document; use a dedicated data/rebuild tree and record run IDs.
