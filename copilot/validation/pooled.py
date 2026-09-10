"""
One premise, many instruments, scored as a single experiment.

The gate has always run one symbol at a time, and that is what makes its verdicts hard to
believe. Nine separate walk-forwards over nine symbols is nine chances for a premise to
look good somewhere, and the project has already seen what that produces: AAPL's holdout
returned +0.035 R over 111 trades - a result that
[ADR-0024](../docs/decisions/0024-a-holdout-pass-needs-an-interval.md) now calls
`insufficient_evidence` rather than a pass, because 111 trades from one name cannot resolve
an effect that small.

Pooling attacks the sample size instead of the rule. The same premise and **one** parameter
set are selected on every symbol's training window at once and scored on every symbol's test
window at once, so a fold is one decision measured against nine instruments rather than nine
decisions measured against one each. That is the remedy `RESEARCH.md` names for a sample too
thin to resolve: *extend the history* - here, sideways.

Why this universe can be pooled
-------------------------------
**Survivorship is largely absent.** The nine are broad ETFs and mega-cap names chosen for
liquidity and for having a modelled spread, not for having outperformed. That is not a
survivorship-free universe in the strict sense - it is not the S&P constituents as of each
historical date - and it is not claimed to be. It is a universe whose selection was made on
tradability rather than on returns, which is the property that matters for not
manufacturing an edge out of hindsight.

**They share a calendar.** All nine are US-listed and trade the same sessions, so a fold
boundary means the same thing for each. The spine below relies on that and on nothing else.

What pooling does not fix
-------------------------
**It does not make the instruments independent.** Nine US equities on the same day are one
market with nine labels. The pooled sample is larger and the *effective* sample is not
larger by the same factor, which is exactly what
:mod:`copilot.validation.evidence` measures - and it will measure it here on trades that are
often simultaneous, so the concurrency term finally does the work it was written for.

**It does not launder a spent holdout.** AAPL's holdout window has been viewed
([ADR-0014](../docs/decisions/0014-the-holdout-is-spent-as-one-more-fold.md)), so those bars
are development data now. A pooled holdout that includes them is not out of sample. This
module deliberately provides **no pooled holdout spend**: the walk-forward is clean and
usable today, and whether a pooled holdout may include a spent symbol's window is a decision
for the owner, not a default chosen by whoever wrote the code.

The spine
---------
Fold windows are integer ranges, and pooling needs them to mean *dates*. The spine is the
sorted union of every pooled symbol's bar dates, carried as ``DailyBar`` values so the
existing evaluator can index it unchanged. **Only ``closed_at`` is ever read** - the price
fields are sentinels, and :func:`pooled_replay` never touches them. A symbol whose history
starts late simply contributes no bars to early windows, which is the correct behaviour and
needs no special case.

"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from copilot.validation.types import BacktestRunResult
from copilot.validation.types import DailyBar


if TYPE_CHECKING:
    from collections.abc import Callable

    from copilot.validation.insample import Replay


SPINE_SYMBOL = "*POOLED*"
"""
Sentinel symbol for spine bars, chosen to be obviously not an instrument.

If it ever reaches a price, a trade record or a report, that is a bug rather than a
symbol nobody recognises.

"""

_SENTINEL_PRICE = Decimal(0)


@dataclass(frozen=True)
class PooledMember:
    """
    One instrument's contribution to the pool.
    """

    symbol: str
    bars: tuple[DailyBar, ...]
    replay: Replay


def spine(members: Sequence[PooledMember]) -> tuple[DailyBar, ...]:
    """
    Return the sorted union of every member's bar dates, as indexable placeholders.

    Union rather than intersection: a symbol that starts in 2017 should shorten *its own*
    contribution to early folds, not shorten the experiment for everything else.

    """
    dates = sorted({bar.closed_at for member in members for bar in member.bars})
    return tuple(
        DailyBar(
            symbol=SPINE_SYMBOL,
            closed_at=when,
            open=_SENTINEL_PRICE,
            high=_SENTINEL_PRICE,
            low=_SENTINEL_PRICE,
            close=_SENTINEL_PRICE,
            volume=0,
        )
        for when in dates
    )


def pooled_replay(members: Sequence[PooledMember]) -> Replay:
    """
    Return a gate-compatible replay that runs every member over the window and merges.

    The evaluator hands this a slice of the spine; the slice's first and last dates are
    the window, and each member replays its own bars inside it under the same parameters.
    Trades come back carrying their own ``symbol``, which is what lets the pooled
    objective charge each one its own costs.

    """

    def _replay(bars: Sequence[DailyBar], parameters: object) -> BacktestRunResult:
        if not bars:
            return BacktestRunResult()
        window_from, window_to = bars[0].closed_at, bars[-1].closed_at

        trades: list[object] = []
        signals: list[object] = []
        counted: dict[str, int] = {}
        for member in members:
            slice_ = tuple(b for b in member.bars if window_from <= b.closed_at <= window_to)
            if not slice_:
                continue
            result = member.replay(slice_, parameters)
            trades.extend(result.trades)
            signals.extend(result.signals)
            counted[member.symbol] = len(result.trades)

        # Sorted by signal instant so the sequence the bootstrap blocks over is the order
        # the trades actually happened in. Unsorted, blocks would group by symbol and the
        # dependence measured would be the wrong dependence.
        trades.sort(key=lambda t: (t.signal_created_at, t.symbol))
        return BacktestRunResult(
            trades=tuple(trades),
            signals=tuple(signals),
            # Features are per-symbol diagnostics that do not compose into a pooled view,
            # and nothing scores them. Dropped rather than concatenated into a soup.
            features=(),
            diagnostics={"trades_by_symbol": counted},
        )

    return _replay


def calibrated_symbol(recorded: str) -> str:
    """
    Return the bare symbol the cost model is keyed on, from a recorded instrument id.

    A replayed trade records ``MSFT.XNAS`` while the spread snapshot is keyed on ``MSFT``,
    because the single-symbol objective was handed the activation's symbol directly and
    never had to bridge the two. Pooling reads the symbol off each trade, so the bridge
    has to exist somewhere; it is here, named, rather than inline where it would look
    like string tidying.

    """
    return recorded.split(".", 1)[0]


def pooled_objective(
    cost_r: Callable[[object, str], Decimal],
) -> Callable[[BacktestRunResult], Decimal]:
    """
    Mean R per trade across the pool, each trade charged its **own** symbol's costs.

    The per-symbol objective takes the symbol as an argument because one run meant one
    instrument. A pooled run does not, and charging every trade one symbol's spread would
    quietly subsidise the wide names with the narrow ones - which is the direction that
    flatters the result.

    Per trade rather than per symbol: a name that signals ten times as often carries ten
    times the weight, which is what *one experiment over a universe* means. Equal-weighting
    the symbols would be a portfolio construction question, and this is not one.

    """

    def _net_expectancy(result: BacktestRunResult) -> Decimal:
        if not result.trades:
            return Decimal(0)
        total = sum(
            (
                trade.r_multiple - cost_r(trade, calibrated_symbol(trade.symbol))
                for trade in result.trades
            ),
            Decimal(0),
        )
        return total / Decimal(len(result.trades))

    return _net_expectancy


def contribution(result: BacktestRunResult) -> Mapping[str, int]:
    """
    Trades per symbol from a pooled replay, or empty for a single-symbol one.

    Read by the report, because *pooled* is a claim that has to be checked: a pool whose
    trades all come from one name is a single-symbol experiment with extra machinery.

    """
    counted = result.diagnostics.get("trades_by_symbol", {})
    return dict(counted) if isinstance(counted, dict) else {}


__all__ = [
    "SPINE_SYMBOL",
    "PooledMember",
    "calibrated_symbol",
    "contribution",
    "pooled_objective",
    "pooled_replay",
    "spine",
]
