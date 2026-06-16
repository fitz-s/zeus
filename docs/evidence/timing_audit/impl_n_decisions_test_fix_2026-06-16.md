# n_decisions test fix — C2 dead-lane migration (2026-06-16)

## Behavior change confirmed

`src/analysis/evidence_report.py` lines 201-218: `n_decisions` now counts
`decision_certificates` rows (`WHERE certificate_type='FinalIntentCertificate'
AND json_extract(payload_json,'$.strategy_key')=?`) instead of `decision_events`.
Rationale: `decision_events` is a 0-row dead lane (venue_ack gate fires ~once/28d);
`decision_certificates` (1.27M live rows) is the active provenance path.
Source filter is explicitly dropped as unmappable to certificate schema; documented as
safe because n_decisions is telemetry-only (no ARM gate reads it).

## Files modified

### `tests/analysis/test_evidence_report_cohort_scope.py`
- Added `_DC_SQL` template and `_insert_certificate()` helper.
- `_seed_chain()`: added `seed_certificate: bool = True` parameter; inserts a
  `FinalIntentCertificate` row by default.
- `test_source_filter_applies_to_all_three_metrics`: live_decision chain passes
  `seed_certificate=False` — that entity never reached the active certificate lane.
- `test_source_filter_excluded_loses_both_count_and_wins`: live_decision chain passes
  `seed_certificate=False` — no certificate means n_decisions=0 (correct).
- `test_n_decisions_is_full_universe_when_cohort_scoped`: bare `_DE_SQL` insert for
  the unsettled decision is now accompanied by `_insert_certificate()` so it counts
  in n_decisions=2.

### `tests/test_p1_findings_evidence_risk.py`
- Added `_DC_INSERT` template, `_insert_certificate()` helper.
- `_seed_evidence()`: inserts certificate alongside decision_events + regret chain.
- `TestF2EvidenceReportDenominatorScoping.test_source_filter_excludes_other_sources`:
  only seeds 1 certificate (shadow entity); live_decision has no cert → n_decisions=1.
- `TestF2EvidenceReportDenominatorScoping.test_no_source_filter_counts_all_sources`:
  seeds 3 certificates (one per entity) → n_decisions=3.

### `tests/analysis/test_live_readiness_tribunal.py`
- `_insert_decision_events()`: now also inserts a `FinalIntentCertificate` row per
  decision_events row, so the 100-row batch tests count n_decisions=100 via the
  new lane.

## Sibling scan
Files examined for the same `INSERT INTO decision_events` + `n_decisions` assertion
pattern: `test_p1_findings_evidence_risk.py`, `test_live_readiness_tribunal.py`,
`test_promotion_readiness.py` (uses `n_decisions` only as a field on a constructed
`EvidenceReport` object — not read from DB; no fix needed).

## Verification
```
pytest tests/analysis/test_evidence_report_cohort_scope.py -q  → 9 passed
pytest tests/test_p1_findings_evidence_risk.py tests/analysis/test_live_readiness_tribunal.py -q  → 52 passed
```
