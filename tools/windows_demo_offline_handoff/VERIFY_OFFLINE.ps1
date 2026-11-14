param([Parameter(Mandatory=$true)][string]$ReferenceSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
try {
    if ($ReferenceSHA256 -cnotmatch '^[0-9a-f]{64}$') { throw 'OFFLINE_REFERENCE_HASH_REQUIRED' }
    $path=Join-Path $PSScriptRoot 'OFFLINE_REFERENCE.json'
    $file=Get-Item -LiteralPath $path
    if ($file.Length -gt 65536 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -cne $ReferenceSHA256) { throw 'OFFLINE_REFERENCE_HASH_MISMATCH' }
    $reference=Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
    $names=@('BACKUP_RECEIPT.json','BACKUP_STATE.json','manifest.json','database.tar','runtime.tar','images.tar','ISOLATED_RESTORE_RECEIPT.json','PRESERVATION_TOOL.zip','RESTORE_TOOL.zip','VERIFY_OFFLINE.ps1')
    if ($reference.protocol -cne 'DEMO_EXTERNAL_COPY_REFERENCE_V1' -or (@($reference.files | ForEach-Object {$_.name} | Sort-Object) -join '|') -cne (@($names | Sort-Object) -join '|')) { throw 'OFFLINE_REFERENCE_INVALID' }
    foreach ($entry in $reference.files) {
        if ($entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$' -or ($entry.bytes -isnot [int] -and $entry.bytes -isnot [long]) -or $entry.bytes -lt 0 -or $entry.bytes -gt 20GB) { throw 'OFFLINE_REFERENCE_INVALID' }
        $target=Join-Path $PSScriptRoot $entry.name
        $info=Get-Item -LiteralPath $target
        if ($info.PSIsContainer -or ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $info.Length -ne $entry.bytes -or (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant() -cne $entry.sha256) { throw 'OFFLINE_FILE_HASH_MISMATCH' }
    }
    [pscustomobject]@{offline_file_verification='ALL_TEN_FILES_MATCH_REFERENCE';reference_sha256=$ReferenceSHA256;file_count=10;files_changed=$false;docker_used=$false;physical_offline_custody_verified=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
    Write-Output 'OFFLINE_FILES_HASH_VERIFIED_NO_DOCKER_NO_WRITES'
    exit 0
} catch {
    $reason='OFFLINE_VERIFY_UNAVAILABLE'
    if (@('OFFLINE_REFERENCE_HASH_REQUIRED','OFFLINE_REFERENCE_HASH_MISMATCH','OFFLINE_REFERENCE_INVALID','OFFLINE_FILE_HASH_MISMATCH') -ccontains $_.Exception.Message) { $reason=$_.Exception.Message }
    [pscustomobject]@{offline_file_verification='BLOCKED';failure_category=$reason;files_changed=$false;docker_used=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
    exit 2
}
