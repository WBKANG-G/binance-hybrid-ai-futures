param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CollectOnce)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) {throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if ((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'HISTORY_CORE.ps1|PLAN_CORE.ps1|PRIVATE_STORAGE.ps1|READ_CORE.ps1|READ_TRANSPORT.ps1|README_KO.md|RUN_READ.ps1') {throw 'INSPECT_TRANSFER_INVALID'}
 foreach ($entry in $manifest.files.PSObject.Properties) {if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) {throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 . (Join-Path $PSScriptRoot 'READ_CORE.ps1')
 . (Join-Path $PSScriptRoot 'PRIVATE_STORAGE.ps1')
 . (Join-Path $PSScriptRoot 'PLAN_CORE.ps1')
 . (Join-Path $PSScriptRoot 'READ_TRANSPORT.ps1')
 . (Join-Path $PSScriptRoot 'HISTORY_CORE.ps1')
 $root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
 Inspect-Require (([int][bool]$CheckOnly+[int][bool]$CollectOnce) -eq 1) 'EXPORT_EXPLICIT_MODE_REQUIRED'
 $pin='2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $plan=Invoke-HistoryReadPlan $root $pin
 if($CheckOnly) {
  [pscustomobject]@{history_read='PINNED_HISTORY_READ_INPUTS_CHECKED_NO_HTTP';anchor_count=$plan.anchors.Count;export_jobs_allowed=0;read_window_utc='2026-10-20';demo_support_verified=$false;credentials_accessed=$false;credentials_saved=$false;actual_http_attempts=0;order_writes=0;source_files_written=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
  Write-Output 'HISTORY_READ_CHECK_PASSED_NO_HTTP';exit 0
 }
 $result=Invoke-HistoryRead $plan
 $result|ConvertTo-Json -Depth 10 -Compress|Write-Output
 if($result.history_read -ceq 'TWO_HISTORY_LISTS_SAVED_NO_EXPORT_JOB_CREATED'){Write-Output 'HISTORY_LISTS_SAVED_NO_EXPORT_NO_ORDERS';exit 0}
 exit 2
} catch {
 $reason='INSPECT_LAUNCHER_FAILED';if ($_.Exception.Message -cmatch '^(INSPECT|EXPORT|READ)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
 [pscustomobject]@{history_read='BLOCKED_BEFORE_COLLECTION';export_jobs_created=0;actual_http_attempts=0;automatic_retry=$false;credentials_saved=$false;failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}
