# src/state AGENTS — Zone K0/K1 (Truth Ownership)

Module book: `docs/reference/modules/state.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

State owns **canonical truth** — the single source of what Zeus's positions are,
what has happened to them, and what on-chain reality says. If you corrupt
append-first discipline, lifecycle grammar, or reconciliation semantics, Zeus
either hallucinates positions (trading on phantom state) or fails to reconcile
with on-chain reality (missing real fills or voiding live positions).

The truth hierarchy is absolute:
`Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)`

Everything downstream — JSON exports, status summaries, reports — is derived.
Derived surfaces may never become truth by being convenient.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `db.py` | Canonical DB write/query substrate | CRITICAL — all truth flows through here |
| `lifecycle_manager.py` | Sole lifecycle transition authority | CRITICAL — only legal phase transitions |
| `ledger.py` | Append-only event spine | CRITICAL — canonical event history |
| `projection.py` | Deterministic projection fold from events | CRITICAL — current state from events |
| `chain_reconciliation.py` | On-chain truth convergence | HIGH — Chain > local |
| `collateral_ledger.py` | pUSD/CTF collateral snapshot + reservations | HIGH — live pre-submit fail-closed truth |
| `venue_command_repo.py` | Durable venue command/event journal | HIGH — command state drives reservation release |
| `job_run_repo.py` | Data-daemon job-run journal | HIGH — scheduler provenance and missed-window truth |
| `source_run_repo.py` | Source-run provenance journal | HIGH — release/completeness truth for data feeds |
| `readiness_repo.py` | Scoped readiness verdict store | CRITICAL — missing/expired readiness must fail closed |
| `market_topology_repo.py` | Market topology authority state | HIGH — venue/source contract freshness |
| `portfolio.py` | Runtime position read model | HIGH — what evaluator/executor see |
| `portfolio_loader_policy.py` | DB-vs-fallback load discipline | HIGH — truth source selection |
| `decision_chain.py` | Point-in-time decision lineage | MEDIUM |

## Domain rules

- **Append-first discipline is load-bearing.** The canonical write path is:
  1. append domain event to `position_events`
  2. fold deterministic projection to `position_current`
  3. event append and projection update in one transaction boundary
  
  Separating steps 1 and 2 into different transactions creates torn state.

- **Lifecycle phases come only from `LifecyclePhase` enum.** The 9 legal
  phases are: `pending_entry → active → day0_window → pending_exit →
  economically_closed → settled`. Terminal: `voided`, `quarantined`,
  `admin_closed`. No code may invent phase strings.

- **Exit intent ≠ economic close ≠ settlement.** These are three separate
  lifecycle events with different semantic meaning:
  - EXIT_INTENT: the decision to exit (phase stays current until order posts)
  - EXIT_ORDER_FILLED: economically closed (P&L locked)
  - SETTLED: market resolution confirmed (final truth)

- **Void requires known absence, not unknown chain status.** If the chain
  status is unknown/stale, you cannot void a local position — it might exist
  on-chain. Only known absence (chain confirmed "not present") permits void.

- **DB commits must precede JSON export writes.** The canonical truth is the
  DB. JSON/status files are derived exports that must trail, never lead.

- **Collateral is asset-specific.** `collateral_ledger.py` treats pUSD as BUY collateral and CTF outcome tokens as SELL inventory; never let pUSD balance satisfy a sell preflight.

- **`strategy_key` is the sole governance identity.** All position attribution,
  risk policy resolution, and performance slicing flows through `strategy_key`.
  Do not invent parallel governance keys.

## Common mistakes

- Setting `phase = "holding"` or similar string literals instead of routing
  through `lifecycle_manager.apply_transition()` → violates INV-01/INV-08
- Separating `INSERT INTO position_events` from `UPDATE position_current` into
  separate transactions → torn state if process crashes between them
- Treating `positions-live.json` as canonical truth instead of querying
  `position_current` from DB → JSON can be stale or partially written
- Collapsing unknown chain status into "empty" → premature void of a position
  that actually exists on-chain (the most expensive possible error)
- "Small schema tweak" that actually changes truth ownership, transaction
  boundaries, or projection semantics → this is always architecture work
- Reading `portfolio.py` cache after a write without going through the
  proper refresh path → stale read model

## Required tests

- `tests/test_db.py`
- `tests/test_dt1_commit_ordering.py`
- `tests/test_dt1_savepoint_integration.py`
- `tests/test_dt4_chain_three_state.py`
- `tests/test_b070_control_overrides_history_v2.py`
- `tests/test_chronicle_dedup.py`
- `tests/test_cross_module_invariants.py`
- `tests/test_job_run_schema.py`
- `tests/test_source_run_schema.py`
- `tests/test_readiness_state_schema.py`
- `tests/test_market_topology_state_schema.py`

## Planning lock

Any schema, truth-owner, projection, reconciliation, or lifecycle write-path
change under `src/state/**` requires an approved packet and planning-lock
evidence.
