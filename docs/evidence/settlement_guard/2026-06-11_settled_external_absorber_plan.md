# Plan evidence: settled-class external-close absorber (exchange_reconcile)

Created: 2026-06-11 ~10:35Z. Authority basis: operator redeem-abandonment directive
2026-06-10 (third-party auto-redeem is standing policy; Zeus duties = accounting +
Confirm-pending-deposit) + RULE 1 (submit latch closed 11h on a settled swept winner).

## Incident
Finding 6a477c8d (position_drift, HK 06-09 high "31°C" NO ×19, opened 06-10T23:00:59Z):
journal long 19, venue position 0 — the operator's standing third-party auto-redeemer
swept the settled position off the shared wallet. No resolution path matched:
- token_suppression door closed: the harvester used to register settled winners
  ('settled_position') but that duty DIED with the abandoned redeem subsystem.
- operator-ack external-close absorber (57c441049d) requires a per-token ack row.
Result: M5 WS-gap reconcile kept the submit latch closed → zero orders.

## Design failure (K=1)
"Suppress settled winners" was coupled to redeem SUBMISSION. Redeem abandoned ⇒ every
settled win swept externally becomes a permanent latch-closing drift. The duty must be
re-homed in the reconciler, redeem-free.

## Change (this edit set)
src/execution/exchange_reconcile.py ONLY — accounting/registry surface; NO on-chain
mutation, NO venue submission, NO redeem path touched:
1. `_market_calendar_terminal_evidence(token_ids, observed_at)` — batch, read-only,
   short-lived `mode=ro` connection to the canonical registry (zeus-forecasts
   market_events) + city timezone from src.config. Terminal iff the market's target
   LOCAL day ended ≥ 24h ago. Fail-closed per token and per pass.
2. New resolver branch in `_resolve_position_drift_tokens_from_current_truth` (before
   the operator-ack absorber): terminal-by-calendar AND venue size 0 AND confirmed
   journal long > 0 AND no open sell locks ⇒ `record_token_suppression`
   (reason='settled_position', source_module='exchange_reconcile.settled_external_absorber',
   full market evidence) + resolve finding as
   `position_drift_settled_external_suppressed`. The EXISTING suppression door then
   keeps it resolved on every future sweep.

## Invariants preserved
- Honest-gate: a NON-terminal disappearance (possible theft/bug) still requires the
  per-token operator-ack path — unchanged.
- Money truth: no synthetic exit price is invented; P&L settlement accounting stays with
  the settlement organs + Confirm-pending-deposit (USDC arrival check).
- INV-37: no cross-DB write; the registry read is a separate short read-only connection
  (three-phase contract: never held across other I/O).
- Append-only: record_token_suppression writes history + legacy upsert via the
  sanctioned writer with its reason allowlist ('settled_position' already allowed).

## Antibody
tests/execution/test_settled_external_absorber.py — terminal+swept ⇒ suppressed+resolved;
non-terminal ⇒ untouched (operator-ack still required); registry-unavailable ⇒ fail-closed.
