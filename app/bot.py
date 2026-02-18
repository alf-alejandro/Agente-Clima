import threading
import logging
from datetime import datetime, timezone

from app.scanner import scan_opportunities
from app.config import (
    MONITOR_INTERVAL, GEMINI_API_KEY, AI_AGENT_ENABLED,
    KELLY_FRACTION_MULTIPLIER, KELLY_MAX_FRACTION, AI_COST_PER_CALL,
)

log = logging.getLogger(__name__)


def kelly_amount(capital, no_price, true_prob_no):
    """Quarter-Kelly position size, capped at KELLY_MAX_FRACTION."""
    net_odds = (1.0 - no_price) / no_price
    prob_lose = 1.0 - true_prob_no
    f_full = (net_odds * true_prob_no - prob_lose) / net_odds
    f_scaled = max(0.0, f_full) * KELLY_FRACTION_MULTIPLIER
    return capital * min(f_scaled, KELLY_MAX_FRACTION)


def _is_high_edge_window():
    """True in the 30 min after NWS model updates (0, 6, 12, 18 UTC)."""
    now = datetime.now(timezone.utc)
    return now.hour % 6 == 0 and now.minute < 30


class BotRunner:
    def __init__(self, portfolio):
        self.portfolio = portfolio
        self._stop_event = threading.Event()
        self._thread = None
        self.scan_count = 0
        self.last_opportunities = []
        self.status = "stopped"
        self.ai_agent = None
        self.ai_agent_enabled = AI_AGENT_ENABLED
        self.ai_call_count = 0
        if self.ai_agent_enabled and GEMINI_API_KEY:
            self._init_agent(GEMINI_API_KEY)

    def _init_agent(self, api_key):
        try:
            from app.ai_agent import WeatherAgent
            self.ai_agent = WeatherAgent(api_key)
            log.info("AI agent initialized")
        except Exception:
            log.exception("Failed to init AI agent")
            self.ai_agent = None

    def enable_agent(self, api_key):
        self._init_agent(api_key)
        self.ai_agent_enabled = bool(self.ai_agent)

    def disable_agent(self):
        self.ai_agent_enabled = False

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.status = "running"

    def stop(self):
        self._stop_event.set()
        self.status = "stopped"

    def _run(self):
        log.info("Bot started")
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception:
                log.exception("Error in bot cycle")
            # Shorter interval during high-edge windows
            interval = max(10, MONITOR_INTERVAL // 2) if _is_high_edge_window() else MONITOR_INTERVAL
            self._stop_event.wait(interval)
        log.info("Bot stopped")

    def _cycle(self):
        self.scan_count += 1
        portfolio = self.portfolio

        # 1. Get current + closed IDs to avoid re-entering same markets
        with portfolio.lock:
            existing_ids = set(portfolio.positions.keys())
            closed_ids = {
                p["condition_id"] for p in portfolio.closed_positions
                if p.get("condition_id")
            }
            existing_ids |= closed_ids

        # 2. Scan Polymarket (no lock — external HTTP)
        opportunities = scan_opportunities(existing_ids)

        # 3. AI evaluate top candidates (no lock — external HTTP, slow)
        evaluated = self._evaluate_opportunities(opportunities)

        # 4. Store for dashboard
        self.last_opportunities = [
            {
                "question": o["question"],
                "no_price": o["no_price"],
                "yes_price": o["yes_price"],
                "volume": o["volume"],
                "profit_cents": o["profit_cents"],
                "ai_recommendation": o.get("ai_recommendation", ""),
                "ai_true_prob": o.get("ai_true_prob"),
                "ai_reasoning": o.get("ai_reasoning", ""),
            }
            for o in evaluated[:20]
        ]

        # 5. Portfolio operations (with lock)
        with portfolio.lock:
            # Enter positions
            for opp in evaluated:
                if not portfolio.can_open_position():
                    break

                # Priority 4: geographic limit
                city = opp.get("city", "")
                if not portfolio.region_has_capacity(city):
                    log.info("Region full, skipping %s (%s)", city, opp["question"][:30])
                    continue

                # AI gate
                rec = opp.get("ai_recommendation", "")
                if rec == "SKIP":
                    log.info("AI SKIP: %s", opp["question"][:45])
                    continue

                # Priority 1: Kelly sizing
                amount = self._calc_amount(opp, portfolio.capital_disponible, portfolio.capital_total)
                if amount >= 1:
                    portfolio.open_position(opp, amount)
                    log.info(
                        "Opened [%s]: %s @ %.1f¢  $%.2f",
                        rec or "default", opp["question"][:40],
                        opp["no_price"] * 100, amount,
                    )

            # Update prices + detect resolutions
            if portfolio.positions:
                portfolio.update_positions()

            # Priority 3: partial exits on profitable positions
            portfolio.check_partial_exits()

            portfolio.record_capital()

    def _evaluate_opportunities(self, opportunities):
        """Run AI on top 3 candidates; rest pass through unmodified."""
        evaluated = []
        ai_budget = 3 if (self.ai_agent_enabled and self.ai_agent) else 0

        for opp in opportunities:
            opp_copy = dict(opp)
            if ai_budget > 0:
                self.ai_call_count += 1
                result = self.ai_agent.evaluate(opp)
                if result:
                    opp_copy["ai_recommendation"] = result.get("recommendation", "")
                    opp_copy["ai_true_prob"] = result.get("true_prob_no")
                    opp_copy["ai_reasoning"] = result.get("reasoning", "")
                ai_budget -= 1
            evaluated.append(opp_copy)

        return evaluated

    def _calc_amount(self, opp, capital_disponible, capital_total):
        """Kelly when AI provides probability, else default 5%. Hard cap: 15% of total capital."""
        true_prob = opp.get("ai_true_prob")
        rec = opp.get("ai_recommendation", "")

        if true_prob and rec in ("ENTER", "REDUCE"):
            amount = kelly_amount(capital_disponible, opp["no_price"], true_prob)
            if rec == "REDUCE":
                amount *= 0.5  # half-size on marginal edge
        else:
            amount = capital_disponible * 0.05  # default 5%

        hard_cap = capital_total * KELLY_MAX_FRACTION  # 15% of total capital
        return min(amount, capital_disponible, hard_cap)
