param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CreateReference)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
    if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$') {throw 'REFERENCE_TRANSFER_INVALID'}
    $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) {throw 'REFERENCE_TRANSFER_INVALID'}
    $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
    $required=@('RUN_REFERENCE.ps1','REFERENCE_CORE.ps1','REVIEW_CORE.ps1','CONFIG.json','README_KO.md','EVIDENCE_REQUIREMENTS_KO.md')
    if ((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne (@($required|Sort-Object) -join '|')) {throw 'REFERENCE_TRANSFER_INVALID'}
    foreach ($entry in $manifest.files.PSObject.Properties) {
        if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) {throw 'REFERENCE_TRANSFER_INVALID'}
    }
    Write-Output 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $PSScriptRoot 'REVIEW_CORE.ps1')
    . (Join-Path $PSScriptRoot 'REFERENCE_CORE.ps1')
    $config=Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'CONFIG.json')|ConvertFrom-Json
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    $result=Invoke-PrivateReference $base $config -CheckOnly:$CheckOnly -CreateReference:$CreateReference
    $result|ConvertTo-Json -Depth 16 -Compress|Write-Output
    if ($result.private_reference -ceq 'SOURCE_RECEIPTS_VERIFIED_NO_REFERENCE_CREATED') {Write-Output 'PRIVATE_REFERENCE_CHECK_PASSED_NO_WRITES_NO_DOCKER';exit 0}
    if ($result.private_reference -ceq 'LOCAL_REFERENCE_VERIFIED_EXTERNAL_CUSTODY_PENDING') {Write-Output 'PRIVATE_REFERENCE_CREATED_LOCAL_ONLY_NO_DOCKER';exit 0}
    exit 2
} catch {
    $reason='REFERENCE_TRANSFER_OR_LAUNCHER_FAILED';if ($_.Exception.Message -cmatch '^(REFERENCE|RECEIPT)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
    [pscustomobject]@{private_reference='BLOCKED_NO_AUTOMATIC_RETRY';failure_category=$reason;source_files_written=$false;docker_used=$false;services_started=$false;external_copy_performed=$false;automatic_retry=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
    exit 2
}
