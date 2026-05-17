# Phase 9A Contract — P8 Observability Absorption + DT#6 Interpretation B

**Written**: 2026-04-18 post P8 close (`73eba2b` on `origin/data-improve`).
**Branch**: `data-improve`.
**Mode**: Gen-Verifier (team-lead direct exec + critic-carol cycle 2).
**Predecessor**: P8 closed with 4 MAJOR observability forward-log items from critic-carol.

## Ruling applied

User 2026-04-18 on DT#6 ambiguity (critic-carol's Interpretation A vs B question):
"采取 B 方案吧，如果真的有问题，我们可以在主线完成后再评估" — adopt Interpretation B; re-evaluate after mainline complete if real problems surface.

User on P9 split approval:
"P9 既然已经完整拆分，现在可以开始实施了" — P9A → P9B → P9C split confirmed; start P9A now.

## Scope — ONE commit delivers

### Code (4 items)

#### S1 — MAJOR-1: `entries_blocked_reason` DATA_DEGRADED branch
- `src/engine/cycle_runner.py:281` — widen the elif tuple to include `RiskLevel.DATA_DEGRADED`, OR inject `entries_blocked_reason = "portfolio_loader_degraded"` inside the DT#6 branch at L180-195 before downstream logic runs.
- Preferred: add explicit assignment inside the DT#6 branch for operator-clarity (critic-carol's suggested reason-code is more informative than `risk_level=DATA_DEGRADED`).

#### S2 — MINOR M4: L195 overwrite-intent comment
- `src/engine/cycle_runner.py:195` — add a one-line comment explaining that `summary["risk_level"]` overwrite is intentional DT#6 semantics (degraded tick supersedes the pre-lookup from `get_current_level()`).

#### S3 — MINOR M2: `run_replay` sweep/audit-mode + metric mismatch warning
- `src/engine/replay.py:1956-1963` — when `mode in (WU_SWEEP_LANE, TRADE_HISTORY_LANE)` AND `temperature_metric != "high"`, emit `logger.warning(...)`. Drop the kwarg silently today; P9A loudens the seam.

#### S4 — MAJORs 3+4: Harden R-BQ.1 antibody
- `tests/test_phase8_shadow_code.py` — rewrite `test_run_cycle_degraded_portfolio_does_not_raise_runtime_error`:
  - Drop the literal-string match (L228) — replaced with "no RuntimeError of any kind escapes DT#6 branch" structural assertion
  - Drop the silent-return path (L236) — all assertions must run unconditionally
  - Harden dummy stubs (`_DummyConn._C`) so downstream code completes without raising (add `lastrowid`, `rowcount`, `__iter__`, etc. as needed)

### Documentation (1 item)

#### S5 — MAJOR-2: DT#6 Interpretation B clarification in authority doc
- `docs/authority/zeus_dual_track_architecture.md §6 DT#6` — append a clarification paragraph:
  - Interpretation B adopted: "read-only" means **no NEW canonical-state entries** (position creation, new risk policy changes); NOT "no JSON cache refresh at all".
  - Cache writes (positions-cache.json, tracker, status_summary) MAY proceed in degraded mode PROVIDED the `PortfolioState.authority` tag is propagated faithfully (`"degraded"` stays `"degraded"` on disk; readers responsible for honoring).
  - `tick_with_portfolio` is advisory — does not persist to `risk_state.db`. Future tick_* variants should either persist or document ephemeral explicitly.
  - Operator visibility: `summary["portfolio_degraded"]=True` + `summary["entries_blocked_reason"]="portfolio_loader_degraded"` + `summary["risk_level"]="DATA_DEGRADED"` — three-signal redundancy.

### New antibodies (2 items)

#### S6 — R-BS: authority-tag propagation in save_portfolio under degraded mode
- NEW test in `tests/test_phase8_shadow_code.py` (or new `test_phase9a_dt6_b_semantics.py` if test file pollution):
- Construct `PortfolioState(positions=[...], authority="degraded", portfolio_loader_degraded=True)` → `save_portfolio(state)` → re-load → assert `loaded.authority == "degraded"`.
- Antibody locks: provenance trap (critic-carol finding 6) is impossible — JSON-refresh in degraded mode **cannot** silently promote authority back to `"canonical_db"`.

#### S7 — R-BT: entries_blocked_reason populated in DT#6 branch
- NEW test: monkeypatch load_portfolio to return degraded; run_cycle; assert `summary["entries_blocked_reason"] == "portfolio_loader_degraded"` (or whatever S1 chose).

## Acceptance gates

1. **Full regression ≤ baseline**: 144 failed / 1846 passed / 95 skipped (post-P8 73eba2b). Post-P9A: ≤144 failed, ≥1848 passed (+2 from R-BS/R-BT), zero new failures.
2. **P8 antibodies still GREEN**: R-BP.1/2 + hardened R-BQ.1/2 all pass.
3. **P5/P6/P7A/P7B targeted suites unchanged-green**.
4. **critic-carol cycle 2 PASS**.
5. **Hard constraints preserved from P8**: no TIGGE import, no v2 writes, no DDL, no evaluator changes, no monitor_refresh changes, Golden Window preserved.

## Hard constraints (forbidden moves)

- NOT scope: DT#2 / DT#5 / DT#7 (those are P9B).
- NOT scope: Gate F activation prep (P9C).
- NOT scope: `monitor_refresh.py` LOW wiring (P9C).
- NOT scope: B093 half-2 data migration (P9C, blocks on Golden Window lift).
- NOT scope: CLI flag `--temperature-metric` on `scripts/run_replay.py` (deferred MINOR; tiny, P9C hygiene).
- No new v2 table writes. No SQL DDL. No TIGGE data import.

## P3.1 guard-removal vocab check

S4 (R-BQ.1 hardening) REPLACES an antibody, not removes a guard. P3.1 applies: grep test-naming vocabulary. S4 strengthens R-BQ.1 without changing behavior under test (still antibody-GREEN for the same code).

Additionally: DT#6 doc update (S5) does not remove any guard — it clarifies an under-specified law. P3.1 check clean.

## R-letter range

P9A uses **R-BS + R-BT** (new). R-BQ.1/2 modified in-place (same letters, improved shape).

## Critic-carol cycle-2 brief (fresh Agent spawn on commit)

Continuation of cycle 1 (she passed P8 first-try). She inherits her own cycle-1 learnings + the 7 new learnings she catalogued. Pre-commitment predictions required. If Write/Edit blocked again, return content in message for team-lead to persist.

Focus areas:
- S4 antibody hardening — is the rewrite actually structural or still text-match-flavored?
- S5 doc clarification — is Interpretation B actually unambiguous, or did a new ambiguity slip in?
- R-BS provenance antibody — does it actually exercise the save_portfolio+reload round-trip, or just the authority-field setter?
- Regression math: 144 → 144 ≤ expected; verify zero new failures

## Forward-log remaining post-P9A

Goes into P9B (DT#2/#5/#7) or P9C (Gate F prep) per master plan:
- MINOR M1: `--temperature-metric` CLI flag
- TEST-GAP 1: `_forecast_reference_for(metric="low")` selects `forecast_low` column (needs data presence, P9C)
- TEST-GAP 2: `status_summary.json` reflects DATA_DEGRADED (needs persistence fix or documented ephemeral)
- TEST-GAP 4: rollback path if `tick_with_portfolio` itself raises
- DT#2 RED force-exit
- DT#5 Kelly executable-price
- DT#7 boundary-day settlement (already law-documented; needs code antibody)
- `Day0LowNowcastSignal.p_vector` proper impl pre-Gate F
- `monitor_refresh.py` LOW wiring
- B093 half-2 (blocks on Golden Window lift)

## Evidence layout

- This contract: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase9a_contract.md`
- Evidence dir: `docs/operations/task_2026-04-16_dual_track_metric_spine/phase9_evidence/` (shared across P9A/B/C)
- critic-carol cycle 2 wide review: `phase9_evidence/critic_carol_phase9a_wide_review.md`
- critic-carol cycle 2 learnings: `phase9_evidence/critic_carol_phase9a_learnings.md`

---

*Authored*: team-lead (Opus, main context), 2026-04-18 post-P8 close.
*Authority basis*: critic-carol P8 forward-log (4 MAJOR) + user DT#6 B ruling + P9 split approval.
