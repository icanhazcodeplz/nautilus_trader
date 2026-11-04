# AMZN high-volume days, 2025-2026

Research notes from 2026-11-03: the days in 2025 and 2026 when AMZN traded much more than on the
days around it, and what likely caused each one.

## Summary

68 days qualified. Of these:
- **Earnings:** 16 days (the report day and the reaction day, for 8 reports).
- **Follow-through after a report:** 6 days.
- **Tariffs and trade:** 9 days, nearly all in 2025.
- **Amazon-specific news:** 9 days (deals, an acquisition, a lawsuit, a bond sale, and more).
- **Market-wide selloffs:** 6 days (DeepSeek, a recession scare, AI and tech selloffs).
- **Fed and economic data:** 4 days (jobs reports, Powell and Williams speeches).
- **Comparable companies' earnings:** 4 days.
- **Expiry and index days:** 4 days (quarterly option expiry, S&P rebalances, Russell
  reconstitution).
- **Late June 2026 cluster:** 5 days around Prime Day (2 of them also counted above).
- **No clear catalyst:** 7 days.

Answers to the specific questions:
- **The Fed:** only one Fed decision day made the list, 2026-04-29, and it was also an earnings day.
  Fed-related volume came from speeches (Powell on 2025-09-23, Williams on 2025-11-21) and from jobs
  reports moving rate expectations.
- **Elections:** none in this window. The 2026 midterms are on November 3, after the data ends.
- **War:** US strikes on Iran coincide with 2026-08-31, and war fears with 2026-10-22. Iran strikes
  are also mentioned just before 2026-06-29.
- **Tariffs:** the biggest non-earnings cause, almost entirely in 2025.
- **Comparable companies:** Walmart (2025-05-15), Oracle (2025-09-10), Broadcom (2026-03-05), and
  Meta and Tesla (2026-10-22). Microsoft and Google report on or near Amazon's own dates, so their
  effect is folded into the earnings days. BABA files 6-Ks rather than 8-Ks, so its dates weren't
  checked.

## Method

- **Day volume:** AMZN's whole-session volume from the catalog, 04:00-20:00 (pre-market, regular
  session and after hours, including the closing cross).
- **High-volume day:** volume at least 1.4x the median of the 10 trading days on each side.
- **Market-wide:** SPY's volume (Alpaca SIP daily bars) was also at least 1.3x its own neighbours'
  that day. Otherwise the volume was Amazon's own.
- **Move:** AMZN's close-to-close return.
- **Earnings dates:** SEC EDGAR 8-K Item 2.02 filings, for AMZN (`EARNINGS_DATES` in
  `opening_range_features.py`) and for the peers (AAPL, MSFT, GOOGL, META, NVDA, WMT, ORCL, COST,
  TSLA, SHOP).
- **Causes:** matched from each day's Alpaca/Benzinga AMZN headlines (the previous 16:00 to the
  day's 16:00), the FOMC and option-expiry calendars, peer earnings, and web searches for a few 2026
  days.

## Earnings days (16)

Every report came out after the close, so each shows up twice: the report day and the reaction day.
The reaction day ran 1.8-4.6x normal volume.

| Report day | Reaction day |
|---|---|
| 2025-02-06 | 2025-02-07 |
| 2025-05-01 | 2025-05-02 |
| 2025-07-31 | 2025-08-01 |
| 2025-10-30 | 2025-10-31 |
| 2026-02-05 | 2026-02-06 |
| 2026-04-29 | 2026-04-30 |
| 2026-07-30 | 2026-07-31 |
| 2026-10-29 | 2026-10-30 |

## Follow-through after earnings (6)

| Day | Volume vs. neighbours | AMZN move | What happened |
|---|---|---|---|
| 2025-08-04 | 2.1x | -1.4% | Second day after Q2 2025 earnings |
| 2025-11-03 | 2.5x | +4.0% | AWS-OpenAI $38B deal, the Monday after Q3 earnings; all-time high |
| 2026-02-09 | 1.8x | -0.8% | Slide after the $200B capex guidance (nine down days in a row) |
| 2026-02-12 | 1.5x | -2.2% | Same slide, plus a Nasdaq tech/software selloff (market-wide) |
| 2026-08-03 | 3.0x | +4.6% | Rally after Q2 2026 earnings continues |
| 2026-08-04 | 1.9x | -2.3% | Bezos files to sell $4B of stock; New Jersey attorney general's antitrust suit |

## Tariffs and trade (9)

All market-wide except 2025-05-13.

| Day | Volume | AMZN | Event |
|---|---|---|---|
| 2025-03-04 | 1.5x | -0.6% | Tariffs on Canada, Mexico and China take effect |
| 2025-04-03 | 2.1x | -9.0% | "Liberation Day" tariffs announced the evening before |
| 2025-04-04 | 2.7x | -4.2% | China retaliates; Nasdaq -6% |
| 2025-04-07 | 2.5x | +2.5% | Whipsaw on a false tariff-pause headline |
| 2025-04-08 | 1.8x | -2.6% | 104% tariff on China |
| 2025-04-09 | 2.3x | +12.0% | 90-day tariff pause |
| 2025-05-12 | 2.0x | +8.1% | US-China Geneva truce |
| 2025-05-13 | 1.4x | +1.3% | Truce follow-through and CPI; AWS-Humain $5B Saudi AI deal |
| 2025-10-10 | 1.9x | -5.0% | Trump threatens 100% tariffs on China |

## Market-wide selloffs (6)

| Day | Volume | AMZN | Event |
|---|---|---|---|
| 2025-01-27 | 1.7x | +0.2% | DeepSeek selloff (Nasdaq -3%) |
| 2025-02-25 | 1.4x | 0.0% | Tech selloff before Nvidia's earnings; no Amazon news |
| 2025-03-10 | 1.5x | -2.4% | "Trump recession" scare (Nasdaq -4%) |
| 2025-11-18 | 1.6x | -4.4% | AI-bubble selloff plus a Rothschild downgrade of Amazon |
| 2025-12-15 | 1.5x | -1.6% | AI and tech selloff; no Amazon news |
| 2026-06-05 | 1.6x | -3.1% | Hot May jobs report (172k against ~80k expected) drives rate-hike fears; Nasdaq -4.2% in a chip rout |

## Fed and economic data (4)

All market-wide except 2025-03-07 and 2025-09-23.

| Day | Volume | AMZN | Event |
|---|---|---|---|
| 2025-01-10 | 1.5x | -1.4% | Strong December jobs report sends yields up |
| 2025-03-07 | 1.5x | -0.7% | February jobs report and a Powell speech; Costco earnings reaction |
| 2025-09-23 | 1.7x | -3.0% | Powell says stocks are "fairly highly valued"; the FTC's Prime "dark patterns" trial opens the same day |
| 2025-11-21 | 1.9x | +1.6% | Fed's Williams signals a rate cut; monthly option expiry |

## Comparable companies' earnings (4)

| Day | Volume | AMZN | Event |
|---|---|---|---|
| 2025-05-15 | 2.1x | -2.4% | Walmart earnings (warned of tariff price hikes); Amazon devices job cuts; retail sales and PPI |
| 2025-09-10 | 2.0x | -3.3% | Oracle +36% on its AI-cloud backlog, read as competition for AWS |
| 2026-03-05 | 1.5x | +1.0% | Broadcom earnings; AWS outage that afternoon; Pentagon labels Anthropic a supply-chain risk |
| 2026-10-22 | 1.5x | -4.6% | Meta and Tesla earnings plus war fears; Nasdaq -2.9% (market-wide) |

## Amazon-specific news (9)

Except 2026-08-31, SPY volume was normal on these days, so the volume was Amazon's own.

| Day | Volume | AMZN | Event |
|---|---|---|---|
| 2025-06-05 | 1.5x | +0.3% | AWS GPU price cuts and a $10B North Carolina data center plan, on the day of the Trump-Musk feud |
| 2025-08-06 | 1.6x | +4.0% | Likely: AWS starts offering OpenAI's open-weight models (from memory, not in the headlines); Shopify earnings the same day |
| 2025-08-14 | 1.9x | +2.9% | Grocery delivery expansion and JPMorgan's "best idea" call (both from the day before). Unconfirmed |
| 2025-11-17 | 1.8x | -0.8% | $15B bond sale, Amazon's first in three years |
| 2026-04-09 | 1.6x | +5.6% | Jassy's shareholder letter: AWS AI revenue run rate over $15B |
| 2026-04-14 | 1.8x | +3.8% | Amazon to acquire Globalstar at $90/share |
| 2026-06-01 | 1.4x | -3.5% | Blue Origin rocket explosion (Amazon's Leo satellites); Anthropic files for an IPO |
| 2026-08-31 | 1.4x | -2.5% | WSJ: FTC to sue Amazon over ad pricing; the same day the US struck Iran and oil rose |
| 2026-10-19 | 1.5x | +5.0% | Nuclear plant deal (960 MW), Trainium chips opened to PyTorch, Morgan Stanley price-target raise |

## Expiry and index days (4)

| Day | Volume | AMZN | Event |
|---|---|---|---|
| 2025-06-20 | 1.4x | -1.3% | June quarterly expiry and S&P rebalance, the day after FOMC |
| 2025-06-27 | 2.2x | +2.9% | Russell reconstitution |
| 2025-12-19 | 1.6x | +0.3% | December quarterly expiry, S&P rebalance and Nasdaq-100 reconstitution; BOJ hike |
| 2026-06-26 | 2.2x | +2.5% | Russell reconstitution |

## Late June 2026 (5 days, 06-24 to 06-30)

- Prime Day (a record $26.4B in sales), Anthropic's Trainium deal, Cerebras-AWS.
- 06-25: -3.1% on "cloud worries"; Micron earnings and PCE; a $48B India investment.
- 06-26: Russell reconstitution (in the table above); capex concerns and insider sales.
- 06-29: +3.2% on the Prime Day record; a Trump threat of 100% tariffs on countries that tax US tech
  companies; US strikes on Iran over the weekend before.
- 06-30: quarter end; an ACCC (Australia) lawsuit; an AWS price increase.

## No clear catalyst (7)

- 2025-06-26: day before the Russell reconstitution; Jassy's AI-workforce comments; Bezos wedding.
- 2025-07-11: last day of Prime Day; analyst price-target raises.
- 2025-09-04: +4.3%; a Quick Suite AI agent report, but the cause is unconfirmed.
- 2026-01-02: first trading day of the year.
- 2026-01-06: +3.4% breakout on a record S&P day; only a weak, auto-generated source.
- 2026-06-24: start of the Prime Day cluster above.
- 2026-08-28: +4.0%; a Claude revenue headline, otherwise unclear.

## How solid these causes are

- **Solid:** the earnings and peer-earnings dates (SEC EDGAR filings), the calendar dates (FOMC,
  expiry, index events) and the volume ratios (the catalog and Alpaca's daily bars).
- **Matched by hand:** the other causes, from each day's headlines plus web searches for a few 2026
  days. A headline on the day doesn't prove it caused the volume.
- **Weakest:** 2025-08-06, 2025-08-14, the late-June 2026 cluster and the seven no-catalyst days.

## Possible use

Most non-earnings high-volume days are either market-wide (tariffs, selloffs, data) or announced
only during the day, so a pre-open feature can't see them. The ones known in advance are the
calendar events: quarterly expiry, index rebalances and reconstitutions, and FOMC days. These could
be tried as a hard-coded calendar flag next to `earnings_open`, though there are only a handful per
year.

Tried 2026-11-03 in `opening_range_features.py`: the tariff days are now dropped from its dataset
(`TARIFF_DAYS`). Flags for quarterly expiry and S&P rebalances, Russell reconstitution, the opens
after BABA, WMT, GOOG and MSFT earnings, and one flag (`event_day`) for quarterly expiry or the
sessions around AMZN's reports didn't help the opening-range model, and neither did leaving those
days out of training. All were removed; `earnings_open` stays the only event flag. Results are in
the script's docstring.

## Sources

- [Briefing.com, Feb 12 2026](https://briefing.com/stock-market-update/archive/2026/2/12/market-moves-sharply-lower)
- [IG: Amazon losing streak](https://www.ig.com/en/news-and-trade-ideas/us-stocks-end-week-lower-as-amazon-losing-streak-extends-260216)
- [Kitco, June 5 2026](https://www.kitco.com/news/off-the-wire/2026-06-05/nasdaq-sp-futures-decline-semiconductors-slip-payrolls-focus)
- [Invezz, June 5 2026](https://invezz.com/ca/news/2026/06/05/dow-tumbles-680-points-as-chip-rout-sends-nasdaq-to-biggest-drop-since-2025/)
- [Tapeboard, June 5 2026](https://tapeboard.com/blog/market-pulse-2026-06-05)
- [StockInvest, June 25 2026](https://stockinvest.us/stock-news/red-day-on-thursday-for-amazon-stock-after-losing-310-2026-06-25)
- [TS2: Amazon sheds $67B on cloud worries](https://ts2.tech/en/amazon-com-nasdaqamzn-sheds-67-billion-on-cloud-worries-taking-shine-off-prime-day-numbers/)
- [FinancialContent, Jan 6 2026](https://markets.financialcontent.com/stocks/article/marketminute-2026-1-6-amazons-january-breakout-propels-s-and-p-500-to-record-highs-amid-consumer-resilience)
  (reads auto-generated; not relied on)
- SEC EDGAR company submissions (`https://data.sec.gov/submissions/CIK##########.json`) for the
  earnings dates
- Alpaca `/v1beta1/news` (Benzinga headlines) and `/v2/stocks/bars` (SIP daily bars)
