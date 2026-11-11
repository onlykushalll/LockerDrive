# 🛡️ FaceRecog-Engine v2.0

> **Standalone 12-Layer Passive Anti-Spoofing & 3D Multi-Angle Biometric Authentication SDK**
> Engineered as a zero-cloud, drop-in biometric security SDK for desktop and edge applications (LockerDrive, TAALA-2KEN, and custom Python apps). Runs 100% offline on Python 3.10–3.14+.

[![Biometrics: 9-Angle 360° TrueDepth](https://img.shields.io/badge/Enrollment-9--Angle%20360%C2%B0%20Burst-success.svg)](#3d-multi-angle-enrollment--master-centroid-sdk)
[![Liveness: 12-Layer Passive PAD](https://img.shields.io/badge/Anti--Spoofing-12--Layer%20Passive%20PAD-blue.svg)](#the-12-layer-passive-anti-spoofing-stack)
[![Python: 3.10 - 3.14+](https://img.shields.io/badge/Python-3.10%20--%203.14%2B-3776AB.svg)](#quick-start)

---

## ✨ Key Capabilities in v2.0

* **⚡ 4-Way Parallel-Sequential ONNX Model Loader (`create_parallel`):** Loads RetinaFace (`det_10g.onnx`), ArcFace (`w600k_r50.onnx`), MiniFASNetV2 (`antispoof_minifasv2.onnx`), and MiDaS 3D Depth (`midas_v21_small_256.onnx`) concurrently while skipping unused InsightFace models and bypassing Windows Smart App Control `simsimd` blocks.
* **🌐 9-Angle 360° Hemisphere Multi-Frame Burst Enrollment (`BiometricEnroller`):**
  * Tracks real-time 3D head pose (**Yaw, Pitch, Roll**) + **Laplacian Focus Sharpness** from 5-point and 68-point landmarks.
  * Captures multi-frame bursts across **5 Cardinal Zones** (`CENTER`, `LEFT`, `RIGHT`, `UP`, `DOWN`) + **4 Diagonal Octants** (`UP_LEFT`, `UP_RIGHT`, `DOWN_LEFT`, `DOWN_RIGHT`).
  * Applies **RANSAC Cosine-Median Outlier Pruning** (`prune_outlier_embeddings`) and synthesizes a quality-weighted **512-d Master Centroid**.
  * Supports **Apple Secure Enclave-style Adaptive Template Evolution** (`update_adaptive_centroid`), gently evolving the user's centroid (5% EMA) on high-confidence unlocks (`>=0.86`).
* **🛡️ 12-Layer Passive Presentation Attack Detection (PAD):**
  1. **`MiniFASNetV2` ONNX** neural anti-spoofing classifier
  2. **`MiDaS v2.1 Small` 3D Monocular Depth** surface curvature analysis
  3. **`CHROM rPPG`** sub-dermal microvascular blood-volume pulse (active from Frame 3 + FFT cardiac band `0.75–3.2 Hz`)
  4. **Projective 5-Point Gaze & Micro-Saccade Attention** (native Python 3.14 implementation, zero `mediapipe.solutions` dependency)
  5. **2D FFT Frequency Moiré** digital screen pixel-grid detector
  6. **Local Binary Pattern (LBP)** Shannon entropy skin micro-texture
  7. **Dual Color-Space (`YCrCb` + `HSV`)** physiological skin cluster & chrominance correlation
  8. **HSV Specular Highlight** screen/photo glare detector
  9. **Temporal Eye-Blink & Head Micro-Movement** kinematics
  10. **Canny + Probabilistic Hough** rectangular phone/tablet bezel boundary detector
  11. **3D Perspective Landmark Ratio Variance** across head micro-oscillations
  12. **64-bit Perceptual `dHash` Hamming Ring-Buffer** video-replay attack detector
* **📹 Hardware Virtual-Camera Blocker (`VirtualCameraDetector`):** Detects and blocks OBS Virtual Camera, ManyCam, DroidCam, Iriun, XSplit, and non-physical DirectShow/MediaFoundation sources.
* **🔐 Universal Encrypted Template Store (`TemplateStore`):** Persists templates with Windows DPAPI (`CryptProtectData`) + HMAC-SHA256 binding and cross-platform AES-256-GCM support.

---

## 🚀 Quick Start (Clone into Any Project)

### 1. Clone from `onlykushalll/Utils` (Sparse Checkout of `~FaceRecog-Engine~` Only)

```powershell
git clone --depth 1 --filter=blob:none --sparse <repository-url> temp_utils
cd temp_utils
git sparse-checkout set "~FaceRecog-Engine~"
Move-Item "~FaceRecog-Engine~" ..\FaceRecog-Engine
cd ..
Remove-Item -Recurse -Force temp_utils

cd FaceRecog-Engine
pip install -r requirements.txt
pip install -e .
python download_models.py
```

### 2. 3-Line Enrollment & Verification in Any Python App

```python
from facerecog_engine import FaceSecurityEngine, SecurityConfig, BiometricEnroller, TemplateStore

config = SecurityConfig(model_dir="FaceRecog-Engine/models", recognition_threshold=0.75, liveness_threshold=0.70)
engine = FaceSecurityEngine.create_parallel(config=config, camera_idx=0)
store  = TemplateStore("user_templates.bin", secret_key="my-app-secret")

# 1. Enroll user (5-angle or 9-angle 360° burst with outlier pruning + master centroid)
enroller = BiometricEnroller(engine, mode="9-angle")
ok, templates, report = enroller.enroll_from_frames(iter_camera_frames())
if ok:
    store.save("owner", templates)

# 2. Verify live frame with all 12 anti-spoofing layers + 2-frame high-confidence fast-track
engine.set_reference_templates(store.load())
result = engine.process_frame(frame, liveness_mode="full")
if result.primary_user_present:
    print(f"Verified! Match={result.recognition_score:.2f}, Liveness={result.liveness_score:.2f}")
    print("12-Layer Breakdown:", engine._liveness.get_layer_breakdown())
```

---

## 🧪 Running the Test Suite

```powershell
cd FaceRecog-Engine
pytest -q
```
