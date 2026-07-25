#!/usr/bin/env python3
"""CLI entry point for the indexer (also what the daily cron runs).

Usage:
    python run_indexer.py            # process up to AGS_MAX_VIDEOS_PER_RUN (default 10)
    python run_indexer.py -n 0       # unlimited backfill run
    python run_indexer.py -n 5       # exactly 5 videos this run
"""
import argparse

from app import indexer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index Astrogoblin videos into the transcript search database."
    )
    parser.add_argument(
        "-n", "--limit", type=int, default=None,
        help="max videos to transcribe this run (0 = unlimited; default: AGS_MAX_VIDEOS_PER_RUN)",
    )
    args = parser.parse_args()
    indexer.run(limit=args.limit)


if __name__ == "__main__":
    main()
