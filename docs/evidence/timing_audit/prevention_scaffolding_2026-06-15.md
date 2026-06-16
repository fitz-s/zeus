# Timestamp Guessing Prevention — Scaffolding Audit
Date: 2026-06-15
Purpose: Locate every concrete hook-point for the "no more guessing" prevention antibodies.
Root-cause framing: timestamps are written by GUESSING, not from a justified basis; prevention must make guessing structurally impossible.

---

## 1. BasisKind — definition, values, usage, and required-on-every-persisted-value feasibility

**EXISTS** — `src/contracts/time_semantics.py:87–101`

```python
class BasisKind(str, Enum):
    MEASURED = "MEASURED"
    ENFORCED = "ENFORCED"
    DERIVED   = "DERIVED"
    EXTERNAL  = "EXTERNAL"
    GUESS     = "GUESS"
```

`BasisKind` annotates every `Entry` in the `REGISTRY` (one entry per declared time constant). Each `Entry` carries a mandatory `basis_kind: BasisKind` field and a prose `basis: str` that must own "guess" in its text if `basis_kind == GUESS` (enforced by `test_guess_basis_entries_are_enumerable` in `tests/test_time_semantics_relations.py:142`).

**Where used beyond `time_semantics.py`:** the `guess_entries()` accessor (`time_semantics.py:947`) is the public surface; the test file and any audit/report script call it. No other production module imports `BasisKind` directly — usage is limited to the registry and its test.

**Could it be REQUIRED on every persisted timing value?** Currently `BasisKind` only annotates registry entries (timeout/TTL/cadence constants). It does NOT annotate individual timestamp columns written to the DB (`recorded_at`, `ingested_at`, `settled_at`, etc.). To extend coverage:
- Add a `TimestampBasis` annotation (same enum, or a reuse of `BasisKind`) as a companion to every `timestamp_*` column declaration in `src/state/db.py` and `src/calibration/*.py`.
- **Antibody attach point:** `src/state/db.py` schema declarations (44 `CURRENT_TIMESTAMP` occurrences) and the `src/ingest/harvester_truth_writer.py:438` settle path.

---

## 2. Existing invariant/relation enforcement — `tests/test_time_semantics_relations.py`

**EXISTS** — `tests/test_time_semantics_relations.py`

**How it asserts:**
- `test_declared_time_relation_holds` (`line 56`): parametrized over every `Relation` in `REGISTRY`; calls `ts.evaluate_relation(entry, relation)` and asserts `check.holds`. Known live violations (none currently — the cluster-1 gamma violation was resolved) go into `_KNOWN_VIOLATIONS` as `xfail(strict)`.
- `test_no_unexpected_live_violations` (`line 111`): sweeps `evaluate_all()` and fails hard if ANY relation fails and is not in `_KNOWN_VIOLATIONS` — this is the load-bearing antibody.
- `test_known_violations_are_still_actually_violated` (`line 90`): anti-stale-xfail guard; removes suppressed xfails that have silently started passing.
- `test_guess_basis_entries_are_enumerable` (`line 128`): lists all `GUESS` entries; validates prose owns "guess".
- `test_every_entry_reads_a_live_value_without_mutation` (`line 149`): stability check.

**Is it run in CI?** Yes. `tests/` is the default `testpaths` in `pytest.ini`. The `full-pytest-sweep.yml` workflow runs `pytest tests/` on every PR and push to main (advisory Phase 1, `continue-on-error: true`). `money-path-required.yml` selects tests via `architecture/money_path_ci.yaml`; `test_time_semantics_relations` is not explicitly listed there but is swept by the full sweep. The time-semantics test does **not** appear to be a `required` gate on its own — it runs under the advisory sweep only.

**Could it be extended to enforce timestamp-basis / format invariants?**
- Yes. The `REGISTRY`'s `Entry.source_ref` already records where each live value lives (`file:line`). A new test parametrized over `REGISTRY` entries could assert: (a) if `basis_kind == GUESS`, a linked GitHub issue or measurement plan must exist; (b) every `GUESS` entry on the money path must have a measured floor declared. The same file is the right place for timestamp-format invariants (e.g., assert all registered entries' source callbacks produce only UTC-aware ISO strings).
- **Antibody attach point:** add a new `test_guess_entries_have_measurement_plan` and `test_no_guess_on_money_path` test case inside `tests/test_time_semantics_relations.py`. Wire the test file into `architecture/money_path_ci.yaml` so it becomes a **required** gate, not just advisory.

---

## 3. CI / pre-commit / check infrastructure

**EXISTS** — multiple layers.

### GitHub Actions (`.github/workflows/`)
- `full-pytest-sweep.yml`: advisory sweep; runs `pytest tests/` on every PR. `continue-on-error: true` (Phase 1). Catches any new test in `tests/` automatically. **Promote to required to harden.**
- `money-path-required.yml`: **required** gate; runs `scripts/ci/semantic_diff_classifier.py --fail-on-unregistered`, selects tests from `architecture/money_path_ci.yaml`. Runs `tests/money_path/` deterministically.
- `money-path-release-gate.yml` / `live-release-gate.yml`: additional required semantic gates.
- `replay-correctness.yml`: replay determinism gate.
- `secrets-scan.yml`: scans for leaked secrets.

### Pre-commit hooks (`.claude/hooks/pre-commit`)
- Two-step gate: `invariant_test` (runs a pytest baseline before every commit) + `secrets_scan`. Wired for both agent commits (Claude Code Bash) and operator direct `git commit` via `.claude/hooks/pre-commit-invariant-test.sh`.
- The invariant test runs a curated baseline (`BASELINE_PASSED` count); adding a timestamp-format or `GUESS`-on-money-path test to the baseline automatically gates every commit.

### Scripts
- `scripts/semantic_linter.py` — no timestamp rules found (confirmed absent).
- `scripts/check_schema_fingerprint.py` — run in `money-path-required.yml`'s `static-semantic` job.
- `scripts/ci/assert_invariant_coverage.py` — asserts invariant coverage against `architecture/money_path_ci.yaml`.

**Where a FAILS-on-naive-timestamp-write antibody would attach:**
1. **Test layer:** Add `tests/test_timestamp_basis.py` asserting that every write site in the money path (harvester, settlement, reactor cycle) either (a) calls a canonical helper or (b) carries a `# basis: ...` annotation. Wire into `money-path-ci.yaml` → becomes a required gate.
2. **Pre-commit hook:** Add an `ast_check` hook that greps for raw `datetime.now()` on known money-path files (`src/execution/`, `src/state/`, `src/ingest/`) and blocks commits that add new raw call sites.
3. **Linter:** Add a `flake8` or `ruff` custom rule (no `pre-commit-config.yaml` exists today — ABSENT) that flags `datetime.now()` without `timezone.utc` or the canonical helper name.

---

## 4. The `date.today()` ban — enforcement status

**ABSENT (enforced only by docstring, not by code)**

`src/contracts/epistemic_context.py:12` documents: `"Strictly forbids 'date.today()' scattered locally across the system."` This is prose only. No lint rule, no CI check, no test enforces the ban.

**Live violators (10 in `src/`; 9+ in `tests/` and `scripts/`):**
```
src/contracts/shoulder_strategy_vnext.py:118
src/calibration/day0_horizon_calibration.py:240
src/data/solar_append.py:463
src/data/hourly_instants_append.py:485
src/data/forecasts_append.py:514
src/data/hole_scanner.py:308
src/data/daily_obs_append.py:1329 (comment), :1403
src/engine/time_context.py:4 (docstring only)
```
Note: `src/engine/time_context.py:4` is a docstring warning, not an actual call.

**Only partial enforcement found:** `tests/test_k8_slice_s.py:46` asserts `"no date.today() in ingestion_guard.py"` for ONE specific file. This is a single-file surgical test, not a global ban.

**Enforcement gap:** No systemic check. The docstring in `epistemic_context.py` cannot be found by any automated pipeline.

**Where the antibody attaches:**
- Add `tests/test_no_date_today_ban.py` that AST-scans `src/execution/`, `src/state/`, `src/ingest/`, `src/contracts/` for `date.today()` calls (not comments/docstrings) and fails with file:line on any hit. Wire into the pre-commit invariant baseline + `money-path-ci.yaml`.
- Alternatively, add a `ruff` rule (`RUF` namespace custom plugin or `flake8-bugbear` equivalent) that bans `date.today()` in the money-path subtree.

---

## 5. Canonical timestamp helper — does one exist?

**ABSENT** — no canonical helper; 410+ raw call sites.

No `utc_now()`, `to_iso()`, `parse_ts()`, `now_utc()`, or equivalent exists anywhere in `src/`. Every timestamp write is a raw call:

```
datetime.now(timezone.utc).isoformat()   — 410 occurrences in src/
CURRENT_TIMESTAMP (SQL DEFAULT)          — 44 occurrences in src/
datetime.utcnow()                        — 0 occurrences (good; deprecated form absent)
```

**Representative raw call sites (money-path / settlement / state):**
```
src/ingest_main.py:241      "alive_at": datetime.now(timezone.utc).isoformat()
src/ingest_main.py:275      "written_at": datetime.now(timezone.utc).isoformat()
src/main.py:1262            "last_completed_at": datetime.now(timezone.utc).isoformat()
src/main.py:6180            completed_at=datetime.now(timezone.utc).isoformat()
src/main.py:8844            started_at=datetime.now(timezone.utc).isoformat()
src/ingest/harvester_truth_writer.py:438  settled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
src/calibration/store.py:577              now = datetime.now(timezone.utc).isoformat()
src/state/db.py:1015,1051,1090,1165...   DEFAULT CURRENT_TIMESTAMP (×44)
```

**Why a canonical helper matters for prevention:** With a single `utc_now()` function, a lint rule or pre-commit grep for raw `datetime.now(` in money-path files is O(1) to write and covers every new call site automatically. Without it, the ban is unenforceable because the pattern to ban IS the correct pattern.

**Where the helper should live:** `src/contracts/time_utils.py` (new file) or `src/contracts/time_semantics.py` (same authority module). Signature:
```python
def utc_now() -> datetime: ...          # returns timezone.utc aware
def to_iso(dt: datetime) -> str: ...    # formats with timespec="seconds", enforces tz
def parse_ts(s: str) -> datetime: ...   # parses ISO, rejects naive
```

**Sites that would adopt it:** 410 `datetime.now()` call sites + 44 `CURRENT_TIMESTAMP` SQL defaults. Adoption is a mechanical rename; the pre-commit hook can block new raw calls immediately after the helper exists.

---

## 6. Format/tz enforcement — any existing test or runtime check on timestamp FORMAT?

**ABSENT** — confirmed.

No test in `tests/` asserts that a persisted timestamp (a) is timezone-aware, (b) uses a consistent format (`Z` vs `+00:00`), or (c) is not a naive `datetime`. The `test_phase10b_dt_seam_cleanup.py` file (which superficially appears relevant) tests forecast-DB row behavior, not timestamp format contracts.

`src/contracts/epistemic_context.py:20–22` checks `tzinfo is not None` at runtime for `EpistemicContext.decision_time_utc`, but only for that one dataclass field and only at object construction time — not for any DB write path.

`src/control/heartbeat_supervisor.py` uses `_parse_utc()` internally for its own `last_failure_at` field but exposes no general format validation.

**Confirmation of system-wide naive corruption risk:** The 44 `CURRENT_TIMESTAMP` SQL defaults write SQLite's local-machine-clock string (no `Z`, no `+00:00`). The 410 `datetime.now(timezone.utc).isoformat()` calls write `2026-06-15T10:23:45.123456+00:00` (Python ISO format with `+00:00` suffix), while `isoformat(timespec="seconds")` in `harvester_truth_writer.py:438` writes `2026-06-15T10:23:45+00:00`. These formats differ and are mixed across tables.

**Where the antibody attaches:**
- A new `tests/test_timestamp_format_invariant.py` that reads sampled rows from each truth-owning table (`zeus_trades`, `zeus-world`, `zeus-forecasts`) and asserts every timestamp column parses as timezone-aware ISO.
- A `parse_ts()` helper (see item 5) that raises on naive input — wiring it into all read paths provides runtime detection.
- A schema-level constraint: SQLite does not enforce column types, but a DB-open validator in `src/state/db.py` could run `PRAGMA integrity_check` plus a timestamp-sample assertion on startup.

---

## 7. Lane-liveness / fail-loud — existing heartbeat/liveness assertion or alerting

**PARTIALLY EXISTS** — heartbeat infrastructure exists; per-cycle table going dark is partially surfaced but primarily fail-soft.

### What exists:
- **Heartbeat writes (ingest daemon):** `src/ingest_main.py:233` `_write_ingest_heartbeat()` writes `state/daemon-heartbeat-ingest.json` every 60 seconds (APScheduler job).
- **Heartbeat supervisor:** `src/control/heartbeat_supervisor.py` reads the external venue heartbeat; raises `HeartbeatNotHealthy` (line 603/627/629) and writes a fail-closed tombstone on consecutive failures. This is **fail-loud** for venue connectivity.
- **Live-lane-dark LOUD signal:** `src/main.py:6017–6030` — when `reactor_mode=live` and operator is authorized but the live lane is not selected, `logger.error("LIVE LANE DARK: ...")` fires once per cycle. This is reactive (fires when already dark) but it IS a loud signal.
- **Ingest staleness thresholds:** `src/ingest_main.py:679–721` — staleness checks with `staleness_h > threshold_h` comparisons exist for source data.

### What is absent / fail-soft:
- **Per-cycle table going dark (silent):** The `summary["degraded"] = True` pattern (`src/main.py:1507`, `:1653`, `:1812`, `:4366`, `:6045`, `:6256`, `:6681`, `:6744`, `:6763`, `:7000`, `:7017`, `:7040`, `:7057`) is pervasive. Most per-cycle failures are logged at `warning` and set a flag — no exception is raised, no alert is fired, no supervisor kills the lane.
- **No per-cycle liveness assertion on a table:** There is no check that says "if the `settled_positions` table has received no write in N cycles, fire an alert." The heartbeat covers daemon liveness, not data-table liveness.
- **`forecast_live_heartbeat` (registered at `time_semantics.py:857`):** The registry documents a 30s heartbeat cadence for the forecast-live daemon and notes it is load-bearing for liveness detection, but also notes "Needs alignment with the supervisor's heartbeat-staleness threshold" — i.e., the supervisor threshold is currently undeclared and unenforced.

### Where a fail-LOUD lane-liveness check would attach:
1. **Supervisor (existing hook):** `src/control/heartbeat_supervisor.py` already has the fail-closed / raise pattern. Add a `DataLaneHealthCheck` that queries the last-write timestamp of each truth-owning table and raises `DataLaneStale` if the gap exceeds the registered `time_semantics` TTL. Wire into the heartbeat cadence.
2. **Reactor per-cycle:** `src/main.py` at the summary-write path (after `_live_lane_degrade_cause` is set) — escalate `summary["degraded"] = True` to `logger.critical()` + a structured alert if the lane has been degraded for more than N consecutive cycles.
3. **Monitor/health endpoint:** `src/observability/scheduler_health.py:58` (writes a health snapshot) is the right place to expose per-lane table-last-write timestamps so an external monitor can alert on them without polling the DB directly.
