# PLAN — init_schema_forecasts ATTACH path drops 2 critical indexes

- Created: 2026-05-14
- Last reused or audited: 2026-05-14
- Authority basis: AGENTS.md §3 high-risk / §4 planning lock; INV-17 (DB authority); src/state/AGENTS.md; relationship-tests-first (CLAUDE.md Fitz #1)
- Status: PLAN ONLY — no implementation. No src/, tests/, or production-file edits permitted by this packet.
- Failing test (in-tree, pre-existing):
  `tests/state/test_forecast_db_split_invariant.py::test_rel1_init_schema_forecasts_critical_indexes`

---

## 1. Root cause

The test instantiates a fresh `sqlite3.connect(":memory:")` and calls
`init_schema_forecasts(conn)`. At that moment the on-disk
`ZEUS_WORLD_DB_PATH` (`state/zeus-world.db`, ~38 GiB in prod, but also
present in dev/test envs) **exists**. So the branch chosen is the
ATTACH-from-world-src path, not the static-fallback path.

- `src/state/db.py:2507` `init_schema_forecasts(conn)` definition.
- `src/state/db.py:2534-2535` decision: `if ZEUS_WORLD_DB_PATH.exists():`
  → ATTACH path. Else → static fallback.
- `src/state/db.py:2537` `ATTACH DATABASE '{world_path}' AS world_src`.
- `src/state/db.py:2552-2563` copies index DDL by selecting
  `world_src.sqlite_master WHERE type='index' AND tbl_name=?` and
  re-issuing with `IF NOT EXISTS` munging.
- `src/state/db.py:2566-2588` static fallback (only taken when world.db is
  absent) calls
  `_create_ensemble_snapshots_v2(conn)` and `_create_calibration_pairs_v2(conn)`
  from `src/state/schema/v2_schema.py`. Those helpers contain the
  CREATE INDEX DDL for the two indexes in question:
  - `src/state/schema/v2_schema.py:166-169` →
    `idx_ensemble_snapshots_v2_lookup` on
    `ensemble_snapshots_v2(city, target_date, temperature_metric, available_at)`.
  - `src/state/schema/v2_schema.py:292-295` →
    `idx_calibration_pairs_v2_city_date_metric` on
    `calibration_pairs_v2(city, target_date, temperature_metric)`.

**The structural defect** (per Fitz #1 — find the design failure, not the
bug instance): the ATTACH path treats `world_src.sqlite_master` as the
authoritative index inventory for forecast-class tables. This is true on
production world.db where these indexes already exist, but is **false on
any world.db built from a stale schema snapshot, a partial migration,
a `init_schema_world_only` deploy, or a test fixture**. The two indexes
were declared in K1's v2 schema helpers (committed under
`61e7f37fb5 fix(k1): replicate world.db schema for forecasts.db
(schema-drift antibody)`). The antibody assumed world.db is the canonical
source of every forecast-class index. That assumption silently breaks
correctness on any environment where the on-disk world.db trails the
canonical v2 helpers.

**Why the static fallback is bypassed**: in the failing test scenario,
world.db exists (operator ran something else first; CI shared fixture;
prod-shaped dev DB). The fallback branch is unreachable even though the
ATTACH branch produces a strictly weaker schema (missing the 2 covering
indexes that the static helpers would have created).

The category of the bug is therefore: **"the two paths through
`init_schema_forecasts` do not produce equivalent post-conditions"** —
an unenforced equivalence invariant between the ATTACH branch and the
static branch.

---

## 2. Fix options compared

### Option A — Always run static index DDL after the ATTACH copy (idempotent superset)

After the ATTACH+DETACH block (line 2565), unconditionally invoke the
index-creation portion of `_create_ensemble_snapshots_v2` and
`_create_calibration_pairs_v2`, OR factor an
`_ensure_v2_forecast_indexes(conn)` helper that issues only the
`CREATE INDEX IF NOT EXISTS` statements for all 4+ v2 indexes
(`idx_ensemble_snapshots_v2_lookup`, `idx_ens_v2_source_run`,
`idx_ens_v2_entry_lookup`, `idx_calibration_pairs_v2_bucket`,
`idx_calibration_pairs_v2_city_date_metric`,
`idx_calibration_pairs_v2_refit_core`).

- Pros: smallest blast radius (≤ 30 LOC); idempotent (`IF NOT EXISTS`);
  closes the ATTACH-vs-static drift permanently; structural superset
  guarantees the test passes on every environment; matches Fitz #1
  "make the category impossible" — both branches converge to the same
  index set.
- Cons: tiny duplication between ATTACH-copy DDL and helper DDL on
  prod (the index is created twice via `IF NOT EXISTS` — second is no-op
  microseconds); helper file must remain the source of truth for the
  index list (a comment lock anchoring v2_schema.py is required).
- Blast radius: `src/state/db.py::init_schema_forecasts` only; no caller
  touches; no schema-version bump; no migration; INV-17 unaffected
  (DB authority direction is preserved).
- INV impact: none. The schema becomes a strict superset of what it was;
  no truth path is reordered; no caller assumption is broken.

### Option B — Backfill world.db with the missing indexes before ATTACH copy

Run `CREATE INDEX IF NOT EXISTS` against world.db inside
`init_schema_forecasts` (or earlier in `init_schema_world_only`) so that
the ATTACH-copy path picks them up.

- Pros: keeps "world.db is the authoritative inventory" framing intact.
- Cons: writes to world.db inside a function whose name says "forecasts"
  (truth-ownership smell — `init_schema_forecasts` mutating world.db);
  on prod world.db (~38 GiB) `CREATE INDEX` would scan the underlying
  table on first run if not already present, which can be minutes-long;
  introduces a hidden write on a hot DB; risks WAL lock contention with
  live writers (per stash MEMORY: `feedback_sqlite_wal_multi_writer_starvation`);
  violates the spirit of the K1 split where forecast-class indexes
  should live with forecast-class tables.
- Blast radius: `src/state/db.py` plus a runtime cost on the next boot
  per environment.
- INV impact: muddles INV-17 (DB authority direction) — a forecasts init
  function writes to world.db; this is exactly the cross-DB authority
  drift the K1 split was meant to prevent.

### Option C — Drop the ATTACH path entirely; always use the static helpers

Remove the `if ZEUS_WORLD_DB_PATH.exists()` branch and always invoke
`_create_settlements`, `_create_observations`, `_create_source_run`, and
the four v2 helpers, plus the post-K1 ALTER chain on whatever schema
already exists.

- Pros: single code path; matches v2_schema.py as the canonical contract;
  removes the schema-drift antibody whose assumption just broke.
- Cons: requires confidence that the static helpers + ALTERs reproduce
  any historical world.db schema migration that has accumulated; the
  whole reason the ATTACH path was added (commit `61e7f37fb5`, the
  schema-drift antibody) is that operator-applied `ALTER TABLE`
  migrations on world.db could outpace `v2_schema.py`. Removing that
  antibody now would re-open the drift hole.
- Blast radius: larger — must audit every ALTER ever applied to world.db's
  forecast-class tables and ensure it lives in a helper.
- INV impact: indirect — re-introduces the drift class that K1 closed.

### Recommended: **Option A**.

Rationale: smallest diff, structurally complete (post-condition
equivalence between ATTACH-branch and static-branch is enforced
explicitly), preserves the schema-drift antibody (commit `61e7f37fb5`
intent), no DB writes on world.db, no INV-17 collision. Makes the bug
**category** impossible (any future v2 index that lives in the static
helpers will be created on the forecasts conn regardless of ATTACH path
state) provided the index list in the helper is treated as the canonical
inventory.

---

## 3. Cross-module relationship tests required BEFORE implementation

Per Fitz: relationship-tests → implementation → function-tests, not
reversible. The current failing test (`test_rel1_init_schema_forecasts_critical_indexes`)
is a function test of one module. The relationship invariant being
violated is between **the two branches inside `init_schema_forecasts`
(ATTACH path vs static-fallback path) and the static index inventory
in `v2_schema.py`**.

Required new relationship tests (sketch — to be authored under a
follow-up packet, NOT this one):

### REL-A — Branch equivalence: ATTACH path and static path produce the same index set

```python
def test_attach_and_static_paths_produce_equivalent_indexes(tmp_path, monkeypatch):
    """Both branches of init_schema_forecasts MUST produce the same index set
    on the forecasts conn."""
    # Build a world.db that DOES have the indexes (production-shaped)
    world_with = tmp_path / "world_with.db"
    monkeypatch.setattr("src.state.db.ZEUS_WORLD_DB_PATH", world_with)
    w = sqlite3.connect(world_with)
    init_schema(w)  # full world schema, indexes included
    w.close()

    # Build a world.db that LACKS the 2 indexes (partial/legacy shaped)
    world_without = tmp_path / "world_without.db"
    w2 = sqlite3.connect(world_without)
    init_schema(w2)
    w2.execute("DROP INDEX IF EXISTS idx_ensemble_snapshots_v2_lookup")
    w2.execute("DROP INDEX IF EXISTS idx_calibration_pairs_v2_city_date_metric")
    w2.commit(); w2.close()

    def _idx_set(world_db):
        monkeypatch.setattr("src.state.db.ZEUS_WORLD_DB_PATH", world_db)
        c = sqlite3.connect(":memory:")
        init_schema_forecasts(c)
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        c.close()
        return {r[0] for r in rows}

    set_with = _idx_set(world_with)
    set_without = _idx_set(world_without)

    # Branch equivalence: missing-index world.db must not produce a weaker
    # index set than the production-shaped one.
    assert set_with == set_without, (
        f"branch divergence: ATTACH(full)={set_with - set_without} vs "
        f"ATTACH(partial)={set_without - set_with}"
    )
```

### REL-B — Canonical-helper coverage: every v2 index declared in v2_schema.py must end up on the forecasts conn

```python
def test_init_schema_forecasts_creates_every_v2_helper_index(tmp_path, monkeypatch):
    """Every CREATE INDEX statement inside v2_schema.py for forecast-class
    tables must be present on the forecasts conn after init_schema_forecasts,
    regardless of world.db ATTACH path or fallback."""
    import re
    src = Path("src/state/schema/v2_schema.py").read_text()
    declared = set(re.findall(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(idx_\w+)", src))
    # Filter to the 4 forecast-class tables
    forecast_idx = {n for n in declared if (
        "ensemble_snapshots_v2" in n or "calibration_pairs_v2" in n
        or "settlements_v2" in n or "market_events_v2" in n
    )}

    # Case 1: world.db absent (static fallback path)
    world = tmp_path / "absent.db"  # does not exist on disk
    monkeypatch.setattr("src.state.db.ZEUS_WORLD_DB_PATH", world)
    c1 = sqlite3.connect(":memory:")
    init_schema_forecasts(c1)
    have1 = {r[0] for r in c1.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    c1.close()
    assert forecast_idx.issubset(have1), f"static fallback missing: {forecast_idx - have1}"

    # Case 2: world.db exists but is empty (ATTACH branch over zero-table source)
    empty_world = tmp_path / "empty.db"
    sqlite3.connect(empty_world).close()  # creates empty file
    monkeypatch.setattr("src.state.db.ZEUS_WORLD_DB_PATH", empty_world)
    c2 = sqlite3.connect(":memory:")
    init_schema_forecasts(c2)
    have2 = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    c2.close()
    assert forecast_idx.issubset(have2), f"ATTACH-over-empty missing: {forecast_idx - have2}"
```

### REL-C — Schema version invariant preserved on both paths

```python
def test_user_version_set_regardless_of_branch(tmp_path, monkeypatch):
    """SCHEMA_FORECASTS_VERSION must be written on both branches (already true,
    must stay true after the fix)."""
    # Both world.db absent and world.db present cases must end with
    # PRAGMA user_version == SCHEMA_FORECASTS_VERSION.
    ...  # symmetric to REL-A
```

These three tests pin the structural invariant **"the two branches of
init_schema_forecasts produce the same post-condition"**, which is what
was actually broken. The current REL-1 critical-indexes test is a
function-level symptom; REL-A/B/C are the relationship-level antibodies.

Order of work in the follow-up implementation packet:
1. Land REL-A, REL-B, REL-C as failing tests (or marked xfail).
2. Implement Option A in `init_schema_forecasts`.
3. Confirm REL-A/B/C + the existing
   `test_rel1_init_schema_forecasts_critical_indexes` go from RED → GREEN.
4. Add `architecture/invariants.yaml` entry citing the new tests
   (relationship-test enforcement).

---

## 4. Planning-lock evidence

### Why the lock fires

- File touched in proposed implementation: `src/state/db.py` (and only
  that file in Option A).
- `src/state/**` truth-ownership / schema is named in AGENTS.md §4
  Planning lock as a stop-and-plan zone.
- `src/state/AGENTS.md` §"Planning lock" repeats the rule: any schema
  change under `src/state/**` requires an approved packet + planning-lock
  evidence.

### Invariants protected / touched

- **INV-17** (DB authority direction — DB > derived JSON): unaffected by
  Option A; muddied by Option B (forecasts init writes to world.db).
  Confirms Option A preference.
- **INV-30 / INV-31** (cross-DB venue command journaling): unrelated;
  Option A does not cross the trades-DB boundary.
- **K1 split contract** (commit `61e7f37fb5` + PLAN
  `docs/operations/task_2026-05-11_forecast_db_split/PLAN.md §5.9`):
  this PLAN packet's fix is a strict refinement, not a contract change.
  The schema-drift antibody (ATTACH copy) is preserved; we add the
  missing post-condition equivalence guarantee.
- **No new invariant is invented**; we make the existing implicit
  invariant ("both branches produce the same schema") explicit and tested.

### Downstream readers depending on the missing indexes

Hot-path queries that the two missing indexes serve:
- `idx_ensemble_snapshots_v2_lookup`
  `(city, target_date, temperature_metric, available_at)` — used by every
  evaluator/replay/calibration scan that fetches the latest available
  snapshot per (city, target_date, metric). Callers in
  `src/engine/evaluator.py` and `src/calibration/store.py` issue inserts
  on the same UNIQUE key shape; lookups by this shape are the dominant
  query family.
- `idx_calibration_pairs_v2_city_date_metric`
  `(city, target_date, temperature_metric)` — used by calibration replay,
  refit, and observation join paths in `src/calibration/store.py`.

Without these indexes a fresh forecasts.db deployment goes to full table
scan on the hot read path; on prod-sized data (~tens of GiB) this
silently degrades evaluator latency rather than failing outright. This
is the "code correct, data shape wrong" failure mode (Fitz Constraint
#4 — data provenance) at the schema-shape level.

### Topology gate (Step 5)

`python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py --plan-evidence <this file>`.

Verdict captured in §5.

---

## 5. Recommended option + rationale

**Option A — append idempotent `CREATE INDEX IF NOT EXISTS` calls for
the four v2 forecast-class tables' indexes after the ATTACH+DETACH
block, factored into a single `_ensure_v2_forecast_indexes(conn)` helper
in `src/state/db.py` that references the index names declared in
`src/state/schema/v2_schema.py`.**

One-line rationale: smallest diff that makes the bug **category**
impossible (post-condition equivalence between branches becomes a
testable invariant) without writing to world.db, without changing
schema versions, without touching INV-17, and while preserving the K1
schema-drift antibody.

---

## 6. Out-of-scope (deferred to follow-up implementation packet)

- Code edits under `src/**` and `tests/**`.
- Modifications to `architecture/invariants.yaml` to register the new
  REL-A/B/C tests.
- Backfilling world.db with the indexes (Option B remains the worse
  choice but is not foreclosed; left for a separate operator decision).
- Reviewing whether any other forecast-class ALTER applied to world.db
  but absent from `v2_schema.py` has the same divergence shape (audit
  task).
