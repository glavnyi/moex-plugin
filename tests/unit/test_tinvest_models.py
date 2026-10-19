from __future__ import annotations

from tinvest.client import quotation_to_float
from tinvest.models import Account, Money, Order, account_balance_view


def test_quotation_to_float_units_nano():
    assert quotation_to_float({"units": "12", "nano": 500_000_000}) == 12.5


def test_quotation_to_float_negative():
    assert quotation_to_float({"units": "-3", "nano": -250_000_000}) == -3.25


def test_quotation_to_float_none_safe():
    assert quotation_to_float(None) is None
    assert quotation_to_float({}) == 0.0  # no units → zero, never a crash


def test_money_from_api():
    m = Money.from_api({"currency": "rub", "units": "100", "nano": 0})
    assert m is not None
    assert m.amount == 100.0
    assert m.currency == "rub"


def test_account_from_api():
    a = Account.from_api({
        "accountId": "acc-1", "name": "Sandbox", "status": "open",
        "openedDate": "2026-10-01T00:00:00Z", "type": "TYPE_ACCOUNT",
        "cashBalance": {"currency": "rub", "units": "1000", "nano": 0},
    })
    assert a.id == "acc-1"
    assert a.name == "Sandbox"
    assert a.opened.startswith("2026-10-01")
    assert a.cash_currency == "rub"


def test_order_state_transitions():
    o = Order.from_api({
        "orderId": "o-1", "direction": "direction_buy",
        "lotsRequested": "10", "lotsExecuted": "4",
        "executionReportStatus": "EXECUTION_REPORT_STATUS_PARTIALLYFILL",
        "figi": "SBER",
    })
    assert o.is_partial
    assert not o.is_terminal


def test_order_terminal_filled():
    o = Order.from_api({
        "orderId": "o-2", "direction": "direction_sell",
        "lotsRequested": "5", "lotsExecuted": "5",
        "executionReportStatus": "EXECUTION_REPORT_STATUS_FILL",
        "figi": "GAZP",
    })
    assert o.is_terminal
    assert not o.is_partial


def test_account_balance_view():
    view = account_balance_view({
        "totalPortfolio": {"units": "200000", "nano": 0},
        "totalCurrencies": {"units": "50000", "nano": 0},
        "totalShares": {"units": "150000", "nano": 0},
    }, "acc-1")
    assert view["account_id"] == "acc-1"
    assert view["total"] == 200000.0
    assert view["currencies"] == 50000.0
    assert view["shares"] == 150000.0
    assert view["synced_at"]


def test_map_order_state_rest_enum():
    from moex.analytics.workflow import map_order_state

    assert map_order_state("EXECUTION_REPORT_STATUS_NEW") == "accepted"
    assert map_order_state("EXECUTION_REPORT_STATUS_FILL") == "filled"
    assert map_order_state("EXECUTION_REPORT_STATUS_PARTIALLYFILL") == "partial"
    assert map_order_state("EXECUTION_REPORT_STATUS_CANCELLED") == "cancelled"
    assert map_order_state("EXECUTION_REPORT_STATUS_REJECTED") == "rejected"
    # legacy aliases
    assert map_order_state("NEW") == "accepted"
    assert map_order_state("WEIRD") == "unknown"
    assert map_order_state("") == "unknown"


def test_pay_in_reads_balance_field_from_api(monkeypatch):
    """SandboxPayInResponse is { balance: MoneyValue } (openapi v1.4)."""
    from tinvest import sandbox_accounts as sa

    class FakeClient:
        sandbox = True

        def sandbox_pay_in(self, account_id, amount, currency):
            return {"balance": {"units": 1000000, "nano": 0, "currency": "rub"}}

    out = sa.pay_in(FakeClient(), "acc-1", 1_000_000.0, "rub")
    assert out["amount"] == 1_000_000.0
    assert out["currency"] == "rub"
    assert out["result"] == "success"


def test_retry_on_transient_retries_valueerror_with_code():
    """OpenRouter wraps 503s as ValueError(dict) — must retry, not re-raise."""
    import sys, types
    from tradingagents.llm_clients import openai_client as oc

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError({"message": "Upstream error from Nvidia: "
                              "Service temporarily overloaded",
                              "code": 503,
                              "metadata": {"error_type": "provider_overloaded"}})
        return "ok"

    out = oc._retry_on_transient(flaky, attempts=6, base_delay=0.01)
    assert out == "ok"
    assert calls["n"] == 3

    def programming_error():
        raise ValueError("bad literal")

    try:
        oc._retry_on_transient(programming_error, attempts=3, base_delay=0.01)
    except ValueError as exc:
        assert "bad literal" in str(exc)
    else:
        raise AssertionError("non-transient ValueError must re-raise")


def test_lenient_float_coercion():
    """LLM junk in optional floats must not kill the structured call."""
    from tradingagents.agents.schemas import (
        PortfolioDecision, TraderProposal, _lenient_float, PortfolioRating,
    )

    assert _lenient_float("N/A") is None
    assert _lenient_float("") is None
    assert _lenient_float(None) is None
    assert _lenient_float("~380 RUB") == 380.0
    assert _lenient_float("377,05") == 377.05
    assert _lenient_float(377.5) == 377.5

    p = PortfolioDecision(
        rating=PortfolioRating.HOLD,
        executive_summary="hold",
        investment_thesis="flat",
        price_target="insufficient data",
    )
    assert p.price_target is None

    t = TraderProposal(action="Hold", reasoning="wait", entry_price="~105")
    assert t.entry_price == 105.0


def test_cert_bundle_is_stable_and_not_rewritten(tmp_path, monkeypatch):
    """The combined bundle must not be truncated under concurrent readers.

    The old stamp check (``combined.stem`` is always ``combined-bundle``)
    made every call rewrite the file in place; a second thread reading it
    mid-write saw a half-written PEM and failed TLS with ``[X509] PEM lib``.
    """
    import tinvest.client as tc

    certs = tmp_path / "certs"
    certs.mkdir()
    monkeypatch.setattr(tc, "_CERT_DIR", certs)
    (certs / "russian_trusted_root_ca.pem").write_bytes(b"RU-ROOT\n")

    fake_certifi = tmp_path / "cacert.pem"
    fake_certifi.write_bytes(b"CERTIFI\n")
    monkeypatch.setitem(__import__("sys").modules, "certifi",
                        type("m", (), {"where": staticmethod(lambda: str(fake_certifi))}))

    first = tc.cert_bundle()
    st_mtime = (certs / "combined-bundle.pem").stat().st_mtime_ns
    payload = (certs / "combined-bundle.pem").read_bytes()
    assert payload == b"CERTIFI\n\nRU-ROOT\n"

    # Second call must be a no-op: same bytes, same mtime.
    assert tc.cert_bundle() == first
    assert (certs / "combined-bundle.pem").read_bytes() == payload
    assert (certs / "combined-bundle.pem").stat().st_mtime_ns == st_mtime


def test_cert_bundle_rebuilds_when_the_inputs_change(tmp_path, monkeypatch):
    import tinvest.client as tc

    certs = tmp_path / "certs"
    certs.mkdir()
    monkeypatch.setattr(tc, "_CERT_DIR", certs)
    (certs / "russian_trusted_root_ca.pem").write_bytes(b"RU-ROOT\n")

    src = tmp_path / "cacert.pem"
    src.write_bytes(b"V1\n")
    monkeypatch.setitem(__import__("sys").modules, "certifi",
                        type("m", (), {"where": staticmethod(lambda: str(src))}))

    tc.cert_bundle()
    assert (certs / "combined-bundle.pem").read_bytes() == b"V1\n\nRU-ROOT\n"

    src.write_bytes(b"V2-CERTIFI\n")
    tc.cert_bundle()
    assert (certs / "combined-bundle.pem").read_bytes() == b"V2-CERTIFI\n\nRU-ROOT\n"
