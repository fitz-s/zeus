# Forecast-DB "database is locked" storm → BEGIN IMMEDIATE (the q-input starvation root)

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: GOAL #83 (continuous settlement-graded alpha) + RULE 1 (a candidate that
  cannot be priced is OUR defect, not absent edge). Follows the boot-crash-loop fix
  (boot_presence_reconcile_2026-06-16.md) — same #122 db-lock disease, different victim.

## Defect (live, evidenced)

After the trading daemon was un-crash-looped, it cycled but produced **zero crosses**. A
recurring rejection was `LIVE_INFERENCE_INPUTS_MISSING: REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE#
BAYES_PRECISION_FUSION_CAPTURE_MISSING` — live candidates could not be priced because the
precision-fusion **capture** was absent. Root cause upstream in the forecast daemon
(`com.zeus.forecast-live`): the replacement-forecast **materialize** was failing with
`database is locked` — **348 occurrences in the last 2000 err lines**, ~25 cities/cycle
(Houston, Hong Kong, London, Karachi, Beijing, …) failing to produce captures/posteriors for
target_date 2026-06-17.

Why busy_timeout did NOT help (the subtle part): `ZEUS_DB_BUSY_TIMEOUT_MS=300000` (5 min) is
set, yet the materialize failed **immediately**. `zeus-forecasts.db` runs in rollback-journal
(`journal_mode=delete`) mode — WAL was deliberately disabled (db.py header: WAL re-bloat). In
delete mode a **deferred** `BEGIN` takes a SHARED lock on the first SELECT and then must
upgrade SHARED→RESERVED/EXCLUSIVE on the first INSERT; if any other connection wrote in
between, SQLite raises `SQLITE_BUSY` IMMEDIATELY and **busy_timeout cannot retry a deferred-
upgrade conflict**. So the 5-min timeout was inert.

## Change (one-line root fix, ×3 hot forecast-DB writers)

Replace deferred `BEGIN` with **`BEGIN IMMEDIATE`** on the three hot writers of
`zeus-forecasts.db` so the write lock is taken at BEGIN — busy_timeout then WAITS (up to 300s)
for the lock instead of failing on the upgrade. Readers are unaffected; the live writer-flock
already serializes same-class writers. No WAL change (respects the deliberate delete-mode
decision). No new cap/gate.

- `scripts/materialize_replacement_forecast.py:268` (the evidenced hot failure — spawned
  fresh each 5-min cycle, so the fix is live immediately, no daemon restart).
- `src/data/bayes_precision_fusion_download.py:830` (download persist; already retry-looped —
  IMMEDIATE makes each attempt clean).
- `scripts/download_replacement_forecast_current_targets.py:529` (manifest writes).

The two module/script writers under `forecast-live` take effect on the daemon's next reload.

## Reversibility / safety

Pure transaction-isolation change (deferred→immediate); identical writes, identical commit. It
cannot corrupt or over-write; worst case it waits longer for a contended lock (bounded by the
300s busy_timeout) instead of failing fast. Rollback: `git revert`.

## Verification

- Baseline: 11 `materialize[...] database is locked` in the prior ~30 min.
- Expected after: materialize lock failures → ~0; `failed_count` per materialize cycle drops;
  precision-fusion captures persist for the 25 previously-failing cities → the trader's
  `BAYES_PRECISION_FUSION_CAPTURE_MISSING` rejections clear → those candidates become priceable.
  (Pricing ≠ a cross: a priced candidate still needs genuine positive after-cost edge. This fix
  removes the INPUT-starvation suppressor so real edge, where it exists, can reach a decision.)
