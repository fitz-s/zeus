# Live Promotion Checklist
> Updated: 2026-03-31

## Must Before Live
- [x] Full automated verification green
  - Evidence: `302 passed, 3 skipped`
- [x] Structural linter green
  - Evidence: `scripts/semantic_linter.py src`
- [x] Paper daemon healthy after reload
  - Evidence: `scripts/healthcheck.py` returns `healthy=true`
- [x] RiskGuard healthy after reload
  - Evidence: `riskguard_alive=true`, `riskguard_fresh=true`
- [x] Keychain entries exist for Polymarket credentials
  - Evidence: macOS `security find-generic-password ...` status `0` for all required services
- [ ] Confirm post-reload cycle behavior
  - Need: verify fresh `status_summary-paper.json` advances and inspect whether Open-Meteo `429` materially drops after current cache/runtime changes
- [ ] Decide how Zeus coordinates with workspace-wide Open-Meteo consumers
  - Need: current evidence suggests `51 source data` automation and other legacy ingestion loops also consume the same upstream budget
- [ ] Explicit decision on real `zeus.db` contamination cleanup
  - Need: operator approval before deleting test fixture rows from shared DB
- [ ] Explicit decision on `bias_correction_enabled`
  - Need: either keep off for promotion, or regenerate pairs/models and enable intentionally
- [ ] Paper soak after current runtime/code changes
  - Need: sustained healthy runtime, no drift, no runaway request pattern

## Should Before Live
- [ ] Complete DST / diurnal local-hour repair beyond current solar ingestion
- [ ] Revisit `MODEL_DIVERGENCE_PANIC` threshold using actual settlement outcomes
- [ ] Close harvester vs bias-correction consistency design if bias correction is planned
- [ ] Reduce Gamma settled-event scan cost if it remains operationally noisy

## Can Defer
- [ ] Expand settlement semantics beyond current WU-only configuration
- [ ] Deeper alpha-overrides review
- [ ] Additional replay / attribution cleanup not blocking immediate live promotion
