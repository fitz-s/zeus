# Contributing

Zeus is closed to external contributions.

This repository is an audit-readable trace of a live, operator-run
trading system. It is not a project to fork, submit pull requests to,
or extend. The code ships as operational fixes on a continuous basis;
there is no roadmap, feature backlog, or issue tracker open to the
public.

## For readers and auditors

If you are here to understand the algorithm or verify the implementation:

- Start with `README.md` for the methodology and trade lifecycle.
- `AGENTS.md` documents the operational law and authority routing that
  governs how the system is changed — useful context for understanding
  why the codebase is structured the way it is.
- Source is under `src/`; tests are under `tests/`.

## Security issues

See [`SECURITY.md`](SECURITY.md) for the private disclosure path.
Do not open public issues for security matters.
