# Data sources for predicting the open

Research notes from 2026-11-01 on data that might help predict AMZN's opening minute
(`opening_range_features.py`): the opening range (`or_bps`, (high - low) / open over 09:30-09:31)
and the up-move classifier (`up30_gt25`). Nothing here has been bought or built yet.

## What's been tried: Alpaca historical options

- `custom/backtest_utils/alpaca_options_download.py` downloads AMZN options bars into
  `data/options/AMZN/` (111 MB, 2025-01 to 2026-10, 457 days of minute bars).
- `custom/scripts/options_features.py` builds the `opt_*` features: implied moves from ATM IV,
  forward variance and the event ratio between expiries, skew, put/call volume and premium, and
  tick-rule flow. They're hooked into `opening_range_features.py` behind `OPTIONS` (off by
  default).
- Alpaca's history has bars and trades from 2024 on, but no quotes, open interest or greeks, so IV
  comes from bar closes.

Results:
- **Opening range:** implied moves have Spearman 0.20-0.26 with the opening range (2026) and
  0.25-0.32 (2025), below `pre0400_range_bps` (0.33/0.35). Adding them to the base-8 model made
  walk-forward MAE 0.08-0.19 bps worse on 2026. No feature cut MAE by more than 1 SE in both years.
- **Classifier:** `opt_last30_pc_volume` has AUC 0.58 in both years on its own, but adding it lost
  to the base model walk-forward.
- **Earnings is the one real signal.** The six post-earnings opens had `opt_next_event_ratio`
  3.1-4.2 and opening ranges of 61-157 bps (median 56). The model predicted them 1.1-2.2x too low,
  but 6 events are too few to fit.

Implied volatility mostly repeats the realized-volatility features already in the model, so
cleaner options data (real quotes) probably wouldn't change this.

## Databento prices

Exact costs from Databento's free `metadata.get_cost` API, for AMZN only, 2025-01-01 to
2026-10-29. Historical data is pay-per-GB with no subscription, and new accounts get $125 of
credit. The API key is in `.env`.

| Data | What it is | Cost |
|---|---|---|
| `XNAS.ITCH` imbalance | Nasdaq opening-cross imbalance (NOII), 09:25-09:30: paired shares, imbalance size and side, indicative opening price | **$0.40**; back to 2018-05 $1.69; with AAPL/GOOG/MSFT/TSLA $2.02 |
| `GLBX.MDP3` NQ + ES continuous, ohlcv-1m | Market-wide overnight and pre-market moves | $4.68 (ohlcv-1s $161) |
| `OPRA` cbbo-1m | Options 1-minute NBBO quotes (since 2013; cbbo-1s since 2025-02-20) | $60.47 |
| `OPRA` statistics | Includes daily open interest | $57.06 |
| `OPRA` definition | Contract definitions | $1.79 |
| `OPRA` trades / ohlcv-1m | Same as what Alpaca gives free | $709.90 / $503.12 |
| `XNAS.ITCH` mbp-1 / mbp-10 | Nasdaq top of book / 10-level book | $39.93 / $120.61 |
| `XNAS.ITCH` statistics | | ~$0 |

Unit prices per GB: XNAS imbalance $16 historical / $19.2 live; OPRA cbbo-1m $2, trades $280.

Live: OPRA's usage-based live pricing ended in June 2025; subscriptions start at $199/month.
Databento's docs say live Nasdaq TotalView-ITCH needs the Plus or Unlimited plan, so **whether the
live imbalance feed is available on a cheaper plan needs confirming with Databento**.

## Alpaca live options data

- The real OPRA feed (NBBO quotes and real-time trades, `wss://stream.data.alpaca.markets/v1beta1/opra`,
  msgpack, 1000-quote subscription limit) needs Algo Trader Plus and a signed OPRA agreement. This
  account's 10,000 requests/min limit suggests it's already on Algo Trader Plus.
- The free indicative feed has derived quotes and 15-minute-delayed trades.
- IV and greeks come only from chain snapshots (`/v1beta1/options/snapshots`), latest point only;
  they don't stream. Open interest is also latest-only, via the contracts endpoint.
- A forum post reported OPRA lags of 20-40s in Aug 2026.

**Recommendation: don't start collecting it.** A few months is about 60 trading days, which is as
many as the walk-forward needs just for its first fit. Databento's cbbo-1m and statistics cover the
same history now for about $117, and the options results above suggest it wouldn't help anyway. If
wanted, a nightly snapshot collector is cheap to add.

Other options providers, for reference: ThetaData $40/month (since 2020), $80 (since 2016), $160
(since 2012); Massive (formerly Polygon) $29-199/month; ORATS $199-899/month (1-minute data since
2020).

## Other sources, most promising first

1. **Nasdaq opening-cross imbalance (Databento, $0.40).** The only data that describes the opening
   print itself. AMZN opens in Nasdaq's opening auction, and imbalance messages are published from
   09:25. Size should relate to the opening range; side and indicative price to the direction of
   the first 30 seconds, which every feature so far has failed on. Test it historically first; the
   live-access question only matters if it predicts something. Alpaca doesn't seem to stream
   imbalances (it has a REST auctions endpoint with prices after the fact).
2. **More history from Alpaca (free).** SIP AMZN trades go back to at least 2018 (fetched from
   2018-01-02). That adds ~1,700 labeled days to the 457 we have, ~30 earnings events instead of 6,
   and a real test of whether features hold across years, which is where most failed.
   `custom/backtest_utils/alpaca_download.py` already does this download.
3. **Earnings calendar (free).** Earnings were the one real signal; with 2018+ history there'd be
   enough events to fit an adjustment. Past dates and times can come from SEC EDGAR 8-K Item 2.02
   filings or news headlines.
4. **Alpaca news (free).** The account already has Benzinga headlines back to at least 2018. AMZN
   had 22 headlines overnight after the 2025-05-01 report vs 8 on an ordinary day, so an overnight
   headline count is a cheap event feature.
5. **NQ/ES futures ($4.68).** Market-wide overnight volatility, like the peer features, but with
   full history, which would fill the 2025 gap where the peer features are empty.

Suggested order: 1 (spends Databento credit, so needs an OK) and 2 (a long download).

## Done (2026-11-01): 2-4

Full results are in the "MORE HISTORY, EARNINGS AND NEWS" section of `opening_range_features.py`'s
docstring.
- **History:** AMZN's catalog now covers 2022-01-03 to 2026-10-30 (1212 days). The year-to-year level
  shifts hold over all five years, so the opening-range model's 250-day window stays best (MAE 14.86
  bps vs 15.57 expanding).
- **Earnings:** `EARNINGS_DATES` in `opening_range_features.py` is hard-coded from SEC EDGAR 8-K Item
  2.02 filings (`https://data.sec.gov/submissions/CIK0001018724.json`). Every release was after the
  close. Add each new quarter by hand. The 20 post-report opens run a median 2.1x a normal range. The
  `earnings_open` flag cuts their MAE from 39 to 28 bps and helps every year (+0.16 +- 0.11 bps
  overall). Not yet in `opening_range_predictions`.
- **News:** overnight headline counts were tried and removed (the data, downloader and features
  were deleted). The best one, the overnight count of headlines tagged with at most 2 symbols, was
  mostly a proxy for earnings: it adds 0.01 bps on top of `earnings_open`.
- **Classifier:** with five years, `pre0400_range_bps` alone beats the base rate in each of
  2023-2026, the first held-out win for the up-move classifier. News and earnings don't help it.

## Done (2026-11-02): 1, the opening-cross imbalance

Bought AMZN's 2025+ Nasdaq imbalance messages from Databento for $0.40
(`custom/backtest_utils/databento_imbalance_download.py`, saved to `data/imbalance/`). Features are in
`build_imbalance_features`, behind `IMBALANCE` (off by default). They didn't help either label. Full
results are in the "OPENING CROSS IMBALANCE" section of `opening_range_features.py`'s docstring.
- **Opening range:** the auction's size (paired shares, the reference price's range) has Spearman
  0.19-0.31 on its own, but adds at most +0.09 +- 0.10 bps of MAE over the pre-open range features.
- **Classifier:** the imbalance's side is too weak. The best version, the 09:28:30 imbalance (contrarian,
  AUC 0.42-0.45), gains 0.003 +- 0.008 in log loss.
- **So a live NOII feed isn't worth paying for.** The other sources (Webull: live only, from 09:28;
  Interactive Brokers: live only; Massive and Alpaca: none for Nasdaq stocks) don't need pursuing.
- **Side finding:** the label's open (the first trade from 09:30) is a median 4.1 bps from the Nasdaq
  cross, often an odd lot printed first on another venue.
- **More imbalance features** (side flips, the imbalance at 09:29 and 09:29:30) didn't help either.

## Done (2026-11-02): pre-open trades and quotes from the catalog

`micro_stats` / `build_micro_features` in `opening_range_features.py`, behind `MICRO` (off by
default), cached in `data/analysis/<SYMBOL>_opening_micro_stats.parquet`. Covers 2022-2026.
- **Opening range:** the last 5 seconds' trade count over its trailing median gains +0.20 +- 0.08 bps,
  better in all 4 scored years, on the model without the peers. It doesn't help the 2026 model with
  the peers, which still predicts best. It's a candidate for the no-peer fallback in
  `opening_range_predictions`.
- **Spreads, depth, trade sizes:** nothing. Trade sizes looked like they predicted the up-move until
  converted to dollars: the catalog isn't split-adjusted (AMZN split 20:1 in June 2022).

## Sources

- [Databento pricing](https://databento.com/pricing)
- [Databento OPRA pricing plans](https://databento.com/blog/introducing-new-opra-pricing-plans)
- [Databento XNAS.ITCH](https://databento.com/docs/datasets/xnas-itch)
- [Databento imbalance schema](https://databento.com/docs/schemas/imbalance)
- [Nasdaq TotalView-ITCH spec](https://nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf)
- [Alpaca real-time option data](https://docs.alpaca.markets/docs/real-time-option-data)
- [Alpaca forum: options chain frozen](https://forum.alpaca.markets/t/options-chain-frozen/19462)
- [Alpaca forum: auction prices](https://forum.alpaca.markets/t/open-close-daily-bar-prices-vs-open-close-auction-prices-on-primary-exchange/14227)
- [QuantVPS options data comparison](https://www.quantvps.com/blog/options-data-api)
- [QuantVPS historical options data](https://www.quantvps.com/blog/download-historical-options-data)
- [ORATS vs ThetaData](https://orats.com/compare/orats-vs-thetadata)
