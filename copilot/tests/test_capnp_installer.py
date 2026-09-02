"""
Contract tests for the Cap'n Proto installer's footprint.

The defect these guard against: the Linux branch honoured ``CAPNP_PREFIX`` while the
macOS branch hardcoded ``/usr/local`` with sudo and reached for Homebrew - so the same
documented invocation (``CAPNP_PREFIX="$HOME/.local" ./scripts/install-capnp.sh``) was a
self-contained user-directory install on one OS and a silent system modification on the
other. A temporary machine must be able to take the toolchain without permanent system
changes on either OS.

Text-level assertions rather than an execution test, deliberately: running the installer
downloads and compiles Cap'n Proto, and the property being protected - one prefix
contract across both branches - is a property of the script's text.

"""

from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "install-capnp.sh"


def test_no_branch_hardcodes_the_system_prefix() -> None:
    text = SCRIPT.read_text()
    assert "--prefix=/usr/local" not in text, (
        "a branch of install-capnp.sh hardcodes /usr/local; every configure call must "
        "honour CAPNP_PREFIX so a user-directory install works on every OS"
    )


def test_every_configure_call_honours_the_prefix_variable() -> None:
    configure_lines = [
        line.strip() for line in SCRIPT.read_text().splitlines() if "./configure" in line
    ]
    assert configure_lines, "the installer no longer calls configure; update this test"
    for line in configure_lines:
        assert '--prefix="${INSTALL_PREFIX}"' in line, f"configure ignores the prefix: {line}"


def test_a_requested_prefix_bypasses_homebrew() -> None:
    # Asking for a prefix means "stay out of the system"; the brew path must be gated.
    text = SCRIPT.read_text()
    assert 'CAPNP_PREFIX:-}" ]] && command -v brew' in text, (
        "the Homebrew attempt is not gated on CAPNP_PREFIX being unset"
    )
