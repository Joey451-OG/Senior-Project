"""
One-time setup: turns on TOTP (authenticator app) multi-factor auth for the Firebase project.

The Firebase console has no switch for TOTP, so this calls the Identity Toolkit admin
API directly. The project must already be upgraded to Firebase Authentication with
Identity Platform (console > Authentication > Settings), which is free on the Spark plan.

Needs a service-account key with permission to edit the project's auth config
(console > Project settings > Service accounts > Generate new private key). The key
is only needed for this script; delete it afterwards, the dashboard doesn't use it.

    GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json .venv/bin/python scripts/enable-firebase-totp.py
"""

import json
import sys

import google.auth
from google.auth.transport.requests import AuthorizedSession

# How many 30-second windows either side of "now" a code is still accepted for (0-10).
adjacent_intervals = 5

# updateMask=mfa replaces the whole MFA config, which also leaves SMS second factors off.
mfa_config = {
    "mfa": {
        "providerConfigs": [
            {"state": "ENABLED", "totpProviderConfig": {"adjacentIntervals": adjacent_intervals}},
        ],
    },
}


def main() -> int:
    service_account_credentials, project_id = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not project_id:
        print("Could not tell which project the credentials belong to", file=sys.stderr)
        return 1

    session = AuthorizedSession(service_account_credentials)
    response = session.patch(
        f"https://identitytoolkit.googleapis.com/admin/v2/projects/{project_id}/config",
        params={"updateMask": "mfa"},
        json=mfa_config,
    )
    if not response.ok:
        print(f"Failed ({response.status_code}): {response.text}", file=sys.stderr)
        return 1

    print(f"TOTP multi-factor auth enabled for {project_id}:")
    print(json.dumps(response.json().get("mfa"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
