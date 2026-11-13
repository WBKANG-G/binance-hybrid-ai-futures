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
    $stage='POST_ADOPTION_LOCAL_READ_ONLY_GATE'
    $output=@(& (Join-Path $root 'RUN_WORKER.cmd') $id)
    $code=$LASTEXITCODE
    $output | Write-Host
    if ($code -ne 0) { exit $code }
    $result=($output -join "`n") | ConvertFrom-Json
    if ($result.post_adoption_local_gate -ne 'REVIEW_COMPLETED_KEEP_PAUSED' -or $result.installed_control_files_verified -ne $true -or $result.orders_enabled -ne $false -or $result.live_trading_enabled -ne $false -or $result.execution_approved -ne $false) { throw 'PROBE_RESULT_INVALID' }
    if ($result.http_attempts -ne 0 -or $result.credential_session_accessed -ne $false -or $result.all_reviewed_state_unchanged -ne $true -or $result.day13_adoption_integrity_verified -ne $true -or $result.shared_audit_integrity_verified -ne $true -or $result.default_execution_denial_verified -ne $true -or $result.pinned_cost_contract.pinned_documented_cost_contract_verified -ne $true) { throw 'PROBE_POST_ADOPTION_GATE_INVALID' }
    Write-Host 'POST_ADOPTION_LOCAL_GATE_REVIEWED_KEEP_PAUSED_NO_ORDERS'
    exit 0
} catch {
    $reason='PROBE_RUNNER_UNAVAILABLE'
    if ($_.Exception.Message -match '^(PROBE|CONTAINER)_[A-Z0-9_]+$') { $reason=$_.Exception.Message }
    [pscustomobject]@{accounted_read_probe='BLOCKED_KEEP_PAUSED';stage=$stage;stop_reason=$reason;request_state='CHECK_WORKER_OUTPUT';order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;automatic_retry=$false;next_action='REVIEW_OUTPUT_NO_REPEAT_AFTER_INTENT'} | ConvertTo-Json -Compress | Write-Host
    exit 1
} finally {
    [Console]::OutputEncoding=$probeConsoleEncoding
}
