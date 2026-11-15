param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
try {
    if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$') { throw 'RECEIPT_TRANSFER_INVALID' }
    $manifestPath=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
    if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) { throw 'RECEIPT_TRANSFER_INVALID' }
    $manifest=Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $required=@('REVIEW.ps1','REVIEW_CORE.ps1','CONFIG.json','README_KO.md')
    if ((@($manifest.files.PSObject.Properties.Name | Sort-Object) -join '|') -cne (@($required | Sort-Object) -join '|')) { throw 'RECEIPT_TRANSFER_INVALID' }
    foreach ($entry in $manifest.files.PSObject.Properties) {
        $path=Join-Path $PSScriptRoot $entry.Name
        if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) { throw 'RECEIPT_TRANSFER_INVALID' }
    }
    Write-Output 'TRANSFER_FILES_VERIFIED'
    . (Join-Path $PSScriptRoot 'REVIEW_CORE.ps1')
    $config=Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'CONFIG.json') | ConvertFrom-Json
    $base=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
    Review-EvidenceReceipts $base $config | ConvertTo-Json -Depth 16 -Compress | Write-Output
    Write-Output 'EVIDENCE_RECEIPT_STRUCTURE_VERIFIED_NO_WRITES_NO_DOCKER'
    exit 0
} catch {
    $reason='RECEIPT_REVIEW_UNAVAILABLE'
    if ($_.Exception.Message -cmatch '^RECEIPT_[A-Z0-9_]{1,100}$') { $reason=$_.Exception.Message }
    [pscustomobject]@{evidence_receipt_review='BLOCKED_NO_WRITES';failure_category=$reason;files_changed=$false;docker_used=$false;services_started=$false;automatic_retry=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false} | ConvertTo-Json -Depth 16 -Compress | Write-Output
    exit 2
}
