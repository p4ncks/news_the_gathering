"""Run once, or keep a separate background collector running."""
import argparse
import logging
import os
import time

from news.collector import collect_all, preparation_limits, retention_limits
from news.locking import collector_lock, CollectorAlreadyRunning
from news.sources import SOURCES


def wait_for_next_run(seconds):
    """Recheck the schedule after suspension or a clock correction.

    Wall time catches up after a machine sleeps; monotonic time prevents a
    backwards clock change from postponing collection indefinitely.
    """
    wall_deadline = time.time() + seconds
    monotonic_deadline = time.monotonic() + seconds
    while True:
        remaining = min(wall_deadline - time.time(), monotonic_deadline - time.monotonic())
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=[s.id for s in SOURCES], help="Repeat to select multiple outlets")
    parser.add_argument("--extract", action="store_true", help="Also extract permitted article text with Trafilatura")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N minutes (minimum 5); omit to run once")
    parser.add_argument("--source-timeout", type=float, default=os.environ.get("NEWS_SOURCE_TIMEOUT_SECONDS", "60"),
                        help="Total preparation budget per outlet in seconds (default: 60)")
    parser.add_argument("--min-edition-ratio", type=float, default=os.environ.get("NEWS_MIN_EDITION_RATIO", "0.5"),
                        help="Reject sharp count drops once an outlet has at least five headlines; 0 disables (default: 0.5)")
    args = parser.parse_args()
    if args.interval and args.interval < 5:
        parser.error("--interval must be at least 5 minutes")
    try:
        preparation_limits(args.source_timeout, args.min_edition_ratio)
        retention_limits()
    except ValueError as error:
        parser.error(str(error))
    sources = [s for s in SOURCES if s.enabled and (not args.source or s.id in args.source)]
    if not sources:
        parser.error("No enabled sources selected; edit news/sources.py to enable a source")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        while True:
            # Hold the database's file lock for each run, then release it while
            # waiting so one-off collection remains available between runs.
            with collector_lock():
                results = collect_all(sources, extract_articles=args.extract,
                                      timeout_seconds=args.source_timeout, minimum_edition_ratio=args.min_edition_ratio)
                for result in results:
                    print(f"{result['source']}: " + (result['error'] or f"{result['count']} headlines saved"), flush=True)
                if not args.interval:
                    return 1 if any(result["error"] for result in results) else 0
            wait_for_next_run(args.interval * 60)
    except CollectorAlreadyRunning as error:
        LOG = logging.getLogger(__name__)
        LOG.error("%s", error)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
