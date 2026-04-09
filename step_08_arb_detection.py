# =============================================================================
# step_08_arb_detection.py
# At each snapshot, check if any pair of bookmakers creates an arb
# (combined implied probability < 100%) on the same market.
#
# Fixes vs previous version:
#   1. Self-arb filtered: best price on side A and side B must come from
#      DIFFERENT bookmakers (cannot bet both sides with the same book).
#   2. Main-line filter: only main_line=True rows are compared, ensuring
#      both sides correspond to the same handicap/total line.
#
# Produces:
#   output/arb_windows.csv
# =============================================================================

import logging
import os
from pathlib import Path

import pandas as pd

from config import DATA_DIR, OUTPUT_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

Path(OUTPUT_DIR).mkdir(exist_ok=True)

SNAPSHOTS_FILE = os.path.join(DATA_DIR, "snapshots.csv")
ARB_OUTPUT     = os.path.join(OUTPUT_DIR, "arb_windows.csv")


def detect_arb_in_market(group: pd.DataFrame) -> list[dict]:
    """
    For a single (fixture_id, market_id, snapshot_mins) group — already filtered
    to main_line=True rows only — find the best price for each outcome_id across
    all bookmakers.

    An arbitrage requires:
      1. over_round < 1.0  (combined implied prob < 100%)
      2. best_a_book != best_b_book  (must be different bookmakers — no self-arb)
    """
    arbs = []

    outcome_ids = group["outcome_id"].dropna().unique()
    if len(outcome_ids) != 2:
        return arbs  # skip non-two-sided markets

    oid_a, oid_b = sorted(outcome_ids)

    side_a = group[group["outcome_id"] == oid_a]
    side_b = group[group["outcome_id"] == oid_b]

    if side_a.empty or side_b.empty:
        return arbs

    best_a_idx   = side_a["price"].idxmax()
    best_b_idx   = side_b["price"].idxmax()
    best_a_price = side_a.loc[best_a_idx, "price"]
    best_b_price = side_b.loc[best_b_idx, "price"]
    best_a_book  = side_a.loc[best_a_idx, "bookmaker"]
    best_b_book  = side_b.loc[best_b_idx, "bookmaker"]

    if best_a_price <= 1.0 or best_b_price <= 1.0:
        return arbs

    # FIX 1: self-arb guard — both sides must come from different bookmakers
    if best_a_book == best_b_book:
        return arbs

    over_round     = (1.0 / best_a_price) + (1.0 / best_b_price)
    arb_margin_pct = (1.0 - over_round) * 100

    if over_round < 1.0:
        arbs.append({
            "fixture_id":    group["fixture_id"].iloc[0],
            "home_team":     group["home_team"].iloc[0],
            "away_team":     group["away_team"].iloc[0],
            "market_id":     group["market_id"].iloc[0],
            "snapshot_mins": group["snapshot_mins"].iloc[0],
            "outcome_a_id":  oid_a,
            "outcome_b_id":  oid_b,
            "best_price_a":  round(best_a_price, 4),
            "best_price_b":  round(best_b_price, 4),
            "book_side_a":   best_a_book,
            "book_side_b":   best_b_book,
            "over_round":    round(over_round, 4),
            "arb_margin_%":  round(arb_margin_pct, 3),
        })

    return arbs


def main():
    if not os.path.exists(SNAPSHOTS_FILE):
        raise FileNotFoundError("Run step_05_build_snapshots.py first.")

    logger.info("Loading snapshots...")
    df = pd.read_csv(SNAPSHOTS_FILE)
    logger.info("Total snapshot rows: %d", len(df))

    # FIX 2: main_line filter — only compare economically equivalent lines
    if "main_line" in df.columns:
        before = len(df)
        df = df[df["main_line"] == True]
        logger.info("After main_line=True filter: %d rows (dropped %d off-main rows)",
                    len(df), before - len(df))
    else:
        logger.warning("main_line column not found — skipping main-line filter")

    arb_rows = []
    groups = df.groupby(["fixture_id", "market_id", "snapshot_mins"])
    logger.info("Checking %d market-snapshot groups for arb...", len(groups))

    for _, group in groups:
        arb_rows.extend(detect_arb_in_market(group))

    if not arb_rows:
        logger.info("No arbitrage windows found.")
        return

    arb_df = pd.DataFrame(arb_rows)
    arb_df = arb_df.sort_values("arb_margin_%", ascending=False)
    arb_df.to_csv(ARB_OUTPUT, index=False)

    logger.info("\n=== ARB WINDOWS FOUND: %d ===", len(arb_df))
    logger.info("\n%s", arb_df.head(20).to_string(index=False))

    # Summary by snapshot window
    by_window = arb_df.groupby("snapshot_mins").agg(
        count=("arb_margin_%", "count"),
        avg_margin=("arb_margin_%", "mean"),
        max_margin=("arb_margin_%", "max"),
    )
    logger.info("\nArb by snapshot window:\n%s", by_window.to_string())

    # Summary by book pair (directional: side_a / side_b)
    pair_counts = (arb_df["book_side_a"] + " / " + arb_df["book_side_b"]).value_counts().head(10)
    logger.info("\nTop arb book pairs (directional):\n%s", pair_counts.to_string())

    # Summary by book pair (symmetric)
    arb_df["book_pair"] = arb_df.apply(
        lambda r: " / ".join(sorted([r["book_side_a"], r["book_side_b"]])), axis=1
    )
    sym_pair_counts = arb_df["book_pair"].value_counts().head(10)
    logger.info("\nTop arb book pairs (symmetric):\n%s", sym_pair_counts.to_string())

    # Filtered summary (margin < 5% — removes likely stale-price artefacts)
    real_arb = arb_df[arb_df["arb_margin_%"] < 5.0]
    logger.info("\n--- Filtered arbs (margin < 5%%, likely actionable): %d ---", len(real_arb))
    if not real_arb.empty:
        logger.info("Mean margin: %.4f%%  Max margin: %.4f%%",
                    real_arb["arb_margin_%"].mean(), real_arb["arb_margin_%"].max())
        real_pair = real_arb["book_pair"].value_counts().head(10)
        logger.info("Top pairs (filtered):\n%s", real_pair.to_string())

    logger.info("\nSaved to %s", ARB_OUTPUT)


if __name__ == "__main__":
    main()