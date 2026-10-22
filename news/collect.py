"""Run once, or keep a separate background collector running."""
import argparse
import logging
import time

from news.collector import collect_all
from news.sources import SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", choices=[s.id for s in SOURCES], help="Repeat to select multiple outlets")
    parser.add_argument("--extract", action="store_true", help="Also extract permitted article text with Trafilatura")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N minutes (minimum 5); omit to run once")
    args = parser.parse_args()
    if args.interval and args.interval < 5:
        parser.error("--interval must be at least 5 minutes")
    sources = [s for s in SOURCES if s.enabled and (not args.source or s.id in args.source)]
    if not sources:
        parser.error("No enabled sources selected; edit news/sources.py to enable a source")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        while True:
            results = collect_all(sources, extract_articles=args.extract)
            for result in results:
                print(f"{result['source']}: " + (result['error'] or f"{result['count']} headlines saved"), flush=True)
            if not args.interval:
                return 1 if any(result["error"] for result in results) else 0
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
