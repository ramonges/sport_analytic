# =============================================================================
# step_07_leaders_followers.py
# For each fixture × outcome × time window, detect which bookmaker moved first.
#
# Fixes vs previous version:
#   1. Reads .json.gz (compressed) files, not just .json
#   2. Normalises leadership counts by window width (events per minute)
#   3. Adds fixtures_quoted count per bookmaker for participation transparency
#   4. Forward-looking only window (no symmetric ±window)
#   5. Self-move deduplication: consecutive identical prices not counted as moves
#
# Produces:
#   data/leader_events.csv        — raw per-event rows
#   output/leader_follower.csv    — aggregated ranking table
# =============================================================================

import gzip
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd
import numpy as np

from config import DATA_DIR, OUTPUT_DIR, SNAPSHOT_MINUTES

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

Path(OUTPUT_DIR).mkdir(exist_ok=True)

HISTORICAL_DIR     = os.path.join(DATA_DIR, "historical")
LEADER_OUTPUT_FILE = os.path.join(OUTPUT_DIR, "leader_follower.csv")
RAW_EVENTS_FILE    = os.path.join(DATA_DIR, "leader_events.csv")

# Windows: (window_start_mins, window_end_mins, width_minutes)
# Width is used to normalise leadership counts to events-per-minute
WINDOWS = [
    (20, 15, 5),
    (15, 10, 5),
    (10,  5, 5),
    ( 5,  2, 3),   # 3-minute window
    ( 2,  1, 1),   # 1-minute window
]


def load_historical(fp: Path) -> dict:
    s = str(fp)
    if s.endswith(".gz"):
        with gzip.open(s, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(s) as f:
        return json.load(f)


def find_historical_files() -> list:
    """Return de-duplicated list of historical files, preferring .json.gz over .json."""
    gz_files   = {f.stem.replace(".json", ""): f for f in Path(HISTORICAL_DIR).glob("*.json.gz")}
    json_files = {f.stem: f                      for f in Path(HISTORICAL_DIR).glob("*.json")}
    merged = {**json_files, **gz_files}   # gz wins on collision
    return sorted(merged.values())


def is_meaningful_move(entries: list, idx: int, min_price_change: float = 0.001) -> bool:
    """
    Returns True if the price at entries[idx] differs from the previous price
    by at least min_price_change in implied probability terms.
    First tick for a bookmaker always counts as a meaningful move.
    This filters out duplicate ticks where price is unchanged.
    """
    if idx == 0:
        return True
    prev_price = entries[idx - 1].get("price")
    curr_price = entries[idx].get("price")
    if not prev_price or not curr_price or prev_price <= 1.0 or curr_price <= 1.0:
        return False
    delta_impl = abs(1.0 / curr_price - 1.0 / prev_price)
    return delta_impl >= min_price_change


def analyse_fixture(payload: dict) -> list[dict]:
    """
    For each time window and each outcome, find which bookmaker made
    the first *meaningful* price change (implied prob change >= 0.001).
    Records lag in seconds for each subsequent book that also moves
    within the same window.
    """
    fixture_id = payload["fixture_id"]
    start_time = payload["start_time"]
    kickoff_ms = start_time * 1000
    odds_by_bk = payload.get("odds", {})

    rows = []

    # Build outcome index: outcome_id -> {bookmaker: [sorted meaningful ticks]}
    outcome_index: dict = defaultdict(dict)
    for bk, entries in odds_by_bk.items():
        if not entries:
            continue
        sorted_entries = sorted(entries, key=lambda x: x.get("changedAt", 0))
        # Keep only ticks that represent meaningful price moves
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
        # window_start_ms is further from kickoff (e.g. T-20)
        # window_end_ms   is closer  to kickoff (e.g. T-15)
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
                continue  # need at least 2 books to define leader/follower

            # Leader = book with smallest changedAt in this window
            leader_bk = min(first_move, key=lambda b: first_move[b]["changedAt"])
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

    # Raw leadership counts per window
    window_leadership_raw = (
        df[df["is_leader"]]
        .groupby(["bookmaker", "window"])
        .size()
        .unstack(fill_value=0)
        .add_prefix("leads_")
    )

    # Per-minute normalised leadership counts per window
    # Divide raw count by window width in minutes
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

    summary = (
        total_per_book.to_frame()
        .join(fixtures_quoted, how="left")
        .join(leader_counts, how="left")
        .join(follower_lag, how="left")
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
    return summary.reset_index()


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

    logger.info("\n=== LEADER / FOLLOWER RANKING ===")
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
        logger.info("\n=== LEADERSHIP EVENTS PER MINUTE (normalised by window width) ===")
        logger.info("\n%s", summary[permin_cols].to_string(index=False))

    logger.info("\nSaved to %s", LEADER_OUTPUT_FILE)


if __name__ == "__main__":
    main()