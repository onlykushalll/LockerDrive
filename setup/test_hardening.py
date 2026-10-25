"""
test_hardening.py -- camera-free, drive-free verification of the
2026-09-20 hardening pass (pin_auth / recovery_auth / face_auth /
state_binding / local_anchor / app's _safe_api).

Runs entirely against a TEMP DIRECTORY: every module's file paths are
re-pointed before anything is touched, so the real E:\\ state files and
the real %LOCALAPPDATA% anchor are never read or written. No camera, no
BitLocker volume, no UAC, no models -- the face ENGINE is never built
(only the template storage layer, which is pure file/crypto code).

Run from the LockerDrive root (or anywhere -- it finds the root itself):
    python setup\\test_hardening.py
Exit code 0 = all checks passed.
"""
import os
import sys
import json
import pickle
import tempfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "FaceRecog-Engine"))

import win32crypt
import numpy as np
from argon2 import PasswordHasher

import pin_auth
import recovery_auth
import vault_secret
import state_binding
import local_anchor
import face_auth

_PASS = 0
_FAIL = 0
_FAILS = []
_TMP_ROOT = tempfile.mkdtemp(prefix="ldhard_")


def check(name, cond, detail=""):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        _FAILS.append(f"{name}" + (f"  [{detail}]" if detail else ""))
        print(f"  FAIL  {name}  {detail}")


def reset_env(scenario):
    """Fresh temp dir + all paths re-pointed + caches cleared. Simulates
    a brand-new machine state for one scenario."""
    d = os.path.join(_TMP_ROOT, scenario)
    os.makedirs(d, exist_ok=True)
    pin_auth._STATE_PATH = os.path.join(d, "pin_state.txt")
    pin_auth._DRIVE_ROOT = d + os.sep
    recovery_auth._RECOVERY_PATH = os.path.join(d, "recovery_state.txt")
    recovery_auth._DRIVE_ROOT = d + os.sep
    vault_secret.SECRET_PATH = os.path.join(d, "vault.secret")
    face_auth.TEMPLATES_PATH = os.path.join(d, "face_templates.pkl")
    local_anchor._ANCHOR_DIR = os.path.join(d, "anchor")
    local_anchor._ANCHOR_PATH = os.path.join(d, "anchor", "security_anchor.json")
    state_binding._clear_cache_for_tests()
    return d


def dpapi_wrap(data: bytes, desc: str) -> bytes:
    return win32crypt.CryptProtectData(data, desc, None, None, None, 0)


def dpapi_unwrap(blob: bytes) -> bytes | None:
    try:
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except Exception:
        return None


def write_legacy_pin(pin: str, attempts=0, until=0.0, tier=0):
    """The original Rust-binary format: four bare positional lines."""
    h = PasswordHasher().hash(pin)
    with open(pin_auth._STATE_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(f"{h}\n{attempts}\n{until}\n{tier}\n")


def write_legacy_recovery(code: str):
    h = PasswordHasher().hash(code)
    with open(recovery_auth._RECOVERY_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(f"hash={h}\n")


def fake_embeddings(n=3, dim=512):
    # L2-normalized like real ArcFace embeddings -- the 2026-10 fix pass
    # added a norm window ([0.25, 4.0]) to _validate_owner_list, so raw
    # standard-normal vectors (norm ~22.6) are no longer plausible templates.
    rng = np.random.default_rng(42)
    out = []
    for _ in range(n):
        v = rng.standard_normal(dim)
        v = v / np.linalg.norm(v)
        out.append(v.astype(np.float32).tolist())
    return out


# ============================================================
print("== S1: PIN policy (no secret) ==")
reset_env("s1")
check("empty PIN rejected", pin_auth.init("") == "INVALID_PIN")
check("2-digit PIN rejected", pin_auth.init("12") == "INVALID_PIN")
check("non-numeric PIN rejected", pin_auth.init("abcd") == "INVALID_PIN")
check("13-digit PIN rejected", pin_auth.init("1234567890123") == "INVALID_PIN")
check("4-digit PIN accepted", pin_auth.init("1234") == "INITIALIZED")
check("double init blocked", pin_auth.init("5678") == "ALREADY_INITIALIZED")
check("correct PIN verifies", pin_auth.verify("1234") == "OK")

print("== S2: legacy v1 file, no secret (fresh-vault compatibility) ==")
reset_env("s2")
write_legacy_pin("9999")
check("legacy file verifies", pin_auth.verify("9999") == "OK")
check("wrong PIN on legacy", pin_auth.verify("0000").startswith("WRONG:"))

print("== S3: lockout escalation ==")
reset_env("s3")
pin_auth.init("4321")
check("1st wrong -> WRONG:2", pin_auth.verify("0000") == "WRONG:2")
check("2nd wrong -> WRONG:1", pin_auth.verify("0000") == "WRONG:1")
check("3rd wrong -> LOCKED:60", pin_auth.verify("0000") == "LOCKED:60")
check("status shows LOCKED", pin_auth.status().startswith("LOCKED:"))
check("correct PIN blocked during lockout", pin_auth.verify("4321").startswith("LOCKED:"))

print("== S4: recovery code, no secret ==")
reset_env("s4")
code = recovery_auth.ensure_exists()
import re as _re
check("code format", bool(_re.match(r"^[0-9A-F]{6}(-[0-9A-F]{6}){3}$", code or "")), repr(code))
check("ensure_exists second call -> None", recovery_auth.ensure_exists() is None)
check("correct code verifies", recovery_auth.verify(code) is True)
check("wrong code rejected", recovery_auth.verify("000000-000000-000000-000000") is False)
write_legacy_recovery("AAAAAA-BBBBBB-CCCCCC-DDDDDD")
# 2026-10 semantics change: the anchor is now marked on EVERY v2 write,
# bound or not -- the unbound v2 written by ensure_exists() above already
# witnessed the upgrade, so a legacy-format file reappearing afterwards
# is a downgrade and is rejected (the old assertion expected True when
# unbound writes left the anchor unmarked). Legacy recovery files still
# load on a vault that never wrote a v2 one (covered in S7b and the
# red-team suite).
check("legacy single-line after v2 existed -> rejected (downgrade)",
      recovery_auth.verify("aaaaaa-bbbbbb-cccccc-dddddd") is False)

print("== S5: templates, no secret ==")
reset_env("s5")
face_auth._save_templates(fake_embeddings())
t = face_auth._load_templates()
check("v2 round-trip (no binding, no secret)",
      isinstance(t, dict) and len(t["owner"]) == 3 and t["owner"][0].shape == (512,))
# v0 raw pickle from before encryption existed
# 2026-10 semantics change: _save_templates above marked the templates
# anchor even though the save was unbound (mark-on-every-v2-write), so a
# v0 file reappearing now is a downgrade attack, not a migration. The
# genuine no-secret legacy-vault acceptance path (nothing ever bound +
# legacy state files present) is covered by the red-team fix suite.
raw_v0 = pickle.dumps({"owner": [np.asarray(e, dtype=np.float32) for e in fake_embeddings()]})
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(raw_v0)
try:
    face_auth._load_templates()
    check("v0 raw pickle after v2 existed -> TEMPLATES_CORRUPT", False, "no exception")
except face_auth.TemplatesCorruptError:
    check("v0 raw pickle after v2 existed -> TEMPLATES_CORRUPT", True)
# restricted unpickler: classic os.system payload must be refused
malicious = b"cos\nsystem\n(S'echo pwned'\ntR."
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(malicious)
try:
    face_auth._load_templates()
    check("malicious pickle rejected", False, "no exception raised")
except face_auth.TemplatesCorruptError:
    check("malicious pickle rejected", True)

print("== S6: PIN with secret registered (binding lifecycle) ==")
reset_env("s6")
vault_secret.save_secret("test-vault-password-123")
state_binding._clear_cache_for_tests()
pin_auth.init("777777")
check("file on disk is DPAPI-wrapped", dpapi_unwrap(open(pin_auth._STATE_PATH, "rb").read()) is not None)
payload = json.loads(dpapi_unwrap(open(pin_auth._STATE_PATH, "rb").read()).decode("utf-8"))
check("v2 payload has binding", bool(payload.get("binding")))
check("anchor marked", local_anchor.is_marked("pin_bound"))
check("correct PIN verifies (v2)", pin_auth.verify("777777") == "OK")

# tamper-inside: edit a field, keep the old binding
payload["failed_attempts"] = 99
forged = dpapi_wrap(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    "LockerDrive pin state")
with open(pin_auth._STATE_PATH, "wb") as f:
    f.write(forged)
check("inside-edit with stale binding -> STATE_CORRUPT", pin_auth.verify("777777") == "STATE_CORRUPT")

# attacker writes a legacy plaintext file (their own PIN hash) after a
# bound file existed -- the headline attack, must be rejected
pin_auth.force_reset("777777")  # restore a good bound file first
write_legacy_pin("1111")
check("attacker legacy file -> STATE_CORRUPT", pin_auth.verify("1111") == "STATE_CORRUPT")

# binding is vault-specific: a binding from a different vault secret fails
pin_auth.force_reset("777777")
good = open(pin_auth._STATE_PATH, "rb").read()
vault_secret.save_secret("a-different-vault-password")
state_binding._clear_cache_for_tests()
check("transplanted binding (other vault) -> STATE_CORRUPT", pin_auth.verify("777777") == "STATE_CORRUPT")
with open(pin_auth._STATE_PATH, "wb") as f:
    f.write(good)
vault_secret.save_secret("test-vault-password-123")
state_binding._clear_cache_for_tests()
check("restored file verifies again", pin_auth.verify("777777") == "OK")

# legacy file + secret + NO anchor yet -> blessed on first load
reset_env("s6b")
vault_secret.save_secret("test-vault-password-123")
state_binding._clear_cache_for_tests()
write_legacy_pin("2468")
check("legacy bless: verifies", pin_auth.verify("2468") == "OK")
check("legacy bless: file now DPAPI", dpapi_unwrap(open(pin_auth._STATE_PATH, "rb").read()) is not None)
check("legacy bless: anchor marked", local_anchor.is_marked("pin_bound"))

print("== S7: recovery with secret ==")
reset_env("s7")
vault_secret.save_secret("test-vault-password-123")
state_binding._clear_cache_for_tests()
code = recovery_auth.generate()
check("generate -> v2 bound", local_anchor.is_marked("recovery_bound"))
check("code verifies", recovery_auth.verify(code) is True)
# tamper-inside: swap hash, keep binding
payload = json.loads(dpapi_unwrap(open(recovery_auth._RECOVERY_PATH, "rb").read()).decode("utf-8"))
payload["hash"] = PasswordHasher().hash("AAAAAA-BBBBBB-CCCCCC-DDDDDD")
forged = dpapi_wrap(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                    "LockerDrive recovery state")
with open(recovery_auth._RECOVERY_PATH, "wb") as f:
    f.write(forged)
check("edited hash with stale binding -> rejected",
      recovery_auth.verify("AAAAAA-BBBBBB-CCCCCC-DDDDDD") is False)
# attacker legacy line after bound file existed
write_legacy_recovery("EEEEEE-EEEEEE-EEEEEE-EEEEEE")
check("attacker legacy recovery -> rejected",
      recovery_auth.verify("EEEEEE-EEEEEE-EEEEEE-EEEEEE") is False)
# legacy bless path (no anchor yet)
reset_env("s7b")
vault_secret.save_secret("test-vault-password-123")
state_binding._clear_cache_for_tests()
write_legacy_recovery("ABC123-DEF456-789ABC-DEF012")
check("legacy recovery bless: verifies",
      recovery_auth.verify("abc123-def456-789abc-def012") is True)
check("legacy recovery bless: file now DPAPI",
      dpapi_unwrap(open(recovery_auth._RECOVERY_PATH, "rb").read()) is not None)

print("== S8: templates with secret ==")
reset_env("s8")
vault_secret.save_secret("test-vault-password-123")
state_binding._clear_cache_for_tests()
# Genuine pre-hardening vault context: a legacy 4-line pin_state (never
# bound) is what tells the 2026-10 legacy-vault rule that a v0 templates
# file is a real migration rather than a plant onto a modern vault.
write_legacy_pin("1357")
# v0 raw pickle + secret + no anchor -> loaded AND upgraded in place
raw_v0 = pickle.dumps({"owner": [np.asarray(e, dtype=np.float32) for e in fake_embeddings()]})
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(raw_v0)
t = face_auth._load_templates()
check("v0 + secret loads", isinstance(t, dict) and len(t["owner"]) == 3)
check("v0 upgraded in place", dpapi_unwrap(open(face_auth.TEMPLATES_PATH, "rb").read()) is not None)
check("templates anchor marked", local_anchor.is_marked("templates_bound"))
# attacker re-writes raw pickle after bound file existed
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(raw_v0)
try:
    face_auth._load_templates()
    check("attacker raw pickle after bound -> TEMPLATES_CORRUPT", False, "no exception")
except face_auth.TemplatesCorruptError:
    check("attacker raw pickle after bound -> TEMPLATES_CORRUPT", True)
# tamper-inside v2: change one embedding value, keep binding
face_auth._save_templates(fake_embeddings())
payload = json.loads(dpapi_unwrap(open(face_auth.TEMPLATES_PATH, "rb").read()).decode("utf-8"))
payload["owner"][0][0] = float(payload["owner"][0][0]) + 0.5
forged = dpapi_wrap(json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                    "LockerDrive face templates")
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(forged)
try:
    face_auth._load_templates()
    check("edited template with stale binding -> TEMPLATES_CORRUPT", False, "no exception")
except face_auth.TemplatesCorruptError:
    check("edited template with stale binding -> TEMPLATES_CORRUPT", True)

print("== S9: template schema validation ==")
reset_env("s9")
bad_cases = {
    "missing owner": {"nope": []},
    "empty owner": {"owner": []},
    "wrong dims": {"owner": [[0.1] * 10]},
    "inconsistent dims": {"owner": [[0.1] * 512, [0.1] * 256]},
    "non-numeric": {"owner": [["a"] * 512]},
    "too many templates": {"owner": [[0.1] * 512] * 200},
}
for name, payload in bad_cases.items():
    try:
        face_auth._validate_owner_list(payload["owner"] if "owner" in payload else None)
        check(f"validation rejects: {name}", False, "accepted")
    except face_auth.TemplatesCorruptError:
        check(f"validation rejects: {name}", True)
nan_row = [[float("nan")] * 512]
try:
    face_auth._validate_owner_list(nan_row)
    check("validation rejects: NaN template", False, "accepted")
except face_auth.TemplatesCorruptError:
    check("validation rejects: NaN template", True)
check("validation accepts good list",
      len(face_auth._validate_owner_list(fake_embeddings())) == 3)

print("== S10: state_binding unit ==")
reset_env("s10")
check("no secret -> compute None",
      state_binding.compute_binding(state_binding.PIN_PURPOSE, b"x") is None)
check("no secret -> verify None",
      state_binding.verify_binding(state_binding.PIN_PURPOSE, b"x", "deadbeef") is None)
vault_secret.save_secret("k")
state_binding._clear_cache_for_tests()
mac = state_binding.compute_binding(state_binding.PIN_PURPOSE, b"content")
# 2026-10 semantics change: bindings are versioned -- "A2:" prefix + hex64
# (argon2id-derived key). Legacy bare-hex bindings still VERIFY (below).
check("compute returns A2: + hex64",
      isinstance(mac, str) and mac.startswith("A2:") and len(mac) == 67)
check("verify accepts same", state_binding.verify_binding(state_binding.PIN_PURPOSE, b"content", mac) is True)
check("verify rejects different", state_binding.verify_binding(state_binding.PIN_PURPOSE, b"tampered", mac) is False)
check("purposes are separated",
      state_binding.verify_binding(state_binding.RECOVERY_PURPOSE, b"content", mac) is False)

print("== S11: app._safe_api wrapper ==")
sys.path.insert(0, os.path.join(ROOT, "ui"))
import app as app_module
@ app_module._safe_api
def _boom():
    raise RuntimeError("kaboom")
@ app_module._safe_api
def _fine():
    return {"ok": True, "data": 7}
check("exception -> well-formed dict",
      _boom() == {"ok": False, "message": "ERROR:RuntimeError"})
check("happy path passes through", _fine() == {"ok": True, "data": 7})
check("wrapper keeps function name", _boom.__name__ == "_boom")

# ============================================================
print()
print(f"RESULT: {_PASS} passed, {_FAIL} failed")
if _FAILS:
    print("Failures:")
    for f in _FAILS:
        print("  -", f)
shutil.rmtree(_TMP_ROOT, ignore_errors=True)
sys.exit(1 if _FAIL else 0)


