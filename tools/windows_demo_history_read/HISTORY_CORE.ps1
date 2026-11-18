function Read-SaveResponse([string]$Path,[string]$Body) {
 $bytes=[Text.Encoding]::UTF8.GetBytes($Body)
 Inspect-Require ($bytes.Length -le 4194304) 'READ_RESPONSE_TOO_LARGE'
 $file=New-Object IO.FileStream($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try {$file.Write($bytes,0,$bytes.Length);$file.Flush($true)} finally {$file.Dispose()}
 Inspect-Require ((Inspect-Hash ([IO.File]::ReadAllBytes($Path))) -ceq (Inspect-Hash $bytes)) 'READ_RESPONSE_READBACK_FAILED'
 return Inspect-Hash $bytes
}
function Read-List([string]$Body) {
 Inspect-Require ($Body.TrimStart().StartsWith('[')) 'READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY'
 try {
  # Validate the standalone array before wrapping; command-output enumeration differs on PS5.1.
  $null=ConvertFrom-Json -InputObject $Body
  $envelope=ConvertFrom-Json -InputObject ('{"items":'+$Body+'}')
  Inspect-Require ($envelope.items -is [Array]) 'READ_RESPONSE_JSON_INVALID_SAVED_PRIVATELY'
  $rows=@($envelope.items)
 } catch {throw 'READ_RESPONSE_JSON_INVALID_SAVED_PRIVATELY'}
 Inspect-Require ($rows.Count -le 1000) 'READ_RESPONSE_ROW_LIMIT'
 foreach($row in $rows){Inspect-Require ($null -ne $row -and $row.GetType() -eq [System.Management.Automation.PSCustomObject]) 'READ_RESPONSE_ROW_INVALID'}
 return [pscustomobject]@{rows=$rows;returned_count=$rows.Count;page_limit_reached=($rows.Count -eq 1000);history_completeness_verified=$false}
}
function Invoke-HistoryRead([object]$Plan) {
 $context=[pscustomobject]@{credentials=$null;watch=[Diagnostics.Stopwatch]::StartNew();server_time=$null;clock_observed_ms=0L;http_attempts=0;max_used_weight=0L;weight_observed=$false;weight_limit=0L}
 $directory=$null;$script:ReferenceAttemptPath=$null;$reports=@();$failure=$null;$schema=$null
 try {
  Inspect-Require (($null -eq $env:LIVE_TRADING_ENABLED -or $env:LIVE_TRADING_ENABLED -ieq 'false') -and $env:TRADING_MODE -ine 'LIVE') 'READ_LIVE_CONTEXT_FORBIDDEN'
  foreach($name in @('BINANCE_DEMO_API_KEY','BINANCE_DEMO_API_SECRET','BINANCE_API_KEY','BINANCE_API_SECRET','BINANCE_LIVE_API_KEY','BINANCE_LIVE_API_SECRET')){Inspect-Require ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) 'READ_ENV_CREDENTIALS_FORBIDDEN'}
  Inspect-Require ($Plan.policy -ceq 'DEMO_HISTORY_READ_20261117_V1' -and $Plan.host -ceq 'https://demo-fapi.binance.com' -and @($Plan.anchors).Count -eq 2 -and @($Plan.anchors.server_id|Select-Object -Unique).Count -eq 2 -and @($Plan.targets).Count -eq 2 -and @($Plan.targets|Where-Object {$_.role -ceq 'EXIT'}).Count -eq 1 -and @($Plan.targets|Where-Object {$_.role -ceq 'STOP_LOSS'}).Count -eq 1 -and $Plan.read_window_start_ms -eq 1792454400000L -and $Plan.read_window_end_ms -eq 1792540799999L) 'READ_PLAN_INVALID'
  $directory=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
  Export-WriteJson (Join-Path $directory 'INTENT.json') ([ordered]@{policy='DEMO_HISTORY_READ_INTENT_V1';manifest_sha256=$Plan.manifest_sha256;start_ms=$Plan.read_window_start_ms;end_ms=$Plan.read_window_end_ms;export_jobs_allowed=0;automatic_retry=$false})
  $context.credentials=Export-Credentials;$context.watch.Restart()
  Export-Clock $context;Export-RateLimit $context
  foreach($anchor in $Plan.anchors){$reply=Export-Http $context '/fapi/v1/order' @{symbol='BTCUSDT';orderId=[string]$anchor.server_id};$data=Export-Decode $reply.body;Export-Anchor $anchor $data;Inspect-Require $reply.weight_observed 'READ_CURRENT_RATE_HEADER_REQUIRED'}
  Inspect-Require ($context.weight_observed -and $context.max_used_weight+100 -le $context.weight_limit) 'READ_INSUFFICIENT_WEIGHT_HEADROOM'
  foreach($role in @('EXIT','STOP_LOSS')) {
   $route='/fapi/v1/allOrders';$field='clientOrderId';$name='REGULAR_20261020.json'
   if($role -ceq 'STOP_LOSS'){$route='/fapi/v1/allAlgoOrders';$field='clientAlgoId';$name='ALGO_20261020.json'}
   $reply=Export-Http $context $route @{symbol='BTCUSDT';startTime='1792454400000';endTime='1792540799999';limit='1000'}
   $hash=Read-SaveResponse (Join-Path $directory $name) $reply.body
   $schema='NON_ARRAY';if($reply.body.TrimStart().StartsWith('[')){$schema='ARRAY'}
   $list=Read-List $reply.body
   $target=@($Plan.targets|Where-Object {$_.role -ceq $role})[0];$matchesFound=0
   foreach($row in $list.rows){$id=Inspect-Member $row $field;if($id -is [string] -and (Inspect-Hash ([Text.Encoding]::UTF8.GetBytes($id))) -ceq $target.request_reference_sha256){$matchesFound++}}
   $reports+=,[pscustomobject]@{role=$role;response_sha256=$hash;returned_count=$list.returned_count;page_limit_reached=$list.page_limit_reached;exact_id_candidate_count=$matchesFound;remote_resolution_verified=$false;absence_proves_not_sent=$false;history_completeness_verified=$false}
  }
  $state='TWO_HISTORY_LISTS_SAVED_NO_EXPORT_JOB_CREATED'
 } catch {
  if($null -eq $directory -and $null -ne $script:ReferenceAttemptPath){$directory=$script:ReferenceAttemptPath}
  $failure='READ_RUN_BLOCKED';if($_.Exception.Message -cmatch '^(READ|EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$failure=$_.Exception.Message};$state='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY'
 } finally {
  if($null -ne $context.credentials){$context.credentials.key=$null;$context.credentials.secret=$null;$context.credentials=$null};$context.watch.Stop()
 }
 $result=[pscustomobject]@{history_read=$state;failure_category=$failure;private_directory=$directory;last_response_schema=$schema;reports=$reports;actual_http_attempts=$context.http_attempts;export_jobs_created=0;old_export_guard_changed=$false;automatic_retry=$false;credentials_saved=$false;raw_client_ids_displayed=$false;archive_downloaded=$false;history_completeness_verified=$false;remote_request_resolution_verified=$false;baseline_adopted=$false;source_files_written=$false;docker_used=$false;services_started=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false;external_ip_usage_fully_known=$false}
 if($null -ne $directory -and [IO.Directory]::Exists($directory)){try {Export-WriteJson (Join-Path $directory 'RECEIPT.json') $result} catch {$result.history_read='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY';$result.failure_category='READ_RECEIPT_WRITE_FAILED'}}
 return $result
}
