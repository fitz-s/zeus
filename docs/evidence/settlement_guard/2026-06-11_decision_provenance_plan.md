# Plan evidence: DecisionProvenanceEnvelope (one envelope, every receipt, queryable)

Created: 2026-06-11 ~13:30Z. Authority basis: OPERATOR LAW 2026-06-11 ~13:20Z (verbatim):
"我要每一个下单决定receipt都有来自那些数据组合，距离发布多久，距离结算多久等等所有的详细
数据全部被记录，每一个被拒绝的具体原因都要写出来，每一个做的决策为什么都需要被查阅。我需要
一切可被溯源" — every order-decision receipt (ACCEPTED and REJECTED, every stage) must carry a
complete provenance envelope: which data combination produced it, age-since-publication of every
input, time-to-settlement, all decision economics, and the FULL untruncated rejection reason.
Everything queryable.

## The K=1 design decision (not N field-patches)

The receipt surfaces ALREADY carry scattered fragments (q_live, snapshot_id, posterior_id,
rejection_reason). The operator does not want N more columns; he wants ONE assembled, queryable,
fail-soft **provenance envelope** computed at decision time from truths that ALREADY EXIST, and
threaded — byte-for-byte the same object — into EVERY decision-receipt writer. The decision is:
*one builder, three threadings, one query path*. Observability only — the envelope must NEVER
change a decision (money-path byte-identity is asserted in a test with the builder monkeypatched
off).

## Existing truths assembled (verified by reading the code — none assumed)

1. Posterior data-combination — `ReplacementForecastPosteriorBundle` (src/data/
   replacement_forecast_bundle_reader.py): `posterior_id`, `provenance_json` (carries
   `replacement_q_mode`, `u0r_fusion`={used_models, dropped_models, excluded_regionals,
   dropped_aliases, raw_model_forecast_ids, anchor_bridge, decorrelated_providers_*, lead_bucket,
   predictive_sigma_c}, `staleness_violations`, `tradeable_latest_selection`, `bin_topology`,
   `settlement_sigma_floor_*`, `capture_status`, `q_lcb_basis`), `dependency_json` (baseline_b0 /
   aifs_sampled_2t / openmeteo_ifs9_anchor source_run_ids), `source_cycle_time`,
   `source_available_at`, `computed_at`, `data_version`.
2. Per-input AGES — `raw_forecast_artifacts` (src/state/schema/v2_schema.py) rows give
   `source_cycle_time` / `source_available_at` / `captured_at` and `artifact_metadata_json`
   (carries anchor-transport `run_authority`: run_pinned_single_runs / provider_meta_declared /
   bucket_partial_run_*). Ages = decision_time minus each timestamp. The anchor's artifact is
   located by the dependency source_run_id.
3. Time-to-settlement — `settlement_day_entry_utc(target_local_date, city_timezone)` (src/strategy/
   market_phase.py) = city-local midnight; local-day END = entry(target_date + 1 day); city tz via
   `cities_by_name[city].timezone`. `executable_market_snapshots.market_end_at` minus decision_time
   = hours_to_market_end.
4. Book — `executable_market_snapshots` (src/state/snapshot_repo.py): `snapshot_id`, `captured_at`,
   `orderbook_top_bid`, `orderbook_top_ask`, `market_end_at` (looked up by executable_snapshot_id).
5. Economics + rejection — `EventSubmissionReceipt` (src/events/reactor.py) carries q_live,
   q_lcb_5pct, c_fee_adjusted, trade_score, direction, kelly_size_usd, mainstream_*; the rejection
   `stage` + FULL `reason` flow through `OpportunityEventReactor._write_regret`.

## Change set

NEW src/contracts/decision_provenance.py — `build_decision_provenance_envelope(forecast_conn,
trade_conn, *, bundle, decision_time, condition_id, token_id, executable_snapshot_row,
economics, direction, rejection)` -> dict. Pure assembly; NO network; fail-soft per field (a
missing sub-truth records `{"<field>": "UNAVAILABLE: <why>"}` — never crashes, never silently
omits). Envelope fields: decision_time, posterior_id, q_mode, fusion_instruments,
anchor_transport (run_authority), dependency_source_run_ids, per_input_ages
{cycle_age_h, available_age_h, capture_age_h}, staleness_violations, posterior_computed_age_h,
time_to_settlement {local_day_end_utc, hours_to_local_day_end, market_end_at, hours_to_market_end},
book {snapshot_id, captured_at, best_bid, best_ask, age_s}, economics {q_live, q_lcb, price,
fee_model='0.05*p*(1-p)*shares', edge, trade_score, kelly_size_usd}, direction +
direction_law_verdict, mainstream verdict if present, rejection {stage, reason FULL TEXT}.

Threading:
- Rejection (regret ledger): `no_trade_regret_events.envelope_json` TEXT via schema-ensure ALTER
  (_ensure_columns, additive append-only convention); `NoTradeRegretEvent.envelope_json` field;
  `OpportunityEventReactor._write_regret` builds the envelope (fail-soft) and passes it.
- No-submit: `edli_no_submit_receipts.envelope_json` TEXT via _ensure_column; written from the
  reactor's accepted-no-submit path. (Envelope is omit-when-None in receipt_json for hash stability,
  mirroring posterior_id/alpha_gap precedent.)
- Accepted submit: append a `DECISION_PROVENANCE` event to the existing append-only
  `provenance_envelope_events` aggregate (subject_type='command', source='OPERATOR',
  payload_json=envelope) via `append_provenance_event` — no new table.

FULL REASON: storage audit confirms the ONLY `[:200]` truncation in the receipt path is the log
warning (reactor.py:942, display) — `rejection_reason` reaches `NoTradeRegretEvent.rejection_reason`
and the TEXT column untruncated. The envelope's `rejection.reason` carries full text too. A
no-truncation pin test guards it.

Query path: extend walker scripts/verify_e2e_money_path.py stage 9 to pretty-print the envelope
(colon-free safe); NEW scripts/query_decision_provenance.py --condition-id/--scope --last N.

## Invariants preserved

- Money-path byte-identity: envelope is observability; a test asserts decisions are identical with
  the builder monkeypatched to return None.
- Live DBs read-only in tests; synthetic in-memory conns with minimal schemas (fixture style copied
  from tests/execution/test_settled_external_absorber.py).
- Schema changes additive only (ADD COLUMN via the schema-ensure convention; never destructive).
- Writer-lock antibody: no raw sqlite3.connect outside the sanctioned readers; the builder only
  READS the passed conns. INV-37: no cross-DB write transaction; the builder reads forecast_conn
  (raw_forecast_artifacts) and trade_conn (snapshots) independently, read-only.
- No settings.json edits; no daemon restarts (any needed restart is reported, not performed).

## Antibody

tests/contracts/test_decision_provenance.py — (1) builder unit: synthetic conns produce an
envelope with ages + time-to-settlement + fusion_instruments populated; (2) RELATIONSHIP: a
rejection written through the regret ledger carries envelope_json with per-input ages AND
time-to-settlement populated (cross-module: reactor._write_regret -> ledger -> column); (3) full-
reason no-truncation pin (a 4000-char reason round-trips byte-identical); (4) money-path byte-
identity with the builder disabled.
