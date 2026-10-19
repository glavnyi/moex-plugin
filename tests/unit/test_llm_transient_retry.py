"""Regression tests for the OpenRouter/openai transient-error retry layer.

Live bug: a real MTSS run died at Portfolio Manager with
``OpenAIConnectionError: Connection error.`` and ZERO retry attempts logged,
because ``_is_transient_openrouter_error`` only inspected ``exc.args``
payloads (ValueError dicts from OpenRouter) and classified every
transport-level exception as permanent.
"""

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PATHS = [
    _ROOT / "upstream" / "TradingAgents-GUI",
    _ROOT,
]
for _p in _PATHS:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tradingagents.llm_clients import openai_client as oc  # noqa: E402


def test_openai_connection_error_is_transient():
    from openai import APIConnectionError

    exc = APIConnectionError(request=None)
    assert oc._is_transient_openrouter_error(exc) is True


def test_openai_timeout_error_is_transient():
    from openai import APITimeoutError

    exc = APITimeoutError(request=None)
    assert oc._is_transient_openrouter_error(exc) is True


def test_stdlib_connection_and_timeout_are_transient():
    assert oc._is_transient_openrouter_error(ConnectionResetError("reset")) is True
    assert oc._is_transient_openrouter_error(TimeoutError("timed out")) is True


def test_openrouter_value_error_codes_still_classified():
    assert oc._is_transient_openrouter_error(
        ValueError({"message": "Provider returned error", "code": 503})) is True
    assert oc._is_transient_openrouter_error(
        ValueError({"message": "Provider returned error", "code": 402})) is False
    assert oc._is_transient_openrouter_error(ValueError("no such key")) is False


def test_retry_on_transient_retries_connection_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(oc.random, "uniform", lambda a, b: 0.0)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            from openai import APIConnectionError

            raise APIConnectionError(request=None)
        return "ok"

    out = oc._retry_on_transient(flaky, attempts=6, base_delay=0.001)
    assert out == "ok"
    assert calls["n"] == 3


def test_retry_on_transient_raises_permanent_error_immediately():
    calls = {"n": 0}

    def broken():
        calls["n"] += 1
        raise ValueError({"message": "insufficient credits", "code": 402})

    with pytest.raises(ValueError):
        oc._retry_on_transient(broken, attempts=6, base_delay=0.001)
    assert calls["n"] == 1
