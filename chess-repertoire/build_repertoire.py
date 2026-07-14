#!/usr/bin/env python3
"""Build an opening repertoire from your chess.com games.

Usage:
    python build_repertoire.py [USERNAME] [options]

If USERNAME is omitted, you'll be prompted to enter it interactively.

Examples:
    python build_repertoire.py
    python build_repertoire.py alexf --months 12 --time-class rapid blitz
    python build_repertoire.py alexf --months 6 --no-ai -o report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from repertoire import analyze, fetch, parse, recommend, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("username", nargs="?", default=None,
                    help="chess.com username (prompted for if omitted)")
    ap.add_argument("--months", type=int, default=12,
                    help="how many recent monthly archives to pull (default 12, 0 = all)")
    ap.add_argument("--time-class", nargs="+", default=None,
                    choices=["rapid", "blitz", "bullet", "daily"],
                    help="only analyze these time controls (default: all)")
    ap.add_argument("--include-unrated", action="store_true",
                    help="include unrated games (default: rated only)")
    ap.add_argument("--no-ai", action="store_true",
                    help="skip the Claude API repertoire recommendations")
    ap.add_argument("--model", default=recommend.DEFAULT_MODEL,
                    help=f"Claude model for recommendations (default {recommend.DEFAULT_MODEL})")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="write the Markdown report here (default: <username>-repertoire.md)")
    ap.add_argument("--cache-dir", type=Path, default=Path(".chesscom-cache"),
                    help="directory for caching completed months (default .chesscom-cache)")
    args = ap.parse_args()

    while not args.username:
        try:
            args.username = input("Enter your chess.com username: ").strip()
        except EOFError:
            print("Error: no username provided.", file=sys.stderr)
            return 1

    print(f"Fetching archives for {args.username}...")
    try:
        raw = fetch.fetch_games(args.username, months=args.months,
                                cache_dir=args.cache_dir)
    except fetch.ChessComError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    games = parse.parse_games(raw, args.username)
    print(f"Parsed {len(games)} standard games ({len(raw)} total fetched).")
    if not games:
        print("Nothing to analyze.", file=sys.stderr)
        return 1

    tc = set(args.time_class) if args.time_class else None
    white, black, priorities = analyze.analyze(
        games, time_classes=tc, rated_only=not args.include_unrated
    )
    print(f"As White: {white.total_games} games ({white.score:.1f}%). "
          f"As Black: {black.total_games} games ({black.score:.1f}%).")
    print(f"Found {len(priorities)} repertoire priorities.")

    recs = None
    if not args.no_ai:
        try:
            print("Generating repertoire recommendations via Claude...")
            recs = recommend.get_recommendations(white, black, priorities,
                                                 model=args.model)
        except EnvironmentError as e:
            print(f"Note: {e}")
        except Exception as e:  # keep the stats report even if the API hiccups
            print(f"Warning: recommendation step failed ({e}). "
                  f"Stats report will still be written.")

    filters = []
    if tc:
        filters.append("/".join(sorted(tc)))
    filters.append("rated only" if not args.include_unrated else "incl. unrated")
    filters_desc = f" — {args.months or 'all'} months, {', '.join(filters)}"

    md = report.render(args.username, white, black, priorities, recs, filters_desc)
    out = args.output or Path(f"{args.username}-repertoire.md")
    out.write_text(md, encoding="utf-8")
    print(f"\nReport written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
