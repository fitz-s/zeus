# Created: 2026-05-04
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-04_strategy_redesign_day0_endgame/PLAN_v3.md §P0 — APScheduler UTC invariant;
# docs/operations/task_2026-05-15_live_order_e2e_verification/LIVE_ORDER_E2E_VERIFICATION_PLAN.md live scheduler collision proof.
"""P0 antibody: ``BlockingScheduler`` in ``src/main.py`` MUST be
constructed with ``timezone=ZoneInfo("UTC")``.

Without an explicit timezone= kwarg APScheduler falls back to the host
machine's local timezone. The deployment box runs America/Chicago
(CDT/CST), which means every cron expression written in UTC — including
``update_reaction_times_utc`` cron jobs at hours 07/09/19/21 UTC — would
fire 5h shifted (CDT) / 6h shifted (CST) from the intended wall-clock.

Operator directive 2026-05-04: "所有的执行时间都需要严格统一用utc，我们
的交易系统遍布全球，必须采用同一个时间语义在不同时区的表达". The Zeus
trading system spans 51 cities across all 24 timezones; UTC is the only
viable canonical execution clock.

This test is structural — it AST-walks ``src/main.py`` resolved via the
repo root, the same approach used by ``tests/test_main_module_scope.py``.
It deliberately does NOT ``import src.main``, because that triggers
module-scope side effects (config JSON load, logging setup, etc.) that
would make the test slow and coupled to runtime config files.
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MAIN_FILE = REPO_ROOT / "src" / "main.py"


def _parse_main() -> ast.Module:
    return ast.parse(
        MAIN_FILE.read_text(encoding="utf-8"),
        filename=str(MAIN_FILE),
    )


def test_main_blocking_scheduler_constructed_with_utc_zoneinfo() -> None:
    tree = _parse_main()

    blocking_scheduler_calls = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BlockingScheduler"
        )
    ]
    assert len(blocking_scheduler_calls) == 1, (
        f"expected exactly one BlockingScheduler(...) construction site in "
        f"{MAIN_FILE}; found {len(blocking_scheduler_calls)}. If the daemon "
        f"now spawns multiple schedulers, every site needs the UTC kwarg."
    )

    call = blocking_scheduler_calls[0]
    tz_kw = next((kw for kw in call.keywords if kw.arg == "timezone"), None)
    assert tz_kw is not None, (
        "BlockingScheduler() must be constructed with an explicit "
        "timezone=ZoneInfo('UTC') kwarg. Without it APScheduler falls "
        "back to the host's local timezone (America/Chicago on the live "
        "deployment box), and every cron-job hour value is reinterpreted "
        "as local rather than UTC — shifting all firings by 5-6 hours."
    )

    value = tz_kw.value
    assert (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "ZoneInfo"
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Constant)
        and value.args[0].value == "UTC"
    ), (
        "BlockingScheduler(timezone=...) must use ZoneInfo('UTC'). Other "
        "encodings (pytz.utc, datetime.timezone.utc, the string 'UTC') are "
        "rejected by this antibody so the canonical execution-tz idiom "
        f"stays uniform across the codebase. Got: {ast.dump(value)}"
    )


def test_zoneinfo_imported_at_module_level() -> None:
    """The UTC kwarg is only as good as its import site. Ensure
    ``src/main.py`` imports ``ZoneInfo`` from ``zoneinfo`` at the top
    level so a later refactor cannot accidentally shadow it.
    """
    tree = _parse_main()

    found = False
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "zoneinfo":
            for alias in node.names:
                if alias.name == "ZoneInfo" and alias.asname is None:
                    found = True
                    break
        if found:
            break

    assert found, (
        f"{MAIN_FILE} must contain a top-level "
        "``from zoneinfo import ZoneInfo`` import so the BlockingScheduler "
        "tz kwarg resolves correctly. If you must alias, update this test."
    )


def _scheduler_add_job_calls_by_id(tree: ast.Module) -> dict[str, ast.Call]:
    calls: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
        ):
            continue
        id_kw = next((kw for kw in node.keywords if kw.arg == "id"), None)
        if id_kw is None or not isinstance(id_kw.value, ast.Constant):
            continue
        if isinstance(id_kw.value.value, str):
            calls[id_kw.value.value] = node
    return calls


def test_interval_discovery_jobs_are_phase_staggered() -> None:
    """Opening hunt and Day0 must not race on the same interval instant."""

    calls = _scheduler_add_job_calls_by_id(_parse_main())
    next_run_values: dict[str, ast.expr] = {}
    for job_id in ("opening_hunt", "day0_capture"):
        call = calls[job_id]
        next_run_kw = next((kw for kw in call.keywords if kw.arg == "next_run_time"), None)
        assert next_run_kw is not None, (
            f"{job_id} must set next_run_time explicitly. Without a first-run "
            "phase, opening_hunt and day0_capture share the same 15-minute "
            "interval instant after daemon restart; the process-local cycle "
            "lock then drops one live cycle by race."
        )
        value = next_run_kw.value
        assert (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "_utc_run_time_after"
        ), f"{job_id} next_run_time must be computed through _utc_run_time_after(...)."
        next_run_values[job_id] = value

    opening_delay = next_run_values["opening_hunt"]
    assert isinstance(opening_delay, ast.Call)
    assert (
        len(opening_delay.args) == 1
        and isinstance(opening_delay.args[0], ast.Name)
        and opening_delay.args[0].id == "OPENING_HUNT_FIRST_DELAY_SECONDS"
    )

    day0_delay = next_run_values["day0_capture"]
    assert isinstance(day0_delay, ast.Call)
    assert (
        len(day0_delay.args) == 1
        and isinstance(day0_delay.args[0], ast.Call)
        and isinstance(day0_delay.args[0].func, ast.Name)
        and day0_delay.args[0].func.id == "_day0_first_delay_seconds"
    )


def test_day0_stagger_helper_offsets_by_half_interval() -> None:
    source = MAIN_FILE.read_text(encoding="utf-8")
    assert "return OPENING_HUNT_FIRST_DELAY_SECONDS + (interval_seconds / 2.0)" in source
