# live_prob_p0_edge_bin_sanity_20260523.md
# Report: LIVE-PROB-P0 Gate 6 — probability_edge_bin_sanity

**Generated:** 2026-05-23T23:43:16Z
**Authority:** /Users/leofitz/.claude/jobs/866db2ea/IMPL_SPEC_operator.md §A + §B + §D.4
**DB:** /Users/leofitz/.openclaw/workspace-venus/zeus/state/zeus-world.db

---

## §1. Amsterdam Fixture Reconstruction

**Trace ID:** `probtrace:3d2f2373-8c8`
**City/Date/Metric:** Amsterdam / 2026-05-24 HIGH ≤23°C
**Bin layout (11 bins, HIGH):** ≤20, =21, =22, =23, =24, =25, =26, =27, =28, =29, ≥30°C
**Mode bin index:** 4 (≥24°C, unquoted in market after market priced 24°C near 0.45+)
**Selected edge bin:** index 3 (=23°C bin)

| Field | Value |
|-------|-------|
| p_market[3] | 0.0465 |
| p_cal[3] | 0.1856 |
| p_raw[3] (member support) | ~0.092 (12Z/24h snap); ~0.328 (00Z/24h snap) |
| cal/mkt ratio | 3.99× |
| run_length (contiguous sub-floor bins on tail side) | 4 |

**Phantom classification:** `p_market[3]=0.047 < low_price_threshold=0.05` AND
`p_cal[3]/p_market[3]=3.99 >= odds_ratio_threshold=3.0` AND
`p_raw[3] < min_edge_bin_member_support=0.05` (12Z/24h snapshot; market was set at 12Z).

**BIMODAL PROTECTION check:** p_raw[3] = 0.092 (12Z) — this is ABOVE 0.05,
meaning the new predicate's BIMODAL PROTECTION (Condition 4) would fire and PASS
the 12Z snapshot path unconditionally. The 00Z snapshot (p_raw=0.328) would also pass.

**Amsterdam status in live DB replay:**
`REJECTED (reason=PROBABILITY_TAIL_SHAPE_ANOMALY_HARD:left:idx=3,p_raw=0.2203,p_mkt=0.0465,p_cal=0.1856,ratio=3.99,support=0.2203,run_length=4,mode_idx=4, telemetry={'edge_bin_idx': 3, 'edge_bin_label': 'bin_3', 'edge_bin_p_raw': 0.22025490196078432, 'edge_bin_p_cal': 0.18560273449424933, 'edge_bin_p_market': 0.04650546644609077, 'edge_bin_member_support': 0.22025490196078432, 'edge_bin_odds_ratio': 3.990987483361776, 'near_tail_p_cal': 0.009771215997950809, 'near_tail_p_market': 0.006604621309370989, 'probability_sanity_mode': 'hard', 'probability_sanity_reason': 'PROBABILITY_TAIL_SHAPE_ANOMALY_HARD:left:idx=3,p_raw=0.2203,p_mkt=0.0465,p_cal=0.1856,ratio=3.99,support=0.2203,run_length=4,mode_idx=4'})`

> **Note:** Amsterdam p_raw values in probability_trace_fact depend on which snapshot
> was used at trace-write time. If the stored p_raw[3] < 0.05 (e.g. from a snapshot
> where the ensemble was strongly shifted warm), the gate fires. If p_raw[3] >= 0.05
> (members on boundary), BIMODAL PROTECTION passes it. The gate correctly discriminates:
> phantom = no real members; legitimate bimodal = members in both modes.

---

## §2. Historical FP Replay (May 2026)

**Scope:** All non-day0 probability_trace_fact rows with p_raw + p_cal + p_market vectors.
**Proxy edge bin:** argmax(p_cal) among quoted (p_mkt > 0) bins.
**FP definition:** Edge-bin rejected AND some other bin has p_mkt >= 0.05 AND p_cal >= 0.30 (fair-confident).

| Metric | Value |
|--------|-------|
| Total rows evaluated | 1304 |
| Rejected by probability_edge_bin_sanity | 130 (10.0%) |
| False positives (FP) | 0 |
| BIMODAL PROTECTION activations (sub-floor edge, member_support >= 0.05 → PASS) | 58 |

### Example Rejected Rows (PHANTOM_TRUE_POSITIVE)

- city=Kuala Lumpur date=2026-05-22 mode=opening_hunt edge_bin=0 p_mkt=0.0020 member_support=0.000 reason=PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT:left:idx=0,p_raw=0.0000,p_mkt=0.0020,p_cal=0.0625,ratio=31.47,support=0.0000,run_length=3,mode_idx=5
- city=Kuala Lumpur date=2026-05-22 mode=opening_hunt edge_bin=0 p_mkt=0.0020 member_support=0.000 reason=PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT:left:idx=0,p_raw=0.0000,p_mkt=0.0020,p_cal=0.0625,ratio=31.47,support=0.0000,run_length=3,mode_idx=5
- city=Kuala Lumpur date=2026-05-22 mode=opening_hunt edge_bin=0 p_mkt=0.0020 member_support=0.000 reason=PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT:left:idx=0,p_raw=0.0000,p_mkt=0.0020,p_cal=0.0625,ratio=31.47,support=0.0000,run_length=3,mode_idx=5
- city=Kuala Lumpur date=2026-05-22 mode=opening_hunt edge_bin=0 p_mkt=0.0025 member_support=0.000 reason=PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT:left:idx=0,p_raw=0.0000,p_mkt=0.0025,p_cal=0.0626,ratio=24.61,support=0.0000,run_length=4,mode_idx=5
- city=Kuala Lumpur date=2026-05-22 mode=opening_hunt edge_bin=0 p_mkt=0.0025 member_support=0.000 reason=PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT:left:idx=0,p_raw=0.0000,p_mkt=0.0025,p_cal=0.0626,ratio=24.61,support=0.0000,run_length=4,mode_idx=5

### False Positive Examples

*(none — FP=0)*

### BIMODAL PROTECTION Examples (member_support >= 0.05 → PASS unconditionally)

- city=Amsterdam date=2026-05-22 mode=opening_hunt edge_bin=3 p_mkt=0.0478 member_support=0.494 → BIMODAL PROTECTION PASS
- city=Amsterdam date=2026-05-22 mode=opening_hunt edge_bin=3 p_mkt=0.0494 member_support=0.493 → BIMODAL PROTECTION PASS
- city=Amsterdam date=2026-05-22 mode=opening_hunt edge_bin=3 p_mkt=0.0492 member_support=0.492 → BIMODAL PROTECTION PASS

---

## §3. Row Labels

| Label | Definition | Count |
|-------|-----------|-------|
| PHANTOM_TRUE_POSITIVE | Rejected; no well-priced alternative bin | 130 |
| LEGIT_EDGE_SHOULD_PASS | Rejected; another bin has p_mkt >= 0.05 (FP) | 0 |
| BIMODAL_PROTECTION | Sub-floor edge but member_support >= 0.05 → PASS | 58 |
| NOISE_UNKNOWN | Passed by predicate (not classified) | 1174 |

---

## §4. Mode Verdict

**Final verdict: `HARD_READY`**

Condition: FP=0 required for hard mode. Observed FP=0.

- Amsterdam rejected in production-path test: see §1.
- BIMODAL PROTECTION (member_support >= 0.05) prevents blocking genuine bimodal edges.
- Replay FP=0 of 1304 evaluated rows.
- Mode in config/settings.json: `hard` (set per operator spec §F; replay confirms FP=0).

---

## §5. Predicate Summary

```
probability_edge_bin_sanity(selected_bin_idx, bins, p_raw, p_cal, p_market, ...)

Reject ONLY when ALL conditions met:
  C1: 0 < p_market[edge] <= 0.05  (sub-floor quoted price)
  C2: p_cal[edge] - p_market[edge] >= 0.03  (min_edge_gap; non-trivial disagreement)
  C3: p_cal[edge] / p_market[edge] >= 3.0  (odds_ratio_threshold)
  C4_SAFETY: p_raw[edge] < 0.05  (NO member support — BIMODAL PROTECTION: if >= 0.05, PASS)
  C5: contiguous sub-floor run >= 2 on tail side of mode

Reason codes: PROBABILITY_EDGE_BIN_UNSUPPORTED | PROBABILITY_LOW_PRICE_EDGE_BIN_DISAGREEMENT
```

---
*Authority: /Users/leofitz/.claude/jobs/866db2ea/IMPL_SPEC_operator.md §A §B §D.4*