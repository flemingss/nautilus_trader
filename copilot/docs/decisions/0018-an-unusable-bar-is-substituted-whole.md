# 18. An unusable bar is substituted whole, from a versioned table

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** Project owner, delegating the shape after the defect was measured

## Context

[ADR-0017](0017-the-evaluation-window-is-pinned-at-both-ends.md) made it possible to keep
the catalog current without moving research, and the live warm-up needs it current. The
first attempt to bring it past 2025-12-31 failed, and not for a reason anyone predicted.

**Marketstack's 2026 rows carry eleven sessions it cannot price.** Eight have a null
close (2026-06-09 and 06-10 for AAPL, MSFT and SPY; 06-15 for MSFT and SPY). Two have a
close of exactly `0.0` for SPY, on 2026-04-07 and 04-08, with open, high and low all sane.
One - MSFT on 2026-01-15 - is field-shifted: its open repeats the previous session's and
the true open sits in the high slot, which reads as an incoherent bar because it is one.

A narrow re-fetch of those exact dates returns identical rows, so this is the vendor's
stored data rather than a bulk-query artefact. AAPL has a close on 2026-06-15 where MSFT
and SPY do not, so it is per-symbol-per-day rather than an outage. Waiting does not fix it.

The ingestion gate refused the batch at a 2.17% rejection ratio against its 2% threshold.
That is the gate working - a half-ingested history is worse than none - and it is also
terminal: nothing writes, and the catalog cannot reach the sessions the warm-up needs.

The bars exist elsewhere. Databento's `EQUS.SUMMARY` covers 2024-07 onward, and the
listing venues' `statistics` schema carries the official closing auction print. Priced
before running, as [ADR-0015](0015-databento-is-the-intraday-source-only.md) requires: the
whole 2026 pull is **$0.0019**.

The conflict is that ADR-0015 says Databento is the intraday source *and only* the
intraday source. That was decided against a different question - whether to replace the
daily series - and it should not be read as forbidding a repair it never considered.

## Decision

**Where the daily vendor cannot produce a readable bar, the whole price bar is taken from
Databento and recorded in `copilot/data/substitutions.py`, a versioned table that
reproduces from source.**

- **The price bar is taken whole.** Open, high, low and volume from the consolidated
  daily bar, close from the listing venue's official auction print. Splicing Marketstack's
  open onto Databento's close would produce a bar that is neither vendor's and can be
  checked against neither. On all eleven, the trade-derived close and the auction print
  agree exactly, which is itself a cross-check.
- **The corporate-action fields stay with the vendor that sells them.** Databento sells no
  splits or dividends, so `split_factor` and `dividend` are left as Marketstack returned
  them. That axis is [ADR-0016](0016-corporate-actions-are-applied-on-read.md)'s, and
  taking those fields from a source that does not have them would be worse than taking
  nothing.
- **Only absences, never disagreements.** Every entry names a shape the vendor's row
  cannot be read at all - null, zero, or shifted - and every one fails a gate that already
  existed. A substitution is not a preference between two sources that both priced the
  day. The reason strings are constants, so adding a fourth is a diff someone must
  justify, and a test pins that.
- **Substituted rows go through the gate, not around it.** `apply_to` runs before
  `normalize`, so a repaired bar still has to be positive, coherent and penny-aligned. If
  this table were ever wrong, the gate is what catches it.
- **The run says what it did.** The backfill prints every substitution with its reason and
  its source, and prints the table entries that matched no vendor row - because an entry
  that never applies leaves a session missing while rejecting nothing, which is the one
  way this could fail silently.
- **`python -m copilot.data.substitutions` re-fetches and compares**, exiting non-zero on
  any disagreement. Verified 2026-09-04: **11/11 reproduce from source.**

## Consequences

- **The catalog now spans 2005-01-03 to 2026-09-03**, 5,452 bars per symbol. The carve
  holds exactly as ADR-0017 promised: 4,280 development, 1,003 holdout, 169 clipped,
  share 18.99% - unchanged. **All six verdicts re-run bit-identical** against a catalog
  169 sessions longer than the one they were filed against, which is the first end-to-end
  proof of that ADR rather than a test of it.
- **The daily series now has mixed provenance, and says so.** Eleven of 5,452 bars per
  symbol come from a second source. That is the cost, and the table plus the ingestion
  report are what keep it from being a hidden one.
- **This does not reach before 2024-07-01.** `EQUS.SUMMARY` starts there, so the same
  defect in 2019 would have no repair. The 2005-2018 series remains irreplaceable and
  unbacked by any second source, which is why the catalog is a backup obligation.
- **The seventeen bad closes the 2018-2025 audit found are not substituted.** They are
  disagreements rather than absences, they sit inside the pinned evaluation window, and
  moving them would move filed verdicts. Correcting them is a separate decision with a
  re-validation attached.
- **The vendor is now known to ship unreadable rows, and that is a standing condition.**
  A close of `0.0` is the shape to watch: a null fails any schema check, but zero is a
  number, and a gate testing only for presence would have written it. Every future
  backfill can trip this, so the daily append this unblocks has to treat a rejection as
  routine rather than exceptional.
