"""
Reads the dashboard's administrator allow-list, /etc/waru/auth-accounts.

Each non-blank line is the email of one Firebase account that may use the dashboard.
Lines starting with '#' are comments, and emails are compared case-insensitively.
The file is re-read on every check, so adding or removing a line takes effect on
the account's next request.

The main API is unprivileged but is in the waru group, so it reads the file itself
instead of going through a privileged helper. Anyone who can write the file can make
themselves an administrator, so it is only trusted when root alone can write it:

    sudo install -d -o root -g root -m 0755 /etc/waru
    sudo install -o root -g waru -m 0640 /dev/null /etc/waru/auth-accounts
"""

import logging
import os
import stat

logger = logging.getLogger("auth_accounts")

# Deliberately not configurable: the location is part of the trust model.
auth_accounts_file_path = "/etc/waru/auth-accounts"
maximum_auth_accounts_file_bytes = 64 * 1024

root_uid = 0


class AuthAccountsFileError(Exception):
    """The allow-list could not be used. The message is safe to show clients."""


def verifyAuthAccountsDirectory() -> None:
    """/etc/waru must be a real root-owned directory that nobody but root can write to."""
    directory_path = os.path.dirname(auth_accounts_file_path)
    try:
        directory_status = os.lstat(directory_path)
    except FileNotFoundError:
        raise AuthAccountsFileError("Auth accounts file not found") from None

    isRootOnlyDirectory = (
        stat.S_ISDIR(directory_status.st_mode)
        and directory_status.st_uid == root_uid
        and not directory_status.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    )
    if not isRootOnlyDirectory:
        logger.error("Refusing to use %s: must be a root-owned directory writable only by root", directory_path)
        raise AuthAccountsFileError("Auth accounts file has unsafe ownership or permissions")


def readAuthAccountsFileText() -> str:
    """
    Read the allow-list, but only if it is a regular root-owned file that neither its
    group nor others can write. The checks are made on the open file descriptor (fstat),
    so the file that was checked is exactly the file that gets read. O_NOFOLLOW refuses
    a symlink and O_NONBLOCK keeps a FIFO planted at the path from hanging the open.
    """
    verifyAuthAccountsDirectory()
    try:
        auth_accounts_fd = os.open(
            auth_accounts_file_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        )
    except FileNotFoundError:
        raise AuthAccountsFileError("Auth accounts file not found") from None
    except PermissionError:
        logger.error("Cannot read %s: is this process in the file's group?", auth_accounts_file_path)
        raise AuthAccountsFileError("Auth accounts file is not readable") from None
    except OSError as error:
        logger.error("Cannot open %s: %s", auth_accounts_file_path, error.strerror)
        raise AuthAccountsFileError("Auth accounts file has unsafe ownership or permissions") from None

    with os.fdopen(auth_accounts_fd, "rb") as auth_accounts_file:
        file_status = os.fstat(auth_accounts_file.fileno())
        isSafeAuthAccountsFile = (
            stat.S_ISREG(file_status.st_mode)
            and file_status.st_uid == root_uid
            and not file_status.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        )
        if not isSafeAuthAccountsFile:
            logger.error(
                "Refusing to use %s: must be a regular root-owned file writable only by root "
                "(found uid %d, gid %d, mode %o)",
                auth_accounts_file_path,
                file_status.st_uid, file_status.st_gid, stat.S_IMODE(file_status.st_mode),
            )
            raise AuthAccountsFileError("Auth accounts file has unsafe ownership or permissions")

        auth_accounts_bytes = auth_accounts_file.read(maximum_auth_accounts_file_bytes + 1)

    if len(auth_accounts_bytes) > maximum_auth_accounts_file_bytes:
        raise AuthAccountsFileError("Auth accounts file too large")
    try:
        return auth_accounts_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise AuthAccountsFileError("Auth accounts file is not valid UTF-8") from None


def parseAuthorizedEmails(auth_accounts_text: str) -> set[str]:
    authorized_emails: set[str] = set()
    for raw_line in auth_accounts_text.splitlines():
        email = raw_line.strip()
        isBlankOrComment = email == "" or email.startswith("#")
        if not isBlankOrComment:
            authorized_emails.add(email.lower())
    return authorized_emails


def readAuthorizedEmails() -> set[str]:
    """Every email in the allow-list, lowercased. Raises AuthAccountsFileError if the file can't be trusted."""
    return parseAuthorizedEmails(readAuthAccountsFileText())
