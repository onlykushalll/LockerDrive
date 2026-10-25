# 🔒 LockerDrive v11.0

> **Production-Grade Biometric BitLocker Vault & Decoy Arcade Suite**
> Apple macOS Sequoia & iOS 18 HIG interface with 60 FPS Gravity-Cascade motion choreography, 3D Volumetric Fibonacci Geodesic `ThinkingOrb`, 9-Angle 360° TrueDepth Face ID (`FaceRecog-Engine v2.0`), 12-layer passive anti-spoofing (`rPPG` + `MiDaS 3D Depth` + `MiniFASNetV2`), Argon2id + DPAPI + HMAC state binding, Upfront Zero-Prompt UAC Daemon, and the **Kushal Studios v3.0** Decoy Arcade Hub.

---

## 🏛️ Repository Architecture

```text
LockerDrive/
├── main.py                              # Unified entrypoint (--gui, --decoy, --enroll-face, --start-daemon)
├── Kushal Studios.bat                   # Launcher for Kushal Studios Decoy Hub (Upfront UAC + Gamepad Icon)
├── Run_LockerDrive.bat                  # Direct launcher for LockerDrive Vault UI (Upfront UAC + Padlock Icon)
├── launch_game.py                       # Windowless subprocess launcher for the 9 Pygame arcade titles
├── assets/
│   ├── lockerdrive_icon.ico / .png      # Custom Two-Tone Gold Padlock application icon
│   ├── kushal_studios_icon.ico / .png   # Custom SNES Gamepad icon for Kushal Studios
│   └── sfx/                             # Synthesized retro WAV sound effects for the 9 games
├── engine/                              # Core Cryptographic, Biometric & Volume Subsystem
│   ├── config.py                        # Dynamic multi-drive, storage & auto-lock persistence
│   ├── local_anchor.py                  # Vault-scoped C:\\ HMAC security witness & model hash pinning
│   ├── state_binding.py                 # Argon2id-derived purpose-separated HMAC-SHA256 bindings
│   ├── vault_secret.py                  # Windows DPAPI wrapper for the BitLocker volume password
│   ├── pin_auth.py                      # Argon2id PIN hashing, v2 DPAPI envelope & escalating lockout
│   ├── recovery_auth.py                 # 96-bit emergency recovery key subsystem with generation witness
│   ├── face_auth.py                     # 9-Angle 360° burst enrollment, live TrueDepth mesh & adaptive centroid
│   ├── vault_mount.py                   # DPAPI-authenticated IPC bridge for Upfront UAC Daemon
│   ├── _elevated_helper.py              # Persistent Administrator daemon (VHDX mount, unlock, lock, live NTFS resize)
│   ├── access_log.py                    # Hash-chained + keyed-MAC tamper-evident audit log
│   ├── session_watch.py                 # Win32 WTS session-lock watcher (instant lock on Win+L)
│   └── ai_regex.py                      # Offline regex AI engine with '010410' one-shot & 2-step vault triggers
├── ui/                                  # User Interfaces
│   ├── app.py                           # PyWebView API bridge, async mount, Shred-Import & Security Command Center
│   ├── index.html                       # Apple Sequoia HIG v11.0 UI (Gravity Cascade Drop + Ethereal Defade + 3D Orb)
│   ├── decoy_app.py                     # Kushal Studios v3.0 Pastel Arcade & AI Hub
│   ├── appdata.py                       # Mtime-cached atomic JSON persistence for game scores & settings
│   └── games/__init__.py                # 9 playable 60 FPS Pygame mini-games
├── FaceRecog-Engine/                    # Standalone v2.0 12-Layer Passive Liveness & 3D Biometric SDK
└── setup/                               # Provisioning, Upgraders, Sync Scripts & 222-Check Verification Suite
    ├── run_all_tests.py                 # Master Verification Suite (177 core security + 45 v3..v11 checks)
    ├── sync_facerecog_to_utils_repo.ps1 # Automated Git sync to https://github.com/onlykushalll/Utils (~FaceRecog-Engine~)
    ├── restore_real_vault_state.py      # Restores real PIN ('0104'), face templates & clean security anchor
    ├── test_hardening.py                # 61 cryptographic & state-binding checks
    ├── test_redteam_fixes.py            # 113 adversarial red-team checks
    └── test_session_watch.py            # 3 Win32 session & DLL hardening checks
```

---

## 🚀 Quick Start & Commands

1. **Launch Kushal Studios (Decoy Arcade + AI Chat):**
   * Double-click **`Kushal Studios.bat`** (approves UAC once upfront).
   * In **Studio AI**, type **`010410`**, **`open vault : 010410`**, **`open the vault : 010410`**, or **`open the vault`** (and enter **`010410`** when prompted) to transition into **LockerDrive Vault** with zero extra UAC prompts.

2. **Launch LockerDrive Vault Directly:**
   * Double-click **`Run_LockerDrive.bat`**.

3. **Run All Security & Biometric Tests:**
   ```powershell
   python setup\run_all_tests.py
   ```

4. **Sync Upgraded `FaceRecog-Engine` to `onlykushalll/Utils` on GitHub:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup\sync_facerecog_to_utils_repo.ps1
   ```
