# =============================================================================
# step_06_clv_scoring.py
# Compute CLV accuracy per bookmaker per snapshot window.
# Uses the native CLV data (step_04) as the closing line benchmark.
#
# Produces:
#   data/clv_scores.csv         — per-fixture per-bookmaker per-snapshot CLV delta
#   output/accuracy_scorecard.csv — aggregated ranking table
# =============================================================================

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


def no_vig_prob_single(price: float, counterpart_price: float) -> float:
    """Implied prob for one side of a two-outcome market, margin-removed."""
    if not price or not counterpart_price:
        return np.nan
    raw_self  = 1.0 / price
    raw_other = 1.0 / counterpart_price
    total     = raw_self + raw_other
    return raw_self / total if total else np.nan


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
    """
    For each snapshot row, compute the CLV delta:
      clv_delta = no_vig_prob(snapshot_price) − no_vig_prob(closing_price)

    Positive = book was offering stale/better-than-closing value (soft)
    Near 0   = book was already at market (sharp)
    """
    rows = []

    for fixture_id, group in snapshots_df.groupby("fixture_id"):
        closing_lines = load_closing_lines(fixture_id)
        if not closing_lines:
            continue

        for _, row in group.iterrows():
            bk       = row["bookmaker"]
            oid      = int(row["outcome_id"]) if pd.notna(row["outcome_id"]) else None
            snap_px  = row["price"]
            mins     = row["snapshot_mins"]

            if not oid or pd.isna(snap_px):
                continue

            closing_bk = closing_lines.get(bk, {})
            closing_px = closing_bk.get(oid)

            if not closing_px or closing_px <= 1.0:
                continue

            # Simplified CLV: raw implied prob difference (no paired-outcome needed)
            snap_impl    = 1.0 / snap_px
            closing_impl = 1.0 / closing_px
            clv_delta    = snap_impl - closing_impl

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
            })

    return pd.DataFrame(rows)


def build_accuracy_scorecard(clv_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate CLV metrics per bookmaker per snapshot window.
    Lower mean_abs_clv = more accurate (closer to closing) at that snapshot.
    """
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
        )
        .reset_index()
    )

    # Pivot so columns = snapshot windows for easy ranking
    pivot = agg.pivot(index="bookmaker", columns="snapshot_mins", values="mean_abs_clv")
    pivot.columns = [f"clv_T-{c}min" for c in pivot.columns]
    pivot = pivot.reset_index()

    # Add overall score = weighted average (closer to kickoff = higher weight)
    weights = {1: 3, 2: 2, 5: 1.5, 10: 1.0, 15: 0.8, 20: 0.5}
    available_cols = [f"clv_T-{m}min" for m in SNAPSHOT_MINUTES if f"clv_T-{m}min" in pivot.columns]

    def weighted_score(row):
        total_w = 0
        total_v = 0
        for col in available_cols:
            mins = int(col.replace("clv_T-", "").replace("min", ""))
            w    = weights.get(mins, 1)
            v    = row[col]
            if pd.notna(v):
                total_v += v * w
                total_w += w
        return total_v / total_w if total_w else np.nan

    pivot["weighted_clv_score"] = pivot.apply(weighted_score, axis=1)
    pivot = pivot.sort_values("weighted_clv_score")

    # Flag soft books (T-2 CLV > 1.5x median)
    if "clv_T-2min" in pivot.columns:
        median_t2 = pivot["clv_T-2min"].median()
        pivot["soft_book"] = pivot["clv_T-2min"] > (1.5 * median_t2)
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