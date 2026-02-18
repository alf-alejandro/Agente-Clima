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

# --- AI Agent ---
GEMINI_API_KEY           = os.environ.get("GEMINI_API_KEY", "")
AI_AGENT_ENABLED         = os.environ.get("AI_AGENT_ENABLED", "true").lower() == "true"
AI_COST_PER_CALL         = float(os.environ.get("AI_COST_PER_CALL", 0.0003))

# --- Kelly position sizing ---
KELLY_FRACTION_MULTIPLIER = float(os.environ.get("KELLY_FRACTION_MULTIPLIER", 0.25))  # quarter-Kelly
KELLY_MAX_FRACTION        = float(os.environ.get("KELLY_MAX_FRACTION", 0.20))         # hard cap 20%

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

# --- Cities ---
WEATHER_CITIES = [
    "chicago", "dallas", "atlanta", "miami", "nyc",
    "seattle", "london", "wellington", "toronto", "seoul",
    "ankara", "paris", "sao-paulo", "buenos-aires",
    "los-angeles", "houston", "phoenix", "denver", "boston",
]
