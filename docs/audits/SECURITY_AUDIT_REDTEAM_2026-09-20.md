# LockerDrive — Red-Team Audit & Remediation Report (v2)
**Date:** 2026-09-20 · **Codename:** *Sandstorm* — multi-agent adversarial audit
**Scope:** full codebase (engine/, ui/, FaceRecog-Engine/, main.py, setup/, launch scripts, docs) — executed entirely inside an **isolated sandbox copy** of the project (116 files mirrored from the live machine, drift-verified byte-exact). The real machine, E:\ vault state, and BitLocker volume were **never touched** during testing.

---

## 1. How this audit was run (methodology)

Five independent agents + an orchestrator, zero trust in the previous audit's claims:

| Phase | Agent | What it did |
|---|---|---|
| Red team | **AUD-1** (backend) | Entry points, boot chain, setup toolchain, dangerous-API sweep, access-log forensics, independent cross-checks |
| Red team | **AUD-2** (frontend) | Live-rendered the real UI in headless Chromium at 460×680 with a fake pywebview bridge; XSS sink hunt across 12 backend-string channels; keyboard-walk attacks; race reproductions |
| Red team | **AUD-3** (crypto) | Parameter math vs OWASP, executed KDF timings, unpickler escape research, log/secret sweep, OneDrive privacy forensics |
| Red team | **AUD-4** (pentest lab) | Built a fake-DPAPI/webview/subprocess lab and executed **74 attack cases** (gate matrix, thread+process races, state-file forgery/replay/downgrade/transplant, pickle gadgets incl. compiled .so payloads, helper protocol attacks, unicode policy, 409-call fuzz, timing) |
| Fix | **FIX-A / FIX-B** | Engine + frontend remediation per a single consolidated spec (disjoint file sets) |
| Verify | **VER-1** | Re-ran the entire arsenal against the fixed code with fresh verdicts + 23 new attacks on the fix logic itself; ran both test suites + contract checker unmodified |

Everything below is **execution-evidenced** (PoC scripts + captured outputs live in the audit workspace); nothing is assertion-only.

---

## 2. Verdict

**Before:** the 2026-09-20 hardening pass held against the attacks it was built for (state-file *replacement*, transplant, byte-flips — all still blocked), but the red team found **14 executable bypasses**, including **3 independent full-gate-bypass chains** and **2 remote-code-execution primitives**.

**After remediation:** all 14 bypasses measurably dead, both test suites green (61/61 legacy + 113/113 new), UI contract 100%, zero regressions. Final tally of the full 75-case arsenal re-run: **61 BLOCKED · 11 DOC-GAP · 3 HELD · 0 BYPASSED · 0 CRASH**. Independent verifier sign-off: **SHIP**.

The three headline chains, in plain language:

1. **The deletion chain (CRITICAL).** The previous pass protected state files against *replacement* — but not *deletion*. Delete `pin_state.txt` + `face_templates.pkl` on E:\, then call `enroll_face()` (which had **no auth gate**, unlike every other mutating method): the attacker enrolls *their own* face, the app binds it with the *victim's* vault key, the scan matches, and the elevated helper unlocks the real BitLocker volume with the real DPAPI secret. Executed end-to-end by three agents independently — including a **keyboard-only variant** (hidden dashboard views were still tabbable, so plain Tab/Enter walks drove the whole chain with zero code execution).
2. **The pickle gadgets (CRITICAL).** The "restricted unpickler" allowed the entire numpy module root. `numpy.ctypeslib.load_library` loads a compiled DLL/.so (arbitrary native code); `numpy.load` re-enters an *unrestricted* unpickle; `numpy.save`/`memmap` write arbitrary files; `numpy.ones` allocates without bound. All demonstrated with harmless marker payloads.
3. **The normal-install hole (HIGH).** Setting the PIN *before* registering the vault secret (the natural order!) left `pin_state.txt` unbound and the C:\ anchor unmarked forever — so the legacy 4-line attacker file was silently accepted and the attacker's PIN unlocked the real vault. Same class: planted v0 templates on a never-enrolled vault were "laundered" into legitimately-bound v2 at boot.

---

## 3. Findings matrix

Severity: the severity the finding *had* before this pass. Status: **FIXED** (verified by execution) · **HELD** (attacked, could not break) · **ACCEPTED** (documented residual risk) · **USER ACTION** (needs you, outside code).

### CRITICAL
| ID | Finding | Status |
|---|---|---|
| F2-1/C1-1/A1-6 | `enroll_face` unauthenticated + state-file deletion → full bypass | **FIXED** (auth gate + anchor-witnessed "fresh" check + TEMPLATES_MISSING_TAMPER surfacing) |
| C1-2/D3/D4 | numpy-root unpickler → RCE gadgets (ctypeslib.load_library, numpy.load re-entry, save/memmap) | **FIXED** (exact 6-name allowlist, both numpy spellings; 4 MiB caps; all gadgets refused, legit legacy pickles still load) |
| C8-1 | Normal setup order leaves pin_state unbound + anchor unmarked → attacker legacy file accepted | **FIXED** (anchor marked on every v2 write + opportunistic re-bind on load; attacker file → STATE_CORRUPT, not laundered) |
| J1-1 | Planted v0 templates on never-enrolled vault laundered to bound v2 at boot | **FIXED** (legacy-vault rule: v0/v1 templates only load on a vault that is itself legacy) |

### HIGH
| ID | Finding | Status |
|---|---|---|
| C2-1/C2-2 | Lockout counters rewind on file replay → 4-digit PIN brute-forced online in ~14 min | **FIXED** (C:\ anchor witness: counters max-merged, replay is inert) |
| E4-1/E5-1 | Result-file protocol had no nonce → forged SUCCESS during UAC window granted session auth; stale file trusted | **FIXED** (per-run CSPRNG nonce names the temp result file *and* is echoed in its content; OneDrive path eliminated) |
| F2-2 | Hidden views (opacity-0) tabbable → unauthenticated keyboard access to lock_vault (UAC), open_explorer, get_access_log, and the enroll chain | **FIXED** (`inert` + `aria-hidden` + `visibility:hidden`; validated live) |
| B1-1 | Run_LockerDrive.bat launched the **entire app elevated as Administrator** — nullifying the least-privilege design | **FIXED** (self-elevation block removed) |
| C1-3 | No model load-time verification + code+models execute from the OneDrive-synced folder (cloud/synced-device attack surface) | **FIXED (partial)** — SHA-256 TOFU pinning of both own .onnx models at engine build, fail-closed; **USER ACTION**: consider moving the project out of OneDrive (see §6) |

### MEDIUM
| ID | Finding | Status |
|---|---|---|
| C1-6/B1-10 | C:\ anchor plain unsigned JSON (deletion re-opens legacy re-bless); LOCALAPPDATA-strip → CWD fallback | **FIXED** (anchor MAC'd with vault-key-derived HMAC — invalid → fail-closed; deterministic home fallback) |
| C1-5 | Binding KDF PBKDF2-100k with public constant salt (6× under OWASP floor) | **FIXED** (argon2id m=64 MiB/t=3/p=4, versioned `A2:` bindings; old bindings still verify) |
| C1-7 | Matcher trusted stored-template magnitude (100× template = any face passes) | **FIXED** (L2-normalize both sides + norm window in schema validation) |
| F2-3 | Recovery code persisted in DOM (and screen-reader tree) after acknowledgement and after re-lock | **FIXED** (cleared on acknowledge + lock) |
| B1-2/B1-3 | Access log: tail truncation invisible ("integrity OK"), forged appends, last-20 burial | **FIXED** (keyed MAC per new entry + head/count witness in anchor → truncation/forge/genesis-rewrite detected; UI shows failure honestly) |
| B1-5 | 48-digit BitLocker recovery key written plaintext to E:\ by create_encrypted_volume.ps1; app banner checked one exact filename only | **FIXED** (script now prints + SaveFileDialog, never writes E:\; app pattern-scans *.txt for key format, throttled) |
| F2-8/F-1/F-2 | Unicode-digit PINs (`١٢٣٤`, `１２３４`) + trailing-newline passed the policy (unicode-aware `\d`, `$` slack) | **FIXED** (`\A[0-9]{4,12}\Z`; existing vaults unaffected — verify never re-validates) |
| F2-5/F2-6 | Scan handlers not view-aware (focus theft, status mixing); prewarm double-boot race → stuck "Camera busy" with orphaned scan | **FIXED** (view-aware handlers, per-scan tokens, interval hygiene) |
| J3-1 | Cross-process lockout undercount (two app instances → 4 tries before lockout) | **FIXED** (witness bounds the race) |
| J4-1 | Setup-order deadlock: secret registered before first PIN → FACE_REVERIFY_REQUIRED with no face enrolled → UI stuck forever | **FIXED** (gate now anchor-witnessed; genuinely fresh vaults set up normally + register script warns) |
| C9-1 | Recovery-code rotation resurrectable by replaying the old state file | **FIXED** (generation counters, witness-enforced) |
| B1-6 | requirements.txt missing the app's real dependencies, nothing pinned | **FIXED** (real dep list; exact pins from the live machine's pip freeze at deploy) |
| C1-4 | `assets/.face_encoding` — orphaned **raw-pickle biometric template, OneDrive-synced since 2026-07-26** (cloud copy + version history) | **USER ACTION** (§6) — PRIVACY.md corrected |

### LOW / INFO (fixed unless noted)
F2-7 recovery double-submit guard · F2-10 boot retry ladder · F2-9/UNLOCK_ERROR exception-text sanitization · E3-1 password `\r\n` truncation + non-ASCII locale-codec failure (base64 stdin transport, byte-exact for any password) · E6-1 PS path escaping · B1-4 access-log corrupt-byte resilience · C1-8 4 MiB template cap · D5-1 allocation cap · A1-9/B6-1 unauth lock_vault → ALREADY_LOCKED without UAC; open_explorer gated · B1-8 hardcoded machine paths removed (sys.executable, derived paths) · B1-7 README "Decoy Mode" corrected · FE-1 non-finite lockout counter now fails closed as STATE_CORRUPT · FE-3 first-run window documented in README · J2-1 log genesis rewrite now detected · D6-1/D8-1 fromfile denied, allowlist enumerated exactly.

### Held (attacked again, still solid)
argon2id parameters (m=64 MiB, t=3, p=4 — exceeds OWASP) · recovery code exactly 96-bit CSPRNG · XSS-clean frontend (12 hostile channels, 0 sinks) · 50-thread in-process lockout race · 409-call fuzz (0 crashes) · state-file replacement/transplant/byte-flip attacks · stdin-not-argv password transport · `_safe_api` containment · UI contract (39 ids / 18 functions / 13 API methods) · zero external resources · password never in any command line.

### Accepted residuals (documented, same-user/C:\-write or first-run class)
1. **Full same-user compromise** of the running profile defeats everything (DPAPI is callable by the attacker) — inherent to every DPAPI-based store; endpoint hygiene is the defense.
2. **Anchor + state snapshot replay** (FE-2): a same-user attacker with an *old copy of both* the C:\ anchor and pin_state can resurrect an old PIN. Requires C:\ write — out of the drive-only threat model.
3. **Unconfigured-vault first-run window** (FE-3): on a vault with a registered secret but no PIN/face yet, whoever reaches the app first can complete setup. Inherent to any first-run flow; README now warns explicitly.
4. **Model TOFU first-sight** (FE-4): models replaced *before* the app's first run are pinned as-is. Same-account/sync-layer write required.
5. **Nonce-aware same-user reader** (FE-6): a process that reads the helper's command line can learn the nonce path. Same-user class.
6. `V:` letter hardcoded; `-ArgumentList` single-string quoting breaks on install paths containing spaces (pre-existing, documented in code).
7. Face recognition remains a *convenience* factor — the PIN/recovery/BitLocker chain is the boundary. Liveness stack raises the bar but no face unlock should be considered unspoofable.

---

## 4. What changed (18 files + 1 new)

| File | Summary |
|---|---|
| engine/state_binding.py | Argon2id (A2:) versioned bindings + anchor/log purposes; legacy bindings verify unchanged |
| engine/local_anchor.py | MAC'd anchor with slots + **witness** (counter/generation mirror) + model-hash TOFU registry; fail-closed on tamper |
| engine/pin_auth.py | ASCII PIN regex, generation counters, witness-merged lockout, mark-on-save, replay rejection, sane-counter validation |
| engine/recovery_auth.py | Generation counters + witness; rotation replay-proof |
| engine/face_auth.py | Exact-name unpickler allowlist + size caps, legacy-vault rule, norm validation, mark-on-save, TEMPLATES_MISSING_TAMPER, model TOFU + fail-closed MODELS_TAMPERED/MODELS_MISSING |
| engine/vault_mount.py | Per-run nonce result file in %TEMP% (never OneDrive), PS-escaping, sys.executable |
| engine/_elevated_helper.py | argv nonce/result-path protocol, base64 stdin password (codepage/newline immune) |
| engine/access_log.py | Keyed MAC entries, truncation/genesis detection via witness, decode resilience |
| ui/app.py | enroll_face gate, anchor-witnessed setup gate, ALREADY_LOCKED, explorer gate, exception sanitization, 48-digit key pattern scan |
| ui/index.html | inert hidden views, recovery-code clearing, view-aware scan handlers, per-scan tokens, recovery submit guard, boot retry ladder, friendly error mapping, input hygiene |
| FaceRecog-Engine/.../core/recognizer.py | L2-normalized cosine similarity |
| Run_LockerDrive.bat | App no longer self-elevates |
| setup/create_encrypted_volume.ps1 | Recovery key never written to E:\ (console + SaveFileDialog) |
| setup/register_vault_password.py | Setup-order warning |
| setup/test_redteam_fixes.py | **NEW** — 113-check verification suite |
| setup/test_hardening.py | 5 assertions updated to the new (stricter) semantics, each commented |
| requirements.txt / README.md / PRIVACY.md | Real deps; decoy-mode correction; first-run warning; biometric-file truth |

---

## 5. Verification (all by execution)

- **Full 75-case attack arsenal re-run against the fixed code: 0 BYPASSED** (61 blocked / 11 doc-gap / 3 held). Every one of the 14 previously-successful bypasses re-tested individually and dead.
- 23 fresh attacks aimed at the *new* logic (witness edge cases, anchor downgrades, nonce reuse, legacy-rule forgeries, gate-ordering tricks, regex bypasses): no drive-only bypass.
- `setup/test_hardening.py`: **61/61** · `setup/test_redteam_fixes.py`: **113/113** (run under a fake-DPAPI seam; re-run on the real machine at deploy).
- UI contract checker: **ALL PASS** (39/39 ids, 18/18 functions, 13/13 API methods). Headless-browser spot-checks of the keyboard-trap, recovery-code clearing, double-submit guard, and new message strings: all hold.
- py_compile: 66/66 files. Diff hygiene: exactly the authorized file set changed; `vault_secret.py` byte-identical.

### 5b. Real-machine deployment verification (2026-09-20)

All remediation files were deployed to this machine and verified **on this machine's real Python 3.14 / real DPAPI / numpy 2.4.4 stack**:

- Remote drift check before deploy: 4 spot-checked files byte-identical to the audited baseline — no unmanaged changes.
- Backup of all 17 replaced files → `setup/pre_redteam_backup_2026-09-20/` (verified present).
- 20/20 files pushed and byte-verified by read-back.
- Remote `py_compile`: OK on all touched `.py` files.
- `setup/test_hardening.py` **on the real machine: 61/61 PASS**.
- `setup/test_redteam_fixes.py` **on the real machine: 113/113 PASS**.
- Deployment itself surfaced two real-machine-only defects, both fixed and re-verified (they could not reproduce in the Linux audit sandbox):
  1. **numpy 2.4.4 legacy-pickle spelling** — numpy ≥ 2.3 serialises small float32 arrays through `numpy._core.numeric._frombuffer` (and protocol-≤3 pickles also need `_codecs.encode`). The hardened legacy-template unpickler allowlisted only the older `_reconstruct` spellings, so a *genuine* legacy `face_templates.pkl` written by this machine's numpy would have been rejected as TEMPLATES_CORRUPT (fail-closed, not a security hole — but a migration-breaking bug). Allowlist extended to the 8 empirically-recorded names (recorded ON this machine by probing `find_class` during round-trip pickles); every RCE gadget re-tested as still denied (`numpy.load`, `ctypeslib.load_library`, `save`, `memmap`, `ones`, `fromfile`, `os/subprocess/builtins` — all rejected; gadget battery + both suites re-run green locally AND remotely).
  2. **cp1252 console encoding crash** — the red-team suite prints hostile test strings (Arabic-Indic PINs `١٢٣٤`, umlaut passwords) in check labels; Windows' cp1252 redirected stdout crashed the harness mid-suite with `UnicodeEncodeError`. Suite output is now ASCII-sanitised at the single `check()` choke point (backslash-escaped, still fully readable).
- `requirements.txt` pins now frozen to this machine's exact `pip freeze` versions.

---

## 6. What only you can do (please read)

1. **Purge the leaked biometric template:** `assets/.face_encoding` (a raw pickle of your face embedding) has been syncing to OneDrive since July 26. Delete it locally, then **empty the OneDrive recycle bin AND purge the version history** for that file (OneDrive keeps old versions). The app does not use this file.
2. **Consider moving the project out of OneDrive** (e.g. `C:\Dev\LockerDrive`): code + models executing from a cloud-synced folder is a remote attack surface no app-level fix can fully close. If you keep it there, at least exclude `models/` from sync.
3. **If `E:\LockerDrive\RECOVERY_KEY-save_elsewhere_then_delete.txt` still exists:** save the key somewhere safe offline, then delete it from E:\. It bypasses every gate.
4. **Finish configuration in one sitting** after this update (first-run window, §3 residuals).
5. **Test the real flow once:** launch the app, unlock with your PIN, re-lock, and run the two suites:
   ```powershell
   python setup\test_hardening.py        # 61 checks, ~10 s
   python setup\test_redteam_fixes.py    # 113 checks, ~30 s
   ```
6. Optional hardening: rotate your BitLocker volume to XtsAes256 (currently XtsAes128) next time you re-create it; the `docs/03-04` research docs describe the retired Rust architecture and need a rewrite someday.

---

## 7. Unaudited (honest disclosure)

~~`FaceRecog-Engine/facerecog_engine/utils/crypto.py` and `utils/quality.py`~~ **Both audited post-deployment (2026-09-20): CLEAN.** `crypto.py` = AES-256-GCM + PBKDF2-HMAC-SHA256 (100k iterations, fresh random 16-byte salt per encryption — unlike the state-binding constant salt this one is fine) with `np.load(..., allow_pickle=False)` on the decrypt path (no pickle gadget) and uniform `CryptoError` wrapping that leaks no key material. `quality.py` = pure OpenCV/numpy math with epsilon-guarded divisions, no I/O, no secrets, no attack surface. Both remain unreferenced by live app paths (package export + own tests only). `assets/.face_encoding` byte content was partially mangled in transit (format identified structurally; values unrecoverable).

*Attack artifacts, PoC scripts, lab harnesses, screenshots, and full agent worklogs are preserved in the audit workspace for independent review.*
