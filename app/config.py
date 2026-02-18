import os

# --- Strategy parameters ---
MIN_NO_PRICE      = float(os.environ.get("MIN_NO_PRICE", 0.88))
MAX_NO_PRICE      = float(os.environ.get("MAX_NO_PRICE", 0.94))
MAX_YES_PRICE     = float(os.environ.get("MAX_YES_PRICE", 0.12))
MIN_VOLUME        = float(os.environ.get("MIN_VOLUME", 200))
MIN_PROFIT_CENTS  = float(os.environ.get("MIN_PROFIT_CENTS", 5.0))
MONITOR_INTERVAL  = int(os.environ.get("MONITOR_INTERVAL", 30))
MAX_POSITIONS     = int(os.environ.get("MAX_POSITIONS", 20))
MAX_HOURS_TO_CLOSE = int(os.environ.get("MAX_HOURS_TO_CLOSE", 8))

STOP_LOSS_RATIO   = float(os.environ.get("STOP_LOSS_RATIO", 0.8))   # stop = max_gain * ratio
STOP_LOSS_ENABLED = os.environ.get("STOP_LOSS_ENABLED", "true").lower() == "true"

# --- Capital ---
INITIAL_CAPITAL = float(os.environ.get("INITIAL_CAPITAL", 100.0))
AUTO_MODE       = os.environ.get("AUTO_MODE", "true").lower() == "true"
AUTO_START      = os.environ.get("AUTO_START", "false").lower() == "true"

# --- API ---
GAMMA = os.environ.get("GAMMA_API", "https://gamma-api.polymarket.com")

# --- Cities ---
WEATHER_CITIES = [
    "chicago", "dallas", "atlanta", "miami", "new-york-city",
    "seattle", "london", "wellington", "toronto", "seoul",
    "ankara", "paris", "sao-paulo", "buenos-aires",
    "los-angeles", "houston", "phoenix", "denver", "boston",
]
