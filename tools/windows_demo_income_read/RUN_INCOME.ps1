param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CollectOnce)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if ($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256) {throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if ((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'ARRAY_CORE.ps1|INCOME_CORE.ps1|PLAN_CORE.ps1|PRIVATE_STORAGE.ps1|READ_CORE.ps1|READ_TRANSPORT.ps1|README_KO.md|RUN_INCOME.ps1') {throw 'INSPECT_TRANSFER_INVALID'}
 foreach ($entry in $manifest.files.PSObject.Properties) {if ($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value) {throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 . (Join-Path $PSScriptRoot 'READ_CORE.ps1')
 . (Join-Path $PSScriptRoot 'PRIVATE_STORAGE.ps1')
 . (Join-Path $PSScriptRoot 'PLAN_CORE.ps1')
 . (Join-Path $PSScriptRoot 'READ_TRANSPORT.ps1')
 . (Join-Path $PSScriptRoot 'ARRAY_CORE.ps1')
 . (Join-Path $PSScriptRoot 'INCOME_CORE.ps1')
 $root=Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'WBKANG-DEMO-Backups'
 Inspect-Require (([int][bool]$CheckOnly+[int][bool]$CollectOnce) -eq 1) 'EXPORT_EXPLICIT_MODE_REQUIRED'
 $pin='2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $plan=Invoke-IncomePlan $root $pin
 if($CheckOnly) {
  [pscustomobject]@{income_read='PINNED_INCOME_INPUTS_CHECKED_NO_HTTP';anchor_count=$plan.anchors.Count;week_start_kst=$plan.week_start_kst;export_jobs_allowed=0;demo_support_verified=$false;credentials_accessed=$false;credentials_saved=$false;actual_http_attempts=0;order_writes=0;source_files_written=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
  Write-Output 'INCOME_READ_CHECK_PASSED_NO_HTTP';exit 0
 }
 $result=Invoke-IncomeRead $plan
 $result|Select-Object -Property * -ExcludeProperty raw_evidence|ConvertTo-Json -Depth 10 -Compress|Write-Output
 if($result.income_read -ceq 'INCOME_RESPONSES_SAVED_WALLET_CANDIDATES_ONLY'){Write-Output 'INCOME_SAVED_NO_BASELINE_ADOPTION_NO_ORDERS';exit 0}
 exit 2
} catch {
 $reason='INSPECT_LAUNCHER_FAILED';if ($_.Exception.Message -cmatch '^(INSPECT|EXPORT|READ|INCOME)_[A-Z0-9_]{1,100}$') {$reason=$_.Exception.Message}
 [pscustomobject]@{income_read='BLOCKED_BEFORE_COLLECTION';export_jobs_created=0;actual_http_attempts=0;automatic_retry=$false;credentials_saved=$false;failure_category=$reason;files_written=$false;docker_used=$false;services_started=$false;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}
