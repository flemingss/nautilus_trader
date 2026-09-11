"""
What attribution has to get right before a premise is rejected on its say-so.

The result it produced on first run - the pool's gross return is cash and factor exposure,
with nothing left - is the kind that ends a line of research. So these pin the properties
that make it trustworthy rather than merely plausible:

- **It recovers what it is given.** Trades built with a known alpha and loading come back
  with that alpha and that loading.
- **Its interval is the evidence interval.** A regression on a constant is the mean, and
  with the same blocks and seed its interval must be ADR-0024's to the digit.
- **The windows are the sessions actually held**, for both of the catalog's timestamp
  conventions. A first draft converted to Eastern and shifted every window a day early.
- **The fast bootstrap is the slow one.** Prefix sums are an optimisation, and an
  optimisation that resamples different rows is a different method.

"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

import pytest

from copilot.validation.attribution import MODELS
from copilot.validation.attribution import DegenerateDesignError
from copilot.validation.attribution import UncoveredTradeError
from copilot.validation.attribution import attribute
from copilot.validation.attribution import bracketed_verdict
from copilot.validation.attribution import exposures
from copilot.validation.evidence import DEFAULT_SEED
from copilot.validation.evidence import assess
from copilot.validation.evidence import block_length
from copilot.validation.evidence import concurrency
from copilot.validation.factors import FactorReturns
from copilot.validation.filed_trades import FiledTrade
from copilot.validation.types import ClosedTrade
from copilot.validation.types import Direction


START = date(2015, 1, 5)
SESSIONS = tuple(
    START + timedelta(days=i) for i in range(700) if (START + timedelta(days=i)).weekday() < 5
)


def factor_table(seed: int = 7, *, risk_free: str = "0") -> dict[date, FactorReturns]:
    """
    Independent daily factor returns of realistic size, on a weekday calendar.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture

    def draw(sd: float) -> Decimal:
        return Decimal(str(round(rng.gauss(0.0003, sd), 6)))

    return {
        day: FactorReturns(
            market_excess=draw(0.010),
            size=draw(0.005),
            value=draw(0.005),
            momentum=draw(0.006),
            risk_free=Decimal(risk_free),
        )
        for day in SESSIONS
    }


def stamp(day: date, *, at_close: bool = False) -> datetime:
    """
    A bar instant in either convention the catalog holds.
    """
    if at_close:
        return datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def filed_trade(
    entry: int,
    exit_: int,
    r: float,
    *,
    leverage: int = 50,
    cost: str = "0",
    direction: Direction = Direction.LONG,
    at_close: bool = False,
) -> FiledTrade:
    """
    A trade entering at session ``entry``'s close and exiting in session ``exit_``.

    ``leverage`` is notional per unit of risk: quantity 50 at 100 against 100 of risk.

    """
    risk = Decimal(100)
    return FiledTrade(
        fold=0,
        trade=ClosedTrade(
            symbol="SPY.ARCX",
            direction=direction,
            quantity=leverage,
            entry_price=Decimal(100),
            exit_price=Decimal(100),
            exit_reason="CLOSED",
            signal_created_at=stamp(SESSIONS[entry], at_close=at_close),
            opened_at=stamp(SESSIONS[entry], at_close=at_close),
            closed_at=stamp(SESSIONS[exit_], at_close=at_close),
            realized_pnl=Decimal(str(r)) * risk,
            risk_amount=risk,
        ),
        cost_r=Decimal(cost),
    )


def window_sum(factors, entry: int, exit_: int, name: str, *, exit_session: bool = True) -> float:
    stop = exit_ + 1 if exit_session else exit_
    return float(sum((getattr(factors[d], name) for d in SESSIONS[entry + 1 : stop]), Decimal(0)))


def built(
    factors,
    *,
    alpha: float,
    betas: dict[str, float],
    count: int = 240,
    noise: float = 0.0,
    seed: int = 3,
) -> list[FiledTrade]:
    """
    Trades whose R is exactly ``alpha + sum(beta * L * F)`` plus optional noise.
    """
    rng = random.Random(seed)  # noqa: S311 - deterministic fixture
    trades = []
    for i in range(count):
        entry = i + 2
        exit_ = entry + 1 + i % 4
        leverage = 30 + (i * 7) % 40
        r = alpha + sum(
            beta * leverage * window_sum(factors, entry, exit_, name)
            for name, beta in betas.items()
        )
        r += rng.gauss(0, noise) if noise else 0.0
        trades.append(filed_trade(entry, exit_, round(r, 10), leverage=leverage))
    return trades


# --------------------------------------------------------------------- recovery


def test_a_known_alpha_and_loading_come_back_exactly_without_noise() -> None:
    factors = factor_table()
    betas = {"market_excess": 0.9, "size": -0.2, "value": 0.3, "momentum": 0.1}
    trades = built(factors, alpha=0.05, betas=betas)

    result = attribute(trades, factors, replicates=0)

    assert abs(result.alpha_r - Decimal("0.05")) <= Decimal("0.000002")
    for loading in result.loadings:
        assert abs(loading.estimate - Decimal(str(betas[loading.factor]))) <= Decimal("0.00001")
    assert result.r_squared == Decimal("1.000000")


def test_with_noise_the_interval_covers_the_truth() -> None:
    factors = factor_table()
    trades = built(factors, alpha=0.02, betas={"market_excess": 1.0}, noise=0.3, count=300)

    result = attribute(trades, factors, model="market")

    assert result.alpha_lower_r < Decimal("0.02") < result.alpha_upper_r
    (beta,) = result.loadings
    assert beta.lower < Decimal("1.0") < beta.upper


def test_the_mean_decomposes_into_cash_factors_and_alpha() -> None:
    factors = factor_table(risk_free="0.0001")
    trades = built(factors, alpha=0.01, betas={"market_excess": 1.1, "value": 0.4}, noise=0.2)

    result = attribute(trades, factors, replicates=0)

    parts = result.risk_free_r + result.explained_r + result.alpha_r
    assert abs(result.mean_r - parts) <= Decimal("0.000003")
    assert result.risk_free_r > 0


def test_costs_come_off_alpha_and_not_off_the_loadings() -> None:
    factors = factor_table()
    free = built(factors, alpha=0.05, betas={"market_excess": 1.0})
    charged = [FiledTrade(t.fold, t.trade, Decimal("0.03")) for t in free]

    gross = attribute(charged, factors, series="gross", replicates=0)
    net = attribute(charged, factors, series="net", replicates=0)

    assert abs((gross.alpha_r - net.alpha_r) - Decimal("0.03")) <= Decimal("0.000002")
    assert abs(gross.loadings[0].estimate - net.loadings[0].estimate) <= Decimal("0.000002")


# ------------------------------------------------------------ the evidence identity


def test_a_regression_on_a_constant_is_the_evidence_interval(monkeypatch) -> None:
    """
    Same blocks, same seed, same draws: the intercept is the mean and its interval is
    ADR-0024's exactly.

    If this ever fails, the two intervals have stopped being comparable.

    """
    monkeypatch.setitem(MODELS, "constant", ())
    factors = factor_table()
    trades = built(factors, alpha=0.03, betas={}, noise=0.8, count=180)

    result = attribute(trades, factors, model="constant")
    evidence = assess([t.trade for t in trades], cost_r=lambda _: Decimal(0))

    assert result.alpha_r == evidence.mean_r
    assert (result.alpha_lower_r, result.alpha_upper_r) == (evidence.lower_r, evidence.upper_r)
    assert result.block_trades == evidence.block_bars


def test_the_fast_bootstrap_resamples_what_a_refit_from_scratch_would() -> None:
    """
    One replicate by hand: draw the blocks, gather the rows, solve least squares directly.
    """
    factors = factor_table()
    trades = built(factors, alpha=0.01, betas={"market_excess": 0.8}, noise=0.5, count=60)
    exposed = exposures(trades, factors, exit_session=True)
    rows = [(e.leverage * e.factor_sums["market_excess"], e.net_r) for e in exposed]
    n = len(rows)
    length = block_length(n, concurrency([t.trade for t in trades]), [e.net_r for e in exposed])

    rng = random.Random(DEFAULT_SEED)  # noqa: S311 - mirrors the module under test
    drawn: list[tuple[float, float]] = []
    for _ in range(math.ceil(n / length)):
        start = rng.randrange(n - length + 1)
        drawn.extend(rows[start : start + length])
    drawn = drawn[:n]
    mean_x = sum(x for x, _ in drawn) / n
    mean_y = sum(y for _, y in drawn) / n
    slope = sum((x - mean_x) * (y - mean_y) for x, y in drawn) / sum(
        (x - mean_x) ** 2 for x, _ in drawn
    )
    intercept = mean_y - slope * mean_x

    one = attribute(trades, factors, model="market", replicates=1)

    assert one.alpha_lower_r == one.alpha_upper_r
    assert abs(one.alpha_lower_r - Decimal(str(intercept))) <= Decimal("0.000001")
    assert abs(one.loadings[0].lower - Decimal(str(slope))) <= Decimal("0.000001")


# ------------------------------------------------------------------------ windows


@pytest.mark.parametrize("at_close", [False, True])
def test_the_window_is_the_sessions_after_entry_through_exit(at_close: bool) -> None:
    """
    Both catalog conventions: midnight UTC on the session date, and the real close.
    """
    factors = factor_table()
    trade = filed_trade(10, 13, 0.0, leverage=1, at_close=at_close)

    (exposure,) = exposures([trade], factors, exit_session=True)

    assert exposure.factor_sums["market_excess"] == window_sum(factors, 10, 13, "market_excess")


def test_excluding_the_exit_session_drops_exactly_that_session() -> None:
    factors = factor_table()
    trade = filed_trade(10, 13, 0.0, leverage=1)

    (included,) = exposures([trade], factors, exit_session=True)
    (excluded,) = exposures([trade], factors, exit_session=False)

    last = float(factors[SESSIONS[13]].market_excess)
    assert included.factor_sums["market_excess"] - excluded.factor_sums[
        "market_excess"
    ] == pytest.approx(last)


def test_a_next_session_exit_has_no_full_session_of_exposure() -> None:
    factors = factor_table()

    (exposure,) = exposures([filed_trade(10, 11, 0.0)], factors, exit_session=False)

    assert exposure.factor_sums == dict.fromkeys(MODELS["four_factor"], 0.0)


def test_the_eastern_conversion_that_shifted_windows_is_not_used() -> None:
    """
    Midnight UTC is the previous evening in New York; the entry session must not move.
    """
    factors = factor_table()
    trade = filed_trade(10, 11, 0.0, leverage=1)

    (exposure,) = exposures([trade], factors, exit_session=True)

    assert exposure.factor_sums["market_excess"] == float(factors[SESSIONS[11]].market_excess)


def test_a_short_is_exposed_the_other_way() -> None:
    factors = factor_table()
    long_ = filed_trade(10, 13, 0.0, leverage=40)
    short = filed_trade(10, 13, 0.0, leverage=40, direction=Direction.SHORT)

    (a,), (b,) = (
        exposures([long_], factors, exit_session=True),
        exposures([short], factors, exit_session=True),
    )

    assert a.leverage == 40.0
    assert b.leverage == -40.0


def test_a_trade_past_the_pinned_factors_is_refused() -> None:
    factors = factor_table()
    past = filed_trade(len(SESSIONS) - 2, len(SESSIONS) - 1, 0.0)
    trimmed = {d: f for d, f in factors.items() if d < SESSIONS[-1]}

    with pytest.raises(UncoveredTradeError, match="Fetch a newer vintage"):
        exposures([past], trimmed, exit_session=True)


def test_exposures_that_cannot_be_separated_are_refused() -> None:
    factors = factor_table()
    same_window = [filed_trade(10, 12, 0.1 * i, leverage=50) for i in range(40)]

    with pytest.raises(DegenerateDesignError):
        attribute(same_window, factors, model="four_factor", replicates=0)


def test_too_few_trades_are_refused() -> None:
    factors = factor_table()

    with pytest.raises(ValueError, match="cannot support a regression"):
        attribute([filed_trade(10, 12, 0.1)] * 5, factors, replicates=0)


# ------------------------------------------------------------------------ verdict


def test_the_bracket_names_a_result_only_when_both_sides_agree() -> None:
    factors = factor_table()
    losing = built(factors, alpha=-0.4, betas={"market_excess": 1.0}, noise=0.2, count=200)
    noisy = built(factors, alpha=0.0, betas={"market_excess": 1.0}, noise=0.6, count=200)

    agreed = bracketed_verdict(
        attribute(losing, factors, exit_session=True),
        attribute(losing, factors, exit_session=False),
    )
    unresolved = bracketed_verdict(
        attribute(noisy, factors, exit_session=True),
        attribute(noisy, factors, exit_session=False),
    )

    assert agreed == "negative_alpha"
    assert unresolved in {"no_alpha_detected", "bracket_disagrees"}


def test_the_bracket_refuses_arguments_in_the_wrong_order() -> None:
    factors = factor_table()
    trades = built(factors, alpha=0.0, betas={"market_excess": 1.0}, noise=0.2, count=60)
    included = attribute(trades, factors, replicates=0)
    excluded = attribute(trades, factors, exit_session=False, replicates=0)

    with pytest.raises(ValueError, match="included fit first"):
        bracketed_verdict(excluded, included)


def test_the_record_names_the_treatment_and_the_verdict() -> None:
    factors = factor_table()
    trades = built(factors, alpha=0.0, betas={"market_excess": 1.0}, noise=0.2, count=60)

    record = attribute(trades, factors, exit_session=False).as_record()

    assert record["exit_session"] == "excluded"
    assert record["factors"] == ["market_excess", "size", "value", "momentum"]
    assert record["verdict"] in {"alpha", "negative_alpha", "no_alpha_detected"}


# ----------------------------------------------------------------------- the command


def test_a_record_without_filed_trades_is_refused_by_name(tmp_path) -> None:
    from copilot.strategies.attribute import UnattributableRecordError
    from copilot.strategies.attribute import label_and_rows

    path = tmp_path / "old_verdict.json"

    with pytest.raises(UnattributableRecordError, match=r"old_verdict\.json files no trade_rows"):
        label_and_rows({"activation": "x", "total_test_trades": 3}, path)


def test_records_are_labelled_by_kind(tmp_path) -> None:
    from copilot.strategies.attribute import label_and_rows

    path = tmp_path / "r.json"

    assert label_and_rows({"activation": "a", "trade_rows": []}, path)[0] == "a (walk-forward)"
    assert (
        label_and_rows({"activation": "a", "holdout": {"trade_rows": []}}, path)[0] == "a (holdout)"
    )
    assert (
        label_and_rows({"symbols": ["SPY", "TLT"], "trade_rows": []}, path)[0] == "pooled SPY+TLT"
    )


def test_the_newest_record_per_activation_and_the_newest_pool_are_read(tmp_path) -> None:
    from copilot.strategies.attribute import newest_records

    verdicts, out = tmp_path / "verdicts", tmp_path / "out"
    verdicts.mkdir()
    out.mkdir()
    for name in (
        "spy-gap-fade-long_20260904T160008Z.json",
        "spy-gap-fade-long_20260911T002723Z.json",
        "spy-gap-fade-long-next-close_20260911T002703Z.json",
    ):
        (verdicts / name).write_text("{}")
    for name, membership in (
        ("pooled_next_close_20260910T183037Z.json", "constant"),
        ("pooled_next_close_20260911T002351Z.json", "constant"),
        ("pooled_next_close_shifting_20260911T160500Z.json", "shifting"),
        ("pooled_next_close_20260911T000000Z.json", None),
    ):
        (out / name).write_text(json.dumps({"membership": membership} if membership else {}))
    holdouts = tmp_path / "holdouts"
    holdouts.mkdir()
    (holdouts / "spent-without-rows.json").write_text("{}")
    (holdouts / "reassessed.json").write_text(json.dumps({"reassessment": {"trade_rows": []}}))

    names = [p.name for p in newest_records(verdicts, out, holdouts)]

    assert names == [
        "spy-gap-fade-long-next-close_20260911T002703Z.json",
        "spy-gap-fade-long_20260911T002723Z.json",
        "pooled_next_close_20260911T002351Z.json",
        "reassessed.json",
    ], "the newest constant-membership pool, and only holdouts with generated rows"


# ------------------------------------------------------------- audit batch C (F18-F31)


@pytest.mark.parametrize("count", [181, 187, 193])
def test_the_constant_regression_matches_the_interval_on_a_count_blocks_do_not_divide(
    monkeypatch,
    count: int,
) -> None:
    """
    Audit F31: both truncations were only ever compared on counts the block length divides.
    """
    monkeypatch.setitem(MODELS, "constant", ())
    factors = factor_table()
    trades = built(factors, alpha=0.03, betas={}, noise=0.8, count=count)

    result = attribute(trades, factors, model="constant")
    evidence = assess([t.trade for t in trades], cost_r=lambda _: Decimal(0))

    assert count % result.block_trades != 0, "the case this test exists for"
    assert (result.alpha_lower_r, result.alpha_upper_r) == (evidence.lower_r, evidence.upper_r)


def test_the_exposure_weighted_fit_recovers_a_known_alpha_and_keeps_the_decomposition() -> None:
    factors = factor_table()
    betas = {"market_excess": 0.9, "value": 0.3}
    trades = built(factors, alpha=0.04, betas=betas, count=240)

    weighted = attribute(trades, factors, replicates=0, weighting="exposure")

    assert weighted.weighting == "exposure"
    assert abs(weighted.alpha_r - Decimal("0.04")) <= Decimal("0.00001")
    parts = weighted.risk_free_r + weighted.explained_r + weighted.alpha_r
    assert abs(weighted.mean_r - parts) <= Decimal("0.000003")


def test_an_unknown_weighting_is_refused() -> None:
    factors = factor_table()

    with pytest.raises(ValueError, match="weighting"):
        attribute(built(factors, alpha=0.0, betas={}), factors, replicates=0, weighting="median")


def test_exposures_reads_the_factors_the_model_names(monkeypatch) -> None:
    """
    Audit F29: exposures summed the four-factor set whatever model was asked for.
    """
    monkeypatch.setitem(MODELS, "size_only", ("size",))
    factors = factor_table()

    (exposure,) = exposures(
        [filed_trade(10, 13, 0.0)],
        factors,
        exit_session=True,
        model="size_only",
    )

    assert set(exposure.factor_sums) == {"size"}


def test_a_same_session_round_trip_is_refused_for_what_it_is() -> None:
    factors = factor_table()

    with pytest.raises(UncoveredTradeError, match="opened and closed on"):
        exposures([filed_trade(10, 10, 0.0)], factors, exit_session=True)


def test_a_session_missing_from_the_factor_files_inside_a_window_is_refused() -> None:
    """
    Audit F29: an interior session one vintage lacks was dropped silently.
    """
    from copilot.data.calendar import is_trading_day

    factors = factor_table()
    held = next(i for i in range(20, 40) if is_trading_day(SESSIONS[i]))
    holed = {d: f for d, f in factors.items() if d != SESSIONS[held]}

    with pytest.raises(UncoveredTradeError, match="carry no return"):
        exposures([filed_trade(held - 2, held + 2, 0.0)], holed, exit_session=True)


def test_at_the_floor_a_singular_resample_is_skipped_not_fatal() -> None:
    """
    Audit F25: a singular block resample raised from inside the loop and aborted the whole fit.

    Thirty trades, one of them exposed to the market: every resample that misses it has a zero
    column. Too many skipped draws withhold the interval rather than report one from the rest.
    """
    factors = factor_table()
    trades = [filed_trade(2 * i + 2, 2 * i + 3, 0.02 * (i % 4), leverage=0) for i in range(30)]
    trades[14] = filed_trade(30, 33, 0.4, leverage=50)

    result = attribute(trades, factors, model="market", replicates=400)

    assert result.skipped_replicates > 20
    assert result.alpha_lower_r is None
    assert result.verdict == "no_interval"


def test_the_bracket_is_filed_as_an_agreement_test() -> None:
    from copilot.validation import attribution

    assert "not a bound" in attribution.bracketed_verdict.__doc__
    assert "Neither side is a bound" in attribution.__doc__


def test_a_premise_whose_trades_all_exit_the_next_session_is_filed_unattributable(tmp_path) -> None:
    """
    Audit F29: with every regressor zero when the exit session is excluded, attribute traced back.
    """
    import json

    from copilot.strategies.attribute import attribute_record
    from copilot.validation.filed_trades import to_rows
    from copilot.validation.walkforward import FoldResult

    factors = factor_table()
    trades = [filed_trade(i + 2, i + 3, 0.01 * (i % 5)) for i in range(60)]
    fold = FoldResult(
        index=0,
        windows=None,
        train_from=stamp(SESSIONS[0]),
        test_from=stamp(SESSIONS[2]),
        test_to=stamp(SESSIONS[70]),
        in_sample=None,
        selected=object(),
        test_trades=len(trades),
        test_score=Decimal(0),
        passed=False,
        reason="test",
        test_trade_details=tuple(t.trade for t in trades),
    )
    path = tmp_path / "next-session-exits_20260911T000000Z.json"
    path.write_text(
        json.dumps(
            {
                "activation": "next-session-exits",
                "trade_rows": to_rows([fold], cost_r=lambda _: Decimal(0)),
            },
        ),
    )

    result = attribute_record(path, factors)

    assert "four_factor_net_exit_excluded" in result["unattributable"]
    assert result["verdict"] == "unattributable"
