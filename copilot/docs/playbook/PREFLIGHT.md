# Preflight

One-time setup, before any broker integration work. Most of this is outside the repository
and none of it is code.

Everything here is verified with the broker rather than assumed from documentation,
including this document. Rules change; a stale preflight is worse than none because it
reads as confirmation.

## Account

- **Broker entity.** Confirm which IBKR entity carries the account, which US products are
  available on it, and whether API trading is permitted.
- **Account type.** Prefer cash or otherwise unlevered to start. Size from **settled USD
  cash actually available** after commissions, fees, pending buy orders and any required
  currency conversion - not from headline equity. Confirm with the carrying entity how
  settlement, buying power, same-day sale and reinvestment, and automatic FX conversion
  apply to the real account.

  **This account is cash**, as of 2026-09-01, pending a margin decision. Two consequences
  follow and neither is a preference:

  - **No short sales.** Reg T places short sales in a margin account. The gap fade's short
    leg (`long = false`) is therefore unavailable at any price, and only the long leg can
    be activated. All three registered activations are already long-only, so nothing
    currently registered is blocked.
  - **Settled cash, not equity, is the sizing base**, and concurrent positions are capped
    by it. Good-faith violations need an intraday round trip, which an entry at the close
    cannot produce - but that is a property of the current entry rule, not of the account,
    and it stops holding if entry moves.
- **Intraday margin rules.** Do not hard-code pattern-day-trader assumptions. FINRA has a
  newer intraday-margin framework with a phase-in, and broker implementation timing varies.
  **Verify the rules applied to this account** rather than relying on a summary, including
  the one in this sentence.
- **Settlement.** Most applicable US securities settle T+1, so cash and securities settle
  one business day after the trade.
- **Permissions.** Confirm US stock and ETF trading permissions before paper integration
  testing.
- **Fractional shares.** Verify current API, instrument, order-type and account support
  before relying on them. The fallback is whole-share sizing and skipping trades that round
  to zero. Fractions materially change small-account viability by removing the
  rounds-to-zero failure, but they do **not** remove the per-order commission minimum,
  which is the term that actually dominates - see [`RISK.md`](RISK.md).

## Market data

Confirm for **every** field used in research and live whether it is real-time, delayed,
frozen, consolidated or non-consolidated.

**Subscriptions can be gated on account equity.** As of 2026-09-01 this account sits below
IBKR's bar, funds have been added, and settlement is expected on or after 2026-09-08. Until
it clears, the only US equity quotes available are the complimentary delayed,
non-consolidated feed - which is enough for broker-integration testing and **not** enough to
set a cost coefficient anyone should trade on.

**Top of book is the requirement; depth is not.** Both candidate entry mechanics are
auctions - market-on-close today, next-session-open under the charter - and an auction
clears at a single price rather than against the continuous book, so depth describes
something the execution model never touches. Position sizes are low single-digit to low
tens of shares against inside quotes of hundreds to thousands, so no order can walk a book.
The open question is **consolidated versus non-consolidated at L1**, not L1 versus L2.

IBKR Securities Japan describes its complimentary US stock and ETF feed as real-time but
**non-consolidated**. A non-consolidated feed is not proof of the consolidated best bid,
offer or spread. Either subscribe to an appropriate feed, or make the execution model and
order limits conservative enough for the feed actually in use.

What this fork has measured, which is a working example of why the distinction matters:

- Delayed US equity quotes work across the universe after completing the market-data
  release forms. Realtime quotes and historical bars still return nothing and IB 2188
  respectively.
- Spread calibration therefore runs on **delayed** quotes, which is an upper bound on the
  realtime NBBO. Conservative in the right direction, and labelled as such in every record
  the calibrator writes.
- Historical US equity bars are not reachable through IB at all on this entitlement, which
  is why the research catalog comes from another vendor.

Full detail in [`../ROADMAP.md`](../ROADMAP.md).

## The API interface itself

Confirmed by a failed run on 2026-09-01, not from documentation.

- **Read-Only API.** TWS has a *Read-Only API* setting (Global Configuration, API,
  Settings). With it enabled the execution client fails to connect with IB **321**, and the
  only visible consequence is that no account and no balances ever reach the cache - the
  failure text names read-only mode, but the checks that fail are two steps downstream of
  it. Read-only is the right setting while orders are disabled and the wrong one from the
  moment an account needs confirming.
- **The account is not on the instrument's venue.** Instruments resolve on `SMART`; the
  execution client registers its account under its own client name, giving `IB-DUT067974`.
  A venue-keyed account lookup must search both or it reports a missing account that is in
  the cache.
- **The paper login name and account id are the same string** on this account
  (`DUT067974`). Settled by observation, not assumed - IB does not require them to match.
- **The paper account is `MARGIN` while the live account is cash.** Paper will accept a
  short sale and size against buying power; the live cash account will do neither. A paper
  pass is not evidence about the cash constraints.
- **Paper does not enforce buying power on a far-from-market limit.** Measured at stage
  six: IB paper accepted a 100,000-share order worth USD 24M on a USD 1M account. A
  rejection path tested only on paper has not been tested.
- **The paper balance is USD 1,000,000**, three orders of magnitude above the target
  account. Size from the configured risk budget, never from reported equity.
- **Instrument identifiers differ between research and the broker.** The catalog names an
  instrument by MIC venue, `AAPL.XNAS`; the IB adapter resolves `AAPL=STK.SMART` under
  `SymbologyMethod.RAW` and reports the venue as `SMART`. An instrument id that a
  walk-forward scored is therefore **not** an instrument id that can be traded, and nothing
  currently maps between them. Confirm the broker-side form before assuming an activation
  can reach an order.

## Session and connection

- **One session per login.** Opening Client Portal, Account Management or the mobile app
  while a run is live can displace the API's historical data service and produce error 162
  while the socket stays connected and contract resolution keeps working. An unattended
  account is a **dedicated** account.
- **TWS reports its clock in local time.** On a JST host, `IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"`
  is required in every process or connections fail with a generic error that hides the
  cause.
- Separate paper and live **accounts, credentials, API client IDs, secret stores,
  configuration files, risk limits, deployment environments and alert destinations.**
  Validate the account identifier at startup and redact it from shared artifacts.
- Never commit credentials or write them to logs. Secrets come from the environment at the
  process boundary and are passed down as arguments, so no module below can acquire a way
  to write one to disk.

## Currency, tax and records

- Keep an explicit JPY and USD cash ledger. **Separate strategy return from USD/JPY
  translation.** A strategy that made USD and lost JPY has not been measured until these
  are split.
- Retain executions, commissions, dividends, withholding, FX conversions, corporate
  actions, monthly and annual statements, and configuration history.
- Obtain Japan-specific advice on foreign securities, dividends, capital gains, FX
  translation, loss treatment and reporting. **The trading engine is not the tax ledger**
  and should not be made into one.

## Checklist

- [ ] Carrying entity, product availability and API permission confirmed
- [ ] Account type, settlement and buying-power treatment confirmed **with the broker**
- [ ] Intraday-margin rules as applied to this account confirmed, not assumed
- [ ] US stock and ETF permissions active
- [ ] Every data field classified: real-time or delayed, consolidated or not
- [ ] Execution model conservative enough for the feed actually in use
- [ ] Paper and live separated across all eight dimensions above
- [ ] Credentials in the environment, absent from source control and logs
- [ ] JPY and USD ledgers separated
- [ ] Record retention and tax advice arranged
