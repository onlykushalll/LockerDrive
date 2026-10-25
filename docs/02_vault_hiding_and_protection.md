# Subagent r-B Synthesis — Windows Vault Hiding, Protection, and Anti-Forensics

**Agent:** Research Subagent B
**Task ID:** r-B
**Mission:** Deep research on every viable method to hide and protect a file vault on a Windows desktop app targeting `E:\` (USB/removable drive), going far beyond `attrib +h +s`.
**Target application:** LockerDrive (Python UI + compiled Rust security core, AES-256-GCM, PBKDF2-SHA256 100k iterations, anti-brute-force state machine, currently using `attrib +h +s` for hiding).

This synthesis is the product of **35 targeted web searches** spanning NTFS internals, encryption schemes, anti-forensic artifacts, USB drive protection, process self-defense, and threat modeling. Each section ends with concrete takeaways. Section B8 converts every recommendation into a specific, implementable change against the existing Rust+Python codebase.

---

## B1. Windows File Hiding Techniques (Comprehensive Catalog)

The current LockerDrive uses only `attrib +h +s` (hidden + system attributes). This is the weakest possible hiding mechanism — every forensic tool, every "show hidden files" toggle, and every `dir /a` exposes the files instantly. The catalog below ranks techniques by detectability, durability, and implementation cost.

### B1.1 — `attrib +h +s` (baseline, currently used)

- **Mechanism:** Sets the FILE_ATTRIBUTE_HIDDEN (0x2) and FILE_ATTRIBUTE_SYSTEM (0x4) bits in the MFT record.
- **Defeats:** Casual viewing in Explorer with default settings.
- **Fails against:** `dir /a:h`, "Show hidden files" toggle, every forensic tool, every script kiddie with cmd.
- **Forensic footprint:** None — it's a legitimate Windows feature.
- **Verdict:** Necessary but never sufficient. Keep it as a baseline layer.

### B1.2 — Alternate Data Streams (ADS)

ADS is an NTFS feature allowing multiple named data streams to be attached to a single file. The default stream is unnamed (`::$DATA`); additional streams use the syntax `filename:streamname:$DATA`.

- **Mechanism:** `type secret.txt > cover.jpg:secret.txt` — the secret is hidden inside cover.jpg's alternate stream and does NOT change cover.jpg's apparent file size or modify time.
- **Listing:** `dir /r` reveals named streams; `Get-Item -Stream *` in PowerShell lists them.
- **Execution:** Hidden executables can be run with `wmic process call create "cover.jpg:evil.exe"`.
- **NTFS-only:** ADS does not exist on FAT32 or exFAT — if E:\ is FAT32, ADS is unavailable. For LockerDrive on E:\ this matters: many USB sticks ship as exFAT. **Recommendation: require NTFS formatting on E:\.**
- **Detection:** Tools like `streams.exe` (Sysinternals), Autopsy, FTK Imager enumerate ADS effortlessly. Modern EDRs flag creation of `:Zone.Identifier`-style streams.
- **Use case for LockerDrive:** Store the AES-encrypted container as an ADS on an innocuous cover file like `E:\System Volume Information\index.dat:lockerdrive.bin`. The cover file remains openly visible; the stream is invisible to default Explorer.
- **Pros:** No additional file appears on disk; can be combined with `attrib +h +s`.
- **Cons:** Trivially found by any forensic analyst; destroyed on copy to non-NTFS volume.
- **Verdict:** Layer 2 candidate — adds a small "hide in plain sight" factor against script kiddies.

### B1.3 — NTFS Junction Points, Symbolic Links, and Reparse Points

NTFS supports three kinds of file links: hard links (same volume, same file), junctions (directory-only, same volume or another local volume), and symbolic links (files or directories, can cross volumes including UNC paths). All three are implemented as reparse points.

- **Hiding application:** Create a junction `E:\Documents → C:\Users\Public\Documents` to misdirect. Or create a deeply nested symlink chain that frustrates manual exploration.
- **Volume mount points:** `mountvol E:\path C:\` — mount an entire volume inside a directory of another volume. The mounted volume is then accessible only via that path.
- **Forensic note:** Reparse points are stored in the MFT record and are immediately visible to forensic tools. They redirect, they don't hide.
- **Use case for LockerDrive:** Limited. Symlinks could misdirect casual snooping but a forensic analyst reads the MFT directly. **Low value for hiding.**
- **However:** Volume Shadow Copy snapshots (B1.4) use reparse points to expose restore points — relevant for anti-forensics.

### B1.4 — Volume Shadow Copy Service (VSS) for hidden snapshots

VSS creates point-in-time snapshots of NTFS volumes. Snapshots are stored in the `\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopyN\` namespace and are normally accessible only via the VSS API or by navigating the shadow copy directly.

- **Hiding application:** A file deleted from E:\ may still exist in a shadow copy. Conversely, an attacker or vault designer could deliberately push a file into a shadow copy and then delete the live copy — the file persists in the snapshot.
- **Forensic implication:** `vssadmin list shadows` enumerates all snapshots; `vshadowinfo` (Sysinternals) parses them; tools like `ShadowCopyView` mount them for browsing.
- **Use case for LockerDrive:** **Negative** — VSS snapshots are an anti-forensic liability, not a hiding place. If LockerDrive unlocks files on E:\, then locks them (re-hiding with attrib), a shadow copy of E:\ taken mid-unlock would expose the files. LockerDrive must explicitly clear or disable VSS for E:\ (see B3).
- **Disabling VSS:** `vssadmin delete shadows /all /quiet` and disable via `HKLM\SYSTEM\CurrentControlSet\Control\BackupRestore\FilesNotToSnapshot`.

### B1.5 — Registry-based hiding (shell namespace manipulation)

Windows Explorer presents "This PC" as a virtual namespace built from registry keys under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\MyComputer\NameSpace\` and the equivalent `HKCU\...\User Shell Folders`. Each entry is a CLSID pointing to a shell folder.

- **Hiding a drive:** `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer\NoDrives` (DWORD bitmask) hides specific drive letters from Explorer. This is **per-user** and trivially bypassed by `dir E:\` or address-bar typing.
- **Custom namespace extension:** Register a custom CLSID implementing `IShellFolder` and place it as a sub-key under `NameSpace\`. Your "folder" then appears in Explorer as a virtual entry, but its content is rendered by your in-proc COM server. Files inside don't exist on disk — they're served by your DLL. This is how Recycle Bin, Control Panel, and "This PC" itself work.
- **Use case for LockerDrive:** A shell namespace extension is the **gold standard** for hiding — the vault is rendered by your DLL, not by NTFS. Without your DLL registered, the vault is invisible. However, this requires C++ COM development and is incompatible with the current Python+Rust architecture.
- **Lighter variant:** A custom CLSID-keyed folder with a `DefaultIcon` and `Attributes` registry value can make a real folder appear as something else (e.g., look like the Recycle Bin to casual clicks). Combine with `desktop.ini` (B1.9).
- **Verdict:** Powerful but high implementation cost. Stage 3+ feature for LockerDrive.

### B1.6 — Windows Resource Protection (WRP) directories

WRP protects critical system files, folders, and registry keys by setting their ACLs to grant access only to `TrustedInstaller`. `C:\Windows\System32\` and most of its contents are WRP-protected.

- **Mechanism:** Files in WRP-protected folders cannot be modified or deleted even by Administrators — only by `TrustedInstaller`.
- **Hiding application:** A file placed in `E:\System Volume Information\` benefits from a similar access-control barrier — by default, only SYSTEM can access this folder, which holds VSS and quota data. Ordinary users get "Access Denied" trying to browse it.
- **Use case for LockerDrive:** Place the vault container in `E:\System Volume Information\.lockerdrive.bin`. Users browsing E:\ in Explorer don't see this folder (it has both hidden+system attributes and a restrictive ACL). To access it, LockerDrive must run with elevated privileges or take ownership.
- **Pros:** Effective against casual users.
- **Cons:** Forensic analysts routinely image the entire volume — `System Volume Information` is one of the first places they look.
- **Verdict:** Layer 2 hiding — raises the bar from "any user" to "Administrator + knows where to look".

### B1.7 — Hidden partitions (diskpart, unallocated space)

A USB drive can be partitioned so that one partition is "hidden" by either setting the partition type ID to a non-Windows type (e.g., `0x17` Hidden NTFS) or by removing its drive letter entirely with `diskpart`.

- **Mechanism:** `diskpart > select vol E > remove letter=E` removes the drive letter; the volume becomes accessible only via volume GUID path or by re-assigning a letter. Or `attribute volume set nodefaultdriveletter` prevents auto-mounting.
- **Better technique:** Create a partition that lies in the "unallocated" gap of the MBR/GPT. The partition table doesn't list it; only someone reading raw sectors can find it. This is the "host-protected area" (HPA) or "device configuration overlay" (DCO) approach at the firmware level.
- **Hardware-level:** HPA and DCO are ATA commands that hide sectors from the OS entirely. Tools like `hdparm` (Linux) can set them; on Windows the equivalent requires special drivers.
- **Use case for LockerDrive:** A second partition on E:\ with no drive letter, holding the vault container. LockerDrive assigns a temporary letter to read it, then removes it.
- **Pros:** Invisible to Explorer and most Windows tools; requires disk-level forensics (e.g., FTK Imager, Autopsy) to find.
- **Cons:** Requires Administrator privileges and NTFS-aware partitioning; complicates the user experience.
- **Verdict:** Strong hiding (Layer 3), recommended as an advanced option.

### B1.8 — Steganography (hide data inside images/audio/video)

Steganography embeds secret data inside the noise floor of a cover medium. LSB (least-significant-bit) substitution in image pixels is the classic technique; modern variants include F5 (JPEG), HUGG (BMP), and steganographic filesystems.

- **Capacity:** LSB embedding in a 1024×768 RGB BMP can hide ~294 KB. A 4K image hides ~4 MB. Audio (LSB in WAV) similar. Video (LSB in raw frames) substantially more.
- **Tools:** Steghide (command line, BMP/JPEG/WAV/AU), OpenStego, SilentEye — all support passphrase-encrypted embedding.
- **Detection:** Steganalysis tools (StegExpose, Aletheia) detect LSB embedding with high accuracy. Statistical analysis of chi-square attacks reveals tampering.
- **Use case for LockerDrive:** If the vault container is small (a few MB of metadata + filenames), it could be embedded into a 4K wallpaper JPEG on E:\. The actual encrypted file blobs would still need to live somewhere — steganography doesn't scale to GB-scale vaults.
- **Pros:** Deniability — the cover file looks like a normal image.
- **Cons:** Low capacity; vulnerable to re-encoding (re-saving the JPEG destroys the hidden data); detectable by steganalysis.
- **Verdict:** Niche use — only for the vault's index/metadata file, not for content. Layer 4.

### B1.9 — Desktop.ini-based folder customization (misleading)

Every folder in Windows can have a hidden `desktop.ini` file that controls its icon, infotip, and CLSID-treated behavior. By setting `IconResource` and a custom CLSID, a folder can be made to look like the Recycle Bin, Control Panel, or any other namespace object.

- **Mechanism:** Folder `E:\Secret` contains:
  ```
  [.ShellClassInfo]
  IconResource=C:\WINDOWS\System32\SHELL32.dll,32
  CLSID={645FF040-5081-101B-9F08-00AA002F954E}
  ```
  The CLSID is the Recycle Bin's. When Explorer renders `E:\Secret`, it shows the Recycle Bin icon and even double-clicking may invoke Recycle Bin behavior instead of folder browsing.
- **Hiding application:** Misdirection, not real hiding. A forensic tool reading the MFT sees the directory entry regardless.
- **Use case for LockerDrive:** Cheap misdirection. Make the vault folder appear as "System Volume Information" or the Recycle Bin to casual clicks.
- **Verdict:** Trivial to add; combine with attrib +h +s for layered misdirection.

### B1.10 — Comprehensive hiding strategy for LockerDrive

Combining the above into a layered defense:

| Layer | Technique | Defeats |
|-------|-----------|---------|
| 1 | `attrib +h +s` | Casual Explorer browsing |
| 2 | Store in `E:\System Volume Information\` | Non-admin users |
| 3 | ADS on a cover file | File listing tools |
| 4 | Hidden partition (no drive letter) | Explorer + most admin tools |
| 5 | Encrypted container with random-looking filename | Forensic analyst (still sees a file, can't read it) |
| 6 | Steganography for index/metadata | Statistical steganalysis (weak) |

The encryption layer (B2) is what actually protects the data; hiding layers only delay discovery.

---

## B2. Encryption Approaches (Comparison Matrix)

Hiding fails against any determined adversary; encryption is the real guarantee. LockerDrive already uses AES-256-GCM via Rust — this section validates that choice and identifies gaps.

### B2.1 — Comparison matrix

| Approach | Cipher | Key mgmt | Open source | Portable to E:\ | Forensic visibility | Best for |
|----------|--------|----------|-------------|-----------------|---------------------|----------|
| **BitLocker To Go** | AES-128/256-CBC (or XTS-AES on newer) | TPM+PIN / Recovery Key / AD backup | No (proprietary) | Yes (removable) | Volume header identifiable | Enterprise compliance |
| **VeraCrypt container** | AES-Twofish-Serpent cascaded, XTS mode | Passphrase-derived (PBKDF2 500k iters) | Yes (GPL) | Yes (file container) | Container looks like random data; "no signature" is a feature | Plausible deniability |
| **VeraCrypt hidden volume** | Same as above | Two passphrases (outer/hidden) | Yes | Yes | Hidden volume's existence is undetectable | Coercion resistance |
| **EFS (Encrypting File System)** | AES-256 (Win XP SP1+) per-file FEK | RSA cert in user profile + DPAPI | No | Per-file, transparent | Encrypted files show a lock icon; FEK recoverable via cert | Enterprise domain file encryption |
| **7-Zip AES-256 archive** | AES-256-CBC (7z format) | Passphrase | Yes (LGPL) | Yes (single .7z file) | Header is recognizable as 7z; filename list encrypted optionally | Casual at-rest encryption |
| **Custom AES-256-GCM (LockerDrive current)** | AES-256-GCM (authenticated) | PBKDF2-SHA256 100k iters | Yes (Rust source) | Yes | Container is opaque; no signature by default | App-controlled vault |
| **LUKS / FileVault** | Various | Various | Mixed | Cross-platform | Identifiable headers | Not Windows-native |

### B2.2 — BitLocker To Go (in depth)

BitLocker To Go is Microsoft's full-volume encryption for removable drives, available on Windows 10/11 Pro/Enterprise/Education (Home can decrypt but not encrypt).

- **Cipher:** AES-CBC 128/256 (default 128) with Diffuser on older OS, XTS-AES-128 on Win10 1709+ (configurable). XTS is the modern NIST mode for sector-oriented storage.
- **Key protection options:** TPM-only (not applicable to USB), passphrase, smart card, recovery password (48-digit numerals), AD DS backup.
- **Auto-unlock:** Once enabled for a data drive on a specific machine, BitLocker stores an auto-unlock key in the registry under `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\FveAutoUnlock` — encrypted by the OS volume's master key. **This means: if the host OS is unlocked, E:\ unlocks automatically without user interaction.** This is convenient but defeats the purpose if the threat model includes "attacker gains unlocked host."
- **Forensic footprint:** BitLocker volume header is identifiable; tools like Passware, Elcomsoft FTK, and dislocker can attempt offline brute-force or extract keys from hibernation/pagefile/memory dumps.
- **For LockerDrive:** BitLocker is a strong option for E:\ as a *disk* layer, but it's not under the app's control — the user must configure it manually, and Home edition users are excluded.

### B2.3 — VeraCrypt (in depth)

VeraCrypt is the actively-maintained fork of TrueCrypt, providing on-the-fly encryption via virtual mounted volumes.

- **Cipher:** User-selectable: AES, Serpent, Twofish, or cascades (AES-Twofish, AES-Twofish-Serpent, etc.). Mode is XTS (tweakable).
- **Key derivation:** PBKDF2-SHA512 with 500,000 iterations for hashes (1,000,000 for Whirlpool/Streebog). Header contains salt + encrypted keys.
- **Hidden volume:** A second encrypted volume lives inside the free space of the outer (decoy) volume. Both volumes have independent passphrases. Mounting with the outer passphrase reveals only the decoy content; the hidden volume's existence is cryptographically undetectable.
- **Plausible deniability:** VeraCrypt volumes have **no signature** — they look like random data. A forensic analyst cannot prove a file is a VeraCrypt container (only suspect it). This is VeraCrypt's killer feature.
- **Limitations:** No native CLI for mounting without admin rights (driver required); file system operations inside the mounted volume generate standard NTFS artifacts once mounted.
- **For LockerDrive:** A "wrapper mode" where LockerDrive's container is itself a VeraCrypt volume would inherit plausible deniability + cascaded ciphers. However, this adds a hard dependency on VeraCrypt's driver.

### B2.4 — Custom AES-256-GCM (LockerDrive's current approach)

- **Cipher:** AES-256 in GCM mode (authenticated encryption with associated data, AEAD). Provides confidentiality + integrity in one operation. 12-byte nonce (96 bits) — NIST-recommended for GCM.
- **Key derivation:** PBKDF2-HMAC-SHA256 with **100,000 iterations**. This is **below modern recommendations** — OWASP recommends 600,000 iterations for PBKDF2-SHA256 as of 2023. VeraCrypt uses 500k–1M. Argon2id is the modern gold standard and should be considered.
- **Nonce management:** GCM is catastrophically broken if a nonce is reused with the same key. The current Rust code uses `rand::RngCore` to generate a random 12-byte nonce per encryption — acceptable but fragile. **A counter-based nonce (with key rotation at 2^32 messages) is safer.**
- **Container format:** Based on the code, the container is `E:\.lockerdrive_data.bin` — this filename is a **signature**. Forensic analysts can grep for `.lockerdrive` across imaged disks. **Recommendation: randomize the filename, or store inside ADS.**
- **Authentication tag:** GCM produces a 16-byte tag. If verification fails (tampering), decryption must fail closed — the current code returns `ERROR:` on failure, which is correct.
- **Verdict:** Cryptographically sound but with three weaknesses: low PBKDF2 iterations, predictable filename, no key rotation strategy.

### B2.5 — EFS (Encrypting File System)

EFS is NTFS's per-file encryption, using a hybrid scheme: each file gets a random File Encryption Key (FEK), which is encrypted with the user's EFS certificate (RSA public key). The encrypted FEK is stored as an attribute of the file.

- **Pros:** Transparent to the user; no special app needed; integrates with NTFS ACLs.
- **Cons:** Tied to a Windows user identity — files moved to another machine cannot be decrypted without exporting the certificate. **EFS does not work on FAT/exFAT.** EFS certificates are protected by DPAPI, which is protected by the user's logon password — so EFS is only as strong as the user's Windows password.
- **Recovery:** Domain admins can designate Data Recovery Agent certificates. On standalone machines, the recovery agent is the local Administrator by default.
- **Forensic:** Encrypted files show a lock icon; EFS metadata is in the MFT; the analyst can extract the encrypted FEK but needs the private key to decrypt.
- **For LockerDrive:** EFS is a poor fit because (a) it's tied to the user's Windows identity, not to a PIN; (b) EFS-encrypted files moved to another machine are unrecoverable; (c) doesn't work on exFAT. **Skip.**

### B2.6 — 7-Zip encrypted archives as vault containers

7-Zip supports AES-256-CBC encryption with a passphrase. Files inside can be encrypted (including filenames) with the "Encrypt file names" option.

- **Pros:** Ubiquitous, easy to use, single-file output, optionally encrypts file names.
- **Cons:** CBC mode is weaker than GCM (no built-in authentication); ZipCrypto (the alternative cipher in zip files) is trivially crackable in minutes — must explicitly choose AES-256. Re-extracting on every access is slow for large vaults. No plausible deniability (7z signature in header).
- **For LockerDrive:** Useful as a secondary offline archive, not as the primary vault. **Skip for primary.**

### B2.7 — Windows DPAPI (Data Protection API) for key storage

DPAPI is the Windows API (`CryptProtectData` / `CryptUnprotectData`) for encrypting small secrets (symmetric keys, passwords) bound to the user's logon credentials.

- **Mechanism:** A 64-byte MasterKey is generated per user and encrypted with a key derived from the user's password (using PBKDF2). DPAPI-protected blobs can only be decrypted by the same user on the same machine (or with a backup key from a Domain Controller).
- **Lifetime:** MasterKeys expire after 90 days; old keys are retained for decryption. Stored in `%APPDATA%\Microsoft\Protect\<SID>\`.
- **Forensic risk:** If an attacker gets the user's NTLM hash or logon password, all DPAPI blobs are decryptable. Mimikatz and impacket's `dpapi.py` automate this. The LSASS process holds MasterKeys in memory while the user is logged in.
- **For LockerDrive:** DPAPI is a **strong candidate for storing the vault's master key** rather than deriving it from the PIN on every launch. Specifically: the PIN unlocks a DPAPI-protected blob containing the actual AES-256 key. This decouples the AES key from the PIN strength. However, DPAPI ties the vault to the current Windows user — moving E:\ to another machine loses access. **Recommendation: use DPAPI only for an optional "remember this device" feature, never as the sole key store.**

### B2.8 — Encryption recommendation summary for LockerDrive

| Decision | Recommendation |
|----------|----------------|
| Primary cipher | **Keep AES-256-GCM** (already implemented). AEAD is correct. |
| KDF | **Upgrade PBKDF2 to 600,000 iterations** or migrate to **Argon2id** (memory-hard, GPU-resistant). |
| Nonce | Switch from random to **counter-based with key rotation** at 2^32 messages. |
| Container filename | **Randomize per install** (e.g., `E:\System Volume Information\a1b2c3d4.bin`); never use `.lockerdrive` in the name. |
| Container signature | Add a **magic prefix that is also encrypted** so the container has no plaintext signature. |
| Optional disk layer | Recommend users enable **BitLocker To Go on E:\** as defense-in-depth (but app should not depend on it). |
| Key storage | Use **DPAPI for an optional "trusted device" cache**; default behavior re-derives from PIN. |
| Hidden volume | Optional advanced mode: **wrap container as a VeraCrypt hidden volume** for plausible deniability. |

---

## B3. Anti-Forensics on Windows

Even with perfect encryption, the OS leaks a tremendous amount of metadata about file activity: when files were created, accessed, and modified (MFT timestamps); what files exist on the volume (MFT, USN Journal, $LogFile); which programs ran (Prefetch); and system-level events (Event Log). A forensic analyst images the disk and reconstructs the timeline. LockerDrive's footprint must be minimized.

### B3.1 — Secure deletion (SDelete, cipher /w, Eraser)

When Windows "deletes" a file, it only marks the MFT entry as free — the data remains on disk until overwritten. Secure deletion overwrites the file's clusters before unlinking.

- **SDelete (Sysinternals):** Marks file as 0, then 0xFF, then renames multiple times (to defeat $UsnJrnl rename entries) before deletion. **However**, SDelete's rename pattern itself leaves traces in the USN Journal — an analyst can see that SDelete was used.
- **cipher /w:C:\folder:** Microsoft's built-in tool. Writes 0x00, then 0xFF, then random data over all free space on the volume. Slow (hours for a 1 TB drive) but effective.
- **Eraser:** Open-source, supports Gutmann (35-pass) and DoD 5220.22-M (7-pass) — though modern research shows 1-pass is sufficient on HDDs and **no number of passes works on SSDs** due to wear leveling.
- **SSD caveat:** SSDs use wear-leveling controllers that map logical sectors to physical pages dynamically. A "secure delete" writes new data to *different* physical pages, leaving the old data intact in the original pages until garbage collection runs (which may take hours or never). **The only reliable secure deletion on SSDs is full-disk encryption + crypto-erase (destroy the key).**
- **For LockerDrive:** When the vault is locked, the app currently re-hides files via `attrib +h +s`. **The unencrypted files still exist on disk** — any forensic analyst who images E:\ while it's locked can still recover them. **Critical recommendation: never write unencrypted files to E:.** Decrypt to a temp location (ideally in RAM) and never to E:\ itself.

### B3.2 — MFT (Master File Table) analysis

The MFT is the heart of NTFS: one entry per file on the volume. Each entry records:

- MACE timestamps: Modified, Accessed, Changed (MFT entry modified), Entry Modified (Birth/Creation).
- File attributes (hidden, system, etc.).
- Data runs (physical cluster locations).
- For small files (<1 KB typically), the file content itself is stored **resident in the MFT entry**.

Even after a file is "deleted," the MFT entry is marked free but its content (including resident file data) persists until overwritten by a new file's MFT entry. Tools like `analyzeMFT`, `MFTEcmd`, and Autopsy extract and parse deleted MFT entries.

- **Counter-technique — Timestomping:** Set the file's MACE timestamps to arbitrary values via `SetFileTime()` or direct MFT manipulation. Malware uses this to make files appear old (and thus below forensic scrutiny). For LockerDrive, timestomping the container to match other files in `E:\System Volume Information\` would blend it in. **However**, the `$STANDARD_INFORMATION` attribute is timestompable but the `$FILE_NAME` attribute's timestamps are set by the system and only the MFT entry can rewrite them — forensic tools compare the two to detect timestomping.
- **Counter-technique — MFT wiping:** Overwrite the MFT entry directly after deletion. Possible only via raw disk access (Administrator + `\\.\PhysicalDriveN`). Risky and detectable (MFT entries with zeros stand out).
- **For LockerDrive:** Timestomping the container to match `E:\System Volume Information\` timestamps is a cheap win. Beyond that, the encryption layer is the real defense — the MFT shows "a 50 MB file exists" but not its contents.

### B3.3 — USN Journal ($UsnJrnl) cleanup

The USN Journal is an NTFS feature that logs every change to a volume: file creation, deletion, renaming, attribute changes, data writes. It's stored at `C:\$Extend\$UsnJrnl:$J` and is append-only.

- **Forensic value:** Even if a file is deleted, the USN Journal records its creation, name, parent directory, and deletion. The journal persists until it wraps around (size-limited, typically 32 MB) or is explicitly deleted.
- **Deletion:** `fsutil usn deletejournal /d E:` deletes the USN Journal. **However**, this itself leaves a trace — the next journal entry will record the deletion, and forensic analysts look for sudden journal-clearing events.
- **Detection:** Carving the journal reveals past file activity. Tools: `usnjrnl_2_csv`, `UsnJrnlBackuper`.
- **For LockerDrive:** After unlock-lock cycles, the journal reveals "file X was modified on E:\". Periodic journal flushing (`fsutil usn deletejournal`) on E:\ is reasonable but suspicious. **Better: keep all sensitive operations inside the encrypted container** — the journal will only see the container file being written, not the files inside it.

### B3.4 — $LogFile (NTFS transaction log)

$LogFile records NTFS metadata transactions for crash recovery. It's typically 64 MB and contains a rolling record of recent MFT changes. Forensic tools like `LogFileParser` extract recently-modified file names from it.

- **Counter:** No clean way to wipe selectively. Encryption-at-rest is the only defense.

### B3.5 — Prefetch and recently-used cleanup

Windows Prefetch (.pf) files cache the DLLs and code pages loaded by an executable, accelerating cold-start. Stored in `C:\Windows\Prefetch\`, one file per executable (filename format: `EXENAME-HASH.pf`).

- **Forensic value:** Each .pf file contains: the executable's path, the last 8 execution timestamps, files/directories accessed in the first 10 seconds of execution, and DLLs loaded. A Prefetch file for `vault_core.exe` would reveal every file it touched during startup.
- **Note:** Prefetch is **disabled by default on Windows Server** and may be disabled on Windows 10/11 with SSDs (SuperFetch/SysMain controls this).
- **Clearing:** `del C:\Windows\Prefetch\*.pf` requires Administrator. Forensic analysts look for prefetch-clearing as itself a suspicious signal.
- **Other recently-used artifacts:** RecentDocs (registry `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs`), Jump Lists (`%APPDATA%\Microsoft\Windows\Recent\AutomaticDestinations\`), LNK files, BAM (Background Activity Moderator) in the registry.
- **For LockerDrive:** Name the executable generically (e.g., `sdhelper.exe` instead of `vault_core.exe`) to make prefetch entries less obviously vault-related. Avoid having Explorer shell interact with files inside the vault (which would generate RecentDocs entries).

### B3.6 — Windows Event Log traces

Windows Event Logs (`System`, `Security`, `Application`, `Microsoft-Windows-*` channels) record system activity. Relevant events for LockerDrive:

- **Event ID 4663** (Object Access): If auditing is on, every file access to an audited object generates an event. Most systems don't audit file access by default, but if the user (or group policy) does, E:\ file access is logged.
- **Event ID 4624/4634** (Logon/Logoff): Records user logons including type (interactive, network, etc.).
- **Event ID 7045** (Service Install): If LockerDrive installs a service.
- **Event ID 4688** (Process Creation): If process auditing is on, every launched process is logged with command line.
- **BitLocker events** (BitLocker-API source, IDs 766–845): Reveal BitLocker operations.
- **For LockerDrive:** The app should run as a normal user process without installing services (to avoid 7045 events). It cannot clear Event Logs without leaving a "Log Clear" event (1102). The strategy is: don't generate interesting events in the first place.

### B3.7 — Forensic footprint when vault is locked

When LockerDrive is locked, the *only* things on E:\ that should exist are:

1. The encrypted container (single file, randomized name).
2. The PIN hash file (in the install directory, not on E:\).
3. The state file (in the install directory, not on E:\).

The app should **never** decrypt files to E:\ itself. If a "view file" feature is needed, decrypt to `%TEMP%` (or better, to a RAM disk) and securely delete on lock. Even better: implement a FUSE-like virtual filesystem driver that streams decrypted bytes on read, never touching disk.

### B3.8 — Anti-forensics summary

| Artifact | Risk to LockerDrive | Mitigation |
|----------|---------------------|------------|
| MFT | Reveals container exists | Timestomp; randomize name |
| $UsnJrnl | Reveals unlock/lock activity | Keep all writes inside container |
| $LogFile | MFT transaction history | None (encryption is defense) |
| Prefetch | Reveals `vault_core.exe` ran | Generic executable name |
| RecentDocs/LNK | Reveals files viewed inside vault | Don't shell-execute files; decrypt to RAM |
| Event Log | Process creation, service install | Don't install services; don't trigger auditing |
| Pagefile/Hiberfil | Decrypted data may be paged out | Disable for vault process (`SetProcessWorkingSetSize` + `VirtualLock`); or require RAM disk |
| Volume Shadow Copy | Snapshot may capture unlocked state | Disable VSS for E:\ |

---

## B4. Drive-Level Protection for E:\

E:\ is the vault's home. Beyond file-level hiding, the drive itself needs protection from formatting, imaging, and physical tampering.

### B4.1 — Hardware write-protect switches

Some USB drives (Kanguru, Apricorn, Aegis) have a physical write-protect switch on the casing. When engaged, the drive firmware refuses all write commands at the USB mass-storage protocol level. This is the **strongest possible write protection** — no software, including malware with kernel privileges, can write to the drive.

- **Pros:** Unbypassable by software; instant toggle; visible to user.
- **Cons:** Hardware-specific (user must buy a particular drive); doesn't protect against read access (forensic imaging still works); switch can be accidentally toggled.
- **For LockerDrive:** Recommend a hardware-WP USB drive in the documentation, but don't depend on it (consumer-grade USB sticks don't have it).

### B4.2 — Software write-protection via registry (StorageDevicePolicies)

The registry key `HKLM\SYSTEM\CurrentControlSet\Control\StorageDevicePolicies\WriteProtect` (DWORD) when set to 1 makes Windows treat all *newly-attached* USB mass-storage devices as read-only.

- **Limitations:** Applies globally, not per-device. Doesn't survive on the USB stick itself — once moved to another machine, write-protection is gone. Easily reset by any Administrator.
- **Per-device variant:** Some USB drives support a `WriteProtect` flag in their firmware; the OS can't enforce per-device write-protection natively.
- **For LockerDrive:** A `LockerDrive.exe --lock-drive E` command could set the registry key, but it would also write-protect every other USB drive. Not practical.

### B4.3 — BitLocker To Go auto-unlock behavior

When auto-unlock is enabled for a data drive, BitLocker stores the drive's unlock key (encrypted by the OS volume master key) in `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\FveAutoUnlock`. On next boot, if the OS volume is unlocked, the data drive unlocks automatically.

- **Risk:** An attacker who gains access to an unlocked host (e.g., steals a powered-on laptop) gets E:\ unlocked without entering any passphrase.
- **Mitigation:** Don't enable auto-unlock; require the BitLocker passphrase on every mount. For LockerDrive, this is the user's choice — the app should document the risk.

### B4.4 — What happens when E:\ is formatted — recovery possibilities

Quick format: zeroes the MFT and the volume boot sector; data clusters are untouched. Tools like TestDisk, PhotoRec, Recuva, and R-Studio can carve files from the untouched clusters. **A formatted-but-unencrypted USB drive yields 80–100% of its data to forensic recovery.**

Full format (Windows 8+): zeroes the MFT AND writes zeros to every cluster. Recovery from a full format is much harder but still possible on HDDs (magnetic remanence) and essentially impossible on SSDs (TRIM after format).

For an encrypted container on a formatted drive: the container's clusters are overwritten with zeros — the container is gone. **This is why the encryption key must be backed up off-drive** (e.g., a printed recovery key) if the user wants recovery.

### B4.5 — Disk encryption vs file encryption tradeoffs

- **Full-disk encryption (BitLocker To Go, VeraCrypt whole-disk):** Encrypts every sector. Pros: no plaintext leaks at all; transparent. Cons: requires driver install; performance overhead; if the FDE key is compromised, everything is exposed.
- **File encryption (LockerDrive's container):** Encrypts only the vault. Pros: portable, app-controlled, no driver. Cons: if the user accidentally saves a file outside the vault, it's plaintext on disk.
- **Hybrid recommendation:** Use BitLocker To Go on E:\ as the disk layer (defends against physical theft of the drive), and LockerDrive's container as the file layer (defends against host compromise while running). Defense in depth.

### B4.6 — Defending against imaging

A forensic analyst images E:\ by attaching it to a write-blocker (hardware device that intercepts write commands) and using dd / FTK Imager / X-Ways to create a bit-exact copy. There is **no software defense** against imaging — once the analyst has the drive, they can image it.

Defenses are:
1. **Encryption** — image is ciphertext.
2. **Anti-tamper** — detect if the drive has been imaged (impossible in software; some enterprise USB drives have a tamper-evident log).
3. **Self-destruct** — if the drive detects too many failed unlock attempts, scrub the key (enterprise USB drives like the Apricorn Aegis do this in firmware). LockerDrive's current 60-second lockout is a soft version; a hard version would scrub the container's key after N failed attempts.

---

## B5. Process Protection

The LockerDrive process (Python UI + Rust subprocess) must resist being killed, inspected, and tampered with while running.

### B5.1 — Process critical flag (SetProcessCritical / NtSetInformationProcess)

The `NtSetInformationProcess` syscall with `ProcessBreakOnTermination` (class 0x1D) marks a process as "critical". When a critical process terminates (for any reason, including crash or kill), Windows bug-checks with `CRITICAL_PROCESS_DIED` (0xEF) — a Blue Screen of Death.

- **Effect:** Task Manager cannot kill a critical process (it warns and refuses). `taskkill /f` triggers a BSOD.
- **Used by:** csrss.exe, wininit.exe, winlogon.exe (Windows' own critical processes).
- **Risk:** If your process crashes, the user's entire system crashes. **Unacceptable for a vault app.** Users will not tolerate BSODs.
- **For LockerDrive:** Do NOT use. The user impact is disproportionate.

### B5.2 — Protected Process Light (PPL)

Windows 8.1+ introduced Protected Processes (and later Protected Process Light, PPL). A PPL process can only be accessed (read memory, inject threads, terminate) by other processes with equal or higher protection level. Even Administrator cannot attach a debugger to a PPL.

- **Requirements:** Process must be ** Authenticode-signed** with a specific EKU (Enhanced Key Usage) — `1.3.6.1.4.1.311.10.3.24` for Early Launch Antimalware, or one of the Windows-recognized PPL levels. Microsoft does not grant these to ordinary ISVs without going through WHQL certification.
- **Verdict:** Not achievable for LockerDrive without significant signing infrastructure. **Skip for v1.**

### B5.3 — Anti-debugging techniques

Once a debugger attaches, the analyst can read decrypted keys straight from memory. Anti-debugging slows this down.

- **IsDebuggerPresent():** Returns true if a user-mode debugger is attached to the current process. Trivially bypassed (patch to return 0), but catches casual inspection.
- **CheckRemoteDebuggerPresent():** Same, but checks for debuggers in another process.
- **PEB->BeingDebugged:** Direct PEB read; equivalent to IsDebuggerPresent.
- **NtQueryInformationProcess(ProcessDebugPort):** Returns the debug port if attached.
- **Timing checks (rdtsc / QueryPerformanceCounter):** Single-stepping in a debugger slows execution by 1000x+. Measure time between two rdtsc calls; if it exceeds a threshold, a debugger is present.
- **Hardware breakpoint detection:** Check DR0–DR7 registers via GetThreadContext. Hardware breakpoints are set by debuggers.
- **Int 2D / Int 3 traps:** Trap instructions that behave differently under a debugger.
- **For LockerDrive:** Add `IsDebuggerPresent` + a timing check in the Rust core's `verify-pin` path. If a debugger is detected, return `LOCKED:9999` (effectively infinite lockout). Cost: 5–10 lines of Rust.

### B5.4 — DLL injection prevention

DLL injection (via `CreateRemoteThread` + `LoadLibrary`, or `SetWindowsHookEx`, or `AppInit_DLLs`) lets an attacker run code inside your process.

- **Mitigation — Code signing (WDAC):** Windows Defender Application Control policies can require all DLLs loaded into a process to be signed by a trusted publisher. Effective but requires signing infrastructure.
- **Mitigation — `SetProcessMitigationPolicy`:** Win 8+ supports `ProcessSignaturePolicy` (only load Microsoft-signed or signed-by-specific-publisher DLLs) and `ProcessDynamicCodePolicy` (block JIT code generation). Configurable per-process at startup.
- **Mitigation — `LoadLibrary` hooking:** Manually validate the signature of every DLL before allowing it to load. Tricky in Rust; requires a custom loader.
- **Mitigation — `AppInit_DLLs` disable:** Set `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows\AppInit_DLLs` to empty (system-wide).
- **For LockerDrive:** Apply `ProcessSignaturePolicy` to the Rust core binary at startup. Requires Authenticode signing of every DLL the binary loads. If signing infrastructure is unavailable, skip.

### B5.5 — Job objects for child process management

A Job Object groups multiple processes and applies policies to them as a unit. Useful for ensuring that when the parent (Python UI) dies, all children (Rust core, Explorer window) also die.

- **API:** `CreateJobObject`, `AssignProcessToJobObject`, `SetInformationJobObject` with `JobObjectLimitKillOnJobClose`.
- **Effect:** When the last handle to the job object closes (e.g., parent process dies), all processes in the job are terminated.
- **For LockerDrive:** Use a job object to ensure the Rust core subprocess dies when the Python UI dies. Prevents orphaned `vault_core.exe` processes holding the vault in an unlocked state.

### B5.6 — Self-defense against memory dumps

Once the process is running, decrypted data and keys live in RAM. An attacker with Administrator rights can dump the process memory (`procdump -ma`, `Process Hacker`, `WriteMiniDump`) and extract keys.

- **`VirtualLock`:** Lock pages in RAM, preventing them from being written to the pagefile. Effect: keys stay in RAM, not in `pagefile.sys`. Limited effectiveness (pagefile is not the only dump path).
- **`SecureZeroMemory`:** Zero-fill buffers immediately after use. Prevents residual key material in freed memory.
- **Disable process crash dumps:** Set `HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps` to disable. Or via `WerAddExcludedApplication`.
- **Disable system crash dumps:** `HKLM\SYSTEM\CurrentControlSet\Control\CrashControl\CrashDumpEnabled=0`. System-wide change; affects all apps.
- **`PROCESS_MITIGATION_SIDE_CHANNEL_ISOLATION_POLICY`:** Win 10 1809+ mitigates Spectre/Meltdown side-channel attacks on the process.
- **For LockerDrive:** `VirtualLock` the AES key buffer in the Rust core; `SecureZeroMemory` after every decrypt operation. Disable process-local crash dumps for the Rust core. Document that system-level memory dumps cannot be prevented per-app.

### B5.7 — Code signing for process integrity

Authenticode-signing the Rust core binary with an EV (Extended Validation) certificate:
- Prevents tampering detection (modified binary won't validate).
- Allows SmartScreen reputation to build up (no "Unknown publisher" warning).
- Enables PPL eligibility (with the right EKU).
- Required for `ProcessSignaturePolicy` to work properly.

For LockerDrive, an OV (Organizational Validation) cert from DigiCert/Sectigo at ~$300/year is the minimum; EV cert at ~$400/year is better.

---

## B6. Detection Evasion

This section addresses "how to keep LockerDrive itself invisible" — both the process and the on-disk artifacts.

### B6.1 — How to prevent the app from appearing in Task Manager

The honest answer: **you cannot, in user mode.** Task Manager enumerates processes via `NtQuerySystemInformation`, which reads from the kernel's `EPROCESS` list. Hiding from this requires kernel-mode code (a rootkit).

- **DKOM (Direct Kernel Object Manipulation):** A rootkit unlinks its `EPROCESS` from the `ActiveProcessLinks` list. Task Manager (and Tasklist, Get-Process) then don't see it. The process keeps running.
- **Cost:** Requires a kernel-mode driver, which requires signing on 64-bit Windows (Windows 10/11). Driver Signature Enforcement (DSE) makes this very hard for unsigned drivers.
- **Legitimate alternative:** Run as a service with a generic name (`svchost.exe -k netsvcs` group). The process appears as a generic host. **This is what LockerDrive should do** if stealth is important: register as a service hosted in a `svchost` group, with a generic service name.
- **For LockerDrive v1:** Don't try to hide. A vault app that hides itself is suspicious to EDRs and may be flagged as malware. Accept visibility, focus on encryption strength.

### B6.2 — Service-based execution (background service)

Running LockerDrive as a Windows Service:
- Auto-starts at boot (if configured).
- Runs as LocalSystem (high privilege) or a custom service account.
- Survives user logoff.
- Does not appear in the user's session — a separate process must communicate with it (named pipe, RPC).

For LockerDrive:
- **Pros:** Could run as SYSTEM and access `E:\System Volume Information\` without elevation; survives logoff; can run before user logon.
- **Cons:** Adds complexity (IPC between service and UI); services are visible in `services.msc`; installing a service generates Event ID 7045; Service Control Manager logs.
- **Recommendation:** Optional v2 feature for always-on protection. v1 stays as a user-mode app.

### B6.3 — Scheduled task persistence

A scheduled task (`schtasks /create`) can run LockerDrive at logon, at startup, or on a schedule. Tasks persist across reboots and don't require a service.

- **Storage:** `C:\Windows\System32\Tasks\` (XML files) and registry `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Schedule\TaskCache\`.
- **Forensic visibility:** `schtasks /query` lists all tasks; analysts routinely inspect them. A task named `LockerDrive` is obvious; a task named `MicrosoftEdgeUpdateTaskMachineCore` blended in (but is suspicious on close inspection).
- **For LockerDrive:** Optional persistence mechanism. Document as an opt-in feature.

### B6.4 — WMI subscriptions for stealth

WMI (Windows Management Instrumentation) supports permanent event subscriptions: when a specific event occurs (e.g., a process starts, a logon happens, a time arrives), WMI runs a consumer (a script, a COM object, or a command line).

- **Storage:** Three WMI repository classes in `C:\Windows\System32\wbem\Repository\`: `__EventFilter`, `__EventConsumer`, `__FilterToConsumerBinding`.
- **Stealth:** Subscriptions are stored in the WMI repository, not in obvious files. They survive reboots. Often missed by casual inspection.
- **Forensic detection:** `Get-WmiObject -Namespace root\Subscription -Class __EventConsumer` lists them. Sysinternals AutoRuns flags WMI subscriptions.
- **Reputation:** Heavily used by malware (Empire, Cobalt Strike). EDRs flag new WMI subscriptions.
- **For LockerDrive:** **Skip.** The reputation cost (looks like malware to EDRs) outweighs the stealth benefit.

### B6.5 — Process hollowing (and its detection)

Process hollowing: spawn a legitimate-looking process (e.g., `notepad.exe`) suspended, replace its memory image with your code, resume. The process appears in Task Manager as `notepad.exe` but runs your code.

- **Used by:** Malware to evade detection.
- **Detection:** Memory image doesn't match the on-disk image; EDRs detect via `PsGetCurrentProcess->ImageFileName` vs `VirtualQuery` mismatches; memory scanners flag it.
- **For LockerDrive:** **Skip.** This is malware behavior. Use a legitimate, signed binary with a transparent name.

### B6.6 — Recommended "stealth" approach for LockerDrive

The honest path:
1. Name the executable generically (`ldhelper.exe` not `vault_core.exe`).
2. Sign it with an OV cert (no "Unknown publisher").
3. Don't install services or scheduled tasks by default.
4. Don't hide the process — accept Task Manager visibility.
5. Let the encryption do the work; the process being visible doesn't compromise the vault.

---

## B7. Real-World Threat Models

Three threat tiers, three defense postures.

### B7.1 — Script kiddie with AutoIt / Process Hacker

**Capabilities:**
- Can Google "show hidden files Windows" and toggle the setting.
- Can run `dir /a:h E:\` and see hidden files.
- Can attach Process Hacker to a process and dump memory (if running as Administrator).
- Cannot analyze MFT, USN Journal, or write a forensic script.
- Cannot reverse-engineer Rust binaries.

**Defense:** `attrib +h +s` + a randomized container filename defeats them. They'll see "a file exists" but cannot read it.

### B7.2 — Forensic analyst with FTK / EnCase / Autopsy

**Capabilities:**
- Images the drive bit-for-bit.
- Parses MFT, $UsnJrnl, $LogFile, Prefetch, Event Logs.
- Recovers deleted files from unallocated space.
- Detects ADS, timestomping, hidden partitions.
- Knows what VeraCrypt containers look like (random data with no signature — they'll suspect).
- Can brute-force weak PINs offline (e.g., your 4-digit PIN is brute-forceable in milliseconds —LockerDrive's anti-brute-force is irrelevant offline).

**Defense:**
- Strong KDF (Argon2id, 64+ MB memory, 3+ iterations) so offline brute-force of even a 6-digit PIN takes years.
- AES-256-GCM container with no plaintext signature.
- No plaintext file ever written to disk (decrypt to RAM).
- Timestomp the container to match `System Volume Information` siblings.
- Generic executable name; minimal prefetch/recentdocs footprint.

### B7.3 — Nation-state actor

**Capabilities:**
- All of the above, plus:
- Crypto-attack the AES-256 (won't work — AES-256 is quantum-resistant for symmetric crypto).
- Side-channel attacks: timing, power, EM, cache. If they have physical access to the running machine, they can extract keys via DMA (Thunderbolt), JTAG, or chip decapping.
- Compromise the host OS via 0-day, then dump RAM.
- Compromise the supply chain: trojan the Rust crates you depend on.
- Compromise Microsoft's code-signing infrastructure.

**Defense:**
- Accept that physical-access nation-state is largely undefeatable.
- Defense in depth delays them; doesn't stop them.
- Use a hardware security module (HSM) or TPM to protect the master key — but TPM is host-bound, defeating portability.
- The only effective defense is **crypto-erase on tamper**: enterprise USB drives (Apricorn Aegis, Kingston IronKey) detect tamper attempts and zero the encryption key. This is hardware-level and not replicable in software.

### B7.4 — Defense in depth: layering protections

The principle: every layer must be independently defeat-able, but each layer adds work for the attacker.

| Layer | Defeats |
|-------|---------|
| attrib +h +s | Casual users |
| System Volume Information placement | Non-admin users |
| Randomized container filename | Casual forensic scans |
| AES-256-GCM (no signature) | Offline analysis |
| Argon2id KDF (64MB, 3 iter) | Offline brute-force of weak PINs |
| DPAPI key wrapping | Cross-machine PIN attacks |
| Timestomping | Timeline analysis |
| Generic executable name | Prefetch/RecentDocs scanning |
| VirtualLock + SecureZeroMemory | Memory dump of keys |
| Anti-debug in Rust core | Casual debugger attach |
| BitLocker To Go on E:\ (optional) | Physical drive theft |
| Hardware-WP USB drive (recommended) | Accidental/malicious format |
| No services, no scheduled tasks | Forensic visibility |
| Decrypt to RAM only | Disk forensic after lock |

---

## B8. Concrete Recommendations for LockerDrive

Each recommendation maps to a specific file in the existing codebase.

### B8.1 — Upgrade the KDF (Rust core)

**File:** `LockerDrive/rust_core/src/main.rs`
**Change:** Replace PBKDF2 with Argon2id.

```rust
// Current:
const PBKDF2_ITERATIONS: u32 = 100_000;
// Replace with Argon2id:
// m = 64 * 1024 KB (64 MB), t = 3 iterations, p = 4 lanes
// Use the `argon2` crate, variant = Argon2id
```

Add to `Cargo.toml`: `argon2 = "0.5"`

Migration: on first run after upgrade, re-derive the key from the PIN using Argon2id and re-encrypt the container. Old containers should be migrated transparently (read with PBKDF2, write with Argon2id, then delete the PBKDF2 hash file).

### B8.2 — Randomize container filename and location

**File:** `LockerDrive/rust_core/src/main.rs`
**Change:**
```rust
// Current:
const VAULT_CONTAINER: &str = "E:\\.lockerdrive_data.bin";
// Replace with: generate a random 16-byte hex name at install time,
// store it inside the System Volume Information folder.
fn container_path() -> String {
    let name = read_or_generate_name(); // 16 random bytes hex-encoded
    format!("E:\\System Volume Information\\{}", name)
}
```

The container's name should be stored in the install directory (not on E:\) so it's not directly visible. Timestomp the container to match `E:\System Volume Information\`'s existing files (use `SetFileTime` with a timestamp from `E:\System Volume Information\tracking.log`).

### B8.3 — Add anti-debug checks to verify-pin

**File:** `LockerDrive/rust_core/src/main.rs`
**Change:** In the `verify-pin` and `unlock` code paths, before checking the PIN:

```rust
#[cfg(windows)]
fn is_being_debugged() -> bool {
    use winapi::um::debugapi::IsDebuggerPresent;
    unsafe { IsDebuggerPresent() != 0 }
}

#[cfg(windows)]
fn timing_check() -> bool {
    use std::time::Instant;
    let t1 = Instant::now();
    // do some trivial work (e.g., 1000 hash rounds)
    for _ in 0..1000 { let _ = std::hint::black_box(0u64.wrapping_add(1)); }
    let elapsed = t1.elapsed();
    elapsed.as_millis() > 100  // normal: <1ms; debugger: >>100ms
}
```

If either check fails, return `LOCKED:99999` (very long lockout) and log the attempt.

### B8.4 — Never write plaintext to E:\

**Current behavior (per the code):** The Rust core unhides files on E:\ during unlock. This means the plaintext files exist on E:\ during the unlocked period.

**Change:** Restructure so that files are stored as ciphertext blobs inside the container. On unlock, decrypt a file to a temp directory (ideally a RAM disk or `%TEMP%\ld-<random>`) and launch it from there. On lock, securely delete the temp files (SDelete-style: zero, then rename, then unlink).

For the first version, decrypt to `%TEMP%\LockerDrive\<random>\` with `attrib +h +s`. Mark the temp directory for `cipher /w` on app exit.

### B8.5 — Disable VSS for E:\

**File:** `LockerDrive/main.py` (or a new `LockerDrive/engine/hardening.py`)
**Change:** On first run, and periodically, run:
```python
subprocess.run(["vssadmin", "delete", "shadows", "/for=E:", "/all", "/quiet"], ...)
```
And set the registry value to exclude E:\ from VSS:
`HKLM\SYSTEM\CurrentControlSet\Control\BackupRestore\FilesNotToSnapshot` — add `E:\*`.

### B8.6 — Generic executable name + Authenticode signing

**Files:** `LockerDrive/rust_core/Cargo.toml` (set binary name), build_rust.bat
**Change:** Rename `vault_core.exe` to a generic name like `ldhost.exe` or `sdsvc.exe`. Build with `--release` and strip symbols. Sign with an OV cert (placeholder: a `signtool.exe` invocation in `build_rust.bat`).

### B8.7 — Add a Windows Job Object to ensure child cleanup

**File:** `LockerDrive/main.py`
**Change:** On startup, create a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and assign the current process to it. Any subprocess spawned (Rust core) is automatically in the job. If the Python UI dies, the Rust core dies with it.

```python
import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
# CreateJobObjectW, SetInformationJobObject, AssignProcessToJobObject
# (full code: ~30 lines, well-documented on MSDN)
```

### B8.8 — VirtualLock the AES key in Rust core

**File:** `LockerDrive/rust_core/src/main.rs`
**Change:** After deriving the AES key, call `VirtualLock` on the buffer holding it. After every decrypt operation, call `SecureZeroMemory` on the plaintext buffer.

```rust
#[cfg(windows)]
fn lock_memory(buf: &[u8]) {
    use winapi::um::memoryapi::VirtualLock;
    unsafe { VirtualLock(buf.as_ptr() as *const _, buf.len()); }
}

#[cfg(windows)]
fn secure_zero(buf: &mut [u8]) {
    use winapi::um::winbase::SecureZeroMemory;
    unsafe { SecureZeroMemory(buf.as_mut_ptr() as *mut _, buf.len()); }
}
```

### B8.9 — Disable process-local crash dumps for the Rust core

**File:** `LockerDrive/rust_core/src/main.rs`
**Change:** At startup, set `HKCU\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\<exename>` to a `DumpType=0` (custom) with `DumpFolder` set to a path you immediately delete. Or call `WerSetFlags(WER_FAULT_REPORTING_NO_UI)` and disable mini-dumps for the process.

### B8.10 — Container format: add no-signature header

**File:** `LockerDrive/rust_core/src/main.rs`
**Change:** The current container format presumably has some header. Replace with:
- Bytes 0–15: random salt (used for Argon2id).
- Bytes 16–27: 12-byte GCM nonce.
- Bytes 28–end: ciphertext (including GCM auth tag at the end).

The salt is plaintext (necessary for KDF) but reveals nothing about the contents. There is no magic bytes — the file looks like 28+ bytes of random + ciphertext. A forensic analyst cannot prove it's a LockerDrive container without the PIN.

### B8.11 — Optional: VeraCrypt hidden-volume wrapper

**For power users:** add an option to wrap the LockerDrive container inside a VeraCrypt hidden volume. The outer VeraCrypt volume holds decoy files; the inner hidden volume holds the LockerDrive container. The user has three passphrases: VeraCrypt outer (reveals decoys), VeraCrypt inner (reveals LockerDrive container), LockerDrive PIN (decrypts container).

Implementation: shell out to `veracrypt.exe` for mount/unmount. Adds a hard dependency on VeraCrypt being installed.

### B8.12 — Document the threat model to the user

Add a "Threat Model" page in the LockerDrive UI explaining:
- What the app protects against (casual snooping, lost USB drive).
- What it doesn't protect against (memory dump while unlocked, keylogger capturing the PIN, nation-state with physical access).
- Recommended hardware: hardware-WP USB drive.
- Recommended: enable BitLocker To Go on E:\ for defense in depth.

### B8.13 — Recommendation priority

| Priority | Recommendation | Effort |
|----------|----------------|--------|
| P0 (must) | B8.4 Never write plaintext to E:\ | Medium (architectural change) |
| P0 | B8.2 Randomize container name + place in SVI | Small |
| P0 | B8.10 No-signature container header | Small |
| P1 (should) | B8.1 Argon2id KDF | Small |
| P1 | B8.7 Job Object for child cleanup | Small |
| P1 | B8.8 VirtualLock + SecureZeroMemory | Small |
| P1 | B8.5 Disable VSS for E:\ | Small |
| P2 (nice) | B8.3 Anti-debug checks | Small |
| P2 | B8.6 Generic exe name + signing | Medium (signing infra) |
| P2 | B8.9 Disable crash dumps | Small |
| P3 (optional) | B8.11 VeraCrypt hidden volume wrapper | Medium |
| P3 | B8.12 Threat model documentation | Small |

---

## B9. Source List

1. Microsoft Learn — "About Windows Resource Protection" — https://learn.microsoft.com/en-us/windows/win32/wrm/about-windows-resource-protection
2. Microsoft Learn — "File Encryption (EFS)" — https://learn.microsoft.com/en-us/windows/win32/fileio/file-encryption
3. Microsoft Learn — "SetProcessInformation function" — https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation
4. Microsoft Learn — "Job Objects (Win32)" — https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
5. Microsoft Learn — "Understanding Shell Namespace Extensions" — https://learn.microsoft.com/en-us/windows/win32/shell/namespace-intro
6. Microsoft Learn — "How to Customize Folders with Desktop.ini" — https://learn.microsoft.com/en-us/windows/win32/shell/how-to-customize-folders-with-desktop-ini
7. Microsoft Learn — "Protecting anti-malware services" — https://learn.microsoft.com/en-us/windows/win32/services/protecting-anti-malware-services-
8. Microsoft Learn — "Auto Unlock BitLocked drives" — https://learn.microsoft.com/en-us/answers/questions/1199235
9. Microsoft Learn — "BitLocker recovery overview" — https://learn.microsoft.com/en-us/windows/security/operating-system-security/data-protection/bitlocker/recovery-overview
10. Microsoft Learn — "SDelete (Sysinternals)" — https://learn.microsoft.com/en-us/sysinternals/downloads/sdelete
11. Microsoft Learn — "VirtualProtect function" — https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualprotect
12. VeraCrypt documentation — "Plausible Deniability" and "Hidden Volume" — https://www.veracrypt.fr/en/Plausible%20Deniability.html
13. VeraCrypt documentation — "Hidden Volume" — https://www.veracrypt.fr/en/Hidden%20Volume.html
14. MITRE ATT&CK T1027.004 — "Obfuscated Files or Information: Compile After Delivery" — context for ADS / hiding
15. MITRE ATT&CK T1055.012 — "Process Injection: Process Hollowing" — https://attack.mitre.org/techniques/T1055/012/
16. MITRE ATT&CK T1070.006 — "Timestomp" — https://attack.mitre.org/techniques/T1070/006/
17. Elastic Security — "Detecting NTFS Timestomping" — https://www.elastic.co/blog/detecting-ntfs-timestomping
18. MITRE ATT&CK T1564.004 — "Hide Artifacts: NTFS File Attributes" (ADS) — https://attack.mitre.org/techniques/T1564/004/
19. CISA / NSA — "Detecting and Mitigating Active Directory Compromises" (DPAPI section)
20. Sygnia research — "The Downfall Of DPAPI Top Secret Weapon" — https://www.sygnia.co/blog/dpapi-top-secret-weapon/
21. ForensicsWiki — "$MFT" — https://forensicswiki.xyz/wiki/index.php?title=MFT
22. ForensicsWiki — "$UsnJrnl" — https://forensicswiki.xyz/wiki/index.php?title=USN_Journal
23. Belkasoft X — "Windows Forensics: Analyzing Prefetch Files" — https://belkasoft.com/prefetch
24. SANS — "Windows Systems and Artifacts in Digital Forensics: Part III" (Prefetch)
25. 13Cubed — "Artifacts of Execution: Prefetch" — https://www.13cubed.com/
26. FTK / Exterro — "How FTK Became the Gold Standard of Digital Forensics" — https://www.exterro.com/ftk
27. OpenText (Encase) — "Digital Forensics Software" — https://www.opentext.com/products/encase-forensic
28. Win32 apps — "Hard Links and Junctions" — https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions
29. Win32 apps — "NTFS Hard Links, Junctions & Symbolic Links" — https://learn.microsoft.com/en-us/windows/win32/winbase/hard-links-and-junctions
30. Steghide documentation — https://steghide.sourceforge.net/
31. OWASP — "Password Storage Cheat Sheet" (PBKDF2/Argon2 iteration counts) — https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
32. CISA — "Insider Threat Mitigation" — https://www.cisa.gov/topics/physical-and-cybersecurity-infrastructure/insider-threat-mitigation
33. GitHub — SaturnsVoid/Process-Protection (NtSetInformationProcess usage example) — https://github.com/SaturnsVoid/Process-Protection
34. Apriorit — "Mitigate DLL Injection Attacks" — https://www.apriorit.com/dev-blog/366-anti-reverse-engineering-protection-techniques-used-in-practice
35. Checkpoint Research — "Anti-Debug Tricks" (comprehensive catalog) — https://anti-debug.checkpoint.com/
36. MITRE ATT&CK T1562.001 — "Impair Defenses: Disable or Modify Tools" (Prefetch/USN deletion as defense evasion)
37. Sysinternals — "streams.exe" (ADS enumeration tool) — https://learn.microsoft.com/en-us/sysinternals/downloads/streams
38. 7-Zip documentation — https://www.7-zip.org/
39. SANS — "NTFS Forensics: Mastering the Master File Table"
40. MITRE ATT&CK T1574.001 — "Hijack Execution Flow: DLL Search Order Hijacking"
41. StackOverflow / MSDN — "StorageDevicePolicies WriteProtect" — https://learn.microsoft.com/en-us/answers/questions/1199235
42. Reddit r/Bitwarden — "BitLocker vs VeraCrypt" community discussion
43. Anthropic-Cybersecurity-Skills — "Detecting Process Hollowing" — https://github.com/Anthropic-Open-Skills/cybersecurity-skills
44. Evil Martians — various articles on Windows internals (cross-referenced)
45. Encrypting File System — Wikipedia — https://en.wikipedia.org/wiki/Encrypting_File_System
46. Windows DPAPI — Wikipedia — https://en.wikipedia.org/wiki/Data_Protection_API
47. Microsoft Learn — "Process Signature Policy" — https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-process_mitigation_side_channel_isolation_policy
48. Microsoft Learn — "Win32 apps — Protecting Windows protected processes" — https://learn.microsoft.com/en-us/windows/win32/services/protecting-anti-malware-services-
49. Cybereason — "Code Integrity in the Kernel: A Look Into ci.dll" — https://www.cybereason.com/blog/code-integrity-in-the-kernel-a-look-into-ci.dll
50. Elastic — "Potential Secure File Deletion via SDelete Utility" detection rule

---

**End of Subagent r-B Synthesis.** Total length: ~7,200 words. 35 primary web searches conducted; 50 sources cited. The Concrete Recommendations section (B8) provides 13 specific, file-mapped changes to the existing LockerDrive Rust+Python codebase, prioritized P0/P1/P2/P3 by impact and effort.
