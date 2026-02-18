import threading
import logging
from datetime import datetime, timezone

from app.scanner import (
    scan_opportunities, fetch_live_prices, fetch_no_price_clob,
    get_prices, fetch_market_live,
)
from app.config import (
    MONITOR_INTERVAL, POSITION_SIZE_MIN, POSITION_SIZE_MAX,
    MIN_NO_PRICE, MAX_NO_PRICE, PRICE_UPDATE_INTERVAL, MAX_POSITIONS,
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
        self.scan_count = 0
        self.last_opportunities = []
        self.status = "stopped"
        self.last_price_update = None   # datetime UTC, updated after each price refresh

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
        self._thread.start()
        self._price_thread.start()
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

        # 3. Verify real-time entry price via CLOB (max 5 to avoid long blocking).
        #    Opportunities that CLOB confirms are out of range are dropped entirely —
        #    they won't be entered and shouldn't appear in the dashboard.
        MAX_CLOB_VERIFY = 5
        with portfolio.lock:
            open_count = len(portfolio.positions)

        slots_available = max(0, MAX_POSITIONS - open_count)
        verify_n = min(len(opportunities), max(slots_available, MAX_CLOB_VERIFY))
        candidates = opportunities[:verify_n]

        verified_opps = []   # will enter these
        display_opps = []    # will show in dashboard (with real-time prices where available)
        clob_entry_ok = True
        clob_entry_fails = 0

        for opp in candidates:
            if self._stop_event.is_set():
                return
            no_tid = opp.get("no_token_id")
            rt_yes, rt_no = None, None
            if clob_entry_ok and no_tid:
                rt_yes, rt_no = fetch_no_price_clob(no_tid)
                if rt_no is not None and rt_no < 0.50:
                    rt_yes, rt_no = None, None
                if rt_no is None:
                    clob_entry_fails += 1
                    if clob_entry_fails >= 2:
                        clob_entry_ok = False

            if rt_no is not None:
                if not (MIN_NO_PRICE <= rt_no <= MAX_NO_PRICE):
                    log.info(
                        "Entry skip %s — CLOB price %.1f¢ out of range",
                        opp["question"][:35], rt_no * 100,
                    )
                    # Price moved out of range — skip entirely (don't display)
                    continue
                opp = {**opp, "no_price": rt_no, "yes_price": rt_yes or round(1 - rt_no, 4)}

            verified_opps.append(opp)
            display_opps.append(opp)

        # Non-candidate opportunities (beyond verify_n) shown with Gamma prices
        display_opps.extend(opportunities[verify_n:verify_n + (20 - len(display_opps))])

        self.last_opportunities = [
            {
                "question": o["question"],
                "no_price": o["no_price"],
                "yes_price": o["yes_price"],
                "volume": o["volume"],
                "profit_cents": o["profit_cents"],
            }
            for o in display_opps[:20]
        ]

        # 4. Fetch current prices for open positions — CLOB first, Gamma fallback
        with portfolio.lock:
            pos_data = [
                (cid, pos.get("no_token_id"), pos.get("slug"))
                for cid, pos in portfolio.positions.items()
            ]

        price_map = {}
        clob_ok_cycle = True
        clob_fail_cycle = 0
        for cid, no_tid, slug in pos_data:
            if self._stop_event.is_set():
                return
            yes_p, no_p = None, None
            if clob_ok_cycle:
                yes_p, no_p = fetch_no_price_clob(no_tid)
                if no_p is not None and no_p < 0.50:
                    log.warning("CLOB sanity fail %.3f for %s — Gamma fallback", no_p, slug[:25])
                    yes_p, no_p = None, None
                if no_p is None:
                    clob_fail_cycle += 1
                    if clob_fail_cycle >= 2:
                        clob_ok_cycle = False
            if no_p is None:
                yes_p, no_p = fetch_live_prices(slug)
            if yes_p is not None and no_p is not None:
                price_map[cid] = (yes_p, no_p)

        # 5. Portfolio operations (with lock — fast, no HTTP inside)
        with portfolio.lock:
            for opp in verified_opps:
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

            if price_map:
                portfolio.apply_price_updates(price_map)

            portfolio.check_partial_exits()
            portfolio.record_capital()

    # ── Price update loop (every PRICE_UPDATE_INTERVAL seconds) ───────────────

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
        clob_ok = True
        clob_failures = 0

        for cid, no_tid, slug in pos_data:
            if self._stop_event.is_set():
                return

            yes_p, no_p = None, None
            source = "Gamma"

            if clob_ok and no_tid:
                yes_p, no_p = fetch_no_price_clob(no_tid)
                if no_p is not None:
                    if no_p < 0.50:
                        log.warning(
                            "CLOB returned %.3f for NO token (likely YES token) — Gamma fallback",
                            no_p,
                        )
                        yes_p, no_p = None, None
                        clob_failures += 1
                    else:
                        source = "CLOB"
                        clob_failures = 0
                else:
                    clob_failures += 1

                if clob_failures >= 2:
                    clob_ok = False
                    log.warning("CLOB unreliable — using Gamma for remaining positions")

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
