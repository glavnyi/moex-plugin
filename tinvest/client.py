"""T-Invest REST client (official T-Invest API v1).

* JSON `POST {base}{Service}/{Method}` with `Authorization: Bearer`;
* sandbox vs production bases are never mixed — ``sandbox=True`` is the
  only mode this project trades in (real trading is out of scope);
* TLS: system CA store + bundled Russian trusted roots (Root + Sub CA,
  extracted live from the T-Bank chain 2026-10-13) — tbank.ru certs are
  signed by the Russian national chain absent from default Linux stores;
* token comes only from env (``TINVEST_TOKEN``), never from settings
  files or logs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SANDBOX_BASE = "https://sandbox-invest-public-api.tinkoff.ru/rest/"
PROD_BASE = "https://invest-public-api.tinkoff.ru/rest/"
SERVICE_PREFIX = "tinkoff.public.invest.api.contract.v1."

_CERT_DIR = Path(__file__).parent / "certs"


class TInvestError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None,
                 http_status: int | None = None, payload=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.payload = payload


def cert_bundle() -> str:
    """Path to combined CA bundle (certifi + Russian trusted roots)."""
    combined = _CERT_DIR / "combined-bundle.pem"
    ru_root = _CERT_DIR / "russian_trusted_root_ca.pem"
    try:
        import certifi

        base = Path(certifi.where()).read_bytes()
        ru = ru_root.read_bytes() if ru_root.exists() else b""
        stamp = f"{len(base)}-{len(ru)}"
        if not combined.exists() or combined.stem != f"combined-{stamp}":
            combined.write_bytes(base + b"\n" + ru)
    except Exception:  # noqa: BLE001 — fall back to ru-only / system
        if ru_root.exists():
            return str(ru_root)
        return ""
    return str(combined)


def quotation_to_float(value) -> float | None:
    if not isinstance(value, dict):
        return None
    return float(value.get("units") or 0) + float(value.get("nano") or 0) / 1e9


def _money(amount: float, currency: str = "rub") -> dict:
    amount = float(amount)
    units = int(amount)
    nano = int(round((amount - units) * 1e9))
    return {"units": units, "nano": nano, "currency": currency.lower()}


def _quotation(amount: float) -> dict:
    units = int(amount)
    nano = int(round((amount - units) * 1e9))
    return {"units": units, "nano": nano}


class TInvestClient:
    def __init__(self, token: str, *, sandbox: bool = True,
                 base_url: str | None = None, timeout=10.0):
        if not token:
            raise TInvestError("empty T-Invest token")
        self.token = token
        self.sandbox = bool(sandbox)
        self.base_url = base_url or (SANDBOX_BASE if sandbox else PROD_BASE)
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # -- lifecycle ----------------------------------------------------
    @classmethod
    def from_settings(cls, *, sandbox: bool = True) -> "TInvestClient | None":
        token = os.environ.get("TINVEST_TOKEN") or os.environ.get(
            "TRADINGAGENTS_TINVEST_TOKEN"
        )
        if not token:
            return None
        return cls(token, sandbox=sandbox)

    def enabled(self) -> bool:
        return bool(self.token)

    # -- core call ----------------------------------------------------
    def call(self, service: str, method: str, payload: dict | None = None) -> dict:
        if not service.startswith(SERVICE_PREFIX):
            service = SERVICE_PREFIX + service
        url = f"{self.base_url}{service}/{method}"
        body = payload or {}
        try:
            resp = self._session.post(
                url, data=json.dumps(body), timeout=self.timeout,
                verify=cert_bundle() or True,
            )
        except requests.RequestException as exc:
            raise TInvestError(f"network error calling {method}: {exc}") from exc
        if resp.status_code >= 400:
            raise TInvestError(
                f"HTTP {resp.status_code} from {method}: {resp.text[:300]}",
                http_status=resp.status_code,
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise TInvestError(f"invalid JSON from {method}") from exc
        if isinstance(data, dict) and "code" in data and "message" in data and "payload" not in data:
            raise TInvestError(
                f"API error {data.get('code')} in {method}: {data.get('message')}",
                code=data.get("code"), payload=data,
            )
        return data.get("payload", data) if isinstance(data, dict) else data

    # -- convenience wrappers used across the app ---------------------
    def get_accounts(self) -> list[dict]:
        payload = self.call("UsersService", "GetAccounts", {})
        return payload.get("accounts") or []

    def get_sandbox_accounts(self) -> list[dict]:
        payload = self.call("SandboxService", "GetSandboxAccounts", {})
        return payload.get("accounts") or []

    def open_sandbox_account(self, currency: str = "rub") -> dict:
        payload = self.call("SandboxService", "OpenSandboxAccount",
                            {"currency": currency.lower()})
        return payload.get("account") or payload

    def close_sandbox_account(self, account_id: str) -> dict:
        return self.call("SandboxService", "CloseSandboxAccount",
                         {"accountId": account_id}) or {}

    def sandbox_pay_in(self, account_id: str, amount: float,
                       currency: str = "rub") -> dict:
        return self.call("SandboxService", "SandboxPayIn", {
            "accountId": account_id,
            "amount": _money(amount, currency),
        }) or {}
