# Track G Financial Reconciliation — READ-side Fix (F106–F111)

**Date**: 2026-05-17
**Branch**: `fix/wave2-track-g-financial-recon-2026-05-17`
**Source report**: `docs/operations/task_2026-05-16_post_pr126_audit/RUN_16_track_G_financial_reconciliation.md`
**Karachi-bridge companion**: `fix/karachi-bridge-structural-2026-05-17` (WRITE-side)

---

## §1 — Verbatim Findings (F106–F111 from §8 of RUN_16)

> **F106** | Schema mismatch: `position_lots.position_id` (INTEGER) ≠ `position_current.position_id` (TEXT UUID); `USING(position_id)` silently empty. Canonical join requires `position_lots.source_command_id → venue_commands.command_id → venue_commands.position_id` | **SEV-1 META** | NEW (Run #16 T G)

> **F107** | 3 of 13 non-voided positions (incl. Karachi 5/17 `c30f28a5-d4e`) have zero `position_lots CONFIRMED_EXPOSURE` rows yet hold positive `cost_basis_usd` and `shares`. Dual-writer divergence between `position_current` and the lot materializer. Max drift $1.70, total un-lot-backed cost $3.33 | **SEV-1** | NEW (Run #16 T G)

> **F108** | `venue_trade_facts` stores per-trade lifecycle revisions sharing `trade_id`; bare `SUM(filled_size)` over-counts by 1×–4×. Any monitor / report using this aggregate over-states filled exposure. Correct form: `SUM(MIN(filled_size) per (position, trade_id))` | **SEV-1** | NEW (Run #16 T G)

> **F109** | Position duplicate `0a0e3b72-46e` and `7557a029-4ad` reference the same `token_id` (London 18°C 5/19 buy_yes), each claiming 6 shares × $0.31 = $1.86. On-chain has 6 shares total. DB over-books 6 shares / $1.86; realized PnL will phantom +$6 if WIN | **SEV-1** | NEW (Run #16 T G)

> **F110** | `settlements_v2` table is EMPTY (0 rows) despite 5 `economically_closed` positions. Cannot disambiguate normal (markets not yet UMA-finalized) from broken settlement writer this run. Direct consequence: F48 fix verification cannot reach a non-zero positive case on current DB | **SEV-2 OBS** | NEW (Run #16 T G)

> **F111** | `economically_closed` positions retain `position_current.shares > 0` post-exit while on-chain holds 0. By-design (preserves PnL recompute), but probes that filter only `phase != 'voided'` over-state live exposure by +95 shares | **SEV-3 SEM** | NEW (Run #16 T G)

---

## §2 — Root-cause (verbatim from RUN_16 §1)

> "Schema mismatch: `position_lots.position_id` (INTEGER) ≠ `position_current.position_id` (TEXT UUID); `USING(position_id)` silently empty."

> "The correct join goes through `venue_commands` (TEXT-keyed `position_id`): `position_lots.source_command_id → venue_commands.command_id → venue_commands.position_id (TEXT UUID)`"

---

## §3 — Production code audit result

**Empirical finding**: No production code in `src/` or `scripts/` contains a broken cross-table join between `position_lots` and `position_current`. All 10 `FROM/JOIN position_lots` locations were audited:

| File | Line(s) | Join form | Status |
|---|---|---|---|
| `src/risk_allocator/governor.py` | 530–538 | self-join + `LEFT JOIN venue_commands ON cmd.command_id = lot.source_command_id` | CANONICAL |
| `src/state/venue_command_repo.py` | 264, 272 | `JOIN venue_trade_facts ON trade_fact_id = lot.source_trade_fact_id` | CANONICAL |
| `src/state/venue_command_repo.py` | 2072, 2090 | `WHERE source_trade_fact_id = ?` (point lookup) | CANONICAL |
| `src/execution/exchange_reconcile.py` | 2131 | `WHERE source_trade_fact_id = ?` (point lookup) | CANONICAL |
| `src/ingest/polymarket_user_channel.py` | 541–548 | self-join MAX(local_sequence) — internal | CANONICAL |
| `src/ingest/polymarket_user_channel.py` | 873 | `JOIN venue_trade_facts ON trade_fact_id = lot.source_trade_fact_id` | CANONICAL |

**No query rewrites required.** F106 identified the broken join as a pattern in the auditor's READ-ONLY probe skeleton, not in deployed code.

**SUM(filled_size) audit (F108)**: The one site in `src/execution/command_recovery.py:751` that uses `SUM(CAST(filled_size))` is already wrapped in a `latest_trade_fact` CTE that deduplicates by `MAX(local_sequence)` per `trade_id`. Correct form already deployed.

---

## §4 — Per-finding outcomes

### F106 — ANTIBODY (no code rewrite needed)

**Outcome**: Antibody test written at `tests/state/test_position_lots_reconciliation.py`.

Static scan test asserts:
1. No `.py` file in `src/` or `scripts/` contains SQL with both `position_lots` and `position_current` bridged directly via `USING(position_id)` or `pl.position_id = pc.position_id` (the broken keyspace cross-join).
2. Canonical probe SQL (report §1 form) returns correct non-null results when position_lots rows exist for a position.

Rationale: Report §11 explicitly calls for this as "F106 antibody" — a pytest that asserts `USING(position_id)` between these two tables is a known-bad pattern.

### F107 — RETRACT (WRITE-side, Karachi-bridge domain)

**Outcome**: No Track G action. Cross-referenced to `fix/karachi-bridge-structural-2026-05-17`.

Verbatim root cause from F107: "Dual-writer divergence between `position_current` and the lot materializer." The 3 un-lot-backed positions (`3a6f0728-c50`, `8f02dc01-b6b`, `c30f28a5-d4e`) lack `position_lots` rows because the materialization path (`exchange_reconcile_entry_fill_materialization` / Karachi synthesizer) did not fire. This is a WRITE-side structural gap — the Karachi-bridge agent's domain. Track G READ-side cannot and must not create missing lot rows (no data backfill per `feedback_no_manual_precedent_for_any_structural_defect`).

**Karachi safety**: With the canonical join, the READ-side queries correctly return NULL / 0 cost for these positions until the bridge fills. This is correct — no false reconciliation.

### F108 — ANTIBODY (no code rewrite needed)

**Outcome**: Antibody test written at `tests/state/test_position_lots_reconciliation.py`.

Static assertion: any SQL in `src/` that references `SUM` and `filled_size` adjacent to `venue_trade_facts` must also reference a deduplication guard (`latest_trade_fact`, `MAX(local_sequence)`, `GROUP BY trade_id`, or `DISTINCT trade_id`).

Functional test: constructs multi-revision `venue_trade_facts` rows for one `trade_id` (MATCHED→MINED→CONFIRMED lifecycle), asserts the `latest_trade_fact` CTE pattern counts 1, while bare SUM would count 3.

### F109 — DOCUMENT-ONLY (no Track G code change)

**Outcome**: Documented here. Operational gate at `fix/karachi-bridge-structural-2026-05-17` or direct operator action required.

Root cause: non-idempotent position-open created duplicate `(token_id, target_date, direction)` rows for London 18°C 5/19. Two positions `0a0e3b72-46e` and `7557a029-4ad` each claim 6 shares; on-chain has 6 total. READ-side cannot void one position without DB write. Operator must void one via ops tooling or confirm the PnL path deduplicates by `(token_id, market_id)` before London 5/19 settles.

**Risk if unresolved**: If London 18°C 5/19 settles WIN, payout received once on-chain ($6) but counted twice in realized PnL (+$6 phantom).

### F110 — DOCUMENT-ONLY (observational)

**Outcome**: No action required. `settlements_v2.rowcount=0` is the current state.

Per report §7: either markets are not yet UMA-finalized (normal — `economically_closed` ≠ market resolved) or the settlement writer is broken. Cannot disambiguate READ-side. Cross-reference: F48 fix correctness cannot be end-to-end validated on current DB because the non-zero positive case is absent.

### F111 — ANTIBODY (lightweight semantic guard)

**Outcome**: Antibody test written at `tests/state/test_position_lots_reconciliation.py`.

Asserts that any SQL in `src/` computing `SUM(shares)` from `position_current` without a `phase` filter is flagged as a potential live-exposure overcount. The by-design behavior (closed positions retain `shares > 0` for PnL recompute) must be made explicit via phase exclusion in any live-exposure aggregate.

---

## §5 — Antibody test file

Created: `tests/state/test_position_lots_reconciliation.py`

Tests written:
1. `test_f106_no_broken_position_id_cross_join_in_src` — static scan, zero-tolerance for `USING(position_id)` between position_lots and position_current
2. `test_f106_canonical_join_returns_results` — functional: canonical `source_command_id → venue_commands → position_id` path returns correct cost aggregation
3. `test_f108_latest_trade_fact_cte_deduplicates` — functional: multi-revision trade_facts produce correct single-count with CTE, over-count without
4. `test_f108_no_bare_sum_filled_size_without_dedup_guard` — static scan for bare `SUM.*filled_size` without dedup guard adjacent to `venue_trade_facts`
5. `test_f111_live_exposure_query_excludes_closed_phases` — static scan: any `SUM(shares)` over `position_current` in src/ must have phase-exclusion guard for closed positions

---

## §6 — Cross-track summary

| Finding | Track | Outcome |
|---|---|---|
| F106 | G (READ) | ANTIBODY — static+functional tests |
| F107 | Karachi-bridge (WRITE) | RETRACT from Track G; cross-referenced |
| F108 | G (READ) | ANTIBODY — static+functional tests |
| F109 | Operator / Karachi-bridge | DOCUMENT-ONLY |
| F110 | Observational | DOCUMENT-ONLY |
| F111 | G (READ) | ANTIBODY — static scan |

**Brief expectation vs evidence**: Brief anticipated 3-8 production query rewrites. Evidence shows 0 rewrites needed — all `position_lots` queries in production already use the canonical `source_command_id → venue_commands` bridge. The correct structural response is antibody coverage, per report §11 recommendations.
