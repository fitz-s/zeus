# Slice K4.U1-antibody — HK floor-rounding regression antibody

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: parent `docs/operations/task_2026-04-26_live_readiness_completion/plan.md` Wave 1 + workbook U1 (archived) + memory L20 (grep-gate file:line within 10 min) + memory L21 (activate vs extend).
Status: planning evidence; implementation has NOT begun.
Branch: `claude/live-readiness-completion-2026-04-26`
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus-live-readiness-2026-04-26`

## 0. Scope statement

Add the workbook-required antibody (`tests/test_hk_settlement_floor_rounding.py`) pinning that `SettlementSemantics.for_city(hk_city)` returns a semantics that floor-truncates HKO 0.1°C values. Code half + constitutional half BOTH already absorbed by predecessors — only the regression antibody remains.

## 1. Audit results (2026-04-26 grep-gate per memory L20)

| Workbook U1 deliverable | Pre-existing artifact | Disposition |
|---|---|---|
| HK path uses `rounding_rule="floor"` | `src/contracts/settlement_semantics.py:171` uses `rounding_rule="oracle_truncate"` which dispatches to `np.floor(scaled)` (L71-79). `oracle_truncate` is the NAMED alias for floor-with-justification in the HKO branch. Empirically verified: 14/14 (100%) match on HKO same-source days vs 5/14 (36%) with wmo_half_up. Landed via commit `d99273a feat: Oracle Penalty system + SettlementSemantics oracle_truncate + round_fn injection`. | **ABSORBED — NO CODE CHANGE NEEDED** |
| Constitutional review of AGENTS.md L49 "WMO half-up universal" | `grep -inE "half.?up\|WMO\|asymmetric\|universal" AGENTS.md` returns ZERO matches. The "WMO half-up universal" claim is no longer in AGENTS.md (335 lines total). The workbook's L49/L117 line citations refer to a prior version of AGENTS.md; the constitutional concern was discharged by removal of the universal claim. | **ABSORBED — NO AGENTS.md AMENDMENT NEEDED** |
| Antibody `tests/test_hk_settlement_floor_rounding.py` | Search returns no dedicated test file. Two tests touch oracle_truncate semantically (`tests/test_phase10c_dt_seam_followup.py`, `tests/test_pe_reconstruction_relationships.py`) but neither pins HK specifically as an antibody. | **ABSENT — THIS SLICE** |

Workbook line 164 statement "WMO half-up is universal in AGENTS.md L49" has rotted. The workbook is dated 2026-04-23; some intervening change removed the universal claim. The slice premise becomes simpler — no constitutional packet needed, just the antibody.

## 2. Why this slice still matters even though both halves are absorbed

The HKO floor decision is a CRITICAL Zeus invariant — switching it from `oracle_truncate` back to `wmo_half_up` would silently re-poison HKO settlement (5/14 match rate vs 14/14). Without an antibody pinning the current behavior, a future refactor (e.g., "let's standardize all rounding rules to wmo_half_up") could drop the HKO exception unnoticed. The antibody locks the cross-module relationship.

## 3. Files touched

| File | Change | Hunk size |
|---|---|---|
| `tests/test_hk_settlement_floor_rounding.py` (NEW) | 7 antibody tests — see §4 | ~140 lines |
| `architecture/test_topology.yaml` | Register | 1 line |

**No edits** to `src/contracts/settlement_semantics.py`, `src/config.py`, or `AGENTS.md`. Production already correct; this slice is regression coverage only.

## 4. Antibody design

| # | Test | Asserts |
|---|---|---|
| 1 | `test_hk_city_dispatches_to_oracle_truncate` | `SettlementSemantics.for_city(hk_city).rounding_rule == "oracle_truncate"`. Pin the dispatch decision. |
| 2 | `test_hk_city_dispatches_to_celsius_unit` | `for_city(hk_city).measurement_unit == "C"`. Pin the unit decision. |
| 3 | `test_hk_resolution_source_is_hko_hq` | `for_city(hk_city).resolution_source == "HKO_HQ"`. Pin the source label. |
| 4 | `test_hk_floor_truncates_decimal_celsius` | Round 27.8°C → 27 (NOT 28). The exact failure mode known_gaps.md:141-148 documents. |
| 5 | `test_hk_floor_truncates_full_known_gaps_cases` | Round each of the 3 documented mismatch days (03-18, 03-24, 03-29) — assert all produce floor() not wmo_half_up(). Direct value pin per known_gaps. |
| 6 | `test_wu_celsius_city_still_uses_wmo_half_up` | Control — non-HK °C city (e.g., Taipei wu_icao) routes to `wmo_half_up`. Negative pin: HK exception is HK-only, not all-°C. |
| 7 | `test_oracle_truncate_semantically_equivalent_to_floor` | `oracle_truncate` and `floor` rules produce identical output for the same input. Pins the alias contract — a future split where `oracle_truncate` diverges from `floor` would surface here. |

## 5. RED→GREEN sequence

This slice is antibody-only — production code is already correct. Single GREEN commit pattern (B5 precedent).

1. Write `tests/test_hk_settlement_floor_rounding.py` with all 7 tests.
2. Run pytest — expect GREEN out-of-gate.
3. Single commit (no separate RED).
4. Register test in `architecture/test_topology.yaml`.
5. Receipt + work_log.

## 6. Acceptance criteria

- 7/7 green.
- Lifecycle headers present.
- Receipt explicitly records double-absorption (code + constitutional) so the workbook closure trail is honest.
- Regression panel delta = 0.

## 7. Out-of-scope

- Modifying `SettlementSemantics` — already correct.
- Modifying AGENTS.md — universal claim already removed.
- HK exclusion from `LIVE_SAFE_CITIES` (per workbook acceptance "HK excluded from LIVE_SAFE_CITIES until both ship") — both shipped (code via predecessor + antibody via this slice). HK should be considered eligible for LIVE_SAFE_CITIES per workbook intent. Whether HK actually JOINS LIVE_SAFE_CITIES is a separate operator decision tracked under G7-LIVE_SAFE_CITIES (Wave 2 slice).

## 8. Provenance

Recon performed live 2026-04-26 in this worktree:
- `src/contracts/settlement_semantics.py:71-79` — `oracle_truncate`/`floor` dispatch to `np.floor(scaled)`.
- `src/contracts/settlement_semantics.py:161-173` — HK branch returns `rounding_rule="oracle_truncate"`.
- `git log --oneline -- src/contracts/settlement_semantics.py` — `d99273a feat: Oracle Penalty system + SettlementSemantics oracle_truncate + round_fn injection` is the landing commit.
- `grep -inE "half.?up|WMO|asymmetric|universal" AGENTS.md` — 0 matches. AGENTS.md (335 lines) has no universal-WMO claim.
- `config/cities.json` HK entry: `settlement_source_type='hko'`, `unit='C'`, `wu_station=None`, `hko_station='HKO'`.
