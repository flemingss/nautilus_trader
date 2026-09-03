# 15. Databento is the intraday source, and only the intraday source

- **Status:** Accepted
- **Date:** 2026-09-03
- **Deciders:** Project owner; shape proposed in-session and revised twice against
  measurement

## Context

Two open items needed a provider decision: intraday history, deferred under "waiting on
spend" until the system earned its cost, and whether to buy consolidated US equity data
at all. A survey ran across Databento, EODHD, FirstRate Data, Norgate, Sharadar and
Massive (formerly Polygon), against four gaps the catalog does not cover:

- **A.** Twenty years of daily EOD, 2005-2025.
- **B.** Splits and dividends.
- **C.** True one-minute bars from a consolidated feed, with an official close.
- **D.** Point-in-time index membership including delisted securities.

The survey's first conclusion was wrong and is recorded here because the correction is
the substance of the decision. Databento was read as a near-free replacement for the
whole daily stack, on the strength of usage-based billing at roughly $0.40/GB against
$125 of signup credits. **Its US equities history begins around 2018-05**; the vendor's
own launch material describes seven years of OHLCV. There is no 2005 history at any
price, and Databento sells no corporate actions and no index membership.

Marketstack, meanwhile, had already been measured and found to be two different vendors
in one subscription: `/splits` and `/dividends` are sound, and independently confirmed
the hand-maintained `SPLITS` table in `cost_model.py` for 2005-2025. Its **intraday
endpoint returns rows shaped like bars that are not bars** - one distinct value across
200 rows for each of open, high, low and close, and a volume field cumulative since the
open, summing to 665% of the day's reported total. Read as minute bars, that hands a
backtest the day's closing price at 09:31.

## Decision

**Databento is taken for intraday, and nothing else. Marketstack is not cancelled.**

- **Databento closes gap C alone**, from roughly 2018 forward, on usage-based billing
  with no dataset subscription: a key reaches every historical dataset and the meter
  runs on uncompressed bytes delivered. Twenty symbols of `ohlcv-1m` prices in the low
  single dollars against the credits, which expire six months after signup.
- **A monthly historical spend limit is set in the vendor portal**, because billing
  follows bytes returned and a single careless full-depth query is the entire risk
  surface. `copilot/data/databento.py` reinforces this: metadata calls are free and
  the pull that spends refuses to run without `--spend`.
- **No intraday feed is trusted before it is measured.** The checks that caught the
  previous vendor - distinct values per price field, volume monotonicity, summed volume
  against the daily total, and OHLC coherence - run as `--probe` and are covered by
  tests that replay the exact failing payload.
- **Databento is also the audit instrument.** Its 2018-2025 daily series is the first
  independent check available on seven of the catalog's twenty years.
- **`ohlcv-1d` is not the official close.** It is trade-derived; the `statistics`
  schema carries official daily values. This matters directly, because [ADR-0013]
  restricts a holdout spend to a `next_close` activation.
- **Gap A and gap B stay with Marketstack for now.** Nothing surveyed under
  professional pricing replaces the 2005-2018 daily series, so the subscription is not
  cancelled on the strength of a vendor that cannot reproduce it. EODHD is the
  candidate replacement at roughly $30/month for 30+ years, corporate actions and
  delisted securities, and it inherits the same rule as Databento: it is not adopted
  until it passes the coherence probe.
- **Gap D stays open and priced.** Norgate Platinum at USD 630/year is the only
  verified source of true point-in-time membership for the S&P 500 and Russell 3000.
  It is deferred until the universe correction actually starts, not rejected.
- **FirstRate Data is not bought.** It fits the one-time-purchase shape, but its price
  is unpublished, its one-minute bars are aggregated from major exchanges plus four
  dark pools rather than the consolidated tape, and its licence does not clearly grant
  perpetual use of downloaded files after cancellation. Two unknowns and a
  non-consolidated tape is not a basis for a purchase.

**The catalog is a backup obligation, not a cache.** Because no live subscription can
reproduce 2005-2018, `~/.nautilus_copilot/catalog` holds the only copy of thirteen
irreplaceable years. It is backed up off the WSL filesystem with a per-file SHA-256
manifest, verified by extracting the archive and comparing hashes rather than by
trusting the write. It is **not** committed: this repository is public and the bars are
licensed vendor data.

## Consequences

- **Cash versus margin does not move any of this.** The transition would unlock the gap
  fade's short leg, which needs the same daily bars and the same corporate actions. No
  purchase should be timed to it.
- **Monthly spend is unchanged** at roughly $50, against a plan that briefly claimed
  $0. Databento adds usage-based cost near zero; the saving depends on the EODHD
  decision, which is now a tracked open item rather than a foregone one.
- **Intraday research is unblocked for 2018 forward only.** Any intraday premise
  evaluated over a window starting before 2018 has no data behind it, which constrains
  the [ADR-0013] revisit.
- **A vendor's pricing page is not evidence.** Both material findings here - the fake
  bars and the 2018 history floor - contradicted the marketing surface and came from
  measurement and documentation respectively. The probe exists so the next vendor is
  cheap to check.
