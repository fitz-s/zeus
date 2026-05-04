# Entries Block Registry — Design (2026-05-04)

## Why

3 days of "find the next gate, remove it, find another gate" = whack-a-mole. 13 gates spread across 5 categories, no single function answers "why are entries blocked right now?" The structural failure is **distributed authority over discovery short-circuit with zero registry**. Adding a new gate is cheap; finding all gates requires reading every file. This is Fitz Constraint #1 ("N bugs = K structural decisions, K << N") in its purest form.

## Scope (ONE PR)

**IN scope** (additive, low-regression-risk):
1. `src/control/entries_block_registry.py` — Block dataclass, 13 adapters, `enumerate_blocks()` / `blocking_blocks()` / `is_clear()` / `first_blocker()`
2. `tests/test_entries_block_registry.py` — adapter unit tests + integration via repro_antibodies fixtures
3. `scripts/zeus_blocks.py` — CLI: prints all blocks (CLEAR + BLOCKING) with file:line and reason
4. CI gate: `tests/test_no_unregistered_block_predicate.py` — grep test that fails if new boolean appears in `cycle_runner.py:752`-style short-circuit without registry registration
5. **Observational integration** at `cycle_runner.py:~752` — emit registry snapshot to `logger.info` + cycle JSON BEFORE the existing short-circuit fires. Existing L715-L751 branches stay unchanged (additive).

**OUT of scope** (deferred — too much regression surface for one PR):
- Wholesale refactor of L715-L751's 11 conditional branches into registry-driven short-circuit. That's the eventual goal, but it changes 11 production code paths — needs its own PR with full regression diff.
- Reducing the number of gates. Some are intentionally redundant (1 + 3 → 4). Registry exposes them; pruning is a separate decision.

## Block dataclass

```python
class BlockCategory(str, Enum):
    FILE_FAIL_CLOSED   = "file_fail_closed"      # gates 1, 2
    DB_CONTROL_PLANE   = "db_control_plane"      # gates 3, 4, 5
    RISKGUARD          = "riskguard"             # gates 6, 7, 8
    RUNTIME_HEALTH     = "runtime_health"        # gates 9, 10
    OPERATOR_ROLLOUT   = "operator_rollout"      # gates 11, 12, 13

class BlockStage(str, Enum):
    DISCOVERY = "discovery"   # blocks at cycle_runner.py:752 short-circuit
    EVALUATOR = "evaluator"   # blocks inside evaluator phase (gate 11 only, currently)

class BlockState(str, Enum):
    CLEAR    = "clear"
    BLOCKING = "blocking"
    UNKNOWN  = "unknown"      # adapter probe raised — fail-closed (treat as BLOCKING)

@dataclass(frozen=True)
class Block:
    id: int                        # 1-13, matches GATE_AUDIT.yaml
    name: str                      # stable kebab/snake_case identifier
    category: BlockCategory
    stage: BlockStage
    state: BlockState
    blocking_reason: Optional[str] # populated only when state == BLOCKING
    state_source: str              # human-readable: "file:state/auto_pause_failclosed.tombstone"
    source_file_line: str          # "src/control/control_plane.py:385" — citation that adapter probes
    owner_module: str              # "src.control.heartbeat_supervisor"
    owner_function: str            # "_write_failclosed_tombstone"
    raw_probe: Mapping[str, Any]   # debug payload — adapter-specific
    notes: str                     # short caveat (1 line)
```

## Registry interface

```python
class EntriesBlockRegistry:
    """Single source of truth for 'why are entries blocked right now?'.

    USAGE:
      registry = EntriesBlockRegistry.from_runtime(deps)   # bind to live deps
      blocks  = registry.enumerate_blocks(stage=BlockStage.DISCOVERY)  # all 10 gates at this stage
      blockers = registry.blocking_blocks(stage=BlockStage.DISCOVERY)  # only state==BLOCKING
      if not registry.is_clear(BlockStage.DISCOVERY):
          first = registry.first_blocker(BlockStage.DISCOVERY)
          ...
    """

    def __init__(self, adapters: Sequence[BlockAdapter]) -> None: ...

    @classmethod
    def from_runtime(cls, deps: RegistryDeps) -> "EntriesBlockRegistry":
        """Build registry with all 13 adapters, bound to live runtime deps."""

    def enumerate_blocks(self, stage: BlockStage | Literal["all"] = "all") -> list[Block]: ...
    def blocking_blocks(self, stage: BlockStage | Literal["all"] = "all") -> list[Block]: ...
    def is_clear(self, stage: BlockStage = BlockStage.DISCOVERY) -> bool: ...
    def first_blocker(self, stage: BlockStage) -> Optional[Block]:
        """Priority order: FILE_FAIL_CLOSED > DB_CONTROL_PLANE > RUNTIME_HEALTH > RISKGUARD > OPERATOR_ROLLOUT.
        Within category, smaller `id` wins. Used for setting entries_blocked_reason."""
```

## Adapter contract

```python
class BlockAdapter(Protocol):
    """Each adapter probes ONE gate and returns a Block."""

    id: int                      # matches GATE_AUDIT.yaml
    name: str
    category: BlockCategory
    stage: BlockStage
    source_file_line: str        # static — same as GATE_AUDIT.yaml entry

    def probe(self, deps: RegistryDeps) -> Block: ...
```

Adapter rules:
- **Pure read.** Adapters never write state. They only probe.
- **Fail-closed.** Adapter exception → Block(state=UNKNOWN, blocking_reason=f"adapter_error:{exc_class}"). Treated as BLOCKING by `is_clear()`.
- **Cheap.** Each probe must be < 50ms. Heavy reads (RiskGuard SQL scan) are cached at registry level via `enumerate_blocks` snapshot.
- **Single source.** One adapter per gate id. No fan-out probing inside one adapter.

## RegistryDeps

```python
@dataclass(frozen=True)
class RegistryDeps:
    state_dir: Path                                # PROJECT_ROOT/state — for file gates
    db_connection_factory: Callable[[], sqlite3.Connection]   # lazy DB conn
    risk_state_provider: RiskStateProvider          # for gate 6 (risk_level)
    riskguard_module: ModuleType                    # for gates 7, 8 (probe _trailing_loss_reference directly)
    heartbeat_module: ModuleType                    # for gate 9
    ws_gap_guard_module: ModuleType                 # for gate 10
    rollout_gate_module: ModuleType                 # for gate 11
    env: Mapping[str, str]                          # os.environ snapshot — for gate 13
```

`RegistryDeps.from_cycle_runner_runtime(...)` factory for the discovery short-circuit caller.

## 13 adapters (one file each in `src/control/block_adapters/`)

| id | adapter file | gate name | stage |
|----|--------------|-----------|-------|
| 1  | `fail_closed_tombstone.py` | auto_pause_failclosed_tombstone | DISCOVERY |
| 2  | `auto_pause_streak.py` | auto_pause_streak_escalation | DISCOVERY |
| 3  | `db_control_overrides.py` | control_overrides_history_entries_gate | DISCOVERY |
| 4  | `entries_paused_flag.py` | entries_paused_in_memory_flag | DISCOVERY |
| 5  | `entries_blocked_reason.py` | entries_blocked_reason_string | DISCOVERY |
| 6  | `risk_level.py` | risk_allows_new_entries_predicate | DISCOVERY |
| 7  | `trailing_loss_reference.py` | trailing_loss_reference_limit100_scan | DISCOVERY |
| 8  | `bankroll_truth_source.py` | bankroll_truth_source_polymarket_wallet_filter | DISCOVERY |
| 9  | `heartbeat_health.py` | heartbeat_supervisor_allow_submit | DISCOVERY |
| 10 | `ws_gap_guard.py` | ws_gap_guard_allow_submit | DISCOVERY |
| 11 | `evaluator_rollout_gate.py` | evaluate_entry_forecast_rollout_gate | **EVALUATOR** |
| 12 | `promotion_evidence_file.py` | entry_forecast_promotion_evidence_file | EVALUATOR (informational; subset of 11) |
| 13 | `rollout_gate_env_var.py` | ZEUS_ENTRY_FORECAST_ROLLOUT_GATE_env_var | EVALUATOR (config; informational — never BLOCKING by itself) |

Notes:
- **Gates 4, 5, 12, 13 are derived/informational.** They probe state that other gates already determine. Registry exposes them anyway so `zeus blocks` is complete and operators can see the full picture.
- **Gate 11 is the only true EVALUATOR-stage gate.** The rest fire pre-discovery. This is why the registry has a `stage` field rather than collapsing to one list.

## Observational integration at cycle_runner.py

Insertion point: immediately before `if _risk_allows_new_entries(risk_level) and not entries_paused and entries_blocked_reason is None:` at L752.

```python
# Registry snapshot — observational, no behavior change.
# Replaces "read 13 files to know why entries are blocked" with one call.
_block_registry = EntriesBlockRegistry.from_cycle_runner_runtime(
    state_dir=PROJECT_ROOT / "state",
    deps=deps,
    risk_level=risk_level,
    entries_paused=entries_paused,
    entries_blocked_reason=entries_blocked_reason,
)
_block_snapshot = _block_registry.enumerate_blocks(stage="all")
deps.logger.info(
    "ENTRIES_BLOCK_REGISTRY_SNAPSHOT cycle=%s blocking=%d total=%d",
    cycle.cycle_id,
    sum(1 for b in _block_snapshot if b.state == BlockState.BLOCKING),
    len(_block_snapshot),
)
# Emit per-block detail at DEBUG (info-level cycle output gets its summary written into cycle JSON below).

# Existing short-circuit unchanged:
if _risk_allows_new_entries(risk_level) and not entries_paused and entries_blocked_reason is None:
    ...
```

The cycle JSON `summary` dict gains a new field:
```python
summary["block_registry"] = [b.to_dict() for b in _block_snapshot]
```

This means every cycle's output now contains the full 13-gate state — no more "I need to grep 5 files to find the blocker."

## CI gate (anti-drift)

`tests/test_no_unregistered_block_predicate.py`:
1. Read `src/engine/cycle_runner.py` source.
2. Locate the function `discover_cycle_opportunities`.
3. Find the line containing `# REGISTRY-GUARDED SHORT-CIRCUIT` marker.
4. Parse the boolean expression on the next line via `ast`.
5. For each `Name`/`Attribute` node referenced, assert it appears in a known-allowlist OR maps to a registered adapter id.
6. Test fails with a message: `"new boolean '<expr>' added to discovery short-circuit but not registered in EntriesBlockRegistry. See REGISTRY_DESIGN.md."`

This is a stage-1 antibody. Stage-2 (next PR) is to make the boolean expression itself BE `registry.is_clear()`, so the antibody becomes vacuous-by-construction.

## Acceptance gate

After implementation:

1. Unit tests pass: each adapter probed against synthetic state returns expected Block.
2. `scripts/zeus_blocks.py` invoked on the running daemon (post-restart) prints 13 rows. Currently expected:
   - Gates 1-10: CLEAR (we just unblocked them)
   - Gate 11: BLOCKING with reason `ENTRY_FORECAST_PROMOTION_EVIDENCE_MISSING`
   - Gates 12-13: informational (12 BLOCKING, 13 CLEAR with value=1)
3. `repro_antibodies.py` extended: inject each gate type → registry returns the expected blocking Block.
4. Daemon natural cycle log shows `ENTRIES_BLOCK_REGISTRY_SNAPSHOT cycle=<id> blocking=N total=13`.
5. Cycle JSON contains `block_registry` field with 13 entries.

## Migration phases

**This PR (Phase 1 — observational):** registry is read-only side-channel. Existing L715-L751 logic unchanged. CI gate prevents new gates from being added without registry registration.

**Next PR (Phase 2 — discovery short-circuit refactor):** L715-L751's 11 branches replaced with `registry.first_blocker(BlockStage.DISCOVERY)`. Reduces 37-line block to 3 lines. Requires full regression diff against pre-refactor behavior.

**Later PR (Phase 3 — evaluator integration):** Wire gate 11's evaluator-phase block through the same registry surface so cycle JSON shows it in the same place. Currently it's only in evaluator output.

## File layout

```
src/control/
├── entries_block_registry.py          # Block, Registry, BlockState, BlockStage, BlockCategory
└── block_adapters/
    ├── __init__.py                     # exports all 13
    ├── _base.py                        # BlockAdapter Protocol + RegistryDeps
    ├── fail_closed_tombstone.py        # gate 1
    ├── auto_pause_streak.py            # gate 2
    ├── db_control_overrides.py         # gate 3
    ├── entries_paused_flag.py          # gate 4
    ├── entries_blocked_reason.py       # gate 5
    ├── risk_level.py                   # gate 6
    ├── trailing_loss_reference.py      # gate 7
    ├── bankroll_truth_source.py        # gate 8
    ├── heartbeat_health.py             # gate 9
    ├── ws_gap_guard.py                 # gate 10
    ├── evaluator_rollout_gate.py       # gate 11
    ├── promotion_evidence_file.py      # gate 12
    └── rollout_gate_env_var.py         # gate 13

scripts/zeus_blocks.py                  # CLI: prints all blocks

tests/
├── test_entries_block_registry.py      # registry unit + adapter unit
└── test_no_unregistered_block_predicate.py   # CI anti-drift gate
```

## Risk

- **Performance.** 13 probes per cycle × 50ms = 650ms worst case. Cycles run every 15min, so overhead is negligible. Heavy probes (RiskGuard SQL scan) cached at registry level.
- **Adapter staleness.** Adapter file:line citations rot. Mitigation: CI gate also re-validates `source_file_line` exists by `grep -c` at test time (stage-1 antibody — full AST validation in next PR).
- **Fail-closed UNKNOWN.** Adapter exception in production cycle could falsely flag CLEAR gates as BLOCKING via `is_clear()`. Initial PR is observational only — `is_clear()` is consulted nowhere in the discovery short-circuit yet, so this is not a runtime risk in Phase 1.

## Authority

- `docs/operations/task_2026-05-04_live_block_root_cause/GATE_AUDIT.yaml` — file:line for all 13 gates (audit timestamp 2026-05-04)
- `docs/operations/task_2026-05-04_live_block_root_cause/ROOT_CAUSE.md` — original 5-SF analysis
- `docs/operations/task_2026-05-04_live_block_root_cause/PR_A_BODY.md` — PR-A + SF6 + SF7 antibodies (now superseded by combined PR body)
- Fitz Constraint #1 (`/Users/leofitz/.claude/CLAUDE.md`): N bugs = K structural decisions, K << N
