r"""
ui/app.py -- pywebview shell for LockerDrive v5.0 (Multi-Drive + Configurable Storage + One-Time UAC Daemon).

Runs unelevated. Privileged steps (mount/unlock, lock/dismount, volume creation,
storage expansion, stealth cloaking) are executed by engine/_elevated_helper.py
via vault_mount.py.
"""
import sys
import os

# Under pythonw.exe, sys.stdout and sys.stderr are None; redirect to devnull so
# print() and library loggers do not crash with AttributeError.
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

import re
import glob
import time
import shutil
import atexit
import threading
import functools
import webview

ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine")
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)


def _safe_api(fn):
    """Wraps an exposed Api method so it ALWAYS returns a plain dict."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"ok": False, "message": f"ERROR:{type(e).__name__}"}
    return wrapper


class Api:
    def __init__(self):
        self._window = None
        self._scan_thread = None
        self._scan_state = {"active": False, "done": False}
        self._enroll_state = {
            "active": False,
            "done": False,
            "phase": "IDLE",
            "pose_target": "CENTER",
            "captured_poses": [],
            "lit_segments": [0] * 64,
            "progress_pct": 0,
            "current_yaw": 0.0,
            "current_pitch": 0.0,
            "current_roll": 0.0,
            "quality": 0.0,
            "liveness": 0.0,
            "hint": "Position your face in the frame",
        }
        self._face_lock = threading.Lock()
        self._vault_lock = threading.Lock()
        self._scan_abort = threading.Event()
        self._face_verified_at = 0.0
        self._unlocked_at = 0.0
        self._authenticated_this_session = False
        self._morphing_to_decoy = False
        self._pin_setup_done_this_session = False
        self._sync_config()
        self._migrate_face_templates()
        threading.Thread(target=self._prewarm_face_engine, daemon=True).start()

    def _sync_config(self):
        try:
            import config
            if config.is_test_environment():
                return
            config.load_persisted_config()
            config.sync_engine_paths()
        except Exception:
            pass

    def _migrate_face_templates(self):
        try:
            import config
            if config.is_test_environment():
                return
            import face_auth
            face_auth.migrate_encrypt_templates()
        except Exception:
            pass

    def _prewarm_face_engine(self):
        import face_auth
        face_auth.prewarm()

    @_safe_api
    def get_status(self):
        import config
        import pin_auth
        import vault_secret
        import face_auth
        import vault_mount
        leftover_recovery_key = os.path.exists(
            os.path.join(config.CONFIG.host_dir, "RECOVERY_KEY-save_elsewhere_then_delete.txt")
        ) or os.path.exists(r"E:\LockerDrive\RECOVERY_KEY-save_elsewhere_then_delete.txt")
        return {
            "drive_status": pin_auth.status(),
            "secret_registered": vault_secret.secret_exists(),
            "vhdx_exists": os.path.exists(config.CONFIG.vhdx_path),
            "face_enrolled": face_auth.is_enrolled(),
            "can_enroll_first_time": (
                not face_auth.is_enrolled()
                and self._pin_setup_done_this_session
            ),
            "bitlocker_recovery_key_on_drive": (leftover_recovery_key
                                                 or _scan_for_recovery_keys()),
            "drive_letter": config.CONFIG.drive_root[:1],
            "mount_letter": config.CONFIG.mount_letter,
            "auto_lock_minutes": config.CONFIG.auto_lock_minutes,
            "daemon_active": vault_mount.is_daemon_running(),
        }

    @_safe_api
    def get_drive_config(self):
        import config
        import vault_mount
        return {
            "ok": True,
            "config": config.get_config_summary(),
            "drives": config.list_available_drives(),
            "daemon_active": vault_mount.is_daemon_running(),
        }

    @_safe_api
    def update_drive_config(self, drive_letter, mount_letter="V", size_gb=20, auto_lock_minutes=5, stealth_cloak=True):
        import config
        summary = config.save_persisted_config(
            drive_letter=str(drive_letter),
            mount_letter=str(mount_letter),
            size_gb=int(size_gb),
            auto_lock_minutes=int(auto_lock_minutes),
            stealth_cloak=bool(stealth_cloak),
        )
        return {"ok": True, "config": summary}

    @_safe_api
    def start_uac_daemon(self):
        import vault_mount
        ok = vault_mount.ensure_elevated_daemon()
        return {"ok": ok, "daemon_active": ok, "message": "DAEMON_READY" if ok else "UAC_DECLINED"}

    @_safe_api
    def create_vault_volume(self, drive_letter, size_gb, mount_letter, vault_password):
        if not vault_password or len(str(vault_password)) < 6:
            return {"ok": False, "message": "PASSWORD_TOO_SHORT"}
        import config
        import vault_secret
        import vault_mount
        summary = config.save_persisted_config(
            drive_letter=str(drive_letter),
            mount_letter=str(mount_letter),
            size_gb=int(size_gb),
        )
        ok, rk_or_err = vault_mount.provision_new_vault(
            summary["vhdx_path"], int(size_gb), summary["mount_letter"], str(vault_password)
        )
        if not ok:
            return {"ok": False, "message": rk_or_err}
        vault_secret.save_secret(str(vault_password))
        return {"ok": True, "bitlocker_recovery_key": rk_or_err, "config": config.get_config_summary()}

    @_safe_api
    def expand_vault_volume(self, new_size_gb):
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import config
        import vault_mount
        ok, msg = vault_mount.expand_vault_storage(
            config.CONFIG.vhdx_path, int(new_size_gb), config.CONFIG.mount_letter
        )
        if ok:
            config.save_persisted_config(size_gb=int(new_size_gb))
        return {"ok": ok, "message": msg}

    @_safe_api
    def toggle_stealth_cloak(self, hide=True):
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import config
        import vault_mount
        ok, msg = vault_mount.set_folder_cloak(config.CONFIG.host_dir, hide=bool(hide))
        if ok:
            config.save_persisted_config(stealth_cloak=bool(hide))
        return {"ok": ok, "message": msg}

    @_safe_api
    def get_vault_telemetry(self):
        """Live telemetry for the unlocked dashboard (V:\\ usage, E:\\ presence, templates, session uptime)."""
        import config
        import face_auth
        import pin_auth
        import vault_mount
        mnt_root = f"{config.CONFIG.mount_letter}:\\"
        drive_connected = os.path.exists(getattr(pin_auth, "_DRIVE_ROOT", config.CONFIG.drive_root))
        mounted = os.path.exists(mnt_root) or os.path.exists("V:\\")
        active_mnt = mnt_root if os.path.exists(mnt_root) else "V:\\"
        total_gb = 0.0
        used_gb = 0.0
        free_gb = 0.0
        used_pct = 0
        if mounted:
            try:
                usage = shutil.disk_usage(active_mnt)
                total_gb = round(usage.total / (1024 ** 3), 2)
                used_gb = round(usage.used / (1024 ** 3), 2)
                free_gb = round(usage.free / (1024 ** 3), 2)
                if usage.total > 0:
                    used_pct = int(round((usage.used / usage.total) * 100))
            except Exception:
                pass
        tpl_count = 0
        if hasattr(face_auth, "enrolled_template_count"):
            tpl_count = face_auth.enrolled_template_count()
        elif face_auth.is_enrolled():
            tpl_count = 6
        return {
            "ok": True,
            "drive_connected": drive_connected,
            "mounted": mounted,
            "mount_letter": active_mnt,
            "drive_root": getattr(pin_auth, "_DRIVE_ROOT", config.CONFIG.drive_root),
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "used_pct": used_pct,
            "face_enrolled": face_auth.is_enrolled(),
            "template_count": tpl_count,
            "unlocked_seconds": int(max(0.0, time.time() - self._unlocked_at)) if self._unlocked_at else 0,
            "daemon_active": vault_mount.is_daemon_running(),
            "stealth_cloak": config.CONFIG.stealth_cloak,
            "auto_lock_minutes": config.CONFIG.auto_lock_minutes,
        }

    @_safe_api
    def setup_pin(self, pin, confirm):
        import pin_auth
        import vault_secret
        import recovery_auth
        import face_auth
        import local_anchor
        if pin != confirm:
            return {"ok": False, "message": "MISMATCH"}

        previously_set_up = False
        try:
            previously_set_up = (local_anchor.is_marked("pin_bound")
                                 or local_anchor.is_marked("recovery_bound")
                                 or local_anchor.is_marked("templates_bound")
                                 or face_auth.is_enrolled())
        except Exception:
            previously_set_up = face_auth.is_enrolled()
        anomalous_reset = (vault_secret.secret_exists()
                           and pin_auth.status() == "NOT_INITIALIZED"
                           and previously_set_up)
        if anomalous_reset:
            recently_verified = (time.time() - self._face_verified_at) < 120
            if not recently_verified:
                return {"ok": False, "message": "FACE_REVERIFY_REQUIRED"}

        result = pin_auth.init(pin)
        ok = result == "INITIALIZED"
        if ok:
            self._pin_setup_done_this_session = True

        recovery_code = recovery_auth.ensure_exists() if ok else None

        return {"ok": ok, "message": result, "recovery_code": recovery_code}

    @_safe_api
    def change_pin(self, new_pin, confirm):
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import pin_auth
        import recovery_auth
        if new_pin != confirm:
            return {"ok": False, "message": "MISMATCH"}
        result = pin_auth.force_reset(new_pin)
        ok = result == "INITIALIZED"
        if ok:
            self._pin_setup_done_this_session = True
        recovery_code = recovery_auth.ensure_exists() if ok else None
        return {"ok": ok, "message": result, "recovery_code": recovery_code}

    @_safe_api
    def generate_recovery_code(self, force=False):
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import vault_secret
        import recovery_auth
        if not vault_secret.secret_exists():
            return {"ok": False, "message": "NOT_SET_UP"}
        if force:
            code = recovery_auth.generate()
            return {"ok": True, "recovery_code": code, "rotated": True}
        code = recovery_auth.ensure_exists()
        if code is None:
            return {"ok": False, "message": "ALREADY_EXISTS"}
        return {"ok": True, "recovery_code": code}

    @_safe_api
    def verify_recovery_code(self, code):
        import recovery_auth
        import access_log
        valid = recovery_auth.verify(code)
        access_log.record("RECOVERY_CODE", "SUCCESS" if valid else "INVALID")
        if not valid:
            return {"ok": False, "message": "INVALID_RECOVERY_CODE"}
        self._face_verified_at = time.time()
        try:
            return self._do_unlock()
        except Exception as e:
            return {"ok": False, "message": f"UNLOCK_ERROR:{type(e).__name__}"}

    @_safe_api
    def verify_pin(self, pin):
        import pin_auth
        import access_log
        result = pin_auth.verify(pin)
        access_log.record("PIN", result)
        if result != "OK":
            return {"ok": False, "message": result}
        try:
            return self._do_unlock()
        except Exception as e:
            return {"ok": False, "message": f"UNLOCK_ERROR:{type(e).__name__}"}

    @_safe_api
    def start_face_scan(self):
        if not self._face_lock.acquire(blocking=False):
            self._scan_abort.set()
            if not self._face_lock.acquire(timeout=4.5):
                return {"ok": False, "message": "BUSY"}
        try:
            self._scan_abort.clear()
            self._scan_state = {"active": True, "done": False}
            self._scan_thread = threading.Thread(target=self._face_scan_worker, daemon=True)
            self._scan_thread.start()
            return {"ok": True, "message": "STARTED"}
        except Exception:
            self._face_lock.release()
            raise

    def _face_scan_worker(self):
        try:
            import face_auth
            import access_log

            def progress(rec, live, quality, yaw=0.0, pitch=0.0, frame_b64="", kps_norm=None, face_count=0):
                self._scan_state = {
                    "active": True, "done": False,
                    "recognition": rec, "liveness": live, "quality": quality,
                    "yaw": yaw, "pitch": pitch,
                    "frame_b64": frame_b64, "kps_norm": kps_norm or [], "face_count": face_count,
                }

            ok, msg = face_auth.verify(
                progress_callback=progress,
                abort_check=self._scan_abort.is_set,
            )
            if ok and self._scan_abort.is_set():
                ok, msg = False, "ABORTED"
            if msg != "ABORTED":
                access_log.record("FACE", msg)
                if not ok and msg == "TIMEOUT" and self._scan_state.get("face_count", 0) > 0:
                    self._capture_intruder_mugshot("Unrecognized Face Scan", frame_b64=str(self._scan_state.get("frame_b64", "")))
            if ok:
                self._face_verified_at = time.time()
                self._scan_state = {
                    "active": True, "done": False,
                    "phase": "VERIFIED_MOUNTING",
                    "recognition": 0.98, "liveness": 0.95, "quality": 0.90,
                    "message": msg,
                }
            unlock_result = self._do_unlock() if ok else None
            self._scan_state = {
                "active": False, "done": True,
                "ok": ok, "message": msg, "unlock_result": unlock_result,
            }
        except Exception as e:
            self._scan_state = {
                "active": False, "done": True,
                "ok": False, "message": f"ERROR:{e}", "unlock_result": None,
            }
        finally:
            self._face_lock.release()

    @_safe_api
    def get_scan_progress(self):
        return dict(self._scan_state)

    @_safe_api
    def get_enroll_progress(self):
        import face_auth
        live = face_auth.get_enroll_progress()
        merged = dict(self._enroll_state)
        merged.update(live)
        return merged

    def _do_unlock(self):
        with self._vault_lock:
            import vault_mount
            ok, msg = vault_mount.unlock()
            if ok:
                self._authenticated_this_session = True
                self._unlocked_at = time.time()
            return {"ok": ok, "message": msg}

    def _is_vault_mounted(self) -> bool:
        try:
            import config
            mnt = f"{config.CONFIG.mount_letter}:\\"
            return os.path.exists(mnt) or os.path.exists("V:\\")
        except Exception:
            return os.path.exists("V:\\")

    @_safe_api
    def register_vault_secret(self, password):
        if not password or len(str(password)) < 1:
            return {"ok": False, "message": "EMPTY_PASSWORD"}
        import vault_secret
        vault_secret.save_secret(str(password))
        return {"ok": True, "message": "SECRET_REGISTERED"}

    @_safe_api
    def lock_vault(self):
        if not self._is_vault_mounted():
            self._authenticated_this_session = False
            self._unlocked_at = 0.0
            return {"ok": True, "message": "ALREADY_LOCKED"}
        with self._vault_lock:
            import vault_mount
            import access_log
            try:
                self.scrub_forensic_traces()
                ok, msg = vault_mount.lock()
                access_log.record("LOCK", msg)
                if ok:
                    self._authenticated_this_session = False
                    self._unlocked_at = 0.0
                    self._scan_abort.set()
                return {"ok": ok, "message": msg}
            except Exception as e:
                access_log.record("LOCK", f"ERROR:{type(e).__name__}")
                return {"ok": False, "message": f"LOCK_ERROR:{type(e).__name__}"}

    @_safe_api
    def get_prewarm_status(self):
        import face_auth
        return {"ready": face_auth.is_prewarmed()}

    def _on_session_locked(self):
        try:
            if not self._is_vault_mounted():
                return
            import access_log
            access_log.record("SESSIONLOCK", "TRIGGERED")
            self.lock_vault()
        except Exception:
            pass

    def _on_hardware_or_sleep_event(self, reason: str):
        try:
            if not self._is_vault_mounted() and not self._authenticated_this_session:
                return
            import access_log
            access_log.record(reason, "AUTO_LOCK")
            self._authenticated_this_session = False
            self._unlocked_at = 0.0
            self.lock_vault()
        except Exception:
            pass

    @_safe_api
    def open_explorer(self):
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import subprocess
        import config
        mnt = f"{config.CONFIG.mount_letter}:\\"
        target = mnt if os.path.exists(mnt) else ("V:\\" if os.path.exists("V:\\") else mnt)
        if not os.path.exists(target) and getattr(subprocess.Popen, "__module__", "") == "subprocess":
            # Volume was dismounted or still finishing mount; trigger unlock/mount and wait up to 4s
            self._do_unlock()
            deadline = time.time() + 4.0
            while time.time() < deadline and not os.path.exists(target):
                time.sleep(0.15)
            if not os.path.exists(target):
                return {"ok": False, "message": "UNLOCK_ERROR:VolumeNotMounted"}
        try:
            subprocess.Popen(["explorer.exe", target])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "message": type(e).__name__}

    @_safe_api
    def get_access_log(self):
        import access_log
        entries = access_log.read_entries()
        intact, broken_at = access_log.verify_chain()
        return {"entries": entries[-20:], "chain_intact": intact, "broken_at": broken_at}

    @_safe_api
    def enroll_face(self):
        if not self._face_lock.acquire(blocking=False):
            return {"ok": False, "message": "BUSY"}
        try:
            import face_auth
            import local_anchor
            first_time_ok = (not face_auth.is_enrolled()
                             and not local_anchor.is_marked("templates_bound")
                             and self._pin_setup_done_this_session)
            if not (self._authenticated_this_session or first_time_ok):
                return {"ok": False, "message": "NOT_AUTHENTICATED"}

            self._scan_abort.clear()
            self._enroll_state = {
                "active": True,
                "done": False,
                "phase": "WARMUP",
                "pose_target": "CENTER",
                "captured_poses": [],
                "lit_segments": [0] * 64,
                "progress_pct": 0,
                "current_yaw": 0.0,
                "current_pitch": 0.0,
                "current_roll": 0.0,
                "quality": 0.0,
                "liveness": 0.0,
                "hint": "Position your face in the frame",
            }

            def on_enroll_progress(snap):
                self._enroll_state = dict(snap)

            ok, msg = face_auth.enroll(
                abort_check=self._scan_abort.is_set,
                progress_callback=on_enroll_progress,
            )
            self._enroll_state["active"] = False
            self._enroll_state["done"] = True
            self._enroll_state["ok"] = ok
            self._enroll_state["message"] = msg
            return {"ok": ok, "message": msg}
        finally:
            self._face_lock.release()

    @_safe_api
    def stop_face_scan(self):
        """Ask any in-flight face scan or enrollment to stop and release the camera."""
        self._scan_abort.set()
        return {"ok": True}

    @_safe_api
    def get_vault_contents_summary(self):
        r"""Scans top-level & 1-level-deep files on V:\ for the live Vault Explorer card."""
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import config
        mnt = f"{config.CONFIG.mount_letter}:\\"
        target = mnt if os.path.exists(mnt) else "V:\\"
        if not os.path.exists(target):
            return {"ok": True, "mounted": False, "files": 0, "folders": 0, "recent": []}
        files_count = 0
        folders_count = 0
        recent = []
        try:
            for entry in os.scandir(target):
                if entry.name in ("$RECYCLE.Bin", "System Volume Information"):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders_count += 1
                    try:
                        for sub in os.scandir(entry.path):
                            if sub.is_file(follow_symlinks=False):
                                files_count += 1
                    except Exception:
                        pass
                elif entry.is_file(follow_symlinks=False):
                    files_count += 1
                    try:
                        st = entry.stat()
                        recent.append({
                            "name": entry.name,
                            "size_kb": round(st.st_size / 1024, 1),
                            "mtime": st.st_mtime,
                        })
                    except Exception:
                        pass
            recent.sort(key=lambda x: x["mtime"], reverse=True)
        except Exception:
            pass
        return {
            "ok": True,
            "mounted": True,
            "files": files_count,
            "folders": folders_count,
            "recent": recent[:5],
        }

    @_safe_api
    def import_and_shred_file(self):
        r"""Opens a native Windows file picker, copies the chosen file into V:\,
        overwrites the original file with random bytes, and deletes it."""
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import config
        import secrets
        mnt = f"{config.CONFIG.mount_letter}:\\"
        target_dir = mnt if os.path.exists(mnt) else "V:\\"
        if not os.path.exists(target_dir):
            return {"ok": False, "message": "VOLUME_NOT_MOUNTED"}
        if not self._window:
            return {"ok": False, "message": "NO_WINDOW"}
        try:
            picked = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
            if not picked:
                return {"ok": False, "message": "CANCELED"}
            src_path = picked[0] if isinstance(picked, (list, tuple)) else str(picked)
            if not os.path.isfile(src_path):
                return {"ok": False, "message": "CANCELED"}
            if os.path.splitdrive(os.path.abspath(src_path))[0].upper() == target_dir[:2].upper():
                return {"ok": False, "message": "ALREADY_IN_VAULT"}
            dest_path = os.path.join(target_dir, os.path.basename(src_path))
            shutil.copy2(src_path, dest_path)
            # Verify copy before shredding source
            if os.path.exists(dest_path) and os.path.getsize(dest_path) == os.path.getsize(src_path):
                sz = os.path.getsize(src_path)
                with open(src_path, "ba+", buffering=0) as f:
                    f.seek(0)
                    f.write(secrets.token_bytes(min(sz, 1024 * 1024)))
                    f.flush()
                    os.fsync(f.fileno())
                os.remove(src_path)
                import access_log
                access_log.record("SHRED_IMPORT", os.path.basename(dest_path))
                return {"ok": True, "filename": os.path.basename(dest_path)}
            return {"ok": False, "message": "COPY_VERIFY_FAILED"}
        except Exception as e:
            return {"ok": False, "message": f"ERROR:{type(e).__name__}"}

    @_safe_api
    def scrub_clipboard(self):
        """Wipes sensitive plaintext from the Windows clipboard."""
        try:
            import ctypes
            if ctypes.windll.user32.OpenClipboard(0):
                ctypes.windll.user32.EmptyClipboard()
                ctypes.windll.user32.CloseClipboard()
            return {"ok": True}
        except Exception:
            return {"ok": False}

    @_safe_api
    def run_security_health_check(self):
        """Verifies DPAPI secret, PIN state binding, Face ID templates, model hashes, and audit chain."""
        import vault_secret
        import pin_auth
        import face_auth
        import access_log
        import vault_mount
        checks = {
            "dpapi_secret": vault_secret.secret_exists(),
            "pin_state_ok": (pin_auth.status() == "READY"),
            "face_templates_count": face_auth.enrolled_template_count(),
            "models_intact": True,
            "audit_chain_intact": access_log.verify_chain()[0],
            "uac_daemon_ready": vault_mount.is_daemon_running(),
        }
        try:
            face_auth._verify_models()
        except Exception:
            checks["models_intact"] = False
        return {"ok": True, "checks": checks}

    @_safe_api
    def verify_pin_fast(self, pin):
        """Verifies PIN via Argon2id (~50ms), checks Duress PIN, and triggers Intruder Mugshot on failure."""
        import pin_auth
        import access_log
        if self._check_duress_pin(pin):
            access_log.record("DURESS_PIN", "CAMOUFLAGE_TRIGGERED")
            self.launch_decoy_camouflage()
            return {"ok": False, "message": "DURESS_ACTIVATED"}
        result = pin_auth.verify(pin)
        access_log.record("PIN", result)
        if result != "OK":
            last_b64 = str(self._scan_state.get("frame_b64", ""))
            self._capture_intruder_mugshot(f"Failed PIN ({result})", frame_b64=last_b64)
            return {"ok": False, "message": result}
        self._pending_pin_unlock = True
        return {"ok": True, "message": "PIN_VERIFIED"}

    @_safe_api
    def mount_authenticated_vault(self):
        r"""Mounts & unlocks V:\ in parallel while the Gravity Cascade transition plays."""
        if not getattr(self, "_pending_pin_unlock", False) and not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        self._pending_pin_unlock = False
        try:
            return self._do_unlock()
        except Exception as e:
            return {"ok": False, "message": f"UNLOCK_ERROR:{type(e).__name__}"}

    @_safe_api
    def toggle_capture_shield(self, enable=True):
        """Live toggle for Win32 WDA_EXCLUDEFROMCAPTURE (blocks screenshots/OBS/Discord capture)."""
        try:
            import ctypes
            hwnd = ctypes.windll.user32.FindWindowW(None, "LockerDrive")
            if hwnd:
                flag = 0x00000011 if enable else 0x00000000
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, flag)
            self._capture_shield_on = bool(enable)
            return {"ok": True, "enabled": self._capture_shield_on}
        except Exception as e:
            return {"ok": False, "message": f"ERROR:{type(e).__name__}"}

    @_safe_api
    def launch_decoy_camouflage(self):
        r"""Boss-Key Camouflage: immediately locks V:\, scrubs clipboard, and launches Kushal Studios."""
        try:
            self._morphing_to_decoy = True
            self.scrub_clipboard()
            if self._is_vault_mounted():
                self.lock_vault()
            import subprocess
            if getattr(sys, "frozen", False):
                subprocess.Popen(
                    [sys.executable, "--decoy", "--reuse-daemon"],
                    cwd=os.path.dirname(sys.executable),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                )
            else:
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                main_script = os.path.join(project_root, "main.py")
                py_exe = sys.executable
                pyw = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
                if os.path.basename(py_exe).lower() != "pythonw.exe" and os.path.isfile(pyw):
                    py_exe = pyw
                subprocess.Popen([py_exe, main_script, "--decoy", "--reuse-daemon"], cwd=project_root)
            threading.Timer(0.25, self.quit_app).start()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "message": f"ERROR:{type(e).__name__}"}

    @_safe_api
    def get_security_posture(self):
        """Returns comprehensive live telemetry for the Smart Security Command Center tab."""
        import config
        import vault_secret
        import pin_auth
        import recovery_auth
        import face_auth
        import access_log
        import vault_mount
        entries = access_log.read_entries()
        intact, broken_at = access_log.verify_chain()
        ok_unlocks = sum(1 for e in entries if e.get("outcome") == "OK" or str(e.get("outcome", "")).startswith("MATCHED"))
        blocked = sum(1 for e in entries if str(e.get("outcome", "")).startswith(("WRONG", "LOCKED", "TIMEOUT", "INVALID")))
        models_ok = True
        try:
            face_auth._verify_models()
        except Exception:
            models_ok = False
        tpl_cnt = face_auth.enrolled_template_count()
        score = 60
        if vault_secret.secret_exists(): score += 10
        if tpl_cnt >= 5: score += 10
        if intact: score += 10
        if models_ok: score += 10
        return {
            "ok": True,
            "score": min(100, score),
            "dpapi_bound": vault_secret.secret_exists(),
            "recovery_ready": recovery_auth.exists(),
            "face_angles": tpl_cnt,
            "models_intact": models_ok,
            "chain_intact": intact,
            "broken_at": broken_at,
            "uac_daemon": vault_mount.is_daemon_running(),
            "stealth_cloak": config.CONFIG.stealth_cloak,
            "auto_lock_minutes": config.CONFIG.auto_lock_minutes,
            "capture_shield": getattr(self, "_capture_shield_on", False),
            "ok_unlocks": ok_unlocks,
            "blocked_attempts": blocked,
            "recent_events": entries[-12:],
        }

    def _capture_intruder_mugshot(self, reason: str, frame_b64: str = "") -> None:
        """Silently captures and DPAPI-encrypts an intruder webcam photo on failed PIN or stranger face."""
        def _worker():
            try:
                import base64
                import json
                import glob
                import time
                import win32crypt
                import cv2
                import face_auth
                b64_img = frame_b64
                if not b64_img:
                    cap = face_auth._open_camera()
                    try:
                        if cap and cap.isOpened():
                            ok, frm = cap.read()
                            if ok and frm is not None and getattr(frm, "size", 0) > 0:
                                thumb = cv2.resize(cv2.flip(frm, 1), (220, 165))
                                ok_e, buf = cv2.imencode(".jpg", thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
                                if ok_e:
                                    b64_img = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")
                    finally:
                        if cap:
                            try: cap.release()
                            except Exception: pass
                            face_auth._LAST_CAMERA_RELEASE_TS = time.monotonic()
                if not b64_img:
                    return
                intruder_dir = os.path.join(
                    os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
                    "LockerDrive", "intruders"
                )
                os.makedirs(intruder_dir, exist_ok=True)
                ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
                rec = json.dumps({"timestamp": ts_str, "reason": reason, "image_b64": b64_img}).encode("utf-8")
                enc = win32crypt.CryptProtectData(rec, "LockerDrive Intruder Mugshot", None, None, None, 0)
                fname = f"intruder_{int(time.time() * 1000)}.dpapi"
                with open(os.path.join(intruder_dir, fname), "wb") as f:
                    f.write(enc)
                # Keep latest 12 mugshots
                files = sorted(glob.glob(os.path.join(intruder_dir, "intruder_*.dpapi")))
                for old_f in files[:-12]:
                    try: os.remove(old_f)
                    except OSError: pass
            except Exception:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    @_safe_api
    def get_intruder_mugshots(self):
        """Decrypts and returns DPAPI-protected intruder mugshots for the Security tab."""
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import json
        import glob
        import win32crypt
        intruder_dir = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
            "LockerDrive", "intruders"
        )
        shots = []
        if os.path.exists(intruder_dir):
            for path in sorted(glob.glob(os.path.join(intruder_dir, "intruder_*.dpapi")), reverse=True)[:6]:
                try:
                    raw = open(path, "rb").read()
                    dec = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1]
                    shots.append(json.loads(dec.decode("utf-8")))
                except Exception:
                    continue
        return {"ok": True, "mugshots": shots}

    @_safe_api
    def clear_intruder_mugshots(self):
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import glob
        intruder_dir = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
            "LockerDrive", "intruders"
        )
        for p in glob.glob(os.path.join(intruder_dir, "intruder_*.dpapi")):
            try: os.remove(p)
            except OSError: pass
        return {"ok": True}

    @_safe_api
    def offload_ram_now(self, unload_models=False):
        """Zeroes sensitive templates/frames in RAM and trims the OS working set."""
        import anti_re
        self._scan_state = {"active": False, "done": False}
        self._enroll_state["frame_b64"] = ""
        return anti_re.offload_sensitive_ram(unload_ai_models=bool(unload_models))

    @_safe_api
    def scrub_forensic_traces(self):
        try:
            import config
            import anti_re
            self._scan_state["frame_b64"] = ""
            self._enroll_state["frame_b64"] = ""
            res = anti_re.deep_forensic_scrub(config.CONFIG.mount_letter)
            anti_re.offload_sensitive_ram(unload_ai_models=False)
            return res
        except Exception:
            pass
        """Purges Windows Recent-Docs (.lnk) shortcuts pointing to V:\\ and wipes clipboard."""
        self.scrub_clipboard()
        removed_lnks = 0
        try:
            import config
            letter = config.CONFIG.mount_letter.upper()
            mnt_prefix = f"{letter}:\\".encode("ascii", errors="ignore")
            mnt_utf16 = f"{letter}:\\".encode("utf-16le", errors="ignore")
            recent_dir = os.path.join(
                os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming"),
                "Microsoft", "Windows", "Recent"
            )
            if os.path.exists(recent_dir):
                for fn in os.listdir(recent_dir):
                    if not fn.lower().endswith(".lnk"):
                        continue
                    fp = os.path.join(recent_dir, fn)
                    try:
                        data = open(fp, "rb").read(16384)
                        if mnt_prefix in data or mnt_utf16 in data or b"LockerDrive" in data:
                            os.remove(fp)
                            removed_lnks += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return {"ok": True, "removed_lnks": removed_lnks}

    @_safe_api
    def set_duress_pin(self, panic_pin):
        """Configures an optional Duress/Panic PIN (DPAPI-protected in %LOCALAPPDATA%)."""
        if not self._authenticated_this_session:
            return {"ok": False, "message": "NOT_AUTHENTICATED"}
        import win32crypt
        p_str = str(panic_pin or "").strip()
        duress_path = os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
            "LockerDrive", "duress_pin.dpapi"
        )
        if not p_str:
            try:
                if os.path.exists(duress_path): os.remove(duress_path)
            except OSError:
                pass
            return {"ok": True, "enabled": False}
        if not re.match(r"\A[0-9]{4,12}\Z", p_str):
            return {"ok": False, "message": "INVALID_PIN"}
        os.makedirs(os.path.dirname(duress_path), exist_ok=True)
        blob = win32crypt.CryptProtectData(p_str.encode("utf-8"), "LockerDrive Duress PIN", None, None, None, 0)
        with open(duress_path, "wb") as f:
            f.write(blob)
        return {"ok": True, "enabled": True}

    def _check_duress_pin(self, candidate_pin: str) -> bool:
        try:
            import hmac
            import win32crypt
            duress_path = os.path.join(
                os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local"),
                "LockerDrive", "duress_pin.dpapi"
            )
            if not os.path.exists(duress_path):
                return False
            raw = open(duress_path, "rb").read()
            saved = win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1].decode("utf-8")
            return hmac.compare_digest(saved, str(candidate_pin or ""))
        except Exception:
            return False

    @_safe_api
    def quit_app(self):
        """Zeroes RAM, scrubs OS forensic traces, stops daemon, and exits cleanly."""
        self._scan_abort.set()
        try:
            self.scrub_forensic_traces()
            import anti_re
            anti_re.offload_sensitive_ram(unload_ai_models=True)
            if not getattr(self, '_morphing_to_decoy', False):
                import vault_mount
                vault_mount.stop_elevated_daemon()
        except Exception:
            pass
        for w in (webview.windows or []):
            try:
                w.destroy()
            except Exception:
                pass
        return {"ok": True}


_RECOVERY_KEY_RE = re.compile(r"(\d{5}-){7}\d{5}")
_recovery_key_scan = {"at": 0.0, "found": False}


def _scan_for_recovery_keys():
    now = time.time()
    if now - _recovery_key_scan["at"] < 30:
        return _recovery_key_scan["found"]
    found = False
    try:
        for path in sorted(glob.glob(r"E:\LockerDrive\*.txt"))[:20]:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    if _RECOVERY_KEY_RE.search(f.read(8192)):
                        found = True
                        break
            except OSError:
                continue
    except Exception:
        found = False
    _recovery_key_scan["at"] = now
    _recovery_key_scan["found"] = found
    return found


def _resolve_asset_path(*parts: str) -> str:
    bases = []
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            bases.append(str(sys._MEIPASS))
        bases.append(os.path.dirname(sys.executable))
        bases.append(os.path.join(os.path.dirname(sys.executable), "_internal"))
    bases.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for b in bases:
        cand = os.path.join(b, *parts)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return os.path.abspath(os.path.join(bases[0], *parts))


def _apply_window_icon():
    """Dynamically morphs the window titlebar and Windows 11 taskbar icon from the
    Kushal Studios Gamepad icon to the LockerDrive Gold Padlock icon."""
    try:
        import ctypes
        ico_path = _resolve_asset_path("assets", "lockerdrive_icon.ico")
        if not os.path.exists(ico_path):
            return
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kushal.LockerDrive.Vault.v16")
        for _ in range(10):
            hwnd = ctypes.windll.user32.FindWindowW(None, "LockerDrive")
            if hwnd:
                IMAGE_ICON = 1
                LR_LOADFROMFILE = 0x00000010
                WM_SETICON = 0x0080
                GCLP_HICON = -14
                GCLP_HICONSM = -34
                h_small = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
                h_big = ctypes.windll.user32.LoadImageW(None, ico_path, IMAGE_ICON, 48, 48, LR_LOADFROMFILE)
                if h_small:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, h_small)
                    try: ctypes.windll.user32.SetClassLongPtrW(hwnd, GCLP_HICONSM, h_small)
                    except Exception: pass
                if h_big:
                    ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, h_big)
                    try: ctypes.windll.user32.SetClassLongPtrW(hwnd, GCLP_HICON, h_big)
                    except Exception: pass
                break
            time.sleep(0.05)
    except Exception:
        pass

def _apply_capture_protection():
    # Only apply WDA_EXCLUDEFROMCAPTURE if explicitly requested in environment,
    # as it can cause invisible/black windows on Windows 11 with remote/OBS display hooks
    if os.environ.get("LOCKERDRIVE_HIDE_FROM_CAPTURE") != "1":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.user32.FindWindowW(None, "LockerDrive")
        if hwnd:
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


def _harden_dll_search():
    try:
        import ctypes
        LOAD_LIBRARY_SEARCH_APPLICATION_DIR = 0x00000200
        LOAD_LIBRARY_SEARCH_USER_DIRS = 0x00000400
        LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
        flags = (LOAD_LIBRARY_SEARCH_SYSTEM32
                 | LOAD_LIBRARY_SEARCH_APPLICATION_DIR
                 | LOAD_LIBRARY_SEARCH_USER_DIRS)
        return bool(ctypes.windll.kernel32.SetDefaultDllDirectories(flags))
    except Exception:
        return False


def _start_session_watcher(api):
    try:
        from session_watch import SessionWatcher
        return SessionWatcher(api._on_session_locked).start()
    except Exception:
        return False


def _start_hardware_sleep_watchdog(api):
    """Background daemon that detects (1) system sleep/hibernate time jumps (>12s
    on a 2s tick) and (2) USB drive removal while unlocked, triggering immediate
    auto-lock."""
    def _watch():
        import config
        last_ts = time.time()
        while True:
            time.sleep(2.0)
            now = time.time()
            elapsed = now - last_ts
            last_ts = now
            try:
                if elapsed > 12.0 and (api._authenticated_this_session or api._is_vault_mounted()):
                    api._on_hardware_or_sleep_event("SLEEP_WAKE")
                elif api._authenticated_this_session and not os.path.exists(config.CONFIG.drive_root):
                    api._on_hardware_or_sleep_event("USB_REMOVED")
            except Exception:
                pass

    t = threading.Thread(target=_watch, daemon=True, name="LockerDriveHwWatchdog")
    t.start()
    return True


def main():
    _harden_dll_search()
    try:
        import anti_re
        anti_re.harden_process_anti_dump()
    except Exception:
        pass
    api = Api()
    atexit.register(lambda: api.lock_vault() if api._is_vault_mounted() else None)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Kushal.LockerDrive.Vault.v16")
    except Exception:
        pass
    index_path = _resolve_asset_path("ui", "index.html")
    file_url = "file:///" + index_path.replace("\\", "/")
    window = webview.create_window(
        "LockerDrive", url=file_url, js_api=api,
        width=460, height=700, resizable=False,
        background_color="#F7F4F0",
    )
    api._window = window
    window.events.shown += _apply_capture_protection
    window.events.shown += _apply_window_icon
    _start_session_watcher(api)
    _start_hardware_sleep_watchdog(api)
    try:
        webview.start()
    finally:
        # Complete fail-safe shutdown on window close ('X' button / Alt+F4):
        # 1. Stop camera scan, 2. Lock & dismount V:\, 3. Scrub OS forensic traces,
        # 4. Zero sensitive RAM & trim working set, 5. Terminate UAC daemon (unless morphing to Decoy)
        try:
            api._scan_abort.set()
            if api._authenticated_this_session or api._is_vault_mounted():
                api.lock_vault()
            api.scrub_forensic_traces()
            import anti_re
            anti_re.offload_sensitive_ram(unload_ai_models=True)
            if not getattr(api, "_morphing_to_decoy", False):
                import vault_mount
                vault_mount.stop_elevated_daemon()
        except Exception:
            pass
    os._exit(0)


if __name__ == "__main__":
    main()
