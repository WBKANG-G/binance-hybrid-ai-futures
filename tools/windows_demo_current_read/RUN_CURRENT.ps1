param([Parameter(Mandatory=$true)][string]$ManifestSHA256,[switch]$CheckOnly,[switch]$CollectOnce)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$source=$null
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'ARRAY_CORE.ps1|CURRENT_CORE.ps1|INCOME_HELPERS.ps1|PLAN_CORE.ps1|PRIVATE_STORAGE.ps1|READ_CORE.ps1|READ_TRANSPORT.ps1|README_KO.md|RUN_CURRENT.ps1|SAVED_INCOME_CORE.ps1|SAVED_TRADE_CORE.ps1|TRADE_HELPERS.ps1'){throw 'INSPECT_TRANSFER_INVALID'}
 foreach($entry in $manifest.files.PSObject.Properties){if($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value){throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('READ_CORE.ps1','PLAN_CORE.ps1','PRIVATE_STORAGE.ps1','ARRAY_CORE.ps1','READ_TRANSPORT.ps1','INCOME_HELPERS.ps1','SAVED_INCOME_CORE.ps1','SAVED_TRADE_CORE.ps1','TRADE_HELPERS.ps1','CURRENT_CORE.ps1')){. (Join-Path $PSScriptRoot $name)}
 Inspect-Require (([int][bool]$CheckOnly+[int][bool]$CollectOnce) -eq 1) 'TRADES_EXPLICIT_MODE_REQUIRED'
 $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $plan=Invoke-IncomePlan (Join-Path $base 'WBKANG-DEMO-Backups') '2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $source=Get-SavedIncome $plan (Join-Path $base 'WBKANG-DEMO-IncomeReads')
 Add-SavedTrade $source $plan (Join-Path $base 'WBKANG-DEMO-TradeReads')
 if($CheckOnly){[pscustomobject]@{current_read='SAVED_INCOME_AND_TRADE_LINKS_CHECKED_NO_HTTP';day_key_kst=$source.day_key;week_key_kst=$source.week_key;saved_wallet_candidate_usdt=$source.wallet;current_open_and_position_scope='ALL_SYMBOLS';btc_settings_scope='BTCUSDT';account_wide_trade_history_verified=$false;credentials_accessed=$false;actual_http_attempts=0;files_written=$false;order_writes=0;baseline_adopted=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output;Write-Output 'CURRENT_READ_CHECK_PASSED_NO_HTTP';exit 0}
 $result=Invoke-CurrentRead $plan $source
 $result|Select-Object -Property * -ExcludeProperty raw_evidence|ConvertTo-Json -Depth 8 -Compress|Write-Output
 if($result.current_read -ceq 'CURRENT_ACCOUNT_OBSERVATIONS_SAVED_NO_CHANGES'){Write-Output 'CURRENT_ACCOUNT_SAVED_NO_CHANGES_NO_ORDERS';exit 0}
 exit 2
} catch {
 $reason='TRADES_LAUNCHER_FAILED';if($_.Exception.Message -cmatch '^(CURRENT|TRADES|INCOME|READ|EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
 [pscustomobject]@{current_read='BLOCKED_BEFORE_COLLECTION';failure_category=$reason;actual_http_attempts=0;credentials_saved=$false;files_written=$false;automatic_retry=$false;order_writes=0;baseline_adopted=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output;exit 2
} finally {if($null -ne $source){Trades-CloseSaved $source}}
