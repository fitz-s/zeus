# F109 — Non-idempotent position-open (systemic, not isolated to London 5/19)

**Discovered**: 2026-05-17 via Track G reconciliation analysis (RUN_16_track_G_financial_reconciliation.md §F109) + orchestrator direct probe.

**Status**: ESCALATED to operator. STRUCTURAL fix required (per `feedback_no_manual_precedent_for_any_structural_defect`); operator-void of duplicates is FORBIDDEN.

## Live evidence (probe 2026-05-17T19:30Z)

### F109 anchor case — London 18°C 2026-05-19 (token_id 113959433546428599583458171463964346033318046435676830124564125503733330054946)

Five `position_current` rows for ONE token:

| position_id | phase | shares | cost_basis_usd |
|---|---|---|---|
| cee5fc85-3dd | voided | 0.0 | 0.0 |
| 2d08b0ec-b2e | voided | 0.0 | 0.0 |
| 3a6f0728-c50 | economically_closed | 5.0 | $1.70 |
| **0a0e3b72-46e** | **pending_exit** | **6.0** | **$1.86** |
| **7557a029-4ad** | **pending_exit** | **6.0** | **$1.86** |

Two active `pending_exit` rows × 6 shares = **12 shares accounted**; on-chain holds 6 shares total. DB over-books by 6 shares / $1.86. **Phantom +$6 PnL if WIN; +$0 on settle (since on-chain shares pay only $6, db expects $12)**.

### Beyond F109 — same pattern across 5+ tokens (probe sample)

| token_id | row_count | position_ids |
|---|---|---|
| 103133...811733 | 5 | f42386fb,bd72ddc4,48a4863d,1c9b8c57,6d8abfb4 |
| 113571...561208 | 4 | 1ba2320d,502acf0f,1f792e8a,d3941108 |
| 113959...054946 | 5 | (London 5/19 above) |
| 14452...248630 | 6 | 4518b333,e9bd54b3,fda13369,579b968a,bfce71ec,f089a9e8 |
| 19355...525094 | 3 | 7fbf9cf2,759c88a3,1bbb697b |

**Verdict**: F109 is the painful symptom; the underlying defect is **systemic non-idempotent position-open** at write-side.

## Why this is NOT operator-fixable per directive

Operator-voiding `7557a029-4ad` OR `0a0e3b72-46e` (or any duplicate row) would:
- Set a precedent that DB-vs-chain drift gets hand-patched
- Mask the structural defect that allowed the duplicate-write in the first place
- Per `feedback_no_manual_precedent_for_any_structural_defect`: **forbidden**

## Structural fix shape (NOT yet dispatched)

**K decision**: position-open writer must be idempotent on `(token_id, market_id, strategy)` AT THE INSERT SITE.

Proposed structural fix:
1. **DB invariant**: add `UNIQUE INDEX ux_position_current_open_per_token ON position_current(token_id) WHERE phase IN ('day0_window','pending_entry','active','pending_exit')`
2. **Writer-side idempotency**: position-open helper checks for an existing active row before INSERT; if found, returns the existing position_id instead of creating a new one
3. **Replay for existing duplicates**: programmatic consolidator that, for each token with multiple active rows, merges them by (a) summing shares/cost if both reference the SAME chain transaction; (b) keeping the first-written row and voiding subsequent if they're true write-races
4. **Antibody**: invariant test asserting `at most 1 (active|pending_*) row per token_id` after every position-open call

## Karachi 5/17 safety

Karachi position `c30f28a5-d4e` has only ONE row (`day0_window`, 1.5873 shares). Not affected by F109. The Karachi-bridge fix (already shipped this PR) handles its trade_decisions gap separately.

## London 5/19 timeline

- **Today**: 2026-05-17
- **London 18°C 5/19 settlement**: ~2 days
- **Risk window**: structural fix MUST land + replay consolidator MUST resolve the duplicate before settlement → otherwise phantom $6 PnL

## Operator decisions required

1. **Approve dispatching a tracer + structural fix executor** for F109 (similar to Karachi-bridge packet: TRIGGER + writer-idempotency + replay consolidator + invariant test)
2. **Decide on packet timing**: ship within WAVE-2 PR, or split into WAVE-3 with a hot-fix branch? London 5/19 in 2 days argues for hot-fix track.
3. **Confirm prohibition of operator-void** for the existing duplicate (zero tolerance per memory)
