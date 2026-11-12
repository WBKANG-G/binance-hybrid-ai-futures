$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = $PSScriptRoot
$container = 'hybrid-windows-api-1'
$stage = 'TRANSFER_VERIFICATION'
function Invoke-Recovery([string[]]$RecoveryArgs) {
    $source = Get-Content -Raw -LiteralPath (Join-Path $root 'recover_unused_shared_clock.py')
    $output = $source | & docker exec -i $container python - @RecoveryArgs
    $code = $LASTEXITCODE
    $output | Write-Host
    if ($code -ne 0) { throw 'RECOVERY_COMMAND_BLOCKED' }
    try { $record = ConvertFrom-Json -InputObject ($output -join "`n") }
    catch { throw 'RECOVERY_RESPONSE_INVALID' }
    if ($record.execution_approved -ne $false -or $record.orders_enabled -ne $false -or $record.live_trading_enabled -ne $false) {
        throw 'RECOVERY_RESPONSE_INVALID'
    }
    return $record
}
try {
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $root 'TRANSFER_MANIFEST.json') | ConvertFrom-Json
    foreach ($item in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $root $item.Name))
        if (-not $path.StartsWith(($root + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
            throw 'TRANSFER_PATH_INVALID'
        }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower() -ne $item.Value) {
            throw 'TRANSFER_FILE_HASH_MISMATCH'
        }
    }
    Write-Host 'TRANSFER_FILES_VERIFIED'
    $stage = 'EXPLICIT_UNUSED_CLOCK_RECOVERY'
    $status = Invoke-Recovery -RecoveryArgs @('--phase','recover','--apply-clock-recovery')
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($status.runtime_clock_recovery -eq 'WAIT_STARTUP_HOLD') {
        $stage = 'READ_ONLY_STARTUP_HOLD'
        if ($watch.Elapsed.TotalSeconds -ge 120) { throw 'STARTUP_HOLD_TIMEOUT' }
        Write-Host 'STARTUP_HOLD_KEEP_PAUSED_NO_ORDERS'
        Start-Sleep -Seconds 3
        $status = Invoke-Recovery -RecoveryArgs @('--phase','status')
    }
    if ($status.runtime_clock_recovery -ne 'RECOVERED_KEEP_PAUSED' -or $status.startup_ready -ne $true) {
        throw 'RECOVERY_FINAL_STATUS_INVALID'
    }
    $stage = 'READ_ONLY_POSTCHECK'
    $final = Invoke-Recovery -RecoveryArgs @('--phase','status')
    if ($final.runtime_clock_recovery -ne 'RECOVERED_KEEP_PAUSED' -or $final.startup_ready -ne $true) {
        throw 'RECOVERY_FINAL_STATUS_INVALID'
    }
    Write-Host 'SHARED_CLOCK_RECOVERED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $allowed = @('TRANSFER_PATH_INVALID','TRANSFER_FILE_HASH_MISMATCH','RECOVERY_COMMAND_BLOCKED',
        'RECOVERY_RESPONSE_INVALID','STARTUP_HOLD_TIMEOUT','RECOVERY_FINAL_STATUS_INVALID')
    $reason = 'RECOVERY_RUNNER_UNAVAILABLE'
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    [pscustomobject]@{runtime_clock_recovery='BLOCKED_KEEP_PAUSED'; stage=$stage; stop_reason=$reason;
        execution_approved=$false; orders_enabled=$false; live_trading_enabled=$false;
        next_action='REVIEW_OUTPUT_NO_AUTOMATIC_RESET_OR_ORDER_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 2
}
