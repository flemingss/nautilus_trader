"""
How much of a premise's return is exposure to things that are free to own.

A long-only rule on US equities earns the equity premium on every day it holds, whether or
not its signal means anything. So a positive expectancy is not yet evidence of an edge: it
has to survive being charged for the market it rode, and for the size, value and momentum
tilts that index funds and factor funds sell for a few basis points a year. What survives is
**alpha**, and alpha is the only part of the return that justifies running a strategy
rather than holding the fund.
[ADR-0026](../docs/decisions/0026-attribution-is-measured-per-trade.md) records the method.

Per trade, in R
---------------
Every entry in this project fills at a session close, so a trade is exposed to exactly the
close-to-close factor returns of each full session after it opened. **Exits are not at a
close.** Measured on SPY next-close, 417 of 439 exit at a stop or target level during the
session and 22 at the open, so the exit session was held for an unknown part of the day.

No daily method can place that part, so both answers are computed:

- **Exit session included** counts the whole session's factor return against a trade that
  saw only some of it. A stop fills on the day the market falls, so the regressor carries
  the full fall and the trade a part of it: the loading shrinks toward zero, less of the
  return is explained, and alpha tends to be flattered.
- **Exit session excluded** counts only full sessions, and the partial move - largest
  exactly when the stop is hit - lands in the residue instead.

**Neither side is a bound.** The expected direction does not always hold: AAPL next-close,
filed 2026-09-11, read -0.026 R included and +0.001 R excluded, the opposite way round
(``docs/AUDIT_2026-09-11.md``, F19). :func:`bracketed_verdict` is therefore an **agreement
test**: it names a result only when both constructions agree, and a conclusion that holds
under both is one the approximation cannot have produced.

The unit problem is the one that needs care. A trade's return is in **R** - P&L per unit of
risk - while factor returns are fractions of capital. The bridge is the trade's own leverage
on its risk budget: a position with notional ``N`` and risk ``K`` earns ``N / K`` R for each
unit of return on the stock. So each factor's return over the holding window is scaled by
that trade's ``N / K``, signed by direction, and the regression is

    R_i - L_i * rf_i  =  alpha  +  sum_k  beta_k * L_i * F_k,i  +  e_i

where ``L_i = +/- N_i / K_i``. ``beta_k`` is then an ordinary factor loading - near one on
the market for a position in SPY - and ``alpha`` is in R per trade, the unit every verdict
reports. The risk-free leg is removed from the return because the factor returns are excess
returns; over a few days it is small, and it is reported rather than assumed away.

Factor returns over a window are **summed**, not compounded. The difference is second order
in daily returns and below a basis point over the holding periods measured here, and the sum
keeps the model linear in the factors.

Two ways the linear fit favours alpha, both left in on purpose
--------------------------------------------------------------
**R is a bracketed payoff.** A stop caps the loss near one R and a target caps the gain, so
the trade's return is a truncated function of the move while the regressor is the whole
move. Truncation attenuates the loading, less is explained, and the intercept keeps the
difference: the construction is generous to alpha, which makes *no alpha found* a stronger
statement than it looks and *alpha found* a weaker one (F18).

**Leverage spans two orders of magnitude.** ``L`` ran from 5 to 380 in the pooled record, so
in an unweighted fit the most levered trades - the tightest stops - carry the loadings. If a
trade's residual scales with its exposure, ``Var(e_i) ~ L_i^2 h_i`` for ``h_i`` sessions held,
and the efficient fit weights each trade by ``1 / (L_i^2 h_i)``. ``weighting="exposure"``
fits that, and ``copilot.strategies.attribute`` files it beside the unweighted fit as a
sensitivity. The unweighted fit stays the verdict: it is ADR-0026's decision, and the
weighted one answers a different question - alpha per trade on the median trade's scale.

The floor
---------
``MIN_TRADES_FOR_ATTRIBUTION`` is thirty: six observations per coefficient of the
five-coefficient model, before any dependence discount. It is a floor below which the fit is
refused, not a sufficiency claim - the interval, not the count, says what a result is worth.
Near it a block resample can repeat so few distinct trades that the design is singular; such
a draw is skipped and counted, and when more than one draw in twenty is skipped no interval
is reported (F25).

Inference
---------
The interval is the moving-block bootstrap of
:mod:`copilot.validation.evidence`, with the same block length, the same seed and the same
sequence of block draws, refitting the regression on each resample. That is not a
convenience. It makes the two intervals comparable by construction: a model with **no**
factors is a regression on a constant, its intercept is the mean, and its interval is the
evidence interval exactly. A test holds that identity.

What this is not
----------------
It is not a gate, and it does not decide anything. It reports the alpha, the loadings and
the decomposition; whether a premise whose return is mostly beta should be revised or
rejected is the owner's call, informed by it.

"""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from copilot.data.calendar import trading_days
from copilot.validation.evidence import DEFAULT_CONFIDENCE
from copilot.validation.evidence import DEFAULT_REPLICATES
from copilot.validation.evidence import DEFAULT_SEED
from copilot.validation.evidence import block_length
from copilot.validation.evidence import concurrency
from copilot.validation.evidence import percentile
from copilot.validation.types import Direction


if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence
    from datetime import date

    from copilot.validation.factors import FactorReturns
    from copilot.validation.filed_trades import FiledTrade


MODELS: dict[str, tuple[str, ...]] = {
    "market": ("market_excess",),
    "four_factor": ("market_excess", "size", "value", "momentum"),
}
"""
The models reported, by name, as the ``FactorReturns`` fields they regress on.

The market alone says how much is beta. The four-factor model - Carhart's, the market plus
size, value and momentum - is the one alpha is judged in, because each of those tilts can be
bought cheaply and a premise that merely rides one has not found anything.

"""

PRIMARY_MODEL = "four_factor"

MIN_TRADES_FOR_ATTRIBUTION = 30
"""
Six observations per coefficient of the five-coefficient model; a refusal floor, not a
sufficiency claim.

See *The floor* above.

"""

WEIGHTINGS = ("none", "exposure")
"""
``none`` is the verdict's ordinary least squares; ``exposure`` weights each trade by ``1
/ (L^2 h)``, the sensitivity described above.
"""

MAX_SKIPPED_FRACTION = 0.05
"""
Most resamples that may be skipped as singular before the interval is withheld.
"""

_SIX = Decimal("0.000001")


class UncoveredTradeError(ValueError):
    """
    A trade was open on sessions the pinned factor returns do not cover.
    """


class DegenerateDesignError(ValueError):
    """
    The regressors cannot be separated, so no coefficient is identified.
    """


@dataclass(frozen=True)
class Exposure:
    """
    One trade as the regression sees it.
    """

    leverage: float
    """
    Signed notional per unit of risk: what one unit of stock return is worth in R.
    """
    factor_sums: dict[str, float]
    risk_free_sum: float
    net_r: float
    gross_r: float
    sessions: int = 0
    """
    Full sessions in the window the factor sums cover.
    """


@dataclass(frozen=True)
class Loading:
    """
    One factor's coefficient, with its interval when one was drawn.
    """

    factor: str
    estimate: Decimal
    lower: Decimal | None
    upper: Decimal | None


@dataclass(frozen=True)
class Attribution:
    """
    One model fitted to one series of trades.
    """

    model: str
    series: str
    """
    ``net`` or ``gross``: which R the dependent variable was built from.
    """
    exit_session: bool
    """
    Whether the partially held exit session's factor returns were counted.
    """
    trades: int
    block_trades: int
    replicates: int
    confidence: Decimal
    mean_r: Decimal
    """
    Mean R per trade of the series, before anything is removed.
    """
    risk_free_r: Decimal
    """
    Mean R per trade earned by the cash leg, removed before fitting.
    """
    explained_r: Decimal
    """
    Mean R per trade the fitted factor exposures account for.
    """
    alpha_r: Decimal
    """
    Mean R per trade left over: ``mean_r - risk_free_r - explained_r``, the intercept.
    """
    alpha_lower_r: Decimal | None
    alpha_upper_r: Decimal | None
    loadings: tuple[Loading, ...]
    r_squared: Decimal
    weighting: str = "none"
    skipped_replicates: int = 0
    """
    Resamples whose design was singular and were not refitted.
    """

    @property
    def verdict(self) -> str:
        """
        Where the alpha interval sits relative to zero, or ``no_interval``.
        """
        if self.alpha_lower_r is None or self.alpha_upper_r is None:
            return "no_interval"
        if self.alpha_lower_r > 0:
            return "alpha"
        if self.alpha_upper_r < 0:
            return "negative_alpha"
        return "no_alpha_detected"

    def as_record(self) -> dict[str, object]:
        """
        Return the filed form.
        """

        def text(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "model": self.model,
            "factors": list(MODELS[self.model]),
            "series": self.series,
            "exit_session": "included" if self.exit_session else "excluded",
            "weighting": self.weighting,
            "trades": self.trades,
            "block_trades": self.block_trades,
            "replicates": self.replicates,
            "confidence": str(self.confidence),
            "mean_r": str(self.mean_r),
            "risk_free_r": str(self.risk_free_r),
            "explained_r": str(self.explained_r),
            "alpha_r": str(self.alpha_r),
            "alpha_lower_r": text(self.alpha_lower_r),
            "alpha_upper_r": text(self.alpha_upper_r),
            "verdict": self.verdict,
            "loadings": [
                {
                    "factor": loading.factor,
                    "estimate": str(loading.estimate),
                    "lower": text(loading.lower),
                    "upper": text(loading.upper),
                }
                for loading in self.loadings
            ],
            "r_squared": str(self.r_squared),
            "skipped_replicates": self.skipped_replicates,
        }


def exposures(
    filed: Sequence[FiledTrade],
    factors: Mapping[date, FactorReturns],
    *,
    exit_session: bool,
    model: str = PRIMARY_MODEL,
) -> tuple[Exposure, ...]:
    """
    Return each trade's leverage, its summed factor returns while open, and its R.

    The window is the sessions strictly after the entry session, up to and including the
    exit session when ``exit_session`` is set and up to the session before it otherwise.
    Excluded, a trade that exits the session after entry has an empty window and no factor
    exposure, which is what it had over any full session.

    A session is the timestamp's **UTC date**, the convention every reader of the catalog
    uses. It is the only one that is right for both stamps the catalog holds: vendor bars
    at midnight UTC on the session date, and patched bars at the session's real close.
    Converting to Eastern first moves the midnight stamps to the previous day, and a first
    draft that did so shifted every window one session early - counting the day before
    entry and dropping the day of exit - which halved the measured market beta of a long
    SPY position. A trade whose exit
    falls past the last pinned session is refused rather than attributed over the part
    that is covered, which would understate its exposure; so is one with a session inside
    its window that the factor files do not carry, and one that opened and closed on the
    same session, which has no close-to-close exposure for this method to measure.

    """
    days = sorted(factors)
    known = set(days)
    last = days[-1]
    out: list[Exposure] = []
    for entry in filed:
        trade = entry.trade
        opened = trade.opened_at.date()
        closed = trade.closed_at.date()
        _require_covered(trade.symbol, opened, closed, known, last)
        start, stop = bisect_right(days, opened), bisect_right(days, closed)
        if not exit_session:
            stop -= 1
        held = [factors[day] for day in days[start:stop]]
        sign = -1 if trade.direction == Direction.SHORT else 1
        notional = Decimal(trade.quantity) * trade.entry_price
        out.append(
            Exposure(
                leverage=sign * float(notional / trade.risk_amount),
                factor_sums={
                    name: float(sum((getattr(f, name) for f in held), Decimal(0)))
                    for name in MODELS[model]
                },
                risk_free_sum=float(sum((f.risk_free for f in held), Decimal(0))),
                net_r=float(entry.net_r),
                gross_r=float(trade.r_multiple),
                sessions=len(held),
            ),
        )
    return tuple(out)


def _require_covered(symbol: str, opened: date, closed: date, known: set[date], last: date) -> None:
    """
    Refuse a trade the pinned factor returns cannot attribute, saying which way.
    """
    if closed == opened:
        raise UncoveredTradeError(
            f"{symbol} opened and closed on {opened}, so it held no close-to-close session; "
            "every entry this method attributes fills at a close.",
        )
    if closed > last:
        raise UncoveredTradeError(
            f"{symbol} held {opened} to {closed}, past the pinned factor returns' last session "
            f"{last}. Fetch a newer vintage (python -m copilot.validation.factors --fetch) and "
            "move the pin.",
        )
    missing = [day for day in trading_days(opened + timedelta(days=1), closed) if day not in known]
    if missing:
        raise UncoveredTradeError(
            f"{symbol} held {opened} to {closed}, and the pinned factor files carry no return "
            f"for {', '.join(d.isoformat() for d in missing)}. Attributing over the rest would "
            "understate its exposure; the vintages disagree on a session.",
        )


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    """
    Solve a small symmetric system by Gaussian elimination with partial pivoting.
    """
    size = len(vector)
    rows = [[*matrix[i], vector[i]] for i in range(size)]
    scale = max((abs(v) for row in matrix for v in row), default=0.0) or 1.0
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(rows[r][column]))
        if abs(rows[pivot][column]) <= 1e-12 * scale:
            raise DegenerateDesignError(
                "the factor exposures are collinear or constant across these trades, so "
                "their loadings cannot be told apart",
            )
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(column + 1, size):
            factor = rows[r][column] / rows[column][column]
            for c in range(column, size + 1):
                rows[r][c] -= factor * rows[column][c]
    solution = [0.0] * size
    for r in range(size - 1, -1, -1):
        known = sum(rows[r][c] * solution[c] for c in range(r + 1, size))
        solution[r] = (rows[r][size] - known) / rows[r][r]
    return solution


def _moments(design: Sequence[Sequence[float]], response: Sequence[float]) -> list[list[float]]:
    """
    Return running sums of each row's ``x x'`` (upper triangle) and ``x y``, from zero.

    Prefix sums, so any contiguous block's normal-equation contribution is one
    subtraction. That is what makes two thousand refits over two thousand trades a few
    seconds rather than minutes, and it resamples exactly the same rows a refit from
    scratch would.

    """
    width = len(design[0])
    pairs = [(i, j) for i in range(width) for j in range(i, width)]
    running = [0.0] * (len(pairs) + width)
    prefix = [running[:]]
    for x, y in zip(design, response, strict=True):
        for index, (i, j) in enumerate(pairs):
            running[index] += x[i] * x[j]
        for i in range(width):
            running[len(pairs) + i] += x[i] * y
        prefix.append(running[:])
    return prefix


def _fit(summed: Sequence[float], width: int) -> list[float]:
    """
    Solve the normal equations from accumulated moments.
    """
    matrix = [[0.0] * width for _ in range(width)]
    index = 0
    for i in range(width):
        for j in range(i, width):
            matrix[i][j] = matrix[j][i] = summed[index]
            index += 1
    return _solve(matrix, list(summed[index:]))


def _bootstrap(
    prefix: Sequence[Sequence[float]],
    *,
    length: int,
    width: int,
    replicates: int,
    seed: int,
) -> tuple[list[list[float]], int]:
    """
    Refit on block resamples; return each coefficient's sorted draws, and the skips.

    The draws are :func:`~copilot.validation.evidence.assess`'s: ``ceil(n / length)`` block
    starts from the same seeded generator, the concatenation truncated to ``n``. Only how a
    block is summed differs - one subtraction of prefix sums rather than row by row. A resample
    whose design is singular is skipped and counted rather than aborting the whole fit.

    """
    draws: list[list[float]] = [[] for _ in range(width)]
    skipped = 0
    if not replicates:
        return draws, skipped
    n = len(prefix) - 1
    rng = random.Random(seed)  # noqa: S311 - fixed for reproducibility, not secrecy
    starts = n - length + 1
    blocks = math.ceil(n / length)
    for _ in range(replicates):
        summed = [0.0] * len(prefix[0])
        remaining = n
        for _ in range(blocks):
            start = rng.randrange(starts)
            take = min(length, remaining)
            high, low = prefix[start + take], prefix[start]
            for index in range(len(summed)):
                summed[index] += high[index] - low[index]
            remaining -= take
        try:
            solution = _fit(summed, width)
        except DegenerateDesignError:
            skipped += 1
            continue
        for index, value in enumerate(solution):
            draws[index].append(value)
    for column in draws:
        column.sort()
    return draws, skipped


def attribute(
    filed: Sequence[FiledTrade],
    factors: Mapping[date, FactorReturns],
    *,
    model: str = PRIMARY_MODEL,
    series: str = "net",
    exit_session: bool = True,
    weighting: str = "none",
    replicates: int = DEFAULT_REPLICATES,
    confidence: Decimal = DEFAULT_CONFIDENCE,
    seed: int = DEFAULT_SEED,
) -> Attribution:
    """
    Fit ``model`` to the trades' excess R and bootstrap the coefficients.

    ``filed`` must be in signal order, as records file it. ``replicates=0`` fits the point
    estimates only, for a decomposition that needs no interval. ``weighting`` is ``none`` or
    ``exposure``; with weights the decomposition's means are the weighted means, so the
    intercept is still ``mean - cash - explained``.

    """
    if series not in {"net", "gross"}:
        raise ValueError(f"series is 'net' or 'gross', not {series!r}")
    if weighting not in WEIGHTINGS:
        raise ValueError(f"weighting is one of {WEIGHTINGS}, not {weighting!r}")
    names = MODELS[model]
    n = len(filed)
    if n < MIN_TRADES_FOR_ATTRIBUTION:
        raise ValueError(
            f"{n} trades cannot support a regression; attribution needs "
            f"{MIN_TRADES_FOR_ATTRIBUTION}",
        )

    exposed = exposures(filed, factors, exit_session=exit_session, model=model)
    observed = [e.net_r if series == "net" else e.gross_r for e in exposed]
    cash = [e.leverage * e.risk_free_sum for e in exposed]
    response = [r - c for r, c in zip(observed, cash, strict=True)]
    design = [[1.0, *(e.leverage * e.factor_sums[name] for name in names)] for e in exposed]
    width = len(design[0])
    weights = [
        1.0 / (e.leverage**2 * max(e.sessions, 1)) if weighting == "exposure" else 1.0
        for e in exposed
    ]
    # Weighted least squares as ordinary least squares on rows scaled by the root weight, so
    # the prefix sums and the block draws are unchanged.
    roots = [math.sqrt(w) for w in weights]
    scaled_design = [[root * x for x in row] for root, row in zip(roots, design, strict=True)]
    scaled_response = [root * y for root, y in zip(roots, response, strict=True)]

    prefix = _moments(scaled_design, scaled_response)
    point = _fit(prefix[n], width)

    # The block is the evidence interval's, drawn from the same series the same way, so the
    # two intervals differ only by what the regression removes.
    net_series = [e.net_r for e in exposed]
    length = block_length(n, concurrency([f.trade for f in filed]), net_series)
    draws, skipped = _bootstrap(
        prefix,
        length=length,
        width=width,
        replicates=replicates,
        seed=seed,
    )
    withheld = replicates and skipped > MAX_SKIPPED_FRACTION * replicates

    tail = float((Decimal(1) - confidence) / 2)

    def bounds(index: int) -> tuple[Decimal | None, Decimal | None]:
        if not replicates or withheld:
            return None, None
        return _decimal(percentile(draws[index], tail)), _decimal(
            percentile(draws[index], 1 - tail),
        )

    total_weight = sum(weights)

    def weighted_mean(values: Sequence[float]) -> float:
        return sum(w * v for w, v in zip(weights, values, strict=True)) / total_weight

    means = [weighted_mean([row[i] for row in design]) for i in range(width)]
    explained = sum(point[i] * means[i] for i in range(1, width))
    fitted_error = sum(
        w * (y - sum(b * x for b, x in zip(point, row, strict=True))) ** 2
        for w, row, y in zip(weights, design, response, strict=True)
    )
    centre = weighted_mean(response)
    spread = sum(w * (y - centre) ** 2 for w, y in zip(weights, response, strict=True))
    alpha_lower, alpha_upper = bounds(0)

    return Attribution(
        model=model,
        series=series,
        exit_session=exit_session,
        trades=n,
        block_trades=length,
        replicates=replicates,
        confidence=confidence,
        mean_r=_decimal(weighted_mean(observed)),
        risk_free_r=_decimal(weighted_mean(cash)),
        explained_r=_decimal(explained),
        alpha_r=_decimal(point[0]),
        alpha_lower_r=alpha_lower,
        alpha_upper_r=alpha_upper,
        loadings=tuple(
            Loading(name, _decimal(point[i + 1]), *bounds(i + 1)) for i, name in enumerate(names)
        ),
        r_squared=_decimal(1 - fitted_error / spread) if spread else Decimal(0),
        weighting=weighting,
        skipped_replicates=skipped,
    )


def bracketed_verdict(included: Attribution, excluded: Attribution) -> str:
    """
    Name the alpha only where both treatments of the exit session agree.

    An agreement test, not a bound: neither treatment is guaranteed to sit on one side of the
    truth. ``bracket_disagrees`` is its own answer rather than a fallback to one side: it says
    the result turns on an approximation this data cannot resolve, which is worth knowing and
    is not the same as finding nothing.

    """
    if included.model != excluded.model or included.series != excluded.series:
        raise ValueError("a bracket compares one model on one series, treated two ways")
    if not included.exit_session or excluded.exit_session:
        raise ValueError("pass the included fit first and the excluded fit second")
    if included.verdict == excluded.verdict:
        return included.verdict
    return "bracket_disagrees"


def _decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_SIX)


__all__ = [
    "MAX_SKIPPED_FRACTION",
    "MIN_TRADES_FOR_ATTRIBUTION",
    "MODELS",
    "PRIMARY_MODEL",
    "WEIGHTINGS",
    "Attribution",
    "DegenerateDesignError",
    "Exposure",
    "Loading",
    "UncoveredTradeError",
    "attribute",
    "bracketed_verdict",
    "exposures",
]
