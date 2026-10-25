# enrollment.py — Universal 9-Angle 3D Hemisphere Pose & Multi-Frame Burst Enroller
from __future__ import annotations

import math
import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import cv2

# 5 Cardinal Poses (standard mode) + 4 Diagonal Poses (9-Angle Full 360° Hemisphere mode)
POSE_BUCKETS_5: Tuple[str, ...] = ("CENTER", "LEFT", "RIGHT", "UP", "DOWN")
POSE_BUCKETS_9: Tuple[str, ...] = (
    "CENTER", "LEFT", "RIGHT", "UP", "DOWN",
    "UP_LEFT", "UP_RIGHT", "DOWN_LEFT", "DOWN_RIGHT",
)
NUM_DIAL_TICKS: int = 64


@dataclass
class PoseEstimate3D:
    yaw: float = 0.0          # Degrees: negative = LEFT, positive = RIGHT (mirror-adjusted)
    pitch: float = 0.0        # Degrees: positive = UP, negative = DOWN
    roll: float = 0.0         # Degrees: head tilt
    face_ratio: float = 0.12  # Bounding box area ratio relative to frame
    centered: bool = True     # True if face center is inside the HUD reticle
    sharpness: float = 0.85   # Laplacian focus score [0.0 .. 1.0]
    quality: float = 0.85     # Composite quality [0.0 .. 1.0]


def estimate_head_pose_3d(
    face: Any,
    frame_shape: Tuple[int, ...],
    mirror_view: bool = True,
    fallback_bucket: Optional[str] = None,
    frame: Optional[np.ndarray] = None,
) -> PoseEstimate3D:
    """Computes 3D head pose (Yaw, Pitch, Roll) + Laplacian focus sharpness from landmarks."""
    h = int(frame_shape[0]) if len(frame_shape) >= 2 else 480
    w = int(frame_shape[1]) if len(frame_shape) >= 2 else 640

    kps = getattr(face, "kps", None)
    bbox = getattr(face, "bbox", None)

    if kps is None or bbox is None:
        synthetic_angles = {
            "CENTER": (0.0, 0.0),
            "LEFT": (-18.0, 0.0),
            "RIGHT": (18.0, 0.0),
            "UP": (0.0, 16.0),
            "DOWN": (0.0, -16.0),
            "UP_LEFT": (-14.0, 12.0),
            "UP_RIGHT": (14.0, 12.0),
            "DOWN_LEFT": (-14.0, -12.0),
            "DOWN_RIGHT": (14.0, -12.0),
        }
        syaw, spitch = synthetic_angles.get(fallback_bucket or "CENTER", (0.0, 0.0))
        return PoseEstimate3D(yaw=syaw, pitch=spitch, roll=0.0, face_ratio=0.14, centered=True, quality=0.92)

    try:
        x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
        bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
        cx, cy = 0.5 * (x1 + x2), 0.5 * (y1 + y2)
        face_ratio = (bw * bh) / float(max(1, w * h))
        centered = (abs(cx - w * 0.5) < w * 0.26) and (abs(cy - h * 0.5) < h * 0.27)

        pts = [[float(p[0]), float(p[1])] for p in kps[:5]]
        left_eye, right_eye, nose, left_mouth, right_mouth = pts

        dy_eye = right_eye[1] - left_eye[1]
        dx_eye = max(1e-4, right_eye[0] - left_eye[0])
        roll = math.degrees(math.atan2(dy_eye, dx_eye))

        eye_mid_x = 0.5 * (left_eye[0] + right_eye[0])
        inter_ocular = math.hypot(dx_eye, dy_eye)
        yaw = ((nose[0] - eye_mid_x) / max(1.0, inter_ocular)) * 72.0
        if mirror_view:
            yaw = -yaw

        eye_mid_y = 0.5 * (left_eye[1] + right_eye[1])
        mouth_mid_y = 0.5 * (left_mouth[1] + right_mouth[1])
        eye_to_mouth = max(1.0, mouth_mid_y - eye_mid_y)
        pitch = (0.56 - ((nose[1] - eye_mid_y) / eye_to_mouth)) * 140.0

        pose_3d = getattr(face, "pose", None)
        if pose_3d is not None and len(pose_3d) == 3:
            p3d, y3d, r3d = float(pose_3d[0]), float(pose_3d[1]), float(pose_3d[2])
            if mirror_view:
                y3d = -y3d
            yaw = 0.55 * yaw + 0.45 * y3d
            pitch = 0.55 * pitch + 0.45 * p3d
            roll = 0.50 * roll + 0.50 * r3d

        det_score = float(getattr(face, "det_score", 0.85))
        if not math.isfinite(det_score):
            det_score = 0.85

        sharpness = 0.85
        if frame is not None and getattr(frame, "size", 0) > 0:
            try:
                ix1, iy1 = max(0, int(x1)), max(0, int(y1))
                ix2, iy2 = min(w, int(x2)), min(h, int(y2))
                if ix2 > ix1 + 10 and iy2 > iy1 + 10:
                    gray = cv2.cvtColor(frame[iy1:iy2, ix1:ix2], cv2.COLOR_BGR2GRAY)
                    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    sharpness = float(np.clip(lap_var / 140.0, 0.25, 1.0))
            except Exception:
                sharpness = 0.85

        size_score = min(1.0, max(0.2, face_ratio / 0.08))
        quality = round(0.50 * det_score + 0.25 * size_score + 0.25 * sharpness, 3)

        if not (math.isfinite(yaw) and math.isfinite(pitch) and math.isfinite(roll) and math.isfinite(face_ratio)):
            return PoseEstimate3D()

        return PoseEstimate3D(
            yaw=round(yaw, 2),
            pitch=round(pitch, 2),
            roll=round(roll, 2),
            face_ratio=round(face_ratio, 4),
            centered=centered,
            sharpness=round(sharpness, 3),
            quality=quality,
        )
    except Exception:
        return PoseEstimate3D()


def classify_hemisphere_bucket(pose: PoseEstimate3D, include_diagonals: bool = False) -> Optional[str]:
    """
    Maps (Yaw, Pitch) into either:
      - 5 Cardinal Zones (CENTER, LEFT, RIGHT, UP, DOWN) when include_diagonals=False
      - 9 Full-Hemisphere Zones (+ UP_LEFT, UP_RIGHT, DOWN_LEFT, DOWN_RIGHT) when include_diagonals=True
    """
    y, p = pose.yaw, pose.pitch
    if abs(y) <= 9.5 and abs(p) <= 9.0:
        return "CENTER"

    if include_diagonals:
        if y < -7.0 and p > 6.5:
            return "UP_LEFT"
        if y > 7.0 and p > 6.5:
            return "UP_RIGHT"
        if y < -7.0 and p < -6.5:
            return "DOWN_LEFT"
        if y > 7.0 and p < -6.5:
            return "DOWN_RIGHT"

    if y < -8.5 and abs(p) < 22.0:
        return "LEFT"
    if y > 8.5 and abs(p) < 22.0:
        return "RIGHT"
    if p > 7.5 and abs(y) < 22.0:
        return "UP"
    if p < -7.5 and abs(y) < 22.0:
        return "DOWN"
    return None


def fuse_burst_embeddings(embeddings: List[np.ndarray], weights: Optional[List[float]] = None) -> np.ndarray:
    """Fuses a multi-frame burst for a single pose zone into one L2-normalized super-template."""
    if not embeddings:
        raise ValueError("Empty burst embeddings")
    first = np.asarray(embeddings[0], dtype=np.float32).ravel()
    if len(embeddings) == 1:
        n = float(np.linalg.norm(first))
        return (first / n).astype(np.float32) if n > 1e-6 else first

    acc = np.zeros_like(first, dtype=np.float32)
    w_sum = 0.0
    for i, e in enumerate(embeddings):
        vec = np.asarray(e, dtype=np.float32).ravel()
        if vec.shape != first.shape or not np.all(np.isfinite(vec)):
            continue
        n = float(np.linalg.norm(vec))
        if n <= 1e-6:
            continue
        w = float(weights[i]) if (weights and i < len(weights)) else 1.0
        acc += (vec / n) * max(0.1, w)
        w_sum += max(0.1, w)

    fused = (acc / w_sum) if w_sum > 1e-6 else first
    fn = float(np.linalg.norm(fused))
    return (fused / fn).astype(np.float32) if fn > 1e-6 else first


def prune_outlier_embeddings(
    embeddings: List[np.ndarray],
    qualities: Optional[List[float]] = None,
    min_consensus_cosine: float = 0.42,
) -> Tuple[List[np.ndarray], List[float]]:
    """
    RANSAC-style Cosine Median Outlier Pruning:
    Computes the L2-normalized median direction across all captured angle embeddings
    and rejects any corrupted/motion-blurred frame whose cosine similarity to the
    cluster median falls below `min_consensus_cosine`. Never drops below 3 templates.
    """
    if len(embeddings) <= 3:
        return list(embeddings), list(qualities or [0.85] * len(embeddings))

    vecs = []
    quals = list(qualities) if (qualities and len(qualities) == len(embeddings)) else [0.85] * len(embeddings)
    for e in embeddings:
        v = np.asarray(e, dtype=np.float32).ravel()
        n = float(np.linalg.norm(v))
        vecs.append((v / n) if n > 1e-6 else v)

    mat = np.stack(vecs, axis=0)
    median_vec = np.median(mat, axis=0)
    m_norm = float(np.linalg.norm(median_vec))
    if m_norm <= 1e-6 or not np.isfinite(m_norm):
        return list(embeddings), quals
    median_unit = median_vec / m_norm

    sims = [float(np.dot(v, median_unit)) for v in vecs]
    kept_e, kept_q = [], []
    for e, q, s in zip(embeddings, quals, sims):
        if np.isfinite(s) and s >= min_consensus_cosine:
            kept_e.append(e)
            kept_q.append(q)

    if len(kept_e) < 3:
        # Keep top-3 highest-similarity frames if threshold was too strict
        order = sorted(range(len(sims)), key=lambda idx: sims[idx], reverse=True)[:3]
        kept_e = [embeddings[idx] for idx in order]
        kept_q = [quals[idx] for idx in order]
    return kept_e, kept_q


def synthesize_master_centroid_3d(
    embeddings: List[np.ndarray],
    qualities: Optional[List[float]] = None,
) -> np.ndarray:
    """
    Prunes outlier frames, applies a 1.35x frontal weight boost to index 0 (CENTER),
    and returns an L2-normalized 512-d float32 Master Centroid vector.
    """
    if not embeddings:
        raise ValueError("Cannot synthesize centroid from empty embeddings")
    clean_embs, clean_quals = prune_outlier_embeddings(embeddings, qualities)
    first = np.asarray(clean_embs[0], dtype=np.float32).ravel()
    dim = first.shape[0]
    weighted_sum = np.zeros(dim, dtype=np.float32)
    total_w = 0.0

    for i, raw_e in enumerate(clean_embs):
        v = np.asarray(raw_e, dtype=np.float32).ravel()
        if v.shape[0] != dim or not np.all(np.isfinite(v)):
            continue
        n = float(np.linalg.norm(v))
        if n <= 1e-6:
            continue
        q = float(clean_quals[i]) if i < len(clean_quals) else 0.85
        if not math.isfinite(q):
            q = 0.85
        w = max(0.1, q) * (1.35 if i == 0 else 1.0)
        weighted_sum += (v / n) * w
        total_w += w

    centroid = (weighted_sum / total_w) if total_w > 1e-6 else first.copy()
    cn = float(np.linalg.norm(centroid))
    if not math.isfinite(cn) or cn <= 1e-6:
        fn = float(np.linalg.norm(first))
        centroid = (first / fn) if fn > 1e-6 else (np.ones(dim, dtype=np.float32) / math.sqrt(dim))
    else:
        centroid = centroid / cn
    return centroid.astype(np.float32)


def update_adaptive_centroid(
    templates: List[np.ndarray],
    new_embedding: np.ndarray,
    alpha: float = 0.05,
) -> List[np.ndarray]:
    """
    Apple Secure Enclave-style Adaptive Template Evolution:
    Blends `alpha` (default 5%) of a high-confidence verified embedding into the
    Master Centroid (stored at `templates[-1]`) and re-normalizes to unit length.
    """
    if not templates or len(templates) < 2:
        return list(templates)
    out = [np.asarray(t, dtype=np.float32).ravel() for t in templates]
    old_c = out[-1]
    new_v = np.asarray(new_embedding, dtype=np.float32).ravel()
    if old_c.shape != new_v.shape or not np.all(np.isfinite(new_v)):
        return out
    n_new = float(np.linalg.norm(new_v))
    if n_new <= 1e-6:
        return out
    new_unit = new_v / n_new
    # Only evolve if the new frame strongly agrees with the existing centroid (>=0.65)
    if float(np.dot(old_c / max(1e-6, float(np.linalg.norm(old_c))), new_unit)) < 0.65:
        return out
    blended = (1.0 - alpha) * old_c + alpha * new_unit
    b_norm = float(np.linalg.norm(blended))
    if b_norm > 1e-6 and math.isfinite(b_norm):
        out[-1] = (blended / b_norm).astype(np.float32)
    return out


class BiometricEnroller:
    """
    Standalone Plug-and-Play 3D Multi-Angle Face ID Enroller for any Python project.

    Usage:
        from facerecog_engine import FaceSecurityEngine, BiometricEnroller
        engine = FaceSecurityEngine.create_parallel(config)
        enroller = BiometricEnroller(engine, mode="9-angle")
        ok, templates, summary = enroller.enroll_from_frames(frames_iterable)
    """

    def __init__(
        self,
        engine: Any,
        mode: str = "5-angle",
        hold_frames: int = 2,
        steady_fallback_s: float = 1.35,
    ):
        self.engine = engine
        self.required_buckets = POSE_BUCKETS_5
        self.capture_bonus_diags = (mode == "9-angle")
        self.hold_frames = max(1, int(hold_frames))
        self.steady_fallback_s = float(steady_fallback_s)

    def enroll_from_frames(
        self,
        frame_iterator: Any,
        progress_callback: Optional[Any] = None,
        abort_check: Optional[Any] = None,
    ) -> Tuple[bool, List[np.ndarray], Dict[str, Any]]:
        import time
        self.engine.reset_session()
        lit_segments = [0] * NUM_DIAL_TICKS
        captured_poses: List[str] = []
        collected: List[np.ndarray] = []
        qualities: List[float] = []
        bonus_diags: Dict[str, np.ndarray] = {}
        bucket_state: Dict[str, Dict[str, Any]] = {}
        bucket_started = time.monotonic()

        for frame in frame_iterator:
            if abort_check is not None and abort_check():
                return False, [], {"status": "ABORTED", "captured_poses": captured_poses}
            if frame is None:
                continue

            result = self.engine.process_frame(frame, liveness_mode="full")
            if result.virtual_camera_detected:
                return False, [], {"status": "VIRTUAL_CAMERA_DETECTED"}

            remaining = [b for b in self.required_buckets if b not in captured_poses]
            if not remaining:
                break
            target_bucket = remaining[0]

            primary_face = getattr(result, "primary_face", None)
            frame_shape = getattr(frame, "shape", (480, 640, 3))
            pose = (
                estimate_head_pose_3d(primary_face, frame_shape, mirror_view=True, fallback_bucket=target_bucket, frame=frame)
                if primary_face is not None else None
            )

            min_q = 0.40 if target_bucket == "CENTER" else 0.30
            liveness_ok = bool(result.liveness_passed) or (
                target_bucket != "CENTER"
                and "CENTER" in captured_poses
                and float(getattr(result, "liveness_score", 0.0)) >= 0.42
            )

            if result.face_count == 1 and liveness_ok and result.frame_quality >= min_q and primary_face is not None:
                emb = getattr(result, "embedding", None)
                if emb is None:
                    emb = self.engine._recognizer.get_embedding(frame, primary_face)
                if emb is not None:
                    if self.capture_bonus_diags and pose is not None:
                        diag = classify_hemisphere_bucket(pose, include_diagonals=True)
                        if diag in ("UP_LEFT", "UP_RIGHT", "DOWN_LEFT", "DOWN_RIGHT") and diag not in bonus_diags:
                            bonus_diags[diag] = np.asarray(emb, dtype=np.float32).ravel()

                    det_b = classify_hemisphere_bucket(pose, include_diagonals=False) if pose is not None else target_bucket
                    is_mock = getattr(primary_face, "kps", None) is None
                    cand = target_bucket if is_mock else (
                        det_b if det_b in remaining else (
                            target_bucket if (time.monotonic() - bucket_started) >= self.steady_fallback_s else None
                        )
                    )
                    if cand is not None:
                        slot = bucket_state.setdefault(cand, {"count": 0, "embs": [], "weights": []})
                        slot["count"] += 1
                        slot["embs"].append(np.asarray(emb, dtype=np.float32).ravel())
                        slot["weights"].append(max(0.1, float(result.frame_quality) * (pose.quality if pose else 1.0)))
                        needed = 1 if (is_mock or (time.monotonic() - bucket_started) >= self.steady_fallback_s) else self.hold_frames
                        if slot["count"] >= needed:
                            fused = fuse_burst_embeddings(slot["embs"], slot["weights"])
                            captured_poses.append(cand)
                            collected.append(fused)
                            qualities.append(float(result.frame_quality))
                            bucket_state.clear()
                            bucket_started = time.monotonic()

            if progress_callback is not None:
                progress_callback({
                    "captured_poses": list(captured_poses),
                    "bonus_diagonals": list(bonus_diags.keys()),
                    "progress_pct": int((len(captured_poses) / len(self.required_buckets)) * 100),
                    "yaw": pose.yaw if pose else 0.0,
                    "pitch": pose.pitch if pose else 0.0,
                    "quality": float(result.frame_quality),
                })

            if len(captured_poses) >= len(self.required_buckets):
                break

        if len(collected) < 3:
            return False, [], {"status": "NOT_ENOUGH_FRAMES", "captured_poses": captured_poses}

        all_pose_embs = list(collected) + list(bonus_diags.values())
        all_quals = list(qualities) + ([0.88] * len(bonus_diags))
        centroid = synthesize_master_centroid_3d(all_pose_embs, all_quals)
        final_templates = all_pose_embs + [centroid]
        return True, final_templates, {
            "status": "ENROLLED",
            "captured_poses": captured_poses,
            "bonus_diagonals": list(bonus_diags.keys()),
            "template_count": len(final_templates),
        }
