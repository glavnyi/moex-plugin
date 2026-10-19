"""T-Invest sandbox integration (accounts, portfolio, orders, operations).

Safety rails (spec §5.1, §6.5, §13):
* the five §5.1 connection modes are resolved once per client —
  ``readonly``/``live_readonly`` talk to production and refuse every write;
* order methods refuse when ``client.can_mutate`` is false, so a
  read-only client cannot place or cancel anything;
* every trade goes through an explicit journal for idempotency;
* tokens only via env, never logged.
"""

from .client import (
    MODES,
    MUTATING_MODES,
    PROD_BASE,
    READ_ONLY_MODES,
    SANDBOX_BASE,
    TInvestClient,
    TInvestError,
    _money,
    _quotation,
    cert_bundle,
    quotation_to_float,
)

__all__ = [
    "MODES",
    "MUTATING_MODES",
    "PROD_BASE",
    "READ_ONLY_MODES",
    "SANDBOX_BASE",
    "TInvestClient",
    "TInvestError",
    "_money",
    "_quotation",
    "cert_bundle",
    "quotation_to_float",
]
