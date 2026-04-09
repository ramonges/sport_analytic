# =============================================================================
# config.py
# Central configuration: API credentials, bookmakers, markets, rate limits
# =============================================================================

import os

# --- API ---
API_KEY  = os.environ.get("ODDSPAPI_KEY", "63ef14a7e6c2801c683d0c697cb2a10c")
BASE_URL = "https://v5.oddspapi.io/en"

# --- Bookmakers under analysis ---
BOOKMAKERS = [
    "pinnacle",
    "198bet",
    "3et",
    "4casters",
    "betfair-ex",
    "kaiyun",
    "singbet",
    "vertex",
    "sharpbet",
    "punter.io",
    "kalshi",
    "polymarket",
    "circasports",
    "bookmaker.eu",
    "prophetx",
    "paradisewager",
    "sx.bet",
]

# --- Rate limits (from OddsPapi docs) ---
# /fixtures/odds, /fixtures/odds/main, /futures/odds  → 10 req/sec
# All other endpoints (historical, CLV, fixtures, etc.) → 100 req/min
RATE_LIMIT_ODDS_PER_SEC     = 10     # high-frequency endpoints
RATE_LIMIT_OTHER_PER_MIN    = 100    # all other endpoints
SLEEP_BETWEEN_OTHER_CALLS   = 60 / RATE_LIMIT_OTHER_PER_MIN  # 0.6s

# --- Snapshot windows (minutes before kickoff) ---
SNAPSHOT_MINUTES = [20, 15, 10, 5, 2, 1]

# --- Local storage paths ---
DATA_DIR   = "data"
LOG_DIR    = "logs"
OUTPUT_DIR = "output"

# --- Sport & tournament IDs (populated by step_01_bootstrap.py) ---
# These are filled in once and cached in data/metadata.json
SOCCER_SPORT_ID       = None   # will be discovered
EPL_TOURNAMENT_ID     = None   # will be discovered
EPL_SEASON_ID         = None   # will be discovered

# --- Market slugs of interest ---
# Asian Handicap and Over/Under — actual outcomeIds discovered in step_01
TARGET_MARKET_TYPES = ["asian_handicap", "totals"]