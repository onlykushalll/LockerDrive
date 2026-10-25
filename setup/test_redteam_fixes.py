"""
test_redteam_fixes.py -- verification suite for the 2026-10 red-team fix
pass (FIX-A, Task 12-a). One focused check per attack the audits proved
against the pre-fix code, plus the backward-compatibility promises the
fix pass must not break.

Runs entirely against TEMP DIRECTORIES: every module's file path is
re-pointed before anything is touched (same technique as
setup/test_hardening.py's reset_env), so the real E:\\ state files, the
real %LOCALAPPDATA% anchor, and the real model dir are never read or
written. No camera, no BitLocker volume, no UAC, no network.

Sandbox seam: on Windows this uses the REAL win32crypt (DPAPI) and the
real webview import path. In the Linux sandbox (or anywhere win32crypt /
webview are absent) a minimal fake is installed into sys.modules BEFORE
any engine import -- same shape as the pentest-lab stubs: fake DPAPI is
a keyed-HMAC round-trip with per-process "profile key" semantics, fake
webview records created windows and never starts an event loop.

Run from anywhere:
    python setup/test_redteam_fixes.py
Exit code 0 = all checks passed.

Coverage areas (FIX_SPEC A15):
  1  enroll gate           9  anchor MAC fail-closed
  2  deletion attacks     10 matcher (scaled template)
  3  unpickler allowlist  11 PIN regex
  4  legacy-vault rule    12 fresh-vault setup after secret-first (J4)
  5  counter replay      13 access log (truncation/forgery/corruption)
  6  generation replay   14 model TOFU
  7  normal-order rebind 15 lock_vault ALREADY_LOCKED
  8  result-file nonce   +  bonus: open_explorer gate, base64 stdin,
                            legacy-binding acceptance, A2 anchor MAC
"""
import os
import sys
import io
import json
import glob
import hmac
import hashlib
import pickle
import re
import shutil
import subprocess
import tempfile
import time
import types
import contextlib
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "FaceRecog-Engine"))

# ------------------------------------------------------------------ seams
def _install_fake_win32crypt():
    """Keyed-HMAC DPAPI stand-in (per-process 'profile key'). Same call
    signature/semantics as the pentest-lab stub: protect/unprotect are
    inverse, any edit or foreign key fails like pywintypes.error."""
    mod = types.ModuleType("win32crypt")
    _KEY = os.urandom(32)
    _MAGIC = b"LDFAKEDPAPI1"  # 12 bytes -- slicing below depends on this

    class error(Exception):
        pass

    def CryptProtectData(data_in, szDataDescr, pvReserved, pPrompt, pbEntropy, dwFlags):
        if not isinstance(data_in, (bytes, bytearray)):
            raise error("CryptProtectData expects bytes")
        import hmac as _hmac, hashlib as _hashlib
        body = bytes(data_in)
        mac = _hmac.new(_KEY, _MAGIC + body, _hashlib.sha256).digest()
        return _MAGIC + mac + body

    def CryptUnprotectData(blob, pPromptStruct, pvReserved, pbEntropy, dwFlags):
        if not isinstance(blob, (bytes, bytearray)) or len(blob) < len(_MAGIC) + 32:
            raise error("The data is invalid")
        import hmac as _hmac, hashlib as _hashlib
        blob = bytes(blob)
        if not blob.startswith(_MAGIC):
            raise error("The data is invalid")
        mac, body = blob[len(_MAGIC):len(_MAGIC) + 32], blob[len(_MAGIC) + 32:]
        if not _hmac.compare_digest(mac, _hmac.new(_KEY, _MAGIC + body, _hashlib.sha256).digest()):
            raise error("The data is invalid")
        return ("", body)

    mod.error = error
    mod.CryptProtectData = CryptProtectData
    mod.CryptUnprotectData = CryptUnprotectData
    sys.modules["win32crypt"] = mod
    return mod


def _install_fake_webview():
    mod = types.ModuleType("webview")

    class _Event:
        def __iadd__(self, h):
            return self

    class Window:
        def __init__(self, *a, **kw):
            self.kwargs = kw
            self.events = types.SimpleNamespace(shown=_Event(), closed=_Event())

    mod.Window = Window
    mod.create_window = lambda *a, **kw: Window(*a, **kw)
    mod.start = lambda *a, **kw: None
    sys.modules["webview"] = mod
    return mod


try:
    import win32crypt  # noqa: F401  (real on Windows)
except Exception:
    win32crypt = _install_fake_win32crypt()

try:
    import webview  # noqa: F401
except Exception:
    webview = _install_fake_webview()

import numpy as np
from argon2 import PasswordHasher

import pin_auth
import recovery_auth
import vault_secret
import state_binding
import local_anchor
import face_auth
import access_log
import vault_mount
import _elevated_helper

# ------------------------------------------------------------------ harness
_PASS = 0
_FAIL = 0
_FAILS = []
_TMP_ROOT = tempfile.mkdtemp(prefix="ldredteam_")
_AREA = ""


def _ascii(s):
    """Windows consoles and PowerShell redirections frequently use cp1252;
    labels/details that embed hostile test strings (unicode PINs, umlaut
    passwords) would crash the whole harness mid-suite with
    UnicodeEncodeError. Keep every printed line pure ASCII (backslash
    escapes stay fully readable). Verified necessary on the real box."""
    return str(s).encode("ascii", "backslashreplace").decode("ascii")


def check(name, cond, detail=""):
    global _PASS, _FAIL
    label = _ascii(f"[{_AREA}] {name}" if _AREA else name)
    detail = _ascii(detail)
    if cond:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        _FAILS.append(f"{label}" + (f"  [{detail}]" if detail else ""))
        print(f"  FAIL  {label}  {detail}")


def area(name):
    global _AREA
    _AREA = name
    print(f"\n== {name} ==")


def reset_env(scenario):
    """Fresh temp dir + every path re-pointed + caches cleared."""
    d = os.path.join(_TMP_ROOT, scenario)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    pin_auth._STATE_PATH = os.path.join(d, "pin_state.txt")
    pin_auth._DRIVE_ROOT = d + os.sep
    recovery_auth._RECOVERY_PATH = os.path.join(d, "recovery_state.txt")
    recovery_auth._DRIVE_ROOT = d + os.sep
    vault_secret.SECRET_PATH = os.path.join(d, "vault.secret")
    face_auth.TEMPLATES_PATH = os.path.join(d, "face_templates.pkl")
    face_auth.MODEL_DIR = os.path.join(d, "models")
    face_auth._engine = None  # never leak a TOFU-checked engine between scenarios
    local_anchor._ANCHOR_DIR = os.path.join(d, "anchor")
    local_anchor._ANCHOR_PATH = os.path.join(d, "anchor", "security_anchor.json")
    access_log._LOG_PATH = os.path.join(d, "access_log.txt")
    access_log._DRIVE_ROOT = d + os.sep
    access_log._chain_state = {"count": None, "head_hash": None}
    state_binding._clear_cache_for_tests()
    return d


def dpapi_wrap(data: bytes, desc: str = "test") -> bytes:
    return win32crypt.CryptProtectData(data, desc, None, None, None, 0)


def dpapi_unwrap(blob: bytes):
    try:
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except Exception:
        return None


def write_legacy_pin(pin, attempts=0, until=0.0, tier=0):
    h = PasswordHasher().hash(pin)
    with open(pin_auth._STATE_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(f"{h}\n{attempts}\n{until}\n{tier}\n")


def write_legacy_recovery(code):
    h = PasswordHasher().hash(code)
    with open(recovery_auth._RECOVERY_PATH, "w", encoding="utf-8", newline="") as f:
        f.write(f"hash={h}\n")


def unit_embeddings(n=3, dim=512, seed=42):
    """L2-normalized embeddings -- the shape real ArcFace produces."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        v = rng.standard_normal(dim)
        v = v / np.linalg.norm(v)
        out.append(v.astype(np.float32).tolist())
    return out


def make_models(d, contents=(b"fake-antispoof-onnx", b"fake-midas-onnx")):
    os.makedirs(face_auth.MODEL_DIR, exist_ok=True)
    for name, blob in zip(face_auth._MODEL_FILES, contents):
        with open(os.path.join(face_auth.MODEL_DIR, name), "wb") as f:
            f.write(blob)


# ------------------------------------------------------- fake face engine
class _FakeResult:
    virtual_camera_detected = False
    face_count = 1
    liveness_passed = True
    primary_user_present = True
    recognition_score = 0.99
    liveness_score = 0.99
    frame_quality = 0.9


class _FakeRecognizer:
    @staticmethod
    def get_embedding(frame, face):
        v = np.random.default_rng(7).standard_normal(512)
        return (v / np.linalg.norm(v)).astype(np.float32)


class _FakeDetector:
    @staticmethod
    def detect(frame):
        return [object()]


def install_fake_face_engine():
    mod = sys.modules.get("facerecog_engine")
    if mod is not None:
        return mod

    class SecurityConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class FaceSecurityEngine:
        def __init__(self, config=None, camera_idx=0):
            self.config = config
            self._detector = _FakeDetector()
            self._recognizer = _FakeRecognizer()
            self.reference_templates = None

        @classmethod
        def create_parallel(cls, config=None, camera_idx=0):
            return cls(config=config, camera_idx=camera_idx)

        def reset_session(self):
            pass

        def set_reference_templates(self, t):
            self.reference_templates = t

        def process_frame(self, frame=None, liveness_mode="full"):
            return _FakeResult()

    mod = types.ModuleType("facerecog_engine")
    mod.SecurityConfig = SecurityConfig
    mod.FaceSecurityEngine = FaceSecurityEngine
    sys.modules["facerecog_engine"] = mod
    return mod


class FakeCap:
    def __init__(self):
        self.released = False

    def isOpened(self):
        return not self.released

    def read(self):
        return True, np.zeros((480, 640, 3), dtype=np.uint8)

    def release(self):
        self.released = True


@contextlib.contextmanager
def fake_camera():
    cap = FakeCap()
    real = face_auth._open_camera
    face_auth._open_camera = lambda: cap
    try:
        yield cap
    finally:
        face_auth._open_camera = real


def make_api():
    """Real ui/app.Api, headless (fake webview already installed)."""
    ui_dir = os.path.join(ROOT, "ui")
    if ui_dir not in sys.path:
        sys.path.insert(0, ui_dir)
    import app as app_module
    api = app_module.Api()
    return app_module, api


# ------------------------------------------------------- subprocess mock
@contextlib.contextmanager
def mock_subprocess(handler):
    """Intercept subprocess.run/Popen at the boundary (powershell launches
    in vault_mount / _elevated_helper, explorer spawns). handler(cmd, kw)
    runs INSTEAD of the process, may write files, and may return a str
    that becomes the (text-mode) stdout of the CompletedProcess."""
    real_run, real_popen = subprocess.run, subprocess.Popen
    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append(("run", list(cmd), kw))
        out = handler(list(cmd), kw)
        if kw.get("text"):
            stdout = out if isinstance(out, str) else ""
            stderr = ""
        else:
            stdout = out.encode("utf-8") if isinstance(out, str) else b""
            stderr = b""
        return subprocess.CompletedProcess(list(cmd), 0, stdout=stdout, stderr=stderr)

    def fake_popen(cmd, *a, **kw):
        calls.append(("Popen", list(cmd), kw))
        return types.SimpleNamespace(pid=4242, wait=lambda: 0, poll=lambda: 0)

    subprocess.run = fake_run
    subprocess.Popen = fake_popen
    try:
        yield calls
    finally:
        subprocess.run = real_run
        subprocess.Popen = real_popen


_ARG_RE = re.compile(r"-ArgumentList '([^']+)'")


def parse_helper_invocation(ps_command):
    """(helper_path, action, result_path, nonce) from the Start-Process
    command vault_mount builds."""
    m = _ARG_RE.search(ps_command)
    if not m:
        return None
    parts = m.group(1).split(" ")
    if len(parts) != 4:
        return None
    return parts


# ==================================================================== 1
area("1: enroll gate (F2-1 / A1-6 / J1)")

reset_env("gate1")
vault_secret.save_secret("gate-vault-pw")
state_binding._clear_cache_for_tests()
install_fake_face_engine()
make_models("gate1")
app_module, api = make_api()

r_setup = api.setup_pin("1234", "1234")
check("fresh setup_pin ok", r_setup.get("ok") is True, repr(r_setup))

with fake_camera():
    r_first = api.enroll_face()
check("fresh + PIN-this-session enroll allowed (passes gate)",
      r_first.get("message", "").startswith("ENROLLED"), repr(r_first))
check("templates now on disk", face_auth.is_enrolled())

# new session (fresh Api): not authenticated, vault HAS enrollment -> gated
_, api_locked_session = make_api()
r_unauth = api_locked_session.enroll_face()
check("unauth enroll on bound vault -> NOT_AUTHENTICATED",
      r_unauth == {"ok": False, "message": "NOT_AUTHENTICATED"}, repr(r_unauth))

# authenticated session -> allowed
_, api_authed = make_api()
api_authed._authenticated_this_session = True
with fake_camera():
    r_authed = api_authed.enroll_face()
check("authenticated enroll allowed (passes gate)",
      r_authed.get("message", "").startswith("ENROLLED"), repr(r_authed))

# bonus (A7): open_explorer gated
r_exp = api_locked_session.open_explorer()
check("unauth open_explorer -> NOT_AUTHENTICATED",
      r_exp == {"ok": False, "message": "NOT_AUTHENTICATED"}, repr(r_exp))

# ==================================================================== 2
area("2: deletion attacks (state-file deletion chain)")

reset_env("del2")
vault_secret.save_secret("del-vault-pw")
state_binding._clear_cache_for_tests()
install_fake_face_engine()
make_models("del2")
app_module, api = make_api()
api.setup_pin("1234", "1234")
with fake_camera():
    api.enroll_face()
check("vault fully set up before deletion",
      face_auth.is_enrolled() and os.path.exists(pin_auth._STATE_PATH))

os.remove(pin_auth._STATE_PATH)
os.remove(face_auth.TEMPLATES_PATH)

_, api2 = make_api()
r_setup = api2.setup_pin("9999", "9999")
check("delete pin+templates -> setup_pin FACE_REVERIFY_REQUIRED",
      r_setup == {"ok": False, "message": "FACE_REVERIFY_REQUIRED"}, repr(r_setup))
r_enroll = api2.enroll_face()
check("delete pin+templates -> enroll NOT_AUTHENTICATED",
      r_enroll == {"ok": False, "message": "NOT_AUTHENTICATED"}, repr(r_enroll))
ok, msg = face_auth.verify()
check("delete templates on bound vault -> verify TEMPLATES_MISSING_TAMPER",
      ok is False and msg == "TEMPLATES_MISSING_TAMPER", repr((ok, msg)))

# ==================================================================== 3
area("3: unpickler allowlist (D3/D4/D5 payloads)")

reset_env("pickle3")
# legacy pin state so the vault looks pre-hardening and the v0 path
# actually reaches the unpickler (the gadget refusals must come from the
# ALLOWLIST, not from the legacy-vault rule)
write_legacy_pin("1357")
payload_dir = os.path.join(_TMP_ROOT, "pickle3_payloads")
os.makedirs(payload_dir, exist_ok=True)
MARKER = os.path.join(_TMP_ROOT, "PWNED_redteam_suite.txt")
if os.path.exists(MARKER):
    os.remove(MARKER)


def gadget(fn, args):
    class G:
        def __reduce__(self):
            return (fn, args)
    return pickle.dumps(G())


def plant_and_load(payload):
    with open(face_auth.TEMPLATES_PATH, "wb") as f:
        f.write(payload)
    try:
        face_auth._load_templates()
        return "loaded"
    except face_auth.TemplatesCorruptError as e:
        return f"refused ({str(e)[:44]})"
    except Exception as e:
        return f"UNEXPECTED {type(e).__name__}"


# numpy.load re-entry: evil .npy carrying an os.system reduce (marker file)
class _Evil:
    def __reduce__(self):
        return (os.system, (f"touch {MARKER}",))


evil_npy = os.path.join(payload_dir, "evil.npy")
np.save(evil_npy, np.array([_Evil()], dtype=object), allow_pickle=True)
out = plant_and_load(gadget(np.load, (evil_npy, None, True)))
check("numpy.load re-entry gadget refused at the allowlist",
      "forbidden name" in out, out)
check("numpy.load gadget did NOT execute", not os.path.exists(MARKER))

# ctypeslib.load_library: compiled .so whose constructor writes the marker
so_path = os.path.join(payload_dir, "pwnlib.so")
have_gcc = shutil.which("gcc") is not None
if have_gcc:
    c_src = so_path[:-3] + ".c"
    with open(c_src, "w") as f:
        f.write('#include <stdio.h>\n__attribute__((constructor)) static void pwn(void){'
                f'FILE* f=fopen("{MARKER}","w"); if(f){{fputs("pwned",f);fclose(f);}}}}\n')
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", so_path, c_src],
                   check=True, capture_output=True, timeout=60)
out = plant_and_load(gadget(np.ctypeslib.load_library, ("pwnlib", payload_dir)))
check("numpy.ctypeslib.load_library gadget refused at the allowlist",
      "forbidden name" in out, out)
check("load_library gadget did NOT execute", not os.path.exists(MARKER))

for label, fn, args in [
    ("numpy.save", np.save, (os.path.join(payload_dir, "out.npy"), np.zeros(4))),
    ("numpy.memmap", np.memmap, (os.path.join(payload_dir, "mm.bin"),)),
    ("numpy.ones(25e6)", np.ones, (25_000_000,)),
    ("numpy.empty", np.empty, (25_000_000,)),
    ("numpy.fromfile", np.fromfile, ("/etc/hostname",)),
]:
    out = plant_and_load(gadget(fn, args))
    check(f"{label} gadget refused at the allowlist", "forbidden name" in out, out)

# probe the allowlist surface directly (D8-style)
probe = face_auth._TemplateUnpickler(io.BytesIO(b""))
allowed, denied = [], []
for mod, name in [("numpy", "dtype"), ("numpy", "ndarray"),
                  ("numpy.core.multiarray", "_reconstruct"),
                  ("numpy.core.multiarray", "scalar"),
                  ("numpy._core.multiarray", "_reconstruct"),
                  ("numpy._core.multiarray", "scalar"),
                  ("numpy", "load"), ("numpy", "save"), ("numpy", "memmap"),
                  ("numpy", "ones"), ("numpy", "empty"), ("numpy", "fromfile"),
                  ("numpy.ctypeslib", "load_library"), ("os", "system"),
                  ("builtins", "eval"), ("subprocess", "run")]:
    try:
        probe.find_class(mod, name)
        allowed.append(f"{mod}.{name}")
    except Exception:
        denied.append(f"{mod}.{name}")
check("allowlist = exactly the 6 array-reconstruction names",
      sorted(allowed) == sorted(["numpy.dtype", "numpy.ndarray",
                                 "numpy.core.multiarray._reconstruct",
                                 "numpy.core.multiarray.scalar",
                                 "numpy._core.multiarray._reconstruct",
                                 "numpy._core.multiarray.scalar"]), repr(allowed))
check("all dangerous names denied",
      sorted(denied) == sorted(["numpy.load", "numpy.save", "numpy.memmap",
                                "numpy.ones", "numpy.empty", "numpy.fromfile",
                                "numpy.ctypeslib.load_library", "os.system",
                                "builtins.eval", "subprocess.run"]), repr(denied))

# size caps BEFORE unpickle (D5-1/C1-8)
out = plant_and_load(b"\x80" + b"A" * (4 * 1024 * 1024 + 10))
check("oversize raw file refused before unpickle",
      "implausibly large" in out, out)

# legit v0 pickles still load and UPGRADE (dict + bare-list shapes) on a
# genuine legacy vault (legacy pin state => vault looks pre-hardening).
# Each shape gets a fresh scenario: the first successful upgrade marks
# the templates anchor, and a second legacy file in the same scenario
# would (correctly) read as a downgrade.
for pi, (label, payload) in enumerate({
    "dict/list": pickle.dumps(
        {"owner": [np.asarray(e, dtype=np.float32) for e in unit_embeddings()]}),
    "bare list": pickle.dumps(
        [np.asarray(e, dtype=np.float32) for e in unit_embeddings()]),
}.items()):
    reset_env(f"pickle3b{pi}")
    vault_secret.save_secret("pickle3-vault-pw")
    state_binding._clear_cache_for_tests()
    write_legacy_pin("1357")
    with open(face_auth.TEMPLATES_PATH, "wb") as f:
        f.write(payload)
    try:
        t = face_auth._load_templates()
        got = isinstance(t, dict) and len(t["owner"]) == 3
    except Exception as e:
        got = f"{type(e).__name__}: {e}"
    check(f"legit v0 loads+upgrades: {label}", got is True, repr(got))
    check(f"legit v0 upgraded in place: {label}",
          dpapi_unwrap(open(face_auth.TEMPLATES_PATH, "rb").read()) is not None)
    check(f"templates anchor marked after upgrade: {label}",
          local_anchor.is_marked("templates_bound"))

# the numpy 1.x GLOBAL spelling (numpy.core.multiarray instead of
# numpy._core.multiarray -- what real pre-numpy-2 legacy files contain)
# must pass find_class too. Minimal STACK_GLOBAL pickle, built by hand
# because this numpy always emits the _core spelling.
def _short_binunicode(s):
    b = s.encode("ascii")
    assert len(b) < 256
    return b"\x8c" + bytes([len(b)]) + b + b"\x94"


def _stack_global_pickle(module, name):
    body = _short_binunicode(module) + _short_binunicode(name) + b"\x93."
    frame_len = len(body).to_bytes(8, "little")
    return b"\x80\x04\x95" + frame_len + body


for old_mod in ("numpy.core.multiarray", "numpy._core.multiarray"):
    for old_name in ("_reconstruct", "scalar"):
        try:
            face_auth._TemplateUnpickler(
                io.BytesIO(_stack_global_pickle(old_mod, old_name))).load()
            got = True
        except Exception as e:
            got = f"{type(e).__name__}: {e}"
        check(f"allowlist accepts {old_mod}.{old_name}", got is True, repr(got))

# ==================================================================== 4
area("4: legacy-vault rule (J1-1 planted v0)")

# modern vault: v2-bound pin + secret; plant v0 templates
reset_env("plant4")
vault_secret.save_secret("plant-vault-pw")
state_binding._clear_cache_for_tests()
pin_auth.init("1234")
check("modern vault pin is v2-bound",
      dpapi_unwrap(open(pin_auth._STATE_PATH, "rb").read()) is not None)
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(pickle.dumps({"owner": [np.asarray(e, dtype=np.float32) for e in unit_embeddings(seed=99)]}))
try:
    face_auth._load_templates()
    got = "loaded"
except face_auth.TemplatesCorruptError as e:
    got = str(e)
check("planted v0 on modern (v2-pin) vault -> TEMPLATES_CORRUPT (planted)",
      "non-legacy vault" in got, got)
check("planted v0 NOT laundered (file still a raw pickle)",
      dpapi_unwrap(open(face_auth.TEMPLATES_PATH, "rb").read()) is None)

# same, attacker also legacy-ifies pin_state (smarter variant): the pin
# anchor is marked, so the legacy pin state is itself a downgrade and
# cannot vouch for the templates file
write_legacy_pin("9999")
try:
    face_auth._load_templates()
    got = "loaded"
except face_auth.TemplatesCorruptError as e:
    got = str(e)
check("planted v0 + attacker legacy pin_state -> still TEMPLATES_CORRUPT",
      "non-legacy vault" in got, got)

# genuine all-legacy vault: legacy pin + legacy recovery + v0 templates
reset_env("legacy4")
vault_secret.save_secret("legacy-vault-pw")
state_binding._clear_cache_for_tests()
write_legacy_pin("2468")
write_legacy_recovery("AAAAAA-BBBBBB-CCCCCC-DDDDDD")
with open(face_auth.TEMPLATES_PATH, "wb") as f:
    f.write(pickle.dumps({"owner": [np.asarray(e, dtype=np.float32) for e in unit_embeddings(seed=7)]}))
try:
    t = face_auth._load_templates()
    ok4 = isinstance(t, dict) and len(t["owner"]) == 3
except face_auth.TemplatesCorruptError as e:
    ok4 = f"TEMPLATES_CORRUPT: {e}"
check("genuine all-legacy vault: v0 accepted", ok4 is True, repr(ok4))
check("genuine all-legacy vault: upgraded in place",
      dpapi_unwrap(open(face_auth.TEMPLATES_PATH, "rb").read()) is not None)
check("genuine all-legacy vault: anchor marked",
      local_anchor.is_marked("templates_bound"))
check("genuine all-legacy vault: templates are now bound v2",
      json.loads(dpapi_unwrap(open(face_auth.TEMPLATES_PATH, "rb").read())).get("binding", "").startswith("A2:"))

# ==================================================================== 5
area("5: lockout counter replay (C2-1/C2-2)")

reset_env("replay5")
vault_secret.save_secret("replay-vault-pw")
state_binding._clear_cache_for_tests()
pin_auth.init("7777")
snapshot = open(pin_auth._STATE_PATH, "rb").read()
r1 = pin_auth.verify("0000"); r2 = pin_auth.verify("0000"); r3 = pin_auth.verify("0000")
check("3 wrong attempts -> LOCKED", r3.startswith("LOCKED:"), repr((r1, r2, r3)))
with open(pin_auth._STATE_PATH, "wb") as f:
    f.write(snapshot)  # attacker replays the zero-counter snapshot
st = pin_auth.status()
check("after snapshot replay: status still LOCKED (witness max-merge)",
      st.startswith("LOCKED:"), st)
r = pin_auth.verify("0000")
check("after snapshot replay: next attempt still LOCKED",
      r.startswith("LOCKED:"), r)

# brute-force loop with replay before EVERY attempt (C2b shape)
locked_responses = 0
for _ in range(12):
    with open(pin_auth._STATE_PATH, "wb") as f:
        f.write(snapshot)
    r = pin_auth.verify("0000")
    if r.startswith("LOCKED:"):
        locked_responses += 1
check("replay-before-every-attempt: all 12 attempts LOCKED (lockout not defeatable)",
      locked_responses == 12, f"{locked_responses}/12 locked")

# ==================================================================== 6
area("6: generation replay (rotation resurrection)")

reset_env("gen6")
vault_secret.save_secret("gen-vault-pw")
state_binding._clear_cache_for_tests()
pin_auth.init("1111")
snap = open(pin_auth._STATE_PATH, "rb").read()
pin_auth.force_reset("2222")  # authenticated-session rotation
with open(pin_auth._STATE_PATH, "wb") as f:
    f.write(snap)  # attacker replays the pre-rotation file
check("replayed pre-rotation pin_state -> STATE_CORRUPT",
      pin_auth.verify("1111") == "STATE_CORRUPT")
check("replayed pre-rotation pin_state: status STATE_CORRUPT",
      pin_auth.status() == "STATE_CORRUPT")

# recovery rotation
reset_env("gen6b")
vault_secret.save_secret("gen-vault-pw-b")
state_binding._clear_cache_for_tests()
code1 = recovery_auth.generate()
snap = open(recovery_auth._RECOVERY_PATH, "rb").read()
code2 = recovery_auth.generate()
check("after rotation, old code invalid",
      recovery_auth.verify(code1) is False and recovery_auth.verify(code2) is True)
with open(recovery_auth._RECOVERY_PATH, "wb") as f:
    f.write(snap)  # replay the pre-rotation file
check("replayed pre-rotation recovery file -> old code still invalid (C9-1 closed)",
      recovery_auth.verify(code1) is False)

# pre-UPGRADE (generation-less, old-format) recovery file replayed after
# a rotation: the witness has seen a generation, so it is stale too
body = {"hash": PasswordHasher().hash(code1)}
payload = {"format": "v2", "hash": body["hash"]}
# a real old-format binding: bare-hex PBKDF2 HMAC over the 2-field canonical
legacy_key = state_binding._derive_key_legacy(state_binding.RECOVERY_PURPOSE)
payload["binding"] = hmac.new(
    legacy_key,
    json.dumps({"hash": body["hash"]}, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    hashlib.sha256).hexdigest()
with open(recovery_auth._RECOVERY_PATH, "wb") as f:
    f.write(dpapi_wrap(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")))
check("pre-upgrade (gen-less) recovery file replayed post-rotation -> invalid",
      recovery_auth.verify(code1) is False)

# ==================================================================== 7
area("7: normal-order rebind (C8-1)")

reset_env("normal7")
r = pin_auth.init("2468")  # no secret yet -- the normal setup order
check("init without secret succeeds", r == "INITIALIZED", r)
check("anchor marked on the UNBOUND v2 save (mark-on-every-v2-write)",
      local_anchor.is_marked("pin_bound"))
vault_secret.save_secret("normal-vault-pw")
state_binding._clear_cache_for_tests()
pin_auth.status()  # any load triggers the opportunistic rebind
payload = json.loads(dpapi_unwrap(open(pin_auth._STATE_PATH, "rb").read()))
check("load rebinds the previously-unbound file in place",
      str(payload.get("binding", "")).startswith("A2:"), repr(payload.get("binding"))[:20])

write_legacy_pin("4321")  # drive-only attacker's legacy file
check("attacker legacy file after rebind -> STATE_CORRUPT",
      pin_auth.verify("4321") == "STATE_CORRUPT")

# ==================================================================== 8
area("8: elevated-helper result-file nonce (E4-1/E5-1)")

reset_env("helper8")


def run_unlock_with_writer(writer):
    """Mock subprocess; `writer(result_path, nonce)` simulates whatever
    ends up in the result file. Returns vault_mount._run_elevated_helper's
    verdict."""
    state = {"res": None}

    def handler(cmd, kw):
        if "powershell" not in str(cmd[0]):
            return
        inv = parse_helper_invocation(cmd[3])
        if inv:
            _helper, _action, result_path, nonce = inv
            state["inv"] = inv
            if writer is not None:
                writer(result_path, nonce)
    with mock_subprocess(handler):
        state["res"] = vault_mount._run_elevated_helper("unlock")
    return state


st = run_unlock_with_writer(lambda p, n: open(p, "w").write("SUCCESS"))
check("forged file WITHOUT nonce -> FAILED", st["res"].startswith("FAILED:"), st["res"])
st = run_unlock_with_writer(lambda p, n: open(p, "w").write("deadbeefdeadbeefdeadbeefdeadbeef|SUCCESS"))
check("forged file with WRONG nonce -> FAILED", st["res"].startswith("FAILED:"), st["res"])
st = run_unlock_with_writer(lambda p, n: open(p, "w").write(f"{n}|FAILED: simulated"))
check("correct nonce|FAILED -> helper's failure text",
      st["res"] == "FAILED: simulated", st["res"])
st = run_unlock_with_writer(lambda p, n: open(p, "w").write(f"{n}|SUCCESS"))
check("correct nonce|SUCCESS -> SUCCESS", st["res"] == "SUCCESS", st["res"])
st = run_unlock_with_writer(None)
check("no result file at all -> FAILED", st["res"].startswith("FAILED:"), st["res"])
check("result files cleaned up after read",
      not glob.glob(os.path.join(tempfile.gettempdir(), "LockerDrive_helper_*.txt")),
      "leftover nonce files")
check("stale SUCCESS from an earlier run is not trusted (nonce changed per run)",
      run_unlock_with_writer(lambda p, n: open(p, "w").write("SUCCESS"))["res"].startswith("FAILED:"))
check("OUTPUT_PATH constant is gone from vault_mount/_elevated_helper",
      not hasattr(vault_mount, "OUTPUT_PATH") and not hasattr(_elevated_helper, "OUTPUT_PATH"))
check("vault_mount uses sys.executable, not a hardcoded path",
      vault_mount.PYTHON_EXE == sys.executable)

# base64 stdin transport (E3-1): password never in the script, ASCII-only line
reset_env("helper8b")
vault_secret.save_secret("pässwörd-with-ümlaut\nand-newline")  # hostile password
state_binding._clear_cache_for_tests()
captured = {}

def handler8b(cmd, kw):
    if "powershell" not in str(cmd[0]):
        return None
    script = cmd[3]
    if "Unlock-BitLocker" in script:
        captured["script"] = script
        captured["stdin"] = kw.get("input")
        return "SUCCESS"  # simulate a working BitLocker step
    return None

with mock_subprocess(handler8b):
    result = _elevated_helper.do_unlock()
check("unlock via helper with non-ASCII password doesn't raise (base64 transport)",
      isinstance(result, str), repr(result))
import base64 as _b64
try:
    decoded = _b64.b64decode(captured["stdin"].strip()).decode("utf-8")
    ok8 = decoded == "pässwörd-with-ümlaut\nand-newline"
except Exception as e:
    ok8 = f"{type(e).__name__}: {e}"
check("base64 stdin decodes to the exact password (\\n and non-ASCII survive)",
      ok8 is True, repr(ok8))
check("password never appears in the PowerShell script text",
      "pässwörd" not in captured["script"] and "FromBase64String" in captured["script"])

# ==================================================================== 9
area("9: anchor MAC (V-05 fail-closed)")

reset_env("anchor9")
vault_secret.save_secret("anchor-vault-pw")
state_binding._clear_cache_for_tests()
pin_auth.init("3141")
check("anchor exists with MAC",
      "mac" in local_anchor.get() and local_anchor.get()["mac"].startswith("A2:"))
raw = json.load(open(local_anchor._ANCHOR_PATH, "r", encoding="utf-8"))
check("anchor body carries slots+witness", "slots" in raw and "witness" in raw, repr(raw.keys()))
# tamper: un-witness the pin upgrade, keep the MAC
raw["slots"]["pin_bound"] = False
with open(local_anchor._ANCHOR_PATH, "w", encoding="utf-8") as f:
    json.dump(raw, f)
check("tampered anchor -> is_marked fails CLOSED (True)",
      local_anchor.is_marked("pin_bound") is True)
check("tampered anchor -> is_marked True for ANY key",
      local_anchor.is_marked("never_existed_key") is True)
check("tampered anchor -> witness reads as absent",
      local_anchor.witness_get("pin") is None)
check("tampered anchor -> get() flags anchor_tampered",
      local_anchor.get().get("anchor_tampered") is True)
write_legacy_pin("9999")
check("legacy pin file under a tampered anchor -> STATE_CORRUPT (fail-closed)",
      pin_auth.verify("9999") == "STATE_CORRUPT")

# legacy flat anchor (pre-fix format) still reads
reset_env("anchor9b")
os.makedirs(local_anchor._ANCHOR_DIR, exist_ok=True)
with open(local_anchor._ANCHOR_PATH, "w", encoding="utf-8") as f:
    json.dump({"pin_bound": True}, f)
check("legacy flat anchor: is_marked works",
      local_anchor.is_marked("pin_bound") is True
      and local_anchor.is_marked("other") is False)

# ==================================================================== 10
area("10: matcher cosine correctness (C1-7)")


def _load_recognizer():
    """Import recognizer.py standalone -- the facerecog_engine package
    __init__ pulls optional modules that may be absent in the sandbox."""
    try:
        from facerecog_engine.core import recognizer as rec
        return rec
    except Exception:
        pkg = types.ModuleType("facerecog_engine")
        exc = types.ModuleType("facerecog_engine.exceptions")
        class ModelLoadError(Exception):
            pass
        exc.ModelLoadError = ModelLoadError
        core = types.ModuleType("facerecog_engine.core")
        sys.modules.setdefault("facerecog_engine", pkg)
        sys.modules.setdefault("facerecog_engine.exceptions", exc)
        sys.modules.setdefault("facerecog_engine.core", core)
        spec = importlib.util.spec_from_file_location(
            "facerecog_engine.core.recognizer",
            os.path.join(ROOT, "FaceRecog-Engine", "facerecog_engine", "core", "recognizer.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


rec = _load_recognizer()
rng = np.random.default_rng(123)
template = rng.standard_normal(512).astype(np.float32)
probe = rng.standard_normal(512).astype(np.float32)
tnorm = template / np.linalg.norm(template)
score_scaled = rec.FaceRecognizer.compute_similarity(template * 100.0, probe)
score_true = rec.FaceRecognizer.compute_similarity(tnorm, probe / np.linalg.norm(probe))
check("scaled (x100) template scores like the true cosine, not 100x it",
      abs(score_scaled - score_true) < 1e-4, f"{score_scaled:.4f} vs {score_true:.4f}")
check("scaled-template stranger score below the 0.75 threshold",
      score_scaled <= 1.0 and score_scaled < face_auth.RECOGNITION_THRESHOLD,
      f"{score_scaled:.4f}")
score_owner = rec.FaceRecognizer.compute_similarity(template * 100.0, tnorm)
check("owner probe still matches (score ~1.0)",
      score_owner > face_auth.RECOGNITION_THRESHOLD, f"{score_owner:.4f}")
check("zero-norm guard -> 0.0",
      rec.FaceRecognizer.compute_similarity(np.zeros(512), probe) == 0.0)
# and the storage layer refuses out-of-norm templates outright
try:
    face_auth._validate_owner_list([(np.asarray(template) * 100.0).tolist()])
    norm_ok = "accepted (BAD)"
except face_auth.TemplatesCorruptError:
    norm_ok = True
check("_validate_owner_list rejects x100-magnitude templates", norm_ok is True, repr(norm_ok))

# ==================================================================== 11
area("11: PIN regex (F-1/F-2)")

reset_env("regex11")
for bad in ["١٢٣٤", "１２３４", "1234\n", " 1234", "1234 ", "12,34", "12.34", "＋1"]:
    check(f"reject {bad!r}", pin_auth.init(bad) == "INVALID_PIN", pin_auth.init(bad))
check("accept '1234'", pin_auth.init("1234") == "INITIALIZED")
check("accept '123456789012' (12 digits)",
      pin_auth.force_reset("123456789012") == "INITIALIZED")
check("reject 13 digits", pin_auth.force_reset("1234567890123") == "INVALID_PIN")

# ==================================================================== 12
area("12: fresh-vault setup after secret-first registration (J4)")

reset_env("j4_12")
vault_secret.save_secret("secret-first-pw")
state_binding._clear_cache_for_tests()
install_fake_face_engine()
make_models("j4_12")
app_module, api = make_api()
r = api.setup_pin("1234", "1234")
check("secret registered FIRST, fresh vault -> setup allowed (no deadlock)",
      r.get("ok") is True and r.get("message") == "INITIALIZED", repr(r))

# ==================================================================== 13
area("13: access log (B1-2/B1-3/B1-4)")

reset_env("log13")
vault_secret.save_secret("log-vault-pw")
state_binding._clear_cache_for_tests()
for i in range(3):
    access_log.record("PIN", f"OK{i}")
check("chain intact after 3 records", access_log.verify_chain() == (True, -1))

# tail truncation (B1-2): drop the last line, chain stays internally valid
lines = open(access_log._LOG_PATH, "r", encoding="utf-8").read().splitlines(keepends=True)
with open(access_log._LOG_PATH, "w", encoding="utf-8") as f:
    f.writelines(lines[:-1])
v = access_log.verify_chain()
check("tail truncation detected via witness -> (False, -2)", v == (False, -2), repr(v))

# forged append without the key (B1-3): attacker appends a valid-chaining
# 5-field line; the witness count/head disagrees
reset_env("log13b")
vault_secret.save_secret("log-vault-pw")
state_binding._clear_cache_for_tests()
access_log.record("PIN", "OK")
entries = access_log.read_entries()
prev = entries[-1]["entry_hash"]
body = f"2026-01-01T00:00:00Z|ATTACKER|UNLOCK|{prev}"
eh = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()
with open(access_log._LOG_PATH, "a", encoding="utf-8") as f:
    f.write(f"{body}|{eh}\n")
v = access_log.verify_chain()
check("forged append (no key) detected -> (False, -2)", v == (False, -2), repr(v))

# corrupt byte (B1-4): history view survives, logging continues
reset_env("log13c")
vault_secret.save_secret("log-vault-pw")
state_binding._clear_cache_for_tests()
access_log.record("PIN", "OK")
access_log.record("PIN", "OK")
blob = bytearray(open(access_log._LOG_PATH, "rb").read())
blob[len(blob) // 2] ^= 0xFF
open(access_log._LOG_PATH, "wb").write(bytes(blob))
try:
    n = len(access_log.read_entries())
    v = access_log.verify_chain()
    access_log.record("PIN", "STILL_LOGGING")  # must not raise
    n2 = len(access_log.read_entries())
    ok13 = (v[0] is False and n2 >= n)
except Exception as e:
    ok13 = f"{type(e).__name__}: {e}"
check("corrupt byte: no crash, chain flagged broken, logging continues",
      ok13 is True, repr(ok13))

# 6-field entries verify normally when signed
reset_env("log13d")
vault_secret.save_secret("log-vault-pw")
state_binding._clear_cache_for_tests()
access_log.record("PIN", "OK")
first = access_log.read_entries()[0]
check("new entries carry a 6th MAC field", "mac" in first, repr(first.keys()))
check("witness mirrors the log", (local_anchor.witness_get("log") or {}).get("count") == 1)

# ==================================================================== 14
area("14: model TOFU (V-21)")

reset_env("tofu14")
make_models("tofu14")
face_auth._verify_models()  # first sight -> record
h1 = local_anchor.model_hash_get("antispoof_minifasv2.onnx")
h2 = local_anchor.model_hash_get("midas_v21_small_256.onnx")
check("TOFU records both model hashes on first sight",
      bool(h1) and bool(h2) and h1 != h2)
face_auth._verify_models()  # unchanged -> still fine
check("unchanged models re-verify cleanly", True)
with open(os.path.join(face_auth.MODEL_DIR, "antispoof_minifasv2.onnx"), "wb") as f:
    f.write(b"tampered-bytes")
try:
    face_auth._verify_models()
    got = "no exception (BAD)"
except face_auth.ModelsTamperedError:
    got = True
check("modified model -> ModelsTamperedError", got is True, repr(got))

# surfaced through enroll/verify
install_fake_face_engine()
with fake_camera():
    ok, msg = face_auth.enroll()
check("enroll surfaces MODELS_TAMPERED", (ok, msg) == (False, "MODELS_TAMPERED"), repr((ok, msg)))
face_auth._save_templates(unit_embeddings())  # enroll bypassed; plant enrollment
ok, msg = face_auth.verify()
check("verify surfaces MODELS_TAMPERED", (ok, msg) == (False, "MODELS_TAMPERED"), repr((ok, msg)))

# missing model files -> MODELS_MISSING (both files pre-checked now)
reset_env("tofu14b")
install_fake_face_engine()
with fake_camera():
    ok, msg = face_auth.enroll()
check("no model files -> enroll MODELS_MISSING", (ok, msg) == (False, "MODELS_MISSING"), repr((ok, msg)))
make_models("tofu14b")  # only midas present
os.remove(os.path.join(face_auth.MODEL_DIR, "antispoof_minifasv2.onnx"))
with fake_camera():
    ok, msg = face_auth.enroll()
check("antispoof missing (midas present) -> enroll MODELS_MISSING",
      (ok, msg) == (False, "MODELS_MISSING"), repr((ok, msg)))

# ==================================================================== 15
area("15: lock_vault ALREADY_LOCKED (V-27)")

reset_env("lock15")
install_fake_face_engine()
make_models("lock15")
app_module, api = make_api()
real_exists = os.path.exists

def fake_exists(path):
    if path == "V:\\":
        return False
    return real_exists(path)

os.path.exists = fake_exists
try:
    r = api.lock_vault()
finally:
    os.path.exists = real_exists
check("V:\\ absent -> lock_vault returns ALREADY_LOCKED (ok: True)",
      r == {"ok": True, "message": "ALREADY_LOCKED"}, repr(r))

# V:\ present -> normal path (mocked helper), auth flag reset
def fake_exists_true(path):
    if path == "V:\\":
        return True
    return real_exists(path)

import vault_mount as vm
real_lock = vm.lock
vm.lock = lambda: (True, "LOCKED")
os.path.exists = fake_exists_true
try:
    api._authenticated_this_session = True
    r = api.lock_vault()
finally:
    os.path.exists = real_exists
    vm.lock = real_lock
check("V:\\ present -> normal lock path, session de-authenticated",
      r.get("ok") is True and api._authenticated_this_session is False, repr(r))

# ==================================================================== bonus
area("bonus: backward-compat bindings + exception sanitization")

reset_env("bonus")
vault_secret.save_secret("bonus-vault-pw")
state_binding._clear_cache_for_tests()
# a LEGACY bare-hex binding (pre-fix file) must still verify
legacy_key = state_binding._derive_key_legacy(state_binding.PIN_PURPOSE)
legacy_binding = hmac.new(
    legacy_key, b'{"failed_attempts":0,"hash":"x","lockout_until":0.0,"lockout_tier":0}',
    hashlib.sha256).hexdigest()
check("legacy bare-hex binding verifies",
      state_binding.verify_binding(state_binding.PIN_PURPOSE,
                                   b'{"failed_attempts":0,"hash":"x","lockout_until":0.0,"lockout_tier":0}',
                                   legacy_binding) is True)
check("A2 and legacy bindings are distinguishable but both verifiable",
      state_binding.compute_binding(state_binding.PIN_PURPOSE, b"content").startswith("A2:"))

# a FULL old-format v2-bound pin file (previous app version: bare-hex
# binding, no generation) must still LOAD and get upgraded in place
reset_env("bonus_oldv2")
vault_secret.save_secret("old-v2-vault-pw")
state_binding._clear_cache_for_tests()
old_hash = PasswordHasher().hash("5150")
old_canonical = json.dumps(
    {"hash": old_hash, "failed_attempts": 1, "lockout_until": 0.0, "lockout_tier": 0},
    sort_keys=True, separators=(",", ":")).encode("utf-8")
old_key = state_binding._derive_key_legacy(state_binding.PIN_PURPOSE)
old_payload = {
    "format": "v2", "hash": old_hash, "failed_attempts": 1,
    "lockout_until": 0.0, "lockout_tier": 0,
    "binding": hmac.new(old_key, old_canonical, hashlib.sha256).hexdigest(),
}
with open(pin_auth._STATE_PATH, "wb") as f:
    f.write(dpapi_wrap(json.dumps(old_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")))
r = pin_auth.verify("5150")
check("old-format v2-bound file (bare-hex binding, no generation) still verifies",
      r == "OK", r)
upgraded = json.loads(dpapi_unwrap(open(pin_auth._STATE_PATH, "rb").read()))
check("old-format file upgraded in place (generation assigned, A2 re-bind)",
      upgraded.get("generation", 0) >= 1 and str(upgraded.get("binding", "")).startswith("A2:"),
      repr({k: upgraded.get(k) for k in ("generation", "binding")}))
r = pin_auth.verify("5150")
check("upgraded file keeps verifying", r == "OK", r)

# exception sanitization (A7): unlock/lock errors carry type names only
reset_env("bonus2")
app_module, api = make_api()
import vault_mount as vm2
real_unlock = vm2.unlock
def boom_unlock():
    raise RuntimeError("C:\\Users\\secret\\path\\details")
vm2.unlock = boom_unlock
try:
    r = api.verify_pin("0000")  # wrong pin -> returns before unlock; force OK path:
    pin_auth.init("1234")
    r = api.verify_pin("1234")
finally:
    vm2.unlock = real_unlock
check("UNLOCK_ERROR carries type name only, no path text",
      r.get("message") == "UNLOCK_ERROR:RuntimeError", repr(r))

# recovery-key pattern scan (A7/B1-5): renamed/copied keys are surfaced
reset_env("bonus3")
app_module, api = make_api()
app_module._recovery_key_scan["at"] = 0.0  # defeat the 30s cache for the test
fake_drive = os.path.join(_TMP_ROOT, "bonus3_drive")
os.makedirs(fake_drive, exist_ok=True)
with open(os.path.join(fake_drive, "my_notes.txt"), "w") as f:
    f.write("random notes\n" + "12345-67890-12345-67890-12345-67890-12345-67890-12345\n")
real_glob, real_exists = glob.glob, os.path.exists
try:
    glob.glob = lambda pat: ([os.path.join(fake_drive, "my_notes.txt")]
                             if pat == r"E:\LockerDrive\*.txt" else [])
    os.path.exists = lambda p: False if p.startswith("E:") else real_exists(p)
    status = api.get_status()
finally:
    glob.glob = real_glob
    os.path.exists = real_exists
check("48-digit key pattern in E:\\LockerDrive\\*.txt -> bitlocker_recovery_key_on_drive",
      status.get("bitlocker_recovery_key_on_drive") is True, repr(status))

# helper __main__ protocol (A8): argv -> "<nonce>|<result>" file
reset_env("bonus4")
vault_secret.save_secret("bonus4-vault-pw")
state_binding._clear_cache_for_tests()
nonce = "deadbeef" * 4
result_path = os.path.join(_TMP_ROOT, "bonus4_result.txt")
if os.path.exists(result_path):
    os.remove(result_path)
old_argv = sys.argv
sys.argv = ["_elevated_helper.py", "unlock", result_path, nonce]
try:
    with mock_subprocess(lambda cmd, kw: "SUCCESS" if "Unlock-BitLocker" in (cmd[3] if len(cmd) > 3 else "") else None):
        import runpy as _runpy
        _runpy.run_path(os.path.join(ROOT, "engine", "_elevated_helper.py"), run_name="__main__")
finally:
    sys.argv = old_argv
content = open(result_path).read() if os.path.exists(result_path) else "<missing>"
check("helper __main__ writes '<nonce>|SUCCESS' to the argv path",
      content == f"{nonce}|SUCCESS", repr(content))

# ------------------------------------------------------------------ result
print()
print(f"RESULT: {_PASS} passed, {_FAIL} failed")
if _FAILS:
    print("Failures:")
    for f in _FAILS:
        print("  -", f)
shutil.rmtree(_TMP_ROOT, ignore_errors=True)
sys.exit(1 if _FAIL else 0)
