$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = $PSScriptRoot
$container = 'hybrid-windows-api-1'
$stage = 'TRANSFER_VERIFICATION'
function Invoke-Storage([string[]]$StorageArgs, [int[]]$AllowedCodes = @(0)) {
    $source = Get-Content -Raw -LiteralPath (Join-Path $root 'configure_runtime_storage.py')
    $output = $source | & docker exec -i $container python - @StorageArgs
    $code = $LASTEXITCODE
    $output | Write-Host
    if ($AllowedCodes -notcontains $code) { throw 'STORAGE_COMMAND_BLOCKED' }
    try { $record = ConvertFrom-Json -InputObject ($output -join "`n") }
    catch { throw 'STORAGE_RESPONSE_INVALID' }
    if ($code -eq 4 -and (Read-InstallProperty $record 'runtime_storage_setup') -ne 'CODE_INSTALL_REQUIRED') {
        throw 'STORAGE_RESPONSE_INVALID'
    }
    return $record
}
try {
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $root 'TRANSFER_MANIFEST.json') | ConvertFrom-Json
    foreach ($item in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $root $item.Name))
        if (-not $path.StartsWith(($root + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'TRANSFER_PATH_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() -ne $item.Value) { throw 'TRANSFER_FILE_HASH_MISMATCH' }
    }
    Write-Host 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $root 'INSTALL_INSPECTION.ps1')
    $stage = 'STORAGE_STATUS'
    $status = Invoke-Storage -StorageArgs @('--phase','status') -AllowedCodes @(0,4)
    if ($status.runtime_storage_setup -eq 'CODE_INSTALL_REQUIRED') {
        $stage = 'CODE_INSTALLATION'
        & (Join-Path $root 'code-install/INSTALL_CODE.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'CODE_INSTALLATION_BLOCKED' }
        $status = Invoke-Storage -StorageArgs @('--phase','status')
    }
    if ($status.runtime_storage_setup -eq 'PREPARATION_REQUIRED') {
        $stage = 'STORAGE_PREPARATION'
        $status = Invoke-Storage -StorageArgs @('--phase','prepare','--apply-storage')
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($status.runtime_storage_setup -eq 'WAIT_STARTUP_HOLD') {
        $stage = 'READ_ONLY_STARTUP_HOLD'
        if ($watch.Elapsed.TotalSeconds -ge 120) { throw 'STARTUP_HOLD_TIMEOUT' }
        Write-Host 'STARTUP_HOLD_KEEP_PAUSED_NO_ORDERS'
        Start-Sleep -Seconds 3
        $status = Invoke-Storage -StorageArgs @('--phase','status')
    }
    if ($status.runtime_storage_setup -eq 'READY_TO_FINALIZE') {
        $stage = 'STORAGE_FINALIZATION'
        $status = Invoke-Storage -StorageArgs @('--phase','finalize','--apply-storage')
    }
    if ($status.runtime_storage_setup -ne 'CONFIGURED_KEEP_PAUSED' -or $status.execution_approved -ne $false) {
        throw 'STORAGE_FINAL_STATUS_INVALID'
    }
    $stage = 'READ_ONLY_POSTCHECK'
    $final = Invoke-Storage -StorageArgs @('--phase','status')
    if ($final.runtime_storage_setup -ne 'CONFIGURED_KEEP_PAUSED' -or $final.execution_approved -ne $false) {
        throw 'STORAGE_FINAL_STATUS_INVALID'
    }
    Write-Host 'CODE_AND_STORAGE_CONFIGURED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $allowed = @('TRANSFER_PATH_INVALID','TRANSFER_FILE_HASH_MISMATCH','STORAGE_COMMAND_BLOCKED',
        'STORAGE_RESPONSE_INVALID','CODE_INSTALLATION_BLOCKED','STARTUP_HOLD_TIMEOUT','STORAGE_FINAL_STATUS_INVALID')
    $reason = 'NEXT_STAGE_UNEXPECTED_ERROR'
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    [pscustomobject]@{runtime_next_stage='BLOCKED_KEEP_PAUSED'; stage=$stage; stop_reason=$reason;
        execution_approved=$false; orders_enabled=$false; live_trading_enabled=$false;
        next_action='SHARE_OUTPUT_NO_AUTOMATIC_REPAIR_OR_ORDER_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 2
}
