"""T-Invest sandbox integration (accounts, portfolio, orders, operations).

Safety rails (spec §5.1, §6.5, §13):
* production client is read-only in this version — order methods refuse
  when ``sandbox=False``;
* every trade goes through an explicit journal for idempotency;
* tokens only via env, never logged.
"""

from .client import (
    PROD_BASE,
    SANDBOX_BASE,
    TInvestClient,
    TInvestError,
    _money,
    _quotation,
    cert_bundle,
    quotation_to_float,
)

__all__ = [
    "PROD_BASE",
    "SANDBOX_BASE",
    "TInvestClient",
    "TInvestError",
    "_money",
    "_quotation",
    "cert_bundle",
    "quotation_to_float",
]
