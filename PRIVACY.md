# Privacy Policy — LockerDrive

**Last Updated:** September 7, 2026

LockerDrive ("we", "our", or "the software") is dedicated to protecting user data through zero-knowledge, hardware-bound encrypted storage.

---

## 1. Zero Cloud Transmission Guarantee

**LockerDrive operates 100% locally on your workstation.**
- **No Remote Telemetry:** The software makes zero outbound network connections. No user activity, file access logs, IP addresses, or system metrics are ever transmitted.
- **Local Biometric Processing:** All facial recognition routines (InsightFace ONNX) execute entirely on your local CPU/GPU via DirectML/CUDA/OpenVINO. Camera frames from your webcam are processed in volatile system RAM and discarded within milliseconds.
- **Local Key Derivation:** Argon2id PIN verification and key derivation occur exclusively in local workstation memory.

---

## 2. Biometric Data Privacy (BIPA & GDPR Article 9 Compliance)

In accordance with the Illinois Biometric Information Privacy Act (740 ILCS 14/) and GDPR Article 9:
- **Biometric Collection Purpose:** Facial scans are used solely to generate a mathematical embedding vector for authorizing vault drive decryption.
- **No Raw Image Storage:** Photographs and video frames are NEVER stored on disk. Only a non-reversible 512-dimensional floating-point mathematical vector is saved.
- **Where the live template lives:** the enrolled templates are stored at `E:\LockerDrive\face_templates.pkl`, wrapped for your Windows profile via DPAPI and HMAC-bound to your vault secret (tampering with the file is detected and rejected). You can destroy your biometric template at any time by deleting that file from the drive and re-enrolling (or not).
- **Orphaned legacy file -- ACTION RECOMMENDED:** an early version stored an *unencrypted* template at `assets/.face_encoding`. Nothing in the current app reads or writes that file. If it exists on your machine, delete it locally **and** purge it from your OneDrive recycle bin and version history (it has been cloud-synced since 2026-07-26 in default setups) -- an unencrypted biometric embedding does not belong in cloud storage.
- **No Third-Party Sharing:** Biometric data is never shared, sold, leased, or transmitted to any third party.

---

## 3. Local Data Storage

LockerDrive creates and manages:
- **Encrypted Virtual Disk (.vhdx):** Stored on your chosen drive or USB device, encrypted using industry-standard bitlocker / AES algorithms.
- **Local Access Logs (`access_log.py`):** An optional audit log of unlock attempts maintained locally on your system drive.

---

## 4. Contact

For inquiries regarding LockerDrive's privacy guarantees, please open an issue on our GitHub repository:
the project repository


