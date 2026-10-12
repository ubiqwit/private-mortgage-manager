"""Live market data: Bank of Canada series (Valet API) and news feeds, cached in the database.

The Valet API (https://www.bankofcanada.ca/valet/docs) is free and needs no key. Each
series is fetched separately so one renamed/retired series can't break the others.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime

from dateutil.relativedelta import relativedelta

from .. import db
from ..models import MarketObservation, NewsItem, Setting
from ..timeutil import today as local_today
from ..timeutil import utcnow

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


def _prime_history():
    """Sorted (dates, values) for prime, cached for the current app context."""
    from flask import g, has_app_context

    if has_app_context() and "_prime_history" in g:
        return g._prime_history
    rows = (db.session.query(MarketObservation.date, MarketObservation.value)
            .filter(MarketObservation.series == PRIME_SERIES).order_by(MarketObservation.date).all())
    hist = ([d for d, _ in rows], [Decimal(str(v)) for _, v in rows])
    if has_app_context():
        g._prime_history = hist
    return hist


def clear_cache():
    from flask import g, has_app_context

    if has_app_context():
        g.pop("_prime_history", None)


def prime_on(day: date):
    """Prime in effect on ``day`` (the latest observation on or before it), or None."""
    from bisect import bisect_right

    dates, values = _prime_history()
    i = bisect_right(dates, day) - 1
    return values[i] if i >= 0 else None


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


def last_fetch():
    raw = Setting.get("market_fetched_at")
    return datetime.fromisoformat(raw) if raw else None


def is_stale():
    fetched = last_fetch()
    return fetched is None or utcnow() - fetched > STALE_AFTER


def feeds():
    return list(DEFAULT_FEEDS)


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
    clear_cache()
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
    from defusedxml.ElementTree import fromstring  # external content: refuse entity tricks

    root = fromstring(xml_bytes)
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
    cutoff = utcnow() - timedelta(days=120)
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

    Setting.set("market_fetched_at", utcnow().isoformat(timespec="seconds"))
    Setting.set("market_last_errors", "\n".join(result["errors"]))
    db.session.commit()
    return result


def _short_error(exc) -> str:
    text = str(exc) or exc.__class__.__name__
    return text if len(text) < 160 else text[:157] + "…"
