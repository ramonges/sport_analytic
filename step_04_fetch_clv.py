# step_04_fetch_clv.py
# This gives opening vs closing line values pre-computed by OddsPapi.

import json
import logging
import os
from pathlib import Path
import time  

from api_client import OddsApiClient
from config import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

FIXTURES_FILE = os.path.join(DATA_DIR, "fixtures.json")
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
CLV_DIR       = os.path.join(DATA_DIR, "clv")
Path(CLV_DIR).mkdir(parents=True, exist_ok=True)

client = OddsApiClient()


def clv_file_path(fixture_id: str) -> str:
    return os.path.join(CLV_DIR, f"{fixture_id}.json")


def already_fetched(fixture_id: str) -> bool:
    return os.path.exists(clv_file_path(fixture_id))


def main():
    if not os.path.exists(FIXTURES_FILE):
        raise FileNotFoundError("Run step_02_fetch_fixtures.py first.")

    with open(FIXTURES_FILE) as f:
        fixtures = json.load(f)
    with open(METADATA_FILE) as f:
        metadata = json.load(f)

    bookmakers = metadata.get("confirmed_bookmakers", [])

    logger.info("Fetching CLV for %d fixtures × %d bookmakers", len(fixtures), len(bookmakers))

    done = skip = error = 0

    for i, fixture in enumerate(fixtures):
        fid = fixture["fixture_id"]

        if already_fetched(fid):
            skip += 1
            continue

        logger.info(
            "[%d/%d] CLV: %s vs %s",
            i + 1, len(fixtures), fixture["home_team"], fixture["away_team"],
        )

        try:
            raw = client.get_fixture_odds_clv(fid, bookmakers=bookmakers)
            payload = {
                "fixture_id": fid,
                "start_time": fixture["start_time"],
                "home_team":  fixture["home_team"],
                "away_team":  fixture["away_team"],
                "clv_data":   raw,
            }
            with open(clv_file_path(fid), "w") as f:
                json.dump(payload, f)
            done += 1
        except Exception as e:
            logger.error("CLV failed for %s: %s", fid, e)
            error += 1
        time.sleep(0.6)

    logger.info("CLV done. Fetched=%d  Skipped=%d  Errors=%d", done, skip, error)


if __name__ == "__main__":
    main()