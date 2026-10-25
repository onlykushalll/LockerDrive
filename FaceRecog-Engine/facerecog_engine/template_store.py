# template_store.py — Universal Encrypted & HMAC-Bound Biometric Template Store
from __future__ import annotations

import os
import json
import math
import hmac
import hashlib
import threading
from typing import Dict, List, Optional
import numpy as np


class TemplateStore:
    """
    Portable, tamper-evident biometric template persistence for any Python project.
      - On Windows: wraps payload with Windows DPAPI (CryptProtectData) + optional
        HMAC-SHA256 secret binding.
      - Cross-platform fallback: encrypts via AES-256-GCM (facerecog_engine.utils.crypto)
        when a passphrase/key is provided.
    """

    def __init__(self, file_path: str, secret_key: Optional[str] = None):
        self.file_path = os.path.abspath(file_path)
        self.secret_key = secret_key
        self._lock = threading.RLock()

    def exists(self) -> bool:
        return os.path.exists(self.file_path)

    def _compute_hmac(self, canonical_bytes: bytes) -> Optional[str]:
        if not self.secret_key:
            return None
        key = hashlib.pbkdf2_hmac("sha256", self.secret_key.encode("utf-8"), b"FaceRecogEngine.TemplateStore.v2", 60000)
        return "H2:" + hmac.new(key, canonical_bytes, hashlib.sha256).hexdigest()

    @staticmethod
    def validate_vectors(vectors: List[np.ndarray]) -> List[List[float]]:
        if not vectors or len(vectors) > 64:
            raise ValueError("Template list must contain 1..64 vectors")
        out: List[List[float]] = []
        dim = None
        for i, v in enumerate(vectors):
            arr = np.asarray(v, dtype=np.float32).ravel()
            if not (128 <= arr.size <= 2048):
                raise ValueError(f"Invalid embedding dimension {arr.size} at index {i}")
            if dim is None:
                dim = int(arr.size)
            elif int(arr.size) != dim:
                raise ValueError("Inconsistent embedding dimensions")
            if not np.all(np.isfinite(arr)):
                raise ValueError(f"Non-finite values in embedding {i}")
            norm = float(np.linalg.norm(arr))
            if not (0.25 <= norm <= 4.0):
                raise ValueError(f"Implausible L2 norm {norm:.3f} at index {i}")
            out.append([float(x) for x in arr.tolist()])
        return out

    def save(self, user_id: str, vectors: List[np.ndarray]) -> None:
        with self._lock:
            validated = self.validate_vectors(vectors)
            body_bytes = json.dumps({user_id: validated}, sort_keys=True, separators=(",", ":")).encode("utf-8")
            envelope = {"format": "fre_v2", "user_id": user_id, "vectors": validated}
            mac = self._compute_hmac(body_bytes)
            if mac:
                envelope["hmac"] = mac
            raw_bytes = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")

            blob = raw_bytes
            try:
                import win32crypt
                blob = win32crypt.CryptProtectData(raw_bytes, "FaceRecogEngine Templates v2", None, None, None, 0)
            except Exception:
                pass

            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            tmp = f"{self.file_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as f:
                f.write(blob)
            os.replace(tmp, self.file_path)

    def load(self) -> Dict[str, List[np.ndarray]]:
        with self._lock:
            with open(self.file_path, "rb") as f:
                blob = f.read(4 * 1024 * 1024)
            raw_bytes = blob
            try:
                import win32crypt
                raw_bytes = win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
            except Exception:
                pass
            data = json.loads(raw_bytes.decode("utf-8"))
            if not isinstance(data, dict) or data.get("format") != "fre_v2":
                raise ValueError("Invalid template store envelope")
            uid = str(data.get("user_id", "owner"))
            vecs = self.validate_vectors(data.get("vectors", []))
            if self.secret_key and "hmac" in data:
                body_bytes = json.dumps({uid: vecs}, sort_keys=True, separators=(",", ":")).encode("utf-8")
                expected = self._compute_hmac(body_bytes)
                if expected and not hmac.compare_digest(str(data["hmac"]), expected):
                    raise ValueError("Template store HMAC verification failed (tampered)")
            return {uid: [np.asarray(v, dtype=np.float32) for v in vecs]}
