r"""
test_session_watch.py -- tests for engine/session_watch.py + the two
doc-04 completion items wired into ui/app.py (2026-09-21).

Checks:
  1. should_lock() decision logic -- exactly the four "user left the
     keyboard" wParam states trigger; connect/logon/unlock never do;
     garbage inputs never raise.
  2. Real watcher lifecycle on this machine: start() registers a hidden
     window with WTSRegisterSessionNotification, and a SYNTHETIC
     WM_WTSSESSION_CHANGE posted straight to that hwnd travels through
     the real wndproc -> should_lock -> callback (wparam 0x7 fires,
     0x8 does not). This proves the full plumbing without actually
     locking the user's Windows session -- the one thing this test
     deliberately does NOT do.
  3. stop() ends the pump thread cleanly.
  4. DLL search-path hardening: app._harden_dll_search() (the REAL
     function from ui/app.py) succeeds, and the entire native stack
     (cv2, numpy, argon2, insightface, onnxruntime, win32*) still
     imports cleanly AFTER the restricted search order is in force --
     the same sequence production runs (flags first, lazy ML imports
     later).

Exit code 0 iff every check passes. Windows-only (win32ts/win32gui),
because the code under test is Windows-only.
"""
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "ui"))

import session_watch  # noqa: E402

_results = []


def check(name, fn):
    try:
        fn()
        _results.append((name, True, ""))
        print(f"PASS  {name}")
    except Exception as e:
        _results.append((name, False, repr(e)))
        print(f"FAIL  {name}: {e!r}")


def _c1_should_lock():
    t = session_watch.should_lock
    # The four lock-triggering states
    assert t(0x2) is True, "WTS_CONSOLE_DISCONNECT must trigger"
    assert t(0x4) is True, "WTS_REMOTE_DISCONNECT must trigger"
    assert t(0x6) is True, "WTS_SESSION_LOGOFF must trigger"
    assert t(0x7) is True, "WTS_SESSION_LOCK must trigger"
    # Everything that means "someone is arriving / back" must NOT trigger
    assert t(0x1) is False, "WTS_CONSOLE_CONNECT must not trigger"
    assert t(0x3) is False, "WTS_REMOTE_CONNECT must not trigger"
    assert t(0x5) is False, "WTS_SESSION_LOGON must not trigger"
    assert t(0x8) is False, "WTS_SESSION_UNLOCK must not trigger"
    # Garbage never raises and never triggers
    for garbage in (None, "", "7", 3.5, [], -1, 0, 0x9):
        assert t(garbage) is False, f"garbage input {garbage!r} must not trigger"


def _c2_lifecycle_and_synthetic_event():
    fired = threading.Event()
    watcher = session_watch.SessionWatcher(fired.set)
    ok = watcher.start()
    assert ok is True, "watcher failed to register on this machine"
    assert watcher._registered and watcher._hwnd, "registered but no hwnd?"

    # Synthetic event through the REAL wndproc: post WM_WTSSESSION_CHANGE
    # with wparam=WTS_SESSION_LOCK straight to the watcher's window.
    import win32gui
    win32gui.PostMessage(watcher._hwnd, session_watch.WM_WTSSESSION_CHANGE, 0x7, 0)
    if not fired.wait(timeout=3.0):
        # one retry -- message pumps occasionally coalesce under load
        win32gui.PostMessage(watcher._hwnd, session_watch.WM_WTSSESSION_CHANGE, 0x7, 0)
        assert fired.wait(timeout=3.0), "callback never fired for WTS_SESSION_LOCK"

    # Negative control: WTS_SESSION_UNLOCK must NOT fire a second time
    fired.clear()
    win32gui.PostMessage(watcher._hwnd, session_watch.WM_WTSSESSION_CHANGE, 0x8, 0)
    import time
    time.sleep(0.5)
    assert not fired.is_set(), "callback fired for WTS_SESSION_UNLOCK (must not)"

    # 3. clean shutdown
    watcher.stop()
    watcher._thread.join(timeout=5.0)
    assert not watcher._thread.is_alive(), "watcher thread did not exit after stop()"


def _c4_dll_flags_native_stack():
    import app  # ui/app.py -- module import only; no window, no Api()
    ok = app._harden_dll_search()
    assert ok is True, "SetDefaultDllDirectories returned FALSE on this machine"

    # The restricted search order is now in force for THIS process --
    # import the full native stack exactly like the app's lazy loads do.
    import cv2          # noqa: F401
    import numpy        # noqa: F401
    import argon2       # noqa: F401
    import onnxruntime  # noqa: F401
    import insightface  # noqa: F401
    import win32gui     # noqa: F401
    import win32ts      # noqa: F401
    import webview      # noqa: F401
    # If any of these raised ImportError/LoadError, the flags broke a
    # DLL dependency and this check fails loudly instead of the app
    # breaking silently at the user's next launch.


def main():
    print("== session_watch / doc-04 completion suite ==")
    check("1. should_lock decision table", _c1_should_lock)
    check("2. lifecycle + synthetic WM_WTSSESSION_CHANGE", _c2_lifecycle_and_synthetic_event)
    check("4. DLL flags + full native stack import", _c4_dll_flags_native_stack)

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"SESSION_WATCH_SUITE: {passed}/{total} " + ("PASS" if passed == total else "FAIL"))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
