# Waru

Waru is a web dashboard for monitoring and administering a Linux server from the browser. It shows
what the machine is doing (load, temperature, memory, processes, who is logged in, scheduled jobs)
and handles routine administration (stopping a process, logging a user off, editing a crontab,
running a maintenance command), so you don't have to open an SSH session for everyday checks.

Waru started from homelab experience: one page to watch and manage a Linux box.

## Features

**Monitoring** (updates every second)
- CPU load and CPU temperature, against the sensor's own high and critical limits
- Memory and swap usage
- Every process, grouped by user, with its share of CPU, memory, disk and I/O
- Logged-in users: when they logged in, for how long, their sessions, current shell and process count
- Every user's crontab, updated live when anyone changes one

**Administration**
- Stop a process
- Log a user off, or force-log them off if their programs won't quit
- Edit any user's crontab except root's. If someone else changed it while you were editing, Waru
  warns you before you overwrite their changes.
- Run maintenance commands as root, such as `apt update`, from a list that only the server's
  administrator can edit, and watch the output live

**Security**
- Sign-in through Firebase Authentication, with a mandatory authenticator-app (TOTP) second factor
- Only e-mail addresses on a root-controlled allow-list get access
- The web-facing part never runs as root. Root actions go through four small helper services that
  only accept a fixed set of requests.
- Root itself is off limits: no killing root processes, no logging off root, no editing root's
  crontab, and only commands from the administrator's list can be run

## How it works

```
Browser ──HTTPS──> reverse proxy (Caddy, nginx, ...)
                        │ HTTP, 127.0.0.1:3002
                        ▼
                   Waru API  (FastAPI, runs as the unprivileged `waru` account;
                        │      also serves the web page)
                        │ Unix sockets in /run/waru-dashboard, `waru` group only
                        ▼
                   4 helpers (run as root): crontabs · processes · log off · commands
```

The backend is Python (FastAPI) and the frontend is Vue 3 with TypeScript. Waru runs as a set of
systemd services under `waru.target`.

## Requirements

- Linux with systemd (logind) and cgroup v2, such as a current Ubuntu or Debian
- `cron`, `git`, `sudo`
- Python 3.10 or newer with venv (Debian/Ubuntu: `sudo apt install python3-venv`)
- Node.js ^22.18 or 24.12 and newer, with npm, to build the web page during installation
- A Firebase project (the free Spark plan is enough) for sign-in
- A reverse proxy that serves HTTPS. Waru only speaks plain HTTP on `127.0.0.1`.

## Installation

### 1. Create the Firebase project

1. In the [Firebase console](https://console.firebase.google.com/), create a project and add a
   **Web app**. Keep its config values (API key, auth domain, project ID, app ID).
2. **Authentication → Sign-in method:** enable **Email/Password** and **Google**.
3. **Authentication → Settings**, in the list on the left:
   - **Domains → Authorized domains:** add the host name you'll open Waru at, e.g.
     `dash.example.com`. Without it, sign-in from that address is refused.
   - **User account management → Password policy:** set the password requirements for new accounts
     (see [Password requirements](#password-requirements)).
   - **User account management → User account linking:** leave the default, **Link accounts that
     use the same email**, so signing in with Google and with a password gives one account.
4. Upgrade the project to **Firebase Authentication with Identity Platform**, which two-factor
   sign-in requires. It's free on the Spark plan, with a limit of 3,000 daily active users, and
   nothing needs migrating. The upgrade is started from **Authentication → Settings**. There's no
   separate menu entry for it: the features that need it (**Blocking functions**, **User activity
   logging**) show a lock icon, and opening one offers the upgrade. If the project isn't upgraded,
   the script in [step 4](#4-turn-on-authenticator-app-sign-in) reports an error.

#### Password requirements

Waru takes its password rules from Firebase, so you set them in one place: **Authentication →
Settings → Password policy**.

- **Rules:** require any of an uppercase letter, a lowercase letter, a number and a special character,
  and set the minimum length (6–30 characters) and maximum length (up to 4,096). Without a policy,
  Firebase only requires 6 characters.
- **Enforcement:** choose **Require**, so Firebase itself refuses passwords that break the rules.
  With **Notify**, only Waru's sign-up form enforces them.
- **Force upgrade on sign in:** leave it off unless you mean to block existing accounts whose
  passwords break the rules until they change them.

Changing the policy later doesn't touch passwords already in use; without **Force upgrade on sign
in**, their owners keep signing in as before. Accounts that sign in with Google have no Waru
password, so Google's own rules apply.

### 2. Run the installer

Download the installer and run it as your normal user, not with sudo. It builds the web page with
your own Node.js, then asks for your sudo password for the rest:

```bash
curl -fsSLO https://raw.githubusercontent.com/Joey451-OG/Senior-Project/main/install.sh
bash install.sh
```

The installer downloads Waru from GitHub into a temporary directory and installs it into
`/opt/waru`. It creates the `waru` service account and the configuration directory `/etc/waru`,
and sets Waru to start at boot. Running it again later upgrades Waru and keeps your settings.

| Option | Meaning |
|---|---|
| `--branch NAME` | Install a branch or tag other than `main` |
| `--env-file FILE` | Fill in `/etc/waru/waru.env` from an existing settings file |
| `--source DIR` | Install from a local copy of the repository instead of downloading it |
| `--add-to-group USER` | Add `USER` to the `waru` group (only needed for development) |

Waru doesn't start until its settings are filled in (next step).

### 3. Configure

Edit the settings file:

```bash
sudoedit /etc/waru/waru.env
```

| Setting | Value |
|---|---|
| `FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `FIREBASE_APP_ID` | Your Firebase web app's config |
| `WARU_ALLOWED_ORIGINS` | The address you'll open Waru at, exactly as the browser shows it, e.g. `https://dash.example.com` |
| `WARU_PORT` | Optional. The local port Waru listens on (default `3002`) |

Then start Waru:

```bash
sudo systemctl start waru.target
```

### 4. Turn on authenticator-app sign-in

The Firebase console has no switch for TOTP, so Waru ships a small script for it. In the Firebase
console, open **Project settings → Service accounts → Generate new private key**, then run:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json \
    /opt/waru/venv/bin/python /opt/waru/scripts/enable-firebase-totp.py
```

You only need to do this once per Firebase project. Delete the key file afterwards; Waru itself
doesn't use it.

### 5. Put HTTPS in front of Waru

Point a reverse proxy at `http://127.0.0.1:3002`. It must pass WebSockets through and must not
buffer responses. With [Caddy](https://caddyserver.com/), which also gets the certificate for you:

```
dash.example.com {
    reverse_proxy 127.0.0.1:3002 {
        flush_interval -1
    }
}
```

An nginx example is in `/opt/waru/install_notes.txt`.

### 6. Add administrators and commands

Two files in `/etc/waru` control access. Both are re-read on every request, so changes take effect
without a restart. Lines starting with `#` are comments.

- **Administrators**, one e-mail address per line:
  ```bash
  sudoedit /etc/waru/auth-accounts
  ```
  Everyone listed can use every feature, including running the listed commands as root.
- **Commands** the dashboard may run as root, one per line, e.g. `apt update`:
  ```bash
  sudoedit /etc/waru/sudo-commands
  ```
  Commands can't answer prompts, so add `-y` or similar where a command would ask. Don't put `sudo`
  in front of them; they already run as root.

## First sign-in

1. Open your Waru address (e.g. `https://dash.example.com`).
2. Choose **Continue with Google**, or **Create an account** with an e-mail address and password.
   The sign-up form lists the server's password requirements and ticks each one off as you type;
   **Create account** won't accept a password until every item is met. New e-mail accounts must then
   confirm their address through the link Firebase sends. If you forget your password, **Forgot
   password?** e-mails you a reset link.
3. Set up an authenticator app (Google Authenticator, Aegis, 1Password, etc.) by scanning the QR
   code. From then on, every sign-in asks for a 6-digit code.
4. If your e-mail isn't in `/etc/waru/auth-accounts` yet, you'll see a "not yet an administrator"
   page. Once the server's administrator adds you, the page opens the dashboard by itself within
   about 15 seconds.

## Using the dashboard

Each card has a status badge (**Live**, **Connecting** or **Offline**) for its live connection. The
☀/☾ button in the top bar switches between light and dark themes.

- **CPU | Memory:** switch between the CPU gauge (outer ring: load, inner ring: temperature) and the
  memory gauge (outer ring: RAM, inner ring: swap). Rings turn amber, then red, as values get high.
- **Logged In:** click a user to see their processes. **Log out** asks all of the user's programs to
  quit. If some keep running, **Force Logout** stops them outright.
- **Processes:** filter by name, user or PID, sort by any column, and use **Above N %** to hide
  idle processes (1 % by default). **Kill** stops a process. A "—" means the value can't be read
  without root.
- **Admin Commands:** **Run** shows a terminal window with the command's live output. Closing the
  window doesn't stop the command; it finishes on the server.
- **Crontabs:** **Edit** opens a user's crontab. If it changes while you're editing, Waru tells you
  and offers **Load latest**. Saving over someone else's changes always asks first.

Waru never offers actions on root: no kill button for root's processes, no log-off for root, and no
editing root's crontab.

## Managing Waru

| Task | Command |
|---|---|
| Status | `systemctl status waru.target` |
| Logs | `journalctl -u 'waru-*' -f` |
| Restart (after editing `waru.env`) | `sudo systemctl restart waru.target` |
| Stop / start | `sudo systemctl stop waru.target` / `sudo systemctl start waru.target` |
| Upgrade | Download and run `install.sh` again |
| Uninstall, keeping `/etc/waru` | `sudo /opt/waru/uninstall.sh` |
| Uninstall everything | `sudo /opt/waru/uninstall.sh --purge` |

`--purge` also deletes `/etc/waru` and the `waru` account. If you uninstalled without `--purge` and
want to remove the rest later, download `uninstall.sh` from the repository and run it with `--purge`.

## Troubleshooting

| Problem | What to check |
|---|---|
| The page shows a startup error | The Firebase values in `/etc/waru/waru.env` are missing or wrong. Fix them, then restart Waru. |
| Sign-in fails or every request is refused | `WARU_ALLOWED_ORIGINS` must match the address bar exactly (scheme, host and port), and the host must be in Firebase's authorized domains. |
| "Not yet an administrator" | Your e-mail isn't in `/etc/waru/auth-accounts`, or that file is writable by someone other than root, in which case Waru ignores it. |
| Admin Commands shows an error | `/etc/waru/sudo-commands` must be owned by root with mode `0644`, or Waru refuses the whole file. |
| "Privileged helper unavailable" | One of the root helpers isn't running: `systemctl status 'waru-helper@*'`. |
| Nothing on port 3002 | Something else is using the port. Set `WARU_PORT` in `waru.env` and point the proxy at it. |

## Development

To run Waru from a checkout with live reload instead of installing it:

```bash
python3 -m venv .venv && .venv/bin/pip install -r dev-requirements.txt
(cd src/frontend && npm ci)
./run-privilged-helper.sh                # the root helpers (asks for sudo)
./run-backend-dev.sh                     # the API on 127.0.0.1:3002, reads .env
(cd src/frontend && npm run dev)         # the page on port 3001
```

Copy `.env.example` to `.env` first and fill it in; more setup details are in
[install_notes.txt](install_notes.txt). The development scripts use the same sockets and port as the
installed service, so stop `waru.target` first. To test a checkout as an installed service, run
`./install.sh --source .`. Backend tests: `cd src/backend && ../../.venv/bin/python -m pytest`.

## License

[GPL-3.0](LICENSE)
