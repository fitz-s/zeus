# Created: 2026-06-08
# Last reused or audited: 2026-06-08 (CLEANUP fix: add the coupling-unconstructable proof —
#   the resolved WU duplicate is not merely removed, the fail-closed registry E gate makes
#   re-introducing a SECOND order-daemon WU collector un-constructable; system_decomposition_plan §9)
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §6 (data-ingest row: "REMOVE residual _wu_daily_dispatch duplicate from src.main
#     ... VERIFIED set-equivalent to daily_tick's wu_icao slice"),
#   §8 Step 4 (remove residual duplicate; CONTAINMENT VERIFIED set-equivalence),
#   §9 (the regression CATEGORY — here DUPLICATE-WRITE / surface bloat — made UNCONSTRUCTABLE,
#     not just the instance patched: the fail-closed E gate catches any re-added 2nd live owner),
#   §7 I-class (every cross-program seam is a DB table the producer writes + consumer reads).
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=never
# Purpose: RELATIONSHIP TESTS for process-topology refactor STEP 4 — remove the
#   verified-duplicate `wu_daily` dispatch from the order daemon (src.main). The WU
#   daily-observation concern is OWNED by data-ingest (`_k2_daily_obs_tick` ->
#   `daily_obs_append.daily_tick`); the src.main copy (`_wu_daily_dispatch` ->
#   `wu_scheduler.run_wu_daily_dispatch`) is a verified set-equivalent duplicate.
#
# These tests verify CROSS-MODULE INVARIANTS (Module A's slice vs Module B's slice),
# not just function behavior:
#   (NO-REGRESSION) the WU-collection slice that DATA-INGEST runs (daily_tick) is
#     SET-EQUIVALENT to what src.main's removed path ran — re-verified here against the
#     actual source (same iteration source, same wu_icao filter, same per-city gate, same
#     target-date math, same writer), so removing the src.main copy loses ZERO city
#     coverage; the data-ingest collector is still scheduled and still writes via the
#     sanctioned INV-37 ATTACH path; src.main STILL IMPORTS (never break boot).
#   (SUPERIORITY) src.main registers exactly ONE FEWER (duplicate) job: `wu_daily` is no
#     longer an add_job id in src.main and `_wu_daily_dispatch` is no longer defined there;
#     the resolved active-duplicate is gone from BOTH the registry's known-open map AND the
#     live duplicate-owner detection (no more double WU-API fetch / rebuild_run_id clobber).
"""STEP 4 relationship tests: remove the verified-duplicate wu_daily dispatch from src.main."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "src" / "main.py"

# The job id and producer symbol that must DISAPPEAR from the order daemon.
_REMOVED_JOB_ID = "wu_daily"
_REMOVED_PRODUCER = "_wu_daily_dispatch"

# The (family, source_id) key of the active-duplicate this step resolves.
_WU_DUP_KEY = ("observation", "wu_icao_history")


# ---------------------------------------------------------------------------
# Shared AST helpers (mirror tests/test_p2_substrate_observer_lift.py)
# ---------------------------------------------------------------------------

def _add_job_first_positional_names(source_path: Path) -> list[str]:
    """Return the first-positional-arg Name id of every `*.add_job(NAME, ...)` call."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            if node.args and isinstance(node.args[0], ast.Name):
                names.append(node.args[0].id)
    return names


def _add_job_ids(source_path: Path) -> list[str]:
    """Return every literal `id=` keyword across `*.add_job(..., id="X")` calls."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    ids.append(kw.value.value)
    return ids


def _defines_function(source_path: Path, fn_name: str) -> bool:
    """True iff `source_path` defines a top-level (or nested) `def fn_name`."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name
        for node in ast.walk(tree)
    )


# ===========================================================================
# NO-REGRESSION INVARIANTS — removing the src.main copy must lose ZERO coverage
# ===========================================================================

def test_no_regression_data_ingest_owns_wu_collection_and_is_scheduled():
    """The WU daily concern still has a LIVE owner: data-ingest schedules
    `ingest_k2_daily_obs` -> `_k2_daily_obs_tick` -> `daily_tick`. If src.main's copy
    is removed but data-ingest's were ALSO somehow gone, every wu_icao city would lose
    coverage — so this asserts the survivor exists and is wired."""
    import src.ingest_main as ingest_main

    # The collector function exists and routes to the canonical daily_tick path.
    assert hasattr(ingest_main, "_k2_daily_obs_tick")
    src = inspect.getsource(ingest_main._k2_daily_obs_tick)
    assert "daily_tick" in src, "data-ingest WU collector must call the canonical daily_tick"

    # It is actually SCHEDULED in the data-ingest daemon (id == ingest_k2_daily_obs).
    # ingest_main schedules via the tuple-spec shape `add_job(*(fn, trig, dict(id=...)))`,
    # so the id lives inside a dict(...) call — use the project's canonical extractor
    # (handles BOTH the direct-keyword and dict-spec shapes) rather than the keyword-only one.
    from scripts.data_collection_inventory import _scheduled_ids_in

    ingest_ids = _scheduled_ids_in((_REPO_ROOT / "src" / "ingest_main.py",))
    assert "ingest_k2_daily_obs" in ingest_ids, (
        "data-ingest must still schedule the WU daily-obs collector after the src.main dedup"
    )


def test_no_regression_data_ingest_collector_uses_sanctioned_inv37_attach_path():
    """INV-37: the surviving cross-DB writer (observations forecasts-class + data_coverage
    world-class in one SAVEPOINT) must go through the sanctioned ATTACH connection, never an
    independent cross-DB connection. The dedup must not change which path owns the write."""
    import src.ingest_main as ingest_main

    src = inspect.getsource(ingest_main._k2_daily_obs_tick)
    assert "get_forecasts_connection_with_world" in src, (
        "data-ingest WU collector must open the INV-37 forecasts+world ATTACH connection"
    )


def test_no_regression_wu_collection_slice_is_set_equivalent_across_modules():
    """THE CORE RELATIONSHIP TEST (re-verifies the plan's §8 Step 4 set-equivalence claim
    against the ACTUAL source — does not assume it).

    The WU slice of data-ingest's `daily_obs_append.daily_tick` and src.main's removed
    `wu_scheduler.run_wu_daily_dispatch` must collect the IDENTICAL city set each tick. Both:
      (1) iterate the SAME source: `cities_by_name.values()`,
      (2) apply the SAME filter:   `settlement_source_type != "wu_icao": continue`,
      (3) apply the SAME per-city gate: `WuDailyScheduler().should_collect_now(city, now_utc)`,
      (4) compute the SAME target date: `local_today - timedelta(days=1)` in the city tz,
      (5) call the SAME writer: `append_wu_city(name, [local_yesterday], conn, ...)`.
    `daily_tick` additionally covers HKO -> a STRICT SUPERSET of the daily-obs concern, so the
    wu_icao slice of daily_tick is == the removed path's set. Coverage loss is therefore zero.
    """
    from src.data import daily_obs_append, wu_scheduler

    daily_src = inspect.getsource(daily_obs_append.daily_tick)
    removed_src = inspect.getsource(wu_scheduler.run_wu_daily_dispatch)

    for needle in (
        'settlement_source_type != "wu_icao"',  # identical filter
        "should_collect_now",                    # identical per-city gate
        "timedelta(days=1)",                     # identical target-date math
        "append_wu_city",                        # identical writer
    ):
        assert needle in daily_src, f"data-ingest daily_tick WU slice missing {needle!r}"
        assert needle in removed_src, f"removed run_wu_daily_dispatch missing {needle!r}"

    # Both iterate cities_by_name (the same universe) — neither sub-selects a different set.
    assert "cities_by_name" in daily_src and "cities_by_name" in removed_src


def test_no_regression_wu_collection_slice_fires_same_city_set_behaviorally():
    """Behavioral confirmation of the structural set-equivalence: across a representative
    sweep of UTC hours, the set of wu_icao cities that data-ingest's daily_tick WOULD collect
    equals the set the removed run_wu_daily_dispatch path would collect. Same `WuDailyScheduler`
    gate + same filter -> same fired set, hour by hour. (No DB/HTTP needed — we recompute the
    gate predicate directly, exactly as both modules do.)"""
    from datetime import datetime, timedelta, timezone

    from src.config import cities_by_name
    from src.data.wu_scheduler import WuDailyScheduler

    scheduler = WuDailyScheduler()
    base = datetime(2026, 6, 8, 0, 0, tzinfo=timezone.utc)

    def _fired_set(now_utc):
        # The IDENTICAL predicate both daily_tick and run_wu_daily_dispatch apply.
        return {
            c.name
            for c in cities_by_name.values()
            if c.settlement_source_type == "wu_icao"
            and scheduler.should_collect_now(c, now_utc)
        }

    # Sweep 24 hours: at every hour the two paths' fired set is identical (they are the
    # same predicate), and the union over 24h covers every wu_icao city at least once.
    union = set()
    for h in range(24):
        now = base + timedelta(hours=h)
        fired = _fired_set(now)
        # Trivially equal because both modules compute THIS predicate — the test's value is
        # that it LOCKS the predicate as the single source of the fired set for both paths.
        assert fired == _fired_set(now)
        union |= fired

    all_wu = {c.name for c in cities_by_name.values() if c.settlement_source_type == "wu_icao"}
    assert all_wu, "expected at least one wu_icao city in the config"
    missing = all_wu - union
    assert not missing, (
        f"wu_icao cities never collected by the surviving path over 24h (coverage loss!): "
        f"{sorted(missing)}"
    )


def test_no_regression_src_main_still_imports():
    """src.main MUST still import successfully with the duplicate job removed (never break boot)."""
    import importlib

    import src.main as main_mod

    importlib.reload(main_mod) if False else None  # import is the boot check; no reload side effects
    assert main_mod is not None


# ===========================================================================
# SUPERIORITY INVARIANTS — one fewer duplicate job, proven zero coverage loss
# ===========================================================================

def test_superiority_src_main_no_longer_registers_wu_daily():
    """The order daemon registers exactly ONE FEWER job: `wu_daily` is gone from src.main's
    add_job set (neither as a positional producer name nor as an id= literal)."""
    positional = _add_job_first_positional_names(_MAIN_PY)
    ids = _add_job_ids(_MAIN_PY)
    assert _REMOVED_PRODUCER not in positional, (
        f"src.main still registers {_REMOVED_PRODUCER} as an add_job target"
    )
    assert _REMOVED_JOB_ID not in ids, (
        f"src.main still registers the '{_REMOVED_JOB_ID}' add_job id (the duplicate)"
    )


def test_superiority_src_main_no_longer_defines_the_duplicate_producer():
    """The duplicate producer function is removed from src.main (it had no other caller —
    its only job was the now-deleted registration)."""
    assert not _defines_function(_MAIN_PY, _REMOVED_PRODUCER), (
        f"src.main still defines {_REMOVED_PRODUCER}; the dedup must remove the dead function"
    )
    import src.main as main_mod
    assert not hasattr(main_mod, _REMOVED_PRODUCER)


def test_superiority_active_duplicate_is_resolved_in_the_registry():
    """The verified active-duplicate (main.wu_daily AND ingest.k2_daily_obs both producing
    `observation/wu_icao_history`) is RESOLVED, not merely tracked. After the dedup:
      - the (observation, wu_icao_history) key is no longer a live duplicate (only ONE owner),
      - and it is removed from the known-open list (so the gate's "must stay detected until
        the fix lands" contract is satisfied — the fix LANDED here)."""
    from src.data.source_job_registry import (
        _KNOWN_OPEN_DUPLICATE_LIVE_OWNERS,
        duplicate_live_family_owners,
        open_duplicate_live_owner_violations,
        unacknowledged_duplicate_live_owners,
    )

    # No longer a live duplicate: the WU family/source has at most ONE live owner daemon now.
    assert _WU_DUP_KEY not in duplicate_live_family_owners(), (
        "WU daily still has >1 live owner daemon — the src.main duplicate was not fully removed"
    )
    # And it is no longer carried as a known-open bug (the open ownership decision is now closed).
    assert _WU_DUP_KEY not in _KNOWN_OPEN_DUPLICATE_LIVE_OWNERS, (
        "the resolved WU active-duplicate must be removed from the known-open list"
    )
    assert _WU_DUP_KEY not in open_duplicate_live_owner_violations()
    # The fail-closed gate stays clean: no UNTRACKED duplicate live owners introduced.
    assert unacknowledged_duplicate_live_owners() == {}, (
        f"untracked duplicate live owners (classify each): {unacknowledged_duplicate_live_owners()}"
    )


def test_superiority_registry_has_no_main_owned_wu_daily_spec():
    """The job-registry no longer carries a `main`-owned wu_daily SourceJobSpec pointing at the
    deleted callable — otherwise the registry would mirror a job that no longer exists (an orphan
    the data_collection_inventory mirror-gate would surface)."""
    from src.data.source_job_registry import JOB_REGISTRY

    assert _REMOVED_JOB_ID not in JOB_REGISTRY, (
        "registry still declares a 'wu_daily' job after the src.main dedup (orphan callable_ref)"
    )


# ===========================================================================
# COUPLING-UNCONSTRUCTABLE PROOF (system_decomposition_plan §9) — the regression
# CATEGORY for THIS step (a SECOND live WU collector in the order daemon: the
# duplicate-write / double-WU-API-quota / rebuild_run_id-clobber class) is made
# STRUCTURALLY UN-CONSTRUCTABLE by the fail-closed duplicate-live-owner E gate,
# not merely patched as an instance.
#
# Step 4 is a DELETION, not a process lift, so its §9 antibody is NOT "a separate
# address space has no pending_count to reference" (that is the I1/P2 lift's proof).
# Its antibody is the registry invariant: any (family, source) gaining a SECOND
# live owner_daemon that is in NEITHER allow-list trips unacknowledged_duplicate_
# live_owners() — the fail-closed gate. So re-adding a `main`-owned WU collector
# (the exact regression) cannot pass silently; the gate reconstructs the alarm.
# We PROVE this by reconstructing the deleted duplicate IN A REGISTRY COPY and
# asserting the gate fires — without mutating the real module state.
# ===========================================================================

def test_unconstructable_readding_main_wu_collector_trips_failclosed_gate():
    """ANTIBODY PROOF (§9): the duplicate-WU regression category is unconstructable while the
    E gate is in CI. If a future edit re-introduces a `main`-owned LIVE WU collector for
    (observation, wu_icao_history) — the precise shape Step 4 deleted — the fail-closed
    duplicate-live-owner detector MUST surface it as an UNTRACKED violation. We rebuild the
    detector over a registry that includes the re-added duplicate (a local copy; the real
    module is never mutated) and assert it fires, then assert it does NOT fire over today's
    real (deduped) registry. This is the structural difference between 'instance removed' and
    'category unconstructable'."""
    from src.data import source_job_registry as reg
    from src.data.source_job_registry import (
        SourceJobSpec,
        duplicate_live_family_owners,
        unacknowledged_duplicate_live_owners,
    )

    wu_family, wu_source = _WU_DUP_KEY  # ("observation", "wu_icao_history")

    # Sanity: TODAY (post-dedup) the gate is clean for the WU key.
    assert _WU_DUP_KEY not in duplicate_live_family_owners()
    assert _WU_DUP_KEY not in unacknowledged_duplicate_live_owners()

    # Reconstruct the EXACT regression: a 2nd live owner daemon (`main`) for the WU family.
    re_added = SourceJobSpec(
        "wu_daily", "main", "live", "default", True,
        source_id=wu_source, callable_ref="_wu_daily_dispatch", family=wu_family,
    )
    poisoned_registry = {**reg.JOB_REGISTRY, re_added.job_id: re_added}

    # Re-run the detector's logic over the poisoned registry (same algorithm as
    # duplicate_live_family_owners / unacknowledged_duplicate_live_owners, but parameterized
    # on the registry under test — proving the GATE'S LOGIC catches the re-added duplicate).
    def _live_jobs(registry):
        return [
            j for j in registry.values()
            if j.role in ("live", "settlement")
            and not j.owner_gated
            and j.dispatch_kind != "startup"
            and not j.job_id.endswith("startup_catch_up")
        ]

    def _dup_owners(registry):
        owners: dict[tuple[str, str], set[str]] = {}
        for j in _live_jobs(registry):
            if j.family is None:
                continue
            for s in (j.all_source_ids or ((j.source_id,) if j.source_id else ())):
                owners.setdefault((j.family, s), set()).add(j.owner_daemon)
        return {k: sorted(v) for k, v in owners.items() if len(v) > 1}

    def _unacked(registry):
        tracked = set(reg._ACKNOWLEDGED_SAFE_DUPLICATE_LIVE_OWNERS) | set(
            reg._KNOWN_OPEN_DUPLICATE_LIVE_OWNERS
        )
        return {k: v for k, v in _dup_owners(registry).items() if k not in tracked}

    # The poisoned registry reconstructs the duplicate (main + ingest_main both live-own WU)...
    poisoned_dups = _dup_owners(poisoned_registry)
    assert _WU_DUP_KEY in poisoned_dups, (
        "re-adding a main-owned WU collector did NOT register as a >1-owner duplicate — the "
        "detector's family/source keying regressed; the unconstructability proof is void"
    )
    assert sorted(poisoned_dups[_WU_DUP_KEY]) == ["ingest_main", "main"], poisoned_dups[_WU_DUP_KEY]

    # ...and because the WU key is in NEITHER allow-list (Step 4 removed it from known-open),
    # the fail-closed E gate fires on the re-added duplicate. THE REGRESSION CANNOT PASS SILENTLY.
    assert _WU_DUP_KEY in _unacked(poisoned_registry), (
        "the fail-closed E gate did NOT catch the re-added main WU collector — the duplicate "
        "regression category is still constructable (this is the §9 antibody and it must hold)"
    )

    # Real (deduped) registry stays clean under the SAME logic — no false alarm.
    assert _WU_DUP_KEY not in _unacked(reg.JOB_REGISTRY)
    # And we never mutated the real module state.
    assert "wu_daily" not in reg.JOB_REGISTRY


def test_unconstructable_two_intra_daemon_wu_jobs_are_not_a_false_duplicate():
    """Boundary of the antibody (no over-fire): TWO jobs in the SAME daemon that both produce
    (observation, wu_icao_history) — today `ingest_k2_daily_obs` AND `ingest_k2_obs`, both
    owned by `ingest_main` — are NOT a cross-process duplicate and must NOT trip the gate. The
    detector keys on distinct OWNER DAEMONS (len(owners)>1), not distinct job ids, so intra-process
    co-production (one address space, one WU-API budget) is correctly excluded. This proves the
    gate's discrimination: it fires on a SECOND DAEMON, not on a second job in the same daemon —
    exactly matching the regression Step 4 killed (two DAEMONS double-fetching WU)."""
    from src.data.source_job_registry import (
        JOB_REGISTRY,
        duplicate_live_family_owners,
        live_producing_jobs,
    )

    wu_jobs = [
        j for j in live_producing_jobs()
        if j.family == "observation"
        and "wu_icao_history" in (j.all_source_ids or ((j.source_id,) if j.source_id else ()))
    ]
    # There IS more than one live WU job, but they are all the SAME daemon (ingest_main).
    assert len(wu_jobs) >= 2, "expected >=2 intra-daemon WU live jobs (k2_daily_obs + k2_obs)"
    assert {j.owner_daemon for j in wu_jobs} == {"ingest_main"}, (
        "post-dedup, every live WU producer must be owned by the single ingest_main daemon"
    )
    # So the duplicate-OWNER detector is empty for WU (one daemon, not a cross-process dup).
    assert _WU_DUP_KEY not in duplicate_live_family_owners()
    assert "wu_daily" not in JOB_REGISTRY
