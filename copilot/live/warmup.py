"""
Warm a live strategy's indicators from the catalog, or refuse and say why.

A live node has no history. ``GapReversalStrategy.on_start`` subscribes to bars and does
nothing else, so an indicator fed only by the subscription needs ``WARMUP_BARS`` real
sessions before it initialises - sixteen trading days during which the strategy runs,
logs, and cannot fire. That reads as "no setups triggered" rather than as a defect, which
is the worst shape a gap of this kind can take.

The catalog is the source, and reading it is possible at all because the evaluation
window is pinned at both ends ([ADR-0017]): research scores a frozen span while the file
itself is kept current, so one series feeds the backtest and the live warm-up. Warming
from anywhere else would put a second series behind the live decisions and make the
replay comparison the playbook requires a reconciliation instead of a comparison.

What is checked, and why it is one check
----------------------------------------
The warm-up asks for the ``count`` sessions immediately before the first live one, and
compares the dates it got against the dates the exchange calendar says those are. That
single comparison catches both ways this can go wrong, and they fail identically if the
comparison is skipped:

- **Stale.** The catalog ends weeks back, so ``_previous_close`` is a close from a
  different regime. The gap rule compares today's open against it and measures a gap that
  is mostly the intervening drift - a signal manufactured by the data being old.
- **Holed.** The catalog is current but a session is missing, as it is whenever a vendor
  returns a null close and the ingest gate rightly refuses the batch. The tail then steps
  over the hole and ``_previous_close`` is two sessions old, which is the stale failure
  again, smaller and much harder to see.

Neither is repairable here, so neither is repaired here: the warm-up raises,
:func:`report` says which sessions are missing, and the operator fixes the catalog before
the session rather than discovering it in a fill.

Refusal is the safe direction because the alternative is not "no trades". A strategy that
warms from a hole still trades; it trades on a gap that did not happen.

[ADR-0017]: ../docs/decisions/0017-the-evaluation-window-is-pinned-at-both-ends.md

"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from typing import TYPE_CHECKING

from copilot.data.calendar import EASTERN
from copilot.data.calendar import is_trading_day
from copilot.data.calendar import session_open
from copilot.data.calendar import trading_days
from copilot.data.catalog import bar_type_for
from copilot.data.catalog import equity_for
from copilot.data.catalog import open_catalog
from copilot.data.catalog import read_daily_bars
from copilot.data.catalog import to_nautilus_bars
from copilot.strategies.activations import load_activations
from nautilus_trader.model import Equity


if TYPE_CHECKING:
    from collections.abc import Sequence

    from copilot.strategies.activations import Activation
    from copilot.validation.types import DailyBar
    from nautilus_trader.model import Bar


CATALOG_PATH_ENV = "COPILOT_CATALOG_PATH"
DEFAULT_CATALOG = "~/.nautilus_copilot/catalog"

MISSING_SESSIONS_SHOWN = 5
"""
Missing sessions named in full before the list is elided.

Enough to see the shape of the gap - one session is a vendor hole, a run of them is a
stale catalog - without printing three weeks of dates into an error message.

"""

CALENDAR_MARGIN_DAYS = 30
"""
Calendar days added to the session lookback before asking the exchange calendar.

Sixteen sessions span at least twenty-two calendar days and more across a holiday week.
The margin is generous on purpose: this only widens a read that is then sliced to the
exact session count, so being wrong on the low side would silently shorten a warm-up
while being wrong on the high side costs a few rows.

"""


class WarmupUnavailableError(ValueError):
    """
    The catalog cannot warm this strategy for this session.
    """


@dataclass(frozen=True)
class WarmupWindow:
    """
    The sessions a warm-up needs, and what the catalog actually holds for them.
    """

    first_session: date
    expected: tuple[date, ...]
    present: tuple[date, ...]
    newest_held: date | None = None
    """
    The newest session in the whole catalog, filled in only when the window is short.

    The fast path reads the window and nothing else, so a catalog that stops before the
    window returns no rows and can say nothing useful about itself. That is precisely
    the stale case, and "no bars in the window" is the least helpful thing to tell an
    operator who needs to know how far back the catalog actually stops. So the diagnosis
    pays for a second, unbounded read that the ready path never makes.

    """

    @property
    def missing(self) -> tuple[date, ...]:
        """
        Return the expected sessions the catalog does not hold.
        """
        held = set(self.present)
        return tuple(day for day in self.expected if day not in held)

    @property
    def ready(self) -> bool:
        """
        Return whether every expected session is present.
        """
        return not self.missing


def expected_sessions(first_session: date, count: int) -> tuple[date, ...]:
    """
    Return the ``count`` trading sessions immediately before ``first_session``.

    ``first_session`` is excluded: it is the session being traded, and its bar has not
    closed yet. Warming on it would feed the strategy the close it is about to decide
    against.

    """
    if count < 1:
        raise ValueError(f"count must be positive, got {count}")
    lookback = timedelta(days=2 * count + CALENDAR_MARGIN_DAYS)
    sessions = trading_days(first_session - lookback, first_session - timedelta(days=1))
    if len(sessions) < count:
        raise ValueError(
            f"the calendar returned {len(sessions)} sessions before {first_session}, "
            f"fewer than the {count} asked for. Widen CALENDAR_MARGIN_DAYS.",
        )
    return sessions[-count:]


def inspect(
    catalog_path: str,
    activation: Activation,
    *,
    first_session: date,
    count: int | None = None,
) -> WarmupWindow:
    """
    Report what the catalog holds for one activation's warm-up, without raising.

    Separate from :func:`load` so an operator can ask "is the catalog ready for
    tomorrow" without building a node, and so the answer names the missing sessions
    rather than only that some are missing.

    """
    count = activation.setup.warmup_bars if count is None else count
    expected = expected_sessions(first_session, count)
    instrument = equity_for(activation.symbol, activation.venue)
    bars = _tail(catalog_path, instrument, first_session, expected[0], count)
    return _window(catalog_path, instrument, first_session, expected, bars)


def load(
    catalog_path: str,
    activation: Activation,
    *,
    first_session: date,
    count: int | None = None,
) -> tuple[Bar, ...]:
    """
    Return the warm-up bars for one activation, or raise naming what is missing.

    Bars come back corporate-action adjusted ([ADR-0016]) and converted the same way the
    replay converts them, so an indicator warmed here holds the value it would hold after
    a backtest over the same sessions.

    [ADR-0016]: ../docs/decisions/0016-corporate-actions-are-applied-on-read.md

    """
    count = activation.setup.warmup_bars if count is None else count
    expected = expected_sessions(first_session, count)
    instrument = equity_for(activation.symbol, activation.venue)
    bar_type = bar_type_for(instrument.id)
    bars = _tail(catalog_path, instrument, first_session, expected[0], count)
    window = _window(catalog_path, instrument, first_session, expected, bars)
    if not window.ready:
        missing = window.missing
        newest = window.newest_held.isoformat() if window.newest_held else "nothing held"
        raise WarmupUnavailableError(
            f"{activation.name}: the catalog cannot warm {count} sessions before "
            f"{first_session}. Missing {len(missing)} of them "
            f"({', '.join(d.isoformat() for d in missing[:MISSING_SESSIONS_SHOWN])}"
            f"{', ...' if len(missing) > MISSING_SESSIONS_SHOWN else ''}); "
            f"newest session held is {newest}. "
            f"Backfill before the session rather than warming from a hole: "
            f"python -m copilot.data.backfill --symbols {activation.symbol} "
            f"--from {expected[0].isoformat()}",
        )
    return tuple(to_nautilus_bars(bars, instrument, bar_type))


def _window(
    catalog_path: str,
    instrument: Equity,
    first_session: date,
    expected: tuple[date, ...],
    bars: Sequence[DailyBar],
) -> WarmupWindow:
    """
    Assemble the window, diagnosing the catalog's extent only when something is missing.
    """
    window = WarmupWindow(
        first_session=first_session,
        expected=expected,
        present=tuple(bar.closed_at.date() for bar in bars),
    )
    if window.ready:
        return window
    return WarmupWindow(
        first_session=window.first_session,
        expected=window.expected,
        present=window.present,
        newest_held=_newest_session(catalog_path, instrument),
    )


def _newest_session(catalog_path: str, instrument: Equity) -> date | None:
    """
    Return the newest session anywhere in the catalog for this instrument, or None.
    """
    stored = read_daily_bars(open_catalog(catalog_path), bar_type_for(instrument.id))
    if not stored:
        return None
    return max(bar.closed_at.date() for bar in stored)


def _tail(
    catalog_path: str,
    instrument: Equity,
    first_session: date,
    earliest: date,
    count: int,
) -> tuple[DailyBar, ...]:
    """
    Read the catalog's last ``count`` bars closing before ``first_session``.

    Read from ``earliest`` rather than from the start of history: the whole point is the
    recent tail, and a warm-up that pulled twenty-one years to use sixteen bars would
    make an operator's readiness check cost more than the session it precedes.

    """
    catalog = open_catalog(catalog_path)
    bar_type = bar_type_for(instrument.id)
    stored = read_daily_bars(
        catalog,
        bar_type,
        start=_midnight(earliest),
        end=_midnight(first_session),
    )
    within = [bar for bar in stored if bar.closed_at.date() < first_session]
    return tuple(within[-count:])


def _midnight(day: date) -> datetime:
    """
    Return midnight UTC on ``day``, the form the catalog reader takes.
    """
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def report(
    catalog_path: str,
    activations: Sequence[Activation],
    *,
    first_session: date,
) -> int:
    """
    Print one readiness line per activation and return a shell exit code.

    Non-zero when any activation cannot be warmed, so this is usable as the gate it is
    meant to be rather than as something an operator has to read carefully.

    """
    print(f"Warm-up readiness for the session opening {first_session.isoformat()}\n")
    blocked = 0
    for activation in activations:
        count = activation.setup.warmup_bars
        window = inspect(catalog_path, activation, first_session=first_session)
        if window.ready:
            print(f"  {activation.name:32} ready    {count} sessions to {window.present[-1]}")
            continue
        blocked += 1
        missing = window.missing
        newest = window.newest_held.isoformat() if window.newest_held else "nothing held"
        shown = ", ".join(d.isoformat() for d in missing[:MISSING_SESSIONS_SHOWN])
        print(
            f"  {activation.name:32} BLOCKED  {len(missing)} of {count} sessions missing "
            f"({shown}{', ...' if len(missing) > MISSING_SESSIONS_SHOWN else ''}); "
            f"newest {newest}",
        )
    if blocked:
        print(
            f"\n{blocked} activation(s) cannot be warmed. Backfill the catalog; a "
            f"strategy warmed from a hole does not stop trading, it trades on a gap "
            f"that did not happen.",
        )
    return 1 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    """
    Report whether the catalog can warm each activation for a given session.
    """
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--catalog",
        default=os.environ.get(CATALOG_PATH_ENV, DEFAULT_CATALOG),
        help=f"Catalog directory (default: ${CATALOG_PATH_ENV} or {DEFAULT_CATALOG})",
    )
    parser.add_argument(
        "--session",
        help="Session to warm for, YYYY-MM-DD (default: the next trading day)",
    )
    args = parser.parse_args(argv)

    if args.session:
        first_session = date.fromisoformat(args.session)
    else:
        first_session = session_to_prepare(datetime.now(tz=UTC))
    return report(args.catalog, load_activations(), first_session=first_session)


def session_to_prepare(now: datetime) -> date:
    """
    Return the session an operator running this check is preparing for.

    **Today's session, when today's session has not opened yet.** The playbook's Before
    checklist runs about an hour before the open, and at that moment the session to be
    ready for is the one about to start - not the next one. Answering "the next trading
    day after today" was the first version, and it was wrong in the direction that
    matters: it reported BLOCKED for a catalog that was in fact ready, which trains an
    operator to disregard the check.

    The anchor is the Eastern date, because a session is named by the exchange's day and
    the operator's is a different one - 21:30 JST is 08:30 the same morning in New York.

    """
    today = now.astimezone(EASTERN).date()
    if is_trading_day(today) and now < session_open(today):
        return today
    ahead = trading_days(today + timedelta(days=1), today + timedelta(days=CALENDAR_MARGIN_DAYS))
    if not ahead:
        raise ValueError(f"no trading session within {CALENDAR_MARGIN_DAYS} days of {today}")
    return ahead[0]


__all__ = [
    "MISSING_SESSIONS_SHOWN",
    "WarmupUnavailableError",
    "WarmupWindow",
    "expected_sessions",
    "inspect",
    "load",
    "report",
    "session_to_prepare",
]


if __name__ == "__main__":
    sys.exit(main())
