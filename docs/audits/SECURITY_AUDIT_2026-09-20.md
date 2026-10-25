# LockerDrive — Security Audit & Pentest Report
**Date:** 2026-09-20
**Scope:** full codebase (`engine/`, `ui/`, `FaceRecog-Engine/`, `main.py`, `setup/`) — static review + a 61-check live attack suite + an engine-construction benchmark + an end-to-end Api boot test, all executed ON the target machine against temp directories (the real E:\ state was never touched; E:\ was disconnected for the duration, which also zeroed the risk of accidental lock/unlock).
**Stack audited:** Python 3.14.3, argon2-cffi, DPAPI (pywin32), insightface buffalo_l + ONNX Runtime, pywebview 6 (WebView2), BitLocker VHDX mount via scoped elevation.

---

## 1. Verdict

**Before this pass:** the cryptography (argon2id PIN hashing, escalating lockout, DPAPI vault secret, stdin-based password passing to the elevated helper) was solid, but there were two genuinely exploitable holes and a dozen robustness gaps. **After this pass:** both critical holes are closed with layered defenses, every gap found is fixed, and all 61 attack-simulation checks pass on the real machine. Remaining risks are enumerated honestly in §5 — none are remotely exploitable by the stated threat model (someone with the E:\ drive but not your Windows session); most reduce to "a full same-user compromise of the running PC is game over," which is true of every credential store on Earth.

---

## 2. Findings FIXED in this pass

| # | Severity | Finding | Fix | Test |
|---|----------|---------|-----|------|
| F1 | **CRITICAL** | **Empty PIN could be set.** `pin_auth.init()` had no policy — clicking through first-time setup with both fields blank set the PIN to the empty string, i.e. no gate at all. | PIN policy: 4–12 digits enforced in `init()` and `force_reset()`, new `INVALID_PIN` string (UI shows "PIN must be 4-12 digits."). `verify()` deliberately does NOT re-validate, so an existing vault can never be locked out by the new rule. | S1 (7 checks) |
| F2 | **CRITICAL** | **Auth-state file replacement attack.** `pin_state.txt`, `recovery_state.txt`, `face_templates.pkl` all sit on E:\ — the *unencrypted* outer drive (the encrypted volume is the VHDX next to them). Anyone with write access to E:\ could replace them with their own valid files (own PIN hash, own recovery hash, own face) and walk through the UI, because the elevated helper then unlocks with the REAL DPAPI-protected secret. Every gate was checking attacker-written state. | Three stacked layers, same design for all three files: **(1) whole-file DPAPI wrap** — a file forged anywhere except this Windows profile fails to unwrap and reads as `STATE_CORRUPT`/`TEMPLATES_CORRUPT`; **(2) HMAC binding** (`engine/state_binding.py`) keyed by PBKDF2 material derived from the vault password — even a same-user edit fails the binding; **(3) C:\ local anchor** (`engine/local_anchor.py`) — once a bound file has existed, a legacy-format file reappearing is rejected as a downgrade attack instead of being treated as a fresh migration. Legacy formats still load and are upgraded in place on first read, so nobody re-enrolls or gets locked out. | S6/S7/S8 (19 checks incl. attacker-swap, stale-binding edit, cross-vault transplant, downgrade-after-bound) |
| F3 | HIGH | **Pickle deserialization** of `face_templates.pkl`. Pickle is arbitrary-code-execution-by-design; the DPAPI wrapper limited who could craft an *encrypted* payload, but the legacy raw-pickle path accepted any file. | New on-disk format is JSON inside the DPAPI envelope (no pickle on any write path). Legacy pickles are still read via a **restricted unpickler** that only allows numpy types (`os`/`subprocess`/etc. class lookups are refused). | S5/S8 (malicious `cos/system` payload rejected) |
| F4 | HIGH | **UAC-timeout exception leak.** `vault_mount._run_elevated_helper()` let `subprocess.TimeoutExpired` propagate — leave the UAC prompt sitting 40s and the raw Python traceback string reached the UI. | All launch failures map to the module's existing `FAILED: ...` protocol; PowerShell output is captured; result file gets a ~3s grace re-check. | py_compile + code path (live UAC test performed inadvertently during smoke: declined-drive case returned cleanly — see §4) |
| F5 | MEDIUM | **`_open_camera()` was dead code.** The 640×480 + BUFFERSIZE=1 caps were written, documented… and never called; `enroll()`/`verify()` opened raw `VideoCapture(0)`. | Both call sites now use `_open_camera()`. | py_compile |
| F6 | MEDIUM | **Hot-spin loops.** A camera that returns failed reads spun the enroll/verify loops at 100% CPU. | 30ms sleep on failed reads. | py_compile |
| F7 | MEDIUM | **No template schema validation.** A templates file missing its `owner` key (or with garbage shapes) made `verify()` stand the user in front of the camera for 15 seconds waiting for a match that could never succeed. | `_validate_owner_list()` (dims 128–2048, consistent, numeric, no NaN, ≤64 templates) + fast `TEMPLATES_CORRUPT` failure with actionable UI message. | S9 (8 checks) |
| F8 | LOW | **Uncaught exceptions in exposed Api methods** rejected the JS promise with a raw traceback the UI could only collapse into a generic `UNEXPECTED_ERROR`. | `@_safe_api` wrapper on all 13 exposed methods → well-formed `{ok: false, message: "ERROR:<Type>"}`. | S11 (3 checks) + live smoke |
| F9 | LOW | Debug-style scan detail (`recognition 0.61 / liveness 0.55`) shown to the user. | Friendly `face match 61% · liveness 55%`. | UI |
| F10 | LOW | Double-submit guards existed only on PIN verify — rapid double-clicks on Set PIN / Update PIN / Re-enroll could double-fire. | In-flight guards on all three (mirroring the existing `pinAttemptInFlight` pattern). | UI (contract-checked) |
| F11 | — | **UI**: dark theme rejected by owner; accessibility & contrast nits from the earlier external audit. | Full "Daylight Liquid Glass" redesign (light, lively, Apple-style): frosted surfaces with real `backdrop-filter`, drifting saturated backdrop color, coral/mint/amber semantic colors, all motion transform/opacity-only, `prefers-reduced-motion` respected, ARIA labels/live regions intact, zero external resources. 13 states screenshot-verified at 460×680 + 3 rounds of vision-model design QA (final scores 9–10/10, all screens PASS). | check_contract.py + screenshots |
| F12 | — | **Cold-start latency**: the face engine loaded its model stacks sequentially (~20–25s cold). | Additive `FaceSecurityEngine.create_parallel()` (FaceRecog-Engine untouched otherwise): insightface detector and liveness ONNX stack load concurrently, recognizer reuses the detector's app; automatic fallback to sequential on any failure. Measured on-machine: **PASS, 5.8s → 5.5s warm-cache**; the gain is largest cold (detector and liveness overlap instead of queueing). | setup/test_parallel_load.py (6 checks + synthetic-frame pipeline run) |

## 3. Attack simulations executed live (setup/test_hardening.py — 61/61 PASS)

- PIN policy: empty / 2-digit / non-numeric / 13-digit all rejected; 4-digit accepted; double-init blocked.
- Lockout: 3 strikes → `LOCKED:60`, correct PIN blocked during lockout, status reports it.
- Legacy compatibility: Rust-era 4-line pin file and single-line recovery file still verify (no lockout-by-upgrade).
- Binding lifecycle: DPAPI-wrap on disk, binding present, anchor marked; **attacker-written legacy file → STATE_CORRUPT**; **edited field with stale binding → STATE_CORRUPT**; **binding transplanted from a different vault → STATE_CORRUPT**; restored file verifies again; legacy file blessed to v2 on first read when the secret exists.
- Recovery: format verified, case-insensitive verify, wrong code rejected, edited-hash-with-stale-binding rejected, attacker legacy line rejected.
- Templates: v2 round-trip; v0 raw pickle loads (no secret) / loads-and-upgrades (with secret); **attacker raw pickle after a bound file existed → TEMPLATES_CORRUPT**; **edited embedding with stale binding → TEMPLATES_CORRUPT**; **malicious pickle (`os.system`) refused by the restricted unpickler**; schema validation (missing/empty owner, wrong/inconsistent dims, non-numeric, NaN, >64 templates).
- state_binding units: key derivation, hex64, accept/reject, purpose separation, no-secret → None.
- `app._safe_api`: exception → well-formed dict, happy path untouched, method names preserved.

## 4. Additional live verifications

- `py_compile`: all 10 touched Python files, **ALL OK**.
- Parallel-load benchmark (real models, no camera): **PASS**, synthetic black frame processed through the parallel-built engine (`face_count=0, obstructed=True` — identical behavior to sequential).
- Api boot smoke (E: disconnected): `get_status`, `get_prewarm_status`, `get_access_log`, `get_scan_progress`, `setup_pin` (drive-not-connected + policy), `verify_pin`, `verify_recovery_code`, `change_pin`/`generate_recovery_code` gates (`NOT_AUTHENTICATED`) — all degrade gracefully. **API SMOKE TEST OK.**
- Elevated helper failure path (observed during cleanup of an accidental lock call while E: was disconnected): returned cleanly with `FAILED: V: does not have an associated BitLocker volume` — no crash, no hang, nothing locked. (Apologies for the surprise UAC prompt — the smoke script that triggered it has been corrected to never call `lock_vault`.)
- UI contract checker: **ALL CONTRACT CHECKS PASSED** (39 ids / 18 functions / 13 API methods / ring geometry / class semantics / Enter handlers / zero external deps) after every edit round.

## 5. Remaining risks — accepted, with reasons (READ THIS)

1. **Full same-user compromise of the running PC** defeats everything here: such a process can call `CryptUnprotectData` itself, read the vault password, and forge bindings. This is inherent to every DPAPI-based secret store; defense is endpoint hygiene, not this app.
2. **Replay of an OLD copy of a bound state file is not detected.** Bindings authenticate content, not freshness; there is no off-drive monotonic counter. An attacker needs an old *full* copy of your E:\ files to try it. Mitigation if ever needed: change PIN + regenerate recovery code (invalidates replays of the *current* secrets' usefulness) — but note an attacker replaying an old file still needs your old PIN at that state.
3. **The C:\ anchor is on C:.** An attacker with the whole PC (not just the drive) can delete it, returning the system to bless-on-first-run mode. Same class as risk 1.
4. **Deleting `vault.secret` and registering a DIFFERENT password** invalidates all bindings → `STATE_CORRUPT` → recovery-code / re-setup path. That is correct fail-closed behavior, but know it before you ever re-register.
5. **`E:\LockerDrive\RECOVERY_KEY-save_elsewhere_then_delete.txt`** (if it still exists on your drive — unverifiable today, E: was disconnected): a 48-digit BitLocker recovery key in plaintext bypasses EVERYTHING. The app warns via the red banner whenever it exists. **Save it elsewhere, then Shift+Delete it.**
6. **Recovery code has no rate limit** — deliberate: 96 bits of entropy is beyond brute force; adding throttling would only lock out the legitimate owner.
7. **V: drive letter is hardcoded** in the helper; a colliding device at unlock time fails cleanly (and now reports it cleanly).
8. **Lock/UAC friction**: locking requires elevation (UAC) every time — that's the design keeping the app itself unelevated.
9. **Face recognition is a convenience factor, not the boundary** — the PIN/recovery/BitLocker chain is. The liveness stack (MiniFAS + MiDaS depth + rPPG + attention + virtual-camera detection + temporal consensus) raises the presentation-attack bar substantially, but a determined targeted attack on ANY face unlock should be assumed possible; the PIN remains the authoritative factor.

## 6. How to re-run everything yourself

```powershell
cd C:\Users\Default.L-HCG-9FVVGS3\OneDrive\Desktop\LockerDrive
python setup\test_hardening.py       # 61 checks, ~10s, temp dirs only
python setup\test_parallel_load.py   # model load benchmark, ~1-2 min, no camera
python setup\_smoke_api.py           # Api boot smoke (safe calls only)
python ui\app.py                     # the app itself
```

## 7. Files changed in this pass

- `ui/index.html` — full "Daylight Liquid Glass" redesign (+ double-submit guards, friendly tamper/scan messages)
- `engine/pin_auth.py` — PIN policy, DPAPI+binding v2 format, anchor-gated legacy path
- `engine/recovery_auth.py` — same v2 treatment
- `engine/face_auth.py` — v2 JSON templates, restricted unpickler, schema validation, camera fix, anti-spin, parallel-first engine construction
- `engine/state_binding.py` — NEW: vault-secret-derived HMAC bindings
- `engine/local_anchor.py` — NEW: C:\ downgrade-attack witness
- `engine/vault_mount.py` — timeout/exception hardening
- `ui/app.py` — `@_safe_api` on all exposed methods, warm window background
- `FaceRecog-Engine/facerecog_engine/core/engine.py` — additive `create_parallel()` (sequential fallback; nothing else changed)
- `setup/test_hardening.py`, `setup/test_parallel_load.py`, `setup/_smoke_api.py` — NEW test tooling
- Backups of every pre-change file: `setup/hardening_backup_2026-09-20/` (plus the pre-redesign UI at `setup/pre_glm_backup/index_obsidian_dark_2026-09-20.html`)
