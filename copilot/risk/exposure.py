"""
The cap that no per-order control can enforce: planned risk across every strategy at once.

Twelve activations size independently and nothing aggregates them. SCHX, XLF, HYG and
EEM are one risk-on trade in four wrappers, so a gap-down morning is four full risk
budgets at once - and the risk engine cannot see it, because it is not a property of any
order. It is a property of the session.

The playbook names the control ([RISK.md], initial live-risk defaults): **maximum total
open planned risk**, and **maximum daily new entries**. This ledger is both. A strategy
asks it for the risk it is about to plan before it submits, and skips if the answer is
no; it gives the risk back when the position closes or the entry never happened.

What "planned" means here
-------------------------
The amount the strategy *intended* to risk - floored quantity times stop distance - not
what a fill later put at risk, and not a position's mark. It is the number the sizing
decided, reserved at the moment of deciding, so two strategies deciding on the same
bar cannot both see an empty ledger. That is the whole race this exists to close.

Order of asking is order of getting
-----------------------------------
The ledger grants in the order it is asked. A session that hands bars to its strategies
in name order therefore gives EEM budget before SCHX every time, and that is arbitrary -
but any ranking without evidence is arbitrary, and a stated arbitrary order is auditable
where an unstated one is not. Ranking signals is a research question and is on the
roadmap; this module records who was refused and why, which is the input that question
needs.

Settled cash, the third cap
---------------------------
The playbook's ``floor(C_settled_net / P)``: a cash account can only buy with cash that has
settled, and the basket's buys draw on one pool of it. So the ledger also holds the settled
cash the session may spend, and a buy commits its notional plus its commission against it.
**A closed position does not give its cash back.** The sale's proceeds settle the next
session (T+1), so cash spent on a position that opened and closed today is still spent;
only an entry that never filled returns what it committed. The pool is what the broker
reported when the session began, less what this session committed - working buys from an
earlier session are the sweep's to have cancelled.

[RISK.md]: ../docs/playbook/RISK.md

"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal


@dataclass(frozen=True)
class Refusal:
    """
    One reservation the ledger turned down, kept so the session can say who lost out.
    """

    key: str
    amount: Decimal
    reason: str


@dataclass
class ExposureLedger:
    """
    Planned risk reserved per strategy, under a session-wide cap and an entry count.
    """

    max_total_risk: Decimal
    """
    Most planned risk that may be open at once, in currency.
    """
    max_new_entries: int
    """
    Most new positions this session may open, whatever the risk arithmetic allows.
    """
    settled_cash: Decimal | None = None
    """
    Settled cash the session may spend on buys, or None where no cash cap applies.

    None in every research replay, which sizes against a risk budget alone. A live
    session always supplies it, read from the broker.

    """
    reserved: dict[str, Decimal] = field(default_factory=dict)
    committed: dict[str, Decimal] = field(default_factory=dict)
    """
    Settled cash committed per strategy: a buy's notional plus its commission.
    """
    entries: int = 0
    refusals: list[Refusal] = field(default_factory=list)

    def __post_init__(self) -> None:
        """
        Refuse a ledger that could never grant anything, which is a misconfiguration.

        Settled cash of zero is not refused. An account with nothing settled is a real
        morning after a sale, and the answer then is that no buy is granted, which the
        cap already gives.

        """
        if self.max_total_risk <= 0:
            raise ValueError(f"max_total_risk must be positive, got {self.max_total_risk}")
        if self.max_new_entries <= 0:
            raise ValueError(f"max_new_entries must be positive, got {self.max_new_entries}")

    @property
    def cash_committed(self) -> Decimal:
        """
        Settled cash committed across every strategy this session.
        """
        return sum(self.committed.values(), Decimal(0))

    @property
    def cash_headroom(self) -> Decimal | None:
        """
        Settled cash still spendable, never negative, or None where no cash cap applies.
        """
        if self.settled_cash is None:
            return None
        return max(self.settled_cash - self.cash_committed, Decimal(0))

    @property
    def total(self) -> Decimal:
        """
        Planned risk currently reserved across every strategy.
        """
        return sum(self.reserved.values(), Decimal(0))

    @property
    def headroom(self) -> Decimal:
        """
        Risk still grantable before the cap, never negative.
        """
        return max(self.max_total_risk - self.total, Decimal(0))

    def reserve(self, key: str, amount: Decimal, cash: Decimal = Decimal(0)) -> bool:
        """
        Reserve ``amount`` of risk and ``cash`` of settled cash for ``key``, or refuse.

        Granted only if the entry count, the risk cap and - where the ledger holds settled
        cash - the cash pool all allow it.

        All or nothing: a strategy sized to risk 1.50 does not get 0.80 of it, because a
        position at half its planned size has a different R and a different expectancy
        from the one the gate scored. It either takes the trade it sized or it skips. A
        strategy that wants to fit the cash sizes down **before** asking, from
        :attr:`cash_headroom`.

        A key that already holds a reservation is refused rather than topped up. One
        position per strategy is the rule the strategies themselves keep, and a second
        reservation under the same key would mean that rule had broken somewhere.

        """
        if amount <= 0:
            self.refusals.append(Refusal(key, amount, "nothing to reserve"))
            return False
        if key in self.reserved:
            self.refusals.append(Refusal(key, amount, "already holds a reservation"))
            return False
        if self.entries >= self.max_new_entries:
            self.refusals.append(
                Refusal(key, amount, f"entry cap reached ({self.max_new_entries})"),
            )
            return False
        if self.total + amount > self.max_total_risk:
            self.refusals.append(
                Refusal(
                    key,
                    amount,
                    f"total planned risk would reach {self.total + amount} against a cap "
                    f"of {self.max_total_risk}",
                ),
            )
            return False
        headroom = self.cash_headroom
        if headroom is not None and cash > headroom:
            self.refusals.append(
                Refusal(
                    key,
                    amount,
                    f"settled cash would reach {self.cash_committed + cash} against "
                    f"{self.settled_cash} settled",
                ),
            )
            return False
        self.reserved[key] = amount
        if cash > 0:
            self.committed[key] = cash
        self.entries += 1
        return True

    def release(self, key: str) -> Decimal:
        """
        Give ``key``'s risk back, returning what it was, or zero if it had none.

        The entry count is **not** decremented. A position that opened and closed still
        counts as one of the session's entries; the cap is on how many the session
        starts, not on how many it holds at once. Nor is the cash: see
        :meth:`release_cash`.

        """
        return self.reserved.pop(key, Decimal(0))

    def release_cash(self, key: str) -> Decimal:
        """
        Give back the settled cash ``key`` committed, for an entry that never filled.

        Separate from :meth:`release` because the two come back at different times. Risk
        is free the moment a position closes; the cash is not free until its sale settles,
        which is not this session.

        """
        return self.committed.pop(key, Decimal(0))

    def as_record(self) -> dict[str, object]:
        """
        Return the JSON form, every amount as a string.
        """
        return {
            "max_total_risk": str(self.max_total_risk),
            "max_new_entries": self.max_new_entries,
            "entries": self.entries,
            "reserved": {k: str(v) for k, v in sorted(self.reserved.items())},
            "total": str(self.total),
            "settled_cash": str(self.settled_cash) if self.settled_cash is not None else "",
            "committed_cash": {k: str(v) for k, v in sorted(self.committed.items())},
            "cash_committed": str(self.cash_committed),
            "refusals": [
                {"key": r.key, "amount": str(r.amount), "reason": r.reason} for r in self.refusals
            ],
        }


__all__ = [
    "ExposureLedger",
    "Refusal",
]
