"""Shared HTTP helpers for MOEX and Russian news sources.

Design rules:

* one session, sane timeouts, bounded retries with backoff;
* small on-disk JSON cache with per-call TTL so a whole analysis run does
  not re-fetch the same ISS pages, while quotes stay fresh;
* honest errors — a failed fetch raises :class:`MoexHttpError` instead of
  returning an empty result that could be mistaken for "no news/no data".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

USER_AGENT = (
    "TradingAgents-MOEX/0.1 (+https://github.com/local/TradingAgents-MOEX; research)"
)

DEFAULT_TIMEOUT = (5.0, 30.0)  # connect, read


class MoexHttpError(RuntimeError):
    """Raised when an HTTP call to a MOEX/RU source fails."""

    def __init__(self, message: str, *, url: str = "", status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


def _cache_root() -> Path:
    base = os.environ.get("TRADINGAGENTS_HOME") or os.path.join(
        os.path.expanduser("~"), ".tradingagents"
    )
    return Path(base) / "cache" / "moex"


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _cache_root() / f"{digest}.json"


def cache_get(key: str, ttl: float) -> Any | None:
    """Return cached payload if younger than ``ttl`` seconds, else None."""
    path = _cache_path(key)
    try:
        if not path.exists():
            return None
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - envelope.get("ts", 0) > ttl:
            return None
        return envelope.get("data")
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(key: str, data: Any) -> None:
    path = _cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        logger.debug("cache write failed for %s", key, exc_info=True)


def cache_invalidate(key: str) -> None:
    """Drop a cached entry (e.g. a transient anti-bot HTML page)."""
    try:
        _cache_path(key).unlink()
    except OSError:
        pass


def build_session(
    *,
    retries: int = 3,
    backoff: float = 0.8,
    status_forcelist: tuple = (429, 500, 502, 503, 504),
) -> requests.Session:
    """A requests session with retry/backoff tuned for JSON APIs."""
    session = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=backoff,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return session


_SESSION: requests.Session | None = None


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = build_session()
    return _SESSION


def get_json(
    url: str,
    params: dict | None = None,
    *,
    ttl: float = 0.0,
    timeout=DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
) -> Any:
    """GET ``url`` (with ``params``) and parse JSON.

    ``ttl`` > 0 enables the on-disk cache. Network failures raise
    :class:`MoexHttpError` after the session-level retries are exhausted.
    """
    full_url = url if not params else f"{url}?{urlencode(params)}"
    if ttl > 0:
        cached = cache_get(full_url, ttl)
        if cached is not None:
            return cached
    sess = session or get_session()
    try:
        resp = sess.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise MoexHttpError(f"request failed: {exc}", url=full_url) from exc
    if resp.status_code >= 400:
        raise MoexHttpError(
            f"HTTP {resp.status_code} from {full_url}",
            url=full_url,
            status=resp.status_code,
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise MoexHttpError(f"invalid JSON from {full_url}", url=full_url) from exc
    if ttl > 0:
        cache_put(full_url, data)
    return data


def get_bytes(
    url: str,
    params: dict | None = None,
    *,
    ttl: float = 0.0,
    timeout=DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    accept: str | None = None,
    user_agent: str | None = None,
) -> bytes:
    """GET ``url`` and return raw bytes (feeds whose XML declares its own
    encoding — ET then decodes per the declaration, avoiding mojibake)."""
    import base64

    full_url = url if not params else f"{url}?{urlencode(params)}"
    if ttl > 0:
        cached = cache_get(full_url, ttl)
        if cached is not None:
            try:
                return base64.b64decode(cached)
            except (ValueError, TypeError):
                pass
    sess = session or get_session()
    headers: dict[str, str] = {}
    if accept:
        headers["Accept"] = accept
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        resp = sess.get(
            url, params=params, timeout=timeout, headers=headers or None
        )
    except requests.RequestException as exc:
        raise MoexHttpError(f"request failed: {exc}", url=full_url) from exc
    if resp.status_code >= 400:
        raise MoexHttpError(
            f"HTTP {resp.status_code} from {full_url}",
            url=full_url,
            status=resp.status_code,
        )
    body = resp.content
    if ttl > 0:
        cache_put(full_url, base64.b64encode(body).decode("ascii"))
    return body


def get_text(
    url: str,
    params: dict | None = None,
    *,
    ttl: float = 0.0,
    timeout=DEFAULT_TIMEOUT,
    session: requests.Session | None = None,
    accept: str | None = None,
    user_agent: str | None = None,
) -> str:
    """GET ``url`` and return raw text (for RSS/Atom feeds)."""
    full_url = url if not params else f"{url}?{urlencode(params)}"
    if ttl > 0:
        cached = cache_get(full_url, ttl)
        if cached is not None:
            return cached
    sess = session or get_session()
    headers: dict[str, str] = {}
    if accept:
        headers["Accept"] = accept
    if user_agent:
        headers["User-Agent"] = user_agent
    try:
        resp = sess.get(
            url,
            params=params,
            timeout=timeout,
            headers=headers or None,
        )
    except requests.RequestException as exc:
        raise MoexHttpError(f"request failed: {exc}", url=full_url) from exc
    if resp.status_code >= 400:
        raise MoexHttpError(
            f"HTTP {resp.status_code} from {full_url}",
            url=full_url,
            status=resp.status_code,
        )
    text = resp.text
    if ttl > 0:
        cache_put(full_url, text)
    return text
