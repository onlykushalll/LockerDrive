# -*- mode: python ; coding: utf-8 -*-
import os
import sys
sys.setrecursionlimit(20000)

from PyInstaller.utils.hooks import collect_all

datas = [
    ('ui/index.html', 'ui'),
    ('assets', 'assets'),
    ('FaceRecog-Engine/models/antispoof_minifasv2.onnx', 'FaceRecog-Engine/models'),
    ('FaceRecog-Engine/models/midas_v21_small_256.onnx', 'FaceRecog-Engine/models'),
    ('FaceRecog-Engine/models/models/buffalo_l/det_10g.onnx', 'FaceRecog-Engine/models/models/buffalo_l'),
    ('FaceRecog-Engine/models/models/buffalo_l/w600k_r50.onnx', 'FaceRecog-Engine/models/models/buffalo_l'),
]
binaries = []
hiddenimports = [
    'webview', 'argon2', 'win32crypt', 'win32gui', 'win32ts', 'win32con', 'win32api',
    'customtkinter', 'pygame', 'onnxruntime', 'cv2', 'anti_re',
    'ai_regex', 'access_log', 'config', 'face_auth', 'local_anchor', 'pin_auth',
    'recovery_auth', 'session_watch', 'state_binding', 'vault_mount', 'vault_secret',
    '_elevated_helper', 'ui.app', 'ui.decoy_app', 'ui.appdata', 'ui.games',
    'facerecog_engine', 'facerecog_engine.enrollment', 'facerecog_engine.template_store'
]

for pkg in ('onnxruntime', 'customtkinter', 'webview'):
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)

a = Analysis(
    ['main.py'],
    pathex=['.', 'engine', 'ui', 'FaceRecog-Engine'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'simsimd', 'albumentations', 'scipy', 'pandas', 'pyarrow',
        'numba', 'llvmlite', 'torch', 'torchvision', 'matplotlib',
        'IPython', 'notebook', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6'
    ],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [('O', None, 'OPTION'), ('O', None, 'OPTION')],
    name='Kushal Studios',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['*.onnx', 'onnxruntime*.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/kushal_studios_icon.ico'],
)

