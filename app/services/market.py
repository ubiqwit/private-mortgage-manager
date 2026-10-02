"""Live market data: Bank of Canada series (Valet API) and news feeds, cached in the database.

The Valet API (https://www.bankofcanada.ca/valet/docs) is free and needs no key. Each
series is fetched separately so one renamed/retired series can't break the others.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime

from dateutil.relativedelta import relativedelta

from .. import db
from ..models import MarketObservation, NewsItem, Setting
from ..timeutil import today as local_today

VALET = "https://www.bankofcanada.ca/valet/observations/{series}/json"
PRIME_SERIES = "V80691311"  # Chartered bank prime business rate (weekly)
POLICY_SERIES = "V39079"  # Target for the overnight rate


@dataclass(frozen=True)
class Series:
    id: str
    label: str
    short: str
    frequency: str
    note: str = ""


SERIES = [
    Series(POLICY_SERIES, "Bank of Canada policy rate (target for the overnight rate)", "Policy rate", "daily",
           "Set by the Bank of Canada at its scheduled announcements; drives prime."),
    Series(PRIME_SERIES, "Prime rate (chartered banks)", "Prime", "weekly",
           "Variable-rate mortgages priced as prime + spread follow this."),
    Series("V80691335", "Posted 5-year conventional mortgage rate", "5-yr posted mortgage", "weekly",
           "Big-bank posted rate — context for pricing renewals."),
    Series("BD.CDN.2YR.DQ.YLD", "Government of Canada 2-year bond yield", "2-yr GoC", "daily",
           "Markets' expectation for rates over the next two years."),
    Series("BD.CDN.5YR.DQ.YLD", "Government of Canada 5-year bond yield", "5-yr GoC", "daily",
           "Benchmark for 5-year fixed mortgage pricing."),
    Series("BD.CDN.10YR.DQ.YLD", "Government of Canada 10-year bond yield", "10-yr GoC", "daily"),
    Series("V41690973", "Consumer Price Index (all items, 2002=100)", "CPI index", "monthly",
           "Inflation is what the Bank of Canada targets (2%)."),
]
SERIES_BY_ID = {s.id: s for s in SERIES}

DEFAULT_FEEDS = [
    ("Bank of Canada press releases", "https://www.bankofcanada.ca/content_type/press-releases/feed/"),
    ("News: Bank of Canada interest rates",
     "https://news.google.com/rss/search?q=%22Bank+of+Canada%22+interest+rate&hl=en-CA&gl=CA&ceid=CA:en"),
    ("News: Ontario housing market",
     "https://news.google.com/rss/search?q=Ontario+housing+market+prices&hl=en-CA&gl=CA&ceid=CA:en"),
]
STALE_AFTER = timedelta(hours=6)
TIMEOUT = 8


# ----------------------------------------------------------------------------
# Reading cached data
# ----------------------------------------------------------------------------
def latest(series: str):
    return (
        MarketObservation.query.filter_by(series=series)
        .order_by(MarketObservation.date.desc())
        .first()
    )


def latest_prime():
    obs = latest(PRIME_SERIES)
    return obs.value if obs else None


def history(series: str, since: date):
    return (MarketObservation.query.filter(MarketObservation.series == series, MarketObservation.date >= since)
            .order_by(MarketObservation.date).all())


def rate_changes(series: str = POLICY_SERIES, since: date | None = None):
    """Dates on which a (step-like) rate changed, newest first: [(date, old, new, bp)]."""
    q = MarketObservation.query.filter_by(series=series)
    if since:
        q = q.filter(MarketObservation.date >= since)
    changes, prev = [], None
    for obs in q.order_by(MarketObservation.date):
        v = Decimal(str(obs.value))
        if prev is not None and v != prev:
            changes.append((obs.date, prev, v, int((v - prev) * 100)))
        prev = v
    return list(reversed(changes))


def recent_policy_change(days: int = 45):
    """The latest policy-rate move if it happened in the last ``days`` days."""
    since = local_today() - timedelta(days=days)
    changes = rate_changes(POLICY_SERIES, since=since - timedelta(days=30))
    if changes and changes[0][0] >= since:
        return changes[0]
    return None


def change_over(series: str, days: int):
    """(latest value, value ~``days`` ago, difference) or None."""
    now = latest(series)
    if not now:
        return None
    past = (MarketObservation.query.filter(MarketObservation.series == series,
                                           MarketObservation.date <= now.date - timedelta(days=days))
            .order_by(MarketObservation.date.desc()).first())
    if not past:
        return None
    return Decimal(str(now.value)), Decimal(str(past.value)), Decimal(str(now.value)) - Decimal(str(past.value))


def cpi_inflation():
    """Year-over-year CPI inflation from the index series: (month, pct) or None."""
    now = latest("V41690973")
    if not now:
        return None
    year_ago = (MarketObservation.query.filter(MarketObservation.series == "V41690973",
                                               MarketObservation.date <= now.date - relativedelta(months=12))
                .order_by(MarketObservation.date.desc()).first())
    if not year_ago or not year_ago.value:
        return None
    pct = (Decimal(str(now.value)) / Decimal(str(year_ago.value)) - 1) * 100
    return now.date, pct.quantize(Decimal("0.1"))


def last_fetch():
    raw = Setting.get("market_fetched_at")
    return datetime.fromisoformat(raw) if raw else None


def is_stale():
    fetched = last_fetch()
    return fetched is None or datetime.utcnow() - fetched > STALE_AFTER


def feeds():
    raw = Setting.get("news_feeds")
    if raw is None:
        return list(DEFAULT_FEEDS)
    out = []
    for line in raw.splitlines():
        if "|" in line:
            label, url = line.split("|", 1)
            if url.strip().startswith(("http://", "https://")):
                out.append((label.strip(), url.strip()))
    return out


# ----------------------------------------------------------------------------
# Fetching
# ----------------------------------------------------------------------------
def parse_valet(payload: dict, series: str):
    """[(date, Decimal)] from a Valet observations response."""
    out = []
    for obs in payload.get("observations", []):
        raw = (obs.get(series) or {}).get("v")
        if raw in (None, ""):
            continue
        try:
            out.append((date.fromisoformat(obs["d"]), Decimal(str(raw))))
        except (KeyError, ValueError, ArithmeticError):
            continue
    return out


def store_observations(series: str, points, source="boc") -> int:
    if not points:
        return 0
    start = min(d for d, _ in points)
    existing = {o.date: o for o in MarketObservation.query.filter(MarketObservation.series == series,
                                                                  MarketObservation.date >= start)}
    added = 0
    for d, v in points:
        row = existing.get(d)
        if row is None:
            db.session.add(MarketObservation(series=series, date=d, value=v, source=source))
            added += 1
        elif Decimal(str(row.value)) != v:
            row.value, row.source = v, source
    return added


def series_request(s: Series, years_back: int = 4):
    """(url, params) for the observations we don't have yet (plus a little overlap for revisions)."""
    last = (MarketObservation.query.filter_by(series=s.id, source="boc")
            .order_by(MarketObservation.date.desc()).first())
    start = last.date - timedelta(days=21) if last else local_today() - relativedelta(years=years_back)
    return VALET.format(series=s.id), {"start_date": start.isoformat()}


def strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def parse_feed(xml_bytes: bytes, source: str):
    """Items from RSS 2.0 or Atom: [dict(title, link, published, summary, source)]."""
    root = ET.fromstring(xml_bytes)
    items = []
    atom = "{http://www.w3.org/2005/Atom}"
    for item in root.iter("item"):
        items.append(dict(
            title=strip_html(item.findtext("title")),
            link=(item.findtext("link") or "").strip(),
            published=_parse_when(item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")),
            summary=strip_html(item.findtext("description"))[:600],
            source=strip_html(item.findtext("source")) or source,
        ))
    for entry in root.iter(f"{atom}entry"):
        link_el = entry.find(f"{atom}link[@rel='alternate']")
        if link_el is None:  # (Elements without children are falsy, so no `or` here.)
            link_el = entry.find(f"{atom}link")
        items.append(dict(
            title=strip_html(entry.findtext(f"{atom}title")),
            link=(link_el.get("href") if link_el is not None else "").strip(),
            published=_parse_when(entry.findtext(f"{atom}updated") or entry.findtext(f"{atom}published")),
            summary=strip_html(entry.findtext(f"{atom}summary"))[:600],
            source=source,
        ))
    return [i for i in items if i["title"] and i["link"].startswith(("http://", "https://"))]


def _parse_when(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def store_news(items) -> int:
    links = [i["link"][:1000] for i in items]
    existing = {n for (n,) in db.session.query(NewsItem.link).filter(NewsItem.link.in_(links))}
    added = 0
    for i in items:
        link = i["link"][:1000]
        if link in existing:
            continue
        existing.add(link)
        db.session.add(NewsItem(source=i["source"][:100], title=i["title"][:500], link=link,
                                published=i["published"], summary=i["summary"]))
        added += 1
    # Keep the table small.
    cutoff = datetime.utcnow() - timedelta(days=120)
    NewsItem.query.filter(NewsItem.published.isnot(None), NewsItem.published < cutoff).delete()
    return added


def _default_get():
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "PrivateMortgageManager/1.0 (+self-hosted)"

    def get(url, params=None):
        resp = session.get(url, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.content

    return get


def refresh(force: bool = False, get=None) -> dict:
    """Fetch every series and feed. Errors are collected, never raised.

    HTTP requests run in parallel (so an unreachable host costs one timeout, not ten);
    parsing and database writes happen on the calling thread.
    """
    import json
    from concurrent.futures import ThreadPoolExecutor

    result = {"series": {}, "news": 0, "errors": []}
    if not force and not is_stale():
        return result
    get = get or _default_get()

    jobs = [("series", s, *series_request(s)) for s in SERIES] + [("feed", label, url, None) for label, url in feeds()]

    def run(job):
        kind, what, url, params = job
        try:
            return job, get(url, params), None
        except Exception as exc:  # noqa: BLE001 — network/HTTP errors are reported, not fatal
            return job, None, exc

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(run, jobs))

    for (kind, what, _url, _params), body, exc in responses:
        name = what.short if kind == "series" else what
        if exc is not None:
            result["errors"].append(f"{name}: {_short_error(exc)}")
            continue
        try:
            if kind == "series":
                result["series"][what.id] = store_observations(what.id, parse_valet(json.loads(body), what.id))
            else:
                result["news"] += store_news(parse_feed(body, what)[:40])
        except Exception as exc:  # noqa: BLE001 — malformed payloads are reported, not fatal
            result["errors"].append(f"{name}: could not read the response ({_short_error(exc)})")

    Setting.set("market_fetched_at", datetime.utcnow().isoformat(timespec="seconds"))
    Setting.set("market_last_errors", "\n".join(result["errors"]))
    db.session.commit()
    return result


def _short_error(exc) -> str:
    text = str(exc) or exc.__class__.__name__
    return text if len(text) < 160 else text[:157] + "…"


# ----------------------------------------------------------------------------
# What it means for the book
# ----------------------------------------------------------------------------
SCENARIOS_BP = (-50, -25, 0, 25, 50)


def portfolio_impact(prime=None) -> dict:
    """How prime moves change income on variable loans, plus context for fixed loans and renewals."""
    from ..models import OPEN_STATUSES, Mortgage
    from . import calc

    prime = Decimal(str(prime)) if prime is not None else None
    book = Mortgage.query.filter(Mortgage.status.in_(OPEN_STATUSES)).order_by(Mortgage.reference).all()
    variable, fixed = [], []
    for m in book:
        bal = m.balance()
        if bal <= 0:
            continue
        (variable if m.rate_type == "variable" else fixed).append((m, bal))

    var_rows = []
    scenario_totals = {bp: Decimal(0) for bp in SCENARIOS_BP}
    for m, bal in variable:
        row = dict(m=m, balance=bal, rate=m.effective_rate(prime), floor=m.rate_floor, spread=m.prime_spread,
                   at_floor=bool(prime is not None and m.rate_floor is not None
                                 and m.effective_rate(prime) == Decimal(str(m.rate_floor))))
        row["monthly"] = calc.monthly_interest(bal, row["rate"], m.compounding)
        var_rows.append(row)
        if prime is not None:
            for bp in SCENARIOS_BP:
                rate = m.effective_rate(prime + Decimal(bp) / 100)
                scenario_totals[bp] += calc.monthly_interest(bal, rate, m.compounding)
    base = scenario_totals[0]
    scenarios = [(bp, calc.money(v), calc.money(v - base)) for bp, v in scenario_totals.items()] if prime is not None else []

    fixed_balance = sum((b for _, b in fixed), Decimal(0))
    fixed_rate = (sum((b * Decimal(str(m.interest_rate)) for m, b in fixed), Decimal(0)) / fixed_balance
                  if fixed_balance else None)
    goc5 = latest("BD.CDN.5YR.DQ.YLD")
    posted5 = latest("V80691335")

    today = local_today()
    renewals = []
    for m, bal in variable + fixed:
        days = (m.maturity_date - today).days
        if days <= 180:
            renewals.append(dict(m=m, balance=bal, days=days, rate=m.effective_rate(prime),
                                 spread_over_prime=(m.effective_rate(prime) - prime) if prime is not None else None))
    renewals.sort(key=lambda r: r["days"])

    return dict(
        prime=prime, variable=var_rows, scenarios=scenarios,
        variable_balance=calc.money(sum((r["balance"] for r in var_rows), Decimal(0))),
        fixed_count=len(fixed), fixed_balance=calc.money(fixed_balance),
        fixed_rate=calc.money(fixed_rate) if fixed_rate is not None else None,
        spread_over_goc5=(calc.money(fixed_rate - Decimal(str(goc5.value))) if fixed_rate is not None and goc5 else None),
        spread_over_posted=(calc.money(fixed_rate - Decimal(str(posted5.value))) if fixed_rate is not None and posted5 else None),
        renewals=renewals,
    )
