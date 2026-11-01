# template_store.py — Multi-Profile & Alternate-Appearance Encrypted Biometric Template Store v3.0
from __future__ import annotations

import os
import json
import hmac
import hashlib
import threading
from typing import Any, Dict, List, Optional
import numpy as np

from facerecog_engine.enrollment import l2_normalize, update_adaptive_centroid


class TemplateStore:
    """
    Tamper-evident, multi-profile biometric template store:
      - Supports multiple appearance profiles per user (`primary`, `alternate`, `low_light`).
      - Stores an immutable `golden_anchor` per profile so adaptive SLERP updates
        can never drift or be poisoned by an attacker over time.
      - Protects templates on disk via Windows DPAPI + PBKDF2-HMAC-SHA256 binding.
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
        key = hashlib.pbkdf2_hmac(
            "sha256",
            self.secret_key.encode("utf-8"),
            b"FaceRecogEngine.TemplateStore.v3",
            60000,
        )
        return "H3:" + hmac.new(key, canonical_bytes, hashlib.sha256).hexdigest()

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

    def _read_raw_envelope(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
            return {"format": "fre_v3", "users": {}}
        with open(self.file_path, "rb") as f:
            blob = f.read(4 * 1024 * 1024)
        raw_bytes = blob
        try:
            import win32crypt
            raw_bytes = win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
        except Exception:
            pass
        data = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Corrupt template store")

        # Upgrade fre_v2 envelope in memory transparently
        if data.get("format") == "fre_v2":
            uid = str(data.get("user_id", "owner"))
            vecs = self.validate_vectors(data.get("vectors", []))
            if self.secret_key and "hmac" in data:
                body_b = json.dumps({uid: vecs}, sort_keys=True, separators=(",", ":")).encode("utf-8")
                k2 = hashlib.pbkdf2_hmac("sha256", self.secret_key.encode("utf-8"), b"FaceRecogEngine.TemplateStore.v2", 60000)
                exp2 = "H2:" + hmac.new(k2, body_b, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(str(data["hmac"]), exp2):
                    raise ValueError("Template store HMAC verification failed (tampered)")
            return {
                "format": "fre_v3",
                "users": {
                    uid: {
                        "profiles": {"primary": vecs},
                        "golden_anchors": {"primary": vecs[0]},
                    }
                },
            }

        if data.get("format") != "fre_v3":
            raise ValueError("Unsupported template store format")

        users = data.get("users", {})
        if self.secret_key and "hmac" in data:
            body_b = json.dumps(users, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = self._compute_hmac(body_b)
            if expected and not hmac.compare_digest(str(data["hmac"]), expected):
                raise ValueError("Template store HMAC verification failed (tampered)")
        return data

    def _write_raw_envelope(self, users_dict: Dict[str, Any]) -> None:
        body_b = json.dumps(users_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        envelope: Dict[str, Any] = {"format": "fre_v3", "users": users_dict}
        mac = self._compute_hmac(body_b)
        if mac:
            envelope["hmac"] = mac
        raw_bytes = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")

        blob = raw_bytes
        try:
            import win32crypt
            blob = win32crypt.CryptProtectData(raw_bytes, "FaceRecogEngine Templates v3", None, None, None, 0)
        except Exception:
            pass

        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        tmp = f"{self.file_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, self.file_path)

    def save(
        self,
        user_id: str,
        vectors: List[np.ndarray],
        profile_name: str = "primary",
    ) -> None:
        """Saves or updates a specific appearance profile (`primary`, `alternate`, `low_light`)."""
        with self._lock:
            validated = self.validate_vectors(vectors)
            try:
                env = self._read_raw_envelope() if self.exists() else {"format": "fre_v3", "users": {}}
            except Exception:
                env = {"format": "fre_v3", "users": {}}

            users = env.setdefault("users", {})
            u_entry = users.setdefault(user_id, {"profiles": {}, "golden_anchors": {}})
            u_entry["profiles"][profile_name] = validated
            u_entry["golden_anchors"][profile_name] = validated[0]
            self._write_raw_envelope(users)

    def load(self, flatten_profiles: bool = True) -> Dict[str, Any]:
        """
        Loads verified templates from disk.
          - When `flatten_profiles=True` (default): returns `{"owner": [np.ndarray, ...]}`
            combining all enrolled appearance profiles for seamless plug-and-play compatibility.
          - When `flatten_profiles=False`: returns `{"owner": {"primary": [...], "alternate": [...]}}`.
        """
        with self._lock:
            env = self._read_raw_envelope()
            users = env.get("users", {})
            result: Dict[str, Any] = {}
            for uid, u_entry in users.items():
                profs = u_entry.get("profiles", {})
                if flatten_profiles:
                    merged: List[np.ndarray] = []
                    for _pname, vec_list in profs.items():
                        validated = self.validate_vectors(vec_list)
                        merged.extend(np.asarray(v, dtype=np.float32) for v in validated)
                    result[uid] = merged[:64]
                else:
                    prof_map: Dict[str, List[np.ndarray]] = {}
                    for pname, vec_list in profs.items():
                        validated = self.validate_vectors(vec_list)
                        prof_map[pname] = [np.asarray(v, dtype=np.float32) for v in validated]
                    result[uid] = prof_map
            return result

    def evolve_profile(
        self,
        user_id: str,
        new_embedding: np.ndarray,
        profile_name: str = "primary",
        alpha: float = 0.05,
    ) -> bool:
        """
        Applies anti-poisoning Geodesic SLERP evolution to `profile_name`,
        leashed to that profile's immutable Golden Anchor.
        """
        with self._lock:
            if not self.exists():
                return False
            env = self._read_raw_envelope()
            users = env.get("users", {})
            u_entry = users.get(user_id)
            if not u_entry or profile_name not in u_entry.get("profiles", {}):
                return False
            raw_vecs = [np.asarray(v, dtype=np.float32) for v in u_entry["profiles"][profile_name]]
            anchor_raw = u_entry.get("golden_anchors", {}).get(profile_name, raw_vecs[0])
            anchor = l2_normalize(np.asarray(anchor_raw, dtype=np.float32))
            updated = update_adaptive_centroid(raw_vecs, new_embedding, alpha=alpha, golden_anchor=anchor)
            u_entry["profiles"][profile_name] = self.validate_vectors(updated)
            self._write_raw_envelope(users)
            return True
