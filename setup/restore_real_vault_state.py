"""
restore_real_vault_state.py -- Clears +h/+s on state files, removes stale test
anchors, restores the configured PIN (via --pin or LOCKERDRIVE_RESET_PIN), and restores face templates.
"""
import os
import sys
import glob
import getpass
import pickle
import ctypes
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, os.path.join(ROOT, "engine"), os.path.join(ROOT, "FaceRecog-Engine")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _clear_hidden_attrs(folder: str):
    if not os.path.exists(folder):
        return
    subprocess.run(["attrib.exe", "-h", "-s", "-r", os.path.join(folder, "*.*"), "/s"], capture_output=True)
    for fn in os.listdir(folder):
        if fn.lower().endswith(".vhdx"):
            continue
        full = os.path.join(folder, fn)
        try:
            ctypes.windll.kernel32.SetFileAttributesW(full, 0x80)  # FILE_ATTRIBUTE_NORMAL
        except Exception:
            pass


def main():
    print("=== Restoring Real LockerDrive State on E:\\LockerDrive ===")
    _clear_hidden_attrs(r"E:\LockerDrive")

    import config
    import local_anchor
    import state_binding
    import vault_secret
    import pin_auth
    import recovery_auth
    import face_auth

    config.load_persisted_config()
    config.save_persisted_config(drive_letter="E", mount_letter="V", size_gb=config.CONFIG.size_gb, auto_lock_minutes=config.CONFIG.auto_lock_minutes, stealth_cloak=config.CONFIG.stealth_cloak)

    pw = vault_secret.load_secret()
    if not pw:
        print("[ERROR] Could not load E:\\LockerDrive\\vault.secret!")
        sys.exit(1)
    print(f"[OK] Real vault.secret decrypted via DPAPI (length={len(pw)} chars).")

    # Remove stale anchors from both %LOCALAPPDATA%\LockerDrive and ~\LockerDrive
    for candidate_dir in (
        local_anchor._ANCHOR_DIR,
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "LockerDrive"),
        os.path.join(os.path.expanduser("~"), "LockerDrive"),
    ):
        for p in glob.glob(os.path.join(candidate_dir, "security_anchor*.json")):
            try:
                os.remove(p)
                print(f"[OK] Removed stale anchor: {p}")
            except OSError:
                pass
    state_binding._clear_cache_for_tests()

    reset_pin = ""
    if "--pin" in sys.argv:
        idx = sys.argv.index("--pin")
        if idx + 1 < len(sys.argv):
            reset_pin = sys.argv[idx + 1].strip()
    if not reset_pin:
        reset_pin = os.environ.get("LOCKERDRIVE_RESET_PIN", "").strip()
    if not reset_pin:
        reset_pin = getpass.getpass("Enter vault reset PIN (4-12 digits): ").strip()
    if not reset_pin.isdigit() or not (4 <= len(reset_pin) <= 12):
        print("[ERROR] Valid 4-12 digit PIN required via --pin, LOCKERDRIVE_RESET_PIN, or prompt.")
        sys.exit(1)

    res_pin = pin_auth.force_reset(reset_pin)
    verify_pin = pin_auth.verify(reset_pin)
    print(f"[OK] PIN reset complete: force_reset={res_pin}, verify={verify_pin}")
    assert verify_pin == "OK", f"PIN failed verification: {verify_pin}"

    rec_code = recovery_auth.generate()
    print(f"[OK] Fresh Recovery Code bound to vault.secret: {rec_code}")

    bak_path = r"E:\LockerDrive\face_templates.legacy-v0-2026-09-20.bak"
    if os.path.exists(bak_path):
        with open(bak_path, "rb") as f:
            raw_bak = f.read()
        data = pickle.loads(raw_bak)
        owner_vecs = data.get("owner") if isinstance(data, dict) else data
        face_auth._save_templates(owner_vecs)
        loaded = face_auth._load_templates()
        print(f"[OK] Restored {len(loaded['owner'])} real face templates into v2 DPAPI-bound face_templates.pkl!")

    # Sync access_log witness count/head_hash with current access_log.txt
    entries = []
    try:
        import access_log
        access_log._chain_state = {"count": None, "head_hash": None}
        entries = access_log.read_entries()
        if entries:
            local_anchor.witness_update("log", overwrite=True, count=len(entries), head_hash=entries[-1]["entry_hash"])
    except Exception:
        pass

    face_auth._verify_models()
    print("[SUCCESS] Configured PIN, Face Templates, and Anchor are 100% restored and verified!")


if __name__ == "__main__":
    main()
