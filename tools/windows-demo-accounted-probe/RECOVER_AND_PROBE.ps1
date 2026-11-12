param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$root=$PSScriptRoot
$stage='TRANSFER_VERIFICATION'
$probeConsoleEncoding=[Console]::OutputEncoding
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
try {
    $manifestPath=Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() -ne $ManifestSHA256) { throw 'PROBE_TRANSFER_HASH_MISMATCH' }
    $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path=[IO.Path]::GetFullPath((Join-Path $root $entry.Name))
        if (-not $path.StartsWith(($root+[IO.Path]::DirectorySeparatorChar),[StringComparison]::OrdinalIgnoreCase)) { throw 'PROBE_TRANSFER_PATH_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'PROBE_TRANSFER_HASH_MISMATCH' }
    }
    Write-Host 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $root 'INSTALL_INSPECTION.ps1')
    $stage='CONTAINER_INSPECTION'
    $raw=@(& docker inspect hybrid-windows-api-1 2>$null)
    $code=$LASTEXITCODE
    $details=Convert-InstallInspection -InspectOutput $raw -InspectExitCode $code
    $id=Read-InstallProperty $details 'Id'
    $config=Read-InstallProperty $details 'Config'
    $labels=Read-InstallProperty $config 'Labels'
    $state=Read-InstallProperty $details 'State'
    if ($id -notmatch '^[0-9a-f]{64}$' -or (Read-InstallProperty $labels 'com.docker.compose.project') -ne 'hybrid-windows' -or (Read-InstallProperty $labels 'com.docker.compose.service') -ne 'api') { throw 'PROBE_CONTAINER_IDENTITY_MISMATCH' }
    if ((Read-InstallProperty $state 'Running') -ne $true -or (Read-InstallProperty (Read-InstallProperty $state 'Health') 'Status') -ne 'healthy') { throw 'PROBE_API_NOT_HEALTHY' }
    if ((Read-InstallProperty $config 'WorkingDir') -ne '/app' -or @('10001','10001:10001') -notcontains (Read-InstallProperty $config 'User')) { throw 'PROBE_EXECUTION_CONTEXT_MISMATCH' }
    function Invoke-ClockWorker([string]$Operation,[string]$Expected='') {
        $lines=@(& (Join-Path $root 'RUN_WORKER.cmd') $id $Operation $Expected)
        $workerCode=$LASTEXITCODE
        if ($workerCode -ne 0) {
            $lines | Write-Host
            exit $workerCode
        }
        $parsed=($lines -join "`n") | ConvertFrom-Json
        if ($parsed.orders_enabled -ne $false -or $parsed.live_trading_enabled -ne $false -or $parsed.execution_approved -ne $false) { throw 'PROBE_RESULT_INVALID' }
        return $parsed
    }
    function Assert-ClockReport([object]$Report) {
        if ($Report.installed_recovery_verified -ne $true -or $Report.http_attempts -ne 0 -or $Report.modeled_weight_charged -ne 0 -or $Report.financial_ledger_unchanged_verified -ne $true -or @('WAIT_STARTUP_HOLD','RECOVERED_KEEP_PAUSED') -notcontains $Report.accounted_clock_recovery) { throw 'PROBE_CLOCK_RESULT_INVALID' }
        if ($Report.recovery_post_snapshot_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'PROBE_CLOCK_RESULT_INVALID' }
    }
    $stage='EXPLICIT_AUDITED_CLOCK_RECOVERY'
    $clock=Invoke-ClockWorker '--recover-clock'
    Assert-ClockReport $clock
    $clock | ConvertTo-Json -Compress | Write-Host
    $snapshot=$clock.recovery_post_snapshot_sha256
    $timer=[Diagnostics.Stopwatch]::StartNew()
    $stage='READ_ONLY_STARTUP_WAIT'
    while ($clock.startup_ready -ne $true) {
        if ($timer.Elapsed.TotalSeconds -ge 110) { throw 'PROBE_CLOCK_STARTUP_WAIT_TIMEOUT' }
        $remaining=[Math]::Max(0,[Math]::Ceiling(70.001-$timer.Elapsed.TotalSeconds))
        Write-Host ('STARTUP_HOLD_KEEP_PAUSED_NO_ORDERS remaining_seconds='+$remaining)
        Start-Sleep -Seconds 5
        $clock=Invoke-ClockWorker '--review-clock' $snapshot
        Assert-ClockReport $clock
        if ($clock.shared_journal_changed -ne $false -or $clock.recovery_post_snapshot_sha256 -ne $snapshot) { throw 'PROBE_CLOCK_REVIEW_CHANGED' }
    }
    $timer.Stop()
    $clock | ConvertTo-Json -Compress | Write-Host
    $stage='EXPLICIT_TWO_GET_PROBE'
    $result=Invoke-ClockWorker '--run-two-gets'
    $result | ConvertTo-Json -Compress | Write-Host
    if ($result.accounted_read_probe -ne 'TWO_GETS_AUDITED_KEEP_PAUSED' -or $result.installed_transport_verified -ne $true -or $result.orders_enabled -ne $false -or $result.live_trading_enabled -ne $false -or $result.execution_approved -ne $false) { throw 'PROBE_RESULT_INVALID' }
    Write-Host 'CLOCK_RECOVERED_TWO_DEMO_GETS_AUDITED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $reason='PROBE_RUNNER_UNAVAILABLE'
    if ($_.Exception.Message -match '^(PROBE|CONTAINER)_[A-Z0-9_]+$') { $reason=$_.Exception.Message }
    [pscustomobject]@{accounted_read_probe='BLOCKED_KEEP_PAUSED';stage=$stage;stop_reason=$reason;request_state='CHECK_WORKER_OUTPUT';order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;automatic_retry=$false;next_action='REVIEW_OUTPUT_NO_REPEAT_AFTER_INTENT'} | ConvertTo-Json -Compress | Write-Host
    exit 1
} finally {
    [Console]::OutputEncoding=$probeConsoleEncoding
}
