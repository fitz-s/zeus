# Created: 2026-07-23
# Last audited: 2026-07-23
# Authority basis: operator directive 2026-07-2x "清空7月之前所有的交易记录作为archive
#   不要再分析" (archive all pre-2026-07-01 trade records out of zeus_trades.db;
#   analytics must never consult them again). Companion to
#   scripts/ops/archive_pre_epoch_trades.py, which performs the physical archive.
"""ANALYSIS_EPOCH: the fixed boundary before which trade-history analysis must
never look, because the underlying rows have been physically archived out of
zeus_trades.db by scripts/ops/archive_pre_epoch_trades.py.

Existing ad-hoc --since/window flags across scripts/ (tradeable_edge_frontier.py
--since, verify_fill_e2e.py --since default, qkernel_arm_replay.py WINDOW_DAYS)
compute their lower bound relative to "now" or a fixed historical marker. Left
alone, a large relative window or a stale absolute default can resolve to a
timestamp before the epoch and try to read rows that no longer exist. Each
such site clamps its computed bound with a plain ``max(computed, EPOCH)``
string comparison — the codebase's existing convention for ISO8601 timestamp
ordering (see scripts/tradeable_edge_frontier.py:_parse_since: "sqlite TEXT
comparison works"). No parsing/config machinery is introduced; these are
inert string constants, one per timestamp shape already in use.

An explicit CLI override (e.g. --since with an operator-supplied date) is
never clamped here — only the *default* is epoch-floored, per the operator
instruction to wire the DEFAULT of each flag to this constant.
"""

from __future__ import annotations

# Tz-aware ISO8601 with numeric UTC offset — the shape produced by
# datetime.now(timezone.utc).isoformat() and by
# scripts/ops/archive_pre_epoch_trades.py's own --epoch default.
ANALYSIS_EPOCH = "2026-07-01T00:00:00+00:00"

# Naive ISO8601 (no offset) — the shape used by scripts whose --since default
# is a bare "YYYY-MM-DDTHH:MM:SS" string (e.g. verify_fill_e2e.py).
ANALYSIS_EPOCH_NAIVE = "2026-07-01T00:00:00"

# Date-only — the shape used by scripts computing a plain date.isoformat()
# window boundary (e.g. qkernel_arm_replay.py's WINDOW_DAYS).
ANALYSIS_EPOCH_DATE = "2026-07-01"
