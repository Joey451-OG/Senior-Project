#!/bin/bash
# Runs one privileged helper as root, for waru-helper@<name>.service (e.g. waru-helper@AdminCron).
# For development use run-privilged-helper.sh, which starts all four from a terminal.
set -euo pipefail

# The directory this script was installed into (/opt/waru), so nothing below hardcodes it.
install_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
helper_name="${1:-}"

# systemd passes the unit's instance name. Only the four known helpers are accepted, so a mistyped
# (or crafted) unit name can never make this run some other file as root.
case "$helper_name" in
    AdminCron | AdminProcesses | AdminLogoff | AdminRunCommands) ;;
    *)
        echo "usage: $0 AdminCron|AdminProcesses|AdminLogoff|AdminRunCommands" >&2
        exit 64
        ;;
esac

# -E ignores PYTHON* variables and -s skips user site-packages, so nothing outside /opt/waru can
# change what root runs. (Not -I: AdminLogoff imports AdminProcesses from its own directory.)
exec "$install_dir/venv/bin/python3" -E -s "$install_dir/privileged/$helper_name.py"
