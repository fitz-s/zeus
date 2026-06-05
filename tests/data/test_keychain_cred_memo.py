# Created: 2026-06-05
# Last reused or audited: 2026-06-05
# Authority basis: Efficiency #2 (boot double-build keychain re-shell) — operator
#   directive "boot 时间过长 ... 找到效率最高的 daemon 运行方案以加快运行".
#
# Relationship invariant (the antibody): the keychain subprocess read is the
# expensive cross-process boundary; _resolve_credentials is called once per
# PolymarketClient construction and boot builds the client twice. The memo must
# collapse N resolutions to ONE keychain read-pair, AND must not corrupt the
# shared cache across callers (each caller gets its own dict), AND must NOT
# memoize a failure (a transient keychain miss must be retryable).
import importlib

import pytest


def _fresh_mod():
    import src.data.polymarket_client as mod

    importlib.reload(mod)
    return mod


def test_two_resolutions_collapse_to_one_keychain_read_pair():
    # INV: boot builds the client twice -> _resolve_credentials twice -> the
    # underlying keychain subprocess must fire exactly ONCE for each key, not
    # once-per-resolution. Pre-memo this read 4 times (2 keys x 2 calls).
    mod = _fresh_mod()
    calls = []

    def _counting_reader(key):
        calls.append(key)
        return f"value-for-{key}"

    mod._cached_keychain_creds.cache_clear()
    original = mod._import_keychain_resolver
    mod._import_keychain_resolver = lambda: _counting_reader
    try:
        first = mod._resolve_credentials()
        second = mod._resolve_credentials()
    finally:
        mod._import_keychain_resolver = original
        mod._cached_keychain_creds.cache_clear()

    assert first == {"private_key": "value-for-openclaw-metamask-private-key",
                     "funder_address": "value-for-openclaw-polymarket-funder-address"}
    assert second == first
    # the load-bearing assertion: 2 keys read once, NOT 2 keys x 2 resolutions.
    assert len(calls) == 2, f"keychain re-shelled {len(calls)} times; memo failed to dedupe"


def test_each_caller_gets_an_independent_dict():
    # INV: a caller mutating its returned dict must not poison the next caller.
    # The cache holds an immutable tuple; _resolve_credentials rebuilds the dict.
    mod = _fresh_mod()
    mod._cached_keychain_creds.cache_clear()
    original = mod._import_keychain_resolver
    mod._import_keychain_resolver = lambda: (lambda key: f"v-{key}")
    try:
        a = mod._resolve_credentials()
        a["private_key"] = "MUTATED"
        b = mod._resolve_credentials()
    finally:
        mod._import_keychain_resolver = original
        mod._cached_keychain_creds.cache_clear()

    assert b["private_key"] == "v-openclaw-metamask-private-key", "mutation leaked through the cache"


def test_failure_is_not_memoized_and_is_retryable():
    # INV: lru_cache must not cache an exception. A transient keychain miss
    # (e.g. keychain locked at boot) must be retried on the next resolution,
    # not turned into a permanent process-lifetime failure.
    mod = _fresh_mod()
    mod._cached_keychain_creds.cache_clear()
    state = {"fail": True}

    def _flaky_reader(key):
        if state["fail"]:
            return ""  # empty -> _cached_keychain_creds raises RuntimeError
        return f"ok-{key}"

    original = mod._import_keychain_resolver
    mod._import_keychain_resolver = lambda: _flaky_reader
    try:
        with pytest.raises(RuntimeError):
            mod._resolve_credentials()
        state["fail"] = False
        recovered = mod._resolve_credentials()
    finally:
        mod._import_keychain_resolver = original
        mod._cached_keychain_creds.cache_clear()

    assert recovered["private_key"] == "ok-openclaw-metamask-private-key", (
        "a transient keychain failure was memoized and never recovered"
    )
