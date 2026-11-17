param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$RequestExportOnce,[switch]$StatusOnce)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) {throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if ((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'EXPORT_CORE.ps1|PLAN_CORE.ps1|PRIVATE_STORAGE.ps1|READ_CORE.ps1|README_KO.md|RUN_EXPORT.ps1') {throw 'INSPECT_TRANSFER_INVALID'}
 foreach ($entry in $manifest.files.PSObject.Properties) {if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) {throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 . (Join-Path $PSScriptRoot 'READ_CORE.ps1')
 . (Join-Path $PSScriptRoot 'PRIVATE_STORAGE.ps1')
 . (Join-Path $PSScriptRoot 'PLAN_CORE.ps1')
 . (Join-Path $PSScriptRoot 'EXPORT_CORE.ps1')
 $root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
 Inspect-Require (([int][bool]$CheckOnly+[int][bool]$RequestExportOnce+[int][bool]$StatusOnce) -eq 1) 'EXPORT_EXPLICIT_MODE_REQUIRED'
 $pin='2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $plan=Invoke-ExportPlan $root $pin
 if($CheckOnly) {
  [pscustomobject]@{history_export='PINNED_EXPORT_INPUTS_CHECKED_NO_HTTP';anchor_count=$plan.anchors.Count;export_kind='ORDER_HISTORY';creation_weight=1000;monthly_creation_limit=10;demo_support_verified=$false;credentials_accessed=$false;credentials_saved=$false;actual_http_attempts=0;order_writes=0;source_files_written=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
  Write-Output 'HISTORY_EXPORT_CHECK_PASSED_NO_HTTP';exit 0
 }
 $mode='StatusOnce';if($RequestExportOnce){$mode='RequestOnce'}
 $result=Invoke-HistoryExport $plan $mode
 $result|ConvertTo-Json -Depth 8 -Compress|Write-Output
 if($result.history_export -ceq 'ONE_EXPORT_JOB_RECORDED_NO_ARCHIVE_DOWNLOADED') {Write-Output 'ONE_EXPORT_JOB_RECORDED_NO_ORDERS';exit 0}
 if($result.history_export -ceq 'ONE_EXISTING_JOB_STATUS_SAVED_NO_ARCHIVE_DOWNLOADED') {Write-Output 'EXISTING_EXPORT_STATUS_SAVED_NO_NEW_JOB';exit 0}
 exit 2
} catch {
 $reason='INSPECT_LAUNCHER_FAILED';if ($_.Exception.Message -cmatch '^(INSPECT|EXPORT)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
 [pscustomobject]@{history_export='BLOCKED_BEFORE_COLLECTION';actual_http_attempts=0;automatic_retry=$false;credentials_saved=$false;failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}
