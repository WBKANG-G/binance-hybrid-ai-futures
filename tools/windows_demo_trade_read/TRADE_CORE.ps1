function Trades-Add([object[]]$Rows,[hashtable]$Seen,[long]$Start,[long]$End) {
 $added=0;$duplicates=0
 foreach($row in $Rows){
  Inspect-Require ($row.symbol -ceq 'BTCUSDT' -and @('BUY','SELL') -ccontains $row.side -and @('BOTH','LONG','SHORT') -ccontains $row.positionSide) 'TRADES_ROW_SCOPE_INVALID'
  $id=Income-Integer $row.id 'TRADES_ID_INVALID';$order=Income-Integer $row.orderId 'TRADES_ORDER_ID_INVALID';$timeText=Income-Integer $row.time 'TRADES_TIME_INVALID'
  Inspect-Require ($id -cmatch '^[0-9]{1,19}$' -and $order -cmatch '^[1-9][0-9]{0,18}$' -and $timeText -cmatch '^[0-9]{13}$') 'TRADES_ID_OR_TIME_INVALID'
  $stamp=[long]$timeText;Inspect-Require ($stamp -ge $Start -and $stamp -le $End) 'TRADES_ROW_OUTSIDE_WINDOW'
  $qty=Income-Decimal $row.qty;$price=Income-Decimal $row.price;$pnl=Income-Decimal $row.realizedPnl;$fee=Income-Decimal $row.commission
  Inspect-Require ($qty -gt 0 -and $price -gt 0 -and $row.commissionAsset -is [string] -and $row.commissionAsset -cmatch '^[A-Z0-9]{2,16}$') 'TRADES_FINANCIAL_ROW_INVALID'
  $key='BTCUSDT:'+$id;$canonical=$order+'|'+$timeText+'|'+$row.side+'|'+$row.positionSide+'|'+(Income-Text $qty)+'|'+(Income-Text $price)+'|'+(Income-Text $pnl)+'|'+(Income-Text $fee)+'|'+$row.commissionAsset
  if($Seen.ContainsKey($key)){Inspect-Require ($Seen[$key].canonical -ceq $canonical) 'TRADES_CONFLICTING_ID';$duplicates++}
  else{$Seen[$key]=[pscustomobject]@{canonical=$canonical;stamp=$stamp;qty=$qty;pnl=$pnl;fee=$fee;fee_asset=$row.commissionAsset;side=$row.side};$added++}
 }
 return [pscustomobject]@{added=$added;duplicates=$duplicates}
}
function Trades-Window([object]$Context,[string]$Directory,[long]$Start,[long]$End,[string]$Label,[hashtable]$Seen) {
 $reply=Export-Http $Context '/fapi/v1/userTrades' @{symbol='BTCUSDT';startTime=[string]$Start;endTime=[string]$End;limit='1000'}
 $hash=Income-Save (Join-Path $Directory ($Label+'.json')) $reply.body
 $list=Read-List $reply.body;$progress=Trades-Add $list.rows $Seen $Start $End
 return [pscustomobject]@{file=($Label+'.json');sha256=$hash;start_ms=$Start;end_ms=$End;returned_count=$list.returned_count;limit_reached=$list.page_limit_reached;duplicate_rows=$progress.duplicates}
}
function Invoke-TradeRead([object]$Plan,[object]$Source) {
 $context=[pscustomobject]@{credentials=$null;watch=[Diagnostics.Stopwatch]::StartNew();server_time=$null;clock_observed_ms=0L;http_attempts=0;max_used_weight=0L;weight_observed=$false;weight_limit=0L}
 $directory=$null;$script:ReferenceAttemptPath=$null;$failure=$null;$evidence=@();$summary=$null;$finished=$false;$savedVerified=$false;$end=$null
 try {
  Inspect-Require (($null -eq $env:LIVE_TRADING_ENABLED -or $env:LIVE_TRADING_ENABLED -ieq 'false') -and $env:TRADING_MODE -ine 'LIVE') 'TRADES_LIVE_CONTEXT_FORBIDDEN'
  foreach($name in @('BINANCE_DEMO_API_KEY','BINANCE_DEMO_API_SECRET','BINANCE_API_KEY','BINANCE_API_SECRET','BINANCE_LIVE_API_KEY','BINANCE_LIVE_API_SECRET')){Inspect-Require ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name))) 'TRADES_ENV_CREDENTIALS_FORBIDDEN'}
  Inspect-Require ($Plan.policy -ceq 'DEMO_INCOME_READ_20261118_V1' -and $Plan.host -ceq 'https://demo-fapi.binance.com' -and @($Plan.anchors).Count -eq 2 -and @($Plan.anchors.server_id|Select-Object -Unique).Count -eq 2) 'TRADES_PLAN_INVALID'
  Trades-VerifySaved $Source;$directory=Reference-NewDirectory ([Guid]::NewGuid().ToString('N'))
  Export-WriteJson (Join-Path $directory 'INTENT.json') ([ordered]@{policy='DEMO_BTC_TRADE_READ_20261118_V1';manifest_sha256=$Plan.manifest_sha256;saved_income_receipt_sha256=$Source.receipt_sha256;old_cutoff_ms=$Source.old_cutoff_ms;start_ms=$Source.week_start_ms;symbol_scope='BTCUSDT';other_symbol_activity_statement='USER_REPORT_NONE_SINCE_20261116_AS_REPORTED_20261118_NOT_INDEPENDENTLY_VERIFIED';export_jobs_allowed=0;automatic_retry=$false})
  $context.credentials=Export-Credentials;$context.watch.Restart();Export-Clock $context;Export-RateLimit $context
  foreach($anchor in $Plan.anchors){$reply=Export-Http $context '/fapi/v1/order' @{symbol='BTCUSDT';orderId=[string]$anchor.server_id};Export-Anchor $anchor (Export-Decode $reply.body);Inspect-Require $reply.weight_observed 'TRADES_CURRENT_RATE_HEADER_REQUIRED'}
  Inspect-Require ($context.weight_observed -and $context.max_used_weight+100 -le $context.weight_limit) 'TRADES_INSUFFICIENT_WEIGHT_HEADROOM'
  $end=[long]$context.server_time
  Inspect-Require ($end -ge $Source.old_cutoff_ms+5000L -and $end-$Source.week_start_ms -le 604800000L) 'TRADES_SAVED_WINDOW_NOT_COVERED'
  $seen=@{};$root=Trades-Window $context $directory $Source.week_start_ms $end 'TRADES_ROOT' $seen;$evidence+=,$root
  if(-not $root.limit_reached){$finished=$true}
  else {
   Inspect-Require ($end -gt $Source.week_start_ms) 'TRADES_SINGLE_MS_SATURATION'
   $mid=[long]$Source.week_start_ms+[long][Math]::Floor(($end-$Source.week_start_ms)/2)
   $left=Trades-Window $context $directory $Source.week_start_ms $mid 'TRADES_LEFT' $seen;$evidence+=,$left
   Inspect-Require (-not $left.limit_reached) 'TRADES_SPLIT_WINDOW_INCOMPLETE'
   $right=Trades-Window $context $directory ($mid+1L) $end 'TRADES_RIGHT' $seen;$evidence+=,$right
   Inspect-Require (-not $right.limit_reached) 'TRADES_SPLIT_WINDOW_INCOMPLETE';$finished=$true
  }
  $priorCount=0;$laterCount=0;$dayCount=0;$pnl=[decimal]0;$fees=[decimal]0;$otherFeeAssets=0;$boundaryEvents=0;$nonzeroCashflowRows=0
  foreach($row in $seen.Values){if($row.stamp -le $Source.old_cutoff_ms){$priorCount++;if($row.pnl -ne 0 -or $row.fee -ne 0){$nonzeroCashflowRows++};if($row.stamp -ge $Source.day_boundary_ms){$dayCount++};$pnl+=$row.pnl;if($row.fee_asset -ceq 'USDT'){$fees+=$row.fee}else{$otherFeeAssets++};if($row.stamp -eq $Source.week_start_ms -or $row.stamp -eq $Source.day_boundary_ms){$boundaryEvents++}}else{$laterCount++}}
  $summary=[pscustomobject]@{symbol='BTCUSDT';unique_trade_rows=$seen.Count;rows_through_saved_income_cutoff=$priorCount;rows_in_saved_day_through_old_cutoff=$dayCount;rows_after_saved_cutoff=$laterCount;prior_window_realized_pnl_usdt=(Income-Text $pnl);prior_window_commission_usdt=(Income-Text $fees);other_commission_asset_rows=$otherFeeAssets;nonzero_financial_trade_rows=$nonzeroCashflowRows;boundary_timestamp_trade_rows=$boundaryEvents;saved_income_rows=0;pnl_and_commission_zero_matches_saved_empty_income=($nonzeroCashflowRows -eq 0 -and $otherFeeAssets -eq 0);no_btc_fills_in_returned_scope=($seen.Count -eq 0);no_btc_fills_through_old_cutoff=($priorCount -eq 0)}
  Trades-VerifySaved $Source;$savedVerified=$true;$state='BTC_TRADE_RESPONSES_SAVED_NO_BASELINE_ADOPTION'
 } catch {
  if($null -eq $directory -and $null -ne $script:ReferenceAttemptPath){$directory=$script:ReferenceAttemptPath}
  $failure='TRADES_RUN_BLOCKED';if($_.Exception.Message -cmatch '^(TRADES|INCOME|READ|EXPORT|INSPECT|REFERENCE)_[A-Z0-9_]{1,100}$'){$failure=$_.Exception.Message};$state='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY'
 } finally {if($null -ne $context.credentials){$context.credentials.key=$null;$context.credentials.secret=$null;$context.credentials=$null};$context.watch.Stop()}
 $result=[pscustomobject]@{trade_read=$state;failure_category=$failure;private_directory=$directory;day_key_kst=$Source.day_key;week_key_kst=$Source.week_key;saved_wallet_candidate_usdt=$Source.wallet;saved_income_cutoff_ms=$Source.old_cutoff_ms;trade_query_end_ms=$end;summary=$summary;response_windows=$evidence;endpoint_window_pagination_finished=$finished;saved_income_hashes_verified_before_after=$savedVerified;saved_positions_flat=$true;current_positions_refreshed=$false;symbol_scope='BTCUSDT';operator_reports_no_manual_or_other_program_non_btc_activity_since='2026-11-16';operator_statement_as_of_kst_day='2026-11-18';operator_statement_extended_to_new_query_end=$false;operator_statement_independently_verified=$false;account_wide_trade_history_verified=$false;boundary_positions_independently_verified=$false;equity_baseline_reconstructed=$false;history_completeness_independently_verified=$false;actual_http_attempts=$context.http_attempts;export_jobs_created=0;old_export_guard_changed=$false;automatic_retry=$false;credentials_saved=$false;source_files_written=$false;request_states_changed=$false;baseline_adopted=$false;docker_used=$false;services_started=$false;order_writes=0;execution_approved=$false;orders_enabled=$false;live_trading_enabled=$false}
 if($null -ne $directory -and [IO.Directory]::Exists($directory)){try{Export-WriteJson (Join-Path $directory 'RECEIPT.json') $result}catch{$result.trade_read='STOPPED_PARTIAL_RECORDS_RETAINED_NO_AUTOMATIC_RETRY';$result.failure_category='TRADES_RECEIPT_WRITE_FAILED'}}
 return $result
}
