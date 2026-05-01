# codex_review_after_data_fixes.md

After P0-P3, ask Pro review to re-run forensic audit.

Required evidence packet:
- Repo SHA and commit list.
- DB copy SHA256.
- Readiness command output.
- SQL outputs from this package.
- Test output.
- Settlement v2 sample rows with market identity.
- Ensemble v2 sample rows with issue/available/fetch times.
- Calibration pair sample rows with snapshot lineage and training flags.
- Backfill manifests and failed-window summaries.

Review question:
Is Zeus now safe for controlled replay/calibration on the audited market subset, or are any row families still evidence-only?
