import os

API_KEY  = os.environ.get("ODDSPAPI_KEY", "63ef14a7e6c2801c683d0c697cb2a10c")
BASE_URL = "https://v5.oddspapi.io/en"

BOOKMAKERS = [
    "pinnacle",
    "198bet",
    "3et",
    "betfair-ex",
    "kaiyun",
    "singbet",
    "vertex",
    "sharpbet",
    "punter.io",
    "polymarket",
    "prophetx",
    "sx.bet",
]

NO_LIMIT_BOOKS = {"198bet", "kaiyun", "singbet"}

EXCHANGE_COMMISSION = {
    "betfair-ex": 0.02,
    "sx.bet":     0.02,
    "polymarket": 0.02,
}

RATE_LIMIT_ODDS_PER_SEC     = 10     
RATE_LIMIT_OTHER_PER_MIN    = 100   
SLEEP_BETWEEN_OTHER_CALLS   = 60 / RATE_LIMIT_OTHER_PER_MIN  

# It is minutes before kickoff
SNAPSHOT_MINUTES = [
    1800, 
    1440,  
    720,  
    300,  
    60,    
    30,    
    20,
    10,
    5,
    1,
]

MAX_STALENESS_MINUTES = 240

DATA_DIR   = "data"
LOG_DIR    = "logs"
OUTPUT_DIR = "output"

SOCCER_SPORT_ID       = None  
EPL_TOURNAMENT_ID     = None   
EPL_SEASON_ID         = None   

# Asian Handicap and Over/Under. It is actual outcome Ids discovered in step 1
TARGET_MARKET_TYPES = ["asian_handicap", "totals"]