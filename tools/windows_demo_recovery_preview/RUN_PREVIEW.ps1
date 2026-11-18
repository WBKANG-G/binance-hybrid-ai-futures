param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$source=$null
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'PREVIEW_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $allowed='ARRAY_CORE.ps1|BASELINE_CORE.ps1|PLAN_CORE.ps1|PREVIEW_CORE.ps1|PRIVATE_READ.ps1|PURE_HELPERS.ps1|READ_CORE.ps1|README_KO.md|RUN_PREVIEW.ps1|SAVED_INCOME_CORE.ps1|SAVED_TRADE_CORE.ps1'
 if((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne $allowed){throw 'PREVIEW_TRANSFER_INVALID'}
 foreach($entry in $manifest.files.PSObject.Properties){if($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value){throw 'PREVIEW_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('READ_CORE.ps1','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PLAN_CORE.ps1','BASELINE_CORE.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1')){. (Join-Path $PSScriptRoot $name)}
 $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $root=Join-Path $base 'WBKANG-DEMO-Backups';$pin='2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $plan=Invoke-IncomePlan $root $pin
 $baseline=Invoke-BaselineReferenceInspection $root $pin
 Inspect-Require ($baseline.baseline_reference_inspection -ceq 'PINNED_PRIOR_BASELINES_AND_HISTORICAL_REPORT_READ') 'PREVIEW_BASELINE_UNAVAILABLE'
 $source=Get-SavedIncome $plan (Join-Path $base 'WBKANG-DEMO-IncomeReads')
 Add-SavedTrade $source $plan (Join-Path $base 'WBKANG-DEMO-TradeReads')
 $current=Get-SavedCurrent $source $plan (Join-Path $base 'WBKANG-DEMO-CurrentReads')
 New-RecoveryPreview $source $plan $current $baseline|ConvertTo-Json -Depth 10 -Compress|Write-Output
 Write-Output 'RECOVERY_PREVIEW_COMPLETE_NO_APPLY_NO_HTTP_NO_WRITES_KEEP_STOPPED'
 exit 0
} catch {
 $reason='PREVIEW_BLOCKED';if($_.Exception.Message -cmatch '^(PREVIEW|CURRENT|TRADES|BASELINE|INCOME|READ|EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
 [pscustomobject]@{recovery_preview='BLOCKED_NO_CHANGES';failure_category=$reason;files_written=$false;credentials_accessed=$false;actual_http_attempts=0;order_writes=0;baseline_adopted=$false;request_states_changed=$false;services_started=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
} finally {if($null -ne $source){Trades-CloseSaved $source}}
