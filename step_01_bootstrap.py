# =============================================================================
# step_01_bootstrap.py
# =============================================================================

import json
import logging
import os
from pathlib import Path

from api_client import OddsApiClient
from config import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

Path(DATA_DIR).mkdir(exist_ok=True)
METADATA_FILE = os.path.join(DATA_DIR, "metadata.json")

client = OddsApiClient()


def to_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("sports", "tournaments", "seasons", "markets", "bookmakers", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
        list_values = [v for v in data.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return list_values[0]
    return []


def find_sport(name_fragment: str = "soccer") -> dict:
    logger.info("Fetching sports list...")
    raw    = client.get_sports()
    sports = to_list(raw)
    logger.info("All sports available:")
    for s in sports:
        logger.info("  id=%-4s  name=%s", s.get("sportId"), s.get("sportName"))
    for s in sports:
        if name_fragment.lower() in s.get("sportName", "").lower():
            logger.info("-> Using sport: %s  id=%s", s["sportName"], s["sportId"])
            return s
    raise ValueError(f"Sport '{name_fragment}' not found.")


def find_tournament(sport_id: int, name_fragment: str = "premier league") -> dict:
    logger.info("Fetching tournaments for sportId=%s...", sport_id)
    raw         = client.get_tournaments(sport_id)
    tournaments = to_list(raw)
    logger.info("All tournaments for sportId=%s (%d total):", sport_id, len(tournaments))
    for t in sorted(tournaments, key=lambda x: x.get("tournamentName", "")):
        logger.info(
            "  id=%-6s  category=%-20s  name=%s",
            t.get("tournamentId"), t.get("categoryName", ""), t.get("tournamentName"),
        )
    for t in tournaments:
        if name_fragment.lower() in t.get("tournamentName", "").lower():
            logger.info("-> Using tournament: %s  id=%s", t["tournamentName"], t["tournamentId"])
            return t
    raise ValueError(
        f"Tournament '{name_fragment}' not found. "
        f"Check the list above and update the name_fragment in main()."
    )


def find_latest_season(tournament_id: int) -> dict:
    logger.info("Fetching seasons for tournamentId=%s...", tournament_id)
    raw     = client.get_seasons(tournament_id)
    seasons = to_list(raw)
    logger.info("Seasons available:")
    for s in seasons:
        logger.info("  id=%-6s  name=%s", s.get("seasonId"), s.get("seasonName"))
    if not seasons:
        raise ValueError(f"No seasons found for tournament {tournament_id}.")
    season = seasons[0]
    logger.info("-> Using season: %s  id=%s", season.get("seasonName"), season.get("seasonId"))
    return season


def discover_ah_totals_market_ids(sport_id: int) -> dict:
    logger.info("Fetching markets for sportId=%s...", sport_id)
    raw     = client.get_markets(sport_id)
    markets = to_list(raw)
    ah_ids, totals_ids = [], []
    for m in markets:
        name = m.get("marketName", "").lower()
        mid  = m.get("marketId")
        if "asian" in name and "handicap" in name:
            ah_ids.append(mid)
        elif "total" in name or ("over" in name and "under" in name):
            totals_ids.append(mid)
    logger.info("Asian Handicap marketIds : %s", ah_ids[:10])
    logger.info("Totals marketIds         : %s", totals_ids[:10])
    return {"asian_handicap": ah_ids, "totals": totals_ids}


def verify_bookmakers() -> list:
    logger.info("Fetching bookmaker catalog...")
    raw       = client.get_bookmakers()
    available = to_list(raw)
    available_slugs = set()
    for b in available:
        slug = b.get("slug") or b.get("bookmaker") or b.get("name")
        if slug:
            available_slugs.add(slug)
    from config import BOOKMAKERS
    confirmed = [b for b in BOOKMAKERS if b in available_slugs]
    missing   = [b for b in BOOKMAKERS if b not in available_slugs]
    if missing:
        logger.warning("Slugs NOT in catalog: %s", missing)
    logger.info("Confirmed bookmakers (%d/%d)", len(confirmed), len(BOOKMAKERS))
    return confirmed


def main():
    metadata = {}

    sport                = find_sport("soccer")
    metadata["sport"]    = sport
    metadata["sport_id"] = sport["sportId"]

    # -----------------------------------------------------------------------
    # If "premier league" is not found above, change the string below to match
    # exactly what appeared in the tournament list (e.g. "english premier")
    # -----------------------------------------------------------------------
    tournament                = find_tournament(sport["sportId"], "premier league")
    metadata["tournament"]    = tournament
    metadata["tournament_id"] = tournament["tournamentId"]

    season                = find_latest_season(tournament["tournamentId"])
    metadata["season"]    = season
    metadata["season_id"] = season["seasonId"]

    market_ids             = discover_ah_totals_market_ids(sport["sportId"])
    metadata["market_ids"] = market_ids

    confirmed                        = verify_bookmakers()
    metadata["confirmed_bookmakers"] = confirmed

    with open(METADATA_FILE, "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info("─" * 50)
    logger.info("  sportId      = %s", metadata["sport_id"])
    logger.info("  tournamentId = %s", metadata["tournament_id"])
    logger.info("  seasonId     = %s", metadata["season_id"])
    logger.info("Metadata saved to %s", METADATA_FILE)


if __name__ == "__main__":
    main()