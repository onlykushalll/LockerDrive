"""
state_binding.py -- binds on-drive auth state files to the vault secret.

Threat this closes: pin_state.txt, recovery_state.txt and
face_templates.pkl all live on E:\\ -- the OUTER drive, which is NOT
BitLocker-encrypted (the encrypted volume is the VHDX those files sit
next to). Anyone with write access to E:\\ (another machine, another OS
session, a snoop with the drive for thirty seconds) can REPLACE those
files with their own valid copies -- their own PIN hash, their own
recovery-code hash, their own face templates -- and then walk through
the normal UI, because the real vault.secret (DPAPI-protected,
unreadable to them) is what the elevated helper actually uses to do the
unlock. The face/PIN/recovery gates would be happily checking state the
attacker wrote.

Binding: an HMAC-SHA256 over the state content, keyed by a key derived
from the vault password inside vault.secret. An attacker who can only
write files to E:\\ cannot compute a valid binding without the BitLocker
password, which they don't have -- it's DPAPI-sealed to the owner's
Windows profile. A forged or edited state file now fails the binding
check and is treated as tampered instead of silently trusted.

KDF versions (2026-10 red-team fix pass):

  * "A2:" bindings -- argon2id (hash_secret_raw, t=3, m=65536 KiB, p=4,
    32-byte output) then HMAC-SHA256. The argon2id salt is the PUBLIC
    purpose constant. A public-constant salt is normally a sin; here it
    is acceptable because the input being hardened is a high-entropy
    vault password stretched through a MEMORY-HARD function -- an
    attacker who wants to brute-force the binding key must pay 64 MiB
    and ~tens of ms PER GUESS, which is the property we actually need.
    The old KDF was PBKDF2-HMAC-SHA256 at 100k iterations (fast to
    batch on GPUs, ~6x under OWASP's current 600k recommendation).
  * legacy bare-hex bindings -- the old PBKDF2-100k construction, still
    VERIFIED so every vault saved by the previous app version keeps
    loading (verify_binding accepts both shapes; compare_digest either
    way). New writes are always "A2:".

Purpose-separated derivation labels -- a binding for one file type is
never valid for another, even though they share the same key material.
The anchor and access-log witnesses (local_anchor.py, access_log.py)
derive their own purposes so a binding for a state file can never be
replayed as an anchor MAC or a log-entry MAC, and vice versa.

Honest limits (documented, not hidden): a process ALREADY running as
the owner's Windows user can call CryptUnprotectData itself, read the
vault password, and forge bindings. This is defense against the
file-swap attacker, not against full same-user code execution -- at
that point the machine is already lost (keyloggers, memory scraping,
all of it). It also can't protect a vault whose secret was never
registered; with no key material there is nothing to bind to, and
callers fall back to the legacy no-binding behavior.

Key material is derived once per process and cached (keyed by purpose
AND KDF version, so an A2 key and a legacy key for the same purpose
coexist during the accept-both verification window); a failed
derivation (secret absent) is NOT cached, so a later call after the
drive shows up still picks it up.
"""
import hashlib
import hmac

from argon2 import low_level
from argon2.low_level import Type

# Purpose-separated derivation labels -- a binding for one file type is
# never valid for another, even though they share the same key material.
PIN_PURPOSE = b"LockerDrive/state-binding/pin/v1"
RECOVERY_PURPOSE = b"LockerDrive/state-binding/recovery/v1"
TEMPLATES_PURPOSE = b"LockerDrive/state-binding/templates/v1"
ANCHOR_PURPOSE = b"LockerDrive/state-binding/anchor/v1"
LOG_PURPOSE = b"LockerDrive/state-binding/accesslog/v1"

# Version prefixes. Bindings written since the 2026-10 fix pass start
# with "A2:" and use the argon2id KDF below; bare 64-hex bindings were
# written by the 2026-09-20 hardening pass and remain verifiable.
BINDING_PREFIX = "A2:"
_PBKDF2_ITERATIONS = 100_000  # legacy KDF -- verification only, never new writes
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_KIB = 65536
_ARGON2_PARALLELISM = 4
_ARGON2_HASH_LEN = 32

# (purpose, kdf-version) -> key. Never caches a failed derivation.
_key_cache: dict[tuple[bytes, str], bytes] = {}


def _load_password() -> str | None:
    try:
        import vault_secret
        return vault_secret.load_secret()
    except Exception:
        return None


def _derive_key_argon2(purpose: bytes) -> bytes | None:
    """Memory-hard HMAC key for a purpose (A2 bindings)."""
    cache_key = (purpose, "A2")
    if cache_key in _key_cache:
        return _key_cache[cache_key]
    password = _load_password()
    if not password:
        return None
    key = low_level.hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=purpose,  # public constant -- see module docstring for why that's OK here
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_KIB,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
        type=Type.ID,
    )
    _key_cache[cache_key] = key
    return key


def _derive_key_legacy(purpose: bytes) -> bytes | None:
    """Legacy PBKDF2-100k key -- used ONLY to verify bindings written by
    the previous app version, never for new writes."""
    cache_key = (purpose, "legacy")
    if cache_key in _key_cache:
        return _key_cache[cache_key]
    password = _load_password()
    if not password:
        return None
    key = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), purpose, _PBKDF2_ITERATIONS
    )
    _key_cache[cache_key] = key
    return key


def compute_binding(purpose: bytes, content: bytes) -> str | None:
    """"A2:" + hex HMAC of `content` under the argon2id-derived purpose
    key, or None if the key is unavailable (in which case callers must
    save WITHOUT a binding, preserving legacy compatibility rather than
    writing a binding that could never verify)."""
    key = _derive_key_argon2(purpose)
    if key is None:
        return None
    return BINDING_PREFIX + hmac.new(key, content, hashlib.sha256).hexdigest()


def verify_binding(purpose: bytes, content: bytes, claimed: str | None) -> bool | None:
    """True  -- binding valid (A2 argon2id or legacy bare-hex).
    False -- binding PRESENT and INVALID (content was tampered with or
             transplanted from another vault).
    None  -- cannot enforce right now (no key material, or the file is
             a legacy file with no binding line at all). Callers decide
             how to treat None; the legacy-compatible choice is to
             accept, since binding is an upgrade, not a format change."""
    if not claimed:
        return None
    claimed = claimed.strip()
    if claimed.startswith(BINDING_PREFIX):
        key = _derive_key_argon2(purpose)
        if key is None:
            return None
        expected = hmac.new(key, content, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, claimed[len(BINDING_PREFIX):].lower())
    key = _derive_key_legacy(purpose)
    if key is None:
        return None
    expected = hmac.new(key, content, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, claimed.lower())


def _clear_cache_for_tests() -> None:
    """Test hook only: key availability changes when vault_secret's path
    is monkeypatched between tests."""
    _key_cache.clear()
