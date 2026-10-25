<#
LockerDrive -- One-time encrypted volume setup
================================================
Run this ONCE, as Administrator:
  Right-click PowerShell -> Run as administrator -> cd into this folder -> .\create_encrypted_volume.ps1

What it does (nothing else, nothing hidden):
  1. Creates a new, empty VHDX file -- a virtual disk that is really just one file
     living at .\vault\lockerdrive.vhdx
  2. Formats it NTFS and gives it a temporary drive letter
  3. Turns on BitLocker for that volume with a password you choose right here
  4. Generates a 48-digit recovery key, prints it to this console, and offers
     a Save dialog so you can store it wherever YOU choose (it is NEVER
     written to the encrypted drive itself -- a recovery key sitting on E:\
     unlocks BitLocker directly and bypasses every gate the app has)
  5. Dismounts everything -- the vault stays closed until the LockerDrive app
     opens it

Does NOT touch E:\, C:\, or anything outside the paths above. Read it before
running it -- that's the whole point of writing it in plain PowerShell instead
of a compiled blob.
#>

param(
    [string]$VaultPath = "E:\LockerDrive\lockerdrive.vhdx",
    [int]$SizeGB = 20,
    [string]$DriveLetter = "V"
)

$ErrorActionPreference = "Stop"

# diskpart's Virtual Disk Service rejects any path containing a literal ".."
# segment, even though $PSScriptRoot\..\vault\... is technically absolute.
# This is why it failed last run. Resolve to a clean canonical path first.
$VaultPath = [System.IO.Path]::GetFullPath($VaultPath)

# --- Guard: must be admin ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Must run as Administrator." -ForegroundColor Red
    Write-Host "Open PowerShell as Administrator, cd here, then run: .\create_encrypted_volume.ps1" -ForegroundColor Red
    exit 1
}

# --- Guard: BitLocker feature must exist (Pro/Enterprise/Education) ---
$bdeCheck = Get-Command manage-bde.exe -ErrorAction SilentlyContinue
if (-not $bdeCheck) {
    Write-Host "manage-bde not found. BitLocker requires Windows Pro, Enterprise, or Education." -ForegroundColor Red
    exit 1
}

# --- Guard: don't clobber an existing vault ---
$vaultDir = Split-Path $VaultPath -Parent
New-Item -ItemType Directory -Path $vaultDir -Force | Out-Null
if (Test-Path $VaultPath) {
    Write-Host "$VaultPath already exists. Refusing to overwrite." -ForegroundColor Red
    Write-Host "Delete it manually first if you really want to start over (you WILL lose whatever is in it)." -ForegroundColor Red
    exit 1
}

Write-Host "Creating $SizeGB GB encrypted volume at $VaultPath ..." -ForegroundColor Cyan

# --- Create + attach + format the VHDX via diskpart (no Hyper-V feature required) ---
$diskpartScript = @"
create vdisk file="$VaultPath" maximum=$($SizeGB * 1024) type=expandable
select vdisk file="$VaultPath"
attach vdisk
create partition primary
format fs=ntfs quick label="LockerDrive"
assign letter=$DriveLetter
"@

$scriptFile = [System.IO.Path]::GetTempFileName()
Set-Content -Path $scriptFile -Value $diskpartScript -Encoding ASCII
diskpart /s $scriptFile
Remove-Item $scriptFile -ErrorAction SilentlyContinue

Start-Sleep -Seconds 2
if (-not (Test-Path "$DriveLetter`:\")) {
    Write-Host "Volume didn't mount at $DriveLetter`:. Check the diskpart output above for the real error." -ForegroundColor Red
    exit 1
}

Write-Host "Volume created at $DriveLetter`:. Enabling BitLocker..." -ForegroundColor Cyan

$securePw = Read-Host "Choose the vault password" -AsSecureString

Enable-BitLocker -MountPoint "$DriveLetter`:" -EncryptionMethod XtsAes256 -PasswordProtector -Password $securePw `
    -UsedSpaceOnly -SkipHardwareTest -ErrorAction Stop | Out-Null
Add-BitLockerKeyProtector -MountPoint "$DriveLetter`:" -RecoveryPasswordProtector -ErrorAction Stop | Out-Null

$vol = Get-BitLockerVolume -MountPoint "$DriveLetter`:"
$recoveryKey = ($vol.KeyProtector | Where-Object { $_.KeyProtectorType -eq 'RecoveryPassword' }).RecoveryPassword

# --- Warn about a key file left by an OLDER version of this script ---
# Previous versions wrote the recovery key to E:\LockerDrive\RECOVERY_KEY-save_elsewhere_then_delete.txt.
# That file unlocks BitLocker directly, bypassing every gate the app has.
$oldKeyFile = "$vaultDir\RECOVERY_KEY-save_elsewhere_then_delete.txt"
if (Test-Path $oldKeyFile) {
    Write-Host ""
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host "!!  OLD RECOVERY KEY FILE STILL ON THE DRIVE                    !!" -ForegroundColor Red
    Write-Host "!!  $oldKeyFile" -ForegroundColor Red
    Write-Host "!!  Anyone who can read E:\ can use it to unlock the vault     !!" -ForegroundColor Red
    Write-Host "!!  directly, bypassing the PIN / face / recovery-code gates.   !!" -ForegroundColor Red
    Write-Host "!!  Save it somewhere safe OFF this drive, then DELETE the file. !!" -ForegroundColor Red
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host ""
}

# --- Show the recovery key prominently in the console ---
# NEVER written to the encrypted drive itself (see header note 4).
Write-Host ""
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host "  YOUR BITLOCKER RECOVERY KEY -- record it NOW" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  $recoveryKey" -ForegroundColor White
Write-Host ""
Write-Host "  This is the ONLY way in if you ever lose the vault password." -ForegroundColor Cyan
Write-Host "  (a save dialog follows, so you can also store it as a file)" -ForegroundColor Cyan
Write-Host "==============================================================" -ForegroundColor Cyan
Write-Host ""

# --- Offer to save it wherever the user chooses (NOT on E:\) ---
try {
    Add-Type -AssemblyName System.Windows.Forms | Out-Null
    $saveDialog = New-Object System.Windows.Forms.SaveFileDialog
    $saveDialog.Title = "Save your LockerDrive recovery key (pick somewhere OFF this drive)"
    $saveDialog.FileName = "LockerDrive_RECOVERY_KEY.txt"
    $saveDialog.InitialDirectory = [Environment]::GetFolderPath("MyDocuments")
    $saveDialog.Filter = "Text file (*.txt)|*.txt|All files (*.*)|*.*"
    if ($saveDialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $targetRoot = [System.IO.Path]::GetPathRoot($saveDialog.FileName).ToUpperInvariant()
        $vaultDriveRoot = [System.IO.Path]::GetPathRoot($VaultPath).ToUpperInvariant()
        if ($targetRoot -eq $vaultDriveRoot -or $targetRoot -eq "$($DriveLetter):".ToUpperInvariant() -or $targetRoot -eq "$($DriveLetter):\".ToUpperInvariant()) {
            Write-Host "WARNING: You cannot save the recovery key directly to the vault or USB drive ($targetRoot)! Key file NOT saved here." -ForegroundColor Red
            Write-Host "Write down your recovery key manually: $recoveryKey" -ForegroundColor Yellow
        } else {
            Set-Content -Path $saveDialog.FileName -Value @"
LockerDrive recovery key -- generated $(Get-Date)
Volume file: $VaultPath

$recoveryKey

This is the ONLY way in if you ever lose the vault password.
Keep it somewhere off this PC (paper, another device, a password manager).
"@
            Write-Host "Recovery key saved to: $($saveDialog.FileName)" -ForegroundColor Green
            Write-Host "Keep that file OFF the encrypted drive and OFF this PC if possible." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Save dialog cancelled -- the key above was NOT saved to any file." -ForegroundColor Yellow
        Write-Host "Write it down NOW; this script will not show it again." -ForegroundColor Yellow
    }
} catch {
    Write-Host "(Save dialog unavailable: $($_.Exception.Message))" -ForegroundColor Yellow
    Write-Host "Write the key above down NOW; this script will not show it again." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Encryption running in background. Check progress any time with:" -ForegroundColor Green
Write-Host "  Get-BitLockerVolume -MountPoint $DriveLetter`:"
Write-Host ""

Lock-BitLocker -MountPoint "$DriveLetter`:" -ForceDismount -ErrorAction SilentlyContinue | Out-Null
Dismount-DiskImage -ImagePath $VaultPath -ErrorAction SilentlyContinue | Out-Null

Write-Host ""
Write-Host "Done. Volume dismounted -- it stays closed until the app opens it." -ForegroundColor Green

# doc-02's adopted baseline (applied 2026-09-21, matches the app's lock
# path in engine/_elevated_helper.py): hide the container itself from
# casual Explorer browsing. +h hides it from "show hidden files"; +s
# additionally keeps it invisible unless "protected operating system
# files" are explicitly un-hidden. Cosmetic layering only -- BitLocker
# is the actual protection. Best-effort: a failure here is reported but
# never fails the setup.
try {
    attrib.exe +h +s $VaultPath
    Write-Host "Container marked hidden+system (cosmetic; BitLocker is the real lock)." -ForegroundColor DarkGray
} catch {
    Write-Host "Note: could not set hidden attributes on the container (harmless)." -ForegroundColor DarkYellow
}





