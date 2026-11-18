param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$source=$null
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'LINK_TRANSFER_INVALID'}
 $m=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 $required=@('READ_CORE.ps1','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PLAN_CORE.ps1','BASELINE_CORE.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1','RESTORE_ASSERT.ps1','CONFIG.json','LINK_CORE.ps1','RUN_LINK.ps1','README_KO.md')
 if((@($m.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne (@($required|Sort-Object) -join '|')){throw 'LINK_TRANSFER_INVALID'}
 foreach($p in $m.files.PSObject.Properties){if($p.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $p.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $p.Value){throw 'LINK_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('READ_CORE.ps1','ARRAY_CORE.ps1','PRIVATE_READ.ps1','PLAN_CORE.ps1','BASELINE_CORE.ps1','PURE_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','PREVIEW_CORE.ps1','RESTORE_ASSERT.ps1','LINK_CORE.ps1')){. (Join-Path $PSScriptRoot $name)}
 $config=Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'CONFIG.json')|ConvertFrom-Json
 $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData);$root=Join-Path $base 'WBKANG-DEMO-Backups'
 $plan=Invoke-IncomePlan $root $config.manifest_sha256
 $baseline=Invoke-BaselineReferenceInspection $root $config.manifest_sha256
 $source=Get-SavedIncome $plan (Join-Path $base 'WBKANG-DEMO-IncomeReads')
 Add-SavedTrade $source $plan (Join-Path $base 'WBKANG-DEMO-TradeReads')
 $current=Get-SavedCurrent $source $plan (Join-Path $base 'WBKANG-DEMO-CurrentReads')
 $preview=New-RecoveryPreview $source $plan $current $baseline
 $restored=Get-RestoredLedgerLink $source $root $config.restore_receipt_sha256 $config
 $result=New-LinkedRecoveryReview $preview $restored
 Trades-VerifySaved $source
 $result|ConvertTo-Json -Depth 10 -Compress|Write-Output
 Write-Output 'RECOVERY_LINK_COMPLETE_CONDITIONAL_DIFF_NO_APPLY_NO_HTTP_NO_WRITES'
 exit 0
} catch {
 $reason='LINK_BLOCKED';if($_.Exception.Message -cmatch '^(LINK|HANDOFF|PREVIEW|BASELINE|INSPECT|CURRENT|TRADES|INCOME|READ|REFERENCE)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
 [pscustomobject]@{recovery_link='BLOCKED_NO_CHANGES';failure_category=$reason;files_written=$false;actual_http_attempts=0;docker_used=$false;baseline_adopted=$false;order_writes=0;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output;exit 2
} finally {if($null -ne $source){Trades-CloseSaved $source}}
