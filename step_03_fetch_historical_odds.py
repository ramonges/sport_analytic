# =============================================================================
# step_03_fetch_historical_odds.py — COMPRESSED VERSION
# Saves only the fields needed for analysis (drops redundant/heavy fields).
# Uses gzip JSON to cut disk usage ~70%.
# =============================================================================

import gzip
import json
import logging
import os
from pathlib import Path

from api_client import OddsApiClient
from config import DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

FIXTURES_FILE  = os.path.join(DATA_DIR, "fixtures.json")
METADATA_FILE  = os.path.join(DATA_DIR, "metadata.json")
HISTORICAL_DIR = os.path.join(DATA_DIR, "historical")
Path(HISTORICAL_DIR).mkdir(parents=True, exist_ok=True)

# Fields to keep per tick — drop priceFractional, priceAmerican, playerId, marketActive
KEEP_FIELDS = {"outcomeId", "marketId", "price", "changedAt", "active", "mainLine", "limit"}

client = OddsApiClient()


def load_json(path):
    with open(path) as f:
        return json.load(f)


def target_market_ids(metadata: dict) -> set:
    mids = set()
    for key in ("asian_handicap", "totals"):
        mids.update(metadata.get("market_ids", {}).get(key, []))
    return mids


def extract_entries(raw: dict, bookmaker: str, target_mids: set) -> list:
    entries = []
    bk_data = raw.get("odds", {}).get(bookmaker, {})
    for odds_id, ts_map in bk_data.items():
        if not isinstance(ts_map, dict):
            continue
        for ts_str, tick in ts_map.items():
            if not isinstance(tick, dict):
                continue
            mid = tick.get("marketId")
            if target_mids and mid not in target_mids:
                continue
            # Keep only essential fields
            entry = {k: tick[k] for k in KEEP_FIELDS if k in tick}
            entries.append(entry)
    return entries


def fixture_file_path(fixture_id: str) -> str:
    return os.path.join(HISTORICAL_DIR, f"{fixture_id}.json.gz")


def already_fetched(fixture_id: str) -> bool:
    # Only trust .json.gz files (compressed, written atomically by this version)
    # Old .json files may be corrupted (truncated by disk-full errors) — ignore them
    p = fixture_file_path(fixture_id)
    if not os.path.exists(p):
        return False
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            data = json.load(f)
        covered = sum(1 for v in data.get("odds", {}).values() if v)
        return covered > 0
    except Exception:
        return False


def save_fixture(fixture_id, fixture, odds):
    payload = {
        "fixture_id": fixture_id,
        "start_time": fixture["start_time"],
        "home_team":  fixture["home_team"],
        "away_team":  fixture["away_team"],
        "odds":       odds,
    }
    with gzip.open(fixture_file_path(fixture_id), "wt", encoding="utf-8") as f:
        json.dump(payload, f)


def main():
    fixtures    = load_json(FIXTURES_FILE)
    metadata    = load_json(METADATA_FILE)
    bookmakers  = metadata.get("confirmed_bookmakers", [])
    target_mids = target_market_ids(metadata)

    # Exclude books with no AH/OU data
    SKIP_BOOKS = {"4casters", "kalshi", "circasports", "bookmaker.eu", "paradisewager"}
    bookmakers = [b for b in bookmakers if b not in SKIP_BOOKS]

    logger.info("Fixtures    : %d", len(fixtures))
    logger.info("Bookmakers  : %d  %s", len(bookmakers), bookmakers)
    logger.info("Est. calls  : %d (~%.0f min)", len(fixtures) * len(bookmakers),
                len(fixtures) * len(bookmakers) / 100)

    done = skip = error = 0

    for i, fixture in enumerate(fixtures):
        fid  = fixture["fixture_id"]
        home = fixture["home_team"]
        away = fixture["away_team"]

        if already_fetched(fid):
            logger.info("[%d/%d] SKIP: %s vs %s", i+1, len(fixtures), home, away)
            skip += 1
            continue

        logger.info("[%d/%d] %s vs %s", i+1, len(fixtures), home, away)
        odds = {}
        for bk in bookmakers:
            try:
                raw     = client.get_fixture_odds_historical(fid, bk)
                entries = extract_entries(raw, bk, target_mids)
                odds[bk] = entries
            except Exception as e:
                logger.warning("  %-15s → error: %s", bk, e)
                odds[bk] = []

        covered = sum(1 for v in odds.values() if v)
        logger.info("  Coverage: %d / %d bookmakers", covered, len(bookmakers))

        try:
            save_fixture(fid, fixture, odds)
            done += 1
        except OSError as e:
            logger.error("  SAVE FAILED %s: %s", fid, e)
            error += 1

    logger.info("Done. fetched=%d  skipped=%d  errors=%d", done, skip, error)


if __name__ == "__main__":
    main()