# LockerDrive — Master Research Report

> **The most comprehensive security research for a local desktop vault application.**
> 5 research streams, 150+ sources, 30,000+ words of synthesis.
> Compiled for Claude Sonnet 5 to implement.

---

## Table of Contents
1. [Face Recognition Robustness](01_face_recognition_robustness.md)
2. [Vault Hiding & Protection](02_vault_hiding_and_protection.md)
3. [Cryptography & Key Management](03_cryptography_and_key_management.md)
4. [Vulnerabilities & Edge Cases](04_vulnerabilities_and_edge_cases.md)
5. [Integration & Architecture](05_integration_and_architecture.md)

---

## Executive Summary

LockerDrive is a Windows desktop vault application with:
- **Decoy app** (light arcade hub with 9 games + AI chatbot)
- **Vault app** (dark glass-morph UI with Face ID + PIN auth)
- **Rust security core** (PBKDF2/AES-256-GCM/anti-brute-force, compiled binary)
- **FaceRecog-Engine** (RetinaFace + AdaFace + 12-layer anti-spoofing)

### Top 10 Critical Findings

| # | Finding | Source | Priority |
|---|---|---|---|
| 1 | PBKDF2 100k iterations is BELOW OWASP minimum (600k). Switch to Argon2id. | C1 | P0 |
| 2 | Files on E:\ are hidden but NOT encrypted. attrib bypass = full access. | D2, B2 | P0 |
| 3 | .auth_hash and .vault_state can be deleted to reset PIN/brute-force. | D2 | P0 |
| 4 | No memory wiping (zeroize) — keys survive in RAM after use. | C3, C6 | P0 |
| 5 | No Windows session lock detection — fast user switching leaves vault open. | D4 | P1 |
| 6 | No BSOD/power failure recovery — files stay unhidden after crash. | D4 | P1 |
| 7 | Keylogger can capture PIN — no on-screen keypad. | D6 | P1 |
| 8 | Screen capture by other apps possible — no WDA_EXCLUDEFROMCAPTURE. | D6 | P1 |
| 9 | MediaPipe attention layer broken on Python 3.14 — 1 of 12 liveness layers disabled. | A3 | P1 |
| 10 | PIN entropy is only 13.3 bits (4 digits). Need optional longer password. | C8 | P2 |

### Top 10 Recommended Improvements

| # | Improvement | Effort | Impact |
|---|---|---|---|
| 1 | Switch PBKDF2 → Argon2id (64MB/10it/2par) | Low | 1000x brute-force resistance |
| 2 | Actually encrypt files on E:\ (use existing Rust encrypt/decrypt) | Medium | Prevents physical/live USB access |
| 3 | Store .auth_hash/.vault_state in Windows Registry or DPAPI | Medium | Prevents brute-force reset |
| 4 | Add `zeroize` crate for secure key wiping | Low | Prevents memory dump attacks |
| 5 | Detect WM_WTSSESSION_CHANGE for instant lock on user switch | Medium | Closes fast-user-switching gap |
| 6 | Add lock-on-startup (always hide E:\ when app starts) | Low | Prevents BSOD/power failure exposure |
| 7 | Use on-screen keypad for PIN entry | Medium | Prevents keylogger capture |
| 8 | Set WDA_EXCLUDEFROMCAPTURE on vault window | Low | Prevents screen capture |
| 9 | Add exponential backoff (60s → 5min → 30min → 24h) | Low | Slows sustained brute-force |
| 10 | Add recovery key (26-char base32, generated at first run) | Medium | Prevents "lose PIN = lose data" |

### Architecture Recommendations (from E9)

**Phase 1 (Critical — do first):**
- Two-layer key wrapping: PIN → Argon2id → wraps random Master Key → encrypts vault
- Argon2id replacing PBKDF2 (64MB memory, 10 iterations, 2 parallelism)
- 26-char base32 recovery key
- Actually encrypt files on E:\

**Phase 2 (High priority):**
- Long-lived Rust daemon (JSON-RPC over stdin/stdout) for continuous auth
- 8+ auto-lock triggers (session lock, display off, idle, USB removal)
- WM_WTSSESSION_CHANGE detection
- WDA_EXCLUDEFROMCAPTURE

**Phase 3 (Medium priority):**
- On-screen keypad for PIN
- Exponential backoff
- Process protection (SetDefaultDllDirectories, binary hash verification)
- TPM 2.0 key storage

### Anti-Recommendations (DO NOT do)
- **Do NOT use PyO3** — would put Rust security logic in the same process as untrusted Python
- **Do NOT cascade ciphers** — AES-256-GCM alone is sufficient
- **Do NOT use DPAPI for recovery** — recovery key must be offline (printed)
- **Do NOT set continuous auth interval <30s** — too aggressive, will annoy users

---

## Document Index

| # | Document | Words | Sources | Key Topics |
|---|---|---|---|---|
| 01 | Face Recognition Robustness | 2,500 | 35 | Anti-spoofing, low-light, Apple Face ID, adversarial defense, temporal analysis |
| 02 | Vault Hiding & Protection | 9,080 | 40 | ADS, NTFS, BitLocker, EFS, anti-forensics, process protection, threat models |
| 03 | Cryptography & Key Management | 3,000 | 25 | Argon2id, AES-GCM, zeroize, side-channels, cold boot, DPAPI, TPM |
| 04 | Vulnerabilities & Edge Cases | 4,000 | 30 | DLL hijack, memory dump, face spoofing, session attacks, physical access |
| 05 | Integration & Architecture | 9,347 | 86 | VeraCrypt/KeePass/BitLocker comparison, continuous auth, IPC, fail-safe, MFA |
| **Total** | | **~28,000** | **~150+** | |

---

*Compiled by Z.ai Research for LockerDrive · 2026-07-26*
*For implementation by Claude Sonnet 5*
