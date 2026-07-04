# Chain-mirror position state model

Created: 2026-07-04
Authority basis: operator directive 2026-07-04 (verbatim intent: "the local
position book must PERFECTLY MIRROR on-chain state; any divergence is a bug").
Reconciliation authority order per root `AGENTS.md` §2: **Chain > Chronicler >
Portfolio**. This document makes that order executable for `position_current`.

## 1. Measured divergence (evidence anchor)

Chain snapshot 2026-07-04 21:20 UTC (`data-api get_positions`, 38 rows):
almost every row is a settled, graded-losing weather token
(`redeemable=True`, `currentValue=$0.00`) plus exactly 2 live non-weather
positions on the shared proxy wallet (`$ANTH` ticker market, "Mythos-Class
Model" market — operator co-trading, foreign to Zeus; see `memory:
shared-wallet-operator-cotrading`).

Local `zeus_trades.db` `position_current` claimed 40 `quarantined` rows at the
same instant, including:
- 18 rows claiming "won, redeemable $236.53" whose tokens are **absent** from
  the chain snapshot entirely (already redeemed by the standing third-party
  auto-redeemer that sweeps every settled winner off the shared wallet — see
  `src/execution/exchange_reconcile.py::_absorb_terminal_chain_closed_phantom`
  for the analogous, already-shipped absorber on the *drift-suppression*
  layer; this document's reconciler is upstream of that layer because these
  rows are not yet terminal locally).
- A Dallas row claiming `chain_shares=1184.57` against a chain size of
  `74.55` for the same asset id (size mismatch, not a phantom).
- One `day0_window` position (`ce105753-e91`, Manila 33°C July 4 buy_no,
  `chain_state='synced'`, `chain_shares=11.1`) whose NO token does not appear
  on chain at all, and whose market has **not** resolved — ambiguous; not
  safe to auto-close (could be `data-api` lag).

The root cause is structural, not a one-off counting bug: `quarantined` is a
**terminal** phase in `src/state/lifecycle_manager.py::LEGAL_LIFECYCLE_FOLDS`
(`QUARANTINED: frozenset({QUARANTINED})`) and the harvester's own settlement
sweep (`src/execution/harvester.py::_settle_positions`) explicitly **skips**
any position whose runtime state is `quarantined` ("runtime state still
non-terminal for settlement" — despite quarantine itself being terminal, no
writer ever moves a quarantined row to `settled`/`voided`). A row that enters
quarantine has no legal, automated path out. This document's reconciler adds
that path **without** touching the systemic `LEGAL_LIFECYCLE_FOLDS` /
`is_terminal_state` law (see §5, "scope decision").

## 2. Market-rule state model

There are only a few *real* facts a position can be in, each derived purely
from `(market resolution status) × (wallet token balance for the held
token)`. Every legacy `phase`/`chain_state` combination must map onto exactly
one of these. None of them is named "quarantined" — that word describes an
*investigation status*, never a market state, and must not survive past one
reconcile cycle once the investigation's evidence (chain truth) is available.

| Market-rule state | Definition | `position_current.phase` | `chain_state` |
|---|---|---|---|
| **OPEN** | Market still live; held token present on chain with expected size. | `active` / `day0_window` / `pending_exit` | `synced` |
| **REDEEMABLE** | Market resolved, held token is the winner, tokens still sitting in the wallet (third-party redeemer has not swept yet). | `settled` | `synced` (chain evidence: still present) |
| **CLOSED_REDEEMED** | Market resolved, held token was the winner, tokens are **absent** from the wallet (redeemed by the third-party auto-redeemer — Zeus never submits `redeemPositions`, per `memory: redeem-abandoned-third-party`). | `settled` | `closed_redeemed` (new — §4) |
| **CLOSED_WORTHLESS** | Market resolved, held token lost. Value is (and always will be) zero, whether the losing tokens are still dust in the wallet or already gone. | `settled` | `closed_worthless` (new — §4) |
| **CLOSED_EXITED** | Zeus itself sold the position pre-resolution (economic close). Already correctly modeled; no repair needed. | `economically_closed` | unchanged |
| **FOREIGN** | Token has no Zeus origin (no `venue_command`/`venue_trade_fact` row references it) on the shared proxy wallet. Never adopted, never counted in Zeus exposure; listed only as a report line. | — (no local row) | — |
| **REVIEW (ambiguous)** | Local row claims an open phase, its held token is absent from the current chain snapshot, but the market has **not** resolved. Could be a `data-api` lag, not proof of loss. Surfaced as a finding; never auto-closed. | unchanged | unchanged |

## 3. Legacy → market-rule mapping + repair rule

| Legacy `phase` / `chain_state` | Market-rule equivalent | Repair rule |
|---|---|---|
| `quarantined` (any `chain_state`) + chain token absent + market resolved + graded winner | `CLOSED_REDEEMED` | Reconciler class (a): append `SETTLED` event (won=true), phase→`settled`, `chain_state='closed_redeemed'`. |
| `quarantined` (any `chain_state`) + chain token absent + market resolved + graded loser | `CLOSED_WORTHLESS` | Reconciler class (a): append `SETTLED` event (won=false), phase→`settled`, `chain_state='closed_worthless'`. |
| `quarantined` / any open phase + chain token present but wrong size | size-mismatch correction (state unchanged) | Reconciler class (b): append `CHAIN_SIZE_CORRECTED` event, `chain_shares` corrected to chain truth. |
| `entry_authority_quarantined` (chain_state) + chain token present, market resolved, size matches | `REDEEMABLE` or `CLOSED_REDEEMED`/`CLOSED_WORTHLESS` per grading | Same as class (a); entry-authority ambiguity is moot once the market has settled — there is nothing left to enter. |
| `chain_confirmed_zero` (chain_state) on a still-open local phase | Already means "chain proved balance zero"; if market resolved, treat as class (a); if market open, treat as class (e) REVIEW. | No new writer needed — this value already exists and already means the correct evidence; the gap was that nothing consumed it into a terminal phase. |
| `chain_absent_confirmed_position_unattributed` (chain_state) | Same treatment as `chain_confirmed_zero` — chain proves absence; grade if resolved, REVIEW if not. | Same as above. |
| Open phase (`active`/`day0_window`/`pending_exit`) + chain token absent + market NOT resolved | `REVIEW` (ambiguous — the Manila `ce105753-e91` case) | Reconciler class (e): finding only, **no write**. Operator or a second read (CLOB balance endpoint) must confirm before any close. |
| Chain token present with Zeus origin (venue_command/venue_trade_fact) but no local `position_current` row | Missing local tracking | Reconciler class (c): finding only, **no row created** (auto-creating a position the entry pipeline never opened is itself a fabrication risk). |
| Chain token present with **no** Zeus origin | `FOREIGN` | Reconciler class (d): report-only line; never adopted, never counted in Zeus exposure. |

## 4. New vocabulary added (minimal, additive)

- `src/contracts/semantic_types.py::ChainState` gains two members:
  `CLOSED_REDEEMED = "closed_redeemed"` and
  `CLOSED_WORTHLESS = "closed_worthless"`. `chain_state` is a bare `TEXT`
  column (no SQL `CHECK` constraint on `position_current.chain_state`), but
  `Position.__post_init__` coerces it through `VenueVisibilityStatus(value)`
  — an unregistered value crashes every `load_portfolio()` call (the exact
  incident class documented next to `CHAIN_CONFIRMED_ZERO` in that file).
  Adding the two members is therefore required, not optional, before the
  reconciler can write them.
- **No new `position_events.event_type` literal.** `event_type` **is**
  `CHECK`-constrained in `position_current`'s sibling table and widening a
  `CHECK` requires a full table-rebuild migration
  (`src/state/db.py::_migrate_readiness_state_status_checks` is the shape of
  that migration elsewhere in this codebase). That blast radius (a live
  money-path table, rewritten while daemons hold open connections) is out of
  proportion to this task. Instead the reconciler reuses the **existing**
  literals that already carry the exact right semantics:
  - `SETTLED` for class (a) grading closes — identical semantics to
    `src/execution/harvester.py::_dual_write_canonical_settlement_if_available`,
    which already always writes `phase_after=SETTLED` regardless of
    won/lost; the win/loss distinction lives in the event payload
    (`won`, `outcome`, `settlement_value`), exactly mirrored here.
  - `CHAIN_SIZE_CORRECTED` for class (b) — already exists for precisely
    this purpose (`src/state/projection.py::_CHAIN_PROJECTION_EVENT_TYPES`
    special-cases it to preserve monitor-snapshot columns across the write).
  - Class (c)/(d)/(e) are report-only; no `position_events` row is written
    for them (nothing local changed).
  - The new evidence this reconciler introduces (chain-absence proof,
    third-party-redeem inference, size-correction deltas) is carried inside
    each event's existing free-form `payload_json`, tagged
    `"reconciler": "chain_mirror"` and
    `"chain_mirror_classification": "closed_redeemed" | "closed_worthless" |
    "size_corrected"`. These classification strings are registered in
    `architecture/money_path_objects.yaml` under
    `chain_mirror_reconciliation_classification` (mirrors the existing
    `command_recovery_reason` entry shape) so the money-path semantic-diff
    gate has a home for them.

## 5. Scope decision: the standing invariant vs. `LEGAL_LIFECYCLE_FOLDS`

The clean systemic fix would widen
`LEGAL_LIFECYCLE_FOLDS[LifecyclePhase.QUARANTINED]` from
`frozenset({QUARANTINED})` to `frozenset({QUARANTINED, SETTLED, VOIDED})` (the
same shape as the existing `ECONOMICALLY_CLOSED` fold), which would make
`is_terminal_state("quarantined")` correctly return `False` everywhere.
That single line touches:

- `TERMINAL_STATES` (programmatically derived — used by `portfolio.py`,
  `cycle_runner.py`, `chain_state.py`, `harvester.py`,
  `observability/status_summary.py`, `projection.py`'s
  `_ABSORBING_POSITION_PHASES`, and more, all money-path).
- `src/state/lifecycle_manager.py::enter_settled_runtime_state` and
  `enter_voided_runtime_state`, which independently hard-reject a
  `quarantined` starting phase (`raise ValueError`) before ever consulting
  `fold_lifecycle_phase` — both would need a new explicit allowed-starting-
  state branch.
- `tests/test_lifecycle_terminal_predicate.py`, which pins "exactly 4
  terminal phases including quarantined" as a law antibody.

This is a legitimate, surgical change in isolation, but its consumers span
the live entry/exit money path this task is explicitly forbidden from
broadening into (`riskguard.py` just merged; calibration/event-reactor
surfaces are owned by a different concurrent agent). Per the task's own
escalation clause, this slice takes the documented off-ramp:

**The reconciler writes directly through the existing, narrower
reconciliation precedent already shipped in this codebase** —
`src/execution/exchange_reconcile.py::_tag_external_operator_closed_position_holdings`
performs exactly this shape of out-of-band terminal correction (a scoped,
explicitly-WHERE-clause-guarded `UPDATE position_current` + a matching
`position_events` append), bypassing `enter_settled_runtime_state`/
`enter_voided_runtime_state` for the same class of problem (a reconciliation
process correcting a stuck/incorrect local state against proven chain
truth, not a normal in-flight lifecycle transition). The chain-mirror
reconciler in this slice uses `src.state.db.append_many_and_project` (the
same append-only event + upsert-projection primitive the canonical
settlement path uses) rather than a bare `UPDATE`, which is a strictly
*stronger* guarantee than the existing precedent it follows — event/
projection consistency is validated by
`src/state/projection.py::validate_event_projection_batch` on every write,
and `upsert_position_current`'s "absorbing phase" reopen guard
(`_ABSORBING_POSITION_PHASES`) does not block this direction of travel: it
only refuses reopening an *already*-absorbed row back into a *non*-absorbing
phase, never the reverse. **No fold-legality bypass is exercised**, because
`fold_lifecycle_phase` is never invoked by this write path (it is a helper
consumed only by the `pending_exit`-family `transition_phase`/`enter_*`
functions, not by `append_many_and_project` itself).

**Follow-up (explicitly not done in this slice):** widen
`LEGAL_LIFECYCLE_FOLDS[QUARANTINED]` and the two `enter_*_runtime_state`
guards so `quarantined → settled/voided` is legal at every layer, and update
`tests/test_lifecycle_terminal_predicate.py` accordingly. That is a
dedicated, reviewed slice of its own (it changes a program-wide invariant
consumed by ~10 modules); doing it inside this reconciliation task would
either (a) silently swap the a systemic invariant define an operator hasn't
explicitly reviewed, or (b) be done hastily under this task's own time
budget with insufficient blast-radius verification. The **net effect**
required by the operator — "no row stays quarantined past one reconcile
cycle" — is still achieved: the recurring reconcile job (§6) reclassifies
*every* `position_current` row via chain truth on every cycle regardless of
its current `phase`, so a `quarantined` row with a gradable chain outcome
(the overwhelming majority observed in the divergence snapshot) is drained
into `settled` within one cycle. Only the genuinely ambiguous residual
(§2 REVIEW state — chain-absent, market still open) is left un-auto-closed,
by explicit operator instruction ("do NOT auto-close").

## 6. Reconciler + standing invariant

- `scripts/reconcile_chain_mirror.py` — dry-run-default CLI. Reads the full
  wallet position set via `PolymarketClient.get_positions_from_api()` (the
  same call that produced the divergence snapshot in §1 — read-only `GET
  /positions` against the venue's data-api, no CLOB/order construction, no
  signing), every `position_current` row via `get_trade_connection`, and
  (read-only, separate connection) `settlement_outcomes` via
  `get_forecasts_connection(write_class=None)`. Classifies every row/token
  per §2/§3, prints a JSON report, and — only with `--apply` — writes
  classes (a) and (b) through `append_many_and_project`. Classes (c)/(d)/(e)
  are never written, `--apply` or not.
- `src/state/chain_mirror_reconciler.py` — the pure classification core
  (dataclasses + functions), imported by both the script and the scheduler
  job, and unit-tested independent of any network/adapter dependency.
- A recurring scheduler job (`chain_mirror_reconcile`, `src/main.py`) runs
  the same diff on a fixed cadence and auto-applies classes (a)/(b) with
  full provenance; classes (c)/(e) are logged as findings (not persisted as
  new tables in this slice — visible in the job's log line and the JSON
  report artifact). This is the "no row stays quarantined past one cycle"
  backstop for the 2026-07-04 divergence's actual shape (chain-gradable
  settlement, not open-market ambiguity).

## 7. Non-goals of this slice

- Redeeming anything. The reconciler and the scheduler job never construct
  or submit a `redeemPositions` transaction — third-party redemption is by
  design (`memory: redeem-abandoned-third-party`).
- Adopting foreign (non-Zeus-origin) tokens into Zeus exposure accounting.
- Auto-closing the open-but-absent REVIEW class.
- Rewiring every existing writer of `phase='quarantined'` /
  `chain_state='entry_authority_quarantined'`
  (`src/state/chain_reconciliation.py`, `src/execution/command_recovery.py`)
  to stop using the word — the standing reconcile job neutralizes the
  *consequence* (rows do not stay stuck) without yet removing the
  *label* at every writer. Tracked as follow-up in §5.
