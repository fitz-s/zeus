# RFC — HKO Fresh-Audit Promotion (Gate 2 Resolution)
# Created: 2026-04-28
# Status: STUB — operator decision required before implementation
# Authority basis: docs/operations/task_2026-04-28_obs_provenance_preflight/plan.md §P2 #5
#                  + docs/operations/current_source_validity.md item 6

## Problem

821 `observations` rows are blocked by
`observations.hko_requires_fresh_source_audit` in
`scripts/verify_truth_surfaces.py:1465-1496`:

```sql
authority='VERIFIED' AND obs_col IS NOT NULL
AND (LOWER(source) LIKE 'hko%' OR city='Hong Kong')
```

Hong Kong is the explicit caution path per
`current_source_validity.md` item 6: "current truth claims for Hong Kong
require fresh audit evidence, not assumption."

There is no code-side mechanism today to promote HKO rows from "needs fresh
audit" to "audited and approved". The gate fail-closes — every HKO row blocks
calibration-pair rebuild until an audit artifact exists.

## Constraints

- HKO is `gap_city` per `tier_resolver.source_role_assessment_for_city_source`.
- The HKO API and HKO native data have stalled relative to the audited
  window (`current_source_validity.md` item 3).
- **Correction merged 2026-04-28**: Hong Kong has no WU ICAO in Zeus.
  Do not route HKO/Hong Kong settlement truth through `wu_icao`, WU, or
  VHHH aliases. Any stale market-description ambiguity remains a
  fresh-audit/operator-evidence question, not an accepted source route.
- `architecture/city_truth_contract.yaml` defines the stable schema; HKO
  routes are NOT promotable through that file.

## Proposed mechanism

### 1. Audit artifact format (operator-owned)

A YAML manifest at `architecture/hko_audit_records.yaml`:

```yaml
schema_version: 1
records:
  - audit_id: hko_2026-04-15_to_2026-04-28
    audited_window:
      start_date: "2026-04-15"
      end_date:   "2026-04-28"
    audited_cities:
      - Hong Kong
    audited_sources:
      - hko_native
      - hko_hourly_accumulator
    evidence_url: "https://..."             # operator-provided primary source
    audit_method: "operator_manual_compare" # enum
    signer: "Fitz"                          # operator name
    signed_at: "2026-04-28T..."             # ISO UTC
    notes: |
      Multi-line freeform.
```

Each record covers a (date-window × city × source) tuple. Promotes
the gate output for matching rows from BLOCKER → cleared.

### 2. tier_resolver promotion branch

Add in `src/data/tier_resolver.py`:

```python
def hko_audit_status(city: str, target_date: date) -> Optional[HkoAuditRecord]:
    """Return the audit record covering this row, or None.
    Reads architecture/hko_audit_records.yaml."""
```

### 3. Predicate change in `verify_truth_surfaces.py`

Around line 1465-1496, subtract audited rows from the blocker count:

```python
blocker_count = base_count - audit_covered_count(...)
```

Pure subtraction; no schema change; idempotent.

### 4. New invariant

`INV-NN: HKO rows MUST NOT enter calibration training without an active
audit record. Audit records expire after their end_date; expired records
fail-closed (treated as no audit).`

Encoded as `tests/test_hko_audit_promotion.py` — negative fixture: HKO row
without audit record blocks Gate 2; positive fixture: same row with
matching audit record passes.

## Open questions for operator

1. **Audit window granularity**: per-date overrides vs city-wide promotion
   per audit window? The 821 rows span ~15 months — granularity affects
   audit overhead.
2. **Evidence URL requirements**: must point to a primary source (HKO
   official datasheet) or operator-attested evidence (operator's own
   measurement)?
3. **Signer authority**: who can sign? Just the operator (Fitz) or any
   project agent?
4. **HK source-route correction**: do not formalize any HK→WU/VHHH
   routing. Hong Kong has no WU ICAO in Zeus; HK remains an HKO/fresh-audit
   caution path unless a future operator-approved primary-source receipt
   proves a different non-WU route.
5. **Audit record retention**: keep all records (history of audits) vs
   active records only?

## Out-of-scope

- Replacing HKO API ingest mechanism (separate work)
- Schema changes to `observations` (we are NOT adding columns; the audit
  table is operator-owned)
- Any future HK source-route exception (must not be WU/VHHH by default; requires separate operator-approved primary-source evidence)

## Stop conditions

- Without operator decisions on the 5 open questions above, this RFC
  cannot move from STUB → APPROVED.
- Even after approval, code changes to `verify_truth_surfaces.py:1465-1496`
  trigger Architecture-class planning lock per AGENTS.md §4.
