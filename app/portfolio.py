import threading
from collections import defaultdict
from app.scanner import now_utc, fetch_market_live, get_prices
from app.config import (
    MAX_POSITIONS, STOP_LOSS_RATIO, STOP_LOSS_ENABLED,
    PARTIAL_EXIT_THRESHOLD, REGION_MAP, MAX_REGION_EXPOSURE,
)


class AutoPortfolio:
    def __init__(self, initial_capital):
        self.lock = threading.Lock()
        self.capital_inicial = initial_capital
        self.capital_total = initial_capital
        self.capital_disponible = initial_capital
        self.positions = {}
        self.closed_positions = []
        self.session_start = now_utc()
        self.capital_history = [
            {"time": now_utc().isoformat(), "capital": initial_capital}
        ]
        self.stop_loss_ratio = STOP_LOSS_RATIO  # mutable at runtime

    def can_open_position(self):
        return (len(self.positions) < MAX_POSITIONS and
                self.capital_disponible >= 1)

    def open_position(self, opp, amount):
        tokens = amount / opp["no_price"]
        max_gain = tokens * 1.0 - amount

        # Dynamic stop: risk at most stop_loss_ratio * max_gain (R:R stays constant)
        dynamic_trigger = -(1.0 - opp["no_price"]) * self.stop_loss_ratio
        stop_price = opp["no_price"] + dynamic_trigger
        stop_value = tokens * stop_price
        stop_loss = stop_value - amount

        pos = {
            **opp,
            "entry_time": now_utc().isoformat(),
            "entry_no": opp["no_price"],
            "current_no": opp["no_price"],
            "allocated": amount,
            "tokens": tokens,
            "max_gain": max_gain,
            "stop_loss": stop_loss,
            "stop_trigger": dynamic_trigger,
            "status": "OPEN",
            "pnl": 0.0,
        }
        self.positions[opp["condition_id"]] = pos
        self.capital_disponible -= amount
        return True

    def get_position_slugs(self):
        """Return [(cid, slug), ...] for live-price fetching. Safe to call without lock."""
        return [(cid, pos["slug"]) for cid, pos in self.positions.items()]

    def apply_price_updates(self, price_map):
        """Apply pre-fetched {cid: (yes_price, no_price)} and close resolved/stopped positions.
        Must be called with self.lock held."""
        to_close = []

        for cid, (yes_price, no_price) in price_map.items():
            if cid not in self.positions:
                continue
            pos = self.positions[cid]
            pos["current_no"] = no_price

            if yes_price >= 0.99:
                resolution = f"YES resolvió — temperatura superó el umbral (YES={yes_price*100:.1f}¢)"
                to_close.append((cid, "LOST", -pos["allocated"], resolution))
            elif no_price >= 0.99:
                resolution = f"NO resolvió — temperatura no superó el umbral (NO={no_price*100:.1f}¢)"
                to_close.append((cid, "WON", pos["max_gain"], resolution))
            elif STOP_LOSS_ENABLED:
                drop = no_price - pos["entry_no"]
                if drop <= pos["stop_trigger"]:
                    sale_value = pos["tokens"] * no_price
                    realized_loss = sale_value - pos["allocated"]
                    resolution = (
                        f"Stop loss @ NO={no_price*100:.1f}¢ "
                        f"(entrada {pos['entry_no']*100:.1f}¢, caída {drop*100:.1f}¢)"
                    )
                    to_close.append((cid, "STOPPED", realized_loss, resolution))

        for cid, status, pnl, resolution in to_close:
            self._close_position(cid, status, pnl, resolution)

    def update_positions(self):
        """Legacy helper: fetch prices and apply.  WARNING — does HTTP; do NOT call while
        holding self.lock.  Prefer get_position_slugs() + apply_price_updates()."""
        slugs = self.get_position_slugs()
        price_map = {}
        for cid, slug in slugs:
            m = fetch_market_live(slug)
            if not m:
                continue
            yes_price, no_price = get_prices(m)
            if yes_price is not None and no_price is not None:
                price_map[cid] = (yes_price, no_price)
        if price_map:
            self.apply_price_updates(price_map)

    def _close_position(self, cid, status, pnl, resolution=""):
        if cid not in self.positions:
            return
        pos = self.positions[cid]
        pos["status"] = status
        pos["pnl"] = pnl
        pos["close_time"] = now_utc().isoformat()
        pos["resolution"] = resolution

        recovered = pos["allocated"] + pnl
        self.capital_disponible += recovered
        self.capital_total += pnl

        self.closed_positions.append(pos.copy())
        del self.positions[cid]

    # ── Partial exit ─────────────────────────────────────────────────────────

    def check_partial_exits(self):
        """Close 50% of positions that have captured PARTIAL_EXIT_THRESHOLD of max gain."""
        for cid, pos in list(self.positions.items()):
            if pos.get("partial_exited"):
                continue
            if pos["max_gain"] <= 0:
                continue
            current_pnl = pos["tokens"] * pos["current_no"] - pos["allocated"]
            if current_pnl / pos["max_gain"] >= PARTIAL_EXIT_THRESHOLD:
                self._partial_exit(cid)

    def _partial_exit(self, cid, fraction=0.5):
        pos = self.positions[cid]
        tokens_sold = pos["tokens"] * fraction
        sale_value = tokens_sold * pos["current_no"]
        cost_fraction = pos["allocated"] * fraction
        realized_pnl = sale_value - cost_fraction

        pos["tokens"] *= (1 - fraction)
        pos["allocated"] *= (1 - fraction)
        pos["max_gain"] *= (1 - fraction)
        pos["partial_exited"] = True

        self.capital_disponible += cost_fraction + realized_pnl
        self.capital_total += realized_pnl

        # Record so the dashboard shows this as a traceable P&L event
        self.closed_positions.append({
            "question": pos["question"],
            "city": pos.get("city", ""),
            "condition_id": cid,
            "entry_no": pos["entry_no"],
            "allocated": round(cost_fraction, 2),
            "pnl": round(realized_pnl, 2),
            "status": "PARTIAL",
            "resolution": (
                f"Salida parcial 50% @ NO={pos['current_no'] * 100:.1f}¢ "
                f"— {int(PARTIAL_EXIT_THRESHOLD * 100)}% ganancia capturada"
            ),
            "entry_time": pos["entry_time"],
            "close_time": now_utc().isoformat(),
        })

    # ── Region exposure ───────────────────────────────────────────────────────

    def get_region_allocated(self, region):
        return sum(
            pos["allocated"]
            for pos in self.positions.values()
            if REGION_MAP.get(pos.get("city", ""), "other") == region
        )

    def region_has_capacity(self, city):
        region = REGION_MAP.get(city, "other")
        allocated = self.get_region_allocated(region)
        return allocated < self.capital_total * MAX_REGION_EXPOSURE

    # ── Learning insights ─────────────────────────────────────────────────────

    def compute_insights(self):
        # Exclude partial exits from win-rate insights — they aren't resolved bets
        closed = [p for p in self.closed_positions if p["status"] != "PARTIAL"]
        if len(closed) < 5:
            return None

        by_hour = defaultdict(lambda: {"won": 0, "total": 0})
        by_city = defaultdict(lambda: {"won": 0, "total": 0})

        for pos in closed:
            try:
                hour = int(pos["entry_time"][11:13])  # fast ISO parse
            except Exception:
                hour = -1
            city = pos.get("city", "unknown")
            won = pos["status"] == "WON"

            if hour >= 0:
                by_hour[hour]["total"] += 1
                if won:
                    by_hour[hour]["won"] += 1

            by_city[city]["total"] += 1
            if won:
                by_city[city]["won"] += 1

        total = len(closed)
        won_total = sum(1 for p in closed if p["status"] == "WON")

        hour_stats = sorted(
            [{"hour": h, "win_rate": round(v["won"] / v["total"], 2), "trades": v["total"]}
             for h, v in by_hour.items() if v["total"] >= 2],
            key=lambda x: x["win_rate"], reverse=True,
        )
        city_stats = sorted(
            [{"city": c, "win_rate": round(v["won"] / v["total"], 2), "trades": v["total"]}
             for c, v in by_city.items() if v["total"] >= 2],
            key=lambda x: x["win_rate"], reverse=True,
        )

        return {
            "overall_win_rate": round(won_total / total, 2),
            "total_trades": total,
            "by_hour": hour_stats[:6],
            "by_city": city_stats[:6],
        }

    # ── Capital snapshot ──────────────────────────────────────────────────────

    def record_capital(self):
        self.capital_history.append({
            "time": now_utc().isoformat(),
            "capital": round(self.capital_total, 2),
        })

    def snapshot(self):
        """Return a dict with the full portfolio state (for JSON API)."""
        pnl = self.capital_total - self.capital_inicial
        roi = (pnl / self.capital_inicial * 100) if self.capital_inicial else 0

        # Exclude PARTIAL entries from won/lost — they're not resolved positions
        won = sum(1 for p in self.closed_positions if p["pnl"] > 0 and p["status"] != "PARTIAL")
        lost = sum(1 for p in self.closed_positions if p["pnl"] <= 0 and p["status"] != "PARTIAL")
        stopped = sum(1 for p in self.closed_positions if p["status"] == "STOPPED")
        partial = sum(1 for p in self.closed_positions if p["status"] == "PARTIAL")

        open_positions = []
        for pos in list(self.positions.values()):
            float_pnl = pos["tokens"] * pos["current_no"] - pos["allocated"]
            open_positions.append({
                "question": pos["question"],
                "city": pos.get("city", ""),
                "entry_no": pos["entry_no"],
                "current_no": pos["current_no"],
                "allocated": round(pos["allocated"], 2),
                "pnl": round(float_pnl, 2),
                "entry_time": pos["entry_time"],
                "status": pos["status"],
                "partial_exited": pos.get("partial_exited", False),
            })

        closed = []
        for pos in self.closed_positions:
            closed.append({
                "question": pos["question"],
                "entry_no": pos["entry_no"],
                "allocated": round(pos["allocated"], 2),
                "pnl": round(pos["pnl"], 2),
                "status": pos["status"],
                "resolution": pos.get("resolution", ""),
                "entry_time": pos["entry_time"],
                "close_time": pos.get("close_time", ""),
            })

        return {
            "capital_inicial": round(self.capital_inicial, 2),
            "capital_total": round(self.capital_total, 2),
            "capital_disponible": round(self.capital_disponible, 2),
            "pnl": round(pnl, 2),
            "roi": round(roi, 2),
            "won": won,
            "lost": lost,
            "stopped": stopped,
            "partial": partial,
            "open_positions": open_positions,
            "closed_positions": closed,
            "capital_history": self.capital_history,
            "session_start": self.session_start.isoformat(),
            "stop_loss_ratio": self.stop_loss_ratio,
            "insights": self.compute_insights(),
        }
