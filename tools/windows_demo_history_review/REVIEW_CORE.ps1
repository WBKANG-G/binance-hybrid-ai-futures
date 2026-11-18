function Review-Open([string]$Path,[int]$Limit,[object]$Streams) {
 $stream=Inspect-Open $Path $Limit;$entry=[pscustomobject]@{stream=$stream;sha256=$null;body=$null};$null=$Streams.Add($entry)
 $entry.sha256=Inspect-StreamHash $stream;$reader=New-Object IO.StreamReader($stream,(New-Object Text.UTF8Encoding($false,$true)),$true,1024,$true)
 try {$entry.body=$reader.ReadToEnd()} finally {$reader.Dispose()}
 return $entry
}
function Review-Choice([object]$Value,[string[]]$Allowed) {if($Value -is [string] -and $Allowed -ccontains $Value){return $Value};return $null}
function Review-Number([object]$Value,[int]$Max=32) {
 if($null -eq $Value -or $Value -is [bool]){return $null}
 $text=[Convert]::ToString($Value,[Globalization.CultureInfo]::InvariantCulture)
 if($text.Length -le $Max -and $text -cmatch '^[0-9]+(\.[0-9]+)?$'){return $text};return $null
}
function Review-Row([object]$Row,[string]$Role,[string]$TargetHash) {
 $field='clientOrderId';$serverField='orderId';$statusField='status';if($Role -ceq 'STOP_LOSS'){$field='clientAlgoId';$serverField='algoId';$statusField='algoStatus'}
 $id=Inspect-Member $Row $field;$idHash=$null
 if($id -is [string] -and $id.Length -gt 0 -and $id.Length -le 128){$idHash=Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($id))}
 $server=Review-Number (Inspect-Member $Row $serverField) 20
 if($null -ne $server -and $server -cnotmatch '^[1-9][0-9]{0,19}$'){$server=$null}
 $reduce=Inspect-Member $Row 'reduceOnly';$reduceSafe=$null
 if($reduce -is [bool]){$reduceSafe=$reduce}elseif($reduce -is [string] -and @('true','false') -ccontains $reduce){$reduceSafe=$reduce -ceq 'true'}
 return [pscustomobject]@{exact_id_match=($null -ne $idHash -and $idHash -ceq $TargetHash);client_id_sha256=$idHash;server_id=$server;symbol=(Review-Choice (Inspect-Member $Row 'symbol') @('BTCUSDT'));side=(Review-Choice (Inspect-Member $Row 'side') @('BUY','SELL'));type=(Review-Choice (Inspect-Member $Row 'type') @('LIMIT','MARKET','STOP_MARKET','TAKE_PROFIT_MARKET','STOP','TAKE_PROFIT','TRAILING_STOP_MARKET'));status=(Review-Choice (Inspect-Member $Row $statusField) @('NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED','EXPIRED_IN_MATCH','TRIGGERING','TRIGGERED','FINISHED'));reduce_only=$reduceSafe;quantity=(Review-Number (Inspect-Member $Row 'origQty'));executed_quantity=(Review-Number (Inspect-Member $Row 'executedQty'));price=(Review-Number (Inspect-Member $Row 'price'));average_price=(Review-Number (Inspect-Member $Row 'avgPrice'));time_ms=(Review-Number (Inspect-Member $Row 'time') 13);update_time_ms=(Review-Number (Inspect-Member $Row 'updateTime') 13)}
}
function Invoke-SavedHistoryReview([object]$Plan,[string]$Parent) {
 $streams=New-Object Collections.ArrayList
 try {
  Inspect-Require ($Plan.policy -ceq 'DEMO_HISTORY_READ_20261117_V1' -and @($Plan.targets).Count -eq 2) 'REVIEW_PLAN_INVALID'
  Reference-AssertPrivateDirectory $Parent
  $folders=@(Get-ChildItem -LiteralPath $Parent -Directory -Filter 'history-read-20261117-*');Inspect-Require ($folders.Count -le 32) 'REVIEW_SEARCH_LIMIT'
  $selected=@()
  foreach($folder in $folders){
   Inspect-Require ($folder.Name -cmatch '^history-read-20261117-[0-9a-f]{32}$') 'REVIEW_DIRECTORY_INVALID'
   Reference-AssertPrivateDirectory $folder.FullName
   $path=Join-Path $folder.FullName 'RECEIPT.json';if(-not [IO.File]::Exists($path)){continue}
   $receiptFile=Review-Open $path 262144 $streams;$receipt=Inspect-Json $receiptFile.body
   if($receipt.history_read -cne 'TWO_HISTORY_LISTS_SAVED_NO_EXPORT_JOB_CREATED'){continue}
   Inspect-Require ([string]::Equals($receipt.private_directory,$folder.FullName,[StringComparison]::OrdinalIgnoreCase) -and $receipt.actual_http_attempts -eq 6 -and $receipt.export_jobs_created -eq 0 -and $receipt.order_writes -eq 0) 'REVIEW_RECEIPT_SCOPE_INVALID'
   foreach($name in @('old_export_guard_changed','automatic_retry','credentials_saved','source_files_written','execution_approved','orders_enabled','live_trading_enabled')){Inspect-Require ($receipt.$name -is [bool] -and -not $receipt.$name) 'REVIEW_RECEIPT_STATE_INVALID'}
   $intentFile=Review-Open (Join-Path $folder.FullName 'INTENT.json') 16384 $streams;$intent=Inspect-Json $intentFile.body
   Inspect-Require ($intent.policy -ceq 'DEMO_HISTORY_READ_INTENT_V1' -and $intent.manifest_sha256 -ceq $Plan.manifest_sha256 -and $intent.start_ms -eq 1792454400000L -and $intent.end_ms -eq 1792540799999L -and $intent.export_jobs_allowed -eq 0) 'REVIEW_INTENT_SCOPE_INVALID'
   Inspect-Require (@($receipt.reports).Count -eq 2) 'REVIEW_REPORT_SCOPE_INVALID'
   $raw=@{}
   foreach($role in @('EXIT','STOP_LOSS')){
    $report=@($receipt.reports|Where-Object {$_.role -ceq $role});Inspect-Require ($report.Count -eq 1 -and $report[0].response_sha256 -cmatch '^[0-9a-f]{64}$') 'REVIEW_REPORT_SCOPE_INVALID'
    $name='REGULAR_20261020.json';if($role -ceq 'STOP_LOSS'){$name='ALGO_20261020.json'}
    $data=Review-Open (Join-Path $folder.FullName $name) 4194304 $streams
    Inspect-Require ($data.sha256 -ceq $report[0].response_sha256) 'REVIEW_RESPONSE_HASH_MISMATCH'
    foreach($countField in @('returned_count','exact_id_candidate_count')){$count=Review-Number $report[0].$countField 4;Inspect-Require ($null -ne $count -and $count -cmatch '^[0-9]{1,4}$' -and [int]$count -le 1000) 'REVIEW_REPORT_COUNT_INVALID'}
    $raw[$role]=[pscustomobject]@{file=$data;report=$report[0]}
   }
   # Matches the saved empty algo response hash visible in the user's completion screen.
   if($raw['STOP_LOSS'].file.sha256 -ceq '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'){$selected+=,[pscustomobject]@{raw=$raw;receipt_sha256=$receiptFile.sha256}}
  }
  Inspect-Require ($selected.Count -eq 1) 'REVIEW_MATCHED_SAVED_RUN_NOT_UNIQUE'
  $run=$selected[0];$reports=@()
  foreach($role in @('EXIT','STOP_LOSS')){
   $target=@($Plan.targets|Where-Object {$_.role -ceq $role});Inspect-Require ($target.Count -eq 1) 'REVIEW_TARGET_NOT_UNIQUE'
   $data=$run.raw[$role];$list=Read-List $data.file.body
   $projected=@($list.rows|ForEach-Object {Review-Row $_ $role $target[0].request_reference_sha256})
   $exact=@($projected|Where-Object {$_.exact_id_match})
   $reports+=,[pscustomobject]@{role=$role;response_sha256=$data.file.sha256;old_reported_count=$data.report.returned_count;corrected_returned_count=$list.returned_count;old_exact_id_candidate_count=$data.report.exact_id_candidate_count;corrected_exact_id_candidate_count=$exact.Count;page_limit_reached=$list.page_limit_reached;safe_preview=@($projected|Select-Object -First 20);preview_truncated=($projected.Count -gt 20);exact_match_preview=@($exact|Select-Object -First 10);remote_resolution_verified=$false;absence_proves_not_sent=$false;history_completeness_verified=$false}
  }
  foreach($entry in $streams){Inspect-Require ((Inspect-StreamHash $entry.stream) -ceq $entry.sha256) 'REVIEW_INPUT_CHANGED'}
  return [pscustomobject]@{saved_history_review='SAVED_HISTORY_REANALYZED_NO_HTTP_NO_WRITES';receipt_sha256=$run.receipt_sha256;reports=$reports;array_handling='ROOT_PROPERTY_ARRAY_NO_COMMAND_PIPELINE_ENUMERATION';old_counts_superseded_by_local_reanalysis=$true;originals_independently_inspected=$false;response_hashes_match_local_receipt=$true;algo_response_hash_matches_user_screen=$true;regular_response_independent_hash_pin=$false;actual_http_attempts=0;credentials_accessed=$false;files_written=$false;old_export_guard_changed=$false;request_states_changed=$false;baseline_adopted=$false;automatic_retry=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
 } catch {
  $reason='REVIEW_UNAVAILABLE';if($_.Exception.Message -cmatch '^(REVIEW|READ|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$reason=$_.Exception.Message}
  return [pscustomobject]@{saved_history_review='BLOCKED_NO_HTTP_NO_WRITES';failure_category=$reason;actual_http_attempts=0;credentials_accessed=$false;files_written=$false;automatic_retry=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
 } finally {foreach($entry in $streams){$entry.stream.Dispose()}}
}
