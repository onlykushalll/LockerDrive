param(
    [string]$VaultPath = 'E:\LockerDrive\lockerdrive.vhdx',
    [string]$DriveLetter = 'V'
)

$logPath = Join-Path $env:TEMP "lockerdrive_verify_mount.log"
"=== Run started $(Get-Date) ===" | Out-File $logPath

try {
    Mount-DiskImage -ImagePath $VaultPath -ErrorAction Stop
    Start-Sleep -Seconds 2
    Get-BitLockerVolume -MountPoint "$($DriveLetter):" |
        Select-Object MountPoint,EncryptionMethod,LockStatus,KeyProtector |
        Out-File $logPath -Append
    "Drive $($DriveLetter): mounted and inspected successfully" | Out-File $logPath -Append
} catch {
    "ERROR: $_" | Out-File $logPath -Append
} finally {
    Dismount-DiskImage -ImagePath $VaultPath -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 1
    "Drive $($DriveLetter): present after dismount: $(Test-Path "$($DriveLetter):\")" | Out-File $logPath -Append
    "=== Completed ===" | Out-File $logPath -Append
}
