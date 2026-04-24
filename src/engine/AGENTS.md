# src/engine AGENTS — Zone K2 (Orchestration)

Module book: `docs/reference/modules/engine.md`
Machine registry: `architecture/module_manifest.yaml`

## WHY this zone matters

Engine is the **live cycle orchestrator** — it sequences data fetch → chain
reconciliation → evaluation → monitoring → exit → settlement harvest every
cycle. Engine coordinates the Money Path stages but **must not redefine truth,
bypass lifecycle law, or collapse distinct truth planes into orchestration
shortcuts.**

The critical danger: engine touches every stage of the pipeline in sequence,
which makes it the easiest place to accidentally short-circuit semantic
boundaries. A performance optimization in sequencing can silently collapse
settlement truth with monitoring truth, or skip reconciliation before
evaluation.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `cycle_runner.py` | Top-level live cycle orchestration hub | CRITICAL — sequences the entire money path |
| `evaluator.py` | Signal → calibration → fusion → edge → FDR → sizing → decision | CRITICAL — where trading decisions happen |
| `monitor_refresh.py` | Monitor and Day0 refresh for held positions | HIGH — Day0 truth plane |
| `lifecycle_events.py` | Lifecycle event bridging between engine and state | HIGH — event emission |
| `replay.py` | Replay/diagnostic path (must maintain parity with live) | HIGH — semantic parity |
| `cycle_runtime.py` | Runtime sequencing helpers | MEDIUM |
| `time_context.py` | Date/lead-time semantics | MEDIUM — timezone sensitivity |

## Domain rules

- **Discovery modes are cycle parameters, not separate runtimes.**
  `opening_hunt`, `update_reaction`, and `day0_capture` share one
  `cycle_runner` with different market discovery filters. They do not
  have separate orchestration paths or separate lifecycle rules.

- **Reconciliation runs before evaluation every cycle.** The chain/local
  convergence check (SYNCED/VOID/QUARANTINE) must complete before the
  evaluator makes new trading decisions. Skipping or reordering this
  creates stale position state.

- **Monitor observation is Day0 truth, not settlement truth.** When
  `monitor_refresh` reads current temperature for held positions, it
  uses Day0 monitoring sources. These approximate settlement risk but
  are not the final settlement observation. Do not treat Day0 readings
  as settlement fact.

- **Replay must maintain semantic parity with live.** Replay may differ
  in I/O (reading from DB vs API) but must not differ in semantic law
  (lifecycle transitions, risk rules, FDR computation, sizing). If
  replay drifts from live semantics, diagnostic output is meaningless.

- **Engine reads risk policy but does not compute it.** RiskGuard emits
  policy; engine/evaluator consumes it. Engine must not embed risk
  computation logic that duplicates or contradicts `src/riskguard/`.

- **Evaluator is signal + decision, not truth writer.** Evaluator
  computes trading decisions but does not directly write canonical
  lifecycle state. Decisions flow through lifecycle_events → state.

## Common mistakes

- Sequencing that collapses exit, settlement, and monitoring into one truth
  plane check → the three are semantically distinct (architecture law §4)
- Treating Day0 monitor data as settlement-grade observation → systematic
  mispricing during settlement window
- Locally patching lifecycle transitions inside engine code instead of
  routing through `lifecycle_manager` → INV-01 violation
- Replay path drifting from live semantic law "because it's just diagnostics"
  → replay becomes useless for parity verification
- Skipping chain reconciliation for "speed" → evaluator sees phantom positions
  that no longer exist on-chain
- Engine bypass of risk check between reconciliation and evaluation → trades
  through a RED risk level

## Required tests

- `tests/test_day0_exit_gate.py`
- `tests/test_day0_runtime_observation_context.py`
- `tests/test_day0_window.py`
- `tests/test_cross_module_invariants.py`
- `tests/test_cross_module_relationships.py`
- `tests/test_bug100_k1_k2_structural.py`

## Planning lock

Any change to cycle sequencing, monitor/Day0 flow, replay parity, or
cross-module actuation order requires a packet and planning-lock evidence.
