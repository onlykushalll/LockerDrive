import os
import numpy as np
import cv2
from typing import List, Dict, Tuple, Optional, Any
from facerecog_engine.exceptions import ModelLoadError

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False

_ARCFACE_112_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def _bbox_iou(box_a: Any, box_b: Any) -> float:
    """Compute Intersection-over-Union between two [x1, y1, x2, y2] bounding boxes."""
    try:
        ax1, ay1, ax2, ay2 = [float(v) for v in box_a[:4]]
        bx1, by1, bx2, by2 = [float(v) for v in box_b[:4]]
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter
        return float(inter / union) if union > 1e-6 else 0.0
    except Exception:
        return 0.0


class FaceRecognizer:
    """Handles face embedding extraction using AdaFace (ONNX) or ArcFace fallback."""

    def __init__(self, model_dir: str, onnx_providers: Optional[List[str]] = None, face_analysis_app: Optional[Any] = None):
        self.model_dir = model_dir
        self.providers = onnx_providers or ["CPUExecutionProvider"]
        self._adaface_session = None
        self._adaface_input_name = "input"
        self._adaface_flip_fusion_enabled = True
        self._app = face_analysis_app

        self._load_adaface()

    def _load_adaface(self) -> None:
        model_path = os.path.join(self.model_dir, "adaface_r100.onnx")
        if not os.path.exists(model_path):
            return

        if not ONNX_AVAILABLE:
            return

        try:
            sess_opts = ort.SessionOptions()
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._adaface_session = ort.InferenceSession(
                model_path,
                sess_options=sess_opts,
                providers=self.providers
            )
            self._adaface_input_name = self._adaface_session.get_inputs()[0].name
        except Exception:
            self._adaface_session = None

    @property
    def has_adaface(self) -> bool:
        return self._adaface_session is not None

    def get_embedding(self, frame: np.ndarray, face: Any) -> np.ndarray:
        """Extract a normalized 512-dimension embedding vector for the target face."""
        if self.has_adaface:
            face_chip = self._extract_adaface_chip(frame, face)
            if face_chip.size > 0:
                try:
                    return self._get_embedding_adaface(face_chip)
                except Exception:
                    pass

        # Use pre-computed InsightFace ArcFace embedding if already attached to `face`
        if hasattr(face, "normed_embedding") and face.normed_embedding is not None:
            return face.normed_embedding

        if self._app is not None and hasattr(self._app, 'extract_embedding'):
            emb = self._app.extract_embedding(frame, face)
            if emb is not None:
                return emb

        # If face_analysis_app is supplied, run analysis and select the face matching `face.bbox` by IoU
        if self._app is not None:
            try:
                faces = self._app.get(frame)
                candidates = [
                    f for f in faces
                    if hasattr(f, "normed_embedding") and f.normed_embedding is not None
                ]
                if candidates:
                    target_bbox = getattr(face, "bbox", None)
                    if target_bbox is not None and len(candidates) > 1:
                        best_f = max(candidates, key=lambda f: _bbox_iou(target_bbox, getattr(f, "bbox", target_bbox)))
                        return best_f.normed_embedding
                    return candidates[0].normed_embedding
            except Exception:
                pass

        raise ValueError("No face recognition model available or face is not valid.")

    def _extract_adaface_chip(self, frame: np.ndarray, face: Any) -> np.ndarray:
        kps = getattr(face, "kps", None)
        if kps is not None:
            try:
                src = np.asarray(kps, dtype=np.float32)
                if src.shape == (5, 2):
                    matrix, _ = cv2.estimateAffinePartial2D(src, _ARCFACE_112_DST, method=cv2.LMEDS)
                    if matrix is not None:
                        return cv2.warpAffine(frame, matrix, (112, 112), borderValue=0.0)
            except Exception:
                pass

        try:
            x1, y1, x2, y2 = [int(v) for v in face.bbox]
        except Exception:
            return np.empty((0, 0, 3), dtype=frame.dtype)

        h, w = frame.shape[:2]
        face_roi = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
        if face_roi.size == 0:
            return face_roi
        return cv2.resize(face_roi, (112, 112), interpolation=cv2.INTER_LINEAR)

    def _get_embedding_adaface(self, face_roi: np.ndarray) -> np.ndarray:
        resized = cv2.resize(face_roi, (112, 112))
        emb = self._run_adaface_chip(resized)

        if self._adaface_flip_fusion_enabled:
            flipped = cv2.flip(resized, 1)
            flipped_emb = self._run_adaface_chip(flipped)
            emb = emb + flipped_emb

        norm = np.linalg.norm(emb)
        if norm < 1e-6:
            return emb
        return emb / norm

    def _run_adaface_chip(self, face_chip_bgr: np.ndarray) -> np.ndarray:
        rgb_chip = cv2.cvtColor(face_chip_bgr, cv2.COLOR_BGR2RGB)
        rgb = rgb_chip.astype(np.float32)
        rgb = ((rgb / 255.0) - 0.5) / 0.5
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis]
        output = self._adaface_session.run(None, {self._adaface_input_name: blob})[0]
        return np.asarray(output[0], dtype=np.float32)

    @staticmethod
    def compute_similarity(emb1: np.ndarray, emb2: np.ndarray) -> float:
        """Compute cosine similarity between two embeddings with L2 normalization
        and strict NaN/Inf/dimension guards."""
        try:
            a = np.asarray(emb1, dtype=np.float32).ravel()
            b = np.asarray(emb2, dtype=np.float32).ravel()
            if a.size == 0 or a.shape != b.shape:
                return 0.0
            n1 = float(np.linalg.norm(a))
            n2 = float(np.linalg.norm(b))
            if not (np.isfinite(n1) and np.isfinite(n2)) or n1 < 1e-12 or n2 < 1e-12:
                return 0.0
            sim = float(np.dot(a / n1, b / n2))
            return sim if np.isfinite(sim) else 0.0
        except Exception:
            return 0.0

    def match_face(self, embedding: np.ndarray, reference_templates: Dict[str, List[np.ndarray]]) -> Tuple[Optional[str], float]:
        """
        Compare target embedding with reference templates list.
        Supports v3.0/v4.0 multi-pose + master-centroid fusion:
          - Evaluates cosine similarity against all stored templates.
          - When >=6 templates exist (5 pose buckets + 1 master centroid at index -1)
            and both the best pose template and master centroid strongly agree (>=0.68),
            applies a bounded multi-angle agreement boost for fast off-axis unlock
            while never inflating below-threshold stranger scores.
        Returns Tuple of (matched_user_id, highest_score).
        """
        best_id = None
        best_score = 0.0

        for user_id, templates in reference_templates.items():
            if not templates:
                continue
            user_scores = [self.compute_similarity(embedding, t) for t in templates]
            user_best = max(user_scores)

            # ArcFace (w600k_r50) genuine same-person cosine similarity is 0.48..0.88
            # (impostor distribution is -0.10..0.32). Calibrate genuine matches >= 0.48
            # smoothly into 0.75..0.98 while leaving impostor scores (<0.48) untouched.
            if not self.has_adaface and user_best >= 0.48 and user_best < 0.75:
                user_best = 0.75 + (user_best - 0.48) * (0.20 / 0.27)

            if len(user_scores) >= 6:
                pose_scores = sorted(user_scores[:-1], reverse=True)
                pose_best = pose_scores[0]
                pose_second = pose_scores[1] if len(pose_scores) > 1 else pose_best
                centroid_score = user_scores[-1]
                if pose_best >= 0.68 and centroid_score >= 0.68:
                    # Blend top-2 pose agreement + master centroid boost
                    agreement = 0.5 * (pose_best + max(pose_second, centroid_score))
                    user_best = min(1.0, max(user_best, pose_best + 0.025 * agreement))

            if user_best > best_score:
                best_score = user_best
                best_id = user_id

        return best_id, float(best_score)
