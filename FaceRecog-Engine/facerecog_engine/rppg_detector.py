# rppg_detector.py — Fast CHROM Remote Photoplethysmography (rPPG) Liveness Detector
import cv2
import numpy as np
from collections import deque
from typing import Any


class CHROMrPPGDetector:
    """
    Extracts sub-dermal blood-volume pulse signals (CHROM algorithm, De Haan & Jeanne 2013)
    from forehead and malar (cheek) vascular ROIs.
      - Fast Early-Window Mode (Frames 3..14): Evaluates chrominance microvascular
        oscillation & physiological G/R/B ratios so fast unlocks (<0.6s) still get
        real rPPG verification.
      - Full Spectral Mode (Frames 15+): Computes Hanning-windowed FFT power spectral
        density in the human cardiac band [0.75 Hz .. 3.2 Hz] (45..192 BPM).
    """

    def __init__(self, fps: float = 25.0, window_size: int = 90):
        self.fps = float(fps)
        self._rgb_history: deque[tuple[float, float, float]] = deque(maxlen=window_size)
        self._last_score: float = 0.0
        self._has_signal: bool = False

    @property
    def has_signal(self) -> bool:
        return self._has_signal

    def reset(self) -> None:
        self._rgb_history.clear()
        self._last_score = 0.0
        self._has_signal = False

    def update(self, frame: np.ndarray, face: Any) -> float:
        if frame is None or face is None or getattr(frame, "size", 0) == 0:
            return self._last_score
        bbox = getattr(face, "bbox", None)
        if bbox is None or len(bbox) < 4:
            return self._last_score

        try:
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = [int(round(float(v))) for v in bbox[:4]]
            bw, bh = max(1, x2 - x1), max(1, y2 - y1)

            # Sample vascular forehead + malar cheek patch
            fx1 = max(0, x1 + int(bw * 0.25))
            fx2 = min(w, x1 + int(bw * 0.75))
            fy1 = max(0, y1 + int(bh * 0.15))
            fy2 = min(h, y1 + int(bh * 0.65))
            if fx2 <= fx1 or fy2 <= fy1:
                return self._last_score

            patch = frame[fy1:fy2, fx1:fx2]
            mean_bgr = cv2.mean(patch)[:3]
            b, g, r = float(mean_bgr[0]), float(mean_bgr[1]), float(mean_bgr[2])
            if (r + g + b) < 15.0:
                return self._last_score

            self._rgb_history.append((r, g, b))
            n = len(self._rgb_history)
            if n < 3:
                return 0.55

            arr = np.asarray(self._rgb_history, dtype=np.float32)
            means = np.mean(arr, axis=0, keepdims=True) + 1e-6
            norm_rgb = arr / means

            # CHROM orthogonal chrominance projection
            xs = 3.0 * norm_rgb[:, 0] - 2.0 * norm_rgb[:, 1]
            ys = 1.5 * norm_rgb[:, 0] + norm_rgb[:, 1] - 1.5 * norm_rgb[:, 2]
            alpha = float(np.std(xs) / (np.std(ys) + 1e-6))
            pulse_sig = xs - alpha * ys
            pulse_std = float(np.std(pulse_sig))

            self._has_signal = True

            # Early-Window Chrominance Liveness (3..14 frames)
            if n < 15:
                # Living skin exhibits subtle microvascular chrominance variance (1e-4 .. 0.04)
                # whereas a frozen photo has ~0 and a flickering screen has unnatural spikes
                if 1e-4 <= pulse_std <= 0.045 and r >= g * 0.85:
                    self._last_score = 0.78
                elif pulse_std < 2e-5:
                    self._last_score = 0.35
                else:
                    self._last_score = 0.66
                return self._last_score

            # Full FFT Cardiac Band [0.75 Hz .. 3.2 Hz] Power Spectral Density (15+ frames)
            detrended = pulse_sig - np.mean(pulse_sig)
            windowed = detrended * np.hanning(n)
            freqs = np.fft.rfftfreq(n, d=1.0 / self.fps)
            mag = np.abs(np.fft.rfft(windowed)) ** 2

            cardiac_mask = (freqs >= 0.75) & (freqs <= 3.20)
            total_power = float(np.sum(mag[1:])) + 1e-9
            cardiac_power = float(np.sum(mag[cardiac_mask])) if np.any(cardiac_mask) else 0.0
            snr_ratio = cardiac_power / total_power

            self._last_score = float(np.clip(0.55 + snr_ratio * 0.65, 0.35, 0.96))
            return self._last_score
        except Exception:
            return self._last_score
