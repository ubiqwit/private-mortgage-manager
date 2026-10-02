import json
from datetime import date, timedelta
from decimal import Decimal

from app import db
from app.models import MarketObservation, NewsItem
from app.services import market

from .conftest import PASSWORD, make_user
from .test_mortgages import create

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Bank of Canada reduces policy rate by 25 basis points</title><link>https://example.com/a</link>
<pubDate>Wed, 17 Sep 2026 13:45:00 GMT</pubDate><description>&lt;p&gt;Rate now 2.50%&lt;/p&gt;</description></item>
<item><title>Bad link</title><link>javascript:alert(1)</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><title>Housing starts rise</title><link href="https://example.com/b"/><updated>2026-09-20T10:00:00Z</updated></entry>
</feed>"""


def valet(series, points):
    return json.dumps({
        "seriesDetail": {series: {"label": series}},
        "observations": [{"d": d, series: {"v": v}} for d, v in points],
    }).encode()


def fake_get(responses):
    def get(url, params=None):
        for key, body in responses.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return body
        raise ConnectionError("blocked")
    return get


def test_parse_valet_skips_blanks():
    payload = json.loads(valet("V39079", [("2026-09-16", "2.75"), ("2026-09-17", "2.50"), ("2026-09-18", "")]))
    assert market.parse_valet(payload, "V39079") == [(date(2026, 9, 16), Decimal("2.75")), (date(2026, 9, 17), Decimal("2.50"))]


def test_parse_feeds():
    items = market.parse_feed(RSS, "BoC")
    assert len(items) == 1  # javascript: link dropped
    assert items[0]["summary"] == "Rate now 2.50%" and items[0]["published"].day == 17
    atom = market.parse_feed(ATOM, "News")
    assert atom[0]["link"] == "https://example.com/b"


def test_refresh_stores_data_and_reports_errors(app):
    get = fake_get({
        "V39079": valet("V39079", [("2026-06-03", "2.75"), ("2026-09-17", "2.50")]),
        "V80691311": valet("V80691311", [("2026-06-04", "4.95"), ("2026-09-18", "4.70")]),
        "press-releases": RSS,
        "news.google.com": ATOM,
    })
    result = market.refresh(force=True, get=get)
    assert result["series"]["V39079"] == 2
    assert market.latest_prime() == Decimal("4.70")
    assert NewsItem.query.count() == 2
    assert any("5-yr GoC" in e for e in result["errors"])  # unreachable series reported, not raised
    # Second run: nothing new, no duplicates.
    again = market.refresh(force=True, get=get)
    assert again["series"]["V39079"] == 0 and NewsItem.query.count() == 2
    assert market.rate_changes() == [(date(2026, 9, 17), Decimal("2.75"), Decimal("2.50"), -25)]
    assert not market.is_stale()


def test_portfolio_impact_with_floor(client):
    create(client, rate_type="variable", prime_spread="5", rate_floor="9.5", record_lender_fee="")
    create(client, borrower_name="Fixed Fred", property_address="2 Oak", record_lender_fee="")
    impact = market.portfolio_impact(Decimal("4.70"))
    assert len(impact["variable"]) == 1 and impact["fixed_count"] == 1
    scen = {bp: (total, diff) for bp, total, diff in impact["scenarios"]}
    # 9.70% on $200k monthly = 1616.67; -25bp hits the 9.50% floor (1583.33); -50bp stays at the floor.
    assert scen[0][0] == Decimal("1616.67")
    assert scen[-25][0] == scen[-50][0] == Decimal("1583.33")
    assert scen[25][1] == Decimal("41.66")  # 1658.33 - 1616.67


def test_market_page_and_manual_prime(client):
    assert client.get("/market/").status_code == 200
    resp = client.post("/market/manual", data={"series": "V80691311", "value": "4.70", "date": "2026-09-18"})
    assert resp.status_code == 302
    obs = MarketObservation.query.one()
    assert obs.source == "manual" and obs.value == Decimal("4.70")
    m = create(client, rate_type="variable", prime_spread="5", record_lender_fee="")
    page = client.get(f"/mortgages/{m.id}")
    assert b"9.70%" in page.data


def test_dashboard_shows_recent_policy_move(app, client):
    today = date.today()
    db.session.add_all([
        MarketObservation(series="V39079", date=today - timedelta(days=60), value=Decimal("2.75")),
        MarketObservation(series="V39079", date=today - timedelta(days=5), value=Decimal("2.50")),
    ])
    db.session.commit()
    create(client)
    assert b"cut the policy rate 25 bp" in client.get("/").data


def test_viewer_can_view_but_not_edit_rates(app):
    make_user("acct@example.com", role="viewer")
    c = app.test_client()
    c.post("/login", data={"email": "acct@example.com", "password": PASSWORD})
    assert c.get("/market/").status_code == 200
    assert c.post("/market/manual", data={"value": "4.7", "date": "2026-09-18"}).status_code == 403
