r"""
session_watch.py -- lock the vault the moment the Windows session locks.

The one doc-04 hardening item that was adopted when the architecture was
designed (docs/05 section E7 sketched the recipe) but never actually
coded: without it, the only automatic re-lock is the UI's 5-minute idle
timer, so Win+L, a screensaver lock, or a fast user switch leaves the
vault mounted for up to five minutes while nobody is at the desk.

How it works (the standard Win32 recipe, per Microsoft's
WM_WTSSESSION_CHANGE / WTSRegisterSessionNotification docs):
  * A hidden window is created on a dedicated background thread and
    registered with WTSRegisterSessionNotification(hwnd,
    NOTIFY_FOR_THIS_SESSION).
  * Windows then posts WM_WTSSESSION_CHANGE (0x02B1) to that window
    whenever the session state changes; wParam says what happened.
  * WTS_SESSION_LOCK (Win+L, screensaver lock), WTS_SESSION_LOGOFF and
    the console/remote DISCONNECT events (fast user switch) all mean
    "the person who unlocked this vault is no longer at the keyboard" --
    on any of those, the configured callback fires.

Honest limitation, identical to the 5-minute idle timer: locking runs
through the briefly-elevated helper (elevation is per-action by design),
and a UAC prompt cannot be answered while the session is locked. So the
lock is ATTEMPTED instantly and the helper sits on its prompt until the
user returns; this narrows the exposure window from minutes to seconds,
but cannot eliminate it under scoped elevation. (The alternative -- a
permanently elevated helper service -- was considered and rejected when
the elevation model was chosen.)

Failure model: NEVER raises into the app. If win32ts/win32gui are
missing, registration fails, or the window can't be created, start()
returns False and the app carries on exactly as before -- a missing
hardening layer, not a broken app.
"""
import threading

WM_WTSSESSION_CHANGE = 0x02B1

# wParam values that mean "the unlocking user left the keyboard":
#   0x2 WTS_CONSOLE_DISCONNECT  (fast user switch away from this session)
#   0x4 WTS_REMOTE_DISCONNECT   (remote session disconnected)
#   0x6 WTS_SESSION_LOGOFF      (user logging off)
#   0x7 WTS_SESSION_LOCK        (Win+L / screensaver / idle lock)
# Deliberately NOT trigger: 0x1/0x3 (connect -- someone is arriving),
# 0x5 (logon), 0x8 (unlock -- the user is back; never auto-unlock).
LOCK_TRIGGERING_STATES = frozenset({0x2, 0x4, 0x6, 0x7})


def should_lock(wparam_value) -> bool:
    """Pure decision logic, separated from all Win32 plumbing so it can
    be unit-tested without Windows firing real events at us. Strict on
    type: the wndproc always delivers wParam as an int, so anything
    else (string, float, None, ...) is garbage and never triggers."""
    if isinstance(wparam_value, bool) or not isinstance(wparam_value, int):
        return False
    return wparam_value in LOCK_TRIGGERING_STATES


class SessionWatcher:
    """Background thread that turns session-lock events into a callback.

    Usage:
        w = SessionWatcher(callback)   # callback(): no args, never raises out
        if not w.start():
            ...  # watcher unavailable; app continues without it
        ...
        w.stop()   # optional -- the thread is a daemon and the app exits
                   # hard via os._exit(0) anyway
    """

    def __init__(self, on_lockout):
        self._on_lockout = on_lockout
        self._thread = None
        self._hwnd = 0
        self._registered = False
        self._alive = threading.Event()    # set once the thread finished trying to register
        self._stopped = threading.Event()

    # -- lifetime ---------------------------------------------------------
    def start(self) -> bool:
        """Start the watcher; True iff it is genuinely registered and
        pumping. Returns False (never raises) if any Win32 piece is
        missing or refuses."""
        try:
            import win32gui  # noqa: F401
            import win32ts  # noqa: F401
        except Exception:
            return False
        if self._thread is not None:
            return self._registered
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="LockerDriveSessionWatch")
        self._thread.start()
        self._alive.wait(timeout=5.0)
        return self._registered

    def stop(self) -> None:
        """Ask the message loop to exit (posts WM_CLOSE; the watcher
        thread's own wndproc runs DestroyWindow, which posts WM_QUIT and
        ends the loop). Safe to call from any thread, safe to repeat."""
        self._stopped.set()
        hwnd = self._hwnd
        if not hwnd:
            return
        try:
            import win32gui
            win32gui.PostMessage(hwnd, 0x0010, 0, 0)  # WM_CLOSE
        except Exception:
            pass

    # -- internals (all on the watcher thread) ---------------------------
    def _run(self):
        import win32gui
        import win32con
        try:
            import win32ts
        except Exception:
            self._alive.set()  # unblock start() -- registration cannot happen
            return

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "LockerDriveSessionWatch"
        wc.lpfnWndProc = self._wnd_proc
        try:
            win32gui.RegisterClass(wc)
            # A plain hidden window (never shown, no WS_VISIBLE): this is
            # the classic recipe -- WTS notifications are delivered to the
            # exact registered hwnd, so it just needs to exist and pump.
            self._hwnd = win32gui.CreateWindowEx(
                0, wc.lpszClassName, "LockerDriveSessionWatch",
                0, 0, 0, 0, 0, 0, 0, 0, None)
            if not self._hwnd:
                raise OSError("CreateWindowEx returned null")
            # Must be called from the thread that owns the window.
            win32ts.WTSRegisterSessionNotification(self._hwnd, 0)  # NOTIFY_FOR_THIS_SESSION
            self._registered = True
        except Exception:
            self._alive.set()  # unblock start(); _registered stays False
            return
        self._alive.set()

        # GetMessage(0, ...) pulls BOTH window messages and thread
        # messages -- WM_QUIT from PostQuitMessage arrives as a thread
        # message, so passing hwnd=0 (not self._hwnd) is required.
        while not self._stopped.is_set():
            try:
                rc, msg = win32gui.GetMessage(0, 0, 0)
            except Exception:
                break
            if rc == 0 or rc == -1:  # WM_QUIT, or a hard error
                break
            try:
                win32gui.TranslateMessage(msg)
                win32gui.DispatchMessage(msg)
            except Exception:
                break

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        import win32gui
        import win32con
        if msg == WM_WTSSESSION_CHANGE:
            if should_lock(wparam):
                self._fire()
            return 0
        if msg == win32con.WM_DESTROY:
            try:
                # Best-effort unregister; the OS cleans up at process
                # exit regardless.
                import win32ts
                win32ts.WTSUnRegisterSessionNotification(hwnd)
            except Exception:
                pass
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _fire(self):
        # Runs on the watcher thread. The callback must never be able to
        # take down the message loop, whatever it does.
        try:
            self._on_lockout()
        except Exception:
            pass
