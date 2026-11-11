"""
Kushal Studios / LockerDrive v16.0 -- Chameleon Unified Entrypoint

Default behavior (double-clicking 'Kushal Studios.exe' with no arguments):
  -> Requests upfront UAC daemon -> launches Kushal Studios v3.0 (Gamepad Icon)
  -> Entering the configured Studio AI trigger morphs into LockerDrive Vault Mode (Gold Padlock Icon)
"""
import sys
import os
import time

# Prevent 'RuntimeError: lost sys.stdin' in --windowed PyInstaller builds
if sys.stdin is None:
    try:
        sys.stdin = open(os.devnull, "r", encoding="utf-8", errors="ignore")
    except Exception:
        pass
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")
    except Exception:
        pass


def _get_bundle_root() -> str:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return str(sys._MEIPASS)
    return os.path.dirname(os.path.abspath(__file__))


HERE = _get_bundle_root()
for _p in (HERE, os.path.join(HERE, "engine"), os.path.join(HERE, "ui"), os.path.join(HERE, "FaceRecog-Engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _ensure_uac_upfront(reuse: bool = False):
    """Requests the UAC prompt FIRST and waits until the elevated daemon is ready
    before any GUI window opens, so unlock/lock/auto-lock never prompt later."""
    try:
        import vault_mount
        vault_mount.ensure_elevated_daemon(timeout_s=30.0, force_restart=(not reuse))
    except Exception:
        pass


def _normalize_argv(raw_args: list[str]) -> list[str]:
    """Normalizes argv whether invoked via python script.py or frozen Kushal Studios.exe."""
    args = list(raw_args)
    if not args:
        return args
    first_low = args[0].lower().replace("\\", "/")
    if first_low.endswith("_elevated_helper.py"):
        args.pop(0)
        if args and args[0] != "--daemon":
            args.insert(0, "--helper-single")
    elif first_low.endswith("launch_game.py"):
        args[0] = "--launch-game"
    elif first_low.endswith("app.py"):
        args[0] = "--gui"
    elif first_low.endswith(("decoy_app.py", "main.py")):
        args.pop(0)
    return args


def _launch_decoy_studio(reuse_daemon: bool = False):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kushal.Studios.Arcade.v16")
    except Exception:
        pass
    _ensure_uac_upfront(reuse=reuse_daemon)
    from ui.decoy_app import DecoyApp
    app = DecoyApp()
    app.mainloop()


def _launch_vault_gui(reuse_daemon: bool = False):
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kushal.LockerDrive.Vault.v16")
    except Exception:
        pass
    _ensure_uac_upfront(reuse=reuse_daemon)
    from ui.app import main as gui_main
    gui_main()


def main():
    args = _normalize_argv(sys.argv[1:])
    arg = args[0] if args else ""
    reuse_daemon = "--reuse-daemon" in args

    # 1. Background Elevated UAC Daemon mode
    if arg == "--daemon" and len(args) >= 2:
        import _elevated_helper
        tok = args[2] if len(args) > 2 else None
        ppid = int(args[3]) if len(args) > 3 else 0
        _elevated_helper.run_daemon(args[1], explicit_token=tok, parent_pid=ppid)
        return

    # 2. Single-shot Elevated Helper fallback mode
    if arg == "--helper-single" and len(args) >= 4:
        import _elevated_helper
        act, rpath, nonce = args[1], args[2], args[3]
        res = _elevated_helper.do_unlock() if act == "unlock" else _elevated_helper.do_lock()
        try:
            with open(rpath, "w", encoding="utf-8") as f:
                f.write(f"{nonce}|{res}")
        except OSError:
            pass
        return

    # 3. Subprocess Pygame Arcade launcher mode
    if arg == "--launch-game" and len(args) >= 2:
        from ui.games import GAMES
        idx = int(args[1])
        if 0 <= idx < len(GAMES):
            import pygame
            pygame.init()
            GAMES[idx]["func"]()
            pygame.display.quit()
            pygame.quit()
        return

    if arg == "--start-daemon":
        import vault_mount
        ok = vault_mount.ensure_elevated_daemon(timeout_s=30.0, force_restart=True)
        print("UAC Daemon:", "ACTIVE" if ok else "DECLINED")
        return

    # 4. Vault GUI mode (launched when Studio AI trigger matches commitment or via --gui)
    if arg in ("--gui", "--vault"):
        _launch_vault_gui(reuse_daemon=reuse_daemon)
        return

    # 5. CLI Face Enrollment mode
    if arg == "--enroll-face":
        import face_auth
        ok, msg = face_auth.enroll()
        print(f"\nEnrollment: {msg}")
        return

    # 6. Default mode (no args or --decoy): Launch Kushal Studios Decoy Hub!
    #    Double-clicking 'Kushal Studios.exe' ALWAYS opens Kushal Studios and never touches sys.stdin.
    _launch_decoy_studio(reuse_daemon=reuse_daemon)


if __name__ == "__main__":
    main()
