#!/usr/bin/env bash
# Render the systemd user units with this clone's path and install them.
#
#   bash copilot/ops/install-units.sh            # install and reload; enable nothing
#   bash copilot/ops/install-units.sh --enable   # and enable every timer
#
# User units, so nothing needs root; `loginctl enable-linger "$USER"` (once, with sudo) keeps
# them running when nobody is logged in. Re-run after pulling changes to the units.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_dir="$repo/copilot/ops/systemd"
target="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$target"
for unit in "$source_dir"/*.service "$source_dir"/*.timer; do
  sed "s|@REPO@|$repo|g" "$unit" > "$target/$(basename "$unit")"
done
systemctl --user daemon-reload
echo "installed $(find "$source_dir" -name '*.service' -o -name '*.timer' | wc -l) units into $target"

if [[ "${1:-}" == "--enable" ]]; then
  for timer in "$source_dir"/*.timer; do
    systemctl --user enable --now "$(basename "$timer")"
  done
  systemctl --user list-timers 'copilot-*'
fi
