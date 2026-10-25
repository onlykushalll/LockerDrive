import os
import math
import types
import tempfile
import numpy as np

from facerecog_engine.enrollment import (
    PoseEstimate3D,
    estimate_head_pose_3d,
    classify_hemisphere_bucket,
    fuse_burst_embeddings,
    prune_outlier_embeddings,
    synthesize_master_centroid_3d,
    update_adaptive_centroid,
    BiometricEnroller,
)
from facerecog_engine.template_store import TemplateStore
from facerecog_engine.attention_detector import AttentionDetector
from facerecog_engine.rppg_detector import CHROMrPPGDetector
from facerecog_engine.liveness_detector import LivenessDetector


def test_9_angle_hemisphere_classification():
    cases = [
        ("CENTER", 0.0, 0.0),
        ("LEFT", -18.0, 0.0),
        ("RIGHT", 18.0, 0.0),
        ("UP", 0.0, 15.0),
        ("DOWN", 0.0, -15.0),
        ("UP_LEFT", -14.0, 12.0),
        ("UP_RIGHT", 14.0, 12.0),
        ("DOWN_LEFT", -14.0, -12.0),
        ("DOWN_RIGHT", 14.0, -12.0),
    ]
    for expected, y, p in cases:
        pose = PoseEstimate3D(yaw=y, pitch=p)
        assert classify_hemisphere_bucket(pose, include_diagonals=True) == expected


def test_outlier_pruning_and_master_centroid():
    rng = np.random.default_rng(123)
    base = rng.standard_normal(512).astype(np.float32)
    base /= np.linalg.norm(base)
    inliers = [base + rng.normal(0, 0.03, size=512).astype(np.float32) for _ in range(5)]
    outlier = -base  # opposite direction (corrupted frame)
    kept, _ = prune_outlier_embeddings(inliers + [outlier], [0.9] * 6, min_consensus_cosine=0.5)
    assert len(kept) == 5
    centroid = synthesize_master_centroid_3d(inliers + [outlier], [0.9] * 6)
    assert abs(float(np.linalg.norm(centroid)) - 1.0) < 1e-4
    assert float(np.dot(centroid, base)) > 0.92


def test_adaptive_centroid_evolution():
    v = np.ones(512, dtype=np.float32) / math.sqrt(512)
    templates = [v.copy() for _ in range(6)]
    updated = update_adaptive_centroid(templates, v, alpha=0.05)
    assert len(updated) == 6
    assert abs(float(np.linalg.norm(updated[-1])) - 1.0) < 1e-4


def test_template_store_roundtrip():
    rng = np.random.default_rng(99)
    vecs = []
    for _ in range(6):
        v = rng.standard_normal(512).astype(np.float32)
        vecs.append(v / np.linalg.norm(v))
    with tempfile.TemporaryDirectory() as td:
        store = TemplateStore(os.path.join(td, "templates.bin"), secret_key="unit-test-secret")
        store.save("kushal", vecs)
        loaded = store.load()
        assert "kushal" in loaded and len(loaded["kushal"]) == 6
        assert np.allclose(loaded["kushal"][0], vecs[0], atol=1e-5)


def test_12_layer_liveness_and_biometric_enroller():
    liv = LivenessDetector(model_dir="")
    img = np.full((480, 640, 3), 140, dtype=np.uint8)
    face = types.SimpleNamespace(
        bbox=[180, 100, 460, 380],
        kps=np.array([[260, 200], [380, 200], [320, 260], [280, 320], [360, 320]], dtype=np.float32),
        det_score=0.95,
    )
    for _ in range(4):
        liv.score_full(img, face)
    bd = liv.get_layer_breakdown()
    assert len(bd) == 12
    assert all(k in bd for k in ("chrom_rppg", "gaze_attention", "fft_moire", "lbp_texture", "dhash_replay"))
