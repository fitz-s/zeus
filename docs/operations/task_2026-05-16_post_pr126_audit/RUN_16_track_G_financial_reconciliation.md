# RUN #16 Track G — Financial Reconciliation Correctness (F106–F111)

**Date**: 2026-05-17/18 (UTC)
**Branch**: `fix/wave-2-lineage-and-k1-cleanup-2026-05-17`
**Worktree**: `.claude/worktrees/zeus-deep-alignment-audit-skill`
**Mode**: READ-ONLY (no DB writes, no code mutations)
**Karachi-blast-radius probe target**: position `c30f28a5-d4e` (Karachi 37°C 5/17)

---

## §0 — Scope and method

NEW ANGLE: book-keeping ↔ on-chain reality reconciliation.

Audited tables / sources:

- `state/zeus_trades.db`: `position_current`, `position_lots`, `position_events`, `venue_commands`, `venue_order_facts`, `venue_trade_facts`, `settlements_v2`, `collateral_reservations`, `collateral_ledger_snapshots`
- `state/risk_state.db`: `risk_state` (latest row only)
- On-chain reality: derived from latest `collateral_ledger_snapshots` row (`authority_tier='CHAIN'`, captured 2026-05-18T00:01:34Z) which mirrors Polymarket CTF token balances + USDC.e + pUSDC

All queries run via read-only sqlite3 URI mode (`?mode=ro`); zero writes confirmed by `git status --short` clean across `state/*.db` (which are untracked anyway).

Macro counts:

| Table | Rows | Note |
|---|---|---|
| `position_current` | 76 | 63 voided / 5 economically_closed / 5 pending_exit / 2 active / 1 day0_window |
| `position_lots` | 17 | 11 CONFIRMED_EXPOSURE / 6 OPTIMISTIC_EXPOSURE; **11 distinct `position_id` values (INT keyspace)** |
| `position_events` | (sampled) | uses TEXT UUID `position_id` (e.g. `'7211b1c5-d3b'`) |
| `venue_commands` | 91 | TEXT UUID `position_id`; canonical join bridge |
| `venue_order_facts` | 190 | no `position_id` column — joins via `command_id` |
| `venue_trade_facts` | 40 | revision history per trade (multi-row per `trade_id`) |
| `settlements_v2` | **0** | empty despite 5 economically_closed positions |
| `collateral_reservations` | 88 (1 OPEN, 87 RELEASED) | |
| `collateral_ledger_snapshots` | 12,631 | latest tier=CHAIN, captured 30 s before audit |

---

## §1 — Schema mismatch breaks the obvious join (F106 — SEV-1 META)

The mandate's Task 1 SQL skeleton was:

```sql
SELECT pc.position_id, pc.cost_basis_usd, SUM(pl.cost_usd) AS lots_sum
  FROM position_current pc LEFT JOIN position_lots pl USING (position_id) ...
```

**This skeleton silently returns empty.** Two distinct keyspaces:

```
PRAGMA table_info(position_current):
  position_id TEXT      e.g. 'c30f28a5-d4e', '7211b1c5-d3b'   (UUID prefix)

PRAGMA table_info(position_lots):
  position_id INTEGER   e.g. 7, 11, 13, 39, 42, 53, 55, 56, 57, 58, 59
```

Cross-membership test: `[str(x) for x in lots_ids if str(x) in cur_ids] == []` — **zero overlap**.

The lots table also has **no `cost_usd` column**; cost must be computed `shares × CAST(entry_price_avg AS REAL)`.

The correct join goes through `venue_commands` (TEXT-keyed `position_id`):

```
position_lots.source_command_id → venue_commands.command_id → venue_commands.position_id (TEXT UUID)
```

**Impact**: any prior reconciliation code that wrote `USING (position_id)` between these two tables would have produced zero drift and a clean bill of health regardless of reality. This is a category-of-bug per Fitz #4 (data provenance) — the type system permits a syntactically valid join that is semantically null.

Per-run probe (canonical form, READ-ONLY):

```sql
WITH lot_cost AS (
  SELECT vc.position_id AS pos_uuid,
         SUM(CAST(pl.shares AS REAL) * CAST(pl.entry_price_avg AS REAL)) AS lots_cost,
         SUM(CAST(pl.shares AS REAL)) AS lots_shares
    FROM position_lots pl
    JOIN venue_commands vc ON vc.command_id = pl.source_command_id
   WHERE pl.state = 'CONFIRMED_EXPOSURE'
   GROUP BY vc.position_id
)
SELECT pc.position_id, pc.phase, pc.cost_basis_usd, pc.shares,
       lc.lots_cost, lc.lots_shares,
       ROUND(pc.cost_basis_usd - COALESCE(lc.lots_cost,0), 6)  AS drift_cost,
       ROUND(pc.shares         - COALESCE(lc.lots_shares,0), 6) AS drift_shares
  FROM position_current pc
  LEFT JOIN lot_cost lc ON lc.pos_uuid = pc.position_id
 WHERE pc.phase NOT IN ('voided');
```

---

## §2 — Cost-basis vs lots reconciliation (F107 — SEV-1)

Run of §1 canonical probe across 13 non-voided positions:

| position_id | phase | cost_basis_usd | shares | lots_cost | drift_cost |
|---|---|---:|---:|---:|---:|
| **3a6f0728-c50** | economically_closed | 1.7000 | 5.0 | **NULL** | **+1.7000** |
| **8f02dc01-b6b** | economically_closed | 1.0458 | 11.62 | **NULL** | **+1.0458** |
| **c30f28a5-d4e** | day0_window (**Karachi 5/17**) | 0.5873 | 1.5873 | **NULL** | **+0.5873** |
| 4cd2f9ee-1d1 | pending_exit | 0.1043 | 1.304337 | 0.104347 | −0.000047 |
| 7211cc19-e02 | economically_closed | 3.1579 | 13.157891 | 3.157894 | ≈0 |
| 8273c341-c79 | pending_exit | 1.3860 | 4.95 | 1.3860 | 0 |
| 0a0e3b72-46e | pending_exit | 1.8600 | 6.0 | 1.86 | 0 |
| 7557a029-4ad | pending_exit | 1.8600 | 6.0 | 1.86 | 0 |
| e914a28a-420 | active | 1.2120 | 12.12 | 1.212 | 0 |
| bf0a16f5-f95 | active | 1.0000 | 100.0 | 1.0 | 0 |
| 6d8abfb4-b87 | pending_exit | 0.1408 | 1.28 | 0.1408 | 0 |
| 1bbb697b-161 | economically_closed | 1.6590 | 23.7 | 1.659 | 0 |
| 43822a1f-e9e | economically_closed | 5.3400 | 35.6 | 5.34 | 0 |

**Cost-basis drift count**: **3 of 13** (23%) non-voided positions have zero matching lots.
**Max-magnitude drift**: **+$1.7000 USD** (`3a6f0728-c50`, London 5/19).
**Total un-lot-backed cost**: $1.70 + $1.0458 + $0.5873 = **$3.3331** ≈ 1.8% of latest pUSD balance ($189.05).

For 10 positions where lots exist, cost reconciles to within ≤ $5 × 10⁻⁵ (floating-point only).

**Share drift count**: identical pattern — same 3 positions show `lots_shares=NULL`; the other 10 reconcile exactly.

**Hypothesis** (un-disproven this run): `position_lots` materialization runs after a `venue_trade_fact` lifecycle event, but for these 3 positions the materialization path was skipped (likely `exchange_reconcile_entry_fill_materialization` did not fire). The cost basis in `position_current` was instead populated by a different code path that bypasses the lots system. Per Fitz #1, the SEV-1 is the divergent dual-writer pattern, not the 3 missing rows.

---

## §3 — Karachi 5/17 specific check (mandate Task 5)

Position `c30f28a5-d4e` — Karachi 37°C 5/17 buy_yes.

| Source | Value |
|---|---|
| `position_current.cost_basis_usd` | $0.587301 |
| `position_current.shares` | 1.5873 |
| `position_current.entry_price` | 0.37 |
| `position_lots` rows | **0** (no `CONFIRMED_EXPOSURE` lot exists) |
| `venue_trade_facts` filled_size (DISTINCT trade_id) | 1.5873 (single MATCHED→CONFIRMED) |
| On-chain CTF token `5391…57884` | 1,587,300 micro = 1.5873 shares |
| Implied `shares × entry_price` | 1.5873 × 0.37 = **0.587301** ✓ |

**Verdict**: book(cost) reconciles **exactly** with on-chain shares × entry price. Money is real, position is real, exposure is real. **But the audit trail (`position_lots`) is missing for this position** — same dual-writer divergence as F107. If reconciliation alarms or PnL post-mortem queries depend on `position_lots`, they will silently skip the Karachi 5/17 position.

---

## §4 — Open-shares vs venue-order-fact / venue-trade-fact (F108 — SEV-1)

Mandate Task 2 asked: `SUM(vof.shares_signed)` per position. `venue_order_facts` has **no `position_id` and no `shares_signed` column** — only `remaining_size` and `matched_size`. The correct surrogate is `venue_trade_facts.filled_size`, joined via `venue_commands`.

Naive `SUM(filled_size)` per position (mandate-style):

| position_id | shares (book) | filled_size SUM | drift (book−SUM) |
|---|---:|---:|---:|
| bf0a16f5-f95 | 100.0 | **400.0** | **−300.0** |
| e914a28a-420 | 12.12 | 108.48 | −96.36 |
| 43822a1f-e9e | 35.6 | 106.80 | −71.20 |
| 1bbb697b-161 | 23.7 | 71.10 | −47.40 |
| 0a0e3b72-46e | 6.0 | 18.0 | −12.0 |
| 7557a029-4ad | 6.0 | 18.0 | −12.0 |
| 3a6f0728-c50 | 5.0 | 10.0 | −5.0 |
| 6d8abfb4-b87 | 1.28 | 3.84 | −2.56 |
| (4 other rows) | … | matches | 0 |

Root cause (proven on `bf0a16f5-f95`): `venue_trade_facts` stores **lifecycle revisions of the same trade**:

```
trade_fact_id=4  trade_id=ab9d… state=MATCHED   filled=100  seq=1
trade_fact_id=5  trade_id=ab9d… state=MINED     filled=100  seq=2
trade_fact_id=6  trade_id=ab9d… state=CONFIRMED filled=100  seq=3
trade_fact_id=10 trade_id=ab9d… state=CONFIRMED filled=100  seq=4
```

All 4 rows share `trade_id`. `SUM(filled_size) = 400` is 4× over-count. The correct aggregation is `SUM(DISTINCT trade_id → latest filled)` or equivalent via window function. Re-run with `MIN(filled) per (position, trade_id)` reconciles **all 13 positions exactly** to `position_current.shares` (drift = 0.0 across the board).

**Impact**: any monitor / alarm / report that computes "filled exposure" via bare `SUM(filled_size)` over `venue_trade_facts` will over-report by 1×–4×. This is a type-of-aggregation invariant that must be encoded as a SQL view or a guard test. (Fitz #2 — make the wrong aggregation unwritable.)

---

## §5 — Position duplicate / double-book (F109 — SEV-1)

By-token reconciliation EXCLUDING `economically_closed` positions:

| Token (last 8) | DB shares | On-chain shares | Drift |
|---|---:|---:|---:|
| …56811733 | 1.28 | 1.28 | 0 |
| …45757884 (Karachi 5/17) | 1.5873 | 1.5873 | 0 |
| …95705768 | 1.304337 | 1.304337 | 0 |
| …48357139 | 12.12 | 12.12 | 0 |
| …35888662 | 100.0 | 100.0 | 0 |
| …62050823 | 4.95 | 4.95 | 0 |
| **…30054946 (London 18°C 5/19)** | **12.0** | **6.0** | **+6.0** |

Two `position_current` rows for the same `token_id=113959…30054946` (London 18°C 5/19):

```
position_id=0a0e3b72-46e   phase=pending_exit  shares=6.0  cost=$1.86
position_id=7557a029-4ad   phase=pending_exit  shares=6.0  cost=$1.86
```

Same city, same target_date, same bin_label, same direction, same token_id. On-chain there are 6.0 shares of this token. The DB claims 12.0 — a **2× over-book**. Cost basis is similarly doubled: $1.86 + $1.86 = $3.72 booked vs $1.86 real.

**Karachi blast radius**: not Karachi-specific, but the same pattern could repeat for any forecast where the position-open path is non-idempotent. The 0a0e3b72/7557a029 pair was opened on the same target with identical state — likely an idempotency-key collision miss at position-open.

**Implication for PnL**: if either position settles WIN, payout will be received once on-chain (6 × $1) but counted twice in the realized-PnL ledger → +$6 phantom profit.

---

## §6 — On-chain CTF balance reconciliation (mandate Task 7)

Latest `collateral_ledger_snapshots` (id=12631, captured 2026-05-18T00:01:34Z, `authority_tier='CHAIN'`):

```
pusd_balance_micro          = 189,052,990   ($189.05 USDC)
pusd_allowance_micro        = 2^63 − 1      (infinite approval — Polymarket pattern)
usdc_e_legacy_balance_micro = 0             (legacy USDC.e fully migrated)
reserved_pusd_for_buys      = 0
ctf_token_balances_json     = 7 token entries (see §5 table)
```

Cross-check: `risk_state` latest row (id=13779) `details_json.initial_bankroll = 189.05` — **matches** chain pUSDC to the cent.

`collateral_reservations` open: 1 row `CTF_SELL` $6.00 (matches `reserved_tokens_for_sells_json` `{113959…30054946: 6000000}`) — **matches**.

**Treasury / collateral verdict**: pUSDC, USDC.e, open reservations all reconcile chain↔DB. No discrepancy at the wallet layer.

---

## §7 — settlements_v2 empty + economically_closed share retention (F110, F111)

```sql
SELECT COUNT(*) FROM settlements_v2;   -- 0
SELECT * FROM settlements_v2 WHERE city='Karachi';   -- 0 rows
SELECT * FROM settlements_v2 WHERE settled_at >= date('now','-7 days');   -- 0 rows
```

5 positions are `economically_closed` (Munich/London/Miami/Wuhan/Singapore 5/18–5/19) — yet `settlements_v2` has zero rows.

Two possible interpretations:

1. **`settlements_v2` is fed from oracle finalization (UMA), and these markets have not yet finalized.** `economically_closed` ≠ "market resolved" — it means the position has been exited on Polymarket but the market itself is still un-arbitrated. PnL is realized at exit, settlement is bookkeeping for the bin.
2. **A writer to `settlements_v2` is broken.**

This run cannot disambiguate without inspecting the settlement-writer code path. Either way, two operational facts hold:

- **F110 SEV-2 OBS**: `settlements_v2.empty == True` means **the monitor_refresh `_check_persistence_anomaly` DEAD READ identified in Run #14/15 T2 is permanently 0** until at least one settlement is recorded. F48 Edits A–C are correct as specified but cannot be observationally validated end-to-end on this DB.
- **F111 SEV-3 SEMANTIC**: 5 `economically_closed` positions retain their original `shares` values (89.08 total) in `position_current`, while on-chain they hold 0 shares of those tokens. The `shares` column is preserved post-exit (presumably so realized PnL can recompute `exit_price × shares`). This is **by-design** but creates a category of probe-error: anyone querying `SELECT SUM(shares) FROM position_current WHERE phase != 'voided'` will overstate live exposure by 89 shares.

By-token reconciliation **WITHOUT** the closed-phase exclusion (mandate-style):

| Token (last 8) | DB shares (all non-voided) | On-chain | Drift |
|---|---:|---:|---:|
| …95705768 | 1.304337 | 1.304337 | 0 |
| …48357139 | 12.12 | 12.12 | 0 |
| …56811733 | 1.28 | 1.28 | 0 |
| …35888662 | 100.0 | 100.0 | 0 |
| …62050823 | 4.95 | 4.95 | 0 |
| …45757884 | 1.5873 | 1.5873 | 0 |
| …30054946 | 17.0 (incl. 5-share closed) | 6.0 | +11.0 |
| …35888662 (Wuhan closed) | 13.157891 | 0.0 | +13.157891 |
| …81914370 (Munich closed) | 23.7 | 0.0 | +23.7 |
| …10160782 (Miami closed) | 35.6 | 0.0 | +35.6 |
| …98113264 (Singapore closed) | 11.62 | 0.0 | +11.62 |

Total phantom drift if closed positions are not excluded: **+95.08 shares**.

---

## §8 — Findings catalog (F106–F111)

| F#  | Title | Sev | Status |
|-----|-------|-----|--------|
| **F106** | Schema mismatch: `position_lots.position_id` (INTEGER) ≠ `position_current.position_id` (TEXT UUID); `USING(position_id)` silently empty. Canonical join requires `position_lots.source_command_id → venue_commands.command_id → venue_commands.position_id` | **SEV-1 META** | **NEW (Run #16 T G)** |
| **F107** | 3 of 13 non-voided positions (incl. Karachi 5/17 `c30f28a5-d4e`) have zero `position_lots CONFIRMED_EXPOSURE` rows yet hold positive `cost_basis_usd` and `shares`. Dual-writer divergence between `position_current` and the lot materializer. Max drift $1.70, total un-lot-backed cost $3.33 | **SEV-1** | **NEW (Run #16 T G)** |
| **F108** | `venue_trade_facts` stores per-trade lifecycle revisions sharing `trade_id`; bare `SUM(filled_size)` over-counts by 1×–4×. Any monitor / report using this aggregate over-states filled exposure. Correct form: `SUM(MIN(filled_size) per (position, trade_id))` | **SEV-1** | **NEW (Run #16 T G)** |
| **F109** | Position duplicate `0a0e3b72-46e` and `7557a029-4ad` reference the same `token_id` (London 18°C 5/19 buy_yes), each claiming 6 shares × $0.31 = $1.86. On-chain has 6 shares total. DB over-books 6 shares / $1.86; realized PnL will phantom +$6 if WIN | **SEV-1** | **NEW (Run #16 T G)** |
| **F110** | `settlements_v2` table is EMPTY (0 rows) despite 5 `economically_closed` positions. Cannot disambiguate normal (markets not yet UMA-finalized) from broken settlement writer this run. Direct consequence: F48 fix verification cannot reach a non-zero positive case on current DB | **SEV-2 OBS** | **NEW (Run #16 T G)** |
| **F111** | `economically_closed` positions retain `position_current.shares > 0` post-exit while on-chain holds 0. By-design (preserves PnL recompute), but probes that filter only `phase != 'voided'` over-state live exposure by +95 shares | **SEV-3 SEM** | **NEW (Run #16 T G)** |

---

## §9 — Aggregate verdicts (executive summary)

| Mandate item | Verdict |
|---|---|
| Cost-basis drift count | **3 / 13 non-voided positions** |
| Cost-basis max-magnitude | **+$1.70 USD** (3a6f0728-c50) |
| Share drift count (lots vs current) | **3 / 13** (same 3 as cost) |
| Collateral consistency (chain pUSDC vs risk_state.initial_bankroll) | ✓ **MATCHES** ($189.05) |
| Open CTF reservations (`collateral_reservations` vs `reserved_tokens_for_sells_json`) | ✓ **MATCHES** (6,000,000 micro `CTF_SELL`) |
| Karachi 5/17 c30f28a5-d4e specific | ✓ cost reconciles on-chain × entry price; ✗ `position_lots` audit trail MISSING |
| Settlements PnL vs expected payouts | **N/A** — `settlements_v2` empty |
| On-chain shares vs DB shares (open phases only) | **6 of 7 tokens match**; 1 token over-booked by +6 shares (F109 London 5/19 duplicate) |
| F# opened | **F106–F111 (6 new)** |

---

## §10 — Cross-track impact

- **F48 (Run #14/15 T2 HOT-FIX-SPEC)**: F110 confirms `settlements_v2.rowcount=0` is the true current state. Run #15 T2's schema-qualified SELECT will read 0 rows correctly (no false-positive DEAD READ), but the F102 secondary blocker (`temp_persistence` empty) is the real obstacle to non-zero discount values.
- **Run #14 F87 / F90 / F48 reframing pattern (Run #16 A LEARNINGS §3)**: F106 is in the same category — a claim that "lots reconcile" is silently true when the join skeleton is wrong. The probe-then-claim rule applies; canonical join via `venue_commands` is now logged.
- **No production code or DB writes performed**. Worktree clean except for these new docs.

---

## §11 — Recommended follow-ups (NOT applied this run)

1. **F109 ops gate** (immediate, today): manually void one of `{0a0e3b72-46e, 7557a029-4ad}` before London 5/19 settles, OR confirm that downstream PnL deduplicates by `(token_id, market_id)`. Voiding is reversible if the system is in fact dedup-aware.
2. **F106 antibody** (next PR): add a `views/v_position_lots_canonical.sql` view that pre-joins via `venue_commands` and a pytest that asserts `USING(position_id)` between `position_lots` and `position_current` raises (or returns 0) — pinning the wrong join as a known-bad pattern.
3. **F108 antibody** (next PR): replace bare `SUM(filled_size)` with a `v_venue_trade_facts_latest` view keyed by `(trade_id, MAX(local_sequence))`. Add unit test that injects a multi-revision trade and asserts no over-count.
4. **F107 dual-writer investigation**: trace the code path that writes `position_current.cost_basis_usd` for `c30f28a5-d4e` / `3a6f0728-c50` / `8f02dc01-b6b` — which writer ran but did not materialize a lot. Likely candidate: optimistic-exposure path that promotes to `position_current` before `exchange_reconcile_entry_fill_materialization` fires.
5. **F111 semantic clarification**: rename `position_current.shares` → `position_current.shares_at_close` for closed phases, or add a `live_shares` computed view that returns 0 for `economically_closed`.
6. **F110 disambiguation**: small script `tools/ops/settlement_recorder.py --dry-run` to check whether the writer fires on UMA resolution events. Out of scope this run.

---

## §12 — Documents written this track

- `RUN_16_track_G_financial_reconciliation.md` (this file, NEW)
- Appends to `FINDINGS_REFERENCE_v2.md` (F106–F111 block)
- Appends to `AUDIT_HISTORY.md` (Run #16 T G row)
- Appends to `CONSOLIDATED_FINDINGS_DOSSIER.md` (Run #16 T G section)
