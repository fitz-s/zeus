-- P-B schema migration (DDL only; no row writes)
-- Created: 2026-04-23
-- Authority basis: pb_schema_plan.md §2.1 + critic-opus pre-review APPROVE_WITH_CONDITIONS
--                  (C1 LIKE→json_extract applied; C2 reactivation contract documented)
--
-- Idempotent via:
--   - sqlite3 ALTER TABLE ADD COLUMN raises "duplicate column" on re-run (caller ignores)
--   - CREATE TRIGGER IF NOT EXISTS is native idempotent
--
-- This file is run via `sqlite3 state/zeus-world.db < this_file.sql`.
-- The caller script (pb_run_migration.py) wraps each ALTER in try/except,
-- matching the pattern at src/state/db.py:748-819.

-- 5 new settlements columns (INV-14 identity spine + provenance vehicle)
ALTER TABLE settlements ADD COLUMN temperature_metric TEXT CHECK (temperature_metric IS NULL OR temperature_metric IN ('high','low'));
ALTER TABLE settlements ADD COLUMN physical_quantity TEXT;
ALTER TABLE settlements ADD COLUMN observation_field TEXT CHECK (observation_field IS NULL OR observation_field IN ('high_temp','low_temp'));
ALTER TABLE settlements ADD COLUMN data_version TEXT;
ALTER TABLE settlements ADD COLUMN provenance_json TEXT;

-- Authority-monotonic trigger (INV-FP-5 enforcement).
-- Reactivation contract: QUARANTINED→VERIFIED requires json_extract(provenance_json, '$.reactivated_by') IS NOT NULL.
CREATE TRIGGER IF NOT EXISTS settlements_authority_monotonic
BEFORE UPDATE OF authority ON settlements
WHEN (OLD.authority = 'VERIFIED' AND NEW.authority = 'UNVERIFIED')
  OR (OLD.authority = 'QUARANTINED' AND NEW.authority = 'VERIFIED'
      AND (NEW.provenance_json IS NULL
           OR json_extract(NEW.provenance_json, '$.reactivated_by') IS NULL))
BEGIN
    SELECT RAISE(ABORT, 'settlements.authority transition forbidden: VERIFIED->UNVERIFIED blocked, or QUARANTINED->VERIFIED missing provenance_json.reactivated_by');
END;
