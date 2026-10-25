import sys
print("python:", sys.version)
try:
    import win32crypt
    print("pywin32: OK")
except ImportError as e:
    print("pywin32: MISSING -", e)

import subprocess
try:
    r = subprocess.run(["cargo", "--version"], capture_output=True, text=True, timeout=10)
    print("cargo:", r.stdout.strip() or r.stderr.strip())
except FileNotFoundError:
    print("cargo: MISSING")

try:
    import pywebview
    print("pywebview:", pywebview.__version__)
except ImportError as e:
    print("pywebview: MISSING -", e)
