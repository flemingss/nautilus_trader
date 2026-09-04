"""
The knob store: which strategy trades what, with which fixed parameters, at which stage.

A setup is code; activation is data (ADR-0005). The code lives in a module beside this
one and declares its own ``SEARCH_SPACE``, because the reasoning that sized that space is
a property of the premise. Everything else - symbol, venue, lifecycle, risk budget, the
validation window - lives in a TOML file under ``registry/`` and is read from here.

Why this exists
---------------
Before it, no ``ParameterGrid`` was constructed anywhere in committed code. The first
walk-forward verdict this fork produced could not be reproduced from the repository,
because the search space existed only in a scratch file. An activation file makes a run
reproducible from a commit, and makes moving a risk budget a reviewable diff rather than
an edit someone made once.

Files rather than a database because there is no database, and because a git-tracked file
is a *better* audit record than a mutable row: it shows who moved a threshold, when, and
what the diff was.

The seeding rule
----------------
An activation's fixed parameters seed the grid as its ``base``, so a candidate starts from
*this* activation's identity rather than from contract defaults. trade-copilot learned
that one expensively (its V1-30): without it, validating a short-leg strategy silently
rebuilt every candidate as the long leg and filed the result against the wrong row.

Searched axes still win over the base, so an activation can never quietly narrow its own
declared search - it can only be searched over, or hold a value the search does not touch.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from copilot.strategies import gap_reversal
from copilot.validation.insample import ParameterGrid


REGISTRY_DIR = Path(__file__).parent / "registry"


class Lifecycle(StrEnum):
    """
    How far an activation has been trusted.

    A field rather than a database state, so promotion is a diff someone reviewed.

    """

    RESEARCH = "RESEARCH"
    """
    Never trades.

    Free to be re-validated, re-parameterised, or deleted.

    """

    PAPER = "PAPER"
    """
    Trades on a paper account.

    Real orders, no capital.

    """

    LIVE = "LIVE"
    """
    Trades real capital.

    Nothing reaches this without a spent holdout.

    """


@dataclass(frozen=True)
class SetupSpec:
    """One registered strategy: how to build it, and what may be searched over it."""

    search_space: Mapping[str, Sequence[Any]]
    factory: Any
    warmup_bars: int
    """
    Bars the rule needs before it can fire.

    Taken from the strategy rather than guessed by the caller. Guessing short makes the
    gate read missing history as failing folds, which looks like a dead premise.

    """


SETUPS: Mapping[str, SetupSpec] = {
    "gap_reversal": SetupSpec(
        search_space=gap_reversal.SEARCH_SPACE,
        factory=gap_reversal.strategy_factory,
        warmup_bars=gap_reversal.WARMUP_BARS,
    ),
}
"""
Every strategy an activation may name.

An activation naming something absent from here fails to load, loudly. The alternative -
skipping it with a logged reason - would let a typo silently remove a strategy from a
validation run.

"""


@dataclass(frozen=True)
class ValidationSettings:
    """
    The walk-forward window, carried per activation.

    Part of what makes a verdict reproducible: the same bars and the same grid under a
    different fold geometry are a different experiment.

    """

    train_bars: int = 252
    test_bars: int = 126
    purge_bars: int = 5
    min_trades: int = 20
    fold_min_trades: int = 5
    holdout_start: str = ""
    """
    This activation's holdout boundary as ``YYYY-MM-DD``; empty means the shared pin.

    [ADR-0012] pinned one date for the whole universe, and that was right while the
    universe was single names with twenty years each. A series that starts in 2017 or
    2020 puts 2022-01-01 at 44 percent or more of its history, far outside the charter's
    15-20 percent band, and ``carve`` refuses it - correctly, because the alternative is
    a boundary that quietly means something different per symbol.

    So the pin moves *into the activation*, where it is still a date in a committed file
    that cannot change without a diff, which was the whole point of pinning by date
    rather than by percentage. [ADR-0020] records the change.

    [ADR-0012]: ../docs/decisions/0012-the-holdout-is-carved-at-2022-01-01.md
    [ADR-0020]: ../docs/decisions/0020-the-holdout-boundary-is-per-activation.md

    """

    @property
    def holdout_boundary(self) -> datetime | None:
        """
        Return the parsed boundary, or None to use the shared pin.
        """
        if not self.holdout_start:
            return None
        return datetime.fromisoformat(self.holdout_start).replace(tzinfo=UTC)


@dataclass(frozen=True)
class Activation:
    """
    One strategy, configured to trade one instrument, at one lifecycle stage.
    """

    name: str
    strategy: str
    lifecycle: Lifecycle
    symbol: str
    venue: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    """
    Fixed parameters that are this activation's identity, not search placeholders.
    """
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    note: str = ""

    @property
    def setup(self) -> SetupSpec:
        """
        The registered strategy this activation names.
        """
        return SETUPS[self.strategy]

    @property
    def trades(self) -> bool:
        """
        Whether this activation may place orders at all.
        """
        return self.lifecycle is not Lifecycle.RESEARCH

    def grid(self) -> ParameterGrid:
        """
        Return the search, seeded with this activation's own identity.

        Searched axes win over the seed, so this can narrow nothing.

        """
        return ParameterGrid(axes_by_name=dict(self.setup.search_space)).with_base(
            dict(self.parameters),
        )


def _decimalise(value: Any) -> Any:
    """
    Carry numeric knobs as ``Decimal``.

    TOML floats are binary floats, and these values are multiplied by an ATR to place
    stops. Numbers are written as strings in the registry for that reason; a float that
    slipped through is converted here rather than silently reaching an order price.

    """
    if isinstance(value, str):
        try:
            return Decimal(value)
        except (ArithmeticError, ValueError):
            return value
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def parse_activation(name: str, raw: Mapping[str, Any]) -> Activation:
    """
    Build an activation from decoded TOML, failing on anything unrecognised.
    """
    strategy = raw.get("strategy")
    if strategy not in SETUPS:
        raise ValueError(
            f"activation {name!r} names unknown strategy {strategy!r}; "
            f"registered: {sorted(SETUPS)}",
        )

    instrument = raw.get("instrument", {})
    for required in ("symbol", "venue"):
        if not instrument.get(required):
            raise ValueError(f"activation {name!r} is missing instrument.{required}")

    unknown = set(raw) - {"strategy", "lifecycle", "instrument", "parameters", "validation", "note"}
    if unknown:
        # A misspelled section would otherwise be silently ignored, and an activation
        # would run with defaults nobody chose.
        raise ValueError(f"activation {name!r} has unknown section(s): {sorted(unknown)}")

    return Activation(
        name=name,
        strategy=strategy,
        lifecycle=Lifecycle(raw.get("lifecycle", Lifecycle.RESEARCH)),
        symbol=str(instrument["symbol"]),
        venue=str(instrument["venue"]),
        parameters={k: _decimalise(v) for k, v in raw.get("parameters", {}).items()},
        validation=ValidationSettings(**raw.get("validation", {})),
        note=str(raw.get("note", "")),
    )


def load_activation(path: Path) -> Activation:
    """
    Read one activation file.
    """
    return parse_activation(path.stem, tomllib.loads(path.read_text()))


def load_activations(directory: Path = REGISTRY_DIR) -> tuple[Activation, ...]:
    """
    Every activation in the registry, in name order.
    """
    return tuple(load_activation(p) for p in sorted(directory.glob("*.toml")))


def find_activation(name: str, directory: Path = REGISTRY_DIR) -> Activation:
    """
    Look one up by name, listing what exists when it is missing.
    """
    path = directory / f"{name}.toml"
    if not path.exists():
        available = sorted(p.stem for p in directory.glob("*.toml"))
        raise KeyError(f"no activation {name!r}; available: {available}")
    return load_activation(path)


__all__ = [
    "REGISTRY_DIR",
    "SETUPS",
    "Activation",
    "Lifecycle",
    "SetupSpec",
    "ValidationSettings",
    "find_activation",
    "load_activation",
    "load_activations",
    "parse_activation",
]
