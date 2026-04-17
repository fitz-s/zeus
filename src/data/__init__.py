# B017 / SD-H: data-provenance migration note.
#
# The Gamma market scanner (src/data/market_scanner.py) used to return a
# bare ``list[dict]`` from ``_get_active_events``. On network failure it
# silently returned a stale cache, making freshness invisible to every
# downstream caller. It now exposes:
#   - ``MarketSnapshot`` dataclass with an ``authority`` literal:
#     ``VERIFIED`` | ``STALE`` | ``EMPTY_FALLBACK`` | ``NEVER_FETCHED``.
#   - ``_get_active_events_snapshot()`` returning MarketSnapshot.
#   - ``get_last_scan_authority()`` module helper for callers that
#     cannot yet migrate to the snapshot API.
# The legacy functions (``find_weather_markets``, ``get_current_yes_price``,
# ``get_sibling_outcomes``, ``_get_active_events``) keep their original
# return types so Dual-Track callers (src/engine/cycle_runtime.py,
# src/engine/monitor_refresh.py) remain untouched during the refactor.
# Once Dual-Track stabilises, those callers SHOULD branch on
# ``get_last_scan_authority()`` or switch to the snapshot API to
# fail-closed on ``STALE`` and ``EMPTY_FALLBACK`` before emitting new
# BUY/SELL signals. See audit bug B017 for the full rationale.
