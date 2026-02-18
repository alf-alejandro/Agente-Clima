import threading
from app.scanner import now_utc, fetch_market_live, get_prices
from app.config import MAX_POSITIONS, STOP_LOSS_RATIO, STOP_LOSS_ENABLED


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

    def can_open_position(self):
        return (len(self.positions) < MAX_POSITIONS and
                self.capital_disponible >= 1)

    def open_position(self, opp, amount):
        tokens = amount / opp["no_price"]
        max_gain = tokens * 1.0 - amount

        # Dynamic stop: risk at most STOP_LOSS_RATIO * max_gain (R:R stays constant)
        dynamic_trigger = -(1.0 - opp["no_price"]) * STOP_LOSS_RATIO
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

    def update_positions(self):
        to_close = []

        for cid, pos in list(self.positions.items()):
            m = fetch_market_live(pos["slug"])
            if not m:
                continue

            yes_price, no_price = get_prices(m)
            if yes_price is None or no_price is None:
                continue

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
                    resolution = f"Stop loss @ NO={no_price*100:.1f}¢ (entrada {pos['entry_no']*100:.1f}¢, caída {drop*100:.1f}¢)"
                    to_close.append((cid, "STOPPED", realized_loss, resolution))

        for cid, status, pnl, resolution in to_close:
            self._close_position(cid, status, pnl, resolution)

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

    def record_capital(self):
        self.capital_history.append({
            "time": now_utc().isoformat(),
            "capital": round(self.capital_total, 2),
        })

    def snapshot(self):
        """Return a dict with the full portfolio state (for JSON API)."""
        pnl = self.capital_total - self.capital_inicial
        roi = (pnl / self.capital_inicial * 100) if self.capital_inicial else 0

        won = sum(1 for p in self.closed_positions if p["pnl"] > 0)
        lost = sum(1 for p in self.closed_positions if p["pnl"] < 0)
        stopped = sum(1 for p in self.closed_positions if p["status"] == "STOPPED")

        open_positions = []
        for pos in list(self.positions.values()):
            float_pnl = pos["tokens"] * pos["current_no"] - pos["allocated"]
            open_positions.append({
                "question": pos["question"],
                "entry_no": pos["entry_no"],
                "current_no": pos["current_no"],
                "allocated": round(pos["allocated"], 2),
                "pnl": round(float_pnl, 2),
                "entry_time": pos["entry_time"],
                "status": pos["status"],
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
            "open_positions": open_positions,
            "closed_positions": closed,
            "capital_history": self.capital_history,
            "session_start": self.session_start.isoformat(),
        }
