# src/engine AGENTS — Zone K3 (Math/Data) + K2 (Execution coordination)

## WHY this zone matters

Engine is the **orchestration layer** — it coordinates the full trading cycle from data fetch through signal generation, calibration, strategy, and execution. The cycle runner is where all the pieces connect.

Critical invariant: **engine may coordinate work, but may not redefine truth**. The lifecycle manager (K0) is the sole state authority. Engine calls into it — never around it.

## Key files

| File | What it does | Danger level |
|------|-------------|--------------|
| `cycle_runner.py` | Full trading cycle orchestration — fetch→signal→strategy→execute | CRITICAL — coordinates everything |
| `evaluator.py` | Signal → strategy → sizing pipeline for each candidate | CRITICAL — core decision logic |
| `cycle_runtime.py` | Heavy runtime helpers extracted from cycle_runner (keeps orchestrator clean) | HIGH |
| `lifecycle_events.py` | Lifecycle event recording — settlement, phase transitions | HIGH — state mutations |
| `monitor_refresh.py` | Position monitoring — stale detection, exit signal refresh | HIGH |
| `replay.py` | Decision replay engine — audit, counterfactual, walk-forward modes | MEDIUM |
| `time_context.py` | Lead-time helpers — timezone-aware target date calculations | MEDIUM |
| `discovery_mode.py` | Discovery mode enum (opening_hunt, update_reaction, day0_capture) | LOW |
| `process_lock.py` | Process-level lock to prevent double-daemon launches (fcntl.flock) | LOW |

## Domain rules

- Exit is not local close — engine must emit `EXIT_INTENT`, not directly close positions (INV-01)
- Settlement is not exit — these are separate lifecycle events (INV-02)
- No direct lifecycle terminalization from orchestration
- No ad hoc phase reassignment — only `LifecyclePhase` enum values (INV-08)
- No silent write-path bypass around canonical truth evolution

## Common mistakes

- Letting monitor/executor code act as the lifecycle law → should call lifecycle_manager
- Depending on deprecated portfolio authority as if it were final-state canonical
- Patching around missing kernel work by inventing new local state
- Performing "local close" on exit decisions instead of expressing exit intent → INV-01 violation
- Skipping chain reconciliation before trading in live mode → truth divergence
