param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$stage = 'TRANSFER_VERIFICATION'
try {
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
    & (Join-Path $root 'RUN_CANDIDATE.cmd') $d.Id
    if ($LASTEXITCODE -ne 0) { throw 'CANDIDATE_REVIEW_BLOCKED' }
    Write-Host 'CANDIDATE_PLAN_VERIFIED_NO_API_REPLACEMENT_NO_AUDIT_APPLY'
    exit 0
} catch {
    $reason = 'DEPLOYMENT_RUNNER_UNAVAILABLE'
    $allowed = @('TRANSFER_MANIFEST_HASH_MISMATCH','TRANSFER_PATH_INVALID','TRANSFER_FILE_HASH_MISMATCH','API_CONTAINER_INSPECTION_FAILED','API_CONTAINER_IDENTITY_MISMATCH','API_CONTAINER_NOT_HEALTHY','API_EXECUTION_CONTEXT_MISMATCH','CANDIDATE_REVIEW_BLOCKED')
    if ($allowed -contains $_.Exception.Message) { $reason = $_.Exception.Message }
    [pscustomobject]@{read_deployment_review='BLOCKED_KEEP_PAUSED';stage=$stage;stop_reason=$reason;api_replaced=$false;audit_apply_attempted=$false;orders_enabled=$false;live_trading_enabled=$false;execution_approved=$false;next_action='SHARE_OUTPUT_NO_AUTOMATIC_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 1
}
