from setuptools import setup, find_packages

setup(
    name="facerecog_engine",
    version="1.1.0",
    description="Standalone Computer Vision, 3D Multi-Pose Face ID, and 12-Layer Passive Liveness Detection Engine",
    author="Kushal",
    packages=find_packages(),
    install_requires=[
        "numpy>=1.20.0",
        "opencv-python>=4.5.0",
        "onnxruntime>=1.15.0",
        "scipy>=1.9.0",
        "insightface>=0.7.3",
        "cryptography>=41.0.0",
    ],
    extras_require={
        "mediapipe": ["mediapipe>=0.10.0"],
    },
    python_requires=">=3.8",
)
