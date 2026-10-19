"""T-Invest REST client (official T-Invest API v1).

* JSON `POST {base}{Service}/{Method}` with `Authorization: Bearer`;
* five connection modes (spec §5.1) resolved once at construction time:

    disabled       — never constructed, ``from_mode`` refuses it
    readonly       — production base, reads only
    sandbox        — sandbox base, reads + writes
    live_readonly  — production base, reads only
    live_trading   — production base, reads + writes

  ``client.sandbox`` says which base/venue is in use; ``client.can_mutate``
  says whether write calls are permitted. They are deliberately separate:
  a production read-only client must still talk to ``PROD_BASE``.
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

# spec §5.1 — the five connection modes, in declaration order.
MODES: tuple[str, ...] = (
    "disabled", "readonly", "sandbox", "live_readonly", "live_trading",
)
READ_ONLY_MODES: frozenset[str] = frozenset({"readonly", "live_readonly"})
MUTATING_MODES: frozenset[str] = frozenset({"sandbox", "live_trading"})

_CERT_DIR = Path(__file__).parent / "certs"


class TInvestError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None,
                 http_status: int | None = None, payload=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.payload = payload


def cert_bundle() -> str:
    """Path to combined CA bundle (certifi + Russian trusted roots).

    Rebuilt only when the inputs actually change, and always atomically:
    concurrent callers (the autotrade daemon and an HTTP handler) used to
    truncate the same file mid-write, and the loser read a half-written PEM
    as ``SSLError [X509] PEM lib``.
    """
    combined = _CERT_DIR / "combined-bundle.pem"
    ru_root = _CERT_DIR / "russian_trusted_root_ca.pem"
    try:
        import certifi

        base = Path(certifi.where()).read_bytes()
        ru = ru_root.read_bytes() if ru_root.exists() else b""
        payload = base + b"\n" + ru
        if not combined.exists() or combined.read_bytes() != payload:
            _atomic_write(combined, payload)
    except Exception:  # noqa: BLE001 — fall back to ru-only / system
        if ru_root.exists():
            return str(ru_root)
        return ""
    return str(combined)


def _atomic_write(path: Path, payload: bytes) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".bundle-", suffix=".pem",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _token() -> str:
    """The API token, from env only (spec §13) — never from settings."""
    return os.environ.get("TINVEST_TOKEN") or os.environ.get(
        "TRADINGAGENTS_TINVEST_TOKEN"
    ) or ""


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
    def __init__(self, token: str, *, mode: str = "sandbox",
                 sandbox: bool | None = None,
                 base_url: str | None = None, timeout=10.0):
        if not token:
            raise TInvestError("empty T-Invest token")
        # ``sandbox=`` is the pre-§5.1 constructor; keep it working.
        if sandbox is not None and mode == "sandbox":
            mode = "sandbox" if sandbox else "live_readonly"
        mode = str(mode or "sandbox").lower()
        if mode not in MODES:
            raise TInvestError(
                f"unknown tinvest mode {mode!r}; use one of {', '.join(MODES)}"
            )
        if mode == "disabled":
            raise TInvestError(
                "tinvest.mode is 'disabled' (§5.1) — nothing to connect to"
            )
        self.token = token
        self.mode = mode
        self.sandbox = mode == "sandbox"
        self.base_url = base_url or (SANDBOX_BASE if self.sandbox else PROD_BASE)
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    # -- mode introspection -------------------------------------------
    @property
    def environment(self) -> str:
        """``SANDBOX`` or ``PRODUCTION`` — shown in every trading surface."""
        return "SANDBOX" if self.sandbox else "PRODUCTION"

    @property
    def can_mutate(self) -> bool:
        """Whether write calls (orders, stops, account mutations) are allowed.

        Only ``sandbox`` and ``live_trading`` (spec §5.1 modes 3 and 5).
        """
        return self.mode in MUTATING_MODES

    @property
    def can_read(self) -> bool:
        """Every constructed mode can read; only ``disabled`` cannot exist."""
        return True

    def mode_info(self) -> dict:
        """The mode block surfaced by ``GET /api/tinvest/status``."""
        return {
            "mode": self.mode,
            "environment": self.environment,
            "sandbox": self.sandbox,
            "can_read": self.can_read,
            "can_mutate": self.can_mutate,
            "base_url": self.base_url,
        }

    def require_mutation(self, what: str) -> None:
        """Raise unless this client may write. Call before any mutating call."""
        if not self.can_mutate:
            raise TInvestError(
                f"{what} refused: tinvest.mode={self.mode!r} is read-only "
                f"({self.environment})"
            )

    # -- lifecycle ----------------------------------------------------
    @classmethod
    def from_mode(cls, mode: str, *, timeout=10.0) -> "TInvestClient | None":
        """Build a client for a §5.1 mode, or ``None`` without a token."""
        token = _token()
        if not token:
            return None
        return cls(token, mode=mode, timeout=timeout)

    @classmethod
    def from_settings(cls, *, sandbox: bool = True,
                      mode: str | None = None,
                      timeout=10.0) -> "TInvestClient | None":
        token = _token()
        if not token:
            return None
        return cls(token, mode=mode or ("sandbox" if sandbox else "live_readonly"),
                   timeout=timeout)

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
