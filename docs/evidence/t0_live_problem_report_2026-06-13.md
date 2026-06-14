# T0 "Live Problem" Report — re-probe verdict
# Created: 2026-06-13
# Last reused or audited: 2026-06-13
# Authority basis: operator request during divergent simplify-scan task; direct re-verification against working tree HEAD + codegraph + live process map
# Status: 3 of 4 RESOLVED by direct probe; 1 PENDING live-log (W2 runtime lens, in flight)

## TL;DR (the headline is the method, not the bugs)

The divergent-discovery sweep (lens 3) flagged **4 "T0 bleeding-now" live bugs**. On direct
re-verification against the current working tree, codegraph, and the live process map:

| Item | Lens claim | Re-probe verdict | Action |
|---|---|---|---|
| **T0-2** day0_fast_obs per-city conn fan-out in reactor write-lock | "unpatched, 283 err/hr" | **FIXED** — `world_conn` now mandatory, fallback deleted (commit `347f713d`), antibody + evidence doc in place | none |
| **T0-4** ERA live stub returns empty dicts → silent no-trade | "loaded gun on live path" | **DEAD ORPHAN** — codegraph + grep find **zero callers**; and it is fail-closed *with an explicit reason dict*, not a silent mistrade | delete orphan (low pri) |
| **T0-3** bias-correction flag ON/OFF divergence engine vs harvester | "live twin-authority, on and off system-wide" | **NOT A BUG** — two *different* flags (`edli.edli_bias_correction_enabled`=ON live-chain vs top-level `bias_correction_enabled`=OFF baseline-diagnostics); ON/OFF split is consistent with baseline=diagnostics-only. A **naming-drift false positive** (dimension #6) | rename one flag to kill the collision; confirm harvester:2125 intent |
| **T0-1** riskguard conn held across bankroll/wallet IO | "bleeding now, 2113 BLOCKED/17h" | **MITIGATED STRUCTURAL RISK** — conn IS held across `bankroll_provider.current()` (riskguard.py:1410/1418 → 1434), BUT `.current()` is 30s-cached + fail-fast busy_timeout + the WAL-leak was fixed 2026-05-10. Realized "bleeding" claim **UNCONFIRMED** | **PENDING** W2 live-log + lsof verdict |

**Net: zero confirmed-bleeding-now after re-probe.** Re-probing prevented an Nx-debug chase on 3 non-bugs.

> **Operating lesson (the real T0 finding):** the divergent-discovery sweep is an excellent **breadth
> engine** — it surfaces candidate fault-lines no structural-size view can see. But its **severity and
> liveness labels are unreliable** (stale HEAD, naming collisions read as divergence, dead code read as
> live). Every candidate MUST be re-probed against live code/process/log before any fix is attempted.
> This is the operator's "decide on re-probed reality, not memory" law, and it cuts *both* directions:
> the sweep under-counted some counts (main.py 152 bare-except not 38) and over-counted severity here.

---

## Per-item detail

### T0-2 — day0_fast_obs connection-burst — FIXED ✓
`src/data/day0_fast_obs.py:971` `_recover_kill_memo_from_events` now takes `world_conn` as a required
kwarg and raises `RuntimeError` if `None` (`:996-1001`). Docstring (`:987-994`): the old "open a fresh
connection when None" fallback was **DELETED** because it caused the 47-simultaneous-per-city
connection burst inside the reactor write lock (commit `347f713d`). Antibody + archaeology doc:
`docs/evidence/lock_storm/2026-06-13_lock_storm_regression_archaeology.md`. The lens read pre-fix state.

### T0-4 — ERA EDLI probability stub — DEAD ORPHAN (not live)
`src/engine/event_reactor_adapter.py:10349` `_forecast_snapshot_probability_and_fdr_proof` is a
FAIL-CLOSED STUB returning empty mappings. **codegraph_callers → "No callers found"**; grep across
`src/**` finds no call site. So no live family routes through it. Even if reached, it returns an
explicit `probability_evidence = {status: no_submit_fail_closed, reason: edli_probability_kernel_unauthored}`
— distinguishable in audit, not a silent mistrade. Severity: dimension #18 (stale rebase residue).
Recommendation: delete the orphan (it is a misleading loaded-looking gun with no trigger wired).

### T0-3 — bias-correction "divergence" — NAMING-DRIFT FALSE POSITIVE
Two distinct flags, near-identical names:
- `edli.edli_bias_correction_enabled` = **true** (settings.json:86). Consumed only in ERA
  (`:11712`, `:12599`): subtracts per-city `model_bias_ens` from `_snapshot_p_raw` on the **live EDLI chain**.
- top-level `bias_correction_enabled` = **false** (settings.json:284). Consumed in `src/main.py:5065`,
  `src/execution/harvester.py:2125`, `src/signal/ensemble_signal.py:499` — the **legacy baseline chain**.

Baseline is diagnostics-only since 2026-06-12 → baseline bias OFF is correct; EDLI bias ON is correct.
**Not a live divergence.** The bug is the *name collision* (dimension #6 vocabulary drift) that made a
reader (and almost this author) see a twin-authority. Residual to confirm: whether `harvester.py:2125`
(settlement attribution) should track the EDLI flag rather than the baseline flag — needs a semantic
trace, low urgency. Fix: rename `bias_correction_enabled` → `baseline_bias_correction_enabled`.

### T0-1 — riskguard connection held across wallet IO — MITIGATED; PENDING live confirmation
`src/riskguard/riskguard.py`: `zeus_conn` (`:1410`) and `risk_conn` (`:1418`) are opened, then
`bankroll_provider.current()` is called at `:1434` while both are held (finally-closed later in `_tick_once`).
`bankroll_provider.current()` (`src/runtime/bankroll_provider.py`) is **30s-cached**: fresh cache (<30s)
returns with no network; stale cache triggers a Polymarket wallet fetch. RiskGuard tick is ~60s, so a
network fetch likely fires roughly every other tick *while the conns are held* — the dimension-#4
conn-across-IO shape. Mitigations already present: (a) short `busy_timeout` so contended reads fail
fast (`:1415`); (b) the WAL reader-handle leak was fixed 2026-05-10 (`:1401-1406`); (c) 30s cache caps
fetch frequency; (d) fail-closed-to-DATA_DEGRADED on wallet-unreachable (`:1435-1462`).

**Unconfirmed:** whether this produces real lock contention now. The lens's "2113 RISK_GUARD_BLOCKED /
17h on 2026-06-13" figure is NOT yet verified against a live log. The W2 runtime lens (lsof on PID 1175,
WAL size, recent riskguard log) is in flight and will return the realized-cost verdict. If confirmed
bleeding, the durable fix is structural (per dimension #4 / root δ): a connection-lifetime context
manager that forbids network IO while a write-class conn is held — i.e. fetch bankroll BEFORE opening
conns, or release-before-fetch — NOT a third hand-patch of the same class.

---

---

## UPDATE (runtime wave) — the REAL T0 risks (these supersede the 4 above)

The runtime lens (live `mode=ro` DB introspection + lsof on the running daemons, this session) found the
actual bleeding — none of it visible to any code read. **The 4 lens-flagged items were mostly noise; these
are the ones.** All re-confirmed live this session (WAL sizes via `ls`, row-counts via `mode=ro`, CHECK via
`sqlite_master`, FDs via `lsof -p`; no writes issued).

### BLEEDING NOW
- **T0-A — `state/zeus_trades.db-wal` = 6.42 GB and growing; no checkpoint job exists.** World DB has
  `world_wal_checkpoint` (main.py:6730, 90s); `grep trade.*checkpoint` → **0 matches**. On the 17 GB
  trade-critical DB. Outcome: disk-fill, or a multi-minute WAL-replay cold-start outage on next restart.
  **Real fix (cheap):** add a `trade_wal_checkpoint` scheduler job mirroring the world one.
- **T0-B — world_class tables physically in the wrong partition.** Live row-counts:
  `decision_log` trades **10965** / world **0** (incrementing); `collateral_ledger_snapshots` **47008** / **0**;
  `token_suppression_history` **18917** / **0** (wrote today). Writers route to the trade conn
  (cycle_runtime.py:2831, main.py:5044, cycle_runner.py:77) though the YAML declares them world_class. Any
  consumer reading via `get_world_connection()` gets **zero rows → silently "no collateral history / no
  suppression"** = correctness-of-money fail-soft. **This is the strongest provenance signal of the whole
  audit** (label says world, disk says trade).
- **T0-C — checkpoint starvation is structural.** PID 24982 holds **9 simultaneous** `zeus_trades.db`
  handles (lsof FDs 5u/9u/13u/20u/33u/39u/42u/52r/61u). SQLite can't checkpoint past the oldest reader →
  even adding a TRUNCATE job returns BUSY until the reader-fan releases. Root cause of T0-A.

### LOADED GUN — aimed at the go-live transition
- **`forecast_posteriors` CHECK time-travel.** Live DDL `CHECK (trade_authority_status IN ('SHADOW_ONLY',
  'SHADOW_VETO_ONLY'))`; code DDL (v2_schema.py:303) `IN ('SHADOW_ONLY')`; provenance module allows a third
  value `LIVE_AUTHORITY` — which **violates both**. All 3251 rows are SHADOW_ONLY today (silent). **It fires
  the first time the engine flips to LIVE_AUTHORITY: the posterior INSERT is CHECK-rejected, the write
  fails, the trade silently does not persist = "0 orders, no error."** Armed precisely for the live
  transition the operator is driving toward. **Must widen the live CHECK to include `LIVE_AUTHORITY` BEFORE
  any live-flip.** Highest go-live relevance.
- **UMA conn-across-Polygon-RPC** — dormant by an in-process latch only; a daemon restart re-arms the
  world-writer-lock-held-across-RPC 4h-lock-storm shape (ingest_main.py:1230 → uma_resolution_listener.py:727).
- **bankroll_provider 4-conn compound hold** (the corrected T0-1) — riskguard holds 2 conns → live Polymarket
  HTTP inside a `threading.Lock` (bankroll_provider.py:538-559) → `_fetch_balance` opens **2 more** conns
  (L393-406), every 30s on cache-miss. Survivable today; a latency spike blocks all bankroll callers.

### 5th generative root (runtime-only): **ε — partition ownership is a YAML *assertion*, not a structural *constraint*.**
The `db_table_ownership.yaml` manifest is descriptive, not prescriptive: nothing enforces that a table
declared `world_class` actually receives its writes in zeus-world.db. ε generates T0-B + the source_run
dual-authority + the decision_log shell + the `legacy_archived`-vs-live mislabel — four findings, one root.
ε is to storage layout what β (no typed value contracts) is to values: *the contract exists as text, not
as enforced structure.*

### Immediate-action shortlist (cheap, high-value, mostly independent of the refactor)
1. **Widen the `forecast_posteriors` CHECK to include `LIVE_AUTHORITY`** (additive migration) — before go-live. **Blocks the goal otherwise.**
2. **Add `trade_wal_checkpoint`** scheduler job (mirror world) — stops T0-A disk/cold-start risk.
3. **Add `ruff` E722** to CI — bans bare `except` repo-wide; ~0 cost, the most memory-logged zero-order
   incidents trace to broad-except (antibody backlog row 1).
4. **partition-ownership pytest** — assert each YAML-declared table's live row-count is 0 in every non-owning
   DB; catches T0-B class on regression.
(Reader-fan ownership for T0-C needs per-job conn-lifetime instrumentation — deferred, prerequisite to a real WAL-truncate fix.)

Full evidence + antibody backlog + first-principles design seeds: `.omc/research/tangle_simplify_deepmap_2026-06-13.md` (lens 4).
