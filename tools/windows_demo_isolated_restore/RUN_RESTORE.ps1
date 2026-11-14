param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$ExecuteRestore,[switch]$CheckOnly)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$backupRoot=$PSScriptRoot
$backupInvoked=$false
$backupOldOutput=$OutputEncoding
$backupOldConsole=[Console]::OutputEncoding
$OutputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
try {
    if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$') { throw 'COLD_TRANSFER_INVALID' }
    $path=Join-Path $backupRoot 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -cne $ManifestSHA256) { throw 'COLD_TRANSFER_INVALID' }
    $manifest=Get-Content -Raw -LiteralPath $path | ConvertFrom-Json
    $required=@('RUN_RESTORE.ps1','RESTORE_CORE.ps1','COMMON.ps1','CONTEXT.ps1','restore_worker.py','cold_archive.py','account_check.sql','CONFIG.json','README_KO.md')
    if ((@($manifest.files.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($required | Sort-Object) -join '|')) { throw 'COLD_TRANSFER_INVALID' }
    foreach ($entry in $manifest.files.PSObject.Properties) {
        if ($entry.Name -cnotmatch '^(?:[A-Za-z_]+\.(?:ps1|py|json|md|sql)|plan/[A-Za-z_]+\.(?:ps1|json|md|cmd))$' -or $entry.Name.Contains('..') -or $entry.Value -cnotmatch '^[0-9a-f]{64}$') { throw 'COLD_TRANSFER_INVALID' }
        $file=[IO.Path]::GetFullPath((Join-Path $backupRoot $entry.Name))
        if (-not $file.StartsWith(($backupRoot+[IO.Path]::DirectorySeparatorChar),[StringComparison]::OrdinalIgnoreCase)) { throw 'COLD_TRANSFER_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant() -cne $entry.Value) { throw 'COLD_TRANSFER_INVALID' }
    }
    Write-Output 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $backupRoot 'CONTEXT.ps1')
    . (Join-Path $backupRoot 'COMMON.ps1')
    . (Join-Path $backupRoot 'RESTORE_CORE.ps1')
    $backupInvoked=$true
    $backupExit=$null
    Invoke-IsolatedRestore -Root $backupRoot -ExecuteRestore:$ExecuteRestore -CheckOnly:$CheckOnly | ForEach-Object {
        if ($_ -is [int]) { $backupExit=$_ } else { Write-Output $_ }
    }
    if (@(0,2) -notcontains $backupExit) { throw 'COLD_LAUNCHER_FAILED' }
    exit $backupExit
} catch {
    [pscustomobject]@{isolated_restore='BLOCKED_EXECUTION_STATE_REVIEW_REQUIRED';failure_category='COLD_TRANSFER_OR_LAUNCHER_FAILED';source_state_may_have_changed=$backupInvoked;automatic_retry=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Compress | Write-Output
    exit 2
} finally {
    $OutputEncoding=$backupOldOutput
    [Console]::OutputEncoding=$backupOldConsole
}
