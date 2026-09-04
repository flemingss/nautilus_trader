"""
Turn account equity into the numbers a strategy sizes with.

The research default risks USD 1,000 per trade. That is an R-unit chosen so that scores
are comparable across instruments; it is not an amount of money anyone decided to risk,
and passing it into a live strategy sizes as if the account were forty times larger than
the one the charter describes. On 2026-09-04 that was the state of the live path: the
activation's ``risk_budget`` reached the broker's strategy verbatim, and the only thing
between it and an order sixty-seven times the account was ``orders_enabled=False``.

The playbook already says what the number should be ([RISK.md]):

    R = A * r
    q = min( floor(R / d),  floor(A * c / P),  floor(C_settled / P),  ... )

This module is the first two terms. ``r`` and ``c`` come from the playbook's initial
live-risk defaults, at the conservative end because they are defaults for a canary and
not a policy anyone has argued up. The settled-cash term is not here: the paper account
is margin with a million dollars in it and cannot exercise it, and a term that cannot be
tested belongs on the roadmap rather than in a path that looks tested.

[RISK.md]: ../docs/playbook/RISK.md

"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR
from decimal import Decimal


DEFAULT_RISK_FRACTION = Decimal("0.0010")
"""
Planned risk per position as a fraction of equity: the low end of the playbook's
0.10% to 0.25%.

The low end, because this is what a live canary risks before anything has earned more,
and because the account-size sweep measured the premise crossing into profit at
USD 25,000 at this fraction and not at all below it. A default that risked more would be
arguing the sweep was wrong.

"""

DEFAULT_MAX_POSITION_FRACTION = Decimal("0.10")
"""
Most of equity one position may be worth at entry: the playbook's "10% until
diversification is proven".
"""

CENT = Decimal("0.01")


@dataclass(frozen=True)
class RiskPolicy:
    """
    The two fractions the playbook's sizing formula takes from policy rather than price.
    """

    risk_fraction: Decimal = DEFAULT_RISK_FRACTION
    max_position_fraction: Decimal = DEFAULT_MAX_POSITION_FRACTION

    def __post_init__(self) -> None:
        """
        Refuse a policy that is not a fraction of anything.
        """
        for name in ("risk_fraction", "max_position_fraction"):
            value = getattr(self, name)
            if not Decimal(0) < value <= Decimal(1):
                raise ValueError(f"{name} must be in (0, 1], got {value}")


@dataclass(frozen=True)
class Budget:
    """
    What one session may size against, and where every number came from.
    """

    equity: Decimal
    """
    What the broker reported, before any allocation was applied.
    """
    allocation: Decimal
    """The capital this activation sizes against: the equity, or less by decision."""
    requested: Decimal | None
    """
    What the operator asked to allocate, kept so a capped request stays visible.
    """
    policy: RiskPolicy
    risk_budget: Decimal
    """Currency at risk per position: ``allocation * risk_fraction``, floored to a cent."""
    max_notional: Decimal
    """Most a position may be worth at entry: ``allocation * max_position_fraction``."""

    @property
    def allocation_capped(self) -> bool:
        """
        Whether the requested allocation exceeded the equity and was brought down to it.
        """
        return self.requested is not None and self.requested > self.equity

    def as_record(self) -> dict[str, str]:
        """
        Return the JSON form, every number as a string so nothing goes through a float.
        """
        return {
            "equity": str(self.equity),
            "allocation": str(self.allocation),
            "requested_allocation": str(self.requested) if self.requested is not None else "",
            "allocation_capped": str(self.allocation_capped),
            "risk_fraction": str(self.policy.risk_fraction),
            "max_position_fraction": str(self.policy.max_position_fraction),
            "risk_budget": str(self.risk_budget),
            "max_notional": str(self.max_notional),
        }


def budget_for(
    equity: Decimal,
    *,
    allocation: Decimal | None = None,
    policy: RiskPolicy | None = None,
) -> Budget:
    """
    Derive the session's sizing numbers from what the broker reports.

    ``allocation`` is the capital this activation may treat as its own. It defaults to the
    whole equity and **can never exceed it**: an operator can decide to run a strategy on
    a thousand dollars of a larger account, which is an ordinary thing to want, but cannot
    decide the account is larger than the broker says. Asking for more is not an error -
    the request is recorded and the equity is used - because the alternative is a session
    that refuses to run on the morning the account dipped below a number in a script.

    Floored to the cent in both outputs, for the same reason quantities floor to whole
    shares: rounding a budget up risks more than the fraction allows.

    """
    if equity <= 0:
        raise ValueError(f"equity must be positive to size against, got {equity}")
    policy = policy or RiskPolicy()
    if allocation is not None and allocation <= 0:
        raise ValueError(f"allocation must be positive, got {allocation}")
    capital = min(allocation, equity) if allocation is not None else equity
    return Budget(
        equity=equity,
        allocation=capital,
        requested=allocation,
        policy=policy,
        risk_budget=(capital * policy.risk_fraction).quantize(CENT, rounding=ROUND_FLOOR),
        max_notional=(capital * policy.max_position_fraction).quantize(
            CENT,
            rounding=ROUND_FLOOR,
        ),
    )


__all__ = [
    "DEFAULT_MAX_POSITION_FRACTION",
    "DEFAULT_RISK_FRACTION",
    "Budget",
    "RiskPolicy",
    "budget_for",
]
