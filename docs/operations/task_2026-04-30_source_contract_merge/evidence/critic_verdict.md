Graph tools are blocked in this session; proceeding with diff-only critic review per the cross-session merge gate scope.

---

# Verdict: APPROVE
**Confidence:** medium
**Merge Allowed:** yes

The change is structurally consistent with Zeus's source-truth invariants: it adds a fail-closed source-contract gate, a city-level quarantine ledger with required conversion-evidence refs, and daemon-independent monitoring. It does not mutate production DB, does not authorize live placement, and tightens (not loosens) entry permissions. Topology routing for the new files (`scripts/watch_source_contract.py`, `tests/test_market_scanner_provenance.py`, the `current_source_validity.md` refresh) is added to the appropriate digest profiles with explicit profile-routing tests. The Paris case is correctly classified as `same_provider_station_change`, and old-position monitoring is structurally preserved because the new gate lives in the discovery path (`find_weather_markets`) only.

---

## Findings

### Medium severity

**M1. False-MATCH hole when WU URL has no extractable station code.**
`src/data/market_scanner.py::_check_source_contract` — if `_infer_source_family` returns `wu_icao` but `_extract_station_id` returns `None` (URL doesn't end in a 3-6 char path component, no `?site=` query, configured station name not present as a token), the function falls through past the family-match branch, past the `expected_station and station_id` MISMATCH branch, past the `source_family is None and station_id is None` UNSUPPORTED branch, and lands on `return MATCH`. Effect: a Paris market resolved via a WU page like `https://www.wunderground.com/cities/fr/paris` (no station segment) would be MATCH-classified despite proving nothing about LFPG vs LFPB. Recommend: when `expected_station` is set, require `station_id is not None` to declare MATCH; otherwise return `UNSUPPORTED` ("settlement station could not be proved from resolutionSource").

**M2. Existing-position monitor/exit path not verified in tests.**
The refresh contract claim is "new entries blocked, old positions monitor/exit." The diff blocks new entries via `find_weather_markets` skipping quarantined cities, but no test demonstrates that an existing Paris position continues to be re-priced/closed by the position-management surfaces (positions.json/risk_state lookups). If any monitor-path code joins by city through `find_weather_markets`, this PR would silently drop existing Paris monitoring. Required post-merge check: grep `find_weather_markets` callers; confirm position monitoring uses token_id/condition_id lookups, not city-keyed discovery.

### Low severity

**L1. Temp-file naming race in `_write_source_contract_quarantines`.**
Both Venus daemon (via `venus_sensing_report._collect_source_contract_watch`) and cron (`scripts/watch_source_contract.py`) write to the same `state/source_contract_quarantine.json` and use a fixed temp name `.source_contract_quarantine.json.tmp`. A simultaneous write would clobber one writer's tmp file before `replace`. Probability is low given typical cron cadence; recommend a PID/random suffix on the tmp filename for an antibody-grade fix.

**L2. `_evidence_ref_present` is permissive — operator-vouched only.**
Release accepts any non-empty string in `evidence_refs[<key>]`. There is no validation that the path exists or the receipt is reachable. This is acceptable as an operator-only release path, but the contract is paper-trail strength, not cryptographic.

**L3. No schema-version check on quarantine load.**
`SOURCE_CONTRACT_QUARANTINE_SCHEMA_VERSION = 1` is written on every save but never validated on load. Forward-compat: a future v2 file read by an old binary will silently coerce. Trivial to add a guard at v2 introduction time; not blocking now.

**L4. Unbounded `transition_history` growth.**
Each release appends a record; long-running flap cycles will grow the file. Consider a soft cap or external rotation before the file gets large enough to slow `load_source_contract_quarantines`.

**L5. `_canonical_city_name` accepts unconfigured names.**
A typo (`"Atlantis"`) would create a quarantine entry that never hits a configured market. Low operational risk, but tightening to require a configured-city match (or warn loudly on unmatched names) would prevent stale phantom quarantines.

**L6. `current_source_validity.md` "audit-bound" boundary is preserved correctly.**
The refresh adds caution-posture text and cites runtime monitor evidence as provenance only, not as runtime permission. The runtime block is enforced by code in `market_scanner.py`, not by the doc — boundary intact.

### Informational

**I1. Profile coverage for the bundled diff.**
The diff spans three digest profiles ("modify data ingestion" for `market_scanner.py`, "add or change script" for `watch_source_contract.py` + `venus_sensing_report.py`, "refresh source current fact" for `current_source_validity.md`). Topology tests assert each profile admits its expected files individually, which is the right invariant. Operators should not run a single planning-lock invocation expecting one profile to admit the full set.

**I2. Hidden-branch coverage matches the operator checklist.**
Same-provider station change, provider-family change, unsupported source, ambiguous source, repeated source changes after release, and Venus-daemon-paused paths are represented in the implementation/test set.

**I3. Bidirectional drift-keyword grep.**
"settlement source", "source contract", "quarantine", "Paris", "backfill", "calibration", "release", and "Venus" all appear in both code and topology routing in consistent senses. No semantic drift between doc and code observed.

---

## Resolution Before Merge

- M1 was addressed before merge by requiring configured station proof when a WU provider family is detected and the city has a configured station.
- Added regression coverage for a stationless Weather Underground URL.
- The rerun requested after this fix was cancelled by operator instruction; the standing critic verdict remains APPROVE / Merge Allowed: yes.
