"""
_elevated_helper.py -- Scoped Administrator worker & One-Time Upfront UAC Daemon.

Supports two execution modes:
  1. Single-shot mode (backward-compatible with all red-team tests):
       python _elevated_helper.py <unlock|lock> <result_path> <nonce>
  2. Persistent Session Daemon mode (zero repeated UAC prompts):
       python _elevated_helper.py --daemon <ipc_dir> [session_token] [parent_pid]
"""
import sys
import os
import json
import time
import base64
import ctypes
import subprocess

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

sys.path.insert(0, os.path.dirname(__file__))
import vault_secret

try:
    from config import CONFIG, load_persisted_config
    VHDX_PATH = CONFIG.vhdx_path
    DRIVE_LETTER = CONFIG.mount_letter
except Exception:
    VHDX_PATH = r"E:\LockerDrive\lockerdrive.vhdx"
    DRIVE_LETTER = "V"


def _ps_quote(val: str) -> str:
    return str(val).replace("'", "''")


def _run_ps(script: str, stdin_data: str | None = None, timeout: int = 35) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return r.stdout.strip() or r.stderr.strip()


def do_unlock(vhdx_path: str | None = None, drive_letter: str | None = None, open_explorer_window: bool = True) -> str:
    vhdx = vhdx_path or VHDX_PATH
    letter = (drive_letter or DRIVE_LETTER).rstrip(r":\/").upper()
    password = vault_secret.load_secret()
    if password is None:
        return "FAILED: NO_SECRET_REGISTERED"
    # ASCII-only transport: base64 survives any codepage and any newline
    # convention, and never puts the plaintext in a command line.
    b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    vhdx_q = _ps_quote(vhdx)
    script = (
        "$ErrorActionPreference = 'Stop'; try { "
        f"try {{ attrib.exe -h -s '{vhdx_q}' }} catch {{}}; "
        f"$img = Get-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue; "
        f"if (-not $img -or -not $img.Attached) {{ Mount-DiskImage -ImagePath '{vhdx_q}' | Out-Null; $img = Get-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue }}; "
        f"if ($img -and $img.Number) {{ "
        f"  $part = Get-Partition -DiskNumber $img.Number -ErrorAction SilentlyContinue | Where-Object {{ $_.Type -ne 'Reserved' -and $_.Size -gt 10MB }} | Select-Object -First 1; "
        f"  if ($part -and $part.DriveLetter -ne '{letter}') {{ Set-Partition -DiskNumber $img.Number -PartitionNumber $part.PartitionNumber -NewDriveLetter '{letter}' -ErrorAction SilentlyContinue }} "
        f"}}; "
        "$b64 = [Console]::In.ReadLine(); "
        "$plain = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)); "
        "$sec = ConvertTo-SecureString -AsPlainText -Force -String $plain; "
        f"for ($w = 0; $w -lt 20; $w++) {{ "
        f"  $bl = Get-BitLockerVolume -MountPoint '{letter}:' -ErrorAction SilentlyContinue; "
        f"  if ($bl) {{ break }}; "
        f"  Start-Sleep -Milliseconds 40 "
        f"}}; "
        f"$bl = Get-BitLockerVolume -MountPoint '{letter}:' -ErrorAction SilentlyContinue; "
        f"if ($bl -and $bl.LockStatus -eq 'Unlocked') {{ Write-Output 'SUCCESS' }} "
        f"else {{ Unlock-BitLocker -MountPoint '{letter}:' -Password $sec | Out-Null; Write-Output 'SUCCESS' }}; "
        f"for ($k = 0; $k -lt 30; $k++) {{ "
        f"  if (Test-Path '{letter}:\\') {{ break }}; "
        f"  Start-Sleep -Milliseconds 40 "
        f"}} "
        "} catch { Write-Output \"FAILED: $_\" } "
        "finally { $plain = $null; $b64 = $null; if ($sec) { $sec.Dispose(); $sec = $null } }"
    )
    output = _run_ps(script, stdin_data=b64 + "\n")
    if not output.endswith("SUCCESS"):
        _run_ps(f"Dismount-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue")
        return output
    if open_explorer_window:
        subprocess.Popen(["explorer.exe", f"{letter}:\\"])
    return "SUCCESS"


def do_lock(vhdx_path: str | None = None, drive_letter: str | None = None, cloak: bool = True) -> str:
    vhdx = vhdx_path or VHDX_PATH
    letter = (drive_letter or DRIVE_LETTER).rstrip(r":\/").upper()
    vhdx_q = _ps_quote(vhdx)
    host_dir_q = _ps_quote(os.path.dirname(vhdx))
    cloak_cmd = (
        f"try {{ attrib.exe -h -s '{host_dir_q}\\*.txt'; attrib.exe -h -s '{host_dir_q}\\*.pkl'; attrib.exe +h +s '{host_dir_q}' }} catch {{}}; "
        if (cloak and host_dir_q) else ""
    )
    # Single combined PowerShell execution (avoids spawning 4 separate powershell.exe processes!)
    combined_script = (
        "$ErrorActionPreference = 'Stop'; "
        f"try {{ Lock-BitLocker -MountPoint '{letter}:' -ForceDismount | Out-Null }} catch {{ Write-Output 'FAILED: $_'; exit }}; "
        f"try {{ Dismount-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue | Out-Null }} catch {{}}; "
        f"try {{ attrib.exe +h +s '{vhdx_q}' }} catch {{}}; "
        f"{cloak_cmd}"
        "Write-Output 'SUCCESS'"
    )
    lock_result = _run_ps(combined_script)
    return "SUCCESS" if lock_result.endswith("SUCCESS") else lock_result


def do_cloak(host_dir: str, hide: bool = True) -> str:
    hd_q = _ps_quote(host_dir)
    flag = "+h +s" if hide else "-h -s"
    _run_ps(f"try {{ attrib.exe -h -s '{hd_q}\\*.txt'; attrib.exe -h -s '{hd_q}\\*.pkl'; attrib.exe {flag} '{hd_q}' }} catch {{}}")
    return "SUCCESS"


def do_provision_volume(vhdx_path: str, size_gb: int, drive_letter: str, password: str) -> str:
    if os.path.exists(vhdx_path):
        return "FAILED: VHDX_ALREADY_EXISTS"
    size_mb = max(1024, int(size_gb) * 1024)
    letter = drive_letter.rstrip(r":\/").upper()
    os.makedirs(os.path.dirname(vhdx_path), exist_ok=True)

    dp_script = (
        f'create vdisk file="{vhdx_path}" maximum={size_mb} type=expandable\n'
        f'select vdisk file="{vhdx_path}"\n'
        f'attach vdisk\n'
        f'create partition primary\n'
        f'format fs=ntfs quick label="LockerDrive"\n'
        f'assign letter={letter}\n'
    )
    import tempfile
    fd, dp_path = tempfile.mkstemp(suffix=".txt", prefix="ld_dp_")
    os.close(fd)
    try:
        with open(dp_path, "w", encoding="ascii") as f:
            f.write(dp_script)
        subprocess.run(["diskpart", "/s", dp_path], capture_output=True, timeout=60, creationflags=0x08000000)
    finally:
        try:
            os.remove(dp_path)
        except OSError:
            pass

    time.sleep(1.5)
    if not os.path.exists(f"{letter}:\\"):
        return "FAILED: DISKPART_MOUNT_FAILED"

    b64 = base64.b64encode(password.encode("utf-8")).decode("ascii")
    vhdx_q = _ps_quote(vhdx_path)
    ps = (
        "$ErrorActionPreference = 'Stop'; "
        "$b64 = [Console]::In.ReadLine(); "
        "$plain = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)); "
        "$sec = ConvertTo-SecureString -AsPlainText -Force -String $plain; "
        "try { "
        f"Enable-BitLocker -MountPoint '{letter}:' -EncryptionMethod XtsAes256 -PasswordProtector -Password $sec -UsedSpaceOnly -SkipHardwareTest | Out-Null; "
        f"Add-BitLockerKeyProtector -MountPoint '{letter}:' -RecoveryPasswordProtector | Out-Null; "
        f"$vol = Get-BitLockerVolume -MountPoint '{letter}:'; "
        "$rk = ($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' }).RecoveryPassword; "
        f"Lock-BitLocker -MountPoint '{letter}:' -ForceDismount | Out-Null; "
        f"Dismount-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue | Out-Null; "
        f"attrib.exe +h +s '{vhdx_q}'; "
        "Write-Output \"SUCCESS:$rk\" "
        "} catch { Write-Output \"FAILED: $_\" } "
        "finally { $plain = $null; $b64 = $null; if ($sec) { $sec.Dispose(); $sec = $null } }"
    )
    return _run_ps(ps, stdin_data=b64 + "\n", timeout=90)


def do_expand_volume(vhdx_path: str, new_size_gb: int, drive_letter: str) -> str:
    """Expands the VHDX virtual disk AND extends the inner BitLocker NTFS volume."""
    if not os.path.exists(vhdx_path):
        return "FAILED: VHDX_NOT_FOUND"
    new_mb = max(2048, int(new_size_gb) * 1024)
    letter = drive_letter.rstrip(r":\/").upper()
    vhdx_q = _ps_quote(vhdx_path)
    host_dir_q = _ps_quote(os.path.dirname(vhdx_path))

    # 1. Remove Hidden/System/ReadOnly attributes so diskpart can open the VHDX for writing
    _run_ps(f"try {{ attrib.exe -h -s -r '{host_dir_q}'; attrib.exe -h -s -r '{vhdx_q}' }} catch {{}}")

    # 2. Lock and dismount the VHDX so diskpart can expand maximum capacity
    _run_ps(
        f"try {{ Lock-BitLocker -MountPoint '{letter}:' -ForceDismount -ErrorAction SilentlyContinue | Out-Null }} catch {{}}; "
        f"try {{ Dismount-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue | Out-Null }} catch {{}}; "
        f"for ($i = 0; $i -lt 25; $i++) {{ "
        f"  $img = Get-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue; "
        f"  if (-not $img -or -not $img.Attached) {{ break }}; "
        f"  Start-Sleep -Milliseconds 40 "
        f"}}"
    )

    # 3. Expand VHDX container via diskpart
    import tempfile
    fd, dp_path = tempfile.mkstemp(suffix=".txt", prefix="ld_exp1_")
    os.close(fd)
    try:
        lines1 = [f'select vdisk file="{vhdx_path}"', f'expand vdisk maximum={new_mb}', 'exit', '']
        with open(dp_path, "w", encoding="ascii") as f:
            f.write('\n'.join(lines1))
        r = subprocess.run(
            ["diskpart", "/s", dp_path],
            capture_output=True, text=True, timeout=60, creationflags=0x08000000
        )
        out_txt = (r.stdout or "") + (r.stderr or "")
        if r.returncode != 0 and "successfully expanded" not in out_txt.lower():
            do_unlock(vhdx_path, letter, open_explorer_window=False)
            return f"FAILED: {out_txt.strip()[:160]}"
    finally:
        try:
            os.remove(dp_path)
        except OSError:
            pass

    # 4. Re-mount and unlock BitLocker volume V:
    u_res = do_unlock(vhdx_path, letter, open_explorer_window=False)
    if u_res != "SUCCESS":
        return u_res

    # 5. Extend the unlocked BitLocker NTFS volume to fill the newly expanded VHDX space
    fd2, dp_ext_path = tempfile.mkstemp(suffix=".txt", prefix="ld_exp2_")
    os.close(fd2)
    try:
        lines2 = ['rescan', f'select volume {letter}', 'extend', 'extend filesystem', 'exit', '']
        with open(dp_ext_path, "w", encoding="ascii") as f:
            f.write('\n'.join(lines2))
        subprocess.run(
            ["diskpart", "/s", dp_ext_path],
            capture_output=True, text=True, timeout=45, creationflags=0x08000000
        )
    finally:
        try:
            os.remove(dp_ext_path)
        except OSError:
            pass

    # Also invoke PowerShell Update-Disk + Resize-Partition as a secondary guarantee
    _run_ps(
        f"try {{ "
        f"  $img = Get-DiskImage -ImagePath '{vhdx_q}' -ErrorAction SilentlyContinue; "
        f"  if ($img -and $img.Number) {{ Update-Disk -Number $img.Number -ErrorAction SilentlyContinue }}; "
        f"  $sz = Get-PartitionSupportedSize -DriveLetter '{letter}' -ErrorAction Stop; "
        f"  if ($sz.SizeMax -gt $sz.SizeMin) {{ "
        f"    Resize-Partition -DriveLetter '{letter}' -Size $sz.SizeMax -ErrorAction SilentlyContinue | Out-Null "
        f"  }} "
        f"}} catch {{}}"
    )
    return "SUCCESS"

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return True
    try:
        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        rc = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
        ctypes.windll.kernel32.CloseHandle(h)
        return rc == 0x00000102
    except Exception:
        return True


def run_daemon(ipc_dir: str, explicit_token: str | None = None, parent_pid: int = 0) -> None:
    global VHDX_PATH, DRIVE_LETTER
    import hmac
    import win32crypt
    os.makedirs(ipc_dir, exist_ok=True)
    seed_file = os.path.join(ipc_dir, "daemon_seed.json")
    ready_file = os.path.join(ipc_dir, "daemon_ready.json")
    req_file = os.path.join(ipc_dir, "daemon_req.json")
    resp_file = os.path.join(ipc_dir, "daemon_resp.json")

    session_token = explicit_token
    token_dpapi = ""
    if not session_token:
        if not os.path.exists(seed_file):
            return
        try:
            with open(seed_file, "r", encoding="utf-8") as f:
                seed = json.load(f)
            token_dpapi = str(seed["token_dpapi"])
            raw_blob = base64.b64decode(token_dpapi.encode("ascii"))
            _, dec_bytes = win32crypt.CryptUnprotectData(raw_blob, None, None, None, 0)
            session_token = dec_bytes.decode("utf-8")
            os.remove(seed_file)
        except Exception:
            return
    else:
        try:
            blob = win32crypt.CryptProtectData(session_token.encode("utf-8"), "LockerDrive IPC Token", None, None, None, 0)
            token_dpapi = base64.b64encode(blob).decode("ascii")
        except Exception:
            token_dpapi = ""

    with open(ready_file, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started_at": time.time(), "token_dpapi": token_dpapi}, f)

    last_activity = time.time()
    try:
        while True:
            if parent_pid > 0 and not _pid_alive(parent_pid):
                try:
                    load_persisted_config()
                    VHDX_PATH = CONFIG.vhdx_path
                    DRIVE_LETTER = CONFIG.mount_letter
                    if os.path.exists(f"{DRIVE_LETTER}:\\"):
                        do_lock(VHDX_PATH, DRIVE_LETTER, cloak=CONFIG.stealth_cloak)
                except Exception:
                    pass
                break

            if (time.time() - last_activity) > 14400:
                break

            if not os.path.exists(req_file):
                time.sleep(0.05)
                continue

            try:
                with open(req_file, "r", encoding="utf-8") as f:
                    req = json.load(f)
                os.remove(req_file)
            except Exception:
                time.sleep(0.05)
                continue

            last_activity = time.time()
            claimed_token = str(req.get("token", ""))
            nonce = str(req.get("nonce", ""))
            action = str(req.get("action", ""))
            if not hmac.compare_digest(claimed_token, session_token):
                res_str = "FAILED: INVALID_DAEMON_TOKEN"
            elif action == "quit":
                res_str = "SUCCESS"
                tmp_r = resp_file + ".tmp"
                with open(tmp_r, "w", encoding="utf-8") as f:
                    json.dump({"nonce": nonce, "result": res_str}, f)
                os.replace(tmp_r, resp_file)
                break
            else:
                try:
                    load_persisted_config()
                    VHDX_PATH = req.get("vhdx_path") or CONFIG.vhdx_path
                    DRIVE_LETTER = (req.get("drive_letter") or CONFIG.mount_letter).rstrip(r":\/")
                except Exception:
                    pass
                if action == "unlock":
                    res_str = do_unlock(VHDX_PATH, DRIVE_LETTER)
                elif action == "lock":
                    res_str = do_lock(VHDX_PATH, DRIVE_LETTER, cloak=bool(req.get("cloak", True)))
                elif action == "cloak":
                    res_str = do_cloak(req.get("host_dir") or os.path.dirname(VHDX_PATH), hide=bool(req.get("hide", True)))
                elif action == "provision":
                    pw_b64 = str(req.get("password_b64", ""))
                    pw = base64.b64decode(pw_b64).decode("utf-8") if pw_b64 else ""
                    res_str = do_provision_volume(VHDX_PATH, int(req.get("size_gb", 20)), DRIVE_LETTER, pw)
                    pw = None
                elif action == "expand":
                    res_str = do_expand_volume(VHDX_PATH, int(req.get("new_size_gb", 40)), DRIVE_LETTER)
                else:
                    res_str = f"FAILED: unknown action {action!r}"

            tmp_r = resp_file + ".tmp"
            with open(tmp_r, "w", encoding="utf-8") as f:
                json.dump({"nonce": nonce, "result": res_str}, f)
            os.replace(tmp_r, resp_file)
    finally:
        for p in (ready_file, req_file, seed_file):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--daemon":
        tok = sys.argv[3] if len(sys.argv) > 3 else None
        ppid = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        run_daemon(sys.argv[2], explicit_token=tok, parent_pid=ppid)
        sys.exit(0)

    action = sys.argv[1] if len(sys.argv) > 1 else ""
    result_path = sys.argv[2] if len(sys.argv) > 2 else ""
    nonce = sys.argv[3] if len(sys.argv) > 3 else ""

    if action == "unlock":
        result = do_unlock()
    elif action == "lock":
        result = do_lock()
    else:
        result = f"FAILED: unknown action {action!r}"

    if result_path:
        try:
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(f"{nonce}|{result}")
        except OSError:
            pass
