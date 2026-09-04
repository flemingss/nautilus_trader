"""
Tests for the session-wide planned-risk ledger.

The property that matters is the race: two strategies deciding on the same bar must not
both see an empty ledger. Everything else here is the arithmetic of a cap and a count,
and the direction each errs in - all or nothing, never partial; entries counted when
opened, not when held.

"""

from __future__ import annotations

from decimal import Decimal

import pytest

from copilot.risk.exposure import ExposureLedger


def ledger(**kwargs: object) -> ExposureLedger:
    base = {"max_total_risk": Decimal("5.00"), "max_new_entries": 2}
    return ExposureLedger(**{**base, **kwargs})  # type: ignore[arg-type]


class TestReserve:
    def test_a_reservation_under_the_cap_is_granted(self) -> None:
        book = ledger()
        assert book.reserve("a", Decimal("1.50"))
        assert book.total == Decimal("1.50")
        assert book.headroom == Decimal("3.50")

    def test_the_second_asker_sees_the_first_reservation(self) -> None:
        """
        The race this module exists to close.
        """
        book = ledger(max_total_risk=Decimal("2.00"))
        assert book.reserve("eem", Decimal("1.50"))
        assert not book.reserve("schx", Decimal("1.50"))
        assert book.total == Decimal("1.50")
        assert book.refusals[0].key == "schx"
        assert "cap of 2.00" in book.refusals[0].reason

    def test_all_or_nothing(self) -> None:
        """
        A position at half its planned size has a different R from the one the gate
        scored, so a strategy is not handed the remainder.
        """
        book = ledger(max_total_risk=Decimal("2.00"))
        book.reserve("a", Decimal("1.50"))
        assert not book.reserve("b", Decimal("1.00"))
        assert book.reserved == {"a": Decimal("1.50")}

    def test_exactly_the_cap_is_allowed(self) -> None:
        book = ledger(max_total_risk=Decimal("2.00"))
        assert book.reserve("a", Decimal("2.00"))
        assert book.headroom == Decimal(0)

    def test_the_entry_cap_binds_before_the_risk_cap(self) -> None:
        """
        Four correlated wrappers at tiny risk still cannot all open.
        """
        book = ledger(max_total_risk=Decimal("100.00"), max_new_entries=2)
        assert book.reserve("schx", Decimal("0.10"))
        assert book.reserve("xlf", Decimal("0.10"))
        assert not book.reserve("hyg", Decimal("0.10"))
        assert "entry cap reached (2)" in book.refusals[0].reason

    def test_a_key_cannot_hold_two_reservations(self) -> None:
        """
        One position per strategy is the strategies' own rule; a second reservation
        under one key means it broke somewhere, and the ledger must not paper over it.
        """
        book = ledger()
        assert book.reserve("a", Decimal("1.00"))
        assert not book.reserve("a", Decimal("1.00"))
        assert book.refusals[0].reason == "already holds a reservation"

    def test_nothing_cannot_be_reserved(self) -> None:
        book = ledger()
        assert not book.reserve("a", Decimal(0))
        assert book.entries == 0


class TestRelease:
    def test_release_frees_the_risk_but_not_the_entry(self) -> None:
        """
        The cap is on how many positions a session starts, not how many it holds.
        """
        book = ledger(max_new_entries=1)
        book.reserve("a", Decimal("1.00"))
        assert book.release("a") == Decimal("1.00")
        assert book.total == Decimal(0)
        assert book.entries == 1
        assert not book.reserve("b", Decimal("1.00"))

    def test_releasing_nothing_is_harmless(self) -> None:
        """
        A denied order releases an entry that may never have reserved; that is not an
        error and must not become one on the event path.
        """
        assert ledger().release("nobody") == Decimal(0)


class TestConstruction:
    @pytest.mark.parametrize("kwargs", [{"max_total_risk": Decimal(0)}, {"max_new_entries": 0}])
    def test_a_ledger_that_could_grant_nothing_is_refused(self, kwargs: dict) -> None:
        with pytest.raises(ValueError, match="must be"):
            ledger(**kwargs)


class TestRecord:
    def test_the_record_names_every_refusal(self) -> None:
        """
        Who lost out and why is the input the ranking question needs.
        """
        book = ledger(max_total_risk=Decimal("1.00"))
        book.reserve("eem", Decimal("1.00"))
        book.reserve("schx", Decimal("0.50"))
        record = book.as_record()
        assert record["total"] == "1.00"
        assert record["reserved"] == {"eem": "1.00"}
        assert record["refusals"][0]["key"] == "schx"
        assert record["entries"] == 1
