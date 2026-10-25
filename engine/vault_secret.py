"""
vault_secret.py -- wraps the BitLocker volume password with Windows DPAPI.

DPAPI (CryptProtectData/CryptUnprotectData) ties the encrypted blob to the
current Windows user profile. Nobody else -- another user account, another
machine, a copy of this file elsewhere -- can decrypt it. This is the actual
Windows API password managers use for this exact purpose; it is not custom
crypto we're relying on.

This module does NOT decide the vault is unlockable. It only answers:
"what is the BitLocker password, if this Windows user is asking for it."
The face/PIN gate in the rest of the app decides whether to ask at all.

Failure mode by design: if this file is missing, corrupted, or fails to
decrypt, load_secret() returns None rather than raising -- the caller
should fall back to asking for the password directly (or the recovery
key), never to a hardcoded default.
"""

import win32crypt
import os

SECRET_PATH = r"E:\LockerDrive\vault.secret"


def save_secret(password: str) -> None:
    """Encrypt `password` with DPAPI and write it to SECRET_PATH."""
    os.makedirs(os.path.dirname(SECRET_PATH), exist_ok=True)
    blob = win32crypt.CryptProtectData(
        password.encode("utf-8"), "LockerDrive vault password", None, None, None, 0
    )
    with open(SECRET_PATH, "wb") as f:
        f.write(blob)


def load_secret() -> str | None:
    """Return the decrypted password, or None if unavailable for any reason."""
    try:
        with open(SECRET_PATH, "rb") as f:
            blob = f.read()
        _description, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8")
    except Exception:
        return None


def secret_exists() -> bool:
    return os.path.isfile(SECRET_PATH)
