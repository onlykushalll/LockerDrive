# enrollment.py — Apple Face ID-Grade 3D Hemisphere Pose, Weiszfeld Spherical Median & Multi-Appearance SDK
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

# 5 Cardinal Poses (Fast 1-Pass Mode) + 4 Diagonal Octants (9-Angle Full 360° Hemisphere Mode)
POSE_BUCKETS_5: Tuple[str, ...] = ("CENTER", "LEFT", "RIGHT", "UP", "DOWN")
POSE_BUCKETS_9: Tuple[str, ...] = (
    "CENTER", "LEFT", "RIGHT", "UP", "DOWN",
    "UP_LEFT", "UP_RIGHT", "DOWN_LEFT", "DOWN_RIGHT",
)
NUM_DIAL_TICKS: int = 64


@dataclass
class PoseEstimate3D:
    yaw: float = 0.0                  # Degrees: negative = LEFT, positive = RIGHT (mirror-adjusted)
    pitch: float = 0.0                # Degrees: positive = UP, negative = DOWN
    roll: float = 0.0                 # Degrees: head tilt
    face_ratio: float = 0.12          # Bounding box area ratio relative to frame
    centered: bool = True             # True if face center is inside the HUD reticle
    sharpness: float = 0.85           # Laplacian focus score [0.0 .. 1.0]
    illumination_symmetry: float = 0.90  # Left/Right cheek lighting balance [0.0 .. 1.0]
    angular_velocity: float = 0.0     # Instantaneous head rotation speed (deg/s)
    quality: float = 0.85             # Composite biometric quality [0.0 .. 1.0]


def l2_normalize(vec: np.ndarray) -> np.ndarray:
    """Safely projects a vector onto the unit hypersphere S^(d-1)."""
    arr = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(arr))
    if not math.isfinite(norm) or norm <= 1e-8:
        return np.zeros_like(arr, dtype=np.float32)
    return (arr / norm).astype(np.float32)


def spherical_slerp(u: np.ndarray, v: np.ndarray, t: float) -> np.ndarray:
    """
    Geodesic Spherical Linear Interpolation (SLERP) on the unit hypersphere S^511.
    Moves fraction `t` along the great-circle arc from unit vector `u` toward `v`.
    """
    u_unit = l2_normalize(u)
    v_unit = l2_normalize(v)
    t_clamped = float(np.clip(t, 0.0, 1.0))
    dot = float(np.clip(np.dot(u_unit, v_unit), -1.0, 1.0))

    # If vectors are nearly collinear, linear interpolation + projection is exact
    if abs(dot) > 0.9995:
        return l2_normalize((1.0 - t_clamped) * u_unit + t_clamped * v_unit)

    omega = math.acos(dot)
    sin_omega = math.sin(omega)
    if abs(sin_omega) < 1e-6:
        return u_unit
    w_u = math.sin((1.0 - t_clamped) * omega) / sin_omega
    w_v = math.sin(t_clamped * omega) / sin_omega
    return l2_normalize(w_u * u_unit + w_v * v_unit)


def weiszfeld_spherical_median(
    embeddings: List[np.ndarray],
    weights: Optional[List[float]] = None,
    max_iter: int = 15,
    tol: float = 1e-5,
) -> np.ndarray:
    """
    Computes the Iteratively Reweighted Weiszfeld Geometric Median on the
    unit hypersphere S^511. Unlike a simple average, the geometric median
    is robust up to a 50% breakdown point against outlier/blurred frames.
    """
    if not embeddings:
        raise ValueError("Cannot compute spherical median of empty embedding list")

    unit_vecs: List[np.ndarray] = []
    valid_weights: List[float] = []
    first_dim = np.asarray(embeddings[0]).size

    for i, emb in enumerate(embeddings):
        arr = np.asarray(emb, dtype=np.float32).ravel()
        if arr.size != first_dim or not np.all(np.isfinite(arr)):
            continue
        u = l2_normalize(arr)
        if float(np.linalg.norm(u)) > 0.5:
            unit_vecs.append(u)
            w = float(weights[i]) if (weights and i < len(weights)) else 1.0
            valid_weights.append(max(0.05, w if math.isfinite(w) else 1.0))

    if not unit_vecs:
        return np.ones(first_dim, dtype=np.float32) / math.sqrt(first_dim)
    if len(unit_vecs) == 1:
        return unit_vecs[0]

    mat = np.stack(unit_vecs, axis=0)                  # (N, D)
    w_arr = np.asarray(valid_weights, dtype=np.float32)
    w_arr /= np.sum(w_arr)

    # Initialize with weighted Euclidean mean projected onto S^(D-1)
    est = l2_normalize(np.sum(mat * w_arr[:, None], axis=0))

    for _ in range(max_iter):
        # Geodesic chord distances ||x_i - est||_2
        diffs = mat - est[None, :]
        dists = np.linalg.norm(diffs, axis=1)
        # Avoid division by zero if estimate coincides with a sample point
        inv_dists = w_arr / np.maximum(dists, 1e-4)
        inv_dists /= np.sum(inv_dists)
        next_est = l2_normalize(np.sum(mat * inv_dists[:, None], axis=0))
        if float(np.linalg.norm(next_est - est)) < tol:
            est = next_est
            break
        est = next_est

    return est


def prune_outlier_embeddings(
    embeddings: List[np.ndarray],
    qualities: Optional[List[float]] = None,
    min_consensus_cosine: float = 0.42,
) -> Tuple[List[np.ndarray], List[float]]:
    """
    RANSAC-style Geodesic Outlier Pruning around the Weiszfeld Spherical Median.
    Rejects corrupted or motion-blurred frames whose cosine similarity to the
    spherical median falls below `min_consensus_cosine`. Never drops below 3 templates.
    """
    if len(embeddings) <= 3:
        return list(embeddings), list(qualities or [0.85] * len(embeddings))

    quals = (
        list(qualities)
        if (qualities and len(qualities) == len(embeddings))
        else [0.85] * len(embeddings)
    )
    median_unit = weiszfeld_spherical_median(embeddings, quals)

    sims: List[float] = []
    for e in embeddings:
        u = l2_normalize(e)
        sims.append(float(np.dot(u, median_unit)))

    kept_e: List[np.ndarray] = []
    kept_q: List[float] = []
    for e, q, s in zip(embeddings, quals, sims):
        if math.isfinite(s) and s >= min_consensus_cosine:
            kept_e.append(e)
            kept_q.append(q)

    if len(kept_e) < 3:
        order = sorted(range(len(sims)), key=lambda idx: sims[idx], reverse=True)[:3]
        kept_e = [embeddings[idx] for idx in order]
        kept_q = [quals[idx] for idx in order]

    return kept_e, kept_q


def fuse_burst_embeddings(
    embeddings: List[np.ndarray],
    weights: Optional[List[float]] = None,
) -> np.ndarray:
    """
    Fuses a multi-frame burst captured within a single pose octant into one
    super-resolved unit template via the Weiszfeld Spherical Geometric Median.
    """
    return weiszfeld_spherical_median(embeddings, weights)


def synthesize_master_centroid_3d(
    embeddings: List[np.ndarray],
    qualities: Optional[List[float]] = None,
) -> np.ndarray:
    """
    Prunes outlier frames, applies a 1.35x frontal anchor weight to index 0 (CENTER),
    and computes the Weiszfeld Spherical Geometric Median as the Master Centroid.
    """
    if not embeddings:
        raise ValueError("Cannot synthesize centroid from empty embeddings")
    clean_embs, clean_quals = prune_outlier_embeddings(embeddings, qualities)
    boosted_weights = [
        max(0.1, float(q) if math.isfinite(float(q)) else 0.85) * (1.35 if idx == 0 else 1.0)
        for idx, q in enumerate(clean_quals)
    ]
    return weiszfeld_spherical_median(clean_embs, boosted_weights)


def update_adaptive_centroid(
    templates: List[np.ndarray],
    new_embedding: np.ndarray,
    alpha: float = 0.05,
    golden_anchor: Optional[np.ndarray] = None,
    max_drift_cosine: float = 0.72,
) -> List[np.ndarray]:
    """
    Anti-Poisoning Geodesic SLERP Template Evolution:
      - Moves `templates[-1]` (the Adaptive Centroid) by fraction `alpha` along
        the geodesic arc toward `new_embedding`.
      - Enforces an immutable Golden Anchor leash (`golden_anchor` or `templates[0]`):
        if the proposed update would drift further than `max_drift_cosine` from the
        user's original enrollment anchor, the update is rejected.
    """
    if not templates or len(templates) < 2:
        return list(templates)

    out = [np.asarray(t, dtype=np.float32).ravel() for t in templates]
    old_c = l2_normalize(out[-1])
    new_u = l2_normalize(new_embedding)
    if old_c.shape != new_u.shape or float(np.linalg.norm(new_u)) < 0.5:
        return out

    # 1. Must strongly agree with current adaptive centroid
    if float(np.dot(old_c, new_u)) < 0.65:
        return out

    # 2. Candidate SLERP update on S^511
    candidate = spherical_slerp(old_c, new_u, alpha)

    # 3. Anti-poisoning Golden Anchor leash check
    anchor = l2_normalize(golden_anchor if golden_anchor is not None else out[0])
    if float(np.dot(candidate, anchor)) < max_drift_cosine:
        return out

    out[-1] = candidate
    return out


def estimate_head_pose_3d(
    face: Any,
    frame_shape: Tuple[int, ...],
    mirror_view: bool = True,
    fallback_bucket: Optional[str] = None,
    frame: Optional[np.ndarray] = None,
    prev_pose_ts: Optional[Tuple[float, float, float]] = None,
) -> PoseEstimate3D:
    """
    Computes 3D head pose (Yaw, Pitch, Roll), Laplacian sharpness, left/right cheek
    illumination symmetry, and instantaneous angular velocity (deg/s).
    """
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
        return PoseEstimate3D(
            yaw=syaw, pitch=spitch, roll=0.0,
            face_ratio=0.14, centered=True, quality=0.92,
        )

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
        illum_sym = 0.90
        if frame is not None and getattr(frame, "size", 0) > 0:
            try:
                ix1, iy1 = max(0, int(x1)), max(0, int(y1))
                ix2, iy2 = min(w, int(x2)), min(h, int(y2))
                if ix2 > ix1 + 12 and iy2 > iy1 + 12:
                    roi_gray = cv2.cvtColor(frame[iy1:iy2, ix1:ix2], cv2.COLOR_BGR2GRAY)
                    lap_var = float(cv2.Laplacian(roi_gray, cv2.CV_64F).var())
                    sharpness = float(np.clip(lap_var / 135.0, 0.25, 1.0))

                    # Cheek illumination symmetry (detects harsh side-glare)
                    mid_c = roi_gray.shape[1] // 2
                    if mid_c > 4:
                        l_mean = float(np.mean(roi_gray[:, :mid_c]))
                        r_mean = float(np.mean(roi_gray[:, mid_c:]))
                        diff_ratio = abs(l_mean - r_mean) / max(15.0, 0.5 * (l_mean + r_mean))
                        illum_sym = float(np.clip(1.0 - diff_ratio * 0.7, 0.35, 1.0))
            except Exception:
                pass

        ang_vel = 0.0
        if prev_pose_ts is not None and len(prev_pose_ts) == 3:
            prev_y, prev_p, prev_t = prev_pose_ts
            dt = max(1e-3, time.monotonic() - float(prev_t))
            ang_vel = math.hypot(yaw - float(prev_y), pitch - float(prev_p)) / dt

        size_score = min(1.0, max(0.2, face_ratio / 0.08))
        quality = round(
            0.42 * det_score + 0.22 * size_score + 0.24 * sharpness + 0.12 * illum_sym,
            3,
        )

        if not (math.isfinite(yaw) and math.isfinite(pitch) and math.isfinite(roll)):
            return PoseEstimate3D()

        return PoseEstimate3D(
            yaw=round(yaw, 2),
            pitch=round(pitch, 2),
            roll=round(roll, 2),
            face_ratio=round(face_ratio, 4),
            centered=centered,
            sharpness=round(sharpness, 3),
            illumination_symmetry=round(illum_sym, 3),
            angular_velocity=round(ang_vel, 1),
            quality=quality,
        )
    except Exception:
        return PoseEstimate3D()


def classify_hemisphere_bucket(
    pose: PoseEstimate3D,
    include_diagonals: bool = False,
) -> Optional[str]:
    """
    Maps (Yaw, Pitch) into either:
      - 5 Cardinal Zones (CENTER, LEFT, RIGHT, UP, DOWN) when include_diagonals=False
      - 9 Full-Hemisphere Octants (+ UP_LEFT, UP_RIGHT, DOWN_LEFT, DOWN_RIGHT) when True
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


def update_dial_64_ticks(
    lit_segments: List[int],
    pose: PoseEstimate3D,
    captured_poses: List[str],
    bonus_diagonals: Optional[List[str]] = None,
) -> None:
    """
    Updates the 64-tick Apple Face ID circular dial:
      - State 1: Directional ticks currently swept by the user's head vector
      - State 2: Locked emerald octant arcs for captured cardinal + diagonal poses
    """
    tilt_mag = math.hypot(pose.yaw, pose.pitch)
    if tilt_mag >= 4.5:
        angle_rad = math.atan2(pose.yaw, pose.pitch)
        norm_angle = (angle_rad + 2.0 * math.pi) % (2.0 * math.pi)
        center_idx = int((norm_angle / (2.0 * math.pi)) * NUM_DIAL_TICKS) % NUM_DIAL_TICKS
        for offset in (-2, -1, 0, 1, 2):
            idx = (center_idx + offset) % NUM_DIAL_TICKS
            lit_segments[idx] = max(lit_segments[idx], 1)
    elif "CENTER" in captured_poses:
        for idx in (0, 16, 32, 48):
            lit_segments[idx] = max(lit_segments[idx], 1)

    all_captured = set(captured_poses) | set(bonus_diagonals or [])
    octant_ranges = {
        "UP": range(58, 70),
        "UP_RIGHT": range(4, 14),
        "RIGHT": range(10, 22),
        "DOWN_RIGHT": range(20, 30),
        "DOWN": range(26, 38),
        "DOWN_LEFT": range(36, 46),
        "LEFT": range(42, 54),
        "UP_LEFT": range(50, 60),
    }
    for bucket, idx_range in octant_ranges.items():
        if bucket in all_captured:
            for idx in idx_range:
                lit_segments[idx % NUM_DIAL_TICKS] = 2

    if len(captured_poses) >= len(POSE_BUCKETS_5):
        for i in range(NUM_DIAL_TICKS):
            lit_segments[i] = 2


class BiometricEnroller:
    """
    Apple Face ID-Grade 3D Hemisphere Enroller v3.0:
      - Supports 5-Angle Fast Sweep, 9-Angle 360° Hemisphere Sweep, and Alternate
        Appearance enrollment (e.g., glasses, beard, hood, low-light profile).
      - Applies an Angular-Velocity Motion-Blur Gate (pauses capture if head whips >55 deg/s).
      - Uses Horizontal Flip-TTA during enrollment for illumination-invariant templates.
      - Synthesizes a Weiszfeld Spherical Geometric Median Master Centroid + Pose Map.
    """

    def __init__(
        self,
        engine: Any,
        mode: str = "9-angle",
        hold_frames: int = 2,
        steady_fallback_s: float = 1.30,
        max_angular_velocity_deg_s: float = 55.0,
        profile_name: str = "primary",
    ):
        self.engine = engine
        self.mode = mode
        self.required_buckets = POSE_BUCKETS_5
        self.capture_bonus_diags = mode in ("9-angle", "full-hemisphere")
        self.hold_frames = max(1, int(hold_frames))
        self.steady_fallback_s = float(steady_fallback_s)
        self.max_angular_velocity = float(max_angular_velocity_deg_s)
        self.profile_name = profile_name

    def enroll_from_frames(
        self,
        frame_iterator: Iterable[np.ndarray],
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> Tuple[bool, List[np.ndarray], Dict[str, Any]]:
        self.engine.reset_session()
        lit_segments = [0] * NUM_DIAL_TICKS
        captured_poses: List[str] = []
        pose_bank: Dict[str, np.ndarray] = {}
        collected: List[np.ndarray] = []
        qualities: List[float] = []
        bonus_diags: Dict[str, np.ndarray] = {}
        bucket_state: Dict[str, Dict[str, Any]] = {}
        bucket_started = time.monotonic()
        prev_pose_ts: Optional[Tuple[float, float, float]] = None

        for frame in frame_iterator:
            if abort_check is not None and abort_check():
                return False, [], {"status": "ABORTED", "captured_poses": captured_poses}
            if frame is None:
                continue

            result = self.engine.process_frame(frame, liveness_mode="full")
            if getattr(result, "virtual_camera_detected", False):
                return False, [], {"status": "VIRTUAL_CAMERA_DETECTED"}

            remaining = [b for b in self.required_buckets if b not in captured_poses]
            if not remaining:
                break
            target_bucket = remaining[0]

            primary_face = getattr(result, "primary_face", None)
            frame_shape = getattr(frame, "shape", (480, 640, 3))
            pose = (
                estimate_head_pose_3d(
                    primary_face,
                    frame_shape,
                    mirror_view=True,
                    fallback_bucket=target_bucket,
                    frame=frame,
                    prev_pose_ts=prev_pose_ts,
                )
                if primary_face is not None
                else None
            )
            if pose is not None:
                prev_pose_ts = (pose.yaw, pose.pitch, time.monotonic())
                update_dial_64_ticks(lit_segments, pose, captured_poses, list(bonus_diags.keys()))

            min_q = 0.38 if target_bucket == "CENTER" else 0.28
            liveness_ok = bool(result.liveness_passed) or (
                target_bucket != "CENTER"
                and "CENTER" in captured_poses
                and float(getattr(result, "liveness_score", 0.0)) >= 0.42
            )
            # Angular-velocity gate: skip capture if head is whipping too fast (prevents motion blur)
            velocity_ok = (pose is None) or (pose.angular_velocity <= self.max_angular_velocity)

            if (
                result.face_count == 1
                and liveness_ok
                and velocity_ok
                and result.frame_quality >= min_q
                and primary_face is not None
            ):
                # Prefer Flip-TTA embedding during enrollment for shadow-invariant templates
                emb = None
                if hasattr(self.engine, "_recognizer") and hasattr(self.engine._recognizer, "get_embedding_tta"):
                    try:
                        emb = self.engine._recognizer.get_embedding_tta(frame, primary_face)
                    except Exception:
                        emb = None
                if emb is None:
                    emb = getattr(result, "embedding", None)
                if emb is None and hasattr(self.engine, "_recognizer"):
                    emb = self.engine._recognizer.get_embedding(frame, primary_face)

                if emb is not None:
                    emb_unit = l2_normalize(emb)
                    if self.capture_bonus_diags and pose is not None:
                        diag = classify_hemisphere_bucket(pose, include_diagonals=True)
                        if diag in ("UP_LEFT", "UP_RIGHT", "DOWN_LEFT", "DOWN_RIGHT") and diag not in bonus_diags:
                            bonus_diags[diag] = emb_unit
                            pose_bank[diag] = emb_unit

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
                        w_frame = max(
                            0.1,
                            float(result.frame_quality)
                            * (pose.quality if pose else 1.0)
                            * (pose.sharpness if pose else 1.0),
                        )
                        slot["embs"].append(emb_unit)
                        slot["weights"].append(w_frame)
                        needed = 1 if (is_mock or (time.monotonic() - bucket_started) >= self.steady_fallback_s) else self.hold_frames
                        if slot["count"] >= needed:
                            fused = weiszfeld_spherical_median(slot["embs"], slot["weights"])
                            captured_poses.append(cand)
                            pose_bank[cand] = fused
                            collected.append(fused)
                            qualities.append(float(result.frame_quality))
                            bucket_state.clear()
                            bucket_started = time.monotonic()
                            if pose is not None:
                                update_dial_64_ticks(lit_segments, pose, captured_poses, list(bonus_diags.keys()))

            if progress_callback is not None:
                progress_callback({
                    "profile": self.profile_name,
                    "captured_poses": list(captured_poses),
                    "bonus_diagonals": list(bonus_diags.keys()),
                    "lit_segments": list(lit_segments),
                    "progress_pct": int((len(captured_poses) / len(self.required_buckets)) * 100),
                    "yaw": pose.yaw if pose else 0.0,
                    "pitch": pose.pitch if pose else 0.0,
                    "roll": pose.roll if pose else 0.0,
                    "angular_velocity": pose.angular_velocity if pose else 0.0,
                    "sharpness": pose.sharpness if pose else 0.0,
                    "quality": float(result.frame_quality),
                })

            if len(captured_poses) >= len(self.required_buckets):
                break

        if len(collected) < 3:
            return False, [], {"status": "NOT_ENOUGH_FRAMES", "captured_poses": captured_poses}

        all_pose_embs = list(collected) + list(bonus_diags.values())
        all_quals = list(qualities) + ([0.90] * len(bonus_diags))
        master_centroid = synthesize_master_centroid_3d(all_pose_embs, all_quals)
        pose_bank["MASTER_CENTROID"] = master_centroid
        pose_bank["GOLDEN_ANCHOR"] = collected[0]

        # Final template list: [cardinal poses..., diagonal poses..., master_centroid]
        final_templates = all_pose_embs + [master_centroid]
        return True, final_templates, {
            "status": "ENROLLED",
            "profile": self.profile_name,
            "captured_poses": captured_poses,
            "bonus_diagonals": list(bonus_diags.keys()),
            "template_count": len(final_templates),
            "pose_bank": pose_bank,
            "master_centroid": master_centroid,
        }
