# step_05_build_snapshots.py

import gzip
import json
import logging
import os
from pathlib import Path

import pandas as pd

from config import DATA_DIR, SNAPSHOT_MINUTES, EXCHANGE_COMMISSION, MAX_STALENESS_MINUTES


logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

HISTORICAL_DIR   = os.path.join(DATA_DIR, "historical")
OUTPUT_SNAPSHOTS = os.path.join(DATA_DIR, "snapshots.csv")


def load_historical(fp: Path) -> dict:
    s = str(fp)
    if s.endswith(".gz"):
        with gzip.open(s, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(s) as f:
        return json.load(f)

def net_exchange_price(raw_price: float, bookmaker: str) -> float:
    c = EXCHANGE_COMMISSION.get(bookmaker.lower())
    if c is None:
        return raw_price
    if not raw_price or raw_price <= 1.0:
        return raw_price
    return 1.0 + (raw_price - 1.0) * (1.0 - c)


def find_historical_files() -> list:
    gz_files   = {f.stem.replace(".json", ""): f for f in Path(HISTORICAL_DIR).glob("*.json.gz")}
    json_files = {f.stem: f                      for f in Path(HISTORICAL_DIR).glob("*.json")}
    merged = {**json_files, **gz_files}   # gz wins on collision - Compressed files for disk space
    return sorted(merged.values())


def get_price_at_snapshot(entries: list, kickoff_ms: int, minutes_before: int):
    target_ms    = kickoff_ms - (minutes_before * 60 * 1000)
    stale_cutoff = target_ms - (MAX_STALENESS_MINUTES * 60 * 1000)
    candidates   = [
        e for e in entries
        if stale_cutoff <= e.get("changedAt", 0) <= target_ms
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x["changedAt"])

def build_snapshots_for_fixture(payload: dict) -> list:
    fixture_id = payload["fixture_id"]
    start_time = payload["start_time"]  
    home_team  = payload["home_team"]
    away_team  = payload["away_team"]
    kickoff_ms = start_time * 1000  
    odds_by_bk = payload.get("odds", {})

    rows = []
    for bookmaker, entries in odds_by_bk.items():
        if not entries:
            continue

        # Group by (marketId, outcomeId)
        grouped: dict = {}
        for e in entries:
            key = (e.get("marketId"), e.get("outcomeId"))
            grouped.setdefault(key, []).append(e)

        for (market_id, outcome_id), group_entries in grouped.items():
            group_entries.sort(key=lambda x: x.get("changedAt", 0))

            for mins in SNAPSHOT_MINUTES:
                snap = get_price_at_snapshot(group_entries, kickoff_ms, mins)
                if snap is None:
                    continue
                rows.append({
                    "fixture_id":    fixture_id,
                    "home_team":     home_team,
                    "away_team":     away_team,
                    "kickoff_epoch_s": start_time,
                    "bookmaker":     bookmaker,
                    "market_id":     market_id,
                    "outcome_id":    outcome_id,
                    "snapshot_mins": mins,
                    "snapshot_ts_ms": kickoff_ms - mins * 60 * 1000,
                    "price":         net_exchange_price(snap.get("price"), bookmaker),
                    "changed_at_ms": snap.get("changedAt"),
                    "active":        snap.get("active", True),
                    "main_line":     snap.get("mainLine", False),
                    "limit":         snap.get("limit"),       # Limit refers to liquidit or volume here
                })
    return rows


def main():
    hist_files = find_historical_files()
    logger.info("Found %d historical fixture files", len(hist_files))

    if not hist_files:
        logger.error("No files found in %s — make sure step_03 completed.", HISTORICAL_DIR)
        # Show what IS in the directory
        all_items = list(Path(HISTORICAL_DIR).iterdir()) if Path(HISTORICAL_DIR).exists() else []
        logger.error("Directory contents (%d items): %s", len(all_items),
                     [f.name for f in all_items[:10]])
        return

    all_rows = []
    for i, fp in enumerate(hist_files):
        try:
            payload = load_historical(fp)
        except Exception as e:
            logger.warning("Could not load %s: %s", fp.name, e)
            continue

        rows = build_snapshots_for_fixture(payload)
        all_rows.extend(rows)

        if (i + 1) % 50 == 0:
            logger.info("  Processed %d / %d fixtures (%d rows so far)",
                        i + 1, len(hist_files), len(all_rows))

    logger.info("Total snapshot rows: %d", len(all_rows))

    if not all_rows:
        logger.error("No snapshot rows produced — check that odds data exists in the files.")
        return

    df = pd.DataFrame(all_rows)

    # We keep only active prices
    if "active" in df.columns:
        df = df[df["active"] != False]

    df.to_csv(OUTPUT_SNAPSHOTS, index=False)
    logger.info("Snapshots saved to %s  shape=%s", OUTPUT_SNAPSHOTS, df.shape)
    logger.info("\n%s", df.head(5).to_string())


if __name__ == "__main__":
    main()