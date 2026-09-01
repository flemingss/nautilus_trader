# Verdict records

One JSON file per validation run, written by `python -m copilot.strategies.validate --write`.

Committed on purpose. A verdict is a measurement, and the same reasoning that keeps
`calibration/out/` in the repository applies: a result that only exists in someone's
terminal cannot be checked, compared, or found again six months later.

Each record carries enough to reproduce it - the activation, the search space as declared
at the time, the seeded parameters, the fold geometry, and the bar range - so a run can be
tied to an experiment rather than to a memory of one.

## Read the flags first

| Field | Meaning |
| --- | --- |
| `costs_modelled` | `false` means the engine charged **no commission and no spread**. The number is gross of costs and is not an edge. |
| `holdout_spent` | `false` means this is walk-forward, which is repeatable. The single-use out-of-sample is a separate, deliberate act. |

A file with `costs_modelled: false` records that the machinery works, not that the premise
makes money. trade-copilot's own analysis names the cost model as the number that decides
every verdict.
