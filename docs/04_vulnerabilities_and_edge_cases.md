# 04. Vulnerabilities & Edge Cases — Red Team Analysis

> Synthesized from 15 targeted web searches across security research, CWE database, and practical attack guides. 30+ unique sources.

## D1. Process-Level Attacks

### DLL Hijacking (CWE-427)
**Severity: HIGH**
- **Attack:** Attacker places a malicious DLL (e.g., `cv2.dll`, `customtkinter.dll`) in the LockerDrive directory. When Python loads, it picks up the malicious DLL.
- **Current mitigation:** None.
- **Recommended mitigation:** 
  - Sign the vault_core.exe with a code signing certificate
  - Set DLL search path to system directories only (`SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32)`)
  - Use `AddDllDirectory()` for specific paths

### Python Memory Dumping
**Severity: HIGH**
- **Attack:** Attacher uses `procdump` or `Process Hacker` to dump the Python process memory. The PIN string and face encoding are in plaintext in memory.
- **Current mitigation:** PIN is passed via stdin to Rust (not stored in Python variables long-term). But the `pin_entry.get()` return value is a Python string in memory.
- **Recommended mitigation:**
  - Clear the PIN entry field immediately after reading: `pin = self.pin_entry.get(); self.pin_entry.delete(0, "end")` — already done ✅
  - Use `ctypes.memmove` to overwrite the Python string buffer after use
  - Keep PIN lifetime as short as possible (pass to Rust immediately)

### Process Hollowing
**Severity: MEDIUM**
- **Attack:** Attacker creates a suspended Python process, injects malicious code, then resumes. The injected code can intercept subprocess calls to vault_core.exe.
- **Current mitigation:** None.
- **Recommended mitigation:** Verify the vault_core.exe hash before each call. Store the expected SHA-256 hash at build time and check at runtime.

### Subprocess Hook
**Severity: MEDIUM**
- **Attack:** Attacker hooks `subprocess.Popen` or `subprocess.run` in Python to intercept the PIN before it reaches Rust.
- **Current mitigation:** PIN is passed via stdin (not command line), making it harder to intercept.
- **Recommended mitigation:** Verify `subprocess` module integrity at startup (check `subprocess.__file__` hasn't been replaced).

## D2. File System Attacks

### Bypass attrib +h +s
**Severity: CRITICAL (by design)**
- **Attack:** Enable "Show hidden files" in File Explorer or use `Get-ChildItem -Force` / `dir /a`
- **Current mitigation:** AES-256-GCM encrypted container (if data is encrypted, showing the file doesn't help)
- **Gap:** The vault currently uses attrib hiding but does NOT always encrypt the actual files on E:\. The encrypt/decrypt commands exist in Rust but are not called by the UI.
- **Recommended mitigation:** 
  - Actually use the encrypt/decrypt Rust commands to create an AES container
  - OR switch to VeraCrypt-style container mounting

### Delete .vault_state to Reset Brute-Force
**Severity: HIGH**
- **Attack:** Attacker deletes `.vault_state` file → brute-force counter resets to 0
- **Current mitigation:** State file is hidden with attrib +h +s and XOR-obfuscated
- **Recommended mitigation:** 
  - Store state in Windows Registry (HKCU\Software\LockerDrive) — harder to find and delete
  - OR bind state to DPAPI (tied to Windows user account)
  - OR store state inside the encrypted container

### Delete .auth_hash to Reset PIN
**Severity: CRITICAL**
- **Attack:** Attacker deletes `.auth_hash` → vault_core re-initializes with default PIN "0104"
- **Current mitigation:** Hash file is hidden with attrib +h +s
- **Recommended mitigation:**
  - Store hash in Windows Registry (HKCU\Software\LockerDrive\AuthHash)
  - OR bind to DPAPI
  - OR store inside an encrypted container on E:\

## D3. Camera/Face Recognition Attacks

### Photo Attack (Level 1)
**Severity: MEDIUM (mitigated)**
- **Attack:** Hold up a printed photo of the enrolled user
- **Current mitigation:** MiniFASNet ONNX antispoof + LBP texture analysis + specular reflection check
- **Assessment:** Well defended. MiniFASNet has >95% accuracy on print attacks.

### Video Replay Attack (Level 2)
**Severity: MEDIUM (mitigated)**
- **Attack:** Play a video of the enrolled user on a phone screen
- **Current mitigation:** Moiré pattern detection (FFT) + temporal blink detection + histogram consistency
- **Assessment:** Well defended for phone screen attacks. Moiré patterns are detectable on LCD screens.

### Virtual Camera Injection
**Severity: HIGH**
- **Attack:** Use OBS Studio virtual camera to feed a video of the enrolled user
- **Current mitigation:** FaceSecurityEngine.has `is_virtual_camera()` check — catches OBS virtual cameras
- **Gap:** Need to verify this check actually works on the current system
- **Recommended mitigation:** Test virtual camera detection with OBS. Add fallback: if camera resolution/format is suspicious (e.g., 1920x1080 MJPEG from a "webcam" that should be 640x480), reject.

### Deepfake Real-Time
**Severity: HIGH (emerging)**
- **Attack:** Use real-time deepfake software (e.g., DeepFaceLab) to impersonate the enrolled user via webcam
- **Current mitigation:** rPPG (heartbeat detection) — deepfakes don't have consistent blood flow patterns. Temporal consensus (3 frames) — deepfakes may have micro-inconsistencies.
- **Assessment:** rPPG is the strongest defense. However, rPPG requires good lighting and high-quality camera. On a $10 webcam in low light, rPPG may not work.
- **Recommended mitigation:** If rPPG SNR is too low, require PIN as second factor (multi-factor auth).

## D4. Session/OS-Level Attacks

### Fast User Switching
**Severity: HIGH**
- **Attack:** User unlocks vault, then another user switches to their account via Fast User Switching. Vault stays unlocked in the background.
- **Current mitigation:** Focus-loss auto-lock (60 seconds). But 60 seconds is a lot of time.
- **Recommended mitigation:** 
  - Detect Windows session change events (WM_WTSSESSION_CHANGE) and lock immediately
  - Reduce auto-lock to 15 seconds for focus loss

### Remote Desktop (RDP)
**Severity: MEDIUM**
- **Attack:** While vault is unlocked locally, someone RDPs into the PC and accesses the unlocked vault.
- **Current mitigation:** Focus-loss auto-lock (RDP may not trigger focus loss on the local session)
- **Recommended mitigation:** Lock vault when RDP session starts (detect via WTS API)

### Safe Mode
**Severity: MEDIUM**
- **Attack:** Boot into Safe Mode → app doesn't start → E:\ is still hidden but attrib can be bypassed
- **Current mitigation:** None (Safe Mode bypasses startup programs)
- **Recommended mitigation:** This is where AES encryption matters. If files are encrypted, safe mode doesn't help the attacker.

### Blue Screen / Power Failure
**Severity: HIGH**
- **Attack:** BSOD or power failure while vault is unlocked → files remain unhidden on next boot
- **Current mitigation:** None. The heartbeat timer doesn't run during BSOD.
- **Recommended mitigation:** 
  - Add a "lock on startup" flag: when the app starts, always hide E:\ first
  - Use a Windows service (runs before user login) to re-hide E:\ on boot
  - OR: use encrypted container (files are always encrypted, unhide doesn't matter)

## D5. Timing/Race Condition Attacks

### TOCTOU: is_locked_out() vs unlock()
**Severity: MEDIUM (mitigated)**
- **Attack:** Call is_locked_out() (returns False), then quickly delete .vault_state, then call unlock() (resets counter)
- **Current mitigation:** unlock() internally checks lockout state in a single Rust call (no TOCTOU gap)
- **Assessment:** The Rust binary handles lockout check + PIN verification + file unhiding in a single atomic operation. ✅

### Window Between Unhide and Explorer Launch
**Severity: LOW**
- **Attack:** Kill the process between `unhide_vault()` and `open_explorer()` — files are unhidden but no explorer window appears
- **Current mitigation:** Both operations happen in the Rust binary in sequence (sub-millisecond gap)
- **Assessment:** Acceptable risk. The window is <1ms.

## D6. UI/GUI Attacks

### PyAutoGUI Automation
**Severity: MEDIUM**
- **Attack:** Use PyAutoGUI to automate typing "0104" into the PIN field
- **Current mitigation:** Anti-brute-force (3 attempts → 60s lockout)
- **Assessment:** The rate limiter prevents automated brute-force. But 3 attempts per 60s = 120 PINs per hour. A 4-digit PIN has 10,000 combinations → 83 hours to brute-force.
- **Recommended mitigation:** Add exponential backoff (see C8). Also: detect rapid input (if 3 PINs entered in <5 seconds, lock for 5 minutes).

### Keylogger
**Severity: HIGH**
- **Attack:** Software keylogger captures PIN as it's typed
- **Current mitigation:** None
- **Recommended mitigation:** 
  - Use on-screen keypad for PIN entry (click, not type) — harder for keyloggers
  - OR: use Windows Hello PIN entry dialog (Windows protects against keyloggers)

### Screen Capture by Another App
**Severity: MEDIUM**
- **Attack:** Another app captures the screen while vault is unlocked, showing file contents
- **Current mitigation:** None
- **Recommended mitigation:** Set `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` on the vault window — prevents screen capture by other apps (Windows 10 2004+)

## D7. Physical Access Attacks

### USB Rubber Ducky
**Severity: HIGH**
- **Attack:** Plug in a USB Rubber Ducky that types the PIN at superhuman speed
- **Current mitigation:** Anti-brute-force rate limiting
- **Recommended mitigation:** Same as PyAutoGUI — exponential backoff + rapid input detection

### Boot from Linux Live USB
**Severity: CRITICAL (unmitigable in software)**
- **Attack:** Boot from Ubuntu Live USB → mount E:\ → read all files
- **Current mitigation:** None (software can't prevent this)
- **Recommended mitigation:** AES-256 encryption of the actual file contents. If files are encrypted, the Linux user sees only ciphertext. This is the ONLY defense against physical access.

### Remove Drive and Plug into Another PC
**Severity: CRITICAL (unmitigable without encryption)**
- **Attack:** Remove E:\ drive → plug into another PC → attrib -h -s → read files
- **Current mitigation:** None
- **Recommended mitigation:** AES-256 encryption. Files must be encrypted at rest, not just hidden.

## D8. Vulnerability Summary Table

| # | Vulnerability | Severity | Likelihood | Current Mitigation | Recommended Fix |
|---|---|---|---|---|---|
| 1 | DLL hijacking | HIGH | Low | None | SetDefaultDllDirectories |
| 2 | Python memory dump | HIGH | Medium | PIN cleared quickly | Overwrite Python string buffer |
| 3 | Delete .vault_state | HIGH | Medium | Hidden + obfuscated | Store in Registry or DPAPI |
| 4 | Delete .auth_hash → default PIN | CRITICAL | Medium | Hidden | Store in Registry or DPAPI |
| 5 | attrib bypass (show hidden) | CRITICAL | High | AES container exists but unused | Actually encrypt files on E:\ |
| 6 | Virtual camera injection | HIGH | Medium | is_virtual_camera() check | Test + add resolution check |
| 7 | Fast user switching | HIGH | Medium | 60s auto-lock | WM_WTSSESSION_CHANGE detection |
| 8 | BSOD/power failure | HIGH | Low | None | Lock-on-startup flag |
| 9 | Keylogger | HIGH | Medium | None | On-screen keypad |
| 10 | Screen capture | MEDIUM | Low | None | WDA_EXCLUDEFROMCAPTURE |
| 11 | PyAutoGUI brute-force | MEDIUM | Medium | 3/60s rate limit | Exponential backoff |
| 12 | Linux Live USB boot | CRITICAL | Low | None | AES-256 file encryption |
| 13 | Remove drive | CRITICAL | Medium | None | AES-256 file encryption |
| 14 | Process hollowing | MEDIUM | Low | None | Binary hash verification |

## D9. Concrete Recommendations (Priority Order)

### P0 — Critical (implement immediately)
1. **Actually encrypt files on E:\** — the Rust encrypt/decrypt commands exist but the UI doesn't call them. Files must be encrypted at rest, not just hidden.
2. **Protect .auth_hash and .vault_state** — store in Windows Registry or DPAPI-bound file. Prevents brute-force reset.
3. **Add lock-on-startup** — always hide E:\ when the app starts, regardless of previous state.

### P1 — High (implement soon)
4. **Detect Windows session lock (WM_WTSSESSION_CHANGE)** — lock vault immediately when user switches or locks.
5. **Add exponential backoff** — 60s → 5min → 30min after repeated lockouts.
6. **Set WDA_EXCLUDEFROMCAPTURE** — prevent screen capture of the vault window.
7. **Use on-screen keypad for PIN** — prevent keylogger capture.

### P2 — Medium (implement when possible)
8. **Add SetDefaultDllDirectories** — prevent DLL hijacking.
9. **Verify vault_core.exe hash** — detect process hollowing/tampering.
10. **Add rapid-input detection** — if 3 PINs in <5 seconds, lock for 5 minutes.
