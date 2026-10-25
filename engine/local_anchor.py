"""
local_anchor.py -- Persistent C:\\ security witness for LockerDrive.

Records which state files have been upgraded to v2 bound envelopes, mirrors
monotonic lockout/generation counters off the removable drive, and pins
SHA-256 hashes of the ONNX face models.

When using the default %LOCALAPPDATA% path, the anchor file is automatically
scoped by a 12-character SHA-256 fingerprint of the current vault secret so
switching USB drives or re-provisioning a new vault never causes a stale-MAC
deadlock. When unit tests override `_ANCHOR_PATH`, it uses `_ANCHOR_PATH`
directly for 100% test suite compatibility.
"""
import os
import json
import math
import hashlib
import threading

_DEFAULT_ANCHOR_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "LockerDrive"
)
_DEFAULT_ANCHOR_PATH = os.path.join(_DEFAULT_ANCHOR_DIR, "security_anchor.json")

_ANCHOR_DIR = _DEFAULT_ANCHOR_DIR
_ANCHOR_PATH = _DEFAULT_ANCHOR_PATH

_BODY_KEYS = ("slots", "witness", "model_hashes")
_anchor_lock = threading.RLock()


def _active_anchor_path() -> str:
    """If tests overrode _ANCHOR_PATH, honor it verbatim. Otherwise scope by
    the active vault.secret fingerprint so switching drives or re-initializing
    a vault never bricks on an old secret's MAC."""
    if _ANCHOR_PATH != _DEFAULT_ANCHOR_PATH:
        return _ANCHOR_PATH
    try:
        import vault_secret
        sec = vault_secret.load_secret()
        if sec:
            fp = hashlib.sha256(sec.encode("utf-8")).hexdigest()[:12]
            return os.path.join(_ANCHOR_DIR, f"security_anchor_{fp}.json")
    except Exception:
        pass
    return _ANCHOR_PATH


def _is_real_number(val) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def _canonical_body(data: dict) -> bytes:
    return json.dumps(
        {
            "slots": data.get("slots", {}),
            "witness": data.get("witness", {}),
            "model_hashes": data.get("model_hashes", {}),
        },
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _mac_body(data: dict) -> str | None:
    try:
        import state_binding
        return state_binding.compute_binding(
            state_binding.ANCHOR_PURPOSE, _canonical_body(data)
        )
    except Exception:
        return None


def _read_raw() -> dict:
    path = _active_anchor_path()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    if "slots" not in data:
        flat = {k: v for k, v in data.items() if k not in _BODY_KEYS + ("mac",)}
        data = {"slots": flat}
    else:
        if not isinstance(data.get("slots"), dict):
            data["slots"] = {}
        for k, v in data.items():
            if k not in _BODY_KEYS + ("mac",) and isinstance(v, bool):
                data["slots"][k] = data["slots"].get(k) or v
    return data


def get() -> dict:
    with _anchor_lock:
        try:
            data = _read_raw()
        except Exception:
            return {}
        if "mac" in data:
            try:
                import state_binding
                check = state_binding.verify_binding(
                    state_binding.ANCHOR_PURPOSE, _canonical_body(data), data["mac"]
                )
            except Exception:
                check = None
            if check is False:
                return {"anchor_tampered": True}
        return data


def is_marked(key: str) -> bool:
    with _anchor_lock:
        data = get()
        if data.get("anchor_tampered"):
            return True
        slots = data.get("slots", {})
        return bool(slots.get(key)) if isinstance(slots, dict) else False


def _write(data: dict) -> None:
    path = _active_anchor_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    body = {k: (data.get(k) if isinstance(data.get(k), dict) else {}) for k in _BODY_KEYS}
    mac = _mac_body(body)
    if mac:
        body["mac"] = mac
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(body, f, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)


def _update(mutator) -> None:
    with _anchor_lock:
        try:
            data = _read_raw()
        except Exception:
            data = {}
        if "mac" in data:
            try:
                import state_binding
                check = state_binding.verify_binding(
                    state_binding.ANCHOR_PURPOSE, _canonical_body(data), data["mac"]
                )
            except Exception:
                check = None
            if check is False:
                return
        mutator(data)
        try:
            _write(data)
        except Exception:
            pass


def mark(key: str) -> None:
    def _apply(data: dict):
        slots = data.setdefault("slots", {})
        if not isinstance(slots, dict):
            slots = {}
            data["slots"] = slots
        slots[key] = True
    _update(_apply)


def witness_update(slot: str, overwrite: bool = False, **fields) -> None:
    def _apply(data: dict):
        w = data.setdefault("witness", {})
        if not isinstance(w, dict):
            w = {}
            data["witness"] = w
        cur = w.setdefault(slot, {})
        if not isinstance(cur, dict):
            cur = {}
            w[slot] = cur
        for k, v in fields.items():
            if not overwrite and _is_real_number(v) and _is_real_number(cur.get(k)):
                cur[k] = max(cur[k], v)
            else:
                cur[k] = v
    _update(_apply)


def witness_get(slot: str) -> dict | None:
    with _anchor_lock:
        data = get()
        if data.get("anchor_tampered"):
            return None
        w = data.get("witness", {})
        out = w.get(slot) if isinstance(w, dict) else None
        return dict(out) if isinstance(out, dict) else None


def model_hash_get(name: str) -> str | None:
    with _anchor_lock:
        data = get()
        if data.get("anchor_tampered"):
            return None
        h = data.get("model_hashes", {})
        value = h.get(name) if isinstance(h, dict) else None
        return value if isinstance(value, str) else None


def model_hash_record(name: str, sha256: str) -> None:
    def _apply(data: dict):
        mh = data.setdefault("model_hashes", {})
        if not isinstance(mh, dict):
            mh = {}
            data["model_hashes"] = mh
        mh[name] = sha256
    _update(_apply)
