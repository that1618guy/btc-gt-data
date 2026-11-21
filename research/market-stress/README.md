# Market Stress Index: research notes

Written Nov 20, 2026. Sample: Mar 6, 2006 (first VVIX print) to Nov 19, 2026, 5,212 sessions. Nov 20, 2026 is the live session and uses the 3:57pm ET values from the reference screenshot (VIX 40.03, VVIX 146.36, COR1M 44.71, SPY 687.32), so treat that day as provisional until the publisher writes the real close.

## Where it came from

A public "Crash Risk Index" (screenshot, Nov 20, 2026) reads 81/100 from four inputs scored out of 25 each, plus three crash triggers:

| Input | Its scale (rebuilt) | Nov 20 |
|---|---|---|
| VIX | 15 to 40 (inferred) | 25.0 |
| VVIX | 85 to 175 (inferred) | 17.0 |
| COR1M | 20 to 60 (matches exactly) | 15.4 |
| SPY under its 100-day | 0% to 10% under (matches exactly) | 23.7 |
| Triggers | SPX < 100d MA, 20d realized vol > 25%, COR1M > 60 | 2 of 3 |

The rebuilt scale gives 81.3 on Nov 20, the same as the screenshot. The thesis: when all four max out and all three triggers fire, forced selling is peaking, and that is the moment to buy.

## What the backtest changed

**COR1M is out.** Its range drifted with market structure: median 61.5 in 2010, 10.2 in 2026. The "COR1M > 60" trigger last printed on Oct 30, 2020. In April 2025, one of the cleanest washouts on record, COR1M topped at 49.9, so the original's crash trigger never fired. Scoring COR1M against its own last year fixes the drift, but the ablation showed it adds nothing: the same washouts get called with or without it (with it, 2008 is called four days earlier), and the only extra call it adds is May 9, 2022 (S&P +3.3% three months later, Bitcoin −21.4%).

**DXY and MOVE stay, but outside the reading.** As inputs to the 0–100 score they knock out May 2010 and Aug 2011 (S&P worst drop after Aug 2011: −1.8%), add May 2022, and the median worst drop after entry gets worse (−8.5% against −4.6%). As a separate check on the kind of flush, they are the most useful thing tested.

## The model

Reading, 0 to 100, a third each:

- Fear: VIX, scored 15 to 40
- Fragility: VVIX, scored 90 to 150
- Damage: S&P 500 under its 100-day average, scored 0% to 10% under

Seals: S&P under its 100-day; 20-session realized vol (log returns, annualized) over 25%.

States: calm under 40, uneasy 40–65, stressed 65–80, panic 80+. Washout = both seals broken with the reading at 80+. An episode opens on the first washout and ends when the reading is back under 40. Clearing = 20+ points under the episode's crest for 3 straight sessions.

Funding check: rates stressed when MOVE is in the top 10% of its last 252 sessions; dollar stressed when DXY is up 2%+ over 20 sessions. Either one = funding stressed.

All thresholds are round numbers set before looking at outcomes. Only the COR1M decision and the funding check's placement (layer, not input) were made after seeing results; both are reported with the alternatives below.

## Results

**Washouts since 2006:** Oct 10, 2008; May 20, 2010; Aug 8, 2011; Mar 9, 2020; Apr 4, 2025. Nov 20, 2026 is the sixth (reading 96.4, higher than 99.6% of sessions since 2006).

Buying the first washout close:

| | S&P 3 months | S&P 12 months | S&P worst drop, next 6 months | Bitcoin 3 months |
|---|---|---|---|---|
| Original's rules (4 washouts) | +6.2%, 3 of 4 up | +25.3%, 3 of 4 up | −11.6% median, −36.0% worst | +23.3% (2020 only) |
| This model (5 washouts) | +11.9%, 4 of 5 up | +25.4%, 5 of 5 up | −4.6% median, −24.8% worst | +26.1% (2020, 2025) |

Every washout so far had a lower close still to come (5 of 5).

**The funding check at washouts.** Funding was stressed at the first washout close every time. In the four episodes where it calmed before the episode ended (after 2, 8, 14 and 31 sessions; May 2010 never calmed), buying the first calm close instead of the first washout close:

| Same 4 washouts | S&P 3 months | S&P 12 months | S&P worst drop | Bitcoin 3 months (2020, 2025) |
|---|---|---|---|---|
| First washout close | +14.8% | +29.4% | −10.2% | +26.1% |
| First close with funding calm | +16.1% | +32.3% | −2.5% | +41.5% |

The gain is mostly less pain, not more return, and most of it is 2020 (worst drop −18.5% from the washout, −2.8% from the calm close). 2008 still fell 20.6% after funding calmed.

**The funding check on every stress flush.** 21 times since 2006 the reading crossed 65 after a reset under 40:

| | S&P 3 months | S&P worst drop | Bitcoin 3 months |
|---|---|---|---|
| Funding calm (5) | +13.6% median, 5 of 5 up | 0.0% median | +28.8%, 4 of 4 up |
| Funding stressed (16) | +3.7%, 12 of 16 up | −5.9% | −2.8%, 4 of 11 up |
| Dollar stressed (7) | +3.3%, 6 of 7 up | −4.6% | −15.5%, 0 of 4 up |

Robustness: across dollar lines from +1% to +3% and MOVE lines from the 80th to 95th percentile, calm flushes stayed 100% up at three months (medians +12.0% to +14.5%) and stressed flushes stayed between +2.5% and +4.5%.

**Clearing as an entry** (20 points off the crest for 3 sessions) did not beat the washout close: S&P +5.4% at 3 months (3 of 5 up), worst drop −7.3%. It stays on the page as a state, not as the entry rule.

## Caveats

- Small samples: 5 washouts, 21 stress flushes, Bitcoin only from Sep 2014 (2 washouts, 15 flushes). 2022 contributes six of the stressed flushes, all in one rates-driven bear market.
- Forward windows overlap inside episodes; the medians above use one entry per episode or per flush, not every day.
- VVIX history has gaps in 2006 (43 sessions without a reading). No episode falls there.
- The research copy of MOVE has no prints from Oct 26 to Nov 18, 2026 (source gap). That only affects the live percentile in this offline build; the publisher pulls the full series.

## Files

- `engine.py` (pandas, research) and `scripts/market_stress_publish.py` (standard library, production) compute the same thing. Checked session by session over 5,170 sessions: largest score difference 1e-12, no state, funding or percentile mismatches.
- `backtest.py` writes `out/results.json`; `ablation.py` runs the drop-one-input and five-input tests; `summary.py` writes `summary.json`, the numbers the page shows.
- `build_data.py` assembles the panel from the public sources listed below.

Sources: Cboe daily history files (VIX, VVIX, VIX3M, COR1M); S&P 500, SPY, DXY, MOVE and BTC-USD daily closes from Yahoo Finance, read from public GitHub mirrors for this offline run; COR1M Oct 28 to Nov 19, 2026 from Cboe's own CSV.
