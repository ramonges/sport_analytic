# =============================================================================
# api_client.py
# =============================================================================

import time
import logging
import requests
from config import API_KEY, BASE_URL, SLEEP_BETWEEN_OTHER_CALLS

logger = logging.getLogger(__name__)


class OddsApiClient:
    def __init__(self, api_key: str = API_KEY, base_url: str = BASE_URL):
        self.api_key  = api_key
        self.base_url = base_url
        self.session  = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict = None, high_freq: bool = False) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        p   = dict(params or {})
        p["apiKey"] = self.api_key

        for attempt in range(3):
            resp = self.session.get(url, params=p, timeout=30)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 2))
                logger.warning("429 on %s — waiting %ss", path, retry_after)
                time.sleep(retry_after)
                continue

            # Log body on error before raising
            if not resp.ok:
                logger.error("HTTP %s on %s — body: %s", resp.status_code, url, resp.text[:300])

            resp.raise_for_status()

            if not high_freq:
                time.sleep(SLEEP_BETWEEN_OTHER_CALLS)

            return resp.json()

        raise RuntimeError(f"Failed after 3 attempts: {path}")

    # Metadata
    def get_sports(self)                        -> dict: return self._get("/sports")
    def get_tournaments(self, sport_id: int)    -> dict: return self._get("/tournaments", {"sportId": sport_id})
    def get_seasons(self, tournament_id: int)   -> dict: return self._get("/seasons", {"tournamentId": tournament_id})
    def get_markets(self, sport_id: int)        -> dict: return self._get("/markets", {"sportId": sport_id})
    def get_bookmakers(self)                    -> dict: return self._get("/bookmakers")

    # Fixtures — NOTE: /fixtures does NOT accept bookmakers param (causes 400)
    def get_fixtures(self, tournament_id: int,
                     start_time_from: int = None,
                     start_time_to: int = None) -> dict:
        params = {"tournamentId": tournament_id}
        if start_time_from:
            params["startTimeFrom"] = start_time_from
        if start_time_to:
            params["startTimeTo"] = start_time_to
        return self._get("/fixtures", params)

    # Odds endpoints — bookmakers param IS supported here
    def get_fixture_odds_current(self, fixture_id: str, bookmakers: list = None) -> dict:
        params = {"fixtureId": fixture_id}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        return self._get("/fixtures/odds", params, high_freq=True)

    def get_fixture_odds_historical(self, fixture_id: str, bookmaker: str) -> dict:
        return self._get("/fixtures/odds/historical", {"fixtureId": fixture_id, "bookmaker": bookmaker})

    def get_fixture_odds_clv(self, fixture_id: str, bookmakers: list = None) -> dict:
        params = {"fixtureId": fixture_id}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        return self._get("/fixtures/odds/clv", params)

    def get_fixtures_odds_main(self, tournament_id: int, bookmakers: list = None,
                               since: int = None) -> dict:
        params = {"tournamentId": tournament_id}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        if since:
            params["since"] = since
        return self._get("/fixtures/odds/main", params, high_freq=True)

    def get_fixture_settlement(self, fixture_id: str) -> dict:
        return self._get("/fixtures/settlement", {"fixtureId": fixture_id})