"""MOEX market data, instruments, news, fundamentals and risk utilities.

This package is market-specific: every function here works with Russian
market data (MOEX ISS, Russian news feeds) and never silently falls back
to another market's data source.
"""

__version__ = "0.1.0"

MARKET_ID = "MOEX"
