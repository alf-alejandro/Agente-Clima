import threading
import time
import logging

from app.scanner import scan_opportunities
from app.config import MONITOR_INTERVAL

log = logging.getLogger(__name__)


class BotRunner:
    def __init__(self, portfolio):
        self.portfolio = portfolio
        self._stop_event = threading.Event()
        self._thread = None
        self.scan_count = 0
        self.last_opportunities = []
        self.status = "stopped"

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
            self._stop_event.wait(MONITOR_INTERVAL)
        log.info("Bot stopped")

    def _cycle(self):
        self.scan_count += 1
        portfolio = self.portfolio

        with portfolio.lock:
            existing_ids = set(portfolio.positions.keys())

        opportunities = scan_opportunities(existing_ids)
        self.last_opportunities = [
            {
                "question": o["question"],
                "no_price": o["no_price"],
                "yes_price": o["yes_price"],
                "volume": o["volume"],
                "profit_cents": o["profit_cents"],
            }
            for o in opportunities[:20]
        ]

        with portfolio.lock:
            # Auto-enter best opportunities
            for opp in opportunities:
                if not portfolio.can_open_position():
                    break
                amount = min(
                    portfolio.capital_disponible * 0.05,
                    portfolio.capital_disponible,
                )
                if amount >= 1:
                    portfolio.open_position(opp, amount)
                    log.info("Opened: %s @ %.1f cents", opp["question"][:50], opp["no_price"] * 100)

            # Update existing positions
            if portfolio.positions:
                portfolio.update_positions()

            portfolio.record_capital()
