# Work Log — Slice K4.U1-antibody

Created: 2026-04-26
Authority basis: `plan.md`, `scope.yaml`.

## 2026-04-26 — slice opened + landed (single-commit B5 pattern)

### Step 0: scaffold + grep-gate audit (per memory L20)

L20 grep-gate within 10-minute window surfaced **DOUBLE absorption**:

1. **Code half ABSORBED**: HK already uses `oracle_truncate` rounding rule (`src/contracts/settlement_semantics.py:171`), which dispatches to `np.floor(scaled)` (L71-79). `oracle_truncate` is a NAMED alias for floor with HKO/UMA justification. Landed via commit `d99273a feat: Oracle Penalty system + SettlementSemantics oracle_truncate + round_fn injection` long before the workbook was authored. Empirical: 14/14 (100%) HKO match vs 5/14 (36%) under wmo_half_up.

2. **Constitutional half ABSORBED**: `grep -inE "half.?up|WMO|asymmetric|universal" AGENTS.md` returns ZERO matches. The "WMO half-up universal" claim cited by the workbook (L49 + L117) is no longer in AGENTS.md (335 lines total). Some intervening change discharged this concern. No constitutional packet needed.

3. **Antibody half OPEN**: No dedicated `tests/test_hk_settlement_floor_rounding.py` (or equivalent direct pin) exists. Two tests touch oracle_truncate semantically (`test_phase10c_dt_seam_followup.py`, `test_pe_reconstruction_relationships.py`) but neither pins HK as a regression antibody.

→ Slice scope reduced to antibody-only. B5 pattern: single GREEN commit (no RED phase) since production code is already correct.

### Step 1: antibody — `tests/test_hk_settlement_floor_rounding.py`

7 tests:
1. `test_hk_city_dispatches_to_oracle_truncate` — pin dispatch decision
2. `test_hk_city_dispatches_to_celsius_unit` — pin unit
3. `test_hk_resolution_source_is_hko_hq` — pin source label
4. `test_hk_floor_truncates_decimal_celsius` — 27.8 → 27 (NOT 28)
5. `test_hk_floor_truncates_full_known_gaps_cases` — 4 representative values across rounding-difference + control
6. `test_wu_celsius_city_still_uses_wmo_half_up` — negative pin: HK exception is HK-only, not all-°C
7. `test_oracle_truncate_semantically_equivalent_to_floor` — alias contract pin

7/7 GREEN out-of-gate (production code pre-correct).

### Step 2: regression panel
- Ran `tests/test_architecture_contracts.py + tests/test_live_safety_invariants.py + tests/test_cross_module_invariants.py + tests/test_hk_settlement_floor_rounding.py`.
- 5 pre-existing fails (4 day0/chain reds + 1 K4 structural-linter).
- delta = 0 NEW failures.

### Step 3: register + close
- `architecture/test_topology.yaml`: registered new test under `tests/`.
- `receipt.json` records DOUBLE absorption.
- Workbook U1 entry now CLOSED — code (predecessor) + AGENTS.md (predecessor removal) + antibody (this slice).

### Notes for downstream
- HK was excluded from `LIVE_SAFE_CITIES` per workbook acceptance "until both ship". Both have shipped (code + antibody). Whether HK actually JOINS LIVE_SAFE_CITIES is a separate operator decision — tracked under G7-LIVE_SAFE_CITIES (Wave 2 slice).
- The `oracle_truncate` alias documents the WHY (UMA truncation semantic). If a future contract wants to drop the alias and use plain `floor`, the alias-contract test (#7) would still hold; only the documentation would change.

### Step 4 (post-review): con-nyx APPROVE — immunity 0.90 (highest of packet)

con-nyx APPROVED `4fd18d9` with empirical re-verification of both absorption halves + per-test review. 2 NICE-TO-HAVE landed inline:

- **NICE-TO-HAVE-1 (fixture cleanup)**: replaced synthetic `_hk_city()` + `_wu_celsius_city()` with `cities_by_name['Hong Kong']` and `cities_by_name['Taipei']`. Tracks production config drift automatically. Surfaced real divergence — production HK has `wu_station=None`, synthetic was `""` (both worked but schema-divergent).

- **NICE-TO-HAVE-2 (alias contract edges)**: extended test 7 sample array from 8 to 16 cases including NaN, +inf, negative-near-zero (-0.5, -0.1), Vostok extreme (-89.9), precision boundaries (1e-10, 1e15). Added explicit NaN-propagation assertion via `np.isnan` masking. Alias contract now pinned across full numpy.floor input space.

7/7 still GREEN post-amendment.

**Operator-visible recommendation forwarded** (con-nyx Ask #3): recommend including HK in G7's INITIAL `LIVE_SAFE_CITIES` set. Reasoning per receipt — "until both ship" intent + both shipped + 14/14 production validation. Tracked in followups_owed.

**Pattern lesson #14 elevated**: L20 grep-gate before scoping multi-deliverable workbook entries is now a critic standing-order per con-nyx Ask #5. U1 slice is the canonical example.

U1 packet status: CLOSED_APPROVED_BY_CRITIC. Category-immunity score 0.90 — highest of the live-readiness-completion packet.
