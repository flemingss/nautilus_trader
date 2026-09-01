"""
Report this fork's divergence from upstream.

Lists every file outside ``copilot/`` that has been changed, and what each one will cost
at the next sync.

Why this exists
---------------
The overlay's original rule was that it changed *zero* upstream files, which made the
maintenance cost self-evidently nil. That rule has been relaxed: upstream changes are
allowed, so the cost is no longer zero and no longer visible on its own.

What replaces the rule is a register. Every upstream file this fork modifies is listed
in ``copilot/docs/UPSTREAM_DELTA.md`` with the reason it is modified and what it would
take to drop it. This tool compares the register against reality and fails when they
disagree, so an undocumented divergence cannot survive a test run.

It also answers the question that actually matters at sync time: **has upstream since
touched the same files?** A delta upstream never goes near is nearly free. A delta
sitting on a file upstream is actively rewriting is a bill that grows.

Usage
-----
    python -m copilot.tools.upstream_delta            # report
    python -m copilot.tools.upstream_delta --fetch    # refresh the upstream snapshot
    python -m copilot.tools.upstream_delta --check    # exit 1 on an unregistered file

Syncing with upstream is **on demand only** - the fork is deliberately held still during
development rather than tracking `develop`. So the local upstream ref is a snapshot that
ages, and the conflict line is a forecast for whenever a sync is actually chosen, never a
task list. `--fetch` refreshes it; nothing else does.

Requires an ``upstream`` remote pointing at ``nautechsystems/nautilus_trader``:

    git remote add upstream https://github.com/nautechsystems/nautilus_trader.git
    git remote set-url --push upstream DISABLED-never-push-upstream
    gh repo set-default flemingss/nautilus_trader

**All three lines matter.** The second makes pushing upstream impossible. The third is
less obvious and more dangerous: ``gh`` picks its target repository from the remotes, so
merely adding ``upstream`` makes ``gh pr create`` try to open a pull request **against the
upstream project**. Neither line survives a fresh clone, because both write to
``.git/config``.

"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


UPSTREAM_REF = "upstream/develop"
REGISTER = Path("copilot/docs/UPSTREAM_DELTA.md")
OVERLAY_PREFIX = "copilot/"

CHURN_RATIO = 5
"""
How much faster upstream must be rewriting a file than we are before the delta is called
churning.

A judgement call, not a measurement: the point is to separate a file upstream is quietly
leaving alone from one our hunks are drifting inside of.

"""

# Rows in the register start with the path in a code span, which is also how the file
# reads as prose. Parsing the document people actually maintain beats keeping a second
# machine-readable copy in step with it.
REGISTER_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|")

REGISTER_HEADING = "## The register"
"""
Only rows under this heading are entries.

The document has other tables - the risk
legend is one - whose first cell is also a code span, and reading the whole file would
register `quiet` and `churning` as if they were paths.

"""


class NoUpstreamError(RuntimeError):
    """
    The `upstream` remote or its ref is not available locally.
    """


@dataclass(frozen=True)
class FileDelta:
    """
    One upstream file this fork has changed.
    """

    path: str
    ours_added: int
    ours_removed: int
    theirs_added: int
    theirs_removed: int
    registered: bool
    committed: bool = True
    """
    False for a change that exists only in the working tree or index.

    Counted the same as a committed one on purpose. The useful moment to be told a file
    needs a register entry is while it is being edited, not after it has been committed
    and pushed - an earlier version compared only `base..HEAD` and stayed silent through
    the entire edit.

    """

    @property
    def upstream_touched(self) -> bool:
        """
        Whether upstream has changed this file since the merge base.
        """
        return bool(self.theirs_added or self.theirs_removed)

    @property
    def risk(self) -> str:
        """
        How much the next sync is likely to cost for this file.
        """
        if not self.upstream_touched:
            return "quiet"
        # Upstream rewriting far more than we did means our hunks sit in moving code.
        ours = self.ours_added + self.ours_removed
        if self.theirs_added + self.theirs_removed > CHURN_RATIO * ours:
            return "churning"
        return "touched"


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", *args],  # noqa: S607 - git resolved from PATH by design
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise NoUpstreamError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def registered_paths(register: Path = REGISTER) -> set[str]:
    """
    Paths listed in the delta register.
    """
    if not register.exists():
        return set()

    lines = register.read_text().splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == REGISTER_HEADING)
    except StopIteration:
        raise ValueError(f"{register} has no '{REGISTER_HEADING}' section to read") from None

    paths: set[str] = set()
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if match := REGISTER_ROW.match(line):
            paths.add(match.group(1))
    return paths


def _combine(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    """
    Approximate the total churn on a path with both committed and pending edits.
    """
    return (a[0] + b[0], a[1] + b[1])


def _numstat(rev_range: str) -> dict[str, tuple[int, int]]:
    """
    Return added and removed line counts per path, ignoring the overlay's directory.
    """
    return _numstat_args("diff", "--numstat", rev_range)


def _numstat_args(*args: str) -> dict[str, tuple[int, int]]:
    """
    Parse `git diff --numstat` output from an arbitrary argument list.
    """
    out: dict[str, tuple[int, int]] = {}
    raw = _git(*args)
    for line in raw.splitlines():
        added, removed, path = ([*line.split("\t", 2), "", ""])[:3]
        if not path or path.startswith(OVERLAY_PREFIX):
            continue
        # Binary files report "-"; count them as changed without a line count.
        out[path] = (
            int(added) if added.isdigit() else 0,
            int(removed) if removed.isdigit() else 0,
        )
    return out


def _uncommitted() -> dict[str, tuple[int, int]]:
    """
    Return changes present only in the working tree or index, plus untracked files.
    """
    out = _numstat_args("diff", "--numstat", "HEAD")
    for line in _git("status", "--porcelain", "-uall").splitlines():
        path = line[3:].strip()
        # A brand-new file outside the overlay is a delta too, and `git diff` will not
        # show it until it is at least staged.
        if line.startswith("??") and path and not path.startswith(OVERLAY_PREFIX):
            out.setdefault(path, (0, 0))
    return out


def collect(ref: str = UPSTREAM_REF) -> tuple[str, int, int, list[FileDelta]]:
    """
    Return the merge base, how far each side has moved, and the per-file deltas.
    """
    base = _git("merge-base", "HEAD", ref)
    committed = _numstat(f"{base}..HEAD")
    pending = _uncommitted()
    theirs = _numstat(f"{base}..{ref}")
    known = registered_paths()

    ours = dict(committed)
    for path, counts in pending.items():
        ours[path] = counts if path not in ours else _combine(ours[path], counts)

    deltas = [
        FileDelta(
            path=path,
            ours_added=added,
            ours_removed=removed,
            theirs_added=theirs.get(path, (0, 0))[0],
            theirs_removed=theirs.get(path, (0, 0))[1],
            registered=path in known,
            committed=path in committed,
        )
        for path, (added, removed) in sorted(ours.items())
    ]
    behind = int(_git("rev-list", "--count", f"{base}..{ref}"))
    ahead = int(_git("rev-list", "--count", f"{base}..HEAD"))
    return base, behind, ahead, deltas


def conflicting_paths(ref: str = UPSTREAM_REF) -> list[str]:
    """
    Paths that would conflict on a merge right now, via a dry-run merge.

    `merge-tree` writes no working-tree or index state, so this is safe to run at any
    time, including with uncommitted work present.

    """
    result = subprocess.run(  # noqa: S603
        ["git", "merge-tree", "--write-tree", "--name-only", "HEAD", ref],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return []  # clean merge
    # On conflict the first line is the tree oid, then the conflicted paths, then prose.
    lines = result.stdout.splitlines()
    noise = ("Auto-merging", "CONFLICT")
    return [line for line in lines[1:] if line and not line.startswith(noise)]


def _report(
    base: str,
    behind: int,
    ahead: int,
    deltas: list[FileDelta],
    conflicts: list[str],
) -> None:
    short = base[:12]
    when = _git("log", "-1", "--format=%ad", "--date=short", base)
    # Under on-demand syncing the local upstream ref is a snapshot that ages by design.
    # Print when it was taken, so a conflict verdict computed against a month-old ref is
    # never mistaken for a current one.
    fetched = _git("log", "-1", "--format=%ad", "--date=short", UPSTREAM_REF)
    print("Upstream delta")
    print(f"  merge base        {short}  {when}")
    print(f"  {UPSTREAM_REF:<17} +{behind} commits since base, snapshot dated {fetched}")
    print(f"  our HEAD          +{ahead} commits since base")
    print("  (snapshot only refreshes on --fetch; syncing is deliberate, not routine)")
    print()

    if not deltas:
        print("  No upstream files changed. The overlay stands alone.")
        return

    print(f"  {len(deltas)} upstream file(s) changed outside {OVERLAY_PREFIX}\n")
    labels = {
        "quiet": "upstream has not touched it since the base",
        "touched": "upstream has changed it too",
        "churning": "upstream is rewriting it far faster than we are",
    }
    for delta in deltas:
        flag = "" if delta.registered else "   << NOT IN REGISTER"
        pending = "" if delta.committed else "  (uncommitted)"
        print(f"  {delta.path}{flag}{pending}")
        print(f"      ours      +{delta.ours_added} -{delta.ours_removed}")
        if delta.upstream_touched:
            print(f"      upstream  +{delta.theirs_added} -{delta.theirs_removed}")
        print(f"      risk      {delta.risk} - {labels[delta.risk]}")
        if delta.path in conflicts:
            print("      MERGE     conflicts with upstream right now")
        print()

    if conflicts:
        print(f"  A merge of {UPSTREAM_REF} would conflict in {len(conflicts)} file(s).")
        print("  This is a forecast for the next deliberate sync, not work to do now.")
        print("  UPSTREAM_DELTA.md says what each change is for.")


def main(argv: list[str] | None = None) -> int:
    """
    Print the delta report.

    Returns a process exit code.

    """
    parser = argparse.ArgumentParser(
        prog="python -m copilot.tools.upstream_delta",
        description="Report and check this fork's divergence from upstream.",
    )
    parser.add_argument("--fetch", action="store_true", help="Fetch upstream before reporting")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any changed upstream file is missing from the register",
    )
    parser.add_argument(
        "--ref",
        default=UPSTREAM_REF,
        help=f"Ref to compare against (default {UPSTREAM_REF})",
    )
    args = parser.parse_args(argv)

    if args.fetch:
        print(f"Fetching {args.ref.split('/')[0]}...")
        _git("fetch", args.ref.split("/")[0], args.ref.split("/", 1)[1])

    try:
        base, behind, ahead, deltas = collect(args.ref)
    except NoUpstreamError as e:
        print(f"error: cannot compare against {args.ref}: {e}", file=sys.stderr)
        for hint in (
            "",
            "Add the remote (fetch only - this fork must never push upstream):",
            "  git remote add upstream https://github.com/nautechsystems/nautilus_trader.git",
            "  git remote set-url --push upstream DISABLED-never-push-upstream",
            "  git fetch upstream develop",
        ):
            print(hint, file=sys.stderr)
        return 2

    _report(base, behind, ahead, deltas, conflicting_paths(args.ref))

    unregistered = [d.path for d in deltas if not d.registered]
    if unregistered and args.check:
        print(
            f"\nFAILED: {len(unregistered)} upstream file(s) changed but not listed in {REGISTER}:",
            file=sys.stderr,
        )
        for path in unregistered:
            print(f"  {path}", file=sys.stderr)
        print(
            "\nAdd a row saying what the change is for and what it would cost to drop.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
