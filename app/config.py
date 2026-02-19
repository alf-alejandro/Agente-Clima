import os

# --- Strategy parameters ---
MIN_NO_PRICE      = float(os.environ.get("MIN_NO_PRICE", 0.88))
MAX_NO_PRICE      = float(os.environ.get("MAX_NO_PRICE", 0.94))
MAX_YES_PRICE     = float(os.environ.get("MAX_YES_PRICE", 0.12))
MIN_VOLUME        = float(os.environ.get("MIN_VOLUME", 200))
MIN_PROFIT_CENTS  = float(os.environ.get("MIN_PROFIT_CENTS", 5.0))
MONITOR_INTERVAL  = int(os.environ.get("MONITOR_INTERVAL", 30))
SCAN_DAYS_AHEAD   = int(os.environ.get("SCAN_DAYS_AHEAD", 1))   # 0 = solo hoy, 1 = hoy + mañana
MIN_LOCAL_HOUR    = int(os.environ.get("MIN_LOCAL_HOUR", 12))   # hora mínima local para mercados futuros
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS", 20))
MAX_HOURS_TO_CLOSE = int(os.environ.get("MAX_HOURS_TO_CLOSE", 8))

STOP_LOSS_RATIO   = float(os.environ.get("STOP_LOSS_RATIO", 0.8))   # stop = max_gain * ratio
STOP_LOSS_ENABLED = os.environ.get("STOP_LOSS_ENABLED", "true").lower() == "true"

# --- Position sizing (5 % – 10 % of total capital, scaled by NO price) ---
POSITION_SIZE_MIN = float(os.environ.get("POSITION_SIZE_MIN", 0.05))  # 5 %
POSITION_SIZE_MAX = float(os.environ.get("POSITION_SIZE_MAX", 0.10))  # 10 %

# --- Price update thread ---
PRICE_UPDATE_INTERVAL = int(os.environ.get("PRICE_UPDATE_INTERVAL", 10))   # seconds

# --- Geographic correlation limits ---
MAX_REGION_EXPOSURE = float(os.environ.get("MAX_REGION_EXPOSURE", 0.25))  # max 25% per region

REGION_MAP = {
    "chicago": "midwest",       "denver": "midwest",
    "dallas": "south",          "houston": "south",
    "atlanta": "south",         "miami": "south",         "phoenix": "south",
    "boston": "northeast",      "nyc": "northeast",
    "seattle": "pacific",       "los-angeles": "pacific",
    "london": "europe",         "paris": "europe",        "ankara": "europe",
    "wellington": "southern",   "buenos-aires": "southern", "sao-paulo": "southern",
    "seoul": "asia",            "toronto": "north_america",
}

# --- Partial exit ---
PARTIAL_EXIT_THRESHOLD = float(os.environ.get("PARTIAL_EXIT_THRESHOLD", 0.70))  # exit 50% at 70% profit captured

# --- Capital ---
INITIAL_CAPITAL = float(os.environ.get("INITIAL_CAPITAL", 100.0))
AUTO_MODE       = os.environ.get("AUTO_MODE", "true").lower() == "true"
AUTO_START      = os.environ.get("AUTO_START", "false").lower() == "true"

# --- API ---
GAMMA = os.environ.get("GAMMA_API", "https://gamma-api.polymarket.com")

# --- City UTC offsets — for next-day market maturity filter ---
# Hardcoded for reliability (no system tzdata dependency).
# NOTE: update US/EU offsets when DST changes (Mar/Nov).
# February 2026: Northern Hemisphere = standard time; Wellington = NZDT (UTC+13).
CITY_UTC_OFFSET = {
    "chicago":      -6,
    "dallas":       -6,
    "atlanta":      -5,
    "miami":        -5,
    "nyc":          -5,
    "boston":       -5,
    "toronto":      -5,
    "seattle":      -8,
    "los-angeles":  -8,
    "houston":      -6,
    "phoenix":      -7,
    "denver":       -7,
    "london":        0,
    "paris":         1,
    "ankara":        3,
    "seoul":         9,
    "wellington":   13,
    "sao-paulo":    -3,
    "buenos-aires": -3,
}

# --- Cities ---
WEATHER_CITIES = [
    "chicago", "dallas", "atlanta", "miami", "nyc",
    "seattle", "london", "wellington", "toronto", "seoul",
    "ankara", "paris", "sao-paulo", "buenos-aires",
    "los-angeles", "houston", "phoenix", "denver", "boston",
]
