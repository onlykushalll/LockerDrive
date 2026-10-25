import cv2
import math
import numpy as np

# Pre-computed gamma LUT cache so low-light frames never run np.power per pixel
_GAMMA_LUT_CACHE = {}


def _get_gamma_lut(gamma: float) -> np.ndarray:
    key = round(float(gamma), 2)
    lut = _GAMMA_LUT_CACHE.get(key)
    if lut is None:
        lut = np.array(
            [np.clip(((i / 255.0) ** key) * 255.0, 0.0, 255.0) for i in range(256)],
            dtype=np.uint8,
        )
        _GAMMA_LUT_CACHE[key] = lut
    return lut


def enhance_low_light(frame: np.ndarray, clahe_obj: cv2.CLAHE = None) -> np.ndarray:
    """
    Applies adaptive CLAHE + fast LUT gamma tuning on L channel of BGR image
    if it is in low light (mean_l <= 100).
    """
    try:
        if frame is None or frame.size == 0 or frame.shape[0] == 0 or frame.shape[1] == 0:
            return frame
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        mean_l = float(np.mean(l))
        if mean_l > 100.0:
            return frame

        # Instantiate CLAHE if not pre-allocated
        if clahe_obj is None:
            clip = 2.5 if mean_l < 65.0 else 2.0
            clahe_obj = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))

        l_enhanced = clahe_obj.apply(l)
        if mean_l < 75.0:
            # Smooth adaptive gamma curve (0.55 for very dark, up to 0.82 for dim)
            gamma = max(0.55, min(0.82, math.log(115.0 / 255.0) / math.log(max(mean_l, 12.0) / 255.0)))
            l_enhanced = cv2.LUT(l_enhanced, _get_gamma_lut(gamma))

        enhanced = cv2.merge([l_enhanced, a, b])
        return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
    except Exception:
        return frame


def resize_with_aspect_ratio(image: np.ndarray, target_size: int, interpolation=cv2.INTER_LINEAR) -> np.ndarray:
    """
    Resizes an image with aspect ratio preservation (padding to square).
    """
    h, w = image.shape[:2]
    if h == 0 or w == 0:
        return np.zeros((target_size, target_size, image.shape[2] if len(image.shape) > 2 else 1), dtype=image.dtype)
    scale = target_size / max(h, w)
    nh, nw = int(h * scale), int(w * scale)
    nh = max(1, nh)
    nw = max(1, nw)
    resized = cv2.resize(image, (nw, nh), interpolation=interpolation)

    pad_h = (target_size - nh) // 2
    pad_w = (target_size - nw) // 2

    padded = cv2.copyMakeBorder(
        resized,
        pad_h,
        target_size - nh - pad_h,
        pad_w,
        target_size - nw - pad_w,
        cv2.BORDER_CONSTANT,
        value=[0, 0, 0]
    )
    return padded
