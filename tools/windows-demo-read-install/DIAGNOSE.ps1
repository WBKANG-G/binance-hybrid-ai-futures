param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$stage='TRANSFER_VERIFICATION'
$root=$PSScriptRoot
$readDiagnosticConsoleEncoding=[Console]::OutputEncoding
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
try {
    $manifestPath=Join-Path $root 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant() -ne $ManifestSHA256) { throw 'DIAGNOSTIC_TRANSFER_HASH_MISMATCH' }
    $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path=[IO.Path]::GetFullPath((Join-Path $root $entry.Name))
        if (-not $path.StartsWith(($root+[IO.Path]::DirectorySeparatorChar),[StringComparison]::OrdinalIgnoreCase)) { throw 'DIAGNOSTIC_TRANSFER_PATH_INVALID' }
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant() -ne $entry.Value) { throw 'DIAGNOSTIC_TRANSFER_HASH_MISMATCH' }
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
    if ($id -notmatch '^[0-9a-f]{64}$' -or (Read-InstallProperty $labels 'com.docker.compose.project') -ne 'hybrid-windows' -or (Read-InstallProperty $labels 'com.docker.compose.service') -ne 'api') { throw 'DIAGNOSTIC_CONTAINER_IDENTITY_MISMATCH' }
    if ((Read-InstallProperty $state 'Running') -ne $true -or (Read-InstallProperty (Read-InstallProperty $state 'Health') 'Status') -ne 'healthy') { throw 'DIAGNOSTIC_API_NOT_HEALTHY' }
    if ((Read-InstallProperty $config 'WorkingDir') -ne '/app' -or @('10001','10001:10001') -notcontains (Read-InstallProperty $config 'User')) { throw 'DIAGNOSTIC_EXECUTION_CONTEXT_MISMATCH' }
    $stage='READ_ONLY_PRECHECK'
    $output=@(& (Join-Path $root 'RUN_GUARD.cmd') $id 'pre')
    $code=$LASTEXITCODE
    $output | Write-Host
    if ($code -ne 0) { exit $code }
    Write-Host 'READ_INSTALL_PRECHECK_VERIFIED_NO_API_REPLACEMENT'
    exit 0
} catch {
    $reason='READ_INSTALL_DIAGNOSTIC_UNAVAILABLE'
    if ($_.Exception.Message -match '^(DIAGNOSTIC|CONTAINER)_[A-Z0-9_]+$') { $reason=$_.Exception.Message }
    [pscustomobject]@{read_code_install='BLOCKED_KEEP_PAUSED';operation_stage=$stage;stop_reason=$reason;api_replacement_started=$false;exchange_http_attempts=0;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;next_action='SHARE_DIAGNOSTIC_OUTPUT_NO_INSTALL_RETRY'} | ConvertTo-Json -Compress | Write-Host
    exit 1
} finally {
    [Console]::OutputEncoding=$readDiagnosticConsoleEncoding
}
