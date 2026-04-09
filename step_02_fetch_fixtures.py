# =============================================================================
# step_02_fetch_fixtures.py
# Fetch PAST EPL fixtures using a date range (startTimeFrom / startTimeTo).
# The API only returns upcoming games by default — time filters give us history.
#
# FIXTURE_LIMIT = 10  <- change to None for full season
# =============================================================================

FIXTURE_LIMIT = None

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from api_client import OddsApiClient
from config import DATA_DIR, BOOKMAKERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

Path(DATA_DIR).mkdir(exist_ok=True)
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")
FIXTURES_FILE = os.path.join(DATA_DIR, "fixtures.json")

client = OddsApiClient()


def load_metadata() -> dict:
    if not os.path.exists(METADATA_FILE):
        raise FileNotFoundError("Run step_01_bootstrap.py first.")
    with open(METADATA_FILE) as f:
        return json.load(f)


def to_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("fixtures", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
        list_values = [v for v in data.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return list_values[0]
    return []


def parse_fixtures(raw) -> list:
    fixtures_raw = to_list(raw)
    if not fixtures_raw:
        logger.warning("No fixtures in response. Raw sample: %s", str(raw)[:300])
        return []
    result = []
    for f in fixtures_raw:
        p = f.get("participants", {})
        result.append({
            "fixture_id":    f.get("fixtureId"),
            "start_time":    f.get("startTime"),
            "home_team":     p.get("participant1Name"),
            "away_team":     p.get("participant2Name"),
            "home_id":       p.get("participant1Id"),
            "away_id":       p.get("participant2Id"),
            "tournament_id": f.get("tournament", {}).get("tournamentId"),
            "season_id":     f.get("season", {}).get("seasonId"),
            "status":        f.get("status", {}).get("statusName"),
        })
    result = [f for f in result if f["fixture_id"] and f["start_time"]]
    result.sort(key=lambda x: x["start_time"])
    return result


def fetch_past_fixtures(tournament_id: int, weeks_back: int = 36) -> list:
    """
    Fetch past EPL fixtures by querying backward in time using startTimeFrom/startTimeTo.
    Queries week by week to stay within API pagination limits.
    """
    now       = int(time.time())
    week_sec  = 7 * 24 * 3600
    all_fixtures = []
    seen_ids     = set()

    logger.info("Fetching past fixtures for tournamentId=%s (last %d weeks)...",
                tournament_id, weeks_back)

    for w in range(weeks_back):
        # Window: [now - (w+1)*week, now - w*week]
        t_to   = now - w * week_sec
        t_from = now - (w + 1) * week_sec

        from_dt = datetime.utcfromtimestamp(t_from).strftime("%Y-%m-%d")
        to_dt   = datetime.utcfromtimestamp(t_to).strftime("%Y-%m-%d")
        logger.info("  Week -%d : %s → %s", w + 1, from_dt, to_dt)

        try:
            raw      = client.get_fixtures(tournament_id,
                                           start_time_from=t_from,
                                           start_time_to=t_to)
            fixtures = parse_fixtures(raw)
            new      = [f for f in fixtures if f["fixture_id"] not in seen_ids]
            seen_ids.update(f["fixture_id"] for f in new)
            all_fixtures.extend(new)
            logger.info("    → %d fixtures this week (%d total so far)", len(new), len(all_fixtures))

            # Stop early if we already have enough
            if FIXTURE_LIMIT and len(all_fixtures) >= FIXTURE_LIMIT:
                break
        except Exception as e:
            logger.warning("    → Error fetching week -%d: %s", w + 1, e)

    # Sort by most recent first
    all_fixtures.sort(key=lambda x: x["start_time"], reverse=True)
    return all_fixtures


def main():
    meta          = load_metadata()
    tournament_id = meta["tournament_id"]

    fixtures = fetch_past_fixtures(tournament_id, weeks_back=36)

    if not fixtures:
        logger.error("No past fixtures found at all.")
        logger.error("The API may require a different endpoint or parameter for historical data.")
        logger.error("Check: does your API subscription include historical fixture access?")
        return

    logger.info("Total past fixtures found: %d", len(fixtures))

    # Apply limit
    if FIXTURE_LIMIT is not None:
        fixtures = fixtures[:FIXTURE_LIMIT]
        logger.info("Limited to %d fixtures", FIXTURE_LIMIT)

    with open(FIXTURES_FILE, "w") as f:
        json.dump(fixtures, f, indent=2)

    logger.info("Saved %d fixtures to %s", len(fixtures), FIXTURES_FILE)
    logger.info("Games selected:")
    for fix in fixtures:
        dt = datetime.utcfromtimestamp(fix["start_time"]).strftime("%Y-%m-%d %H:%M")
        logger.info(
            "  %s  %-25s vs %-25s  [%s]",
            dt, fix["home_team"], fix["away_team"], fix["status"],
        )


if __name__ == "__main__":
    main()