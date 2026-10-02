"""Market data lookups (Bank of Canada series cached in the database)."""
from __future__ import annotations

from ..models import MarketObservation

PRIME_SERIES = "V80691311"  # Chartered bank prime business rate (weekly)


def latest(series: str):
    return (
        MarketObservation.query.filter_by(series=series)
        .order_by(MarketObservation.date.desc())
        .first()
    )


def latest_prime():
    obs = latest(PRIME_SERIES)
    return obs.value if obs else None
