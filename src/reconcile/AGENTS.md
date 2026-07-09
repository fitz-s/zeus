# src/reconcile AGENTS — R2-core (recovery/reconcile rebuild)

Module book: none yet (rebuild-in-flight; design lives in docs/rebuild/)
Machine registry: `architecture/module_manifest.yaml`
Design authority: `docs/rebuild/EXECUTION_MASTER_2026-07-07.md` §E R2-a/R2-b + §C + §E2,
`docs/rebuild/whole_system_first_principles_2026-07-07.md` §2.4 + §7.1

## WHY this zone matters

`src/reconcile` is the target-form replacement for the 31-pass recovery mountain in
`src/execution/command_recovery.py` (16.6K lines) and parts of `src/execution/
exchange_reconcile.py`: ONE local-truth snapshot contract + ONE chain-truth snapshot
contract → a diff engine with a small (150-300 line) predicate table of REAL venue
behaviors → corrective events. `src/state/chain_mirror_reconciler.py` (1033 lines) is
the target-form template this package follows.

**Inert by design (R2-core).** Nothing outside tests and the replay harness calls
`diff_engine.reconcile()` yet. Wiring it into a live cycle — migrating the 31 legacy
passes onto this engine, each gated on certificate-native replay evidence against the
legacy pass it replaces — is R2-c, the next wave. No promotion flag exists here because
nothing calls this package at all (§C6 no-shadow-modes axiom: a promotion flag without a
caller is exactly the kind of shadow machinery the axiom forbids).

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `local_truth.py` | `LocalTruthSnapshot`: what Zeus itself believes (venue_commands + position_current + collateral_reservations), ONE SQL shape. Subsumes three pre-existing competing "local truth" definitions (see module docstring's file:line survey). | MEDIUM — contract surface |
| `chain_truth.py` | `ChainTruthSnapshot`: what the outside world says happened (on-chain positions + settlement resolutions + venue order/trade fact stream), deduped via `src.state.fill_dedup.canonical_trade_fact_cte` — the ONLY dedup primitive, never copied. | MEDIUM — contract surface |
| `diff_engine.py` | `classify(local, chain) -> findings`, the 4-5-predicate table, `apply_corrective_event`, `reconcile()` runner. Per-row isolation from birth (a raising command/position never aborts the pass). | HIGH — outcome-deciding, future money-path writer once R2-c wires it live |
| `replay.py` | Certificate/event-native replay harness: replays a historical window from persisted facts (read-only, no network) and compares diff-engine findings against what legacy passes actually appended. The R2-c acceptance tool. | LOW — read-only |

## Domain rules

- **Local truth vs chain truth.** local_truth is Zeus's own derived belief (command state
  machine + position projection + reservation lifecycle); chain_truth is independent
  ground evidence Zeus observed FROM the venue/chain. The diff engine exists to find where
  the two diverge — never conflate the two snapshot contracts.
- **The predicate table holds ONLY real venue behaviors** (§7.1: 4-5 of them, ~150-300
  lines), each with a docstring naming the specific venue behavior it handles and citing
  the legacy evidence for that behavior. Do NOT grow this into a second command_recovery.py
  by re-adding self-inflicted-scar passes (§7.1 names three scar classes explicitly
  excluded: EDLI↔venue_commands dual-ledger sync, multi-writer projection drift, and
  sibling-module bug patches already fixed elsewhere).
- **BUILD-INTO-TARGET.** This package is a clean-namespace target component. Legacy files
  (`command_recovery.py`, `exchange_reconcile.py`) may only be touched for deletion or a
  single-line seam edit — never patched with new reconcile logic that belongs here instead.
- **Money-path caution on `apply_corrective_event`.** Only writes=True findings get a
  corrective-event body; in R2-core only `RESERVATION_ORPHANED_FILL_AFTER_RELEASE` has one,
  and it appends an append-only evidence marker (never mutates `collateral_reservations`
  balances directly) — see `diff_engine.apply_corrective_event`'s docstring for why.
