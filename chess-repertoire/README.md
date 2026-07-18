# Opening Explorer

**Become Brilliant.**

Pulls your chess.com game history, maps the openings you're actually facing, scores your performance in each, and generates a concrete repertoire targeting the lines that are costing you the most rating.

## How it works

1. **Fetch** — Pulls your monthly game archives from the chess.com public API (free, no auth). Completed months are cached to `.chesscom-cache/` so repeat runs only re-fetch the current month.
2. **Parse** — Extracts opening data from the `ECO`/`ECOUrl` PGN headers chess.com embeds in every game. No engine needed; years of games parse in milliseconds.
3. **Analyze** — Groups games by opening family per color, computes score % and record, and flags **repertoire priorities**: openings you face frequently (≥4% of games, ≥8 games) where your score sits meaningfully below your overall baseline. Priorities are ranked by frequency × underperformance — i.e., expected rating impact of fixing them.
4. **Recommend** (optional) — Sends the aggregated stats to the Claude API, which returns a concrete repertoire: exact variations to adopt, defining move sequences, plans, common traps, and a one-week study plan.
5. **Report** — Everything lands in a single Markdown file.

## Setup

```bash
pip install requests
export ANTHROPIC_API_KEY=sk-ant-...   # optional; skip for stats-only
```

## Usage

```bash
# Full report: last 12 months, all time controls, with AI repertoire
python build_repertoire.py YOUR_USERNAME

# Rapid + blitz only, last 6 months, stats only
python build_repertoire.py YOUR_USERNAME --months 6 --time-class rapid blitz --no-ai

# Everything you've ever played
python build_repertoire.py YOUR_USERNAME --months 0
```

| Flag | Default | Purpose |
|---|---|---|
| `--months N` | 12 | Recent monthly archives to pull (0 = all) |
| `--time-class ...` | all | Filter to rapid/blitz/bullet/daily |
| `--include-unrated` | off | Rated games only by default |
| `--no-ai` | off | Skip Claude recommendations |
| `--model` | claude-sonnet-4-6 | Model for recommendations |
| `-o FILE` | `<username>-repertoire.md` | Output path |

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

## Notes

- Variants (bughouse, chess960, etc.) are excluded automatically.
- Time-control matters: your blitz and rapid opening problems are often different animals. Run with `--time-class rapid` and `--time-class blitz` separately if your ratings diverge.
- Re-run monthly. The priority ranking shifts as your repertoire changes, and the cache makes repeat runs nearly instant.
