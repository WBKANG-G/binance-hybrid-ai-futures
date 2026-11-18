param([Parameter(Mandatory=$true)][string]$ManifestSHA256)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
try {
 $path=Join-Path $PSScriptRoot 'TRANSFER_MANIFEST.json'
 if($ManifestSHA256 -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSHA256){throw 'INSPECT_TRANSFER_INVALID'}
 $manifest=Get-Content -Raw -LiteralPath $path|ConvertFrom-Json
 if((@($manifest.files.PSObject.Properties.Name|Sort-Object) -join '|') -cne 'ARRAY_CORE.ps1|PLAN_CORE.ps1|PRIVATE_READ.ps1|READ_CORE.ps1|README_KO.md|REVIEW_CORE.ps1|RUN_REVIEW.ps1'){throw 'INSPECT_TRANSFER_INVALID'}
 foreach($entry in $manifest.files.PSObject.Properties){if($entry.Value -cnotmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $entry.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.Value){throw 'INSPECT_TRANSFER_INVALID'}}
 Write-Output 'TRANSFER_FILES_VERIFIED'
 foreach($name in @('READ_CORE.ps1','PLAN_CORE.ps1','PRIVATE_READ.ps1','ARRAY_CORE.ps1','REVIEW_CORE.ps1')){. (Join-Path $PSScriptRoot $name)}
 $base=[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
 $plan=Invoke-HistoryReadPlan (Join-Path $base 'WBKANG-DEMO-Backups') '2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d'
 $result=Invoke-SavedHistoryReview $plan (Join-Path $base 'WBKANG-DEMO-HistoryReads')
 $result|Select-Object -Property * -ExcludeProperty reports|ConvertTo-Json -Depth 5 -Compress|Write-Output
 if($result.saved_history_review -cne 'SAVED_HISTORY_REANALYZED_NO_HTTP_NO_WRITES'){exit 2}
 $result.reports|Select-Object role,old_reported_count,corrected_returned_count,old_exact_id_candidate_count,corrected_exact_id_candidate_count|Format-Table -AutoSize|Out-String -Width 220|Write-Output
 foreach($report in $result.reports){
  Write-Output ('ROLE='+$report.role)
  $report.safe_preview|ForEach-Object {
   $stamp=$null;if($null -ne $_.time_ms -and $_.time_ms -cmatch '^[0-9]{13}$'){$stamp=[DateTimeOffset]::FromUnixTimeMilliseconds([long]$_.time_ms).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss')}
   [pscustomobject]@{Exact=$_.exact_id_match;ServerID=$_.server_id;Side=$_.side;Type=$_.type;Status=$_.status;Qty=$_.quantity;Filled=$_.executed_quantity;Price=$_.price;UTC=$stamp}
  }|Format-Table -AutoSize -Wrap|Out-String -Width 220|Write-Output
 }
 Write-Output 'SAVED_HISTORY_REVIEW_COMPLETE_NO_HTTP_NO_WRITES';exit 0
} catch {
 $reason='REVIEW_LAUNCHER_FAILED';if($_.Exception.Message -cmatch '^(REVIEW|READ|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
 [pscustomobject]@{saved_history_review='BLOCKED_NO_HTTP_NO_WRITES';failure_category=$reason;actual_http_attempts=0;credentials_accessed=$false;files_written=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}|ConvertTo-Json -Compress|Write-Output
 exit 2
}
