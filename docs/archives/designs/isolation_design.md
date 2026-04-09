# Paper/Live Isolation: Structural Design

**Author:** isolation-architect  
**Date:** 2026-04-07  
**Status:** PROPOSAL (rev 2 u2014 updated with dual-daemon discovery analysis)  
**Problem:** Two independent daemons sharing signal infrastructure create parallel phantom positions in the same markets

---

## Root Cause (Revised)

The contamination is NOT an env-filter bug. Both paper and live daemons:
1. Share `zeus.db` for signals, calibration, and market discovery
2. Independently call `execute_discovery_phase()` on the same Polymarket market feed
3. Independently evaluate the same `MarketCandidate` objects through `evaluate_candidate()`
4. Independently pass `check_position_allowed()` which only checks the LOCAL portfolio
5. Independently enter the same city/target_date/bin market

Paper writes to `positions-paper.json`, live writes to `positions-live.json`. Both entries are legitimate within their own mode. But when trade data flows through `zeus.db` (chronicle, position_events, trade_decisions), cross-contamination occurs because env filtering is convention-based.

This is two problems:
- **P1 (data isolation):** Trade data in zeus.db can leak across modes via missing/NULL env filters
- **P2 (discovery ownership):** No mechanism prevents both daemons from entering the same market

---

## Current State

### How isolation works today

1. **Per-process state files** (`mode_state_path()` in `config.py`): `positions-paper.json` / `positions-live.json`. Physical separation. Works for position state.
2. **Shared database** (`zeus.db`): env column on 4 tables (`trade_decisions`, `chronicle`, `decision_log`, `position_events`). Runtime `WHERE env = ?` filtering. Fails.
3. **position_current** (projection table): has `env` column, but `upsert_position_current()` backfills NULL env to `"paper"` as a hotfix rather than rejecting it structurally.
4. **Process lock** (`process_lock.py`): Prevents two daemons of the SAME mode. Does NOT prevent paper + live running simultaneously.
5. **Discovery pipeline** (`cycle_runtime.py:execute_discovery_phase`): Both daemons iterate the same `markets` list, construct the same `MarketCandidate` objects, and evaluate independently. No cross-mode awareness.
6. **Risk limits** (`risk_limits.py:check_position_allowed`): Only checks local portfolio heat/city/cluster exposure. Cannot see the other mode's positions.

### Why the current approach fails

The env column is a **convention**, not a **constraint**. Every new table, every new query, every new function must remember to:
- Include `env` in INSERTs
- Filter by `env` in SELECTs
- Default to `settings.mode` when env is None

But even if env filtering were perfect, it wouldn't prevent P2: both daemons independently discovering and entering the same market. `check_position_allowed()` checks the local mode's portfolio, not the other mode's.

This is the `if unit == "C"` pattern from CLAUDE.md. 40+ call sites already do `env = getattr(pos, "env", None) or settings.mode` or `query_env = settings.mode if env is None else env`. Each is an opportunity for the bug to recur.

### Tables with env column (current)
| Table | env column | NOT NULL | CHECK constraint |
|-------|-----------|----------|------------------|
| position_events | CREATE TABLE | YES | NO |
| chronicle | ALTER TABLE (backfill) | YES (default 'paper') | NO |
| trade_decisions | ALTER TABLE (backfill) | YES (default 'paper') | NO |
| decision_log | ALTER TABLE (backfill) | YES (default 'paper') | NO |
| position_current | projection | YES (runtime backfill) | NO |

### Shared world data (NOT env-tagged, correctly shared)
- `settlements` u2014 market outcomes
- `forecast_skill` u2014 ENS calibration
- `platt_models` u2014 calibration models
- `replay_results` u2014 replay analysis

---

## Option Evaluation

### Option A: Physical Database Separation

**Design:** `zeus-paper.db`, `zeus-live.db`, `zeus-shared.db`

| Criterion | Assessment |
|-----------|------------|
| Contamination | **UNCONSTRUCTABLE** u2014 different files, OS-level isolation |
| Lines of code | ~150-200 (connection factory, migration script, path wiring) |
| Migration path | SQLite dump/restore per table; one-time script |
| Test breakage | Moderate u2014 any test that calls `get_connection()` needs to specify which DB |
| New table risk | Zero u2014 new trade tables go in mode-specific DB by default |

**Strengths:**
- Strongest isolation guarantee. Cross-contamination requires opening the wrong file.
- `get_connection(mode)` returns the right DB. No SQL rewriting needed.
- Shared data (settlements, calibration) stays in one place u2014 no duplication.
- Matches the pattern that ALREADY WORKS: `mode_state_path()` for JSON files.

**Weaknesses:**
- Three database files instead of one.
- Cross-DB queries (e.g., join position_events with settlements) require ATTACH or application-level join.
- Migration is a one-time cost but non-trivial.

### Option B: Typed Connection Wrapper

**Design:** `ModeConnection` that auto-injects `WHERE env=?` on reads, `env` value on writes.

| Criterion | Assessment |
|-----------|------------|
| Contamination | **DETECTABLE** at best u2014 SQL rewriting is fragile, subqueries/CTEs break |
| Lines of code | ~300+ (SQL parser, edge cases, testing) |
| Migration path | Wrap existing `get_connection()` u2014 low migration cost |
| Test breakage | High u2014 any raw SQL that doesn't match the parser pattern fails silently |
| New table risk | Medium u2014 depends on SQL pattern matching correctness |

**Verdict: REJECT.** SQL rewriting is a leaky abstraction. Zeus uses complex queries with CTEs, subqueries, and dynamic table names (`_legacy_position_events_table()`). A regex/AST SQL rewriter would need to handle all of these correctly. This trades one class of bugs (missing env filter) for another (incorrect SQL rewriting).

### Option C: Typed Position ID

**Design:** `PositionId(trade_id, env)` replaces bare `str` trade IDs everywhere.

| Criterion | Assessment |
|-----------|------------|
| Contamination | **Partially unconstructable** u2014 untagged IDs are a TypeError |
| Lines of code | ~500+ (every function signature, every call site) |
| Migration path | Invasive u2014 touches every module |
| Test breakage | Very high u2014 every test that uses trade_id strings breaks |
| New table risk | Medium u2014 helps for position lookups, doesn't help for other tables |

**Verdict: REJECT as primary solution.** The typed ID makes untagged *positions* unconstructable but doesn't prevent a new table (e.g., `order_audit_log`) from being created without env. The isolation boundary should be at the *connection* level, not the *data* level. However, `PositionId` is a good complementary measure.

### Option D: Schema-Level Enforcement

**Design:** `NOT NULL CHECK(env IN ('paper','live'))` on all env columns + mode-scoped views.

| Criterion | Assessment |
|-----------|------------|
| Contamination | **DETECTABLE** u2014 NULL env is a schema error, but wrong-mode reads still possible |
| Lines of code | ~100 (ALTER TABLE + CHECK constraints + views) |
| Migration path | Simple u2014 ALTER TABLE for CHECK, CREATE VIEW for filtered access |
| Test breakage | Low u2014 views are additive |
| New table risk | Medium u2014 must remember to add CHECK + view for each new table |

**Verdict: NECESSARY BUT INSUFFICIENT.** CHECK constraints prevent NULL env (good), but every query must still remember to use the view instead of the table. A developer writing `SELECT * FROM position_events` instead of `SELECT * FROM position_events_paper` gets cross-mode data.

### Option E: Physical Separation + Schema Enforcement (RECOMMENDED)

**Design:** Combine Option A (physical separation for trade data) with Option D (CHECK constraints as defense-in-depth).

---

## Recommendation: Option A (Physical DB Separation)

### Why this is the Zeus-native answer

Zeus already solved this problem for state files:
```python
def mode_state_path(filename: str, mode: Optional[str] = None) -> Path:
    # positions.json u2192 positions-paper.json / positions-live.json
    return STATE_DIR / f"{stem}-{mode}{ext}"
```

This works perfectly. Zero contamination incidents from state files. The database should follow the same pattern.

### Architecture

```
state/
  zeus-shared.db      # settlements, forecast_skill, platt_models, replay_results
  zeus-paper.db       # trade_decisions, chronicle, decision_log, position_events, position_current
  zeus-live.db        # trade_decisions, chronicle, decision_log, position_events, position_current
  risk_state-paper.db # (already separated via mode_state_path)
  risk_state-live.db  # (already separated via mode_state_path)
```

### Connection factory

```python
# New: mode-aware connection factory
def get_trade_connection(mode: str | None = None) -> sqlite3.Connection:
    """Connection for trade-facing tables. Mode-isolated."""
    mode = mode or os.environ.get("ZEUS_MODE", settings.mode)
    db_path = STATE_DIR / f"zeus-{mode}.db"
    return _connect(db_path)

def get_shared_connection() -> sqlite3.Connection:
    """Connection for shared world data (settlements, calibration)."""
    return _connect(STATE_DIR / "zeus-shared.db")

# Deprecated: remove after migration
def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Legacy. Prefer get_trade_connection() or get_shared_connection()."""
    ...
```

### What makes contamination unconstructable

1. **No shared file** u2014 paper daemon opens `zeus-paper.db`, live daemon opens `zeus-live.db`. There is no file that contains both modes' trade data.
2. **No env column needed** u2014 The env column becomes redundant in trade DBs (kept for provenance, but not relied upon for isolation). A query cannot return wrong-mode data because the data doesn't exist in that file.
3. **New table safety** u2014 When a developer creates a new trade-facing table, it goes in the mode-specific DB by default. No env column to forget.
4. **Shared data stays shared** u2014 Settlements, calibration, and forecast skill are genuinely shared across modes. They belong in `zeus-shared.db`.

### Migration plan

**Phase 1: Schema + factory (non-breaking)**
1. Add `get_trade_connection()` and `get_shared_connection()` to `db.py`
2. Create `zeus-shared.db` with shared tables
3. Create `zeus-paper.db` and `zeus-live.db` with trade tables
4. Add `CHECK(env IN ('paper','live'))` to all env columns (defense-in-depth)
5. `get_connection()` still works, returns legacy `zeus.db`

**Phase 2: Migrate call sites (incremental)**
1. Grep all `get_connection()` calls (~40 sites)
2. Classify each as trade-facing or shared
3. Replace with `get_trade_connection()` or `get_shared_connection()`
4. Each replacement is independently testable

**Phase 3: Data migration (one-time)**
1. Script: copy trade rows from `zeus.db` to `zeus-{mode}.db` based on env column
2. Script: copy shared rows from `zeus.db` to `zeus-shared.db`
3. Verify row counts match
4. Rename `zeus.db` to `zeus-legacy.db` (keep as backup)

**Phase 4: Remove legacy path**
1. Delete `get_connection()` fallback
2. Remove env column from WHERE clauses (no longer needed for isolation)
3. Keep env column in schema for provenance/audit

### Table classification

| Table | DB | Rationale |
|-------|-----|----------|
| `trade_decisions` | mode-specific | Trade data, env-tagged |
| `chronicle` | mode-specific | Trade lifecycle events |
| `decision_log` | mode-specific | Per-cycle decision artifacts |
| `position_events` | mode-specific | Position lifecycle spine |
| `position_current` | mode-specific | Position projection |
| `outcome_fact` | mode-specific | Settlement outcomes per trade |
| `settlements` | shared | Market outcomes (not mode-specific) |
| `forecast_skill` | shared | ENS calibration |
| `platt_models` | shared | Calibration models |
| `replay_results` | shared | Replay analysis |
| `control_overrides` | shared | Admin overrides apply to both modes |

### Lines of code estimate

| Component | Lines | Risk |
|-----------|-------|------|
| Connection factory (`db.py`) | ~30 | Low |
| Schema init for split DBs | ~50 | Low |
| Migration script | ~80 | Medium (one-time) |
| Call site updates (~40 sites) | ~80 (mostly s/get_connection/get_trade_connection/) | Low per-site |
| Test updates | ~60 | Low |
| **Total** | **~300** | |

### Cross-DB query handling

The main concern with physical separation is cross-DB joins. Analysis of current queries:

1. **Position + settlement joins** u2014 occur in `query_settled_positions()` and riskguard. Solution: `ATTACH DATABASE 'zeus-shared.db' AS shared` at connection time, then `shared.settlements` in queries. SQLite supports this natively.
2. **Portfolio loader** u2014 reads only `position_current`. Pure trade-facing. No cross-DB needed.
3. **Decision chain** u2014 reads `trade_decisions`, `decision_log`, `position_events`. All trade-facing. No cross-DB needed.
4. **Chronicler** u2014 writes to `chronicle`. Pure trade-facing.

ATTACH is the standard SQLite pattern for this. It's well-tested and adds no complexity to the query itself.

### Why NOT the other options

| Option | Fatal flaw |
|--------|------------|
| B (SQL rewriting) | Fragile with CTEs, dynamic table names, subqueries. Trades one bug class for another. |
| C (Typed PositionId) | Only protects position lookups, not arbitrary table access. Massive refactor for partial coverage. |
| D alone (CHECK + views) | Makes NULL env impossible but wrong-mode reads still require developer discipline. |

### Analogy to the temperature fix

| Temperature | Paper/Live |
|-------------|------------|
| Problem: bare `float` for temps, no unit tracking | Problem: bare `sqlite3.Connection`, no mode tracking |
| Fix: `Bin.unit` typed field | Fix: `get_trade_connection(mode)` typed factory |
| Makes °C/°F mixing a TypeError | Makes cross-mode access a FileNotFoundError |
| Unit travels with the data | Mode travels with the connection |

---

## Decision

**Option A: Physical database separation**, with CHECK constraints as defense-in-depth.

This is the only option where contamination is **unconstructable** u2014 the data physically cannot exist in the wrong file. Every other option relies on developer discipline at N call sites, which is exactly the pattern that produced the current bug.

---

## P2: Discovery Ownership (The Deeper Problem)

Physical DB separation solves P1 (data isolation) but NOT P2 (duplicate market entry). Even with separate databases, both daemons will independently discover the same Atlanta HDD market, evaluate the same edge, and enter independently.

### Option Analysis for Discovery Ownership

#### Option P2-A: Market-Level Lock Table

**Design:** Shared `zeus-shared.db` gets a `market_claims` table:
```sql
CREATE TABLE market_claims (
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    bin_label TEXT NOT NULL,
    direction TEXT NOT NULL,
    claimed_by TEXT NOT NULL CHECK(claimed_by IN ('paper', 'live')),
    claimed_at TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    PRIMARY KEY (city, target_date, bin_label, direction)
);
```

Before entering, `execute_discovery_phase()` checks:
```python
existing = conn.execute(
    "SELECT claimed_by FROM market_claims WHERE city=? AND target_date=? AND bin_label=? AND direction=?",
    (city, target_date, bin_label, direction)
).fetchone()
if existing:
    # Market already held by the other mode u2014 skip
    continue
```

| Criterion | Assessment |
|-----------|------------|
| Prevents duplicate entry | YES u2014 first-to-claim wins |
| Race condition risk | LOW u2014 SQLite WAL + exclusive transaction |
| Implementation cost | ~50 lines (claim table + check in discovery phase) |
| Semantic correctness | GOOD u2014 both modes CAN hold same market if desired (remove PRIMARY KEY) |
| Cleanup | On settlement/exit, release the claim |

**Weakness:** This is a coordination lock in shared state u2014 it works but it's a runtime check, not a structural guarantee. A developer who adds a new entry path must remember to check the claim table.

#### Option P2-B: Single Discovery Daemon (RECOMMENDED)

**Design:** Only ONE daemon runs the discovery pipeline. The other only monitors/exits.

```
paper daemon: discovery + monitoring + exit
live daemon:  monitoring + exit ONLY
```

Live positions are created by PROMOTING a paper position:
```python
def promote_to_live(trade_id: str) -> None:
    """Move a paper position to live. Single atomic operation."""
    paper_pos = load_from_paper(trade_id)
    write_to_live(paper_pos)
    mark_paper_voided(trade_id, reason="promoted_to_live")
```

| Criterion | Assessment |
|-----------|------------|
| Prevents duplicate entry | **UNCONSTRUCTABLE** u2014 live daemon has no discovery phase |
| Race condition risk | ZERO u2014 only one discoverer |
| Implementation cost | ~100 lines (remove discovery from live cycle, add promote function) |
| Semantic correctness | EXCELLENT u2014 matches the actual intent: paper validates, live executes |
| Migration | Simple u2014 live `cycle_runner.py` skips `_execute_discovery_phase()` |

**Why this is the right structural decision:**

1. **Paper IS the validation layer.** The entire point of paper trading is to validate signals before committing real capital. Having both modes independently discover defeats this purpose.

2. **Promotion is the natural lifecycle.** Paper position u2192 validated u2192 promote to live. This makes the paper-to-live transition explicit and auditable, not implicit via parallel discovery.

3. **One discoverer = zero coordination.** No claim tables, no locks, no race conditions. The live daemon simply cannot create positions from scratch.

4. **Matches process_lock.py pattern.** Process lock already separates daemons by mode. Single-discoverer extends this: mode determines not just "which lock file" but "what operations are permitted."

#### Option P2-C: Cross-Mode Portfolio Visibility

**Design:** `check_position_allowed()` reads BOTH portfolios before allowing entry.

```python
def check_position_allowed_cross_mode(...):
    paper_portfolio = load_portfolio(mode="paper")
    live_portfolio = load_portfolio(mode="live")
    combined_exposure = paper_exposure + live_exposure
    # Check against combined limits
```

| Criterion | Assessment |
|-----------|------------|
| Prevents duplicate entry | PARTIAL u2014 prevents same city/bin overlap, but both could still enter different bins |
| Race condition risk | MEDIUM u2014 TOCTOU between check and entry |
| Implementation cost | ~80 lines |
| Semantic correctness | POOR u2014 paper and live shouldn't share risk limits |

**Verdict: REJECT.** Paper and live have different capital bases and risk profiles. Combining them conflates independent financial contexts.

### P2 Recommendation: Single Discovery Daemon (P2-B)

This is the structural decision that makes the entire duplicate-entry bug category impossible:

| Layer | Decision | Bug category eliminated |
|-------|----------|------------------------|
| Data isolation (P1) | Physical DB separation | Cross-mode data reads |
| Discovery ownership (P2) | Single discoverer | Duplicate market entry |
| Defense-in-depth | CHECK constraints | NULL env values |

### Implementation: Removing Discovery from Live

In `cycle_runner.py:run_cycle()` (line 254):
```python
# Current: both modes discover
if risk_level == RiskLevel.GREEN and not entries_paused and entries_blocked_reason is None:
    p_dirty, t_dirty = _execute_discovery_phase(...)

# Proposed: only paper discovers
if (risk_level == RiskLevel.GREEN 
    and not entries_paused 
    and entries_blocked_reason is None
    and settings.mode == "paper"):  # <-- structural gate
    p_dirty, t_dirty = _execute_discovery_phase(...)
```

Live entries come exclusively through `promote_to_live()`, which:
1. Reads a validated paper position
2. Creates a live position with the same parameters
3. Voids the paper position with `reason="promoted_to_live"`
4. Logs the promotion in `chronicle` for audit

Promotion can be triggered by:
- Manual command (`zeus promote <trade_id>`)
- Automated rule (paper position validated for N cycles with stable edge)
- Future: riskguard auto-promotion gate

---

## Combined Architecture

```
state/
  zeus-shared.db      # settlements, forecast_skill, platt_models, replay_results
  zeus-paper.db       # trade data for paper mode
  zeus-live.db        # trade data for live mode (entries only via promotion)
  risk_state-paper.db # (already separated)
  risk_state-live.db  # (already separated)

paper daemon:
  - discovery phase: YES
  - monitoring phase: YES  
  - exit phase: YES
  - writes to: zeus-paper.db, positions-paper.json

live daemon:
  - discovery phase: NO (unconstructable u2014 code path removed)
  - monitoring phase: YES
  - exit phase: YES
  - writes to: zeus-live.db, positions-live.json
  - entries only via: promote_to_live()
```

Three structural decisions, three bug categories eliminated:
1. Physical DB u2192 cross-mode data reads impossible
2. Single discoverer u2192 duplicate market entries impossible  
3. CHECK constraints u2192 NULL env impossible
