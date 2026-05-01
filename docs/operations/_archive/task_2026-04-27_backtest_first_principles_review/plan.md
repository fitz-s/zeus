# Backtest First-Principles Review — Top-Level Plan

Created: 2026-04-27
Last reused/audited: 2026-04-27
Authority basis: `zeus/AGENTS.md` §1 (money path, probability chain), `docs/operations/current_state.md` (mainline = midstream remediation, P4 BLOCKED), `docs/reports/authority_history/zeus_live_backtest_shadow_boundary.md`, `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
Status: planning evidence; not authority. Does not mutate code/DB/manifests.
Branch: `claude/mystifying-varahamihira-3d3733`

---

## 0. Purpose

This packet exists because the existing replay/backtest stack at [src/engine/replay.py](../../../src/engine/replay.py) (2382 lines) is a hybrid state machine that mixes three structurally distinct goals into one module under the umbrella of `BACKTEST_AUTHORITY_SCOPE = "diagnostic_non_promotion"`. The result: every consumer is uncertain what a replay run actually proves, and the path to lift `diagnostic_non_promotion` is not enumerable.

This is a Fitz Constraint #1 case (structural decisions > patches). The 39 forensic + adversarial findings against backtest collapse into **K=4 structural decisions**, K << 39.

This packet does NOT implement. It produces three on-disk authority-aligned design artifacts that the next implementation packets can reference:

1. **`01_backtest_upgrade_design.md`** — the typed structural redesign (purpose-split, sentinel sizing, decision-time provenance enforcement, file split).
2. **`02_blocker_handling_plan.md`** — explicit blocker triage with type/owner/unblock-criteria for each, plus parallel-track sequencing.
3. **`03_data_layer_issues.md`** — disk-verified data-layer issues with row-level evidence, prioritized by what unblocks each backtest purpose.

---

## 1. Reality calibration (verified against disk + external 2026-04-27)

This packet's premises were calibrated against external reality before writing, not just repo internals:

| Claim | Verification | Truth |
|---|---|---|
| `forecasts` table empty | live SQL probe | **23,466 rows** (handoff doc was already stale) |
| `forecast_issue_time` recorded | live SQL probe | **NULL on every row** — F11 hindsight risk realised |
| `raw_payload_hash` recorded | live SQL probe | **NULL on every row** — F16 / F18 confirmed |
| Empty-provenance distribution | live SQL probe | **39,431/39,437 = 99% of `wu_icao_history` rows** ; ogimet+hko sources have 0% empty |
| `market_events` populated | live SQL probe | **0 rows** (all 3 tables) — F13 confirmed |
| `zeus_trades.db` has trade history | live SQL probe | **0 rows in every table** — `trade_history_audit` lane has nothing to audit |
| Polymarket weather market count | external (polymarket.com) | **361 live temperature markets 2026-04-27** |
| Polymarket US resolution source | external (polymarket.com/event/* × 4 cities verbatim) | **Wunderground (KLGA/KORD/KMIA/KLAX)** — Zeus assumption verified correct. ⚠ See [04 §C3](04_corrections_2026-04-27.md#c3-polymarket-us-weather-market-resolution-source) — earlier "NOAA" claim was WebSearch hallucination, retracted |
| Polymarket public price-history feed | external (multiple primary sources) | **4 layers exist**: Gamma API (Zeus uses), public Subgraph (6 sub-subgraphs incl. orderbook), Data API REST `/trades`, WebSocket Market Channel. ⚠ See [04 §C4](04_corrections_2026-04-27.md#c4-polymarket-no-public-historical-archive-api) — earlier "no archive API" was wrong |
| ECMWF ENS dissemination lag | external (confluence.ecmwf.int/display/DAC/Dissemination+schedule verbatim) | **Day 0 ENS = base + 6h40m**; Day 1 = +6h44m; Day 15 = +7h40m (linear ~4 min/day). ⚠ See [04 §C1](04_corrections_2026-04-27.md#c1-ecmwf-ens-dissemination-lag) — earlier "40 minutes" was wrong (misread "40 min earlier" delta as absolute) |
| ECMWF ENS member count | external (ecmwf.int/en/forecasts/documentation-and-support/medium-range-forecasts verbatim) | **51 (50 perturbed + 1 control)**; HRES is separate. **Zeus's `primary_members: 51` is correct.** ⚠ See [04 §C2](04_corrections_2026-04-27.md#c2-ecmwf-ens-member-count) — earlier "52" was wrong (TC tracks product confusion) |
| Oracle shadow snapshot coverage | disk count | **48 cities × 10 dates (2026-04-15 to 2026-04-26)** — does NOT overlap with most settlements |
| Settlement temperature_metric | live SQL probe | **100% high (0 low rows)** — C4 confirmed |

External evidence summary lives in `evidence/reality_calibration.md`.

---

## 2. Document index

| File | Purpose | Audience |
|---|---|---|
| [01_backtest_upgrade_design.md](01_backtest_upgrade_design.md) | Structural redesign: 3-purpose split, typed contracts, file decomposition | Implementer + critic |
| [02_blocker_handling_plan.md](02_blocker_handling_plan.md) | Per-blocker triage (type / owner / unblock-criteria), parallel tracks | Operator + team-lead |
| [03_data_layer_issues.md](03_data_layer_issues.md) | Data-layer concrete issues with row-count evidence, ranked by unblocking power | Data engineering + operator |

---

## 3. Out of scope

- Implementing any redesigned module. Each implementation lands in its own packet (`task_2026-04-27_backtest_purpose_split_part_*`).
- Mutating `state/zeus-world.db` or `state/zeus_trades.db` rows.
- Authorizing live promotion of any replay-derived metric.
- Polymarket data ingestion (the `market_events_v2` populate path) — that is a separate data-engineering packet (forensic P4.A).
- LOW-track settlement writer (forensic C4) — separate packet.
- TIGGE local rsync or P4 readiness re-run — operator/data-engineering.

---

## 4. Authority and governance posture

- **Planning lock**: this packet plus the three sub-docs touch only `docs/operations/task_2026-04-27_backtest_first_principles_review/**`. No code, no DB, no manifest, no `architecture/**`. Per `zeus/AGENTS.md` §3 the planning-lock check is informational; this packet does not require lock evidence because it does not modify governed surfaces.
- **Mesh maintenance**: future implementation packets MUST update `architecture/source_rationale.yaml` (when adding new src files), `architecture/test_topology.yaml` (when adding tests), and `architecture/script_manifest.yaml` (when adding scripts). This packet does not.
- **Memory L20 grep-gate**: every file:line citation in the sub-docs was verified within the writing window via fresh `grep`/`Read`. Premise-rot rate measured on this packet: 0% (sample size small but disk fully re-probed).
- **Memory L22 commit boundary**: implementation packets MUST NOT auto-commit before critic review (con-nyx primary, surrogate `code-reviewer@opus` parallel).
- **Memory L24 git scope**: never `git add -A` with co-tenant active; commit only files inside this packet folder.

---

## 5. Decision request to operator

Before any implementation packet derived from these designs lands, operator must answer five questions (each labelled in the relevant sub-doc):

| # | Question | Blocking | In doc |
|---|---|---|---|
| Q1 | Adopt purpose-split (`SKILL` / `ECONOMICS` / `DIAGNOSTIC`) as typed contract, or keep single replay module? | Whole upgrade design | 01 §3 |
| Q2 | For decision-time-truth typing: hard reject `RECONSTRUCTED` provenance, or annotate-and-allow? | F11 antibody | 01 §5 |
| Q3 | Polymarket data ingestion source: live websocket capture going forward, or third-party historical archive (paid), or both? | Economics-grade backtest | 02 §3.B |
| Q4 | LOW-track settlements: build a parallel writer now (data-engineering effort) or defer until v2 cutover? | LOW-track backtest | 02 §3.D |
| Q5 | Empty-provenance WU rows (39,431): quarantine all, or scope a partial backfill from oracle_shadow + WU log replay? | Training readiness | 02 §3.A, 03 §2 |

---

## 6. Provenance and re-audit triggers

This plan must be re-audited if:

- Any of the v2 tables (`market_events_v2`, `ensemble_snapshots_v2`, `calibration_pairs_v2`, `settlements_v2`) becomes non-empty.
- The forensic P0→P4 sequencing in `zeus_world_data_forensic_audit_package_2026-04-23/` is replaced by a newer audit.
- `BACKTEST_AUTHORITY_SCOPE` is lifted from `diagnostic_non_promotion` by an authority packet.
- Polymarket changes its CTF / neg-risk / settlement model materially.
- Zeus onboards LOW-track markets in production.
