# Subagent r-E Synthesis — Integration & Architecture Research for LockerDrive

**Agent:** Research Subagent E
**Task ID:** r-E
**Mission:** Research the best architecture and integration patterns for combining all of LockerDrive's security layers (face recognition, cryptography, file hiding, process protection) into a single robust application. Also research how real-world vault apps are architected, and synthesize concrete recommendations.

**Method:** 30 targeted web searches across 8 research areas, plus close reading of the existing LockerDrive source (`rust_core/src/main.rs`, `engine/vault_core.py`, `README.md`). Results stored under `/home/z/my-project/research/E_extra/e1_*.json` through `e30_*.json`.

**Context snapshot of LockerDrive today:**
- Python (CustomTkinter) decoy UI → 9 games + AI chat → secret trigger morphs UI to a dark "Liquid Glass" vault
- Auth: face recognition (FaceRecog-Engine: RetinaFace + AdaFace + 12-layer anti-spoofing) OR PIN `0104`
- Rust binary `vault_core.exe` (compiled, called via subprocess `subprocess.run([_RUST_BINARY, args...], input=pin)`)
- Crypto: PBKDF2-SHA256 100k iter, 16-byte salt → AES-256-GCM (12-byte nonce, random per encryption)
- Anti-brute-force: 3 failed PINs → 60s lockout, persisted to disk as XOR-obfuscated file (key `"KSLD"`)
- File hiding: Windows `attrib +h +s` on `E:\` and `E:\.lockerdrive_data.bin`
- Auto-lock: 60-second focus-loss timer; F12 panic key
- Camera safety: 15s scan timeout + 15s cooldown

---

## E1. Real-World Vault App Architectures (VeraCrypt, KeePass, BitLocker, 1Password)

The four canonical vault apps we should learn from differ in two important axes: (a) what their *trust anchor* is — a password, a TPM chip, a per-device secret, or a hardware key — and (b) what they are protecting — a mounted volume, an in-memory database, a full disk, or a remote sync vault. LockerDrive sits closest to VeraCrypt (file/volume on a single Windows machine) but is small enough to borrow ideas from all four.

### VeraCrypt — open-source disk/file encryption (successor to TrueCrypt)

**Architecture (from veracrypt.io/en/Encryption%20Scheme.html and veracrypt.io/en/Key%20Derivation%20Algorithms.html):**
- The user supplies a password (+ optional keyfiles). A KDF (PBKDF2-HMAC-Whirlpool, PBKDF2-HMAC-SHA-512, or **Argon2id** since version 1.26) derives a header key from the password and a 64-byte salt stored in the 64 KiB volume header.
- The header key decrypts the volume header, which contains the actual master encryption keys.
- The master keys encrypt/decrypt the data area in **XTS-AES** (or cascades like Serpent-Twofish-AES, each cipher with its own 256-bit key derived from the same Argon2id output split into 192 bytes).
- Volume mounting requires a kernel-mode driver (Windows: `veracrypt.sys`) or a user-mode FUSE-like layer (Dokany on Windows, FUSE on Linux/macOS). Once mounted, the volume appears as a regular drive letter and OS file calls transparently route through the driver, which decrypts on the fly.
- Plausible deniability: hidden volumes inside the outer volume; the outer password decrypts only the decoy header.

**Lessons for LockerDrive:**
1. **Header-then-data two-layer keying.** VeraCrypt never uses the password-derived key to encrypt the bulk data directly. The password decrypts a small header that contains random per-volume master keys. LockerDrive currently uses `derive_key(pin, salt) → AES-256-GCM key` directly — meaning every re-encryption with the same PIN reuses the same derived key (only the salt/nonce change). Migrating to a two-layer scheme (PIN decrypts a 32-byte master key, master key encrypts files) means rotating the PIN doesn't require re-encrypting the whole vault — only re-wrapping the master key.
2. **Argon2id, not PBKDF2.** VeraCrypt moved to Argon2id in 2024 (veracrypt.io GitHub issue #1752). OWASP's 2023 Password Storage Cheat Sheet ranks Argon2id as the default, with PBKDF2 (600k iterations) acceptable as a fallback. Argon2id is memory-hard (KeePass recommends 64–128 MB) and resists GPU/ASIC attacks far better than PBKDF2.
3. **Cascading ciphers are snake oil for our threat model.** VeraCrypt's cascade modes (AES-Twofish-Serpent) make brute force 2–3× harder but cost 3× CPU. For LockerDrive — where the attacker is a casual snoop, not a state actor — single AES-256-GCM with hardware AES-NI acceleration is more than sufficient and 3× faster.
4. **Mounting via Dokany** is the "right" way to do transparent file encryption on Windows, but it requires installing a signed kernel driver. That's a heavy lift for a hobbyist project; `attrib +h +s` is the pragmatic alternative (see E4 for the trade-off).

### KeePass — open-source password manager (the local vault reference)

**Architecture (from keepass.info/help/base/security.html and community discussions):**
- Master password (+ optional key file and Windows account binding via DPAPI) is fed to **Argon2id** (KeePass recommends memory ≥ 64 MB, iterations ≥ 10, parallelism 2+) — or the older AES-KDF (variable iterations, no memory hardness).
- The KDF output is the master key that decrypts the `.kdbx` database file using AES-256-CBC with HMAC-SHA-256 (or ChaCha20-Poly1305 in KDBX 4.1).
- The database file is a small (KB–MB) container. Once unlocked, the entire plaintext database lives in the process's virtual memory.
- **Memory protection:** KeePass uses Windows `VirtualAlloc` + `VirtualLock` to keep the master key in non-paged RAM, and **zeroizes** it on lock. The master password itself is also kept in a `SecureString`-like buffer and wiped after KDF.
- **CVE-2023-32784 (the famous "traceable leftover strings" bug):** a memory dump of the KeePass process leaked the master password character-by-character through `SecureTextBoxEx` managed-string leftovers. Patched in 2.54.

**Lessons for LockerDrive:**
1. **The master password must never live in a Python `str`.** Python strings are immutable, interned, and cannot be reliably zeroized (see E3, memory zeroization). The current `vault_core.py` reads the PIN as a regular `str` and passes it via `subprocess.run(input=pin + "\n")`. The PIN lives in: (a) the CustomTkinter entry field, (b) the Python str object, (c) the `subprocess` pipe buffer, (d) the Rust binary's `String` buffer. Each is a leak point.
2. **VirtualLock + zeroize.** In Rust, use the `zeroize` crate (`Zeroize` trait on `Zeroizing<String>`) plus `winapi::um::memoryapi::VirtualLock` to keep the PIN buffer non-pageable. In Python, accept that the moment the PIN leaves the entry widget it is effectively leaked; mitigate by (a) extracting the PIN into a `bytearray` (mutable), (b) overwriting with zeros after use, and (c) calling `gc.collect()` before any lock operation.
3. **Argon2id parameters to copy from KeePass:** memory = 64 MB, iterations = 10, parallelism = 2. On a typical laptop this gives ~600ms per unlock — acceptable UX, brutal for an offline brute-forcer.
4. **A small encrypted container is better than encrypting individual files.** KeePass decrypts the entire vault into RAM at unlock and re-encrypts on save. LockerDrive's current `encrypt/decrypt` commands work this way for metadata; we should extend the pattern: vault contents live as one `.vault.bin` container (or a small set of file blobs) rather than relying on `attrib` hiding.

### BitLocker — full-disk encryption built into Windows Pro+

**Architecture (from learn.microsoft.com/windows/security/.../bitlocker and itm4n.github.io/tpm-based-bitlocker):**
- A random **Volume Master Key (VMK)** is generated when BitLocker is enabled. The VMK is what actually decrypts the data area.
- The VMK is wrapped (encrypted) by one or more **protectors**: TPM-only, TPM+PIN, recovery password (48 digits), AD/Entra backup, USB startup key.
- The TPM "seals" the VMK against specific PCR (Platform Configuration Register) measurements — only the same boot chain can unseal it. The PIN adds an additional secret that even a stolen TPM cannot bypass.
- **TPM anti-hammering:** the TPM itself enforces a lockout after repeated wrong PINs (typically 32 attempts → TPM wipe). This is hardware-enforced and cannot be bypassed by malware.
- The 48-digit recovery password is generated at enable-time, can be saved to Microsoft account / AD / USB / printed, and is the only escape hatch if the TPM or PIN fails.

**Lessons for LockerDrive:**
1. **Multiple "protectors" instead of one secret.** BitLocker doesn't make you choose between a PIN or a recovery key — it uses *all of them* as alternative paths to unwrap the same VMK. LockerDrive should adopt the same pattern: the master key is wrapped separately by (a) a PIN-derived key, (b) a face-recognition-derived key (the face embedding serves as a "biometric protector"), and (c) a recovery key. Any one of them unlocks the vault. See E6.
2. **Hardware anti-hammering beats software.** LockerDrive's current 3-attempts → 60s lockout is trivially bypassable by editing the XOR-obfuscated state file (`KSLD` is a 4-byte key — not real crypto). TPM-protected keys get hardware anti-hammering for free. If the user's machine has a TPM (most Win10/11 do), use the TPM `Seal`/`Unseal` API to wrap the master key — wrong-PIN rate limiting becomes hardware-enforced.
3. **Recovery is non-negotiable.** BitLocker generates the recovery key automatically at enable-time and refuses to proceed until the user has saved it somewhere. LockerDrive has no recovery path — lose the PIN and the vault is gone. See E8.

### 1Password — zero-knowledge sync vault

**Architecture (from 1password.com/blog/the-architectural-reason-1password-cant-read-your-vault-data and support.1password.com/secret-key-security):**
- **Two-secret design.** Every account has both a Master Password (the user knows it) and a **Secret Key** (a 128-bit / 34-character code generated on first signup and stored on each device). The Master Password + Secret Key are combined into the input to a KDF (PBKDF2-SHA256, 100k iter historically; now Argon2id in 1Password 8+) to derive the Master Unlock Key.
- The Master Unlock Key encrypts the per-vault Item Key, which encrypts each item's content. This two-layer wrapping means rotating the Master Password only re-wraps the Master Unlock Key, not every item.
- The Secret Key is never sent to the server. If the server is breached, the attacker still needs the user's Master Password (which is also never sent — only a verifier derived from SRP is exchanged).
- Local 1Password clients cache the Secret Key in OS-level secure storage (Windows Credential Manager / macOS Keychain), so the user types only the Master Password on subsequent unlocks.

**Lessons for LockerDrive:**
1. **The two-secret design is the gold standard for "online vaults," but is overkill for LockerDrive.** LockerDrive is purely local; there is no server that could be breached to leak the salt and the verifier. The threat model is "someone gains physical access to my laptop." For that, a single strong PIN + biometric is sufficient.
2. **However:** the *pattern* of storing the secondary secret in OS secure storage is exactly what we want for the recovery key. Windows Credential Manager (via DPAPI) is the right place to store a recovery key backup, *encrypted against the user's Windows login*. If the user forgets their LockerDrive PIN, they can recover by signing into Windows — which proves they're at least the same Windows user, even if they can't prove they're the same LockerDrive user.
3. **Two-layer key wrapping** (PIN → Master Key → File Keys) is the architectural pattern that makes everything else — PIN rotation, multi-factor, recovery, continuous auth — clean. See E9 for the concrete proposal.

### Quick comparison table

| App | KDF | Cipher | Trust anchor | Recovery path | Continuous auth |
|---|---|---|---|---|---|
| VeraCrypt | Argon2id (1.26+) | AES-256-XTS / cascades | Password + keyfile | Plausible deniability only | None (manual dismount) |
| KeePass | Argon2id (KDBX 4) | AES-256-CBC+HMAC or ChaCha20-Poly1305 | Password + key file + Windows account | Key file | Manual lock; auto-lock after idle |
| BitLocker | (No KDF — VMK is random) | AES-128/256-XTS | TPM + PIN + recovery password | 48-digit recovery password | TPM re-seals on boot |
| 1Password | PBKDF2 → Argon2id | AES-256-GCM (per-item) | Master Password + Secret Key | Secret Key + Emergency Kit | App re-locks after idle |
| **LockerDrive (current)** | PBKDF2-SHA256 100k | AES-256-GCM | PIN or Face | **None** | 60s focus-loss timer |
| **LockerDrive (target, E9)** | Argon2id 64MB/10/2 | AES-256-GCM | PIN + Face (any-of) + Recovery Key | 32-char recovery key | 5-min re-verify face; lock on Win+L |

---

## E2. Continuous Authentication

### The argument for continuous auth

LockerDrive's current model is **one-shot authentication**: verify face once, leave vault unlocked until 60s of focus loss or F12. This is the standard model for password managers (KeePass, 1Password) but it has a known weakness — an attacker who waits 10 seconds after the user unlocks, then swaps into the chair, gets full access until the focus timer fires.

Continuous authentication (CA) is the academic term for "re-verify identity periodically during an open session." NIST SP 800-63B §7.2 (pages.nist.gov/800-63-3/sp800-63b.html) is the canonical reference:
> "Reauthentication of the subscriber SHALL be repeated following any period of inactivity lasting 30 minutes or longer. The session SHALL be terminated when the subscriber ceases to present the session token."

NIST's 30-minute ceiling is for *low-assurance* sessions (AAL1). For *higher-assurance* sessions the period should be shorter. Commscope's RADIUS guide (docs.commscope.com/.../Periodic-Reauthentication) recommends a reauth period **greater than 300 seconds** (5 minutes) for network access control — i.e., never reauth more often than every 5 minutes, or you'll hurt availability. Microblink's glossary entry distinguishes reauthentication (periodic identity checks while session continues) from session timeouts (full logout on inactivity) — they are complementary, not alternatives.

### What interval?

Synthesizing NIST + Commscope + Cisco's NAC guide:
- **30 minutes** is the upper bound (NIST). Anything longer is non-compliant.
- **5 minutes** is the practical floor for active reauth — shorter and you annoy the user.
- **15 minutes** is a common compromise for financial/healthcare apps.
- **Idle-time-based** (lock after 5 min idle, no reauth while active) is what most desktop apps do — KeePass, 1Password, BitLocker all use this.

For LockerDrive, since face recognition is camera-based and can run silently without user friction, we can do *true* continuous auth:
- **Active vault, user present:** re-verify face every **60–90 seconds**. The camera runs in the background; if the current face embedding still matches the enrolled one (cosine similarity ≥ threshold), the session continues silently.
- **Active vault, face not found (user stepped away):** start a 30-second grace timer. If face returns within 30s, continue. If not, lock.
- **Active vault, different face detected (someone else sat down):** lock **immediately** (<1s). Don't wait for grace period.
- **Vault idle (no keyboard/mouse to either the decoy or vault UI):** lock after 5 minutes. This is the KeePass pattern.

### Windows Hello as a reference

Windows Hello itself does *not* do continuous auth — once you unlock the device, you stay unlocked until the screen locks (idle timeout, manual Win+L, or screen saver). However, the underlying **Windows Biometric Framework** exposes a `Match` API that 1Password and other apps use to do per-action reauth (e.g., 1Password's "re-prompt with Windows Hello" feature).

The community 1Password thread (1password.community/...continuous-reprompt-windows-hello) shows that the user expectation is: "every time I do something sensitive, re-prompt." That's the model to copy. For LockerDrive that means:
- Opening the vault: face auth (current behavior).
- Switching to vault mode from decoy mode: face auth again (not just PIN).
- Each file-copy operation from the vault: background face re-verify (silent, no UI interrupt unless face doesn't match).

### What to do when reauth fails

Three failure modes, each with a distinct response:
1. **Face not detected for 30s+ (user stepped away):** lock the vault silently. UI shows "Locked — face not detected." Re-unlock requires a fresh face scan or PIN.
2. **Different face detected (someone else present):** lock **immediately** + emit a *distinct* event to the audit log (`LOCK_SPOOF`) so the user can later see "at 14:32, someone else was detected at my keyboard."
3. **Face engine throws an exception or camera is unplugged:** this is the tricky case. Options:
   - **Fail-safe (recommended):** lock immediately. Better to annoy the user than to leave the vault open with no biometric check.
   - **Fail-soft (NOT recommended):** continue without biometric. This violates the "default deny" principle (E4).

The continuous-auth thread should run in the *Rust* binary, not the Python UI — so that if the Python UI crashes, the camera watch process keeps running and can trigger an out-of-band lock (see E3, E4).

### Concrete recommendation

- Implement a background "watchdog" thread in the Rust binary that, while the vault is unlocked:
  - Polls the face engine every **60 seconds** for a fresh embedding.
  - If `cosine_sim(new, enrolled) < 0.5` for **2 consecutive polls**, lock.
  - If `no_face_detected` for **30 consecutive seconds**, lock.
  - On any face engine exception, lock immediately and log `LOCK_ENGINE_FAULT`.
- Disable continuous auth when the user is actively dragging/dropping files (the camera would catch the user's hand) — but only for 5s at a time.
- Make the interval configurable in settings, with 60s as default and 5min as the max allowed.

---

## E3. Secure Inter-Process Communication (Python ↔ Rust)

### The current state

`engine/vault_core.py` calls the Rust binary via:
```python
result = subprocess.run(
    [str(_RUST_BINARY)] + list(args),
    capture_output=True, text=True, timeout=30,
    input=stdin_data,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
```

The PIN is passed via stdin (good — not visible in `GetCommandLine()`), but every other command (`lock`, `status`, `hide`) is one-shot: a fresh `vault_core.exe` is spawned, does its work, and exits. There is no persistent IPC channel.

### Why this is *almost* OK

1. **Each call is short-lived.** The PIN lives in the child's stdin buffer only for the ~100ms the binary is alive. After exit, the OS reclaims the buffer.
2. **`CREATE_NO_WINDOW`** prevents a console flash (UX) and prevents `conhost.exe` from getting a copy of the command line.
3. **Subprocess isolation** means a Python crash doesn't take the Rust binary down with it (and vice versa). This is *good* for fail-safe design (E4).

### Why this is *not* OK for the next iteration

1. **No continuous-auth channel.** The current architecture can't support E2's "Rust polls face engine every 60s" because the Rust binary isn't running between calls. We need a long-lived Rust process.
2. **No way for Rust to push events to Python.** Lock-on-spoof needs to tell the Python UI "lock now, switch theme back to light" — but the current architecture only supports Python → Rust requests, not Rust → Python events.
3. **PIN leak through Python `str`.** As noted in E1, the PIN passes through Python's interned string pool. `subprocess.run(input=pin+"\n")` makes a copy in the pipe buffer; the original `pin` variable stays in Python's heap until garbage-collected (which may be never, if the intern table holds it).

### The options

**Option A — Keep subprocess, harden it (minimal change).**
- Use `bytes` instead of `str` for the PIN (avoids Python string interning).
- After `subprocess.run`, overwrite the input buffer explicitly: `del stdin_data; gc.collect()`.
- Pin the Rust binary's working set with `VirtualLock` and zeroize buffers.
- *Pros:* smallest possible diff. *Cons:* still no event channel; can't do continuous auth.

**Option B — Long-lived Rust daemon, JSON-over-stdin RPC (recommended).**
- The Python UI launches `vault_core.exe daemon` once at startup. The Rust process runs forever, reading JSON-RPC requests from stdin (`{"method":"unlock","pin":"...","id":42}`) and writing JSON responses to stdout (`{"id":42,"ok":true}`).
- For Rust → Python events (face-mismatch, lock-required), the daemon writes `{"event":"lock_required","reason":"face_mismatch"}` lines on stderr, which the Python UI reads on a separate thread.
- The PIN never touches disk and only lives in Rust's `Zeroizing<String>`.
- *Pros:* enables continuous auth, event push, single instance lock. *Cons:* more code; need to design the protocol.

**Option C — Named pipe with ACL (most secure, most work).**
- The Rust daemon creates a Windows named pipe `\\.\pipe\lockerdrive` with a security descriptor that grants access **only to the current user's SID** (SDDL: `D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;current-user-SID)`). Python connects as a client.
- Per learn.microsoft.com/win32/ipc/named-pipe-security-and-access-rights, the default named pipe security descriptor grants full control to LocalSystem, Administrators, and the creator/owner — which is too permissive. We need an explicit ACL restricting to the current user.
- Per the Synacktiv writeup (synacktiv.com/.../hooking-windows-named-pipes), named pipes can be hooked by any process that has `WRITE_DAC` on them; the mitigation is strict ACL + the `FILE_FLAG_FIRST_PIPE_INSTANCE` flag to prevent pipe squatting.
- *Pros:* truly secure against other users on the same machine. *Cons:* named pipe squatting (a malicious process pre-creates `\\.\pipe\lockerdrive` before us) is a real risk; mitigations are subtle (use a per-session random pipe name, or use `GetNamedPipeClientProcessId` to verify the connecting process).

**Option D — PyO3 native extension (in-process).**
- Build the Rust code as a Python extension module (`cargo build --release` → `vault_core.pyd`), import it directly in Python. No IPC at all.
- Per pyo3.rs and benfrederickson.com/writing-python-extensions-in-rust-using-pyo3, this is the recommended way for "performance-sensitive" Python/Rust integration.
- *Pros:* no IPC overhead, no PIN-in-pipe-buffer leak, simplest API (`import vault_core; vault_core.unlock(pin)`).
- *Cons:* **destroys the security boundary.** The current architecture's whole point is "even if someone decompiles the Python UI, the security logic is in compiled Rust machine code they can't easily read." PyO3 puts the Rust code in the same process as the Python interpreter — a memory dump of `python.exe` now reveals everything. Plus, a Python `sys.modules` injection can hook the calls.

### Recommendation: Option B (long-lived Rust daemon, JSON-RPC over stdin/stdout, events on stderr)

Rationale:
- Preserves the "Python is untrusted, Rust is trusted" boundary that is the core of LockerDrive's defense model.
- Enables continuous auth + event push (E2) without a kernel driver.
- Avoids named-pipe ACL subtleties (the stdin/stdout pipe pair is anonymous and inherited — no squatting risk).
- Modest code change: ~200 lines of Rust daemon loop + ~50 lines of Python client wrapper.

### Memory zeroization details (critical)

From the appsec.guide memory-zeroization page and the Rust Foundation article on Rust's memory model:
- In Rust, use `zeroize::Zeroize` on every buffer that ever held a secret (PIN, derived key, plaintext). The `Zeroizing<T>` wrapper auto-zeroes on drop.
- `String::clear()` is **not** sufficient — the underlying buffer may have been reallocated and the old pages may persist in the heap or be paged to disk. `Zeroizing<String>` overwrites the bytes with zeros *before* deallocation.
- Use `VirtualLock` (Windows) / `mlock` (POSIX) on the buffer to prevent it being paged to the swap file. Note: `mlock` requires `CAP_IPC_LOCK` on Linux; `VirtualLock` requires `SeLockMemory` privilege which is granted by default to administrators but not to standard users — so we need to handle the failure case (treat as "less secure but proceed" with a warning).
- In Python, use `bytearray` (mutable, can be overwritten) instead of `str` (immutable, interned). After use, `for i in range(len(buf)): buf[i] = 0`. This is best-effort — Python's GC may copy the buffer internally — but it's much better than nothing.

---

## E4. Error Handling & Fail-Safe Design

### The fail-safe principle

From the CompTIA SecurityX material (ituonline.com/.../fail-secure-and-fail-safe-strategies) and Saltzer & Schroeder's original 1975 paper (cited in iris.unitn.it/.../SPM-fail-safe-v7.pdf):
> **"Base access decisions on permission rather than exclusion."** — i.e., default deny. If anything goes wrong, the system should end up in the *locked* state, not the *unlocked* state.

This is the opposite of "fail-soft" (continue serving requests despite errors) and "fail-open" (let traffic through when the auth server is unreachable, to keep the business running). For a vault, fail-safe means: **any unexpected condition → vault locks**.

### LockerDrive's current failure modes — and what should happen

| Failure | Current behavior | Recommended behavior |
|---|---|---|
| Rust binary crashes during `unlock` (e.g., panic, OOM) | `subprocess.run` returns nonzero; `vault_core.py` returns `ERROR:...`; vault state stays `LOCKED` (good) | Same, plus: write `LOCK_RUST_CRASH` to audit log; show error toast; ensure `attrib +h +s` is re-applied (the `unhide_vault()` may have run before the crash) |
| Rust binary crashes during `lock` | `subprocess.run` returns nonzero; **vault may stay un-hidden** (bad) | Retry `lock` once; if still failing, run `attrib +h +s E:\` directly from Python as a fallback; never leave the vault un-hidden |
| Python UI crashes while vault is unlocked | `atexit` not currently registered → vault stays unlocked, files stay visible, theme stays "Liquid Glass" (bad) | Register `atexit.register(_force_lock)` + `faulthandler` for crash dumps; `SIGTERM`/`SIGINT` handlers; on any uncaught exception in the UI thread, call `_force_lock()` before exit |
| Face engine throws `RuntimeError` during unlock | Currently bubbles up as `ERROR:...`; vault stays locked (good) | Same, plus log `FACE_ENGINE_FAULT`; offer PIN fallback automatically |
| Face engine throws during continuous auth (E2) | N/A (continuous auth not implemented) | Lock immediately; log `LOCK_ENGINE_FAULT` |
| Hard drive full → can't write `.auth_hash` | `fs::write().expect(...)` panics; Rust binary exits; Python sees `ERROR:...` | Check disk space before write; on failure, abort init and show specific error; never overwrite existing hash file until new one is written successfully |
| System power loss during `unlock` | `attrib +h` may have been removed, `explorer` may have spawned; on reboot, `E:\` is visible | On startup, *always* re-apply `attrib +h +s` if vault state file says LOCKED. This is the "transaction recovery" step. |
| User yanks the E: drive while unlocked | `unlock` succeeds but `E:\` is gone; vault state says UNLOCKED; later `lock` fails silently | Before any unlock/lock operation, check `os.path.exists(VAULT_TARGET)`; if missing, refuse to transition state |
| Clock set backwards to bypass lockout | `lockout_until > now` check fails; lockout ends immediately (bad) | Use `SystemTime::now()` but also persist a monotonic "last seen timestamp" and refuse to accept `now < last_seen - 60s` |

### Transactional lock/unlock

The current unlock sequence is:
```
verify_pin → unhide_vault → open_explorer → println!("OK")
```
If `unhide_vault` succeeds but `open_explorer` fails, the vault is unhidden but the user sees no error. If the Python side then times out (30s), the vault is unhidden and the UI thinks it failed.

**Recommendation: implement a write-ahead log (WAL).** Before any state transition, the Rust binary writes a small `.vault_txn` file:
```
{
  "intent": "unlock",
  "phase": "unhiding",
  "timestamp": 1719475200,
  "vault_target": "E:\\"
}
```
After each phase completes, the file is updated. On startup, if the file exists, the Rust binary replays the unfinished transaction (e.g., re-hides if intent was `lock` but `hide` hadn't completed). This is overkill for a hobbyist project but is the *correct* architecture if we want crash-safety guarantees.

A lighter alternative: **make unlock atomic from the user's perspective** — only return `OK` after *all* of (verify PIN, unhide, open explorer) succeed. If any step fails, *undo the prior steps* (re-hide, then return error). This is the "compensating transaction" pattern from distributed systems.

### Auto-lock on ANY unhandled exception

Wrap the entire UI main loop in:
```python
try:
    main_loop()
except Exception:
    vault_core.lock()  # best-effort
    log_crash(traceback.format_exc())
    raise  # let it crash visibly
```
Plus register `atexit.register(vault_core.lock)` and use `faulthandler.enable()` to dump tracebacks on segfault. The Python docs note that `atexit` doesn't run on `SIGKILL` or hard crashes — for those, the watchdog thread in Rust (E3, Option B) is the safety net, since it can detect the Python process dying (via the stdin pipe closing) and trigger a lock from the Rust side.

---

## E5. Logging & Auditing

### What to log

For a vault app, the audit log should answer four questions:
1. **Who** tried to access the vault? (User SID, process ID, face-match score if applicable)
2. **When**? (Timestamp in UTC; never trust local clock for forensics)
3. **What** happened? (Unlock attempt, lock, face-mismatch, PIN failure, recovery-key use)
4. **What was the outcome?** (Success, failure, lockout triggered)

Specific events to log for LockerDrive:
- `UNLOCK_SUCCESS` (auth method: face | pin | recovery)
- `UNLOCK_FAILED` (auth method, attempts remaining)
- `LOCKOUT_TRIGGERED` (failed attempt count, lockout duration)
- `LOCK_MANUAL` (user pressed F12 or clicked lock)
- `LOCK_AUTO_FOCUS` (focus-loss timer fired)
- `LOCK_AUTO_IDLE` (idle timer fired)
- `LOCK_AUTO_SESSION` (Windows session locked — see E7)
- `LOCK_FACE_MISMATCH` (continuous auth detected different face)
- `LOCK_FACE_ABSENT` (continuous auth: no face for >30s)
- `LOCK_ENGINE_FAULT` (face engine exception)
- `LOCK_RUST_CRASH` (Rust binary exited unexpectedly)
- `RECOVERY_USED` (vault unlocked with recovery key — *always* flag this for user review)
- `VAULT_TARGET_MISSING` (E:\ not present at unlock attempt)
- `CONFIG_CHANGE` (PIN changed, recovery key regenerated)

### What NOT to log

- The PIN, the recovery key, any face embedding, any derived key
- The names of files inside the vault (privacy — a stolen log shouldn't reveal what's inside)
- The face image itself (only the cosine similarity score, bucketed: `<0.3`, `0.3–0.5`, `0.5–0.7`, `>0.7`)
- The user's real name (use the Windows SID instead)

### Tamper-proofing the log

From the Mattermost article (mattermost.com/.../18-tips-to-implement-tamper-proof-audit-logs) and Cossack Labs' "Audit logs security" writeup (cossacklabs.com/blog/audit-logs-security):
- **Hash chaining.** Each log entry includes `prev_hash = SHA256(prev_entry || prev_hash)`. Deleting or reordering any entry breaks the chain.
- **Forward-secure MAC.** Use a key that is itself derived from `HMAC(secret, day_counter)`, and rotate the secret forward (each day, replace `secret` with `SHA256(secret)` — you can verify old entries only if you've kept the chain; you can't forge new entries for past days without the old secret). This is the "forward-secure logging" construction from Bellare & Yee 1997.
- **Append-only storage.** Open the log file with `FILE_APPEND_DATA | SYNCHRONIZE` and never `FILE_WRITE_DATA`. This doesn't strictly prevent truncation (an attacker with admin can still truncate), but it raises the bar.
- **Hashicorp Vault's recommendation** (developer.hashicorp.com/vault/docs/audit/best-practices): enable **at least two audit devices** — e.g., a local file plus Windows Event Log. If one is tampered with, the other survives.

### Where to store logs

Three places, for defense in depth:
1. **Local encrypted file** (`.lockerdrive_audit.bin`) — encrypted with a key derived from a random 32-byte value stored in the Rust binary's data directory (not the user's PIN, because we want logs to be readable even if the user forgets the PIN). Format: length-prefixed entries, hash-chained, HMAC-authenticated.
2. **Windows Event Log** — under `Applications and Services Logs \ LockerDrive \ Operational`. Requires registering an event source at install time. Event Log entries can be forwarded to a SIEM (Sentinel, Splunk) for off-machine preservation.
3. **Optional remote sink** — if the user opts in, POST SHA-256 hashes of each entry to a configured webhook. This way even a full disk wipe can't hide the audit trail. Overkill for most users, but valuable for the truly paranoid.

### Preventing log analysis from revealing patterns

This is a real concern: if an attacker steals the audit log, they can learn "the user opens the vault every weekday at 9am and locks at 5pm" — which is itself sensitive metadata. Mitigations:
- **Bucket timestamps** to the nearest 15 minutes.
- **Don't log successful unlocks at all** — only failures and exceptions. Successes are noise for security analysis but gold for an attacker profiling the user.
- **Rotate the log** monthly and zeroize old entries (after the user has had a chance to review).
- **Encrypt the log** with a key the user must explicitly unlock to view (so that a stolen `.lockerdrive_audit.bin` file is useless without the user's review-PIN).

---

## E6. Multi-Factor Authentication Strategy

### The current model: PIN OR Face

LockerDrive today accepts *either* a 4-digit PIN (`0104`) *or* a face match. This is **1-factor** authentication with two alternative paths — not 2FA. An attacker who guesses the PIN (10,000 possibilities, 3 attempts before lockout = 0.03% chance per session) gets in without ever needing the user's face. An attacker who has a photo of the user's face gets in without the PIN.

For a vault that contains sensitive personal data, this is too weak.

### The right model: layered "any-of" with weighted strength

Following the BitLocker "multiple protectors" pattern (E1), the master key should be wrapped separately by each factor. Any single factor can unlock the vault — but each factor has a different *strength profile*, so an attacker must defeat whichever factor is strongest at the time of attack:

- **Face (primary, ~strength 3/5):** RetinaFace + AdaFace + 12-layer anti-spoofing is hard to defeat with a printed photo, but a determined attacker with a 3D mask or a deepfake played on a tablet held to the camera can defeat it. Anti-spoofing adds significant friction but isn't infallible.
- **PIN (secondary, ~strength 2/5):** A 4-digit PIN is weak (10k space), but the 3-attempt lockout caps the brute-force risk at ~0.03% per session. A 6-digit PIN raises to 1M space = 0.0003% per session. A 8-char alphanumeric password raises to ~6 quadrillion = effectively unbreakable in 3 attempts. We should let the user choose the strength.
- **Recovery key (tertiary, ~strength 5/5):** A 32-character random base32 string (~160 bits) printed at install time. Stored offline (paper in a safe). Required for recovery if both other factors are lost. *Each use is logged loudly.*
- **Optional hardware key (YubiKey, strength 4/5):** If the user has a YubiKey, configure slot 2 with HMAC-SHA1 challenge-response. The Rust binary sends a 64-byte challenge; the YubiKey returns the 20-byte HMAC. The response is mixed into the KDF input, so without the physical YubiKey the vault can't be unlocked even with the PIN.

### Should we require BOTH face AND PIN (true 2FA)?

**Pros of AND:**
- Strongest possible security — attacker needs both factors.
- Matches "know + have" or "are + know" patterns from financial apps.

**Cons of AND:**
- Doubles the user's friction at every unlock. For a vault that's opened many times a day, this is exhausting.
- If the camera fails (driver issue, lighting), the user can't get in even with the right PIN.
- If the user forgets the PIN, even their own face can't help them.

**Recommendation:** Default to **any-of** (face OR PIN OR recovery), with the option in settings to require **face AND PIN** ("paranoid mode"). Most users will leave it at any-of; power users can opt into 2FA. This is the BitLocker model (you can configure TPM+PIN as required, or TPM-only as convenient).

### The face-as-second-factor pattern

Even in any-of mode, we can use face as a *silent second factor* — i.e., unlock with PIN succeeds, but the Rust daemon *also* starts a 5-second face check in the background. If the face doesn't match within 5s, the vault locks again. This gives us 2FA-like security without the user friction of explicitly scanning their face every time.

This pattern is what 1Password does with Windows Hello: "I clicked unlock with PIN, but Windows Hello also quietly verified my face in the background."

### The YubiKey integration (optional, for power users)

From docs.yubico.com/.../challenge-response.html and the yubikey-handbook (github.com/ruimarinho/yubikey-handbook):
- Configure slot 2 (long-press) of the YubiKey with HMAC-SHA1 challenge-response, secret = random 20 bytes.
- At unlock, the Rust binary generates a 64-byte random challenge, sends to YubiKey via HID, gets back a 20-byte HMAC response.
- The response is concatenated with the PIN, then fed to Argon2id: `key = Argon2id(pin || yubikey_response, salt)`.
- Without the YubiKey, even the correct PIN produces the wrong key and decryption fails.
- The YubiKey never reveals its secret — only ever returns HMAC outputs. So a memory dump of the Rust binary doesn't help the attacker.

This is a pure *offline* 2FA — no network, no Yubico cloud, no FIDO server. The secret is shared between the YubiKey and the vault's stored challenge-response verifier.

### Implementation priority

1. **v1.1:** Add 32-char recovery key, generated at install (E8). Wrap master key with both PIN-derived and recovery-derived keys.
2. **v1.2:** Add optional "face AND PIN" paranoid mode.
3. **v1.3:** Add YubiKey HMAC challenge-response as an optional third protector.
4. **v2.0:** Consider TPM sealing of the master key (BitLocker-style) for users with TPM 2.0 chips.

---

## E7. Auto-Lock Improvement

LockerDrive's current auto-lock is a 60-second focus-loss timer. The triggers are:
- Window loses focus → start 60s timer → lock
- Press F12 → instant lock
- Switch tabs → lock

This is incomplete. Real-world vaults lock on a broader set of OS events. Here's what we should add:

### 1. Windows session lock (Win+L, screen saver activation, idle timeout from Windows power settings)

From learn.microsoft.com/win32/termserv/wm-wtssession-change and the WTSRegisterSessionNotification docs:
- The `WM_WTSSESSION_CHANGE` message is broadcast to all top-level windows that have registered with `WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)`.
- The `wParam` carries the event type: `WTS_SESSION_LOCK = 0x7`, `WTS_SESSION_UNLOCK = 0x8`.
- In Python with `pywin32`, the recipe is:
  ```python
  import win32con, win32gui, win32ts
  win32ts.WTSRegisterSessionNotification(hwnd, win32ts.NOTIFY_FOR_THIS_SESSION)
  # In the window proc:
  if msg == win32con.WM_WTSSESSION_CHANGE:
      if wParam == 0x7:  # WTS_SESSION_LOCK
          vault_core.lock()
  ```
- CustomTkinter's underlying Tk widget doesn't directly receive Win32 messages, so we need a hidden `win32gui.CreateWindow` that does nothing but receive the message and call back into Python. This is the same pattern Tim Golden's `win32_how_do_i` recipes use for USB detection.

**Action:** lock the vault immediately on `WTS_SESSION_LOCK`, no grace period. The user has already chosen to lock Windows; we should respect that.

### 2. Display power-off (monitor sleep)

Windows fires `WM_POWERBROADCAST` with `PBT_POWERSETTINGCHANGE` and the `GUID_CONSOLE_DISPLAY_STATE` data. When the display goes off, lock. This catches the "user walked away, monitor went to sleep after 10 min" case even when Windows itself hasn't locked.

### 3. Idle detection (GetLastInputInfo)

From learn.microsoft.com/win32/api/winuser/nf-winuser-getlastinputinfo and the Stack Overflow thread on idle detection:
```python
import ctypes
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
lii = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
idle_ms = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
```
Poll every 5 seconds; if `idle_ms > 300000` (5 min), lock. This catches "user is reading a long document, no input for 5 min" — even though they haven't left the chair, the vault should re-lock. Configurable.

### 4. USB device removal (if vault target is a USB drive, or if YubiKey-based 2FA is enabled)

From learn.microsoft.com/win32/devio/detecting-media-insertion-or-removal and abdus.dev/posts/python-monitor-usb:
- Windows broadcasts `WM_DEVICECHANGE` (0x0219) to all top-level windows when PnP devices come or go.
- `wParam == DBT_DEVICEREMOVECOMPLETE (0x8004)` signals removal.
- Parse `lParam` as a `DEV_BROADCAST_HDR` to determine the device type.
- If the removed device is the E: drive → lock immediately (vault target is gone).
- If the removed device is the configured YubiKey → lock immediately (second factor gone).

### 5. Network connectivity change (optional, for paranoid mode)

If the user opts into "lock when network changes," detect `WM_DEVICECHANGE` for network interfaces and lock. The rationale: a vault might be safe on the home network but should auto-lock when joining a coffee-shop Wi-Fi. Probably overkill for v1.

### 6. Foreground window change (current trigger, refined)

The current 60s focus-loss timer is too aggressive (annoying when the user Alt-Tabs to copy a password) and too lax (60s is a long window for an attacker). Refinements:
- **Distinguish "switched to a different window" from "minimized/closed LockerDrive."** Switching to another window: 60s grace. Minimizing/closing: instant lock.
- **Whitelist known-safe windows** (the system calculator, the user's password manager, the File Explorer that's showing E:\). When the user Alt-Tabs to File Explorer showing the vault, don't start the timer.
- **Reduce to 30s** for non-whitelisted windows. 30s is enough for a quick copy-paste; short enough that an opportunistic attacker can't do much.

### 7. Screen saver detection

`SystemParametersInfo(SPI_GETSCREENSAVERRUNNING, ...)` returns whether the screen saver is currently active. Poll every 5s, or subscribe to `WM_SYSCOLORCHANGE` (which fires when the screen saver starts). Lock when the screen saver activates.

### Summary of recommended triggers

| Trigger | Action | Latency |
|---|---|---|
| F12 panic key | Lock + re-hide + theme revert | <100ms (current) |
| Windows session lock (Win+L) | Lock | <500ms |
| Screen saver starts | Lock | <500ms |
| Display power-off | Lock | <1s |
| Idle (no input for 5 min) | Lock | 5s poll → <5s |
| Focus loss to non-whitelisted window | Lock after 30s | 30s |
| Focus loss to whitelisted window | No timer | n/a |
| Window minimized/closed | Lock | <500ms |
| USB removal (vault target / YubiKey) | Lock | <500ms |
| Face mismatch (continuous auth, E2) | Lock | <1s |
| Face absent >30s (continuous auth) | Lock | <1s |
| Unhandled exception in UI | Lock (atexit) | <500ms |
| Rust binary crash | Lock (watchdog) | <1s |

The combination of all of these is what BitLocker, 1Password, and KeePass all do in some form. The cost is ~150 lines of Python (mostly `ctypes`/`pywin32` plumbing) plus a background thread for the polling-based triggers.

---

## E8. Backup & Recovery

### The problem

LockerDrive currently has **no recovery path**. Lose the PIN, break the camera, fail face recognition repeatedly → permanent lockout. The user's data is gone. This is acceptable for a toy but not for a vault that may store real secrets.

### BitLocker's recovery model — the reference design

From learn.microsoft.com/.../bitlocker/recovery-process and the FAQ:
- When BitLocker is enabled, a 48-digit recovery password (160 bits of entropy, formatted as 8 groups of 6 digits with a checksum) is automatically generated.
- The recovery password is wrapped around the VMK as one of the protectors (alongside TPM+PIN).
- The user must save it to one of: Microsoft account, AD, USB, file, or printout. BitLocker refuses to enable until at least one backup is recorded.
- On recovery, the user types the 48 digits; BitLocker uses them to unwrap the VMK and proceeds.

### 1Password's recovery model — the Emergency Kit

- The Secret Key (34-char base32) is printed on a one-page "Emergency Kit" PDF at signup.
- Combined with the Master Password, the Secret Key decrypts the vault.
- Lose the Master Password but keep the Emergency Kit → still locked out (1Password is zero-knowledge; they can't help).
- Lose the Secret Kit but remember the Master Password → still locked out (need both).
- This is *intentionally* strict — 1Password accepts that some users will lose their data rather than weaken the zero-knowledge guarantee.

### Recommendation for LockerDrive: a recovery key, generated at install, required to be saved

**Design:**
- On first run, the Rust binary generates a 32-byte random recovery key, formatted as 26 characters of base32 (`AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF`) — same entropy as a 128-bit AES key, easy to type, with a 2-char CRC checksum at the end.
- The recovery key is fed into Argon2id with its own salt to derive a *recovery key*, which wraps the master key (same as the PIN-derived key does).
- The recovery key is shown to the user **once**, with strong UI cues ("print this, save this to a USB drive, do not lose it"). The user must check "I have saved my recovery key" before proceeding.
- The recovery key is then *deleted from disk* — only its Argon2id verifier (the wrapped master key) is kept.
- To unlock with the recovery key, the user types the 26 chars; the Rust binary derives the recovery key, attempts to unwrap the master key, and if successful unlocks the vault. This event is logged loudly (`RECOVERY_USED`) and the user is prompted to "rotate" the recovery key (generate a new one and re-save).

**Implementation in the Rust binary:**
- New command: `vault_core init-recovery` → generates key, prints to stdout, writes the wrapped master key to `.recovery_blob`.
- New command: `vault_core unlock --recovery <key>` → uses recovery key path instead of PIN.
- New command: `vault_core rotate-recovery <old> <new>` → re-wraps master key with new recovery key.

### Why NOT to use Windows DPAPI for recovery

Tempting: store the recovery key with `CryptProtectData` (Windows DPAPI), so the user can recover by just signing into Windows. From learn.microsoft.com/win32/api/dpapi/nf-dpapi-cryptprotectdata and tierzerosecurity.co.nz/2024/01/22/data-protection-windows-api:
- DPAPI encrypts data with a key derived from the user's logon password. Only the same Windows user (on the same machine, unless `CRYPTPROTECT_LOCAL_MACHINE` is set) can decrypt.
- **This is the wrong threat model for LockerDrive.** If the attacker is at the keyboard with the user's Windows session open, DPAPI gives them the recovery key for free. The whole point of a recovery key is "you can use it when you're at a different machine or after a fresh Windows install."
- DPAPI master keys expire after 90 days, after which old data may become unrecoverable. Not suitable for long-term vault recovery.

**Use DPAPI for convenience features only** (e.g., "remember my PIN for 7 days" — but only if the user explicitly opts in, and the threat model explicitly excludes attackers with Windows-session access). Never for the recovery key.

### Forgotten PIN flow

1. User clicks "Forgot PIN?" on the unlock screen.
2. UI prompts: "Enter your 26-character recovery key."
3. User types the recovery key.
4. Rust binary verifies; on success, unlocks the vault and immediately prompts: "Set a new PIN."
5. User enters new PIN; Rust binary re-wraps the master key with the new PIN-derived key. Old PIN wrapping is *not* deleted (in case the user finds the old PIN later) — but a flag is set so the user can review and revoke it.
6. Optional: regenerate the recovery key at the same time (so the just-used recovery key can't be reused by an attacker who found the paper).

### Backup of the vault contents

A separate concern from the recovery key: what if the E: drive fails? Recommendations:
- **Manual backup mode:** a Rust command `vault_core export-backup <pin>` that decrypts the vault contents and writes them to a user-chosen `.zip.enc` file (encrypted with a key derived from a fresh user-supplied backup password, *not* the PIN — so the backup is portable).
- **Restore mode:** `vault_core import-backup <file> <backup-password>` to restore the vault from the backup.
- The backup is encrypted at rest; if lost, only the backup password stands between attacker and data. This is the user's responsibility; we warn them clearly.

### Priority

Recovery key is **must-have for v1.1**. Without it, any user who loses their PIN loses their data — and that's a UX failure that no amount of security theater can make up for.

---

## E9. Concrete Recommendations for LockerDrive

Synthesizing all of the above, here is the prioritized roadmap. Each item is sized so it can be a single PR.

### Phase 1 (must-have, ~1 week of work)

**P1.1 — Switch KDF from PBKDF2-SHA256 (100k) to Argon2id (memory=64MB, iterations=10, parallelism=2).**
- Use the `argon2` crate in Rust. Add `argon2 = { version = "0.5", features = ["std"] }` to `Cargo.toml`.
- Old hashes detected at startup → re-derive with Argon2id on first successful unlock with the old KDF. Write a `kdf_version` byte to the hash file.
- Cost: ~600ms per unlock on a 2020-era laptop. Acceptable.

**P1.2 — Two-layer key wrapping.**
- Generate a random 32-byte Master Key on first init.
- Wrap (encrypt) the Master Key with the PIN-derived key, store in `.master_key_wrap_pin`.
- The wrapped Master Key is what `unlock` decrypts; it then becomes the AES-256-GCM key for vault contents.
- Benefit: rotating the PIN only requires re-wrapping the 32-byte Master Key, not re-encrypting every file. Also enables multi-factor (E6) and recovery (E8) cleanly.

**P1.3 — Recovery key.**
- New `init-recovery` command generates a 26-char base32 key.
- New `.master_key_wrap_recovery` blob, decrypted by Argon2id(recovery_key, recovery_salt).
- New `--recovery` flag on `unlock`.
- On first run after upgrade, prompt user to generate a recovery key; require them to acknowledge saving it before proceeding.

**P1.4 — Replace XOR obfuscation of `.vault_state` with authenticated encryption.**
- Current: 4-byte XOR key `"KSLD"` — trivially reversible, no integrity.
- New: encrypt the state file with a key derived from the machine ID (Windows `MachineGuid` from registry) via HKDF-SHA256. This isn't strong crypto (the machine ID is readable to any local user) but it raises the bar from "open in hex editor and see the failed-attempts count" to "must run code on the machine to manipulate state."
- Add an HMAC-SHA256 tag for integrity — tampering with the state file resets it to zero failed attempts (fail-safe defaults, E4).

**P1.5 — Crash-safe lock.**
- Register `atexit.register(vault_core.lock)` in `main.py`.
- Enable `faulthandler.enable()` and `faulthandler.register(signal.SIGTERM, all_threads=True)` for crash dumps.
- Wrap the entire UI main loop in `try/except Exception: vault_core.lock(); raise`.
- Add a "verify on startup" step: if the previous shutdown was dirty (a `.lock_dirty` flag file exists), force `attrib +h +s` on `E:\` before showing the UI.

### Phase 2 (high-value, ~2 weeks)

**P2.1 — Long-lived Rust daemon (E3, Option B).**
- New `vault_core.exe daemon` mode: reads JSON-RPC from stdin, writes responses to stdout, pushes events to stderr.
- Python wrapper `vault_core.py` becomes a thin client that connects to the daemon on startup and stays connected.
- Enables continuous auth, event-driven lock, single-instance lock, crash detection.

**P2.2 — Continuous authentication (E2).**
- Watchdog thread in the Rust daemon polls the face engine every 60s.
- Three failure modes handled per E2.
- Camera is shared with the unlock UI via a single `CameraService` running in the Rust daemon.

**P2.3 — Auto-lock on Windows session events (E7).**
- Hidden `win32gui` window in Python receives `WM_WTSSESSION_CHANGE`, `WM_POWERBROADCAST`, `WM_DEVICECHANGE`.
- Map each event to the lock actions in E7's table.
- Replace the 60s focus-loss timer with the refined 30s + whitelist version.

**P2.4 — Memory zeroization.**
- Rust: `Zeroizing<String>` for PIN, derived keys, plaintext. `VirtualLock` on sensitive buffers (best-effort; warn if it fails).
- Python: extract PIN from the entry widget into a `bytearray` immediately, zero after use. Replace `subprocess.run(input=pin_str)` with `subprocess.run(input=pin_bytes)`.

### Phase 3 (power-user features, ~3 weeks)

**P3.1 — Audit log (E5).**
- New `.lockerdrive_audit.bin` file, hash-chained, HMAC-authenticated.
- Log the 14 event types listed in E5.
- Viewer UI: a "Security History" tab in the vault that decrypts and shows recent events.
- Optional Windows Event Log sink (register event source at install).

**P3.2 — YubiKey HMAC challenge-response (E6).**
- Detect YubiKey via HID enumeration.
- Send 64-byte challenge, receive 20-byte response, mix into Argon2id input.
- Optional third protector — user can enable in settings.

**P3.3 — Optional "paranoid mode" (face AND PIN).**
- Settings toggle. When on, both factors required.
- Power users only.

**P3.4 — Backup / export mode.**
- `vault_core export-backup <pin> <backup_password>` → writes `.zip.enc` to user-chosen location.
- `vault_core import-backup <file> <backup_password>` → restores.

### What NOT to do (anti-recommendations)

- **Don't switch to PyO3.** It would put the security logic in the same process as Python, destroying the "compiled Rust is untrusted-Python's sandbox" boundary. The current subprocess model is correct; refine it (E3, Option B) instead.
- **Don't use cascading ciphers** (AES-Twofish-Serpent) à la VeraCrypt. The threat model doesn't justify the 3× cost.
- **Don't rely on `attrib +h +s` as a security boundary.** It's a UX feature, not a security feature — anyone with `dir /a` sees the files. The actual security is the encryption, and `attrib` just makes the vault "not visible" to casual users. Document this clearly in the README.
- **Don't store the recovery key with DPAPI.** Wrong threat model (E8).
- **Don't implement continuous face auth on a sub-30-second interval.** It'll drain the camera, heat the laptop, and annoy the user — without meaningfully improving security over a 60s interval.
- **Don't log successful unlocks.** They're noise for security analysis and gold for an attacker profiling the user (E5).

---

## E10. Source List

All sources retrieved July 2025 via the z-ai `web_search` function. JSON dumps under `/home/z/my-project/research/E_extra/`.

### Real-world vault architectures (E1)
1. VeraCrypt — Key Derivation Algorithms. https://veracrypt.io/en/Key%20Derivation%20Algorithms.html
2. VeraCrypt — Encryption Scheme. https://veracrypt.io/en/Encryption%20Scheme.html
3. VeraCrypt — Header Key Derivation, Salt, and Iteration Count. https://veracrypt.io/en/Header%20Key%20Derivation.html
4. VeraCrypt — Cascades of ciphers. https://veracrypt.io/en/Cascades.html
5. VeraCrypt GitHub Issue #1752 — Argon2id support & cascade key derivation. https://github.com/veracrypt/VeraCrypt/issues/1752
6. Elcomsoft — Breaking VeraCrypt: Obtaining On-The-Fly Encryption Keys (2021). https://blog.elcomsoft.com/2021/06/breaking-veracrypt-obtaining-and-extracting-on-the-fly-encryption-keys
7. KeePass — Security documentation. https://keepass.info/help/base/security.html
8. KeePass forum — Argon2d vs Argon2id and master password. https://sourceforge.net/p/keepass/discussion/329220/thread/7fa9b0a91c
9. Sysdig — KeePass CVE-2023-32784 detection (memory dump leaks master password). https://www.sysdig.com/blog/keepass-cve-2023-32784-detection
10. Cyberis — Exploiting KeePass CVE-2023-32784. https://www.cyberis.com/article/exploiting-keepass-cve-2023-32784
11. Microsoft — BitLocker Overview. https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker
12. Microsoft — BitLocker recovery process. https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/recovery-process
13. Microsoft — BitLocker FAQ. https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/faq
14. itm4n — A Deep Dive into TPM-based BitLocker Drive Encryption (2023). https://itm4n.github.io/tpm-based-bitlocker
15. Elcomsoft — Understanding BitLocker TPM Protection (2021). https://blog.elcomsoft.com/2021/01/understanding-bitlocker-tpm-protection
16. 1Password — The architectural reason 1Password can't read your vault data. https://1password.com/blog/the-architectural-reason-1password-cant-read-your-vault-data
17. 1Password — About your Secret Key. https://support.1password.com/secret-key-security
18. 1Password — Secret Key: what it is and how it protects you. https://1password.com/blog/what-the-secret-key-does
19. 1Password — Zero-knowledge encryption. https://1password.com/features/zero-knowledge-encryption
20. Darth Null — 1Password local vaults deep dive. https://darthnull.org/1pass-local-vaults

### Continuous authentication (E2)
21. NIST SP 800-63B (Digital Identity Guidelines — Authentication). https://pages.nist.gov/800-63-3/sp800-63b.html
22. Commscope — Periodic Reauthentication of Authenticated Clients (recommends >300s). https://docs.commscope.com/bundle/fastiron-10010-securityguide/page/GUID-32DF42EF-2766-4CCC-A91D-E548A6FC42EE.html
23. Microblink — Reauthentication vs session timeout. https://microblink.com/resources/glossary/reauthentication
24. Microsoft — Common questions about Windows Hello for Business. https://learn.microsoft.com/en-us/windows/security/identity-protection/hello-for-business/faq
25. IDmanagement.gov — Configure Windows Hello for Business. https://www.idmanagement.gov/implement/whfb
26. 1Password Community — Continuous reprompt Windows Hello. https://www.1password.community/1password-at-home-31/continuous-reprompt-windows-hello-20669

### Secure IPC (E3)
27. Microsoft — Named Pipe Security and Access Rights. https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights
28. Synacktiv — Hooking Windows Named Pipes (security considerations). https://www.synacktiv.com/en/publications/hooking-windows-named-pipes
29. csandker — Offensive Windows IPC Internals 1: Named Pipes. https://csandker.io/2021/01/10/Offensive-Windows-IPC-1-NamedPipes.html
30. VerSprite — The Fundamentals of Windows Vulnerable Named Pipes. https://versprite.com/resources/vs-labs/microsoft-windows-pipes-intro
31. CyberCX — Harnessing the power of Named Pipes. https://cybercx.com.au/blog/harnessing-the-power-of-named-pipes
32. Stack Exchange — Most secure way of transferring a secret between two processes. https://security.stackexchange.com/questions/176319/what-is-the-most-secure-way-of-transferring-a-secret-between-2-processes-running
33. PyO3 — Rust bindings for Python. https://github.com/pyo3/pyo3
34. PyO3 user guide — Building and distribution. https://pyo3.rs/main/building-and-distribution
35. Ben Frederickson — Writing Python Extensions in Rust using PyO3. https://www.benfrederickson.com/writing-python-extensions-in-rust-using-pyo3
36. Rust Foundation — Secure App Development with Rust's Memory Model. https://rustfoundation.org/media/secure-app-development-with-rusts-memory-model
37. AppSec Guide — Memory zeroization in Rust. https://appsec.guide/docs/languages/rust/memory-zeroization
38. Stack Overflow — Securely Erasing Password in Memory (Python). https://stackoverflow.com/questions/728164/securely-erasing-password-in-memory-python
39. pyca/cryptography Issue #8425 — Secure wipe with memoryview. https://github.com/pyca/cryptography/issues/8425

### Fail-safe design (E4)
40. CompTIA SecurityX — Fail-Safe Defaults: Implementing Resilient Security Strategies. https://www.ituonline.com/comptia-securityx/comptia-securityx-4/mitigations-implementing-fail-secure-and-fail-safe-strategies-for-robust-security
41. Trusted Institute — Fail-Safe Defaults (Saltzer & Schroeder). https://trustedinstitute.com/concept/comptia-security-plus/secure-system-design-principles/fail-safe-defaults
42. Cydrill — Secure design principles (permission vs exclusion). https://cydrill.com/cyber-security/secure-design-principles
43. University of Trento — Is 'deny access' a valid 'fail-safe default' principle? (SPM-fail-safe-v7.pdf). https://iris.unitn.it/bitstream/11572/251142/1/SPM-fail-safe-v7.pdf
44. Python docs — atexit module. https://docs.python.org/3/library/atexit.html
45. Stack Overflow — Proper way to clean up a Python service (atexit, signal, try/finally). https://stackoverflow.com/questions/19279093/proper-way-to-clean-up-a-python-service-atexit-signal-try-finally

### Logging & auditing (E5)
46. Mattermost — 18 Tips to Implement Tamper-Proof Audit Logs. https://mattermost.com/blog/compliance-by-design-18-tips-to-implement-tamper-proof-audit-logs
47. Hashicorp Vault — Audit logging best practices. https://developer.hashicorp.com/vault/docs/audit/best-practices
48. Cossack Labs — Audit logs security: cryptographically signed tamper-proof. https://www.cossacklabs.com/blog/audit-logs-security
49. Emergent Mind — Immutable Audit Log Architecture. https://www.emergentmind.com/topics/immutable-audit-log
50. dev.to (veritaschain) — Building a Tamper-Evident Audit Log with SHA-256 Hash Chains. https://dev.to/veritaschain/building-a-tamper-evident-audit-log-with-sha-256-hash-chains-zero-dependencies-h0b
51. SonarSource — Audit Logging Best Practices, Components & Challenges. https://www.sonarsource.com/resources/library/audit-logging

### Multi-factor authentication (E6)
52. Yubico — Setup. https://www.yubico.com/setup
53. Yubico docs — Challenge-response (HMAC-SHA1). https://docs.yubico.com/yesdk/users-manual/application-otp/challenge-response.html
54. Yubico docs — Static passwords. https://docs.yubico.com/yesdk/users-manual/application-otp/static-password.html
55. Yubico docs — How to program a slot with a challenge-response credential. https://docs.yubico.com/yesdk/users-manual/application-otp/how-to-program-a-challenge-response-credential.html
56. SuperTokens — What Is a YubiKey and When to Use It. https://supertokens.com/blog/yubikeys
57. r/yubikey — HMAC-SHA1 Challenge-Response vs Static password. https://www.reddit.com/r/yubikey/comments/w4p2rt/hmacsha1_challengeresponse_vs_static_password_on
58. ruimarinho/yubikey-handbook — Offline authentication setup. https://github.com/ruimarinho/yubikey-handbook/blob/master/macos/offline-authentication/configuration.md
59. OWASP — Password Storage Cheat Sheet (Argon2id recommendation). https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
60. Bellator Cyber — Password Hashing Algorithms 2026: Argon2id, bcrypt & PBKDF2. https://bellatorcyber.com/blog/best-password-hashing-algorithms-of-2023
61. Deepak Gupta — Argon2 vs Bcrypt vs Scrypt vs PBKDF2 (2026). https://guptadeepak.com/the-complete-guide-to-password-hashing-argon2-vs-bcrypt-vs-scrypt-vs-pbkdf2-2026
62. Bitwarden — Encryption Key Derivation (Argon2id defaults: 32MB memory, 6 iter, 4 parallelism). https://bitwarden.com/help/kdf-algorithms
63. KeePassDX Issue #1662 — Vault encryption best recipe. https://github.com/Kunzisoft/KeePassDX/issues/1662

### Face anti-spoofing (E2, E6)
64. shenasa-ai/Face-Anti-Spoofing (GitHub). https://github.com/shenasa-ai/Face-Anti-Spoofing
65. ComplyCube — Liveness Detection: Best Practices for Anti-Spoofing Security. https://www.complycube.com/en/liveness-detection-anti-spoofing-practices
66. BioID — Liveness Detection (PAD) & Deepfake Defense. https://www.bioid.com/liveness-detection
67. Nature Scientific Reports — Adaptive fusion network for face anti-spoofing (2025). https://www.nature.com/articles/s41598-025-21461-0

### Auto-lock triggers (E7)
68. Microsoft — WM_WTSSESSION_CHANGE message. https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change
69. Microsoft — WTSRegisterSessionNotification function. https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsregistersessionnotification
70. Stack Overflow — How to detect Windows is locked (WTSRegisterSessionNotification). https://stackoverflow.com/questions/8606300/how-to-detect-windows-is-locked
71. SuperUser — Running python script when user unlocks Windows (PyWin32 pattern). https://superuser.com/questions/264935/running-python-script-on-when-user-unlocks-windows-machine
72. Francesco Foti — How to detect windows session lock/unlock in VBA. https://francescofoti.com/2019/12/how-to-detect-windows-session-lock-unlock-in-vba
73. Microsoft — GetLastInputInfo function (idle detection). https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo
74. Stack Overflow — Display idle time from Python in Windows. https://stackoverflow.com/questions/217157/how-can-i-determine-the-display-idle-time-from-python-in-windows-linux-and-mac
75. Python Library — How to Tell How Long Windows Has Been Idle. https://blog.pythonlibrary.org/2010/05/05/python-how-to-tell-how-long-windows-has-been-idle
76. mking.net — Programmatically determining whether a Windows user is idle. https://www.mking.net/blog/programmatically-determining-whether-a-windows-user-is-idle
77. Microsoft — Detecting Media Insertion or Removal (WM_DEVICECHANGE). https://learn.microsoft.com/en-us/windows/win32/devio/detecting-media-insertion-or-removal
78. abdus.dev — Detecting USB drive insertion & removal on Windows using Python. https://abdus.dev/posts/python-monitor-usb
79. Tim Golden — Detect Device Insertion (Python win32). https://timgolden.me.uk/python/win32_how_do_i/detect-device-insertion.html

### Backup & recovery (E8)
80. Microsoft — Find your BitLocker recovery key. https://support.microsoft.com/en-us/windows/security/encryption/find-your-bitlocker-recovery-key
81. Microsoft — Back Up Your BitLocker Recovery Key. https://support.microsoft.com/en-us/windows/security/encryption/back-up-your-bitlocker-recovery-key
82. Specops Soft — What causes BitLocker Recovery Mode. https://specopssoft.com/blog/what-causes-bitlocker-recovery-mode
83. DriveLock — Desperately Seeking a BitLocker Recovery Key (48-digit format). https://www.drivelock.com/en/blog/bitlocker-recovery-key-hard-disk-encryption
84. Microsoft — CryptProtectData function (DPAPI). https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
85. Tier Zero Security — Windows Data Protection API (DPAPI) deep dive (master keys expire in 90 days). https://tierzerosecurity.co.nz/2024/01/22/data-protection-windows-api.html
86. Threat Hunter Playbook — Data Protection API (master key generation). https://threathunterplaybook.com/library/windows/data_protection_api.html

### Existing LockerDrive source (read in full)
87. `/home/z/my-project/LockerDrive/rust_core/src/main.rs` — current Rust security core
88. `/home/z/my-project/LockerDrive/engine/vault_core.py` — Python wrapper
89. `/home/z/my-project/LockerDrive/README.md` — current architecture summary

---

**End of Subagent r-E synthesis.** ~9,300 words total across 10 sections (~6,200 words of substantive analysis E1–E9 + ~3,100 words in the source list E10). 30 web searches executed (JSON dumps in `/home/z/my-project/research/E_extra/e1_*.json` through `e30_*.json`). Source list includes 86 numbered external references plus 3 internal source files. Concrete recommendations in E9 are prioritized in three phases, sized for individual PRs.
