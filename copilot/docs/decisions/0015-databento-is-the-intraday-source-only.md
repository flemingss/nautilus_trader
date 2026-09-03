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

- **Databento closes gap C alone**, on usage-based billing with no dataset
  subscription: a key reaches every historical dataset and the meter runs on
  uncompressed bytes delivered. Credits expire six months after signup.

  **How far back depends on what is meant by "consolidated", and the survey run on
  2026-09-03 answers it. Documentation does not.**

  | Dataset        | From       | Shape        | `ohlcv-1m` | Volume vs the tape        |
  | -------------- | ---------- | ------------ | ---------- | ------------------------- |
  | `ARCX.PILLAR`  | 2018-05-01 | one venue    | yes        | one venue's share         |
  | `XNAS.ITCH`    | 2018-05-01 | one venue    | yes        | ~38% (AAPL, measured)     |
  | `XNYS.PILLAR`  | 2018-05-01 | one venue    | yes        | one venue's share         |
  | `DBEQ.BASIC`   | 2023-03-28 | multi-venue  | yes        | one row per venue per day |
  | `EQUS.MINI`    | 2023-03-28 | composite    | yes        | ~3%, measured             |
  | `EQUS.SUMMARY` | 2024-07-01 | consolidated | **no**     | ~100%, measured           |

  So the earlier claim that intraday reaches 2018 holds **only for single-venue feeds**.
  Consolidated one-minute bars begin 2023-03-28, and the fully consolidated daily
  series begins 2024-07-01. `EQUS.MINI` is the module default: the deepest dataset
  serving one-minute bars off a composite. Its prices track the tape and **its volume
  does not**, so a premise with a volume filter takes `XNAS.ITCH`'s honest single-venue
  count and its 2018 depth instead.

- **Measured prices, 20 symbols, 2026-09-03.** `ohlcv-1d` for the whole audit window
  costs **$0.06**; consolidated `ohlcv-1m` from 2023-03 costs **$3.74**; single-venue
  `ohlcv-1m` from 2018-05 costs **$12.62**. The entire programme is roughly $16 against
  $125 of credits.

- **One query shape is a trap, and it is the reason the spend limit is not optional.**
  `statistics` on `EQUS.SUMMARY` for those same 20 symbols over 18 months prices at
  **$1,246** - ten times the signup credit, differing from a $0.01 query only by the
  schema name. The same schema on `XNAS.ITCH` over 7.6 years costs $0.03. Price every
  new query shape with `--cost` before running it.
- **The vendor portal cap is USD 100 per month, warning at 90%** (set 2026-09-03),
  because billing follows bytes returned and a single careless full-depth query is the
  entire risk surface. It is a backstop, not a budget: the planned programme is roughly
  $16 against $125 of credits, so **reaching the cap is a defect signal, not a sign the
  work grew**, and the query shape that caused it is found before the number is raised.
  `copilot/data/databento.py` reinforces the same discipline in code: metadata calls
  are free, `--cost` prices any shape before it runs, and the pull that spends refuses
  to run without `--spend`.
- **No intraday feed is trusted before it is measured.** The checks that caught the
  previous vendor - distinct values per price field, volume monotonicity, summed volume
  against the daily total, and OHLC coherence - run as `--probe` and are covered by
  tests that replay the exact failing payload.
- **Databento is the audit instrument, and the audit has already been run.** Executed
  2026-09-03 for $0.09 total, against each listing venue's **official closing auction
  print** (`statistics`, stat type 11) rather than a trade-derived bar, because the
  venue that lists a security runs the auction that sets its official close.
  **38,523 symbol-days compared, 2018-05-01 to 2025-12-31.**

  | Disagreement                          | Days   | Share       |
  | ------------------------------------- | ------ | ----------- |
  | Split adjustment (AAPL 4:1, expected) | 588    | 1.53%       |
  | Under 1 bps, representation only      | 166    | 0.43%       |
  | 1-10 bps                              | 20     | 0.05%       |
  | **Over 10 bps, materially wrong**     | **17** | **0.044%**  |

  So **Marketstack's closes are right 99.96% of the time** on material terms, which is
  the first independent evidence the catalog can carry a verdict at all. The 17 bad
  closes are real: PEP 2024-12-05 reads 159.1350 against an official 160.49, and an
  independent third source confirms **160.49 to the cent**. Three of the worst carry
  sub-penny values (19.7050, 177.9450, 159.1350) that no closing auction print can
  have, which is a cheap detector for the class.
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

## What the audit found that nothing else would have

**Four symbols carry an unadjusted split as fact.** The catalog's own bars move
**AMZN -94.9%** (2022-06-06), **GOOGL -95.1%** (2022-07-18), **WMT -66.1%**
(2024-02-26) and **KO -50.1%** (2012-08-13) in a single day. `marketstack.py` asserted
in its module docstring that this "does not exist in the raw series", generalising from
AAPL, which *is* adjusted. That claim is now corrected in place.

A gap strategy reads a -95% day as the largest gap in its history, so **any verdict
over those four names would have been invalid**. The three names with filed verdicts -
AAPL, MSFT, SPY - are unaffected, which is luck rather than design: the defect sits in
the catalog waiting for the universe expansion that [ADR-0009]'s sweep and the
survivor-bias correction both require. Repair is tracked as its own work; the vendor's
`/splits` endpoint carries the factors and `split_factor` is already stored per row.

This is the argument for the audit in one line: **$0.09 of metered data found a defect
that would have silently corrupted every verdict on a widened universe.**

## Consequences

- **Cash versus margin does not move any of this.** The transition would unlock the gap
  fade's short leg, which needs the same daily bars and the same corporate actions. No
  purchase should be timed to it.
- **Monthly spend is unchanged** at roughly $50, against a plan that briefly claimed
  $0. Databento adds usage-based cost near zero; the saving depends on the EODHD
  decision, which is now a tracked open item rather than a foregone one.
- **Intraday research is unblocked for 2023 forward on a composite, 2018 forward on a
  single venue.** Neither reaches the development window's start, so the [ADR-0013]
  revisit cannot be evaluated over the same history as the daily gate. An intraday
  premise is testable on recent regimes only, and must be reported that way.
- **The fidelity probe passed on first contact.** AAPL on `EQUS.MINI`, 200 rows: 117-130
  distinct values per price field, volume non-monotonic, zero incoherent rows. The
  previous vendor failed the same measurement with one distinct value per field.
- **A vendor's pricing page is not evidence.** Both material findings here - the fake
  bars and the 2018 history floor - contradicted the marketing surface and came from
  measurement and documentation respectively. The probe exists so the next vendor is
  cheap to check.
