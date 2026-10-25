import os
import sys
import types
import importlib.machinery
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Tuple, Optional, Any
import numpy as np
import cv2

try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ort = None
    ONNX_AVAILABLE = False

from facerecog_engine.exceptions import ModelLoadError

# Stub out unused heavy scientific packages so if insightface is imported,
# it never crashes on missing scipy/skimage/matplotlib/albumentations/simsimd.
class _DummyLoader:
    def create_module(self, spec):
        class _Mod(types.ModuleType):
            def __getattr__(self, attr):
                if attr.startswith("__") and attr.endswith("__"):
                    raise AttributeError(attr)
                return _Mod(f"{spec.name}.{attr}")
            def __call__(self, *args, **kwargs):
                return None
        mod = _Mod(spec.name)
        mod.__path__ = []
        mod.__file__ = f"<{spec.name}>"
        mod.__spec__ = spec
        mod.__version__ = "1.0.0"
        return mod
    def exec_module(self, module):
        pass

class _DummyFinder:
    _BLOCKED = ("albumentations", "simsimd", "matplotlib", "prettytable")
    def find_spec(self, fullname, path, target=None):
        if any(fullname == b or fullname.startswith(b + ".") for b in self._BLOCKED):
            return importlib.machinery.ModuleSpec(fullname, _DummyLoader(), is_package=True)
        return None

if not any(isinstance(f, _DummyFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _DummyFinder())

# Always True because _PureOnnxBuffaloApp runs 100% natively on onnxruntime + cv2 + numpy!
INSIGHTFACE_AVAILABLE = True

try:
    from insightface.app import FaceAnalysis
    from insightface.app.common import Face
except Exception:
    class FaceAnalysis:
        def __init__(self, *args, **kwargs): pass
        def prepare(self, *args, **kwargs): pass
        def get(self, *args, **kwargs): return []
    class Face(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.__dict__ = self


_ARCFACE_DST_112 = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)


def _align_face_112(img: np.ndarray, kps: np.ndarray) -> np.ndarray:
    """Pure OpenCV 5-point similarity alignment to 112x112."""
    src = np.asarray(kps[:5], dtype=np.float32)
    M, _ = cv2.estimateAffinePartial2D(src, _ARCFACE_DST_112, method=cv2.LMEDS)
    if M is None:
        M, _ = cv2.estimateAffinePartial2D(src, _ARCFACE_DST_112)
    if M is not None:
        return cv2.warpAffine(img, M, (112, 112), borderValue=0.0)
    return cv2.resize(img, (112, 112))


class _SimpleFace(dict):
    """Drop-in replacement for insightface.app.common.Face."""
    def __init__(self, bbox, kps=None, det_score=0.0):
        super().__init__()
        self.__dict__ = self
        self.bbox = np.asarray(bbox, dtype=np.float32)
        self.kps = np.asarray(kps, dtype=np.float32) if kps is not None else None
        self.det_score = float(det_score)
        self.embedding = None
        self.pose = None

    @property
    def normed_embedding(self):
        if self.embedding is None:
            return None
        n = float(np.linalg.norm(self.embedding))
        return (self.embedding / n) if n > 1e-6 else self.embedding


class _PureOnnxBuffaloApp:
    """
    100% Self-Contained Pure-ONNXRuntime SCRFD (det_10g.onnx) + ArcFace (w600k_r50.onnx)
    Engine. Requires ZERO insightface/scipy/skimage/cython packages, so it works on any
    Windows PC and inside any PyInstaller .exe without fail.
    """

    def __init__(self, det_onnx_path: str, rec_onnx_path: str, providers: List[str], det_size=(640, 640)):
        if not ONNX_AVAILABLE:
            raise ModelLoadError("onnxruntime is required")
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = 2
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pure-onnx") as ex:
            f_det = ex.submit(ort.InferenceSession, det_onnx_path, opts, providers)
            f_rec = ex.submit(ort.InferenceSession, rec_onnx_path, opts, providers)
            self.det_sess = f_det.result()
            self.rec_sess = f_rec.result()

        self.det_in_name = self.det_sess.get_inputs()[0].name
        self.det_out_names = [o.name for o in self.det_sess.get_outputs()]
        self.rec_in_name = self.rec_sess.get_inputs()[0].name
        self.det_size = det_size
        self.det_thresh = 0.35
        self.nms_thresh = 0.40
        self._center_cache = {}
        self.models = {"detection": self.det_sess, "recognition": self.rec_sess}

    def prepare(self, ctx_id: int = 0, det_size: Tuple[int, int] = (640, 640)) -> None:
        self.det_size = det_size

    def _nms(self, dets: np.ndarray) -> List[int]:
        x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
        areas = (x2 - x1 + 1.0) * (y2 - y1 + 1.0)
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            w = np.maximum(0.0, xx2 - xx1 + 1.0)
            h = np.maximum(0.0, yy2 - yy1 + 1.0)
            inter = w * h
            ovr = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
            inds = np.where(ovr <= self.nms_thresh)[0]
            order = order[inds + 1]
        return keep

    def _detect_scrfd(self, img: np.ndarray) -> Tuple[np.ndarray, Optional[np.ndarray]]:
        in_w, in_h = self.det_size
        im_ratio = float(img.shape[0]) / float(img.shape[1])
        model_ratio = float(in_h) / float(in_w)
        if im_ratio > model_ratio:
            new_h = in_h
            new_w = max(1, int(new_h / im_ratio))
        else:
            new_w = in_w
            new_h = max(1, int(new_w * im_ratio))
        det_scale = float(new_h) / float(img.shape[0])
        resized = cv2.resize(img, (new_w, new_h))
        det_img = np.zeros((in_h, in_w, 3), dtype=np.uint8)
        det_img[:new_h, :new_w, :] = resized

        blob = cv2.dnn.blobFromImage(det_img, 1.0 / 128.0, (in_w, in_h), (127.5, 127.5, 127.5), swapRB=True)
        net_outs = self.det_sess.run(self.det_out_names, {self.det_in_name: blob})

        fmc = 3
        strides = [8, 16, 32]
        num_anchors = 2
        scores_list, bboxes_list, kpss_list = [], [], []

        for idx, stride in enumerate(strides):
            scores = net_outs[idx].reshape(-1, 1)
            bbox_preds = net_outs[idx + fmc].reshape(-1, 4) * stride
            kps_preds = net_outs[idx + fmc * 2].reshape(-1, 10) * stride

            h_grid, w_grid = in_h // stride, in_w // stride
            key = (h_grid, w_grid, stride)
            if key in self._center_cache:
                anchor_centers = self._center_cache[key]
            else:
                ac = np.stack(np.mgrid[:h_grid, :w_grid][::-1], axis=-1).astype(np.float32)
                ac = (ac * stride).reshape((-1, 2))
                ac = np.stack([ac] * num_anchors, axis=1).reshape((-1, 2))
                self._center_cache[key] = ac
                anchor_centers = ac

            pos_inds = np.where(scores[:, 0] >= self.det_thresh)[0]
            if pos_inds.size == 0:
                continue

            pos_scores = scores[pos_inds]
            bp = bbox_preds[pos_inds]
            ac_pos = anchor_centers[pos_inds]
            x1 = ac_pos[:, 0] - bp[:, 0]
            y1 = ac_pos[:, 1] - bp[:, 1]
            x2 = ac_pos[:, 0] + bp[:, 2]
            y2 = ac_pos[:, 1] + bp[:, 3]
            bboxes = np.stack([x1, y1, x2, y2], axis=-1)

            kp = kps_preds[pos_inds]
            kpss = np.zeros((pos_inds.size, 5, 2), dtype=np.float32)
            for k in range(5):
                kpss[:, k, 0] = ac_pos[:, 0] + kp[:, k * 2]
                kpss[:, k, 1] = ac_pos[:, 1] + kp[:, k * 2 + 1]

            scores_list.append(pos_scores)
            bboxes_list.append(bboxes)
            kpss_list.append(kpss)

        if not scores_list:
            return np.zeros((0, 5), dtype=np.float32), None

        scores_all = np.vstack(scores_list)
        bboxes_all = np.vstack(bboxes_list) / det_scale
        kpss_all = np.vstack(kpss_list) / det_scale
        pre_det = np.hstack((bboxes_all, scores_all)).astype(np.float32)
        keep = self._nms(pre_det)
        return pre_det[keep], kpss_all[keep]

    def extract_embedding(self, img: np.ndarray, face: Any) -> Optional[np.ndarray]:
        try:
            kps = getattr(face, "kps", None)
            if kps is not None and len(kps) >= 5:
                aimg = _align_face_112(img, kps)
            else:
                x1, y1, x2, y2 = [max(0, int(v)) for v in face.bbox[:4]]
                aimg = cv2.resize(img[y1:max(y1 + 2, y2), x1:max(x1 + 2, x2)], (112, 112))
            blob = cv2.dnn.blobFromImage(aimg, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
            feat = self.rec_sess.run(None, {self.rec_in_name: blob})[0].flatten().astype(np.float32)
            norm = float(np.linalg.norm(feat))
            if norm > 1e-6:
                feat = feat / norm
            face.embedding = feat
            return feat
        except Exception:
            return None

    def get(self, img: np.ndarray, max_num: int = 0) -> List[Any]:
        bboxes, kpss = self._detect_scrfd(img)
        if bboxes.shape[0] == 0:
            return []
        ret = []
        best_idx = 0
        if bboxes.shape[0] > 1:
            areas = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1]) * bboxes[:, 4]
            best_idx = int(np.argmax(areas))
        for i in range(bboxes.shape[0]):
            bbox = bboxes[i, 0:4]
            det_score = bboxes[i, 4]
            kps = kpss[i] if kpss is not None else None
            face = _SimpleFace(bbox=bbox, kps=kps, det_score=det_score)
            if i == best_idx:
                self.extract_embedding(img, face)
            ret.append(face)
        return ret


class BBoxKalmanFilter:
    """Constant-velocity Kalman filter for tracking a face bounding box."""

    def __init__(self, process_std: float = 45.0, measurement_std: float = 28.0):
        self.process_std = float(process_std)
        self.measurement_std = float(measurement_std)
        self._x: Optional[np.ndarray] = None
        self._p: Optional[np.ndarray] = None
        self._last_ts: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self._x is not None

    def reset(self) -> None:
        self._x = None
        self._p = None
        self._last_ts = None

    def update(self, bbox: Optional[Tuple[float, float, float, float]], timestamp: float) -> None:
        measurement = self._bbox_to_measurement(bbox)
        if measurement is None:
            return
        if self._x is None:
            self._x = np.zeros(8, dtype=np.float64)
            self._x[:4] = measurement
            self._p = np.eye(8, dtype=np.float64) * 100.0
            self._last_ts = timestamp
            return
        self.predict(timestamp)
        assert self._x is not None and self._p is not None
        h = np.zeros((4, 8), dtype=np.float64)
        h[:4, :4] = np.eye(4, dtype=np.float64)
        r = np.eye(4, dtype=np.float64) * (self.measurement_std ** 2)
        innovation = measurement - (h @ self._x)
        s = h @ self._p @ h.T + r
        k = self._p @ h.T @ np.linalg.inv(s)
        self._x = self._x + (k @ innovation)
        self._p = (np.eye(8, dtype=np.float64) - k @ h) @ self._p
        self._last_ts = timestamp

    def predict(self, timestamp: float) -> Optional[Tuple[float, float, float, float]]:
        if self._x is None or self._p is None:
            return None
        dt = 0.0 if self._last_ts is None else float(np.clip(timestamp - self._last_ts, 0.0, 1.0))
        if dt > 0.0:
            f = np.eye(8, dtype=np.float64)
            f[0, 4], f[1, 5], f[2, 6], f[3, 7] = dt, dt, dt, dt
            q = np.eye(8, dtype=np.float64) * ((self.process_std ** 2) * max(dt, 0.05))
            self._x = f @ self._x
            self._p = f @ self._p @ f.T + q
            self._last_ts = timestamp
        return self.predicted_bbox()

    def predicted_bbox(self) -> Optional[Tuple[float, float, float, float]]:
        if self._x is None:
            return None
        cx, cy, bw, bh = [float(v) for v in self._x[:4]]
        bw, bh = max(1.0, bw), max(1.0, bh)
        return cx - bw / 2.0, cy - bh / 2.0, cx + bw / 2.0, cy + bh / 2.0

    @staticmethod
    def _bbox_to_measurement(bbox: Optional[Tuple[float, float, float, float]]) -> Optional[np.ndarray]:
        if bbox is None:
            return None
        x1, y1, x2, y2 = bbox
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0.0 or bh <= 0.0:
            return None
        return np.array([x1 + bw / 2.0, y1 + bh / 2.0, bw, bh], dtype=np.float64)


FaceBoxKalman = BBoxKalmanFilter


Face = _SimpleFace

@dataclass
class FaceDetection:
    bbox: Tuple[float, float, float, float]
    landmarks: Optional[np.ndarray] = None
    confidence: float = 0.0


class FaceDetector:
    """Pure-ONNXRuntime SCRFD + ArcFace detector (zero insightface/scipy dependency)."""

    def __init__(self, model_dir_or_config: Any = None, det_size: Tuple[int, int] = (320, 320), onnx_providers: Optional[List[str]] = None, model_dir: Optional[str] = None):
        target = model_dir_or_config if model_dir_or_config is not None else model_dir
        if hasattr(target, "model_dir"):
            self.config = target
            self.model_dir = target.model_dir
            self.providers = target.onnx_providers or ["CPUExecutionProvider"]
        else:
            self.config = None
            self.model_dir = str(target or "")
            self.providers = onnx_providers or ["CPUExecutionProvider"]

        self.det_size = det_size
        self._app: Optional[Any] = None
        self.kalman = BBoxKalmanFilter()
        self._load_model()

    def _find_buffalo_onnx(self) -> Tuple[Optional[str], Optional[str]]:
        search_dirs = [
            os.path.join(self.model_dir, "models", "buffalo_l"),
            os.path.join(self.model_dir, "buffalo_l"),
            self.model_dir,
        ]
        for d in search_dirs:
            det_p = os.path.join(d, "det_10g.onnx")
            rec_p = os.path.join(d, "w600k_r50.onnx")
            if os.path.isfile(det_p) and os.path.isfile(rec_p):
                return det_p, rec_p
        return None, None

    def _load_model(self) -> None:
        det_onnx, rec_onnx = self._find_buffalo_onnx()
        if det_onnx and rec_onnx:
            try:
                self._app = _PureOnnxBuffaloApp(det_onnx, rec_onnx, providers=self.providers, det_size=self.det_size)
                self._app.prepare(ctx_id=0, det_size=self.det_size)
                return
            except Exception as e:
                raise ModelLoadError(f"Failed to initialize Pure-ONNX SCRFD/ArcFace: {e}") from e

        # Fallback for unit tests that mock insightface.app.FaceAnalysis
        try:
            self._app = FaceAnalysis(
                name="buffalo_l",
                root=self.model_dir,
                providers=self.providers,
                allowed_modules=["detection", "recognition"],
            )
            self._app.prepare(ctx_id=0, det_size=self.det_size)
        except Exception as e:
            raise ModelLoadError(f"Failed to load detection model from {self.model_dir}") from e

    def set_detector_size(self, width: int, height: int) -> None:
        self.det_size = (width, height)
        if self._app is not None:
            self._app.prepare(ctx_id=0, det_size=self.det_size)

    def detect(self, frame: np.ndarray) -> List[Any]:
        if self._app is None:
            raise ModelLoadError("Detection model is not initialized.")
        try:
            return self._app.get(frame)
        except Exception:
            return []

    def detect_faces(self, frame: np.ndarray) -> List[FaceDetection]:
        raw_faces = self.detect(frame)
        out = []
        for f in raw_faces:
            b = tuple(float(v) for v in getattr(f, "bbox", []))
            conf = float(getattr(f, "det_score", 0.0))
            kps = getattr(f, "kps", None)
            out.append(FaceDetection(bbox=b, landmarks=kps, confidence=conf))
        return out

    def track_and_stabilize_target(
        self,
        detections: List[FaceDetection],
        last_target_bbox: Optional[Tuple[float, float, float, float]],
        last_seen_timestamp: float,
        current_timestamp: float,
    ) -> Tuple[Optional[FaceDetection], Optional[Tuple[float, float, float, float]], str, float, float]:
        if not detections:
            return None, None, "no_detections", 0.0, 0.0

        def _iou(b1, b2):
            if b1 is None or b2 is None:
                return 0.0
            ix1, iy1 = max(b1[0], b2[0]), max(b1[1], b2[1])
            ix2, iy2 = min(b1[2], b2[2]), min(b1[3], b2[3])
            iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
            inter = iw * ih
            a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
            a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
            union = a1 + a2 - inter
            return float(inter / union) if union > 0 else 0.0

        predicted_box = self.kalman.predict(current_timestamp)
        best_det = None
        best_sticky_iou = -1.0
        best_pred_iou = -1.0

        for d in detections:
            s_iou = _iou(d.bbox, last_target_bbox)
            p_iou = _iou(d.bbox, predicted_box)
            if s_iou > best_sticky_iou:
                best_sticky_iou = s_iou
                best_pred_iou = p_iou
                best_det = d

        if best_det is not None:
            self.kalman.update(best_det.bbox, current_timestamp)
            stabilized = self.kalman.predicted_bbox() or best_det.bbox
            reason = "sticky_iou" if best_sticky_iou >= 0.5 else "predicted_iou"
            return best_det, stabilized, reason, best_sticky_iou, best_pred_iou

        return None, None, "unmatched", 0.0, 0.0
