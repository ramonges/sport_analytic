import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

STEPS = {
    1: ("step_01_bootstrap",             "Discover sportId / tournamentId / seasonId"),
    2: ("step_02_fetch_fixtures",        "Enumerate all EPL fixtures"),
    3: ("step_03_fetch_historical_odds", "Pull full price timeline (slow, run once)"),
    4: ("step_04_fetch_clv",             "Pull native CLV endpoint"),
    5: ("step_05_build_snapshots",       "Build T-30h…T-1min snapshot table"),
    6: ("step_06_clv_scoring",           "Compute CLV accuracy per bookmaker"),
    7: ("step_07_leaders_followers",     "Leader / follower detection"),
    8: ("step_08_arb_detection",         "Arbitrage window detection"),
    9: ("step_09_vig_evolution",         "Vig evolution chart per bookmaker"),
}


def run_step(step_num: int):
    module_name, description = STEPS[step_num]
    logger.info("=" * 60)
    logger.info("STEP %d: %s", step_num, description)
    logger.info("=" * 60)
    import importlib
    mod = importlib.import_module(module_name)
    mod.main()


def main():
    parser = argparse.ArgumentParser(description="OddsPapi EPL analysis pipeline")
    parser.add_argument("--from",  dest="from_step", type=int, default=1)
    parser.add_argument("--only",  dest="only_step", type=int, default=None)
    args = parser.parse_args()

    if args.only_step:
        if args.only_step not in STEPS:
            logger.error("Unknown step: %d", args.only_step)
            sys.exit(1)
        run_step(args.only_step)
    else:
        for step_num in sorted(STEPS.keys()):
            if step_num < args.from_step:
                continue
            run_step(step_num)

    logger.info("Pipeline complete.")


if __name__ == "__main__":
    main()