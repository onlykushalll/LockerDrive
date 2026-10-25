"""
pin_auth.py -- PIN hashing, verification, and lockout state, in pure
Python via argon2-cffi. Replaces the compiled rust_core/vault_core.exe
binary and its rust_call() subprocess bridge in app.py/main.py.

Motivated by an Application Control policy on this machine that
intermittently blocked the standalone .exe (WinError 4551) -- rather than
keep chasing that one instance, this removes the standalone-unsigned-exe
risk category entirely.

Protocol preserved from rust_core/src/main.rs so app.py/index.html don't
need to change: same three operations (init/verify/status), same
output strings (DRIVE_NOT_CONNECTED, NOT_INITIALIZED, ALREADY_INITIALIZED,
STATE_CORRUPT, OK, WRONG:{remaining}, LOCKED:{seconds}, READY,
INITIALIZED), same lockout policy (3 wrong attempts -> escalating lockout
60s / 300s / 1800s, capped at tier 2, full reset on a correct PIN).
One NEW string, INVALID_PIN, added by the 2026-09-20 hardening pass (the
UI knows it; older callers just print it, which is still comprehensible).

Argon2's PHC-format encoded hash is self-describing (parameters are
embedded in the string itself), so this can verify a hash the Rust binary
already produced without needing to match its exact parameters.

Hardening pass (2026-09-20) -- the state file's format went from four
plaintext lines on the unencrypted outer drive (E:\\) to a DPAPI-wrapped
JSON envelope, for two stacked reasons:

  1. DPAPI wrap = ORIGIN authentication. Anyone who can write to E:\\
     (another machine, another OS session) could previously replace
     pin_state.txt with their own file -- their own PIN hash -- and the
     app would verify THEIR PIN and then happily unlock with the real
     DPAPI-protected vault secret. A file forged anywhere other than
     this Windows profile simply fails to unwrap and reads as
     STATE_CORRUPT.
  2. HMAC binding (engine/state_binding.py) = CONTENT authentication.
     The envelope carries an HMAC keyed by material derived from the
     vault password, so even a same-user edit (someone in this profile
     without the BitLocker password) can't alter the hash or the
     counters undetected.

Red-team fix pass (2026-10) -- three replay-shaped holes the audits
proved against the above (state-file snapshot replay rewinding the
lockout; the normal-order PIN-before-secret vault staying unbound and
anchor-unmarked forever; cross-process counter undercount):

  * The envelope gains a "generation" counter (monotonic: every
    init/force_reset bumps it). It is mirrored in the C:\\ anchor
    witness; a file whose generation is OLDER than the witness was
    replayed from a snapshot -> STATE_CORRUPT. The canonical form
    includes "generation" only when present, so legacy 4-field files
    (v2 files from the previous version, and 4-line v1 files) still
    verify byte-for-byte.
  * failed_attempts / lockout_until are max-merged with the anchor
    witness on every load, and written back to it on every save. A
    drive-only attacker can replay/replace pin_state.txt all they like
    -- the lockout lives on C:\\ too now, and it only counts UP. On a
    successful verify both the file AND the witness counters reset.
  * The anchor is marked on EVERY v2 write, bound or not. Previously an
    unbound save (normal setup order: PIN set before the vault secret
    was registered) left the anchor unmarked, so a later attacker
    legacy-file replacement was still blessable. Now the very first
    save witnesses the upgrade; and an unbound v2 file found on a vault
    whose secret HAS since been registered is opportunistically re-bound
    on load (same in-place upgrade the templates file already had).

  The legacy 4-line plaintext format still LOADS (nobody re-enrolls or
  gets locked out by an upgrade), and is blessed in place to v2 on that
  first load. engine/local_anchor.py (on C:\\, which a drive-only
  attacker never touches) remembers the upgrade, so a legacy file
  REAPPEARING after a bound one existed is treated as a downgrade attack
  rather than a fresh migration. Honest limits are documented in
  state_binding.py: a full same-user compromise of this PC can still
  forge everything (it can call CryptProtectData itself); deleting
  vault.secret and re-registering a DIFFERENT password invalidates
  bindings -> STATE_CORRUPT (recovery-code / re-setup path).

PIN policy (2026-10): ASCII digits only, 4-12, via \\A...\\Z (the old
"^\\d{4,12}$" accepted Arabic-Indic/fullwidth digits -- hash-distinct
from '1234', a self-lockout trap -- and '$' let a trailing newline
through). Existing vaults are untouched: verify() never re-validates
the PIN, only init/force_reset do.

Fix over the Rust version (kept from the first port): the state file's
read-modify-write is wrapped in a lock -- concurrent verify calls could
each read the same failed_attempts count and independently compute the
next state, undercounting failures and weakening the lockout, which
matters a lot for a 4-digit PIN where the lockout throttle is the
primary defense. Since this app is a single process, an in-process
threading.Lock closes that race for the realistic threat model
(pywebview's own concurrent method dispatch).
"""
import os
import re
import json
import math
import time
import threading
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

_STATE_PATH = r"E:\LockerDrive\pin_state.txt"
_DRIVE_ROOT = r"E:\\"
_MAX_ATTEMPTS = 3
_LOCKOUT_SECONDS = [60, 300, 1800]  # indexed by tier, capped at the last entry
_ANCHOR_KEY = "pin_bound"

# 4-12 ASCII digits. Short enough to remember, long enough that the
# escalating lockout is a real wall, digits-only matches what the numeric
# keypads/UI inputmode already assume. \A...\Z (not ^...$) so a trailing
# newline can't ride through, and [0-9] (not \d) so Unicode digit
# look-alikes are rejected instead of silently creating a second,
# hash-distinct PIN the user can never type again.
_PIN_RE = re.compile(r"\A[0-9]{4,12}\Z")

_hasher = PasswordHasher()  # argon2-cffi's current defaults -- only used for NEW hashes
_state_lock = threading.Lock()


def _validate_pin(pin) -> bool:
    return isinstance(pin, str) and bool(_PIN_RE.match(pin))


def _drive_connected() -> bool:
    return os.path.exists(_DRIVE_ROOT)


def _witness_pin() -> dict:
    """The pin witness slot from the C:\\ anchor ({} when absent)."""
    try:
        import local_anchor
        return local_anchor.witness_get("pin") or {}
    except Exception:
        return {}


def _canonical_unbound(state: dict) -> bytes:
    """Deterministic bytes of the state fields -- identical construction
    on save and load, so the HMAC covers exactly what was written and
    nothing presentation-level (key order, whitespace). "generation" is
    included ONLY when present, so the legacy 4-field canonical form --
    and therefore every file written by the previous version -- still
    verifies unchanged."""
    payload = {
        "hash": str(state["hash"]),
        "failed_attempts": int(state["failed_attempts"]),
        "lockout_until": float(state["lockout_until"]),
        "lockout_tier": int(state["lockout_tier"]),
    }
    if "generation" in state:
        payload["generation"] = int(state["generation"])
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _save_state(state: dict, reset_witness_counters: bool = False) -> None:
    """Writes the v2 envelope: DPAPI(json{hash, failed_attempts,
    lockout_until, lockout_tier, generation, binding?}). The binding is
    omitted when the vault secret isn't available (a fresh vault has
    nothing to bind to, and a binding that could never verify would just
    brick the file later) -- but the anchor is marked on EVERY v2 write,
    bound or not: the slot has been upgraded to v2 either way, and a
    legacy-format file reappearing later must be treated as a downgrade
    even when the v2 file was unbound. After the write, the counters and
    generation are mirrored into the C:\\ anchor witness (monotonic
    max-merge). reset_witness_counters=True (successful verify) lowers
    the witness counters back to 0 alongside the file's."""
    import win32crypt
    import state_binding
    import local_anchor

    generation = int(state.get("generation") or 0)
    if generation <= 0:
        # first save of a generation-less state (legacy v1 load or a
        # pre-2026-10 v2 file): start counting from the witness
        generation = max(1, int(_witness_pin().get("generation") or 0))
        state["generation"] = generation

    payload = {
        "format": "v2",
        "hash": str(state["hash"]),
        "failed_attempts": int(state["failed_attempts"]),
        "lockout_until": float(state["lockout_until"]),
        "lockout_tier": int(state["lockout_tier"]),
        "generation": generation,
    }
    binding = state_binding.compute_binding(
        state_binding.PIN_PURPOSE, _canonical_unbound(state)
    )
    if binding:
        payload["binding"] = binding
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encrypted = win32crypt.CryptProtectData(
        raw, "LockerDrive pin state", None, None, None, 0
    )
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    tmp_path = f"{_STATE_PATH}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(encrypted)
    os.replace(tmp_path, _STATE_PATH)  # atomic same-volume rename
    local_anchor.mark(_ANCHOR_KEY)  # every v2 write, bound or not
    local_anchor.witness_update(
        "pin",
        failed_attempts=int(state["failed_attempts"]),
        lockout_until=float(state["lockout_until"]),
        generation=generation,
    )
    if reset_witness_counters:
        # a correct PIN resets the lockout state everywhere it's mirrored;
        # plain witness_update only ever max-merges, so lower explicitly.
        # The generation is intentionally NOT reset (monotonic forever).
        local_anchor.witness_update(
            "pin", overwrite=True, failed_attempts=0, lockout_until=0.0
        )


def _strict_int(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"expected int, got {type(v).__name__}")
    return int(v)


def _strict_float(v) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"expected float, got {type(v).__name__}")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("non-finite float")
    return f


def _parse_v1_lines(lines: list) -> dict:
    """The original Rust-binary format: four bare positional lines -- the
    raw PHC-format hash string, then failed_attempts, lockout_until,
    lockout_tier. Confirmed against a real file, not assumed (an earlier
    version of this parser assumed 'key=value' and was wrong)."""
    if len(lines) < 4 or not lines[0]:
        raise ValueError("pin_state.txt present but malformed (expected 4 lines)")
    try:
        return {
            "hash": lines[0],
            "failed_attempts": int(lines[1]),
            "lockout_until": float(lines[2]),
            "lockout_tier": int(lines[3]),
        }
    except (ValueError, IndexError) as e:
        raise ValueError(f"pin_state.txt present but fields didn't parse: {e}")


def _assert_sane_counters(state: dict, origin: str) -> None:
    """FE-1 hardening (2026-10 red-team verification): counters must be
    finite and non-negative. A non-finite lockout_until (inf/NaN --
    reachable only by a same-user edit of the DPAPI blob or the C:\\
    anchor) previously escaped as an OverflowError from int(inf - now);
    fail closed as STATE_CORRUPT instead of crashing."""
    if (not math.isfinite(state["lockout_until"])
            or state["failed_attempts"] < 0 or state["lockout_tier"] < 0):
        raise ValueError(f"{origin} counters are non-finite or negative")


def _load_state() -> dict | None:
    """None => not yet initialized (file doesn't exist). Raises ValueError
    on a file that exists but is unusable -- corrupt, downgraded, or
    binding-invalid -- callers map that to STATE_CORRUPT.

    Read order:
      v2 -- DPAPI-wrapped JSON envelope (binding check, witness merge,
            generation replay check, opportunistic rebind/re-version)
      v1 -- legacy plaintext 4-line format; accepted and blessed in
            place (rewritten as v2) on first load, unless
            local_anchor says a bound file already existed -- a legacy
            file reappearing after that is a downgrade attack."""
    import win32crypt
    import state_binding
    import local_anchor

    if not os.path.exists(_STATE_PATH):
        return None
    try:
        with open(_STATE_PATH, "rb") as f:
            raw = f.read(1024 * 1024 + 1)
    except OSError as e:
        raise ValueError(f"pin_state.txt unreadable: {e}")
    if len(raw) > 1024 * 1024:
        raise ValueError("pin_state.txt exceeds 1 MiB size cap")

    # v2: DPAPI-wrapped JSON envelope
    unwrapped = None
    try:
        unwrapped = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
    except Exception:
        unwrapped = None

    if unwrapped is not None:
        try:
            payload = json.loads(unwrapped.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ValueError("pin_state.txt v2 envelope is not valid JSON")
        if not isinstance(payload, dict) or payload.get("format") != "v2":
            raise ValueError("pin_state.txt v2 envelope has unexpected content")
        try:
            if not isinstance(payload.get("hash"), str) or not payload["hash"]:
                raise ValueError("hash must be non-empty str")
            state = {
                "hash": payload["hash"],
                "failed_attempts": _strict_int(payload["failed_attempts"]),
                "lockout_until": _strict_float(payload["lockout_until"]),
                "lockout_tier": _strict_int(payload["lockout_tier"]),
            }
            raw_gen = payload.get("generation", 0)
            generation = _strict_int(raw_gen) if raw_gen is not None else 0
        except (KeyError, TypeError, ValueError, OverflowError) as e:
            raise ValueError(f"pin_state.txt v2 fields didn't parse: {e}")
        _assert_sane_counters(state, "pin_state.txt v2")
        if generation > 0:
            state["generation"] = generation

        claimed = payload.get("binding")
        if claimed:
            check = state_binding.verify_binding(
                state_binding.PIN_PURPOSE, _canonical_unbound(state), claimed
            )
            if check is False:
                raise ValueError(
                    "pin_state.txt binding check failed (tampered or replaced)")

        witness = _witness_pin()
        witness_gen = int(witness.get("generation") or 0)
        if 0 < generation < witness_gen:
            raise ValueError(
                "pin_state.txt generation is older than the local witness "
                "(stale replay of a previous PIN state)")
        if generation <= 0 and witness_gen > 0:
            # A file with NO generation can only predate the 2026-10 fix
            # pass; the witness proves a newer generation existed since.
            # That combination is a pre-fix snapshot replayed after a
            # reset/rotation -> reject rather than resurrect an old PIN.
            raise ValueError(
                "pin_state.txt has no generation but the local witness is "
                "newer (pre-upgrade snapshot replay)")

        # Replay-proof the counters: whatever the file says, the lockout
        # state is at least what the C:\\ witness last saw.
        # Witness values are same-user-writable (C:\\) -- sanitize before
        # merging: non-numeric, negative, or non-finite entries (a tampered
        # anchor) must not crash the app or lock the user out forever;
        # they are ignored in favor of the file's own counters.
        try:
            w_attempts = int(witness.get("failed_attempts") or 0)
        except (TypeError, ValueError, OverflowError):
            w_attempts = 0
        try:
            w_lockout = float(witness.get("lockout_until") or 0.0)
        except (TypeError, ValueError, OverflowError):
            w_lockout = 0.0
        if not math.isfinite(w_lockout):
            w_lockout = 0.0
        state["failed_attempts"] = max(state["failed_attempts"], max(w_attempts, 0))
        state["lockout_until"] = max(state["lockout_until"], max(w_lockout, 0.0))

        if generation <= 0 or (not claimed and state_binding.compute_binding(
                state_binding.PIN_PURPOSE, _canonical_unbound(state))):
            # Opportunistic in-place upgrade: assign the generation (and
            # a binding, now that key material exists) so this file is
            # replay-proof from here on. Mirrors what the templates file
            # already did for unbound v2 saves.
            if generation <= 0:
                state["generation"] = max(1, witness_gen)
            _save_state(state)
        return state

    # v1: legacy plaintext -- reject outright once a bound file has
    # existed; a downgrade to the unprotected format is not a migration.
    if local_anchor.is_marked(_ANCHOR_KEY):
        raise ValueError(
            "pin_state.txt reverted to legacy format after a bound file "
            "existed (downgrade attack or restore of an old backup)")

    with open(_STATE_PATH, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\r\n") for line in f.readlines()]
    state = _parse_v1_lines(lines)
    _assert_sane_counters(state, "pin_state.txt legacy")

    # Bless in place: rewrite as v2 (DPAPI + binding when possible) so
    # the protection starts with this very run. If no key material is
    # available (fresh vault, secret not registered yet), leave the v1
    # file as-is -- there is nothing to bind to yet.
    if state_binding.compute_binding(state_binding.PIN_PURPOSE, _canonical_unbound(state)):
        state["generation"] = max(1, int(_witness_pin().get("generation") or 0))
        _save_state(state)
    return state


def init(pin: str) -> str:
    if not _drive_connected():
        return "DRIVE_NOT_CONNECTED"
    if not _validate_pin(pin):
        return "INVALID_PIN"
    with _state_lock:
        if os.path.exists(_STATE_PATH):
            return "ALREADY_INITIALIZED"
        os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
        state = {
            "hash": _hasher.hash(pin),
            "failed_attempts": 0,
            "lockout_until": 0.0,
            "lockout_tier": 0,
            "generation": max(1, int(_witness_pin().get("generation") or 0) + 1),
        }
        _save_state(state)
    return "INITIALIZED"


def verify(pin: str) -> str:
    if not _drive_connected():
        return "DRIVE_NOT_CONNECTED"
    with _state_lock:
        try:
            state = _load_state()
        except ValueError:
            return "STATE_CORRUPT"
        if state is None:
            return "NOT_INITIALIZED"

        now = time.time()
        if state["lockout_until"] > now:
            return f"LOCKED:{int(state['lockout_until'] - now)}"

        try:
            _hasher.verify(state["hash"], pin)
            correct = True
        except VerifyMismatchError:
            correct = False
        except (VerificationError, InvalidHash):
            return "STATE_CORRUPT"

        if correct:
            state["failed_attempts"] = 0
            state["lockout_until"] = 0.0
            state["lockout_tier"] = 0
            _save_state(state, reset_witness_counters=True)
            return "OK"

        state["failed_attempts"] += 1
        if state["failed_attempts"] >= _MAX_ATTEMPTS:
            tier = state["lockout_tier"]
            duration = _LOCKOUT_SECONDS[min(tier, len(_LOCKOUT_SECONDS) - 1)]
            state["lockout_until"] = now + duration
            state["lockout_tier"] = min(tier + 1, len(_LOCKOUT_SECONDS) - 1)
            state["failed_attempts"] = 0
            _save_state(state)
            return f"LOCKED:{duration}"

        _save_state(state)
        return f"WRONG:{_MAX_ATTEMPTS - state['failed_attempts']}"


def force_reset(pin: str) -> str:
    """Overwrites any existing PIN state unconditionally, no
    ALREADY_INITIALIZED check. Callers are responsible for the security
    gate -- this exists specifically for use from an already-
    authenticated session (e.g. Settings, reachable only once unlocked),
    where re-deriving that check here would be redundant with the
    authentication that already happened to get there. Every reset bumps
    the generation, so a snapshot of the pre-reset file can never be
    replayed back in afterwards (the C:\\ witness remembers the bump)."""
    if not _drive_connected():
        return "DRIVE_NOT_CONNECTED"
    if not _validate_pin(pin):
        return "INVALID_PIN"
    with _state_lock:
        state = {
            "hash": _hasher.hash(pin),
            "failed_attempts": 0,
            "lockout_until": 0.0,
            "lockout_tier": 0,
            "generation": max(1, int(_witness_pin().get("generation") or 0) + 1),
        }
        _save_state(state, reset_witness_counters=True)
    return "INITIALIZED"


def status() -> str:
    if not _drive_connected():
        return "DRIVE_NOT_CONNECTED"
    with _state_lock:
        try:
            state = _load_state()
        except ValueError:
            return "STATE_CORRUPT"
        if state is None:
            return "NOT_INITIALIZED"
        now = time.time()
        if state["lockout_until"] > now:
            return f"LOCKED:{int(state['lockout_until'] - now)}"
        return "READY"
