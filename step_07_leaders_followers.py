# step_07_leaders_followers.py
# For each fixture × outcome × time window, detect which bookmaker moved first.

import gzip
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd
import numpy as np

from config import DATA_DIR, OUTPUT_DIR, EXCHANGE_COMMISSION, NO_LIMIT_BOOKS

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

Path(OUTPUT_DIR).mkdir(exist_ok=True)

HISTORICAL_DIR     = os.path.join(DATA_DIR, "historical")
LEADER_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "leader_follower.csv")
RAW_EVENTS_FILE    = os.path.join(DATA_DIR, "leader_events.csv")

# Windows: (window_start_mins, window_end_mins, width_minutes)
# Width is used to normalise leadership counts to events-per-minute
WINDOWS = [
    (1800, 1440, 360),  # T-30h → T-24h  (6h wide)
    (1440,  720, 720),  # T-24h → T-12h  (12h wide)
    ( 720,  300, 420),  # T-12h → T-5h   (7h wide)
    ( 300,   60, 240),  # T-5h  → T-1h   (4h wide)
    (  60,   30,  30),  # T-1h  → T-30m  (30m wide)
    (  30,   20,  10),  # T-30m → T-20m  (10m wide)
    (  20,   10,  10),  # T-20m → T-10m  (10m wide)
    (  10,    5,   5),  # T-10m → T-5m   (5m wide)
    (   5,    1,   4),  # T-5m  → T-1m   (4m wide)
]


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
    merged = {**json_files, **gz_files}   # gz wins on collision
    return sorted(merged.values())


def is_meaningful_move(entries: list, idx: int, min_price_change: float = 0.001) -> bool:
    if idx == 0:
        return True
    prev_price = entries[idx - 1].get("price")
    curr_price = entries[idx].get("price")
    if not prev_price or not curr_price or prev_price <= 1.0 or curr_price <= 1.0:
        return False
    delta_impl = abs(1.0 / curr_price - 1.0 / prev_price)
    return delta_impl >= min_price_change

def confidence(entry: dict) -> float:
    limit   = entry.get("limit")
    price = entry.get("price")
    if not limit or not price or price <= 1.0:
        return 0.0
    return float(limit) * (1.0 / price) #Most important formula here that gives the confidence level of a bookmaker odds.

def analyse_fixture(payload: dict) -> list[dict]:
    fixture_id = payload["fixture_id"]
    start_time = payload["start_time"]
    kickoff_ms = start_time * 1000
    odds_by_bk = payload.get("odds", {})

    rows = []

    outcome_index: dict = defaultdict(dict)
    for bk, entries in odds_by_bk.items():
        if not entries:
            continue
        for e in entries:
            e["price"] = net_exchange_price(e.get("price"), bk)
        sorted_entries = sorted(entries, key=lambda x: x.get("changedAt", 0))

        meaningful = [
            e for i, e in enumerate(sorted_entries)
            if is_meaningful_move(sorted_entries, i)
        ]
        for e in meaningful:
            oid = e.get("outcomeId")
            if oid is None:
                continue
            outcome_index[oid].setdefault(bk, []).append(e)

    for window_start_mins, window_end_mins, window_width_mins in WINDOWS:
        window_start_ms = kickoff_ms - (window_start_mins * 60 * 1000)
        window_end_ms   = kickoff_ms - (window_end_mins   * 60 * 1000)

        for oid, bk_entries in outcome_index.items():
            # For each bookmaker: first meaningful tick inside this window
            first_move: dict[str, dict] = {}
            for bk, entries in bk_entries.items():
                in_window = [
                    e for e in entries
                    if window_start_ms <= e.get("changedAt", 0) <= window_end_ms
                ]
                if in_window:
                    first_move[bk] = in_window[0]

            if len(first_move) < 2:
                continue  # We need at least 2 books to define leader/follower


            leader_bk = max(first_move, key=lambda b: confidence(first_move[b]))
            leader_ts = first_move[leader_bk]["changedAt"]

            for bk, entry in first_move.items():
                lag_ms  = entry["changedAt"] - leader_ts
                lag_sec = lag_ms / 1000.0
                rows.append({
                    "fixture_id":          fixture_id,
                    "outcome_id":          oid,
                    "window":              f"T-{window_start_mins}→T-{window_end_mins}",
                    "window_start_mins":   window_start_mins,
                    "window_end_mins":     window_end_mins,
                    "window_width_mins":   window_width_mins,
                    "bookmaker":           bk,
                    "is_leader":           bk == leader_bk,
                    "first_move_ts_ms":    entry["changedAt"],
                    "lag_sec":             lag_sec,
                    "price_after":         entry.get("price"),
                    "confidence_score": confidence(entry),  

                })

    return rows


def build_leader_summary(df: pd.DataFrame, total_fixtures: int) -> pd.DataFrame:
    """
    Aggregate per bookmaker:
      - total_appearances: events (outcome × window) in which book participated
      - leadership_count: raw count of leader events
      - leadership_rate_%: leadership_count / total_appearances
      - fixtures_quoted: number of distinct fixtures the book appears in
      - mean/median lag (seconds, follower events only)
      - per-window leadership counts (raw and per-minute normalised)
    """
    total_per_book = df.groupby("bookmaker").size().rename("total_appearances")

    fixtures_quoted = (
        df.groupby("bookmaker")["fixture_id"]
        .nunique()
        .rename("fixtures_quoted")
    )

    leader_counts = (
        df[df["is_leader"]]
        .groupby("bookmaker")
        .size()
        .rename("leadership_count")
    )

    follower_lag = (
        df[~df["is_leader"]]
        .groupby("bookmaker")["lag_sec"]
        .agg(mean_lag_sec="mean", median_lag_sec="median")
    )

    window_leadership_raw = (
        df[df["is_leader"]]
        .groupby(["bookmaker", "window"])
        .size()
        .unstack(fill_value=0)
        .add_prefix("leads_")
    )

    window_widths = {
        f"T-{ws}→T-{we}": ww
        for ws, we, ww in WINDOWS
    }

    window_leadership_norm = window_leadership_raw.copy()
    for col in window_leadership_norm.columns:
        window_name = col.replace("leads_", "")
        width = window_widths.get(window_name, 1)
        window_leadership_norm[col] = (window_leadership_norm[col] / width).round(2)
    window_leadership_norm.columns = [
        c.replace("leads_", "leads_permin_") for c in window_leadership_norm.columns
    ]

    leader_confidence = (
        df[df["is_leader"]]
        .groupby("bookmaker")["confidence_score"]
        .agg(mean_confidence_leader="mean", median_confidence_leader="median")
    )

    summary = (
        total_per_book.to_frame()
        .join(fixtures_quoted, how="left")
        .join(leader_counts, how="left")
        .join(follower_lag, how="left")
        .join(leader_confidence, how="left") 
        .join(window_leadership_raw, how="left")
        .join(window_leadership_norm, how="left")
    )


    summary["leadership_count"]  = summary["leadership_count"].fillna(0).astype(int)
    summary["fixtures_quoted"]   = summary["fixtures_quoted"].fillna(0).astype(int)
    summary["participation_rate_%"] = (
        summary["fixtures_quoted"] / total_fixtures * 100
    ).round(1)

    summary["leadership_rate_%"] = (
        summary["leadership_count"] / summary["total_appearances"] * 100
    ).round(2)

    summary = summary.sort_values("leadership_rate_%", ascending=False)
    summary["leader_rank"] = range(1, len(summary) + 1)
    summary = summary.reset_index()  
    summary["has_limit_data"] = ~summary["bookmaker"].isin(NO_LIMIT_BOOKS)

    return summary


def main():
    hist_files = find_historical_files()
    total_fixtures = len(hist_files)
    logger.info("Analysing leader/follower for %d fixtures...", total_fixtures)

    if total_fixtures == 0:
        logger.error("No historical files found in %s", HISTORICAL_DIR)
        return

    all_rows = []
    for i, fp in enumerate(hist_files):
        try:
            payload = load_historical(fp)
        except Exception as e:
            logger.warning("Could not load %s: %s", fp.name, e)
            continue

        if not any(payload.get("odds", {}).values()):
            continue
        
        rows = analyse_fixture(payload)
        all_rows.extend(rows)

        if (i + 1) % 50 == 0:
            logger.info("  %d / %d fixtures processed (%d events so far)",
                        i + 1, total_fixtures, len(all_rows))

    if not all_rows:
        logger.error("No leader/follower rows generated. Check historical data.")
        return

    df = pd.DataFrame(all_rows)
    logger.info("Total move events: %d", len(df))

    df.to_csv(RAW_EVENTS_FILE, index=False)
    logger.info("Raw leader events saved to %s", RAW_EVENTS_FILE)

    summary = build_leader_summary(df, total_fixtures)
    summary.to_csv(LEADER_OUTPUT_FILE, index=False)

    logger.info(" LEADER / FOLLOWER RANKING")
    display_cols = [
        "bookmaker", "fixtures_quoted", "participation_rate_%",
        "total_appearances", "leadership_count", "leadership_rate_%",
        "mean_lag_sec", "median_lag_sec", "leader_rank"
    ]
    display_cols = [c for c in display_cols if c in summary.columns]
    logger.info("\n%s", summary[display_cols].to_string(index=False))

    # Show normalised per-minute breakdown
    permin_cols = ["bookmaker"] + [c for c in summary.columns if "permin" in c]
    if len(permin_cols) > 1:
        logger.info("LEADERSHIP EVENTS PER MINUTE (normalised by window width)")
        logger.info("\n%s", summary[permin_cols].to_string(index=False))

    logger.info("Saved to %s", LEADER_OUTPUT_FILE)


if __name__ == "__main__":
    main()