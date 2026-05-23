# T2 No-Trade Events — SCAFFOLD Design Doc

**Authority**: PHASE_2_ULTRAPLAN.md v3.1 §5.2, sha `00c2399742`
**Branch**: `feat/phase2-t2-no-trade-events-20260520`
**Base**: origin/main @ `62177ecfa4` (T1 merged, SCHEMA_VERSION 14)
**Status**: SCAFFOLD (production pass pending)
**Created**: 2026-05-20

---

## §1 Problem Statement

The evaluator (`src/engine/evaluator.py`) produces 69 `rejection_reasons=[...]` callsites across evaluation stages. These raw string reasons are:

- opaque to downstream analysis (no enum constraint → typo drift, duplicate strings, category rot)
- not persisted (no instrumentation table → no rejection rate metrics, no per-market analysis)
- F3 counter (`_cnt_inc("day0_nowcast_write_failed_total")`) missing from the `[DAY0_NOWCAST_WRITE_FAILED]` warning-log block (~line 2439, inside the `except Exception as _nowcast_write_exc` handler)

T2 addresses all three via:

1. `NoTradeReason` StrEnum — canonical 66-member enum (65 + UNCATEGORIZED fallback) covering all 69 callsites (4 shared: `ENS_FETCH_INSUFFICIENT_MEMBERS`×2, `DAY0_NO_FORECAST_HOURS_REMAIN`×2, `CROSSCHECK_UNAVAILABLE`×3, `POLICY_GATED`×2)
2. `no_trade_events` table — world DB instrumentation table persisting every rejection event; PK matches DecisionNaturalKey for FK-like joins with `decision_events`
3. Production wiring — 69 callsite migration replaces raw strings/f-strings with `NoTradeReason.X.value` + `reason_detail` capture + `write_no_trade_event()` calls

SCAFFOLD pass creates the skeleton + antibodies; production pass implements bodies + migrates callsites.

---

## §2 Surface (5 Artifacts)

| Artifact | Path | Status |
|---|---|---|
| Enum | `src/contracts/no_trade_reason.py` | SCAFFOLD created |
| Writer/reader | `src/state/no_trade_events.py` | SCAFFOLD created |
| Schema | `src/state/schema/no_trade_events_schema.py` | SCAFFOLD created |
| Antibody A | `tests/test_inv_no_trade_events_completeness.py` | xfail created |
| Antibody B | `tests/test_inv_evaluator_rejection_enum_exhaustiveness.py` | xfail created |
| Manifest | `architecture/db_table_ownership.yaml` | stub added |
| Manifest | `architecture/source_rationale.yaml` | T2 entries added |
| Design doc | this file | SCAFFOLD created |

**Schema constraints** (per ULTRAPLAN §5.2 verbatim):
- Table: `no_trade_events`, world DB (`zeus-world.db`), K1 split
- PK: `(market_slug, temperature_metric, target_date, observation_time, decision_seq)` — matches `decision_events` natural key for FK-like joins
- Columns: `market_slug, temperature_metric, target_date, observation_time, decision_seq, reason (CHECK enum), reason_detail TEXT, observed_at, schema_version`
- `reason CHECK (reason IN (...))` — enum values from `NoTradeReason` baked into DDL
- `reason_detail TEXT` — two-tier model DETAIL (free-form diagnostic; f-string interpolations go here)
- `schema_version CHECK (schema_version IN (14, 15))` — production pass bumps to 15
- INV-37: `write_no_trade_event(natural_key: DecisionNaturalKey, reason: NoTradeReason, reason_detail: Optional[str], observed_at: str, *, conn: sqlite3.Connection)` — conn REQUIRED, caller-provided, never auto-opened on write path. Reader `read_no_trade_events_by_market` auto-opens if conn=None.
- decision_seq derived atomically via `allocate_decision_seq` (UNION of decision_events + no_trade_events under db_writer_lock) — cross-table collision-free.
- Writer sig per §5.2: `natural_key: DecisionNaturalKey` (5-tuple from `src/contracts/decision_natural_key.py`); decision_seq field in natural_key is ignored (overwritten by allocator)

---

## §3 Callsite Migration Plan (69 rows)

All line numbers verified against `git show origin/main:src/engine/evaluator.py`.

| # | evaluator.py line | rejection_stage | raw reason (fragment) | NoTradeReason member |
|---|---|---|---|---|
| 1 | 1888 | SIGNAL_QUALITY | "Day0 observation unavailable" | `DAY0_OBSERVATION_UNAVAILABLE` |
| 2 | 1901 | SIGNAL_QUALITY | `[source_rejection_reason]` (dynamic) | `OBSERVATION_SOURCE_UNAUTHORIZED` |
| 3 | 1921 | SIGNAL_QUALITY | `[observation_quality_rejection]` (dynamic) | `OBSERVATION_QUALITY_REJECTED` |
| 4 | 1935 | MARKET_FILTER | `[live_entry_forecast_blocker]` (dynamic) | `ENTRY_FORECAST_ROLLOUT_BLOCKED` |
| 5 | 1960 | SIGNAL_QUALITY | f"invalid support_index for..." | `INVALID_SUPPORT_INDEX` |
| 6 | 1967 | SIGNAL_QUALITY | f"support_index mismatch for..." | `SUPPORT_INDEX_MISMATCH` |
| 7 | 2001 | SIGNAL_QUALITY | "< 3 parseable bins" | `INSUFFICIENT_BINS` |
| 8 | 2011 | SIGNAL_QUALITY | f"bin topology: {e}" | `BIN_TOPOLOGY_INVALID` |
| 9 | 2020 | SIGNAL_QUALITY | "support topology has no executable bins" | `NO_EXECUTABLE_BINS` |
| 10 | 2095 | ENTRY_FORECAST | "ENTRY_FORECAST_READER_DB_UNAVAILABLE" | `ENTRY_FORECAST_READER_DB_UNAVAILABLE` |
| 11 | 2125 | ENTRY_FORECAST | `[reader_result.reason_code]` (dynamic) | `ENTRY_FORECAST_READER_REJECTED` |
| 12 | 2144 | SIGNAL_QUALITY | `[str(e)]` SourceNotEnabled | `ENS_SOURCE_NOT_ENABLED` |
| 13 | 2152 | SIGNAL_QUALITY | `[str(e)]` generic Exception | `ENS_FETCH_FAILED` |
| 14 | 2160 | SIGNAL_QUALITY | "ENS fetch failed or < 51 members" | `ENS_FETCH_INSUFFICIENT_MEMBERS` |
| 15 | 2170 | SIGNAL_QUALITY | "ENS fetch failed or < 51 members" | `ENS_FETCH_INSUFFICIENT_MEMBERS` |
| 16 | 2178 | SIGNAL_QUALITY | f"...DEGRADED_FORECAST_FALLBACK..." | `FORECAST_SOURCE_DEGRADED` |
| 17 | 2191 | SIGNAL_QUALITY | "Forecast source evidence incomplete..." | `FORECAST_EVIDENCE_INCOMPLETE` |
| 18 | 2207 | SIGNAL_QUALITY | `[str(e)]` KeyError/TypeError ens_times | `ENS_TIMES_PARSE_ERROR` |
| 19 | 2227 | SIGNAL_QUALITY | "Solar/DST context unavailable for Day0" | `SOLAR_DST_CONTEXT_UNAVAILABLE` |
| 20 | 2242 | SIGNAL_QUALITY | "No Day0 forecast hours remain for target date" | `DAY0_NO_FORECAST_HOURS_REMAIN` |
| 21 | 2253 | SIGNAL_QUALITY | "ENS fetch failed, < 51 members, or insufficient finite required-hour members" | `ENS_INSUFFICIENT_REQUIRED_HOUR_MEMBERS` |
| 22 | 2277 | SIGNAL_QUALITY | `[str(e)]` ValueError EnsembleSignal ctor | `ENS_SIGNAL_CONSTRUCTION_FAILED` |
| 23 | 2300 | SIGNAL_QUALITY | "No Day0 forecast hours remain for target date" | `DAY0_NO_FORECAST_HOURS_REMAIN` |
| 24 | 2309 | SIGNAL_QUALITY | "Day0 low observation unavailable" | `DAY0_LOW_OBSERVATION_UNAVAILABLE` |
| 25 | 2340 | SIGNAL_QUALITY | f"Day0 low slot rejected: causality_status=...INV-16" | `DAY0_LOW_CAUSALITY_REJECTED` |
| 26 | 2361 | SIGNAL_QUALITY | "Day0 current observation became unavailable before signal routing" | `DAY0_CURRENT_OBS_UNAVAILABLE` |
| 27 | 2451 | SIGNAL_QUALITY | "Day0 forecast has insufficient finite remaining ensemble members" | `DAY0_FORECAST_INSUFFICIENT_MEMBERS` |
| 28 | 2470 | SIGNAL_QUALITY | "EXECUTABLE_FORECAST_MEMBERS_UNIT_MISMATCH" | `EXECUTABLE_FORECAST_MEMBERS_UNIT_MISMATCH` |
| 29 | 2484 | SIGNAL_QUALITY | "EXECUTABLE_FORECAST_MEMBER_EXTREMA_INVALID" | `EXECUTABLE_FORECAST_MEMBER_EXTREMA_INVALID` |
| 30 | 2519 | SIGNAL_QUALITY | "P_raw is non-finite, negative, non-normalized..." | `P_RAW_INVALID` |
| 31 | 2536 | SIGNAL_QUALITY | "ENS snapshot persistence failed: decision_snapshot_id unavailable" | `ENS_SNAPSHOT_PERSISTENCE_FAILED` |
| 32 | 2555 | SIGNAL_QUALITY | "DT7_boundary_day_ambiguous" | `DT7_BOUNDARY_DAY_AMBIGUOUS` |
| 33 | 2581 | SIGNAL_QUALITY | "ENS snapshot p_raw persistence failed: canonical p_raw unavailable" | `ENS_SNAPSHOT_P_RAW_PERSISTENCE_FAILED` |
| 34 | 2623 | AUTHORITY_GATE | "authority gate failed due to DB query fault" | `AUTHORITY_GATE_DB_FAULT` |
| 35 | 2634 | AUTHORITY_GATE | f"insufficient_verified_calibration:..." | `INSUFFICIENT_VERIFIED_CALIBRATION` |
| 36 | 2684 | AUTHORITY_GATE | f"forecast data_version...does not resolve to a registered source_family..." | `UNKNOWN_FORECAST_SOURCE_FAMILY` |
| 37 | 2715 | AUTHORITY_GATE | f"forecast data_version...was present without source_id..." | `FORECAST_PROVENANCE_INCOMPLETE` |
| 38 | 2733 | AUTHORITY_GATE | f"ens_result source_id=...disagrees with data_version=..." | `FORECAST_PROVENANCE_INCONSISTENT` |
| 39 | 2764 | AUTHORITY_GATE | f"forecast source_id=...has no registered calibration bucket source_id..." | `UNSUPPORTED_CALIBRATION_SOURCE_ID` |
| 40 | 2811 | AUTHORITY_GATE | "P_cal is non-finite, negative, non-normalized..." | `P_CAL_INVALID` |
| 41 | 2827 | AUTHORITY_GATE | f"invalid calibration maturity level {cal_level!r}: {exc}" | `CALIBRATION_MATURITY_INVALID` |
| 42 | 2845 | AUTHORITY_GATE | "calibration_level=4 has no Platt model;..." | `CALIBRATION_IMMATURE_NO_PLATT` |
| 43 | 2871 | AUTHORITY_GATE | `[str(exc)]` ValueError native_multibin_buy_no | `NATIVE_MULTIBIN_BUY_NO_FLAG_INVALID` |
| 44 | 2950 | MARKET_FILTER | `[str(e)]` EmptyOrderbookError | `MARKET_EMPTY_ORDERBOOK` |
| 45 | 2963 | MARKET_FILTER | `[str(e)]` generic exception clob loop | `MARKET_LIQUIDITY_ERROR` |
| 46 | 2989 | OBSERVATION_SOURCE_UNAUTHORIZED | f"{crosscheck_model} crosscheck unavailable: {e}" | `CROSSCHECK_UNAVAILABLE` |
| 47 | 3007 | OBSERVATION_SOURCE_UNAUTHORIZED | f"{crosscheck_model} crosscheck unavailable" (None) | `CROSSCHECK_UNAVAILABLE` |
| 48 | 3032 | OBSERVATION_SOURCE_UNAUTHORIZED | "GFS crosscheck unavailable" | `GFS_CROSSCHECK_UNAVAILABLE` |
| 49 | 3067 | OBSERVATION_SOURCE_UNAUTHORIZED | f"{crosscheck_model} crosscheck unavailable: {e}" (inner except) | `CROSSCHECK_UNAVAILABLE` |
| 50 | 3083 | ANTI_CHURN | f"{primary_model}/{crosscheck_model} CONFLICT" | `MODEL_CONFLICT` |
| 51 | 3111 | AUTHORITY_GATE | f"ALPHA_TARGET_MISMATCH:{exc}" | `ALPHA_TARGET_MISMATCH` |
| 52 | 3126 | AUTHORITY_GATE | f"AUTHORITY_VIOLATION:{exc}" | `AUTHORITY_VIOLATION` |
| 53 | 3385 | SIGNAL_QUALITY | "selected edge is missing canonical support_index" | `SELECTED_EDGE_MISSING_SUPPORT_INDEX` |
| 54 | 3398 | SIGNAL_QUALITY | f"selected support index {bin_idx} has no executable token payload" | `SELECTED_EDGE_NO_TOKEN_PAYLOAD` |
| 55 | 3413 | SIGNAL_QUALITY | "strategy_key_unclassified" | `STRATEGY_KEY_UNCLASSIFIED` |
| 56 | 3427 | MARKET_FILTER | `[ci_rejection_reason]` (dynamic) | `CONFIDENCE_BAND_INSUFFICIENT` |
| 57 | 3463 | MARKET_FILTER | `[ultra_low_price_reason]` (dynamic) | `CENTER_BUY_ULTRA_LOW_PRICE` |
| 58 | 3479 | ANTI_CHURN | "REENTRY_BLOCKED" | `REENTRY_BLOCKED` |
| 59 | 3494 | ANTI_CHURN | "TOKEN_COOLDOWN" | `TOKEN_COOLDOWN` |
| 60 | 3511 | ANTI_CHURN | "ALREADY_HELD_SAME_TOKEN" | `ALREADY_HELD_SAME_TOKEN` |
| 61 | 3529 | ORACLE_BLACKLISTED | f"oracle_error_rate=...> 10% — city blacklisted" | `ORACLE_BLACKLISTED` |
| 62 | 3578 | DDD_HALT | `[str(exc)]` DDDFailClosed | `DDD_FAIL_CLOSED` |
| 63 | 3593 | DDD_HALT | f"DDD Rail 1 HALT: cov=..." | `DDD_RAIL1_HALT` |
| 64 | 3660 | SIZING_TOO_SMALL | `[str(exc)]` ValueError dynamic_kelly_mult | `KELLY_SIZING_ERROR` |
| 65 | 3677 | SIZING_TOO_SMALL | `[reason]` POLICY_GATED/POLICY_EXIT_ONLY | `POLICY_GATED` |
| 66 | 3772 | RISK_REJECTED | `[str(exc)]` FeeRateUnavailableError | `EXECUTION_PRICE_FEE_RATE_UNAVAILABLE` |
| 67 | 3804 | RISK_REJECTED | `[str(exc)]` ValueError _size_at_execution_price_boundary | `EXECUTION_PRICE_SIZING_ERROR` |
| 68 | 3822 | RISK_REJECTED | f"${size:.2f} < ${limits.min_order_usd}..." | `SIZE_BELOW_MINIMUM` |
| 69 | 3850 | RISK_REJECTED | `[reason]` check_position_allowed risk limits | `RISK_LIMITS_EXCEEDED` |

**Notes**:
- **F3 counter** (ULTRAPLAN §5.2, evaluator.py:2444 area): `_cnt_inc("day0_nowcast_write_failed_total")` belongs inside the `except Exception as _nowcast_write_exc` handler at the `[DAY0_NOWCAST_WRITE_FAILED]` warning-log block (~line 2439). This is a SEPARATE task from row 27. Row 27 (line 2451) is a normal rejection callsite (`DAY0_FORECAST_INSUFFICIENT_MEMBERS`); F3 wiring is in the enclosing exception handler ~12 lines earlier.
- Rows 14–15 (lines 2160, 2170): duplicate string; both map to `ENS_FETCH_INSUFFICIENT_MEMBERS`.
- Rows 20, 23 (lines 2242, 2300): duplicate string; both map to `DAY0_NO_FORECAST_HOURS_REMAIN`.
- Rows 46, 47, 49 (lines 2989, 3007, 3067): three `CROSSCHECK_UNAVAILABLE` callsites (two different except blocks + None-result check).
- Rows 65, [3677]: `[reason]` variable carries either `"POLICY_GATED"` or `"POLICY_EXIT_ONLY"` — both map to `POLICY_GATED`.
- Dynamic callsites (rows 2, 3, 4, 11, 13, 18, 22, 43, 44, 45, 56, 57, 62, 64, 65, 66, 67, 69): production pass wraps the dynamic value into enum-typed call; original dynamic value becomes `reason_detail`.
- Distinct enum members used: 65 (out of 69 callsites; 4 shared: 2+2+3+2 duplicates). Total enum size: 66 (65 + UNCATEGORIZED fallback).

**reason_detail capture transform** (two-tier model per §5.1):
- Raw string callsite: `reason_detail = "<the literal string>"` (carry exact text for query-ability)
- f-string callsite: `reason_detail = f"..."` (interpolated at callsite, passed as str)
- Dynamic/`str(e)` callsite: `reason_detail = str(e)` or `reason_detail = variable` (original value preserved)

**Natural-key threading plan**:
Many early-stage callsites (rows 1–26, lines 1888–2361) fire inside helper functions that receive `market_slug` + `city` but may not yet have `temperature_metric`/`target_date`/`observation_time` in scope. The production-pass approach:
1. The evaluator top-level loop constructs `natural_key` before entering the helper chain (market_slug + temperature_metric + target_date are available from the outer `for market_slug, ...` loop; observation_time from the Day0 observation context).
2. Pass `natural_key` as an additional argument through `_make_rejection_decision` (or extract from the returned rejection dict at the callsite).
3. For callsites inside helper closures lacking the 5-tuple, thread `natural_key` as a parameter (same pattern decision_events uses — see `write_decision_event` which receives `natural_key` from the evaluator's call frame).
4. `decision_seq` is the sequence within the 4-tuple scope: derive atomically in the writer under db_writer_lock (same pattern as `decision_events.py:210`).
5. For callsites at rows 44–69 (market-filter + later stages), `natural_key` is always in scope as the evaluator has a fully-constructed candidate context by that point.

---

## §4 Production Pass Checklist

- [ ] Fill `write_no_trade_event()` body in `src/state/no_trade_events.py`
- [ ] Fill `read_no_trade_events_by_market()` body in `src/state/no_trade_events.py`
- [ ] Bump `SCHEMA_VERSION` 14→15 in `src/state/db.py` + update CHECK constraint in schema file
- [ ] Wire `ensure_table(conn)` into `db.py:init_schema` (world DB path)
- [ ] Create migration script `scripts/migrate_no_trade_events_create_2026_05_21.py`
- [ ] Migrate all 69 callsites in `evaluator.py`: replace raw strings/f-strings with `NoTradeReason.<member>` + add `reason_detail=<original>` + add `write_no_trade_event(natural_key, reason, reason_detail, conn=conn)` call
- [ ] Wire F3 counter (`_cnt_inc("day0_nowcast_write_failed_total")`) inside the `[DAY0_NOWCAST_WRITE_FAILED]` except handler ~line 2439 (NOT at line 2451 which is a separate normal rejection)
- [ ] Thread `natural_key` parameter through `_make_rejection_decision` or top-level evaluator call frame per natural-key threading plan in §3
- [ ] Remove `@pytest.mark.xfail` from completeness antibody + `test_inv_no_orphan_enum_members` (INV-A `test_inv_evaluator_no_raw_string_literals_post_migration` becomes GREEN automatically)
- [ ] Verify `test_inv_evaluator_no_raw_string_literals_post_migration` passes (0 raw/f-string sites remain)
- [ ] Verify `test_inv_evaluator_callsite_count` passes (still 69)
- [ ] Verify `test_inv_no_orphan_enum_members` passes (no orphan members)
- [ ] Verify `test_inv_migration_table_member_values_valid` passes (already GREEN)
- [ ] PR-T2 must include BOTH (a) 69 callsite migration AND (b) F3 counter wiring in one PR
