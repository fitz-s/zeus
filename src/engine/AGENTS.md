# src/engine AGENTS

Engine is Zeus's orchestration layer. It coordinates the live cycle from data
load through evaluation, monitoring, and replay, but it must not redefine
truth, lifecycle law, or source semantics by sequencing shortcuts.

## Read this before editing

- Module book: `docs/reference/modules/engine.md`
- Machine registry: `architecture/module_manifest.yaml`
- Runtime law: `docs/authority/zeus_current_architecture.md`,
  `architecture/invariants.yaml`, `architecture/task_boot_profiles.yaml`

## Top hazards

- engine sequencing can collapse exit, settlement, and monitoring into one
  wrong truth plane
- Day0/monitor paths are source-sensitive and must respect current source facts
- replay and live may diverge in I/O, not in semantic law
- orchestration shortcuts can bypass lifecycle, riskguard, or execution rules

## Canonical truth surfaces

- `cycle_runner.py`
- `evaluator.py`
- `monitor_refresh.py`
- `replay.py`
- `lifecycle_events.py`

## High-risk files

| File | Role |
|------|------|
| `cycle_runner.py` | top orchestration hub |
| `evaluator.py` | decision synthesis and gating |
| `cycle_runtime.py` | runtime sequencing helpers |
| `lifecycle_events.py` | lifecycle event bridging |
| `monitor_refresh.py` | monitor and Day0 refresh logic |
| `replay.py` | replay parity and diagnostic path |
| `time_context.py` | date/lead-time semantics |

## Required tests

- `tests/test_day0_exit_gate.py`
- `tests/test_day0_runtime_observation_context.py`
- `tests/test_day0_window.py`
- `tests/test_cross_module_invariants.py`
- `tests/test_cross_module_relationships.py`
- `tests/test_bug100_k1_k2_structural.py`

## Do not assume

- engine is safe because it is "just orchestration"
- current monitor data can stand in for settlement truth
- replay can drift from live semantics as long as interfaces still fit
- lifecycle law can be patched locally inside engine

## Planning lock

Any change to cycle sequencing, monitor/Day0 flow, replay parity, or
cross-module actuation order requires a packet and planning-lock evidence.
