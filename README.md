# btc-gt-data

Public JSON files for State of Blocks dashboards, served from GitHub Pages
(`https://that1618guy.github.io/btc-gt-data/<file>.json`).

## Green Face Tracker

What Bitcoin did after CZ's bullish posts. A crowding gauge for Binance BTC perps sits beside it as the backdrop.
Page: stateofblocks.com/dashboards/green-face/. Data: docs/greenface.json.

- `scripts/greenface_publish.py` writes `docs/greenface.json` every 6 hours (`.github/workflows/greenface.yml`).
- `data/cz_posts.csv` is the post log.
- `research/greenface/` is the backtest behind the method note.

### Adding a post

One command, from the repo root:

```
python3 scripts/add_post.py 2026-11-20 "Bitcoin is resilient." hype https://x.com/cz_binance/status/...
```

The arguments are the date (YYYY-MM-DD, UTC), the label (the post's words, as the page should show them),
the kind (`hype` or `dip`) and the source URL. Add `--approx` if the date is not exact, or `--dry-run` to check
without writing. The script checks the row, appends it to `data/cz_posts.csv`, and prints what the page's hero
will show. Then commit `data/cz_posts.csv`; the page picks it up on the next 6-hourly run.

A post appears once its day's BTC close is in the price series, usually the day after.
The hero always shows the latest hype post, even when a dip post is more recent.
