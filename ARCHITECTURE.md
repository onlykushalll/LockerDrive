# LockerDrive — System & Cryptographic Architecture

> **Proprietary IP Asset** — Copyright (c) 2026 Kushal (`@onlykushalll`). All Rights Reserved.  
> Technical due diligence & acquisition inquiries: `creatormanager27@gmail.com`

---

## 1. Dual-Persona Runtime Model

1. **Decoy Surface (`ui/decoy_app.py` + `ui/games/` + `engine/ai_regex.py`):** Launches cleanly by default as *Kushal Studios v3.0* with 9 playable 60 FPS Pygame mini-games and an offline regex conversational UI. Vault activation uses HMAC-SHA256 trigger commitments (`_TRIG_SALT` + `_PRIMARY_COMMITMENT`) so static binary/strings inspection exposes no plaintext trigger keyword.
2. **Upfront UAC Elevation (`engine/vault_mount.py` + `engine/_elevated_helper.py`):** Approves Administrator elevation once at startup and runs a persistent privileged helper communicating over DPAPI-encrypted IPC tokens inside `%LOCALAPPDATA%\LockerDrive\ipc`, allowing instant mount/lock transitions without mid-session UAC prompts.
3. **BitLocker Encrypted Storage (`XTS-AES-256` VHDX):** Data resides in an encrypted virtual disk image (`lockerdrive.vhdx`) configured via environment variables (`LOCKERDRIVE_*`) or local `%LOCALAPPDATA%\LockerDrive\vault_config.json`.

## 2. Authentication & Anti-Tamper Binding Hierarchy

- **Vault Secret (`engine/vault_secret.py`):** Wrapped via Windows `CryptProtectData` (`DPAPI`) per user SID.
- **State Binding (`engine/state_binding.py`):** Derives purpose-separated HMAC-SHA256 keys from the DPAPI-protected vault secret (`pin`, `recovery`, `face`, `log`, `anchor`).
- **PIN Subsystem (`engine/pin_auth.py`):** Argon2id memory-hard hashing inside v2 DPAPI envelopes with escalating lockouts and strict integer/float fuzz guards.
- **Downgrade Witness (`engine/local_anchor.py`):** Stores an off-drive HMAC witness on `C:` (`%LOCALAPPDATA%\LockerDrive`) using `RLock` concurrency synchronization to block offline rollback of older state files or unblessing attacks.
- **Hash-Chained Audit Log (`engine/access_log.py`):** Pipe/newline-sanitized append log verified by forward-linked SHA-256 chain hashes and keyed MACs.
- **Session Watchdog (`engine/session_watch.py`):** Subscribes to `WM_WTSSESSION_CHANGE` (`Win+L` / RDP disconnect) to auto-lock mounted volumes immediately.

## 3. Biometric Pipeline (`FaceRecog-Engine v2.0`)

- **4-Way Parallel Loader (`create_parallel`):** Loads RetinaFace (`det_10g`), ArcFace (`w600k_r50`), MiniFASNetV2, and MiDaS depth ONNX graphs concurrently.
- **9-Angle 360° Enrollment (`BiometricEnroller`):** Combines Yaw/Pitch/Roll pose estimation, Laplacian sharpness gating, RANSAC cosine-median outlier pruning, and adaptive centroid refinement.
- **12-Layer Passive Anti-Spoofing (PAD):** Fuses neural spoof classification, monocular 3D depth curvature, CHROM rPPG heart-rate FFT (`0.75–3.2 Hz`), 5-point gaze/saccade kinematics, FFT screen Moiré, LBP micro-texture, dual-colorspace skin correlation, specular glare, phone bezel contour detection, temporal blink/parallax checks, perceptual `dHash` replay rings, and virtual-camera hardware validation (`VirtualCameraDetector`).

## 4. Non-Destructive Test Suite

`python setup/run_all_tests.py` runs bytecode checks, static snippet checks, and **177 core security checks + 45 live/fuzz/concurrency checks** (`test_hardening.py`, `test_redteam_fixes.py`, `test_session_watch.py`) exclusively inside temporary sandboxes — never mutating real user drives, anchors, or cameras.
