"""
Attribute every filed result's return to the market and style factors, from its record.

    python -m copilot.strategies.attribute
    python -m copilot.strategies.attribute --write
    python -m copilot.strategies.attribute path/to/record.json ...

With no paths it reads the newest verdict per activation and the newest pooled record: the
results the roadmap currently quotes. Every record carries its scored trades as
``trade_rows``, so nothing here replays, reads the catalog, or prices a cost - the charge a
trade was scored at is the one filed beside it.

For each result it reports four fits, and they answer different questions:

- **Four-factor, net, exit session included and excluded** - the verdict. Is there alpha
  after costs and after charging for the market, size, value and momentum? Exits fill
  during the session, so its factor return is counted both ways and the verdict is named
  only where the two agree (see :func:`~copilot.validation.attribution.bracketed_verdict`).
- **Market only, net** - how much of the answer the market alone gives. If this and the
  four-factor alpha agree, the style tilts are not doing the work.
- **Four-factor, gross, point estimate** - the decomposition. How the gross return splits
  into cash, factor exposure and residue, before costs take their share.
- **Four-factor, net, exposure-weighted** - a sensitivity, not the verdict. Leverage spans two
  orders of magnitude, so the unweighted fit is carried by the most levered trades; weighting
  by ``1 / (L^2 h)`` asks the same question on the median trade's scale.

**Read the results as one body of evidence.** The pool re-selects over the same symbols whose
walk-forwards sit beside it, so the results are not independent tests, and nothing here adjusts
for reading many of them at 90%. None is needed to reject a family; one is needed before any
single result is called alpha (``docs/AUDIT_2026-09-11.md``, F24). A fit the method cannot make -
a premise whose trades all exit the session after entry has no full session to regress - is
filed as unattributable with its reason rather than stopping the run.

Records without ``trade_rows`` predate 2026-09-10 and are refused by name rather than
skipped, so a result cannot drop out of the table silently.

"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from copilot.validation.attribution import PRIMARY_MODEL
from copilot.validation.attribution import Attribution
from copilot.validation.attribution import DegenerateDesignError
from copilot.validation.attribution import UncoveredTradeError
from copilot.validation.attribution import attribute
from copilot.validation.attribution import bracketed_verdict
from copilot.validation.factors import FACTOR_DIR
from copilot.validation.factors import PINNED
from copilot.validation.factors import load_factors
from copilot.validation.filed_trades import from_rows


STRATEGIES_DIR = Path(__file__).parent
VERDICTS_DIR = STRATEGIES_DIR / "verdicts"
OUT_DIR = STRATEGIES_DIR / "out"
HOLDOUTS_DIR = STRATEGIES_DIR / "holdouts"


class UnattributableRecordError(ValueError):
    """
    A record carries no filed trades to attribute.
    """


def newest_records(
    verdicts_dir: Path = VERDICTS_DIR,
    out_dir: Path = OUT_DIR,
    holdouts_dir: Path = HOLDOUTS_DIR,
) -> tuple[Path, ...]:
    """
    Return the newest verdicts and pooled record, and every reassessed holdout.
    """
    newest: dict[str, Path] = {}
    for path in sorted(verdicts_dir.glob("*_*.json")):
        newest[path.stem.rsplit("_", 1)[0]] = path
    # The default pool is the constant-membership one (ADR-0031); a shifting-membership run is
    # filed for comparison and attributed only when named.
    pooled = [
        path
        for path in sorted(out_dir.glob("pooled_*.json"), key=lambda p: p.stem.rsplit("_", 1)[-1])
        if json.loads(path.read_text()).get("membership") == "constant"
    ]
    reassessed = [
        path
        for path in sorted(holdouts_dir.glob("*.json"))
        if "trade_rows" in json.loads(path.read_text()).get("reassessment", {})
    ]
    return (*newest.values(), *pooled[-1:], *reassessed)


def label_and_rows(record: dict[str, Any], path: Path) -> tuple[str, list[dict[str, Any]]]:
    """
    Name a record and return its filed trades, from whichever kind of record it is.
    """
    holdout = record.get("holdout")
    reassessment = record.get("reassessment")
    if "trade_rows" in record:
        rows, kind = record["trade_rows"], "walk-forward"
    elif isinstance(holdout, dict) and "trade_rows" in holdout:
        rows, kind = holdout["trade_rows"], "holdout"
    elif isinstance(reassessment, dict) and "trade_rows" in reassessment:
        rows, kind = reassessment["trade_rows"], "holdout, reassessed"
    else:
        raise UnattributableRecordError(
            f"{path.name} files no trade_rows. It predates filed trades (2026-09-10); "
            "refile it with the command that wrote it.",
        )
    if "activation" in record:
        return f"{record['activation']} ({kind})", rows
    return f"pooled {'+'.join(record['symbols'])}", rows


def attribute_record(path: Path, factors: dict) -> dict[str, Any]:
    """
    Run the three fits over one record and return the filed form.
    """
    record = json.loads(path.read_text())
    label, rows = label_and_rows(record, path)
    filed = from_rows(rows)
    specifications: dict[str, dict[str, Any]] = {
        "four_factor_net": {"model": PRIMARY_MODEL, "series": "net"},
        "four_factor_net_exit_excluded": {
            "model": PRIMARY_MODEL,
            "series": "net",
            "exit_session": False,
        },
        "market_net": {"model": "market", "series": "net"},
        "four_factor_gross": {"model": PRIMARY_MODEL, "series": "gross", "replicates": 0},
        "four_factor_net_exposure_weighted": {
            "model": PRIMARY_MODEL,
            "series": "net",
            "weighting": "exposure",
        },
    }
    fits: dict[str, Attribution] = {}
    unattributable: dict[str, str] = {}
    for name, specification in specifications.items():
        try:
            fits[name] = attribute(filed, factors, **specification)
        except (DegenerateDesignError, UncoveredTradeError, ValueError) as e:
            unattributable[name] = str(e)
    bracketed = {"four_factor_net", "four_factor_net_exit_excluded"} <= set(fits)
    return {
        "record": path.name,
        "label": label,
        "verdict": bracketed_verdict(
            fits["four_factor_net"],
            fits["four_factor_net_exit_excluded"],
        )
        if bracketed
        else "unattributable",
        "fits": {name: fit.as_record() for name, fit in fits.items()},
        "unattributable": unattributable,
    }


def _line(result: dict[str, Any]) -> str:
    if result["verdict"] == "unattributable":
        reasons = "; ".join(f"{k}: {v}" for k, v in result["unattributable"].items())
        return f"{result['label']:44s} unattributable ({reasons})"
    net = result["fits"]["four_factor_net"]
    excluded = result["fits"]["four_factor_net_exit_excluded"]
    market = result["fits"]["market_net"]
    gross = result["fits"]["four_factor_gross"]
    beta = next(item for item in net["loadings"] if item["factor"] == "market_excess")

    def interval(fit: dict[str, Any]) -> str:
        return f"{fit['alpha_r']:>9s} [{fit['alpha_lower_r']:>9s}, {fit['alpha_upper_r']:>9s}]"

    return (
        f"{result['label']:44s} {net['trades']:>5d}  "
        f"net {net['mean_r']:>9s}  "
        f"alpha4 {interval(net)}  "
        f"excl {interval(excluded)}  "
        f"alphaM {market['alpha_r']:>9s}  "
        f"beta {beta['estimate']:>6s}  "
        f"gross {gross['mean_r']:>8s} = cash {gross['risk_free_r']:>8s} "
        f"+ factors {gross['explained_r']:>8s} + alpha {gross['alpha_r']:>8s}  "
        f"{result['verdict']}"
    )


def main(argv: list[str] | None = None) -> int:
    """
    Attribute the named records, or the current ones, and optionally file the result.
    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.strategies.attribute",
        description="Regress filed trades on market and style factors.",
    )
    parser.add_argument("records", nargs="*", type=Path, help="Records to attribute")
    parser.add_argument("--write", action="store_true", help="File the result as JSON")
    args = parser.parse_args(argv)

    paths = tuple(args.records) or newest_records()
    factors = load_factors()
    try:
        results = [attribute_record(path, factors) for path in paths]
    except UnattributableRecordError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2

    print(
        "R per trade, net. alpha4: four-factor alpha, exit session included; excl: excluded; "
        "alphaM: market only.",
    )
    for result in results:
        print(_line(result))

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = OUT_DIR / f"attribution_{stamp}.json"
        filed = {
            "run_at": datetime.now(UTC).isoformat(),
            "factors": {"directory": FACTOR_DIR.name, "pinned": PINNED},
            "reading": {
                "verdict": (
                    "four-factor net alpha, named only where the exit session included and "
                    "excluded agree; an agreement test, neither side is a bound"
                ),
                "independence": (
                    "the pool re-selects over the symbols whose walk-forwards are listed beside "
                    "it; these are one body of evidence, not independent tests, and no "
                    "multiplicity adjustment is applied"
                ),
                "sensitivity": (
                    "four_factor_net_exposure_weighted weights each trade by 1/(L^2 h); it is "
                    "reported, not the verdict"
                ),
            },
            "results": results,
        }
        path.write_text(json.dumps(filed, indent=2) + "\n")
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "UnattributableRecordError",
    "attribute_record",
    "label_and_rows",
    "newest_records",
]
