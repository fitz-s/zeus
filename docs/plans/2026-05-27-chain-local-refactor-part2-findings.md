# Chain/Local Position Model Refactor — Part 2 Audit (2026-05-27)

> Verdict on PR #347: meaningful improvement, NOT a complete first-principles
> object-model simplification. Five deeper findings remain. Source: user
> review delivered 2026-05-27 after PR #347 fix commit ab4cc0e835.

## Material progress from PR #347

| Area | PR #347 status | Verdict |
|------|----------------|---------|
| chain_verified_at positive-only | absence branches write `last_chain_absence_observed_at` | direction correct; absence field NOT durable in projection |
| chain-only fake Position | reconcile + loader emit `ChainOnlyFact` | reduced; review lifecycle/timeout not yet canonical event |
| Loader synthetic Position | `_chain_only_quarantine_position_from_row` deleted | correct; `save_portfolio` does not persist `chain_only_facts` to JSON sidecar |
| get_open_positions(chain_view) | now pure | one direct mutation authority inversion fixed |
| size_mismatch illegal state | enum-legal | runtime-only mutation; no durable review write |
| Rescue as fill | added `fill_authority="venue_position_observed"` | still sets `entry_fill_verified=True`, `order_status="filled"`, active phase, mutates economics — core object model NOT eliminated |
| ChainState name collision | aliases added | alias-only; both class names still exist |
| Training gate | `is_training_eligible_position` exists | policy-only; not wired into harvester money path |
| `position_current` | unchanged | missing `fill_authority`, `entry_fill_verified`, `chain_shares`, `chain_verified_at`, `last_chain_absence_observed_at`, `entered_at` — the core projection gap |

## Deep findings remaining

### Finding D0 (P1) — Balance-only rescue still becomes active + fill-verified; downgraded authority is not durable

Runtime rescue branch (no linked venue trade fact path) still:
- sets `entry_fill_verified = True`
- sets `order_status = "filled"`
- mutates `entry_price/cost_basis_usd/size_usd/shares` from chain aggregate
- transitions pending → active runtime state
- emits canonical `CHAIN_SYNCED` (NOT `VENUE_POSITION_OBSERVED`)
- payload omits `fill_authority` / `recovery_authority`
- `build_position_current_projection()` omits `fill_authority`, `entry_fill_verified`, `chain_shares`, `chain_verified_at`, `last_chain_absence_observed_at`

**Failure mode after restart:** loader reconstructs active projection without the downgraded authority. Downstream monitor/exit/learning over-trust the position.

**Fix:** durable projection for `fill_authority` + `recovery_authority` + chain observation fields; balance-only rescue emits `VENUE_POSITION_OBSERVED` event; do NOT set `entry_fill_verified=True` for balance-only path.

### Finding D-Quarantine (P1) — Size mismatch fallback enum-legal but no durable review event

PR C1 fixed the enum vocabulary (`LifecycleState.QUARANTINED.value`). But when canonical baseline missing, `_append_canonical_size_correction_if_available()` returns False and the fallback only mutates runtime fields — no `REVIEW_REQUIRED` durable fact written.

**Failure mode:** restart loses the unresolved mismatch; loader sees stale `position_current`.

**Fix:** `record_position_review_required(...)` durable write in the no-baseline branch.

### Finding D-Chain-Only (P1/P2) — `ChainOnlyFact` has no lifecycle/expiry/review fold

PR C2/E2 replaced fake Position with `ChainOnlyFact` and durable suppression rows. But `check_quarantine_timeouts()` still iterates `portfolio.positions` only — no 48h timeout/escalation fold for `chain_only_facts`. README says "48h forced exit eval" but no consumer implements it for the new carrier.

**Failure mode:** chain-only inventory can block entries indefinitely unless operator clears manually.

**Fix:** durable review status on token suppression rows (`unresolved | acknowledged | expired | resolved`); timeout fold; loader returns status.

### Finding B2 (P2) — `ChainState` name collision not eliminated; aliases preserve ambiguity

`ChainSnapshotCompleteness = ChainState` and `VenueVisibilityStatus = ChainState` are aliases only. Two `ChainState` Python classes still exist. Production code imports `ChainState`, not the domain-specific names.

**Failure mode:** future patch imports wrong `ChainState`; branch miss in reconcile/void/quarantine.

**Fix:** rename CLASSES (not just add aliases); ban bare `ChainState` imports in production via static test.

### Finding D2-Wire (P2) — Training eligibility policy-only; not wired into writer seam

`is_training_eligible_position()` + `TRAINING_ELIGIBLE_FILL_AUTHORITIES` exist as policy boundary. But `harvester.maybe_write_learning_pair()` is snapshot-keyed — no per-position context — so the gate is bypassed in practice.

**Fix:** `VerifiedTrainingExample.from_position(...)` typed input; writer accepts only the typed value; static test bans direct `add_calibration_pair_v2` from untyped callsites.

## PR sequencing (per audit §7)

| PR | Scope | Risk |
|----|-------|------|
| A2 | Runtime invariant tests (DB reload), not static scanners | low |
| **D0** | **Durable authority projection + `VENUE_POSITION_OBSERVED` rescue event split** | **medium — schema migration + behavior change** |
| D1 | `ChainOnlyFact` review lifecycle (status/expiry/timeout) | medium |
| B2 | Real type rename + production-import ban | low |
| D2 | Training writer typed input (`VerifiedTrainingExample`) | medium — depends on D0 |

## Deletion list (after preconditions)

- `QUARANTINE_SENTINEL` — when no consumer references placeholder semantics
- `Position.is_quarantine_placeholder` — when ChainOnlyFact fully replaces placeholders
- Bare `ChainState` imports — when production migrates to domain-specific names
- `entry_fill_verified` as rescue authority — when `has_verified_trade_fill` / `has_tradable_exposure` split complete
- Rescue `CHAIN_SYNCED` for balance-only — when `VENUE_POSITION_OBSERVED` wired + projected
- Runtime-only size mismatch quarantine — when durable review fact path exists
- Static source-shape tests as primary proof — when runtime integration tests prove invariants
- `LEARNING_AUTHORITY_REQUIRED` scanner — when `VerifiedTrainingExample` writer boundary enforced
- `Position` module doc "source of truth" — when Position truly projection-only

## Decision: scope of follow-up

PR D0 is the smallest high-confidence next slice. New branch off PR #347 head
(`claude/chain-local-refactor-d0`) so D0 can rebase onto main after #347
merges, OR ship as a follow-up commit on the same branch if reviewer
prefers a single growing PR.
