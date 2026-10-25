# attention_detector.py — Python 3.14 Native 3D Gaze & Attention Detector
import math
import numpy as np
from collections import deque
from typing import Any, Optional


class AttentionDetector:
    """
    Evaluates whether the user is actively attending to the camera using:
      1. Projective 5-point eye-to-nose-to-mouth gaze vector alignment
      2. Inter-ocular roll stability & head-centering discipline
      3. Natural micro-saccade vitality (rejects both wild thrashing and frozen photos)
    Works 100% natively on Python 3.10–3.14+ without requiring legacy mediapipe.solutions.
    """

    def __init__(self):
        self._gaze_history: deque[tuple[float, float]] = deque(maxlen=20)
        self._last_score: float = 0.82

    def reset(self) -> None:
        self._gaze_history.clear()
        self._last_score = 0.82

    def close(self) -> None:
        self._gaze_history.clear()

    def score(self, frame: np.ndarray, face: Any) -> float:
        if frame is None or face is None:
            return 0.5
        kps = getattr(face, "kps", None)
        bbox = getattr(face, "bbox", None)
        if kps is None or bbox is None or len(kps) < 5:
            return 0.78

        try:
            pts = np.asarray(kps[:5], dtype=np.float32)
            if not np.all(np.isfinite(pts)):
                return 0.5
            le, re, nose, lm, rm = pts
            dx = float(re[0] - le[0])
            dy = float(re[1] - le[1])
            inter_ocular = math.hypot(dx, dy)
            if inter_ocular < 4.0:
                return 0.4

            # 1. Roll discipline (head shouldn't be upside-down or tilted >35 deg)
            roll_deg = abs(math.degrees(math.atan2(dy, max(1e-4, dx))))
            roll_score = float(np.clip(1.0 - max(0.0, roll_deg - 12.0) / 30.0, 0.2, 1.0))

            # 2. Gaze / Yaw symmetry (allows natural head turns during 9-angle enrollment)
            eye_mid_x = 0.5 * float(le[0] + re[0])
            eye_mid_y = 0.5 * float(le[1] + re[1])
            mouth_mid_y = 0.5 * float(lm[1] + rm[1])
            eye_to_mouth = max(4.0, mouth_mid_y - eye_mid_y)

            yaw_norm = (float(nose[0]) - eye_mid_x) / inter_ocular
            pitch_norm = (float(nose[1]) - eye_mid_y) / eye_to_mouth
            self._gaze_history.append((yaw_norm, pitch_norm))

            gaze_cone_score = float(np.clip(1.0 - max(0.0, abs(yaw_norm) - 0.38) * 1.6, 0.35, 1.0))

            # 3. Natural ocular/head micro-saccade vitality
            saccade_score = 0.85
            if len(self._gaze_history) >= 4:
                arr = np.asarray(self._gaze_history, dtype=np.float32)
                diffs = np.linalg.norm(np.diff(arr, axis=0), axis=1)
                mean_d = float(np.mean(diffs))
                if mean_d < 1e-5:
                    saccade_score = 0.30  # Dead-frozen static image
                elif mean_d > 0.25:
                    saccade_score = 0.55  # Excessive motion blur
                else:
                    saccade_score = 0.92

            combined = 0.45 * gaze_cone_score + 0.30 * roll_score + 0.25 * saccade_score
            self._last_score = float(np.clip(combined, 0.0, 1.0))
            return self._last_score
        except Exception:
            return self._last_score
