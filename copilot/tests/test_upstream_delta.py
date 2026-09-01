"""
Enforce the upstream delta register.

The overlay used to guarantee it changed zero upstream files, which made the maintenance
cost self-evident. Upstream changes are now allowed, so the guarantee is replaced by a
register — and a register nobody checks decays into fiction within a few commits.

These tests are the check. An upstream file changed without a row in
`copilot/docs/UPSTREAM_DELTA.md` fails the suite, so documenting a divergence is part of
making it rather than a good intention.

They skip rather than fail when the `upstream` remote is unavailable: a fresh clone or an
offline machine legitimately cannot compare, and a test that fails there would train
people to ignore it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from copilot.tools.upstream_delta import (
    REGISTER,
    NoUpstreamError,
    collect,
    registered_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def delta():
    """The current delta against upstream, or a skip when upstream is unreachable."""
    try:
        _base, _behind, _ahead, deltas = collect()
    except NoUpstreamError as exc:
        pytest.skip(
            f"no upstream ref to compare against ({exc}). Run: "
            "git remote add upstream https://github.com/nautechsystems/nautilus_trader.git "
            "&& git fetch upstream develop",
        )
    return deltas


def test_every_changed_upstream_file_is_registered(delta):
    """
    The rule the register exists to enforce.

    Failing here means a change was made outside `copilot/` without recording why. Add a
    row to `copilot/docs/UPSTREAM_DELTA.md` giving the reason and what it would take to
    drop the change — that row is what makes the next upstream sync a review of a short
    list rather than an archaeology exercise.
    """
    unregistered = sorted(d.path for d in delta if not d.registered)
    assert not unregistered, (
        f"{len(unregistered)} upstream file(s) changed but absent from {REGISTER}: {unregistered}"
    )


def test_the_register_has_no_stale_rows(delta):
    """
    A row for a file we no longer change is worse than no row.

    It inflates the apparent cost of a sync and sends someone looking for a change that
    is not there. Rows are only meaningful while the divergence exists.
    """
    changed = {d.path for d in delta}
    stale = sorted(registered_paths() - changed)
    assert not stale, (
        f"{REGISTER} lists path(s) this fork no longer changes: {stale}. "
        "Remove the row — the divergence is gone."
    )


def test_registered_paths_exist():
    """Catches a typo in a path, which would otherwise silently register nothing."""
    missing = sorted(p for p in registered_paths() if not (REPO_ROOT / p).exists())
    assert not missing, f"{REGISTER} lists path(s) that do not exist: {missing}"


def test_nothing_maintainer_owned_is_touched(delta):
    """
    Paths that stay off limits whatever the delta policy is.

    These belong to upstream's maintainers: release notes they curate, CI they own, and
    a roadmap that is theirs and not this fork's — `copilot/docs/ROADMAP.md` is ours.
    """
    forbidden_files = {"RELEASES.md", "ROADMAP.md"}
    forbidden_dirs = (".github/workflows/", ".github/actions/")

    offending = sorted(
        d.path for d in delta if d.path in forbidden_files or d.path.startswith(forbidden_dirs)
    )
    assert not offending, f"maintainer-owned upstream path(s) modified: {offending}"


def test_the_register_documents_its_own_enforcement():
    """
    The register must say that it is checked.

    Someone reading it needs to know a missing row breaks the build, or they will treat
    it as optional documentation and it will rot.
    """
    text = REGISTER.read_text()
    assert "test_upstream_delta" in text
    assert "Cost to drop" in text, "every row needs a stated cost of carrying the change"
