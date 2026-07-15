from src.data.market_scanner import _fetch_family_cached_fee_details


def test_substrate_fee_cache_rebinds_current_rate_to_each_token() -> None:
    class Clob:
        calls = 0

        def get_fee_rate_details(self, token_id: str) -> dict:
            self.calls += 1
            return {"base_fee": 1000, "token_id": token_id}

    clob = Clob()
    cache = {}
    yes = _fetch_family_cached_fee_details(
        clob, "yes", cache_key="family", fee_details_cache=cache
    )
    no = _fetch_family_cached_fee_details(
        clob, "no", cache_key="family", fee_details_cache=cache
    )

    assert clob.calls == 1
    assert yes["fee_rate_fraction"] == no["fee_rate_fraction"] == 0.10
    assert no["token_id"] == "no"
