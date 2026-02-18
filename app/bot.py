import threading
import logging
from datetime import datetime, timezone

from app.scanner import (
    scan_opportunities, fetch_live_prices, fetch_no_price_clob,
    get_prices, fetch_market_live,
)
from app.config import (
    MONITOR_INTERVAL, GEMINI_API_KEY, AI_AGENT_ENABLED,
    AI_COST_PER_CALL, POSITION_SIZE_MIN, POSITION_SIZE_MAX,
    MIN_NO_PRICE, MAX_NO_PRICE, AI_SCAN_INTERVAL, PRICE_UPDATE_INTERVAL,
)

log = logging.getLogger(__name__)


def calc_position_size(capital_total, capital_disponible, no_price):
    """Linear scale between POSITION_SIZE_MIN and POSITION_SIZE_MAX of capital_total.

    Higher no_price → higher implied probability → larger allocation.
      no_price == MIN_NO_PRICE  →  POSITION_SIZE_MIN  (5 %)
      no_price == MAX_NO_PRICE  →  POSITION_SIZE_MAX  (10 %)
    Result is always capped at capital_disponible.
    """
    price_range = MAX_NO_PRICE - MIN_NO_PRICE
    if price_range <= 0:
        pct = POSITION_SIZE_MIN
    else:
        t = (no_price - MIN_NO_PRICE) / price_range
        t = max(0.0, min(1.0, t))
        pct = POSITION_SIZE_MIN + t * (POSITION_SIZE_MAX - POSITION_SIZE_MIN)
    return min(capital_total * pct, capital_disponible)


class BotRunner:
    def __init__(self, portfolio):
        self.portfolio = portfolio
        self._stop_event = threading.Event()
        self._thread = None
        self._price_thread = None
        self._ai_thread = None
        self.scan_count = 0
        self.last_opportunities = []
        self.status = "stopped"
        self.ai_agent = None
        self.ai_agent_enabled = AI_AGENT_ENABLED
        self.ai_call_count = 0
        # Serialise AI API calls: only 1 in flight at a time
        self._ai_lock = threading.Lock()
        self.last_price_update = None   # datetime UTC, updated after each price refresh
        if self.ai_agent_enabled and GEMINI_API_KEY:
            self._init_agent(GEMINI_API_KEY)

    # ── Agent lifecycle ────────────────────────────────────────────────────────

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

    # ── Thread management ──────────────────────────────────────────────────────

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._price_thread = threading.Thread(target=self._run_prices, daemon=True)
        self._ai_thread = threading.Thread(target=self._run_ai, daemon=True)
        self._thread.start()
        self._price_thread.start()
        self._ai_thread.start()
        self.status = "running"

    def stop(self):
        self._stop_event.set()
        self.status = "stopped"

    # ── Main scan loop (every MONITOR_INTERVAL seconds) ────────────────────────

    def _run(self):
        log.info("Bot scan loop started")
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception:
                log.exception("Error in bot cycle")
            self._stop_event.wait(MONITOR_INTERVAL)
        log.info("Bot scan loop stopped")

    def _cycle(self):
        self.scan_count += 1
        portfolio = self.portfolio

        # Watchdog: restart price thread if it crashed
        if self._price_thread is not None and not self._price_thread.is_alive():
            log.warning("Price updater thread died — restarting")
            self._price_thread = threading.Thread(target=self._run_prices, daemon=True)
            self._price_thread.start()

        # 1. Collect IDs to skip (with lock, fast)
        with portfolio.lock:
            existing_ids = set(portfolio.positions.keys())
            closed_ids = {
                p["condition_id"] for p in portfolio.closed_positions
                if p.get("condition_id")
            }
            existing_ids |= closed_ids

        # 2. Scan Polymarket (no lock — external HTTP)
        opportunities = scan_opportunities(existing_ids)

        # 3. Store for dashboard (no AI at entry)
        self.last_opportunities = [
            {
                "question": o["question"],
                "no_price": o["no_price"],
                "yes_price": o["yes_price"],
                "volume": o["volume"],
                "profit_cents": o["profit_cents"],
                "ai_recommendation": "",
                "ai_true_prob": None,
                "ai_reasoning": "",
            }
            for o in opportunities[:20]
        ]

        # 4. Fetch current prices for open positions — CLOB first, Gamma fallback
        with portfolio.lock:
            pos_data = [
                (cid, pos.get("no_token_id"), pos.get("slug"))
                for cid, pos in portfolio.positions.items()
            ]

        price_map = {}
        for cid, no_tid, slug in pos_data:
            if self._stop_event.is_set():
                return
            yes_p, no_p = fetch_no_price_clob(no_tid)
            if no_p is None:
                yes_p, no_p = fetch_live_prices(slug)
            if yes_p is not None and no_p is not None:
                price_map[cid] = (yes_p, no_p)

        # 5. Portfolio operations (with lock — fast, no HTTP inside)
        with portfolio.lock:
            # Enter new positions (no AI gate)
            for opp in opportunities:
                if not portfolio.can_open_position():
                    break
                city = opp.get("city", "")
                if not portfolio.region_has_capacity(city):
                    log.info("Region full, skipping %s (%s)", city, opp["question"][:30])
                    continue
                amount = calc_position_size(
                    portfolio.capital_total,
                    portfolio.capital_disponible,
                    opp["no_price"],
                )
                if amount >= 1:
                    portfolio.open_position(opp, amount)
                    log.info(
                        "Opened: %s @ %.1f¢  $%.2f",
                        opp["question"][:40], opp["no_price"] * 100, amount,
                    )

            # Apply fetched prices + check resolutions / stop-loss
            if price_map:
                portfolio.apply_price_updates(price_map)

            # Calculation-based partial exit: take 50 % at PARTIAL_EXIT_THRESHOLD
            portfolio.check_partial_exits()

            portfolio.record_capital()

    # ── Price update loop (every PRICE_UPDATE_INTERVAL seconds) ───────────────
    # Keeps current_no fresh so the dashboard shows live P&L between cycles.

    def _run_prices(self):
        log.info("Price updater started")
        while not self._stop_event.is_set():
            self._stop_event.wait(PRICE_UPDATE_INTERVAL)
            if self._stop_event.is_set():
                break
            try:
                self._refresh_prices()
            except Exception:
                log.exception("Error refreshing prices")
        log.info("Price updater stopped")

    def _refresh_prices(self):
        """Fetch current YES/NO prices — CLOB real-time first, Gamma fallback.

        Circuit breaker: if CLOB fails twice in a row, skip it for the rest
        of this cycle so timeouts don't pile up.
        """
        with self.portfolio.lock:
            pos_data = [
                (cid, pos.get("no_token_id"), pos.get("slug"))
                for cid, pos in self.portfolio.positions.items()
            ]

        updated = 0
        clob_ok = True          # flip to False after 2 consecutive CLOB failures
        clob_failures = 0

        for cid, no_tid, slug in pos_data:
            if self._stop_event.is_set():
                return

            yes_p, no_p = None, None
            source = "Gamma"

            if clob_ok and no_tid:
                yes_p, no_p = fetch_no_price_clob(no_tid)
                if no_p is not None:
                    source = "CLOB"
                    clob_failures = 0
                else:
                    clob_failures += 1
                    if clob_failures >= 2:
                        clob_ok = False
                        log.warning("CLOB unavailable — using Gamma for remaining positions")

            if no_p is None:
                yes_p, no_p = fetch_live_prices(slug)

            if no_p is None:
                continue

            with self.portfolio.lock:
                if cid in self.portfolio.positions:
                    old = self.portfolio.positions[cid]["current_no"]
                    self.portfolio.positions[cid]["current_no"] = no_p
                    if abs(no_p - old) >= 0.001:
                        log.info(
                            "Price [%s] %s: %.4f → %.4f",
                            source, slug[:30] if slug else cid[:20], old, no_p,
                        )
                    updated += 1

        if updated > 0 or not pos_data:
            self.last_price_update = datetime.now(timezone.utc)

    # ── AI take-profit loop (every AI_SCAN_INTERVAL seconds) ──────────────────
    # Waits first, then evaluates open positions one-by-one, serialised.

    def _run_ai(self):
        log.info("AI take-profit loop started")
        # First wait before first sweep so the bot has time to enter positions
        self._stop_event.wait(AI_SCAN_INTERVAL)
        while not self._stop_event.is_set():
            try:
                self._ai_cycle()
            except Exception:
                log.exception("Error in AI cycle")
            self._stop_event.wait(AI_SCAN_INTERVAL)
        log.info("AI take-profit loop stopped")

    def _ai_cycle(self):
        """Evaluate each open position with AI; EXIT if forecast turns against us."""
        if not (self.ai_agent_enabled and self.ai_agent):
            return

        with self.portfolio.lock:
            positions_snapshot = [(cid, dict(pos)) for cid, pos in self.portfolio.positions.items()]

        for cid, pos in positions_snapshot:
            if self._stop_event.is_set():
                break
            # Serialised: one AI API call at a time (no parallel requests)
            with self._ai_lock:
                try:
                    self.ai_call_count += 1
                    result = self.ai_agent.evaluate_position(pos)
                    if not result:
                        continue
                    rec = result.get("recommendation", "")
                    log.info(
                        "AI take-profit %s → %s (%s)",
                        pos.get("question", "")[:40],
                        rec,
                        result.get("reasoning", ""),
                    )
                    if rec == "EXIT":
                        with self.portfolio.lock:
                            if cid in self.portfolio.positions:
                                p = self.portfolio.positions[cid]
                                realized_pnl = p["tokens"] * p["current_no"] - p["allocated"]
                                status = "WON" if realized_pnl > 0 else "LOST"
                                reason = (
                                    f"AI EXIT: {result.get('reasoning', '')} "
                                    f"(NO={p['current_no']*100:.1f}¢)"
                                )
                                self.portfolio._close_position(cid, status, realized_pnl, reason)
                                self.portfolio.record_capital()
                except Exception:
                    log.exception("AI take-profit eval failed: %s", pos.get("question", "")[:45])
