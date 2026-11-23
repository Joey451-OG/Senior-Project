#!/bin/bash
# Installs Waru as a systemd service, or upgrades an existing install in place.
#
#   ./install.sh [--env-file FILE] [--add-to-group USER]...
#
# Run it from the repository as your normal user: it builds the frontend with your own Node/npm,
# then asks for sudo for everything else. What it sets up:
#   /opt/waru                   the code, a Python venv, the built frontend, start-*.sh, uninstall.sh
#   /etc/waru/waru.env          settings: Firebase web config, allowed origins, port (kept if present)
#   /etc/waru/auth-accounts     administrator e-mails, one per line   (created from packaging/config/
#   /etc/waru/sudo-commands     root commands the dashboard may run    with comments only; never replaced)
#   waru-api.service            the API, as the unprivileged `waru` service account
#   waru-helper@<name>.service  the four root helpers (AdminCron, AdminProcesses, AdminLogoff,
#                               AdminRunCommands)
#   waru.target                 starts and stops all five; enabled, so Waru starts at boot
#
# Options:
#   --env-file FILE       start /etc/waru/waru.env from FILE (e.g. your development .env) instead of
#                         .env.example. Ignored when /etc/waru/waru.env already exists.
#   --add-to-group USER   also add USER to the `waru` group (repeatable). Only needed for running the
#                         development servers (run-*.sh) against the helpers; members of the group
#                         can use the root helpers' sockets directly, so add only administrators.
#
# Waru speaks plain HTTP on 127.0.0.1; put a reverse proxy that serves HTTPS in front of it (see
# install_notes.txt). uninstall.sh removes it again.
set -Eeuo pipefail

install_dir=/opt/waru
config_dir=/etc/waru
service_account=waru
units=(waru-api.service waru-helper@AdminCron.service waru-helper@AdminProcesses.service
    waru-helper@AdminLogoff.service waru-helper@AdminRunCommands.service)
firebase_variables=(FIREBASE_API_KEY FIREBASE_AUTH_DOMAIN FIREBASE_PROJECT_ID FIREBASE_APP_ID)

script_path=$(readlink -f "${BASH_SOURCE[0]}")
repo_dir=$(dirname "$script_path")
frontend_dir="$repo_dir/src/frontend"

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

# --- Arguments ---

env_file=""
group_members=()
is_frontend_already_built=no
is_build_frontend_only=no
original_arguments=("$@")
while [ $# -gt 0 ]; do
    case "$1" in
        --env-file)
            [ $# -ge 2 ] || fail "--env-file needs a file"
            env_file=$(readlink -f "$2")
            [ -f "$env_file" ] || fail "--env-file: $2 is not a file"
            shift 2
            ;;
        --add-to-group)
            [ $# -ge 2 ] || fail "--add-to-group needs a user name"
            group_members+=("$2")
            shift 2
            ;;
        # Internal: set when this script re-runs itself under sudo or as the source tree's owner.
        --frontend-already-built) is_frontend_already_built=yes; shift ;;
        --build-frontend-only) is_build_frontend_only=yes; shift ;;
        -h | --help) usage 0 ;;
        *) warn "unknown option: $1"; usage 64 ;;
    esac
done

# --- Frontend build (never as root unless root owns the source tree) ---

buildFrontend() {
    say "Building the frontend (src/frontend)"
    if ! command -v npm >/dev/null || ! command -v node >/dev/null; then
        fail "node and npm are needed to build the frontend (package.json asks for Node ^22.18 or >=24.12).
       If they're installed for your user only (e.g. nvm), run ./install.sh without sudo."
    fi
    local node_version major minor
    node_version=$(node -p 'process.versions.node')
    IFS=. read -r major minor _ <<<"$node_version"
    if ! { [ "$major" -eq 22 ] && [ "$minor" -ge 18 ]; } &&
        ! { [ "$major" -eq 24 ] && [ "$minor" -ge 12 ]; } && ! [ "$major" -ge 25 ]; then
        fail "Node $node_version is too old or unsupported; the frontend needs ^22.18 or >=24.12"
    fi
    cd "$frontend_dir"
    # npm ci wipes node_modules and reinstalls it, so only when the lock file is newer than it.
    if [ ! -f node_modules/.package-lock.json ] || [ package-lock.json -nt node_modules/.package-lock.json ]; then
        npm ci --no-audit --no-fund
    fi
    # Type-checks, then writes the production build to src/frontend/dist.
    npm run build
    [ -f dist/index.html ] || fail "the build didn't produce src/frontend/dist/index.html"
    cd - >/dev/null
}

if [ "$is_build_frontend_only" = yes ]; then
    buildFrontend
    exit 0
fi

if [ "$EUID" -ne 0 ]; then
    # Build with this user's own PATH (nvm etc.) and file ownership, then do the rest as root.
    buildFrontend
    command -v sudo >/dev/null || fail "sudo is needed for the rest; run this script as root instead"
    say "Continuing as root"
    exec sudo bash "$script_path" --frontend-already-built "${original_arguments[@]}"
fi

if [ "$is_frontend_already_built" = no ]; then
    # Started as root: build as whoever owns the source tree, so node_modules and dist stay theirs.
    build_user=$(stat -c %U "$frontend_dir")
    if [ "$build_user" = root ]; then
        buildFrontend
    else
        runuser -u "$build_user" -- bash "$script_path" --build-frontend-only
    fi
fi

# --- From here on: root ---

trap 'warn "install.sh failed at line $LINENO. Fix the problem and run it again; re-running is safe."' ERR
# Everything installed must stay readable by the waru account, whatever root's own umask is.
umask 022

# --- Preflight ---

say "Checking the system"
[ -d /run/systemd/system ] || fail "systemd isn't running as the init system"
command -v loginctl >/dev/null || fail "loginctl (systemd-logind) is missing; the logged-in users card needs it"
command -v python3 >/dev/null || fail "python3 is missing"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' ||
    fail "Python 3.10 or newer is needed (found $(python3 -V 2>&1))"
command -v crontab >/dev/null || warn "no crontab command: the Crontabs card won't work until cron is installed"
[ -f /sys/fs/cgroup/cgroup.controllers ] || warn "not a cgroup v2 system: the logged-in users card may show no shells"
[ -f "$frontend_dir/dist/index.html" ] || fail "src/frontend/dist/index.html is missing; the frontend build didn't run"
[ ! -L "$install_dir" ] || fail "$install_dir is a symlink; refusing to install through it"
# The API trusts /etc/waru only while it's a root-owned directory nobody else can write to.
[ ! -L "$config_dir" ] || fail "$config_dir is a symlink; Waru refuses to trust it, make it a real directory"

# Two records for uninstall.sh, both root-only:
#   /opt/waru/.install-manifest  marks /opt/waru as install.sh's; also remembers the installed
#                                requirements.txt so an upgrade only runs pip when it changed.
#   /etc/waru/installed-account  whether install.sh created the waru user and group. It lives in
#                                /etc/waru because it must outlast a plain uninstall (which keeps the
#                                account and /etc/waru) until --purge removes both.
manifest="$install_dir/.install-manifest"
account_record="$config_dir/installed-account"
# shellcheck disable=SC2015  # "|| true": a missing file just means "no value"
readRecordValue() { [ -f "$1" ] && sed -n "s/^$2=//p" "$1" | tail -n 1 || true; }
created_group=$(readRecordValue "$account_record" created_group)
created_user=$(readRecordValue "$account_record" created_user)
previous_requirements_hash=$(readRecordValue "$manifest" requirements_sha256)
requirements_hash=$(sha256sum "$repo_dir/requirements.txt" | cut -d' ' -f1)
source_commit=$(git -c safe.directory="$repo_dir" -C "$repo_dir" describe --always --dirty 2>/dev/null || echo unknown)

writeManifest() {
    install -d -o root -g root -m 0755 "$install_dir"
    cat >"$manifest" <<EOF
# Written by install.sh, read by uninstall.sh. Don't edit.
requirements_sha256=$1
source_commit=$source_commit
installed_at=$(date -Iseconds)
EOF
    chmod 0600 "$manifest"
}

writeAccountRecord() {
    install -d -o root -g root -m 0755 "$config_dir"
    cat >"$account_record" <<EOF
# Written by install.sh, read by uninstall.sh --purge, which deletes the waru user and group only if
# install.sh created them. Don't edit.
created_group=${created_group:-no}
created_user=${created_user:-no}
EOF
    chmod 0600 "$account_record"
}

# --- Service account ---

say "Setting up the '$service_account' service account"
if ! getent group "$service_account" >/dev/null; then
    groupadd --system "$service_account"
    created_group=yes
    note "created group $service_account"
else
    note "group $service_account already exists"
fi
if ! getent passwd "$service_account" >/dev/null; then
    nologin_shell=$(command -v nologin || echo /usr/sbin/nologin)
    useradd --system --gid "$service_account" --home-dir /nonexistent --no-create-home \
        --shell "$nologin_shell" --comment "Waru dashboard API" "$service_account"
    created_user=yes
    note "created user $service_account (no login, no home)"
else
    note "user $service_account already exists"
    id -nG "$service_account" 2>/dev/null | grep -qw "$service_account" || usermod -aG "$service_account" "$service_account"
fi
for member in "${group_members[@]}"; do
    getent passwd "$member" >/dev/null || fail "--add-to-group: no user named $member"
    usermod -aG "$service_account" "$member"
    note "added $member to group $service_account (takes effect at their next login)"
done
# Recorded right away, so uninstall.sh knows what to remove even if a later step fails.
writeAccountRecord
writeManifest "$previous_requirements_hash"

# --- Stop a running install while its files are replaced ---

was_running=no
if systemctl is-active --quiet waru.target "${units[@]}" 2>/dev/null; then
    was_running=yes
    say "Stopping the running install for the upgrade"
    systemctl stop waru.target "${units[@]}"
fi

# --- Files in /opt/waru ---

# Replace $2 with a copy of directory $1, owned by root and read-only to everyone else.
copyTree() {
    local source=$1 destination=$2
    rm -rf "$destination.new"
    cp -R "$source" "$destination.new"
    find "$destination.new" \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} +
    chown -R root:root "$destination.new"
    chmod -R u=rwX,go=rX "$destination.new"
    rm -rf "$destination"
    mv "$destination.new" "$destination"
}

say "Installing files into $install_dir"
copyTree "$repo_dir/src/backend" "$install_dir/backend"
rm -f "$install_dir/backend/test_project.py" "$install_dir/backend/ws_test.py"
copyTree "$repo_dir/src/privileged" "$install_dir/privileged"
copyTree "$frontend_dir/dist" "$install_dir/frontend"
install -o root -g root -m 0755 "$repo_dir/start-backend.sh" "$repo_dir/start-privileged-helper.sh" \
    "$repo_dir/uninstall.sh" "$install_dir/"
install -o root -g root -m 0644 "$repo_dir/requirements.txt" "$install_dir/"

# --- Python venv ---

venv_dir="$install_dir/venv"
system_python_version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
venv_python_version=$("$venv_dir/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
if [ "$venv_python_version" != "$system_python_version" ]; then
    # Missing, broken, or made by a Python the system no longer has (e.g. after a distro upgrade).
    say "Creating the Python venv ($venv_dir, Python $system_python_version)"
    rm -rf "$venv_dir"
    python3 -m venv "$venv_dir" ||
        fail "couldn't create a venv; on Debian/Ubuntu install python3-venv (apt install python3-venv)"
    previous_requirements_hash=""
fi
if [ "$requirements_hash" != "$previous_requirements_hash" ]; then
    say "Installing Python packages (requirements.txt)"
    "$venv_dir/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check \
        -r "$install_dir/requirements.txt"
else
    note "Python packages unchanged since the last install"
fi
# The service can't write to /opt/waru, so compile the bytecode now rather than on every start.
"$venv_dir/bin/python3" -m compileall -q "$install_dir/backend" "$install_dir/privileged" >/dev/null
writeManifest "$requirements_hash"

# --- Configuration in /etc/waru ---

say "Checking the configuration in $config_dir"
# The API trusts /etc/waru only while it's a root-owned directory nobody else can write to.
install -d -o root -g root -m 0755 "$config_dir"

# Create $1 as a copy of file $5 (empty without one) if missing, then make sure it has owner $2:$3
# and mode $4.
ensureConfigFile() {
    local file_path=$1 owner=$2 group=$3 mode=$4 initial_content=${5:-/dev/null}
    [ ! -L "$file_path" ] || fail "$file_path is a symlink; Waru refuses symlinks, replace it with a regular file"
    if [ -e "$file_path" ]; then
        [ -f "$file_path" ] || fail "$file_path exists but isn't a regular file"
        chown "$owner:$group" "$file_path"
        chmod "$mode" "$file_path"
        note "kept $file_path"
    else
        install -o "$owner" -g "$group" -m "$mode" "$initial_content" "$file_path"
        note "created $file_path"
    fi
}

is_new_env_file=no
if [ ! -e "$config_dir/waru.env" ]; then
    is_new_env_file=yes
    if [ -z "$env_file" ] && [ -f "$repo_dir/.env" ] && [ -t 0 ]; then
        read -r -p "    Start /etc/waru/waru.env from your development .env? [y/N] " answer
        case "$answer" in [yY]*) env_file="$repo_dir/.env" ;; esac
    fi
fi
ensureConfigFile "$config_dir/waru.env" root "$service_account" 0640 "${env_file:-$repo_dir/.env.example}"
ensureConfigFile "$config_dir/auth-accounts" root "$service_account" 0640 "$repo_dir/packaging/config/auth-accounts"
ensureConfigFile "$config_dir/sudo-commands" root root 0644 "$repo_dir/packaging/config/sudo-commands"

# --- systemd units ---

say "Installing the systemd units"
install -o root -g root -m 0644 "$repo_dir"/packaging/systemd/waru.target \
    "$repo_dir"/packaging/systemd/waru-api.service "$repo_dir"/packaging/systemd/waru-helper@.service \
    /etc/systemd/system/
for drop_in_dir in "$repo_dir"/packaging/systemd/*.service.d; do
    target_dir="/etc/systemd/system/$(basename "$drop_in_dir")"
    install -d -o root -g root -m 0755 "$target_dir"
    install -o root -g root -m 0644 "$drop_in_dir"/*.conf "$target_dir/"
done
install -o root -g root -m 0644 "$repo_dir/packaging/tmpfiles.d/waru.conf" /etc/tmpfiles.d/waru.conf
systemd-tmpfiles --create /etc/tmpfiles.d/waru.conf
systemctl daemon-reload
systemctl enable --quiet waru.target
note "waru.target enabled (starts at boot)"

# --- Start ---

missing_variables=()
for variable_name in "${firebase_variables[@]}"; do
    # Unset, empty, or still the .env.example placeholder
    if ! grep -Eq "^[[:space:]]*$variable_name=[\"']?[^\"'[:space:]]" "$config_dir/waru.env" ||
        grep -Eq "^[[:space:]]*$variable_name=.*your-project-id" "$config_dir/waru.env"; then
        missing_variables+=("$variable_name")
    fi
done
port=$(sed -n "s/^[[:space:]]*WARU_PORT=[\"']\{0,1\}\([0-9]*\).*/\1/p" "$config_dir/waru.env" | tail -n 1)
port=${port:-3002}

# Waru's own units are stopped by now, so anything still holding these belongs to something else,
# most likely the development scripts (run-*.sh), which use the same port and socket paths.
if command -v ss >/dev/null && [ -n "$(ss -Hltn "sport = :$port" 2>/dev/null)" ]; then
    warn "something is already listening on 127.0.0.1:$port (the dev backend, run-backend-dev.sh?);
         stop it, or set WARU_PORT in $config_dir/waru.env"
fi
if pgrep -f '/privileged/Admin(Cron|Processes|Logoff|RunCommands)\.py' >/dev/null; then
    warn "privileged helpers from run-privilged-helper.sh are running; stop them, because the service's
         helpers take over the same sockets in /run/waru-dashboard"
fi

if [ ${#missing_variables[@]} -gt 0 ]; then
    say "Not starting Waru yet: /etc/waru/waru.env is missing ${missing_variables[*]}"
    [ "$was_running" = no ] || warn "Waru was running before this upgrade and is now stopped"
else
    say "Starting Waru"
    systemctl restart waru.target

    is_api_answering=no
    for _ in $(seq 30); do
        if "$venv_dir/bin/python3" -c "import sys, urllib.request
sys.exit(urllib.request.urlopen('http://127.0.0.1:$port/', timeout=2).status != 200)" 2>/dev/null; then
            is_api_answering=yes
            break
        fi
        sleep 0.5
    done
    for unit in "${units[@]}"; do
        if systemctl is-active --quiet "$unit"; then
            note "$unit: running"
        else
            warn "$unit isn't running; see: journalctl -u $unit"
        fi
    done
    if [ "$is_api_answering" = yes ]; then
        note "the dashboard answers on http://127.0.0.1:$port/"
    else
        warn "nothing answered on http://127.0.0.1:$port/ yet; see: journalctl -u waru-api.service"
    fi
fi

trap - ERR

# --- What's left to do ---

if grep -Eq '^[[:space:]]*WARU_COOKIE_SECURE=["'"'"']?false' "$config_dir/waru.env"; then
    warn "WARU_COOKIE_SECURE=false in $config_dir/waru.env lets the session cookie travel over plain HTTP;
         remove that line once the HTTPS reverse proxy is in place"
fi

say "Done"
cat <<EOF

Next steps:
EOF
if [ ${#missing_variables[@]} -gt 0 ]; then
    cat <<EOF
  - Fill in the settings (Firebase web config, WARU_ALLOWED_ORIGINS = the reverse proxy's
    https:// address, optionally WARU_PORT), then start Waru:
        sudoedit $config_dir/waru.env
        sudo systemctl start waru.target
EOF
elif [ "$is_new_env_file" = yes ]; then
    cat <<EOF
  - Check the settings copied into $config_dir/waru.env, above all WARU_ALLOWED_ORIGINS,
    which must be the reverse proxy's https:// address:
        sudoedit $config_dir/waru.env
EOF
fi
if ! grep -Eq '^[[:space:]]*[^#[:space:]]' "$config_dir/auth-accounts"; then
    cat <<EOF
  - Add administrator e-mails, one per line:
        sudoedit $config_dir/auth-accounts
EOF
fi
cat <<EOF
  - Optionally list root commands the dashboard may run, one per line:
        sudoedit $config_dir/sudo-commands
  - Point an HTTPS reverse proxy at http://127.0.0.1:$port (example in install_notes.txt),
    and add its host name to Firebase's authorized domains.
  - After changing waru.env:  sudo systemctl restart waru.target
    Status and logs:          systemctl status waru.target; journalctl -u 'waru-*' -f
    Uninstall:                sudo $install_dir/uninstall.sh [--purge]
EOF
