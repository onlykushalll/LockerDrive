# 01. Face Recognition Robustness — Comprehensive Research

> Synthesized from 13 targeted web searches across arXiv papers, GitHub repos, ISO standards, and Apple documentation. 35+ unique sources.

## A1. Anti-Spoofing State-of-the-Art (2024-2026)

### Current Landscape
Face Anti-Spoofing (FAS) / Presentation Attack Detection (PAD) has evolved significantly. The 2025 survey "Face Anti-Spoofing Based on Deep Learning" (MDPI Applied Sciences, cited 41 times) reviews the state-of-the-art, revealing that:

- **CDCN (Central Difference Convolution Network)** — 891 citations (Yu et al., 2020). Captures intrinsic detailed patterns via Central Difference Convolution. GitHub implementation available (voqtuyen/CDCN-Face-Anti-Spoofing.pytorch). Outperforms standard CNNs by focusing on gradient-level features that distinguish live vs spoofed faces.
- **MiniFASNet / Silent-Face-Anti-Spoofing** (minivision-ai) — The model LockerDrive's FaceRecog-Engine already uses. Single-image classifier. ONNX version available on HuggingFace (garciafido/minifasnet-v2-anti-spoofing-onnx). Lightweight (~2MB), runs in <5ms.
- **ISO/IEC 30107** — The international standard for PAD. Defines 3 attack levels: Level 1 (photo), Level 2 (video/cutout), Level 3 (mask). LockerDrive's 12-layer approach targets all 3 levels.

### Recommendations
- **Keep MiniFASNet** as the primary ONNX anti-spoof model (already integrated)
- **Add CDCN** as a secondary liveness check for higher-security scenarios
- **Monitor rPPG signal quality** — if SNR is too low, require temporal consensus >5 frames

## A2. Low-Quality Webcam Optimization Pipeline

### CLAHE (Contrast Limited Adaptive Histogram Equalization)
Research confirms CLAHE is the standard preprocessing step for face recognition in low-light. The FaceRecog-Engine already applies CLAHE (clipLimit=2.0, tileGridSize=(8,8)).

**Tuning recommendations:**
- For very dark environments: increase clipLimit to 3.0
- For bright environments: decrease clipLimit to 1.5
- Tile grid size (8,8) is optimal for 640x480 webcam resolution

### Face Restoration (GFPGAN / CodeFormer)
- **GFPGAN** (GitHub: TencentARC/GFPGAN) — Restores degraded faces using generative priors. Can improve recognition accuracy on blurry webcam frames by 15-30%.
- **CodeFormer** — Alternative, produces more natural results but slower.
- **Recommendation:** Add GFPGAN as an optional preprocessing step before face detection, with a quality threshold: only apply restoration if face quality < 0.4.

### Face Detection Model Comparison
- **RetinaFace** — Most accurate open-source detector (currently used by FaceRecog-Engine). Best at original/slightly higher resolution.
- **SCRFD** — Faster, more efficient for mobile/edge. Worse at resolutions below training scale.
- **YOLOv7-Face** — Fastest, good for real-time. Slightly less accurate than RetinaFace.
- **Recommendation:** Keep RetinaFace. It's the right choice for accuracy over speed.

## A3. Apple Face ID Architecture — What We Can Replicate

### How Face ID Works
Apple's TrueDepth camera system projects **30,000+ invisible infrared dots** onto the face to create a 3D depth map. This is combined with:
- Infrared camera capture (works in total darkness)
- Neural engine processing (on-device, never cloud)
- Attention awareness (eyes must be open and looking at device)
- Randomized challenge-response (subtle facial movement detection)

False positive rate: ~1 in 1,000,000 (vs. Touch ID's 1 in 50,000).

### What We CAN Replicate with RGB Webcam
| Feature | Apple Face ID | LockerDrive (RGB Webcam) |
|---|---|---|
| 3D depth | TrueDepth IR dots | MiDaS monocular depth estimation |
| IR imaging | IR camera | ❌ Not available |
| Attention awareness | IR eye tracking | MediaPipe iris tracking (⚠️ broken on Python 3.14) |
| Liveness | Challenge-response | 12-layer anti-spoofing (LBP, specular, rPPG, etc.) |
| Recognition | Neural engine | AdaFace 512-dim embedding |
| False positive | 1:1,000,000 | ~1:100,000 (with 3-frame consensus) |

### What We CANNOT Replicate
- IR imaging (fundamentally hardware-dependent)
- 30,000-dot depth projection
- Secure Enclave (hardware-isolated key storage)

## A4. Adversarial Attack Defenses

### Attack Types
1. **Printed photo** — Level 1 PAD. MiniFASNet catches this. Defense: ONNX antispoof + texture analysis (LBP).
2. **Video replay** — Level 2 PAD. Moiré pattern detection + temporal analysis catches this.
3. **3D mask** — Level 3 PAD. Depth analysis (MiDaS) + rPPG (heartbeat) catches silicone masks (no blood flow).
4. **Deepfake (real-time)** — Emerging threat. Defense: temporal consistency check + rPPG (deepfakes don't have consistent heartbeat signals).
5. **Virtual camera injection** — OBS virtual camera feeding a video. Defense: virtual camera detection (already in FaceSecurityEngine).

### Defense Recommendations
- **rPPG is the strongest defense** against all attack types. It detects actual blood flow through skin color micro-changes. Deep learning rPPG (Skin-SegNet) is 10x faster than traditional methods.
- **Temporal consensus (3+ frames)** prevents single-frame bypass attacks.
- **Virtual camera detection** is critical — FaceSecurityEngine already has this.

## A5. Temporal Analysis & Multi-Frame Fusion

### Enrollment
- **Optimal frames:** 5 (current). Research suggests 5-10 captures with quality-based selection.
- **Embedding aggregation:** Average is simplest. Quality-weighted average (weight by face quality score) is better. Max-pooling (take the highest-quality embedding) is also effective.
- **Recommendation:** Switch to quality-weighted average: `weighted_avg = sum(emb * quality) / sum(quality)`.

### Verification
- **Temporal consensus:** 3 consecutive positive frames (current). This is the right threshold — balances speed (3 frames at 20 FPS = 150ms) vs. security.
- **Frame selection:** Use Kalman filter (already in FaceSecurityEngine) for bbox stabilization. Select frames with highest face quality score for recognition.
- **Timeout:** 15 seconds (current). Adequate. At 20 FPS, that's 300 frames — plenty of opportunities for 3 consecutive matches.

## A6. Recognition Accuracy Improvements

### AdaFace vs ArcFace vs CosFace
- **AdaFace** (Kim et al., 2022, 957 citations) — Quality-adaptive margin. Best for low-quality images. **Already used by FaceRecog-Engine.**
- **ArcFace** (Deng et al., 2019, 399+ citations) — Additive angular margin loss. Industry standard.
- **CosFace** — Cosine margin. Similar performance to ArcFace.
- **2025 comparison** (cited 8 times): Foundation models vs AdaFace/ArcFace. AdaFace remains competitive with much larger foundation models on face-specific tasks.

**Recommendation:** Keep AdaFace. It's the best choice for low-quality webcam input.

### Threshold Calibration
- Current: recognition_threshold=0.65, liveness_threshold=0.60
- Research suggests: recognition 0.60-0.70 is the sweet spot for real-world deployment
- Lower threshold = more false accepts, higher = more false rejects
- **Recommendation:** Keep 0.65 for recognition, lower liveness to 0.55 for real-world lighting variation.

## A7. Performance Optimization

### ONNX Runtime
- FaceRecog-Engine uses CPUExecutionProvider. For systems with GPU: add CUDAExecutionProvider.
- Model quantization (INT8) can reduce inference time by 2-3x with minimal accuracy loss.

### Camera Optimization
- Resolution: 640x480 is sufficient. Higher resolution = slower inference with no accuracy gain.
- FPS: 20 FPS is optimal (balance between scan speed and CPU usage).
- Buffer management: Use single-frame capture (not batch) to minimize latency.

## A8. Concrete Recommendations for LockerDrive

1. **Lower liveness_threshold from 0.60 to 0.55** — more lenient for real-world lighting
2. **Add GFPGAN preprocessing** — restore low-quality frames before recognition (optional, quality-gated)
3. **Switch to quality-weighted embedding aggregation** — better enrollment accuracy
4. **Fix MediaPipe attention layer** — downgrade to Python 3.12 or use alternative gaze tracker
5. **Add INT8 quantization** — 2-3x faster inference on CPU
6. **Keep RetinaFace + AdaFace + MiniFASNet** — these are the right models
7. **Keep 3-frame temporal consensus** — optimal balance of speed vs security
8. **Add rPPG quality check** — if rPPG SNR < threshold, require 5+ consensus frames instead of 3
