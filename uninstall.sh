#!/bin/bash
# Removes a Waru install made by install.sh.
#
#   ./uninstall.sh [--purge] [--yes]          (also installed as /opt/waru/uninstall.sh)
#
# Stops and disables waru.target and its five services, then removes their unit files, the
# /run/waru-dashboard socket directory and /opt/waru.
# Kept unless --purge is given: /etc/waru (waru.env, auth-accounts, sudo-commands), so a later
# install picks up the same settings, administrators and commands.
#
# Options:
#   --purge   also delete /etc/waru, and the `waru` user and group if install.sh created them (a group
#             that already existed, or that has other members, is left alone)
#   --yes     don't ask for confirmation
set -Eeuo pipefail

install_dir=/opt/waru
config_dir=/etc/waru
service_account=waru
units=(waru-api.service waru-helper@AdminCron.service waru-helper@AdminProcesses.service
    waru-helper@AdminLogoff.service waru-helper@AdminRunCommands.service)
unit_files=(waru.target waru-api.service waru-helper@.service)
drop_in_files=(waru-helper@AdminCron.service.d/sandbox.conf waru-helper@AdminProcesses.service.d/sandbox.conf
    waru-helper@AdminLogoff.service.d/sandbox.conf)

script_path=$(readlink -f "${BASH_SOURCE[0]}")

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
fail() {
    printf '\033[31merror:\033[0m %s\n' "$*" >&2
    exit 1
}
usage() {
    sed -n '2,/^set -/{/^set -/d;s/^# \{0,1\}//;p}' "$script_path"
    exit "${1:-0}"
}

is_purge=no
is_confirmed=no
original_arguments=("$@")
while [ $# -gt 0 ]; do
    case "$1" in
        --purge) is_purge=yes ;;
        --yes | -y) is_confirmed=yes ;;
        -h | --help) usage 0 ;;
        *) warn "unknown option: $1"; usage 64 ;;
    esac
    shift
done

if [ "$EUID" -ne 0 ]; then
    command -v sudo >/dev/null || fail "run this script as root"
    exec sudo bash "$script_path" "${original_arguments[@]}"
fi

# --- What install.sh recorded ---

# /opt/waru/.install-manifest marks /opt/waru as install.sh's; /etc/waru/installed-account says
# whether install.sh created the waru user and group (kept in /etc/waru so it outlasts a plain
# uninstall, until --purge).
manifest="$install_dir/.install-manifest"
account_record="$config_dir/installed-account"
# shellcheck disable=SC2015  # "|| true": a missing file just means "no value"
readRecordValue() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -n 1 || true; }
created_group=$(readRecordValue "$account_record" created_group)
created_user=$(readRecordValue "$account_record" created_user)

is_install_dir_ours=no
if [ -d "$install_dir" ] && [ ! -L "$install_dir" ] && [ -f "$manifest" ]; then
    is_install_dir_ours=yes
fi

# --- Confirm ---

echo "This will stop Waru and remove:"
echo "  - waru.target, waru-api.service, waru-helper@.service and their drop-ins in /etc/systemd/system"
echo "  - /etc/tmpfiles.d/waru.conf and /run/waru-dashboard"
if [ "$is_install_dir_ours" = yes ]; then
    echo "  - $install_dir"
elif [ -e "$install_dir" ]; then
    echo "  (not $install_dir: it has no install manifest, so it may not be install.sh's; remove it by hand)"
fi
if [ "$is_purge" = yes ]; then
    echo "  - $config_dir, including the administrator list and the commands list"
    if [ "$created_user" = yes ]; then
        echo "  - the '$service_account' user"
    fi
    if [ "$created_group" = yes ]; then
        echo "  - the '$service_account' group, unless other users are still in it"
    fi
else
    echo "Kept: $config_dir (use --purge to delete it, and the '$service_account' account if install.sh made it)"
fi
if [ "$is_confirmed" = no ]; then
    [ -t 0 ] || fail "not a terminal; pass --yes to uninstall without confirming"
    read -r -p "Continue? [y/N] " answer
    case "$answer" in [yY]*) ;; *) echo "Nothing changed."; exit 0 ;; esac
fi

# --- Services ---

say "Stopping and disabling Waru"
systemctl disable --now --quiet waru.target 2>/dev/null || true
systemctl stop "${units[@]}" 2>/dev/null || true

say "Removing the systemd units"
for unit_file in "${unit_files[@]}"; do
    rm -f "/etc/systemd/system/$unit_file"
done
for drop_in_file in "${drop_in_files[@]}"; do
    rm -f "/etc/systemd/system/$drop_in_file"
    # Leave the directory if an administrator put their own overrides there (systemctl edit).
    rmdir "/etc/systemd/system/$(dirname "$drop_in_file")" 2>/dev/null || true
done
for leftover_dir in /etc/systemd/system/waru-api.service.d /etc/systemd/system/waru-helper@*.service.d; do
    if [ -d "$leftover_dir" ]; then
        note "left in place (your own overrides): $leftover_dir"
    fi
done
rm -f /etc/tmpfiles.d/waru.conf
systemctl daemon-reload
systemctl reset-failed "${units[@]}" waru.target 2>/dev/null || true

# The helpers remove their own sockets on shutdown; this clears the directory itself.
if [ -d /run/waru-dashboard ] && [ ! -L /run/waru-dashboard ]; then
    rm -rf /run/waru-dashboard
fi

# --- Files ---

if [ "$is_install_dir_ours" = yes ]; then
    say "Removing $install_dir"
    rm -rf "$install_dir"
fi

if [ "$is_purge" = yes ]; then
    if [ -d "$config_dir" ] && [ ! -L "$config_dir" ]; then
        say "Removing $config_dir"
        rm -rf "$config_dir"
    fi

    if [ "$created_user" = yes ] && getent passwd "$service_account" >/dev/null; then
        say "Removing the '$service_account' user"
        userdel "$service_account"
    fi
    if [ "$created_group" = yes ] && getent group "$service_account" >/dev/null; then
        remaining_members=$(getent group "$service_account" | cut -d: -f4)
        if [ -n "$remaining_members" ]; then
            note "kept the '$service_account' group: still has members ($remaining_members)"
        else
            say "Removing the '$service_account' group"
            groupdel "$service_account"
        fi
    fi
    if [ "$created_user" != yes ] && getent passwd "$service_account" >/dev/null; then
        note "kept the '$service_account' user: it existed before install.sh ran"
    fi
fi

say "Waru is uninstalled"
if [ "$is_purge" = no ] && [ -d "$config_dir" ]; then
    note "$config_dir was kept; uninstall.sh --purge (in the repository) removes it"
fi
