# Security Policy

Zeus is a closed, operator-run live trading engine. There is no public
deployment, public API, or external support channel.

## Reporting a vulnerability

Do not open a public GitHub issue for any security matter.

If you discover a potential vulnerability — in the source code, the
settlement logic, credential handling, or any other surface — report it
privately to the repository owner. Include:

- A clear description of the issue
- The affected file(s) or component
- A proof of concept or reasoning, if available
- A suggested remediation (optional)

Contact: via the GitHub profile of the repository owner, or the email
address associated with the account.

## Credential and secret handling

- Live trading credentials (API keys, wallet private keys, venue tokens)
  are sourced exclusively from environment variables or the system keychain.
- No secrets are committed to this repository. The `.gitleaks.toml`
  configuration enforces this on every push.
- `config/settings.json` is operator-local and not tracked in version
  control. See `config/settings.example.json` for the safe template.

## Scope

This repository contains the source of a live trading system operating
on real capital. Any discovered exposure of credentials, wallet addresses,
or venue authentication tokens should be treated as high severity and
reported immediately.
