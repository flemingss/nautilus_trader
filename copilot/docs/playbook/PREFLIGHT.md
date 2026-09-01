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
