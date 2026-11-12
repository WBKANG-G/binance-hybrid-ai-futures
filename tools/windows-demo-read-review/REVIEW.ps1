param([Parameter(Mandatory=$true)][string]$ManifestSHA256, [switch]$WaitStartupHold, [switch]$AuditApply)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$stage = 'TRANSFER_VERIFICATION'
$auditApplyAttempted = $false
try {
    if ($WaitStartupHold -and $AuditApply) { throw 'OPERATION_FLAGS_INVALID' }
    $root = $PSScriptRoot
    $manifestPath = Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() -ne $ManifestSHA256) { throw 'TRANSFER_MANIFEST_HASH_MISMATCH' }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    foreach ($item in $manifest.files.PSObject.Properties) {
        $path = [IO.Path]::GetFullPath((Join-Path $root $item.Name))
        if (-not $path.StartsWith(($root + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) { throw 'TRANSFER_PATH_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -ne $item.Value) { throw 'TRANSFER_FILE_HASH_MISMATCH' }
    }
    Write-Host 'TRANSFER_FILES_VERIFIED'
    $stage = 'CONTAINER_INSPECTION'
    $raw = @(& docker inspect hybrid-windows-api-1 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'API_CONTAINER_INSPECTION_FAILED' }
    $items = @((($raw -join "`n") | ConvertFrom-Json))
    if ($items.Count -ne 1) { throw 'API_CONTAINER_IDENTITY_MISMATCH' }
    $d = $items[0]
    if ($d.Id -notmatch '^[0-9a-f]{64}$') { throw 'API_CONTAINER_IDENTITY_MISMATCH' }
    if ($d.Name -ne '/hybrid-windows-api-1' -or $d.Config.Labels.'com.docker.compose.project' -ne 'hybrid-windows' -or $d.Config.Labels.'com.docker.compose.service' -ne 'api') { throw 'API_CONTAINER_IDENTITY_MISMATCH' }
    if ($d.State.Running -ne $true -or $d.State.Health.Status -ne 'healthy') { throw 'API_CONTAINER_NOT_HEALTHY' }
    if ($d.Config.WorkingDir -ne '/app' -or $d.Config.User -notin @('10001','10001:10001')) { throw 'API_EXECUTION_CONTEXT_MISMATCH' }
    $stage = 'CANDIDATE_LOCAL_REVIEW'
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $waiting = $false
    while ($true) {
        $candidateArgs = @($d.Id)
        if ($waiting) { $candidateArgs += '--status' }
        if ($AuditApply) { $candidateArgs += '--readers-stopped'; $auditApplyAttempted = $true }
        $output = @(& (Join-Path $root 'RUN_CANDIDATE.cmd') @candidateArgs)
        $code = $LASTEXITCODE
        $output | Write-Host
        if ($code -eq 0) {
            if ($AuditApply) {
                $post = ($output -join "`n") | ConvertFrom-Json
                if ($post.read_deployment_review -ne 'AUDIT_CONFIGURED_KEEP_PAUSED' -or $post.audit_postcheck_passed -ne $true -or $post.orders_enabled -ne $false -or $post.live_trading_enabled -ne $false -or $post.execution_approved -ne $false) { throw 'CANDIDATE_REVIEW_BLOCKED' }
            }
            break
        }
        if ($code -ne 2 -or -not $WaitStartupHold) { throw 'CANDIDATE_REVIEW_BLOCKED' }
        $status = ($output -join "`n") | ConvertFrom-Json
        if ($status.read_deployment_review -ne 'WAIT_STARTUP_HOLD' -or $status.orders_enabled -ne $false -or $status.live_trading_enabled -ne $false -or $status.execution_approved -ne $false) { throw 'CANDIDATE_REVIEW_BLOCKED' }
        if ($watch.Elapsed.TotalSeconds -ge 120) { throw 'STARTUP_HOLD_TIMEOUT' }
        Write-Host 'STARTUP_HOLD_KEEP_PAUSED_NO_ORDERS'
        $waiting = $true
        Start-Sleep -Seconds 3
    }
    if ($AuditApply) { Write-Host 'AUDIT_SCHEMA_AND_BINDING_CONFIGURED_KEEP_PAUSED_NO_ORDERS' }
    elseif ($WaitStartupHold) { Write-Host 'UNUSED_CLOCK_RECOVERED_CANDIDATE_PLAN_VERIFIED_KEEP_PAUSED' }
    else { Write-Host 'CANDIDATE_PLAN_VERIFIED_NO_API_REPLACEMENT_NO_AUDIT_APPLY' }
    exit 0
} catch {
    $reason = 'DEPLOYMENT_RUNNER_UNAVAILABLE'
    $allowed = @('TRANSFER_MANIFEST_HASH_MISMATCH','TRANSFER_PATH_INVALID','TRANSFER_FILE_HASH_MISMATCH','API_CONTAINER_INSPECTION_FAILED','API_CONTAINER_IDENTITY_MISMATCH','API_CONTAINER_NOT_HEALTHY','API_EXECUTION_CONTEXT_MISMATCH','CANDIDATE_REVIEW_BLOCKED','STARTUP_HOLD_TIMEOUT','OPERATION_FLAGS_INVALID')
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    [pscustomobject]@{read_deployment_review='BLOCKED_KEEP_PAUSED';stage=$stage;stop_reason=$reason;api_replaced=$false;audit_apply_attempted=$auditApplyAttempted;orders_enabled=$false;live_trading_enabled=$false;execution_approved=$false;next_action='SHARE_OUTPUT_NO_AUTOMATIC_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 1
}
