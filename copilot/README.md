# copilot — downstream overlay

Fork-local capability that NautilusTrader does not ship, carried in one directory so
this fork stays easy to keep current with upstream.

## Why a separate directory

This fork tracks `nautechsystems/nautilus_trader`. Every file we change upstream is a
file that can conflict on the next merge, so the overlay lives entirely under
`copilot/`, a path upstream will never create.

**As of the current state, this overlay changes zero upstream files.** A
`git merge upstream/develop` should never conflict with anything here.

The one change that *would* touch upstream is deferred and recorded in
[`docs/ROADMAP.md`](docs/ROADMAP.md): exposing `RiskEngine.set_trading_state` through
pyo3. It needs a Rust toolchain and a full workspace build, which this environment
does not have.

`trade-copilot/` is excluded from git via `.git/info/exclude` — local only, so it adds
no diff against upstream. Verify with `git status` before any commit.

## For agents

[`AGENTS.md`](AGENTS.md) in this directory records every departure from the repository's
root process, with the reasoning for each. Read it alongside the root `AGENTS.md` — it
supplements those rules rather than replacing them.

## What is here

| Path | Purpose |
| --- | --- |
| `calibration/` | Measure real quoted spreads from IB to calibrate the backtest cost model |
| `data/` | Marketstack EOD ingestion, a US trading calendar, and the Nautilus catalog bridge |
| `risk/` | Account-wide rolling-window circuit breakers (consecutive stops, drawdown) |
| `validation/` | Types and a Nautilus-backed `Replay` for the walk-forward gate |
| `tests/` | Tests for all of the above |
| `docs/` | **`ROADMAP.md` is the central record** — kill chain, open work, and the detail behind both. Plus the changelog and fusion plan |
| `AGENTS.md` | Fork-local process departures and their reasoning |
| `ruff.toml` | Lint config, scoped here so upstream files stay untouched |

## Provenance

`risk/protections.py`, `validation/types.py` and `data/marketstack.py` are ported from the author's
`trade-copilot` project (a HITL trading-signal advisor). They are **ported, not
imported**: trade-copilot is a separate repository, and the point of the port is that
this overlay stands alone. Design rationale is preserved in the docstrings and
attributed to the original ADRs.

## Running

Requires the `nautilus_trader` wheel and an environment variable that the IB adapter
needs on this host:

```bash
# TWS reports its clock in JST, which the Rust ibapi crate has no alias for; without
# this every IB connection fails with a generic "Failed to connect".
export IBAPI_TIMEZONE_ALIASES="JST=Asia/Tokyo"
export IB_V2_HOST=172.17.112.1 IB_V2_PORT=7497

# Tests
PYTHONPATH=. python -m pytest copilot/tests/ -q

# Spread calibration (read-only; constructs no execution client)
python -m copilot.calibration.spread_snapshot

# Backfill US equity history into a Nautilus catalog (read-only; writes files)
export MARKETSTACK_API_KEY=...
python -m copilot.data.backfill --symbols AAPL,MSFT,SPY --from 2005-01-01 --dry-run
```

The catalog defaults to `~/.nautilus_copilot/catalog`, outside the repository, and is
overridable with `COPILOT_CATALOG_PATH` or `--catalog`. Run `--dry-run` first: it
fetches and gates without writing, so the rejection report can be read before anything
lands on disk.

Environment knobs for the calibrator: `COPILOT_CAL_SYMBOLS`, `COPILOT_CAL_SECONDS`,
`COPILOT_CAL_CLIENT_ID`, `COPILOT_CAL_MARKET_DATA_TYPE`, `COPILOT_CAL_SYMBOLOGY`.
