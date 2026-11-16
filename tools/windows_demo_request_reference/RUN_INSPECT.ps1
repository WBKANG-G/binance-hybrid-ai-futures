param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) {throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if ((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'INSPECT_CORE.ps1|README_KO.md|RUN_INSPECT.ps1') {throw 'INSPECT_TRANSFER_INVALID'}
 foreach ($entry in $manifest.files.PSObject.Properties) {if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) {throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 . (Join-Path $PSScriptRoot 'INSPECT_CORE.ps1')
 $root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
 $result=Invoke-RequestReferenceInspection $root '2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $result|ConvertTo-Json -Depth 8 -Compress|Write-Output
 if ($result.request_reference_inspection -ceq 'PINNED_PRIOR_LEDGER_FOUR_REQUEST_REFERENCES_READ') {Write-Output 'FOUR_PRIOR_REQUEST_REFERENCES_READ_NO_WRITES_NO_DOCKER';exit 0}
 exit 2
} catch {
 $reason='INSPECT_LAUNCHER_FAILED';if ($_.Exception.Message -cmatch '^INSPECT_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
 [pscustomobject]@{request_reference_inspection='BLOCKED_NO_WRITES';failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}
