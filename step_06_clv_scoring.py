# step_06_clv_scoring.py
# Compute CLV accuracy per bookmaker per snapshot window.

import json
import logging
import os
from pathlib import Path

import pandas as pd
import numpy as np

from config import DATA_DIR, OUTPUT_DIR, SNAPSHOT_MINUTES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

Path(OUTPUT_DIR).mkdir(exist_ok=True)

SNAPSHOTS_FILE   = os.path.join(DATA_DIR, "snapshots.csv")
CLV_DIR          = os.path.join(DATA_DIR, "clv")
CLV_SCORES_FILE  = os.path.join(DATA_DIR, "clv_scores.csv")
SCORECARD_FILE   = os.path.join(OUTPUT_DIR, "accuracy_scorecard.csv")

def load_closing_lines(fixture_id: str) -> dict:
    """
    Load the native CLV data for a fixture.
    Returns dict: {bookmaker: {outcome_id: closing_price}}

    Real structure:
      clv_data["odds"][bookmaker][oddsId]["clv"] = {price, outcomeId, changedAt, ...}
    The timestamp key is literally the string "clv".
    """
    path = os.path.join(CLV_DIR, f"{fixture_id}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)

    result = {}
    odds_block = data.get("clv_data", {}).get("odds", {})

    for bk, odds_map in odds_block.items():
        if not isinstance(odds_map, dict):
            continue
        for odds_id, ts_map in odds_map.items():
            if not isinstance(ts_map, dict):
                continue
            tick = ts_map.get("clv")
            if not isinstance(tick, dict):
                continue
            oid = tick.get("outcomeId")
            px  = tick.get("price")
            if oid and px and px > 1.0:
                result.setdefault(bk, {})[oid] = px

    return result


def compute_clv_scores(snapshots_df: pd.DataFrame) -> pd.DataFrame:
    # Pre-compute overround per (fixture, bookmaker, market_id, snapshot_mins)
    overround_map = {}
    for keys, grp in snapshots_df.groupby(
            ["fixture_id", "bookmaker", "market_id", "snapshot_mins"]):
        prices = grp["price"].dropna().values
        if len(prices) == 2 and all(p > 1.0 for p in prices):
            overround_map[keys] = round(1/prices[0] + 1/prices[1], 6)

    rows = []
    for fixture_id, group in snapshots_df.groupby("fixture_id"):
        closing_lines = load_closing_lines(fixture_id)
        if not closing_lines:
            continue

        for _, row in group.iterrows():
            bk      = row["bookmaker"]
            oid     = int(row["outcome_id"]) if pd.notna(row["outcome_id"]) else None
            snap_px = row["price"]
            mins    = row["snapshot_mins"]

            if not oid or pd.isna(snap_px):
                continue

            closing_bk = closing_lines.get(bk, {})
            closing_px = closing_bk.get(oid)

            if not closing_px or closing_px <= 1.0:
                continue

            # CLV delta unchanged, raw implied prob
            snap_impl    = 1.0 / snap_px
            closing_impl = 1.0 / closing_px
            clv_delta    = snap_impl - closing_impl

            overround = overround_map.get(
                (fixture_id, bk, row["market_id"], mins)
            )

            rows.append({
                "fixture_id":    fixture_id,
                "bookmaker":     bk,
                "outcome_id":    oid,
                "market_id":     row["market_id"],
                "snapshot_mins": mins,
                "snap_price":    snap_px,
                "closing_price": closing_px,
                "snap_impl":     snap_impl,
                "closing_impl":  closing_impl,
                "clv_delta":     clv_delta,
                "abs_clv":       abs(clv_delta),
                "overround":     overround,   #None if market not fully paired
            })

    return pd.DataFrame(rows)


def build_accuracy_scorecard(clv_df: pd.DataFrame) -> pd.DataFrame:
#    Aggregate CLV metrics per bookmaker per snapshot window. Lower mean_abs_clv = more accurate (closer to closing) at that snapshot.
    agg = (
        clv_df
        .groupby(["bookmaker", "snapshot_mins"])
        .agg(
            mean_abs_clv=("abs_clv", "mean"),
            median_abs_clv=("abs_clv", "median"),
            std_clv=("clv_delta", "std"),
            clv_corr=("snap_impl", lambda x: x.corr(
                clv_df.loc[x.index, "closing_impl"])),
            n_observations=("abs_clv", "count"),
            mean_overround=("overround", "mean"),
        )
        .reset_index()
    )

    # Pivot mean_abs_clv so columns = snapshot windows for easy ranking
    pivot = agg.pivot(index="bookmaker", columns="snapshot_mins", values="mean_abs_clv")
    pivot.columns = [f"clv_T-{c}min" for c in pivot.columns]
    pivot = pivot.reset_index()

    # Also pivot mean_overround for vig evolution,  used by step_09
    vig_pivot = agg.pivot(index="bookmaker", columns="snapshot_mins", values="mean_overround")
    vig_pivot.columns = [f"vig_T-{c}min" for c in vig_pivot.columns]
    vig_pivot = vig_pivot.reset_index()
    pivot = pivot.merge(vig_pivot, on="bookmaker", how="left")

    # Weights: closer to kickoff = higher weight
    weights = {
        1:    3.0,
        5:    2.5,
        10:   2.0,
        20:   1.5,
        30:   1.2,
        60:   1.0,
        300:  0.7,
        720:  0.5,
        1440: 0.3,
        1800: 0.2,
    }
    available_cols = [f"clv_T-{m}min" for m in SNAPSHOT_MINUTES if f"clv_T-{m}min" in pivot.columns]

    def weighted_score(row):
        total_w = 0
        total_v = 0
        for col in available_cols:
            mins = int(col.replace("clv_T-", "").replace("min", ""))
            w    = weights.get(mins, 1.0)
            v    = row[col]
            if pd.notna(v):
                total_v += v * w
                total_w += w
        return total_v / total_w if total_w else np.nan

    pivot["weighted_clv_score"] = pivot.apply(weighted_score, axis=1)
    pivot = pivot.sort_values("weighted_clv_score")

    # Soft book flag, dynamically uses closest available window to kickoff
    clv_cols = [c for c in pivot.columns if c.startswith("clv_T-")]
    if clv_cols:
        closest_col  = min(clv_cols, key=lambda c: int(c.replace("clv_T-", "").replace("min", "")))
        median_close = pivot[closest_col].median()
        pivot["soft_book"] = pivot[closest_col] > (1.5 * median_close)
    else:
        pivot["soft_book"] = False

    pivot["rank"] = range(1, len(pivot) + 1)
    return pivot


def main():
    if not os.path.exists(SNAPSHOTS_FILE):
        raise FileNotFoundError("Run step_05_build_snapshots.py first.")

    logger.info("Loading snapshots...")
    df = pd.read_csv(SNAPSHOTS_FILE)
    logger.info("Snapshot rows: %d", len(df))

    logger.info("Computing CLV scores...")
    clv_df = compute_clv_scores(df)
    logger.info("CLV score rows: %d", len(clv_df))

    if clv_df.empty:
        logger.error("No CLV scores computed. Check that CLV files exist in data/clv/")
        return

    clv_df.to_csv(CLV_SCORES_FILE, index=False)
    logger.info("CLV scores saved to %s", CLV_SCORES_FILE)

    logger.info("Building accuracy scorecard...")
    scorecard = build_accuracy_scorecard(clv_df)
    scorecard.to_csv(SCORECARD_FILE, index=False)

    logger.info("\n=== ACCURACY SCORECARD ===")
    logger.info("\n%s", scorecard.to_string(index=False))
    logger.info("\nSaved to %s", SCORECARD_FILE)


if __name__ == "__main__":
    main()