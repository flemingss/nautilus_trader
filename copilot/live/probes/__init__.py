"""
Investigations that produced a finding, kept runnable because the finding is evidence.

Nothing here is part of the operator's day. Each module was written to answer one
question against a live broker - what a controlled order does, which order types the
paper account accepts, how a session survives supervision, whether subscriptions
interfere, what a stranded order looks like and how it is recovered - and each answer
is recorded in the changelog and, where it changed a decision, an ADR.

They stay runnable rather than being reduced to prose because two of them are how the
adapter fixes get re-verified: ``strand_recovery`` is the confirmation of the
adopted-order cancel fix, and ``subscription_interference`` of the subscription keying
fix. The harvest procedure in ``docs/MAINTENANCE.md`` is when that matters.

The operator's day lives one directory up: ``preflight``, ``warmup``, ``run_activation``,
``cancel_working``, and the ``session``, ``node`` and ``symbology`` they are built on.

"""
