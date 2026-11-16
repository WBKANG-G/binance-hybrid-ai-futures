function Digest([string]$Text) {return (Receipt-Digest ([Text.Encoding]::UTF8.GetBytes($Text)))}
function Assert-HandoffMediaFacts([object]$Value) {
    if ($Value.root -cne 'D:\') { throw 'HANDOFF_WINDOWS_D_DRIVE_REQUIRED' }
    foreach ($flag in @('is_boot','is_system','is_offline','is_readonly')) {
        if ($Value.$flag -isnot [bool]) { throw 'HANDOFF_MEDIA_IDENTITY_UNVERIFIED' }
        if ($Value.$flag) { throw 'HANDOFF_EXTERNAL_USB_REQUIRED' }
    }
    if ($Value.bus -cne 'USB') { throw 'HANDOFF_EXTERNAL_USB_REQUIRED' }
    if ($Value.filesystem -isnot [string] -or @('NTFS','exFAT') -notcontains $Value.filesystem) { throw 'HANDOFF_FILESYSTEM_UNSUPPORTED_NO_FORMAT_PERFORMED' }
    if ($Value.identity_sha256 -cnotmatch '^[0-9a-f]{64}$' -or ($Value.free_bytes -isnot [int] -and $Value.free_bytes -isnot [long]) -or $Value.free_bytes -lt 0) { throw 'HANDOFF_MEDIA_IDENTITY_UNVERIFIED' }
}
function Read-HandoffMedia([string]$Drive) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or $Drive -cne 'D') { throw 'HANDOFF_WINDOWS_D_DRIVE_REQUIRED' }
    $root=$Drive+':\'
    $info=New-Object IO.DriveInfo($root)
    if (-not $info.IsReady) { throw 'HANDOFF_MEDIA_NOT_READY' }
    try {
        $partition=@(Get-Partition -DriveLetter $Drive -ErrorAction Stop)
        if ($partition.Count -ne 1) { throw 'UNVERIFIED' }
        $disk=Get-Disk -Number $partition[0].DiskNumber -ErrorAction Stop
        $volume=@(Get-Volume -DriveLetter $Drive -ErrorAction Stop)
        if ($volume.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$volume[0].UniqueId) -or [string]::IsNullOrWhiteSpace([string]$disk.UniqueId)) { throw 'UNVERIFIED' }
    } catch { throw 'HANDOFF_MEDIA_IDENTITY_UNVERIFIED' }
    if (([IO.File]::GetAttributes($root) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'HANDOFF_REPARSE_POINT' }
    $filesystem=$info.DriveFormat
    if ($filesystem -ieq 'exFAT') { $filesystem='exFAT' }
    if ($filesystem -ieq 'NTFS') { $filesystem='NTFS' }
    $facts=[pscustomobject]@{root=$root;identity_sha256=(Digest (([string]$disk.UniqueId)+'|'+([string]$volume[0].UniqueId)+'|'+([string]$disk.Size)+'|'+$filesystem));free_bytes=$info.AvailableFreeSpace;filesystem=$filesystem;bus=([string]$disk.BusType);is_boot=$disk.IsBoot;is_system=$disk.IsSystem;is_offline=$disk.IsOffline;is_readonly=$disk.IsReadOnly}
    Assert-HandoffMediaFacts $facts
    return $facts
}
