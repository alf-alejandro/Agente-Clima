"""
POLYMARKET AUTO SCANNER
=======================
Versión autónoma del scanner con reinversión automática.

Combina:
- Interface visual del scanner interactivo
- Auto-entrada y reinversión del bot 24/7
- Stop-loss dinámico
- Monitoreo en tiempo real

Uso:
    python polymarket_auto_scanner.py --capital 100 --auto
    
Modos:
    --auto: Reinvierte ganancias y busca nuevas oportunidades automáticamente
    --manual: Pregunta antes de cada entrada (default)
"""

import requests
import json
import sys
import time
import os
from datetime import datetime, timezone
from collections import defaultdict

# ─── CONFIG ──────────────────────────────────────────────────────────────────
MIN_NO_PRICE      = 0.88
MAX_NO_PRICE      = 0.94
MAX_YES_PRICE     = 0.12
MIN_VOLUME        = 200
MIN_PROFIT_CENTS  = 5.0
MONITOR_INTERVAL  = 30
MAX_POSITIONS     = 20
MAX_HOURS_TO_CLOSE = 8

STOP_LOSS_TRIGGER = -0.10
STOP_LOSS_ENABLED = True

GAMMA = "https://gamma-api.polymarket.com"

WEATHER_CITIES = [
    "chicago", "dallas", "atlanta", "miami", "new-york-city",
    "seattle", "london", "wellington", "toronto", "seoul",
    "ankara", "paris", "sao-paulo", "buenos-aires",
    "los-angeles", "houston", "phoenix", "denver", "boston",
]

# ─── COLORES ─────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"
CLEAR  = "\033[2J\033[H"

def c(color, text):
    return f"{color}{text}{RESET}"

def now_utc():
    return datetime.now(timezone.utc)

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

# ─── API Y PARSERS (copiados del scanner original) ──────────────────────────
def parse_price(val):
    try: return float(val)
    except: return None

def parse_date(val):
    if not val: return None
    try: return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except: return None

def get_prices(m):
    raw = m.get("outcomePrices") or "[]"
    try:
        prices = json.loads(raw) if isinstance(raw, str) else raw
        yes = parse_price(prices[0]) if len(prices) > 0 else None
        no  = parse_price(prices[1]) if len(prices) > 1 else None
        if yes is not None and yes < 0: yes = None
        if no  is not None and no  < 0: no  = None
        if yes == 0.0 and no is not None and no >= 0.99: yes = 0.001
        if no == 0.0 and yes is not None and yes >= 0.99: no = 0.001
        return yes, no
    except:
        return None, None

def build_event_slug(city, date):
    months = {1:"january",2:"february",3:"march",4:"april",5:"may",6:"june",
              7:"july",8:"august",9:"september",10:"october",11:"november",12:"december"}
    return f"highest-temperature-in-{city}-on-{months[date.month]}-{date.day}-{date.year}"

def fetch_event_by_slug(slug):
    try:
        r = requests.get(f"{GAMMA}/events", params={"slug": slug, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except: pass
    return None

def fetch_market_live(slug):
    try:
        r = requests.get(f"{GAMMA}/markets", params={"slug": slug, "limit": 1}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
    except: pass
    return None

def scan_opportunities(existing_ids=None):
    """Escanea oportunidades excluyendo IDs ya en posición."""
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
                "question": m.get("question", ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "volume": volume,
                "end_date": end_dt,
                "slug": m.get("slug", ""),
                "profit_cents": round(profit, 1),
            })
    
    opportunities.sort(key=lambda x: x["no_price"], reverse=True)
    return opportunities

# ─── PORTFOLIO MANAGER ───────────────────────────────────────────────────────
class AutoPortfolio:
    def __init__(self, initial_capital):
        self.capital_inicial = initial_capital
        self.capital_total = initial_capital
        self.capital_disponible = initial_capital
        self.positions = {}
        self.closed_positions = []
        self.session_start = now_utc()
    
    def can_open_position(self):
        return (len(self.positions) < MAX_POSITIONS and 
                self.capital_disponible >= 1)
    
    def open_position(self, opp, amount):
        """Abre posición con monto específico."""
        tokens = amount / opp["no_price"]
        max_gain = tokens * 1.0 - amount
        
        stop_price = opp["no_price"] + STOP_LOSS_TRIGGER
        stop_value = tokens * stop_price
        stop_loss = stop_value - amount
        
        pos = {
            **opp,
            "entry_time": now_utc(),
            "entry_no": opp["no_price"],
            "current_no": opp["no_price"],
            "allocated": amount,
            "tokens": tokens,
            "max_gain": max_gain,
            "stop_loss": stop_loss,
            "status": "OPEN",
            "pnl": 0.0,
        }
        
        self.positions[opp["condition_id"]] = pos
        self.capital_disponible -= amount
        return True
    
    def update_positions(self):
        """Actualiza precios y detecta cierres."""
        to_close = []
        
        for cid, pos in self.positions.items():
            m = fetch_market_live(pos["slug"])
            if not m:
                continue
            
            yes_price, no_price = get_prices(m)
            if yes_price is None or no_price is None:
                continue
            
            pos["current_no"] = no_price
            
            # Resolución
            if yes_price >= 0.99:
                to_close.append((cid, "LOST", -pos["allocated"]))
            elif no_price >= 0.99:
                to_close.append((cid, "WON", pos["max_gain"]))
            elif STOP_LOSS_ENABLED:
                drop = no_price - pos["entry_no"]
                if drop <= STOP_LOSS_TRIGGER:
                    sale_value = pos["tokens"] * no_price
                    realized_loss = sale_value - pos["allocated"]
                    to_close.append((cid, "STOPPED", realized_loss))
        
        for cid, status, pnl in to_close:
            self.close_position(cid, status, pnl)
    
    def close_position(self, cid, status, pnl):
        """Cierra posición y actualiza capital."""
        if cid not in self.positions:
            return
        
        pos = self.positions[cid]
        pos["status"] = status
        pos["pnl"] = pnl
        pos["close_time"] = now_utc()
        
        recovered = pos["allocated"] + pnl
        self.capital_disponible += recovered
        self.capital_total += pnl
        
        self.closed_positions.append(pos.copy())
        del self.positions[cid]

# ─── DISPLAY ─────────────────────────────────────────────────────────────────
def print_status(portfolio, scan_count):
    """Imprime status compacto."""
    elapsed = (now_utc() - portfolio.session_start).total_seconds() / 60
    pnl = portfolio.capital_total - portfolio.capital_inicial
    roi = (pnl / portfolio.capital_inicial) * 100
    
    won = sum(1 for p in portfolio.closed_positions if p["pnl"] > 0)
    lost = sum(1 for p in portfolio.closed_positions if p["pnl"] < 0)
    stopped = sum(1 for p in portfolio.closed_positions if p["status"] == "STOPPED")
    
    print(c(BOLD, "\n" + "─"*70))
    print(c(BOLD, f"  AUTO SCANNER  |  Sesión: {int(elapsed)}min  |  Escaneos: {scan_count}"))
    print(c(BOLD, "─"*70))
    pnl_sign = '+' if pnl >= 0 else ''
    roi_sign = '+' if roi >= 0 else ''
    pnl_str = f"{pnl_sign}${pnl:.2f}"
    roi_str = f"{roi_sign}{roi:.1f}%"
    print(f"  Capital: ${portfolio.capital_total:.2f}  |  Disponible: ${portfolio.capital_disponible:.2f}  |  P&L: {c(GREEN if pnl >= 0 else RED, pnl_str)} ({roi_str})")
    print(f"  Abiertas: {len(portfolio.positions)}/{MAX_POSITIONS}  |  Cerradas: {won}G {lost}P {stopped}SL")
    
    if portfolio.positions:
        print(c(DIM, "\n  Posiciones activas:"))
        for pos in list(portfolio.positions.values())[:5]:
            cur = pos["current_no"]
            float_pnl = pos["tokens"] * cur - pos["allocated"]
            pnl_sign = '+' if float_pnl >= 0 else ''
            print(f"    • {pos['question'][:45]}  NO:{cur*100:.1f}¢  P&L:{pnl_sign}${float_pnl:.2f}")
        if len(portfolio.positions) > 5:
            print(c(DIM, f"    ... y {len(portfolio.positions) - 5} más"))
    
    print(c(BOLD, "─"*70))

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    auto_mode = "--auto" in sys.argv
    capital = 100.0
    
    for i, arg in enumerate(sys.argv):
        if arg == "--capital" and i+1 < len(sys.argv):
            capital = float(sys.argv[i+1])
    
    clear_screen()
    print(c(BOLD, "\n" + "═"*70))
    print(c(BOLD, "  POLYMARKET AUTO SCANNER"))
    print(c(BOLD, "═"*70))
    print(f"  Modo: {c(GREEN, 'AUTO-REINVERSIÓN') if auto_mode else c(YELLOW, 'MANUAL')}")
    print(f"  Capital inicial: ${capital:.2f}")
    print(f"  Estrategia: NO {MIN_NO_PRICE*100:.0f}-{MAX_NO_PRICE*100:.0f}¢  |  Stop-loss: {abs(STOP_LOSS_TRIGGER)*100:.0f}¢")
    print(c(BOLD, "═"*70))
    
    portfolio = AutoPortfolio(capital)
    scan_count = 0
    
    try:
        while True:
            scan_count += 1
            
            # 1. ESCANEAR
            print(f"\n{c(CYAN, f'[Escaneo #{scan_count}]')} Buscando oportunidades...")
            existing_ids = set(portfolio.positions.keys())
            opportunities = scan_opportunities(existing_ids)
            print(f"  Encontradas: {len(opportunities)} nuevas oportunidades")
            
            # 2. MOSTRAR OPORTUNIDADES
            if opportunities:
                print(c(DIM, "\n  Top oportunidades:"))
                for i, opp in enumerate(opportunities[:10], 1):
                    print(f"    {i:2}. NO@{opp['no_price']*100:.1f}¢  Vol${opp['volume']:>8,.0f}  {opp['question'][:50]}")
            
            # 3. DECIDIR ENTRADA
            if opportunities and portfolio.can_open_position():
                if auto_mode:
                    # Auto: entrar en las mejores hasta llenar capacidad
                    for opp in opportunities:
                        if not portfolio.can_open_position():
                            break
                        
                        amount = min(
                            portfolio.capital_disponible * 0.05,  # 5% del disponible
                            portfolio.capital_disponible
                        )
                        
                        if amount >= 1:
                            portfolio.open_position(opp, amount)
                            print(c(GREEN, f"  [OK] Entrada: {opp['question'][:45]}  ${amount:.2f} @ {opp['no_price']*100:.1f}¢"))
                else:
                    # Manual: preguntar
                    cont = input(f"\n  ¿Entrar en {len(opportunities)} oportunidades? [s/n]: ").strip().lower()
                    if cont in ("s", "si", "sí", "y", "yes"):
                        for opp in opportunities:
                            if not portfolio.can_open_position():
                                break
                            amount = min(
                                portfolio.capital_disponible * 0.05,
                                portfolio.capital_disponible
                            )
                            if amount >= 1:
                                portfolio.open_position(opp, amount)
            
            # 4. ACTUALIZAR POSICIONES
            if portfolio.positions:
                portfolio.update_positions()
            
            # 5. MOSTRAR STATUS
            print_status(portfolio, scan_count)
            
            # 6. ESPERAR
            if auto_mode:
                print(f"\n  Próximo escaneo en {MONITOR_INTERVAL}s...")
                time.sleep(MONITOR_INTERVAL)
            else:
                cont = input(f"\n  [Enter] = continuar  |  [q] = salir: ").strip().lower()
                if cont == 'q':
                    break
                time.sleep(5)  # Pausa breve
            
    except KeyboardInterrupt:
        print(f"\n\n{c(YELLOW, '  Sesión detenida.')}\n")
    
    # REPORTE FINAL
    print(c(BOLD, "\n" + "═"*70))
    print(c(BOLD, "  REPORTE FINAL"))
    print(c(BOLD, "═"*70))
    pnl_total = portfolio.capital_total - portfolio.capital_inicial
    roi = (pnl_total / portfolio.capital_inicial) * 100
    print(f"  Capital inicial: ${portfolio.capital_inicial:.2f}")
    print(f"  Capital final: ${portfolio.capital_total:.2f}")
    pnl_sign = '+' if pnl_total >= 0 else ''
    roi_sign = '+' if roi >= 0 else ''
    pnl_str = f"{pnl_sign}${pnl_total:.2f}"
    roi_str = f"({roi_sign}{roi:.1f}%)"
    print(f"  P&L neto: {c(GREEN if pnl_total >= 0 else RED, pnl_str + '  ' + roi_str)}")
    print(f"  Trades: {len(portfolio.closed_positions)}")
    print(c(BOLD, "═"*70 + "\n"))

if __name__ == "__main__":
    main()