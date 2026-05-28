# Zeus Decision Kernel

`src/decision_kernel` owns EDLI proof-carrying decision certificates.

Rules:
- Certificates, not raw events, rows, bool gates, or receipts, are the authority
  for EDLI decisions.
- Every certificate must carry a mode, claim type, semantic key, role-labeled
  parent edges, authority identity, algorithm identity, payload hash, and
  certificate hash.
- LIVE and NO_SUBMIT certificates require source availability, agent receipt,
  and persistence times at or before the decision time.
- Public market-channel data may create market-data or quote certificates, but
  it must not create fill certificates.
- NO_SUBMIT certificates must not contain actionable trade scores, execution
  commands, venue submissions, or `submitted=true` semantics.
- Keep reactor integration thin: fetch event, invoke the compiler, persist
  compiler output, then mark processed or dead-letter.
