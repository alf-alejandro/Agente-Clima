import requests
import json
from datetime import datetime, timezone

from app.config import (
    GAMMA, WEATHER_CITIES, MIN_NO_PRICE, MAX_NO_PRICE,
    MAX_YES_PRICE, MIN_VOLUME, MIN_PROFIT_CENTS,
)


def now_utc():
    return datetime.now(timezone.utc)


def parse_price(val):
    try:
        return float(val)
    except Exception:
        return None


def parse_date(val):
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except Exception:
        return None


def get_prices(m):
    raw = m.get("outcomePrices") or "[]"
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = parse_price(prices[0]) if len(prices) > 0 else None
        no  = parse_price(prices[1]) if len(prices) > 1 else None
        if yes is not None and yes < 0:
            yes = None
        if no is not None and no < 0:
            no = None
        if yes == 0.0 and no is not None and no >= 0.99:
            yes = 0.001
        if no == 0.0 and yes is not None and yes >= 0.99:
            no = 0.001
        return yes, no
    except Exception:
        return None, None


def build_event_slug(city, date):
    months = {
        1: "january", 2: "february", 3: "march", 4: "april",
        5: "may", 6: "june", 7: "july", 8: "august",
        9: "september", 10: "october", 11: "november", 12: "december",
    }
    return f"highest-temperature-in-{city}-on-{months[date.month]}-{date.day}-{date.year}"


def fetch_event_by_slug(slug):
    try:
        r = requests.get(f"{GAMMA}/events", params={"slug": slug, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except Exception:
        pass
    return None


def fetch_market_live(slug):
    try:
        r = requests.get(f"{GAMMA}/markets", params={"slug": slug, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except Exception:
        pass
    return None


def scan_opportunities(existing_ids=None):
    """Scan for NO-side weather opportunities, excluding already-held IDs."""
    if existing_ids is None:
        existing_ids = set()

    today = now_utc().date()
    opportunities = []

    for city in WEATHER_CITIES:
        slug = build_event_slug(city, today)
        event = fetch_event_by_slug(slug)
        if not event:
            continue

        for m in (event.get("markets") or []):
            condition_id = m.get("conditionId")
            if condition_id in existing_ids:
                continue

            yes_price, no_price = get_prices(m)
            if yes_price is None or no_price is None:
                continue

            volume = parse_price(m.get("volume") or 0) or 0
            if volume < MIN_VOLUME:
                continue

            if not (MIN_NO_PRICE <= no_price <= MAX_NO_PRICE and yes_price <= MAX_YES_PRICE):
                continue

            profit = (1.0 - no_price) * 100
            if profit < MIN_PROFIT_CENTS:
                continue

            end_dt = parse_date(m.get("endDate"))
            if end_dt and (now_utc() - end_dt).total_seconds() > 0:
                continue

            opportunities.append({
                "condition_id": condition_id,
                "city": city,
                "question": m.get("question", ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "volume": volume,
                "end_date": end_dt.isoformat() if end_dt else None,
                "slug": m.get("slug", ""),
                "profit_cents": round(profit, 1),
            })

    opportunities.sort(key=lambda x: x["no_price"], reverse=True)
    return opportunities
