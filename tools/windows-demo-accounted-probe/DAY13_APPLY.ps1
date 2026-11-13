param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$StatusOnly)
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
    function Invoke-AdoptionWorker([string]$Operation) {
        $lines=@(& (Join-Path $root 'RUN_WORKER.cmd') $id $Operation)
        $workerCode=$LASTEXITCODE
        $lines | Write-Host
        if ($workerCode -ne 0) { exit $workerCode }
        $parsed=($lines -join "`n") | ConvertFrom-Json
        if ($parsed.accounted_day13_adoption -ne 'APPLIED_VERIFIED_KEEP_PAUSED' -or $parsed.installed_adoption_modules_verified -ne $true -or $parsed.orders_enabled -ne $false -or $parsed.live_trading_enabled -ne $false -or $parsed.execution_approved -ne $false -or $parsed.day_adopted -ne $true -or $parsed.period_keys_match -ne $true -or $parsed.prior_financial_history_preserved -ne $true -or $parsed.prior_receipts_and_files_preserved -ne $true -or $parsed.application_http_attempts -ne 8 -or $parsed.application_modeled_weight_charged -ne 131 -or $parsed.application_audit_events_added -ne 16 -or $parsed.application_resource_checkpoints_added -ne 16 -or $parsed.financial_event_added -ne 1 -or $parsed.financial_outbox_added -ne 1) { throw 'PROBE_DAY13_ADOPTION_RESULT_INVALID' }
        return $parsed
    }
    if (-not $StatusOnly) {
        $stage='EXPLICIT_DAY13_ADOPTION'
        $adoption=Invoke-AdoptionWorker '--apply-day13'
        if ($adoption.http_attempts -ne 8) { throw 'PROBE_DAY13_ADOPTION_RESULT_INVALID' }
    }
    $stage='INDEPENDENT_READ_ONLY_ADOPTION_STATUS'
    $verified=Invoke-AdoptionWorker '--day13-adoption-status'
    if ($verified.http_attempts -ne 0) { throw 'PROBE_DAY13_STATUS_SENT_REQUEST' }
    Write-Host 'DAY13_APPLIED_AUDIT_AND_HISTORY_VERIFIED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $reason='PROBE_RUNNER_UNAVAILABLE'
    if ($_.Exception.Message -match '^(PROBE|CONTAINER)_[A-Z0-9_]+$') { $reason=$_.Exception.Message }
    [pscustomobject]@{accounted_read_probe='BLOCKED_KEEP_PAUSED';stage=$stage;stop_reason=$reason;request_state='CHECK_WORKER_OUTPUT';order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;automatic_retry=$false;next_action='RUN_DAY13_STATUS_NO_APPLY_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 1
} finally {
    [Console]::OutputEncoding=$probeConsoleEncoding
}
