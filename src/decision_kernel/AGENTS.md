# Zeus Decision Kernel

`src/decision_kernel` owns EDLI proof-carrying decision certificates.

Rules:
- Certificates, not raw events, rows, bool gates, or receipts, are the authority
  for EDLI decisions.
- Every certificate must carry the `LIVE` mode, claim type, semantic key, role-labeled
  parent edges, authority identity, algorithm identity, payload hash, and
  certificate hash.
- Certificates require source availability, agent receipt,
  and persistence times at or before the decision time.
- Public market-channel data may create market-data or quote certificates, but
  it must not create fill certificates.
- Pre-submit certificates prove one pending live decision. They cannot claim a
  venue submission; only the verified actionable graph may mint an execution
  command, and the execution receipt records the actual outcome.
- Keep reactor integration thin: fetch event, invoke the compiler, persist
  compiler output, then mark processed or dead-letter.
