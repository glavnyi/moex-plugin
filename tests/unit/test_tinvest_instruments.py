"""T-Invest figi resolution — the broker rejects non-tradeable duplicates."""

from types import SimpleNamespace

import pytest

from tinvest.client import TInvestError
from tinvest.instruments import find_instrument_ref


class _FakeClient:
    def __init__(self, instruments):
        self._instruments = instruments
        self.calls = []

    def call(self, service, method, params):
        self.calls.append((service, method, params))
        return {"instruments": self._instruments}


SBER_LIVE = [
    {"ticker": "SBER", "figi": "TCS019029540", "isin": "RU0009029540",
     "uid": "u1", "apiTradeAvailableFlag": False},
    {"ticker": "SBER", "figi": "BBG004730N88", "isin": "RU0009029540",
     "uid": "u2", "apiTradeAvailableFlag": True},
    {"ticker": "SBERP", "figi": "BBG0047315Y7", "isin": "RU0009029557",
     "uid": "u3", "apiTradeAvailableFlag": True},
]


def test_prefers_api_tradeable_figi():
    ref = find_instrument_ref(_FakeClient(SBER_LIVE), "SBER",
                              isin="RU0009029540")
    # TCS019029540 is view-only and was rejected by the broker live (30052).
    assert ref["figi"] == "BBG004730N88"
    assert ref["uid"] == "u2"


def test_exact_ticker_match_over_sberp():
    ref = find_instrument_ref(_FakeClient(SBER_LIVE), "SBER")
    assert ref["ticker"] == "SBER"


def test_falls_back_when_no_api_flag():
    only_view = [{"ticker": "SBER", "figi": "TCS019029540",
                  "isin": "RU0009029540", "apiTradeAvailableFlag": False}]
    ref = find_instrument_ref(_FakeClient(only_view), "SBER")
    assert ref["figi"] == "TCS019029540"


def test_raises_when_unknown():
    with pytest.raises(TInvestError):
        find_instrument_ref(_FakeClient([]), "ZZZZ")
