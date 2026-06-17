# Single belief authority + position-lifecycle integrity (2026-06-12)

Authority basis: settlement-losses incident 2026-06-12 (HK 30°C / Karachi 37°C /
KL 33°C, all BUY NO on the winning bin) + external deep consult
REQ-20260612-052802 (ChatGPT Pro; gist 2a3d4d5c4fa80388c7735ffd13bc5ad3) +
root-cause reports /tmp/exit_retry_loop_rootcause.md and
/tmp/hk_fill_registration_rootcause.md. Operator directive: "找到最佳策略开始
进行完整实现" — fix the organs, restart live, manage the partially-filled
Beijing 06-14 position on correct logic.

## The K-decisions

K1 — SINGLE BELIEF AUTHORITY (landed fc30c46c04). The exit monitor's
probability comes from the replacement-chain posterior (`forecast_posteriors`),
the SAME authority entry used. Evidence: 719/719 monitor refreshes of the
Karachi position had `last_monitor_prob_is_fresh=False` (its legacy source
tables are 0 rows since inception) while the entry posterior had re-ranked the
held bin to family top 18h before settlement. New module
`src/engine/position_belief.py`; primary-authority seam in
`monitor_probability_refresh`; belief-dead watchdog (BELIEF_AUTHORITY_FAULT at
3 consecutive stale cycles with fresh market price).

K3 — POSITION LIFECYCLE INTEGRITY (this commit):
1. Fill-orphan recovery lane: a venue fill whose WS_USER CONFIRMED message was
   lost (user-channel dropout, HK 30°C 06-12: 3h gap 18:24Z→22:19Z) exists only
   as a REST trade fact and could NEVER reach FILL_CONFIRMED — position never
   materialised, P&L never booked. Recovery = UserTradeObserved under the
   already-legal RECONCILE_SOURCE authority with a mandatory provenance payload
   (REST fact + terminal venue-command state + grace window).
   `append_reconcile_recovered_fill` (live_order_reconcile.py),
   `append_rest_filled_orphan_trade_facts_to_edli` (edli_trade_fact_bridge.py),
   wired flag-gated (`edli_rest_filled_bridge_enabled`, default ON — it only
   ADDS registration of venue-confirmed fills, never weakens a gate).
2. Exit-retry persistence: `exit_retry_count` / `next_exit_retry_at` lived only
   on the in-memory Position; every load_portfolio() reset them to 0, so the
   MAX_EXIT_RETRIES → backoff_exhausted terminal was unreachable and
   exit_pending_missing positions retried forever (HK 06-09: 724 identical
   EXIT_ORDER_REJECTED events). Persisted: position_current columns (additive
   ALTER in ledger.py), canonical tuple (projection.py), projection builder
   (lifecycle_events.py), loader view (db.py), from-row loader (portfolio.py).
   Schema fingerprint re-pinned (intentional migration).
3. Chain-truth gate activation: the gate (the DESIGNED resolution for
   exit_pending_missing — on-chain CTF balance probe) was permanently bypassed
   because POLYMARKET_FUNDER_ADDRESS was never in the daemon env. The gate now
   resolves the funder from the same Keychain authority PolymarketClient uses
   (`resolve_funder_address`, address-only accessor, never the private key);
   env vars remain an explicit override. NOTE: the recoverable-state in-memory
   close stays NOT persisted by design (standing antibody
   test_recoverable_exit_pending_missing_does_not_persist_admin_close) — the
   loop terminates through the chain gate's bounded, now-persisted backoff.

## Verification
- tests/engine/test_position_belief_authority.py (16) — K1.
- tests/events/test_edli_trade_fact_bridge.py (7, incl. 4 new orphan-recovery
  antibodies: past-grace recovery with provenance, in-grace deferral, WS-truth
  precedence, non-terminal-command refusal).
- tests/test_exit_retry_persistence.py (3) — projection carries retry state,
  DB round-trip, legacy-row default.
- Broad sweep tests/events+execution+state+lifecycle: 1214 passed; all 32
  observed failures verified pre-existing on pristine HEAD via stash-swap.

## Live verification plan (post-restart)
- MONITOR_REFRESHED payloads show prob_is_fresh=True,
  selected_method=replacement_posterior on Denver 06-12 + Beijing 06-14
  (partially filled 5sh@0.73 11:44Z) positions.
- HK 30°C 06-12 orphan recovers via the rest-filled bridge on the next
  user-channel reconcile cycle and books its settlement loss.
- HK 06-09 / Paris 06-12 exit_pending_missing positions resolve through the
  chain-truth gate (balance 0 → void with chain evidence) instead of looping.
