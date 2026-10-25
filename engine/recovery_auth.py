"""
recovery_auth.py -- one-time-generated recovery code, for the scenario
where BOTH the PIN and face verification are unavailable (PIN forgotten,
camera broken, etc). Deliberately stored in its own file, separate from
pin_state.txt, specifically so that losing/corrupting/tampering with the
PIN state doesn't also destroy the one way back in.

Generated automatically the first time a PIN is successfully set (fresh
setup or a reset after loss) if one doesn't already exist. Shown to the
user exactly once at that moment -- this module never re-displays it,
and there is no way to retrieve a lost code short of generating a new
one, which invalidates the old one. If it wasn't written down, it's
gone, same as a real password.

Uses the same argon2-cffi hashing as pin_auth.py. Deliberately no
lockout/rate-limit here, unlike the PIN: the code is 96 bits of random
entropy (4 groups of 6 hex chars), vastly beyond anything brute-forceable
even without throttling -- the PIN's lockout exists specifically because
a 4-digit PIN has so little entropy that throttling is the only real
defense; that reasoning doesn't apply here.

Reusable (not single-use) by design -- for a small personal project, a
full regenerate-and-redisplay-after-every-use flow adds real complexity
for a benefit that doesn't clearly outweigh it here. Call generate()
manually any time to invalidate the current code and issue a new one
(e.g. if you suspect it was seen by someone).

Hardening pass (2026-09-20) -- the state file went from one plaintext
line on the unencrypted outer drive (E:\\) to a DPAPI-wrapped JSON
envelope, same layered design as pin_state.txt (see that module's
docstring for the full rationale):
  1. DPAPI wrap: a file forged anywhere other than this Windows profile
     fails to unwrap and the code is rejected.
  2. HMAC binding (engine/state_binding.py) keyed by vault-password
     material: a same-user edit of the stored hash fails the binding.
  The legacy single-line format still loads and is blessed in place to
  v2 on first read; engine/local_anchor.py remembers the upgrade, so a
  legacy file reappearing afterwards is a downgrade attack and is
  rejected. Honest limits documented in state_binding.py.

Red-team fix pass (2026-10) -- the audits resurrected a ROTATED code by
replaying an old copy of recovery_state.txt (the binding authenticates
content, not freshness). The envelope now carries a "generation"
counter, mirrored monotonically in the C:\\ anchor witness: every
generate() bumps it, and a file whose generation is older than the
witness (or a pre-2026-10 file with no generation, on a vault whose
witness has seen one) reads as a stale replay and is rejected --
verify() returns False, forcing a fresh generate() from an
authenticated session. The canonical form includes "generation" only
when present, so legacy files from the previous version still verify
byte-for-byte. The anchor is also marked on every v2 write, bound or
not (the same normal-order hole pin_state had).
"""
import os
import json
import secrets
import threading
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

_RECOVERY_PATH = r"E:\LockerDrive\recovery_state.txt"
_DRIVE_ROOT = r"E:\\"
_ANCHOR_KEY = "recovery_bound"

_hasher = PasswordHasher()
_ensure_lock = threading.Lock()


def _drive_connected() -> bool:
    return os.path.exists(_DRIVE_ROOT)


def exists() -> bool:
    return os.path.exists(_RECOVERY_PATH)


def _witness_generation() -> int:
    try:
        import local_anchor
        return int((local_anchor.witness_get("recovery") or {}).get("generation") or 0)
    except Exception:
        return 0


def _canonical_unbound(payload: dict) -> bytes:
    """Deterministic bytes of the payload -- identical construction on
    save and load. "generation" is included ONLY when present, so the
    legacy {"hash": ...} canonical form -- and therefore every file
    written by the previous version -- still verifies unchanged."""
    body = {"hash": str(payload["hash"])}
    if "generation" in payload:
        body["generation"] = int(payload["generation"])
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def generate() -> str:
    """Creates a new recovery code, overwriting any existing one, and
    returns the PLAINTEXT code -- the only time it is ever available.
    Caller is responsible for showing it to the user exactly once and
    not logging/persisting the plaintext anywhere else. The new file's
    generation is one past the C:\\ witness, so the PREVIOUS file (any
    copy of it an attacker may hold) can never verify again."""
    import win32crypt
    import state_binding
    import local_anchor

    code = "-".join(secrets.token_hex(3).upper() for _ in range(4))
    os.makedirs(os.path.dirname(_RECOVERY_PATH), exist_ok=True)

    payload = {"format": "v2", "hash": _hasher.hash(code),
               "generation": max(1, _witness_generation() + 1)}
    binding = state_binding.compute_binding(
        state_binding.RECOVERY_PURPOSE, _canonical_unbound(payload)
    )
    if binding:
        payload["binding"] = binding

    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encrypted = win32crypt.CryptProtectData(
        raw, "LockerDrive recovery state", None, None, None, 0
    )
    tmp_path = f"{_RECOVERY_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(encrypted)
    os.replace(tmp_path, _RECOVERY_PATH)
    local_anchor.mark(_ANCHOR_KEY)  # every v2 write, bound or not
    local_anchor.witness_update("recovery", generation=payload["generation"])
    return code


def ensure_exists() -> str | None:
    """Atomically checks for and creates a recovery code if one doesn't
    exist yet. Returns the new plaintext code if one was just created,
    None if one already existed (nothing new to show). Use this instead
    of a separate exists()-then-generate() from calling code -- that
    pattern has a real race if called concurrently (e.g. a fast double
    submit): two calls could both see "doesn't exist" and each generate
    a different code, with only one landing on disk while the other is
    shown to the user and never actually valid."""
    with _ensure_lock:
        if exists():
            return None
        return generate()


def _load_payload() -> dict:
    """Returns the canonical body {"hash": ..., "generation"?: ...} of
    the stored state (the exact bytes the binding covers). Raises
    ValueError on corrupt/downgraded/binding-invalid/stale-replayed
    files. Handles both the v2 DPAPI envelope and the legacy single-line
    format (blessing the legacy file to v2 on first read when key
    material is available)."""
    import win32crypt
    import state_binding
    import local_anchor

    with open(_RECOVERY_PATH, "rb") as f:
        raw = f.read()

    unwrapped = None
    try:
        unwrapped = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
    except Exception:
        unwrapped = None

    if unwrapped is not None:
        try:
            payload = json.loads(unwrapped.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("recovery_state.txt v2 envelope is not valid JSON")
        if not isinstance(payload, dict) or not isinstance(payload.get("hash"), str) or not payload["hash"]:
            raise ValueError("recovery_state.txt v2 envelope has unexpected content")

        body = {"hash": payload["hash"]}
        raw_gen = payload.get("generation", 0)
        if raw_gen is not None and (isinstance(raw_gen, bool) or not isinstance(raw_gen, int)):
            raise ValueError("recovery_state.txt generation must be an int")
        generation = int(raw_gen or 0)
        if generation > 0:
            body["generation"] = generation

        claimed = payload.get("binding")
        if claimed:
            check = state_binding.verify_binding(
                state_binding.RECOVERY_PURPOSE, _canonical_unbound(body), claimed
            )
            if check is False:
                raise ValueError(
                    "recovery_state.txt binding check failed (tampered or replaced)")

        witness_gen = _witness_generation()
        if 0 < generation < witness_gen:
            raise ValueError(
                "recovery_state.txt generation is older than the local witness "
                "(stale replay of a rotated recovery code)")
        if generation <= 0 and witness_gen > 0:
            # no generation = pre-2026-10 file; the witness proves a newer
            # generation was saved since -> pre-upgrade snapshot replay
            raise ValueError(
                "recovery_state.txt has no generation but the local witness is "
                "newer (pre-upgrade snapshot replay)")
        return body

    # legacy single-line plaintext
    if local_anchor.is_marked(_ANCHOR_KEY):
        raise ValueError(
            "recovery_state.txt reverted to legacy format after a bound file "
            "existed (downgrade attack or restore of an old backup)")
    with open(_RECOVERY_PATH, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\r\n") for line in f.readlines()]
    if not lines or not lines[0].startswith("hash="):
        raise ValueError("recovery_state.txt present but malformed")
    stored_hash = lines[0][len("hash="):]

    # bless in place if key material is available
    if state_binding.compute_binding(
            state_binding.RECOVERY_PURPOSE,
            json.dumps({"hash": stored_hash}, sort_keys=True, separators=(",", ":")).encode("utf-8")):
        tmp_payload = {"format": "v2", "hash": stored_hash,
                       "generation": max(1, _witness_generation())}
        binding = state_binding.compute_binding(
            state_binding.RECOVERY_PURPOSE, _canonical_unbound(tmp_payload))
        if binding:
            tmp_payload["binding"] = binding
            blob = json.dumps(tmp_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            encrypted = win32crypt.CryptProtectData(
                blob, "LockerDrive recovery state", None, None, None, 0)
            tmp_path = f"{_RECOVERY_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp_path, "wb") as f:
                f.write(encrypted)
            os.replace(tmp_path, _RECOVERY_PATH)
            local_anchor.mark(_ANCHOR_KEY)
            local_anchor.witness_update("recovery", generation=tmp_payload["generation"])
    return {"hash": stored_hash}


def verify(code: str) -> bool:
    if not _drive_connected() or not os.path.exists(_RECOVERY_PATH):
        return False
    try:
        body = _load_payload()
        _hasher.verify(body["hash"], code.strip().upper())
        return True
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHash, OSError, ValueError):
        return False
